"""Read model-call run logs for Desktop task observation."""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from novel_forge.desktop.jobs import DesktopJobRecord

_RUN_LOG_RE = re.compile(r"运行日志[:：]\s*(?P<path>.+)")
_PREVIEW_LIMIT = 12_000


@dataclass(frozen=True)
class ModelCallRecord:
    """One persisted model call from logs/<run_id>/model_calls."""

    path: Path
    event: str
    recorded_at: str = ""
    task: str = ""
    provider: str = ""
    model: str = ""
    route: str = ""
    status: str = "unknown"
    latency_ms: float = 0.0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cost_usd: float = 0.0
    max_tokens: int = 0
    temperature: float = 0.0
    finish_reason: str = ""
    attempt: int = 0
    max_attempts: int = 0
    next_max_tokens: int = 0
    will_retry: bool = False
    request_preview: str = ""
    response_preview: str = ""
    error_preview: str = ""


@dataclass(frozen=True)
class ModelCallActivity:
    """Latest live model-call lifecycle update attached to a Desktop job."""

    event: str = ""
    status: str = ""
    task: str = ""
    provider: str = ""
    model: str = ""
    attempt: int = 0
    max_attempts: int = 0
    max_tokens: int = 0
    next_max_tokens: int = 0
    finish_reason: str = ""
    latency_ms: float = 0.0


@dataclass(frozen=True)
class ModelCallSnapshot:
    """Aggregated model-call state for one Desktop job."""

    run_dir: Path | None
    records: tuple[ModelCallRecord, ...] = ()
    missing_reason: str = ""
    truncated: bool = False
    activity: ModelCallActivity | None = None

    @property
    def total_calls(self) -> int:
        return len(self.records)

    @property
    def success_count(self) -> int:
        return sum(1 for item in self.records if item.status == "success")

    @property
    def failed_count(self) -> int:
        return sum(1 for item in self.records if item.status != "success")

    @property
    def retry_count(self) -> int:
        return sum(1 for item in self.records if item.will_retry)

    @property
    def terminal_failed_count(self) -> int:
        return sum(
            1 for item in self.records if item.status != "success" and not item.will_retry
        )

    @property
    def prompt_tokens(self) -> int:
        return sum(item.prompt_tokens for item in self.records)

    @property
    def completion_tokens(self) -> int:
        return sum(item.completion_tokens for item in self.records)

    @property
    def total_tokens(self) -> int:
        total = sum(item.total_tokens for item in self.records)
        return total or self.prompt_tokens + self.completion_tokens

    @property
    def total_cost_usd(self) -> float:
        return sum(item.cost_usd for item in self.records)

    @property
    def total_latency_ms(self) -> float:
        return sum(item.latency_ms for item in self.records)

    def provider_token_totals(self) -> tuple[tuple[str, int], ...]:
        totals: Counter[str] = Counter()
        for item in self.records:
            key = " / ".join(part for part in (item.provider, item.model) if part) or "unknown"
            totals[key] += item.total_tokens or item.prompt_tokens + item.completion_tokens
        return tuple(totals.most_common(6))

    def task_latency_totals(self) -> tuple[tuple[str, float], ...]:
        totals: Counter[str] = Counter()
        for item in self.records:
            totals[item.task or "unknown"] += item.latency_ms
        return tuple(totals.most_common(6))


def load_model_call_snapshot(job: DesktopJobRecord, *, max_files: int = 120) -> ModelCallSnapshot:
    """Load and aggregate persisted model-call logs for a desktop job."""

    activity = latest_model_call_activity(job)
    run_dir = _run_dir_for_job(job)
    if run_dir is None:
        return ModelCallSnapshot(
            run_dir=None,
            missing_reason="运行日志尚未写入任务结果；任务完成后会显示调用明细。",
            activity=activity,
        )
    model_calls_dir = run_dir / "model_calls"
    if not model_calls_dir.exists():
        return ModelCallSnapshot(
            run_dir=run_dir,
            missing_reason="未找到 model_calls 目录。",
            activity=activity,
        )
    files = sorted(path for path in model_calls_dir.rglob("*.json") if path.is_file())
    truncated = len(files) > max_files
    selected = files[-max_files:] if truncated else files
    records: list[ModelCallRecord] = []
    for path in selected:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(data, dict):
            records.append(_record_from_payload(path, data))
    if not records:
        return ModelCallSnapshot(
            run_dir=run_dir,
            missing_reason="model_calls 目录中没有可读取的调用记录。",
            activity=activity,
        )
    return ModelCallSnapshot(
        run_dir=run_dir,
        records=tuple(records),
        truncated=truncated,
        activity=activity,
    )


