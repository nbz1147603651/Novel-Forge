"""Per-project structured diagnostics for CLI, API, and desktop workflows."""

from __future__ import annotations

import json
import logging
import shutil
import threading
import traceback
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from novel_forge.obs.context import (
    ContextTextFormatter,
    JsonLogFormatter,
    RunContextFilter,
    bind_log_context,
    current_log_context,
)
from novel_forge.obs.tracer import PipelineTrace
from novel_forge.persistence.filesystem import atomic_write_text
from novel_forge.persistence.models import ProjectLayout

# Events routed to pipeline.jsonl (run lifecycle + step flow)
_PIPELINE_EVENTS: frozenset[str] = frozenset(
    {
        "run_started",
        "run_finished",
        "run_failed",
        "chapter_started",
        "step",
    }
)

# ---------------------------------------------------------------------------
# Module-level logger access (used by lower layers for structured events)
# ---------------------------------------------------------------------------

_log = logging.getLogger(__name__)

_current_logger: ContextVar[ProjectRunLogger | None] = ContextVar(
    "novel_forge_project_run_logger",
    default=None,
)


def set_project_logger(logger: ProjectRunLogger | None = None) -> Token[ProjectRunLogger | None]:
    """Set the current task's project logger for use by lower layers.

    The return token can be passed to :func:`reset_project_logger`; callers
    using the historical fire-and-forget form may safely ignore it.
    """

    return _current_logger.set(logger)


def reset_project_logger(token: Token[ProjectRunLogger | None]) -> None:
    """Restore the project logger active before :func:`set_project_logger`."""

    _current_logger.reset(token)


def get_project_logger() -> ProjectRunLogger | None:
    """Return the module-level project logger, or None if not set."""
    return _current_logger.get()


# Events routed to api_summary.jsonl (API call lifecycle summaries)
_API_EVENTS: frozenset[str] = frozenset(
    {
        "api_call_start",
        "api_call_done",
        "api_call_error",
        "api_call_timeout",
        "api_call_incomplete",
        "api_call_empty_response",
        "api_call_length_truncated",
        "api_stream_start",
        "api_stream_done",
        "api_stream_error",
    }
)
_MODEL_CALL_RETRY_EVENTS: frozenset[str] = frozenset(
    {
        "api_call_empty_response",
        "api_call_length_truncated",
    }
)
# Levels routed to errors.jsonl
_ERROR_LEVELS: frozenset[str] = frozenset({"ERROR", "CRITICAL", "WARNING"})
_FORMAT_DIAGNOSTIC_STEPS: frozenset[str] = frozenset(
    {
        "format_repaired",
        "format_repair_strategy_miss",
        "format_retry",
        "format_retry_exhausted",
    }
)
_FORMAT_ERROR_STEPS: frozenset[str] = frozenset(
    {
        "format_repair_strategy_miss",
        "format_retry",
        "format_retry_exhausted",
    }
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _utc_now_iso() -> str:
    return _utc_now().isoformat()


def _slugify(text: str) -> str:
    cleaned = []
    for ch in text.strip().lower():
        if ch.isalnum() or ch in {"-", "_"}:
            cleaned.append(ch)
        else:
            cleaned.append("-")
    slug = "".join(cleaned).strip("-")
    return slug or "run"


def _serialize_json(value: Any, *, compact: bool) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        if isinstance(value, str) and compact and len(value) > 800:
            return value[:799] + "…"
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, BaseModel):
        return _serialize_json(value.model_dump(mode="json"), compact=compact)
    if is_dataclass(value) and not isinstance(value, type):
        return _serialize_json(asdict(value), compact=compact)
    if isinstance(value, dict):
        items = list(value.items())
        if compact and len(items) > 40:
            items = items[:40]
        return {str(k): _serialize_json(v, compact=compact) for k, v in items}
    if isinstance(value, (list, tuple, set)):
        items = list(value)
        if compact and len(items) > 40:
            items = items[:40]
        return [_serialize_json(item, compact=compact) for item in items]
    return str(value)


