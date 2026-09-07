"""Engine-owned mutations for the shared humanize pattern library.

Both desktop clients use :class:`HumanizeLibrary` as the durable source.  This
service adds an optimistic-concurrency boundary around it so a UI never writes
directly to the SQLite store or silently replaces a newer library revision.
"""

from __future__ import annotations

import hashlib
import json
import secrets
from dataclasses import dataclass
from typing import Callable, Literal

from novel_forge.core.schemas.humanize_library import HumanizeLibraryEntry
from novel_forge.memory.humanize_library_store import (
    HumanizeLibrary,
    LibraryError,
)

HumanizeLibraryFactory = Callable[[], HumanizeLibrary]
HumanizeSeverity = Literal["critical", "high", "medium", "low"]


class HumanizeLibraryMutationError(ValueError):
    """A requested humanize-library mutation cannot be applied safely."""


class HumanizeLibraryConflictError(HumanizeLibraryMutationError):
    """The caller based its edit on an obsolete library revision."""


@dataclass(frozen=True)
class HumanizePatternInput:
    """The editable fields shared by the PySide and Nimo pattern editors."""

    pattern_id: str | None = None
    name: str = ""
    category: str = ""
    severity: str = "medium"
    keywords: tuple[str, ...] = ()
    notes: str = ""
    example_phrase: str = ""
    source: Literal["user", "imported"] = "user"


@dataclass(frozen=True)
class HumanizeLibrarySnapshot:
    entries: tuple[HumanizeLibraryEntry, ...]
    revision: str


@dataclass(frozen=True)
class HumanizeLibraryMutationResult:
    entry: HumanizeLibraryEntry | None
    revision: str


_SEVERITY_LABELS: dict[str, HumanizeSeverity] = {
    "严重": "critical",
    "高": "high",
    "中": "medium",
    "低": "low",
    "critical": "critical",
    "high": "high",
    "medium": "medium",
    "low": "low",
}


def load_humanize_library(
    *,
    library_factory: HumanizeLibraryFactory | None = None,
) -> HumanizeLibrarySnapshot:
    """Load a stable snapshot of the globally shared pattern library."""

    factory = library_factory or HumanizeLibrary.from_default_path
    with factory() as library:
        return _snapshot(library.list_all())


def humanize_pattern_view(entry: HumanizeLibraryEntry) -> dict[str, object]:
    """Project one library entry into the stable dual-client read contract."""

    source_labels = {"builtin": "内置", "user": "用户", "imported": "导入"}
    return {
        "id": entry.pattern_id,
        "name": entry.pattern_name,
        "source_label": source_labels[entry.source],
        "category": entry.category,
        "severity": entry.severity,
        "hit_count": entry.hit_count,
        "last_chapter_label": (
            f"第 {entry.last_hit_chapter} 章" if entry.last_hit_chapter is not None else "—"
        ),
        "enabled": entry.enabled,
        "keywords": list(entry.keywords),
        "notes": entry.notes,
        "example_phrase": entry.example_phrases[0] if entry.example_phrases else "",
    }


