"""Helpers for detecting and invalidating stale downstream chapter artifacts."""

from __future__ import annotations

import hashlib
import json
import logging
import re
import shutil
import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Any

from novel_forge.persistence.filesystem import FileSystemStorage, atomic_write_text
from novel_forge.persistence.models import ProjectLayout

if TYPE_CHECKING:
    from novel_forge.story_kernel.schemas import StoryKernel

_log = logging.getLogger(__name__)
_CHAPTER_PATTERN = re.compile(r"chapter_(\d+)")


class InvalidationScope(StrEnum):
    """Manual final-revision downstream invalidation policy."""

    NONE = "none"
    NEXT = "next"
    VOLUME = "volume"
    DOWNSTREAM = "downstream"


class RevisionScope(StrEnum):
    """Generic revision impact scope for source artifacts and text revisions."""

    LOCAL = "local"
    FORWARD_ONLY = "forward_only"
    FROM_CHAPTER_N = "from_chapter_n"
    VOLUME = "volume"
    WHOLE_BOOK = "whole_book"


class UpstreamArtifactKind(StrEnum):
    """Project-level artifacts that can invalidate chapter generation context."""

    SPEC = "spec"
    STORY_BIBLE = "story_bible"
    CHARACTER_BIBLE = "character_bible"
    OUTLINE = "outline"
    STYLE_PROFILE = "style_profile"
    NARRATIVE_BLUEPRINT = "narrative_blueprint"
    CHAPTER_CONTRACTS = "chapter_contracts"
    NARRATIVE_CONTRACT = "narrative_contract"
    EDITORIAL_CONTRACT = "editorial_contract"


class ChapterCleanupConvergenceError(RuntimeError):
    """Raised when destructive chapter cleanup did not reach a safe restart point."""

    def __init__(
        self,
        message: str,
        *,
        invalidated_chapters: tuple[int, ...] = (),
        failed_stage: str = "cleanup",
    ) -> None:
        super().__init__(message)
        self.invalidated_chapters = invalidated_chapters
        self.failed_stage = failed_stage