class ProjectRunLogger:
    """Persist detailed run logs under one project directory.

    Directory layout
    ----------------
        logs/<run_id>/
        summary.json          — run-level metadata and trace summary
        events.jsonl          — chronological event stream (all steps + API lifecycle)
        application.jsonl     — correlated Python log records (JSON Lines, INFO+)
        python.log            — correlated human-readable WARNING+ records
        model_calls/
            NNN_<task>.json               — non-chapter API calls (init, etc.)
            ch_<N>/
                NNN_<task>.json           — API calls belonging to chapter N
    """

    def __init__(
        self,
        *,
        layout: ProjectLayout,
        project_id: str,
        command: str,
        metadata: dict[str, Any] | None = None,
        keep_runs: int = 20,
    ) -> None:
        self._layout = layout
        self._project_id = project_id
        self._command = command
        self._started_at = _utc_now()
        self._ended_at: datetime | None = None
        self._finalized = False
        self._write_lock = threading.RLock()
        self._event_index = 0
        self._call_index = 0
        self._pending_calls: dict[str, dict[str, Any]] = {}
        self._stream_progress: dict[str, dict[str, Any]] = {}
        self._current_chapter: int | None = None

        run_stamp = self._started_at.strftime("%Y%m%d-%H%M%S")
        suffix = uuid.uuid4().hex[:8]
        self.run_id = f"{run_stamp}_{_slugify(command)}_{suffix}"
        self.request_id = uuid.uuid4().hex
        self.run_dir = self._layout.logs_dir / self.run_id
        self.model_calls_dir = self.run_dir / "model_calls"
        self.format_errors_dir = self.run_dir / "format_errors"
        # Full chronological stream (backward compatible)
        self.events_path = self.run_dir / "events.jsonl"
        # Categorised sub-streams for targeted analysis
        self.pipeline_events_path = self.run_dir / "pipeline.jsonl"
        self.api_summary_path = self.run_dir / "api_summary.jsonl"
        self.errors_path = self.run_dir / "errors.jsonl"
        self.summary_path = self.run_dir / "summary.json"
        self.format_errors_path = self.run_dir / "format_errors.jsonl"
        self.python_log_path = self.run_dir / "python.log"
        self.application_log_path = self.run_dir / "application.jsonl"

        self.model_calls_dir.mkdir(parents=True, exist_ok=True)
        self._format_error_index = 0
        self._summary: dict[str, Any] = {
            "schema_version": 1,
            "run_id": self.run_id,
            "project_id": self._project_id,
            "command": self._command,
            "request_id": self.request_id,
            "status": "running",
            "started_at": self._started_at.isoformat(),
            "ended_at": None,
            "duration_ms": None,
            "run_dir": str(self.run_dir),
            "metadata": _serialize_json(metadata or {}, compact=False),
            "result": None,
            "trace_summary": None,
            "error": None,
        }

        # Attach run-scoped handlers to the root logger. The context filter is
        # deliberately strict: when two runs overlap, each file receives only
        # records bound to its own run_id.
        self._py_log_handler: logging.FileHandler | None = None
        self._application_log_handler: logging.FileHandler | None = None
        try:
            fh = logging.FileHandler(self.python_log_path, encoding="utf-8")
            fh.setLevel(logging.WARNING)
            fh.setFormatter(
                ContextTextFormatter(
                    fmt="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
                    datefmt="%Y-%m-%d %H:%M:%S",
                )
            )
            fh.addFilter(RunContextFilter(self.run_id))
            logging.getLogger("novel_forge").addHandler(fh)
            self._py_log_handler = fh

            application_handler = logging.FileHandler(self.application_log_path, encoding="utf-8")
            application_handler.setLevel(logging.INFO)
            application_handler.setFormatter(JsonLogFormatter())
            application_handler.addFilter(RunContextFilter(self.run_id))
            logging.getLogger("novel_forge").addHandler(application_handler)
            self._application_log_handler = application_handler
        except Exception:
            pass  # Never let log setup break the run

        self._write_summary()
        self.log_event("run_started", metadata or {})

        # Prune stale run directories AFTER the new one is created so the
        # current run is always preserved.
        if keep_runs > 0:
            self._prune_old_runs(keep=keep_runs)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @property
    def _active_model_calls_dir(self) -> Path:
        """Return the chapter-specific subdirectory when inside a chapter, else the base dir."""
        if self._current_chapter is not None:
            d = self.model_calls_dir / f"ch_{self._current_chapter:03d}"
            d.mkdir(exist_ok=True)
            return d
        return self.model_calls_dir

    def _prune_old_runs(self, *, keep: int) -> None:
        """Delete oldest run directories, keeping only the most recent *keep* runs.

        Directories whose ``summary.json`` still shows ``"running"`` status are
        skipped to avoid deleting a concurrently-active run.
        """
        log_dir = self._layout.logs_dir
        if not log_dir.exists():
            return

        candidates: list[Path] = sorted(
            [d for d in log_dir.iterdir() if d.is_dir() and d.name != self.run_id],
            key=lambda d: d.name,  # ISO-timestamp prefix → lexicographic ≡ chronological
        )

        # Filter out any dirs that look concurrently active
        prunable: list[Path] = []
        for d in candidates:
            summary = d / "summary.json"
            if summary.exists():
                try:
                    status = json.loads(summary.read_text(encoding="utf-8")).get("status")
                    if status == "running":
                        continue  # skip – may be an active parallel run
                except Exception:
                    pass
            prunable.append(d)

        to_delete = prunable[: max(0, len(prunable) - keep + 1)]
        for old_dir in to_delete:
            try:
                shutil.rmtree(old_dir, ignore_errors=True)
            except Exception:
                pass

    def _write_summary(self) -> None:
        with self._write_lock:
            atomic_write_text(
                self.summary_path,
                json.dumps(self._summary, indent=2, ensure_ascii=False, default=str),
            )

    def _append_event(self, payload: dict[str, Any]) -> None:
        line = json.dumps(payload, ensure_ascii=False, default=str) + "\n"
        with self._write_lock:
            # Always write to the full chronological stream
            with self.events_path.open("a", encoding="utf-8") as handle:
                handle.write(line)
            # Route to categorised sub-streams
            event = payload.get("event", "")
            level = payload.get("level", "INFO")
            if event in _PIPELINE_EVENTS:
                with self.pipeline_events_path.open("a", encoding="utf-8") as handle:
                    handle.write(line)
            elif event in _API_EVENTS:
                with self.api_summary_path.open("a", encoding="utf-8") as handle:
                    handle.write(line)
            if level in _ERROR_LEVELS:
                with self.errors_path.open("a", encoding="utf-8") as handle:
                    handle.write(line)

    def _correlation_fields(self) -> dict[str, Any]:
        """Return the stable identity included in every persisted record."""

        context = current_log_context()
        fields: dict[str, Any] = {
            "run_id": self.run_id,
            "project_id": self._project_id,
            "command": self._command,
            "request_id": context.get("request_id") or self.request_id,
        }
        for key in ("chapter", "step", "task", "call_id"):
            value = context.get(key)
            if value not in (None, ""):
                fields[key] = value
        if self._current_chapter is not None:
            fields.setdefault("chapter", self._current_chapter)
        return fields

    @contextmanager
    def activate(self, **context: Any) -> Iterator[ProjectRunLogger]:
        """Bind this logger to the current async task/thread for one workflow.

        All normal Python logs, lower-layer structured events, and router
        callbacks become correlated with exactly one project run and are
        restored afterward.
        """

        token = set_project_logger(self)
        try:
            with bind_log_context(
                run_id=self.run_id,
                project_id=self._project_id,
                command=self._command,
                request_id=self.request_id,
                **context,
            ):
                yield self
        finally:
            reset_project_logger(token)

    def log_event(self, event: str, data: Any | None = None, *, level: str = "INFO") -> None:
        with self._write_lock:
            # Track chapter transitions so model_calls/ can be sub-divided by chapter.
            if event == "chapter_started" and isinstance(data, dict):
                ch = data.get("chapter")
                if isinstance(ch, int):
                    self._current_chapter = ch

            self._event_index += 1
            payload = {
                "schema_version": 1,
                "event_id": f"{self.run_id}:evt_{self._event_index:06d}",
                "sequence": self._event_index,
                "ts": _utc_now_iso(),
                "level": level.upper(),
                **self._correlation_fields(),
                "event": event,
                "data": _serialize_json(data, compact=True),
            }
            self._append_event(payload)

    def log_step(self, step: str, data: Any | None = None, **extra: Any) -> None:
        """Persist a step event without interleaving stream aggregates or files."""

        with self._write_lock:
            self._log_step_locked(step, data, **extra)

    def _log_step_locked(self, step: str, data: Any | None = None, **extra: Any) -> None:
        if step == "llm_stream_delta" and isinstance(data, dict):
            self._record_stream_delta(data)
            return
        if step in {"llm_stream_end", "llm_stream_error"} and isinstance(data, dict):
            self._flush_stream_progress(str(data.get("stream_id") or ""))
        payload: dict[str, Any] = {"step": step}
        if extra:
            payload.update(extra)
        if data is not None:
            if step in _FORMAT_DIAGNOSTIC_STEPS and isinstance(data, dict):
                self._write_format_error(step, data)
            payload["data"] = data
        level = "WARNING" if step in _FORMAT_ERROR_STEPS else "INFO"
        self.log_event("step", payload, level=level)

    def _record_stream_delta(self, data: dict[str, Any]) -> None:
        """Aggregate high-frequency UI deltas without persisting generated text."""

        stream_id = str(data.get("stream_id") or "").strip() or "unknown"
        summary = self._stream_progress.setdefault(
            stream_id,
            {
                "stream_id": stream_id,
                "task": str(data.get("task") or ""),
                "chapter": data.get("chapter", ""),
                "attempt": data.get("attempt", 0),
                "stream_kind": str(data.get("stream_kind") or ""),
                "output_kind": str(data.get("output_kind") or ""),
                "delta_count": 0,
                "emitted_text_chars": 0,
                "emitted_reasoning_chars": 0,
                "text_length": 0,
                "reasoning_length": 0,
            },
        )
        summary["delta_count"] = int(summary["delta_count"] or 0) + 1
        summary["text_length"] = max(
            int(summary["text_length"] or 0),
            int(data.get("text_length") or 0),
        )
        summary["reasoning_length"] = max(
            int(summary["reasoning_length"] or 0),
            int(data.get("reasoning_length") or 0),
        )
        for segment in data.get("segments", []) or []:
            if not isinstance(segment, dict):
                continue
            emitted = len(str(segment.get("text") or ""))
            key = (
                "emitted_reasoning_chars"
                if str(segment.get("kind") or "") == "reasoning"
                else "emitted_text_chars"
            )
            summary[key] = int(summary[key] or 0) + emitted

    def _flush_stream_progress(self, stream_id: str = "") -> None:
        stream_ids = [stream_id] if stream_id else list(self._stream_progress)
        for current_id in stream_ids:
            summary = self._stream_progress.pop(current_id, None)
            if summary is None:
                continue
            self.log_event(
                "step",
                {"step": "llm_stream_delta_summary", "data": summary},
            )

    def _write_format_error(self, step: str, data: dict[str, Any]) -> None:
        """Persist one malformed-response diagnostic as its own JSON file."""
        self._format_error_index += 1
        task = _slugify(str(data.get("task") or "unknown"))
        attempt = data.get("attempt", self._format_error_index)
        file_name = f"{self._format_error_index:03d}_{task}_attempt_{attempt}.json"
        self.format_errors_dir.mkdir(parents=True, exist_ok=True)
        path = self.format_errors_dir / file_name
        payload = {
            "schema_version": 1,
            "event": step,
            "recorded_at": _utc_now_iso(),
            **self._correlation_fields(),
            **_serialize_json(data, compact=False),
        }
        atomic_write_text(
            path,
            json.dumps(payload, indent=2, ensure_ascii=False, default=str),
        )
        data["log_file"] = str(path)

        summary = {
            "recorded_at": payload["recorded_at"],
            "event": step,
            "task": data.get("task"),
            "attempt": data.get("attempt"),
            "max_attempts": data.get("max_attempts"),
            "error_type": data.get("error_type"),
            "error": data.get("error"),
            "repaired": data.get("repaired"),
            "repair_action": data.get("repair_action"),
            "log_file": str(path),
        }
        line = json.dumps(summary, ensure_ascii=False, default=str) + "\n"
        with self.format_errors_path.open("a", encoding="utf-8") as handle:
            handle.write(line)

    def record_router_event(self, event: str, payload: dict[str, Any]) -> None:
        """Persist a router lifecycle event without interleaving concurrent calls."""

        with self._write_lock:
            self._record_router_event_locked(event, payload)

    def _record_router_event_locked(self, event: str, payload: dict[str, Any]) -> None:
        call_id = str(payload.get("call_id") or "")
        serialized = _serialize_json(payload, compact=False)
        if event in {"api_call_start", "api_stream_start"}:
            self._pending_calls[call_id] = serialized
            self.log_event(
                event,
                {
                    "call_id": call_id,
                    "task": serialized.get("task"),
                    "provider": serialized.get("provider"),
                    "model": serialized.get("model"),
                    "max_tokens": serialized.get("max_tokens"),
                    "temperature": serialized.get("temperature"),
                },
            )
            return

        retry_event = event in _MODEL_CALL_RETRY_EVENTS
        will_retry = retry_event and bool(serialized.get("will_retry"))
        start_payload = (
            self._pending_calls.get(call_id, {})
            if will_retry
            else self._pending_calls.pop(call_id, {})
        )
        if not start_payload:
            # Event belongs to a concurrent run whose observer was also
            # registered on the global router. Skip to avoid cross-run
            # model-call log contamination.
            return
        combined = dict(start_payload)
        combined.update(serialized)
        self._call_index += 1

        task = str(combined.get("task") or "unknown")

        # Determine file suffix based on event type
        if event in {"api_call_error", "api_stream_error"}:
            file_suffix = "_error"
        elif event == "api_call_timeout":
            file_suffix = "_timeout"
        elif event == "api_call_incomplete":
            file_suffix = "_incomplete"
        elif event == "api_call_empty_response":
            file_suffix = "_empty_response"
        elif event == "api_call_length_truncated":
            file_suffix = "_length_truncated"
        else:
            file_suffix = ""

        file_name = f"{self._call_index:03d}_{_slugify(task)}{file_suffix}.json"
        atomic_write_text(
            self._active_model_calls_dir / file_name,
            json.dumps(
                {
                    "schema_version": 1,
                    "event": event,
                    "recorded_at": _utc_now_iso(),
                    **self._correlation_fields(),
                    **combined,
                },
                indent=2,
                ensure_ascii=False,
                default=str,
            ),
        )

        event_data = {
            "call_id": call_id,
            "task": combined.get("task"),
            "provider": combined.get("provider"),
            "model": combined.get("model"),
            "model_call_file": file_name,
            "latency_ms": combined.get("latency_ms"),
            "prompt_tokens": combined.get("prompt_tokens"),
            "completion_tokens": combined.get("completion_tokens"),
            "total_tokens": combined.get("total_tokens"),
        }
        for key in (
            "attempt",
            "max_attempts",
            "max_tokens",
            "next_max_tokens",
            "model_output_limit",
            "finish_reason",
            "will_retry",
        ):
            if key in combined:
                event_data[key] = combined[key]
        response = combined.get("response")
        if isinstance(response, dict):
            for key in (
                "structured_output_mode",
                "structured_output_downgraded_from",
                "structured_output_reason",
            ):
                value = response.get(key)
                if value:
                    event_data[key] = value
        if event in {"api_call_error", "api_stream_error"}:
            event_data["error"] = combined.get("error")
            self.log_event(event, event_data, level="ERROR")
        elif event == "api_call_timeout":
            event_data["timeout_s"] = combined.get("timeout_s")
            self.log_event("api_call_timeout", event_data, level="WARNING")
        elif retry_event:
            event_data["status"] = "retrying" if will_retry else "error"
            self.log_event(event, event_data, level="WARNING")
        else:
            self.log_event(event, event_data)

    def finalize(
        self,
        *,
        status: str,
        result: Any | None = None,
        trace_summary: dict[str, Any] | PipelineTrace | None = None,
        error: BaseException | None = None,
    ) -> None:
        """Finalize a run exactly once and detach its scoped file handlers."""

        with self._write_lock:
            self._finalize_locked(
                status=status,
                result=result,
                trace_summary=trace_summary,
                error=error,
            )

    def _finalize_locked(
        self,
        *,
        status: str,
        result: Any | None = None,
        trace_summary: dict[str, Any] | PipelineTrace | None = None,
        error: BaseException | None = None,
    ) -> None:
        if self._finalized:
            return
        self._finalized = True
        self._ended_at = _utc_now()
        self._flush_stream_progress()

        if self._pending_calls:
            for call_id, payload in list(self._pending_calls.items()):
                try:
                    self._call_index += 1
                    task = str(payload.get("task") or "unknown")
                    file_name = f"{self._call_index:03d}_{_slugify(task)}_incomplete.json"
                    atomic_write_text(
                        self._active_model_calls_dir / file_name,
                        json.dumps(
                            {
                                "schema_version": 1,
                                "event": "api_call_incomplete",
                                "recorded_at": _utc_now_iso(),
                                **self._correlation_fields(),
                                **payload,
                            },
                            indent=2,
                            ensure_ascii=False,
                            default=str,
                        ),
                    )
                    self.log_event(
                        "api_call_incomplete",
                        {"call_id": call_id, "task": task, "model_call_file": file_name},
                        level="WARNING",
                    )
                except Exception as exc:
                    # Orphaned call logging must never break finalize flow.
                    _log.debug(
                        "finalize_incomplete_call_write_failed | call_id=%s | error=%s",
                        call_id,
                        exc,
                    )
            self._pending_calls.clear()

        trace_payload: Any = trace_summary
        if isinstance(trace_summary, PipelineTrace):
            trace_payload = trace_summary.summary()

        error_payload = None
        if error is not None:
            error_payload = {
                "type": type(error).__name__,
                "message": str(error),
                "traceback": "".join(
                    traceback.format_exception(type(error), error, error.__traceback__)
                ),
            }
            # Structured TTS diagnostics (error_code/action/missing_prerequisites)
            # ride on errors exposing a ``payload`` dict so the UI can render
            # actionable remediation instead of only a message.
            structured_payload = getattr(error, "payload", None)
            if isinstance(structured_payload, dict):
                error_payload["payload"] = dict(structured_payload)
            self.log_event("run_failed", error_payload, level="ERROR")
        else:
            self.log_event("run_finished", {"status": status})

        self._summary.update(
            {
                "status": status,
                "ended_at": self._ended_at.isoformat(),
                "duration_ms": round(
                    (self._ended_at - self._started_at).total_seconds() * 1000,
                    2,
                ),
                "result": _serialize_json(result, compact=True),
                "trace_summary": _serialize_json(trace_payload, compact=False),
                "error": error_payload,
            }
        )
        self._write_summary()

        # Detach the run-scoped handlers so subsequent runs don't inherit them.
        if self._py_log_handler is not None:
            try:
                logging.getLogger("novel_forge").removeHandler(self._py_log_handler)
                self._py_log_handler.close()
            except Exception:
                pass
            self._py_log_handler = None
        if self._application_log_handler is not None:
            try:
                logging.getLogger("novel_forge").removeHandler(self._application_log_handler)
                self._application_log_handler.close()
            except Exception:
                pass
            self._application_log_handler = None
