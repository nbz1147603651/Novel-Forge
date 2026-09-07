"""Checkpoint and pending-state helpers for chapter studio sessions."""

from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Union

from pydantic import BaseModel, Field, TypeAdapter
from typing_extensions import TypeAlias

from novel_forge.core.config import get_settings
from novel_forge.core.review.review_orchestration import build_chapter_review_matrix
from novel_forge.core.schemas.canon import CreativeReport
from novel_forge.core.schemas.chapter import (
    AlignmentReport,
    CausalValidationReport,
    ChapterOutcome,
    ChapterRepairReport,
    PlotGuardDecision,
)
from novel_forge.core.schemas.continuity import (
    ChapterBridge,
    ChapterPlan,
    ChapterStatePacket,
    ContinuityReport,
    RepairPlan,
)
from novel_forge.core.schemas.eval_schema import EvalReport
from novel_forge.core.schemas.reading_power import ReadingPowerReport
from novel_forge.core.schemas.review import RepairTicket, ReviewFinding
from novel_forge.core.schemas.story_state import ChapterExitState
from novel_forge.core.utils.string import carry_forward_text
from novel_forge.core.utils.text_validation import display_word_count
from novel_forge.persistence.models import ProjectLayout
from novel_forge.pipeline.long.preflight import LongProjectBundle
from novel_forge.workspace.contracts import DecisionCheckpoint, DecisionOption

_logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PendingChapterReviewState:
    current_text: str
    performed_edits: int
    outcome: ChapterOutcome
    alignment_report: AlignmentReport
    chapter_repair_report: ChapterRepairReport | None
    causal_report: CausalValidationReport | None
    continuity_report: ContinuityReport
    repair_plan: RepairPlan
    eval_report: EvalReport
    guard_decision: PlotGuardDecision | None
    warnings: tuple[str, ...]
    reading_power_report: ReadingPowerReport | None = None
    guard_compliance_report: dict[str, Any] | None = None
    review_findings: tuple[ReviewFinding, ...] = ()
    repair_tickets: tuple[RepairTicket, ...] = ()
    # Counts only post-guard repair executions.  It is persisted with the
    # checkpoint so restarting Desktop/API cannot reset the automatic-repair
    # budget and create an unbounded archive loop.
    archive_quality_repair_attempts: int = 0
    authoring_refined_text_hash: str = ""


class ReplanHistoryEntry(BaseModel):
    """A single replan attempt record for cross-run persistence."""

    timestamp: str = ""
    attempt_number: int = 0
    failed_stage: str = ""
    failure_kind: str = ""
    violations: list[str] = Field(default_factory=list)
    actionable_guidance: list[str] = Field(default_factory=list)


class BaseChapterSessionState(BaseModel):
    stage: Literal["plan_checkpoint", "guard_checkpoint"]
    checkpoint_id: str
    project_id: str
    chapter_number: int
    canon_watermark: int | None = None
    # max_edit_rounds 字段已下线：长篇 WAVE 阶段固定为单次连贯起稿，会话状态不再记录编辑轮次。
    notes: str = ""
    rewrite_strategy: str = "auto"
    writing_mode: str = "whole_chapter"
    introduced_characters: list[str] = Field(default_factory=list)
    replan_history: list[ReplanHistoryEntry] = Field(default_factory=list)


class PlanCheckpointSessionState(BaseChapterSessionState):
    stage: Literal["plan_checkpoint"] = "plan_checkpoint"
    trace_summary: dict[str, Any] = Field(default_factory=dict)


class GuardCheckpointSessionState(BaseChapterSessionState):
    stage: Literal["guard_checkpoint"] = "guard_checkpoint"
    pending_result: dict[str, Any] = Field(default_factory=dict)


ChapterSessionState: TypeAlias = Union[
    PlanCheckpointSessionState,
    GuardCheckpointSessionState,
]
_SESSION_STATE_ADAPTER: TypeAdapter[ChapterSessionState] = TypeAdapter(ChapterSessionState)


