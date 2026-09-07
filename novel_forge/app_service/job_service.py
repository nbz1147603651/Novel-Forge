"""Qt-free background job service for UI and API clients."""

from __future__ import annotations

import asyncio
import builtins
import dataclasses
import json
import logging
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Any, Literal, cast

from novel_forge.app_service.book_autorun import (
    BookAutorunCoordinator,
    BookAutorunState,
)
from novel_forge.app_service.contracts import (
    DecisionRequest,
    JobCommand,
    JobEvent,
    JobEventType,
    JobKind,
    JobRecord,
    JobScope,
    JobState,
    JobStepEvent,
    utc_now_iso,
)
from novel_forge.app_service.error_diagnostics import prompt_task_from_error_payload
from novel_forge.app_service.event_stream import EventSubscription, JobEventStream
from novel_forge.app_service.task_flow_error_log import (
    TaskFlowErrorLog,
    entries_from_job_record,
)
from novel_forge.app_service.workspace_commands import (
    CommandExecutor,
    PreparedCommand,
    WorkspaceCommandExecutor,
)
from novel_forge.common.token_usage import apply_live_usage_payload
from novel_forge.control_plane.shadow import ShadowRecorder
from novel_forge.core.config import Settings, get_settings
from novel_forge.core.exceptions import BLOCK_KIND_INPUT_INTEGRITY, NovelForgeException
from novel_forge.persistence.models import ProjectLayout
from novel_forge.pipeline.progress import compact_progress_event_history, is_non_progress_step_event
from novel_forge.workspace.projects import is_generated_test_project_id
from novel_forge.workspace.runtime import RuntimeServices, create_runtime_services

logger = logging.getLogger(__name__)

_MAX_JOB_EVENTS = 160
_MAX_STREAM_RESULTS = 128
_MAX_PERSISTED_JOBS_PER_PROJECT = 120
_JOB_HISTORY_FILENAME = "task_flow_history.json"
_DEFAULT_MAX_CONCURRENT_JOBS = 4
_MAX_COMPACT_STRING = 6000
_MAX_COMPACT_ITEMS = 80
_MAX_COMPACT_DEPTH = 4
_CONTROL_PLANE_INTENT_DIRNAME = "control_plane_intents"
_SYSTEM_STATE_DIRNAME = ".engine"
_SYSTEM_DURABLE_JOB_KINDS = frozenset(
    {
        JobKind.OLLAMA_RUNTIME_CONTROL.value,
        JobKind.OLLAMA_PULL_MODEL.value,
        JobKind.OLLAMA_DELETE_MODEL.value,
    }
)
_WRITE_JOB_KINDS = frozenset(
    {
        "init_long",
        "run_short",
        "run_chapter",
        "prepare_chapter",
        "reevaluate_chapter",
        "repair_continuity",
        "repair_issues",
        "polish_chapter",
        "resolve_chapter_checkpoint",
        "resolve_chapter_checkpoint_finalize",
        "repair_causal",
        "reextract_relationships",
        "repair_motif_history",
        "rebuild_memory_vectors",
        "book_consistency",
        "book_editorial_audit",
        "global_repair_queue",
        "sync_chapter_contracts",
        "extend_outline",
    }
)
_PROJECT_EVIDENCE_JOB_KINDS = frozenset(
    {
        JobKind.PLANNING_HORIZON.value,
        JobKind.AUTHORING_CHAT.value,
        JobKind.REPAIR_CASE.value,
        JobKind.SEMANTIC_CONSISTENCY.value,
    }
)
_PROJECT_DURABLE_JOB_KINDS = _WRITE_JOB_KINDS | _PROJECT_EVIDENCE_JOB_KINDS


def _project_label_from_request(request: Any) -> str:
    """Extract the author-facing work name carried by a workflow request."""

    if isinstance(request, dict):
        value = request.get("title")
    else:
        value = getattr(request, "title", "")
    return str(value or "").strip()


_TTS_JOB_KINDS = frozenset(
    {
        "tts_build_voice_team",
        "tts_generate_script",
        "tts_synthesize",
        "tts_full_pipeline",
        "tts_post_archive",
        "tts_export_audio",
        "tts_export_audiobook",
    }
)


@dataclass(frozen=True)
class ClosedTaskErrorCleanup:
    """Engine-owned result for guarded task-diagnostic cleanup."""

    cleared_task_ids: list[str]
    cleared_error_entry_ids: list[str]
    retained_pending_error_entry_ids: list[str]


_JOB_KIND_TO_SECTION: dict[str, str] = {
    "run_chapter": "chapters",
    "prepare_chapter": "chapters",
    "polish_chapter": "chapters",
    "repair_continuity": "chapters",
    "repair_causal": "chapters",
    "repair_issues": "chapters",
    "reevaluate_chapter": "chapters",
    "resolve_chapter_checkpoint": "chapters",
    "resolve_chapter_checkpoint_finalize": "chapters",
    "reextract_relationships": "chapters",
    "init_long": "projects",
    "run_short": "projects",
    "book_consistency": "details",
    "book_editorial_audit": "details",
    "global_repair_queue": "details",
    "rebuild_memory_vectors": "details",
    "sync_chapter_contracts": "details",
    "tts_build_voice_team": "details",
    "tts_generate_script": "details",
    "tts_synthesize": "details",
    "tts_full_pipeline": "details",
    "tts_post_archive": "details",
    "tts_export_audio": "details",
    "tts_export_audiobook": "details",
    "extend_outline": "projects",
    "planning_horizon": "projects",
    "polish_outline": "projects",
}


@dataclass
class PendingJob:
    record: JobRecord
    worker: "_JobWorker"
    intent_ready: bool = True


def _kind_value(kind: JobKind | str) -> str:
    return kind.value if hasattr(kind, "value") else str(kind)


def _compact_value(value: Any, *, depth: int = 0) -> Any:
    if depth >= _MAX_COMPACT_DEPTH:
        if isinstance(value, (str, int, float, bool)) or value is None:
            return value
        return str(type(value).__name__)
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    if isinstance(value, str):
        return value[:_MAX_COMPACT_STRING] + ("…" if len(value) > _MAX_COMPACT_STRING else "")
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    if isinstance(value, dict):
        return {
            str(key): _compact_value(item, depth=depth + 1)
            for key, item in list(value.items())[:_MAX_COMPACT_ITEMS]
        }
    if isinstance(value, (list, tuple, set)):
        return [_compact_value(item, depth=depth + 1) for item in list(value)[:_MAX_COMPACT_ITEMS]]
    return str(value)[:_MAX_COMPACT_STRING]


def _compact_payload(_step: str, data: Any) -> dict[str, Any]:
    if data is None:
        return {}
    if hasattr(data, "model_dump"):
        data = data.model_dump(mode="json")
    if isinstance(data, dict):
        compacted = _compact_value(data)
        if isinstance(compacted, dict) and _step.startswith("llm_stream_"):
            # An ellipsis can replace exactly one character, leaving lengths
            # equal. Carry explicit clipping flags instead of guessing later.
            for field in ("text", "reasoning"):
                if isinstance(data.get(field), str):
                    compacted[f"{field}_truncated"] = compacted.get(field) != data[field]
        return compacted if isinstance(compacted, dict) else {}
    if isinstance(data, list):
        return {"items": len(data)}
    text = str(data).strip()
    return {"preview": text[:160] + ("…" if len(text) > 160 else "")} if text else {}


def _error_payload(exc: Exception) -> dict[str, Any]:
    detail = str(exc).strip() or type(exc).__name__
    if isinstance(exc, NovelForgeException):
        context = _compact_value(exc.context)
        compact_context = context if isinstance(context, dict) else {}
        block_kind = str(compact_context.get("block_kind") or "")
        violation_kind = str(compact_context.get("violation_kind") or "")
        if violation_kind in {
            "upstream_source_conflict",
            "upstream_source_review_required",
            "upstream_semantic_not_ready",
        }:
            violations = [
                str(item).strip()
                for item in list(compact_context.get("violations") or [])
                if str(item).strip()
            ]
            summary = "；".join(violations[:5]) or exc.message
            title = {
                "upstream_source_conflict": "上游来源存在语义冲突",
                "upstream_source_review_required": "上游来源需要作者确认",
                "upstream_semantic_not_ready": "上游语义一致性尚未就绪",
            }[violation_kind]
            recommended_action = {
                "upstream_source_conflict": "在修复工作台生成候选，经作者批准后再准备本章。",
                "upstream_source_review_required": "确认这些证据可同时成立，或指定权威含义。",
                "upstream_semantic_not_ready": "刷新语义一致性；失败时检查模型、预算和结构化响应。",
            }[violation_kind]
            recovery_kind = {
                "upstream_source_conflict": "review_semantic_repairs",
                "upstream_source_review_required": "review_semantic_evidence",
                "upstream_semantic_not_ready": "refresh_semantic_consistency",
            }[violation_kind]
            return {
                "title": title,
                "summary": summary[:500],
                "detail": detail,
                "exception_type": type(exc).__name__,
                "error_code": exc.error_code,
                "context": compact_context,
                "retryable": False,
                "cause_code": violation_kind,
                "auto_repair_state": "blocked",
                "auto_repair_explanation": (
                    "问题属于上游语义来源；重做 Bridge/Plan 不能替代来源编译、"
                    "作者确认或版本化修订。"
                ),
                "recommended_action": recommended_action,
                "recovery_actions": [
                    {
                        "kind": recovery_kind,
                        "label": recommended_action,
                    }
                ],
            }
        if block_kind == BLOCK_KIND_INPUT_INTEGRITY:
            violations = [
                str(item).strip()
                for item in list(compact_context.get("violations") or [])
                if str(item).strip()
            ]
            failed_stage = str(compact_context.get("failed_stage") or "").strip()
            summary = "；".join(violations[:5]) or exc.message
            return {
                "title": "章节输入不完整",
                "summary": summary[:500],
                "detail": detail,
                "exception_type": type(exc).__name__,
                "error_code": exc.error_code,
                "context": compact_context,
                "recovery_actions": [
                    "检查并修复章节大纲的 POV、目标与场景定位。",
                    (
                        "重新生成 Bridge/Plan 后再写作。"
                        if failed_stage != "planning_input"
                        else "修复或重新生成当前章节的上游大纲。"
                    ),
                ],
            }
        return {
            "title": "任务失败",
            "summary": f"{type(exc).__name__}: {exc.message[:200]}",
            "detail": detail,
            "exception_type": type(exc).__name__,
            "error_code": exc.error_code,
            "context": compact_context,
        }
    # Callers may attach a pre-built ``error_summary`` attribute to a plain
    # RuntimeError to carry structured diagnostics (e.g. per-character TTS
    # voice-team reuse failures) through to the UI without inventing a new
    # exception subclass per scenario.
    caller_summary = getattr(exc, "error_summary", None)
    if isinstance(caller_summary, dict) and caller_summary:
        merged = {
            "title": str(caller_summary.get("title") or "任务失败").strip(),
            "summary": str(caller_summary.get("summary") or detail[:200]).strip(),
            "detail": str(caller_summary.get("detail") or detail).strip(),
            "exception_type": str(caller_summary.get("exception_type") or type(exc).__name__),
        }
        for key in (
            "category",
            "diagnoses",
            "confirmable_character_ids",
            "error_code",
            "missing_artifact",
            "context",
            "retryable",
            "recovery_actions",
            "attempt",
            "auto_resolved",
            "log_path",
            "run_log_dir",
        ):
            if key in caller_summary:
                merged[key] = caller_summary[key]
        return merged
    return {
        "title": "任务失败",
        "summary": f"{type(exc).__name__}: {detail[:200]}",
        "detail": detail,
        "exception_type": type(exc).__name__,
    }


