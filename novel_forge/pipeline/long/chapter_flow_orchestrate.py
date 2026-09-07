"""Orchestration primitives, shared utilities, and planning entry points.

Extracted from ``chapter_flow.py`` during the Task-17 decomposition.
Contains shared helper functions used across generate/review/finalize stages,
plus ``prepare_chapter_plan``, ``load_prepared_chapter_artifacts``,
``execute_chapter_pipeline``, and the review-phase state carriers.
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
from typing import TYPE_CHECKING, Any

from novel_forge.common.constants import severity_at_least
from novel_forge.core.exceptions import (
    BLOCK_KIND_INPUT_INTEGRITY,
    ConsistencyViolationError,
    ModelGatewayError,
    RecoveryTarget,
)
from novel_forge.core.review.review_contracts import source_text_hash
from novel_forge.core.schemas.chapter import ChapterResult
from novel_forge.core.schemas.continuity import (
    ChapterBridge,
    ChapterPlan,
    ChapterStatePacket,
)
from novel_forge.core.schemas.reading_power import ReadingPowerReport
from novel_forge.core.schemas.review import ReviewFinding
from novel_forge.core.utils.coerce import coerce_float as _canonical_coerce_float
from novel_forge.core.utils.macro_guard_helpers import (
    load_macro_guard_adjustment_state,
    load_recent_audit_entries,
    should_trigger_macro_guard,
)
from novel_forge.core.utils.pipeline_helpers import (
    validate_threshold,
)
from novel_forge.core.utils.text_validation import count_chapter_words
from novel_forge.obs.tracer import PipelineTrace
from novel_forge.pipeline.long.canon_ops import persist_chapter_state_packet
from novel_forge.pipeline.long.execution_models import (
    ChapterExecutionContext,
    ChapterReviewArtifacts,
    FlowContextAdapter,
    PreparedChapterArtifacts,
)
from novel_forge.pipeline.long.preflight import LongProjectBundle
from novel_forge.pipeline.long.services.knowledge_boundary_audit import (
    run_knowledge_boundary_audit,
)
from novel_forge.pipeline.long.services.upstream_compass import (
    audit_outline_source_consistency,
)
from novel_forge.pipeline.long.services.upstream_compass import (
    blocking_messages as upstream_compass_blocking_messages,
)
from novel_forge.pipeline.long.stages.finalize_report import (
    evaluate_chapter_text,
    extract_and_validate,
)
from novel_forge.pipeline.long.stages.planning import generate_bridge_and_plan
from novel_forge.pipeline.long.stages.word_count import (
    clear_word_count_rejections,
    record_word_count_rejection,
    run_word_count_restructure,
    word_count_archive_gate_enabled,
    word_count_archive_gate_max_rejections,
    word_count_rejection_limit_reached,
)
from novel_forge.pipeline.steps.macro_guard_step import MacroGuardInput, MacroGuardStep

if TYPE_CHECKING:
    from novel_forge.core.schemas.review_state import ReviewProgressState

_logger = logging.getLogger(__name__)
_coerce_float = _canonical_coerce_float


def _make_on_step_with_tokens(
    on_step: Any,
    trace: PipelineTrace,
) -> Any:
    """Wrap an on_step callback to inject ``tokens_so_far`` into every payload.

    The wrapper reads ``trace.total_tokens`` at call time so that each event
    carries the cumulative token count up to that point in the pipeline.
    """

    def _wrapped(event: str, payload: dict[str, Any]) -> None:
        payload = dict(payload) if payload else {}
        payload["tokens_so_far"] = getattr(trace, "total_tokens", 0)
        on_step(event, payload)

    return _wrapped


def _coerce_reading_power_report(payload: Any) -> ReadingPowerReport | None:
    """Build a ReadingPowerReport from a persisted payload with local metadata stripped."""
    if payload is None:
        return None
    if isinstance(payload, ReadingPowerReport):
        return payload
    if not isinstance(payload, dict):
        return None

    model_fields = getattr(ReadingPowerReport, "model_fields", {})
    report_payload = {key: value for key, value in payload.items() if key in model_fields}
    try:
        return ReadingPowerReport.model_validate(report_payload)
    except Exception as exc:
        _logger.debug("reading_power_report_restore_failed | error=%s", exc)
        return None


def _alignment_meets_threshold(score: float, threshold: float) -> bool:
    """Check if alignment score meets threshold."""
    return validate_threshold(score, threshold)


def _schedule_embedding_prefetch(
    context: ChapterExecutionContext,
    plan: Any,
    chapter_number: int,
) -> asyncio.Task[None] | None:
    """Fire-and-forget: pre-generate embeddings for Draft-stage memory queries.

    Uses scene summaries from the finalized plan as likely search queries.
    Failures are logged but never propagate to the caller.
    """
    settings = getattr(context, "settings", None)
    if not bool(getattr(settings, "long_perf_embedding_prefetch_enabled", True)):
        return None
    memory_ctx = getattr(context, "memory_context", None)
    if memory_ctx is None:
        return None
    episodic = getattr(memory_ctx, "episodic_memory", None)
    if episodic is None:
        return None
    scene_intents = list(getattr(plan, "scene_intents", []) or [])
    if not scene_intents:
        return None
    queries = [
        str(getattr(scene, "summary", "") or "").strip()
        for scene in scene_intents[:8]  # cap to avoid excessive batch size
    ]
    queries = [q for q in queries if q]
    if not queries:
        return None

    async def _prefetch() -> None:
        try:
            generate_batch = getattr(episodic, "_generate_embeddings_batch", None)
            if callable(generate_batch):
                await generate_batch(queries)
                _logger.debug(
                    "embedding_prefetch_done | chapter=%d | queries=%d",
                    chapter_number,
                    len(queries),
                )
        except Exception as exc:
            _logger.debug(
                "embedding_prefetch_failed | chapter=%d | error=%s",
                chapter_number,
                exc,
            )

    try:
        return asyncio.get_event_loop().create_task(_prefetch())
    except RuntimeError:
        return None


async def _persist_stage_artifact_safely(
    *,
    context: ChapterExecutionContext,
    bundle: Any,
    chapter_number: int,
    artifact_type: str,
    payload: dict[str, Any],
    previous_artifact_type: str | None = None,
    event_ledger: list[dict[str, Any]] | None = None,
) -> None:
    """Persist a chapter stage artifact without changing the orchestration outcome.

    I/O is offloaded to a worker thread to avoid blocking the event loop.
    """

    def _sync_persist() -> None:
        from novel_forge.pipeline.long.services.context.source_artifacts import (
            load_stage_artifact,
            persist_stage_artifact,
        )

        previous_artifact = (
            load_stage_artifact(
                context.storage,
                bundle.layout,
                chapter_number=chapter_number,
                artifact_type=previous_artifact_type,
            )
            if previous_artifact_type
            else None
        )
        persist_stage_artifact(
            storage=context.storage,
            layout=bundle.layout,
            project_id=getattr(bundle, "project_id", "") or "unknown",
            chapter_number=chapter_number,
            artifact_type=artifact_type,
            payload=payload,
            chapter_source_slice=getattr(bundle, "chapter_source_slice", None),
            previous_artifact=previous_artifact,
            event_ledger=event_ledger,
        )

    try:
        await asyncio.to_thread(_sync_persist)
    except Exception as exc:
        _logger.warning(
            "stage_artifact_persist_failed | chapter=%d | artifact=%s | error=%s",
            chapter_number,
            artifact_type,
            exc,
        )


def _reading_power_is_fallback(report: Any) -> bool:
    return bool(
        getattr(report, "is_fallback", False)
        or str(getattr(report, "evaluation_status", "") or "").strip().lower() == "fallback"
    )


def _reading_power_archive_block_messages(
    review: ChapterReviewArtifacts,
    *,
    settings: Any,
    chapter_number: int,
) -> list[str]:
    """Return hard archive-block messages for the configured reader-pull policy."""
    report = getattr(review, "reading_power_report", None)
    if report is None or _reading_power_is_fallback(report):
        return []

    policy = str(
        getattr(settings, "long_reading_power_archive_policy", "floor_only") or "floor_only"
    ).strip()
    if policy not in {"off", "floor_only", "floor_or_core_high"}:
        policy = "floor_only"
    if policy == "off":
        return []

    messages: list[str] = []
    score = _coerce_float(getattr(report, "overall_score", 10.0), 10.0)
    hard_floor = _coerce_float(
        getattr(settings, "long_reading_power_hard_block_threshold", 3.0),
        3.0,
    )
    if hard_floor > 0.0 and score < hard_floor:
        messages.append(
            f"章节 {chapter_number} 追读力分 {score:.1f} 低于硬阻断线 {hard_floor:.1f}，"
            "拒绝保存并强制重新规划。"
        )

    if policy != "floor_or_core_high":
        return messages

    block_types = {
        str(item or "").strip().lower()
        for item in list(
            getattr(
                settings,
                "long_reading_power_archive_block_issue_types",
                ["hook_missing", "prev_hook_unfulfilled"],
            )
            or []
        )
        if str(item or "").strip()
    }
    if not block_types:
        return messages

    try:
        from novel_forge.core.schemas.reading_power_repair import _build_reading_power_issues

        blocking_issues = [
            issue
            for issue in _build_reading_power_issues(report)
            if str(getattr(issue, "issue_type", "") or "").strip().lower() in block_types
            and severity_at_least(str(getattr(issue, "severity", "medium") or "medium"), "high")
        ]
    except Exception as exc:
        _logger.debug(
            "reading_power_archive_issue_projection_failed | chapter=%d | error=%s",
            chapter_number,
            exc,
        )
        blocking_issues = []

    if blocking_issues:
        summaries = "；".join(
            str(getattr(issue, "summary", "") or "") for issue in blocking_issues[:3]
        )
        messages.append(
            f"章节 {chapter_number} 仍存在核心追读力阻断问题：{summaries or '未命名问题'}。"
        )
    return messages


def _guard_archive_block_messages(
    review: ChapterReviewArtifacts,
    *,
    settings: Any,
    chapter_number: int,
    on_step: Any | None = None,
) -> list[str]:
    """Return hard archive-block messages for configured guard-compliance policy."""
    policy = str(getattr(settings, "long_guard_archive_policy", "warn") or "warn").strip()
    if policy != "block_actionable":
        return []

    report = getattr(review, "guard_compliance_report", None)
    if not isinstance(report, dict):
        return []
    expected_hash = source_text_hash(getattr(review, "current_text", "") or "")
    report_hash = str(report.get("source_text_hash") or report.get("_text_hash") or "").strip()
    if expected_hash and report_hash and report_hash != expected_hash:
        if callable(on_step):
            on_step(
                "guard_report_stale_skipped",
                {
                    "chapter": chapter_number,
                    "stored_hash": report_hash,
                    "current_hash": expected_hash,
                    "policy": policy,
                },
            )
        return []

    block_statuses = {
        str(item or "").strip().lower().replace("-", "_")
        for item in list(
            getattr(settings, "long_guard_archive_block_statuses", ["non_compliant"]) or []
        )
        if str(item or "").strip()
    }
    if not block_statuses:
        return []
    min_confidence = _coerce_float(
        getattr(settings, "long_guard_archive_block_min_confidence", 0.8),
        0.8,
    )
    blockers: list[dict[str, Any]] = []
    for raw in list(report.get("compliance_results") or []):
        if not isinstance(raw, dict):
            continue
        status = str(raw.get("status", "") or "").strip().lower().replace("-", "_")
        if status not in block_statuses:
            continue
        if raw.get("repairable") is False:
            continue
        confidence = _coerce_float(raw.get("confidence"), 0.0)
        if confidence < min_confidence:
            continue
        blockers.append(raw)

    if not blockers:
        return []
    snippets = "；".join(
        str(item.get("constraint", "") or item.get("notes", "") or "未命名护栏")[:80]
        for item in blockers[:3]
    )
    return [f"章节 {chapter_number} 存在 {len(blockers)} 条高置信 AI 护栏违约：{snippets}。"]


def _archive_policy_block_messages(
    review: ChapterReviewArtifacts,
    *,
    settings: Any,
    chapter_number: int,
    on_step: Any | None = None,
) -> list[str]:
    """Collect optional, policy-driven archive blockers beyond structural hard gates."""
    return [
        *_reading_power_archive_block_messages(
            review,
            settings=settings,
            chapter_number=chapter_number,
        ),
        *_guard_archive_block_messages(
            review,
            settings=settings,
            chapter_number=chapter_number,
            on_step=on_step,
        ),
    ]


def _should_include_pre_final_evaluation(settings: Any) -> bool:
    """Run pre-final eval whenever it can affect polish decisions."""
    if bool(getattr(settings, "long_polish_enabled", False)):
        return True
    auto_threshold = _coerce_float(
        getattr(settings, "long_polish_auto_trigger_threshold", 7.0),
        0.0,
    )
    return auto_threshold > 0.0


def _save_reading_power_next_chapter_constraints(
    context: ChapterExecutionContext,
    prepared: PreparedChapterArtifacts,
    *,
    chapter_number: int,
) -> None:
    """Persist reader-pull timeline state only after the current chapter is accepted."""
    if prepared.window_manager is None:
        return
    bundle = prepared.bundle
    try:
        wm = prepared.window_manager
        next_constraints = wm.get_next_chapter_constraints()
        if next_constraints:
            next_chapter = chapter_number + 1
            constraints_path = (
                bundle.layout.root / "states" / f"reading_power_constraints_ch{next_chapter}.json"
            )
            context.storage.save_json(constraints_path, next_constraints)
            context.on_step(
                "reading_power_next_chapter_constraints",
                {"chapter": chapter_number, "next_chapter": next_chapter},
            )
        wm.save_timeline_state()
    except Exception as exc:
        _logger.warning(
            "reading_power_window_finalize_failed | chapter=%d | error=%s",
            chapter_number,
            exc,
        )


def _load_reading_power_report_for_text(
    *,
    storage: Any,
    layout: Any,
    chapter_number: int,
    current_text: str,
) -> tuple[ReadingPowerReport | None, dict[str, Any] | None]:
    """Load the persisted reading-power report only if it matches current_text."""
    path_fn = getattr(layout, "reading_power_report_path", None)
    if not callable(path_fn):
        return None, None

    path = path_fn(chapter_number)
    exists = getattr(storage, "exists", None)
    try:
        if callable(exists) and not exists(path):
            return None, None
        payload = storage.load_json(path)
    except Exception as exc:
        _logger.debug(
            "reading_power_report_load_failed | chapter=%d | error=%s",
            chapter_number,
            exc,
        )
        return None, None

    if not isinstance(payload, dict):
        return None, None

    from novel_forge.core.utils.text_hash import source_text_hash

    if payload.get("source_text_hash") != source_text_hash(current_text):
        return None, None
    return _coerce_reading_power_report(payload), payload


def _get_field(source: Any, key: str, default: Any = None) -> Any:
    """Read a field from dict/object uniformly."""
    if source is None:
        return default
    if isinstance(source, dict):
        return source.get(key, default)
    return getattr(source, key, default)


def _clean_text(value: Any) -> str:
    # Structured carry-forward items project their text under ``text``.
    text_attr = getattr(value, "text", None)
    if text_attr is not None and not isinstance(value, (str, bytes)):
        value = text_attr
    elif isinstance(value, dict) and ("text" in value or "item" in value):
        value = value.get("text") or value.get("item")
    return str(value or "").strip()


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, set):
        return list(value)
    if isinstance(value, str):
        text = value.strip()
        return [text] if text else []
    return []


def _load_report_for_text_hash(
    storage: Any,
    path: Any,
    model_cls: Any,
    expected_hash: str,
) -> Any | None:
    """Load a persisted report only when it is explicitly bound to *expected_hash*."""
    try:
        if not path.exists():
            return None
        payload = storage.load_json(path)
        if not isinstance(payload, dict):
            return None
        report_hash = str(payload.get("source_text_hash", "") or "").strip()
        if not expected_hash or report_hash != expected_hash:
            return None
        return model_cls.model_validate(payload)
    except Exception:
        return None


async def _run_knowledge_boundary_verification(
    context: ChapterExecutionContext,
    prepared: PreparedChapterArtifacts,
    current_text: str,
    chapter_number: int,
) -> list[ReviewFinding]:
    """Run advisory knowledge-boundary checks against the final review text."""
    return await run_knowledge_boundary_audit(
        runner=context,
        storage=context.storage,
        bundle=prepared.bundle,
        packet=prepared.packet,
        current_text=current_text,
        chapter_number=chapter_number,
        stage="review_finalize",
        block_high_confidence=True,
    )


def _emit_input_integrity_check(
    context: ChapterExecutionContext,
    *,
    stage: str,
    chapter_number: int,
    bundle: Any | None = None,
    packet: Any | None = None,
    bridge: Any | None = None,
    plan: Any | None = None,
    chapter_text: str = "",
    block_on_error: bool = False,
) -> dict[str, Any]:
    """Emit a structured input-completeness report and optionally fail closed.

    Local code may verify whether required source fields exist, but it must not
    invent narrative content to fill them.  Missing fields are classified by
    their owning artifact before a recovery target is selected:

    - source-owned fields require an upstream/manual correction;
    - Bridge- and Plan-owned fields may regenerate Bridge + Plan.

    A mixed failure is deliberately manual.  Regenerating a plan cannot repair
    a broken outline, canon packet, or other source artifact.
    """
    missing_required: list[str] = []
    missing_by_owner: dict[str, list[str]] = {
        "source": [],
        "bridge": [],
        "plan": [],
    }
    warnings: list[str] = []
    stats: dict[str, Any] = {}

    def add_missing(field_name: str, *, owner: str) -> None:
        missing_required.append(field_name)
        missing_by_owner[owner].append(field_name)

    outline = _get_field(bundle, "chapter_outline", None)
    pov = _clean_text(_get_field(outline, "pov_character", ""))
    goal = _clean_text(_get_field(outline, "goal", ""))
    setting = _clean_text(_get_field(outline, "setting", ""))
    involved_characters = _as_list(_get_field(outline, "involved_characters", []))
    involved_characters = [_clean_text(name) for name in involved_characters if _clean_text(name)]
    stats["outline_involved_characters"] = len(involved_characters)
    if not pov:
        add_missing("chapter_outline.pov_character", owner="source")
    if not goal:
        add_missing("chapter_outline.goal", owner="source")
    if not setting:
        warnings.append("chapter_outline.setting 为空，场景锚点可能不足。")
    if not involved_characters:
        warnings.append("chapter_outline.involved_characters 为空，角色约束可能过弱。")

    known_characters = _as_list(_get_field(packet, "known_characters", []))
    known_characters = [_clean_text(name) for name in known_characters if _clean_text(name)]
    must_carry_forward = _as_list(_get_field(packet, "must_carry_forward", []))
    must_carry_forward = [_clean_text(item) for item in must_carry_forward if _clean_text(item)]
    canon_context = _get_field(packet, "canon_context", {})
    canon_characters = (
        _get_field(canon_context, "characters", {}) if isinstance(canon_context, dict) else {}
    )
    canon_character_count = len(canon_characters) if isinstance(canon_characters, dict) else 0
    stats["packet_known_characters"] = len(known_characters)
    stats["packet_must_carry_forward"] = len(must_carry_forward)
    stats["packet_canon_characters"] = canon_character_count
    if packet is not None and not known_characters:
        warnings.append("chapter_state_packet.known_characters 为空，后续角色追踪可能降级。")
    if packet is not None and canon_character_count == 0:
        warnings.append("chapter_state_packet.canon_context.characters 为空，角色状态约束弱。")

    if bridge is not None:
        bridge_summary = _clean_text(_get_field(bridge, "bridge_summary", ""))
        opening_location = _clean_text(_get_field(bridge, "opening_location", ""))
        action_handoff = _clean_text(_get_field(bridge, "action_handoff", ""))
        stats["bridge_pending_questions"] = len(
            _as_list(_get_field(bridge, "pending_questions", []))
        )
        if not bridge_summary:
            add_missing("chapter_bridge.bridge_summary", owner="bridge")
        if not opening_location:
            warnings.append("chapter_bridge.opening_location 为空，开场空间锚点可能不足。")
        if not action_handoff:
            warnings.append("chapter_bridge.action_handoff 为空，章节接力可能变弱。")

    scene_intents = _as_list(_get_field(plan, "scene_intents", []))
    stats["plan_scene_count"] = len(scene_intents)
    if plan is not None:
        opening_contract = _clean_text(_get_field(plan, "opening_contract", ""))
        closing_contract = _clean_text(_get_field(plan, "closing_contract", ""))
        required_transitions = [
            _clean_text(item)
            for item in _as_list(_get_field(plan, "required_state_transitions", []))
            if _clean_text(item)
        ]
        stats["plan_required_state_transitions"] = len(required_transitions)
        if not scene_intents:
            add_missing("chapter_plan.scene_intents", owner="plan")
        if not opening_contract:
            add_missing("chapter_plan.opening_contract", owner="plan")
        if not closing_contract:
            add_missing("chapter_plan.closing_contract", owner="plan")
        if not required_transitions:
            warnings.append("chapter_plan.required_state_transitions 为空，状态推进约束偏弱。")

        missing_scene_characters: list[str] = []
        missing_scene_locations = 0
        for index, scene in enumerate(scene_intents, start=1):
            scene_id = _clean_text(_get_field(scene, "scene_id", "")) or f"scene_{index:02d}"
            req_chars = [
                _clean_text(name)
                for name in _as_list(_get_field(scene, "required_characters", []))
                if _clean_text(name)
            ]
            if not req_chars:
                missing_scene_characters.append(scene_id)
            if not _clean_text(_get_field(scene, "location", "")):
                missing_scene_locations += 1
        stats["plan_scenes_missing_required_characters"] = len(missing_scene_characters)
        stats["plan_scenes_missing_location"] = missing_scene_locations
        if missing_scene_characters:
            preview = "、".join(missing_scene_characters[:3])
            warnings.append(
                f"scene_intents.required_characters 存在空值（{len(missing_scene_characters)} 个场景，如 {preview}）。"
            )

    text_non_ws = count_chapter_words(str(chapter_text or ""))
    if chapter_text:
        stats["text_chars_non_ws"] = text_non_ws
        if text_non_ws < 400:
            warnings.append("chapter_text 有效长度较短，质检与抽取稳定性可能下降。")

    status = "ok"
    if missing_required:
        status = "error"
    elif warnings:
        status = "warn"

    recovery_target = RecoveryTarget.MANUAL
    if missing_required and not missing_by_owner["source"]:
        recovery_target = RecoveryTarget.PLAN

    payload = {
        "chapter": chapter_number,
        "stage": stage,
        "status": status,
        "missing_required": missing_required,
        "missing_by_owner": missing_by_owner,
        "recovery_target": recovery_target.value,
        "warnings": warnings,
        "stats": stats,
    }
    if status == "ok":
        _logger.info(
            "input_integrity_check | chapter=%d | stage=%s | status=ok",
            chapter_number,
            stage,
        )
    else:
        _logger.warning(
            "input_integrity_check | chapter=%d | stage=%s | status=%s | missing=%s | warnings=%s",
            chapter_number,
            stage,
            status,
            missing_required,
            warnings[:5],
        )
    context.on_step("input_integrity_check", payload)
    if block_on_error and missing_required:
        violations = [f"{stage} 缺少必填输入 {field_name}" for field_name in missing_required]
        raise ConsistencyViolationError(
            violations,
            violation_kind="input_contract",
            failed_stage=stage,
            replan_target=recovery_target,
            block_kind=BLOCK_KIND_INPUT_INTEGRITY,
        )
    return payload


# Threshold constants: now owned by decisions.RepairThresholds (built from Settings).
# These legacy constants remain only as ultimate fallbacks for _change_ratio().
_CHANGE_BUDGET_THRESHOLD_DEFAULT = 0.15


async def prepare_chapter_plan(
    context: ChapterExecutionContext,
    *,
    bundle: LongProjectBundle,
    chapter_number: int,
    trace: PipelineTrace,
    memory: Any,
) -> PreparedChapterArtifacts:
    """Build chapter context, bridge, and plan artifacts."""
    _run_upstream_source_preflight(
        context,
        bundle=bundle,
        chapter_number=chapter_number,
    )
    # 加载叙事蓝图（若存在），注入叙事坐标
    blueprint_data: dict[str, Any] | None = None
    if context.storage.exists(bundle.layout.blueprint_path):
        blueprint_data = context.storage.load_json(bundle.layout.blueprint_path)

    packet = await memory.build_packet(
        layout=bundle.layout,
        canon_state=bundle.canon_state,
        chapter_number=chapter_number,
        chapter_outline=bundle.chapter_outline,
        current_volume=bundle.current_volume,
        character_bible=bundle.character_bible,
        character_profile_selector=context.select_character_profiles,
        previous_report_compactor=context.compact_previous_creative_report,
        blueprint_data=blueprint_data,
        world_setting_brief=bundle.world_setting_brief,
        story_bible_rules=list(bundle.story_bible.rules) if bundle.story_bible.rules else None,
        overused_vocabulary_window=getattr(context.settings, "long_overused_vocabulary_window", 5),
        previous_ending_min_chars=getattr(context.settings, "long_previous_ending_min_chars", 600),
        previous_ending_max_chars=getattr(context.settings, "long_previous_ending_max_chars", 1500),
        previous_ending_tail_paragraphs=getattr(
            context.settings,
            "long_boundary_prev_tail_paragraphs",
            5,
        ),
    )
    _emit_input_integrity_check(
        context,
        stage="planning_input",
        chapter_number=chapter_number,
        bundle=bundle,
        packet=packet,
        block_on_error=True,
    )
    await asyncio.to_thread(
        persist_chapter_state_packet, context.storage, bundle.layout, chapter_number, packet
    )
    context.on_step("state_packet", packet)

    with trace.step("context_compress"):
        compress_stats = await context.compress_prompt_context(
            packet.previous_creative_report,
            packet.character_profiles,
            packet=packet,
        )
    if compress_stats.get("candidates", 0) > 0:
        context.on_step("context_compress", compress_stats)

    window_manager, window_config = _initialize_reading_power_runtime(
        context,
        bundle=bundle,
        chapter_number=chapter_number,
        blueprint_data=blueprint_data,
    )

    bridge, plan, memory_hints, scene_plan_validation_report = await generate_bridge_and_plan(
        context,
        bundle,
        packet,
        chapter_number,
        trace,
        window_manager=window_manager,
        window_config=window_config,
    )

    # ── Embedding prefetch: warm the cache for Draft-stage memory retrieval ──
    # Scene summaries from the finalized plan will likely be used as semantic
    # search queries during the Draft stage's StageMemoryBuilder.  Pre-generating
    # their embeddings here (fire-and-forget) reduces Draft-stage latency.
    _prefetch_embedding_task = _schedule_embedding_prefetch(context, plan, chapter_number)

    prepared = PreparedChapterArtifacts(
        bundle=bundle,
        packet=packet,
        bridge=bridge,
        plan=plan,
        memory_hints=memory_hints,
        scene_plan_validation_report=scene_plan_validation_report,
        window_manager=window_manager,
        window_config=window_config,
    )
    return await rehydrate_prepared_runtime_context(
        context,
        prepared=prepared,
        chapter_number=chapter_number,
    )


def _run_upstream_source_preflight(
    context: ChapterExecutionContext,
    *,
    bundle: LongProjectBundle,
    chapter_number: int,
) -> dict[str, Any] | None:
    """Fail before packet compression or provider calls when the source conflicts."""

    _run_semantic_source_preflight(
        context,
        bundle=bundle,
        chapter_number=chapter_number,
    )

    if not bool(getattr(context.settings, "long_upstream_compass_enabled", True)):
        return None
    report = audit_outline_source_consistency(
        outline=bundle.chapter_outline,
        chapter_number=chapter_number,
        blocking_enabled=bool(
            getattr(context.settings, "long_upstream_compass_blocking", True)
        ),
    )
    payload = report.model_dump()
    payload["phase"] = "source_preflight"
    try:
        context.storage.save_json(
            bundle.layout.reports_dir / f"chapter_{chapter_number:03d}_upstream_compass.json",
            payload,
        )
    except OSError as exc:
        _logger.warning(
            "upstream_source_preflight_report_save_failed | chapter=%d | error=%s",
            chapter_number,
            exc,
        )
    context.on_step("upstream_source_preflight", payload)
    if report.blocking:
        messages = upstream_compass_blocking_messages(report) or [
            f"章节 {chapter_number} 上游大纲存在内部冲突。"
        ]
        raise ConsistencyViolationError(
            messages,
            violation_kind="upstream_source_conflict",
            failed_stage="upstream_source_preflight",
            replan_target=RecoveryTarget.MANUAL,
        )
    return payload


def _run_semantic_source_preflight(
    context: ChapterExecutionContext,
    *,
    bundle: LongProjectBundle,
    chapter_number: int,
) -> dict[str, Any] | None:
    """Read the existing compiler projection without making a provider call."""

    if not bool(getattr(context.settings, "long_semantic_consistency_enabled", True)):
        return None
    from novel_forge.persistence.authoring_store import AuthoringStore
    from novel_forge.persistence.semantic_consistency import semantic_consistency_view
    from novel_forge.pipeline.long.services.init.init_coherence_v2 import (
        semantic_compiler_runtime_fingerprint,
    )

    project_root = getattr(bundle.layout, "root", None)
    if project_root is None or AuthoringStore(project_root).policy() is None:
        return None
    projection = semantic_consistency_view(
        context.storage,
        bundle.layout,
        expected_runtime_fingerprint=semantic_compiler_runtime_fingerprint(context),
    )
    payload = projection.model_dump(mode="json")
    payload.update(chapter=chapter_number, phase="semantic_source_preflight")
    if projection.status != "clean" and not bool(
        getattr(context.settings, "long_semantic_consistency_blocking", False)
    ):
        payload["shadow_only"] = True
    context.on_step("semantic_source_preflight", payload)
    if projection.status == "clean":
        return payload
    if not bool(getattr(context.settings, "long_semantic_consistency_blocking", False)):
        return payload
    if projection.status == "conflict":
        violation_kind = "upstream_source_conflict"
        message = "上游来源存在已裁决的明确语义冲突；请在修复工作台修订并审批来源。"
    elif projection.status == "review_required":
        violation_kind = "upstream_source_review_required"
        message = "上游语义证据不足或存在歧义；请由作者确认兼容性或权威含义。"
    else:
        violation_kind = "upstream_semantic_not_ready"
        message = "上游语义编译缺失、过期或失败；请先刷新语义一致性。"
    raise ConsistencyViolationError(
        [f"{message} {projection.reason}".strip()],
        violation_kind=violation_kind,
        failed_stage="semantic_source_preflight",
        replan_target=RecoveryTarget.SEMANTIC,
    )


def _initialize_reading_power_runtime(
    context: ChapterExecutionContext,
    *,
    bundle: LongProjectBundle,
    chapter_number: int,
    blueprint_data: dict[str, Any] | None = None,
) -> tuple[Any | None, Any | None]:
    """Recreate non-serialized reading-power runtime from current source facts."""
    window_manager: Any | None = None
    window_config: Any | None = None
    config_enabled = False
    try:
        style_profile = getattr(bundle, "style_profile", None)
        reading_power_enabled = bool(getattr(context.settings, "reading_power_enabled", True))
        if style_profile is None or not reading_power_enabled:
            return None, None
        from novel_forge.pipeline.style_profile_helpers import (
            coerce_reading_power_window_config,
        )

        window_config = coerce_reading_power_window_config(style_profile)
        if window_config is None or not getattr(window_config, "enabled", False):
            return None, window_config
        config_enabled = True
        if blueprint_data is None and context.storage.exists(bundle.layout.blueprint_path):
            blueprint_data = context.storage.load_json(bundle.layout.blueprint_path)
        from novel_forge.pipeline.long.services.quality.reading_power_timeline_window_manager import (
            ReadingPowerTimelineWindowManager,
        )

        window_manager = ReadingPowerTimelineWindowManager(
            blueprint=blueprint_data,
            storage=context.storage,
            layout=bundle.layout,
            config=window_config,
        )
    except Exception as exc:
        context.on_step(
            "reading_power_window_init_failed",
            {
                "chapter": chapter_number,
                "error": str(exc),
                "config_enabled": config_enabled,
            },
        )
        _logger.warning(
            "reading_power_window_init_failed | chapter=%d | error=%s", chapter_number, exc
        )
    return window_manager, window_config


async def rehydrate_prepared_runtime_context(
    context: ChapterExecutionContext,
    *,
    prepared: PreparedChapterArtifacts,
    chapter_number: int,
) -> PreparedChapterArtifacts:
    """Attach current runtime-only guidance to persisted planning artifacts."""
    window_manager = prepared.window_manager
    window_config = prepared.window_config
    if window_manager is None:
        window_manager, window_config = _initialize_reading_power_runtime(
            context,
            bundle=prepared.bundle,
            chapter_number=chapter_number,
        )

    memory_hints = prepared.memory_hints
    if memory_hints is None:
        try:
            from novel_forge.pipeline.long.services.context.stage_memory_builder import (
                collect_planning_memory_hints,
            )

            memory_hints = await collect_planning_memory_hints(
                context,
                prepared.bundle,
                chapter_number,
            )
        except Exception as exc:
            context.on_step(
                "prepared_runtime_memory_rehydrate_failed",
                {"chapter": chapter_number, "error": str(exc)},
            )
            _logger.warning(
                "prepared_runtime_memory_rehydrate_failed | chapter=%d | error=%s",
                chapter_number,
                exc,
            )

    reading_power_hint = prepared.reading_power_hint
    if reading_power_hint is None and window_manager is not None:
        try:
            reading_power_hint = window_manager.build_reading_power_hint(
                current_chapter=chapter_number,
                chapter_outline=prepared.bundle.chapter_outline,
            )
        except Exception as exc:
            context.on_step(
                "prepared_runtime_reading_power_rehydrate_failed",
                {"chapter": chapter_number, "error": str(exc)},
            )
            _logger.warning(
                "prepared_runtime_reading_power_rehydrate_failed | chapter=%d | error=%s",
                chapter_number,
                exc,
            )

    return dataclasses.replace(
        prepared,
        memory_hints=memory_hints,
        window_manager=window_manager,
        window_config=window_config,
        reading_power_hint=reading_power_hint,
    )


async def load_prepared_chapter_artifacts(
    context: ChapterExecutionContext,
    *,
    bundle: LongProjectBundle,
    chapter_number: int,
) -> PreparedChapterArtifacts:
    """Reload planning artifacts written by the prepare stage."""
    scene_report: dict[str, Any] | None = None
    try:
        from novel_forge.pipeline.long.services.generation.scene_writing import (
            scene_plan_report_path,
        )

        report_path = scene_plan_report_path(bundle.layout, chapter_number)
        if context.storage.exists(report_path):
            scene_report = context.storage.load_json(report_path)
    except Exception:
        scene_report = None
    prepared = PreparedChapterArtifacts(
        bundle=bundle,
        packet=ChapterStatePacket.model_validate(
            context.storage.load_json(bundle.layout.chapter_state_packet_path(chapter_number))
        ),
        bridge=ChapterBridge.model_validate(
            context.storage.load_json(bundle.layout.chapter_bridge_path(chapter_number))
        ),
        plan=ChapterPlan.model_validate(
            context.storage.load_json(bundle.layout.chapter_plan_path(chapter_number))
        ),
        scene_plan_validation_report=scene_report,
    )
    return await rehydrate_prepared_runtime_context(
        context,
        prepared=prepared,
        chapter_number=chapter_number,
    )


@dataclasses.dataclass(frozen=True)
class _RecoveredDraft:
    text: str
    performed_edits: int


def _load_recoverable_text(storage: Any, path: Any) -> str | None:
    try:
        if storage.exists(path):
            text = storage.load_text(path)
            if text and len(str(text).strip()) > 200:
                return str(text).strip()
    except Exception:
        return None
    return None


def _recover_latest_draft(storage: Any, layout: Any, chapter_number: int) -> _RecoveredDraft | None:
    """Try to load the most recent reviewable draft version saved to disk.

    :func:`generate_chapter_prose` runs DRAFT + WAVE and returns the woven prose
    (``v1_wave.md``).  If the pipeline is interrupted after WAVE completed but
    before the review-progress checkpoint is written, this function finds that
    reviewable text so an emergency checkpoint can be created.

    Raw ``v0_draft.md`` is deliberately not recoverable as ``draft_done``:
    it has not passed through WAVE, so resuming from it would skip the
    scene-weaving stage.
    """
    wave_path_fn = getattr(layout, "chapter_wave_draft_path", None)
    if callable(wave_path_fn):
        wave_text = _load_recoverable_text(storage, wave_path_fn(chapter_number))
        if wave_text is not None:
            return _RecoveredDraft(text=wave_text, performed_edits=1)

    # Legacy fallback: pre-WAVE projects may only have vN_edited.md snapshots.
    # Do not include version 0, because it is now the raw DRAFT-stage artifact.
    for ver in range(10, 0, -1):
        path = layout.chapter_draft_path(chapter_number, ver)
        text = _load_recoverable_text(storage, path)
        if text is not None:
            return _RecoveredDraft(text=text, performed_edits=ver)
    return None


@dataclasses.dataclass
class ReviewTextState:
    """Text and generation metadata for the review phase."""

    current_text: str = ""
    performed_edits: int = 0
    text_change_history: list[dict[str, Any]] = dataclasses.field(default_factory=list)
    draft_meta: dict[str, Any] = dataclasses.field(default_factory=dict)
    wave_meta: dict[str, Any] = dataclasses.field(default_factory=dict)
    wave_integrity: dict[str, Any] = dataclasses.field(default_factory=dict)
    pre_wave_chapter_repair_report: Any = None
    post_repair_polish_modified_text: bool = False
    humanize_modified_text: bool = False


@dataclasses.dataclass
class ReviewReportsState:
    """Quality and repair reports bound to the current review text."""

    alignment_report: Any = None
    continuity_report: Any = None
    chapter_repair_report: Any = None
    causal_report: Any = None
    continuity_repair: Any = None  # for repair_plan extraction
    final_rp_report: Any = None
    quality_lane_eval_report: Any = None
    quality_lane_eval_text_hash: str = ""
    guard_compliance_report: dict[str, Any] | None = None
    guard_review_findings: list[Any] = dataclasses.field(default_factory=list)
    guard_repair_tickets: list[Any] = dataclasses.field(default_factory=list)


@dataclasses.dataclass
class RepairBudgetState:
    """Repair loop budget, exhaustion, and loop result bookkeeping."""

    repair_exhausted: bool = False
    total_repair_rounds_used: int = 0
    total_rounds_cap: int = 0
    cumulative_change_ratio: float = 0.0
    baseline_text: str | None = None
    chapter_quality_repair: dict[str, Any] = dataclasses.field(default_factory=dict)
    cont_loop_result: Any = None
    causal_result: Any = None
    rp_result: Any = None


@dataclasses.dataclass
class ReviewDiagnosticsState:
    """Warnings and side-audit diagnostics accumulated during review."""

    review_warnings: list[str] = dataclasses.field(default_factory=list)
    pov_drift_findings: list[Any] = dataclasses.field(default_factory=list)
    pov_drift_tickets: list[Any] = dataclasses.field(default_factory=list)
    cross_dim_action: Any = None


class _ReviewPhaseState:
    """Grouped mutable state carrier for ``review_chapter_draft`` internals.

    The legacy flattened attributes remain available so the staged review flow
    can be migrated incrementally without changing checkpoint behavior.
    """

    _FIELD_TO_GROUP = {
        "current_text": "text",
        "performed_edits": "text",
        "text_change_history": "text",
        "draft_meta": "text",
        "wave_meta": "text",
        "wave_integrity": "text",
        "pre_wave_chapter_repair_report": "text",
        "post_repair_polish_modified_text": "text",
        "humanize_modified_text": "text",
        "alignment_report": "reports",
        "continuity_report": "reports",
        "chapter_repair_report": "reports",
        "causal_report": "reports",
        "continuity_repair": "reports",
        "final_rp_report": "reports",
        "quality_lane_eval_report": "reports",
        "quality_lane_eval_text_hash": "reports",
        "guard_compliance_report": "reports",
        "guard_review_findings": "reports",
        "guard_repair_tickets": "reports",
        "repair_exhausted": "budget",
        "total_repair_rounds_used": "budget",
        "total_rounds_cap": "budget",
        "cumulative_change_ratio": "budget",
        "baseline_text": "budget",
        "chapter_quality_repair": "budget",
        "cont_loop_result": "budget",
        "causal_result": "budget",
        "rp_result": "budget",
        "review_warnings": "diagnostics",
        "pov_drift_findings": "diagnostics",
        "pov_drift_tickets": "diagnostics",
        "cross_dim_action": "diagnostics",
    }

    def __init__(self, **kwargs: Any) -> None:
        object.__setattr__(self, "text", ReviewTextState())
        object.__setattr__(self, "reports", ReviewReportsState())
        object.__setattr__(self, "budget", RepairBudgetState())
        object.__setattr__(self, "diagnostics", ReviewDiagnosticsState())
        for key, value in kwargs.items():
            setattr(self, key, value)

    def __getattr__(self, name: str) -> Any:
        group_name = self._FIELD_TO_GROUP.get(name)
        if group_name is None:
            raise AttributeError(name)
        return getattr(getattr(self, group_name), name)

    def __setattr__(self, name: str, value: Any) -> None:
        group_name = self._FIELD_TO_GROUP.get(name)
        if group_name is None:
            object.__setattr__(self, name, value)
            return
        setattr(getattr(self, group_name), name, value)


def _record_text_change(
    state: _ReviewPhaseState,
    *,
    stage: str,
    before_text: str,
    after_text: str,
    applied: bool | None = None,
    reason: str = "",
) -> None:
    """Append a compact text-change provenance entry for review telemetry."""

    changed = before_text != after_text
    if applied is None:
        applied = changed
    entry: dict[str, Any] = {
        "stage": stage,
        "before_hash": source_text_hash(before_text) if before_text else "",
        "after_hash": source_text_hash(after_text) if after_text else "",
        "before_chars": len(before_text or ""),
        "after_chars": len(after_text or ""),
        "changed": changed,
        "applied": bool(applied),
    }
    if reason:
        entry["reason"] = reason
    state.text_change_history.append(entry)


_REVIEW_PROGRESS_STAGE_ORDER = (
    "draft_done",
    "quality_done",
    "causal_repair_done",
    "repair_done",
    "canon_done",
    "refinement_done",
    "final_verify_done",
)


@dataclasses.dataclass(frozen=True)
class _ReviewResumePlan:
    """Resolved skip plan for review-progress resume checkpoints."""

    stage: str | None
    skip_draft: bool
    skip_quality: bool
    skip_causal_repair: bool
    skip_repair: bool
    skip_extract: bool
    run_text_refinement: bool


def _review_resume_plan(resume_stage: str | None) -> _ReviewResumePlan:
    stage = str(resume_stage or "").strip() or None
    if stage not in _REVIEW_PROGRESS_STAGE_ORDER:
        stage = None
    completed = -1
    if stage is not None:
        completed = _REVIEW_PROGRESS_STAGE_ORDER.index(stage)
    skip_extract = completed >= _REVIEW_PROGRESS_STAGE_ORDER.index("canon_done")
    return _ReviewResumePlan(
        stage=stage,
        skip_draft=completed >= _REVIEW_PROGRESS_STAGE_ORDER.index("draft_done"),
        skip_quality=completed >= _REVIEW_PROGRESS_STAGE_ORDER.index("quality_done"),
        skip_causal_repair=completed >= _REVIEW_PROGRESS_STAGE_ORDER.index("causal_repair_done"),
        skip_repair=completed >= _REVIEW_PROGRESS_STAGE_ORDER.index("repair_done"),
        skip_extract=skip_extract,
        run_text_refinement=not skip_extract,
    )


async def _generate_and_wave(
    *,
    runner: Any,
    context: ChapterExecutionContext,
    prepared: PreparedChapterArtifacts,
    trace: PipelineTrace,
    chapter_number: int,
    resume_progress: ReviewProgressState | None,
    skip_draft: bool,
    resume_stage: str | None,
    on_step: Any,
) -> _ReviewPhaseState:
    """Phase 2 — Generate: DRAFT + WAVE + opening guard patch.

    When *skip_draft* is True the phase restores ``current_text`` and
    ``performed_edits`` from *resume_progress* instead of regenerating.
    """
    runner = FlowContextAdapter(runner)
    state = _ReviewPhaseState()
    bundle = prepared.bundle

    from novel_forge.core.schemas.review_state import (
        ReviewProgressState as _ReviewProgressState,
    )
    from novel_forge.core.schemas.review_state import (
        save_review_progress,
    )
    from novel_forge.pipeline.long.chapter_flow import (
        generate_chapter_prose,
        run_opening_guard_patch,
    )

    if skip_draft and resume_progress is not None:
        state.current_text = resume_progress.current_text
        state.performed_edits = resume_progress.performed_edits
        on_step(
            "resume_from_progress",
            {
                "chapter": chapter_number,
                "completed_stage": resume_stage,
                "skipped": "draft_generation",
            },
        )
    else:
        # ── Build reading power hint for draft stage ──
        _draft_rp_hint = prepared.reading_power_hint
        if _draft_rp_hint is None and prepared.window_manager is not None:
            try:
                _draft_rp_hint = prepared.window_manager.build_reading_power_hint(
                    current_chapter=chapter_number,
                    chapter_outline=bundle.chapter_outline,
                )
            except Exception as _exc:
                _logger.debug(
                    "draft_reading_power_hint_build_failed | chapter=%d | error=%s",
                    chapter_number,
                    _exc,
                )

        try:
            _gen_artifacts = await generate_chapter_prose(
                runner,
                bundle,
                prepared.packet,
                prepared.bridge,
                prepared.plan,
                chapter_number,
                trace,
                planning_hints=prepared.memory_hints,
                reading_power_hint=_draft_rp_hint,
            )
            state.current_text = _gen_artifacts.current_text
            state.performed_edits = _gen_artifacts.performed_edits
            state.draft_meta = dict(_gen_artifacts.draft_meta or {})
            state.wave_meta = _gen_artifacts.wave_meta
            state.pre_wave_chapter_repair_report = _gen_artifacts.chapter_repair_report
            _record_text_change(
                state,
                stage="generate_draft_wave",
                before_text="",
                after_text=state.current_text,
                applied=True,
            )
            for warning in list(state.wave_meta.get("warnings", []) or []):
                warning_text = str(warning)
                if warning_text and warning_text not in state.review_warnings:
                    state.review_warnings.append(warning_text)
        except (asyncio.CancelledError, KeyboardInterrupt, ModelGatewayError):
            _recovered = _recover_latest_draft(context.storage, bundle.layout, chapter_number)
            if _recovered:
                try:
                    save_review_progress(
                        storage=context.storage,
                        layout=bundle.layout,
                        chapter_number=chapter_number,
                        progress=_ReviewProgressState(
                            completed_stage="draft_done",
                            current_text=_recovered.text,
                            performed_edits=_recovered.performed_edits,
                        ),
                    )
                    _logger.info(
                        "emergency_checkpoint_saved | chapter=%d | stage=draft_done",
                        chapter_number,
                    )
                except Exception as _emergency_exc:
                    _logger.warning(
                        "emergency_checkpoint_save_failed | chapter=%d | stage=draft_done | %s",
                        chapter_number,
                        _emergency_exc,
                    )
            raise

        # ── Save progress: draft complete ──
        await asyncio.to_thread(
            save_review_progress,
            storage=context.storage,
            layout=bundle.layout,
            chapter_number=chapter_number,
            progress=_ReviewProgressState(
                completed_stage="draft_done",
                current_text=state.current_text,
                performed_edits=state.performed_edits,
            ),
        )

    # ── Opening guard patch ──
    if resume_progress is None or resume_stage == "draft_done":
        try:
            patched_text = await run_opening_guard_patch(
                runner,
                bundle,
                prepared.packet,
                prepared.bridge,
                prepared.plan,
                state.current_text,
                chapter_number,
                trace,
            )
            if patched_text != state.current_text:
                _pre_opening_guard_text = state.current_text
                state.current_text = patched_text
                _record_text_change(
                    state,
                    stage="opening_guard_patch",
                    before_text=_pre_opening_guard_text,
                    after_text=state.current_text,
                    applied=True,
                )
                await asyncio.to_thread(
                    save_review_progress,
                    storage=context.storage,
                    layout=bundle.layout,
                    chapter_number=chapter_number,
                    progress=_ReviewProgressState(
                        completed_stage="draft_done",
                        current_text=state.current_text,
                        performed_edits=state.performed_edits,
                    ),
                )
        except Exception as _og_exc:
            _logger.warning(
                "opening_guard_patch_error | chapter=%d | error=%s",
                chapter_number,
                _og_exc,
            )

    return state


async def execute_chapter_pipeline(
    context: ChapterExecutionContext,
    *,
    chapter_number: int,
    bundle: LongProjectBundle,
    trace: PipelineTrace,
    memory: Any,
) -> ChapterResult:
    """Execute the direct chapter pipeline via shared staged primitives."""
    from novel_forge.pipeline.long.chapter_flow_finalize import (
        _persist_macro_guard_result,
        _should_include_pre_final_evaluation,
        finalize_chapter_result,
    )
    from novel_forge.pipeline.long.chapter_flow_review import (
        review_chapter_draft,
    )

    prepared = await prepare_chapter_plan(
        context,
        bundle=bundle,
        chapter_number=chapter_number,
        trace=trace,
        memory=memory,
    )
    review = await review_chapter_draft(
        context,
        prepared=prepared,
        trace=trace,
        include_evaluation=_should_include_pre_final_evaluation(context.settings),
        emit_evaluation_step=False,
        persist_evaluation=False,
    )

    if not review.refinement_done:
        review = await _apply_terminal_word_count_polish(context, review, trace)

    result = await finalize_chapter_result(
        context,
        review=review,
        trace=trace,
        emit_evaluate_step=True,
    )

    # ── Reading power window manager: accepted chapter only ───────────────
    _save_reading_power_next_chapter_constraints(
        context,
        prepared,
        chapter_number=chapter_number,
    )

    # === MacroGuard: multi-chapter trajectory audit (non-blocking) ===
    try:
        if should_trigger_macro_guard(
            chapter_number=chapter_number,
            layout=bundle.layout,
            storage=context.storage,
            settings=context.settings,
        ):
            audit_entries = load_recent_audit_entries(
                context.storage,
                bundle.layout,
                n=getattr(context.settings, "long_macro_guard_max_audit_chapters", 5),
            )
            if audit_entries:
                macro_step = MacroGuardStep(
                    context.router,
                    context.builder,
                    settings=context.settings,
                    trace=trace,
                )
                macro_report = await macro_step.run(
                    MacroGuardInput(
                        chapter_number=chapter_number,
                        audit_entries=audit_entries,
                        outline=bundle.outline,
                        canon_state=bundle.canon_state.model_dump(mode="json")
                        if bundle.canon_state
                        else {},
                        settings=context.settings,
                        adjustment_state=load_macro_guard_adjustment_state(
                            context.storage,
                            bundle.layout,
                        ),
                    )
                )
                _persist_macro_guard_result(
                    context.storage,
                    bundle.layout,
                    chapter_number,
                    macro_report,
                )
                result = result.model_copy(update={"macro_guard_report": macro_report})
                context.on_step(
                    "macro_guard_audit",
                    {
                        "chapter": chapter_number,
                        "drift_score": macro_report.drift_score,
                        "action": macro_report.recommended_action,
                    },
                )
    except Exception as exc:
        _logger.warning(
            "macro_guard_audit_failed | chapter=%d | error=%s",
            chapter_number,
            exc,
            exc_info=True,
        )
        context.on_step(
            "macro_guard_audit_failed",
            {"chapter": chapter_number, "error": str(exc)},
        )

    return result


async def _apply_terminal_word_count_polish(
    context: ChapterExecutionContext,
    review: ChapterReviewArtifacts,
    trace: PipelineTrace,
) -> ChapterReviewArtifacts:
    """Run target word-count polish after narrative quality gates have passed."""
    runner = FlowContextAdapter(context)
    bundle = review.prepared.bundle
    chapter_number = bundle.chapter_outline.chapter_number
    gate_enabled = word_count_archive_gate_enabled(runner._settings)
    if not gate_enabled:
        context.on_step(
            "word_count_archive_gate_skipped",
            {"chapter": chapter_number, "reason": "config_disabled"},
        )
        return review

    runtime_ready = (
        callable(getattr(context.router, "route", None))
        and callable(getattr(context.builder, "build", None))
        and callable(getattr(context.storage, "save_text", None))
    )
    if not runtime_ready:
        context.on_step(
            "word_count_archive_gate_skipped",
            {"chapter": chapter_number, "reason": "runtime_dependencies_unavailable"},
        )
        return review

    result = await run_word_count_restructure(
        runner=runner,
        bundle=bundle,
        packet=review.prepared.packet,
        bridge=review.prepared.bridge,
        plan=review.prepared.plan,
        current_text=review.current_text,
        chapter_number=chapter_number,
        trace=trace,
    )
    context.on_step(
        "word_count_archive_gate",
        {"chapter": chapter_number, "phase": "terminal_polish", **result.event_payload()},
    )
    if result.accepted:
        clear_word_count_rejections(context.storage, bundle.layout, chapter_number)
    if not result.accepted and result.before.band in {"structural", "hard_reject"}:
        rejections = record_word_count_rejection(
            context.storage,
            bundle.layout,
            chapter_number,
            result,
        )
        limit = word_count_archive_gate_max_rejections(runner._settings)
        if word_count_rejection_limit_reached(runner._settings, rejections):
            warning = (
                f"末端字数精修达到拒绝上限：当前 {result.before.actual}/"
                f"{result.before.target} 字，处于 {result.before.band} 区间；"
                f"润色模块仍未达标（原因：{result.reason}）。本次降级为警告归档。"
            )
            context.on_step(
                "word_count_archive_gate_limit_reached",
                {
                    "chapter": chapter_number,
                    "phase": "terminal_polish",
                    "rejection_count": rejections,
                    "max_rejections": limit,
                    "reason": result.reason,
                    "before": result.before.as_dict(),
                },
            )
            return dataclasses.replace(
                review,
                warnings=[*(review.warnings or []), warning],
                allow_word_count_archive_bypass=True,
            )
        raise RuntimeError(
            f"章节 {chapter_number} 末端字数精修未达标："
            f"{result.before.actual}/{result.before.target}，"
            f"原因：{result.reason}（第 {rejections}/{limit} 次）。"
        )
    if not result.changed:
        return review

    # ── Continuity bottom-line defense after word-count polish ──────────
    # Word-count restructuring may break causal/continuity coherence.
    # Run a lightweight continuity eval; if score drops below the
    # hard-block threshold, roll back to pre-polish text (accept word
    # count non-compliance rather than archiving incoherent prose).
    try:
        from novel_forge.pipeline.long.stages.finalize_checks import (
            _archive_hard_block_thresholds,
        )
        from novel_forge.pipeline.steps.continuity_eval.context import (
            ContinuityEvalInput,
        )
        from novel_forge.pipeline.steps.continuity_eval.core import (
            ContinuityEvalStep,
        )

        continuity_hard_block, _ = _archive_hard_block_thresholds(runner._settings)
        _cont_step = ContinuityEvalStep(
            runner._router,
            runner._builder,
            settings=runner._settings,
            trace=trace,
        )
        _cont_report = await _cont_step.run(
            ContinuityEvalInput(
                chapter_number=chapter_number,
                chapter_text=result.text,
                chapter_state_packet=review.prepared.packet,
                chapter_bridge=review.prepared.bridge,
                chapter_plan=review.prepared.plan,
                pov_switch=getattr(bundle.chapter_outline, "pov_switch", False),
                project_path=getattr(bundle.layout, "root", None),
                chapter_source_slice=getattr(bundle, "chapter_source_slice", None),
            )
        )
        _cont_score = float(getattr(_cont_report, "continuity_score", 10.0) or 10.0)
        if _cont_score < continuity_hard_block:
            rollback_warning = (
                f"末端字数精修后连贯性跌破底线（{_cont_score:.1f} < "
                f"{continuity_hard_block:.1f}），回滚到精修前文本。"
            )
            context.on_step(
                "word_count_polish_continuity_rollback",
                {
                    "chapter": chapter_number,
                    "continuity_score": _cont_score,
                    "threshold": continuity_hard_block,
                    "action": "rollback_to_pre_polish_text",
                },
            )
            return dataclasses.replace(
                review,
                warnings=[*(review.warnings or []), rollback_warning],
            )
    except Exception as exc:  # noqa: BLE001
        # Continuity check failure should not block the polish flow;
        # log and proceed (the finalize hard gate remains as backstop).
        _logger.warning(
            "word_count_polish_continuity_check_failed | chapter=%d | error=%s",
            chapter_number,
            exc,
        )
        context.on_step(
            "word_count_polish_continuity_check_error",
            {"chapter": chapter_number, "error": str(exc)},
        )

    await asyncio.to_thread(
        context.storage.save_text,
        bundle.layout.chapter_draft_path(chapter_number, 95),
        result.text,
    )
    warning = (
        f"末端字数精修：{result.before.actual} → {result.after.actual} "
        f"（目标 {result.after.target}，{result.reason}）。"
    )
    context.on_step(
        "word_count_polish_applied",
        {
            "chapter": chapter_number,
            "mode": result.mode,
            "before": result.before.as_dict(),
            "after": result.after.as_dict(),
        },
    )
    context.on_step(
        "text_changed_before_archive",
        {
            "chapter": chapter_number,
            "reason": "word_count_polish_before_archive",
            "before_hash": source_text_hash(review.current_text),
            "after_hash": source_text_hash(result.text),
            "refresh_quality": True,
            "refresh_eval": True,
            "refresh_outcome": True,
        },
    )

    refreshed_outcome, refreshed_eval = await asyncio.gather(
        extract_and_validate(
            runner,
            bundle,
            review.prepared.packet,
            review.prepared.bridge,
            review.prepared.plan,
            result.text,
            chapter_number,
            trace,
            review.continuity_report,
            repair_exhausted=True,
        ),
        evaluate_chapter_text(
            context,
            bundle=bundle,
            chapter_number=chapter_number,
            current_text=result.text,
            trace=trace,
            emit_step=False,
            persist=False,
        ),
    )
    return dataclasses.replace(
        review,
        current_text=result.text,
        performed_edits=review.performed_edits + 1,
        outcome=refreshed_outcome,
        eval_report=refreshed_eval,
        warnings=[*(review.warnings or []), warning],
        quality_reports_stale_after_text_change=True,
        quality_reports_stale_reason="word_count_polish_before_archive",
    )


async def _apply_terminal_humanize(
    context: ChapterExecutionContext,
    review: ChapterReviewArtifacts,
    trace: PipelineTrace,
) -> ChapterReviewArtifacts:
    """Run final humanize and return changed text for the archive gate to rejudge."""
    from novel_forge.pipeline.long.chapter_flow_finalize import (
        _humanize_result_changed,
        _run_humanize_pass,
    )

    runner = FlowContextAdapter(context)
    prepared = review.prepared
    bundle = prepared.bundle
    chapter_number = bundle.chapter_outline.chapter_number
    before_text = review.current_text
    humanize_result = await _run_humanize_pass(
        runner,
        prepared,
        current_text=review.current_text,
        chapter_number=chapter_number,
        trace=trace,
        context=context,
        polish_modified=bool(review.quality_reports_stale_after_text_change),
    )
    current_text = humanize_result.current_text
    humanize_changed = _humanize_result_changed(
        humanize_result,
        before_text=before_text,
        after_text=current_text,
    )
    if not humanize_changed:
        return review

    return dataclasses.replace(
        review,
        current_text=current_text,
        performed_edits=review.performed_edits + 1,
    )