# ReviewProgressState and I/O helpers now live in core/schemas/review_state.py
# Re-exported here for backward compatibility with workspace-internal callers.
from novel_forge.core.schemas.review_state import (  # noqa: E402, F401
    ReviewProgressState,
    clear_review_progress,
    load_review_progress,
    save_review_progress,
)


def _artifact_rel(layout: ProjectLayout, path: Path) -> str:
    return str(path.relative_to(layout.root))


def checkpoint_file_paths(bundle: LongProjectBundle, chapter_number: int) -> dict[str, Path]:
    return {
        "checkpoint": bundle.layout.chapter_checkpoint_path(chapter_number),
        "session": bundle.layout.chapter_session_path(chapter_number),
        "review_draft": bundle.layout.chapter_review_draft_path(chapter_number),
    }


def load_checkpoint(bundle: LongProjectBundle, chapter_number: int) -> DecisionCheckpoint:
    path = bundle.layout.chapter_checkpoint_path(chapter_number)
    return DecisionCheckpoint.model_validate_json(path.read_text(encoding="utf-8"))


def load_session_state(
    bundle: LongProjectBundle,
    chapter_number: int,
) -> ChapterSessionState:
    path = bundle.layout.chapter_session_path(chapter_number)
    return _SESSION_STATE_ADAPTER.validate_json(path.read_text(encoding="utf-8"))


def save_plan_session_state(
    *,
    storage: Any,
    bundle: LongProjectBundle,
    chapter_number: int,
    checkpoint_id: str,
    project_id: str,
    canon_watermark: int,
    notes: str,
    trace_summary: dict[str, Any],
    rewrite_strategy: str = "auto",
    writing_mode: str = "whole_chapter",
    introduced_characters: list[str] | None = None,
    replan_history: list[ReplanHistoryEntry] | None = None,
) -> None:
    session_state = PlanCheckpointSessionState(
        checkpoint_id=checkpoint_id,
        project_id=project_id,
        chapter_number=chapter_number,
        canon_watermark=canon_watermark,
        notes=notes.strip(),
        rewrite_strategy=str(rewrite_strategy or "auto"),
        writing_mode="scene_level" if writing_mode == "scene_level" else "whole_chapter",
        introduced_characters=list(introduced_characters or []),
        trace_summary=trace_summary,
        replan_history=list(replan_history or []),
    )
    storage.save_json(
        bundle.layout.chapter_session_path(chapter_number),
        session_state.model_dump(mode="json"),
    )


def save_guard_session_state(
    *,
    storage: Any,
    bundle: LongProjectBundle,
    chapter_number: int,
    checkpoint_id: str,
    project_id: str,
    canon_watermark: int,
    notes: str,
    pending_state: PendingChapterReviewState,
    rewrite_strategy: str = "auto",
    writing_mode: str = "whole_chapter",
    introduced_characters: list[str] | None = None,
    replan_history: list[ReplanHistoryEntry] | None = None,
) -> None:
    session_state = GuardCheckpointSessionState(
        checkpoint_id=checkpoint_id,
        project_id=project_id,
        chapter_number=chapter_number,
        canon_watermark=canon_watermark,
        notes=notes.strip(),
        rewrite_strategy=str(rewrite_strategy or "auto"),
        writing_mode="scene_level" if writing_mode == "scene_level" else "whole_chapter",
        introduced_characters=list(introduced_characters or []),
        replan_history=list(replan_history or []),
        pending_result=serialize_pending_result(pending_state),
    )
    storage.save_json(
        bundle.layout.chapter_session_path(chapter_number),
        session_state.model_dump(mode="json"),
    )


def clear_session_state(bundle: LongProjectBundle, chapter_number: int) -> None:
    for path in checkpoint_file_paths(bundle, chapter_number).values():
        if path.exists():
            path.unlink()
    # Also clean up any stale review-progress checkpoint
    progress_path = bundle.layout.chapter_review_progress_path(chapter_number)
    if progress_path.exists():
        progress_path.unlink()


