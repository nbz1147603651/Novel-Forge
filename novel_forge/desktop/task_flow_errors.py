"""Persistent task-flow error archive for the desktop UI."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from novel_forge.app_service.task_flow_error_log import project_auto_repair_guidance
from novel_forge.desktop.task_flow import (
    task_flow_is_cancelled,
)
from novel_forge.desktop.task_flow import (
    task_flow_job_chapter_number as _shared_job_chapter_number,
)
from novel_forge.persistence.filesystem import atomic_write_text
from novel_forge.persistence.models import ProjectLayout

_ARCHIVE_DIRNAME = "task_flow_errors"
_RECORDS_DIRNAME = "records"
_INDEX_FILENAME = "index.json"
_SUMMARY_FILENAME = "summary.json"
_STREAM_FILENAME = "errors.jsonl"
_SCHEMA_VERSION = 2
_MAX_ARCHIVE_ENTRIES_PER_PROJECT = 1000
_CHAPTER_LABEL_RE = re.compile(r"第\s*(\d+)\s*章")
_ERROR_EVENT_STEPS = frozenset(
    {
        "format_repaired",
        "format_repair_strategy_miss",
        "format_repair_local_fallback",
        "format_retry",
        "format_retry_exhausted",
        "tts_auto_trigger_failed",
        "tts_auto_trigger_partial",
    }
)
_FAILED_STATUS = "failed"
_CHECKPOINT_RESOLUTION_KINDS = frozenset(
    {
        "resolve_chapter_checkpoint",
        "resolve_chapter_checkpoint_finalize",
    }
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _entry_id(*parts: object) -> str:
    raw = "\u241f".join(str(part or "") for part in parts)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def _slugify(text: str) -> str:
    cleaned = []
    for ch in text.strip().lower():
        if ch.isalnum() or ch in {"-", "_"}:
            cleaned.append(ch)
        else:
            cleaned.append("-")
    slug = "".join(cleaned).strip("-")
    return slug or "entry"


def _attempt_text(payload: dict[str, Any]) -> str:
    attempt = payload.get("attempt")
    max_attempts = payload.get("max_attempts")
    if isinstance(attempt, int) and isinstance(max_attempts, int) and max_attempts > 0:
        return f"{attempt}/{max_attempts}"
    return ""


def _truncate_text(text: object, limit: int = 1200) -> str:
    value = " ".join(str(text or "").split()).strip()
    if len(value) <= limit:
        return value
    return value[: limit - 1].rstrip() + "…"


def _status_value(record: Any) -> str:
    status = getattr(record, "status", "")
    return str(getattr(status, "value", status) or "")


def _event_payload(event: Any) -> dict[str, Any]:
    payload = getattr(event, "payload", {})
    return payload if isinstance(payload, dict) else {}


def task_flow_error_recovery_text(summary: dict[str, Any] | None) -> str:
    """Render structured backend recovery actions for desktop error surfaces."""
    data = summary if isinstance(summary, dict) else {}
    detail = str(data.get("detail") or "").strip()
    actions: list[str] = []
    for raw in list(data.get("recovery_actions") or []):
        if isinstance(raw, dict):
            text = str(raw.get("description") or raw.get("action") or "").strip()
        else:
            text = str(raw or "").strip()
        if text and text not in actions:
            actions.append(text)
    if not actions:
        return detail
    action_text = "\n".join(f"{index}. {item}" for index, item in enumerate(actions, start=1))
    if detail:
        return f"{detail}\n\n建议操作：\n{action_text}"
    return f"建议操作：\n{action_text}"


_AUTO_REPAIR_STATE_LABELS = {
    "resolved": "已自动无损修复",
    "retryable": "可安全重试",
    "blocked": "为保护用户意图而停止",
    "exhausted": "自动重试已耗尽",
    "rolled_back": "已回滚到安全版本",
    "not_applicable": "不适用自动修复",
    "unknown": "未分类",
}


def task_flow_error_guidance_lines(entry: Mapping[str, Any]) -> list[str]:
    """Return the same Engine-owned repair explanation for both PySide surfaces."""

    explicit = bool(
        entry.get("cause_code")
        or entry.get("auto_repair_state")
        or entry.get("auto_repair_explanation")
    )
    guidance = (
        {
            "cause_code": entry.get("cause_code"),
            "auto_repair_state": entry.get("auto_repair_state"),
            "auto_repair_explanation": entry.get("auto_repair_explanation"),
            "recommended_action": entry.get("recommended_action"),
            "recovery_action_kinds": entry.get("recovery_action_kinds"),
        }
        if explicit
        else project_auto_repair_guidance(
            step=str(entry.get("event_step") or entry.get("task") or ""),
            payload={},
            error=str(entry.get("error") or entry.get("excerpt") or ""),
        )
    )
    state = str(guidance.get("auto_repair_state") or "unknown")
    lines = [
        f"自动修复：{_AUTO_REPAIR_STATE_LABELS.get(state, state or '未分类')}",
        "为何停止："
        + str(guidance.get("auto_repair_explanation") or "Engine 未提供停止原因。"),
        "建议动作：" + str(guidance.get("recommended_action") or "查看完整运行日志。"),
    ]
    raw_recovery_kinds = guidance.get("recovery_action_kinds")
    recovery_kinds = [
        str(item).strip()
        for item in (raw_recovery_kinds if isinstance(raw_recovery_kinds, list) else [])
        if str(item).strip()
    ]
    if recovery_kinds:
        lines.append("可用恢复：" + "、".join(recovery_kinds))
    return lines


def _project_archive_dir(storage_root: Path, project_id: str) -> Path:
    return ProjectLayout(storage_root / project_id).states_dir / _ARCHIVE_DIRNAME


def _record_file_name(entry: dict[str, Any]) -> str:
    task = _slugify(str(entry.get("task") or "task"))
    return f"{entry['id']}_{task}.json"


def _compact_index_entry(entry: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "id",
        "time",
        "archived_at",
        "project_id",
        "job_id",
        "job_kind",
        "job_label",
        "chapter_number",
        "event_step",
        "kind",
        "task",
        "attempt",
        "error",
        "log_file",
        "status",
        "actionability",
        "inactive_reason",
        "cause_code",
        "auto_repair_state",
        "auto_repair_explanation",
        "recommended_action",
        "recovery_action_kinds",
    )
    return {key: entry.get(key, "") for key in keys}


def _entry_time(entry: dict[str, Any]) -> str:
    return str(entry.get("time") or entry.get("archived_at") or "")


def _coerce_chapter_number(raw: object) -> int | None:
    try:
        value = int(raw) if raw is not None and str(raw).strip() else 0
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def _chapter_number_from_label(label: object) -> int | None:
    match = _CHAPTER_LABEL_RE.search(str(label or ""))
    if match is None:
        return None
    return _coerce_chapter_number(match.group(1))


def task_flow_job_chapter_number(record: Any) -> int | None:
    """Best-effort chapter number extraction shared by history and UI rendering."""
    return _shared_job_chapter_number(record)


def task_flow_entry_chapter_number(entry: dict[str, Any]) -> int | None:
    """Best-effort chapter number extraction for compact archive/error entries."""

    for key in ("chapter_number", "chapter"):
        chapter_number = _coerce_chapter_number(entry.get(key))
        if chapter_number is not None:
            return chapter_number
    return _chapter_number_from_label(entry.get("job_label") or entry.get("job"))


def _project_id_from_record(record: Any) -> str:
    return str(getattr(record, "project_id", "") or "").strip()


def _record_kind(record: Any) -> str:
    return str(getattr(record, "kind", "") or "").strip()


def _checkpoint_session_exists(layout: ProjectLayout, chapter_number: int) -> bool:
    return (
        layout.chapter_checkpoint_path(chapter_number).exists()
        and layout.chapter_session_path(chapter_number).exists()
    )


def task_flow_job_inactive_reason(
    record: Any,
    *,
    storage_root: Path | str | None,
) -> str:
    """Return why a failed history job should no longer be treated as actionable.

    Chapter checkpoint resolution jobs are only actionable while the paired
    checkpoint/session files still exist. Once both UI state files are gone,
    the failed job is an audit trail entry, not a current task-flow problem.
    """

    if storage_root is None:
        return ""
    if _status_value(record) != _FAILED_STATUS:
        return ""
    if _record_kind(record) not in _CHECKPOINT_RESOLUTION_KINDS:
        return ""
    project_id = _project_id_from_record(record)
    chapter_number = task_flow_job_chapter_number(record)
    if not project_id or chapter_number is None:
        return ""
    layout = ProjectLayout(Path(storage_root).expanduser() / project_id)
    if _checkpoint_session_exists(layout, chapter_number):
        return ""
    return "对应章节断点/会话已不存在，已作为历史失败保留，不再影响当前任务流。"


def task_flow_job_is_inactive(
    record: Any,
    *,
    storage_root: Path | str | None,
) -> bool:
    return bool(task_flow_job_inactive_reason(record, storage_root=storage_root))


def annotate_task_flow_error_entry(
    entry: dict[str, Any],
    *,
    storage_root: Path | str | None,
) -> dict[str, Any]:
    """Add chapter/actionability metadata to one error entry."""

    annotated = dict(entry)
    chapter_number = task_flow_entry_chapter_number(annotated)
    if chapter_number is not None:
        annotated["chapter_number"] = chapter_number

    status = str(annotated.get("status", "") or "").strip().lower()
    job_kind = str(annotated.get("job_kind", "") or "").strip()
    project_id = str(annotated.get("project_id", "") or "").strip()
    if (
        storage_root is not None
        and status == _FAILED_STATUS
        and job_kind in _CHECKPOINT_RESOLUTION_KINDS
        and project_id
        and chapter_number is not None
    ):
        layout = ProjectLayout(Path(storage_root).expanduser() / project_id)
        if not _checkpoint_session_exists(layout, chapter_number):
            annotated["actionability"] = "historical"
            annotated["inactive_reason"] = (
                "对应章节断点/会话已不存在，已作为历史失败保留，不再影响当前任务流。"
            )
            annotated["auto_resolved"] = "true"
            return annotated

    annotated.setdefault("actionability", "active")
    return annotated


def entries_from_job_record(record: Any) -> list[dict[str, Any]]:
    """Extract archive-ready error entries from one desktop job record."""

    entries: list[dict[str, Any]] = []
    project_id = str(getattr(record, "project_id", "") or "").strip()
    if not project_id:
        return entries

    status = _status_value(record)
    chapter_number = task_flow_job_chapter_number(record)
    for event in getattr(record, "events", []) or []:
        step = str(getattr(event, "step", "") or "")
        if step not in _ERROR_EVENT_STEPS:
            continue
        payload = _event_payload(event)
        attempt = _attempt_text(payload)
        task = str(payload.get("task") or "")
        error = _truncate_text(payload.get("error"))
        log_file = str(payload.get("log_file") or "")
        entry_id = _entry_id(
            getattr(record, "job_id", ""),
            getattr(event, "at", ""),
            step,
            task,
            attempt,
            error,
            log_file,
        )
        kind_label = (
            "格式已修复"
            if step in {"format_repaired", "format_repair_local_fallback"}
            else "格式错误"
        )
        entry = {
            "schema_version": _SCHEMA_VERSION,
            "id": entry_id,
            "archived_at": _now_iso(),
            "time": str(getattr(event, "at", "") or ""),
            "project_id": project_id,
            "job_id": str(getattr(record, "job_id", "") or ""),
            "job_kind": str(getattr(record, "kind", "") or ""),
            "job_label": str(getattr(record, "label", "") or ""),
            "chapter_number": chapter_number,
            "event_step": step,
            "kind": kind_label,
            "task": task,
            "attempt": attempt,
            "error": error,
            "excerpt": _truncate_text(payload.get("raw_excerpt"), 1600),
            "log_file": log_file,
            "status": status,
        }
        entry.update(
            project_auto_repair_guidance(
                step=step,
                payload=payload,
                error=error,
            )
        )
        entries.append(entry)

    error_summary = getattr(record, "error_summary", {})
    summary = error_summary if isinstance(error_summary, dict) else {}
    error = (
        str(summary.get("summary") or "").strip()
        or str(summary.get("detail") or "").strip()
        or str(getattr(record, "error", "") or "").strip()
    )
    if status == _FAILED_STATUS and not task_flow_is_cancelled(record) and error:
        entry_id = _entry_id(
            getattr(record, "job_id", ""),
            "job_failed",
            getattr(record, "current_step", ""),
            error,
        )
        entry = {
            "schema_version": _SCHEMA_VERSION,
            "id": entry_id,
            "archived_at": _now_iso(),
            "time": str(getattr(record, "updated_at", "") or ""),
            "project_id": project_id,
            "job_id": str(getattr(record, "job_id", "") or ""),
            "job_kind": str(getattr(record, "kind", "") or ""),
            "job_label": str(getattr(record, "label", "") or ""),
            "chapter_number": chapter_number,
            "event_step": "job_failed",
            "kind": "任务失败",
            "task": str(getattr(record, "current_step", "") or ""),
            "attempt": "",
            "error": _truncate_text(error),
            "excerpt": _truncate_text(summary.get("detail"), 1600),
            "log_file": "",
            "status": status,
        }
        entry.update(
            project_auto_repair_guidance(
                step="job_failed",
                payload={},
                error=error,
                summary=summary,
            )
        )
        entries.append(entry)
    return entries


class TaskFlowErrorArchive:
    """Write/read task-flow errors using a run-log-style local archive."""

    def __init__(
        self,
        storage_root: Path | str,
        *,
        max_entries_per_project: int = _MAX_ARCHIVE_ENTRIES_PER_PROJECT,
    ) -> None:
        self.storage_root = Path(storage_root).expanduser()
        self.max_entries_per_project = max(1, int(max_entries_per_project or 1))

    def archive_dir(self, project_id: str) -> Path:
        return _project_archive_dir(self.storage_root, project_id)

    def archive_job(self, record: Any) -> int:
        entries = entries_from_job_record(record)
        project_id = str(getattr(record, "project_id", "") or "").strip()
        if not entries or not project_id:
            return 0
        return self.append_entries(project_id, entries)

    def append_entries(self, project_id: str, entries: list[dict[str, Any]]) -> int:
        archive_dir = self.archive_dir(project_id)
        records_dir = archive_dir / _RECORDS_DIRNAME
        records_dir.mkdir(parents=True, exist_ok=True)

        existing = self._read_records(archive_dir)
        by_id: dict[str, dict[str, Any]] = {
            str(item.get("id") or ""): item
            for item in existing
            if str(item.get("id") or "").strip()
        }

        added: list[dict[str, Any]] = []
        for raw_entry in entries:
            entry = annotate_task_flow_error_entry(
                dict(raw_entry),
                storage_root=self.storage_root,
            )
            entry["schema_version"] = _SCHEMA_VERSION
            entry.setdefault("archived_at", _now_iso())
            entry_id = str(entry.get("id") or "").strip()
            if not entry_id or entry_id in by_id:
                continue
            by_id[entry_id] = entry
            added.append(entry)
            atomic_write_text(
                records_dir / _record_file_name(entry),
                json.dumps(entry, ensure_ascii=False, indent=2, default=str),
            )

        if not added:
            return 0

        stream_path = archive_dir / _STREAM_FILENAME
        with stream_path.open("a", encoding="utf-8") as handle:
            for entry in added:
                handle.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")

        merged = sorted(by_id.values(), key=_entry_time, reverse=True)
        kept = merged[: self.max_entries_per_project]
        keep_ids = {str(item.get("id") or "") for item in kept}
        for path in records_dir.glob("*.json"):
            if not any(path.name.startswith(f"{entry_id}_") for entry_id in keep_ids):
                try:
                    path.unlink()
                except OSError:
                    pass

        self._write_index(archive_dir, project_id, kept)
        self._write_summary(archive_dir, project_id, kept)
        return len(added)

    def summary(self, *, project_id: str = "") -> dict[str, Any]:
        archive_dirs = self._iter_archive_dirs(project_id=project_id)
        entry_count = 0
        project_count = 0
        latest_time = ""
        paths: list[str] = []
        for archive_dir in archive_dirs:
            summary = self._read_summary(archive_dir)
            if not summary:
                records = self._read_records(archive_dir)
                summary = self._summary_payload(
                    project_id=self._project_id_from_archive_dir(archive_dir),
                    records=records,
                )
            count = int(summary.get("entry_count") or 0)
            if count <= 0:
                continue
            project_count += 1
            entry_count += count
            candidate = str(summary.get("latest_time") or "")
            if candidate > latest_time:
                latest_time = candidate
            paths.append(str(archive_dir))
        return {
            "schema_version": _SCHEMA_VERSION,
            "entry_count": entry_count,
            "project_count": project_count,
            "latest_time": latest_time,
            "paths": paths,
        }

    def clear(self, *, project_id: str = "") -> int:
        removed = 0
        for archive_dir in self._iter_archive_dirs(project_id=project_id):
            if not archive_dir.exists():
                continue
            try:
                shutil.rmtree(archive_dir)
                removed += 1
            except OSError:
                continue
        return removed

    def _iter_archive_dirs(self, *, project_id: str = "") -> list[Path]:
        if project_id:
            return [self.archive_dir(project_id)]
        root = self.storage_root
        if not root.exists():
            return []
        try:
            project_dirs = [item for item in root.iterdir() if item.is_dir()]
        except OSError:
            return []
        return [
            ProjectLayout(project_dir).states_dir / _ARCHIVE_DIRNAME
            for project_dir in project_dirs
        ]

    def _read_records(self, archive_dir: Path) -> list[dict[str, Any]]:
        records_dir = archive_dir / _RECORDS_DIRNAME
        records: list[dict[str, Any]] = []
        if not records_dir.exists():
            return records
        for path in records_dir.glob("*.json"):
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(raw, dict):
                records.append(raw)
        return records

    def _write_index(
        self,
        archive_dir: Path,
        project_id: str,
        records: list[dict[str, Any]],
    ) -> None:
        payload = {
            "schema_version": _SCHEMA_VERSION,
            "project_id": project_id,
            "updated_at": _now_iso(),
            "entry_count": len(records),
            "entries": [_compact_index_entry(entry) for entry in records],
        }
        atomic_write_text(
            archive_dir / _INDEX_FILENAME,
            json.dumps(payload, ensure_ascii=False, indent=2, default=str),
        )

    def _write_summary(
        self,
        archive_dir: Path,
        project_id: str,
        records: list[dict[str, Any]],
    ) -> None:
        atomic_write_text(
            archive_dir / _SUMMARY_FILENAME,
            json.dumps(
                self._summary_payload(project_id=project_id, records=records),
                ensure_ascii=False,
                indent=2,
                default=str,
            ),
        )

    def _read_summary(self, archive_dir: Path) -> dict[str, Any]:
        path = archive_dir / _SUMMARY_FILENAME
        if not path.exists():
            return {}
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return raw if isinstance(raw, dict) else {}

    @staticmethod
    def _summary_payload(project_id: str, records: list[dict[str, Any]]) -> dict[str, Any]:
        latest_time = ""
        kind_counts: dict[str, int] = {}
        task_counts: dict[str, int] = {}
        for record in records:
            candidate = _entry_time(record)
            if candidate > latest_time:
                latest_time = candidate
            kind = str(record.get("kind") or "未知")
            task = str(record.get("task") or "unknown")
            kind_counts[kind] = kind_counts.get(kind, 0) + 1
            task_counts[task] = task_counts.get(task, 0) + 1
        return {
            "schema_version": _SCHEMA_VERSION,
            "project_id": project_id,
            "updated_at": _now_iso(),
            "entry_count": len(records),
            "latest_time": latest_time,
            "kind_counts": dict(sorted(kind_counts.items())),
            "task_counts": dict(sorted(task_counts.items())),
        }

    def _project_id_from_archive_dir(self, archive_dir: Path) -> str:
        try:
            return archive_dir.parent.parent.name
        except Exception:
            return ""


def task_flow_error_archive_summary(
    storage_root: Path | str,
    *,
    project_id: str = "",
) -> dict[str, Any]:
    return TaskFlowErrorArchive(storage_root).summary(project_id=project_id)


def clear_task_flow_error_archive(
    storage_root: Path | str,
    *,
    project_id: str = "",
) -> int:
    return TaskFlowErrorArchive(storage_root).clear(project_id=project_id)
