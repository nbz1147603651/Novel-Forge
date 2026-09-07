"""Small shared runner for book-level auditors."""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from novel_forge.persistence.filesystem import atomic_write_json


@dataclass
class BookAuditChapter:
    """Normalized chapter payload for book-level auditors."""

    chapter_number: int
    title: str = ""
    summary: str = ""
    text: str = ""
    key_events: list[str] = field(default_factory=list)

    def to_summary_payload(self) -> dict[str, Any]:
        return {
            "chapter_number": self.chapter_number,
            "title": self.title,
            "summary": self.summary,
            "key_events": self.key_events,
        }

    def to_text_payload(self) -> dict[str, Any]:
        return {
            "chapter_number": self.chapter_number,
            "title": self.title,
            "text": self.text,
        }


AuditChunkFn = Callable[[list[BookAuditChapter], int], Awaitable[dict[str, Any]]]


class BookAuditRunner:
    """Batch, checkpoint, and merge book-level audit calls.

    The runner is intentionally domain-agnostic: consistency and editorial
    auditors supply their own chunk callback and result merge semantics.
    """

    def __init__(
        self,
        *,
        audit_name: str,
        chapters: list[BookAuditChapter],
        batch_size: int = 12,
        checkpoint_path: Path | None = None,
        batch_timeout_s: float = 0.0,
    ) -> None:
        self.audit_name = audit_name
        self.chapters = sorted(chapters, key=lambda item: item.chapter_number)
        self.batch_size = max(1, int(batch_size or 12))
        self.checkpoint_path = checkpoint_path
        self.batch_timeout_s = max(0.0, float(batch_timeout_s or 0.0))

    async def run(self, call_chunk: AuditChunkFn) -> list[dict[str, Any]]:
        checkpoint = self._load_checkpoint()
        completed = {
            str(item.get("batch_index"))
            for item in checkpoint.get("batches", [])
            if isinstance(item, dict)
        }
        results = list(checkpoint.get("results", []) or [])
        for batch_index, batch in enumerate(self._chunks(), start=1):
            if str(batch_index) in completed:
                continue
            result = await self._call_chunk_with_timeout(call_chunk, batch, batch_index)
            results.append(result)
            checkpoint.setdefault("batches", []).append(
                {
                    "batch_index": batch_index,
                    "chapters": [chapter.chapter_number for chapter in batch],
                }
            )
            checkpoint["results"] = results
            self._save_checkpoint(checkpoint)
        return results

    async def _call_chunk_with_timeout(
        self,
        call_chunk: AuditChunkFn,
        batch: list[BookAuditChapter],
        batch_index: int,
    ) -> dict[str, Any]:
        call = call_chunk(batch, batch_index)
        if self.batch_timeout_s > 0:
            return await asyncio.wait_for(call, timeout=self.batch_timeout_s)
        return await call

    def _chunks(self) -> list[list[BookAuditChapter]]:
        return [
            self.chapters[index : index + self.batch_size]
            for index in range(0, len(self.chapters), self.batch_size)
        ]

    def _load_checkpoint(self) -> dict[str, Any]:
        if not self.checkpoint_path or not self.checkpoint_path.exists():
            return self._new_checkpoint()
        try:
            payload = json.loads(self.checkpoint_path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                return self._new_checkpoint()
            if payload.get("signature") != self._checkpoint_signature():
                return self._new_checkpoint()
            return payload
        except (OSError, json.JSONDecodeError):
            return self._new_checkpoint()

    def _save_checkpoint(self, payload: dict[str, Any]) -> None:
        if self.checkpoint_path is None:
            return
        payload.update(
            {
                "schema_version": 1,
                "audit_name": self.audit_name,
                "signature": self._checkpoint_signature(),
                "batch_size": self.batch_size,
                "chapters": [chapter.chapter_number for chapter in self.chapters],
            }
        )
        self.checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(self.checkpoint_path, payload)

    def _new_checkpoint(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "audit_name": self.audit_name,
            "signature": self._checkpoint_signature(),
            "batch_size": self.batch_size,
            "chapters": [chapter.chapter_number for chapter in self.chapters],
            "batches": [],
            "results": [],
        }

    def _checkpoint_signature(self) -> str:
        payload = {
            "audit_name": self.audit_name,
            "batch_size": self.batch_size,
            "chapters": [
                {
                    "chapter_number": chapter.chapter_number,
                    "title": chapter.title,
                    "summary": chapter.summary,
                    "text": chapter.text,
                    "key_events": list(chapter.key_events),
                }
                for chapter in self.chapters
            ],
        }
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()