def _classify_error_kind(exc: Exception) -> str:
    """Classify an exception for the control-plane error_kind field."""
    exc_name = type(exc).__name__
    # Map common NovelForge exception types to coarse categories
    if "Gateway" in exc_name or "RateLimit" in exc_name or "Authentication" in exc_name:
        return "gateway"
    if "Timeout" in exc_name:
        return "timeout"
    if "Consistency" in exc_name or "Compliance" in exc_name:
        return "consistency"
    if "Config" in exc_name:
        return "config"
    if "Persistence" in exc_name or "Storage" in exc_name:
        return "persistence"
    if "Cancelled" in exc_name:
        return "cancelled"
    return "internal"


class _JobWorker:
    def __init__(
        self,
        service: "JobService",
        record: JobRecord,
        command: JobCommand,
        prepared: PreparedCommand,
    ) -> None:
        self._service = service
        self.record = record
        self.command = command
        self.prepared = prepared
        self._cancel_requested = threading.Event()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._task: asyncio.Task[Any] | None = None
        self._loop_lock = Lock()
        self._decision_provider: Any | None = None
        self._thread = threading.Thread(
            target=self._run,
            name=f"novel-forge-job-{record.job_id}",
            daemon=True,
        )

    def start(self) -> None:
        self._thread.start()

    def _run(self) -> None:
        try:
            self._run_impl()
        finally:
            with self._service._lock:
                self._service._stopping_workers.pop(self.record.job_id, None)
            self._service._maybe_start_pending_jobs()

    def join(self, timeout: float | None = None) -> None:
        self._thread.join(timeout)

    def request_cancel(self) -> None:
        self._cancel_requested.set()
        with self._loop_lock:
            loop = self._loop
            task = self._task
        if loop is not None and task is not None:
            try:
                loop.call_soon_threadsafe(task.cancel)
            except RuntimeError:
                pass

    def provide_decision(self, request: DecisionRequest) -> bool:
        provider = self._decision_provider
        if provider is None:
            return False
        return bool(
            provider.provide_decision(
                request.decision_id,
                request.choice,
                custom_text=request.custom_text,
                approval_version=request.approval_version,
            )
        )

    def _run_impl(self) -> None:
        if self._cancel_requested.is_set():
            return
        self._service._handle_started(self.record.job_id)
        runtime = None
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        with self._loop_lock:
            self._loop = loop
        context_token = None
        if self._service._shadow.enabled:
            from novel_forge.control_plane.context import set_execution_context

            context_token = set_execution_context(
                work_unit_id=self.record.job_id,
                run_attempt_id=self._service._shadow.attempt_id_for(self.record.job_id),
                project_id=self.record.project_id,
            )
        try:
            self._service.require_authoring_dispatch_enabled(self.record)
            runtime = self._service._runtime_factory(self.command.mock)
            from novel_forge.pipeline.long.human_decision import CallbackDecisionProvider

            resolved_decisions = self.command.metadata.get("resolved_decisions")
            preseeded = resolved_decisions if isinstance(resolved_decisions, list) else None
            self._decision_provider = CallbackDecisionProvider(
                lambda payload: self._service._handle_decision_required(
                    self.record.job_id,
                    payload,
                ),
                preseeded_decisions=preseeded,
            )
            runtime.human_decision_provider = self._decision_provider
            runtime.planning_job_service = self._service

            async def _run_task() -> dict[str, Any]:
                if self._cancel_requested.is_set():
                    raise asyncio.CancelledError
                current = asyncio.current_task()
                with self._loop_lock:
                    self._task = current
                try:
                    return await self._service._executor.run(
                        dataclasses.replace(
                            self.prepared,
                            metadata={
                                **self.prepared.metadata,
                                "_is_cancelled": self._cancel_requested.is_set,
                            },
                        ),
                        runtime,
                        lambda step, data: self._service._handle_step(
                            self.record.job_id,
                            step,
                            _compact_payload(step, data),
                        ),
                    )
                finally:
                    try:
                        await runtime.shutdown()
                    except Exception as exc:
                        logger.warning(
                            "Job %s runtime shutdown failed: %s", self.record.job_id, exc
                        )

            result = loop.run_until_complete(_run_task())
        except asyncio.CancelledError:
            self._service._handle_cancelled_worker_exit(self.record.job_id)
            return
        except Exception as exc:
            from novel_forge.workspace.planning_jobs import PlanningTaskPending

            if isinstance(exc, PlanningTaskPending) and not self._cancel_requested.is_set():
                self._service._handle_finished(self.record.job_id, exc.payload)
                return
            if self._cancel_requested.is_set():
                self._service._handle_cancelled_worker_exit(self.record.job_id)
                return
            if runtime is not None:
                try:
                    loop.run_until_complete(runtime.shutdown())
                except Exception:
                    pass
            self._service._handle_failed(self.record.job_id, exc)
            return
        finally:
            try:
                pending = asyncio.all_tasks(loop)
                for task in pending:
                    task.cancel()
                if pending:
                    loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
                loop.run_until_complete(loop.shutdown_asyncgens())
            except Exception:
                logger.exception("Job %s loop cleanup failed", self.record.job_id)
            finally:
                loop.close()
            with self._loop_lock:
                self._loop = None
                self._task = None
            if context_token is not None:
                from novel_forge.control_plane.context import reset_execution_context

                reset_execution_context(context_token)

        self._service._handle_finished(self.record.job_id, result)


