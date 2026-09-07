"""One authoritative post-archive dispatch for automatic chapter dubbing."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from novel_forge.core.config import Settings
from novel_forge.core.utils.text_hash import source_text_hash
from novel_forge.obs.logger import get_logger
from novel_forge.persistence.models import ProjectLayout
from novel_forge.tts.schemas import ChapterAudioResult
from novel_forge.tts.services.automation import resolve_audio_automation_mode
from novel_forge.tts.services.delivery import TTSDeliveryNotReadyError, require_delivery_ready
from novel_forge.workspace.execution_result import StepCallback

_log = get_logger("workspace.post_archive_tts")

# Reasons that justify a single delayed auto-retry: they typically resolve on
# their own within seconds as the author finishes approving a voice.  Must stay
# in sync with RETRYABLE_REASONS in novel_forge/workspace/tts_ops/execution.py.
_RETRYABLE_REASONS = frozenset({
    "pending_approval",
    "expired",
    "narrator_unavailable",
})


def _emit(
    callback: StepCallback,
    step: str,
    payload: dict[str, Any],
) -> None:
    """Deliver optional progress without changing a completed chapter outcome."""

    if callback is None:
        return
    try:
        callback(step, payload)
    except Exception:
        _log.debug("post-archive TTS progress callback failed for %s", step, exc_info=True)


def _write_record(
    storage: Any, layout: ProjectLayout, chapter_number: int, record: dict[str, Any]
) -> None:
    save_json = getattr(storage, "save_json", None)
    if not callable(save_json):
        return
    try:
        save_json(layout.tts_auto_run_path(chapter_number), record)
    except Exception:
        # The dispatch record is observability data.  A filesystem error here
        # must not turn an already durable chapter into a failed chapter run.
        _log.exception(
            "post_archive_tts_record_write_failed | chapter=%d",
            chapter_number,
        )


def queue_post_archive_tts(
    *,
    settings: Settings,
    storage: Any,
    project_id: str,
    chapter_number: int,
    parent_job_id: str = "",
    on_step_progress: StepCallback = None,
) -> dict[str, Any] | None:
    """Persist the queue handoff before a background audio worker starts."""

    if not (settings.tts_enabled and settings.tts_auto_trigger_after_chapter):
        return None
    project_dir = getattr(storage, "existing_project_dir", None)
    if not callable(project_dir):
        return None
    try:
        layout = ProjectLayout(project_dir(project_id))
        chapter_text = layout.chapter_path(chapter_number).read_text(encoding="utf-8")
    except (OSError, ValueError):
        return None
    if not chapter_text.strip():
        return None
    now = datetime.now(timezone.utc).isoformat()
    automation_mode = resolve_audio_automation_mode(None, settings=settings)
    record: dict[str, Any] = {
        "schema_version": "1.2",
        "chapter_number": chapter_number,
        "parent_job_id": parent_job_id,
        "trigger": "post_archive",
        "status": "queued",
        "provider": str(settings.tts_default_provider or ""),
        "automation_mode": automation_mode.value,
        "source_text_hash": source_text_hash(chapter_text),
        "assembled_audio_path": "",
        "completed_segments": 0,
        "total_segments": 0,
        "error": "",
        # WP1: structured per-character failure reasons surfaced from
        # execute_full_tts_pipeline so the UI can tell the author which voice
        # to confirm/expire/switch instead of one opaque string.
        "diagnoses": [],
        # WP3: single delayed-retry bookkeeping.  retry_count is inherited
        # across queue->run transitions so a second failure stays final.
        "retry_count": 0,
        "next_retry_at": "",
        "queued_at": now,
        "started_at": "",
        "finished_at": "",
    }
    _write_record(storage, layout, chapter_number, record)
    _emit(
        on_step_progress,
        "tts_auto_trigger_queued",
        {
            "chapter": chapter_number,
            "parent_job_id": parent_job_id,
            "automation_mode": automation_mode.value,
        },
    )
    return record


def _audio_path_is_deliverable(layout: ProjectLayout, value: Any) -> bool:
    raw_path = str(value or "").strip()
    if not raw_path:
        return False
    path = Path(raw_path)
    if not path.is_absolute():
        path = layout.root / path
    try:
        return path.is_file() and path.stat().st_size >= 128
    except OSError:
        return False


def _segment_counts(payload: dict[str, Any]) -> tuple[int, int]:
    results = payload.get("segment_results")
    if not isinstance(results, list):
        return 0, 0
    completed = sum(
        1
        for item in results
        if isinstance(item, dict) and str(item.get("status") or "").lower() == "completed"
    )
    return completed, len(results)


async def run_post_archive_tts(
    runtime: Any,
    *,
    project_id: str,
    chapter_number: int,
    on_step_progress: StepCallback = None,
) -> dict[str, Any] | None:
    """Run automatic TTS from the persisted final chapter, never transient text.

    This dispatcher is deliberately shared by direct chapter runs and the
    Desktop checkpoint-finalization path.  A TTS failure never rolls back a
    successfully archived chapter, but it is persisted and reported as a
    resumable ``partial`` or ``failed`` TTS outcome rather than a false
    completion.
    """

    settings = getattr(runtime, "settings", None)
    if not isinstance(settings, Settings):
        return None
    if not (settings.tts_enabled and settings.tts_auto_trigger_after_chapter):
        return None

    storage = getattr(runtime, "storage", None)
    project_dir = getattr(storage, "existing_project_dir", None)
    if not callable(project_dir):
        return None
    try:
        layout = ProjectLayout(project_dir(project_id))
    except Exception:
        _log.exception("post_archive_tts_project_lookup_failed | project=%s", project_id)
        _emit(
            on_step_progress,
            "tts_auto_trigger_skipped",
            {"chapter": chapter_number, "reason": "project_unavailable"},
        )
        return None
    chapter_path = layout.chapter_path(chapter_number)
    try:
        chapter_text = chapter_path.read_text(encoding="utf-8")
    except OSError as exc:
        _emit(
            on_step_progress,
            "tts_auto_trigger_skipped",
            {"chapter": chapter_number, "reason": "final_chapter_unavailable", "error": str(exc)},
        )
        return None
    if not chapter_text.strip():
        _emit(
            on_step_progress,
            "tts_auto_trigger_skipped",
            {"chapter": chapter_number, "reason": "final_chapter_empty"},
        )
        return None

    started_at = datetime.now(timezone.utc)
    automation_mode = resolve_audio_automation_mode(None, settings=settings)

    # ── Preflight: avoid launching a doomed job when the voice team is not
    # ready for reuse.  The pipeline's ``allow_voice_team_rebuild=False`` path
    # already returns structured ``diagnoses``, but only *after* the job has
    # been submitted and started.  Running the same readiness check here lets
    # us short-circuit with a matching ``status=failed`` record before the
    # ``running`` record is written, saving a wasted worker slot and making the
    # structured diagnoses available in the parent job's followup result
    # without needing a separate task-flow entry.
    try:
        from novel_forge.workspace.tts_ops.execution import (
            _load_tts_upstream_context,
            _merge_character_inputs,
        )
        from novel_forge.workspace.tts_ops.readiness import diagnose_voice_team_readiness

        provider_str = str(settings.tts_default_provider or "").strip().lower()
        # Preflight is unnecessary for mock / disabled providers; they never
        # need a real voice team and the readiness check would always fail.
        if provider_str not in ("", "mock"):
            upstream = _load_tts_upstream_context(layout, chapter_number)
            enriched_characters = _merge_character_inputs(
                [], upstream["characters"], upstream["character_voices"]
            )
            diagnosis = diagnose_voice_team_readiness(
                layout=layout,
                settings=settings,
                provider=provider_str,
                enriched_characters=enriched_characters,
            )
            if not diagnosis.ready:
                now = datetime.now(timezone.utc).isoformat()
                preflight_record: dict[str, Any] = {
                    "schema_version": "1.2",
                    "chapter_number": chapter_number,
                    "trigger": "post_archive",
                    "status": "failed",
                    "provider": provider_str,
                    "automation_mode": automation_mode.value,
                    "source_text_hash": source_text_hash(chapter_text),
                    "assembled_audio_path": "",
                    "completed_segments": 0,
                    "total_segments": 0,
                    "error": diagnosis.error,
                    "error_code": diagnosis.error_code,
                    "missing_artifact": diagnosis.missing_artifact,
                    "diagnoses": list(diagnosis.diagnoses),
                    "confirmable_character_ids": list(diagnosis.confirmable_character_ids),
                    "queued_at": now,
                    "started_at": now,
                    "finished_at": now,
                    "retry_count": 0,
                    "next_retry_at": "",
                }
                _write_record(storage, layout, chapter_number, preflight_record)
                _emit(
                    on_step_progress,
                    "tts_auto_trigger_failed",
                    {
                        "chapter": chapter_number,
                        "error": diagnosis.error,
                        "error_code": diagnosis.error_code,
                        "missing_artifact": diagnosis.missing_artifact,
                        "diagnoses": list(diagnosis.diagnoses),
                        "automation_mode": automation_mode.value,
                    },
                )
                return preflight_record
    except Exception:
        # Preflight is best-effort: a failure here (e.g. corrupted upstream
        # artifacts) must not prevent the pipeline from running, because the
        # pipeline itself will produce a matching structured failure.
        _log.exception(
            "post_archive_tts_preflight_failed | project=%s chapter=%d",
            project_id,
            chapter_number,
        )

    queued_record: dict[str, Any] = {}
    load_json = getattr(storage, "load_json", None)
    if callable(load_json):
        try:
            candidate = load_json(layout.tts_auto_run_path(chapter_number))
            # Adopt "queued" records (normal hand-off) and "retry_scheduled"
            # records (WP3 in-process retry re-entry) so ``retry_count`` is
            # inherited and the second failure stays terminal.
            if (
                isinstance(candidate, dict)
                and candidate.get("status") in ("queued", "retry_scheduled")
                and candidate.get("source_text_hash") == source_text_hash(chapter_text)
            ):
                queued_record = dict(candidate)
            # R5 fix: concurrency guard.  If another worker is already running
            # TTS for this chapter (status="running" started within the last
            # 5 minutes), skip this invocation to avoid duplicate synthesis,
            # checkpoint corruption, and wasted provider quota.  The 5-minute
            # window tolerates clock skew and slow startups; a genuinely stuck
            # record will be superseded after the window expires.
            elif (
                isinstance(candidate, dict)
                and candidate.get("status") == "running"
                and candidate.get("source_text_hash") == source_text_hash(chapter_text)
            ):
                started_at_str = str(candidate.get("started_at") or "")
                if started_at_str:
                    try:
                        started_at_dt = datetime.fromisoformat(started_at_str)
                        age_s = (datetime.now(timezone.utc) - started_at_dt).total_seconds()
                        if 0 <= age_s < 300:  # 5 minutes
                            _log.info(
                                "post_archive_tts_skipped_concurrent | project=%s chapter=%d age_s=%.0f",
                                project_id,
                                chapter_number,
                                age_s,
                            )
                            _emit(
                                on_step_progress,
                                "tts_auto_trigger_skipped",
                                {
                                    "chapter": chapter_number,
                                    "reason": "concurrent_run_in_progress",
                                    "started_at": started_at_str,
                                },
                            )
                            return dict(candidate)
                    except (ValueError, TypeError):
                        pass  # unparseable timestamp → treat as stale
        except Exception:
            queued_record = {}
    record: dict[str, Any] = {
        **queued_record,
        "schema_version": "1.2",
        "chapter_number": chapter_number,
        "trigger": "post_archive",
        "status": "running",
        "provider": str(settings.tts_default_provider or ""),
        "automation_mode": automation_mode.value,
        "source_text_hash": source_text_hash(chapter_text),
        "assembled_audio_path": "",
        "completed_segments": 0,
        "total_segments": 0,
        "error": "",
        # Inherit queued diagnoses/retry_count if present; default to empty/0
        # for records written by older queue_post_archive_tts (schema 1.1).
        "diagnoses": list(queued_record.get("diagnoses") or []),
        "retry_count": int(queued_record.get("retry_count") or 0),
        "next_retry_at": str(queued_record.get("next_retry_at") or ""),
        "queued_at": str(queued_record.get("queued_at") or ""),
        "started_at": started_at.isoformat(),
        "finished_at": "",
    }
    _write_record(storage, layout, chapter_number, record)
    _emit(
        on_step_progress,
        "tts_auto_trigger_started",
        {"chapter": chapter_number, "automation_mode": automation_mode.value},
    )

    try:
        from novel_forge.workspace.tts_ops.execution import execute_full_tts_pipeline

        execution = await execute_full_tts_pipeline(
            project_id=project_id,
            chapter_number=chapter_number,
            chapter_text=chapter_text,
            characters=[],
            settings=settings,
            layout=layout,
            provider=str(settings.tts_default_provider or ""),
            automation_mode=automation_mode,
            # Chapter archival is a background operation: it may use the
            # author's confirmed voices, never create or replace them.
            allow_voice_team_rebuild=False,
            on_step_progress=on_step_progress,
        )
        payload = execution.result if isinstance(execution.result, dict) else {}
    except Exception as exc:  # TTS must not invalidate an already archived chapter.
        payload = {"error": str(exc)}
        _log.exception(
            "post_archive_tts_failed | project=%s chapter=%d",
            project_id,
            chapter_number,
        )

    completed_segments, total_segments = _segment_counts(payload)
    audio_path = str(payload.get("assembled_audio_path") or "")
    delivery_ready = bool(payload.get("delivery_ready"))
    blocking_reasons = list(payload.get("delivery_blocking_reasons") or [])
    record.update(
        {
            "assembled_audio_path": audio_path,
            "completed_segments": completed_segments,
            "total_segments": total_segments,
            "delivery_ready": delivery_ready,
            "delivery_blocking_reasons": blocking_reasons,
            "finished_at": datetime.now(timezone.utc).isoformat(),
        }
    )
    error = str(payload.get("error") or "").strip()
    if error:
        diagnoses = list(payload.get("diagnoses") or [])
        error_code = str(payload.get("error_code") or "")
        missing_artifact = str(payload.get("missing_artifact") or "")
        confirmable_ids = list(payload.get("confirmable_character_ids") or [])
        record.update({
            "status": "failed",
            "error": error,
            "error_code": error_code,
            "missing_artifact": missing_artifact,
            "diagnoses": diagnoses,
            "confirmable_character_ids": confirmable_ids,
            "finished_at": datetime.now(timezone.utc).isoformat(),
        })
        # WP3: schedule one delayed retry when the failure is recoverable
        # (author just hasn't clicked "approve" yet, or a designed voice's
        # activation deadline slipped).  Reasons like "missing" or
        # "provider_mismatch" never self-resolve and stay final.  The retry
        # counter is inherited from queued_record so a second failure stays
        # terminal without bespoke state.
        retry_count = int(record.get("retry_count") or 0)
        retry_enabled = bool(getattr(settings, "tts_post_archive_retry_enabled", True))
        retry_delay_s = int(getattr(settings, "tts_post_archive_retry_delay_s", 60))
        retryable = any(
            isinstance(d, dict) and d.get("reason") in _RETRYABLE_REASONS
            for d in diagnoses
        )
        if retry_enabled and retry_count == 0 and retryable:
            retry_at = datetime.now(timezone.utc) + timedelta(seconds=retry_delay_s)
            record.update({
                "status": "retry_scheduled",
                "retry_count": retry_count + 1,
                "next_retry_at": retry_at.isoformat(),
            })
            _write_record(storage, layout, chapter_number, record)
            _emit(
                on_step_progress,
                "tts_auto_trigger_retry_scheduled",
                {
                    "chapter": chapter_number,
                    "delay_s": retry_delay_s,
                    "retry_count": retry_count + 1,
                    "error": error,
                },
            )
            # The retry is performed in-process: no external scheduler reads
            # ``next_retry_at`` (it is diagnostic only).  Sleeping inside this
            # job keeps the single-retry semantics self-contained; the
            # recursive call inherits ``retry_count=1`` from the record file
            # (status "retry_scheduled" is accepted below), so a second
            # failure stays terminal and is returned as ``status=failed``.
            #
            # R4 trade-off note: ``asyncio.sleep(retry_delay_s)`` occupies
            # the worker slot for up to 60 s (default).  This is acceptable
            # because (a) post-archive TTS is a low-priority background job,
            # (b) the job manager's ``max_concurrent_jobs`` is typically ≥ 2,
            # and (c) migrating to a QTimer / job-manager-level scheduler
            # would require a new persistent timer registry and complicate
            # the single-retry guarantee.  If slot pressure becomes an issue,
            # consider reducing ``tts_post_archive_retry_delay_s`` or moving
            # the retry dispatch to ``job_service._maybe_start_pending_jobs``.
            #
            # R1 fix: if the job is cancelled during the sleep (e.g. user
            # closes the app or the job manager shuts down), we must not
            # leave the record in "retry_scheduled" limbo.  Catch
            # CancelledError and mark the record as "cancelled" so the
            # voice-studio UI can surface the interruption and the author
            # can manually retry.
            try:
                await asyncio.sleep(retry_delay_s)
            except asyncio.CancelledError:
                record.update({
                    "status": "cancelled",
                    "error": "自动配音重试等待期间任务被取消",
                    "finished_at": datetime.now(timezone.utc).isoformat(),
                })
                _write_record(storage, layout, chapter_number, record)
                _emit(
                    on_step_progress,
                    "tts_auto_trigger_failed",
                    {
                        "chapter": chapter_number,
                        "error": "自动配音重试等待期间任务被取消",
                        "error_type": "CancelledError",
                    },
                )
                raise
            return await run_post_archive_tts(
                runtime,
                project_id=project_id,
                chapter_number=chapter_number,
                on_step_progress=on_step_progress,
            )
        _write_record(storage, layout, chapter_number, record)
        _emit(
            on_step_progress,
            "tts_auto_trigger_failed",
            {
                "chapter": chapter_number,
                "error": error,
                "error_code": error_code,
                "missing_artifact": missing_artifact,
                "diagnoses": diagnoses,
                "error_type": "TTSExecutionError",
            },
        )
        return record

    try:
        require_delivery_ready(ChapterAudioResult.model_validate(payload))
    except (ValueError, TTSDeliveryNotReadyError) as exc:
        reasons = list(getattr(exc, "reasons", ()) or blocking_reasons)
    else:
        reasons = []
    path_is_deliverable = _audio_path_is_deliverable(layout, audio_path)
    if not path_is_deliverable and "missing_assembled_audio" not in reasons:
        reasons.append("audio_path_outside_project")
    if not reasons and path_is_deliverable:
        record["status"] = "completed"
        _write_record(storage, layout, chapter_number, record)
        _emit(
            on_step_progress,
            "tts_auto_trigger_completed",
            {"chapter": chapter_number, "is_complete": True, "audio_path": audio_path},
        )
        return record

    record.update(
        {
            "status": "partial",
            "error": ("TTS 尚未达到最终交付条件；已保留分段进度，可在配音工作室续跑。"),
            "delivery_blocking_reasons": reasons,
        }
    )
    _write_record(storage, layout, chapter_number, record)
    _emit(
        on_step_progress,
        "tts_auto_trigger_partial",
        {
            "chapter": chapter_number,
            "is_complete": bool(payload.get("is_complete")),
            "delivery_ready": False,
            "delivery_blocking_reasons": reasons,
            "completed_segments": completed_segments,
            "total_segments": total_segments,
            "audio_path": audio_path,
        },
    )
    return record


class TTSPostArchiveError(RuntimeError):
    """Structured wrapper for post-archive TTS failures.

    Carries the rich ``error_code`` / ``missing_artifact`` / ``diagnoses``
    that ``execute_full_tts_pipeline`` produces but that a bare
    ``RuntimeError`` would discard.  ``_run_tts_post_archive_command``
    raises this instead of ``RuntimeError`` so ``summarize_desktop_error``
    and ``_handle_failed`` can preserve structured diagnostic fields for
    task-flow panels and voice-studio integration.

    ``error_summary`` is an optional pre-built dict for UI rendering.
    ``job_service._error_payload`` reads this attribute via ``getattr``
    and merges it into the job failure payload.
    """

    def __init__(
        self,
        *,
        message: str,
        error_code: str = "",
        missing_artifact: str = "",
        diagnoses: list[dict[str, str]] | None = None,
        confirmable_character_ids: list[str] | None = None,
        error_summary: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.missing_artifact = missing_artifact
        self.diagnoses: list[dict[str, str]] = list(diagnoses or [])
        self.confirmable_character_ids: list[str] = list(confirmable_character_ids or [])
        self.error_summary: dict[str, Any] = dict(error_summary or {})