def summarize_chapter_exit_state(exit_state: ChapterExitState | None) -> str:
    if exit_state is None:
        return "本章暂无额外承接事项。"
    carry_forward = [
        carry_forward_text(item)
        for item in exit_state.must_carry_forward
        if carry_forward_text(item)
    ]
    if not carry_forward:
        return "本章暂无额外承接事项。"
    return "；".join(carry_forward[:3])


def summarize_exit_state(outcome: ChapterOutcome | None) -> str:
    """Unwrap a ChapterOutcome and summarize its chapter_exit_state.

    This is the only entry point that accepts a ChapterOutcome container;
    summarize_chapter_exit_state is kept strict on ChapterExitState so mypy
    can intercept accidental container/inner-field confusion.
    """
    if outcome is None:
        return summarize_chapter_exit_state(None)
    return summarize_chapter_exit_state(outcome.chapter_exit_state)


_AUTO_REPAIR_NOTE_BLOCK_RE = re.compile(
    r"【自动修复提示】[\s\S]*?(?=\n\s*\n|$)",
    re.IGNORECASE,
)


def _strip_auto_repair_notes(notes: str) -> str:
    """Remove system-generated replan hints from user-facing outline goals."""
    text = str(notes or "").strip()
    if not text:
        return ""

    cleaned = _AUTO_REPAIR_NOTE_BLOCK_RE.sub("", text)
    cleaned = re.sub(
        r"^-+\s*High-severity continuity issues remain after repair:.*$",
        "",
        cleaned,
        flags=re.IGNORECASE | re.MULTILINE,
    )
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return cleaned


def apply_notes_to_bundle(bundle: LongProjectBundle, notes: str) -> None:
    raw_note_text = str(notes or "").strip()
    if raw_note_text:
        outline_notes = str(getattr(bundle.chapter_outline, "notes", "") or "").strip()
        if raw_note_text not in outline_notes:
            bundle.chapter_outline.notes = (
                f"{outline_notes}\n{raw_note_text}".strip() if outline_notes else raw_note_text
            )

    note_text = _strip_auto_repair_notes(raw_note_text)
    if not note_text:
        return
    goal = bundle.chapter_outline.goal.strip()
    if note_text in goal:
        return

    # 如果 notes 包含连贯性问题反馈，提取并格式化为结构化因果约束
    if (
        "需注意以下连贯性问题" in note_text
        or "opening_causal_gap" in note_text
        or "event_without_cause" in note_text
    ):
        bundle.chapter_outline.goal = (
            f"{goal}\n\n⚠️ 因果链约束（上次生成遗留问题，本次必须避免）：\n{note_text}"
        ).strip()
    else:
        bundle.chapter_outline.goal = f"{goal}\n补充约束：{note_text}".strip()


