"""Polish history persistence for outline and spec refinement operations.

Each polish operation (analyze or execute) is recorded as a history entry
containing before/after snapshots, user intent, and AI suggestions.
History is stored as JSON Lines for append-only efficiency.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from novel_forge.persistence.filesystem import atomic_append_text


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class PolishHistoryEntry:
    """One polish operation record."""

    timestamp: str
    operation_type: str
    user_hint: str
    selected_suggestions: list[str]
    focus_fields: list[str]
    changed_keys: list[str]
    before_snapshot: dict[str, Any]
    after_snapshot: dict[str, Any]
    ai_suggestions: list[str]
    creative_note: dict[str, Any] | None = None
    chapter_range: str = ""
    result_type: str = "execute"


class PolishHistoryRecorder:
    """Records polish history for outline and spec operations."""

    def __init__(self, project_dir: Path) -> None:
        self._project_dir = project_dir
        self._outline_history_path = project_dir / "reports" / "polish_outline_history.jsonl"
        self._spec_history_path = project_dir / "reports" / "polish_spec_history.jsonl"

    def record_outline_polish(
        self,
        *,
        user_hint: str,
        selected_suggestions: list[str],
        focus_fields: list[str],
        chapter_range: str,
        before_outline: dict[str, Any],
        after_outline: dict[str, Any],
        changed_chapters: list[int],
        ai_suggestions: list[str],
        result_type: str = "execute",
    ) -> None:
        entry = PolishHistoryEntry(
            timestamp=_utc_now_iso(),
            operation_type="outline_polish",
            user_hint=user_hint,
            selected_suggestions=selected_suggestions,
            focus_fields=focus_fields,
            changed_keys=[str(c) for c in changed_chapters],
            before_snapshot=before_outline,
            after_snapshot=after_outline,
            ai_suggestions=ai_suggestions,
            chapter_range=chapter_range,
            result_type=result_type,
        )
        self._append_entry(self._outline_history_path, entry)

    def record_spec_polish(
        self,
        *,
        user_hint: str,
        selected_suggestions: list[str],
        focus_fields: list[str],
        before_spec: dict[str, Any],
        after_spec: dict[str, Any],
        changed_keys: list[str],
        ai_suggestions: list[str],
        creative_note: dict[str, Any] | None = None,
        result_type: str = "execute",
    ) -> None:
        entry = PolishHistoryEntry(
            timestamp=_utc_now_iso(),
            operation_type="spec_polish",
            user_hint=user_hint,
            selected_suggestions=selected_suggestions,
            focus_fields=focus_fields,
            changed_keys=changed_keys,
            before_snapshot=before_spec,
            after_snapshot=after_spec,
            ai_suggestions=ai_suggestions,
            creative_note=creative_note or None,
            result_type=result_type,
        )
        self._append_entry(self._spec_history_path, entry)

    def load_outline_history(self) -> list[PolishHistoryEntry]:
        return self._load_entries(self._outline_history_path)

    def load_spec_history(self) -> list[PolishHistoryEntry]:
        return self._load_entries(self._spec_history_path)

    def _append_entry(self, path: Path, entry: PolishHistoryEntry) -> None:
        line = json.dumps(asdict(entry), ensure_ascii=False, default=str) + "\n"
        atomic_append_text(path, line)

    def _load_entries(self, path: Path) -> list[PolishHistoryEntry]:
        if not path.exists():
            return []
        entries: list[PolishHistoryEntry] = []
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    entries.append(PolishHistoryEntry(**data))
                except (json.JSONDecodeError, TypeError):
                    continue
        return entries
