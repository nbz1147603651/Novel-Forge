"""Manual edit tracking — detect and record user's hand-edits to chapter text."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from novel_forge.core.utils.text_validation import count_chapter_words
from novel_forge.persistence.filesystem import atomic_write_text


@dataclass
class EditRecord:
    """Record of a detected manual edit."""

    chapter_number: int
    timestamp: str
    previous_hash: str
    current_hash: str
    word_count_delta: int
    similarity_ratio: float


@dataclass
class ManualEditLog:
    """Accumulated log of manual edits for a project."""

    edits: list[EditRecord] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "edits": [
                {
                    "chapter_number": e.chapter_number,
                    "timestamp": e.timestamp,
                    "previous_hash": e.previous_hash,
                    "current_hash": e.current_hash,
                    "word_count_delta": e.word_count_delta,
                    "similarity_ratio": e.similarity_ratio,
                }
                for e in self.edits
            ]
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ManualEditLog:
        edits = []
        for e in data.get("edits", []):
            edits.append(
                EditRecord(
                    chapter_number=e.get("chapter_number", 0),
                    timestamp=e.get("timestamp", ""),
                    previous_hash=e.get("previous_hash", ""),
                    current_hash=e.get("current_hash", ""),
                    word_count_delta=e.get("word_count_delta", 0),
                    similarity_ratio=e.get("similarity_ratio", 1.0),
                )
            )
        return cls(edits=edits)


def _text_hash(text: str) -> str:
    """Compute a content hash for change detection."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _word_count(text: str) -> int:
    return count_chapter_words(text)


def check_for_manual_edits(
    chapter_path: Path,
    snapshot_dir: Path,
    chapter_number: int,
) -> EditRecord | None:
    """Compare current chapter file against last known snapshot.

    Returns an EditRecord if the file has changed, or None if unchanged.
    Saves a new snapshot upon detection.
    """
    if not chapter_path.exists():
        return None

    current_text = chapter_path.read_text(encoding="utf-8")
    current_hash = _text_hash(current_text)

    snapshot_dir.mkdir(parents=True, exist_ok=True)
    snapshot_path = snapshot_dir / f"chapter_{chapter_number:03d}_snapshot.txt"

    if snapshot_path.exists():
        previous_text = snapshot_path.read_text(encoding="utf-8")
        previous_hash = _text_hash(previous_text)

        if current_hash == previous_hash:
            return None

        # Detected change
        import difflib

        ratio = difflib.SequenceMatcher(
            None,
            previous_text.splitlines(),
            current_text.splitlines(),
        ).ratio()

        wc_delta = _word_count(current_text) - _word_count(previous_text)

        record = EditRecord(
            chapter_number=chapter_number,
            timestamp=datetime.now(timezone.utc).isoformat(),
            previous_hash=previous_hash,
            current_hash=current_hash,
            word_count_delta=wc_delta,
            similarity_ratio=round(ratio, 4),
        )

        return record

    # First snapshot — save baseline
    atomic_write_text(snapshot_path, current_text)
    return None


def save_snapshot(chapter_path: Path, snapshot_dir: Path, chapter_number: int) -> None:
    """Save a snapshot of the current chapter text for future comparison."""
    if not chapter_path.exists():
        return
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    snapshot_path = snapshot_dir / f"chapter_{chapter_number:03d}_snapshot.txt"
    text = chapter_path.read_text(encoding="utf-8")
    atomic_write_text(snapshot_path, text)


def load_edit_log(log_path: Path) -> ManualEditLog:
    """Load the manual edit log from disk."""
    if not log_path.exists():
        return ManualEditLog()
    raw = json.loads(log_path.read_text(encoding="utf-8"))
    return ManualEditLog.from_dict(raw)


def save_edit_log(log_path: Path, log: ManualEditLog) -> None:
    """Save the manual edit log to disk."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(
        log_path,
        json.dumps(log.to_dict(), ensure_ascii=False, indent=2),
    )


def record_manual_edit(
    project_dir: Path,
    chapter_number: int,
) -> EditRecord | None:
    """Check for manual edits on a chapter and record if found.

    Returns the EditRecord if an edit was detected, None otherwise.
    """
    chapters_dir = project_dir / "chapters"
    chapter_path = chapters_dir / f"chapter_{chapter_number:03d}.md"
    snapshot_dir = project_dir / "states" / "edit_snapshots"
    log_path = project_dir / "states" / "manual_edit_log.json"

    record = check_for_manual_edits(chapter_path, snapshot_dir, chapter_number)
    if record is not None:
        log = load_edit_log(log_path)
        log.edits.append(record)
        save_edit_log(log_path, log)

    return record