def build_plan_checkpoint(
    bundle: LongProjectBundle,
    packet: ChapterStatePacket,
    bridge: ChapterBridge,
    plan: ChapterPlan,
    scene_plan_validation_report: dict[str, Any] | None = None,
) -> DecisionCheckpoint:
    carry = (
        "；".join(
            carry_forward_text(item)
            for item in packet.must_carry_forward[:3]
            if carry_forward_text(item)
        )
        if packet.must_carry_forward
        else "无"
    )
    summary = (
        f"{bundle.chapter_outline.title or f'第 {bundle.chapter_outline.chapter_number} 章'}\n"
        f"{bridge.bridge_summary or '桥接已生成，可继续写作。'}\n"
        f"需承接：{carry}\n"
        f"方案共 {len(plan.scene_intents)} 个场景意图。"
    )
    validation_blocked = scene_plan_validation_report is not None and not bool(
        scene_plan_validation_report.get("valid")
    )
    options = [
        DecisionOption(
            option_id="write_now",
            label="确认方案并写作",
            description="沿当前桥接与章节方案继续生成本章正文。",
            is_recommended=True,
        ),
        DecisionOption(
            option_id="edit_plan_and_write",
            label="编辑方案后写作",
            description="在工作台产物中直接修改方案内容，然后沿修改后的方案写作。",
        ),
        DecisionOption(
            option_id="regenerate_plan",
            label="重新生成方案",
            description="保留当前上下文，重新生成桥接与章节计划。",
        ),
        DecisionOption(
            option_id="regenerate_plan_with_notes",
            label="补充约束后重生",
            description="把你的补充要求带入，再重新生成桥接与章节计划。",
        ),
    ]
    prompt = "章节方案已备好。先确认方案，再决定是否继续写作。"
    if validation_blocked:
        validation_report = scene_plan_validation_report or {}
        issues = [
            str(item.get("message") or item.get("summary") or item.get("code") or "").strip()
            for item in list(validation_report.get("issues", []) or [])
            if isinstance(item, dict)
        ]
        issue_text = "；".join([item for item in issues if item][:3]) or "场景边界或依赖未通过验证"
        summary = f"{summary}\n场景级计划验证未通过：{issue_text}"
        prompt = "场景级计划验证未通过，当前不会进入草稿。请重新生成方案，或切回整章写作。"
        options = [
            DecisionOption(
                option_id="regenerate_plan",
                label="重新生成场景方案",
                description="保留当前上下文，重新生成场景计划并再次验证。",
                is_recommended=True,
            ),
            DecisionOption(
                option_id="regenerate_plan_with_notes",
                label="补充约束后重生",
                description="把你的补充要求带入，再重新生成场景计划。",
            ),
            DecisionOption(
                option_id="switch_to_whole_chapter",
                label="改用整章写作",
                description="切回普通整章写作模式，重新生成章节方案。",
            ),
        ]
    return DecisionCheckpoint(
        checkpoint_id=f"plan-{bundle.chapter_outline.chapter_number:03d}-{uuid.uuid4().hex[:8]}",
        checkpoint_type="plan_checkpoint",
        summary=summary,
        prompt=prompt,
        options=options,
        related_artifacts=[
            _artifact_rel(
                bundle.layout,
                bundle.layout.chapter_state_packet_path(bundle.chapter_outline.chapter_number),
            ),
            _artifact_rel(
                bundle.layout,
                bundle.layout.chapter_bridge_path(bundle.chapter_outline.chapter_number),
            ),
            _artifact_rel(
                bundle.layout,
                bundle.layout.chapter_plan_path(bundle.chapter_outline.chapter_number),
            ),
            _artifact_rel(
                bundle.layout,
                bundle.layout.plans_dir
                / f"chapter_{bundle.chapter_outline.chapter_number:03d}_scene_plan.json",
            ),
            _artifact_rel(
                bundle.layout,
                bundle.layout.reports_dir
                / f"chapter_{bundle.chapter_outline.chapter_number:03d}_scene_plan_validation.json",
            ),
        ],
    )


