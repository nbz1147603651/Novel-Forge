"""Job cards and mode selectors for the workflow page."""

from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from PySide6.QtCore import Property, QEasingCurve, QPropertyAnimation, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFontMetrics, QPainter, QPaintEvent, QPixmap, QResizeEvent
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QStyle,
    QStyleOptionFrame,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from novel_forge.app_service.workflow_projection import (
    project_run_transparency,
    visible_workflow_steps,
)
from novel_forge.desktop.components.primitives import ActionButton
from novel_forge.desktop.constants import animations_supported
from novel_forge.desktop.jobs import DesktopJobRecord, DesktopJobState
from novel_forge.desktop.pages.standalone.init_manual_repair_dialog import (
    init_manual_repair_available,
)
from novel_forge.desktop.pages.workflow.artifacts import (
    StepArtifactDialog,
    _compute_progress,
    _parallel_pairs_for_kind,
    _resolve_step_key,
    _steps_for_kind,
    step_has_artifacts,
)
from novel_forge.desktop.progress import (
    _get_phase_index,
    _is_init_long_outline_step,
    _last_non_transient_step,
    _phase_step_for_event,
    display_step_name,
    display_step_name_for_job,
    format_init_long_outline_step_label,
    init_long_resume_anchor_step,
    memory_stage_status_for_job,
    resolve_checkpoint_resume_step,
)
from novel_forge.desktop.task_flow import (
    task_flow_failure_text,
    task_flow_is_cancelled,
    task_flow_status_spec,
)
from novel_forge.desktop.theme import resolve_qcolor
from novel_forge.desktop.tokens.radius import SURFACE_RADIUS
from novel_forge.desktop.widgets import (
    Badge,
    PipelineStep,
    StepDotState,
    StepIndicatorRow,
    StepIndicatorState,
    Surface,
    clear_layout,
)
from novel_forge.desktop.workflow_requests import summarize_job_result
from novel_forge.pipeline.progress import compute_progress_percent, is_non_progress_step_event


def _result_warnings(payload: dict[str, object] | None) -> list[str]:
    if not isinstance(payload, dict):
        return []
    warnings = payload.get("warnings")
    if not isinstance(warnings, list):
        metadata = payload.get("metadata")
        warnings = metadata.get("warnings") if isinstance(metadata, dict) else []
    return [str(item).strip() for item in (warnings or []) if str(item).strip()]


def _fmt_local(ts: str) -> str:
    """Parse a UTC ISO timestamp and return it formatted in local time."""
    try:
        dt = datetime.fromisoformat(ts)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone().strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return ts[:19].replace("T", " ")