class JobService:
    async def create_authoring_proposal(
        self, runtime: Any, project_id: str, request: Any, *, proposal_id: str = ""
    ) -> Any:
        from novel_forge.app_service.authoring_foundation import create_authoring_proposal

        return await create_authoring_proposal(
            runtime, project_id, request, proposal_id=proposal_id
        )

    async def execute_authoring_suggestion(
        self, runtime: Any, project_id: str, chapter: int, suggestion: Any, *, action_id: str
    ) -> dict[str, Any]:
        from novel_forge.persistence.authoring_store import AuthoringStore

        root = runtime.storage.existing_project_dir(project_id)
        if suggestion.action == "prepare":
            AuthoringStore(root).require("prepare", chapter, explicit=True)
            record = self.submit(
                JobCommand(
                    job_id=action_id,
                    kind=JobKind.PREPARE_CHAPTER,
                    project_id=project_id,
                    payload={"project_id": project_id, "chapter_number": chapter},
                )
            )
            return {"action": "prepare", "task_id": record.job_id}
        if suggestion.action == "apply_approved_proposal":
            from novel_forge.app_service.authoring_proposals import apply_proposal

            view = await apply_proposal(runtime, self, project_id, suggestion.proposal_id)
            return {
                "action": "apply_approved_proposal",
                "proposal_id": view.id,
                **view.application_result,
            }
        raise ValueError("共创动作不在白名单")

    def planning_candidate_published(self, state: dict[str, Any]) -> None:
        self._handle_finished(state["job_id"], state)

    def maybe_refresh_semantic_consistency(self, project_id: str) -> JobRecord | None:
        """Auto-queue after source publication only for active non-manual policies."""

        if self._storage_root is None:
            return None
        from novel_forge.app_service.authoring_commands import AuthoringCommands
        from novel_forge.persistence.authoring_store import AuthoringStore, story_input_version
        from novel_forge.persistence.filesystem import FileSystemStorage

        root = FileSystemStorage(self._storage_root).existing_project_dir(project_id)
        policy = AuthoringStore(root).policy()
        if policy is None or policy.stopped or policy.mode == "manual":
            return None
        return AuthoringCommands(FileSystemStorage(self._storage_root), self).refresh_semantic_consistency(
            project_id,
            expected_story_version=story_input_version(root),
            expected_policy_version=policy.version,
        )

    def submit_planning_horizon(self, state: dict[str, Any]) -> JobRecord:
        from novel_forge.app_service.planning_jobs import submit_planning_job

        return submit_planning_job(self, state)

    def __init__(
        self,
        *,
        storage_root: Path | None = None,
        load_persisted_history: bool = True,
        executor: CommandExecutor | None = None,
        runtime_factory: Callable[[bool], RuntimeServices] | None = None,
        max_concurrent_jobs: int = _DEFAULT_MAX_CONCURRENT_JOBS,
        max_concurrent_tts_jobs: int | None = None,
        shadow_recorder: ShadowRecorder | None = None,
    ) -> None:
        self._lock = Lock()
        self._history_lock = Lock()
        self._jobs: dict[str, JobRecord] = {}
        self._workers: dict[str, _JobWorker] = {}
        self._stopping_workers: dict[str, _JobWorker] = {}
        self._pending_jobs: dict[str, PendingJob] = {}
        # Compatibility-only observation hook for older tests/extensions.
        # Durable autorun ownership lives in ``_book_autorun``.
        self._autorun_pending_ids: set[str] = set()
        self._stream = JobEventStream()
        self._executor = executor or WorkspaceCommandExecutor()
        self._runtime_factory = runtime_factory or (lambda mock: create_runtime_services(mock=mock))
        self._max_concurrent_jobs = max(1, int(max_concurrent_jobs or 1))
        self._tts_concurrency_follows_settings = max_concurrent_tts_jobs is None
        configured_tts_jobs = (
            get_settings().tts_background_pipeline_concurrency
            if max_concurrent_tts_jobs is None
            else max_concurrent_tts_jobs
        )
        self._max_concurrent_tts_jobs = self._bounded_tts_pipeline_limit(configured_tts_jobs)
        self._shutdown_requested = False
        self._storage_root = (
            storage_root.expanduser().resolve()
            if storage_root is not None
            else self._resolve_storage_root()
        )
        self._error_log = (
            TaskFlowErrorLog(self._storage_root) if self._storage_root is not None else None
        )
        # Shadow recorder for control-plane dual-write (None = disabled)
        self._shadow = shadow_recorder or ShadowRecorder(None)
        settings = get_settings()
        self._book_autorun = BookAutorunCoordinator(
            storage_root=self._storage_root,
            submit_command=self.submit,
            resume_job=self.resume_durable_job,
            get_job=self.get,
            notify_state=self._publish_book_autorun_state,
            checkpoint_budget=settings.autorun_max_checkpoint_resolve_attempts,
            failure_budget=settings.autorun_max_stage_failures,
            checkpoint_backoff_ms=settings.autorun_checkpoint_resolve_backoff_base_ms,
            failure_backoff_ms=settings.autorun_failure_backoff_base_ms,
        )
        # Run startup reconciliation if control plane is enabled
        self._run_startup_reconciliation()
        self._run_startup_publication_reconciliation()
        if load_persisted_history:
            self._load_persisted_history()
            self._recover_unfinished_system_ollama_intents()
            self._backfill_error_log_from_history()
            from novel_forge.app_service.authoring_proposals import recover_checkpoint_proposals

            recover_checkpoint_proposals(self)
        if self._storage_root is not None:
            from novel_forge.persistence.authoring_store import AuthoringStore

            for marker in self._storage_root.glob("*/.authoring/disabled.json"):
                project_root = marker.parent.parent
                if AuthoringStore(project_root).disabled():
                    try:
                        self.pause_authoring(project_root.name, disable=True)
                    except (OSError, ValueError):
                        logger.warning(
                            "Disabled authoring state needs recovery for %s", project_root.name
                        )
        self._book_autorun.recover()
        if load_persisted_history:
            from novel_forge.app_service.planning_jobs import recover_planning_jobs

            recover_planning_jobs(self)

    @property
    def storage_root(self) -> Path | None:
        """Read-only root used by Qt-free workflow and lineage projections."""

        return self._storage_root

    def _run_startup_reconciliation(self) -> None:
        """Scan the control plane for stale work units on startup."""
        store = getattr(self._shadow, "_store", None)
        if store is None:
            return
        try:
            from novel_forge.control_plane.reconciler import StartupReconciler

            timeout = get_settings().runtime_control_heartbeat_timeout_s
            reconciler = StartupReconciler(
                store,
                heartbeat_timeout_s=timeout,
                storage_root=self._storage_root,
            )
            result = reconciler.reconcile()
            if result.needs_attention > 0:
                logger.info(
                    "Startup reconciliation found %d work unit(s) needing attention "
                    "(retry_wait=%d, waiting_human=%d)",
                    result.needs_attention,
                    result.retry_wait,
                    result.waiting_human,
                )
        except Exception:
            logger.exception("Startup reconciliation failed (non-fatal)")

    def _run_startup_publication_reconciliation(self) -> None:
        """Rebuild derived publication views left pending by a prior crash."""

        if self._storage_root is None:
            return
        try:
            from novel_forge.workspace.publication import (
                reconcile_pending_publication_projections,
            )

            result = reconcile_pending_publication_projections(self._storage_root)
            if result["recovered"] or result["failed"]:
                logger.info(
                    "Publication reconciliation scanned=%d recovered=%d failed=%d",
                    result["scanned"],
                    result["recovered"],
                    result["failed"],
                )
        except Exception:
            logger.exception("Publication reconciliation failed (non-fatal)")

    def _check_idempotent(self, kind: str, project_id: str, payload: dict[str, Any]) -> str | None:
        """Check if a committed WorkUnit with the same intent already exists.

        Returns the existing job_id if the submit should be skipped, or None.
        """
        store = getattr(self._shadow, "_store", None)
        if store is None:
            return None
        kind_str = kind.value if hasattr(kind, "value") else str(kind)
        from novel_forge.control_plane.resume import check_idempotent_submit
        from novel_forge.control_plane.shadow import _compute_idempotency_key, _is_write_kind

        if not _is_write_kind(kind_str):
            return None
        try:
            key = _compute_idempotency_key(kind_str, project_id, payload)
            result = check_idempotent_submit(store, key)
            if result.should_skip_submit:
                logger.info(
                    "Idempotent skip: WorkUnit %s already committed for %s",
                    result.existing_work_unit_id,
                    key,
                )
                return result.existing_work_unit_id
        except Exception:
            logger.debug("Idempotency check failed (non-fatal)", exc_info=True)
        return None

    def submit(self, command: JobCommand) -> JobRecord:
        # A local source change can otherwise leave a long-lived API process
        # with stale imported Settings/schema classes.  Rejecting only new
        # work preserves already-running jobs and gives every transport the
        # same explicit recovery action.
        from novel_forge.common.runtime_identity import require_engine_revision_current

        require_engine_revision_current()
        prepared = self._executor.prepare(command)
        record_kwargs: dict[str, Any] = {}
        if command.job_id.strip():
            record_kwargs["job_id"] = command.job_id.strip()
        record_kind = command.record_kind or prepared.kind
        record = JobRecord(
            **record_kwargs,
            kind=record_kind,
            label=prepared.label,
            project_id="" if command.scope == JobScope.SYSTEM else prepared.project_id,
            project_label=(
                ""
                if command.scope == JobScope.SYSTEM
                else _project_label_from_request(prepared.request)
            ),
            scope=command.scope,
        )
        self.require_authoring_dispatch_enabled(record)
        # Idempotency check: skip submit if a committed WorkUnit with the
        # same idempotency key already exists in the control plane.
        idempotency_result = (
            self._check_idempotent(record_kind, prepared.project_id, command.payload)
            if record.scope == JobScope.PROJECT
            else None
        )
        if idempotency_result is not None:
            existing = self._jobs.get(idempotency_result)
            if existing is not None:
                return existing.model_copy(deep=True)
        worker = _JobWorker(self, record, command, prepared)
        start_worker = False
        with self._lock:
            if self._shutdown_requested:
                raise RuntimeError("JobService is shutting down")
            if record.job_id in self._jobs:
                return self._jobs[record.job_id].model_copy(deep=True)
            if self._tts_concurrency_follows_settings:
                self._max_concurrent_tts_jobs = self._bounded_tts_pipeline_limit(
                    get_settings().tts_background_pipeline_concurrency
                )
            conflict = self._find_active_write_conflict_locked(record)
            if conflict is None:
                conflict = self._find_active_tts_duplicate_locked(record, command)
            if conflict is None:
                conflict = self._find_active_system_duplicate_locked(record, command)
            if conflict is not None:
                return conflict.model_copy(deep=True)
            self._jobs[record.job_id] = record
            needs_durable_intent = (
                _kind_value(record.kind) in _PROJECT_EVIDENCE_JOB_KINDS
                or bool(command.payload.get("authoring_approval_id"))
            )
            self._pending_jobs[record.job_id] = PendingJob(
                record=record, worker=worker, intent_ready=not needs_durable_intent
            )
            if not needs_durable_intent and self._can_start_locked(record):
                self._pending_jobs.pop(record.job_id, None)
                self._workers[record.job_id] = worker
                start_worker = True
            snapshot = record.model_copy(deep=True)
        intent_payload_path = self._persist_control_plane_intent(record, command)
        if needs_durable_intent and not intent_payload_path:
            error = RuntimeError("任务意图未能持久化，未启动模型请求")
            with self._lock:
                self._pending_jobs.pop(record.job_id, None)
                self._workers.pop(record.job_id, None)
            self._handle_failed(record.job_id, error)
            raise error
        if record.scope == JobScope.PROJECT:
            self._shadow.on_submit(
                record.job_id,
                command,
                record,
                intent_payload_path=intent_payload_path,
            )
        if _kind_value(record.kind) == JobKind.INIT_LONG.value and bool(
            command.metadata.get("autorun_after_init")
        ):
            self._book_autorun.register_init(
                record=record,
                launch_key=str(command.metadata.get("idempotency_key") or ""),
                total_chapters=int(command.payload.get("total_chapters") or 1),
                writing_mode=(
                    "scene_level"
                    if command.metadata.get("writing_mode") == "scene_level"
                    else "whole_chapter"
                ),
                mock=command.mock,
            )
        if start_worker:
            worker.start()
        elif needs_durable_intent:
            with self._lock:
                pending = self._pending_jobs.get(record.job_id)
                if pending is not None:
                    self._pending_jobs[record.job_id] = dataclasses.replace(
                        pending, intent_ready=True
                    )
            self._maybe_start_pending_jobs()
        return snapshot

    def cancel(self, job_id: str, reason: str = "用户已取消") -> JobRecord:
        worker: _JobWorker | None = None
        with self._lock:
            record = self._jobs.get(job_id)
            if record is None:
                raise KeyError(job_id)
            if record.status not in {JobState.RUNNING, JobState.QUEUED}:
                return record
            record.status = JobState.FAILED
            record.current_step = "cancelled"
            record.error = reason
            record.updated_at = utc_now_iso()
            worker = self._workers.pop(job_id, None)
            if worker is not None:
                self._stopping_workers[job_id] = worker
            pending = self._pending_jobs.pop(job_id, None)
            if worker is None and pending is not None:
                worker = pending.worker
        if worker is not None:
            worker.request_cancel()
        self._persist_terminal_job(job_id)
        if record.scope == JobScope.PROJECT:
            self._shadow.on_cancelled(job_id, reason)
        self._publish(job_id, JobEventType.JOB_CANCELLED, payload={"reason": reason})
        self._book_autorun.on_job_cancelled(job_id, for_restart=self._shutdown_requested)
        self._maybe_start_pending_jobs()
        return self.get(job_id) or record

    def resume(self, job_id: str) -> JobRecord:
        """Resume a terminal or startup-reconciled job from durable intent.

        Normal task failures are represented in ``task_flow_history.json``;
        process crashes are represented by a retry-wait WorkUnit.  Both paths
        replay the same project-owned command envelope, while the pipeline's
        artifact validation decides the first incomplete stage.
        """

        existing = self.get(job_id)
        if existing is not None:
            if existing.status in {JobState.RUNNING, JobState.QUEUED}:
                return existing
            if existing.status not in {JobState.FAILED, JobState.PAUSED}:
                raise KeyError(job_id)
            command = self._load_persisted_intent(existing.project_id, job_id, scope=existing.scope)
            if command is None:
                raise KeyError(job_id)
            return self._submit_resumed_command(command, resumed_from_job_id=job_id)

        store = getattr(self._shadow, "_store", None)
        if store is not None:
            from novel_forge.control_plane.resume import WorkUnitResumer

            record = WorkUnitResumer(store).resume(self, job_id)
            if isinstance(record, JobRecord):
                return record
        raise KeyError(job_id)

    def resume_durable_job(
        self, project_id: str, job_id: str, resumed_job_id: str = ""
    ) -> JobRecord:
        """Resume a persisted intent even when its in-memory record was lost.

        Engine autorun uses this after a process restart: the session knows the
        exact attempt id, while a hard crash may have happened before that
        running record was copied into terminal history.
        """

        existing = self.get(job_id)
        if existing is not None:
            command = self._load_persisted_intent(project_id, job_id)
            if command is None:
                raise KeyError(job_id)
            return self._submit_resumed_command(
                command,
                resumed_from_job_id=job_id,
                job_id=resumed_job_id,
            )
        command = self._load_persisted_intent(project_id, job_id)
        if command is None:
            raise KeyError(job_id)
        return self._submit_resumed_command(
            command,
            resumed_from_job_id=job_id,
            job_id=resumed_job_id,
        )

    def start_book_autorun(
        self,
        *,
        project_id: str,
        chapter_number: int,
        mode: str = "book",
        launch_key: str = "",
        writing_mode: str = "whole_chapter",
        force: bool = False,
        skip_done: bool = True,
        mock: bool = False,
    ) -> JobRecord | None:
        """Start or idempotently resume the Engine-owned chapter state machine."""

        normalized_mode = "chapter" if mode == "chapter" else "book"
        normalized_writing_mode = (
            "scene_level" if writing_mode == "scene_level" else "whole_chapter"
        )
        return self._book_autorun.start(
            project_id=project_id,
            chapter_number=chapter_number,
            mode=cast(Literal["chapter", "book"], normalized_mode),
            launch_key=launch_key,
            writing_mode=cast(Literal["whole_chapter", "scene_level"], normalized_writing_mode),
            force=force,
            skip_done=skip_done,
            mock=mock,
        )

    def pause_book_autorun(self, project_id: str, *, reason: str = "用户已暂停连跑") -> str:
        return self._book_autorun.pause(project_id, reason=reason)

    def reset_book_autorun_after_cleanup(
        self,
        project_id: str,
        *,
        from_chapter: int,
    ) -> bool:
        return self._book_autorun.reset_after_chapter_cleanup(
            project_id,
            from_chapter=from_chapter,
        )

    def resolve_book_autorun_checkpoint(
        self,
        *,
        project_id: str,
        checkpoint_id: str,
        option_id: str,
        notes: str = "",
        force: bool = False,
        authoring_approval_id: str = "",
        job_id: str = "",
    ) -> JobRecord | None:
        return self._book_autorun.resolve_checkpoint(
            project_id=project_id,
            checkpoint_id=checkpoint_id,
            option_id=option_id,
            notes=notes,
            force=force,
            authoring_approval_id=authoring_approval_id,
            job_id=job_id,
        )

    def book_autorun_state(self, project_id: str) -> BookAutorunState | None:
        return self._book_autorun.state(project_id)

    def active_book_autorun_project_ids(self) -> list[str]:
        return self._book_autorun.active_project_ids()

    def resume_project(
        self,
        project_id: str,
        kind: JobKind | str,
        *,
        metadata_updates: dict[str, Any] | None = None,
        payload_updates: dict[str, Any] | None = None,
        fallback_command: JobCommand | None = None,
    ) -> JobRecord:
        """Resume the newest durable intent for one project and workflow kind.

        This is the project-level recovery path used when task history has been
        cleared or the app restarted.  It deliberately creates a new attempt ID
        while retaining ``resumed_from_job_id`` for diagnostics.
        """

        normalized_project_id = project_id.strip()
        kind_value = _kind_value(kind)
        if not normalized_project_id or kind_value not in _PROJECT_DURABLE_JOB_KINDS:
            raise KeyError(project_id)

        records = self.list(project_id=normalized_project_id)
        for record in records:
            if _kind_value(record.kind) != kind_value:
                continue
            if record.status in {JobState.RUNNING, JobState.QUEUED}:
                return record

        for record in records:
            if _kind_value(record.kind) != kind_value:
                continue
            if record.status not in {JobState.FAILED, JobState.PAUSED}:
                continue
            command = self._load_persisted_intent(normalized_project_id, record.job_id)
            if command is not None:
                return self._submit_resumed_command(
                    command,
                    resumed_from_job_id=record.job_id,
                    metadata_updates=metadata_updates,
                    payload_updates=payload_updates,
                )

        latest = self._latest_persisted_intent(normalized_project_id, kind_value)
        if latest is not None:
            source_job_id, command = latest
            return self._submit_resumed_command(
                command,
                resumed_from_job_id=source_job_id,
                metadata_updates=metadata_updates,
                payload_updates=payload_updates,
            )

        if fallback_command is None:
            raise KeyError(project_id)
        return self._submit_resumed_command(
            fallback_command,
            resumed_from_job_id="",
            metadata_updates=metadata_updates,
            payload_updates=payload_updates,
        )

    def get(self, job_id: str) -> JobRecord | None:
        with self._lock:
            record = self._jobs.get(job_id)
            if record is not None:
                self._backfill_project_id_locked(record)
            return record.model_copy(deep=True) if record is not None else None

    def runtime_activity_counts(self) -> tuple[int, int]:
        """Return running/queued counts without exposing project or job data."""

        with self._lock:
            return len(self._workers), len(self._pending_jobs)

    def refresh_runtime_settings(self, settings: Settings) -> None:
        """Refresh defaults for future jobs without cancelling in-flight work."""

        with self._lock:
            if self._tts_concurrency_follows_settings:
                self._max_concurrent_tts_jobs = self._bounded_tts_pipeline_limit(
                    settings.tts_background_pipeline_concurrency
                )
        self._book_autorun.refresh_defaults(
            checkpoint_budget=settings.autorun_max_checkpoint_resolve_attempts,
            failure_budget=settings.autorun_max_stage_failures,
            checkpoint_backoff_ms=settings.autorun_checkpoint_resolve_backoff_base_ms,
            failure_backoff_ms=settings.autorun_failure_backoff_base_ms,
        )

    def list(self, project_id: str | None = None) -> list[JobRecord]:
        with self._lock:
            for record in self._jobs.values():
                self._backfill_project_id_locked(record)
            records = [
                record.model_copy(deep=True)
                for record in self._jobs.values()
                if not project_id or record.project_id == project_id
            ]
        return sorted(
            records,
            key=lambda item: item.created_at,
            reverse=True,
        )

    def clear_inactive_history(self, job_ids: set[str] | None = None) -> builtins.list[str]:
        """Remove completed, failed, and safely suspended task-flow records.

        ``PAUSED`` is a non-executing state in the desktop task-flow contract:
        it may represent an abandoned checkpoint or a stale decision request.
        It is therefore removable when no live worker or queued submission owns
        it.  Running and queued work always remains protected.
        """

        removable_states = {JobState.SUCCEEDED, JobState.FAILED, JobState.PAUSED}
        with self._lock:
            removed = [
                record.model_copy(deep=True)
                for job_id, record in self._jobs.items()
                if record.status in removable_states
                and (job_ids is None or job_id in job_ids)
                and job_id not in self._workers
                and job_id not in self._pending_jobs
            ]
            removed_ids = {record.job_id for record in removed}
            for job_id in removed_ids:
                self._jobs.pop(job_id, None)

        scopes = {(record.scope, record.project_id) for record in removed}
        for scope, project_id in scopes:
            path = self._history_path(project_id, scope=scope)
            if path is None:
                continue
            try:
                remaining = [
                    record
                    for record in self._read_history_records(path)
                    if record.job_id not in removed_ids
                ]
                self._write_history_records(path, remaining)
            except OSError:
                logger.warning("Failed to rewrite job history for %s", project_id)
        return [record.job_id for record in removed]

    def acknowledge_error_log_entries(self, entry_ids: set[str]) -> builtins.list[str]:
        """Persist human review state for exact task-flow diagnostics."""

        self._ensure_error_entries_archived(entry_ids)
        if self._error_log is None:
            return []
        return self._error_log.acknowledge_entries(entry_ids)

    def reopen_error_log_entries(self, entry_ids: set[str]) -> builtins.list[str]:
        """Return explicitly confirmed diagnostics to the pending queue."""

        self._ensure_error_entries_archived(entry_ids)
        if self._error_log is None:
            return []
        return self._error_log.reopen_entries(entry_ids)

    def clear_closed_error_log_entries(self, entry_ids: set[str]) -> ClosedTaskErrorCleanup:
        """Remove only Engine-verified closed diagnostics and terminal task cards."""

        self._ensure_error_entries_archived(entry_ids)
        if self._error_log is None:
            return ClosedTaskErrorCleanup([], [], [])
        result = self._error_log.remove_closed_entries(entry_ids)
        cleared_task_ids = self.clear_inactive_history(set(result.task_ids))
        return ClosedTaskErrorCleanup(
            cleared_task_ids=cleared_task_ids,
            cleared_error_entry_ids=result.removed_entry_ids,
            retained_pending_error_entry_ids=result.retained_pending_entry_ids,
        )

    def _ensure_error_entries_archived(self, entry_ids: set[str]) -> None:
        """Make live diagnostics durable before their review state is mutated.

        A retry may expose an error before its job becomes terminal.  Persisting
        the compact record first lets confirmation/reopen remain durable without
        treating the live task itself as closed or altering its execution.
        """

        if self._error_log is None:
            return
        wanted = {str(entry_id).strip() for entry_id in entry_ids if str(entry_id).strip()}
        if not wanted:
            return
        with self._lock:
            records = [record.model_copy(deep=True) for record in self._jobs.values()]
        for record in records:
            generated_ids = {str(entry["id"]) for entry in entries_from_job_record(record)}
            if wanted.intersection(generated_ids):
                self._error_log.archive_job(record)

    def list_error_log(
        self,
        project_id: str | None = None,
    ) -> builtins.list[dict[str, Any]]:
        """Return durable diagnostics, including entries retained after task cleanup."""

        archived = (
            self._error_log.entries(project_id=project_id or "")
            if self._error_log is not None
            else []
        )
        with self._lock:
            current = [record.model_copy(deep=True) for record in self._jobs.values()]
        if project_id:
            current = [record for record in current if record.project_id == project_id]
        merged = {str(entry.get("id") or ""): entry for entry in archived}
        for record in current:
            for entry in entries_from_job_record(record):
                merged.setdefault(str(entry["id"]), entry)
        return sorted(merged.values(), key=lambda entry: str(entry.get("time") or ""), reverse=True)

    def _resume_gated_work_unit(
        self,
        job_id: str,
        request: DecisionRequest,
    ) -> JobRecord | None:
        """Resume a WAITING_HUMAN work unit after the author's decision.

        Used when no live worker can consume the decision (typically after an
        app restart while the flow was gated on a chapter confirmation).
        Returns the resubmitted JobRecord, or None when the control plane
        has no such gated work unit.
        """

        store = getattr(self._shadow, "_store", None)
        if store is None:
            return None
        from novel_forge.control_plane.enums import WorkUnitState
        from novel_forge.control_plane.resume import WorkUnitResumer

        resumer = WorkUnitResumer(store)
        work_unit = resumer.get_work_unit(job_id)
        if work_unit is None or work_unit.state != WorkUnitState.WAITING_HUMAN:
            return None
        decision = request.model_dump(mode="json")
        record = resumer.resume_after_decision(self, job_id, decision)
        if isinstance(record, JobRecord):
            self._publish(
                job_id,
                JobEventType.JOB_STEP,
                step="human_decision_submitted",
                payload=decision,
            )
            return record
        return None

    def provide_decision(self, job_id: str, payload: dict[str, Any]) -> JobRecord:
        request = DecisionRequest.model_validate(payload)
        with self._lock:
            worker = self._workers.get(job_id)
            record = self._jobs.get(job_id)
            pending = record.pending_decision if record is not None else {}
        if pending:
            required = bool(pending.get("requires_explicit_approval")) or pending.get("kind") in {
                "init_copilot_gate",
                "init_request_drift_gate",
            }
            if request.decision_id != pending.get("decision_id") or request.choice not in {
                option.get("id") for option in pending.get("options", [])
            }:
                raise ValueError("选项不属于当前等待决定")
            if required and (
                not request.approval_version
                or request.approval_version != pending.get("approval_version")
            ):
                raise ValueError("批准版本已过期或客户端不支持版本批准；请刷新后重新确认")
        if worker is None or not worker.provide_decision(request):
            # The worker vanished (e.g. an app restart while the flow was
            # gated on "第 N 章确认后继续"): fall back to the durable
            # control-plane gate, which records the decision into the intent
            # payload and resumes the WAITING_HUMAN work unit.
            resumed = self._resume_gated_work_unit(job_id, request)
            if resumed is not None:
                with self._lock:
                    if job_id in self._jobs:
                        self._jobs[job_id].pending_decision = {}
                self._persist_terminal_job(job_id, include_running=True)
                return resumed
            raise KeyError(job_id)
        self._publish(
            job_id,
            JobEventType.JOB_STEP,
            step="human_decision_submitted",
            payload=request.model_dump(mode="json"),
        )
        record = self.get(job_id)
        if record is None:
            raise KeyError(job_id)
        with self._lock:
            self._jobs[job_id].pending_decision = {}
        self._persist_terminal_job(job_id, include_running=True)
        return self.get(job_id) or record

    def subscribe(self, job_id: str | None = None) -> Iterator[JobEvent]:
        subscription = self._stream.subscribe(job_id)
        try:
            yield from subscription
        finally:
            subscription.close()

    def open_subscription(
        self,
        job_id: str | None = None,
        *,
        max_queue_size: int = 0,
    ) -> EventSubscription:
        return self._stream.subscribe(job_id, max_queue_size=max_queue_size)

    def replay_events(self, job_id: str) -> builtins.list[JobEvent]:
        record = self.get(job_id)
        if record is None:
            raise KeyError(job_id)
        events = [
            JobEvent(
                job_id=job_id,
                type=JobEventType.JOB_SNAPSHOT,
                payload={"record": record.model_dump(mode="json")},
            )
        ]
        events.extend(
            JobEvent(
                job_id=job_id,
                type=JobEventType.JOB_STEP,
                at=event.at,
                step=event.step,
                payload=event.payload,
            )
            for event in compact_progress_event_history(record.events, limit=_MAX_JOB_EVENTS)
        )
        terminal_type = {
            JobState.SUCCEEDED: JobEventType.JOB_SUCCEEDED,
            JobState.FAILED: JobEventType.JOB_FAILED,
            JobState.PAUSED: JobEventType.JOB_PAUSED,
        }.get(record.status)
        if terminal_type is not None:
            events.append(
                JobEvent(
                    job_id=job_id,
                    type=terminal_type,
                    payload=record.result
                    if terminal_type != JobEventType.JOB_FAILED
                    else record.error_summary,
                )
            )
        return events

    def cancel_all(self, reason: str = "服务正在关闭") -> None:
        with self._lock:
            job_ids = [
                job_id
                for job_id, record in self._jobs.items()
                if record.status in {JobState.RUNNING, JobState.QUEUED}
            ]
        for job_id in job_ids:
            try:
                self.cancel(job_id, reason=reason)
            except KeyError:
                pass

    def shutdown(self, *, wait_s: float = 2.0, reason: str = "服务正在关闭") -> None:
        with self._lock:
            self._shutdown_requested = True
            workers = list(self._workers.values())
            job_ids = list(self._pending_jobs) + list(self._workers)
        self._book_autorun.shutdown()
        for job_id in job_ids:
            try:
                self.cancel(job_id, reason=reason)
            except KeyError:
                pass
        deadline = time.monotonic() + max(0.0, wait_s)
        for worker in workers:
            worker.join(max(0.0, deadline - time.monotonic()))

    @staticmethod
    def _resolve_storage_root() -> Path | None:
        try:
            return Path(get_settings().storage_root).expanduser().resolve()
        except Exception:
            return None

    def _history_path(
        self,
        project_id: str,
        *,
        scope: JobScope = JobScope.PROJECT,
    ) -> Path | None:
        if self._storage_root is None:
            return None
        if scope == JobScope.SYSTEM:
            return self._storage_root / _SYSTEM_STATE_DIRNAME / "states" / _JOB_HISTORY_FILENAME
        if not project_id:
            return None
        return ProjectLayout(self._storage_root / project_id).states_dir / _JOB_HISTORY_FILENAME

    def _persist_control_plane_intent(self, record: JobRecord, command: JobCommand) -> str:
        """Persist a resumable command envelope inside the owning project.

        The control-plane database remains metadata-only.  Its WorkUnit stores
        this path, while the full user intent lives under the project that owns
        the work.  A failed/reconciled WorkUnit can therefore be rebuilt
        without pretending that an LLM call is deterministic.
        """

        # The envelope is also the non-control-plane recovery source used by
        # NIMO's project-level "继续立项" action, so persistence must not depend
        # on whether shadow recording is enabled.
        if self._storage_root is None:
            return ""
        if not self._is_durable_record(record):
            return ""
        path = self._intent_path(record.project_id, record.job_id, scope=record.scope)
        if path is None:
            return ""
        payload = command.model_dump(mode="json")
        payload["job_id"] = record.job_id
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            temp_path = path.with_suffix(".tmp")
            temp_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            temp_path.replace(path)
            return str(path)
        except OSError:
            logger.warning(
                "Failed to persist control-plane intent | job=%s path=%s",
                record.job_id,
                path,
                exc_info=True,
            )
            return ""

    def _load_persisted_intent(
        self,
        project_id: str,
        job_id: str,
        *,
        scope: JobScope = JobScope.PROJECT,
    ) -> JobCommand | None:
        if not job_id or self._storage_root is None:
            return None
        path = self._intent_path(project_id, job_id, scope=scope)
        if path is None:
            return None
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            command = JobCommand.model_validate(raw)
        except (OSError, ValueError, TypeError):
            return None
        command_project_id = (
            command.project_id.strip() or str(command.payload.get("project_id") or "").strip()
        )
        if command.scope != scope or command_project_id != project_id:
            logger.warning(
                "Ignoring mismatched persisted intent | expected_project=%s actual_project=%s",
                project_id,
                command_project_id,
            )
            return None
        return command

    def _intent_path(
        self,
        project_id: str,
        job_id: str,
        *,
        scope: JobScope = JobScope.PROJECT,
    ) -> Path | None:
        if self._storage_root is None or not job_id:
            return None
        if scope == JobScope.SYSTEM:
            return (
                self._storage_root
                / _SYSTEM_STATE_DIRNAME
                / "states"
                / _CONTROL_PLANE_INTENT_DIRNAME
                / f"{job_id}.json"
            )
        if not project_id:
            return None
        return (
            ProjectLayout(self._storage_root / project_id).states_dir
            / _CONTROL_PLANE_INTENT_DIRNAME
            / f"{job_id}.json"
        )

    @staticmethod
    def _is_durable_record(record: JobRecord) -> bool:
        kind = _kind_value(record.kind)
        if record.scope == JobScope.SYSTEM:
            return kind in _SYSTEM_DURABLE_JOB_KINDS
        return bool(record.project_id) and kind in _PROJECT_DURABLE_JOB_KINDS

    def _latest_persisted_intent(
        self,
        project_id: str,
        kind: str,
    ) -> tuple[str, JobCommand] | None:
        if self._storage_root is None:
            return None
        intent_dir = (
            ProjectLayout(self._storage_root / project_id).states_dir
            / _CONTROL_PLANE_INTENT_DIRNAME
        )
        try:
            candidates = sorted(
                (path for path in intent_dir.glob("*.json") if path.is_file()),
                key=lambda path: path.stat().st_mtime_ns,
                reverse=True,
            )
        except OSError:
            return None
        for path in candidates:
            command = self._load_persisted_intent(project_id, path.stem)
            if command is not None and _kind_value(command.kind) == kind:
                return path.stem, command
        return None

    def _submit_resumed_command(
        self,
        command: JobCommand,
        *,
        resumed_from_job_id: str,
        metadata_updates: dict[str, Any] | None = None,
        payload_updates: dict[str, Any] | None = None,
        job_id: str = "",
    ) -> JobRecord:
        metadata = dict(command.metadata)
        metadata.update(metadata_updates or {})
        metadata["resume_requested"] = True
        if resumed_from_job_id:
            metadata["resumed_from_job_id"] = resumed_from_job_id
        payload = {**command.payload, **(payload_updates or {})}
        resumed = command.model_copy(
            update={
                "job_id": job_id,
                "payload": payload,
                "metadata": metadata,
            }
        )
        return self.submit(resumed)

    @staticmethod
    def _read_history_records(path: Path) -> builtins.list[JobRecord]:
        if not path.exists():
            return []
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        if not isinstance(raw, list):
            return []
        records: builtins.list[JobRecord] = []
        for item in raw:
            if isinstance(item, dict):
                record = JobRecord.from_history_payload(item)
                if record is not None:
                    records.append(record)
        return records

    @staticmethod
    def _write_history_records(path: Path, records: builtins.list[JobRecord]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = [
            record.to_history_payload() for record in records[:_MAX_PERSISTED_JOBS_PER_PROJECT]
        ]
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp_path.replace(path)

    def _load_persisted_history(self) -> None:
        root = self._storage_root
        if root is None or not root.exists():
            return
        loaded: dict[str, JobRecord] = {}
        try:
            project_dirs = [
                item
                for item in root.iterdir()
                if item.is_dir()
                and not is_generated_test_project_id(item.name)
                and item.name.casefold() != "dlq"
            ]
        except OSError:
            return
        terminal = {JobState.SUCCEEDED, JobState.FAILED, JobState.PAUSED}
        for project_dir in project_dirs:
            path = ProjectLayout(project_dir).states_dir / _JOB_HISTORY_FILENAME
            for record in self._read_history_records(path):
                if record.status in terminal and record.project_id:
                    loaded[record.job_id] = record
        system_path = self._history_path("", scope=JobScope.SYSTEM)
        if system_path is not None:
            for record in self._read_history_records(system_path):
                if record.status in terminal and record.scope == JobScope.SYSTEM:
                    loaded[record.job_id] = record
        with self._lock:
            self._jobs.update({k: v for k, v in loaded.items() if k not in self._jobs})

    def _recover_unfinished_system_ollama_intents(self) -> None:
        """Reconcile interrupted Ollama commands against the real model catalog.

        System jobs have no project directory to inspect after an Engine
        restart.  Their durable intent is therefore adjudicated against the
        native catalog first: a present pulled model and an absent deleted
        model are already successful.  Only an unresolved intent is replayed.
        """

        root = self._storage_root
        if root is None:
            return
        intent_dir = root / _SYSTEM_STATE_DIRNAME / "states" / _CONTROL_PLANE_INTENT_DIRNAME
        try:
            candidates = sorted(path for path in intent_dir.glob("*.json") if path.is_file())
        except OSError:
            return
        with self._lock:
            terminal_job_ids = {
                record.job_id
                for record in self._jobs.values()
                if record.scope == JobScope.SYSTEM
                and record.status in {JobState.SUCCEEDED, JobState.FAILED, JobState.PAUSED}
            }
        for path in candidates:
            job_id = path.stem
            if job_id in terminal_job_ids:
                continue
            command = self._load_persisted_intent("", job_id, scope=JobScope.SYSTEM)
            if command is None or _kind_value(command.kind) not in _SYSTEM_DURABLE_JOB_KINDS:
                continue
            resolution = self._resolve_ollama_recovery(command)
            if resolution is not None:
                self._record_recovered_system_terminal(command, job_id, resolution)
                continue
            self._record_recovered_system_interruption(command, job_id)
            try:
                self._submit_resumed_command(
                    command,
                    resumed_from_job_id=job_id,
                    metadata_updates={"engine_restart_recovery": True},
                )
            except Exception:
                logger.warning("Failed to resume system Ollama task %s after restart", job_id)

    @staticmethod
    def _resolve_ollama_recovery(command: JobCommand) -> dict[str, Any] | None:
        kind = _kind_value(command.kind)
        if kind not in {JobKind.OLLAMA_PULL_MODEL.value, JobKind.OLLAMA_DELETE_MODEL.value}:
            return None
        model = str(command.payload.get("model") or "").strip()
        if not model:
            return None
        try:
            from novel_forge.app_service.ollama_control import get_ollama_control_service

            exists = get_ollama_control_service(get_settings()).model_exists(model)
        except Exception:
            return None
        if kind == JobKind.OLLAMA_PULL_MODEL.value and exists:
            return {
                "model": model,
                "status": "already_present_after_restart",
                "percent": 100,
                "checkpoint": "committed",
            }
        if kind == JobKind.OLLAMA_DELETE_MODEL.value and not exists:
            return {
                "model": model,
                "status": "already_absent_after_restart",
                "checkpoint": "committed",
            }
        return None

    def _record_recovered_system_terminal(
        self,
        command: JobCommand,
        job_id: str,
        result: dict[str, Any],
    ) -> None:
        try:
            prepared = self._executor.prepare(command)
        except Exception:
            return
        record = JobRecord(
            job_id=job_id,
            kind=command.kind,
            label=prepared.label,
            scope=JobScope.SYSTEM,
            status=JobState.SUCCEEDED,
            current_step="recovery_committed",
            result=result,
        )
        with self._lock:
            self._jobs[job_id] = record
        self._persist_terminal_job(job_id)

    def _record_recovered_system_interruption(self, command: JobCommand, job_id: str) -> None:
        try:
            prepared = self._executor.prepare(command)
        except Exception:
            return
        record = JobRecord(
            job_id=job_id,
            kind=command.kind,
            label=prepared.label,
            scope=JobScope.SYSTEM,
            status=JobState.FAILED,
            current_step="engine_restart_recovery",
            error="Engine 重启前任务未完成；已按持久意图重新提交。",
        )
        with self._lock:
            self._jobs[job_id] = record
        self._persist_terminal_job(job_id)

    def _backfill_error_log_from_history(self) -> None:
        if self._error_log is None:
            return
        with self._lock:
            records = [record.model_copy(deep=True) for record in self._jobs.values()]
        for record in records:
            try:
                self._error_log.archive_job(record)
            except OSError:
                logger.warning("Failed to backfill task-flow diagnostics for %s", record.job_id)

    def _persist_terminal_job(self, job_id: str, *, include_running: bool = False) -> None:
        with self._lock:
            record = self._jobs.get(job_id)
            snapshot = record.model_copy(deep=True) if record is not None else None
        if snapshot is None or not self._is_durable_record(snapshot):
            return
        if (
            snapshot.status not in {JobState.SUCCEEDED, JobState.FAILED, JobState.PAUSED}
            and not include_running
        ):
            return
        if snapshot.pending_decision and snapshot.status == JobState.RUNNING:
            snapshot.status = JobState.PAUSED  # durable wait, live worker remains running
        path = self._history_path(snapshot.project_id, scope=snapshot.scope)
        if path is not None:
            try:
                with self._history_lock:
                    history = self._read_history_records(path)
                    merged = [snapshot]
                    merged.extend(item for item in history if item.job_id != snapshot.job_id)
                    self._write_history_records(path, merged)
            except OSError:
                logger.warning("Failed to persist task history for %s", snapshot.job_id)
        if self._error_log is not None:
            try:
                self._error_log.archive_job(snapshot)
            except OSError:
                logger.warning("Failed to archive task-flow diagnostics for %s", snapshot.job_id)
        if self._storage_root is not None and snapshot.project_id:
            from novel_forge.app_service.authoring_proposals import reconcile_checkpoint_job

            try:
                reconcile_checkpoint_job(self._storage_root / snapshot.project_id, snapshot)
            except (OSError, ValueError):
                logger.warning("Failed to reconcile authoring proposal for %s", snapshot.job_id)

    def _active_count_locked(self) -> int:
        return len(self._workers) + len(self._stopping_workers)

    def _bounded_tts_pipeline_limit(self, configured: int | None) -> int:
        """Reserve one global worker slot for novel/control-plane work."""

        available = max(1, self._max_concurrent_jobs - 1)
        return min(available, max(1, int(configured or 1)))

    def _active_tts_count_locked(self) -> int:
        return sum(
            1
            for job_id in self._workers
            if (record := self._jobs.get(job_id)) is not None
            and _kind_value(record.kind) in _TTS_JOB_KINDS
        )

    def _active_tts_project_locked(self, project_id: str) -> bool:
        if not project_id:
            return False
        return any(
            (record := self._jobs.get(job_id)) is not None
            and record.project_id == project_id
            and _kind_value(record.kind) in _TTS_JOB_KINDS
            for job_id in self._workers
        )

    def _can_start_locked(self, record: JobRecord) -> bool:
        try:
            self.require_authoring_dispatch_enabled(record)
        except ValueError:
            return False
        if any(
            worker.record.project_id == record.project_id
            for worker in self._stopping_workers.values()
        ):
            return False
        if self._active_count_locked() >= self._max_concurrent_jobs:
            return False
        if _kind_value(record.kind) in _TTS_JOB_KINDS:
            if self._active_tts_count_locked() >= self._max_concurrent_tts_jobs:
                return False
            if self._active_tts_project_locked(record.project_id):
                return False
        return (
            self._find_active_write_conflict_locked(
                record,
                ignore_job_ids={record.job_id},
                statuses={JobState.RUNNING},
            )
            is None
        )

    def _find_active_tts_duplicate_locked(
        self,
        record: JobRecord,
        command: JobCommand,
    ) -> JobRecord | None:
        kind = _kind_value(record.kind)
        if kind not in _TTS_JOB_KINDS or not record.project_id:
            return None
        chapter_number = command.payload.get("chapter_number")
        for job_id, existing in self._jobs.items():
            if existing.project_id != record.project_id or _kind_value(existing.kind) != kind:
                continue
            if existing.status not in {JobState.RUNNING, JobState.QUEUED}:
                continue
            pending = self._pending_jobs.get(job_id)
            worker = self._workers.get(job_id)
            active_command = (
                pending.worker.command
                if pending is not None
                else (worker.command if worker is not None else None)
            )
            if active_command is None:
                continue
            if active_command.payload.get("chapter_number") == chapter_number:
                return existing
        return None

    def _find_active_system_duplicate_locked(
        self,
        record: JobRecord,
        command: JobCommand,
    ) -> JobRecord | None:
        """Serialize active Engine-global operations for one native resource."""

        if record.scope != JobScope.SYSTEM:
            return None
        kind = _kind_value(record.kind)
        if kind not in _SYSTEM_DURABLE_JOB_KINDS:
            return None
        resource = (
            "runtime"
            if kind == JobKind.OLLAMA_RUNTIME_CONTROL.value
            else str(command.payload.get("model") or "")
        )
        explicit_key = str(command.metadata.get("idempotency_key") or "")
        for job_id, existing in self._jobs.items():
            if existing.scope != JobScope.SYSTEM:
                continue
            if existing.status not in {JobState.RUNNING, JobState.QUEUED}:
                continue
            pending = self._pending_jobs.get(job_id)
            worker = self._workers.get(job_id)
            active_command = (
                pending.worker.command
                if pending is not None
                else (worker.command if worker is not None else None)
            )
            if active_command is None:
                continue
            existing_kind = _kind_value(existing.kind)
            active_resource = (
                "runtime"
                if existing_kind == JobKind.OLLAMA_RUNTIME_CONTROL.value
                else str(active_command.payload.get("model") or "")
            )
            active_key = str(active_command.metadata.get("idempotency_key") or "")
            same_runtime = (
                kind == JobKind.OLLAMA_RUNTIME_CONTROL.value
                and existing_kind == JobKind.OLLAMA_RUNTIME_CONTROL.value
            )
            same_model = bool(resource) and resource == active_resource
            if same_runtime or same_model or (explicit_key and explicit_key == active_key):
                return existing
        return None

    def _find_active_write_conflict_locked(
        self,
        record: JobRecord,
        *,
        ignore_job_ids: set[str] | None = None,
        statuses: set[JobState] | None = None,
    ) -> JobRecord | None:
        kind = _kind_value(record.kind)
        if kind not in _WRITE_JOB_KINDS or not record.project_id:
            return None
        ignored = ignore_job_ids or set()
        active_statuses = statuses or {JobState.RUNNING, JobState.QUEUED}
        for existing in self._jobs.values():
            if existing.job_id == record.job_id or existing.job_id in ignored:
                continue
            if existing.project_id != record.project_id:
                continue
            if _kind_value(existing.kind) not in _WRITE_JOB_KINDS:
                continue
            if existing.status in active_statuses:
                return existing
        return None

    def _maybe_start_pending_jobs(self) -> None:
        to_start: list[_JobWorker] = []
        with self._lock:
            if self._shutdown_requested:
                return
            pending_items = sorted(
                self._pending_jobs.items(),
                key=lambda item: (
                    _kind_value(item[1].record.kind) in _TTS_JOB_KINDS,
                    item[1].record.created_at,
                ),
            )
            for job_id, pending in pending_items:
                if not pending.intent_ready or not self._can_start_locked(pending.record):
                    continue
                self._pending_jobs.pop(job_id, None)
                self._workers[job_id] = pending.worker
                to_start.append(pending.worker)
                if self._active_count_locked() >= self._max_concurrent_jobs:
                    break
        for worker in to_start:
            worker.start()

    def _publish_section_change(self, job_id: str, project_id: str, kind: JobKind | str) -> None:
        section = _JOB_KIND_TO_SECTION.get(_kind_value(kind))
        if section is None or not project_id:
            return
        self._publish(
            job_id,
            JobEventType.SECTION_CHANGED,
            payload={"project_id": project_id, "section": section},
        )

    def _publish_book_autorun_state(self, state: BookAutorunState) -> None:
        """Invalidate both UI projections whenever orchestration state changes."""

        self._publish(
            state.active_job_id or f"autorun:{state.project_id}",
            JobEventType.SECTION_CHANGED,
            payload={
                "project_id": state.project_id,
                "section": "chapters",
                "autorun": state.model_dump(mode="json"),
            },
        )

    def _publish(
        self,
        job_id: str,
        event_type: JobEventType,
        *,
        step: str = "",
        payload: dict[str, Any] | None = None,
    ) -> None:
        self._stream.publish(
            JobEvent(
                job_id=job_id,
                type=event_type,
                step=step,
                payload=payload or {},
            )
        )

    def _handle_started(self, job_id: str) -> None:
        with self._lock:
            record = self._jobs.get(job_id)
            if record is None:
                return
            record.status = JobState.RUNNING
            record.current_step = ""
            record.current_step_payload = {}
            record.updated_at = utc_now_iso()
        self._shadow.on_started(job_id)
        self._publish(job_id, JobEventType.JOB_STARTED)

    def _backfill_project_id_locked(
        self,
        record: JobRecord,
        payload: dict[str, Any] | None = None,
    ) -> None:
        """Bind legacy auto-project jobs as soon as their run logger identifies them.

        Older automatic short/long jobs were submitted without a project ID,
        even though ``run_log_started`` already carries the ID created by the
        worker. The backfill makes their in-flight artifact windows usable;
        the route now assigns the ID earlier for all new jobs.
        """

        if record.scope != JobScope.PROJECT or record.project_id:
            return

        candidate_payloads: list[dict[str, Any]] = [
            item
            for item in (payload, record.current_step_payload, record.result)
            if isinstance(item, dict)
        ]
        candidate_payloads.extend(
            event.payload for event in reversed(record.events) if isinstance(event.payload, dict)
        )
        for candidate in candidate_payloads:
            project_id = str(candidate.get("project_id") or "").strip()
            if project_id:
                record.project_id = project_id
                return

        # Bounded event history can eventually drop run_log_started, but its
        # path is retained in ``record.result`` for task diagnostics. Recover
        # the project directory only when it is clearly nested under storage.
        run_log_dir = str(record.result.get("run_log_dir") or "").strip()
        if not run_log_dir or self._storage_root is None:
            return
        try:
            relative = Path(run_log_dir).resolve().relative_to(self._storage_root.resolve())
        except (OSError, ValueError):
            return
        if relative.parts:
            record.project_id = relative.parts[0]

    def _handle_step(self, job_id: str, step: str, payload: dict[str, Any]) -> None:
        token_payload: dict[str, Any] | None = None
        with self._lock:
            record = self._jobs.get(job_id)
            if record is None or record.status == JobState.FAILED:
                return
            self._backfill_project_id_locked(record, payload)
            if step == "repair_attempt_guidance" and "dimension" in payload:
                record.current_step = str(payload["dimension"])
                record.current_step_payload = dict(payload)
            elif not is_non_progress_step_event(step):
                record.current_step = step
                record.current_step_payload = dict(payload)
            record.updated_at = utc_now_iso()
            record.events.append(JobStepEvent(at=record.updated_at, step=step, payload=payload))
            if step in {
                "llm_stream_start",
                "llm_stream_restart",
                "llm_stream_end",
                "llm_stream_error",
                "llm_stream_validation",
            }:
                stream_id = str(payload.get("stream_id") or "")
                if stream_id:
                    previous = record.stream_results.pop(stream_id, None)
                    snapshot = {**(previous.payload if previous else {}), **payload}
                    if snapshot.get("validation_status") == "validated":
                        snapshot.pop("error", None)
                        snapshot.pop("error_type", None)
                    record.stream_results[stream_id] = JobStepEvent(
                        at=record.updated_at, step=step, payload=snapshot
                    )
                    while len(record.stream_results) > _MAX_STREAM_RESULTS:
                        # Quiet/slow siblings may have no deltas for a while.
                        # Evict an old settled attempt before its running peer.
                        evict = next(
                            (
                                key
                                for key, event in record.stream_results.items()
                                if event.step not in {"llm_stream_start", "llm_stream_restart"}
                                and event.payload.get("validation_status")
                                not in {"validating", "repairing"}
                            ),
                            next(iter(record.stream_results)),
                        )
                        del record.stream_results[evict]
            record.events = compact_progress_event_history(record.events, limit=_MAX_JOB_EVENTS)
            if step in {"run_log_started", "model_call_update"}:
                run_log_dir = str(payload.get("run_log_dir") or "").strip()
                if run_log_dir:
                    result_payload = dict(record.result)
                    result_payload["run_log_dir"] = run_log_dir
                    record.result = result_payload
            call_id = str(payload.get("call_id") or "").strip()
            duplicate_call = bool(
                call_id
                and any(
                    event.step == "model_call_update"
                    and event is not record.events[-1]
                    and str(event.payload.get("call_id") or "").strip() == call_id
                    and str(event.payload.get("status") or "").strip().lower() == "success"
                    for event in record.events
                )
            )
            new_model_success = (
                step == "model_call_update"
                and str(payload.get("status") or "").strip().lower() == "success"
                and not duplicate_call
            )
            has_usage_update = new_model_success or "tokens_so_far" in payload
            if has_usage_update:
                record.cumulative_tokens, record.cumulative_cost_usd = apply_live_usage_payload(
                    record.cumulative_tokens,
                    record.cumulative_cost_usd,
                    payload,
                    is_model_call=new_model_success,
                )
                token_payload = {
                    "project_id": record.project_id,
                    "cumulative_tokens": record.cumulative_tokens,
                    "cumulative_cost_usd": record.cumulative_cost_usd,
                }
        self._publish(job_id, JobEventType.JOB_STEP, step=step, payload=payload)
        self._shadow.on_step(job_id, step)
        if token_payload is not None:
            self._publish(job_id, JobEventType.TOKEN_UPDATE, payload=token_payload)

    def _handle_decision_required(self, job_id: str, payload: object) -> None:
        event_payload = payload if isinstance(payload, dict) else {"payload": payload}
        with self._lock:
            record = self._jobs.get(job_id)
            if record is not None:
                record.pending_decision = event_payload
        self._persist_terminal_job(job_id, include_running=True)
        self._publish(job_id, JobEventType.DECISION_REQUIRED, payload=event_payload)

    def _handle_finished(self, job_id: str, result: dict[str, Any]) -> None:
        payload = result if isinstance(result, dict) else {}
        project_id = str(payload.get("project_id", "")).strip()
        status = str(payload.get("status", "")).strip()
        raw_checkpoint = payload.get("checkpoint")
        checkpoint: dict[str, Any] = raw_checkpoint if isinstance(raw_checkpoint, dict) else {}
        if status == "needs_decision":
            with self._lock:
                record = self._jobs.get(job_id)
                if record is None:
                    return
                kind = record.kind
                record.status = JobState.PAUSED
                record.current_step = str(
                    checkpoint.get("checkpoint_type", "guard_checkpoint") or "guard_checkpoint"
                )
                record.error = ""
                if record.scope == JobScope.PROJECT:
                    record.project_id = project_id or record.project_id
                record.result = payload
                record.updated_at = utc_now_iso()
                self._workers.pop(job_id, None)
            self._persist_terminal_job(job_id)
            if record.scope == JobScope.PROJECT:
                self._shadow.on_paused(job_id)
            self._publish(job_id, JobEventType.JOB_PAUSED, payload=payload)
            self._publish_section_change(job_id, project_id or record.project_id, kind)
            snapshot = self.get(job_id)
            if snapshot is not None:
                self._book_autorun.on_job_paused(snapshot)
            self._maybe_start_pending_jobs()
            return
        with self._lock:
            record = self._jobs.get(job_id)
            if record is None or record.status == JobState.FAILED:
                return
            kind = record.kind
            record.status = JobState.SUCCEEDED
            record.current_step = "completed"
            record.error = ""
            if record.scope == JobScope.PROJECT:
                record.project_id = project_id or record.project_id
            record.result = payload
            record.updated_at = utc_now_iso()
            self._workers.pop(job_id, None)
        self._persist_terminal_job(job_id)
        if record.scope == JobScope.PROJECT:
            self._shadow.on_succeeded(job_id)
        self._publish(job_id, JobEventType.JOB_SUCCEEDED, payload=payload)
        self._publish_section_change(job_id, project_id or record.project_id, kind)
        snapshot = self.get(job_id)
        if snapshot is not None:
            self._book_autorun.on_job_succeeded(snapshot)
            if _kind_value(snapshot.kind) == "planning_horizon":
                self._book_autorun.on_planning_finished(snapshot)
        self._maybe_start_pending_jobs()

    def _handle_failed(self, job_id: str, error: Exception) -> None:
        summary = _error_payload(error)
        title = str(summary.get("title") or "").strip()
        text = str(summary.get("summary") or title or error or "任务失败")
        failed_prompt_task = prompt_task_from_error_payload(summary)
        with self._lock:
            record = self._jobs.get(job_id)
            if record is None or record.status == JobState.FAILED:
                return
            record.status = JobState.FAILED
            record.current_step = failed_prompt_task or record.current_step or "failed"
            record.error = text
            record.error_summary = summary
            record.updated_at = utc_now_iso()
            self._workers.pop(job_id, None)
        self._persist_terminal_job(job_id)
        if record.scope == JobScope.PROJECT:
            self._shadow.on_failed(job_id, _classify_error_kind(error), summary)
        self._publish(job_id, JobEventType.JOB_FAILED, payload=summary)
        self._publish(job_id, JobEventType.SECTION_CHANGED, payload={"section": "jobs"})
        snapshot = self.get(job_id)
        if snapshot is not None:
            self._book_autorun.on_job_failed(snapshot)
            if _kind_value(snapshot.kind) == "planning_horizon":
                self._book_autorun.on_planning_finished(snapshot)
        self._maybe_start_pending_jobs()

    def _handle_cancelled_worker_exit(self, job_id: str) -> None:
        with self._lock:
            self._workers.pop(job_id, None)
        self._maybe_start_pending_jobs()

    def authoring_activity(self, project_id: str) -> tuple[str, str]:
        """A cancelled record is not proof its worker has safely left."""
        with self._lock:
            for job_id in self._stopping_workers:
                if self._jobs[job_id].project_id == project_id:
                    return "stopping", job_id
            for job_id, worker in self._workers.items():
                if worker.record.project_id == project_id:
                    return "running", job_id
            for job_id, record in self._jobs.items():
                if record.project_id == project_id and record.status in {
                    JobState.QUEUED,
                    JobState.RUNNING,
                }:
                    return "running", job_id
        return "idle", ""

    def require_authoring_dispatch_enabled(self, record: JobRecord) -> None:
        if (
            self._storage_root is None
            or not record.project_id
            or _kind_value(record.kind) in _TTS_JOB_KINDS
        ):
            return
        from novel_forge.persistence.authoring_store import AuthoringStore

        AuthoringStore(self._storage_root / record.project_id).require_enabled()

    def pause_authoring(self, project_id: str, *, disable: bool = False) -> None:
        """Durable barrier first; queued and in-flight tasks use the same service."""
        if self._storage_root is None:
            raise ValueError("缺少作品存储目录")
        from novel_forge.persistence.authoring_store import AuthoringStore
        from novel_forge.persistence.filesystem import FileSystemStorage

        root = FileSystemStorage(self._storage_root).existing_project_dir(project_id)
        store = AuthoringStore(root)
        try:
            if disable:
                store.disable()
            else:
                store.stop()
        finally:
            self.pause_book_autorun(project_id)
            for record in self.list(project_id=project_id):
                if (
                    record.status in {JobState.QUEUED, JobState.RUNNING}
                    and _kind_value(record.kind) not in _TTS_JOB_KINDS
                ):
                    self.cancel(record.job_id, reason="作者已暂停/停用共创；保留候选与费用")

    def enable_authoring(
        self, project_id: str, *, expected_version: int, input_version: str
    ) -> None:
        if self._storage_root is None:
            raise ValueError("缺少作品存储目录")
        from novel_forge.persistence.authoring_store import AuthoringStore
        from novel_forge.persistence.filesystem import FileSystemStorage

        root = FileSystemStorage(self._storage_root).existing_project_dir(project_id)
        with self._lock:
            if any(
                worker.record.project_id == project_id for worker in self._stopping_workers.values()
            ) or any(
                record.project_id == project_id
                and record.status in {JobState.QUEUED, JobState.RUNNING}
                for record in self._jobs.values()
            ):
                raise ValueError("仍在安全停止；请等待任务退出后重新启用")
            AuthoringStore(root).enable(
                expected_version=expected_version, input_version=input_version
            )