def build_guard_checkpoint(
    bundle: LongProjectBundle,
    chapter_number: int,
    *,
    current_text: str,
    alignment_report: AlignmentReport,
    continuity_report: ContinuityReport,
    eval_report: EvalReport,
    causal_report: CausalValidationReport | None,
    guard_decision: PlotGuardDecision | None,
    reading_power_report: ReadingPowerReport | None = None,
    chapter_repair_report: ChapterRepairReport | None = None,
    warnings: tuple[str, ...] = (),
    repair_tickets: tuple[RepairTicket, ...] = (),
) -> DecisionCheckpoint:
    settings = get_settings()
    review_matrix = build_chapter_review_matrix(
        current_text=current_text,
        alignment_report=alignment_report,
        continuity_report=continuity_report,
        eval_report=eval_report,
        causal_report=causal_report,
        reading_power_report=reading_power_report,
        chapter_repair_report=chapter_repair_report,
        guard_decision=guard_decision,
        warnings=warnings,
        repair_tickets=repair_tickets,
        must_fix_severity=getattr(settings, "repair_must_fix_severity", "critical"),
        alignment_threshold=float(getattr(settings, "long_alignment_threshold", 7.0) or 7.0),
        reading_power_threshold=float(
            getattr(settings, "long_reading_power_repair_threshold", 6.0) or 6.0
        ),
    )
    summary = (
        f"章节草稿已完成，当前 {display_word_count(current_text):,} 字。\n"
        f"{review_matrix.score_line}。\n"
        f"{review_matrix.decision.decision_line}".strip()
    )
    options = [
        DecisionOption(
            option_id="accept_and_finalize",
            label="接受并归档",
            description="不再额外调整，直接归档正文与 Canon。",
            is_recommended=review_matrix.decision.recommended_option == "accept_and_finalize",
        ),
        DecisionOption(
            option_id="apply_repairs_and_finalize",
            label="应用修复后归档",
            description=review_matrix.decision.repair_option_description,
            is_recommended=(
                review_matrix.decision.recommended_option == "apply_repairs_and_finalize"
            ),
        ),
        DecisionOption(
            option_id="adjust_outline_and_finalize",
            label="调整后续大纲",
            description="先调整后续大纲，再归档本章。",
            is_recommended=(
                review_matrix.decision.recommended_option == "adjust_outline_and_finalize"
            ),
        ),
        DecisionOption(
            option_id="pause_for_human",
            label="暂停人工处理",
            description="保留当前检查结果，稍后再回来决定。",
            is_recommended=review_matrix.decision.recommended_option == "pause_for_human",
        ),
    ]
    related_artifacts = [
        _artifact_rel(bundle.layout, bundle.layout.chapter_review_draft_path(chapter_number)),
        _artifact_rel(bundle.layout, bundle.layout.creative_report_path(chapter_number)),
        _artifact_rel(bundle.layout, bundle.layout.alignment_report_path(chapter_number)),
        _artifact_rel(bundle.layout, bundle.layout.chapter_causal_report_path(chapter_number)),
        _artifact_rel(bundle.layout, bundle.layout.continuity_report_path(chapter_number)),
        _artifact_rel(bundle.layout, bundle.layout.eval_report_path(chapter_number)),
        _artifact_rel(bundle.layout, bundle.layout.guard_report_path(chapter_number)),
    ]
    kb_path_fn = getattr(bundle.layout, "knowledge_boundary_report_path", None)
    if callable(kb_path_fn):
        related_artifacts.append(_artifact_rel(bundle.layout, kb_path_fn(chapter_number)))
    rp_path_fn = getattr(bundle.layout, "reading_power_report_path", None)
    if callable(rp_path_fn):
        related_artifacts.append(_artifact_rel(bundle.layout, rp_path_fn(chapter_number)))
    return DecisionCheckpoint(
        checkpoint_id=f"guard-{chapter_number:03d}-{uuid.uuid4().hex[:8]}",
        checkpoint_type="guard_checkpoint",
        summary=summary,
        prompt="章节正文、检查结果与 AI 护栏建议都已齐备。请选择下一步如何归档。",
        options=[
            option
            for option in options
            if option.option_id in review_matrix.decision.allowed_option_ids
        ],
        related_artifacts=related_artifacts,
    )