def _truncate_message(message: str, limit: int = 180) -> str:
    text = " ".join(str(message or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


_LLM_TASK_DISPLAY_NAMES: dict[str, str] = {
    "plan_outline": "叙事蓝图",
    "plan_outline_batch": "章节大纲（首批）",
    "plan_outline_continue": "章节大纲（续写）",
}


def _llm_task_display_name(raw_task: object) -> str:
    """Return a display label for LLM TaskType values, not workflow step keys."""
    task = str(raw_task or "").strip()
    if not task:
        return ""
    return _LLM_TASK_DISPLAY_NAMES.get(task, display_step_name(task))


def _is_cancelled_job(job: DesktopJobRecord) -> bool:
    """Detect if a FAILED job was actually cancelled (user or auto-pilot)."""
    return task_flow_is_cancelled(job)


# These events update the task log or accounting metadata, but do not change
# anything that is rendered inside a JobCard.  Keeping them out of the card
# render signature prevents a token/streaming burst from rebuilding the whole
# card layout on every event.  The job manager still publishes the events and
# the task observation store still receives them; this only narrows the
# expensive visual invalidation boundary.
_CARD_NON_RENDER_EVENTS = frozenset(
    {
        "run_log_started",
        "model_call_update",
        "token_escalation",
        "token_budget_normalized",
        "format_validation_success",
        "preflight_token_estimate",
        "memory_gap_compensated",
        "memory_update_failed",
        "budget_status",
        "style_drift_warning",
        "audit_result_update",
        "llm_stream_start",
        "llm_stream_delta",
        "llm_stream_restart",
        "llm_stream_end",
        "llm_stream_error",
        "human_decision_requested",
        "human_decision_resolved",
        "human_decision_timeout",
        "repair_repeated_issue_guard",
        "repair_strategy_diagnosis",
        "repair_round_focus",
        "prompt_pressure",
    }
)


def _last_card_render_event_signature(job: DesktopJobRecord) -> tuple[str, str]:
    """Return the latest event that can change visible card content.

    ``updated_at`` and the raw event count change for almost every streaming
    callback.  Neither is visible in the card, so using them as render keys
    forces repeated layout rebuilds while a model is producing tokens.  A
    timestamp is retained for the relevant event so two visible events with
    the same step still invalidate the card deterministically.
    """
    for event in reversed(job.events):
        step = str(event.step or "")
        if step in _CARD_NON_RENDER_EVENTS:
            continue
        return step, str(event.at or "")
    return "", ""


_CHAPTER_DECISION_JOB_KINDS = frozenset(
    {
        "prepare_chapter",
        "resolve_chapter_checkpoint",
        "resolve_chapter_checkpoint_finalize",
        "run_chapter",
    }
)


def _job_status_key(job: DesktopJobRecord) -> str:
    return job.status.value if isinstance(job.status, DesktopJobState) else str(job.status)


def _job_result(job: DesktopJobRecord) -> dict[str, Any]:
    return job.result if isinstance(job.result, dict) else {}


def _job_result_status(job: DesktopJobRecord) -> str:
    return str(_job_result(job).get("status", "") or "").strip().lower()


def _checkpoint_payload(job: DesktopJobRecord) -> dict[str, Any]:
    checkpoint = _job_result(job).get("checkpoint")
    return checkpoint if isinstance(checkpoint, dict) else {}


def _checkpoint_type(job: DesktopJobRecord) -> str:
    checkpoint_type = str(_checkpoint_payload(job).get("checkpoint_type", "") or "").strip()
    if checkpoint_type in {"plan_checkpoint", "guard_checkpoint"}:
        return checkpoint_type
    current_step = str(job.current_step or "").strip()
    if current_step in {"plan_checkpoint", "guard_checkpoint"}:
        return current_step
    for event in reversed(job.events):
        step = str(event.step or "").strip()
        if step in {"plan_checkpoint", "guard_checkpoint"}:
            return step
    return "guard_checkpoint"


def _is_checkpoint_decision_result(job: DesktopJobRecord) -> bool:
    return job.kind in _CHAPTER_DECISION_JOB_KINDS and _job_result_status(job) == "needs_decision"


def _checkpoint_decision_step(job: DesktopJobRecord) -> str:
    return _checkpoint_type(job)


def _is_replanned_plan_checkpoint(job: DesktopJobRecord) -> bool:
    if _checkpoint_type(job) != "plan_checkpoint":
        return False
    if any(str(event.step or "") == "consistency_replan" for event in job.events):
        return True
    label = str(job.label or "")
    if "方案重生" in label or "重新规划" in label:
        return True
    prompt = str(_checkpoint_payload(job).get("prompt", "") or "")
    return "已自动重新规划" in prompt or "重新规划" in prompt


def _checkpoint_status_badge_spec(job: DesktopJobRecord) -> tuple[str, str]:
    checkpoint_type = _checkpoint_type(job)
    if job.status == DesktopJobState.PAUSED:
        if checkpoint_type == "plan_checkpoint":
            return ("等待方案确认", "warning")
        return ("等待归档选择", "warning")
    if job.status == DesktopJobState.SUCCEEDED:
        if checkpoint_type == "plan_checkpoint":
            if _is_replanned_plan_checkpoint(job):
                return ("已重新规划", "muted")
            return ("方案已生成", "muted")
        return ("决策已确认", "muted")
    if checkpoint_type == "plan_checkpoint":
        return ("方案待确认", "warning")
    return ("归档待确认", "warning")


def _job_badge_spec(job: DesktopJobRecord) -> tuple[str, str]:
    if _is_cancelled_job(job):
        status = task_flow_status_spec(job)
        return (status.label, status.tone)
    if _is_checkpoint_decision_result(job):
        return _checkpoint_status_badge_spec(job)
    if job.kind in {"tts_synthesize", "tts_full_pipeline", "tts_post_archive"}:
        result_status = _job_result_status(job)
        if result_status == "partial":
            return ("可续跑", "warning")
        if result_status == "skipped":
            return ("已跳过", "muted")
    status = task_flow_status_spec(job)
    return (status.label, status.tone)


def _checkpoint_decision_detail(job: DesktopJobRecord) -> str:
    if not _is_checkpoint_decision_result(job):
        return ""
    checkpoint_type = _checkpoint_type(job)
    if checkpoint_type == "plan_checkpoint":
        plan_kind = "重规划方案" if _is_replanned_plan_checkpoint(job) else "章节方案"
        if job.status == DesktopJobState.PAUSED:
            return f"业务结果：已生成{plan_kind}，等待确认后再写正文。"
        if job.status == DesktopJobState.SUCCEEDED:
            return f"业务结果：已生成{plan_kind}，等待确认后再写正文。"
        return f"业务结果：已生成{plan_kind}。"
    if job.status == DesktopJobState.PAUSED:
        return "业务结果：草稿和质量检查已完成，等待选择是否归档。"
    if job.status == DesktopJobState.SUCCEEDED:
        return "业务结果：归档选择已确认，后续归档卡会继续落盘。"
    return "业务结果：草稿等待归档决策。"


def _checkpoint_metric_summary(job: DesktopJobRecord) -> str:
    if not _is_checkpoint_decision_result(job):
        return ""
    summary = summarize_job_result(_job_result(job))
    return f"草稿指标：{summary}" if summary else ""


# Status color palette for JobCard background animation.
# Each entry maps a status key to ``(token_name, alpha)`` so the QColor is
# resolved dynamically at call time — theme switches are picked up without
# manual RGB sync.
_JOB_STATUS_TOKEN_MAP: dict[str, tuple[str, int]] = {
    "queued": ("text.muted", 34),
    "running": ("accent.primary.hover", 42),
    "succeeded": ("status.success.warm", 38),
    "failed": ("status.danger.alt", 42),
    "paused": ("text.muted", 34),
    "decision": ("accent.primary.hover", 30),
    "decision_done": ("text.muted.soft", 22),
    "cancelled": ("text.muted.soft", 22),
}


def _job_status_color(status_key: str) -> QColor:
    """Resolve the background QColor for a job-card status at call time."""
    entry = _JOB_STATUS_TOKEN_MAP.get(status_key, _JOB_STATUS_TOKEN_MAP["queued"])
    token, alpha = entry
    return resolve_qcolor(token, alpha)


def _card_color_key(job: DesktopJobRecord) -> str:
    if _is_cancelled_job(job):
        return "cancelled"
    if _is_checkpoint_decision_result(job):
        return "decision" if job.status == DesktopJobState.PAUSED else "decision_done"
    return _job_status_key(job)


def _summary_step_index(kind: str, raw_step: str, steps: Sequence[object]) -> int | None:
    resolved = _resolve_step_key(kind, raw_step)
    visible_keys = {str(getattr(step, "key", "") or "") for step in steps}
    if resolved not in visible_keys:
        resolved = _nearest_visible_step_key(kind, resolved, visible_keys)
    for index, step in enumerate(steps):
        if resolved == getattr(step, "key", ""):
            return index
    for index, step in enumerate(steps):
        key = getattr(step, "key", "")
        is_prefix = bool(getattr(step, "is_prefix", False))
        if is_prefix and str(resolved).startswith(str(key)):
            return index
    return None


def _summary_step_index_for_job(job: DesktopJobRecord, steps: Sequence[object]) -> int | None:
    visible_keys = {str(getattr(step, "key", "") or "") for step in steps}
    if _init_long_blueprint_to_outline_completed_key(job, visible_keys):
        return None
    resolved = _indicator_current_step_key(job, visible_keys, raw_step=_stable_current_step(job))
    current_index = _step_index_in_sequence(steps, resolved)
    floor_key = _resume_floor_step_key_for_job(job, visible_keys)
    floor_index = _step_index_in_sequence(steps, floor_key) if floor_key else None
    if floor_index is not None and (current_index is None or floor_index > current_index):
        return floor_index
    return current_index


def _step_index_in_sequence(steps: Sequence[object], key: str) -> int | None:
    if not key:
        return None
    for index, step in enumerate(steps):
        step_key = str(getattr(step, "key", "") or "")
        is_prefix = bool(getattr(step, "is_prefix", False))
        if key == step_key or (is_prefix and key.startswith(step_key)):
            return index
    return None


def _strictly_increasing_anchors(raw_anchors: list[int]) -> list[float]:
    anchors: list[float] = []
    previous = -1.0
    for raw in raw_anchors:
        value = float(raw)
        if value <= previous:
            value = previous + 0.001
        anchors.append(value)
        previous = value
    return anchors


def _remap_progress_to_summary_steps(
    kind: str,
    raw_progress: int,
    *,
    current_step: str = "",
    current_index: int | None = None,
) -> int | None:
    """Project semantic progress onto visible summary-step positions.

    The shared progress tables describe pipeline phase percentages, while the
    task-flow indicator renders only summary steps. This remaps the raw value
    so the bar ends near the currently highlighted summary step instead of
    overshooting it.
    """
    steps = _visible_steps_for_kind(kind)
    if len(steps) < 2:
        return None

    raw_anchors = [compute_progress_percent(kind, "running", step.key) for step in steps]
    step_count = len(steps)
    visual_anchors = [round(((index + 0.5) / step_count) * 100) for index in range(step_count)]
    if current_index is None and current_step:
        current_index = _summary_step_index(kind, current_step, steps)
    if current_index is not None and raw_progress == raw_anchors[current_index]:
        return visual_anchors[current_index]

    effective_anchors = _strictly_increasing_anchors(raw_anchors)

    if raw_progress <= effective_anchors[0]:
        if effective_anchors[0] <= 0:
            return visual_anchors[0]
        ratio = max(0.0, min(raw_progress / effective_anchors[0], 1.0))
        result = round(ratio * visual_anchors[0])
        if current_index is not None:
            result = min(result, visual_anchors[current_index])
        return result

    for index in range(len(effective_anchors) - 1):
        start_raw = effective_anchors[index]
        end_raw = effective_anchors[index + 1]
        if raw_progress <= end_raw:
            span = end_raw - start_raw
            if span <= 0:
                result = visual_anchors[index]
            else:
                ratio = max(0.0, min((raw_progress - start_raw) / span, 1.0))
                start_visual = visual_anchors[index]
                end_visual = visual_anchors[index + 1]
                result = round(start_visual + (end_visual - start_visual) * ratio)
            if current_index is not None:
                result = min(result, visual_anchors[current_index])
            return result

    tail_start = effective_anchors[-1]
    tail_end = 100
    if tail_start >= tail_end:
        result = visual_anchors[-1]
    else:
        ratio = max(0.0, min((raw_progress - tail_start) / (tail_end - tail_start), 1.0))
        result = round(visual_anchors[-1] + (99 - visual_anchors[-1]) * ratio)
    if current_index is not None:
        result = min(result, visual_anchors[current_index])
    return result


def _compute_card_progress(job: DesktopJobRecord) -> int:
    if _is_checkpoint_decision_result(job):
        decision_step = _checkpoint_decision_step(job)
        raw_progress = compute_progress_percent(job.kind, "paused", decision_step)
        remapped = _remap_progress_to_summary_steps(
            job.kind,
            raw_progress,
            current_step=decision_step,
        )
        return remapped if remapped is not None else raw_progress

    if job.status in {DesktopJobState.RUNNING, DesktopJobState.QUEUED} and not _stable_current_step(
        job
    ):
        return 0

    raw_progress = _compute_progress(job)
    if job.status in {DesktopJobState.QUEUED, DesktopJobState.SUCCEEDED}:
        return raw_progress

    steps = _visible_steps_for_kind(job.kind)
    current_step = _stable_current_step(job)
    visible_keys = {step.key for step in steps}
    between_completed_key = _init_long_blueprint_to_outline_completed_key(job, visible_keys)
    current_index = _summary_step_index_for_job(job, steps) if steps else None
    remapped = _remap_progress_to_summary_steps(
        job.kind,
        raw_progress,
        current_step="" if between_completed_key else current_step,
        current_index=current_index,
    )
    return remapped if remapped is not None else raw_progress


_CHAPTER_FLOW_PHASE_HINTS: dict[str, str] = {
    "resolve_chapter_checkpoint": (
        "阶段 1/2：写作、审查、修复与归档前评估；完成后停在归档选择，等待确认后进入归档执行。"
    ),
    "resolve_chapter_checkpoint_finalize": (
        "阶段 2/2：承接上一条方案执行，只处理确认后的正文落盘、长期状态与记忆更新。"
    ),
}


def _phase_label_for_kind(kind: str) -> str:
    if kind == "resolve_chapter_checkpoint":
        return "阶段 1/2"
    if kind == "resolve_chapter_checkpoint_finalize":
        return "阶段 2/2"
    return ""


def _display_job_label(job: DesktopJobRecord) -> str:
    """Return a card title that makes split chapter stages explicit."""
    label = str(job.label or "")
    phase = _phase_label_for_kind(job.kind)
    if not phase or "（阶段 " in label:
        return label
    if " · " in label:
        title, rest = label.split(" · ", 1)
        return f"{title}（{phase}） · {rest}"
    return f"{label}（{phase}）"


_MEMORY_CONTEXT_EVENT_BY_STAGE: dict[str, str] = {
    "planning": "memory_planning_context",
    "draft": "memory_draft_context",
    "finalize": "memory_finalize_context",
}

_MEMORY_CONTEXT_STAGE_LABELS: dict[str, str] = {
    "planning": "规划",
    "draft": "起草",
    "finalize": "收束",
}

_LAYER_SHORT_LABELS: dict[str, str] = {
    "L0_identity": "L0",
    "L1_core_memory": "L1",
    "L2_on_demand": "L2",
    "L3_deep_search": "L3",
}

_CARD_FIXED_HEIGHT = 212
_CARD_MIN_HEIGHT = _CARD_FIXED_HEIGHT
_CARD_MAX_HEIGHT = _CARD_FIXED_HEIGHT

_AUXILIARY_STEP_KEYS_BY_KIND: dict[str, frozenset[str]] = {
    "init_long": frozenset(
        {
            "init_character_system",
            "init_entity_registry",
            "init_entity_graph",
            "creative_director_packet",
            "derive_init_coherence_profile",
            "refine_init_coherence_profile",
            "build_init_coherence_profile",
            "extract_init_coherence_claims",
            "retrieve_init_conflict_candidates",
            "adjudicate_init_conflict_candidates",
            "adjudicate_blueprint_coherence",
            "repair_init_artifact_patch",
            "plan_blueprint_fragments",
            "plan_blueprint_validated",
            "plan_blueprint_repaired",
            "adjudicate_outline_inheritance",
            "adjudicate_contract_coherence",
            "init_source_artifacts",
            "init_source_artifacts_resume",
            "init_source_artifacts_repair",
            "init_source_artifacts_repaired",
            "init_source_artifacts_repair_gate",
            "source_artifacts_repair_targeted",
            "init_repair_reaudit_started",
            "init_repair_reaudit_stage",
            "init_repair_reaudit_passed",
            "init_repair_reaudit_failed",
            "init_knowledge_boundaries",
        }
    ),
}

_INIT_COHERENCE_STAGE_VISIBLE_STEP: dict[str, str] = {
    "blueprint_coherence": "plan_blueprint",
    "outline_inheritance": "plan_outline",
    "contract_coherence": "plan_chapter_contracts",
}
_INIT_COHERENCE_ARTIFACT_VISIBLE_STEP: dict[str, str] = {
    "blueprint": "plan_blueprint",
    "outline": "plan_outline",
    "chapter_contracts": "plan_chapter_contracts",
}

_INIT_LONG_HIDDEN_VISIBLE_ANCHORS: dict[str, str] = {
    "creative_director_packet": "plan_blueprint",
    "init_knowledge_boundaries": "init_character_bible",
    "init_upstream_health": "init_story_bible",
}

_INIT_COHERENCE_RAW_STEP_PREFIXES: tuple[str, ...] = (
    "init_claim_entity_adjudication_",
    "init_coherence_report_resumed",
    "extract_init_coherence_claims",
    "retrieve_init_conflict_candidates",
    "adjudicate_init_conflict_candidates",
    "adjudicate_blueprint_coherence",
    "adjudicate_outline_inheritance",
    "adjudicate_contract_coherence",
    "repair_init_artifact_patch",
    "repair_init_artifact_targets",
    "init_repair_reaudit",
)

# Legacy runs may have a progress event without the stage payload now emitted
# by the coherence services.  These raw steps occur after blueprint generation;
# using the first workflow milestone as their fallback made a 79% operation
# appear as a 4% “规格确认” task.
_INIT_COHERENCE_UNSTAGED_DEFAULT_VISIBLE_STEP = "plan_blueprint"
_INIT_COHERENCE_PRE_BLUEPRINT_VISIBLE_STEPS = frozenset(
    {
        "spec",
        "init_web_research",
        "init_story_bible",
        "plan_blueprint_elements",
        "init_character_bible",
        "profile_style",
    }
)

_INIT_LONG_BLUEPRINT_TO_OUTLINE_RAW_STEPS = frozenset(
    {
        "plan_blueprint_validated",
        "plan_blueprint_repaired",
        "plan_blueprint_subplot_weave_validation",
        "plan_blueprint_subplot_matrix",
        "plan_blueprint_fragments",
        "derive_init_coherence_profile_start",
        "derive_init_coherence_profile",
        "refine_init_coherence_profile_start",
        "refine_init_coherence_profile",
        "build_init_coherence_profile_start",
        "build_init_coherence_profile",
        "init_coherence_profile_resumed",
        "init_creative_refinement",
        "init_creative_refinement_resumed",
        "init_creative_refinement_skipped",
        "adjudicate_blueprint_coherence",
        "repair_init_artifact_patch",
    }
)


class _ElidedLabel(QLabel):
    """Single-line label that preserves the full text in a tooltip."""

    def __init__(self, text: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._full_text = ""
        self.setWordWrap(False)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setText(text)

    def setText(self, text: str) -> None:
        self._full_text = str(text or "")
        self.setToolTip(self._full_text)
        self._apply_elide_text()

    def resizeEvent(self, event: QResizeEvent) -> None:
        self._apply_elide_text()
        super().resizeEvent(event)

    def _apply_elide_text(self) -> None:
        width = self.contentsRect().width()
        if width <= 0:
            QLabel.setText(self, self._full_text)
            return
        metrics = QFontMetrics(self.font())
        elided = metrics.elidedText(self._full_text, Qt.TextElideMode.ElideRight, width)
        QLabel.setText(self, elided)


def _memory_stage_badge_spec(
    stage: str,
    state: str,
    *,
    parallel_running: bool = False,
) -> tuple[str, str]:
    """Return (label, tone) for one workflow memory stage."""
    label_prefix = {
        "indexing": "索引",
        "summary": "摘要",
        "motif": "母题",
    }.get(stage, stage)
    if state == "done":
        return (f"{label_prefix}完成", "success")
    if state == "running":
        if stage == "indexing":
            return ("索引中", "warning")
        return (f"{label_prefix}处理中", "warning")
    if state == "failed":
        return (f"{label_prefix}失败", "danger")
    if state == "pending" and parallel_running:
        # In concurrent-memory phase, pending subtasks are queued in the same
        # parallel lane and should share the active warning tone.
        return (f"{label_prefix}待命", "warning")
    return (f"{label_prefix}待命", "muted")


def _token_step_summary(payload: dict[str, object] | None, *, max_items: int = 8) -> str:
    if not isinstance(payload, dict):
        return ""
    items = payload.get("token_steps")
    if not isinstance(items, list):
        return ""

    parts: list[str] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        step_key = str(item.get("step", "") or "").strip()
        tokens = item.get("tokens")
        if not step_key or not isinstance(tokens, int) or tokens <= 0:
            continue
        parts.append(f"{display_step_name(step_key)} {tokens:,}")
        if len(parts) >= max_items:
            break
    return "  ·  ".join(parts)


def _stage_memory_context_payloads(job: DesktopJobRecord) -> dict[str, dict[str, object]]:
    _chapter_kinds = {
        "prepare_chapter",
        "resolve_chapter_checkpoint",
        "resolve_chapter_checkpoint_finalize",
        "run_chapter",
    }
    if job.kind not in _chapter_kinds:
        return {}

    payloads: dict[str, dict[str, object]] = {}
    step_to_stage = {value: key for key, value in _MEMORY_CONTEXT_EVENT_BY_STAGE.items()}
    for event in reversed(job.events):
        step = str(getattr(event, "step", "") or "")
        stage = step_to_stage.get(step)
        if not stage or stage in payloads:
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        if payload:
            payloads[stage] = payload

    ordered: dict[str, dict[str, object]] = {}
    for stage in ("planning", "draft", "finalize"):
        if stage in payloads:
            ordered[stage] = payloads[stage]
    return ordered


def _memory_context_badge_spec(stage: str, payload: dict[str, object]) -> tuple[str, str]:
    label_prefix = _MEMORY_CONTEXT_STAGE_LABELS.get(stage, stage)
    requested = payload.get("requested_layers")
    resolved = payload.get("resolved_layers")
    requested_count = len(requested) if isinstance(requested, list) else 0
    resolved_count = len(resolved) if isinstance(resolved, list) else 0

    if requested_count <= 0:
        return (f"{label_prefix} 无层", "muted")
    if resolved_count >= requested_count:
        return (f"{label_prefix} {resolved_count}/{requested_count} 层", "success")
    if resolved_count > 0:
        return (f"{label_prefix} {resolved_count}/{requested_count} 层", "warning")
    return (f"{label_prefix} 未命中", "muted")


def _memory_context_detail_line(stage: str, payload: dict[str, object]) -> str:
    label_prefix = _MEMORY_CONTEXT_STAGE_LABELS.get(stage, stage)
    pieces: list[str] = []

    resolved = payload.get("resolved_layers")
    if isinstance(resolved, list) and resolved:
        layer_labels = [
            _LAYER_SHORT_LABELS.get(str(item), str(item)) for item in resolved if str(item).strip()
        ]
        if layer_labels:
            pieces.append("/".join(layer_labels))

    _counts = payload.get("counts")
    counts = _counts if isinstance(_counts, dict) else {}
    _flags = payload.get("flags")
    flags = _flags if isinstance(_flags, dict) else {}

    for key, label in (
        ("relevant_history", "历史"),
        ("previous_chapter_events", "上章"),
        ("motif_suggestions", "母题"),
    ):
        value = counts.get(key)
        if isinstance(value, int) and value > 0:
            pieces.append(f"{label} {value}")

    for key, label in (
        ("has_outline_context", "大纲"),
        ("has_motif_continuity", "母题连贯"),
    ):
        if flags.get(key):
            pieces.append(label)

    if payload.get("history_reused"):
        pieces.append("复用历史")

    if not pieces:
        pieces.append("已记录")
    return f"{label_prefix}：{' · '.join(pieces[:5])}"


def _memory_stage_summary(
    memory_stages: dict[str, str],
    *,
    parallel_running: bool = False,
) -> str:
    parts: list[str] = []
    for key in ("indexing", "summary", "motif"):
        text, _ = _memory_stage_badge_spec(
            key,
            memory_stages.get(key, "pending"),
            parallel_running=parallel_running,
        )
        parts.append(text)
    return f"记忆进度：{' · '.join(parts)}"


def _memory_context_summary(memory_contexts: dict[str, dict[str, object]]) -> str:
    details = [
        _memory_context_detail_line(stage, payload) for stage, payload in memory_contexts.items()
    ]
    return f"记忆命中：{'  ｜  '.join(details)}"


def _latest_format_retry_summary(job: DesktopJobRecord) -> str:
    """Return a compact retry summary for malformed JSON/model-format errors."""
    for event in reversed(job.events):
        if event.step not in {
            "format_repaired",
            "format_repair_strategy_miss",
            "format_retry",
            "format_retry_exhausted",
        }:
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        task = str(payload.get("task", "") or "").strip()
        attempt = payload.get("attempt")
        max_attempts = payload.get("max_attempts")
        error = _truncate_message(str(payload.get("error", "") or ""), 96)
        if event.step == "format_repaired":
            repair_action = str(payload.get("repair_action", "") or "")
            prefix = "格式已专用修复" if repair_action == "llm_format_repair" else "格式已本地修复"
            error = ""
        elif event.step == "format_repair_strategy_miss":
            prefix = "格式修复模块请求中"
        elif isinstance(attempt, int) and isinstance(max_attempts, int) and max_attempts > 0:
            prefix = f"格式重试：第 {attempt}/{max_attempts} 次"
        else:
            prefix = "格式重试：已记录"
        if task:
            prefix += f" · {_llm_task_display_name(task)}"
        if error:
            prefix += f" · {error}"
        return prefix
    return ""


def _latest_format_retry_object_name(job: DesktopJobRecord) -> str:
    """Return a Qt objectName matching the severity of the latest format event."""
    for event in reversed(job.events):
        if event.step == "format_repaired":
            return "cardMeta"
        if event.step in {
            "format_repair_strategy_miss",
            "format_retry",
            "format_retry_exhausted",
        }:
            return "dangerText"
    return "cardMeta"


def _latest_event_index(job: DesktopJobRecord, step_name: str) -> int:
    for index in range(len(job.events) - 1, -1, -1):
        if str(job.events[index].step or "") == step_name:
            return index
    return -1


def _checkpoint_prompt_replan_reason(job: DesktopJobRecord) -> str:
    prompt = str(_checkpoint_payload(job).get("prompt", "") or "").strip()
    if not prompt or "重新规划" not in prompt:
        return ""
    match = re.search(r"未通过质量校验[（(](.+?)[）)]，?已自动重新规划", prompt)
    if match:
        return match.group(1).strip()
    return prompt


def _word_count_replan_text(payload: dict[str, object]) -> str:
    before = payload.get("before")
    reason = str(payload.get("reason", "") or "").strip()
    if isinstance(before, dict):
        actual = before.get("actual")
        target = before.get("target")
        band = str(before.get("band", "") or "").strip()
        if isinstance(actual, int) and isinstance(target, int) and target > 0:
            parts = [f"字数 {actual}/{target}"]
            if band:
                parts.append(band)
            if reason:
                parts.append(reason)
            return "，".join(parts)
    if reason:
        return f"字数闸门：{reason}"
    return "字数闸门未通过"


def _replan_reason_texts(job: DesktopJobRecord) -> list[str]:
    texts: list[str] = []
    replan_index = _latest_event_index(job, "consistency_replan")
    if replan_index >= 0:
        payload = (
            job.events[replan_index].payload
            if isinstance(job.events[replan_index].payload, dict)
            else {}
        )
        violations = payload.get("violations")
        if isinstance(violations, list):
            texts.extend(str(item).strip() for item in violations if str(item).strip())
        for key in ("message", "reason", "error"):
            text = str(payload.get(key, "") or "").strip()
            if text:
                texts.append(text)
        if not texts:
            for event in reversed(job.events[:replan_index]):
                payload = event.payload if isinstance(event.payload, dict) else {}
                step = str(event.step or "")
                if step == "word_count_archive_gate":
                    texts.append(_word_count_replan_text(payload))
                    break
                if step in {
                    "reading_power_repair_warning",
                    "reading_power_repair_no_op",
                    "causal_validation",
                    "continuity_eval",
                    "alignment",
                    "guard_constraint_compliance_check",
                    "state_adjudication_final",
                    "guidance_contract_audit",
                }:
                    for key in ("message", "summary", "reason", "error"):
                        text = str(payload.get(key, "") or "").strip()
                        if text:
                            texts.append(f"{display_step_name(step)}：{text}")
                            break
                    if texts:
                        break
            if not texts:
                texts.append("质量校验未通过，触发重新规划")

    prompt_reason = _checkpoint_prompt_replan_reason(job)
    if prompt_reason:
        texts.append(prompt_reason)

    deduped: list[str] = []
    seen: set[str] = set()
    for text in texts:
        compact = " ".join(str(text or "").split())
        if compact and compact not in seen:
            seen.add(compact)
            deduped.append(compact)
    return deduped


def _word_count_reason_tag(text: str) -> str:
    match = re.search(r"字数\s*(\d+)\s*/\s*(\d+)", text)
    if match:
        actual = int(match.group(1))
        target = max(1, int(match.group(2)))
        if actual < target * 0.8:
            return "字数不足"
        if actual > target * 1.25:
            return "字数超长"
        return "字数偏离"
    if any(marker in text for marker in ("不足", "过短", "too short", "short_text")):
        return "字数不足"
    if any(marker in text for marker in ("超长", "过长", "compress")):
        return "字数超长"
    return "字数闸门"


def _reason_tag_for_text(text: str) -> tuple[str, str]:
    lower = text.lower()
    if any(marker in lower for marker in ("plan_scene", "target_words", "场景 target_words")):
        return ("方案结构", "warning")
    if any(marker in lower for marker in ("word_count", "hard_reject", "candidate_", "字数")):
        return (_word_count_reason_tag(text), "warning")
    if any(marker in lower for marker in ("alignment", "对齐")):
        return ("对齐未过", "warning")
    if any(marker in lower for marker in ("continuity", "连贯")):
        return ("连贯性", "warning")
    if any(marker in lower for marker in ("causal", "因果")):
        return ("因果", "warning")
    if any(marker in lower for marker in ("reading_power", "追读")):
        return ("追读力", "warning")
    if any(marker in lower for marker in ("guard", "护栏")):
        return ("AI护栏", "warning")
    if any(marker in lower for marker in ("state", "状态裁判", "叙事状态")):
        return ("状态裁判", "warning")
    if any(marker in lower for marker in ("canon", "extract", "提取")):
        return ("Canon", "warning")
    if any(marker in lower for marker in ("json", "format", "格式")):
        return ("格式", "danger")
    if any(marker in lower for marker in ("model", "gateway", "llm", "模型", "调用失败")):
        return ("模型调用", "danger")
    if any(marker in lower for marker in ("提示词", "prompt")):
        return ("提示词泄露", "danger")
    return ("校验未过", "warning")


def _replan_reason_badge_specs(
    job: DesktopJobRecord,
    *,
    max_tags: int = 3,
) -> list[tuple[str, str, str]]:
    reasons = _replan_reason_texts(job)
    if not reasons:
        return []
    grouped: dict[str, tuple[str, list[str]]] = {}
    for reason in reasons:
        label, tone = _reason_tag_for_text(reason)
        if label not in grouped:
            grouped[label] = (tone, [])
        grouped[label][1].append(reason)
    specs: list[tuple[str, str, str]] = []
    for label, (tone, items) in grouped.items():
        tooltip = "\n".join(items[:3])
        specs.append((label, tone, tooltip))
        if len(specs) >= max_tags:
            break
    return specs


def _replan_reason_signature(job: DesktopJobRecord) -> tuple[str, ...]:
    return tuple(label for label, _tone, _tooltip in _replan_reason_badge_specs(job))


def _step_matches_key(step: object, key: str) -> bool:
    step_key = str(getattr(step, "key", "") or "")
    if bool(getattr(step, "is_prefix", False)):
        return key.startswith(step_key)
    return key == step_key


def _init_coherence_stage_step(raw_step: str, payload: object) -> str:
    if not _is_init_coherence_raw_step(raw_step):
        return ""
    if not isinstance(payload, dict):
        return ""
    stage = str(payload.get("stage") or "").strip()
    if stage:
        return _INIT_COHERENCE_STAGE_VISIBLE_STEP.get(stage, "")
    artifact = str(payload.get("artifact") or "").strip()
    return _INIT_COHERENCE_ARTIFACT_VISIBLE_STEP.get(artifact, "")


def _is_init_coherence_raw_step(raw_step: str) -> bool:
    return any(raw_step.startswith(prefix) for prefix in _INIT_COHERENCE_RAW_STEP_PREFIXES)


def _init_coherence_event_family(raw_step: str) -> str:
    """Return the event family used to recover stage metadata from history."""

    for prefix in (
        "extract_init_coherence_claims",
        "retrieve_init_conflict_candidates",
        "adjudicate_init_conflict_candidates",
        "init_claim_entity_adjudication_",
        "repair_init_artifact_patch",
        "repair_init_artifact_targets",
        "init_repair_reaudit",
    ):
        if raw_step.startswith(prefix):
            return prefix
    return raw_step


def _latest_init_coherence_stage_step(
    job: DesktopJobRecord,
    raw_step: str,
) -> str:
    """Recover a visible coherence stage from its nearest sibling event.

    A job may resume from history created before each claim event carried a
    ``stage`` field.  The adjacent ``*_start``/cache/adjudication event still
    records the stage, and is a more reliable source than unrelated earlier
    visible pipeline work.
    """

    family = _init_coherence_event_family(raw_step)
    for event in reversed(job.events):
        event_step = str(event.step or "")
        if _init_coherence_event_family(event_step) != family:
            continue
        stage_step = _init_coherence_stage_step(event_step, event.payload)
        if stage_step:
            return stage_step
    return ""


def _has_entered_init_long_outline(job: DesktopJobRecord) -> bool:
    if job.kind != "init_long":
        return False
    return any(_is_init_long_outline_step(event.step) for event in job.events)


def _is_init_long_blueprint_to_outline_transition(
    raw_step: str,
    payload: object,
    job: DesktopJobRecord | None = None,
) -> bool:
    if raw_step in _INIT_LONG_BLUEPRINT_TO_OUTLINE_RAW_STEPS:
        return True
    if _is_init_coherence_raw_step(raw_step):
        if isinstance(payload, dict):
            stage = str(payload.get("stage") or "").strip()
            if stage:
                return stage == "blueprint_coherence"
        # Fallback: search event history for blueprint_coherence stage.
        # Handles batch steps with incomplete payloads or edge cases where
        # the current step's payload doesn't carry the stage field.
        if job is not None:
            return _has_blueprint_coherence_stage_in_history(job)
        return False
    return False


def _has_blueprint_coherence_stage_in_history(job: DesktopJobRecord) -> bool:
    """Check if any coherence event in history has stage=blueprint_coherence."""
    for event in reversed(job.events):
        if not _is_init_coherence_raw_step(event.step):
            continue
        if isinstance(event.payload, dict):
            stage = str(event.payload.get("stage") or "").strip()
            if stage:
                return stage == "blueprint_coherence"
    return False


def _init_long_blueprint_to_outline_completed_key(
    job: DesktopJobRecord,
    visible_keys: set[str],
) -> str:
    if job.kind != "init_long" or _has_entered_init_long_outline(job):
        return ""
    current = _stable_current_step(job)
    if not current:
        return ""
    payload = _latest_payload_for_raw_step(job, current)
    if _is_init_long_blueprint_to_outline_transition(current, payload, job=job):
        return _nearest_visible_step_key(job.kind, "plan_blueprint", visible_keys)
    # Fallback: even if the current step isn't recognized as a transition,
    # coherence events in history indicate we're still in the blueprint
    # coherence phase (e.g. current_step was updated to a non-coherence
    # step between coherence events).
    if _has_blueprint_coherence_stage_in_history(job):
        return _nearest_visible_step_key(job.kind, "plan_blueprint", visible_keys)
    return ""


def _latest_payload_for_raw_step(job: DesktopJobRecord, raw_step: str) -> dict[str, Any]:
    if raw_step == str(job.current_step or ""):
        current_payload = getattr(job, "current_step_payload", {})
        if isinstance(current_payload, dict) and current_payload:
            return current_payload
    for event in reversed(job.events):
        if event.step == raw_step and isinstance(event.payload, dict):
            return event.payload
    return {}


def _stable_current_step(job: DesktopJobRecord) -> str:
    """Return the current semantic step, ignoring retry/format diagnostic events."""
    current = job.current_step or (job.events[-1].step if job.events else "")
    if is_non_progress_step_event(current):
        return _last_non_transient_step(job)
    return current


def _indicator_step_key_for_event(
    kind: str,
    raw_step: str,
    payload: object,
    visible_keys: set[str],
) -> str:
    if kind == "resolve_chapter_checkpoint" and raw_step == "resume_from_progress":
        if isinstance(payload, dict):
            resume_step = resolve_checkpoint_resume_step(payload)
            if resume_step:
                return _nearest_visible_step_key(
                    kind,
                    _resolve_step_key(kind, resume_step),
                    visible_keys,
                )
    if kind == "init_long":
        if raw_step == "init_resume_anchor" and isinstance(payload, dict):
            anchor_step = str(payload.get("step") or payload.get("anchor_step") or "").strip()
            if anchor_step:
                return _nearest_visible_step_key(
                    kind,
                    _resolve_step_key(kind, anchor_step),
                    visible_keys,
                )
        staged_step = _init_coherence_stage_step(raw_step, payload)
        if staged_step:
            return staged_step
    resolved_step = _resolve_step_key(kind, raw_step)
    return _nearest_visible_step_key(kind, resolved_step, visible_keys)


def _resume_floor_step_key_for_job(job: DesktopJobRecord, visible_keys: set[str]) -> str:
    raw_step = init_long_resume_anchor_step(job)
    if not raw_step:
        return ""
    return _nearest_visible_step_key(
        job.kind,
        _resolve_step_key(job.kind, raw_step),
        visible_keys,
    )


def _is_init_long_outline_claim_prefetch_event(event: object) -> bool:
    step = str(getattr(event, "step", "") or "")
    if step != "extract_init_coherence_claims":
        return False
    payload = getattr(event, "payload", {})
    if not isinstance(payload, dict):
        return False
    return (
        str(payload.get("stage") or "").strip() == "outline_inheritance"
        and str(payload.get("artifact") or "").strip() == "outline"
        and str(payload.get("extraction_mode") or "").strip() == "stream"
    )


def _is_init_long_outline_claim_prefetch_active(job: DesktopJobRecord) -> bool:
    if job.kind != "init_long" or str(job.current_step or "") != "extract_init_coherence_claims":
        return False
    payload = _latest_payload_for_raw_step(job, "extract_init_coherence_claims")
    return str(payload.get("stage") or "").strip() == "outline_inheritance"


def _latest_init_long_outline_display_name(job: DesktopJobRecord) -> str:
    for event in reversed(job.events):
        if not _is_init_long_outline_step(str(event.step or "")):
            continue
        payload = event.payload if isinstance(event.payload, dict) else {}
        base = display_step_name(event.step)
        return format_init_long_outline_step_label(base, payload)
    return "章节大纲"


def _active_display_step_name_for_job(job: DesktopJobRecord) -> str:
    label = display_step_name_for_job(job)
    if _init_resume_anchor_is_ahead_of_current(job):
        return f"依赖重检 · {label}"
    return label


def _init_resume_anchor_is_ahead_of_current(job: DesktopJobRecord) -> bool:
    if job.kind != "init_long":
        return False
    current_raw = _stable_current_step(job)
    if not current_raw or current_raw == "init_resume_anchor":
        return False
    steps = _visible_steps_for_kind(job.kind)
    visible_keys = {str(getattr(step, "key", "") or "") for step in steps}
    current_key = _indicator_current_step_key(job, visible_keys, raw_step=current_raw)
    floor_key = _resume_floor_step_key_for_job(job, visible_keys)
    current_index = _step_index_in_sequence(steps, current_key)
    floor_index = _step_index_in_sequence(steps, floor_key) if floor_key else None
    return floor_index is not None and (current_index is None or floor_index > current_index)


def _init_long_outline_generation_summary(job: DesktopJobRecord) -> str:
    if not _is_init_long_outline_claim_prefetch_active(job):
        return ""
    if not any(_is_init_long_outline_step(str(event.step or "")) for event in job.events):
        return ""
    latest = _latest_init_long_outline_display_name(job)
    if not latest:
        return ""
    return f"大纲生成：{latest}"


def _init_long_chapter_design_matrix_summary(job: DesktopJobRecord) -> str:
    if job.kind != "init_long":
        return ""
    payload = _latest_payload_for_raw_step(job, "plan_chapter_design_matrix")
    chapters = payload.get("chapters")
    if not isinstance(chapters, (int, float)) or int(chapters) <= 0:
        return ""
    total = payload.get("total_chapters")
    chapter_count = int(chapters)
    if isinstance(total, (int, float)) and int(total) > 0 and int(total) != chapter_count:
        coverage = f"{chapter_count}/{int(total)}章"
    else:
        coverage = f"{chapter_count}章"

    parts = [f"已覆盖 {coverage}", "结构预计算，非大纲正文"]
    entities = payload.get("entities")
    if isinstance(entities, (int, float)) and int(entities) > 0:
        parts.append(f"实体 {int(entities)}")
    return f"章节设计矩阵：{' · '.join(parts)}"


def _init_long_outline_claim_prefetch_summary(job: DesktopJobRecord) -> str:
    if job.kind != "init_long":
        return ""
    events = [event for event in job.events if _is_init_long_outline_claim_prefetch_event(event)]
    if not events:
        return ""

    total_claims = 0
    empty_batches = 0
    fallback_claims = 0
    filtered_claims = 0
    latest_claims = 0
    latest_parallel = 0
    for event in events:
        payload = event.payload if isinstance(event.payload, dict) else {}
        claims = payload.get("claims")
        batch_claims = int(claims) if isinstance(claims, (int, float)) else 0
        latest_claims = batch_claims
        total_claims += max(0, batch_claims)
        if batch_claims <= 0:
            empty_batches += 1
        fallback = payload.get("fallback_claims")
        if isinstance(fallback, (int, float)) and fallback > 0:
            fallback_claims += int(fallback)
        filtered = payload.get("filtered_claims")
        if isinstance(filtered, (int, float)) and filtered > 0:
            filtered_claims += int(filtered)
        max_parallel = payload.get("max_parallel")
        if isinstance(max_parallel, (int, float)) and max_parallel > 0:
            latest_parallel = int(max_parallel)

    parts = [
        f"{len(events)} 批",
        f"累计 {total_claims} 条",
        f"最近 {latest_claims} 条",
    ]
    if empty_batches:
        parts.append(f"空批 {empty_batches}")
    if fallback_claims:
        parts.append(f"本地兜底 {fallback_claims} 条")
    if filtered_claims:
        parts.append(f"过滤低信号 {filtered_claims} 条")
    if latest_parallel > 1:
        parts.append(f"并发 {latest_parallel}")
    return f"并行预取：一致性 Claims（大纲） · {' · '.join(parts)}"


def _latest_visible_context_for_raw_step(
    job: DesktopJobRecord,
    raw_step: str,
    visible_keys: set[str],
) -> str:
    """Return the most recent visible step context seen *before* raw_step.

    Only events that occur before the first occurrence of raw_step in the
    history are considered.  Coherence-related visible steps (plan_blueprint,
    plan_outline, plan_chapter_contracts) are excluded from the context so
    that coherence sub-steps without a stage payload fall back to the last
    non-coherence visible step (e.g. derive_editorial_contract) rather than
    inheriting context from a later pipeline phase.
    """
    coherence_visible_steps = set(_INIT_COHERENCE_STAGE_VISIBLE_STEP.values())
    latest_context = ""
    latest_matching_context = ""
    found_raw_step = False
    for event in job.events:
        event_key = _indicator_step_key_for_event(job.kind, event.step, event.payload, visible_keys)
        if event.step == raw_step:
            found_raw_step = True
            latest_matching_context = latest_context
        if (
            event_key in visible_keys
            and not found_raw_step
            and event_key not in coherence_visible_steps
        ):
            latest_context = event_key
    return latest_matching_context or latest_context


def _indicator_current_step_key(
    job: DesktopJobRecord,
    visible_keys: set[str],
    *,
    raw_step: str = "",
) -> str:
    current = raw_step or _stable_current_step(job)
    if not current:
        return ""
    payload = _latest_payload_for_raw_step(job, current)
    if (
        job.kind == "init_long"
        and _is_init_coherence_raw_step(current)
        and not _init_coherence_stage_step(current, payload)
    ):
        staged_step = _latest_init_coherence_stage_step(job, current)
        if staged_step:
            return staged_step
        context_step = _latest_visible_context_for_raw_step(job, current, visible_keys)
        if context_step and context_step not in _INIT_COHERENCE_PRE_BLUEPRINT_VISIBLE_STEPS:
            return context_step
        if _INIT_COHERENCE_UNSTAGED_DEFAULT_VISIBLE_STEP in visible_keys:
            return _INIT_COHERENCE_UNSTAGED_DEFAULT_VISIBLE_STEP
        if context_step:
            return context_step
    return _indicator_step_key_for_event(job.kind, current, payload, visible_keys)


def _visible_steps_for_kind(kind: str) -> list[PipelineStep]:
    return [
        PipelineStep(step.key, step.label, step.is_prefix)
        for step in visible_workflow_steps(kind)
    ]


def _visible_parallel_pairs_for_kind(kind: str, visible_keys: set[str]) -> list[tuple[str, str]]:
    return [
        (left, right)
        for left, right in _parallel_pairs_for_kind(kind)
        if left in visible_keys and right in visible_keys
    ]


def _nearest_visible_step_key(kind: str, resolved_key: str, visible_keys: set[str]) -> str:
    if resolved_key in visible_keys:
        return resolved_key
    if kind == "init_long":
        anchor = _INIT_LONG_HIDDEN_VISIBLE_ANCHORS.get(resolved_key, "")
        if anchor in visible_keys:
            return anchor

    steps = _steps_for_kind(kind)
    matched_index = -1
    for index, step in enumerate(steps):
        if resolved_key == str(getattr(step, "key", "") or ""):
            matched_index = index
            break
    for index, step in enumerate(steps):
        if matched_index >= 0:
            break
        if _step_matches_key(step, resolved_key):
            matched_index = index
            break
    if matched_index < 0:
        return resolved_key

    for step in reversed(steps[:matched_index]):
        step_key = str(getattr(step, "key", "") or "")
        if step_key in visible_keys:
            return step_key
    for step in steps[matched_index + 1 :]:
        step_key = str(getattr(step, "key", "") or "")
        if step_key in visible_keys:
            return step_key
    return resolved_key


_INDICATOR_HIGH_WATER_RESET_STEPS: frozenset[str] = frozenset(
    {
        "init_resume_rollback",
        "short_resume_rollback",
        "consistency_replan",
        "regenerate_plan",
        "regenerate_plan_with_notes",
        "edit_plan_and_write",
        "adjust_outline_and_finalize",
    }
)
_INDICATOR_HIGH_WATER_RESET_ACTIONS: frozenset[str] = frozenset(
    {"rollback", "reset", "replan", "invalidate"}
)


def _indicator_event_resets_high_water(event: object) -> bool:
    step = str(getattr(event, "step", "") or "")
    if step in _INDICATOR_HIGH_WATER_RESET_STEPS:
        return True
    payload = getattr(event, "payload", {})
    if not isinstance(payload, dict):
        return False
    action = str(payload.get("action") or "").strip().lower()
    return action in _INDICATOR_HIGH_WATER_RESET_ACTIONS


def _indicator_done_through_state(
    steps: Sequence[object],
    completed_index: int | None,
) -> StepIndicatorState:
    states: list[StepDotState] = ["pending"] * len(steps)
    if completed_index is None:
        return StepIndicatorState(tuple(states))
    completed_index = max(0, min(completed_index, len(states) - 1))
    for index in range(completed_index + 1):
        states[index] = "done"
    return StepIndicatorState(tuple(states))


def _indicator_frontier_state(
    steps: Sequence[object],
    frontier_index: int | None,
    *,
    frontier_state: StepDotState = "active",
) -> StepIndicatorState:
    states: list[StepDotState] = ["pending"] * len(steps)
    if frontier_index is None:
        return StepIndicatorState(tuple(states))
    frontier_index = max(0, min(frontier_index, len(states) - 1))
    for index in range(frontier_index):
        states[index] = "done"
    states[frontier_index] = frontier_state
    return StepIndicatorState(tuple(states))


_INIT_LONG_TERMINAL_STAGE_OUTCOMES: dict[str, tuple[str, StepDotState]] = {
    "profile_style": ("profile_style", "done"),
    "profile_style_resumed": ("profile_style", "done"),
    "profile_style_failed": ("profile_style", "failed"),
    "profile_style_skipped": ("profile_style", "skipped"),
}


def _overlay_init_long_terminal_stage_outcomes(
    job: DesktopJobRecord,
    steps: Sequence[PipelineStep],
    state: StepIndicatorState,
) -> StepIndicatorState:
    """Keep optional init stages truthful after later stages are reached.

    The long-project initializer is intentionally allowed to continue when
    style-profile generation fails (unless the user enables the required
    setting).  A high-water progress indicator would otherwise render that
    stage as done merely because a later stage started, even though
    ``style_profile.json`` was never persisted.
    """
    if job.kind != "init_long":
        return state

    latest_outcomes: dict[str, StepDotState] = {}
    for event in job.events:
        outcome = _INIT_LONG_TERMINAL_STAGE_OUTCOMES.get(str(event.step or ""))
        if outcome is None:
            continue
        stage_key, dot_state = outcome
        latest_outcomes[stage_key] = dot_state

    if not latest_outcomes:
        return state

    indexes = {step.key: index for index, step in enumerate(steps)}
    dot_states = list(state.dot_states)
    for stage_key, dot_state in latest_outcomes.items():
        index = indexes.get(stage_key)
        if index is not None:
            dot_states[index] = dot_state
    return StepIndicatorState(tuple(dot_states))


def _indicator_state_for_job(
    job: DesktopJobRecord,
    steps: Sequence[PipelineStep],
) -> StepIndicatorState:
    if not steps:
        return StepIndicatorState(())

    visible_keys = {str(getattr(step, "key", "") or "") for step in steps}
    high_water_index: int | None = None
    latest_visible_context = ""
    current_raw_step = _stable_current_step(job)
    current_payload = _latest_payload_for_raw_step(job, current_raw_step)
    current_phase_step = _phase_step_for_event(job.kind, current_raw_step, current_payload)
    current_phase = _get_phase_index(job.kind, current_phase_step)

    for event in job.events:
        event_payload = event.payload if isinstance(event.payload, dict) else {}
        event_phase_step = _phase_step_for_event(job.kind, event.step, event_payload)
        event_phase = _get_phase_index(job.kind, event_phase_step)
        if event_phase >= 0 and current_phase >= 0 and event_phase > current_phase:
            continue

        step_key = _indicator_step_key_for_event(
            job.kind,
            event.step,
            event.payload,
            visible_keys,
        )
        if (
            job.kind == "init_long"
            and _is_init_coherence_raw_step(event.step)
            and not _init_coherence_stage_step(event.step, event.payload)
            and latest_visible_context
        ):
            step_key = latest_visible_context

        event_index = _step_index_in_sequence(steps, step_key)
        if _indicator_event_resets_high_water(event):
            high_water_index = event_index
        elif event_index is not None:
            high_water_index = (
                event_index if high_water_index is None else max(high_water_index, event_index)
            )

        if step_key in visible_keys:
            latest_visible_context = step_key

    if _is_checkpoint_decision_result(job) and job.status == DesktopJobState.SUCCEEDED:
        decision_step = _resolve_step_key(job.kind, _checkpoint_decision_step(job))
        decision_key = _nearest_visible_step_key(job.kind, decision_step, visible_keys)
        return _overlay_init_long_terminal_stage_outcomes(
            job,
            steps,
            _indicator_done_through_state(
                steps,
                _step_index_in_sequence(steps, decision_key),
            ),
        )

    if job.status == DesktopJobState.SUCCEEDED:
        return _overlay_init_long_terminal_stage_outcomes(
            job,
            steps,
            StepIndicatorState(tuple("done" for _ in steps)),
        )

    if job.status == DesktopJobState.FAILED:
        failed_step = job.current_step or (job.events[-1].step if job.events else "")
        if failed_step == "cancelled" or is_non_progress_step_event(failed_step):
            failed_step = _last_non_transient_step(job)
        resolved_failed_step = _resolve_step_key(job.kind, failed_step)
        failed_key = _nearest_visible_step_key(job.kind, resolved_failed_step, visible_keys)
        failed_index = _step_index_in_sequence(steps, failed_key)
        if failed_index is None:
            failed_index = high_water_index
        frontier_state: StepDotState = "skipped" if _is_cancelled_job(job) else "failed"
        return _overlay_init_long_terminal_stage_outcomes(
            job,
            steps,
            _indicator_frontier_state(steps, failed_index, frontier_state=frontier_state),
        )

    if job.status in {
        DesktopJobState.RUNNING,
        DesktopJobState.QUEUED,
        DesktopJobState.PAUSED,
    }:
        between_completed_key = _init_long_blueprint_to_outline_completed_key(
            job,
            visible_keys,
        )
        if between_completed_key:
            return _overlay_init_long_terminal_stage_outcomes(
                job,
                steps,
                _indicator_done_through_state(
                    steps,
                    _step_index_in_sequence(steps, between_completed_key),
                ),
            )

        current_step_key = _indicator_current_step_key(
            job,
            visible_keys,
            raw_step=_stable_current_step(job),
        )
        floor_step_key = _resume_floor_step_key_for_job(job, visible_keys)
        candidate_indexes = [
            index
            for index in (
                high_water_index,
                _step_index_in_sequence(steps, current_step_key),
                _step_index_in_sequence(steps, floor_step_key),
            )
            if index is not None
        ]
        frontier_index = max(candidate_indexes) if candidate_indexes else None
        return _overlay_init_long_terminal_stage_outcomes(
            job,
            steps,
            _indicator_frontier_state(steps, frontier_index),
        )

    return _overlay_init_long_terminal_stage_outcomes(
        job,
        steps,
        _indicator_frontier_state(steps, high_water_index),
    )


def _auxiliary_step_summary(job: DesktopJobRecord) -> str:
    hidden_keys = _AUXILIARY_STEP_KEYS_BY_KIND.get(job.kind, frozenset())
    if not hidden_keys:
        return ""

    hidden_steps = [
        step
        for step in _steps_for_kind(job.kind)
        if str(getattr(step, "key", "") or "") in hidden_keys
    ]
    if not hidden_steps:
        return ""

    resolved_events = {_resolve_step_key(job.kind, event.step) for event in job.events}
    fallback_events = {
        _resolve_step_key(job.kind, event.step)
        for event in job.events
        if str(event.step or "").endswith("_failed")
        or (
            isinstance(event.payload, dict)
            and bool(str(event.payload.get("fallback", "") or "").strip())
        )
    }
    current_key = _resolve_step_key(job.kind, _stable_current_step(job))
    skip_outline_claim_prefetch = _is_init_long_outline_claim_prefetch_active(job)

    done_labels: list[str] = []
    fallback_labels: list[str] = []
    pending_labels: list[str] = []
    current_label = ""
    failed_label = ""
    for step in hidden_steps:
        step_key = str(getattr(step, "key", "") or "")
        label = str(getattr(step, "label", "") or step_key)
        if skip_outline_claim_prefetch and step_key == "extract_init_coherence_claims":
            continue
        if current_key == step_key and job.status == DesktopJobState.FAILED:
            failed_label = label
        elif current_key == step_key and job.status in {
            DesktopJobState.RUNNING,
            DesktopJobState.QUEUED,
        }:
            current_label = label
        elif step_key in fallback_events:
            fallback_labels.append(label)
        elif step_key in resolved_events:
            done_labels.append(label)
        elif job.status == DesktopJobState.SUCCEEDED:
            pending_labels.append(label)

    parts: list[str] = []
    if failed_label:
        parts.append(f"{failed_label}失败")
    if current_label:
        parts.append(f"当前 {current_label}")
    if fallback_labels:
        parts.append(f"已兜底 {'、'.join(fallback_labels)}")
    if done_labels:
        parts.append(f"已过 {'、'.join(done_labels)}")
    if pending_labels:
        parts.append(f"未触发 {'、'.join(pending_labels)}")
    if not parts:
        return ""
    return f"后台步骤：{'；'.join(parts)}"


def _compact_step_summary(job: DesktopJobRecord) -> str:
    """Return the current semantic step as a compact detail line."""
    steps = _visible_steps_for_kind(job.kind)
    if not steps:
        return ""

    current_raw_step = (
        _checkpoint_decision_step(job)
        if _is_checkpoint_decision_result(job)
        else _stable_current_step(job)
    )
    visible_keys = {str(getattr(step, "key", "") or "") for step in steps}
    resolved_current = _indicator_current_step_key(job, visible_keys, raw_step=current_raw_step)
    current_index = -1
    for index, step in enumerate(steps):
        if resolved_current == step.key:
            current_index = index
            break
    if current_index < 0:
        for index, step in enumerate(steps):
            if step.is_prefix and resolved_current.startswith(step.key):
                current_index = index
                break

    if current_index < 0 and job.status == DesktopJobState.SUCCEEDED:
        current_index = len(steps) - 1
    elif current_index < 0:
        for event in reversed(job.events):
            resolved = _indicator_step_key_for_event(
                job.kind,
                event.step,
                event.payload,
                visible_keys,
            )
            for index, step in enumerate(steps):
                if (step.is_prefix and resolved.startswith(step.key)) or resolved == step.key:
                    current_index = index
                    break
            if current_index >= 0:
                break

    floor_key = _resume_floor_step_key_for_job(job, visible_keys)
    floor_index = _step_index_in_sequence(steps, floor_key) if floor_key else None
    if floor_index is not None and floor_index > current_index:
        current_index = floor_index

    total = len(steps)
    if current_index >= 0:
        step_label = steps[current_index].label
        return f"步骤：{step_label} · {current_index + 1}/{total}"

    return f"步骤：等待开始 · 0/{total}"


def _init_resume_anchor_detail(job: DesktopJobRecord) -> str:
    if job.kind != "init_long" or job.status not in {
        DesktopJobState.RUNNING,
        DesktopJobState.QUEUED,
        DesktopJobState.PAUSED,
    }:
        return ""
    raw_step = init_long_resume_anchor_step(job)
    if not raw_step:
        return ""
    resolved = _resolve_step_key(job.kind, raw_step)
    label = ""
    for step in _steps_for_kind(job.kind):
        step_key = str(getattr(step, "key", "") or "")
        if resolved == step_key or (
            bool(getattr(step, "is_prefix", False)) and resolved.startswith(step_key)
        ):
            label = str(getattr(step, "label", "") or "")
            break
    if not label:
        label = display_step_name(raw_step).removesuffix("生成").removesuffix("（已恢复）")
    return f"恢复锚点：{label}已校验落盘；低层依赖重检不代表进度倒退"


def _init_repair_retry_tooltip(job: DesktopJobRecord) -> str:
    """Return retry copy that matches the failed init layer."""

    haystack_parts = [
        job.current_step,
        job.error,
        job.error_summary.get("summary") if isinstance(job.error_summary, dict) else "",
        job.error_summary.get("detail") if isinstance(job.error_summary, dict) else "",
    ]
    haystack = "\n".join(str(part or "") for part in haystack_parts)
    if any(
        marker in haystack
        for marker in (
            "source_artifacts",
            "init_source_artifacts",
            "源头 artifact",
            "源头准入",
            "unresolved_contract_entity",
        )
    ):
        return "复用已生成蓝图、大纲与契约，仅重跑源头准入修复和初始化收尾。"
    return "复用可验证的已落盘产物，重新提交自动修复与初始化复审。"


_RUN_INSIGHT_STATUS_LABELS = {
    "inactive": "未触发",
    "pending": "待执行",
    "running": "执行中",
    "success": "已通过",
    "warning": "需留意",
    "blocked": "已阻断",
    "skipped": "已跳过",
    "rolled_back": "已回滚",
}


class RunInsightDialog(QDialog):
    """Compact PySide detail view for Engine-owned chapter transparency."""

    def __init__(
        self,
        insight: dict[str, Any],
        efficiency: dict[str, Any],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("runInsightDialog")
        self.setWindowTitle(str(insight.get("label") or "运行透明度"))
        self.setModal(True)
        self.setMinimumSize(520, 380)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 18, 22, 18)
        layout.setSpacing(10)
        title = QLabel(str(insight.get("label") or "运行透明度"))
        title.setObjectName("dlgTitle")
        layout.addWidget(title)
        status = str(insight.get("status") or "inactive")
        state = QLabel(_RUN_INSIGHT_STATUS_LABELS.get(status, status))
        state.setObjectName("runInsightStatus")
        state.setProperty("insightStatus", status)
        layout.addWidget(state)

        detail = QTextEdit()
        detail.setObjectName("runInsightDetail")
        detail.setReadOnly(True)
        summary = str(insight.get("summary") or "Engine 未提供结论。")
        explanation = str(insight.get("detail") or "Engine 未提供更多说明。")
        metric_lines = [
            f"模型调用：{int(efficiency.get('llm_calls', 0) or 0)}",
            f"Tokens：{int(efficiency.get('total_tokens', 0) or 0):,}",
            f"费用（USD）：{float(efficiency.get('cost_usd', 0.0) or 0.0):.4f}",
            f"MCP 查询：{int(efficiency.get('research_queries', 0) or 0)}",
            f"证据缓存：{int(efficiency.get('research_cache_hits', 0) or 0)}",
            f"语义修改：{int(efficiency.get('semantic_mutations', 0) or 0)}",
            f"报告刷新：{int(efficiency.get('report_refreshes', 0) or 0)}",
            f"回滚：{int(efficiency.get('rollbacks', 0) or 0)}",
            f"终稿验证：{int(efficiency.get('final_hash_verifications', 0) or 0)}",
        ]
        detail.setPlainText(
            f"本次结论\n{summary}\n\n说明\n{explanation}\n\n效率摘要\n"
            + "\n".join(metric_lines)
        )
        layout.addWidget(detail, 1)
        close_btn = ActionButton("关闭", variant="primary")
        close_btn.clicked.connect(self.accept)
        layout.addWidget(close_btn, 0, Qt.AlignmentFlag.AlignRight)


class JobCard(Surface):
    """Task card showing percentage progress, status and details."""

    def __init__(
        self,
        job: DesktopJobRecord,
        storage_root: Path | None = None,
        historical: bool = False,
        enable_init_repair_actions: bool = False,
        init_repair_blocked_project_ids: set[str] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__("card", parent)
        self.setGraphicsEffect(None)  # type: ignore[arg-type]
        self._storage_root = storage_root
        self._job: DesktopJobRecord | None = None
        self._historical = bool(historical)
        self._enable_init_repair_actions = bool(enable_init_repair_actions)
        self._init_repair_blocked_project_ids = set(init_repair_blocked_project_ids or set())
        self._render_signature: tuple[object, ...] | None = None
        self._meta_label: QLabel | None = None
        self._elapsed_timer = QTimer(self)
        self._elapsed_timer.setInterval(1000)
        self._elapsed_timer.timeout.connect(self._tick_elapsed)
        self._card_color = _job_status_color("queued")
        self._color_animation = QPropertyAnimation(self, b"card_color")
        self._color_animation.setDuration(300)
        self._color_animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        # --- Paint throttling (60 fps cap) -----------------------------------
        # Multiple rapid update() calls (animation frames, progress ticks) are
        # coalesced into a single repaint per 16 ms window.
        self._repaint_pending: bool = False
        self._repaint_timer = QTimer(self)
        self._repaint_timer.setSingleShot(True)
        self._repaint_timer.setInterval(16)  # ~60 fps
        self._repaint_timer.timeout.connect(self._flush_repaint)
        # --- Pixmap cache ----------------------------------------------------
        # Cache the painted background so paintEvent is a cheap blit when the
        # card colour and size have not changed.
        self._cached_pixmap: QPixmap | None = None
        self._cached_pixmap_size: QSize | None = None
        self._cached_pixmap_color: QColor | None = None
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(16, 10, 16, 10)
        self._layout.setSpacing(5)
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        self.setMinimumHeight(_CARD_FIXED_HEIGHT)
        self.setProperty("historical", self._historical)
        self.setProperty("compact", self._historical)
        self.set_job(job)

    # Signals for action bar buttons — wired by parent (WorkflowPage → DesktopJobManager)
    stop_requested = Signal(str)  # job_id
    resume_requested = Signal(str, str, int)  # job_id, project_id, chapter_number
    checkpoint_resume_requested = Signal(str, str, int)  # job_id, project_id, chapter_number
    init_retry_requested = Signal(str, str)  # job_id, project_id
    init_manual_repair_requested = Signal(str, str)  # job_id, project_id

    @staticmethod
    def _signature_for(job: DesktopJobRecord) -> tuple[object, ...]:
        result = job.result if isinstance(job.result, dict) else {}
        warning_count = len(_result_warnings(result)) if result else 0
        checkpoint = result.get("checkpoint") if isinstance(result.get("checkpoint"), dict) else {}
        return (
            job.job_id,
            job.status,
            job.current_step,
            _last_card_render_event_signature(job),
            job.error,
            bool(job.error_summary),
            result.get("overall_score"),
            result.get("word_count"),
            result.get("status"),
            result.get("applied_option_id"),
            checkpoint.get("checkpoint_type") if isinstance(checkpoint, dict) else "",
            warning_count,
            _replan_reason_signature(job),
        )

    def _init_repair_actions_available(self, job: DesktopJobRecord) -> bool:
        if not self._enable_init_repair_actions:
            return False
        if job.kind != "init_long" or job.status != DesktopJobState.FAILED:
            return False
        if not job.project_id or self._storage_root is None:
            return False
        if job.project_id in self._init_repair_blocked_project_ids:
            return False
        haystack_parts = [
            job.current_step,
            job.error,
            job.error_summary.get("summary") if isinstance(job.error_summary, dict) else "",
            job.error_summary.get("detail") if isinstance(job.error_summary, dict) else "",
        ]
        haystack = "\n".join(str(part or "") for part in haystack_parts)
        if not any(
            marker in haystack
            for marker in (
                "init_readiness",
                "outline_inheritance",
                "初始化准入",
                "章节大纲继承",
                "大纲继承",
                "source_artifacts",
                "源头 artifact",
                "源头准入",
                "needs_repair",
            )
        ):
            return False
        return init_manual_repair_available(self._storage_root / job.project_id)

    def _get_card_color(self) -> QColor:
        return self._card_color

    def _set_card_color(self, value: QColor) -> None:
        self._card_color = QColor(value)
        self._schedule_repaint()

    card_color = Property(QColor, _get_card_color, _set_card_color)

    def _schedule_repaint(self) -> None:
        if self._repaint_pending:
            return
        self._repaint_pending = True
        self._repaint_timer.start()

    def _flush_repaint(self) -> None:
        self._repaint_pending = False
        self.update()

    def _invalidate_pixmap_cache(self) -> None:
        self._cached_pixmap = None
        self._cached_pixmap_size = None
        self._cached_pixmap_color = None

    def refresh_theme_colors(self) -> None:
        """Refresh the token-derived status wash and its cached pixmap."""
        if self._job is None:
            return
        self._color_animation.stop()
        self._card_color = _job_status_color(_card_color_key(self._job))
        self._invalidate_pixmap_cache()
        self._schedule_repaint()

    def _animate_status_color(self, status: str) -> None:
        target = _job_status_color(status)
        if not animations_supported():
            self._card_color = QColor(target)
            self._schedule_repaint()
            return
        self._color_animation.stop()
        self._color_animation.setStartValue(self._card_color)
        self._color_animation.setEndValue(target)
        self._color_animation.start()

    def paintEvent(self, event: QPaintEvent) -> None:
        current_size = self.size()
        current_color = self._card_color
        cache_valid = (
            self._cached_pixmap is not None
            and self._cached_pixmap_size == current_size
            and self._cached_pixmap_color is not None
            and self._cached_pixmap_color == current_color
        )
        if not cache_valid:
            pixmap = QPixmap(current_size)
            pixmap.fill(QColor(0, 0, 0, 0))
            opt = QStyleOptionFrame()
            opt.initFrom(self)
            painter = QPainter(pixmap)
            self.style().drawControl(QStyle.ControlElement.CE_ShapedFrame, opt, painter, self)
            if current_color.isValid() and current_color.alpha() > 0:
                painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(current_color)
                painter.drawRoundedRect(
                    self.rect().adjusted(1, 1, -1, -1), SURFACE_RADIUS, SURFACE_RADIUS
                )
            painter.end()
            self._cached_pixmap = pixmap
            self._cached_pixmap_size = current_size
            self._cached_pixmap_color = QColor(current_color)
        blit = QPainter(self)
        cached_pixmap = self._cached_pixmap
        if cached_pixmap is not None:
            blit.drawPixmap(0, 0, cached_pixmap)

    def resizeEvent(self, event: QResizeEvent) -> None:
        self._invalidate_pixmap_cache()
        super().resizeEvent(event)

    def sizeHint(self) -> QSize:
        hint = super().sizeHint()
        hint.setHeight(max(hint.height(), self.minimumHeight()))
        return hint

    def minimumSizeHint(self) -> QSize:
        hint = super().minimumSizeHint()
        hint.setHeight(max(hint.height(), self.minimumHeight()))
        return hint

    def set_storage_root(self, storage_root: Path | None) -> None:
        if self._storage_root == storage_root:
            return
        self._storage_root = storage_root
        self._render_signature = None
        if self._job is not None:
            self.set_job(self._job)

    def set_init_repair_blocked_project_ids(self, project_ids: set[str] | None) -> None:
        normalized = {str(project_id or "").strip() for project_id in (project_ids or set())}
        normalized.discard("")
        if self._init_repair_blocked_project_ids == normalized:
            return
        self._init_repair_blocked_project_ids = normalized
        self._render_signature = None
        if self._job is not None:
            self.set_job(self._job)

    def set_historical(self, historical: bool) -> None:
        value = bool(historical)
        if self._historical == value:
            return
        self._historical = value
        self.setProperty("historical", value)
        self.setProperty("compact", value)
        self.style().unpolish(self)
        self.style().polish(self)
        self._render_signature = None
        if self._job is not None:
            self.set_job(self._job)

    def set_job(self, job: DesktopJobRecord) -> None:
        signature = (
            *self._signature_for(job),
            self._historical,
            tuple(sorted(self._init_repair_blocked_project_ids)),
        )
        if signature == self._render_signature:
            return
        self._invalidate_pixmap_cache()
        old_status = self._job.status if self._job else None
        old_color_key = _card_color_key(self._job) if self._job else None
        self._job = job
        self._render_signature = signature
        self._meta_label = None
        clear_layout(self._layout)
        if self._historical:
            self._layout.setContentsMargins(14, 8, 14, 8)
        else:
            self._layout.setContentsMargins(16, 10, 16, 10)
        self._layout.setSpacing(4 if self._historical else 5)
        self.setMinimumHeight(132 if self._historical else _CARD_FIXED_HEIGHT)
        self._render(job)
        color_key = _card_color_key(job)
        if job.status != old_status or color_key != old_color_key:
            self._animate_status_color(color_key)

    @staticmethod
    def _elapsed_text(created_at: str) -> str:
        """Return 'MM:SS' or 'HH:MM:SS' elapsed since created_at."""
        try:
            start = datetime.fromisoformat(created_at)
            if start.tzinfo is None:
                start = start.replace(tzinfo=timezone.utc)
            secs = max(0, int((datetime.now(timezone.utc) - start).total_seconds()))
        except Exception:
            return ""
        h, rem = divmod(secs, 3600)
        m, s = divmod(rem, 60)
        if h:
            return f"{h}:{m:02d}:{s:02d}"
        return f"{m:02d}:{s:02d}"

    def _tick_elapsed(self) -> None:
        """Update the meta label in-place every second for running jobs."""
        if self._meta_label is None:
            return
        job = self._job
        if job is None:
            return
        elapsed = self._elapsed_text(job.created_at)
        text = f"{job.project_id or '自动项目'}  ·  已运行 {elapsed}"
        self._meta_label.setText(text)

    @staticmethod
    def _chapter_number_from(job: DesktopJobRecord) -> int:
        chapter_number = int((job.result or {}).get("chapter_number", 0) or 0)
        if not chapter_number:
            import re

            m = re.search(r"第\s*(\d+)\s*章", job.label)
            if m:
                chapter_number = int(m.group(1))
        return chapter_number

    def _artifact_context_for_job(self, job: DesktopJobRecord) -> tuple[Path | None, int]:
        project_dir = None
        chapter_number = 0
        if job.project_id and self._storage_root:
            project_dir = self._storage_root / job.project_id
        _chapter_kinds = {
            "run_chapter",
            "prepare_chapter",
            "resolve_chapter_checkpoint",
            "resolve_chapter_checkpoint_finalize",
            "tts_synthesize",
            "tts_full_pipeline",
            "tts_post_archive",
        }
        if job.kind in _chapter_kinds:
            chapter_number = self._chapter_number_from(job)
        return project_dir, chapter_number

    def _artifact_clickable_indices(
        self,
        job: DesktopJobRecord,
        steps: Sequence[PipelineStep],
    ) -> set[int]:
        project_dir, chapter_number = self._artifact_context_for_job(job)
        if project_dir is None:
            return set()
        return {
            index
            for index, step in enumerate(steps)
            if step_has_artifacts(
                kind=job.kind,
                step_key=step.key,
                project_dir=project_dir,
                chapter_number=chapter_number,
            )
        }

    def _build_action_bar(self, job: DesktopJobRecord) -> QWidget | None:
        """Build action bar with stop/resume/checkpoint-resume buttons."""
        chapter_kinds = {
            "run_chapter",
            "prepare_chapter",
            "resolve_chapter_checkpoint",
            "resolve_chapter_checkpoint_finalize",
        }
        buttons: list[ActionButton] = []

        if job.status in {DesktopJobState.RUNNING, DesktopJobState.QUEUED}:
            btn = ActionButton("⏹ 停止", variant="secondary")
            btn.setProperty("compact", True)
            btn.clicked.connect(lambda: self.stop_requested.emit(job.job_id))
            buttons.append(btn)

        if job.status == DesktopJobState.PAUSED:
            cn = self._chapter_number_from(job)
            pid = job.project_id
            btn = ActionButton("▶ 恢复", variant="primary")
            btn.setProperty("compact", True)
            btn.clicked.connect(
                lambda checked=False, jid=job.job_id, pid=pid, cn=cn: self.resume_requested.emit(
                    jid, pid, cn
                )
            )
            buttons.append(btn)

        if self._init_repair_actions_available(job):
            pid = job.project_id
            retry_btn = ActionButton("AI修复", variant="primary")
            retry_btn.setProperty("compact", True)
            retry_btn.setToolTip(_init_repair_retry_tooltip(job))
            retry_btn.clicked.connect(
                lambda checked=False, jid=job.job_id, pid=pid: self.init_retry_requested.emit(
                    jid, pid
                )
            )
            buttons.append(retry_btn)

            manual_btn = ActionButton("人工修复", variant="secondary")
            manual_btn.setProperty("compact", True)
            manual_btn.setToolTip("打开矛盾点与修复建议，手动编辑关联产物后再复审。")
            manual_btn.clicked.connect(
                lambda checked=False, jid=job.job_id, pid=pid: (
                    self.init_manual_repair_requested.emit(jid, pid)
                )
            )
            buttons.append(manual_btn)

        if job.status == DesktopJobState.FAILED and job.kind in chapter_kinds:
            cn = self._chapter_number_from(job)
            pid = job.project_id
            # Only show checkpoint button if review_progress.json exists on disk
            has_checkpoint = False
            if pid and cn and self._storage_root:
                ch = str(cn).zfill(3)
                progress_path = (
                    self._storage_root / pid / "states" / f"chapter_{ch}_review_progress.json"
                )
                has_checkpoint = progress_path.exists()
            if has_checkpoint:
                btn = ActionButton("🔄 断点续写", variant="primary")
                btn.setProperty("compact", True)
                btn.clicked.connect(
                    lambda checked=False, jid=job.job_id, pid=pid, cn=cn: (
                        self.checkpoint_resume_requested.emit(jid, pid, cn)
                    )
                )
                buttons.append(btn)

        if not buttons:
            return None

        host = QWidget()
        host.setObjectName("jobCardActionBar")
        host.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Maximum)
        layout = QHBoxLayout(host)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        for btn in buttons:
            layout.addWidget(btn)
        return host

    def _build_detail_panel(
        self,
        detail_lines: list[tuple[str, str]],
    ) -> QWidget:
        content = QWidget()
        content.setObjectName("jobCardDetailContent")
        content.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Maximum)
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(4)

        for text, object_name in detail_lines:
            label = QLabel(text)
            label.setObjectName(object_name)
            label.setWordWrap(True)
            label.setMinimumWidth(0)
            label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
            label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            label.setToolTip(text)
            content_layout.addWidget(label)

        return content

    def _show_run_insight(
        self,
        insight: dict[str, Any],
        efficiency: dict[str, Any],
    ) -> None:
        dialog = RunInsightDialog(insight, efficiency, parent=self.window())
        dialog.exec()

    def _build_run_insight_row(self, job: DesktopJobRecord) -> QWidget | None:
        transparency = project_run_transparency(job)
        insights = transparency.get("run_insights")
        efficiency = transparency.get("efficiency")
        if self._historical or not isinstance(insights, list) or not insights:
            return None
        bounded_efficiency = efficiency if isinstance(efficiency, dict) else {}
        host = QWidget()
        host.setObjectName("runInsightStrip")
        row = QHBoxLayout(host)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        for raw_insight in insights[:4]:
            if not isinstance(raw_insight, dict):
                continue
            insight = dict(raw_insight)
            status = str(insight.get("status") or "inactive")
            label = str(insight.get("label") or "运行状态")
            state_label = _RUN_INSIGHT_STATUS_LABELS.get(status, status)
            button = QPushButton(f"{label} · {state_label}")
            button.setObjectName("runInsightChip")
            button.setProperty("insightStatus", status)
            button.setMinimumWidth(0)
            button.setMaximumHeight(28)
            button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            button.setToolTip(str(insight.get("summary") or "点击查看详情"))
            button.clicked.connect(
                lambda checked=False, item=insight, metrics=dict(bounded_efficiency): (
                    self._show_run_insight(item, metrics)
                )
            )
            row.addWidget(button, 1)
        return host if row.count() else None

    def _render(self, job: DesktopJobRecord) -> None:
        top_row = QHBoxLayout()
        top_row.setSpacing(10)
        title = _ElidedLabel(_display_job_label(job))
        title.setObjectName("cardTitle")
        title.setProperty("compact", self._historical)
        title.style().unpolish(title)
        title.style().polish(title)
        top_row.addWidget(title, 1)

        for reason_text, reason_tone, reason_tooltip in _replan_reason_badge_specs(job):
            reason_badge = Badge(reason_text, tone=reason_tone)
            reason_badge.setToolTip(reason_tooltip)
            top_row.addWidget(reason_badge)

        badge_text, badge_tone = _job_badge_spec(job)
        badge = Badge(badge_text, tone=badge_tone)
        top_row.addWidget(badge)
        self._layout.addLayout(top_row)

        is_active = job.status in {DesktopJobState.RUNNING, DesktopJobState.QUEUED}
        if is_active:
            elapsed = self._elapsed_text(job.created_at)
            meta_text = f"{job.project_id or '自动项目'}  ·  已运行 {elapsed}"
        elif self._historical:
            meta_text = (
                f"{job.project_id or '自动项目'}  ·  历史记录  ·  {_fmt_local(job.updated_at)}"
            )
        else:
            meta_text = f"{job.project_id or '自动项目'}  ·  {_fmt_local(job.updated_at)}"
        meta = _ElidedLabel(meta_text)
        meta.setObjectName("cardMeta")
        self._layout.addWidget(meta)
        self._meta_label = meta
        if is_active:
            if not self._elapsed_timer.isActive():
                self._elapsed_timer.start()
        else:
            self._elapsed_timer.stop()

        percent = _compute_card_progress(job)
        bar_row = QHBoxLayout()
        bar_row.setSpacing(8)

        bar = QProgressBar()
        bar.setObjectName("jobProgress")
        bar.setRange(0, 100)
        bar.setValue(percent)
        bar.setTextVisible(False)
        bar.setFixedHeight(7)
        bar_row.addWidget(bar, 1)

        percent_label = QLabel(f"{percent}%")
        percent_label.setObjectName("jobProgressPct")
        percent_label.setFixedWidth(38)
        percent_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        bar_row.addWidget(percent_label)
        self._layout.addLayout(bar_row)

        insight_row = self._build_run_insight_row(job)
        if insight_row is not None:
            self._layout.addWidget(insight_row)

        steps = _visible_steps_for_kind(job.kind)
        show_indicator = bool(steps) and not self._historical
        if show_indicator:
            visible_keys = {step.key for step in steps}
            indicator = StepIndicatorRow(
                steps,
                parallel_pairs=_visible_parallel_pairs_for_kind(job.kind, visible_keys),
                clickable_indices=self._artifact_clickable_indices(job, steps),
            )
            indicator.step_clicked.connect(
                lambda idx, row=indicator: self._on_step_clicked(idx, row)
            )
            indicator.apply_state(_indicator_state_for_job(job, steps))
            indicator.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            self._layout.addWidget(indicator)

        detail_lines: list[tuple[str, str]] = []
        step_summary = _compact_step_summary(job)
        if step_summary:
            detail_lines.append((step_summary, "progressDetail"))

        resume_anchor_detail = _init_resume_anchor_detail(job)
        if resume_anchor_detail and not self._historical:
            detail_lines.append((resume_anchor_detail, "cardHint"))

        checkpoint_detail = _checkpoint_decision_detail(job)
        if checkpoint_detail:
            detail_lines.append((checkpoint_detail, "cardHint"))

        auxiliary_summary = _auxiliary_step_summary(job)
        if auxiliary_summary and not self._historical:
            detail_lines.append((auxiliary_summary, "cardMeta"))

        design_matrix_summary = _init_long_chapter_design_matrix_summary(job)
        if design_matrix_summary and not self._historical:
            detail_lines.append((design_matrix_summary, "cardMeta"))

        outline_generation_summary = _init_long_outline_generation_summary(job)
        if outline_generation_summary and not self._historical:
            detail_lines.append((outline_generation_summary, "cardMeta"))

        outline_claim_summary = _init_long_outline_claim_prefetch_summary(job)
        if outline_claim_summary and not self._historical:
            object_name = (
                "progressDetail" if _is_init_long_outline_claim_prefetch_active(job) else "cardMeta"
            )
            detail_lines.append((outline_claim_summary, object_name))

        phase_hint = _CHAPTER_FLOW_PHASE_HINTS.get(job.kind)
        if phase_hint and not self._historical:
            detail_lines.append((phase_hint, "cardHint"))

        if job.status in {DesktopJobState.RUNNING, DesktopJobState.PAUSED} and job.current_step:
            prefix = "等待处理" if job.status == DesktopJobState.PAUSED else "正在执行"
            rich_name = _active_display_step_name_for_job(job)
            detail_lines.append((f"{prefix}：{rich_name}", "progressDetail"))

        memory_stages = memory_stage_status_for_job(job)
        if memory_stages:
            parallel_running = (job.current_step or "") == "memory_concurrent_tasks_started"
            memory_object_name = (
                "progressDetail" if (job.current_step or "").startswith("memory_") else "cardMeta"
            )
            detail_lines.append(
                (
                    _memory_stage_summary(memory_stages, parallel_running=parallel_running),
                    memory_object_name,
                )
            )

        if _is_checkpoint_decision_result(job):
            metric_summary = _checkpoint_metric_summary(job)
            if metric_summary:
                detail_lines.append((metric_summary, "cardMeta"))
        elif job.status == DesktopJobState.SUCCEEDED and job.result:
            summary = summarize_job_result(job.result)
            if summary:
                score = job.result.get("overall_score")
                detail_lines.append(
                    (
                        summary,
                        "progressScore"
                        if (score is None or float(score) >= 7.0)
                        else "progressScoreFail",
                    )
                )
            warnings = _result_warnings(job.result)
            if warnings:
                detail_lines.append((f"提醒：{warnings[0]}", "memoryWarningMarker"))
            token_steps = _token_step_summary(job.result)
            if token_steps and job.kind in {"run_short", "run_chapter"}:
                detail_lines.append((f"步骤 Token：{token_steps}", "cardMeta"))

        memory_contexts = _stage_memory_context_payloads(job)
        if memory_contexts and not self._historical:
            detail_lines.append((_memory_context_summary(memory_contexts), "cardMeta"))

        format_retry_summary = _latest_format_retry_summary(job)
        if format_retry_summary:
            detail_lines.append((format_retry_summary, _latest_format_retry_object_name(job)))

        error_text, error_detail = task_flow_failure_text(job)
        if error_text:
            detail_lines.insert(0, (error_text, "dangerText"))
            if error_detail:
                detail_lines.insert(1, (error_detail, "cardHint"))

        if self._historical:
            detail_lines = detail_lines[:2]

        action_bar = self._build_action_bar(job)
        if detail_lines or action_bar:
            self._layout.addStretch(1)
            footer_row = QHBoxLayout()
            footer_row.setContentsMargins(0, 2, 0, 0)
            footer_row.setSpacing(12)
            if detail_lines:
                footer_row.addWidget(
                    self._build_detail_panel(detail_lines),
                    1,
                    Qt.AlignmentFlag.AlignBottom,
                )
            else:
                footer_row.addStretch(1)
            if action_bar:
                footer_row.addWidget(
                    action_bar,
                    0,
                    Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignBottom,
                )
            self._layout.addLayout(footer_row)

    def _on_step_clicked(self, index: int, indicator: StepIndicatorRow) -> None:
        job = self._job
        if job is None:
            return
        project_dir, chapter_number = self._artifact_context_for_job(job)
        nav_target = StepArtifactDialog.show_for_step(
            kind=job.kind,
            step_key=indicator.step_key(index),
            step_label=indicator.step_label(index),
            project_dir=project_dir,
            chapter_number=chapter_number,
            parent=self.window(),
        )
        if nav_target:
            win = self.window()
            if hasattr(win, "switch_page"):
                win.switch_page(nav_target)

    def shutdown(self) -> None:
        if getattr(self, "_shutdown_done", False):
            return
        self._shutdown_done = True
        from novel_forge.desktop.shutdown_utils import safe_disconnect, safe_stop_timer

        safe_stop_timer(self._elapsed_timer)
        safe_disconnect(self._elapsed_timer.timeout)


class ModeBar(QWidget):
    """Two large mutually-exclusive mode buttons."""

    mode_changed = Signal(str)

    # 200ms: matches the "toggle" preset in Motion.DURATIONS (spec).
    _FADE_IN_DURATION_MS = 200

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._suppress_fade = True
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        self._btn_short = self._make_btn("短篇创作", "一次提交 · 自动生成完整短篇", "short")
        self._btn_long = self._make_btn("长篇初始化", "分章推进 · 立项后去章台逐章续写", "long")

        layout.addWidget(self._btn_short, 1)
        layout.addWidget(self._btn_long, 1)
        self.select("short")
        self._suppress_fade = False

    def _make_btn(self, title: str, subtitle: str, mode_id: str) -> QPushButton:
        button = QPushButton()
        button.setObjectName("modeBtn")
        button.setCheckable(True)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setMinimumHeight(66)

        inner = QVBoxLayout(button)
        inner.setContentsMargins(14, 10, 14, 10)
        inner.setSpacing(3)

        title_label = QLabel(title)
        title_label.setObjectName("modeBtnTitle")
        title_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        inner.addWidget(title_label)

        subtitle_label = QLabel(subtitle)
        subtitle_label.setObjectName("modeBtnSub")
        subtitle_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        inner.addWidget(subtitle_label)

        button.clicked.connect(lambda checked=False, current=mode_id: self.select(current))
        return button

    def _fade_in_active(self, button: QPushButton) -> None:
        """Apply a 200ms opacity fade to the active mode button (D1-safe).

        Keeps a Python reference to the animation on the instance so
        callers (and tests) can introspect it without hitting a dangling
        ``widget.property("_motion_anim")`` after ``DeleteWhenStopped``
        reaps the C++ object.
        """
        try:
            from novel_forge.desktop.motion import Motion

            anim = Motion.fade_in(button, duration=self._FADE_IN_DURATION_MS)
            self._last_fade_anim = anim
        except Exception:  # noqa: BLE001 — animation is purely cosmetic
            return

    def select(self, mode: str) -> None:
        is_short = mode == "short"
        active_button: QPushButton | None = None
        for button, active in ((self._btn_short, is_short), (self._btn_long, not is_short)):
            button.setChecked(active)
            button.setProperty("selected", active)
            button.style().unpolish(button)
            button.style().polish(button)
            if active:
                active_button = button
        if active_button is not None and not self._suppress_fade:
            self._fade_in_active(active_button)
        self.mode_changed.emit(mode)

    def current_mode(self) -> str:
        return "short" if self._btn_short.isChecked() else "long"


class SubModeBar(QWidget):
    """Smaller toggle for long panel sub-modes — init only (chapter studio lives in 章台)."""

    mode_changed = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        self._btn_init = self._make_btn("长篇初始化", "init")
        layout.addWidget(self._btn_init, 1)
        layout.addStretch(3)
        self.select("init")

    def _make_btn(self, label: str, mode_id: str) -> QPushButton:
        button = QPushButton(label)
        button.setObjectName("subModeBtn")
        button.setCheckable(True)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setMinimumHeight(36)
        button.clicked.connect(lambda checked=False, current=mode_id: self.select(current))
        return button

    def select(self, mode: str) -> None:
        self._btn_init.setChecked(True)
        self._btn_init.setProperty("selected", True)
        self._btn_init.style().unpolish(self._btn_init)
        self._btn_init.style().polish(self._btn_init)
        self.mode_changed.emit("init")

    def select_chapter(self) -> None:
        pass  # chapter mode removed — redirect to 章台 page

    def current_mode(self) -> str:
        return "init"


class CancelJobDialog(QDialog):
    """确认停止任务的对话框。"""

    def __init__(
        self,
        job_label: str,
        current_step: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("errorLogDialog")
        self.setWindowTitle("确认停止任务")
        self.setModal(True)
        self.setMinimumSize(420, 220)

        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 24)
        root.setSpacing(12)

        title = QLabel("确认停止任务")
        title.setObjectName("dlgTitle")
        root.addWidget(title)

        info = QLabel(f"任务：{job_label}")
        info.setObjectName("dlgBody")
        info.setWordWrap(True)
        root.addWidget(info)

        step = QLabel(f"当前步骤：{current_step}")
        step.setObjectName("dlgBody")
        step.setWordWrap(True)
        root.addWidget(step)

        hint = QLabel("停止后可以从断点恢复")
        hint.setObjectName("dlgHint")
        hint.setWordWrap(True)
        root.addWidget(hint)

        root.addSpacing(8)

        btn_row = QHBoxLayout()
        btn_row.addStretch()

        cancel_btn = ActionButton("取消", variant="secondary")
        cancel_btn.setFixedHeight(36)
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)

        confirm_btn = ActionButton("确认停止", variant="danger")
        confirm_btn.setFixedHeight(36)
        confirm_btn.setDefault(True)
        confirm_btn.clicked.connect(self.accept)
        btn_row.addWidget(confirm_btn)

        root.addLayout(btn_row)


def show_stop_confirm_dialog(
    parent: QWidget | None,
    job_label: str,
    current_step: str,
) -> bool:
    """显示停止任务确认对话框，返回 True（确认）或 False（取消）。"""
    dialog = CancelJobDialog(job_label, current_step, parent)
    return dialog.exec() == QDialog.DialogCode.Accepted
