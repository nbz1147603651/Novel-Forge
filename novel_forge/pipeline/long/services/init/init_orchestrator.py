"""Main long-project initialization orchestrator."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING, Any, Literal, cast

from pydantic import ValidationError as PydanticValidationError

from novel_forge.core.schemas import CharacterBible, StoryOutline
from novel_forge.pipeline.long.human_decision import (
    HumanDecisionOption,
    HumanDecisionProvider,
    HumanDecisionRequest,
    HumanDecisionResponse,
    request_human_decision,
)
from novel_forge.pipeline.long.services.init.init_cache import (
    _build_base_ctx,
    _build_long_init_request_payload,
    _ensure_long_init_request_fresh,
    _has_generated_chapters,
    _load_cached_json_or_rollback,
    _load_cached_model_or_rollback,
    _rollback_cached_init_step,
)
from novel_forge.pipeline.long.services.init.init_chapter_contracts import (
    _batched_generate_chapter_contracts,
    _persist_chapter_contract_runtime_artifacts,
    _source_artifact_resume_rebuild_chapters,
    merge_reusable_and_rebuilt_chapter_contracts,
)
from novel_forge.pipeline.long.services.init.init_character_bible import (
    _apply_knowledge_boundaries_to_character_bible,
    _character_bible_without_knowledge_boundaries_payload,
    _character_generation_mode,
    _character_knowledge_boundaries_complete,
    _knowledge_boundary_payload_from_character_bible,
    _knowledge_boundary_upstream_hashes,
    _load_cached_character_bible_for_mode,
    _load_or_generate_character_relationship_matrix,
    _load_or_refresh_character_bible_source,
    _load_reusable_outline_polish,
    _outline_polish_report_path,
    _save_character_generation_metadata,
    _save_outline_polish_report,
)
from novel_forge.pipeline.long.services.init.init_common import (
    BLUEPRINT_ARTIFACT,
    CHAPTER_CONTRACTS_ARTIFACT,
    CHARACTER_SYSTEM_BUILD_VERSION,
    CREATIVE_DIRECTOR_PACKET_BUILD_VERSION,
    ENTITY_GRAPH_BUILD_VERSION,
    OUTLINE_ARTIFACT,
    STATUS_SUCCEEDED,
    BibleDerivedProvider,
    BlueprintElementSelectInput,
    BlueprintElementSelection,
    BlueprintElementSelectStep,
    EditorialContract,
    EditorialContractInput,
    EditorialContractStep,
    EntityRegistry,
    ForbiddenElementRegistry,
    InitArtifact,
    InitCoherenceError,
    InitRepairContext,
    InitRepairOrchestrator,
    InitV2BlockCache,
    NarrativeBlueprint,
    NarrativeStateStore,
    RunnerProtocol,
    SpecStep,
    StoryBible,
    StorySpec,
    TaskType,
    blueprint_to_fragments,
    build_character_bible_split,
    build_character_system,
    build_creative_director_packet,
    build_entity_graph,
    build_init_entity_catalog,
    build_outline_stream_claim_chunks,
    build_reading_power_window_config_from_settings,
    build_relationship_prompt_overview,
    build_story_bible_split,
    build_subplot_execution_matrix,
    calculate_route_aware_max_tokens,
    dump_story_bible_for_prompt,
    get_init_repair_policy,
    has_manual_selector_preferences,
    hash_payload,
    init_coherence_artifact_hashes,
    load_reusable_init_coherence_profile,
    manifest_for_context,
    outline_h,
    pre_normalize_blueprint_payload,
    prefetch_init_coherence_claim_chunks,
    project_character_bible,
    refine_init_coherence_profile,
    repair_outline_resume_state,
    run_init_coherence_v2_gate,
    set_init_profile_mode_metric,
    should_use_volume_mode,
    validate_editorial_contract,
    validate_plan_outline_context,
    validate_subplot_weave,
)
from novel_forge.pipeline.long.services.init.init_context import build_init_context
from novel_forge.pipeline.long.services.init.init_contract_flow import (
    initialize_narrative_state_and_contracts,
    reaudit_changed_chapter_contracts,
    repair_claim_contract_coverage,
)
from novel_forge.pipeline.long.services.init.init_creative_refinement import (
    _maybe_refine_blueprint_creatively,
    _try_local_blueprint_coherence_fallback,
)
from novel_forge.pipeline.long.services.init.init_outline import generate_or_resume_blueprint
from novel_forge.pipeline.long.services.init.init_outline_batch import (
    _batched_generate_outline,
    _outline_entity_audit,
    _outline_entity_catalog,
)
from novel_forge.pipeline.long.services.init.init_outline_helpers import (
    _load_partial_outline_chapters_from_session,
)
from novel_forge.pipeline.long.services.init.init_repair_manifest import (
    _persist_init_repair_outcome,
)
from novel_forge.pipeline.long.services.init.init_repair_targets import (
    _init_coherence_repair_round_start,
    _repair_init_artifact_payload,
)
from novel_forge.pipeline.long.services.init.init_source_artifact_repair import (
    _load_init_artifact_repairs,
    _persist_source_artifacts_from_init,
)
from novel_forge.pipeline.long.services.init.init_source_resume import (
    _cached_outline_matches_reveal_guard,
    _emit_init_resume_classified,
    _finish_init_after_source_artifacts,
    _init_coherence_allows_llm_followup_repair,
    _init_coherence_blocks,
    _init_coherence_focus_chapters,
    _init_coherence_focus_chapters_after_repair,
    _init_coherence_max_repair_rounds,
    _init_coherence_repair_stop_decision,
    _load_optional_json,
    _load_reusable_init_coherence_report,
    _load_source_artifacts_resume_bundle,
    _local_story_fallbacks_enabled,
    _record_init_coherence_repair_loop_stop,
    _record_init_resume_decision,
    _repair_outline_reveal_guard_manifest_from_downstream,
    _save_init_readiness,
    _seed_init_coherence_profile,
    _with_init_entity_catalog,
)
from novel_forge.pipeline.long.services.init.init_story_bible import (
    _classify_init_readiness_resume,
    _detect_init_resume_anchor,
    _deterministic_non_character_registry,
    _entity_registry_needs_llm_supplement,
    _merge_entity_registries,
    _set_entity_registry_mode_metric,
    _supplemental_non_character_registry,
)
from novel_forge.pipeline.long.services.init.init_upstream_health import (
    assert_upstream_health_allows_progress,
    record_character_bible_health,
    record_character_system_health,
    record_creative_packet_health,
    record_entity_graph_health,
    record_story_bible_health,
    research_context_fingerprint,
    story_bible_research_context_matches,
)
from novel_forge.pipeline.long.services.init.init_user_intent import (
    build_user_intent_card,
    enforce_explicit_input_on_story_spec,
)
from novel_forge.research.contracts import (
    OutlineResearchGrounding,
    ResearchDossier,
    ResearchReport,
)
from novel_forge.research.evidence import (
    build_init_retrieval_evidence_pack,
    research_uncertainty_notes,
)
from novel_forge.research.service import (
    failed_outline_research_grounding,
    failed_research_dossier,
    failed_research_report,
    ground_outline_research,
    research_runtime_fingerprint,
    run_init_web_research,
    synthesize_init_research_dossier,
)

if TYPE_CHECKING:
    from novel_forge.pipeline.chapter_runner import InitLongResult

_log = logging.getLogger(__name__)


def _record_character_system_manifest(
    ctx: Any,
    *,
    cache: InitV2BlockCache,
    upstream_hashes: dict[str, str],
    character_system: Any,
    source: str,
) -> None:
    manifest_for_context(ctx).record_success(
        artifact="character_system",
        workflow="init_long",
        step="init_character_system",
        input_hashes=upstream_hashes,
        output_hashes={"character_system": hash_payload(character_system)},
        paths={
            "cache": str(cache.path_for("character_system")),
            "state": str(ctx.layout.states_dir / "init_v2" / "character_system.json"),
        },
        metadata={"source": source},
        input_signature=hash_payload(upstream_hashes),
        schema_version=2,
        workflow_version="init.character_system.v2",
    )


def _editorial_max_repair_rounds(settings: Any) -> int:
    """Bounded regeneration rounds for the editorial contract repair loop.

    Mirrors the init_coherence_max_repair_rounds ceiling used by the
    blueprint/outline loops: default 2, hard-capped at 3. Keeping a local
    copy avoids depending on a helper that is not reliably imported into
    this module's namespace.
    """
    try:
        value = int(getattr(settings, "init_coherence_max_repair_rounds", 2) or 0)
    except (TypeError, ValueError):
        value = 2
    return max(1, min(value, 3))


def _world_rule_prompt_ctx(settings: Any) -> dict[str, Any]:
    """Build the ``world_rule_governance`` context slice for the init prompt.

    Exposes the configured governance thresholds to ``init_story_world_rules.j2``
    so the prompt reflects the user's 火候 settings rather than hardcoded numbers.
    """
    return {
        "rule_count_min": int(getattr(settings, "world_rule_count_min", 10) or 10),
        "rule_count_max": int(getattr(settings, "world_rule_count_max", 14) or 14),
        "hard_rule_min": int(getattr(settings, "world_rule_hard_min", 3) or 3),
        "always_on_hard_cap": int(getattr(settings, "world_rule_always_on_hard_cap", 4) or 4),
        "category_min": int(getattr(settings, "world_rule_category_min", 4) or 4),
    }


_INIT_COPILOT_GATE_TITLES: dict[str, str] = {
    "characters": "确认角色与关系",
    "concept": "确认创意方向",
    "blueprint": "确认叙事蓝图",
    "outline": "确认章节大纲",
    "contracts": "确认叙事契约",
}


def _normalize_init_copilot_gates(gates: tuple[str, ...] | list[str] | set[str] | None) -> set[str]:
    return {str(gate).strip().lower() for gate in (gates or ()) if str(gate).strip()}


def _init_gate_json_payload(text: str) -> Any:
    cleaned = str(text or "").strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        if lines and lines[0].lstrip().startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        cleaned = "\n".join(lines).strip()
    if not cleaned:
        raise ValueError("未提供编辑后的 JSON。")
    return json.loads(cleaned)


def _init_gate_artifact_payload(artifact: Any) -> Any:
    if hasattr(artifact, "model_dump"):
        return artifact.model_dump(mode="json")
    return artifact


def _load_reusable_init_research_report(
    ctx: Any,
    *,
    path: Any,
    spec: Any,
    enabled: bool,
    provider_name: str,
    config_fingerprint: str = "",
    allow_failed: bool = False,
) -> ResearchReport | None:
    """Return a cached research report when it still matches this init request.

    Failed web research normally remains retryable.  A resume with a persisted
    StoryBible is different: its non-blocking research fallback is already part
    of the StoryBible's prompt context.  Re-running that fallback can change
    the downstream dossier and incorrectly force a full source-artifact
    rollback, even though the author did not change the init request.
    """
    storage = getattr(ctx, "storage", None)
    if storage is None or not storage.exists(path):
        return None
    try:
        report = ResearchReport.model_validate(storage.load_json(path))
    except Exception as exc:  # noqa: BLE001 - stale reports are safely regenerated
        _log.warning("init_web_research_cache_unreadable | error=%s", exc)
        return None
    expected_fingerprint = hash_payload(spec.model_dump(mode="json"))
    if report.spec_fingerprint != expected_fingerprint:
        return None
    if report.enabled != enabled:
        return None
    if report.status == "failed" and not allow_failed:
        return None
    if enabled and config_fingerprint and report.config_fingerprint != config_fingerprint:
        return None
    requested_provider = (provider_name or "auto").strip().lower() or "auto"
    if enabled and requested_provider not in {"auto", "noop", "none"}:
        if report.provider.strip().lower() != requested_provider:
            return None
    return report


def _load_reusable_init_research_dossier(
    ctx: Any,
    *,
    path: Any,
    spec: Any,
    research_report: ResearchReport,
    enabled: bool,
) -> ResearchDossier | None:
    storage = getattr(ctx, "storage", None)
    if storage is None or not storage.exists(path):
        return None
    try:
        dossier = ResearchDossier.model_validate(storage.load_json(path))
    except Exception as exc:  # noqa: BLE001 - stale reports are safely regenerated
        _log.warning("init_research_dossier_cache_unreadable | error=%s", exc)
        return None
    if dossier.status == "failed":
        return None
    expected_enabled = bool(
        enabled and getattr(getattr(ctx, "settings", None), "research_dossier_enabled", True)
    )
    if dossier.enabled != expected_enabled:
        return None
    if dossier.spec_fingerprint != hash_payload(spec.model_dump(mode="json")):
        return None
    if dossier.research_report_fingerprint != hash_payload(research_report.model_dump(mode="json")):
        return None
    expected_config = research_runtime_fingerprint(getattr(ctx, "settings", None), dossier.provider)
    if dossier.config_fingerprint and dossier.config_fingerprint != expected_config:
        return None
    return dossier


def _load_reusable_outline_research_grounding(
    ctx: Any,
    *,
    path: Any,
    outline: Any,
    dossier: ResearchDossier,
    enabled: bool,
) -> OutlineResearchGrounding | None:
    storage = getattr(ctx, "storage", None)
    if storage is None or not storage.exists(path):
        return None
    try:
        grounding = OutlineResearchGrounding.model_validate(storage.load_json(path))
    except Exception as exc:  # noqa: BLE001 - stale reports are safely regenerated
        _log.warning("outline_research_grounding_cache_unreadable | error=%s", exc)
        return None
    if grounding.status == "failed":
        return None
    expected_enabled = bool(
        enabled
        and getattr(getattr(ctx, "settings", None), "outline_research_grounding_enabled", True)
        and dossier.status == "succeeded"
        and bool(dossier.summary)
    )
    if grounding.enabled != expected_enabled:
        return None
    if grounding.dossier_fingerprint != hash_payload(dossier.model_dump(mode="json")):
        return None
    outline_payload = outline.model_dump(mode="json") if hasattr(outline, "model_dump") else outline
    if grounding.outline_fingerprint != hash_payload(outline_payload):
        return None
    expected_config = research_runtime_fingerprint(getattr(ctx, "settings", None), dossier.provider)
    if grounding.config_fingerprint and grounding.config_fingerprint != expected_config:
        return None
    return grounding


async def _request_init_copilot_gate(
    *,
    enabled_gates: set[str],
    gate: str,
    project_id: str,
    on_step: Any,
    human_decision_provider: HumanDecisionProvider | None,
    artifact: Any,
    message: str,
    editable: bool = False,
) -> HumanDecisionResponse | None:
    if gate not in enabled_gates:
        return None
    options = [
        HumanDecisionOption(
            "continue",
            "继续",
            "接受当前产物并继续初始化。",
        )
    ]
    if editable:
        options.append(
            HumanDecisionOption(
                "apply_edits",
                "应用编辑 JSON",
                "使用 custom_text 中的完整 JSON 替换当前产物，并在任务持锁上下文内校验写回。",
            )
        )
    options.append(
        HumanDecisionOption(
            "abort",
            "终止立项",
            "停止当前初始化任务，保留已落盘的中间产物。",
        )
    )
    artifact_payload = _init_gate_artifact_payload(artifact)
    request = HumanDecisionRequest(
        decision_id=f"init:{project_id}:{gate}:{hash_payload(artifact_payload)[:12]}",
        kind="init_copilot_gate",
        project_id=project_id,
        chapter_number=0,
        title=_INIT_COPILOT_GATE_TITLES.get(gate, "确认初始化产物"),
        message=message,
        options=tuple(options),
        default_option="continue",
        timeout_seconds=3600,
        risk="medium" if editable else "low",
        cost_hint="AI 伴随立项会在此等待人工确认。",
        metadata={
            "gate": gate,
            "editable": editable,
            "artifact": artifact_payload,
        },
    )
    response = await request_human_decision(
        provider=human_decision_provider,
        on_step=on_step,
        request=request,
    )
    if response.choice == "abort":
        raise RuntimeError("AI 伴随立项已由用户终止。")
    return response


async def _request_concept_copilot_gate(
    *,
    enabled_gates: set[str],
    project_id: str,
    on_step: Any,
    human_decision_provider: HumanDecisionProvider | None,
    candidates: tuple[Any, ...],
    default_candidate_id: str,
) -> str:
    """Let the author choose among valid concept candidates via the existing provider."""

    if "concept" not in enabled_gates:
        return default_candidate_id
    valid = [candidate for candidate in candidates if not candidate.intent_conflicts]
    options = [
        HumanDecisionOption(
            candidate.candidate_id,
            f"选择 {candidate.candidate_id}",
            candidate.packet.notes or "采用该候选作为后续确定性融合的创意种子。",
        )
        for candidate in valid
    ]
    options.append(
        HumanDecisionOption(
            "deterministic_fallback",
            "使用确定性方向",
            "不采用多候选，沿用既有确定性 CreativeDirectorPacket 构建路径。",
        )
    )
    options.append(HumanDecisionOption("abort", "终止立项", "保留已落盘的中间产物。"))
    valid_ids = {candidate.candidate_id for candidate in valid}
    default_option = (
        default_candidate_id if default_candidate_id in valid_ids else "deterministic_fallback"
    )
    artifact = [candidate.model_dump(mode="json") for candidate in candidates]
    request = HumanDecisionRequest(
        decision_id=f"init:{project_id}:concept:{hash_payload(artifact)[:12]}",
        kind="init_copilot_gate",
        project_id=project_id,
        chapter_number=0,
        title=_INIT_COPILOT_GATE_TITLES["concept"],
        message="AI 仅推荐候选；请选择最终创意方向。所有候选均不得覆盖用户明确意图。",
        options=tuple(options),
        default_option=default_option,
        timeout_seconds=3600,
        risk="low",
        cost_hint="选择不会触发额外模型调用。",
        metadata={"gate": "concept", "artifact": artifact},
    )
    response = await request_human_decision(
        provider=human_decision_provider,
        on_step=on_step,
        request=request,
    )
    if response.choice == "abort":
        raise RuntimeError("AI 伴随立项已由用户终止。")
    return response.choice


async def _request_init_drift_confirmation(
    *,
    project_id: str,
    drift: tuple[str, ...],
    can_reinitialize: bool,
    on_step: Any,
    human_decision_provider: HumanDecisionProvider | None,
) -> str:
    """Require an explicit decision before replacing confirmed project facts."""

    options = [
        HumanDecisionOption(
            "abort_keep_existing",
            "保留现有项目",
            "停止本次恢复/重生，不修改已确认事实。",
        ),
        HumanDecisionOption(
            "use_new_project",
            "改用新项目",
            "保留当前项目，使用新 project_id 创建另一个项目。",
        ),
    ]
    if can_reinitialize:
        options.append(
            HumanDecisionOption(
                "confirm_reinitialize",
                "确认重建 AI 产物",
                "当前尚无正文；明确授权按新输入重建初始化派生产物。",
            )
        )
    request = HumanDecisionRequest(
        decision_id=f"init:{project_id}:request_drift:{hash_payload(drift)[:12]}",
        kind="init_request_drift_gate",
        project_id=project_id,
        chapter_number=0,
        title="新输入与已确认项目事实冲突",
        message="系统已保留现有项目，不会静默覆盖。请选择如何处理本次输入。",
        options=tuple(options),
        default_option="abort_keep_existing",
        timeout_seconds=3600,
        risk="high",
        cost_hint="默认保留已确认项目，不触发模型或联网调用。",
        metadata={"drift": list(drift), "can_reinitialize": can_reinitialize},
    )
    response = await request_human_decision(
        provider=human_decision_provider,
        on_step=on_step,
        request=request,
    )
    return response.choice


def _load_existing_outline_research_grounding_context(
    ctx: Any,
    *,
    outline: Any,
) -> dict[str, Any] | None:
    path = ctx.layout.reports_dir / "outline_research_grounding.json"
    storage = getattr(ctx, "storage", None)
    if storage is None or not storage.exists(path):
        return None
    try:
        grounding = OutlineResearchGrounding.model_validate(storage.load_json(path))
    except Exception as exc:  # noqa: BLE001 - resume should remain best effort
        _log.warning("outline_research_grounding_resume_unreadable | error=%s", exc)
        return None
    chapters = getattr(outline, "chapters", []) or []
    return grounding.prompt_context(max_chapter_notes=max(1, len(chapters)))


async def init_long_project(
    runner: RunnerProtocol,
    premise: str,
    *,
    project_id: str,
    genre: str = "",
    tone: str = "",
    title: str = "",
    language: str = "zh",
    characters_hint: str = "",
    world_hint: str = "",
    conflict_hint: str = "",
    pov_hint: str = "",
    opening_style: str = "",
    ending_style: str = "",
    extra_instructions: str = "",
    total_chapters: int = 20,
    words_per_chapter: int = 3000,
    volume_mode: Literal["auto", "on", "off"] = "auto",
    chapters_per_volume: int = 0,
    blueprint_element_preferences: dict[str, Any] | None = None,
    polish_hint: str = "",
    research_enabled: bool = False,
    research_provider: str = "auto",
    research_query_hint: str = "",
    copilot_gates: tuple[str, ...] = (),
    creative_exploration: Literal["adaptive", "single"] = "adaptive",
    planning_commitment: Literal["progressive", "full"] = "full",
    human_decision_provider: HumanDecisionProvider | None = None,
) -> InitLongResult:
    """Initialize a long-mode project: Enrich → Bible → Outline → CanonState.

    **Resume-aware**: if a previous run was interrupted, already-
    persisted artifacts (spec, bible, outline) are loaded from disk
    and the pipeline resumes from the next incomplete step.

    Args:
        runner: ``ChapterRunner`` instance (typed via Protocol to avoid circular imports).
        premise: 故事前提
        project_id: 项目唯一标识
        genre / tone / title / language: 创作参数
        characters_hint / world_hint / conflict_hint / pov_hint: 创作提示
        opening_style / ending_style / extra_instructions: 创作约束
        total_chapters: 目标章节数（默认 20 章）
        words_per_chapter: 每章目标字数（默认 3000 字）
        volume_mode: 分卷模式（auto/on/off）
        chapters_per_volume: 每卷章节数（仅分卷时生效，0=自动）
        blueprint_element_preferences: 手动要素偏好（勾选/锁定/权重/预置）。
    """
    ctx = build_init_context(runner, project_id)
    config = ctx.config
    settings = ctx.settings
    on_step = ctx.on_step
    storage = ctx.storage
    layout = ctx.layout
    enabled_copilot_gates = _normalize_init_copilot_gates(copilot_gates)
    effective_creative_exploration = creative_exploration
    if not bool(getattr(settings, "init_adaptive_creative_exploration_enabled", True)):
        effective_creative_exploration = "single"
    effective_planning_commitment = planning_commitment
    if not bool(getattr(settings, "init_progressive_planning_enabled", True)):
        effective_planning_commitment = "full"
    if (
        effective_creative_exploration != creative_exploration
        or effective_planning_commitment != planning_commitment
    ):
        on_step(
            "init_feature_flag_fallback",
            {
                "requested_creative_exploration": creative_exploration,
                "effective_creative_exploration": effective_creative_exploration,
                "requested_planning_commitment": planning_commitment,
                "effective_planning_commitment": effective_planning_commitment,
            },
        )
    init_coherence_reports: dict[str, dict[str, Any] | None] = {
        "blueprint_coherence": None,
        "outline_inheritance": None,
        "contract_coherence": None,
        "claim_contract_coverage": None,
        "source_artifacts": None,
    }
    init_repairs: list[dict[str, Any]] = _load_init_artifact_repairs(ctx)

    expected_total_words = total_chapters * words_per_chapter
    use_volume_mode = should_use_volume_mode(
        volume_mode=volume_mode,
        total_chapters=total_chapters,
        expected_total_words=expected_total_words,
        chapter_threshold=config.volume_auto_chapter_threshold,
        word_threshold=config.volume_auto_word_threshold,
    )
    effective_chapters_per_volume = (
        chapters_per_volume if chapters_per_volume > 0 else config.default_chapters_per_volume
    )
    research_config_fingerprint = (
        research_runtime_fingerprint(settings, research_provider) if research_enabled else ""
    )
    request_payload = _build_long_init_request_payload(
        premise=premise,
        genre=genre,
        tone=tone,
        title=title,
        language=language,
        characters_hint=characters_hint,
        world_hint=world_hint,
        conflict_hint=conflict_hint,
        pov_hint=pov_hint,
        opening_style=opening_style,
        ending_style=ending_style,
        extra_instructions=extra_instructions,
        total_chapters=total_chapters,
        words_per_chapter=words_per_chapter,
        volume_mode=volume_mode,
        chapters_per_volume=chapters_per_volume,
        effective_volume_mode=use_volume_mode,
        effective_chapters_per_volume=effective_chapters_per_volume,
        blueprint_element_preferences=blueprint_element_preferences,
        polish_hint=polish_hint,
        research_enabled=research_enabled,
        research_provider=research_provider,
        research_query_hint=research_query_hint,
        research_config_fingerprint=research_config_fingerprint,
        research_use_llm_planning=(
            bool(getattr(settings, "research_use_llm_planning", True)) if research_enabled else True
        ),
        research_model_prior_enabled=(
            bool(getattr(settings, "research_model_prior_enabled", False))
            if research_enabled
            else False
        ),
        creative_exploration=creative_exploration,
        planning_commitment=planning_commitment,
    )
    freshness = _ensure_long_init_request_fresh(
        ctx,
        project_id=project_id,
        request_payload=request_payload,
    )
    if bool(getattr(freshness, "requires_confirmation", False)):
        can_reinitialize = not _has_generated_chapters(layout)
        drift_choice = await _request_init_drift_confirmation(
            project_id=project_id,
            drift=tuple(getattr(freshness, "drift", ()) or ()),
            can_reinitialize=can_reinitialize,
            on_step=on_step,
            human_decision_provider=human_decision_provider,
        )
        if drift_choice != "confirm_reinitialize" or not can_reinitialize:
            raise RuntimeError(
                "已保留现有项目；本次输入未覆盖已确认事实。"
                "如需并行方案，请使用新 project_id。"
            )
        _ensure_long_init_request_fresh(
            ctx,
            project_id=project_id,
            request_payload=request_payload,
            allow_confirmed_reset=True,
        )
    init_v2_cache = InitV2BlockCache(layout.root)
    request_fingerprint = hash_payload(request_payload)
    resume_anchor = _detect_init_resume_anchor(
        ctx,
        total_chapters=total_chapters,
    )
    if resume_anchor is not None:
        on_step("init_resume_anchor", resume_anchor)
    resume_readiness = _load_optional_json(ctx, layout.reports_dir / "init_readiness.json")
    if isinstance(resume_readiness, dict):
        _emit_init_resume_classified(
            ctx,
            _classify_init_readiness_resume(resume_readiness),
        )
    source_resume_bundle = _load_source_artifacts_resume_bundle(
        ctx,
        premise=premise,
        total_chapters=total_chapters,
        words_per_chapter=words_per_chapter,
        use_volume_mode=use_volume_mode,
    )
    if source_resume_bundle is not None:
        resume_mode = str(source_resume_bundle.resume_mode or "source_artifacts_only")
        resume_reason = (
            "claim_contract_coverage_blocked"
            if resume_mode == "claim_coverage"
            else (
                "contract_coherence_blocked"
                if resume_mode == "contract_coherence"
                else "source_artifacts_only_blocked"
            )
        )
        source_resume_payload = {
            "source": "cached_init_artifacts",
            "chapters": source_resume_bundle.total_chapters,
            "reason": resume_reason,
            "resume_mode": resume_mode,
            "reused_artifacts": [
                "spec",
                "story_bible",
                "character_bible",
                "blueprint",
                "outline",
                "narrative_contract",
                "chapter_contracts",
                "character_system",
                "entity_graph",
                "creative_director_packet",
            ],
        }
        resume_event = (
            "init_claim_contract_coverage_resume"
            if resume_mode == "claim_coverage"
            else (
                "init_contract_coherence_resume"
                if resume_mode == "contract_coherence"
                else "init_source_artifacts_resume"
            )
        )
        on_step(resume_event, source_resume_payload)
        _record_init_resume_decision(
            ctx,
            stage=(
                "claim_contract_coverage"
                if resume_mode == "claim_coverage"
                else (
                    "contract_coherence"
                    if resume_mode == "contract_coherence"
                    else "source_artifacts"
                )
            ),
            artifact=(
                CHAPTER_CONTRACTS_ARTIFACT
                if resume_mode in {"claim_coverage", "contract_coherence"}
                else "source_artifacts"
            ),
            action="reused",
            reason=resume_reason,
            metadata=source_resume_payload,
        )
        source_resume_chapter_contracts = source_resume_bundle.chapter_contracts
        source_resume_readiness = source_resume_bundle.readiness_report
        source_resume_entity_catalog = build_init_entity_catalog(
            source_resume_bundle.entity_graph,
            source_resume_bundle.character_bible,
            chapter_contracts=source_resume_chapter_contracts,
        )
        source_resume_outline_grounding = _load_existing_outline_research_grounding_context(
            ctx,
            outline=source_resume_bundle.outline,
        )

        async def _reaudit_source_resume_contracts(
            updated_contracts: dict[str, Any],
            focus_chapters: list[int],
        ) -> dict[str, Any]:
            entity_catalog = build_init_entity_catalog(
                source_resume_bundle.entity_graph,
                source_resume_bundle.character_bible,
                chapter_contracts=updated_contracts,
            )
            return await reaudit_changed_chapter_contracts(
                ctx=ctx,
                blueprint=source_resume_bundle.blueprint,
                outline=source_resume_bundle.outline,
                llm_contract=source_resume_bundle.narrative_contract,
                chapter_contracts=updated_contracts,
                project_id=project_id,
                init_entity_catalog=entity_catalog,
                outline_ctx=source_resume_bundle.outline_ctx,
                total_chapters=source_resume_bundle.total_chapters,
                narrative_complexity=source_resume_bundle.spec.narrative_complexity,
                init_coherence_reports=source_resume_bundle.coherence_reports,
                init_repairs=init_repairs,
                focus_chapters=focus_chapters,
                outline_research_grounding=source_resume_outline_grounding,
            )

        if resume_mode == "claim_coverage":
            source_resume_chapter_contracts = await repair_claim_contract_coverage(
                ctx=ctx,
                blueprint=source_resume_bundle.blueprint,
                outline=source_resume_bundle.outline,
                llm_contract=source_resume_bundle.narrative_contract,
                chapter_contracts=source_resume_chapter_contracts,
                settings=settings,
                on_step=on_step,
                project_id=project_id,
                init_entity_catalog=source_resume_entity_catalog,
                outline_ctx=source_resume_bundle.outline_ctx,
                total_chapters=source_resume_bundle.total_chapters,
                narrative_complexity=source_resume_bundle.spec.narrative_complexity,
                init_coherence_reports=source_resume_bundle.coherence_reports,
                init_repairs=init_repairs,
                outline_research_grounding=source_resume_outline_grounding,
            )
            source_resume_readiness = _save_init_readiness(
                ctx,
                reports=source_resume_bundle.coherence_reports,
                repairs=init_repairs,
            )
            on_step(
                "init_claim_contract_coverage_resumed",
                {
                    "status": "verified",
                    "chapters": len(
                        source_resume_chapter_contracts.get("chapter_contracts", []) or []
                    ),
                    "path": str(layout.reports_dir / "init_claim_contract_coverage.json"),
                },
            )
        elif resume_mode == "contract_coherence":
            contract_report = source_resume_bundle.coherence_reports.get("contract_coherence") or {}
            focus_chapters: list[int] = []
            for value in contract_report.get("focus_chapters", []) or []:
                try:
                    chapter_number = int(value)
                except (TypeError, ValueError):
                    continue
                if chapter_number > 0:
                    focus_chapters.append(chapter_number)
            focus_chapters = sorted(set(focus_chapters))
            if not focus_chapters:
                focus_chapters = (
                    _init_coherence_focus_chapters(
                        ctx,
                        report=contract_report,
                        artifact=CHAPTER_CONTRACTS_ARTIFACT,
                    )
                    or []
                )
            if not focus_chapters:
                for item in source_resume_chapter_contracts.get("chapter_contracts", []):
                    if not isinstance(item, dict):
                        continue
                    try:
                        chapter_number = int(item.get("chapter_number") or 0)
                    except (TypeError, ValueError):
                        continue
                    if chapter_number > 0:
                        focus_chapters.append(chapter_number)
                focus_chapters = sorted(set(focus_chapters))
            source_resume_chapter_contracts = await _reaudit_source_resume_contracts(
                source_resume_chapter_contracts,
                focus_chapters,
            )
            source_resume_readiness = _save_init_readiness(
                ctx,
                reports=source_resume_bundle.coherence_reports,
                repairs=init_repairs,
            )
            on_step(
                "init_contract_coherence_resumed",
                {
                    "status": "verified",
                    "focus_chapters": focus_chapters,
                    "reused_artifacts": source_resume_payload["reused_artifacts"],
                },
            )
        source_artifact_rebuild_chapters = _source_artifact_resume_rebuild_chapters(
            ctx,
            outline=source_resume_bundle.outline,
        )
        if source_artifact_rebuild_chapters:
            (
                rebuilt_contracts,
                _rebuilt_coverage,
            ) = await _batched_generate_chapter_contracts(
                ctx,
                outline=source_resume_bundle.outline,
                narrative_contract=source_resume_bundle.narrative_contract,
                project_id=project_id,
                entity_catalog=source_resume_entity_catalog,
                focus_chapters=source_artifact_rebuild_chapters,
                outline_research_grounding=source_resume_outline_grounding,
            )
            source_resume_chapter_contracts, source_resume_contract_coverage = (
                merge_reusable_and_rebuilt_chapter_contracts(
                    source_resume_chapter_contracts,
                    rebuilt_contracts,
                    outline=source_resume_bundle.outline,
                    settings=settings,
                )
            )
            source_resume_chapter_contracts["coverage"] = source_resume_contract_coverage
            source_resume_chapter_contracts = await _reaudit_source_resume_contracts(
                source_resume_chapter_contracts,
                source_artifact_rebuild_chapters,
            )
        # Always cross the same runtime-artifact boundary after late resume.
        # Even when no LLM rebuild is needed, the resume loader may have migrated
        # stale identity fields to the current deterministic design matrix.
        source_resume_entity_catalog = build_init_entity_catalog(
            source_resume_bundle.entity_graph,
            source_resume_bundle.character_bible,
            chapter_contracts=source_resume_chapter_contracts,
        )
        milestone_index = _persist_chapter_contract_runtime_artifacts(
            ctx,
            outline=source_resume_bundle.outline,
            chapter_contracts=source_resume_chapter_contracts,
            llm_contract=source_resume_bundle.narrative_contract,
            project_id=project_id,
            entity_catalog=source_resume_entity_catalog,
            outline_research_grounding=source_resume_outline_grounding,
        )
        if source_artifact_rebuild_chapters:
            on_step(
                "init_source_artifacts_contracts_rebuilt",
                {
                    "rebuilt": source_artifact_rebuild_chapters,
                    "coverage": source_resume_contract_coverage,
                    "milestone_count": len(milestone_index.milestones),
                },
            )
        await _persist_source_artifacts_from_init(
            ctx,
            project_id=project_id,
            spec=source_resume_bundle.spec,
            story_bible=source_resume_bundle.story_bible,
            character_bible=source_resume_bundle.character_bible,
            character_system=source_resume_bundle.character_system,
            entity_graph=source_resume_bundle.entity_graph,
            style_profile=source_resume_bundle.style_profile,
            creative_packet=source_resume_bundle.creative_packet,
            blueprint=source_resume_bundle.blueprint,
            outline=source_resume_bundle.outline,
            narrative_contract=source_resume_bundle.narrative_contract,
            chapter_contracts=source_resume_chapter_contracts,
            readiness_report=source_resume_readiness,
            coherence_reports=source_resume_bundle.coherence_reports,
            repairs=init_repairs,
            outline_ctx=source_resume_bundle.outline_ctx,
            total_chapters=source_resume_bundle.total_chapters,
            post_repair_reaudit=_reaudit_source_resume_contracts,
        )
        return await _finish_init_after_source_artifacts(
            ctx,
            project_id=project_id,
            story_bible=source_resume_bundle.story_bible,
            character_bible=source_resume_bundle.character_bible,
            outline=source_resume_bundle.outline,
            entity_registry=source_resume_bundle.entity_registry,
            entity_graph=source_resume_bundle.entity_graph,
        )

    # ⓪ Enrich premise via SpecStep
    cached_spec = _load_cached_model_or_rollback(
        ctx,
        "spec",
        layout.spec_path,
        StorySpec.model_validate,
    )
    if cached_spec is not None:
        enriched_spec = cached_spec
        on_step("spec_resumed", enriched_spec)
    else:
        spec_step = SpecStep(ctx.router, ctx.builder, settings=settings, trace=ctx.trace)
        enriched_spec = await spec_step.run(
            {
                "theme": premise,
                "genre": genre,
                "tone": tone,
                "title": title,
                "language": language,
                "characters_hint": characters_hint,
                "world_hint": world_hint,
                "conflict_hint": conflict_hint,
                "pov_hint": pov_hint,
                "opening_style": opening_style,
                "ending_style": ending_style,
                "extra_instructions": extra_instructions,
                "length_target": expected_total_words,
            }
        )
        storage.save_json(layout.spec_path, enriched_spec.model_dump(mode="json"))
        on_step("spec", enriched_spec)

    enriched_spec, protected_spec_fields = enforce_explicit_input_on_story_spec(
        enriched_spec,
        request_payload,
        expected_total_words=expected_total_words,
    )
    if protected_spec_fields:
        storage.save_json(layout.spec_path, enriched_spec.model_dump(mode="json"))
        on_step(
            "spec_user_intent_restored",
            {
                "fields": list(protected_spec_fields),
                "action": "preserve_explicit_user_input",
            },
        )

    # Initialize project seeds with LLM-determined genre (after enriched_spec is ready)
    try:
        ForbiddenElementRegistry.init_project_seeds(layout.root, genre=enriched_spec.genre or genre)
    except Exception as exc:
        _log.warning("forbidden_element_warmup_failed | error=%s", exc)

    research_report_path = layout.reports_dir / "init_web_research.json"
    research_report = _load_reusable_init_research_report(
        ctx,
        path=research_report_path,
        spec=enriched_spec,
        enabled=research_enabled,
        provider_name=research_provider,
        config_fingerprint=research_config_fingerprint,
        # Research is advisory, but its fallback dossier is an upstream input
        # once StoryBible has been persisted.  Preserve the original failed
        # report on resume so the validated StoryBible can be reused; a changed
        # request or research runtime fingerprint still rejects the cache.
        allow_failed=bool(research_enabled and storage.exists(layout.bible_path)),
    )
    research_report_was_resumed = research_report is not None
    if research_report is not None:
        on_step(
            "init_web_research_resumed",
            {
                "status": research_report.status,
                "enabled": research_report.enabled,
                "provider": research_report.provider,
                "sources": len(research_report.sources),
                "model_prior": research_report.model_prior.status,
                "path": str(research_report_path),
            },
        )
    else:
        try:
            if research_enabled:
                on_step(
                    "init_web_research_start",
                    {
                        "enabled": True,
                        "provider": research_provider,
                    },
                )
            research_report = await run_init_web_research(
                spec=enriched_spec,
                settings=settings,
                enabled=research_enabled,
                provider_name=research_provider,
                query_hint=research_query_hint,
                ctx=ctx,
                use_llm_planning=bool(
                    research_enabled and getattr(settings, "research_use_llm_planning", True)
                ),
                model_prior_enabled=bool(
                    research_enabled and getattr(settings, "research_model_prior_enabled", False)
                ),
            )
        except Exception as exc:  # noqa: BLE001 - research must not block initialization
            _log.warning("init_web_research_failed | error=%s", exc)
            research_report = failed_research_report(
                spec=enriched_spec,
                settings=settings,
                enabled=research_enabled,
                provider_name=research_provider,
                query_hint=research_query_hint,
                error=exc,
            )
        storage.save_json(research_report_path, research_report.model_dump(mode="json"))
    if not research_report_was_resumed:
        if research_report.status == "failed":
            on_step(
                "init_web_research_failed",
                {
                    "provider": research_report.provider,
                    "model_prior": research_report.model_prior.status,
                    "warnings": research_report.warnings,
                    "path": str(research_report_path),
                },
            )
        elif research_report.status == "skipped":
            on_step(
                "init_web_research_skipped",
                {
                    "enabled": research_report.enabled,
                    "provider": research_report.provider,
                    "model_prior": research_report.model_prior.status,
                    "warnings": research_report.warnings,
                    "path": str(research_report_path),
                },
            )
        else:
            on_step(
                "init_web_research",
                {
                    "status": research_report.status,
                    "provider": research_report.provider,
                    "queries": len(research_report.queries),
                    "sources": len(research_report.sources),
                    "model_prior": research_report.model_prior.status,
                    "warnings": research_report.warnings,
                    "path": str(research_report_path),
                },
            )

    research_dossier_path = layout.reports_dir / "init_research_dossier.json"
    research_dossier = _load_reusable_init_research_dossier(
        ctx,
        path=research_dossier_path,
        spec=enriched_spec,
        research_report=research_report,
        enabled=research_enabled,
    )
    if research_dossier is not None:
        on_step(
            "init_research_dossier_resumed",
            {
                "status": research_dossier.status,
                "provider": research_dossier.provider,
                "path": str(research_dossier_path),
            },
        )
    else:
        try:
            research_dossier = await synthesize_init_research_dossier(
                ctx=ctx,
                spec=enriched_spec,
                research_report=research_report,
                enabled=research_enabled,
            )
        except Exception as exc:  # noqa: BLE001 - dossier must not block initialization
            _log.warning("init_research_dossier_failed | error=%s", exc)
            research_dossier = failed_research_dossier(
                spec=enriched_spec,
                research_report=research_report,
                settings=settings,
                error=exc,
            )
        storage.save_json(research_dossier_path, research_dossier.model_dump(mode="json"))
        on_step(
            "init_research_dossier",
            {
                "status": research_dossier.status,
                "provider": research_dossier.provider,
                "warnings": research_dossier.warnings,
                "path": str(research_dossier_path),
            },
        )

    # ① Generate StoryBible（世界观设定）
    base_ctx = _build_base_ctx(enriched_spec, expected_total_words, premise=premise)
    base_ctx["user_intent"] = build_user_intent_card(request_payload)
    research_evidence_pack = build_init_retrieval_evidence_pack(
        report=research_report,
        dossier=research_dossier,
        purpose="long_init",
        token_budget=int(getattr(settings, "init_research_evidence_token_budget", 1800) or 1800),
    )
    base_ctx["research_evidence_pack"] = research_evidence_pack.model_dump(mode="json")
    base_ctx["research_uncertainty"] = research_uncertainty_notes(
        report=research_report,
        dossier=research_dossier,
    )
    on_step(
        "init_research_evidence_pack",
        {
            "cards": len(research_evidence_pack.evidence_cards),
            "used_tokens": research_evidence_pack.estimated_evidence_tokens,
            "token_budget": research_evidence_pack.evidence_token_budget,
            "omitted": research_evidence_pack.omitted_candidate_count_lower_bound,
        },
    )
    base_ctx["research_context"] = (
        research_dossier.prompt_context()
        if research_dossier.status == "succeeded"
        else research_report.prompt_context()
    )
    story_research_fingerprint = research_context_fingerprint(base_ctx["research_context"])

    # ── Phase A: Parallel execution of ② StoryBible + ④ BlueprintElementSelect ──
    # Both only depend on enriched_spec (base_ctx), no inter-dependency.

    async def _task_story_bible() -> StoryBible:
        """Generate or resume StoryBible."""
        sb = _load_cached_model_or_rollback(
            ctx,
            "story_bible",
            layout.bible_path,
            StoryBible.model_validate,
        )
        if sb is not None:
            enforce_research_fingerprint = bool(
                research_enabled or research_dossier.status == "succeeded"
            )
            if enforce_research_fingerprint and not story_bible_research_context_matches(
                ctx,
                story_research_fingerprint,
            ):
                _rollback_cached_init_step(
                    ctx,
                    "story_bible",
                    ValueError("cached StoryBible research_context_fingerprint mismatch"),
                )
                sb = None
        if sb is not None:
            health = record_story_bible_health(
                ctx,
                story_bible=sb,
                research_context=base_ctx["research_context"],
            )
            story_check = (
                health.get("checks", {}).get("story_bible", {}) if isinstance(health, dict) else {}
            )
            if bool(story_check.get("blocked", False)):
                _rollback_cached_init_step(
                    ctx,
                    "story_bible",
                    ValueError(
                        "cached StoryBible 缺少或不满足 WorldRuleBook；"
                        "必须重新初始化源头及全部下游产物"
                    ),
                )
                sb = None
            else:
                on_step("init_story_bible_resumed", {"story_bible": sb})
                return cast(StoryBible, sb)
        on_step("init_story_bible_starting", {})
        with ctx.trace.step("init_story_bible"):
            world_rule_cfg = _world_rule_prompt_ctx(ctx.settings)
            sb = await build_story_bible_split(
                ctx,
                base_ctx={**base_ctx, "world_rule_governance": world_rule_cfg},
                language=language,
            )
        storage.save_json(layout.bible_path, dump_story_bible_for_prompt(sb, mode="json"))
        record_story_bible_health(
            ctx, story_bible=sb, research_context=base_ctx["research_context"]
        )
        assert_upstream_health_allows_progress(ctx, artifact="story_bible")
        on_step("init_story_bible", {"story_bible": sb})
        return sb

    _should_reselect = has_manual_selector_preferences(blueprint_element_preferences)

    async def _task_blueprint_elements() -> BlueprintElementSelection:
        """Generate or resume BlueprintElementSelect."""
        if not _should_reselect:
            cached_es = _load_cached_model_or_rollback(
                ctx,
                "blueprint_elements",
                layout.blueprint_elements_path,
                BlueprintElementSelection.model_validate,
            )
        else:
            cached_es = None
        if cached_es is not None:
            es = cached_es
            on_step("plan_blueprint_elements_resumed", {"element_selection": es})
            return cast(BlueprintElementSelection, es)
        selector_step = BlueprintElementSelectStep(
            ctx.router,
            ctx.builder,
            settings=settings,
            trace=ctx.trace,
        )
        on_step("plan_blueprint_elements_starting", {})
        es = await selector_step.run(
            BlueprintElementSelectInput(
                spec=enriched_spec,
                mode="long",
                preferences=blueprint_element_preferences,
            )
        )
        storage.save_json(
            layout.blueprint_elements_path,
            es.model_dump(mode="json"),
        )
        on_step("plan_blueprint_elements", {"element_selection": es})
        return es

    # Execute ② and ④ concurrently
    story_bible_result, element_selection_result = await asyncio.gather(
        _task_story_bible(),
        _task_blueprint_elements(),
    )
    story_bible = story_bible_result
    element_selection = element_selection_result

    # ── Phase B: ③ CharacterBible (depends on ② StoryBible) ──
    character_generation_mode = _character_generation_mode(settings)
    cached_character_bible = _load_cached_character_bible_for_mode(
        ctx,
        expected_generation_mode=character_generation_mode,
    )
    if cached_character_bible is not None:
        character_bible = cached_character_bible
        on_step("init_character_bible_resumed", {"character_bible": character_bible})
    else:
        on_step(
            "init_character_bible_starting",
            {
                "generation_mode": character_generation_mode,
            },
        )
        with ctx.trace.step("init_character_bible"):
            character_bible = await build_character_bible_split(
                ctx,
                base_ctx=base_ctx,
                story_bible=story_bible,
                language=language,
            )
        storage.save_json(layout.characters_path, character_bible.model_dump(mode="json"))
        on_step("init_character_bible", {"character_bible": character_bible})

    source_character_bible = _load_or_refresh_character_bible_source(
        ctx,
        character_bible=character_bible,
        generation_mode=character_generation_mode,
    )

    relationship_matrix = await _load_or_generate_character_relationship_matrix(
        ctx,
        base_ctx=base_ctx,
        story_bible=story_bible,
        character_bible=source_character_bible,
        generation_mode=character_generation_mode,
    )

    # V2 character system: keep typed identity/entity links internally and
    # project only character-to-character relationships for chapter generation.
    character_system_upstreams = {
        "builder_version": CHARACTER_SYSTEM_BUILD_VERSION,
        "story_bible": hash_payload(story_bible),
        "character_bible": hash_payload(source_character_bible),
        "relationship_matrix": hash_payload(relationship_matrix),
    }
    cached_character_system = init_v2_cache.load_success(
        "character_system",
        request_fingerprint=request_fingerprint,
        upstream_hashes=character_system_upstreams,
    )
    character_manifest = manifest_for_context(ctx)
    if cached_character_system is not None:
        cached_hash = hash_payload(cached_character_system)
        cached_record = character_manifest.matching_record(
            "character_system",
            input_hashes=character_system_upstreams,
            output_hashes={"character_system": cached_hash},
            statuses={STATUS_SUCCEEDED},
            allow_reusable_failure=False,
        )
        if cached_record is None and character_manifest.get("character_system") is not None:
            cached_character_system = None
            on_step(
                "init_character_system_cache_rejected",
                {"reason": "artifact_manifest_mismatch"},
            )
    if cached_character_system is not None:
        from novel_forge.core.schemas.init_v2 import CharacterSystem

        character_system = CharacterSystem.model_validate(cached_character_system)
        on_step(
            "init_character_system_resumed",
            {
                "relationships": len(character_system.relationship_edges),
                "identity_links": len(character_system.identity_links),
                "issues": len(character_system.audit),
            },
        )
    else:
        character_system = build_character_system(
            source_character_bible,
            relationship_matrix=relationship_matrix,
        )
        init_v2_cache.save_success(
            "character_system",
            request_fingerprint=request_fingerprint,
            upstream_hashes=character_system_upstreams,
            payload=character_system.model_dump(mode="json"),
        )
        on_step(
            "init_character_system",
            {
                "relationships": len(character_system.relationship_edges),
                "identity_links": len(character_system.identity_links),
                "issues": len(character_system.audit),
            },
        )
    storage.save_json(
        layout.states_dir / "init_v2" / "character_system.json",
        character_system.model_dump(mode="json"),
    )
    _record_character_system_manifest(
        ctx,
        cache=init_v2_cache,
        upstream_hashes=character_system_upstreams,
        character_system=character_system,
        source="cache" if cached_character_system is not None else "generated",
    )
    projected_character_bible = project_character_bible(source_character_bible, character_system)
    if projected_character_bible.model_dump(mode="json") != character_bible.model_dump(mode="json"):
        character_bible = projected_character_bible
        storage.save_json(layout.characters_path, character_bible.model_dump(mode="json"))
    _save_character_generation_metadata(
        ctx,
        generation_mode=character_generation_mode,
        source_character_bible=source_character_bible,
        projected_character_bible=character_bible,
    )
    character_gate_response = await _request_init_copilot_gate(
        enabled_gates=enabled_copilot_gates,
        gate="characters",
        project_id=project_id,
        on_step=on_step,
        human_decision_provider=human_decision_provider,
        artifact=character_bible,
        message="请确认角色设定、人物关系和关系投影是否可以进入后续知识边界、风格与大纲生成。",
        editable=True,
    )
    if character_gate_response is not None and character_gate_response.choice == "apply_edits":
        edited_payload = _init_gate_json_payload(character_gate_response.custom_text)
        source_character_bible = CharacterBible.model_validate(edited_payload)
        character_system_upstreams = {
            "builder_version": CHARACTER_SYSTEM_BUILD_VERSION,
            "story_bible": hash_payload(story_bible),
            "character_bible": hash_payload(source_character_bible),
            "relationship_matrix": hash_payload(relationship_matrix),
            "source": "init_copilot_gate",
        }
        character_system = build_character_system(
            source_character_bible,
            relationship_matrix=relationship_matrix,
        )
        init_v2_cache.save_success(
            "character_system",
            request_fingerprint=request_fingerprint,
            upstream_hashes=character_system_upstreams,
            payload=character_system.model_dump(mode="json"),
        )
        storage.save_json(
            layout.states_dir / "init_v2" / "character_system.json",
            character_system.model_dump(mode="json"),
        )
        _record_character_system_manifest(
            ctx,
            cache=init_v2_cache,
            upstream_hashes=character_system_upstreams,
            character_system=character_system,
            source="copilot_manual_edit",
        )
        character_bible = project_character_bible(source_character_bible, character_system)
        storage.save_json(layout.characters_path, character_bible.model_dump(mode="json"))
        _save_character_generation_metadata(
            ctx,
            generation_mode="copilot_manual_edit",
            source_character_bible=source_character_bible,
            projected_character_bible=character_bible,
        )
        on_step(
            "init_copilot_characters_applied",
            {
                "characters": len(character_bible.characters),
                "relationships": len(character_system.relationship_edges),
            },
        )

    record_character_system_health(ctx, character_system=character_system)
    assert_upstream_health_allows_progress(ctx, artifact="character_system")

    try:
        genre = genre or "universal_minimal"
        provider = BibleDerivedProvider()
        derived = provider.derive_from_bible(
            story_bible,
            characters=[
                c.model_dump(mode="json") if hasattr(c, "model_dump") else c
                for c in (getattr(character_bible, "characters", []) or [])
            ],
        )
        ForbiddenElementRegistry.save_seeds(
            layout.root,
            derived,
            metadata={
                "derived_from": "bible",
                "genre": genre,
                "chapter_count": 0,
            },
        )
    except Exception as exc:
        _log.warning("forbidden_element_warmup_failed | error=%s", exc)

    # ── Phase B-2: Knowledge Boundaries（角色知识边界初始化） ──
    # Generate per-character knowledge boundaries after CharacterBible is complete.
    # Updates character_bible in-place and persists to disk.
    async def _task_knowledge_boundaries() -> None:
        """Generate or resume knowledge boundaries; never propagates failures."""
        try:
            with ctx.trace.step("init_knowledge_boundaries"):
                kb_upstreams = _knowledge_boundary_upstream_hashes(
                    story_bible=story_bible,
                    character_bible=character_bible,
                )
                kb_response = init_v2_cache.load_success(
                    "knowledge_boundaries",
                    request_fingerprint=request_fingerprint,
                    upstream_hashes=kb_upstreams,
                )
                kb_source = "cache"
                if kb_response is None and _character_knowledge_boundaries_complete(
                    character_bible
                ):
                    kb_response = _knowledge_boundary_payload_from_character_bible(character_bible)
                    init_v2_cache.save_success(
                        "knowledge_boundaries",
                        request_fingerprint=request_fingerprint,
                        upstream_hashes=kb_upstreams,
                        payload=kb_response,
                    )
                    kb_source = "character_bible"
                if kb_response is None:
                    kb_source = "llm"
                    kb_response = await ctx.call_with_retry(
                        TaskType.INIT_KNOWLEDGE_BOUNDARIES,
                        {
                            "story_bible": dump_story_bible_for_prompt(story_bible, mode="json"),
                            "character_bible": _character_bible_without_knowledge_boundaries_payload(
                                character_bible
                            ),
                        },
                        max_tokens=calculate_route_aware_max_tokens(
                            ctx.router,
                            TaskType.INIT_KNOWLEDGE_BOUNDARIES,
                            6000,
                            prompt_overhead=4000,
                            min_tokens=4096,
                        ),
                        temperature=getattr(ctx.settings, "temp_init_knowledge_boundaries", 0.3),
                        required_keys=("characters",),
                        max_retries=2,
                    )
                    init_v2_cache.save_success(
                        "knowledge_boundaries",
                        request_fingerprint=request_fingerprint,
                        upstream_hashes=kb_upstreams,
                        payload=kb_response,
                    )

                boundaries_applied = _apply_knowledge_boundaries_to_character_bible(
                    character_bible,
                    kb_response,
                )
                storage.save_json(layout.characters_path, character_bible.model_dump(mode="json"))
                step_name = (
                    "init_knowledge_boundaries_resumed"
                    if kb_source in {"cache", "character_bible"}
                    else "init_knowledge_boundaries"
                )
                manifest_for_context(ctx).record_success(
                    artifact="knowledge_boundaries",
                    workflow="init_long",
                    step="init_knowledge_boundaries",
                    input_hashes=kb_upstreams,
                    output_hashes={
                        "knowledge_boundaries": hash_payload(kb_response),
                        "character_bible": hash_payload(character_bible),
                    },
                    paths={
                        "character_bible": str(layout.characters_path),
                        "cache": str(init_v2_cache.path_for("knowledge_boundaries")),
                    },
                    metadata={
                        "source": kb_source,
                        "characters_with_boundaries": boundaries_applied,
                        "total_characters": len(character_bible.characters),
                    },
                )
                on_step(
                    step_name,
                    {
                        "characters_with_boundaries": boundaries_applied,
                        "total_characters": len(character_bible.characters),
                        "source": kb_source,
                    },
                )
                _log.info(
                    "init_knowledge_boundaries | source=%s | applied=%d / %d",
                    kb_source,
                    boundaries_applied,
                    len(character_bible.characters),
                )
        except Exception as exc:
            _log.warning(
                "init_knowledge_boundaries_failed | error=%s | error_type=%s",
                exc,
                type(exc).__name__,
            )
            on_step(
                "init_knowledge_boundaries_failed",
                {"error": str(exc), "error_type": type(exc).__name__},
            )

    # ③− Generate ProjectStyleProfile（项目专属风格规范，基于全部要素推导）
    # Intentionally generated before NarrativeBlueprint so the blueprint can
    # consume project-level style guidance. StoryBible + BlueprintElementSelect
    # remain the parallel portion of initialization.
    style_required = getattr(settings, "style_profile_required", False)

    async def _task_style_profile(
        character_bible_payload: dict[str, Any],
    ) -> Any:
        """Generate or resume ProjectStyleProfile."""
        if not settings.style_profile_enabled:
            on_step("profile_style_skipped", {})
            return None
        from novel_forge.core.schemas.style_profile import ProjectStyleProfile

        cached_style = _load_cached_json_or_rollback(
            ctx,
            "style_profile",
            layout.style_profile_path,
        )
        if cached_style is not None:
            try:
                sp = ProjectStyleProfile.model_validate(cached_style)
                on_step("profile_style_resumed", {"style_profile": sp})
                return sp
            except Exception as exc:
                _log.warning("风格规范加载失败：%s", exc)
                _rollback_cached_init_step(ctx, "style_profile", exc)

        from novel_forge.pipeline.steps.profile_style_step import (
            ProfileStyleInput,
            ProfileStyleStep,
        )

        profile_step = ProfileStyleStep(
            ctx.router,
            ctx.builder,
            settings=settings,
            trace=ctx.trace,
        )
        try:
            sp = await profile_step.run(
                ProfileStyleInput(
                    title=enriched_spec.title,
                    genre=enriched_spec.genre,
                    tone=enriched_spec.tone,
                    writing_style_mode="",
                    narrative_complexity=enriched_spec.narrative_complexity,
                    story_bible=dump_story_bible_for_prompt(story_bible, mode="json"),
                    character_bible=character_bible_payload,
                    blueprint_elements=element_selection.model_dump(mode="json"),
                    extra_instructions=enriched_spec.extra_instructions,
                    synopsis=getattr(story_bible, "premise", "") or enriched_spec.theme or "",
                    premise=getattr(story_bible, "premise", "") or "",
                )
            )
            if sp is not None:
                sp.reading_power_window_config = build_reading_power_window_config_from_settings(
                    settings
                )
            storage.save_json(
                layout.style_profile_path,
                sp.model_dump(mode="json"),
            )
            on_step("profile_style", {"style_profile": sp})
            return sp
        except Exception as exc:
            from novel_forge.pipeline.long.services.style_profile_recovery import (
                recover_style_profile_from_project_logs,
            )

            recovery_min_mtime: float | None = None
            try:
                recovery_min_mtime = layout.init_request_meta_path.stat().st_mtime
            except OSError:
                recovery_min_mtime = None
            recovered_style, recovery_meta = recover_style_profile_from_project_logs(
                layout.root,
                min_mtime=recovery_min_mtime,
            )
            if recovered_style is not None:
                recovered_style.reading_power_window_config = (
                    build_reading_power_window_config_from_settings(settings)
                )
                storage.save_json(
                    layout.style_profile_path,
                    recovered_style.model_dump(mode="json"),
                )
                _log.warning(
                    "风格规范主流程失败，已从日志回填 style_profile.json | error=%s | recovery=%s",
                    exc,
                    recovery_meta,
                )
                on_step(
                    "profile_style",
                    {"style_profile": recovered_style, "recovered_from_logs": recovery_meta},
                )
                return recovered_style

            if style_required:
                raise
            _log.warning("风格规范生成失败（不影响后续流程）：%s", exc)
            on_step("profile_style_failed", {"error": str(exc)})
            return None

    async def _task_entity_graph(
        character_bible_payload: dict[str, Any],
    ) -> tuple[EntityRegistry, Any]:
        """Build or resume the V2 entity graph before blueprint generation."""
        from novel_forge.core.schemas.init_v2 import EntityGraph

        def _matches_character_system(graph: EntityGraph) -> bool:
            expected = {
                (item.character_id, item.name)
                for item in character_system.roster
                if item.character_id and item.name
            }
            actual = {
                (item.entity_id, item.name)
                for item in graph.entities
                if item.entity_type == "character" and item.entity_id and item.name
            }
            return actual == expected

        upstreams = {
            "builder_version": ENTITY_GRAPH_BUILD_VERSION,
            "spec": hash_payload(enriched_spec),
            "story_bible": hash_payload(story_bible),
            "character_system": hash_payload(character_system),
            "blueprint_elements": hash_payload(element_selection),
        }
        cached_graph = init_v2_cache.load_success(
            "entity_graph",
            request_fingerprint=request_fingerprint,
            upstream_hashes=upstreams,
        )
        state_store = NarrativeStateStore(layout.root)
        if cached_graph is not None:
            graph = EntityGraph.model_validate(cached_graph)
            if _matches_character_system(graph):
                registry = EntityRegistry(entities=graph.entities)
                state_store.save_entity_registry(registry)
                storage.save_json(
                    state_store.root / "entity_graph.json",
                    graph.model_dump(mode="json"),
                )
                on_step(
                    "init_entity_graph_resumed",
                    {"entities": len(graph.entities), "links": len(graph.entity_links)},
                )
                _set_entity_registry_mode_metric(ctx, "resumed")
                return registry, graph

        # The raw narrative-state files have no build-version fingerprint.
        # Reusing them after a cache miss can silently resurrect entities made
        # by an older extractor, so only the versioned init-v2 cache may resume
        # this derived artifact. A miss deliberately rebuilds from source truth.
        character_registry, _baseline_graph = build_entity_graph(
            registry=EntityRegistry(),
            character_system=character_system,
            original_character_bible=source_character_bible,
        )
        deterministic_non_character_registry = _deterministic_non_character_registry(
            enriched_spec.model_dump(mode="json"),
            dump_story_bible_for_prompt(story_bible, mode="json"),
            element_selection.model_dump(mode="json"),
        )
        baseline_registry = _merge_entity_registries(
            character_registry,
            deterministic_non_character_registry,
        )

        registry = baseline_registry
        mode = "deterministic"
        needs_llm_supplement = _entity_registry_needs_llm_supplement(baseline_registry)
        if getattr(settings, "narrative_state_enabled", True) and needs_llm_supplement:
            try:
                entity_data = await ctx.call_with_retry(
                    TaskType.INIT_ENTITY_REGISTRY,
                    {
                        "spec": enriched_spec.model_dump(mode="json"),
                        "story_bible": dump_story_bible_for_prompt(story_bible, mode="json"),
                        "character_bible": character_bible_payload,
                        "character_system": character_system.model_dump(mode="json"),
                        "existing_entity_registry": baseline_registry.model_dump(mode="json"),
                        "entity_registry_scope": {
                            "mode": "supplement_non_character_entities_only",
                            "include_entity_types": [
                                "location",
                                "item",
                                "organization",
                                "concept",
                                "unknown",
                            ],
                            "exclude": "人物实体与人物关系已由 CharacterSystem 确定性生成。",
                        },
                    },
                    max_tokens=calculate_route_aware_max_tokens(
                        ctx.router,
                        TaskType.INIT_ENTITY_REGISTRY,
                        4200,
                        prompt_overhead=3500,
                        min_tokens=4096,
                    ),
                    temperature=getattr(settings, "temp_init_entity_registry", 0.2),
                    required_keys=("entities",),
                )
                registry = EntityRegistry.model_validate(entity_data)
                raw_entity_count = len(registry.entities)
                registry = _supplemental_non_character_registry(
                    registry,
                    baseline_registry=baseline_registry,
                )
                registry = _merge_entity_registries(baseline_registry, registry)
                mode = "deterministic_plus_llm"
                on_step(
                    "init_entity_registry",
                    {
                        "entities": len(registry.entities),
                        "raw_entities": raw_entity_count,
                        "deterministic_entities": len(baseline_registry.entities),
                        "mode": mode,
                    },
                )
            except Exception as exc:
                _log.warning(
                    "实体注册表生成失败，已回退到角色系统派生实体图谱 | error=%s",
                    exc,
                )
                on_step(
                    "init_entity_registry_failed",
                    {
                        "error": str(exc),
                        "error_type": type(exc).__name__,
                        "fallback": "deterministic_entity_graph",
                        "entities": len(registry.entities),
                    },
                )
                mode = "deterministic_fallback"
        elif not getattr(settings, "narrative_state_enabled", True):
            mode = "deterministic_narrative_state_disabled"
            on_step(
                "init_entity_registry",
                {
                    "entities": len(registry.entities),
                    "deterministic_entities": len(baseline_registry.entities),
                    "mode": mode,
                },
            )
        else:
            on_step(
                "init_entity_registry",
                {
                    "entities": len(registry.entities),
                    "deterministic_entities": len(baseline_registry.entities),
                    "mode": mode,
                },
            )
        _set_entity_registry_mode_metric(ctx, mode)

        registry, graph = build_entity_graph(
            registry=registry,
            character_system=character_system,
            original_character_bible=source_character_bible,
        )
        state_store.save_entity_registry(registry)
        storage.save_json(
            state_store.root / "entity_graph.json",
            graph.model_dump(mode="json"),
        )
        init_v2_cache.save_success(
            "entity_graph",
            request_fingerprint=request_fingerprint,
            upstream_hashes=upstreams,
            payload=graph.model_dump(mode="json"),
        )
        on_step(
            "init_entity_graph", {"entities": len(graph.entities), "links": len(graph.entity_links)}
        )
        return registry, graph

    # ── Phase C: StyleProfile ∥ EntityGraph ∥ KnowledgeBoundaries ──
    # When init_kb_phase_c_parallel is enabled, knowledge-boundary generation
    # runs alongside Phase C. Style/Entity prompts are built from a snapshot
    # taken before KB applies in-place (their prompt construction stays inside
    # the synchronous prefix, so the event loop guarantees determinism), and
    # the existing sensory post-sync below reconciles entity_graph afterwards.
    # Disable the switch to restore the original serial order (KB first, then
    # Style ∥ Entity with the post-KB character bible).
    kb_phase_c_parallel = bool(getattr(settings, "init_kb_phase_c_parallel", True))
    on_step("init_foundation_starting", {})
    phase_c_character_bible_payload = character_bible.model_dump(mode="json")
    if kb_phase_c_parallel:
        kb_task = asyncio.create_task(_task_knowledge_boundaries())
        style_task = asyncio.create_task(_task_style_profile(phase_c_character_bible_payload))
        entity_task = asyncio.create_task(_task_entity_graph(phase_c_character_bible_payload))
        _kb_result, style_profile_result, entity_graph_result = await asyncio.gather(
            kb_task,
            style_task,
            entity_task,
        )
    else:
        await _task_knowledge_boundaries()
        style_profile_result, entity_graph_result = await asyncio.gather(
            _task_style_profile(character_bible.model_dump(mode="json")),
            _task_entity_graph(character_bible.model_dump(mode="json")),
        )
    entity_registry, entity_graph = entity_graph_result

    # Health checks must run after KB has been applied to character_bible in
    # either branch, so they stay here instead of next to the task definition.
    record_character_bible_health(ctx, character_bible=character_bible)
    assert_upstream_health_allows_progress(ctx, artifact="character_bible")

    # ③ Generate StoryOutline（按模型能力感知批次串行生成）
    # NOTE: ``extra_instructions`` is intentionally excluded from outline_ctx.
    # Those rules were already encoded into story_bible.banned_intent_rules and
    # style_profile.forbidden_phrases during INIT_STORY_BIBLE / PROFILE_STYLE.
    # Re-injecting the raw text here caused the same rules to be consumed 3x
    # (preset → spec → outline), amplifying motif density. The outline should
    # derive its imagery from premise/characters, not from a raw instruction list.
    # Built after Phase C so character_bible already carries knowledge boundaries.
    outline_ctx = {key: value for key, value in base_ctx.items() if key != "extra_instructions"}
    outline_ctx.update(
        {
            "spec": enriched_spec,
            "story_bible": story_bible,
            "character_bible": character_bible.model_dump(mode="json"),
            "total_chapters": total_chapters,
            "words_per_chapter": words_per_chapter,
            "use_volume_mode": use_volume_mode,
            "blueprint_element_selection": element_selection.model_dump(mode="json"),
            "style_profile": None,
            "character_system": character_system.model_dump(mode="json"),
            "relationship_overview": build_relationship_prompt_overview(character_system),
            "entity_graph": None,
            "creative_director_packet": None,
        }
    )
    validate_plan_outline_context(
        outline_ctx,
        source="init_service.outline_ctx",
    )

    # ③a Generate NarrativeBlueprint（全局叙事蓝图）
    # Runs after StyleProfile for quality; if style generation is skipped or
    # recovered as None, the template still handles style_profile=None gracefully.
    outline_thinking_blueprint = ctx.is_outline_option_enabled(
        capability="thinking",
        enabled=settings.outline_thinking,
        allowed_providers_raw=settings.outline_thinking_providers,
        allowed_models_raw=settings.outline_thinking_models,
        task_type=TaskType.PLAN_OUTLINE,
    )

    _kb_name_to_sensory: dict[str, list[str]] = {}
    for profile in character_bible.characters:
        sensory = profile.knowledge_boundaries.sensory_access_rules
        if sensory:
            _kb_name_to_sensory[profile.name] = sensory
    if _kb_name_to_sensory:
        updated_entities: list[Any] = []
        for entity in entity_registry.entities:
            sensory_rules = _kb_name_to_sensory.get(entity.name)
            if sensory_rules and entity.entity_type == "character":
                entity = entity.model_copy(update={"sensory_access_rules": sensory_rules})
            updated_entities.append(entity)
        entity_registry = EntityRegistry(entities=updated_entities)
        try:
            entity_graph = entity_graph.model_copy(update={"entities": updated_entities})
            state_store = NarrativeStateStore(layout.root)
            state_store.save_entity_registry(entity_registry)
            storage.save_json(
                state_store.root / "entity_graph.json",
                entity_graph.model_dump(mode="json"),
            )
            on_step(
                "init_entity_graph_sensory_rules_synced",
                {"characters_with_sensory_rules": len(_kb_name_to_sensory)},
            )
        except Exception as exc:
            _log.warning(
                "init_entity_graph_sensory_sync_failed | error=%s | error_type=%s",
                exc,
                type(exc).__name__,
            )

    # Build the prompt allow-list from the final, persisted graph.  Doing this
    # before cleanup/sensory sync left a stale 700+ entity catalog in memory.
    init_entity_catalog = build_init_entity_catalog(entity_graph, character_bible)

    record_entity_graph_health(ctx, entity_graph=entity_graph)
    assert_upstream_health_allows_progress(ctx, artifact="entity_graph")

    from novel_forge.pipeline.long.services.narrative_evidence_sync import (
        sync_narrative_evidence,
    )

    evidence_sync = await sync_narrative_evidence(
        memory_context=ctx.memory_context,
        project_root=layout.root,
        ledger_entries=[],
    )
    on_step("init_narrative_evidence_sync", evidence_sync)

    if style_profile_result is not None:
        outline_ctx["style_profile"] = style_profile_result.model_dump(mode="json")
    outline_ctx["entity_graph"] = entity_graph.model_dump(mode="json")

    creative_upstreams = {
        "builder_version": CREATIVE_DIRECTOR_PACKET_BUILD_VERSION,
        "story_bible": hash_payload(story_bible),
        "character_system": hash_payload(character_system),
        "entity_graph": hash_payload(entity_graph),
        "blueprint_elements": hash_payload(element_selection),
    }
    cached_creative_packet = init_v2_cache.load_success(
        "creative_director_packet",
        request_fingerprint=request_fingerprint,
        upstream_hashes=creative_upstreams,
    )
    if cached_creative_packet is not None:
        from novel_forge.core.schemas.init_v2 import CreativeDirectorPacket

        creative_packet = CreativeDirectorPacket.model_validate(cached_creative_packet)
        on_step(
            "creative_director_packet_resumed",
            {
                "motifs": len(creative_packet.signature_motifs),
                "tensions": len(creative_packet.relationship_tensions),
            },
        )
    else:
        deterministic_creative_packet = build_creative_director_packet(
            story_bible=story_bible,
            character_system=character_system,
            element_selection=element_selection,
            entity_graph=entity_graph,
        )
        creative_packet = deterministic_creative_packet
        if effective_creative_exploration == "adaptive":
            from novel_forge.pipeline.long.services.init.init_creative_exploration import (
                merge_creative_director_packet,
                run_adaptive_creative_exploration,
            )
            from novel_forge.pipeline.long.services.init.init_v2 import StructuredTaskRunner

            exploration = await run_adaptive_creative_exploration(
                runner=StructuredTaskRunner(
                    ctx=ctx,
                    cache=init_v2_cache,
                    request_fingerprint=request_fingerprint,
                ),
                user_intent=base_ctx["user_intent"],
                creative_context={
                    "spec": enriched_spec.model_dump(mode="json"),
                    "story_bible": story_bible.model_dump(mode="json"),
                    "character_system": character_system.model_dump(mode="json"),
                    "blueprint_element_selection": element_selection.model_dump(mode="json"),
                    "research_evidence_pack": base_ctx["research_evidence_pack"],
                    "research_uncertainty": base_ctx["research_uncertainty"],
                    "language": enriched_spec.language,
                },
                deterministic_packet=deterministic_creative_packet,
                upstream_hashes=creative_upstreams,
                on_step=on_step,
                candidate_temperature=getattr(
                    settings,
                    "temp_init_creative_direction_candidates",
                    0.75,
                ),
                selection_temperature=getattr(
                    settings,
                    "temp_init_creative_direction_select",
                    0.15,
                ),
            )
            concept_choice = await _request_concept_copilot_gate(
                enabled_gates=enabled_copilot_gates,
                project_id=project_id,
                on_step=on_step,
                human_decision_provider=human_decision_provider,
                candidates=exploration.candidates,
                default_candidate_id=exploration.selected_candidate_id,
            )
            if concept_choice == "deterministic_fallback":
                creative_packet = deterministic_creative_packet
            elif concept_choice and concept_choice != exploration.selected_candidate_id:
                chosen = next(
                    (
                        candidate
                        for candidate in exploration.candidates
                        if candidate.candidate_id == concept_choice
                        and not candidate.intent_conflicts
                    ),
                    None,
                )
                creative_packet = (
                    merge_creative_director_packet(
                        seed=chosen.packet,
                        deterministic=deterministic_creative_packet,
                    )
                    if chosen is not None
                    else exploration.packet
                )
            else:
                creative_packet = exploration.packet
            on_step(
                "creative_direction_selected",
                {
                    "candidate_id": concept_choice or exploration.selected_candidate_id,
                    "model_calls": exploration.model_calls,
                    "used_fallback": exploration.used_fallback,
                    "fallback_reason": exploration.fallback_reason,
                },
            )
        init_v2_cache.save_success(
            "creative_director_packet",
            request_fingerprint=request_fingerprint,
            upstream_hashes=creative_upstreams,
            payload=creative_packet.model_dump(mode="json"),
        )
        on_step(
            "creative_director_packet",
            {
                "motifs": len(creative_packet.signature_motifs),
                "tensions": len(creative_packet.relationship_tensions),
            },
        )
    storage.save_json(
        layout.plans_dir / "creative_director_packet.json",
        creative_packet.model_dump(mode="json"),
    )
    record_creative_packet_health(ctx, creative_packet=creative_packet)
    assert_upstream_health_allows_progress(ctx, artifact="creative_director_packet")
    outline_ctx["creative_director_packet"] = creative_packet.model_dump(mode="json")

    init_coherence_profile: dict[str, Any] | None = None
    init_profile_mode = "single_task"
    set_init_profile_mode_metric(ctx, init_profile_mode)

    blueprint = await generate_or_resume_blueprint(
        ctx=ctx,
        layout=layout,
        storage=storage,
        total_chapters=total_chapters,
        element_selection=element_selection,
        init_v2_cache=init_v2_cache,
        request_fingerprint=request_fingerprint,
        enriched_spec=enriched_spec,
        character_system=character_system,
        entity_graph=entity_graph,
        outline_ctx=outline_ctx,
        creative_packet=creative_packet,
        settings=settings,
        outline_thinking_blueprint=outline_thinking_blueprint,
        use_volume_mode=use_volume_mode,
        on_step=on_step,
    )

    from novel_forge.pipeline.long.services.blueprint.blueprint_validation import validate_blueprint

    validation_result = validate_blueprint(
        blueprint,
        total_chapters=total_chapters,
        narrative_complexity=enriched_spec.narrative_complexity,
    )
    if validation_result.errors:
        _log.warning("blueprint_validation_failed | errors=%s", validation_result.errors)
    if validation_result.warnings:
        _log.info("blueprint_validation_warnings | warnings=%s", validation_result.warnings)
    on_step("plan_blueprint_validated", {"result": validation_result})

    if validation_result.errors:
        repair_outcome = await InitRepairOrchestrator(
            get_init_repair_policy(InitArtifact.BLUEPRINT)
        ).repair(
            blueprint,
            InitRepairContext(
                service_ctx=ctx,
                outline_ctx=outline_ctx,
                total_chapters=total_chapters,
                narrative_complexity=enriched_spec.narrative_complexity,
                outline_thinking=outline_thinking_blueprint,
            ),
        )
        _persist_init_repair_outcome(
            ctx,
            artifact="narrative_blueprint",
            outcome=repair_outcome,
        )
        blueprint = cast(NarrativeBlueprint, repair_outcome.payload)
        repaired_validation_result = validate_blueprint(
            blueprint,
            total_chapters=total_chapters,
            narrative_complexity=enriched_spec.narrative_complexity,
        )
        if repaired_validation_result.warnings:
            _log.info(
                "blueprint_repair_validation_warnings | warnings=%s",
                repaired_validation_result.warnings,
            )
        if repaired_validation_result.errors:
            _log.error(
                "blueprint_repair_validation_failed | errors=%s",
                repaired_validation_result.errors,
            )
            raise ValueError(
                "Blueprint repair failed validation: "
                + "; ".join(repaired_validation_result.errors)
            )
        storage.save_json(layout.blueprint_path, blueprint.model_dump(mode="json"))
        on_step(
            "plan_blueprint_repaired",
            {
                "blueprint": blueprint,
                "validation_result": repaired_validation_result,
                "repair_attempts": repair_outcome.attempts_as_dicts(),
            },
        )

    init_coherence_profile = load_reusable_init_coherence_profile(ctx, require_refined=True)
    if init_coherence_profile is None:
        on_step(
            "build_init_coherence_profile_start",
            {"status": "running", "profile_mode": init_profile_mode},
        )
        init_coherence_profile = await refine_init_coherence_profile(
            ctx,
            current_profile=_seed_init_coherence_profile(enriched_spec),
            spec=enriched_spec.model_dump(mode="json"),
            story_bible=dump_story_bible_for_prompt(story_bible, mode="json"),
            character_bible=character_bible.model_dump(mode="json"),
            creative_director_packet=creative_packet.model_dump(mode="json"),
            blueprint=blueprint.model_dump(mode="json"),
        )
        on_step(
            "build_init_coherence_profile",
            {
                "profile_mode": init_profile_mode,
                "genre_tags": init_coherence_profile.get("genre_tags", []),
                "state_axes": len(
                    (
                        init_coherence_profile.get("project_ontology", {})
                        if isinstance(init_coherence_profile.get("project_ontology", {}), dict)
                        else {}
                    ).get("state_axes", [])
                ),
                "payoff_types": len(
                    (
                        init_coherence_profile.get("project_ontology", {})
                        if isinstance(init_coherence_profile.get("project_ontology", {}), dict)
                        else {}
                    ).get("payoff_types", [])
                ),
                "summary": init_coherence_profile.get("summary", ""),
            },
        )
    if init_coherence_profile is None:
        raise InitCoherenceError("初始化一致性 v2 缺少项目画像。")
    init_coherence_profile = _with_init_entity_catalog(
        init_coherence_profile,
        entity_graph=entity_graph,
        character_bible=character_bible,
    )

    blueprint = await _maybe_refine_blueprint_creatively(
        ctx,
        spec=enriched_spec,
        story_bible=story_bible,
        character_bible=character_bible,
        creative_packet=creative_packet,
        init_coherence_profile=init_coherence_profile,
        blueprint=blueprint,
        total_chapters=total_chapters,
    )

    repair_round = 0
    local_blueprint_fallback_used = False
    blueprint_followup_repair_used = False
    blueprint_last_issue_keys: set[str] | None = None
    blueprint_stagnant_rounds = 0
    focus_chapters: list[int] | None = None
    while True:
        if init_coherence_profile is None:
            raise InitCoherenceError("初始化一致性 v2 缺少项目画像。")
        blueprint_artifacts = {BLUEPRINT_ARTIFACT: blueprint.model_dump(mode="json")}
        blueprint_report = _load_reusable_init_coherence_report(
            ctx,
            stage="blueprint_coherence",
            artifact=BLUEPRINT_ARTIFACT,
            artifacts=blueprint_artifacts,
        )
        if blueprint_report is None:
            blueprint_report = await run_init_coherence_v2_gate(
                ctx,
                stage="blueprint_coherence",
                repair_artifact=BLUEPRINT_ARTIFACT,
                profile=init_coherence_profile,
                artifacts=blueprint_artifacts,
                focus_chapters=focus_chapters,
            )
            _record_init_resume_decision(
                ctx,
                stage="blueprint_coherence",
                artifact=BLUEPRINT_ARTIFACT,
                action="regenerated",
                reason="cache_missing_or_rejected",
                expected_hashes=init_coherence_artifact_hashes(blueprint_artifacts),
            )
        init_coherence_reports["blueprint_coherence"] = blueprint_report
        repair_round = max(
            repair_round,
            _init_coherence_repair_round_start(
                settings,
                init_repairs,
                artifact=BLUEPRINT_ARTIFACT,
                report=blueprint_report,
            ),
        )
        if not _init_coherence_blocks(ctx, blueprint_report):
            break
        (
            stop_repair,
            stop_reason,
            blueprint_current_issue_keys,
            blueprint_stagnant_rounds,
        ) = _init_coherence_repair_stop_decision(
            settings,
            blueprint_report,
            previous_issue_keys=blueprint_last_issue_keys,
            stagnant_rounds=blueprint_stagnant_rounds,
        )
        if stop_repair:
            _record_init_coherence_repair_loop_stop(
                ctx,
                stage="blueprint_coherence",
                artifact=BLUEPRINT_ARTIFACT,
                report=blueprint_report,
                reason=stop_reason,
                previous_issue_keys=blueprint_last_issue_keys,
                current_issue_keys=blueprint_current_issue_keys,
                stagnant_rounds=blueprint_stagnant_rounds,
            )
            _save_init_readiness(
                ctx,
                reports=init_coherence_reports,
                repairs=init_repairs,
            )
            raise InitCoherenceError(
                "叙事蓝图一致性裁判停止自动修复："
                f"{stop_reason}；{blueprint_report.get('summary') or blueprint_report.get('verdict')}"
            )
        blueprint_last_issue_keys = blueprint_current_issue_keys
        auto_repair = bool(getattr(settings, "init_coherence_auto_repair", True))
        repair_limit_reached = repair_round >= _init_coherence_max_repair_rounds(settings)
        allow_followup_repair = (
            auto_repair
            and repair_limit_reached
            and (
                _init_coherence_allows_llm_followup_repair(
                    settings,
                    artifact=BLUEPRINT_ARTIFACT,
                    report=blueprint_report,
                    repairs=init_repairs,
                    repair_round=repair_round,
                    followup_used=blueprint_followup_repair_used,
                )
            )
        )
        if not auto_repair or (repair_limit_reached and not allow_followup_repair):
            if (
                auto_repair
                and not local_blueprint_fallback_used
                and _local_story_fallbacks_enabled(settings)
            ):
                fallback_blueprint = _try_local_blueprint_coherence_fallback(
                    ctx,
                    blueprint=blueprint,
                    report=blueprint_report,
                    round_index=repair_round + 1,
                    repairs=init_repairs,
                )
                if fallback_blueprint is not None:
                    local_blueprint_fallback_used = True
                    blueprint = fallback_blueprint
                    storage.save_json(layout.blueprint_path, blueprint.model_dump(mode="json"))
                    focus_chapters = _init_coherence_focus_chapters_after_repair(
                        ctx,
                        report=blueprint_report,
                        artifact=BLUEPRINT_ARTIFACT,
                        repairs=init_repairs,
                    )
                    continue
            _save_init_readiness(
                ctx,
                reports=init_coherence_reports,
                repairs=init_repairs,
            )
            raise InitCoherenceError(
                "叙事蓝图一致性裁判未通过："
                f"{blueprint_report.get('summary') or blueprint_report.get('verdict')}"
            )
        if allow_followup_repair:
            blueprint_followup_repair_used = True
        repair_round += 1
        repaired_blueprint_payload = await _repair_init_artifact_payload(
            ctx,
            artifact=BLUEPRINT_ARTIFACT,
            payload=blueprint.model_dump(mode="json"),
            report=blueprint_report,
            round_index=repair_round,
            repairs=init_repairs,
        )
        normalized_repair_payload = pre_normalize_blueprint_payload(
            repaired_blueprint_payload,
            total_chapters=total_chapters,
        )
        blueprint = NarrativeBlueprint.model_validate(normalized_repair_payload)
        storage.save_json(layout.blueprint_path, blueprint.model_dump(mode="json"))
        focus_chapters = _init_coherence_focus_chapters_after_repair(
            ctx,
            report=blueprint_report,
            artifact=BLUEPRINT_ARTIFACT,
            repairs=init_repairs,
        )

    await _request_init_copilot_gate(
        enabled_gates=enabled_copilot_gates,
        gate="blueprint",
        project_id=project_id,
        on_step=on_step,
        human_decision_provider=human_decision_provider,
        artifact=blueprint,
        message="请确认叙事蓝图可以进入编辑契约和章节大纲推导。第一版只支持继续或终止，不做局部重生。",
    )

    async def _derive_and_persist_editorial_contract() -> Any:
        from novel_forge.pipeline.long.services.init.init_source_resume import (
            _editorial_contract_input_hashes,
            _editorial_readiness_payload,
            _load_reusable_editorial_contract,
            _selected_editorial_element_ids,
        )

        selected_element_ids = _selected_editorial_element_ids(element_selection)
        editorial_input_hashes = _editorial_contract_input_hashes(
            title=getattr(story_bible, "title", "") or enriched_spec.title,
            total_chapters=total_chapters,
            story_bible=story_bible,
            character_bible=character_bible,
            style_profile=outline_ctx.get("style_profile"),
            blueprint=blueprint,
            blueprint_elements=element_selection,
            creative_director_packet=creative_packet,
        )
        cached_contract = _load_reusable_editorial_contract(
            ctx,
            total_chapters=total_chapters,
            selected_element_ids=selected_element_ids,
            input_hashes=editorial_input_hashes,
        )
        if cached_contract is not None:
            return cached_contract

        # Derive with a bounded regeneration loop. Unlike blueprint/outline
        # (which use run_init_coherence_v2_gate + _repair_init_artifact_payload),
        # the editorial contract has a local deterministic validator, so a
        # temperature-jittered regeneration is the right repair primitive: a
        # single bad generation (e.g. a stray colon corrupting an element_id
        # during JSON repair, or a malformed climax chapter) must not fatally
        # abort a 30+ minute init run. The loop is bounded by
        # init_coherence_max_repair_rounds and stops early on stagnation.
        max_rounds = _editorial_max_repair_rounds(settings)
        last_issue_fingerprints: set[str] | None = None
        stagnant_rounds = 0
        editorial_contract: EditorialContract | None = None
        editorial_readiness = None
        for round_index in range(1, max_rounds + 1):
            ctx.on_step(
                "derive_editorial_contract",
                {
                    "status": "running",
                    "round": round_index,
                    "max_rounds": max_rounds,
                },
            )
            try:
                with ctx.trace.step("derive_editorial_contract"):
                    editorial_contract = await EditorialContractStep(
                        ctx.router,
                        ctx.builder,
                        settings=settings,
                        trace=ctx.trace,
                        on_step=on_step,
                    ).run(
                        EditorialContractInput(
                            title=getattr(story_bible, "title", "") or enriched_spec.title,
                            total_chapters=total_chapters,
                            story_bible=dump_story_bible_for_prompt(story_bible, mode="json"),
                            character_bible=character_bible.model_dump(mode="json"),
                            style_profile=outline_ctx.get("style_profile"),
                            blueprint=blueprint.model_dump(mode="json"),
                            blueprint_elements=element_selection.model_dump(mode="json"),
                            creative_director_packet=creative_packet.model_dump(mode="json"),
                        )
                    )
            except PydanticValidationError as exc:
                event_payload = {
                    "round": round_index,
                    "max_rounds": max_rounds,
                    "reason": "schema_validation_failed",
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                }
                if round_index < max_rounds:
                    ctx.on_step("editorial_contract_repair_retry", event_payload)
                    continue
                ctx.on_step("editorial_contract_repair_stopped", event_payload)
                raise
            editorial_readiness = validate_editorial_contract(
                editorial_contract,
                total_chapters=total_chapters,
                selected_element_ids=selected_element_ids,
            )
            critical_findings = [
                item for item in editorial_readiness.findings if item.severity == "critical"
            ]
            if not critical_findings:
                break
            # Stagnation detection: if the same critical issue fingerprints
            # persist across rounds, stop early to avoid burning retries on a
            # model that keeps producing the same defect.
            current_fingerprints = {
                f"{item.issue_type}:{item.metadata}" for item in critical_findings
            }
            if last_issue_fingerprints is not None:
                overlap = last_issue_fingerprints & current_fingerprints
                if overlap and len(current_fingerprints) >= len(last_issue_fingerprints):
                    stagnant_rounds += 1
                else:
                    stagnant_rounds = 0
            last_issue_fingerprints = current_fingerprints
            if round_index < max_rounds and stagnant_rounds >= 1:
                ctx.on_step(
                    "editorial_contract_repair_stopped",
                    {
                        "round": round_index,
                        "max_rounds": max_rounds,
                        "reason": "no_blocking_issue_reduction",
                        "critical_count": len(critical_findings),
                        "summary": editorial_readiness.summary,
                    },
                )
                break
            ctx.on_step(
                "editorial_contract_repair_retry",
                {
                    "round": round_index,
                    "max_rounds": max_rounds,
                    "critical_count": len(critical_findings),
                    "summary": editorial_readiness.summary,
                },
            )
        assert editorial_contract is not None
        assert editorial_readiness is not None
        if any(item.severity == "critical" for item in editorial_readiness.findings):
            storage.save_json(
                layout.reports_dir / "init_editorial_readiness.json",
                editorial_readiness.model_dump(mode="json"),
            )
            raise InitCoherenceError(f"编辑契约校验未通过：{editorial_readiness.summary}")

        storage.save_json(
            layout.editorial_contract_path,
            editorial_contract.model_dump(mode="json"),
        )
        storage.save_json(
            layout.reports_dir / "init_editorial_readiness.json",
            _editorial_readiness_payload(
                editorial_contract,
                editorial_readiness,
                input_hashes=editorial_input_hashes,
            ),
        )
        manifest_for_context(ctx).record_success(
            artifact="editorial_contract",
            workflow="init_long",
            step="derive_editorial_contract",
            input_hashes=editorial_input_hashes,
            output_hashes={"editorial_contract": hash_payload(editorial_contract)},
            paths={
                "editorial_contract": str(layout.editorial_contract_path),
                "readiness": str(layout.reports_dir / "init_editorial_readiness.json"),
            },
            metadata={
                "voices": len(editorial_contract.character_voices),
                "climax_markers": len(editorial_contract.climax_markers),
                "symbols": len(editorial_contract.symbol_policies),
            },
        )
        on_step(
            "derive_editorial_contract",
            {
                "voices": len(editorial_contract.character_voices),
                "climax_markers": len(editorial_contract.climax_markers),
                "symbols": len(editorial_contract.symbol_policies),
                "path": str(layout.editorial_contract_path),
            },
        )
        return editorial_contract

    outline_claim_prefetch_tasks: list[asyncio.Task[Any]] = []
    try:
        outline_claim_prefetch_parallel = int(
            getattr(settings, "init_coherence_claim_max_parallel", 2) or 2
        )
    except (TypeError, ValueError):
        outline_claim_prefetch_parallel = 2
    outline_claim_prefetch_chunk_semaphore = asyncio.Semaphore(
        max(1, min(8, outline_claim_prefetch_parallel))
    )

    def _schedule_outline_claim_prefetch(
        batch_start: int,
        batch_end: int,
        chapters: list[Any],
    ) -> None:
        if (
            polish_hint
            or init_coherence_profile is None
            or not bool(getattr(settings, "init_stream_claim_prefetch_enabled", True))
        ):
            return
        chunks = build_outline_stream_claim_chunks(settings, chapters=chapters)
        if not chunks:
            return

        async def _run_prefetch() -> None:
            try:
                await prefetch_init_coherence_claim_chunks(
                    ctx,
                    stage="outline_inheritance",
                    profile=init_coherence_profile,
                    chunks=chunks,
                    concurrency_limiter=outline_claim_prefetch_chunk_semaphore,
                )
            except Exception as exc:
                _log.warning(
                    "outline_stream_claim_prefetch_failed | batch=%s-%s | error=%s",
                    batch_start,
                    batch_end,
                    exc,
                )
                ctx.on_step(
                    "extract_init_coherence_claims_failed",
                    {
                        "stage": "outline_inheritance",
                        "artifact": OUTLINE_ARTIFACT,
                        "batch_start": batch_start,
                        "batch_end": batch_end,
                        "error_type": type(exc).__name__,
                        "error": str(exc),
                        "fallback": "final_full_extract",
                    },
                )

        outline_claim_prefetch_tasks.append(asyncio.create_task(_run_prefetch()))

    async def _drain_outline_claim_prefetch() -> None:
        if not outline_claim_prefetch_tasks:
            return
        results = await asyncio.gather(*outline_claim_prefetch_tasks, return_exceptions=True)
        failed = [item for item in results if isinstance(item, Exception)]
        if failed:
            _log.warning(
                "outline_stream_claim_prefetch_partial_failure | failed=%s",
                len(failed),
            )

    def _persist_blueprint_sidecars() -> None:
        subplot_weave_validation = validate_subplot_weave(blueprint).to_dict()
        subplot_weave_validation_path = layout.reports_dir / "subplot_weave_validation.json"
        storage.save_json(subplot_weave_validation_path, subplot_weave_validation)
        on_step(
            "plan_blueprint_subplot_weave_validation",
            {
                "warnings": len(subplot_weave_validation.get("warnings", [])),
                "suggestions": len(subplot_weave_validation.get("suggestions", [])),
                "loop_check_passed": subplot_weave_validation.get("loop_check_passed", False),
                "feedback_check_passed": subplot_weave_validation.get(
                    "feedback_check_passed", False
                ),
                "path": str(subplot_weave_validation_path),
            },
        )

        subplot_execution_matrix = build_subplot_execution_matrix(blueprint).to_dict()
        storage.save_json(layout.subplot_execution_matrix_path, subplot_execution_matrix)
        on_step(
            "plan_blueprint_subplot_matrix",
            {
                "subplots": len(subplot_execution_matrix.get("rows", [])),
                "closed_loop_ratio": subplot_execution_matrix.get("closed_loop_ratio", 1.0),
                "path": str(layout.subplot_execution_matrix_path),
            },
        )

        blueprint_fragments = blueprint_to_fragments(blueprint)
        blueprint_fragment_upstreams = {
            "blueprint": hash_payload(blueprint),
            "creative_director_packet": hash_payload(creative_packet),
        }
        init_v2_cache.save_success(
            "blueprint_fragments",
            request_fingerprint=request_fingerprint,
            upstream_hashes=blueprint_fragment_upstreams,
            payload=blueprint_fragments.model_dump(mode="json"),
        )
        storage.save_json(
            layout.plans_dir / "narrative_blueprint_fragments.json",
            blueprint_fragments.model_dump(mode="json"),
        )
        on_step(
            "plan_blueprint_fragments",
            {
                "phases": len(blueprint_fragments.narrative_phases),
                "subplots": len(blueprint_fragments.subplot_plan),
                "suspense": len(blueprint_fragments.suspense_schedule),
            },
        )

    def _outline_has_id_first_fields(outline: StoryOutline) -> bool:
        if not _outline_entity_audit(
            outline.chapters,
            character_bible,
            entity_catalog=_outline_entity_catalog(
                entity_registry=entity_registry, character_bible=character_bible
            ),
        )["ok"]:
            return False
        hard_through = int(outline.hard_through_chapter or outline.total_chapters)
        for chapter in outline.chapters:
            if chapter.chapter_number > hard_through:
                continue
            if not str(getattr(chapter, "pov_character_id", "") or "").strip():
                return False
            cast_plan = getattr(chapter, "cast_plan", None)
            if not str(getattr(cast_plan, "pov_entity_id", "") or "").strip():
                return False
            emotional_plan = getattr(chapter, "emotional_plan", None)
            if not (
                str(getattr(emotional_plan, "subject_entity_id", "") or "").strip()
                and str(getattr(emotional_plan, "pressure_source", "") or "").strip()
                and str(getattr(emotional_plan, "relationship_choice", "") or "").strip()
            ):
                return False
        return True

    async def _load_or_generate_outline() -> StoryOutline:
        planning_target_end = (
            total_chapters
            if effective_planning_commitment == "full"
            else min(10, total_chapters)
        )
        design_target_end = (
            total_chapters
            if effective_planning_commitment == "full"
            else min(5, total_chapters)
        )
        recovered_outline_chapters = _load_partial_outline_chapters_from_session(
            storage,
            layout,
            total_chapters=total_chapters,
        )
        cached_outline = None
        if recovered_outline_chapters:
            on_step(
                "plan_outline_session_resume_preferred",
                {
                    "chapters_done": len(recovered_outline_chapters),
                    "chapters_total": total_chapters,
                    "ignored_canonical_outline": storage.exists(layout.outline_path),
                    "reason": "verified_outline_session_ledger",
                },
            )
        else:
            cached_outline = _load_cached_model_or_rollback(
                ctx,
                "outline",
                layout.outline_path,
                StoryOutline.model_validate,
            )
        if cached_outline is not None:
            outline = cached_outline
            if outline_h.is_outline_complete(outline) and (
                int(outline.planned_through_chapter or 0) < planning_target_end
                or int(outline.hard_through_chapter or 0) < design_target_end
            ):
                return await _batched_generate_outline(
                    ctx,
                    existing_outline=outline,
                    outline_ctx=outline_ctx,
                    blueprint=blueprint,
                    total_chapters=total_chapters,
                    words_per_chapter=words_per_chapter,
                    use_volume_mode=use_volume_mode,
                    effective_chapters_per_volume=effective_chapters_per_volume,
                    character_bible=character_bible,
                    entity_registry=entity_registry,
                    editorial_contract=editorial_contract,
                    on_outline_batch_ready=_schedule_outline_claim_prefetch,
                    target_start_chapter=int(outline.hard_through_chapter or 0) + 1,
                    target_end_chapter=planning_target_end,
                    design_through_chapter=design_target_end,
                    preserve_committed_chapters=True,
                )
            if outline_h.is_outline_complete(outline):
                planned_end = int(outline.planned_through_chapter or outline.total_chapters)
                outline = outline_h.normalize_outline_chapters(
                    outline,
                    total_chapters=planned_end,
                    words_per_chapter=words_per_chapter,
                )
                outline.total_chapters = total_chapters
                outline = outline_h.backfill_involved_characters(outline, character_bible)
                outline = outline_h.normalize_outline_volumes(
                    outline,
                    total_chapters=outline.total_chapters,
                    use_volume_mode=use_volume_mode,
                    chapters_per_volume=effective_chapters_per_volume,
                )
                if not _outline_has_id_first_fields(outline):
                    on_step(
                        "plan_outline_cache_rejected",
                        {
                            "reason": "missing_id_first_cast_or_emotional_plan",
                            "action": "regenerate_outline",
                            "path": str(layout.outline_path),
                        },
                    )
                    return await _batched_generate_outline(
                        ctx,
                        existing_outline=None,
                        outline_ctx=outline_ctx,
                        blueprint=blueprint,
                        total_chapters=total_chapters,
                        words_per_chapter=words_per_chapter,
                        use_volume_mode=use_volume_mode,
                        effective_chapters_per_volume=effective_chapters_per_volume,
                        character_bible=character_bible,
                        entity_registry=entity_registry,
                        editorial_contract=editorial_contract,
                        on_outline_batch_ready=_schedule_outline_claim_prefetch,
                        target_end_chapter=planning_target_end,
                        design_through_chapter=design_target_end,
                    )
                if not _cached_outline_matches_reveal_guard(
                    ctx,
                    editorial_contract=editorial_contract,
                    outline=outline,
                ):
                    if _repair_outline_reveal_guard_manifest_from_downstream(
                        ctx,
                        editorial_contract=editorial_contract,
                        outline=outline,
                    ):
                        on_step(
                            "plan_outline_reveal_guard_manifest_repaired",
                            {
                                "reason": "validated_downstream_resume",
                                "path": str(layout.outline_path),
                            },
                        )
                        storage.save_json(layout.outline_path, outline.model_dump(mode="json"))
                        if layout.outline_session_path.exists():
                            layout.outline_session_path.unlink()
                        on_step("plan_outline_resumed", outline)
                        return outline
                    on_step(
                        "plan_outline_cache_rejected",
                        {
                            "reason": "reveal_guard_manifest_mismatch",
                            "action": "regenerate_outline",
                            "path": str(layout.outline_path),
                        },
                    )
                    return await _batched_generate_outline(
                        ctx,
                        existing_outline=None,
                        outline_ctx=outline_ctx,
                        blueprint=blueprint,
                        total_chapters=total_chapters,
                        words_per_chapter=words_per_chapter,
                        use_volume_mode=use_volume_mode,
                        effective_chapters_per_volume=effective_chapters_per_volume,
                        character_bible=character_bible,
                        entity_registry=entity_registry,
                        editorial_contract=editorial_contract,
                        on_outline_batch_ready=_schedule_outline_claim_prefetch,
                        target_end_chapter=planning_target_end,
                        design_through_chapter=design_target_end,
                    )
                storage.save_json(layout.outline_path, outline.model_dump(mode="json"))
                if layout.outline_session_path.exists():
                    layout.outline_session_path.unlink()
                on_step("plan_outline_resumed", outline)
                return outline
            repair_report = repair_outline_resume_state(
                storage,
                layout,
                apply=True,
                total_chapters=total_chapters,
            )
            on_step(
                "plan_outline_partial_cache_quarantined",
                {
                    "path": str(layout.outline_path),
                    "moved_artifacts": repair_report.get("moved_artifacts", []),
                    "accepted_chapters_done": (
                        repair_report.get("checkpoints", {}).get("accepted_chapters_done", 0)
                        if isinstance(repair_report.get("checkpoints"), dict)
                        else 0
                    ),
                },
            )
            return await _batched_generate_outline(
                ctx,
                existing_outline=None,
                outline_ctx=outline_ctx,
                blueprint=blueprint,
                total_chapters=total_chapters,
                words_per_chapter=words_per_chapter,
                use_volume_mode=use_volume_mode,
                effective_chapters_per_volume=effective_chapters_per_volume,
                character_bible=character_bible,
                entity_registry=entity_registry,
                editorial_contract=editorial_contract,
                on_outline_batch_ready=_schedule_outline_claim_prefetch,
                target_end_chapter=planning_target_end,
                design_through_chapter=design_target_end,
            )
        return await _batched_generate_outline(
            ctx,
            existing_outline=None,
            outline_ctx=outline_ctx,
            blueprint=blueprint,
            total_chapters=total_chapters,
            words_per_chapter=words_per_chapter,
            use_volume_mode=use_volume_mode,
            effective_chapters_per_volume=effective_chapters_per_volume,
            character_bible=character_bible,
            entity_registry=entity_registry,
            editorial_contract=editorial_contract,
            on_outline_batch_ready=_schedule_outline_claim_prefetch,
            target_end_chapter=planning_target_end,
            design_through_chapter=design_target_end,
        )

    editorial_contract = await _derive_and_persist_editorial_contract()

    # ── Backfill TTS voice hints from editorial_contract → character_bible ──
    _backfill_tts_voice_hints_to_character_bible(
        character_bible=character_bible,
        editorial_contract=editorial_contract,
        storage=storage,
        layout=layout,
    )

    # ── Phase D/E overlap: pre-create narrative_state directory ──
    # While the outline LLM call is in progress, prepare the filesystem
    # structure that Phase E (initialize_narrative_state_and_contracts) will
    # need.  This avoids a cold mkdir + SQLite init at the D→E boundary.
    async def _prefetch_narrative_state_dir() -> None:
        try:
            ns_dir = layout.root / "narrative_state"
            ns_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass

    _ns_prefetch_task = asyncio.create_task(_prefetch_narrative_state_dir())

    try:
        _persist_blueprint_sidecars()
        outline = await _load_or_generate_outline()
    except Exception:
        if outline_claim_prefetch_tasks:
            await _drain_outline_claim_prefetch()
        _ns_prefetch_task.cancel()
        raise
    # Ensure prefetch completed (normally instant; await for safety)
    await _ns_prefetch_task

    # ── Optional outline polish ───────────────────────────────────────
    if polish_hint and outline:
        try:
            if (
                _load_reusable_outline_polish(
                    ctx,
                    outline=outline,
                    polish_hint=polish_hint,
                )
                is not None
            ):
                await _drain_outline_claim_prefetch()
            else:
                from novel_forge.pipeline.steps.polish_outline_step import (
                    PolishOutlineInput,
                    PolishOutlineStep,
                )

                input_outline_hash = hash_payload(outline)
                polish_input = PolishOutlineInput(
                    story_outline=outline,
                    user_hint=polish_hint,
                )
                polish_step = PolishOutlineStep(
                    ctx.router,
                    ctx.builder,
                    settings=settings,
                    trace=ctx.trace,
                )
                polish_result = await polish_step.run(polish_input)
                applied = polish_result.adjusted_outline is not None
                if polish_result.adjusted_outline is not None:
                    outline = polish_result.adjusted_outline
                    storage.save_json(
                        layout.outline_path,
                        outline.model_dump(mode="json"),
                    )
                    _log.info(
                        "outline_polish_applied | changed_chapters=%s",
                        polish_result.changed_chapters,
                    )
                _save_outline_polish_report(
                    ctx,
                    polish_hint=polish_hint,
                    input_outline_hash=input_outline_hash,
                    output_outline=outline,
                    changed_chapters=polish_result.changed_chapters,
                    applied=applied,
                )
                on_step(
                    "plan_outline_polish",
                    {
                        "changed_chapters": polish_result.changed_chapters,
                        "applied": applied,
                        "path": str(_outline_polish_report_path(ctx)),
                    },
                )
        except Exception as exc:
            _log.warning("outline_polish_skipped | error=%s", exc)
            on_step(
                "plan_outline_polish_skipped",
                {"error": str(exc), "error_type": type(exc).__name__},
            )

    await _drain_outline_claim_prefetch()

    outline_gate_response = await _request_init_copilot_gate(
        enabled_gates=enabled_copilot_gates,
        gate="outline",
        project_id=project_id,
        on_step=on_step,
        human_decision_provider=human_decision_provider,
        artifact=outline,
        message="请确认章节大纲是否可以进入继承裁判和章节契约生成。可用完整 StoryOutline JSON 替换当前大纲。",
        editable=True,
    )
    if outline_gate_response is not None and outline_gate_response.choice == "apply_edits":
        edited_payload = _init_gate_json_payload(outline_gate_response.custom_text)
        outline = StoryOutline.model_validate(edited_payload)
        storage.save_json(layout.outline_path, outline.model_dump(mode="json"))
        on_step(
            "init_copilot_outline_applied",
            {"chapters": len(getattr(outline, "chapters", []) or [])},
        )

    repair_round = 0
    outline_followup_repair_used = False
    outline_last_issue_keys: set[str] | None = None
    outline_stagnant_rounds = 0
    focus_chapters = None
    while True:
        if init_coherence_profile is None:
            raise InitCoherenceError("初始化一致性 v2 缺少项目画像。")
        outline_artifacts = {
            BLUEPRINT_ARTIFACT: blueprint.model_dump(mode="json"),
            OUTLINE_ARTIFACT: outline.model_dump(mode="json"),
        }
        outline_report = _load_reusable_init_coherence_report(
            ctx,
            stage="outline_inheritance",
            artifact=OUTLINE_ARTIFACT,
            artifacts=outline_artifacts,
        )
        if outline_report is None:
            outline_report = await run_init_coherence_v2_gate(
                ctx,
                stage="outline_inheritance",
                repair_artifact=OUTLINE_ARTIFACT,
                profile=init_coherence_profile,
                artifacts=outline_artifacts,
                focus_chapters=focus_chapters,
            )
            _record_init_resume_decision(
                ctx,
                stage="outline_inheritance",
                artifact=OUTLINE_ARTIFACT,
                action="regenerated",
                reason="cache_missing_or_rejected",
                expected_hashes=init_coherence_artifact_hashes(outline_artifacts),
            )
        init_coherence_reports["outline_inheritance"] = outline_report
        repair_round = max(
            repair_round,
            _init_coherence_repair_round_start(
                settings,
                init_repairs,
                artifact=OUTLINE_ARTIFACT,
                report=outline_report,
            ),
        )
        if not _init_coherence_blocks(ctx, outline_report):
            break
        (
            stop_repair,
            stop_reason,
            outline_current_issue_keys,
            outline_stagnant_rounds,
        ) = _init_coherence_repair_stop_decision(
            settings,
            outline_report,
            previous_issue_keys=outline_last_issue_keys,
            stagnant_rounds=outline_stagnant_rounds,
        )
        if stop_repair:
            _record_init_coherence_repair_loop_stop(
                ctx,
                stage="outline_inheritance",
                artifact=OUTLINE_ARTIFACT,
                report=outline_report,
                reason=stop_reason,
                previous_issue_keys=outline_last_issue_keys,
                current_issue_keys=outline_current_issue_keys,
                stagnant_rounds=outline_stagnant_rounds,
            )
            _save_init_readiness(
                ctx,
                reports=init_coherence_reports,
                repairs=init_repairs,
            )
            raise InitCoherenceError(
                "章节大纲继承裁判停止自动修复："
                f"{stop_reason}；{outline_report.get('summary') or outline_report.get('verdict')}"
            )
        outline_last_issue_keys = outline_current_issue_keys
        auto_repair = bool(getattr(settings, "init_coherence_auto_repair", True))
        repair_limit_reached = repair_round >= _init_coherence_max_repair_rounds(settings)
        allow_followup_repair = (
            auto_repair
            and repair_limit_reached
            and (
                _init_coherence_allows_llm_followup_repair(
                    settings,
                    artifact=OUTLINE_ARTIFACT,
                    report=outline_report,
                    repairs=init_repairs,
                    repair_round=repair_round,
                    followup_used=outline_followup_repair_used,
                )
            )
        )
        if not auto_repair or (repair_limit_reached and not allow_followup_repair):
            _save_init_readiness(
                ctx,
                reports=init_coherence_reports,
                repairs=init_repairs,
            )
            raise InitCoherenceError(
                "章节大纲继承裁判未通过："
                f"{outline_report.get('summary') or outline_report.get('verdict')}"
            )
        if allow_followup_repair:
            outline_followup_repair_used = True
        repair_round += 1
        repaired_outline_payload = await _repair_init_artifact_payload(
            ctx,
            artifact=OUTLINE_ARTIFACT,
            payload=outline.model_dump(mode="json"),
            report=outline_report,
            round_index=repair_round,
            repairs=init_repairs,
        )
        outline = StoryOutline.model_validate(repaired_outline_payload)
        storage.save_json(layout.outline_path, outline.model_dump(mode="json"))
        focus_chapters = _init_coherence_focus_chapters_after_repair(
            ctx,
            report=outline_report,
            artifact=OUTLINE_ARTIFACT,
            repairs=init_repairs,
        )

    outline_grounding_path = layout.reports_dir / "outline_research_grounding.json"
    outline_research_grounding = _load_reusable_outline_research_grounding(
        ctx,
        path=outline_grounding_path,
        outline=outline,
        dossier=research_dossier,
        enabled=research_enabled,
    )
    if outline_research_grounding is not None:
        on_step(
            "outline_research_grounding_resumed",
            {
                "status": outline_research_grounding.status,
                "path": str(outline_grounding_path),
            },
        )
    else:
        try:
            outline_research_grounding = await ground_outline_research(
                ctx=ctx,
                spec=enriched_spec,
                story_bible=story_bible,
                blueprint=blueprint,
                outline=outline,
                dossier=research_dossier,
                enabled=research_enabled,
            )
        except Exception as exc:  # noqa: BLE001 - grounding must not block initialization
            _log.warning("outline_research_grounding_failed | error=%s", exc)
            outline_research_grounding = failed_outline_research_grounding(
                dossier=research_dossier,
                outline=outline,
                settings=settings,
                error=exc,
            )
        storage.save_json(
            outline_grounding_path,
            outline_research_grounding.model_dump(mode="json"),
        )
        on_step(
            "outline_research_grounding",
            {
                "status": outline_research_grounding.status,
                "chapter_notes": len(outline_research_grounding.chapter_notes),
                "warnings": outline_research_grounding.warnings,
                "path": str(outline_grounding_path),
            },
        )
    hard_through_chapter = int(outline.hard_through_chapter or outline.total_chapters)
    outline_ctx["outline_research_grounding"] = outline_research_grounding.prompt_context(
        max_chapter_notes=max(1, hard_through_chapter)
    )

    narrative_contract_state = await initialize_narrative_state_and_contracts(
        ctx=ctx,
        layout=layout,
        story_bible=story_bible,
        character_bible=character_bible,
        blueprint=blueprint,
        outline=outline,
        language=language,
        words_per_chapter=words_per_chapter,
        settings=settings,
        on_step=on_step,
        project_id=project_id,
        entity_registry=entity_registry,
        init_entity_catalog=init_entity_catalog,
        outline_ctx=outline_ctx,
        total_chapters=hard_through_chapter,
        enriched_spec=enriched_spec,
        init_coherence_profile=init_coherence_profile,
        init_coherence_reports=init_coherence_reports,
        init_repairs=init_repairs,
        outline_research_grounding=outline_ctx.get("outline_research_grounding"),
    )
    llm_contract = cast(dict[str, Any], narrative_contract_state["llm_contract"])
    chapter_contracts = cast(dict[str, Any], narrative_contract_state["chapter_contracts"])
    await _request_init_copilot_gate(
        enabled_gates=enabled_copilot_gates,
        gate="contracts",
        project_id=project_id,
        on_step=on_step,
        human_decision_provider=human_decision_provider,
        artifact={
            "narrative_contract": llm_contract,
            "chapter_contracts": chapter_contracts,
        },
        message="请确认叙事契约与章节契约可以作为后续章节输入。第一版只支持继续或终止。",
    )
    readiness_report = _save_init_readiness(
        ctx,
        reports=init_coherence_reports,
        repairs=init_repairs,
    )

    async def _reaudit_source_repair_contracts(
        updated_contracts: dict[str, Any],
        focus_chapters: list[int],
    ) -> dict[str, Any]:
        entity_catalog = build_init_entity_catalog(
            entity_graph,
            character_bible,
            chapter_contracts=updated_contracts,
        )
        return await reaudit_changed_chapter_contracts(
            ctx=ctx,
            blueprint=blueprint,
            outline=outline,
            llm_contract=llm_contract,
            chapter_contracts=updated_contracts,
            project_id=project_id,
            init_entity_catalog=entity_catalog,
            outline_ctx=outline_ctx,
            total_chapters=total_chapters,
            narrative_complexity=enriched_spec.narrative_complexity,
            init_coherence_reports=init_coherence_reports,
            init_repairs=init_repairs,
            focus_chapters=focus_chapters,
            init_coherence_profile=init_coherence_profile,
            outline_research_grounding=outline_ctx.get("outline_research_grounding"),
        )

    chapter_contracts = await _persist_source_artifacts_from_init(
        ctx,
        project_id=project_id,
        spec=enriched_spec,
        story_bible=story_bible,
        character_bible=character_bible,
        character_system=character_system,
        entity_graph=entity_graph,
        style_profile=style_profile_result,
        creative_packet=creative_packet,
        blueprint=blueprint,
        outline=outline,
        narrative_contract=llm_contract,
        chapter_contracts=chapter_contracts,
        readiness_report=readiness_report,
        coherence_reports=init_coherence_reports,
        repairs=init_repairs,
        outline_ctx=outline_ctx,
        total_chapters=total_chapters,
        post_repair_reaudit=_reaudit_source_repair_contracts,
    )

    return await _finish_init_after_source_artifacts(
        project_id=project_id,
        ctx=ctx,
        story_bible=story_bible,
        character_bible=character_bible,
        outline=outline,
        entity_registry=entity_registry,
        entity_graph=entity_graph,
    )


def _backfill_tts_voice_hints_to_character_bible(
    *,
    character_bible: CharacterBible,
    editorial_contract: Any,
    storage: Any,
    layout: Any,
) -> None:
    """将 editorial_contract.character_voices 中的 tts_voice_hints 回写到 character_bible。

    editorial_contract 阶段产出了结构化的 CharacterVoiceProfile（含 tts_voice_hints），
    将其反向同步到 CharacterProfile.tts_voice_hints，使持久化后的 character_bible.json
    包含完整的结构化声纹数据。
    """
    try:
        voice_profiles = getattr(editorial_contract, "character_voices", None) or []
        if not voice_profiles:
            return

        # 按角色名建立索引
        voice_hints_by_name: dict[str, dict] = {}
        for vp in voice_profiles:
            hints = getattr(vp, "tts_voice_hints", None)
            if hints:
                name = getattr(vp, "character", "")
                if name:
                    voice_hints_by_name[name] = hints

        if not voice_hints_by_name:
            return

        backfilled = 0
        for profile in character_bible.characters:
            hints = voice_hints_by_name.get(profile.name)
            if hints is not None and profile.tts_voice_hints is None:
                profile.tts_voice_hints = hints
                backfilled += 1

        if backfilled > 0:
            storage.save_json(
                layout.characters_path,
                character_bible.model_dump(mode="json"),
            )
            _log.info(
                "tts_voice_hints_backfilled | count=%d",
                backfilled,
            )
    except Exception as exc:
        _log.warning(
            "tts_voice_hints_backfill_skipped | error=%s",
            exc,
        )