def save_humanize_pattern(
    pattern: HumanizePatternInput,
    *,
    expected_revision: str,
    pattern_id: str | None = None,
    library_factory: HumanizeLibraryFactory | None = None,
) -> HumanizeLibraryMutationResult:
    """Create or update a user-owned pattern under one library lock."""

    normalized = _normalized_input(pattern)

    def mutate(
        library: HumanizeLibrary, entries: tuple[HumanizeLibraryEntry, ...]
    ) -> HumanizeLibraryEntry:
        effective_id = pattern_id or normalized.pattern_id
        existing = _entry_by_id(entries, effective_id) if effective_id else None
        if existing is None:
            entry = HumanizeLibraryEntry(
                pattern_id=_new_pattern_id(effective_id, entries, normalized.source),
                pattern_name=normalized.name,
                category=normalized.category,
                severity=_severity(normalized.severity),
                example_phrases=_example_phrases(normalized.example_phrase),
                keywords=list(normalized.keywords),
                source=normalized.source,
                notes=normalized.notes,
                detection_method="regex",
                enabled=True,
            )
            library._insert_entry(entry)
            library._sync_fts_insert(entry)
            return entry
        if existing.source == "builtin":
            raise HumanizeLibraryMutationError("内置拟人化模式为代码所有，不能编辑。")
        entry = existing.model_copy(
            update={
                "pattern_name": normalized.name,
                "category": normalized.category,
                "severity": _severity(normalized.severity),
                "example_phrases": _example_phrases(normalized.example_phrase),
                "keywords": list(normalized.keywords),
                "notes": normalized.notes,
                "embedding_signature": None,
                "vector_stale": True,
            }
        )
        library._update_entry(entry)
        library._sync_fts_update(entry)
        return entry

    return _mutate(
        expected_revision,
        "save",
        mutate,
        library_factory=library_factory or HumanizeLibrary.from_default_path,
    )


def set_humanize_pattern_enabled(
    pattern_id: str,
    enabled: bool,
    *,
    expected_revision: str,
    library_factory: HumanizeLibraryFactory | None = None,
) -> HumanizeLibraryMutationResult:
    """Enable or disable a pattern without giving either UI database access."""

    def mutate(
        library: HumanizeLibrary, entries: tuple[HumanizeLibraryEntry, ...]
    ) -> HumanizeLibraryEntry:
        existing = _required_entry(entries, pattern_id)
        entry = existing.model_copy(update={"enabled": enabled})
        library._update_entry(entry)
        library._sync_fts_update(entry)
        return entry

    return _mutate(
        expected_revision,
        "set_enabled",
        mutate,
        library_factory=library_factory or HumanizeLibrary.from_default_path,
    )


def remove_humanize_pattern(
    pattern_id: str,
    *,
    expected_revision: str,
    library_factory: HumanizeLibraryFactory | None = None,
) -> HumanizeLibraryMutationResult:
    """Remove a non-builtin pattern in the same transaction as its revision check."""

    def mutate(library: HumanizeLibrary, entries: tuple[HumanizeLibraryEntry, ...]) -> None:
        existing = _required_entry(entries, pattern_id)
        if existing.source == "builtin":
            raise HumanizeLibraryMutationError("内置拟人化模式不能删除。")
        library._conn.execute("DELETE FROM entries WHERE pattern_id = ?", (pattern_id,))
        library._conn.execute("DELETE FROM entries_fts WHERE pattern_id = ?", (pattern_id,))
        return None

    return _mutate(
        expected_revision,
        "remove",
        mutate,
        library_factory=library_factory or HumanizeLibrary.from_default_path,
    )


def merge_humanize_patterns(
    source_pattern_id: str,
    target_pattern_id: str,
    *,
    expected_revision: str,
    library_factory: HumanizeLibraryFactory | None = None,
) -> HumanizeLibraryMutationResult:
    """Move a user/imported pattern's hit history to another pattern and remove it."""

    if source_pattern_id == target_pattern_id:
        raise HumanizeLibraryMutationError("合并源和目标不能相同。")

    def mutate(
        library: HumanizeLibrary, entries: tuple[HumanizeLibraryEntry, ...]
    ) -> HumanizeLibraryEntry:
        source = _required_entry(entries, source_pattern_id)
        target = _required_entry(entries, target_pattern_id)
        if source.source == "builtin":
            raise HumanizeLibraryMutationError("内置拟人化模式不能作为合并来源。")
        merged = target.model_copy(
            update={
                "hit_count": target.hit_count + source.hit_count,
                "last_hit_chapter": source.last_hit_chapter or target.last_hit_chapter,
            }
        )
        library._update_entry(merged)
        library._sync_fts_update(merged)
        library._conn.execute("DELETE FROM entries WHERE pattern_id = ?", (source.pattern_id,))
        library._conn.execute("DELETE FROM entries_fts WHERE pattern_id = ?", (source.pattern_id,))
        return merged

    return _mutate(
        expected_revision,
        "merge",
        mutate,
        library_factory=library_factory or HumanizeLibrary.from_default_path,
    )


