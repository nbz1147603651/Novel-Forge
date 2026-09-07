"""Durable, UI-safe diagnostic entries for Engine task-flow failures.

The Engine keeps job history intentionally short and lets users dismiss completed
or failed task cards. Diagnostics must outlive that presentation history: this
module stores compact, structured error entries beside the owning project while
the full run log remains in ``logs/<run_id>``.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from novel_forge.persistence.filesystem import atomic_write_text
from novel_forge.persistence.models import ProjectLayout

_ARCHIVE_DIRNAME = "task_flow_errors"
_RECORDS_DIRNAME = "records"
_MAX_ENTRIES_PER_PROJECT = 1000
_SCHEMA_VERSION = 3
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
_AUTO_RESOLVED_EVENT_STEPS = frozenset({"format_repaired", "format_repair_local_fallback"})


def _recovery_action_kinds(summary: dict[str, Any]) -> list[str]:
    actions: list[str] = []
    for item in summary.get("recovery_actions", []) or []:
        if isinstance(item, dict):
            value = item.get("kind") or item.get("action")
        else:
            value = ""
        action = _text(value, limit=80)
        if action and action not in actions:
            actions.append(action)
    return actions


def _auto_repair_guidance(
    *,
    step: str,
    payload: dict[str, Any],
    error: str,
    summary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Classify repair behavior once at the Engine boundary.

    Prefer structured producer fields, then fall back to a bounded taxonomy.
    UI clients receive the result and never need to infer domain semantics from
    provider text.
    """

    source = {**(summary or {}), **payload}
    explicit_state = _text(source.get("auto_repair_state"), limit=80)
    explicit_code = _text(source.get("cause_code"), limit=120)
    explicit_explanation = _text(source.get("auto_repair_explanation"), limit=600)
    explicit_action = _text(source.get("recommended_action"), limit=400)
    if explicit_state or explicit_code:
        return {
            "cause_code": explicit_code or "producer_classified",
            "auto_repair_state": explicit_state or "unknown",
            "auto_repair_explanation": explicit_explanation,
            "recommended_action": explicit_action,
            "recovery_action_kinds": _recovery_action_kinds(source),
        }

    normalized_step = step.casefold()
    haystack = " ".join(
        _text(value, limit=1200)
        for value in (
            error,
            source.get("error_type"),
            source.get("error_kind"),
            source.get("category"),
            source.get("detail"),
            source.get("reason"),
        )
    ).casefold()
    if step in _AUTO_RESOLVED_EVENT_STEPS:
        return {
            "cause_code": "known_format_alias",
            "auto_repair_state": "resolved",
            "auto_repair_explanation": "已按登记的兼容规则执行本地无损迁移，未删除未知语义。",
            "recommended_action": "无需操作；可继续当前任务。",
            "recovery_action_kinds": [],
        }
    if "format_retry_exhausted" in normalized_step or (
        "extra_forbidden" in haystack and "retry" in haystack
    ):
        return {
            "cause_code": "format_retry_exhausted",
            "auto_repair_state": "exhausted",
            "auto_repair_explanation": (
                "格式重试已耗尽。未知字段不能被通用删除，否则可能丢失作者意图或模型语义。"
            ),
            "recommended_action": "使用当前运行时重试失败步骤，或检查模板与格式合同版本。",
            "recovery_action_kinds": _recovery_action_kinds(source),
        }
    if any(
        marker in haystack
        for marker in ("运行版本陈旧", "stale runtime", "template version", "模板版本")
    ):
        return {
            "cause_code": "stale_runtime",
            "auto_repair_state": "retryable",
            "auto_repair_explanation": "失败任务使用的模板或运行时版本与当前配置不一致。",
            "recommended_action": "重启或继续失败步骤，使新任务使用当前 RuntimeServices。",
            "recovery_action_kinds": _recovery_action_kinds(source),
        }
    if any(
        marker in haystack
        for marker in ("intent_conflict", "user_intent", "locked", "意图冲突", "锁定要素")
    ):
        return {
            "cause_code": "intent_protection",
            "auto_repair_state": "blocked",
            "auto_repair_explanation": "候选修复会改变用户明确设定，因此意图门禁拒绝自动应用。",
            "recommended_action": "保留当前安全版本；如需改变设定，请由作者明确确认。",
            "recovery_action_kinds": _recovery_action_kinds(source),
        }
    if "rollback" in normalized_step or any(
        marker in haystack for marker in ("rolled_back", "已回滚", "质量回归", "regression")
    ):
        return {
            "cause_code": "quality_regression",
            "auto_repair_state": "rolled_back",
            "auto_repair_explanation": "修复后的文本未通过回归验证，系统已恢复上一个已验证版本。",
            "recommended_action": "查看残余问题，必要时进行人工定向修改。",
            "recovery_action_kinds": _recovery_action_kinds(source),
        }
    if any(
        marker in haystack
        for marker in (
            "network",
            "timeout",
            "authentication",
            "rate limit",
            "provider",
            "网络",
            "超时",
        )
    ) or _truthy(source.get("retryable")):
        return {
            "cause_code": "provider_unavailable",
            "auto_repair_state": "retryable",
            "auto_repair_explanation": "模型或网络服务未完成请求；这不是可通过改写正文修复的问题。",
            "recommended_action": "检查模型路由与网络状态后安全重试。",
            "recovery_action_kinds": _recovery_action_kinds(source),
        }
    if "format_" in normalized_step or "validationerror" in haystack:
        return {
            "cause_code": "format_contract_mismatch",
            "auto_repair_state": "retryable",
            "auto_repair_explanation": "模型输出与格式合同不一致，系统将只执行有界格式重试。",
            "recommended_action": "等待格式重试；若耗尽，请核对模板与合同版本。",
            "recovery_action_kinds": _recovery_action_kinds(source),
        }
    return {
        "cause_code": "not_auto_repairable",
        "auto_repair_state": "not_applicable",
        "auto_repair_explanation": "该错误不属于可安全自动改文的已登记场景。",
        "recommended_action": "查看任务日志和 Engine 提供的恢复操作。",
        "recovery_action_kinds": _recovery_action_kinds(source),
    }