def latest_model_call_activity(job: DesktopJobRecord) -> ModelCallActivity | None:
    """Return the newest live call update without reading prompt or response bodies."""

    for event in reversed(tuple(getattr(job, "events", ()) or ())):
        if str(getattr(event, "step", "") or "") != "model_call_update":
            continue
        payload = getattr(event, "payload", {})
        if not isinstance(payload, dict):
            continue
        return ModelCallActivity(
            event=str(payload.get("event") or "").strip(),
            status=str(payload.get("status") or "").strip(),
            task=str(payload.get("task") or "").strip(),
            provider=str(payload.get("provider") or "").strip(),
            model=str(payload.get("model") or "").strip(),
            attempt=_safe_int(payload.get("attempt")),
            max_attempts=_safe_int(payload.get("max_attempts")),
            max_tokens=_safe_int(payload.get("max_tokens")),
            next_max_tokens=_safe_int(payload.get("next_max_tokens")),
            finish_reason=str(payload.get("finish_reason") or "").strip(),
            latency_ms=_safe_float(payload.get("latency_ms")),
        )
    return None


def _run_dir_for_job(job: DesktopJobRecord) -> Path | None:
    result = job.result if isinstance(job.result, dict) else {}
    for raw in (
        result.get("run_log_dir"),
        result.get("run_dir"),
        (job.error_summary or {}).get("run_log_dir")
        if isinstance(job.error_summary, dict)
        else "",
    ):
        path = _clean_path(raw)
        if path:
            return Path(path)
    for event in getattr(job, "events", []) or []:
        payload = getattr(event, "payload", {})
        if not isinstance(payload, dict):
            continue
        for key in ("run_log_dir", "run_dir"):
            path = _clean_path(payload.get(key))
            if path:
                return Path(path)
    nested = _run_dir_from_error_payload(getattr(job, "error_summary", {}))
    if nested:
        return Path(nested)
    for line in str(job.error or "").splitlines():
        match = _RUN_LOG_RE.search(line)
        if match:
            path = _clean_path(match.group("path"))
            if path:
                return Path(path)
    return None


def _run_dir_from_error_payload(raw: Any) -> str:
    if isinstance(raw, dict):
        for key in ("run_log_dir", "run_dir"):
            path = _clean_path(raw.get(key))
            if path:
                return path
        for value in raw.values():
            path = _run_dir_from_error_payload(value)
            if path:
                return path
        return ""
    if isinstance(raw, (list, tuple, set)):
        for value in raw:
            path = _run_dir_from_error_payload(value)
            if path:
                return path
        return ""
    if isinstance(raw, str):
        for line in raw.splitlines():
            match = _RUN_LOG_RE.search(line)
            if match:
                path = _clean_path(match.group("path"))
                if path:
                    return path
    return ""


def _record_from_payload(path: Path, data: dict[str, Any]) -> ModelCallRecord:
    event = str(data.get("event") or "").strip()
    response = data.get("response") if isinstance(data.get("response"), dict) else {}
    request = data.get("request") if isinstance(data.get("request"), dict) else {}
    prompt_tokens = _safe_int(data.get("prompt_tokens") or response.get("prompt_tokens"))
    completion_tokens = _safe_int(
        data.get("completion_tokens") or response.get("completion_tokens")
    )
    total_tokens = _safe_int(data.get("total_tokens") or response.get("total_tokens"))
    will_retry = bool(data.get("will_retry"))
    response_preview = _clip(_response_preview(response))
    error_preview = _clip(_error_preview(data.get("error")))
    if not error_preview and event in {
        "api_call_empty_response",
        "api_call_length_truncated",
    }:
        response_preview = _clip(_retry_diagnostic_preview(event, data, will_retry=will_retry))
    return ModelCallRecord(
        path=path,
        event=event,
        recorded_at=str(data.get("recorded_at") or "").strip(),
        task=str(data.get("task") or request.get("task_type") or "").strip(),
        provider=str(data.get("provider") or "").strip(),
        model=str(data.get("model") or response.get("model_id") or "").strip(),
        route=str(data.get("route") or "").strip(),
        status=_status_from_event(event),
        latency_ms=_safe_float(data.get("latency_ms") or response.get("latency_ms")),
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens or prompt_tokens + completion_tokens,
        cost_usd=_safe_float(data.get("cost_usd") or response.get("cost_usd")),
        max_tokens=_safe_int(data.get("max_tokens") or request.get("max_tokens")),
        temperature=_safe_float(data.get("temperature") or request.get("temperature")),
        finish_reason=str(data.get("finish_reason") or response.get("finish_reason") or "").strip(),
        attempt=_safe_int(data.get("attempt")),
        max_attempts=_safe_int(data.get("max_attempts")),
        next_max_tokens=_safe_int(data.get("next_max_tokens")),
        will_retry=will_retry,
        request_preview=_clip(_request_preview(request)),
        response_preview=response_preview,
        error_preview=error_preview,
    )


def _status_from_event(event: str) -> str:
    if event in {"api_call_done", "api_stream_done"}:
        return "success"
    if event in {"api_call_timeout"}:
        return "timeout"
    if event in {"api_call_incomplete", "api_stream_incomplete"}:
        return "incomplete"
    if event in {"api_call_error", "api_stream_error"}:
        return "error"
    if event == "api_call_empty_response":
        return "empty_response"
    if event == "api_call_length_truncated":
        return "length_truncated"
    return event or "unknown"


def _retry_diagnostic_preview(event: str, data: dict[str, Any], *, will_retry: bool) -> str:
    if event == "api_call_empty_response":
        message = "模型返回了空响应，本次没有可展示的输出。"
        if will_retry:
            message += "系统已自动发起下一次尝试。"
        finish_reason = str(data.get("finish_reason") or "").strip()
        if finish_reason:
            message += f"\n完成原因：{finish_reason}"
        return message

    current_limit = _safe_int(data.get("max_tokens"))
    next_limit = _safe_int(data.get("next_max_tokens"))
    completion_tokens = _safe_int(data.get("completion_tokens"))
    message = "模型输出达到长度上限，当前结果可能不完整。"
    if completion_tokens:
        message += f"\n已生成 Token：{completion_tokens:,}"
    if current_limit:
        message += f"\n当前输出上限：{current_limit:,}"
    if will_retry and next_limit:
        message += f"\n系统已将输出上限提升至 {next_limit:,} 并自动重试。"
    return message


def _request_preview(request: dict[str, Any]) -> str:
    messages = request.get("messages")
    if not isinstance(messages, list):
        return json.dumps(request, ensure_ascii=False, indent=2, default=str)
    parts: list[str] = []
    for message in messages:
        if not isinstance(message, dict):
            continue
        role = str(message.get("role") or "message").strip()
        content = _stringify_content(message.get("content"))
        if content:
            parts.append(f"[{role}]\n{content}")
    return "\n\n".join(parts)


def _response_preview(response: dict[str, Any]) -> str:
    content = response.get("content")
    if content is not None:
        return _stringify_content(content)
    return json.dumps(response, ensure_ascii=False, indent=2, default=str)


def _error_preview(raw: Any) -> str:
    if raw is None:
        return ""
    if isinstance(raw, str):
        return raw
    return json.dumps(raw, ensure_ascii=False, indent=2, default=str)


def _stringify_content(value: Any) -> str:
    if isinstance(value, str):
        return value
    if value is None:
        return ""
    return json.dumps(value, ensure_ascii=False, indent=2, default=str)


def _clean_path(raw: Any) -> str:
    text = str(raw or "").strip()
    if not text:
        return ""
    return text.strip("\"' ")


def _clip(text: str, *, limit: int = _PREVIEW_LIMIT) -> str:
    clean = str(text or "").strip()
    if len(clean) <= limit:
        return clean
    return clean[:limit] + "\n\n…已截断，仅显示前部预览。"


def _safe_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _safe_float(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0