def serialize_pending_result(state: PendingChapterReviewState) -> dict[str, Any]:
    return {
        "current_text": state.current_text,
        "performed_edits": state.performed_edits,
        "outcome": state.outcome.model_dump(mode="json"),
        "alignment_report": state.alignment_report.model_dump(mode="json"),
        "chapter_repair_report": (
            state.chapter_repair_report.model_dump(mode="json")
            if state.chapter_repair_report is not None
            else None
        ),
        "causal_report": (
            state.causal_report.model_dump(mode="json") if state.causal_report is not None else None
        ),
        "continuity_report": state.continuity_report.model_dump(mode="json"),
        "repair_plan": state.repair_plan.model_dump(mode="json"),
        "eval_report": state.eval_report.model_dump(mode="json"),
        "reading_power_report": (
            state.reading_power_report.model_dump(mode="json")
            if state.reading_power_report is not None
            else None
        ),
        "guard_decision": (
            state.guard_decision.model_dump(mode="json")
            if state.guard_decision is not None
            else None
        ),
        "warnings": list(state.warnings),
        "guard_compliance_report": state.guard_compliance_report,
        "review_findings": [f.model_dump(mode="json") for f in (state.review_findings or ())],
        "repair_tickets": [t.model_dump(mode="json") for t in (state.repair_tickets or ())],
        "archive_quality_repair_attempts": state.archive_quality_repair_attempts,
        "authoring_refined_text_hash": state.authoring_refined_text_hash,
    }


def deserialize_pending_result(payload: dict[str, Any]) -> PendingChapterReviewState:
    return PendingChapterReviewState(
        authoring_refined_text_hash=str(payload.get("authoring_refined_text_hash") or ""),
        current_text=str(payload.get("current_text", "") or ""),
        performed_edits=int(payload.get("performed_edits", 0) or 0),
        outcome=ChapterOutcome.model_validate(payload.get("outcome") or {}),
        alignment_report=AlignmentReport.model_validate(payload.get("alignment_report") or {}),
        chapter_repair_report=(
            ChapterRepairReport.model_validate(payload["chapter_repair_report"])
            if payload.get("chapter_repair_report")
            else None
        ),
        causal_report=(
            CausalValidationReport.model_validate(payload["causal_report"])
            if payload.get("causal_report")
            else None
        ),
        continuity_report=ContinuityReport.model_validate(payload.get("continuity_report") or {}),
        repair_plan=RepairPlan.model_validate(payload.get("repair_plan") or {}),
        eval_report=EvalReport.model_validate(payload.get("eval_report") or {}),
        reading_power_report=(
            ReadingPowerReport.model_validate(payload["reading_power_report"])
            if payload.get("reading_power_report")
            else None
        ),
        guard_decision=(
            PlotGuardDecision.model_validate(payload["guard_decision"])
            if payload.get("guard_decision")
            else None
        ),
        warnings=tuple(str(item) for item in (payload.get("warnings") or []) if str(item).strip()),
        guard_compliance_report=(
            payload.get("guard_compliance_report")
            if isinstance(payload.get("guard_compliance_report"), dict)
            else None
        ),
        review_findings=tuple(
            ReviewFinding.model_validate(item)
            for item in (payload.get("review_findings") or [])
            if isinstance(item, dict)
        ),
        repair_tickets=tuple(
            RepairTicket.model_validate(item)
            for item in (payload.get("repair_tickets") or [])
            if isinstance(item, dict)
        ),
        archive_quality_repair_attempts=_coerce_nonnegative_int(
            payload.get("archive_quality_repair_attempts", 0)
        ),
    )


def _coerce_nonnegative_int(value: Any) -> int:
    """Read a legacy checkpoint counter without letting malformed data reset a run."""
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def load_creative_report_payload(
    creative_report: CreativeReport | None,
    *,
    storage: Any,
    layout: ProjectLayout,
    chapter_number: int,
    artifact_loader: Any | None = None,
) -> dict[str, Any]:
    path = layout.creative_report_path(chapter_number)
    if storage.exists(path):
        report_data_raw = (
            artifact_loader.load_json(path)
            if artifact_loader is not None
            else storage.load_json(path)
        )
        return report_data_raw if isinstance(report_data_raw, dict) else {}
    if creative_report is None:
        return {}
    return creative_report.model_dump(mode="json")
