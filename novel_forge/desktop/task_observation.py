"""Window-level derived task observation state for Desktop UI."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any

from PySide6.QtCore import QObject, Signal

from novel_forge.common.token_usage import TokenUsage, apply_live_usage_payload
from novel_forge.desktop.jobs import (
    DesktopJobEvent,
    DesktopJobRecord,
    DesktopJobState,
)
from novel_forge.desktop.progress import compute_task_flow_progress, display_step_name_for_job
from novel_forge.desktop.stream_history import load_stream_history
from novel_forge.desktop.task_flow import (
    TaskFlowOutcome,
    TaskFlowScope,
    task_flow_is_cancelled,
    task_flow_job_chapter_number,
    task_flow_matches_scope,
    task_flow_outcome,
    task_flow_status_spec,
)
from novel_forge.pipeline.progress import display_step_name, is_non_progress_step_event

# Maximum total text length retained in the observation accumulator before
# the oldest segments are dropped.  This caps memory usage for very long
# streams; the render layer no longer needs its own clipping because the
# accumulator keeps the state bounded.
_STREAM_ACCUM_LIMIT = 50_000


# Backwards-compatible public name used by existing focus widgets.
TaskFocusScope = TaskFlowScope


@dataclass(frozen=True)
class StreamSegment:
    """One ordered piece of a stream, either content or reasoning.

    The ``kind`` is ``"content"`` (final answer text) or ``"reasoning"``
    (thinking / chain-of-thought).  The ordering of segments in
    :attr:`ObservedStreamState.segments` reflects the real interleaving
    produced by the model, so the UI can render a faithful interleaved
    view without re-inferring order.
    """

    kind: str
    text: str


@dataclass(frozen=True)
class ObservedStreamState:
    """Aggregated in-memory preview for one LLM stream."""

    job_id: str
    stream_id: str
    task: str = ""
    chapter_number: int = 0
    attempt: int = 0
    status: str = "streaming"
    text: str = ""
    text_length: int = 0
    reasoning_text: str = ""
    reasoning_length: int = 0
    segments: tuple[StreamSegment, ...] = ()
    started_at: str = ""
    updated_at: str = ""
    ended_at: str = ""
    error: str = ""
    discarded: bool = False
    source: str = "llm_stream"
    truncated: bool = False
    provider: str = ""
    model: str = ""
    max_tokens: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    latency_ms: float = 0.0
    cost_usd: float = 0.0
    operation_id: str = ""
    output_kind: str = ""
    validation_status: str = ""
    repair_source: str = ""


@dataclass(frozen=True)
class ObservedDecisionState:
    """Current human decision request/response state for one job."""

    job_id: str
    decision_id: str
    title: str = ""
    message: str = ""
    kind: str = ""
    project_id: str = ""
    chapter_number: int = 0
    options: tuple[dict[str, str], ...] = ()
    default_option: str = ""
    timeout_seconds: int = 0
    risk: str = ""
    cost_hint: str = ""
    status: str = "pending"
    choice: str = ""
    custom_text: str = ""
    timed_out: bool = False
    requested_at: str = ""
    resolved_at: str = ""
    approval_version: str = ""
    requires_explicit_approval: bool = False


@dataclass(frozen=True)
class ObservedTaskState:
    """Derived task state rendered by TaskFocusPanel."""

    job: DesktopJobRecord
    focus_reason: str
    current_node: str
    status_label: str
    status_tone: str
    stream: ObservedStreamState | None = None
    decision: ObservedDecisionState | None = None
    diagnostics: tuple[str, ...] = ()
    events: tuple[str, ...] = ()
    usage: TokenUsage = TokenUsage()
    progress_percent: int = 0

    @property
    def job_id(self) -> str:
        return self.job.job_id

    @property
    def has_active_decision(self) -> bool:
        return self.decision is not None and self.decision.status == "pending"

    @property
    def has_live_stream(self) -> bool:
        return (
            self.stream is not None
            and self.stream.source == "llm_stream"
            and self.stream.status == "streaming"
        )


@dataclass(frozen=True)
class ObservedAttentionGroup:
    """Project-level attention group for compact task observation UI.

    The store still tracks each job and stream independently; this grouping
    is a presentation layer that keeps the pet/dialog focused on the current
    project-level work instead of showing every historical job as a top-level
    node.
    """

    key: str
    label: str
    primary: ObservedTaskState
    states: tuple[ObservedTaskState, ...] = ()
    active_count: int = 0
    history_count: int = 0
    live_stream_count: int = 0
    decision_count: int = 0

    @property
    def job_id(self) -> str:
        return self.primary.job_id


_STREAM_EVENTS = {
    "llm_stream_start",
    "llm_stream_delta",
    "llm_stream_restart",
    "llm_stream_end",
    "llm_stream_error",
    "llm_stream_validation",
}
_MODEL_CALL_EVENTS = {"model_call_update"}
_DECISION_EVENTS = {
    "human_decision_requested",
    "human_decision_resolved",
    "human_decision_timeout",
}
_DIAGNOSTIC_EVENTS = {
    "repair_repeated_issue_guard",
    "repair_strategy_diagnosis",
    "repair_attempt_guidance",
    "repair_round_focus",
    "format_validation_success",
    "human_decision_requested",
    "human_decision_resolved",
    "human_decision_timeout",
    "llm_stream_restart",
    "llm_stream_error",
}
_TERMINAL_RECENT_SECONDS = 10 * 60


def _parse_dt(value: str) -> datetime:
    try:
        dt = datetime.fromisoformat(str(value or ""))
    except ValueError:
        return datetime.min.replace(tzinfo=timezone.utc)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _seconds_since(value: str) -> float:
    dt = _parse_dt(value)
    if dt.year <= 1:
        return float("inf")
    return max(0.0, (datetime.now(timezone.utc) - dt).total_seconds())


def _chapter_number_for(job: DesktopJobRecord, payload: dict[str, Any] | None = None) -> int:
    payload = payload or {}
    for raw in (
        payload.get("chapter_number"),
        payload.get("chapter"),
        (job.result or {}).get("chapter_number"),
    ):
        try:
            text = str(raw or "").strip()
            if text:
                return int(text)
        except (TypeError, ValueError):
            pass
    return task_flow_job_chapter_number(job) or 0


def _safe_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _event_fingerprint(event: Any) -> str:
    """Return a stable fingerprint for a job event.

    Used as a cursor for incremental ingest.  Combines ``at``, ``step``
    and a hash of the payload so that two events with the same timestamp
    but different payloads are distinguishable.  The hash is truncated to
    keep the fingerprint compact.
    """
    at = str(getattr(event, "at", "") or "")
    step = str(getattr(event, "step", "") or "")
    payload = getattr(event, "payload", None)
    try:
        payload_json = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        payload_json = str(payload)
    payload_hash = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()[:12]
    return f"{at}|{step}|{payload_hash}"


def _display_task(raw: Any) -> str:
    task = str(raw or "").strip()
    if not task:
        return ""
    return display_step_name(task.lower())


def _event_summary(step: str, payload: dict[str, Any]) -> str:
    task = _display_task(payload.get("task"))
    if step == "llm_stream_start":
        return f"开始流式输出：{task or '文本生成'}"
    if step == "llm_stream_restart":
        attempt = payload.get("attempt")
        return f"流式重试：旧预览已丢弃{f'，第 {attempt} 次' if attempt else ''}"
    if step == "llm_stream_error":
        return f"流式错误：{str(payload.get('error') or payload.get('message') or '').strip()}"
    if step == "repair_repeated_issue_guard":
        return "重复问题守卫触发，倾向升级修复策略"
    if step == "repair_strategy_diagnosis":
        strategy = str(payload.get("preferred_strategy") or payload.get("strategy") or "").strip()
        reason = str(payload.get("reason") or payload.get("diagnostic_summary") or "").strip()
        return (
            "策略诊断" + (f"：{strategy}" if strategy else "") + (f" · {reason}" if reason else "")
        )
    if step == "repair_attempt_guidance":
        strategy = str(
            payload.get("strategy") or payload.get("mode") or payload.get("action") or ""
        ).strip()
        return "修复指引" + (f"：{strategy}" if strategy else "")
    if step == "repair_round_focus":
        selected = int(payload.get("selected_count") or 0)
        skipped = int(payload.get("skipped_count") or 0)
        return f"本轮聚焦 {selected} 个问题" + (f"，暂缓 {skipped} 个" if skipped else "")
    if step == "format_validation_success":
        task_label = task or str(payload.get("task") or "模型输出").strip()
        source = str(payload.get("parse_source") or "").strip()
        suffix = f" · {source}" if source else ""
        return f"格式校验通过：{task_label}{suffix}"
    if step == "human_decision_requested":
        return f"等待确认：{str(payload.get('title') or '高风险修复决策').strip()}"
    if step == "human_decision_resolved":
        return f"确认完成：{str(payload.get('choice') or '').strip()}"
    if step == "human_decision_timeout":
        return f"确认超时：使用默认选项 {str(payload.get('choice') or '').strip()}"
    if step == "run_log_started":
        return "运行日志已就绪"
    if step == "model_call_update":
        task = task or str(payload.get("task") or "模型调用").strip()
        status = str(payload.get("status") or "").strip()
        if status == "running":
            return f"模型调用中：{task}"
        if status == "retrying":
            reason = (
                "返回空内容"
                if payload.get("event") == "api_call_empty_response"
                else "输出达到上限"
            )
            next_max = _safe_int(payload.get("next_max_tokens"))
            suffix = f"，输出上限提升至 {next_max}" if next_max else ""
            return f"模型调用自动重试：{task} · {reason}{suffix}"
        if status == "success":
            tokens = _safe_int(payload.get("total_tokens"))
            return f"模型调用完成：{task}" + (f" · Token {tokens}" if tokens else "")
        return f"模型调用异常：{task}"
    return display_step_name(step)


def _normalize_options(raw: Any) -> tuple[dict[str, str], ...]:
    if not isinstance(raw, list):
        return ()
    options: list[dict[str, str]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        option_id = str(item.get("id") or "").strip()
        if not option_id:
            continue
        options.append(
            {
                "id": option_id,
                "label": str(item.get("label") or option_id).strip(),
                "description": str(item.get("description") or "").strip(),
            }
        )
    return tuple(options)


class _StreamAccum:
    """Mutable accumulator for one stream during incremental ingest.

    The desktop job event log is a sliding window (latest 160 events).  To
    avoid re-replaying the entire event list on every ingest (which is
    O(n) per 90ms tick and compounds for long streams), we keep a per-
    stream accumulator that survives across ingests.  When a new batch of
    events arrives, only the unseen tail is applied.

    Segments are merged: consecutive same-kind chunks append to the last
    segment rather than creating a new one, keeping the segment count
    bounded.  When total text exceeds :data:`_STREAM_ACCUM_LIMIT`, the
    oldest segments are dropped and ``truncated`` is set so the UI can
    show a "前文已折叠" notice.
    """

    __slots__ = (
        "job_id",
        "stream_id",
        "task",
        "chapter_number",
        "attempt",
        "status",
        "segments",
        "started_at",
        "updated_at",
        "ended_at",
        "error",
        "discarded",
        "source",
        "truncated",
        "received_structured",
        "provider",
        "model",
        "max_tokens",
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
        "latency_ms",
        "cost_usd",
        "operation_id",
        "output_kind",
        "validation_status",
        "repair_source",
        "reported_text_length",
        "reported_reasoning_length",
    )

    def __init__(
        self,
        *,
        job_id: str,
        stream_id: str,
        task: str = "",
        chapter_number: int = 0,
        attempt: int = 0,
        source: str = "llm_stream",
        started_at: str = "",
    ) -> None:
        self.job_id = job_id
        self.stream_id = stream_id
        self.task = task
        self.chapter_number = chapter_number
        self.attempt = attempt
        self.status = "streaming"
        self.segments: list[StreamSegment] = []
        self.started_at = started_at
        self.updated_at = started_at
        self.ended_at = ""
        self.error = ""
        self.discarded = False
        self.source = source
        self.truncated = False
        self.received_structured = False
        self.provider = ""
        self.model = ""
        self.max_tokens = 0
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.total_tokens = 0
        self.latency_ms = 0.0
        self.cost_usd = 0.0
        self.operation_id = ""
        self.output_kind = ""
        self.validation_status = ""
        self.repair_source = ""
        self.reported_text_length = 0
        self.reported_reasoning_length = 0

    def append_segment(self, kind: str, text: str) -> None:
        """Append text to the segment list, merging same-kind tail."""
        if not text:
            return
        if self.segments and self.segments[-1].kind == kind:
            last = self.segments[-1]
            self.segments[-1] = StreamSegment(kind=kind, text=last.text + text)
        else:
            self.segments.append(StreamSegment(kind=kind, text=text))
        self._enforce_limit()

    def _enforce_limit(self) -> None:
        """Drop oldest segments if total text exceeds the accumulator limit."""
        total = sum(len(seg.text) for seg in self.segments)
        if total <= _STREAM_ACCUM_LIMIT:
            return
        self.truncated = True
        while self.segments and total > _STREAM_ACCUM_LIMIT:
            dropped = self.segments.pop(0)
            total -= len(dropped.text)

    def _replace_content_text(self, full_text: str) -> None:
        """Replace all content segments with a single full-text segment.

        Used for legacy delta events that carry the cumulative ``text``
        field rather than incremental segments.  Reasoning segments are
        preserved (they sit before content in the segment list).
        """
        reasoning_segs = [seg for seg in self.segments if seg.kind == "reasoning"]
        self.segments = reasoning_segs + [StreamSegment(kind="content", text=full_text)]
        self._enforce_limit()

    @property
    def text(self) -> str:
        return "".join(seg.text for seg in self.segments if seg.kind == "content")

    @property
    def reasoning_text(self) -> str:
        return "".join(seg.text for seg in self.segments if seg.kind == "reasoning")

    @property
    def text_length(self) -> int:
        return sum(len(seg.text) for seg in self.segments if seg.kind == "content")

    @property
    def reasoning_length(self) -> int:
        return sum(len(seg.text) for seg in self.segments if seg.kind == "reasoning")

    def to_state(self) -> ObservedStreamState:
        return ObservedStreamState(
            job_id=self.job_id,
            stream_id=self.stream_id,
            task=self.task,
            chapter_number=self.chapter_number,
            attempt=self.attempt,
            status=self.status,
            text=self.text,
            text_length=max(self.text_length, self.reported_text_length),
            reasoning_text=self.reasoning_text,
            reasoning_length=max(self.reasoning_length, self.reported_reasoning_length),
            segments=tuple(self.segments),
            started_at=self.started_at,
            updated_at=self.updated_at,
            ended_at=self.ended_at,
            error=self.error,
            discarded=self.discarded,
            source=self.source,
            truncated=self.truncated,
            provider=self.provider,
            model=self.model,
            max_tokens=self.max_tokens,
            prompt_tokens=self.prompt_tokens,
            completion_tokens=self.completion_tokens,
            total_tokens=self.total_tokens,
            latency_ms=self.latency_ms,
            cost_usd=self.cost_usd,
            operation_id=self.operation_id,
            output_kind=self.output_kind,
            validation_status=self.validation_status,
            repair_source=self.repair_source,
        )


class TaskObservationStore(QObject):
    """Derive user-facing task focus state from Desktop job events."""

    changed = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._managed_jobs: list[DesktopJobRecord] = []
        self._external_jobs: dict[str, DesktopJobRecord] = {}
        self._jobs: list[DesktopJobRecord] = []
        self._streams_by_job: dict[str, tuple[ObservedStreamState, ...]] = {}
        self._decisions_by_job: dict[str, ObservedDecisionState] = {}
        self._diagnostics_by_job: dict[str, tuple[str, ...]] = {}
        self._events_by_job: dict[str, tuple[str, ...]] = {}
        self._external_decisions: dict[str, ObservedDecisionState] = {}
        # Incremental ingest state.
        # Per-job per-stream accumulators: job_id -> {stream_id -> _StreamAccum}.
        self._stream_accum: dict[str, dict[str, _StreamAccum]] = {}
        # Per-job fingerprint of the last processed event, for delta detection.
        # The fingerprint is "at|step|payload_hash".  When the stored
        # fingerprint is not found among the job's current events (e.g. the
        # sliding window evicted it, or the job was rebuilt), the job's
        # accumulators are reset and a full replay is performed.
        self._job_last_event_fp: dict[str, str] = {}

    def ingest_jobs(self, jobs: list[DesktopJobRecord]) -> None:
        """Refresh derived state from the latest DesktopJobRecord snapshot.

        Uses incremental processing: for each job, only events after the
        last processed fingerprint are applied to the stream accumulators.
        If the fingerprint cannot be located (sliding window evicted it or
        the job was rebuilt), the job's accumulators are reset and a full
        replay is performed.  This keeps per-tick cost proportional to the
        number of *new* events rather than the total event count.
        """

        self._managed_jobs = list(jobs)
        self._prune_external_jobs()
        self._jobs = [*self._managed_jobs, *self._external_jobs.values()]
        seen_job_ids = {job.job_id for job in self._jobs}
        # Drop accumulators for jobs that are no longer present.
        stale = [jid for jid in self._stream_accum if jid not in seen_job_ids]
        for jid in stale:
            self._stream_accum.pop(jid, None)
            self._job_last_event_fp.pop(jid, None)

        streams: dict[str, tuple[ObservedStreamState, ...]] = {}
        decisions: dict[str, ObservedDecisionState] = {}
        diagnostics: dict[str, tuple[str, ...]] = {}
        events: dict[str, tuple[str, ...]] = {}

        for job in self._jobs:
            new_events = self._select_new_events(job)
            if new_events is None:
                # Full replay fallback.  Prefer persistent run logs when the
                # job event window has already slid past the last cursor; this
                # keeps a long live preview from vanishing back to the empty
                # placeholder.  If the log is unavailable, replay the current
                # in-memory window as before.
                rebuilt_until = self._rebuild_from_run_log(job)
                if rebuilt_until is None:
                    self._stream_accum.pop(job.job_id, None)
                    new_events = job.events
                else:
                    new_events = [
                        event
                        for event in job.events
                        if _parse_dt(str(getattr(event, "at", "") or "")) > rebuilt_until
                    ]
            self._apply_events_incremental(job, new_events)
            accum_map = self._stream_accum.get(job.job_id, {})
            self._settle_terminal_streams(job, accum_map)
            states = sorted(
                (accum.to_state() for accum in accum_map.values()),
                key=lambda item: _parse_dt(item.updated_at),
            )
            streams[job.job_id] = tuple(states)
            decision = self._decision_for_job(job)
            if decision is None:
                decision = self._external_decisions.get(job.job_id)
            elif decision.status != "pending":
                self._external_decisions.pop(job.job_id, None)
            if decision is not None:
                decisions[job.job_id] = decision
            diagnostics[job.job_id] = self._diagnostics_for_job(job)
            events[job.job_id] = self._events_for_job(job)
            if job.events:
                last = job.events[-1]
                self._job_last_event_fp[job.job_id] = _event_fingerprint(last)

        self._streams_by_job = streams
        self._decisions_by_job = decisions
        self._diagnostics_by_job = diagnostics
        self._events_by_job = events
        self.changed.emit()

    @staticmethod
    def _settle_terminal_streams(
        job: DesktopJobRecord,
        accum_map: dict[str, _StreamAccum],
    ) -> None:
        """Close stale live previews when their owning job is already terminal."""

        outcome = task_flow_outcome(job)
        if outcome not in {
            TaskFlowOutcome.SUCCEEDED,
            TaskFlowOutcome.FAILED,
            TaskFlowOutcome.CANCELLED,
        }:
            return
        for accum in accum_map.values():
            if accum.status not in {"streaming", "running"}:
                continue
            accum.ended_at = job.updated_at or accum.updated_at
            accum.updated_at = accum.ended_at
            if outcome == TaskFlowOutcome.SUCCEEDED:
                accum.status = "complete"
            elif outcome == TaskFlowOutcome.CANCELLED:
                accum.status = "restarted"
                accum.discarded = True
                accum.error = ""
            else:
                accum.status = "error"
                accum.error = str(job.error or "").strip()

    def begin_external_task(
        self,
        task_id: str,
        *,
        kind: str,
        label: str,
        project_id: str = "",
        current_step: str = "",
        chapter_number: int = 0,
    ) -> str:
        """Register a non-JobManager worker in the shared observation graph."""

        raw_id = str(task_id or "").strip() or hashlib.sha256(_now_iso().encode()).hexdigest()[:12]
        job_id = raw_id if raw_id.startswith("external:") else f"external:{raw_id}"
        now = _now_iso()
        self._external_jobs[job_id] = DesktopJobRecord(
            job_id=job_id,
            kind=str(kind or "external_task").strip(),
            label=str(label or "后台任务").strip(),
            project_id=str(project_id or "").strip(),
            status=DesktopJobState.RUNNING,
            created_at=now,
            updated_at=now,
            current_step=str(current_step or "").strip(),
            result={"chapter_number": max(0, int(chapter_number or 0))},
        )
        self.ingest_jobs(self._managed_jobs)
        return job_id

    def ingest_external_step(
        self,
        job_id: str,
        step: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        """Forward an external worker event through the normal stream pipeline."""

        record = self._external_jobs.get(str(job_id or "").strip())
        if record is None or record.status in {DesktopJobState.FAILED, DesktopJobState.SUCCEEDED}:
            return
        event_payload = dict(payload or {})
        now = _now_iso()
        event_step = str(step or "progress").strip()
        call_id = str(event_payload.get("call_id") or "").strip()
        duplicate_model_success = bool(
            call_id
            and event_step == "model_call_update"
            and str(event_payload.get("status") or "").strip().lower() == "success"
            and any(
                event.step == "model_call_update"
                and str(event.payload.get("call_id") or "").strip() == call_id
                and str(event.payload.get("status") or "").strip().lower() == "success"
                for event in record.events
            )
        )
        record.updated_at = now
        if not is_non_progress_step_event(event_step):
            record.current_step = event_step
            record.current_step_payload = event_payload
        record.events.append(DesktopJobEvent(at=now, step=event_step, payload=event_payload))
        if len(record.events) > 160:
            record.events = record.events[-160:]
        new_model_success = (
            event_step == "model_call_update"
            and str(event_payload.get("status") or "").strip().lower() == "success"
            and not duplicate_model_success
        )
        if new_model_success or "tokens_so_far" in event_payload:
            record.cumulative_tokens, record.cumulative_cost_usd = apply_live_usage_payload(
                record.cumulative_tokens,
                record.cumulative_cost_usd,
                event_payload,
                is_model_call=new_model_success,
            )
        self.ingest_jobs(self._managed_jobs)

    def complete_external_task(
        self,
        job_id: str,
        *,
        error: str = "",
    ) -> None:
        """Mark an external task terminal while preserving a prior failure."""

        record = self._external_jobs.get(str(job_id or "").strip())
        if record is None:
            return
        if record.status == DesktopJobState.FAILED and not error:
            return
        record.updated_at = _now_iso()
        for accum in self._stream_accum.get(record.job_id, {}).values():
            if accum.status != "streaming":
                continue
            accum.status = "error" if error else "complete"
            accum.ended_at = record.updated_at
            if error:
                accum.error = str(error).strip()
        if error:
            record.status = DesktopJobState.FAILED
            record.error = str(error).strip()
            record.current_step = "failed"
        else:
            record.status = DesktopJobState.SUCCEEDED
            record.current_step = "completed"
        self.ingest_jobs(self._managed_jobs)

    def _prune_external_jobs(self) -> None:
        stale_ids = [
            job_id
            for job_id, job in self._external_jobs.items()
            if job.status in {DesktopJobState.FAILED, DesktopJobState.SUCCEEDED}
            and _seconds_since(job.updated_at) > _TERMINAL_RECENT_SECONDS
        ]
        for job_id in stale_ids:
            self._external_jobs.pop(job_id, None)

    def _select_new_events(self, job: DesktopJobRecord) -> list[Any] | None:
        """Return the unseen tail of ``job.events``, or None for full replay.

        Locates the last-processed event fingerprint among the job's current
        events.  If found, returns all events *after* it.  If not found
        (the sliding window evicted it, or the job was rebuilt with a
        different event stream), returns None to signal that the caller
        should reset accumulators and replay from scratch.
        """
        last_fp = self._job_last_event_fp.get(job.job_id)
        if not last_fp or not job.events:
            return None
        for idx, event in enumerate(job.events):
            if _event_fingerprint(event) == last_fp:
                return list(job.events[idx + 1 :])
        # Fingerprint not found among current events — full replay needed.
        return None

    def _rebuild_from_run_log(self, job: DesktopJobRecord) -> datetime | None:
        """Rebuild LLM stream accumulators from a persisted run log if available."""

        run_log_dir = _run_log_dir_for_job(job)
        if not run_log_dir:
            return None
        history = load_stream_history(run_log_dir)
        if not history:
            return None
        accum_map: dict[str, _StreamAccum] = {}
        latest = datetime.min.replace(tzinfo=timezone.utc)
        for stream in history:
            accum = _StreamAccum(
                job_id=job.job_id,
                stream_id=stream.stream_id,
                task=stream.task,
                attempt=stream.attempt,
                source="llm_stream",
                started_at=stream.started_at,
            )
            accum.status = stream.status
            accum.ended_at = stream.ended_at
            accum.error = stream.error
            accum.discarded = stream.discarded
            accum.segments = [
                StreamSegment(kind=seg.kind, text=seg.text)
                for seg in stream.segments
                if seg.kind in {"content", "reasoning"} and seg.text
            ]
            accum.received_structured = bool(accum.segments)
            accum.updated_at = stream.ended_at or stream.started_at
            accum._enforce_limit()
            accum_map[stream.stream_id] = accum
            latest = max(latest, _parse_dt(accum.updated_at))
        self._stream_accum[job.job_id] = accum_map
        return latest if latest.year > 1 else None

    def _apply_events_incremental(self, job: DesktopJobRecord, events: list[Any]) -> None:
        """Apply a list of events to the job's stream accumulators.

        Creates accumulators on demand for new stream_ids.  Each event
        type updates the accumulator state.  Segment-bearing delta events
        append to the accumulator's segment list (merging same-kind tail),
        preserving the real interleaving order of content and reasoning.
        """
        accum_map = self._stream_accum.setdefault(job.job_id, {})
        for event in events:
            step = getattr(event, "step", "")
            if step not in _STREAM_EVENTS and step not in _MODEL_CALL_EVENTS:
                continue
            payload = event.payload if isinstance(event.payload, dict) else {}
            if step == "model_call_update":
                stream_id = str(payload.get("call_id") or "").strip()
            else:
                stream_id = str(payload.get("stream_id") or "").strip()
            if not stream_id:
                continue
            accum = accum_map.get(stream_id)
            if accum is None:
                accum = _StreamAccum(
                    job_id=job.job_id,
                    stream_id=stream_id,
                    source="model_call" if step == "model_call_update" else "llm_stream",
                    started_at=getattr(event, "at", ""),
                )
                accum_map[stream_id] = accum
            self._apply_one_event(accum, job, step, payload, getattr(event, "at", ""))

    def _apply_one_event(
        self,
        accum: _StreamAccum,
        job: DesktopJobRecord,
        step: str,
        payload: dict[str, Any],
        at: str,
    ) -> None:
        """Mutate a single accumulator to reflect one event."""
        accum.updated_at = at or accum.updated_at
        if step in _STREAM_EVENTS:
            accum.operation_id = str(
                payload.get("operation_id") or accum.operation_id or ""
            ).strip()
            accum.output_kind = str(
                payload.get("output_kind") or accum.output_kind or ""
            ).strip()
            accum.repair_source = str(
                payload.get("repair_source") or accum.repair_source or ""
            ).strip()
            validation_status = str(payload.get("validation_status") or "").strip()
            if validation_status:
                accum.validation_status = validation_status
            text_length = _safe_int(payload.get("text_length"))
            reasoning_length = _safe_int(payload.get("reasoning_length"))
            if text_length:
                accum.reported_text_length = max(accum.reported_text_length, text_length)
            if reasoning_length:
                accum.reported_reasoning_length = max(
                    accum.reported_reasoning_length,
                    reasoning_length,
                )
        if step == "model_call_update":
            status = str(payload.get("status") or "").strip()
            accum.provider = str(payload.get("provider") or accum.provider or "").strip()
            accum.model = str(payload.get("model") or accum.model or "").strip()
            accum.max_tokens = _safe_int(payload.get("max_tokens")) or accum.max_tokens
            accum.prompt_tokens = _safe_int(payload.get("prompt_tokens")) or accum.prompt_tokens
            accum.completion_tokens = (
                _safe_int(payload.get("completion_tokens")) or accum.completion_tokens
            )
            accum.total_tokens = _safe_int(payload.get("total_tokens")) or accum.total_tokens
            try:
                accum.latency_ms = float(payload.get("latency_ms") or accum.latency_ms or 0.0)
            except (TypeError, ValueError):
                pass
            try:
                accum.cost_usd = float(payload.get("cost_usd") or accum.cost_usd or 0.0)
            except (TypeError, ValueError):
                pass
            if status in {"running", "retrying"}:
                # ``model_call_update`` is a lifecycle event, not proof that
                # the provider exposes incremental response chunks.  Keep it
                # distinct from a real ``llm_stream`` so the UI does not
                # advertise a live stream while it can only wait for the
                # completed response preview.
                accum.status = "running"
                if status == "retrying":
                    next_max_tokens = _safe_int(payload.get("next_max_tokens"))
                    if next_max_tokens:
                        accum.max_tokens = next_max_tokens
                accum.task = str(payload.get("task") or accum.task or "").strip()
                accum.chapter_number = _chapter_number_for(job, payload) or accum.chapter_number
                accum.started_at = accum.started_at or at
                accum.error = ""
                accum.discarded = False
                accum.source = "model_call"
                return
            text = str(payload.get("response_preview") or payload.get("error_preview") or "")
            accum.status = "complete" if status == "success" else "error"
            accum.task = str(payload.get("task") or accum.task or "").strip()
            accum.chapter_number = _chapter_number_for(job, payload) or accum.chapter_number
            accum.ended_at = at
            if text:
                accum.segments = [StreamSegment(kind="content", text=text)]
                accum._enforce_limit()
            accum.error = str(payload.get("error_preview") or "").strip()
            accum.source = "model_call"
            return
        if step == "llm_stream_start":
            accum.status = "streaming"
            accum.task = str(payload.get("task") or accum.task or "").strip()
            accum.chapter_number = _chapter_number_for(job, payload) or accum.chapter_number
            accum.attempt = _safe_int(payload.get("attempt")) or accum.attempt
            accum.started_at = at
            accum.segments = []
            accum.error = ""
            accum.discarded = False
            accum.source = "llm_stream"
            accum.received_structured = False
            accum.validation_status = str(payload.get("validation_status") or "").strip()
            accum.reported_text_length = 0
            accum.reported_reasoning_length = 0
            return
        if step == "llm_stream_delta":
            accum.status = "streaming"
            # Prefer structured segments (order-preserving); fall back to
            # legacy delta/text fields for backward compatibility.
            segments_raw = payload.get("segments")
            if isinstance(segments_raw, list):
                saw_structured = False
                for seg in segments_raw:
                    if not isinstance(seg, dict):
                        continue
                    kind = str(seg.get("kind") or "content").strip()
                    text = str(seg.get("text") or "")
                    if kind not in ("content", "reasoning"):
                        kind = "content"
                    saw_structured = True
                    accum.append_segment(kind, text)
                if saw_structured:
                    accum.received_structured = True
            else:
                if accum.received_structured:
                    return
                # Legacy: if the event carries a full "text" field, use it
                # to replace the content segments (old events included the
                # cumulative text in every delta).  Otherwise append "delta".
                full_text = str(payload.get("text") or "")
                if full_text:
                    accum._replace_content_text(full_text)
                else:
                    delta = str(payload.get("delta") or "")
                    if delta:
                        accum.append_segment("content", delta)
            return
        if step == "llm_stream_restart":
            accum.status = "restarted"
            reset_output = bool(payload.get("reset_output"))
            accum.discarded = not reset_output
            if reset_output:
                accum.segments = []
                accum.truncated = False
                accum.received_structured = False
                accum.reported_text_length = 0
                accum.reported_reasoning_length = 0
            accum.error = str(payload.get("error") or payload.get("message") or "").strip()
            return
        if step == "llm_stream_end":
            accum.status = (
                accum.validation_status
                if accum.validation_status in {"validating", "repairing", "retrying"}
                else "complete"
            )
            accum.ended_at = at
            accum.error = ""
            # If a final text is provided and differs from accumulated, sync.
            final_text = str(payload.get("text") or "")
            final_reasoning = str(payload.get("reasoning") or "")
            if final_text and final_text != accum.text:
                accum._replace_content_text(final_text)
            if final_reasoning:
                current_reasoning = accum.reasoning_text
                if not current_reasoning:
                    accum.segments.insert(
                        0,
                        StreamSegment(kind="reasoning", text=final_reasoning),
                    )
                    accum._enforce_limit()
                elif final_reasoning != current_reasoning and final_reasoning.startswith(
                    current_reasoning
                ):
                    accum.append_segment("reasoning", final_reasoning[len(current_reasoning) :])
            return
        if step == "llm_stream_validation":
            status = accum.validation_status
            accum.status = {
                "validating": "validating",
                "repairing": "repairing",
                "retrying": "retrying",
                "validated": "validated",
                "failed": "validation_failed",
            }.get(status, accum.status)
            validated_text = payload.get("text")
            if status == "validated" and isinstance(validated_text, str):
                accum._replace_content_text(validated_text)
            if status == "failed":
                accum.error = str(payload.get("error") or "结构化输出未通过校验。").strip()
            else:
                accum.error = ""
            if status in {"validated", "failed"}:
                accum.ended_at = at
            return
        if step == "llm_stream_error":
            accum.status = "error"
            accum.error = str(payload.get("error") or payload.get("message") or "").strip()
            return

    def ingest_decision_required(self, job_id: str, payload: object) -> None:
        """Record an immediate Desktop HITL request before the next job bind."""

        if not isinstance(payload, dict):
            return
        decision = self._decision_from_payload(job_id, payload, "", status="pending")
        if decision is None:
            return
        self._external_decisions[job_id] = decision
        self._decisions_by_job[job_id] = decision
        self.changed.emit()

    def mark_decision_submitted(self, job_id: str, decision_id: str, choice: str) -> None:
        """Optimistically clear a pending decision after the user clicks an option."""

        current = self._decisions_by_job.get(job_id)
        if current is None or current.decision_id != decision_id:
            return
        updated = replace(current, status="resolved", choice=choice, resolved_at=_now_iso())
        self._decisions_by_job[job_id] = updated
        self._external_decisions.pop(job_id, None)
        self.changed.emit()

    def focus_for_scope(
        self,
        scope: TaskFocusScope | str,
        *,
        project_id: str = "",
        chapter_number: int = 0,
    ) -> ObservedTaskState | None:
        """Return the best task to focus for a scope."""

        candidates = self.candidates_for_scope(
            scope,
            project_id=project_id,
            chapter_number=chapter_number,
        )
        return candidates[0] if candidates else None

    def candidates_for_scope(
        self,
        scope: TaskFocusScope | str,
        *,
        project_id: str = "",
        chapter_number: int = 0,
    ) -> list[ObservedTaskState]:
        """Return all positive-score focus candidates for a scope, best first."""

        focus_scope = TaskFocusScope(scope)
        candidates = [
            job
            for job in self._jobs
            if self._job_matches_scope(
                job,
                focus_scope,
                project_id=project_id,
                chapter_number=chapter_number,
            )
        ]
        if not candidates:
            return []

        scored = [
            (
                self._score_job(
                    job,
                    focus_scope,
                    project_id=project_id,
                    chapter_number=chapter_number,
                ),
                job,
            )
            for job in candidates
        ]
        scored = [(score, job) for score, job in scored if score > 0]
        if not scored:
            return []
        scored.sort(key=lambda item: item[0], reverse=True)
        return [self._state_for_job(job, focus_scope) for _, job in scored]

    def attention_groups_for_scope(
        self,
        scope: TaskFocusScope | str,
        *,
        project_id: str = "",
        chapter_number: int = 0,
    ) -> list[ObservedAttentionGroup]:
        """Return project-level focus groups for compact observation UI.

        This is intentionally layered on top of job-level candidates: streaming
        state remains attached to its exact job, while the pet/dialog switcher
        collapses old jobs from the same project into one visible attention
        entry.
        """

        focus_scope = TaskFocusScope(scope)
        states = self.candidates_for_scope(
            focus_scope,
            project_id=project_id,
            chapter_number=chapter_number,
        )
        if not states:
            return []
        grouped: dict[str, list[ObservedTaskState]] = {}
        labels: dict[str, str] = {}
        for state in states:
            key = self._attention_group_key(
                state,
                focus_scope,
                chapter_number=chapter_number,
            )
            grouped.setdefault(key, []).append(state)
            labels.setdefault(key, self._attention_group_label(state))

        groups: list[ObservedAttentionGroup] = []
        for key, group_states in grouped.items():
            primary = group_states[0]
            active_count = sum(1 for item in group_states if self._state_needs_attention(item))
            live_stream_count = sum(1 for item in group_states if item.has_live_stream)
            decision_count = sum(1 for item in group_states if item.has_active_decision)
            groups.append(
                ObservedAttentionGroup(
                    key=key,
                    label=labels[key],
                    primary=primary,
                    states=tuple(group_states),
                    active_count=active_count,
                    history_count=max(0, len(group_states) - active_count),
                    live_stream_count=live_stream_count,
                    decision_count=decision_count,
                )
            )
        return groups

    def state_for_job_id(
        self,
        job_id: str,
        scope: TaskFocusScope | str,
        *,
        project_id: str = "",
        chapter_number: int = 0,
    ) -> ObservedTaskState | None:
        """Return one in-scope positive-score task state by job id."""

        job_key = str(job_id or "").strip()
        if not job_key:
            return None
        focus_scope = TaskFocusScope(scope)
        for job in self._jobs:
            if job.job_id != job_key:
                continue
            if not self._job_matches_scope(
                job,
                focus_scope,
                project_id=project_id,
                chapter_number=chapter_number,
            ):
                return None
            score = self._score_job(
                job,
                focus_scope,
                project_id=project_id,
                chapter_number=chapter_number,
            )
            if score <= 0:
                return None
            return self._state_for_job(job, focus_scope)
        return None

    def streams_for_job_id(
        self,
        job_id: str,
        *,
        source: str = "",
    ) -> tuple[ObservedStreamState, ...]:
        """Return retained stream history for one observed job.

        Streams are returned oldest-first, matching the accumulator order used
        internally.  The UI can reverse this for "latest first" selectors while
        still preserving a stable chronological API.
        """

        streams = self._streams_by_job.get(str(job_id or "").strip(), ())
        source_key = str(source or "").strip()
        if source_key:
            streams = tuple(stream for stream in streams if stream.source == source_key)
        return streams

    def has_active_attention(self) -> bool:
        """Whether any job currently deserves global floating attention."""

        return self.active_attention_count() > 0

    def active_attention_count(self) -> int:
        """Return active project-level attention count."""

        return sum(
            1
            for group in self.attention_groups_for_scope(TaskFocusScope.GLOBAL)
            if group.active_count > 0
        )

    def active_usage(self) -> TokenUsage:
        """Usage across active jobs, counted once even when UI groups collapse them."""

        usage = TokenUsage(source="active_jobs")
        seen: set[str] = set()
        for group in self.attention_groups_for_scope(TaskFocusScope.GLOBAL):
            for state in group.states:
                if state.job_id in seen or not self._state_needs_attention(state):
                    continue
                seen.add(state.job_id)
                usage = usage.plus(state.usage, source="active_jobs")
        return usage

    def _attention_group_key(
        self,
        state: ObservedTaskState,
        scope: TaskFocusScope,
        *,
        chapter_number: int,
    ) -> str:
        if state.has_active_decision:
            return f"decision:{state.job_id}"
        project = (state.job.project_id or "").strip()
        if not project:
            return f"job:{state.job_id}"
        if scope == TaskFocusScope.CHAPTER:
            chapter = (
                state.stream.chapter_number
                if state.stream is not None and state.stream.chapter_number
                else _chapter_number_for(state.job)
            )
            chapter = chapter or chapter_number
            return f"project:{project}:chapter:{chapter or 0}"
        return f"project:{project}"

    @staticmethod
    def _attention_group_label(state: ObservedTaskState) -> str:
        project = (state.job.project_id or "").strip()
        if project:
            return project
        label = (state.job.label or state.job.kind or "").strip()
        return label or "自动项目"

    @staticmethod
    def _state_needs_attention(state: ObservedTaskState) -> bool:
        if state.has_active_decision:
            return True
        if state.job.status in {DesktopJobState.QUEUED, DesktopJobState.RUNNING}:
            return True
        if state.job.status == DesktopJobState.PAUSED:
            return True
        return False

    def _state_for_job(self, job: DesktopJobRecord, scope: TaskFocusScope) -> ObservedTaskState:
        stream = self._latest_stream(job.job_id)
        decision = self._decisions_by_job.get(job.job_id)
        current_node = self._current_node(job, stream=stream, decision=decision)
        label, tone = self._status_spec(job, stream=stream, decision=decision)
        return ObservedTaskState(
            job=job,
            focus_reason=self._focus_reason(job, scope, stream=stream, decision=decision),
            current_node=current_node,
            status_label=label,
            status_tone=tone,
            stream=stream,
            decision=decision,
            diagnostics=self._diagnostics_by_job.get(job.job_id, ()),
            events=self._events_by_job.get(job.job_id, ()),
            usage=self._usage_for_job(job),
            progress_percent=compute_task_flow_progress(job),
        )

    def _usage_for_job(self, job: DesktopJobRecord) -> TokenUsage:
        prompt = completion = total = calls = 0
        cost = 0.0
        for stream in self._streams_by_job.get(job.job_id, ()):
            if stream.source != "model_call" or stream.status != "complete":
                continue
            prompt += max(0, stream.prompt_tokens)
            completion += max(0, stream.completion_tokens)
            total += max(0, stream.total_tokens or stream.prompt_tokens + stream.completion_tokens)
            cost += max(0.0, stream.cost_usd)
            calls += 1
        cumulative = max(0, int(job.cumulative_tokens or 0))
        source = "model_calls"
        if cumulative > total:
            total = cumulative
            source = "trace_cumulative"
        cost = max(cost, max(0.0, float(job.cumulative_cost_usd or 0.0)))
        return TokenUsage(
            prompt_tokens=prompt,
            completion_tokens=completion,
            total_tokens=total,
            cost_usd=cost,
            call_count=calls,
            source=source,
        )

    def _streams_for_job(self, job: DesktopJobRecord) -> tuple[ObservedStreamState, ...]:
        """Full-replay fallback that builds stream states from all events.

        Retained for tests and any caller that needs a stateless snapshot
        without touching the incremental accumulators.  The main ingest
        path uses :meth:`_apply_events_incremental` instead.
        """
        accum_map: dict[str, _StreamAccum] = {}
        for event in job.events:
            step = getattr(event, "step", "")
            if step not in _STREAM_EVENTS and step not in _MODEL_CALL_EVENTS:
                continue
            payload = event.payload if isinstance(event.payload, dict) else {}
            if step == "model_call_update":
                stream_id = str(payload.get("call_id") or "").strip()
            else:
                stream_id = str(payload.get("stream_id") or "").strip()
            if not stream_id:
                continue
            accum = accum_map.get(stream_id)
            if accum is None:
                accum = _StreamAccum(
                    job_id=job.job_id,
                    stream_id=stream_id,
                    source="model_call" if step == "model_call_update" else "llm_stream",
                    started_at=getattr(event, "at", ""),
                )
                accum_map[stream_id] = accum
            self._apply_one_event(accum, job, step, payload, getattr(event, "at", ""))
        states = sorted(
            (accum.to_state() for accum in accum_map.values()),
            key=lambda item: _parse_dt(item.updated_at),
        )
        return tuple(states)

    def _decision_for_job(self, job: DesktopJobRecord) -> ObservedDecisionState | None:
        current: ObservedDecisionState | None = None
        for event in job.events:
            if event.step not in _DECISION_EVENTS:
                continue
            payload = event.payload if isinstance(event.payload, dict) else {}
            if event.step == "human_decision_requested":
                current = self._decision_from_payload(
                    job.job_id, payload, event.at, status="pending"
                )
            elif event.step in {"human_decision_resolved", "human_decision_timeout"}:
                resolved = self._decision_from_payload(
                    job.job_id,
                    payload,
                    event.at,
                    status="timeout" if event.step == "human_decision_timeout" else "resolved",
                )
                if resolved is not None:
                    current = resolved
        return current

    def _decision_from_payload(
        self,
        job_id: str,
        payload: dict[str, Any],
        at: str,
        *,
        status: str,
    ) -> ObservedDecisionState | None:
        decision_id = str(payload.get("decision_id") or "").strip()
        if not decision_id:
            return None
        return ObservedDecisionState(
            job_id=job_id,
            decision_id=decision_id,
            title=str(payload.get("title") or "需要确认").strip(),
            message=str(payload.get("message") or "").strip(),
            kind=str(payload.get("kind") or "").strip(),
            project_id=str(payload.get("project_id") or "").strip(),
            chapter_number=_safe_int(payload.get("chapter_number")),
            options=_normalize_options(payload.get("options")),
            default_option=str(payload.get("default_option") or "").strip(),
            timeout_seconds=_safe_int(payload.get("timeout_seconds")),
            risk=str(payload.get("risk") or "").strip(),
            cost_hint=str(payload.get("cost_hint") or "").strip(),
            status=status,
            choice=str(payload.get("choice") or "").strip(),
            custom_text=str(payload.get("custom_text") or "").strip(),
            timed_out=bool(payload.get("timed_out", status == "timeout")),
            requested_at=at,
            resolved_at=at if status != "pending" else "",
            approval_version=str(payload.get("approval_version") or ""),
            requires_explicit_approval=bool(payload.get("requires_explicit_approval")),
        )

    def _diagnostics_for_job(self, job: DesktopJobRecord) -> tuple[str, ...]:
        items: list[str] = []
        for event in job.events:
            if event.step not in _DIAGNOSTIC_EVENTS:
                continue
            payload = event.payload if isinstance(event.payload, dict) else {}
            summary = _event_summary(event.step, payload).strip()
            if summary and summary not in items:
                items.append(summary[:260])
        return tuple(items[-6:])

    def _events_for_job(self, job: DesktopJobRecord) -> tuple[str, ...]:
        items: list[str] = []
        for event in job.events[-12:]:
            payload = event.payload if isinstance(event.payload, dict) else {}
            summary = _event_summary(event.step, payload).strip()
            if summary:
                items.append(summary[:220])
        return tuple(items[-8:])

    def _latest_stream(self, job_id: str) -> ObservedStreamState | None:
        streams = self._streams_by_job.get(job_id, ())
        if not streams:
            return None
        llm_streams = tuple(stream for stream in streams if stream.source == "llm_stream")
        model_calls = tuple(stream for stream in streams if stream.source == "model_call")

        # Prefer a real LLM stream whenever it is actively producing text.
        # Some providers and task paths only expose ``model_call_update``;
        # keeping those calls as a fallback makes the focus panel explain the
        # live state instead of claiming that a blank preview is streaming.
        active_llm = [
            stream
            for stream in llm_streams
            if not stream.discarded and stream.status == "streaming"
        ]
        if active_llm:
            return active_llm[-1]

        active_calls = [
            stream for stream in model_calls if not stream.discarded and stream.status == "running"
        ]
        if active_calls:
            return active_calls[-1]

        live_llm = [stream for stream in llm_streams if not stream.discarded]
        if live_llm:
            return live_llm[-1]
        live_calls = [stream for stream in model_calls if not stream.discarded]
        return (live_calls or list(streams))[-1]

    def _job_matches_scope(
        self,
        job: DesktopJobRecord,
        scope: TaskFocusScope,
        *,
        project_id: str,
        chapter_number: int,
    ) -> bool:
        return task_flow_matches_scope(
            job,
            scope,
            project_id=project_id,
            chapter_number=chapter_number,
        )

    def _score_job(
        self,
        job: DesktopJobRecord,
        scope: TaskFocusScope,
        *,
        project_id: str,
        chapter_number: int,
    ) -> float:
        cancelled = task_flow_is_cancelled(job)
        decision = None if cancelled else self._decisions_by_job.get(job.job_id)
        stream = None if cancelled else self._latest_stream(job.job_id)
        if cancelled:
            if _seconds_since(job.updated_at) > _TERMINAL_RECENT_SECONDS:
                return 0.0
            base = 15_000.0
        elif decision is not None and decision.status == "pending":
            base = 100_000.0
        elif stream is not None and stream.status in {"streaming", "running"}:
            base = 85_000.0
        elif job.status in {DesktopJobState.RUNNING, DesktopJobState.QUEUED}:
            base = 75_000.0
        elif job.status == DesktopJobState.PAUSED:
            base = 70_000.0
        elif job.status == DesktopJobState.FAILED:
            if _seconds_since(job.updated_at) > _TERMINAL_RECENT_SECONDS:
                return 0.0
            base = 55_000.0
        elif job.status == DesktopJobState.SUCCEEDED:
            if _seconds_since(job.updated_at) > _TERMINAL_RECENT_SECONDS:
                return 0.0
            base = 20_000.0
        else:
            return 0.0

        if scope == TaskFocusScope.CHAPTER and chapter_number > 0:
            job_chapter = _chapter_number_for(job)
            if job_chapter == chapter_number:
                base += 5_000.0
        if project_id and job.project_id == project_id:
            base += 1_000.0
        return base + _parse_dt(job.updated_at).timestamp() / 1_000_000.0

    def _current_node(
        self,
        job: DesktopJobRecord,
        *,
        stream: ObservedStreamState | None,
        decision: ObservedDecisionState | None,
    ) -> str:
        if task_flow_is_cancelled(job):
            for event in reversed(job.events):
                if str(event.step or "").strip() not in {"cancelled", "failed"}:
                    return f"{display_step_name(event.step)} · 已取消"
            return "任务已取消"
        if decision is not None and decision.status == "pending":
            return "等待人工确认"
        if stream is not None and stream.task:
            if stream.source == "model_call":
                if stream.status == "running":
                    suffix = "模型调用中"
                elif stream.status == "error":
                    suffix = "模型调用错误"
                else:
                    suffix = "模型输出"
                return f"{_display_task(stream.task) or stream.task} · {suffix}"
            suffix = {
                "streaming": "流式输出",
                "validating": "结构校验",
                "repairing": "结构修复",
                "retrying": "结构重试",
                "validated": "结构已校验",
                "validation_failed": "结构校验失败",
                "error": "流式输出错误",
            }.get(stream.status, "文本生成")
            return f"{_display_task(stream.task) or stream.task} · {suffix}"
        if job.current_step:
            return display_step_name_for_job(job)
        return "等待开始"

    def _status_spec(
        self,
        job: DesktopJobRecord,
        *,
        stream: ObservedStreamState | None,
        decision: ObservedDecisionState | None,
    ) -> tuple[str, str]:
        if task_flow_is_cancelled(job):
            status = task_flow_status_spec(job)
            return (status.label, status.tone)
        if decision is not None and decision.status == "pending":
            status = task_flow_status_spec(job, has_pending_decision=True)
            return (status.label, status.tone)
        if stream is not None and stream.source == "model_call" and stream.status == "running":
            return ("调用中", "warning")
        if stream is not None and stream.status == "streaming":
            return ("输出中", "warning")
        if stream is not None and stream.status in {"validating", "repairing", "retrying"}:
            return (
                {
                    "validating": "校验中",
                    "repairing": "修复中",
                    "retrying": "重试中",
                }[stream.status],
                "warning",
            )
        if stream is not None and stream.status == "validation_failed":
            return ("校验失败", "error")
        status = task_flow_status_spec(job)
        label = "运行中" if status.outcome == TaskFlowOutcome.RUNNING else status.label
        tone = "default" if status.outcome == TaskFlowOutcome.QUEUED else status.tone
        return (label, tone)

    def _focus_reason(
        self,
        job: DesktopJobRecord,
        scope: TaskFocusScope,
        *,
        stream: ObservedStreamState | None,
        decision: ObservedDecisionState | None,
    ) -> str:
        if task_flow_is_cancelled(job):
            return task_flow_status_spec(job).focus_reason
        if decision is not None and decision.status == "pending":
            return "需要确认"
        if stream is not None and stream.source == "model_call" and stream.status == "running":
            return "模型调用"
        if stream is not None and stream.status == "streaming":
            return "当前节点输出"
        if stream is not None and stream.status in {"validating", "repairing", "retrying"}:
            return "结构校验"
        if stream is not None and stream.status == "validation_failed":
            return "需要处理"
        status = task_flow_status_spec(job)
        if status.outcome in {
            TaskFlowOutcome.QUEUED,
            TaskFlowOutcome.RUNNING,
            TaskFlowOutcome.PAUSED,
            TaskFlowOutcome.FAILED,
            TaskFlowOutcome.CANCELLED,
        }:
            return status.focus_reason
        if scope == TaskFocusScope.GLOBAL:
            return "最近完成"
        return "当前关注"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _run_log_dir_for_job(job: DesktopJobRecord) -> str:
    result = job.result if isinstance(job.result, dict) else {}
    error_summary = job.error_summary if isinstance(job.error_summary, dict) else {}
    for raw in (
        result.get("run_log_dir"),
        result.get("run_dir"),
        error_summary.get("run_log_dir"),
        error_summary.get("run_dir"),
    ):
        text = str(raw or "").strip()
        if text:
            return text
    for event in getattr(job, "events", []) or []:
        payload = getattr(event, "payload", {})
        if not isinstance(payload, dict):
            continue
        for key in ("run_log_dir", "run_dir"):
            text = str(payload.get(key) or "").strip()
            if text:
                return text
    return ""
