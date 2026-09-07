"""GlobalAuditStore — SQLite control plane for global audit runs.

Extracted from workspace/global_audit.py so that pipeline can
import this class without a reverse dependency on workspace.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

GLOBAL_AUDIT_SCHEMA_VERSION = 1


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _stable_id(prefix: str, payload: Any, *, length: int = 16) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(raw.encode()).hexdigest()[:length]
    return f"{prefix}_{digest}"


def _to_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return dataclasses.asdict(value)
    return {}


def _coerce_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


@dataclass
class GlobalAuditSlice:
    """A planned global-audit unit, usually volume/arc/thread oriented."""

    slice_id: str
    slice_kind: str
    chapters: list[int]
    boundary_chapters: list[int] = field(default_factory=list)
    focus_dimensions: list[str] = field(default_factory=list)
    dimension: str = ""
    dimension_role: str = "shared"
    source_refs: list[dict[str, Any]] = field(default_factory=list)
    status: str = "pending"

    def model_dump(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


class GlobalAuditStore:
    """SQLite control plane for global audit runs, evidence, and repair queues."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.init_schema()

    def init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS audit_runs (
                    run_id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    completed_at TEXT,
                    chapter_range_json TEXT NOT NULL,
                    analysis_mode TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    result_summary_json TEXT NOT NULL DEFAULT '{}',
                    coverage_metrics_json TEXT NOT NULL DEFAULT '{}'
                );
                CREATE TABLE IF NOT EXISTS audit_slices (
                    run_id TEXT NOT NULL,
                    slice_id TEXT NOT NULL,
                    slice_kind TEXT NOT NULL,
                    chapters_json TEXT NOT NULL,
                    boundary_chapters_json TEXT NOT NULL,
                    focus_dimensions_json TEXT NOT NULL,
                    source_refs_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    PRIMARY KEY (run_id, slice_id)
                );
                CREATE TABLE IF NOT EXISTS chapter_paragraph_index (
                    run_id TEXT NOT NULL,
                    chapter_number INTEGER NOT NULL,
                    paragraph_index INTEGER NOT NULL,
                    text_summary TEXT NOT NULL,
                    chapter_hash TEXT NOT NULL,
                    paragraph_hash TEXT NOT NULL,
                    source_path TEXT NOT NULL,
                    PRIMARY KEY (run_id, chapter_number, paragraph_index)
                );
                CREATE TABLE IF NOT EXISTS evidence_windows (
                    run_id TEXT NOT NULL,
                    window_id TEXT NOT NULL,
                    finding_id TEXT NOT NULL,
                    chapter_number INTEGER NOT NULL,
                    paragraph_span_json TEXT NOT NULL,
                    text TEXT NOT NULL,
                    source_hash TEXT NOT NULL,
                    locator_method TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    PRIMARY KEY (run_id, window_id)
                );
                CREATE TABLE IF NOT EXISTS global_findings (
                    run_id TEXT NOT NULL,
                    finding_id TEXT NOT NULL,
                    slice_id TEXT NOT NULL,
                    dimension TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    chapters_involved_json TEXT NOT NULL,
                    primary_chapter INTEGER NOT NULL,
                    repair_readiness_json TEXT NOT NULL,
                    finding_json TEXT NOT NULL,
                    PRIMARY KEY (run_id, finding_id)
                );
                CREATE TABLE IF NOT EXISTS locator_candidates (
                    run_id TEXT NOT NULL,
                    candidate_id TEXT NOT NULL,
                    finding_id TEXT NOT NULL,
                    locator_method TEXT NOT NULL,
                    chapter_number INTEGER NOT NULL,
                    paragraph_span_json TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    source_hash TEXT NOT NULL,
                    candidate_json TEXT NOT NULL,
                    PRIMARY KEY (run_id, candidate_id)
                );
                CREATE TABLE IF NOT EXISTS repair_queue_items (
                    run_id TEXT NOT NULL,
                    queue_item_id TEXT NOT NULL,
                    finding_id TEXT NOT NULL,
                    slice_id TEXT NOT NULL,
                    target_chapter INTEGER NOT NULL,
                    paragraph_span_json TEXT NOT NULL,
                    source_hash TEXT NOT NULL,
                    status TEXT NOT NULL,
                    attempt_count INTEGER NOT NULL DEFAULT 0,
                    ticket_json TEXT NOT NULL,
                    last_verification_result_json TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (run_id, queue_item_id)
                );
                CREATE TABLE IF NOT EXISTS verification_results (
                    run_id TEXT NOT NULL,
                    verification_id TEXT NOT NULL,
                    queue_item_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    target_chapter INTEGER NOT NULL,
                    source_hash TEXT NOT NULL,
                    result_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (run_id, verification_id)
                );
                """
            )

    def start_run(
        self,
        *,
        run_id: str,
        project_id: str,
        chapter_numbers: list[int],
        analysis_mode: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO audit_runs (
                    run_id, project_id, status, started_at, completed_at,
                    chapter_range_json, analysis_mode, metadata_json,
                    result_summary_json, coverage_metrics_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    project_id,
                    "running",
                    _utc_now(),
                    None,
                    _json_dump(chapter_numbers),
                    analysis_mode,
                    _json_dump({"schema_version": GLOBAL_AUDIT_SCHEMA_VERSION, **(metadata or {})}),
                    "{}",
                    "{}",
                ),
            )

    def finish_run(
        self,
        *,
        run_id: str,
        status: str,
        result_summary: dict[str, Any],
        coverage_metrics: dict[str, Any],
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE audit_runs
                SET status = ?, completed_at = ?, result_summary_json = ?,
                    coverage_metrics_json = ?
                WHERE run_id = ?
                """,
                (
                    status,
                    _utc_now(),
                    _json_dump(result_summary),
                    _json_dump(coverage_metrics),
                    run_id,
                ),
            )

    def replace_slices(
        self, run_id: str, slices: list[GlobalAuditSlice] | list[dict[str, Any]]
    ) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM audit_slices WHERE run_id = ?", (run_id,))
            for item in slices:
                payload = (
                    item.model_dump() if isinstance(item, GlobalAuditSlice) else _to_dict(item)
                )
                conn.execute(
                    """
                    INSERT INTO audit_slices (
                        run_id, slice_id, slice_kind, chapters_json,
                        boundary_chapters_json, focus_dimensions_json,
                        source_refs_json, status
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        run_id,
                        str(payload.get("slice_id") or ""),
                        str(payload.get("slice_kind") or ""),
                        _json_dump(payload.get("chapters") or []),
                        _json_dump(payload.get("boundary_chapters") or []),
                        _json_dump(payload.get("focus_dimensions") or []),
                        _json_dump(payload.get("source_refs") or []),
                        str(payload.get("status") or "pending"),
                    ),
                )

    def replace_chapter_paragraph_index(self, run_id: str, rows: list[dict[str, Any]]) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM chapter_paragraph_index WHERE run_id = ?", (run_id,))
            for row in rows:
                conn.execute(
                    """
                    INSERT INTO chapter_paragraph_index (
                        run_id, chapter_number, paragraph_index, text_summary,
                        chapter_hash, paragraph_hash, source_path
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        run_id,
                        _coerce_int(row.get("chapter_number"), 0),
                        _coerce_int(row.get("paragraph_index"), 0),
                        str(row.get("text_summary") or ""),
                        str(row.get("chapter_hash") or ""),
                        str(row.get("paragraph_hash") or ""),
                        str(row.get("source_path") or ""),
                    ),
                )

    def replace_findings(self, run_id: str, findings: list[Any]) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM global_findings WHERE run_id = ?", (run_id,))
            conn.execute("DELETE FROM locator_candidates WHERE run_id = ?", (run_id,))
            conn.execute("DELETE FROM evidence_windows WHERE run_id = ?", (run_id,))
            for finding in findings:
                payload = _to_dict(finding)
                finding_id = str(payload.get("finding_id") or payload.get("issue_id") or "")
                locator_candidates = [
                    item
                    for item in payload.get("locator_candidates") or []
                    if isinstance(item, dict)
                ]
                conn.execute(
                    """
                    INSERT INTO global_findings (
                        run_id, finding_id, slice_id, dimension, severity,
                        chapters_involved_json, primary_chapter,
                        repair_readiness_json, finding_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        run_id,
                        finding_id,
                        str(payload.get("slice_id") or "whole_book"),
                        str(payload.get("dimension") or payload.get("category") or ""),
                        str(payload.get("severity") or ""),
                        _json_dump(payload.get("chapters_involved") or []),
                        _coerce_int(payload.get("primary_chapter"), 0),
                        _json_dump(payload.get("repair_readiness") or {}),
                        _json_dump(payload),
                    ),
                )
                for candidate in locator_candidates:
                    candidate_id = str(
                        candidate.get("candidate_id") or _stable_id("locator", candidate)
                    )
                    conn.execute(
                        """
                        INSERT INTO locator_candidates (
                            run_id, candidate_id, finding_id, locator_method,
                            chapter_number, paragraph_span_json, confidence,
                            source_hash, candidate_json
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            run_id,
                            candidate_id,
                            finding_id,
                            str(candidate.get("locator_method") or ""),
                            _coerce_int(candidate.get("chapter_number"), 0),
                            _json_dump(candidate.get("paragraph_span") or []),
                            float(candidate.get("confidence", 0.0) or 0.0),
                            str(candidate.get("source_hash") or ""),
                            _json_dump(candidate),
                        ),
                    )
                    conn.execute(
                        """
                        INSERT INTO evidence_windows (
                            run_id, window_id, finding_id, chapter_number,
                            paragraph_span_json, text, source_hash,
                            locator_method, confidence
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            run_id,
                            candidate_id,
                            finding_id,
                            _coerce_int(candidate.get("chapter_number"), 0),
                            _json_dump(candidate.get("paragraph_span") or []),
                            str(candidate.get("text") or ""),
                            str(candidate.get("source_hash") or ""),
                            str(candidate.get("locator_method") or ""),
                            float(candidate.get("confidence", 0.0) or 0.0),
                        ),
                    )

    def replace_repair_queue_items(self, run_id: str, items: list[dict[str, Any]]) -> None:
        now = _utc_now()
        with self._connect() as conn:
            conn.execute("DELETE FROM repair_queue_items WHERE run_id = ?", (run_id,))
            for item in items:
                conn.execute(
                    """
                    INSERT INTO repair_queue_items (
                        run_id, queue_item_id, finding_id, slice_id, target_chapter,
                        paragraph_span_json, source_hash, status, attempt_count,
                        ticket_json, last_verification_result_json, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        run_id,
                        str(item.get("queue_item_id") or ""),
                        str(item.get("finding_id") or ""),
                        str(item.get("slice_id") or "whole_book"),
                        _coerce_int(item.get("target_chapter"), 0),
                        _json_dump(item.get("paragraph_span") or []),
                        str(item.get("source_hash") or ""),
                        str(item.get("status") or "manual_review"),
                        _coerce_int(item.get("attempt_count"), 0),
                        _json_dump(item.get("ticket") or {}),
                        _json_dump(item.get("last_verification_result") or {}),
                        now,
                        now,
                    ),
                )

    def latest_completed_run_id(self, project_id: str) -> str:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT run_id
                FROM audit_runs
                WHERE project_id = ? AND status = 'completed'
                ORDER BY completed_at DESC, started_at DESC
                LIMIT 1
                """,
                (project_id,),
            ).fetchone()
        return str(row["run_id"]) if row is not None else ""

    def load_repair_queue_items(
        self,
        *,
        run_id: str,
        statuses: list[str],
        limit: int,
    ) -> list[dict[str, Any]]:
        statuses = [str(item) for item in statuses if str(item)]
        if not statuses:
            return []
        placeholders = ",".join("?" for _ in statuses)
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT *
                FROM repair_queue_items
                WHERE run_id = ? AND status IN ({placeholders})
                ORDER BY
                    CASE status
                        WHEN 'ready' THEN 0
                        WHEN 'verify_first' THEN 1
                        WHEN 'manual_review' THEN 2
                        ELSE 3
                    END,
                    target_chapter ASC,
                    queue_item_id ASC
                LIMIT ?
                """,
                (run_id, *statuses, max(1, int(limit or 1))),
            ).fetchall()
        items: list[dict[str, Any]] = []
        for row in rows:
            ticket = json.loads(row["ticket_json"] or "{}")
            last_verification = json.loads(row["last_verification_result_json"] or "{}")
            items.append(
                {
                    "run_id": row["run_id"],
                    "queue_item_id": row["queue_item_id"],
                    "finding_id": row["finding_id"],
                    "slice_id": row["slice_id"],
                    "target_chapter": int(row["target_chapter"] or 0),
                    "paragraph_span": json.loads(row["paragraph_span_json"] or "[]"),
                    "source_hash": row["source_hash"],
                    "status": row["status"],
                    "attempt_count": int(row["attempt_count"] or 0),
                    "ticket": ticket if isinstance(ticket, dict) else {},
                    "last_verification_result": (
                        last_verification if isinstance(last_verification, dict) else {}
                    ),
                }
            )
        return items

    def update_repair_queue_item(
        self,
        *,
        run_id: str,
        queue_item_id: str,
        status: str,
        attempt_count: int,
        last_verification_result: dict[str, Any] | None = None,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE repair_queue_items
                SET status = ?, attempt_count = ?, last_verification_result_json = ?,
                    updated_at = ?
                WHERE run_id = ? AND queue_item_id = ?
                """,
                (
                    status,
                    max(0, int(attempt_count or 0)),
                    _json_dump(last_verification_result or {}),
                    _utc_now(),
                    run_id,
                    queue_item_id,
                ),
            )

    def insert_verification_result(
        self,
        *,
        run_id: str,
        queue_item_id: str,
        target_chapter: int,
        source_hash: str,
        status: str,
        result: dict[str, Any],
    ) -> str:
        verification_id = _stable_id(
            "verification",
            {
                "run_id": run_id,
                "queue_item_id": queue_item_id,
                "target_chapter": target_chapter,
                "status": status,
                "created_at": _utc_now(),
            },
        )
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO verification_results (
                    run_id, verification_id, queue_item_id, status, target_chapter,
                    source_hash, result_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    verification_id,
                    queue_item_id,
                    status,
                    int(target_chapter or 0),
                    source_hash,
                    _json_dump(result),
                    _utc_now(),
                ),
            )
        return verification_id

    def invalidate_chapter_items(
        self,
        chapter_number: int,
        *,
        reason: str = "chapter_regenerated",
    ) -> int:
        """Mark pending repair queue items for a chapter as stale.

        Called after a chapter is regenerated (re-run pipeline) so that
        book-level repair tickets targeting the old text are not executed
        against the new text. Returns the number of items invalidated.
        """
        with self._connect() as conn:
            cursor = conn.execute(
                """
                UPDATE repair_queue_items
                SET status = 'stale',
                    last_verification_result_json = ?,
                    updated_at = ?
                WHERE target_chapter = ?
                  AND status IN ('ready', 'verify_first', 'blocked')
                """,
                (
                    _json_dump({"invalidated": True, "reason": reason}),
                    _utc_now(),
                    int(chapter_number),
                ),
            )
            return cursor.rowcount

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn
