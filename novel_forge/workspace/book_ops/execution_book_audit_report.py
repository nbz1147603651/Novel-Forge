"""Report persistence helpers for whole-book consistency audit execution."""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from novel_forge.persistence.models import ProjectLayout
from novel_forge.workspace.book_ops.execution_book_audit_helpers import _compare_audit_reports


def _load_latest_audit_payload(layout: ProjectLayout) -> dict[str, Any] | None:
    latest_path = layout.reports_dir / "book_consistency_audit_latest.json"
    candidates = [latest_path]
    index_path = layout.reports_dir / "book_consistency_audit_index.json"
    if index_path.exists():
        try:
            index_data = json.loads(index_path.read_text(encoding="utf-8"))
            audits = index_data.get("audits", [])
            if isinstance(audits, list) and audits:
                latest_entry = next(
                    (item for item in reversed(audits) if isinstance(item, dict)),
                    None,
                )
                if latest_entry:
                    path_name = str(latest_entry.get("path", "") or "")
                    if path_name:
                        candidates.append(layout.reports_dir / path_name)
        except (OSError, json.JSONDecodeError):
            pass
    for candidate in candidates:
        try:
            if candidate.exists():
                payload = json.loads(candidate.read_text(encoding="utf-8"))
                if isinstance(payload, dict):
                    return payload
        except (OSError, json.JSONDecodeError):
            continue
    return None


def _attach_previous_audit_delta(
    layout: ProjectLayout,
    report_payload: dict[str, Any],
) -> None:
    if "delta_from_previous" in report_payload:
        return
    previous_payload = _load_latest_audit_payload(layout)
    if previous_payload is None:
        return
    report_payload["delta_from_previous"] = _compare_audit_reports(
        previous_payload,
        report_payload,
    )


def _save_versioned_audit_report(
    layout: ProjectLayout,
    report_payload: dict[str, Any],
    timestamp: str | None = None,
) -> str:
    if timestamp is None:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    layout.reports_dir.mkdir(parents=True, exist_ok=True)
    _attach_previous_audit_delta(layout, report_payload)

    def _next_timestamp(value: str) -> str:
        try:
            dt = datetime.strptime(value, "%Y%m%d_%H%M%S").replace(tzinfo=timezone.utc)
        except ValueError:
            dt = datetime.now(timezone.utc)
        return (dt + timedelta(seconds=1)).strftime("%Y%m%d_%H%M%S")

    report_path = layout.reports_dir / f"book_consistency_audit_{timestamp}.json"
    while report_path.exists():
        timestamp = _next_timestamp(timestamp)
        report_path = layout.reports_dir / f"book_consistency_audit_{timestamp}.json"

    import tempfile

    temp_fd, temp_path = tempfile.mkstemp(
        prefix=".book_consistency_audit.",
        suffix=".json.tmp",
        dir=str(layout.reports_dir),
    )
    try:
        with os.fdopen(temp_fd, "w", encoding="utf-8") as f:
            json.dump(report_payload, f, indent=2, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp_path, report_path)
    except Exception:
        try:
            os.unlink(temp_path)
        except FileNotFoundError:
            pass
        raise
    return timestamp


def _update_audit_index(
    layout: ProjectLayout,
    timestamp: str,
    chapter_count: int,
    issue_count: int,
    analysis_mode: str,
) -> None:
    index_path = layout.reports_dir / "book_consistency_audit_index.json"
    if index_path.exists():
        index_data = json.loads(index_path.read_text(encoding="utf-8"))
    else:
        index_data = {"audits": []}
    audits = index_data.get("audits", [])
    if not isinstance(audits, list):
        audits = []

    path_name = f"book_consistency_audit_{timestamp}.json"
    entry = {
        "timestamp": timestamp,
        "path": path_name,
        "chapter_count": chapter_count,
        "issue_count": issue_count,
        "analysis_mode": analysis_mode,
    }

    replaced = False
    next_audits: list[dict[str, Any]] = []
    seen_paths: set[str] = set()
    for raw_item in audits:
        if not isinstance(raw_item, dict):
            continue
        item_path = str(raw_item.get("path", "") or "")
        item_ts = str(raw_item.get("timestamp", "") or "")
        if item_ts == timestamp or item_path == path_name:
            if not replaced:
                next_audits.append(entry)
                seen_paths.add(path_name)
                replaced = True
            continue
        if item_path and item_path in seen_paths:
            continue
        if item_path:
            seen_paths.add(item_path)
        next_audits.append(raw_item)
    if not replaced:
        next_audits.append(entry)
    index_data["audits"] = next_audits
    import tempfile

    temp_fd, temp_path = tempfile.mkstemp(
        prefix=".book_consistency_audit_index.",
        suffix=".json.tmp",
        dir=str(layout.reports_dir),
    )
    try:
        with os.fdopen(temp_fd, "w", encoding="utf-8") as f:
            json.dump(index_data, f, indent=2, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp_path, index_path)
    except Exception:
        try:
            os.unlink(temp_path)
        except FileNotFoundError:
            pass
        raise


def _update_audit_symlink(layout: ProjectLayout, timestamp: str) -> None:
    symlink_path = layout.reports_dir / "book_consistency_audit_latest.json"
    target_name = f"book_consistency_audit_{timestamp}.json"
    if symlink_path.exists() or symlink_path.is_symlink():
        symlink_path.unlink()
    try:
        os.symlink(target_name, symlink_path)
    except OSError:
        pass


def _save_canonical_audit_report(layout: ProjectLayout, report_payload: dict[str, Any]) -> Path:
    """Write the canonical audit report used by desktop/API artifact viewers."""
    report_path = layout.reports_dir / "book_consistency_audit.json"
    layout.reports_dir.mkdir(parents=True, exist_ok=True)

    import tempfile

    temp_fd, temp_path = tempfile.mkstemp(
        prefix=".book_consistency_audit_current.",
        suffix=".json.tmp",
        dir=str(layout.reports_dir),
    )
    try:
        with os.fdopen(temp_fd, "w", encoding="utf-8") as f:
            json.dump(report_payload, f, indent=2, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp_path, report_path)
    except Exception:
        try:
            os.unlink(temp_path)
        except FileNotFoundError:
            pass
        raise
    return report_path