def _mutate(
    expected_revision: str,
    operation: str,
    mutation: Callable[
        [HumanizeLibrary, tuple[HumanizeLibraryEntry, ...]], HumanizeLibraryEntry | None
    ],
    *,
    library_factory: HumanizeLibraryFactory,
) -> HumanizeLibraryMutationResult:
    if not expected_revision:
        raise HumanizeLibraryMutationError("提交拟人化库修改必须携带当前版本。")
    try:
        with library_factory() as library:
            # The public library CRUD helpers take this same lock themselves.
            # This engine boundary intentionally uses the internal SQL helpers so
            # revision comparison and write are a single cross-process operation.
            with library.library_lock(f"engine_{operation}"):
                before = _snapshot(library.list_all())
                if before.revision != expected_revision:
                    raise HumanizeLibraryConflictError("拟人化库已被其他客户端更新，请刷新后重试。")
                entry = mutation(library, before.entries)
                library._conn.commit()
                after = _snapshot(library.list_all())
    except HumanizeLibraryMutationError:
        raise
    except (LibraryError, OSError, ValueError) as exc:
        raise HumanizeLibraryMutationError(str(exc)) from exc
    return HumanizeLibraryMutationResult(entry=entry, revision=after.revision)


def _snapshot(entries: list[HumanizeLibraryEntry]) -> HumanizeLibrarySnapshot:
    ordered = tuple(sorted(entries, key=lambda item: item.pattern_id))
    payload = [entry.model_dump(mode="json") for entry in ordered]
    revision = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()
    return HumanizeLibrarySnapshot(entries=ordered, revision=revision)


def _normalized_input(pattern: HumanizePatternInput) -> HumanizePatternInput:
    name = pattern.name.strip()
    if not name:
        raise HumanizeLibraryMutationError("拟人化模式名称不能为空。")
    return HumanizePatternInput(
        pattern_id=pattern.pattern_id.strip() if pattern.pattern_id else None,
        name=name,
        category=pattern.category.strip(),
        severity=pattern.severity.strip().lower(),
        keywords=tuple(item.strip() for item in pattern.keywords if item.strip()),
        notes=pattern.notes.strip(),
        example_phrase=pattern.example_phrase.strip(),
        source=pattern.source,
    )


def _new_pattern_id(
    requested: str | None,
    entries: tuple[HumanizeLibraryEntry, ...],
    source: Literal["user", "imported"],
) -> str:
    existing_ids = {entry.pattern_id for entry in entries}
    if requested:
        if requested in existing_ids:
            raise HumanizeLibraryMutationError(f"拟人化模式 ID 已存在：{requested}")
        return requested
    while True:
        candidate = f"lib_{source}_{secrets.token_hex(4)}"
        if candidate not in existing_ids:
            return candidate


def _entry_by_id(
    entries: tuple[HumanizeLibraryEntry, ...], pattern_id: str | None
) -> HumanizeLibraryEntry | None:
    if not pattern_id:
        return None
    return next((entry for entry in entries if entry.pattern_id == pattern_id), None)


def _required_entry(
    entries: tuple[HumanizeLibraryEntry, ...], pattern_id: str
) -> HumanizeLibraryEntry:
    entry = _entry_by_id(entries, pattern_id)
    if entry is None:
        raise HumanizeLibraryMutationError(f"未找到拟人化模式：{pattern_id}")
    return entry


def _severity(value: str) -> HumanizeSeverity:
    try:
        return _SEVERITY_LABELS[value]
    except KeyError as exc:
        raise HumanizeLibraryMutationError(f"不支持的拟人化模式严重度：{value}") from exc


def _example_phrases(value: str) -> list[str]:
    return [value] if value else []