@dataclass(frozen=True)
class ManualRevisionRecord:
    """Persisted metadata for a manual final-draft revision."""

    chapter_number: int
    revision_id: str
    source_text_hash: str
    scope: InvalidationScope = InvalidationScope.DOWNSTREAM
    range_start: int | None = None
    range_end: int | None = None
    affected_chapters: tuple[int, ...] = field(default_factory=tuple)
    status: str = "applied"

    def to_payload(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["scope"] = self.scope.value
        payload["affected_chapters"] = list(self.affected_chapters)
        return payload


@dataclass(frozen=True)
class UpstreamArtifactRevisionRecord:
    """Persisted metadata for a project-level source artifact revision."""

    artifact_kind: UpstreamArtifactKind
    revision_id: str
    previous_hash: str = ""
    current_hash: str = ""
    scope: RevisionScope = RevisionScope.FORWARD_ONLY
    from_chapter: int | None = None
    range_start: int | None = None
    range_end: int | None = None
    affected_chapters: tuple[int, ...] = field(default_factory=tuple)
    invalidated_chapters: tuple[int, ...] = field(default_factory=tuple)
    reason: str = "upstream_artifact_revision"
    status: str = "applied"

    def to_payload(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["artifact_kind"] = self.artifact_kind.value
        payload["scope"] = self.scope.value
        payload["affected_chapters"] = list(self.affected_chapters)
        payload["invalidated_chapters"] = list(self.invalidated_chapters)
        return payload


@dataclass(frozen=True)
class TextRevisionRecord:
    """Persisted metadata for non-manual chapter text rewrites."""

    chapter_number: int
    revision_id: str
    source: str
    previous_hash: str = ""
    current_hash: str = ""
    scope: RevisionScope = RevisionScope.LOCAL
    reason: str = ""
    status: str = "applied"

    def to_payload(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["scope"] = self.scope.value
        return payload


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def _coerce_scope(value: Any) -> InvalidationScope:
    raw = str(value or "").strip().lower()
    revision_mapping = {
        RevisionScope.LOCAL.value: InvalidationScope.NONE,
        RevisionScope.FORWARD_ONLY.value: InvalidationScope.DOWNSTREAM,
        RevisionScope.FROM_CHAPTER_N.value: InvalidationScope.DOWNSTREAM,
        RevisionScope.VOLUME.value: InvalidationScope.VOLUME,
        RevisionScope.WHOLE_BOOK.value: InvalidationScope.DOWNSTREAM,
    }
    if raw in revision_mapping:
        return revision_mapping[raw]
    try:
        return InvalidationScope(raw)
    except ValueError:
        return InvalidationScope.DOWNSTREAM


def _coerce_revision_scope(value: Any) -> RevisionScope:
    raw = str(value or "").strip().lower()
    invalidation_mapping = {
        InvalidationScope.NONE.value: RevisionScope.LOCAL,
        InvalidationScope.NEXT.value: RevisionScope.FROM_CHAPTER_N,
        InvalidationScope.VOLUME.value: RevisionScope.VOLUME,
        InvalidationScope.DOWNSTREAM.value: RevisionScope.FROM_CHAPTER_N,
    }
    if raw in invalidation_mapping:
        return invalidation_mapping[raw]
    try:
        return RevisionScope(raw)
    except ValueError:
        return RevisionScope.FORWARD_ONLY


def _coerce_artifact_kind(value: Any) -> UpstreamArtifactKind | None:
    raw = str(value or "").strip().lower()
    try:
        return UpstreamArtifactKind(raw)
    except ValueError:
        return None


def _parse_chapter_number(path: Path) -> int | None:
    match = _CHAPTER_PATTERN.search(path.name)
    if match is None:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


def _parse_prefixed_chapter_number(path: Path, prefix: str, suffix: str = ".json") -> int | None:
    name = path.name
    if not name.startswith(prefix) or not name.endswith(suffix):
        return None
    return _parse_chapter_value(name[len(prefix) : -len(suffix)])


def _parse_chapter_value(value: Any) -> int | None:
    try:
        return int(str(value).strip())
    except (ValueError, TypeError):
        return None


def _chapter_in_range(chapter_number: int, completed_chapter: int, max_chapter: int | None) -> bool:
    return chapter_number > completed_chapter and (
        max_chapter is None or chapter_number <= max_chapter
    )


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _text_sha256(text: str) -> str:
    return hashlib.sha256(str(text or "").encode("utf-8")).hexdigest()


def _load_json_path(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _volume_end_for_chapter(layout: ProjectLayout, chapter_number: int) -> int | None:
    payload = _load_json_path(layout.outline_path)
    if not isinstance(payload, dict) or not bool(payload.get("volume_mode")):
        return None
    volumes = payload.get("volumes")
    if not isinstance(volumes, list):
        return None
    for item in volumes:
        if not isinstance(item, dict):
            continue
        start = _parse_chapter_value(item.get("start_chapter"))
        end = _parse_chapter_value(item.get("end_chapter"))
        if start is None or end is None:
            continue
        if start <= chapter_number <= end:
            return end
    return None


def resolve_manual_invalidation_range(
    *,
    scope: InvalidationScope,
    chapter_number: int,
    layout: ProjectLayout,
    finalized_numbers: list[int] | None = None,
) -> tuple[int | None, int | None, tuple[int, ...]]:
    """Resolve a manual revision scope into an explicit affected chapter range."""
    if chapter_number <= 0 or scope is InvalidationScope.NONE:
        return None, None, ()

    finalized = (
        finalized_numbers if finalized_numbers is not None else finalized_chapter_numbers(layout)
    )
    downstream = [number for number in finalized if number > chapter_number]
    if not downstream:
        return None, None, ()

    end_chapter: int | None
    if scope is InvalidationScope.NEXT:
        end_chapter = chapter_number + 1
    elif scope is InvalidationScope.VOLUME:
        end_chapter = _volume_end_for_chapter(layout, chapter_number)
        if end_chapter is None:
            return None, None, ()
    else:
        end_chapter = None

    affected = tuple(
        number for number in downstream if end_chapter is None or number <= end_chapter
    )
    if not affected:
        return None, end_chapter, ()
    return min(affected), end_chapter, affected


def _remove_path(path: Path) -> bool:
    """Remove a file or directory if it exists."""
    if not path.exists():
        return False
    if path.is_dir():
        shutil.rmtree(path)
    else:
        path.unlink()
    return True


def invalidate_chapter_tts_artifacts(
    layout: ProjectLayout,
    chapter_number: int,
    *,
    include_metadata: bool = True,
    include_script: bool = True,
) -> list[Path]:
    """Remove chapter-scoped TTS artifacts after the authoritative text changes.

    Narrator and voice-team contracts are project-scoped and intentionally kept.
    The chapter metadata, script, synthesized segments, assembled audio and result
    manifest all depend on the exact chapter text and must move together.
    """
    if chapter_number < 1:
        return []

    root = Path(layout.root)
    tts_dir = Path(getattr(layout, "tts_dir", root / "tts"))
    reports_dir = Path(getattr(layout, "reports_dir", root / "reports"))
    states_dir = Path(getattr(layout, "states_dir", root / "states"))
    audio_dir_factory = getattr(layout, "tts_audio_dir", None)
    take_dir_factory = getattr(layout, "tts_take_dir", None)
    result_path_factory = getattr(layout, "tts_audio_result_path", None)
    script_path_factory = getattr(layout, "tts_dubbing_script_path", None)
    sound_resolution_factory = getattr(layout, "tts_sound_resolution_path", None)
    sound_generation_factory = getattr(layout, "tts_sound_generation_report_path", None)
    speech_timeline_factory = getattr(layout, "tts_speech_timeline_path", None)
    mix_plan_factory = getattr(layout, "tts_mix_plan_path", None)
    render_report_factory = getattr(layout, "tts_mix_render_report_path", None)
    audio_quality_factory = getattr(layout, "tts_audio_quality_report_path", None)

    removed: list[Path] = []
    paths: list[Path] = [
        (
            audio_dir_factory(chapter_number)
            if callable(audio_dir_factory)
            else tts_dir / "audio" / f"chapter_{chapter_number:03d}"
        ),
        (
            take_dir_factory(chapter_number)
            if callable(take_dir_factory)
            else tts_dir / "takes" / f"chapter_{chapter_number:03d}"
        ),
        (
            result_path_factory(chapter_number)
            if callable(result_path_factory)
            else tts_dir / "results" / f"chapter_{chapter_number:03d}_audio.json"
        ),
        (
            sound_resolution_factory(chapter_number)
            if callable(sound_resolution_factory)
            else tts_dir / "sound_resolutions" / f"chapter_{chapter_number:03d}.json"
        ),
        (
            sound_generation_factory(chapter_number)
            if callable(sound_generation_factory)
            else tts_dir / "sound_generation" / f"chapter_{chapter_number:03d}.json"
        ),
        (
            speech_timeline_factory(chapter_number)
            if callable(speech_timeline_factory)
            else tts_dir / "timelines" / f"chapter_{chapter_number:03d}.json"
        ),
        (
            mix_plan_factory(chapter_number)
            if callable(mix_plan_factory)
            else tts_dir / "mix_plans" / f"chapter_{chapter_number:03d}.json"
        ),
        (
            render_report_factory(chapter_number)
            if callable(render_report_factory)
            else tts_dir / "render_reports" / f"chapter_{chapter_number:03d}.json"
        ),
        (
            audio_quality_factory(chapter_number)
            if callable(audio_quality_factory)
            else tts_dir / "quality" / f"chapter_{chapter_number:03d}.json"
        ),
    ]
    if include_metadata:
        paths.append(reports_dir / f"chapter_{chapter_number:03d}_tts_metadata.json")
    if include_script:
        paths.append(
            script_path_factory(chapter_number)
            if callable(script_path_factory)
            else tts_dir / "scripts" / f"chapter_{chapter_number:03d}_script.json"
        )
    for path in paths:
        if _remove_path(path):
            removed.append(path)

    # Current checkpoints are chapter-scoped.  The project-wide path remains a
    # migration source and is removed only when it belongs to this chapter.
    progress_path_factory = getattr(layout, "tts_progress_path_for_chapter", None)
    if callable(progress_path_factory):
        progress_path = Path(progress_path_factory(chapter_number))
        if _remove_path(progress_path):
            removed.append(progress_path)

    legacy_progress_path = Path(
        getattr(layout, "tts_progress_path", states_dir / "tts_progress.json")
    )
    if legacy_progress_path.exists():
        progress = _load_json_path(legacy_progress_path)
        progress_chapter = (
            _parse_chapter_value(progress.get("chapter_number"))
            if isinstance(progress, dict)
            else None
        )
        if progress_chapter == chapter_number and _remove_path(legacy_progress_path):
            removed.append(legacy_progress_path)

    return removed


def invalidate_all_tts_audio_derivatives(layout: ProjectLayout) -> list[Path]:
    """Remove synthesized chapter audio after a project-level voice contract changes."""
    root = Path(layout.root)
    tts_dir = Path(getattr(layout, "tts_dir", root / "tts"))
    removed: list[Path] = []

    audio_root = tts_dir / "audio"
    if audio_root.exists():
        for path in sorted(audio_root.glob("chapter_*")):
            chapter_number = _parse_chapter_number(path)
            # chapter_000 stores previews and does not derive from chapter text.
            if chapter_number is not None and chapter_number >= 1 and _remove_path(path):
                removed.append(path)

    results_root = tts_dir / "results"
    if results_root.exists():
        for path in sorted(results_root.glob("chapter_*_audio.json")):
            if _remove_path(path):
                removed.append(path)

    takes_root = tts_dir / "takes"
    if takes_root.exists():
        for path in sorted(takes_root.glob("chapter_*")):
            if _remove_path(path):
                removed.append(path)

    states_dir = Path(getattr(layout, "states_dir", root / "states"))
    progress_dir = states_dir / "tts_progress"
    if _remove_path(progress_dir):
        removed.append(progress_dir)
    progress_path = Path(getattr(layout, "tts_progress_path", states_dir / "tts_progress.json"))
    if _remove_path(progress_path):
        removed.append(progress_path)
    return removed


def _prune_jsonl_chapter_records(
    path: Path,
    completed_chapter: int,
    max_chapter: int | None = None,
) -> set[int]:
    """Prune JSONL records with ``chapter_number`` after ``completed_chapter``."""
    if not path.exists():
        return set()

    removed: set[int] = set()
    kept_lines: list[str] = []
    changed = False

    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        _log.warning("Failed to load JSONL chapter records from %s", path)
        return set()

    for line in lines:
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            kept_lines.append(line)
            continue
        if not isinstance(payload, dict):
            kept_lines.append(line)
            continue
        chapter_no = _parse_chapter_value(payload.get("chapter_number"))
        if chapter_no is not None and _chapter_in_range(chapter_no, completed_chapter, max_chapter):
            removed.add(chapter_no)
            changed = True
            continue
        kept_lines.append(json.dumps(payload, ensure_ascii=False))

    if changed:
        text = "\n".join(kept_lines)
        atomic_write_text(path, text + ("\n" if text else ""))

    return removed


def _recompute_element_progress_totals(payload: dict[str, Any]) -> None:
    """Recompute aggregate counters after pruning chapter-level progress."""
    chapters = payload.get("chapters", {})
    if not isinstance(chapters, dict):
        payload["totals"] = {"scheduled": 0, "hit": 0, "weak": 0, "miss": 0}
        payload["pending_element_ids"] = []
        payload["arbiter_totals"] = {"runs": 0, "reviewed": 0, "changed": 0}
        return

    totals = {"scheduled": 0, "hit": 0, "weak": 0, "miss": 0}
    arbiter_totals = {"runs": 0, "reviewed": 0, "changed": 0}
    latest_by_element: dict[str, tuple[int, str]] = {}

    for chapter_key, chapter_data in chapters.items():
        if not isinstance(chapter_data, dict):
            continue
        chapter_no = _parse_chapter_value(chapter_data.get("chapter_number", chapter_key))
        if chapter_no is None:
            chapter_no = 0

        arbiter = chapter_data.get("arbiter")
        if isinstance(arbiter, dict):
            try:
                reviewed = int(arbiter.get("reviewed_count", 0) or 0)
            except (ValueError, TypeError):
                reviewed = 0
                _log.debug("Invalid reviewed_count in arbiter data")
            try:
                changed = int(arbiter.get("changed_count", 0) or 0)
            except (ValueError, TypeError):
                changed = 0
                _log.debug("Invalid changed_count in arbiter data")
            if reviewed > 0:
                arbiter_totals["runs"] += 1
            arbiter_totals["reviewed"] += max(0, reviewed)
            arbiter_totals["changed"] += max(0, changed)

        for item in list(chapter_data.get("results", []) or []):
            if not isinstance(item, dict):
                continue
            status = str(item.get("status", "") or "").strip().lower()
            if status in totals:
                totals[status] += 1

            element_id = str(item.get("element_id", "") or "").strip()
            if not element_id:
                continue
            previous = latest_by_element.get(element_id)
            if previous is None or chapter_no >= previous[0]:
                latest_by_element[element_id] = (chapter_no, status)

    payload["totals"] = totals
    payload["pending_element_ids"] = sorted(
        element_id
        for element_id, (_, status) in latest_by_element.items()
        if status in {"scheduled", "weak", "miss"}
    )
    payload["arbiter_totals"] = arbiter_totals


def _prune_element_progress(
    storage: FileSystemStorage,
    layout: ProjectLayout,
    completed_chapter: int,
    max_chapter: int | None = None,
) -> set[int]:
    """Prune element_progress chapters newer than ``completed_chapter``."""
    path = layout.element_progress_path
    if not storage.exists(path):
        return set()

    try:
        payload = storage.load_json(path)
    except (OSError, json.JSONDecodeError):
        _log.warning("Failed to load element progress from %s", path)
        return set()
    if not isinstance(payload, dict):
        return set()

    chapters = payload.get("chapters")
    if not isinstance(chapters, dict):
        return set()

    removed: set[int] = set()
    kept: dict[str, Any] = {}

    for key, chapter_data in chapters.items():
        chapter_no = _parse_chapter_value(
            chapter_data.get("chapter_number", key) if isinstance(chapter_data, dict) else key
        )
        if chapter_no is None:
            kept[str(key)] = chapter_data
            continue
        if _chapter_in_range(chapter_no, completed_chapter, max_chapter):
            removed.add(chapter_no)
            continue
        kept[str(chapter_no)] = chapter_data

    if not removed:
        return set()

    payload["chapters"] = kept
    _recompute_element_progress_totals(payload)
    storage.save_json(path, payload)
    return removed


def _prune_narrative_state(
    storage: FileSystemStorage,
    layout: ProjectLayout,
    completed_chapter: int,
    max_chapter: int | None = None,
) -> set[int]:
    """Prune LLM-adjudicated narrative state for chapters after the waterline."""
    root = layout.narrative_state_dir
    removed: set[int] = set()
    projection_needs_refresh = False

    for pattern_root, pattern in (
        (root, "chapter_*_state_adjudication.json"),
        (root / "evidence", "chapter_*_evidence.json"),
    ):
        if not pattern_root.exists():
            continue
        for path in pattern_root.glob(pattern):
            chapter_no = _parse_chapter_number(path)
            if chapter_no is None or not _chapter_in_range(
                chapter_no, completed_chapter, max_chapter
            ):
                continue
            if _remove_path(path):
                removed.add(chapter_no)

    report_index_path = root / "adjudication_report_index.json"
    if storage.exists(report_index_path):
        try:
            payload = storage.load_json(report_index_path)
        except Exception:
            _log.warning("Failed to load narrative-state report index from %s", report_index_path)
            payload = {}
        reports = payload.get("reports", []) if isinstance(payload, dict) else []
        if isinstance(reports, list):
            kept_reports: list[Any] = []
            changed = False
            for item in reports:
                chapter_no = (
                    _parse_chapter_value(item.get("chapter_number"))
                    if isinstance(item, dict)
                    else None
                )
                if chapter_no is not None and _chapter_in_range(
                    chapter_no, completed_chapter, max_chapter
                ):
                    removed.add(chapter_no)
                    changed = True
                    continue
                kept_reports.append(item)
            if changed:
                payload["reports"] = kept_reports
                storage.save_json(report_index_path, payload)

    pending_path = root / "pending_queue.json"
    if pending_path.exists():
        try:
            data = json.loads(pending_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            _log.warning("Failed to load narrative-state pending queue from %s", pending_path)
            data = {}
        raw_items = data.get("pending_items", data) if isinstance(data, dict) else data
        if isinstance(raw_items, list):
            kept_items: list[Any] = []
            changed = False
            for item in raw_items:
                chapter_no = (
                    _parse_chapter_value(item.get("chapter_number"))
                    if isinstance(item, dict)
                    else None
                )
                if chapter_no is not None and _chapter_in_range(
                    chapter_no, completed_chapter, max_chapter
                ):
                    removed.add(chapter_no)
                    changed = True
                    continue
                kept_items.append(item)
            if changed:
                atomic_write_text(
                    pending_path,
                    json.dumps({"pending_items": kept_items}, ensure_ascii=False, indent=2),
                )
                projection_needs_refresh = True

    ledger_removed = _prune_jsonl_chapter_records(
        root / "state_ledger.jsonl",
        completed_chapter,
        max_chapter,
    )
    if ledger_removed:
        removed.update(ledger_removed)
        projection_needs_refresh = True

    if projection_needs_refresh:
        try:
            from novel_forge.narrative_state.store import NarrativeStateStore

            NarrativeStateStore(layout.root).save_projection()
        except Exception:
            _log.warning(
                "Failed to refresh narrative-state projection after cleanup", exc_info=True
            )

    return removed


def _prune_chapter_audit_log(
    layout: ProjectLayout,
    completed_chapter: int,
    max_chapter: int | None = None,
) -> set[int]:
    """Prune plot-guard audit log rows for invalidated chapters."""
    return _prune_jsonl_chapter_records(
        layout.states_dir / "chapter_audit_log.jsonl",
        completed_chapter,
        max_chapter,
    )


def _prune_macro_guard_state(
    storage: FileSystemStorage,
    layout: ProjectLayout,
    completed_chapter: int,
    max_chapter: int | None = None,
) -> set[int]:
    """Remove aggregate macro-guard cooldown markers created by invalidated chapters."""
    removed: set[int] = set()
    for path in (
        layout.states_dir / "macro_guard_adjustment_state.json",
        layout.states_dir / "macro_guard_cooldown.json",
    ):
        if not storage.exists(path):
            continue
        try:
            payload = storage.load_json(path)
        except Exception:
            _log.warning("Failed to load macro-guard state from %s", path)
            continue
        chapter_no = _parse_chapter_value(payload.get("last_adjustment_chapter"))
        if chapter_no is not None and _chapter_in_range(chapter_no, completed_chapter, max_chapter):
            if _remove_path(path):
                removed.add(chapter_no)

    return removed


def _prune_progression_ledger(
    storage: FileSystemStorage,
    layout: ProjectLayout,
    completed_chapter: int,
    max_chapter: int | None = None,
) -> set[int]:
    """Prune accepted progression ledger entries newer than ``completed_chapter``."""
    path = layout.progression_ledger_path
    if not storage.exists(path):
        return set()

    try:
        payload = storage.load_json(path)
    except Exception:
        _log.warning("Failed to load progression ledger from %s", path)
        return set()
    if not isinstance(payload, dict):
        return set()

    entries = payload.get("entries")
    if not isinstance(entries, list):
        return set()

    removed: set[int] = set()
    kept_entries: list[Any] = []
    kept_chapters: list[int] = []
    for entry in entries:
        chapter_no = (
            _parse_chapter_value(entry.get("chapter_number")) if isinstance(entry, dict) else None
        )
        if chapter_no is not None and _chapter_in_range(chapter_no, completed_chapter, max_chapter):
            removed.add(chapter_no)
            continue
        kept_entries.append(entry)
        if chapter_no is not None:
            kept_chapters.append(chapter_no)

    if not removed:
        return set()

    payload["entries"] = kept_entries
    payload["last_chapter"] = max(kept_chapters, default=0)
    storage.save_json(path, payload)
    return removed


def canon_watermark(storage: FileSystemStorage, layout: ProjectLayout) -> int:
    """Return the canon current_chapter watermark for one project."""
    watermark = 0
    path = layout.canon_dir / "canon_current.json"
    if storage.exists(path):
        try:
            payload = storage.load_json(path)
        except (OSError, json.JSONDecodeError):
            _log.warning("Failed to load canon watermark from %s", path)
        else:
            if isinstance(payload, dict):
                try:
                    watermark = max(0, int(payload.get("current_chapter", 0) or 0))
                except (TypeError, ValueError):
                    watermark = 0
    return max(watermark, _story_kernel_watermark(layout))


def _story_kernel_watermark(layout: ProjectLayout) -> int:
    """Return current_chapter from the SQLite StoryKernel DB when available."""
    db_path = layout.story_kernel_db_path
    if not db_path.exists():
        return 0
    try:
        with sqlite3.connect(str(db_path)) as connection:
            row = connection.execute(
                "SELECT current_chapter, kernel_json FROM kernel_meta LIMIT 1"
            ).fetchone()
    except sqlite3.Error:
        return 0
    if row is None:
        return 0
    try:
        current_chapter = int(row[0] or 0)
    except (TypeError, ValueError):
        current_chapter = 0
    if current_chapter > 0:
        return current_chapter
    try:
        payload = json.loads(str(row[1] or "{}"))
    except json.JSONDecodeError:
        return 0
    if not isinstance(payload, dict):
        return 0
    try:
        return max(0, int(payload.get("current_chapter", 0) or 0))
    except (TypeError, ValueError):
        return 0


def finalized_chapter_numbers(layout: ProjectLayout) -> list[int]:
    """Return all finalized chapter numbers detected from chapter markdown files."""
    numbers = {
        number
        for path in layout.chapters_dir.glob("chapter_*.md")
        if (number := _parse_chapter_number(path)) is not None
    }
    return sorted(numbers)


def update_canon_watermark(
    storage: FileSystemStorage,
    layout: ProjectLayout,
    new_watermark: int,
) -> bool:
    """Update the canon current_chapter watermark.

    This is used after cleanup to ensure stale_chapter_cutoff returns None,
    so chapters no longer appear as stale in the UI.

    Args:
        storage: FileSystemStorage instance
        layout: ProjectLayout instance
        new_watermark: The new watermark value to set

    Returns:
        True if the watermark was updated, False otherwise
    """
    from novel_forge.persistence.filesystem import atomic_write_json

    path = layout.canon_dir / "canon_current.json"
    if not storage.exists(path):
        return False

    try:
        payload = storage.load_json(path)
    except (OSError, json.JSONDecodeError):
        _log.warning("Failed to load canon state from %s", path)
        return False

    if not isinstance(payload, dict):
        payload = {}

    current = payload.get("current_chapter", 0)
    if isinstance(current, int) and current == new_watermark:
        return False

    payload["current_chapter"] = new_watermark
    atomic_write_json(path, payload)
    return True


def _chapter_context_mtime(layout: ProjectLayout, chapter_number: int) -> float | None:
    for path in (
        layout.chapter_state_packet_path(chapter_number),
        layout.chapter_bridge_path(chapter_number),
        layout.chapter_plan_path(chapter_number),
        layout.chapter_review_draft_path(chapter_number),
        layout.chapter_path(chapter_number),
    ):
        if path.exists():
            return path.stat().st_mtime
    return None


def session_canon_watermark(
    storage: FileSystemStorage,
    layout: ProjectLayout,
    chapter_number: int,
) -> int | None:
    """Read ``canon_watermark`` from chapter session payload if present."""
    path = layout.chapter_session_path(chapter_number)
    if not storage.exists(path):
        return None
    try:
        payload = storage.load_json(path)
    except (OSError, json.JSONDecodeError):
        _log.warning("Failed to load chapter session from %s", path)
        return None
    if not isinstance(payload, dict):
        return None
    raw = payload.get("canon_watermark")
    if raw is None:
        return None
    try:
        return max(0, int(raw))
    except (TypeError, ValueError):
        return None


def is_checkpoint_session_fresh_for_current_chain(
    storage: FileSystemStorage,
    layout: ProjectLayout,
    chapter_number: int,
) -> bool:
    """Return True when chapter session watermark matches the current canon watermark.

    This marks checkpoints generated *after* the latest upstream rewrite.
    """
    return session_canon_watermark(storage, layout, chapter_number) == canon_watermark(
        storage, layout
    )


def _chapter_content_matches_snapshot(layout: ProjectLayout, chapter_number: int) -> bool:
    """Return True if the chapter file content matches its last-saved snapshot.

    When mtime suggests an upstream chapter was edited, this check confirms
    whether the *content* actually changed.  A pure mtime bump (editor auto-
    save, ``touch``, filesystem copy, etc.) that leaves content identical must
    NOT be treated as a structural rewrite that invalidates downstream chapters.
    """
    chapter_path = layout.chapter_path(chapter_number)
    snapshot_path = layout.states_dir / f"chapter_{chapter_number:03d}_snapshot.txt"
    if not chapter_path.exists() or not snapshot_path.exists():
        # No snapshot → cannot confirm content is unchanged → assume changed
        return False
    try:

        def _sha(p: Path) -> str:
            return hashlib.sha256(p.read_bytes()).hexdigest()

        return _sha(chapter_path) == _sha(snapshot_path)
    except OSError:
        return False


def _legacy_stale_chapters(storage: FileSystemStorage, layout: ProjectLayout) -> set[int]:
    """Return stale chapters inferred from legacy watermark/mtime heuristics."""
    watermark = canon_watermark(storage, layout)
    finalized = finalized_chapter_numbers(layout)
    stale_numbers: set[int] = {number for number in finalized if number > watermark}

    # If an earlier chapter was rewritten after a later chapter was generated,
    # the downstream chapter's planning context will now be older than its
    # current upstream.
    for chapter_number in finalized:
        if chapter_number <= 1:
            continue
        previous_path = layout.chapter_path(chapter_number - 1)
        context_mtime = _chapter_context_mtime(layout, chapter_number)
        if context_mtime is None or not previous_path.exists():
            continue
        if context_mtime < previous_path.stat().st_mtime:
            # mtime suggests the previous chapter file is newer than this
            # chapter's context artifacts.  Before flagging downstream as
            # stale, verify that the *content* of the previous chapter
            # actually differs from its archived snapshot.  A mtime-only bump
            # (editor auto-save, manual ``touch``, OS copy, etc.) with
            # unchanged content does NOT constitute a structural rewrite.
            prev_chapter_number = chapter_number - 1
            if _chapter_content_matches_snapshot(layout, prev_chapter_number):
                # Content identical to snapshot → not a real edit; skip
                continue
            stale_numbers.update(number for number in finalized if number >= chapter_number)
            break

    return stale_numbers


def _manual_revision_status_paths(layout: ProjectLayout) -> list[Path]:
    status_dir = layout.states_dir / "final_revision_status"
    if not status_dir.exists():
        return []
    return sorted(status_dir.glob("chapter_*.json"))


def _status_matches_current_chapter_text(layout: ProjectLayout, payload: dict[str, Any]) -> bool:
    chapter_number = _parse_chapter_value(payload.get("chapter_number"))
    if chapter_number is None:
        return False
    chapter_path = layout.chapter_path(chapter_number)
    if not chapter_path.exists():
        return False
    expected_hash = str(
        payload.get("current_hash") or payload.get("source_text_hash") or ""
    ).strip()
    if not expected_hash:
        return False
    try:
        return _file_sha256(chapter_path) == expected_hash
    except OSError:
        return False


def _manual_revision_stale_chapters(layout: ProjectLayout) -> set[int]:
    finalized = finalized_chapter_numbers(layout)
    stale: set[int] = set()
    for path in _manual_revision_status_paths(layout):
        payload = _load_json_path(path)
        if not isinstance(payload, dict):
            continue
        if str(payload.get("status") or "applied") != "applied":
            continue
        if not _status_matches_current_chapter_text(layout, payload):
            continue
        if not bool(
            payload.get("requires_state_reextract")
            or payload.get("requires_canon_reextract")
            or payload.get("requires_reevaluation")
        ):
            continue
        chapter_number = _parse_chapter_value(payload.get("chapter_number"))
        if chapter_number is None:
            continue
        scope = _coerce_scope(payload.get("scope", InvalidationScope.DOWNSTREAM.value))
        raw_affected = payload.get("affected_chapters")
        affected: tuple[int, ...] = ()
        if isinstance(raw_affected, list):
            affected = tuple(
                number
                for item in raw_affected
                if (number := _parse_chapter_value(item)) is not None
            )
        if not affected:
            _start, _end, affected = resolve_manual_invalidation_range(
                scope=scope,
                chapter_number=chapter_number,
                layout=layout,
                finalized_numbers=finalized,
            )
        stale.update(affected)
    return stale


def _resolve_upstream_revision_range(
    storage: FileSystemStorage,
    layout: ProjectLayout,
    *,
    scope: RevisionScope,
    from_chapter: int | None = None,
    finalized_numbers: list[int] | None = None,
) -> tuple[int | None, int | None, tuple[int, ...]]:
    finalized = (
        finalized_numbers if finalized_numbers is not None else finalized_chapter_numbers(layout)
    )
    if not finalized or scope is RevisionScope.LOCAL:
        return None, None, ()

    if scope is RevisionScope.WHOLE_BOOK:
        return min(finalized), max(finalized), tuple(finalized)

    start = from_chapter
    if start is None:
        start = max(1, canon_watermark(storage, layout) + 1)
    if start <= 0:
        start = 1

    end: int | None = None
    if scope is RevisionScope.VOLUME:
        end = _volume_end_for_chapter(layout, start)
        if end is None:
            end = max(finalized)

    affected = tuple(
        number for number in finalized if number >= start and (end is None or number <= end)
    )
    if not affected:
        return None, end, ()
    return min(affected), end, affected


def _preview_paths_for_chapters(layout: ProjectLayout, chapters: tuple[int, ...]) -> list[str]:
    paths: set[Path] = set()
    for chapter_number in chapters:
        for path in (
            layout.chapter_checkpoint_path(chapter_number),
            layout.chapter_session_path(chapter_number),
            layout.chapter_review_progress_path(chapter_number),
            layout.chapter_source_slice_path(chapter_number),
            layout.chapter_state_packet_path(chapter_number),
            layout.chapter_bridge_path(chapter_number),
            layout.chapter_plan_path(chapter_number),
            layout.chapter_review_draft_path(chapter_number),
            layout.alignment_report_path(chapter_number),
            layout.continuity_report_path(chapter_number),
            layout.chapter_causal_report_path(chapter_number),
            layout.quality_gate_report_path(chapter_number),
            layout.contract_execution_report_path(chapter_number),
            layout.reports_dir / f"chapter_{chapter_number:03d}_tts_metadata.json",
            layout.tts_dubbing_script_path(chapter_number),
            layout.tts_audio_dir(chapter_number),
            layout.tts_take_dir(chapter_number),
            layout.tts_audio_result_path(chapter_number),
            layout.tts_sound_resolution_path(chapter_number),
            layout.tts_sound_generation_report_path(chapter_number),
            layout.tts_speech_timeline_path(chapter_number),
            layout.tts_mix_plan_path(chapter_number),
            layout.tts_mix_render_report_path(chapter_number),
            layout.tts_audio_quality_report_path(chapter_number),
        ):
            if path.exists():
                paths.add(path)
    return [str(path) for path in sorted(paths)]


def preview_upstream_artifact_impact(
    storage: FileSystemStorage,
    layout: ProjectLayout,
    *,
    artifact_kind: UpstreamArtifactKind | str,
    scope: RevisionScope | str = RevisionScope.FORWARD_ONLY,
    from_chapter: int | None = None,
) -> dict[str, Any]:
    """Return affected chapters and paths for an upstream artifact change.

    This function is intentionally read-only. It must not delete, touch, or prune files.
    """
    kind = (
        artifact_kind
        if isinstance(artifact_kind, UpstreamArtifactKind)
        else _coerce_artifact_kind(artifact_kind)
    )
    if kind is None:
        raise ValueError(f"Unknown upstream artifact kind: {artifact_kind}")
    revision_scope = scope if isinstance(scope, RevisionScope) else _coerce_revision_scope(scope)
    range_start, range_end, affected = _resolve_upstream_revision_range(
        storage,
        layout,
        scope=revision_scope,
        from_chapter=from_chapter,
    )
    return {
        "artifact_kind": kind.value,
        "scope": revision_scope.value,
        "from_chapter": from_chapter,
        "range_start": range_start,
        "range_end": range_end,
        "affected_chapters": list(affected),
        "paths": _preview_paths_for_chapters(layout, affected),
    }


def _remove_chapter_source_slices(layout: ProjectLayout, chapters: tuple[int, ...]) -> set[int]:
    removed: set[int] = set()
    for chapter_number in chapters:
        if _remove_path(layout.chapter_source_slice_path(chapter_number)):
            removed.add(chapter_number)
    return removed


def record_upstream_artifact_revision(
    storage: FileSystemStorage,
    layout: ProjectLayout,
    *,
    artifact_kind: UpstreamArtifactKind | str,
    previous_hash: str = "",
    current_hash: str | None = None,
    scope: RevisionScope | str = RevisionScope.FORWARD_ONLY,
    from_chapter: int | None = None,
    reason: str = "upstream_artifact_revision",
    apply_invalidation: bool = True,
    finalized_numbers: list[int] | None = None,
) -> UpstreamArtifactRevisionRecord:
    """Persist an upstream artifact revision marker and optionally invalidate artifacts."""
    kind = (
        artifact_kind
        if isinstance(artifact_kind, UpstreamArtifactKind)
        else _coerce_artifact_kind(artifact_kind)
    )
    if kind is None:
        raise ValueError(f"Unknown upstream artifact kind: {artifact_kind}")
    revision_scope = scope if isinstance(scope, RevisionScope) else _coerce_revision_scope(scope)
    artifact_path = _artifact_path(layout, kind)
    effective_current_hash = (
        current_hash if current_hash is not None else _optional_file_hash(artifact_path)
    )
    range_start, range_end, affected = _resolve_upstream_revision_range(
        storage,
        layout,
        scope=revision_scope,
        from_chapter=from_chapter,
        finalized_numbers=finalized_numbers,
    )

    invalidated: set[int] = set()
    if apply_invalidation and affected:
        invalidated.update(
            invalidate_downstream_generated_artifacts(
                storage,
                layout,
                completed_chapter=min(affected) - 1,
                delete_chapter_files=False,
                max_chapter=max(affected),
            )
        )
        invalidated.update(_remove_chapter_source_slices(layout, affected))

    record = UpstreamArtifactRevisionRecord(
        artifact_kind=kind,
        revision_id=datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ"),
        previous_hash=str(previous_hash or ""),
        current_hash=str(effective_current_hash or ""),
        scope=revision_scope,
        from_chapter=from_chapter,
        range_start=range_start,
        range_end=range_end,
        affected_chapters=affected,
        invalidated_chapters=tuple(sorted(invalidated)),
        reason=reason,
    )
    status_dir = _upstream_revision_status_dir(layout)
    status_dir.mkdir(parents=True, exist_ok=True)
    payload = record.to_payload()
    if finalized_numbers is not None:
        # Isolated candidates have no chapter files. Freeze the approved impact
        # so future chapters do not become stale merely because they appear.
        payload["affected_chapters_fixed"] = True
    atomic_write_text(
        _upstream_revision_status_path(layout, kind),
        json.dumps(payload, ensure_ascii=False, indent=2),
    )
    return record


def _status_matches_current_artifact(layout: ProjectLayout, payload: dict[str, Any]) -> bool:
    kind = _coerce_artifact_kind(payload.get("artifact_kind"))
    if kind is None:
        return False
    expected_hash = str(payload.get("current_hash") or "").strip()
    if not expected_hash:
        return False
    return _optional_file_hash(_artifact_path(layout, kind)) == expected_hash


def _upstream_artifact_stale_chapters(
    storage: FileSystemStorage,
    layout: ProjectLayout,
) -> set[int]:
    status_dir = _upstream_revision_status_dir(layout)
    if not status_dir.exists():
        return set()
    finalized = finalized_chapter_numbers(layout)
    stale: set[int] = set()
    for path in sorted(status_dir.glob("*.json")):
        payload = _load_json_path(path)
        if not isinstance(payload, dict):
            continue
        if str(payload.get("status") or "applied") != "applied":
            continue
        if not _status_matches_current_artifact(layout, payload):
            continue
        raw_affected = payload.get("affected_chapters")
        affected: tuple[int, ...] = ()
        if isinstance(raw_affected, list):
            affected = tuple(
                number
                for item in raw_affected
                if (number := _parse_chapter_value(item)) is not None
            )
        if not affected and not payload.get("affected_chapters_fixed"):
            _start, _end, affected = _resolve_upstream_revision_range(
                storage,
                layout,
                scope=_coerce_revision_scope(payload.get("scope")),
                from_chapter=_parse_chapter_value(payload.get("from_chapter")),
                finalized_numbers=finalized,
            )
        stale.update(affected)
    return stale


def record_text_revision(
    layout: ProjectLayout,
    *,
    chapter_number: int,
    source: str,
    previous_hash: str = "",
    current_hash: str = "",
    scope: RevisionScope | str = RevisionScope.LOCAL,
    reason: str = "",
) -> TextRevisionRecord:
    """Persist lightweight metadata for an automated chapter text rewrite."""
    revision_scope = scope if isinstance(scope, RevisionScope) else _coerce_revision_scope(scope)
    safe_source = re.sub(r"[^a-zA-Z0-9_.-]+", "_", str(source or "text_revision")).strip("_")
    if not safe_source:
        safe_source = "text_revision"
    record = TextRevisionRecord(
        chapter_number=chapter_number,
        revision_id=datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ"),
        source=safe_source,
        previous_hash=str(previous_hash or ""),
        current_hash=str(current_hash or ""),
        scope=revision_scope,
        reason=reason,
    )
    status_dir = _text_revision_status_dir(layout)
    status_dir.mkdir(parents=True, exist_ok=True)
    status_path = status_dir / f"chapter_{chapter_number:03d}_{safe_source}.json"
    atomic_write_text(status_path, json.dumps(record.to_payload(), ensure_ascii=False, indent=2))
    return record


def revision_stale_chapters(storage: FileSystemStorage, layout: ProjectLayout) -> set[int]:
    """Explicit revision evidence, without legacy missing-watermark heuristics."""
    return _manual_revision_stale_chapters(layout).union(
        _upstream_artifact_stale_chapters(storage, layout)
    )


def scoped_stale_chapters(storage: FileSystemStorage, layout: ProjectLayout) -> set[int]:
    """Return the exact chapter set considered stale for UI and preflight checks."""
    return _legacy_stale_chapters(storage, layout).union(revision_stale_chapters(storage, layout))


def is_chapter_stale(
    storage: FileSystemStorage,
    layout: ProjectLayout,
    chapter_number: int,
) -> bool:
    return chapter_number in scoped_stale_chapters(storage, layout)


def stale_chapter_cutoff(storage: FileSystemStorage, layout: ProjectLayout) -> int | None:
    """Return the earliest stale chapter for legacy cutoff-style callers."""
    stale_numbers = scoped_stale_chapters(storage, layout)
    return min(stale_numbers) if stale_numbers else None


def _combined_hash(paths: list[Path]) -> str:
    pieces: list[str] = []
    for path in sorted(paths):
        if not path.exists() or not path.is_file():
            continue
        try:
            pieces.append(f"{path.name}:{_file_sha256(path)}")
        except OSError:
            continue
    return _text_sha256("\n".join(pieces)) if pieces else ""


def _optional_file_hash(path: Path) -> str:
    if not path.exists() or not path.is_file():
        return ""
    try:
        return _file_sha256(path)
    except OSError:
        return ""


def _artifact_path(layout: ProjectLayout, artifact_kind: UpstreamArtifactKind) -> Path:
    if artifact_kind is UpstreamArtifactKind.SPEC:
        return layout.spec_path
    if artifact_kind is UpstreamArtifactKind.STORY_BIBLE:
        return layout.bible_path
    if artifact_kind is UpstreamArtifactKind.CHARACTER_BIBLE:
        return layout.characters_path
    if artifact_kind is UpstreamArtifactKind.OUTLINE:
        return layout.outline_path
    if artifact_kind is UpstreamArtifactKind.STYLE_PROFILE:
        return layout.style_profile_path
    if artifact_kind is UpstreamArtifactKind.NARRATIVE_BLUEPRINT:
        return layout.blueprint_path
    if artifact_kind is UpstreamArtifactKind.CHAPTER_CONTRACTS:
        return layout.plans_dir / "chapter_contracts.json"
    if artifact_kind is UpstreamArtifactKind.NARRATIVE_CONTRACT:
        return layout.narrative_contract_path
    if artifact_kind is UpstreamArtifactKind.EDITORIAL_CONTRACT:
        return layout.editorial_contract_path
    raise ValueError(f"Unsupported artifact kind: {artifact_kind}")


def _core_artifact_hashes(layout: ProjectLayout) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for artifact_kind in UpstreamArtifactKind:
        hashes[artifact_kind.value] = _optional_file_hash(_artifact_path(layout, artifact_kind))
    return hashes


def _source_artifact_hashes(layout: ProjectLayout) -> dict[str, str]:
    if not layout.source_artifacts_dir.exists():
        return {}
    hashes: dict[str, str] = {}
    for path in sorted(layout.source_artifacts_dir.glob("*.json")):
        if path.is_file():
            hashes[path.stem] = _optional_file_hash(path)
    return hashes


def _upstream_revision_status_dir(layout: ProjectLayout) -> Path:
    return layout.states_dir / "upstream_revision_status"


def _upstream_revision_status_path(
    layout: ProjectLayout,
    artifact_kind: UpstreamArtifactKind,
) -> Path:
    return _upstream_revision_status_dir(layout) / f"{artifact_kind.value}.json"


def _text_revision_status_dir(layout: ProjectLayout) -> Path:
    return layout.states_dir / "text_revision_status"


def build_upstream_revision_fingerprint(
    storage: FileSystemStorage,
    layout: ProjectLayout,
    chapter_number: int,
) -> dict[str, Any]:
    """Capture the upstream revision state a chapter run depends on."""
    previous_chapter = chapter_number - 1
    previous_hash = ""
    if previous_chapter >= 1 and layout.chapter_path(previous_chapter).exists():
        try:
            previous_hash = _file_sha256(layout.chapter_path(previous_chapter))
        except OSError:
            previous_hash = ""

    upstream_status_paths: list[Path] = []
    for path in _manual_revision_status_paths(layout):
        chapter_no = _parse_chapter_number(path)
        if chapter_no is not None and chapter_no < chapter_number:
            upstream_status_paths.append(path)
    upstream_artifact_dir = _upstream_revision_status_dir(layout)
    if upstream_artifact_dir.exists():
        upstream_status_paths.extend(
            path for path in sorted(upstream_artifact_dir.glob("*.json")) if path.is_file()
        )

    projection_path = layout.narrative_state_dir / "story_state_projection.json"
    ledger_path = layout.narrative_state_dir / "state_ledger.jsonl"
    chapter_source_slice_path = layout.chapter_source_slice_path(chapter_number)
    return {
        "schema_version": "1.1",
        "chapter_number": chapter_number,
        "previous_chapter": previous_chapter if previous_chapter >= 1 else None,
        "previous_chapter_hash": previous_hash,
        "canon_watermark": canon_watermark(storage, layout),
        "narrative_projection_hash": _optional_file_hash(projection_path),
        "state_ledger_hash": _optional_file_hash(ledger_path),
        "upstream_revision_status_hash": _combined_hash(upstream_status_paths),
        "core_artifact_hashes": _core_artifact_hashes(layout),
        "source_artifact_hashes": _source_artifact_hashes(layout),
        "chapter_source_slice_hash": _optional_file_hash(chapter_source_slice_path),
    }


def assert_upstream_revision_fingerprint_current(
    storage: FileSystemStorage,
    layout: ProjectLayout,
    chapter_number: int,
    expected: dict[str, Any] | None,
) -> None:
    """Raise when a chapter run is about to persist against stale upstream context."""
    if not expected:
        return
    current = build_upstream_revision_fingerprint(storage, layout, chapter_number)
    comparable_keys = [key for key in current if key in expected and key != "schema_version"]
    if comparable_keys and all(expected.get(key) == current.get(key) for key in comparable_keys):
        return
    raise RuntimeError(
        "上游章节或叙事状态已在本章生成期间发生变化，当前结果已转为待复核草稿，不会写入正式状态链。"
    )


def save_orphaned_chapter_draft(
    layout: ProjectLayout,
    chapter_number: int,
    text: str,
    *,
    reason: str,
    expected_fingerprint: dict[str, Any] | None = None,
) -> Path:
    """Persist generated text that failed the final upstream freshness barrier."""
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    root = layout.states_dir / "orphan_chapter_drafts" / f"chapter_{chapter_number:03d}"
    root.mkdir(parents=True, exist_ok=True)
    draft_path = root / f"{timestamp}.md"
    meta_path = root / f"{timestamp}.json"
    atomic_write_text(draft_path, text)
    atomic_write_text(
        meta_path,
        json.dumps(
            {
                "schema_version": "1.0",
                "timestamp": _utc_timestamp(),
                "chapter_number": chapter_number,
                "reason": reason,
                "expected_fingerprint": expected_fingerprint or {},
            },
            ensure_ascii=False,
            indent=2,
        ),
    )
    return draft_path


def _prune_revision_marker_payload(
    storage: FileSystemStorage,
    path: Path,
    payload: dict[str, Any],
    *,
    completed_chapter: int,
    max_chapter: int | None,
    dynamic_resolved: bool,
) -> set[int]:
    raw_affected = payload.get("affected_chapters")
    removed: set[int] = set()
    if isinstance(raw_affected, list):
        remaining: list[int] = []
        for item in raw_affected:
            number = _parse_chapter_value(item)
            if number is not None and _chapter_in_range(number, completed_chapter, max_chapter):
                removed.add(number)
                continue
            if number is not None:
                remaining.append(number)
        if removed:
            if remaining:
                payload["affected_chapters"] = remaining
            else:
                payload["affected_chapters"] = []
                payload["status"] = "resolved"
            storage.save_json(path, payload)
            return removed

    if dynamic_resolved:
        payload["status"] = "resolved"
        storage.save_json(path, payload)
    return removed


def _prune_upstream_revision_status(
    storage: FileSystemStorage,
    layout: ProjectLayout,
    completed_chapter: int,
    max_chapter: int | None = None,
) -> set[int]:
    status_dir = _upstream_revision_status_dir(layout)
    if not status_dir.exists():
        return set()
    removed: set[int] = set()
    for path in sorted(status_dir.glob("*.json")):
        payload = _load_json_path(path)
        if not isinstance(payload, dict):
            continue
        if str(payload.get("status") or "applied") != "applied":
            continue

        scope = _coerce_revision_scope(payload.get("scope"))
        start = _parse_chapter_value(payload.get("range_start"))
        if start is None:
            start = _parse_chapter_value(payload.get("from_chapter"))
        if start is None and scope is RevisionScope.WHOLE_BOOK:
            start = 1

        dynamic_resolved = (
            max_chapter is None
            and start is not None
            and _chapter_in_range(start, completed_chapter, max_chapter)
        )
        removed.update(
            _prune_revision_marker_payload(
                storage,
                path,
                payload,
                completed_chapter=completed_chapter,
                max_chapter=max_chapter,
                dynamic_resolved=dynamic_resolved,
            )
        )
    return removed


def _prune_manual_revision_status(
    storage: FileSystemStorage,
    layout: ProjectLayout,
    completed_chapter: int,
    max_chapter: int | None = None,
) -> set[int]:
    removed: set[int] = set()
    for path in _manual_revision_status_paths(layout):
        payload = _load_json_path(path)
        if not isinstance(payload, dict):
            continue
        if str(payload.get("status") or "applied") != "applied":
            continue
        chapter_number = _parse_chapter_value(payload.get("chapter_number"))
        if chapter_number is None or _chapter_in_range(
            chapter_number, completed_chapter, max_chapter
        ):
            continue

        scope = _coerce_scope(payload.get("scope", InvalidationScope.DOWNSTREAM.value))
        dynamic_resolved = max_chapter is None and scope is not InvalidationScope.NONE
        removed.update(
            _prune_revision_marker_payload(
                storage,
                path,
                payload,
                completed_chapter=completed_chapter,
                max_chapter=max_chapter,
                dynamic_resolved=dynamic_resolved,
            )
        )
    return removed


def invalidate_downstream_generated_artifacts(
    storage: FileSystemStorage,
    layout: ProjectLayout,
    *,
    completed_chapter: int,
    delete_chapter_files: bool = False,
    max_chapter: int | None = None,
) -> list[int]:
    """Clear resumable/generated downstream artifacts after an upstream rewrite.

    Finalized chapter markdown files are preserved by default so the UI can still surface
    them as stale historical output. Set delete_chapter_files=True to also remove
    the chapter .md files themselves.

    Pending checkpoints and non-finalized planning/review artifacts are always removed
    to prevent the app from resuming an outdated downstream chain.
    """

    candidate_numbers: set[int] = set()
    candidate_paths: dict[int, set[Path]] = {}
    for root, pattern in (
        (layout.states_dir, "chapter_*"),
        (layout.states_dir / "chapter_publications", "chapter_*.json"),
        (layout.states_dir / "final_revision_status", "chapter_*"),
        (layout.states_dir / "final_revision_versions", "chapter_*"),
        (layout.states_dir / "review_progress_rollbacks", "chapter_*"),
        (layout.plans_dir, "chapter_*"),
        (layout.reports_dir, "chapter_*"),
        (layout.reports_dir / "revisions", "chapter_*"),
        (layout.chapters_dir, "chapter_*.md"),
        (layout.drafts_dir, "chapter_*"),
        (layout.narrative_state_dir, "chapter_*"),
        (layout.narrative_state_dir / "evidence", "chapter_*"),
        (layout.states_dir / "word_count_archive_gate", "chapter_*.json"),
        (layout.tts_dir / "scripts", "chapter_*_script.json"),
        (layout.tts_dir / "audio", "chapter_*"),
        (layout.tts_dir / "results", "chapter_*_audio.json"),
        (layout.tts_dir / "sound_resolutions", "chapter_*.json"),
        (layout.tts_dir / "sound_generation", "chapter_*.json"),
    ):
        if not root.exists():
            continue
        for path in root.glob(pattern):
            number = _parse_chapter_number(path)
            if number is not None and _chapter_in_range(number, completed_chapter, max_chapter):
                candidate_numbers.add(number)
                candidate_paths.setdefault(number, set()).add(path)

    for root, pattern, prefix in (
        (layout.states_dir, "macro_guard_report_ch*.json", "macro_guard_report_ch"),
        (layout.states_dir, "macro_guard_hint_ch*.json", "macro_guard_hint_ch"),
        (layout.states_dir, "macro_guard_alert_ch*.json", "macro_guard_alert_ch"),
        (layout.states_dir, "reading_power_window_ch*.json", "reading_power_window_ch"),
        (layout.states_dir, "reading_power_gate_ch*.json", "reading_power_gate_ch"),
        (
            layout.states_dir,
            "reading_power_constraints_ch*.json",
            "reading_power_constraints_ch",
        ),
        (layout.states_dir, "targeted_repairs_ch*.json", "targeted_repairs_ch"),
    ):
        if not root.exists():
            continue
        for path in root.glob(pattern):
            number = _parse_prefixed_chapter_number(path, prefix)
            if number is not None and _chapter_in_range(number, completed_chapter, max_chapter):
                candidate_numbers.add(number)
                candidate_paths.setdefault(number, set()).add(path)

    # Helper: list of per-chapter artifact paths to delete
    def _all_artifact_paths(ch: int) -> tuple[Path, ...]:
        return (
            layout.chapter_checkpoint_path(ch),
            layout.chapter_session_path(ch),
            layout.chapter_meta_path(ch),
            layout.chapter_state_packet_path(ch),
            layout.chapter_exit_state_path(ch),
            layout.chapter_canon_outcome_path(ch),
            layout.chapter_publication_path(ch),
            layout.chapter_bridge_path(ch),
            layout.chapter_plan_path(ch),
            layout.chapter_review_draft_path(ch),
            layout.creative_report_path(ch),
            layout.alignment_report_path(ch),
            layout.chapter_repair_report_path(ch),
            layout.continuity_report_path(ch),
            layout.chapter_causal_report_path(ch),
            layout.repair_plan_path(ch),
            layout.repair_metrics_report_path(ch),
            layout.guard_report_path(ch),
            layout.eval_report_path(ch),
            layout.chapter_memory_diagnostics_path(ch),
            layout.critic_report_path(ch),
            layout.quality_gate_report_path(ch),
            layout.reading_power_report_path(ch),
            layout.humanize_report_path(ch),
            layout.knowledge_boundary_report_path(ch),
            layout.state_adjudication_report_path(ch),
            layout.contract_execution_report_path(ch),
            layout.expression_repetition_report_path(ch),
            layout.arc_liveness_report_path(ch),
            layout.milestone_window_report_path(ch),
            layout.stage_visibility_diagnostics_path(ch),
            layout.reports_dir / f"chapter_{ch:03d}_tts_metadata.json",
            layout.tts_dubbing_script_path(ch),
            layout.tts_audio_dir(ch),
            layout.tts_take_dir(ch),
            layout.tts_audio_result_path(ch),
            layout.tts_sound_resolution_path(ch),
            layout.tts_sound_generation_report_path(ch),
            layout.tts_speech_timeline_path(ch),
            layout.tts_mix_plan_path(ch),
            layout.tts_mix_render_report_path(ch),
            layout.tts_audio_quality_report_path(ch),
            layout.reports_dir / "revisions" / f"chapter_{ch:03d}_humanize_layer.json",
            layout.reports_dir / f"chapter_{ch:03d}_guidance_plan.json",
            layout.reports_dir / f"chapter_{ch:03d}_guidance_contract_audit.json",
            layout.states_dir / f"chapter_{ch:03d}_repair_continuity_progress.json",
            layout.states_dir / f"chapter_{ch:03d}_repair_causal_progress.json",
            layout.states_dir / f"chapter_{ch:03d}_causal_repair_attempts.json",
            layout.states_dir / f"chapter_{ch:03d}_rewrite_context.json",
            layout.states_dir / f"chapter_{ch:04d}_before_repair_checkpoint.json",
            layout.states_dir / f"chapter_{ch}_memory_pending.json",
            layout.states_dir / f"chapter_{ch:03d}_memory_pending.json",
            layout.states_dir / "word_count_archive_gate" / f"chapter_{ch:03d}.json",
            layout.states_dir / f"macro_guard_report_ch{ch}.json",
            layout.states_dir / f"macro_guard_hint_ch{ch}.json",
            layout.states_dir / f"macro_guard_alert_ch{ch}.json",
            layout.states_dir / f"reading_power_window_ch{ch}.json",
            layout.states_dir / f"reading_power_gate_ch{ch}.json",
            layout.states_dir / f"reading_power_constraints_ch{ch}.json",
            layout.states_dir / f"targeted_repairs_ch{ch}.json",
            layout.narrative_state_dir / f"chapter_{ch:03d}_state_adjudication.json",
            layout.narrative_state_dir / "evidence" / f"chapter_{ch:03d}_evidence.json",
            layout.states_dir / "final_revision_status" / f"chapter_{ch:03d}.json",
            layout.states_dir / "final_revision_versions" / f"chapter_{ch:03d}",
        )

    invalidated: list[int] = []
    for chapter_number in sorted(candidate_numbers):
        chapter_path = layout.chapter_path(chapter_number)
        has_final_text = chapter_path.exists()
        chapter_changed = False

        # A preserved chapter file may still be stale historical output.  Its
        # script/audio must never remain reusable after upstream invalidation.
        if invalidate_chapter_tts_artifacts(layout, chapter_number):
            chapter_changed = True

        # Always remove checkpoint / session / review-progress files
        for path in (
            layout.chapter_checkpoint_path(chapter_number),
            layout.chapter_session_path(chapter_number),
            layout.chapter_review_progress_path(chapter_number),
        ):
            if storage.exists(path):
                path.unlink()
                chapter_changed = True

        if not has_final_text or delete_chapter_files:
            # Delete the finalized chapter file itself
            if _remove_path(chapter_path):
                chapter_changed = True

            # Delete all discovered chapter-scoped artifacts, including newly
            # added report/state files that have not yet been registered below.
            for path in sorted(candidate_paths.get(chapter_number, set())):
                if _remove_path(path):
                    chapter_changed = True

            # Delete all per-chapter artifacts
            for path in _all_artifact_paths(chapter_number):
                if _remove_path(path):
                    chapter_changed = True

            # Delete the entire draft directory (v0_draft.md, v*_edited.md, etc.)
            draft_dir = layout.chapter_draft_dir(chapter_number)
            if _remove_path(draft_dir):
                chapter_changed = True

            # Delete snapshot files in states/
            snapshot_path = layout.states_dir / f"chapter_{chapter_number:03d}_snapshot.txt"
            if _remove_path(snapshot_path):
                chapter_changed = True

            macro_hint_path = layout.states_dir / f"macro_guard_hint_ch{chapter_number}.json"
            if _remove_path(macro_hint_path):
                chapter_changed = True

            macro_alert_path = layout.states_dir / f"macro_guard_alert_ch{chapter_number}.json"
            if _remove_path(macro_alert_path):
                chapter_changed = True

            rollback_dir = layout.states_dir / "review_progress_rollbacks"
            for path in rollback_dir.glob(f"chapter_{chapter_number:03d}_*.json"):
                if _remove_path(path):
                    chapter_changed = True

        if chapter_changed:
            invalidated.append(chapter_number)

    pruned_forbidden_chapters = _prune_forbidden_repetition_index(
        storage,
        layout,
        completed_chapter,
        max_chapter,
    )

    # Prune chapter-based aggregate progress as well (prevents stale planning hints
    # from leaking into regenerated chapters).
    pruned_chapters = _prune_element_progress(storage, layout, completed_chapter, max_chapter)
    pruned_narrative_state = _prune_narrative_state(
        storage,
        layout,
        completed_chapter,
        max_chapter,
    )
    pruned_audit_log = _prune_chapter_audit_log(layout, completed_chapter, max_chapter)
    pruned_macro_guard = _prune_macro_guard_state(storage, layout, completed_chapter, max_chapter)
    pruned_progression_ledger = _prune_progression_ledger(
        storage,
        layout,
        completed_chapter,
        max_chapter,
    )
    pruned_upstream_revisions = _prune_upstream_revision_status(
        storage,
        layout,
        completed_chapter,
        max_chapter,
    )
    pruned_manual_revisions = _prune_manual_revision_status(
        storage,
        layout,
        completed_chapter,
        max_chapter,
    )

    aggregate_pruned = (
        set(pruned_forbidden_chapters)
        .union(pruned_chapters)
        .union(pruned_narrative_state)
        .union(pruned_audit_log)
        .union(pruned_macro_guard)
        .union(pruned_progression_ledger)
        .union(pruned_upstream_revisions)
        .union(pruned_manual_revisions)
    )
    if aggregate_pruned:
        invalidated = sorted(set(invalidated).union(aggregate_pruned))

    return invalidated


def _prune_forbidden_repetition_index(
    storage: FileSystemStorage,
    layout: ProjectLayout,
    completed_chapter: int,
    max_chapter: int | None = None,
) -> set[int]:
    """Remove entries for chapters > completed_chapter from the forbidden repetition index."""
    index_path = layout.forbidden_repetition_index_path
    if not storage.exists(index_path):
        return set()
    removed: set[int] = set()
    try:
        raw = storage.load_json(index_path)
        if not isinstance(raw, dict):
            return set()
        pruned: dict[str, Any] = {}
        for key, value in raw.items():
            chapter_no = _parse_chapter_value(key)
            if chapter_no is not None and _chapter_in_range(
                chapter_no, completed_chapter, max_chapter
            ):
                removed.add(chapter_no)
                continue
            pruned[str(key)] = value
        if len(pruned) < len(raw):
            storage.save_json(index_path, pruned)
    except Exception:
        _log.warning("Failed to prune forbidden repetition index at %s", index_path)
        return set()
    return removed


def clean_all_stale_chapters(
    storage: FileSystemStorage,
    layout: ProjectLayout,
    *,
    keep_chapter_0: bool = False,
) -> list[int]:
    """Clean all chapters that are downstream of the current canon watermark.

    This function removes all chapter files and their associated artifacts
    for chapters that were generated based on an older canon state.

    Args:
        storage: FileSystemStorage instance
        layout: ProjectLayout instance
        keep_chapter_0: If True, only removes chapters > 0 (always True in practice)

    Returns:
        List of chapter numbers that were removed
    """
    watermark = canon_watermark(storage, layout)
    if watermark <= 0:
        return []

    invalidated = invalidate_downstream_generated_artifacts(
        storage,
        layout,
        completed_chapter=watermark,
        delete_chapter_files=True,
    )
    return invalidated


async def _rollback_story_kernel_for_regeneration(
    layout: ProjectLayout,
    *,
    project_id: str,
    completed_chapter: int,
    from_chapter: int,
) -> StoryKernel | None:
    """Rollback the SQLite StoryKernel on the caller's event loop."""

    from novel_forge.story_kernel.store import StoryKernelStore

    db_path = layout.story_kernel_db_path
    if not db_path.exists():
        return None

    store = StoryKernelStore(str(db_path))
    try:
        await store.init_db()
        try:
            kernel = await store.load_kernel(project_id)
        except ValueError:
            raise ChapterCleanupConvergenceError(
                "StoryKernel 缺少作品记录，无法证明清理后的 Canon 水位。",
                failed_stage="story_kernel",
            ) from None

        snapshot = store.snapshot_path(completed_chapter)
        if snapshot.exists():
            restored = await store.rollback_to(completed_chapter)
        elif kernel.current_chapter == completed_chapter:
            # Idempotent retry after a previously verified rollback.  The
            # target snapshot may have been pruned later by retention policy.
            restored = kernel
        else:
            legacy_snapshot = layout.canon_dir / f"canon_v{completed_chapter}.json"
            if legacy_snapshot.exists():
                from novel_forge.story_kernel.schemas import StoryKernel

                restored = StoryKernel.model_validate_json(
                    legacy_snapshot.read_text(encoding="utf-8")
                )
                if restored.current_chapter != completed_chapter:
                    raise ChapterCleanupConvergenceError(
                        "Canon 快照水位与清理目标不匹配。",
                        failed_stage="story_kernel",
                    )
                await store.save_kernel(restored)
            else:
                raise ChapterCleanupConvergenceError(
                    f"缺少第 {completed_chapter} 章 StoryKernel 快照，"
                    "无法在不保留后续事实的前提下安全回滚。",
                    failed_stage="story_kernel",
                )

        for snapshot_number in store.list_snapshots():
            if snapshot_number >= from_chapter:
                store.snapshot_path(snapshot_number).unlink(missing_ok=True)
        return restored
    finally:
        await store.close()


def _remove_legacy_canon_snapshots(layout: ProjectLayout, *, from_chapter: int) -> None:
    """Remove JSON Canon snapshots that belong to the invalidated range."""

    for path in layout.canon_dir.glob("canon_v*.json"):
        match = re.fullmatch(r"canon_v(\d+)", path.stem)
        if match is not None and int(match.group(1)) >= from_chapter:
            path.unlink(missing_ok=True)


def _chapter_cleanup_convergence_issues(
    storage: FileSystemStorage,
    layout: ProjectLayout,
    *,
    project_id: str,
    completed_chapter: int,
    from_chapter: int,
) -> list[str]:
    """Return restart-blocking inconsistencies left after chapter cleanup."""

    issues: list[str] = []
    from novel_forge.core.exceptions import StorageError

    canon_path = layout.canon_dir / "canon_current.json"
    canon_payload: dict[str, Any] | None = None
    if storage.exists(canon_path):
        try:
            payload = storage.load_json(canon_path)
            if not isinstance(payload, dict):
                raise TypeError("Canon JSON 顶层不是对象")
            canon_payload = payload
            json_watermark = int(payload.get("current_chapter", 0) or 0)
        except (
            AttributeError,
            OSError,
            StorageError,
            TypeError,
            ValueError,
            json.JSONDecodeError,
        ):
            issues.append("Canon JSON 无法读取")
        else:
            if json_watermark != completed_chapter:
                issues.append(
                    f"Canon JSON 水位为 {json_watermark}，期望 {completed_chapter}"
                )

    db_path = layout.story_kernel_db_path
    if db_path.exists():
        try:
            with sqlite3.connect(str(db_path)) as connection:
                row = connection.execute(
                    "SELECT current_chapter, kernel_json FROM kernel_meta "
                    "WHERE project_id = ? LIMIT 1",
                    (project_id,),
                ).fetchone()
        except sqlite3.Error as exc:
            issues.append(f"StoryKernel 无法读取：{exc}")
        else:
            if row is None:
                issues.append("StoryKernel 缺少作品记录")
            else:
                try:
                    column_watermark = int(row[0] or 0)
                    kernel_payload = json.loads(str(row[1] or "{}"))
                    if not isinstance(kernel_payload, dict):
                        raise TypeError("StoryKernel JSON 顶层不是对象")
                    kernel_watermark = int(kernel_payload.get("current_chapter", 0) or 0)
                except (AttributeError, TypeError, ValueError, json.JSONDecodeError):
                    issues.append("StoryKernel 水位记录无法解析")
                else:
                    if column_watermark != completed_chapter:
                        issues.append(
                            f"StoryKernel 表水位为 {column_watermark}，"
                            f"期望 {completed_chapter}"
                        )
                    if kernel_watermark != completed_chapter:
                        issues.append(
                            f"StoryKernel JSON 水位为 {kernel_watermark}，"
                            f"期望 {completed_chapter}"
                        )
                    if canon_payload is None:
                        issues.append("Canon JSON 缺失，无法与 StoryKernel 核对")
                    elif canon_payload != kernel_payload:
                        issues.append("Canon JSON 与 StoryKernel 内容不一致")

    remaining_chapters = [
        number for number in finalized_chapter_numbers(layout) if number >= from_chapter
    ]
    if remaining_chapters:
        issues.append(f"正文仍存在：{remaining_chapters}")

    remaining_publications = sorted(
        number
        for path in (layout.states_dir / "chapter_publications").glob("chapter_*.json")
        if (number := _parse_chapter_number(path)) is not None and number >= from_chapter
    )
    if remaining_publications:
        issues.append(f"发布投影仍存在：{remaining_publications}")

    remaining_kernel_snapshots: list[int] = []
    for path in (layout.root / "snapshots").glob("kernel_v*.db"):
        match = re.fullmatch(r"kernel_v(\d+)", path.stem)
        if match is not None and int(match.group(1)) >= from_chapter:
            remaining_kernel_snapshots.append(int(match.group(1)))
    remaining_kernel_snapshots.sort()
    if remaining_kernel_snapshots:
        issues.append(f"StoryKernel 快照仍存在：{remaining_kernel_snapshots}")

    remaining_legacy_snapshots: list[int] = []
    for path in layout.canon_dir.glob("canon_v*.json"):
        match = re.fullmatch(r"canon_v(\d+)", path.stem)
        if match is not None and int(match.group(1)) >= from_chapter:
            remaining_legacy_snapshots.append(int(match.group(1)))
    remaining_legacy_snapshots.sort()
    if remaining_legacy_snapshots:
        issues.append(f"Canon 快照仍存在：{remaining_legacy_snapshots}")

    return issues


async def regenerate_from_chapter_async(
    storage: FileSystemStorage,
    layout: ProjectLayout,
    from_chapter: int,
) -> list[int]:
    """Delete all artifacts from ``from_chapter`` onwards and rollback canon.

    This is the complete cleanup for "从第N章重新生成":
    - Removes chapter files, drafts, plans, reports, states, snapshots
    - Prunes forbidden repetition index
    - Rolls back canon state to chapter N-1
    - Removes canon snapshots for chapters >= from_chapter

    Args:
        storage: FileSystemStorage instance
        layout: ProjectLayout for the project
        from_chapter: First chapter to regenerate (inclusive, >= 1)

    Returns:
        List of chapter numbers whose artifacts were removed
    """
    if from_chapter < 1:
        return []

    from novel_forge.persistence.foundation_guard import require_versioned_maintenance_write

    require_versioned_maintenance_write(layout.root, "从此章起删除重写")
    completed_chapter = from_chapter - 1

    invalidated: list[int] = []
    project_id = layout.root.name
    try:
        invalidated = invalidate_downstream_generated_artifacts(
            storage,
            layout,
            completed_chapter=completed_chapter,
            delete_chapter_files=True,
        )
        restored_kernel = await _rollback_story_kernel_for_regeneration(
            layout,
            project_id=project_id,
            completed_chapter=completed_chapter,
            from_chapter=from_chapter,
        )
        _remove_legacy_canon_snapshots(layout, from_chapter=from_chapter)
        if restored_kernel is None:
            # JSON-only legacy projects retain their established compatibility
            # path.  Modern projects always mirror the exact SQLite snapshot.
            update_canon_watermark(storage, layout, completed_chapter)
        else:
            storage.save_json(
                layout.canon_dir / "canon_current.json",
                restored_kernel.model_dump(mode="json"),
            )
    except ChapterCleanupConvergenceError as exc:
        if exc.invalidated_chapters:
            raise
        raise ChapterCleanupConvergenceError(
            str(exc),
            invalidated_chapters=tuple(sorted(invalidated)),
            failed_stage=exc.failed_stage,
        ) from exc
    except Exception as exc:
        raise ChapterCleanupConvergenceError(
            f"章节清理在持久化阶段中断：{exc}",
            invalidated_chapters=tuple(sorted(invalidated)),
            failed_stage="persistence",
        ) from exc

    issues = _chapter_cleanup_convergence_issues(
        storage,
        layout,
        project_id=project_id,
        completed_chapter=completed_chapter,
        from_chapter=from_chapter,
    )
    if issues:
        raise ChapterCleanupConvergenceError(
            "章节清理未收敛：" + "；".join(issues),
            invalidated_chapters=tuple(sorted(invalidated)),
            failed_stage="verification",
        )
    return invalidated


def regenerate_from_chapter(
    storage: FileSystemStorage,
    layout: ProjectLayout,
    from_chapter: int,
) -> list[int]:
    """Synchronous adapter for CLI and desktop callers.

    Async callers must await :func:`regenerate_from_chapter_async`; failing
    loudly here prevents a nested ``asyncio.run`` from being swallowed after
    destructive filesystem work has already begun.
    """

    import asyncio

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        pass
    else:
        raise RuntimeError(
            "regenerate_from_chapter() cannot run inside an active event loop; "
            "await regenerate_from_chapter_async() instead"
        )

    from novel_forge.core.infra.async_runner import run_async_coro

    return run_async_coro(regenerate_from_chapter_async(storage, layout, from_chapter))