def project_auto_repair_guidance(
    *,
    step: str,
    payload: dict[str, Any],
    error: str,
    summary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Expose the Engine-owned repair taxonomy to legacy desktop projections."""

    return _auto_repair_guidance(
        step=step,
        payload=payload,
        error=error,
        summary=summary,
    )


def _entry_id(*parts: object) -> str:
    raw = "\u241f".join(str(part or "") for part in parts)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def _text(value: object, *, limit: int = 1600) -> str:
    text = " ".join(str(value or "").split()).strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _truthy(value: object) -> bool:
    return value is True or str(value).casefold() == "true"


def is_closed_task_flow_error(entry: dict[str, Any]) -> bool:
    """Return whether a compact diagnostic is safe to remove from its index."""

    return _truthy(entry.get("auto_resolved")) or bool(
        _text(entry.get("acknowledged_at"), limit=80)
    )


@dataclass(frozen=True)
class ClosedTaskFlowErrorClearResult:
    """Exact result of a guarded diagnostic-index cleanup."""

    removed_entry_ids: list[str]
    retained_pending_entry_ids: list[str]
    task_ids: list[str]


def _mapping(value: object) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _value(record: Any, field: str) -> str:
    value = getattr(record, field, "")
    return str(getattr(value, "value", value) or "").strip()


def _event_payload(event: Any) -> dict[str, Any]:
    return _mapping(getattr(event, "payload", {}))


def _attempt_label(payload: dict[str, Any]) -> str:
    attempt = payload.get("attempt")
    maximum = payload.get("max_attempts")
    if isinstance(attempt, int) and isinstance(maximum, int) and maximum > 0:
        return f"{attempt}/{maximum}"
    if isinstance(attempt, int) and attempt > 0:
        return str(attempt)
    return ""


def _run_log_path(record: Any, summary: dict[str, Any]) -> str:
    for key in ("log_path", "run_log_dir"):
        value = _text(summary.get(key), limit=2000)
        if value:
            return value
    for event in reversed(list(getattr(record, "events", []) or [])):
        payload = _event_payload(event)
        for key in ("log_path", "run_log_dir", "log_file"):
            value = _text(payload.get(key), limit=2000)
            if value:
                return value
    return ""


def _is_cancelled(record: Any) -> bool:
    return _value(record, "current_step").casefold() == "cancelled"


def entries_from_job_record(record: Any) -> list[dict[str, Any]]:
    """Build stable diagnostic records from one durable job record.

    The output deliberately contains a bounded excerpt rather than a traceback.
    The full exception and provider traces stay in the run log referenced by
    ``log_path``.
    """

    project_id = _value(record, "project_id")
    if not project_id:
        return []

    job_id = _value(record, "job_id")
    job_kind = _value(record, "kind")
    job_label = _value(record, "label")
    status = _value(record, "status").casefold()
    entries: list[dict[str, Any]] = []

    for event in list(getattr(record, "events", []) or []):
        step = _value(event, "step")
        if step not in _ERROR_EVENT_STEPS:
            continue
        payload = _event_payload(event)
        attempt = _attempt_label(payload)
        error = _text(payload.get("error") or payload.get("message"))
        log_path = _text(payload.get("log_file") or payload.get("run_log_dir"), limit=2000)
        entry_id = _entry_id(
            job_id,
            _value(event, "at"),
            step,
            payload.get("task"),
            attempt,
            error,
            log_path,
        )
        entries.append(
            {
                "id": entry_id,
                "time": _value(event, "at"),
                "project_id": project_id,
                "job_id": job_id,
                "job_kind": job_kind,
                "job_label": job_label,
                "kind": "格式已修复" if step in _AUTO_RESOLVED_EVENT_STEPS else "格式错误",
                "task": _text(payload.get("task") or step, limit=200),
                "attempt": attempt,
                "error": error or "模型输出需要修复",
                "excerpt": _text(payload.get("raw_excerpt"), limit=1600),
                "log_path": log_path,
                "auto_resolved": step in _AUTO_RESOLVED_EVENT_STEPS,
                **_auto_repair_guidance(
                    step=step,
                    payload=payload,
                    error=error,
                ),
            }
        )

    summary = _mapping(getattr(record, "error_summary", {}))
    error = _text(summary.get("summary") or summary.get("detail") or _value(record, "error"))
    if status == "failed" and not _is_cancelled(record):
        step = _value(record, "current_step") or "failed"
        entry_id = _entry_id(job_id, "job_failed", step, error)
        entries.append(
            {
                "id": entry_id,
                "time": _value(record, "updated_at"),
                "project_id": project_id,
                "job_id": job_id,
                "job_kind": job_kind,
                "job_label": job_label,
                "kind": "任务失败",
                "task": step,
                "attempt": _attempt_label(summary),
                "error": error or "任务失败，但未收到异常摘要。",
                "excerpt": _text(summary.get("detail"), limit=1600),
                "log_path": _run_log_path(record, summary),
                "auto_resolved": bool(summary.get("auto_resolved", False)),
                **_auto_repair_guidance(
                    step=step,
                    payload={},
                    error=error,
                    summary=summary,
                ),
            }
        )
    return entries


class TaskFlowErrorLog:
    """A compact persistent error index, independent of desktop UI code."""

    def __init__(self, storage_root: Path | str) -> None:
        self._storage_root = Path(storage_root).expanduser()

    def archive_job(self, record: Any) -> int:
        entries = entries_from_job_record(record)
        if not entries:
            return 0
        project_id = _value(record, "project_id")
        records_dir = self._records_dir(project_id)
        records_dir.mkdir(parents=True, exist_ok=True)
        existing_ids = self._record_ids(records_dir)
        added = 0
        for entry in entries:
            entry_id = _text(entry.get("id"), limit=200)
            if not entry_id or entry_id in existing_ids:
                continue
            path = records_dir / f"{entry_id}.json"
            atomic_write_text(path, json.dumps(entry, ensure_ascii=False, indent=2, default=str))
            existing_ids.add(entry_id)
            added += 1
        self._trim(records_dir)
        return added

    def entries(self, *, project_id: str = "") -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for records_dir in self._all_records_dirs(project_id=project_id):
            try:
                paths = list(records_dir.glob("*.json"))
            except OSError:
                continue
            for path in paths:
                try:
                    raw = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    continue
                if isinstance(raw, dict) and str(raw.get("id") or "").strip():
                    results.append(raw)
        return sorted(results, key=lambda item: str(item.get("time") or ""), reverse=True)

    def summary(self, *, project_id: str = "") -> dict[str, Any]:
        """Return a bounded aggregate without exposing individual diagnostics."""

        entries = self.entries(project_id=project_id)
        project_ids = {
            _text(entry.get("project_id"), limit=255)
            for entry in entries
            if _text(entry.get("project_id"), limit=255)
        }
        return {
            "schema_version": _SCHEMA_VERSION,
            "entry_count": len(entries),
            "project_count": len(project_ids),
            "latest_time": str(entries[0].get("time") or "") if entries else "",
        }

    def clear(self, *, project_id: str = "") -> int:
        """Remove compact diagnostic indexes while keeping full run logs intact."""

        removed = 0
        for archive_dir in self._all_archive_dirs(project_id=project_id):
            if not archive_dir.exists():
                continue
            try:
                shutil.rmtree(archive_dir)
            except OSError:
                continue
            removed += 1
        return removed

    def acknowledge_entries(
        self,
        entry_ids: set[str],
        *,
        acknowledged_at: str | None = None,
    ) -> list[str]:
        """Persist explicit human review for exact diagnostic entries.

        Acknowledgement is review metadata only: it neither retries the job nor
        changes provider/model health.  Automatic recoveries are already closed
        and intentionally remain unchanged.
        """

        timestamp = _text(acknowledged_at, limit=80) or datetime.now(timezone.utc).isoformat()
        return self._set_acknowledgement(entry_ids, timestamp)

    def reopen_entries(self, entry_ids: set[str]) -> list[str]:
        """Remove explicit human-review markers from exact diagnostic entries."""

        return self._set_acknowledgement(entry_ids, None)

    def remove_closed_entries(self, entry_ids: set[str]) -> ClosedTaskFlowErrorClearResult:
        """Prune only confirmed or automatically recovered diagnostic indexes.

        The closed-state check happens beside the durable records, not in the
        UI, so a stale browser view or hand-crafted command cannot remove a
        pending diagnostic.  Provider traces and full run logs live elsewhere
        and are never considered here.
        """

        wanted = {str(entry_id).strip() for entry_id in entry_ids if str(entry_id).strip()}
        if not wanted:
            return ClosedTaskFlowErrorClearResult([], [], [])

        matched: dict[str, list[tuple[Path, dict[str, Any]]]] = {}
        for records_dir in self._all_records_dirs(project_id=""):
            try:
                paths = list(records_dir.glob("*.json"))
            except OSError:
                continue
            for path in paths:
                raw = self._read_record(path)
                if raw is None:
                    continue
                entry_id = _text(raw.get("id"), limit=200)
                if entry_id not in wanted:
                    continue
                matched.setdefault(entry_id, []).append((path, raw))

        removed: set[str] = set()
        retained_pending: set[str] = set()
        task_ids: set[str] = set()
        for entry_id, records in matched.items():
            if not all(is_closed_task_flow_error(raw) for _, raw in records):
                retained_pending.add(entry_id)
                continue
            removed_any = False
            for path, raw in records:
                try:
                    path.unlink()
                except OSError:
                    continue
                removed_any = True
                task_id = _text(raw.get("job_id"), limit=200)
                if task_id:
                    task_ids.add(task_id)
            if removed_any:
                removed.add(entry_id)
        return ClosedTaskFlowErrorClearResult(
            removed_entry_ids=sorted(removed),
            retained_pending_entry_ids=sorted(retained_pending),
            task_ids=sorted(task_ids),
        )

    def _set_acknowledgement(self, entry_ids: set[str], value: str | None) -> list[str]:
        wanted = {str(entry_id).strip() for entry_id in entry_ids if str(entry_id).strip()}
        if not wanted:
            return []

        changed: set[str] = set()
        for records_dir in self._all_records_dirs(project_id=""):
            try:
                paths = list(records_dir.glob("*.json"))
            except OSError:
                continue
            for path in paths:
                raw = self._read_record(path)
                if raw is None:
                    continue
                entry_id = _text(raw.get("id"), limit=200)
                if entry_id not in wanted:
                    continue
                if _truthy(raw.get("auto_resolved")):
                    continue
                previous = _text(raw.get("acknowledged_at"), limit=80)
                if value is None:
                    if not previous:
                        continue
                    raw.pop("acknowledged_at", None)
                else:
                    if previous == value:
                        continue
                    raw["acknowledged_at"] = value
                try:
                    atomic_write_text(
                        path, json.dumps(raw, ensure_ascii=False, indent=2, default=str)
                    )
                except OSError:
                    continue
                changed.add(entry_id)
        return sorted(changed)

    @staticmethod
    def _read_record(path: Path) -> dict[str, Any] | None:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return raw if isinstance(raw, dict) else None

    def _record_ids(self, records_dir: Path) -> set[str]:
        try:
            paths = list(records_dir.glob("*.json"))
        except OSError:
            return set()
        record_ids: set[str] = set()
        for path in paths:
            raw = self._read_record(path)
            if raw is None:
                continue
            entry_id = _text(raw.get("id"), limit=200)
            if entry_id:
                record_ids.add(entry_id)
        return record_ids

    def _records_dir(self, project_id: str) -> Path:
        return (
            ProjectLayout(self._storage_root / project_id).states_dir
            / _ARCHIVE_DIRNAME
            / _RECORDS_DIRNAME
        )

    def _archive_dir(self, project_id: str) -> Path:
        return self._records_dir(project_id).parent

    def _all_records_dirs(self, *, project_id: str) -> list[Path]:
        if project_id:
            return [self._records_dir(project_id)]
        try:
            project_dirs = [path for path in self._storage_root.iterdir() if path.is_dir()]
        except OSError:
            return []
        return [self._records_dir(path.name) for path in project_dirs]

    def _all_archive_dirs(self, *, project_id: str) -> list[Path]:
        if project_id:
            return [self._archive_dir(project_id)]
        try:
            project_dirs = [path for path in self._storage_root.iterdir() if path.is_dir()]
        except OSError:
            return []
        return [self._archive_dir(path.name) for path in project_dirs]

    @staticmethod
    def _trim(records_dir: Path) -> None:
        try:
            paths = sorted(
                records_dir.glob("*.json"), key=lambda path: path.stat().st_mtime, reverse=True
            )
        except OSError:
            return
        for path in paths[_MAX_ENTRIES_PER_PROJECT:]:
            try:
                path.unlink()
            except OSError:
                continue


__all__ = [
    "ClosedTaskFlowErrorClearResult",
    "TaskFlowErrorLog",
    "entries_from_job_record",
    "is_closed_task_flow_error",
    "project_auto_repair_guidance",
]
