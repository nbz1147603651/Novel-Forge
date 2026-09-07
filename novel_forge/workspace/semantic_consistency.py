"""Durable semantic refresh executed by the existing JobService."""

from __future__ import annotations

from typing import Any

from novel_forge.core.domain.world_context import dump_story_bible_for_prompt
from novel_forge.core.schemas.bible import StoryBible
from novel_forge.core.schemas.spec import StorySpec
from novel_forge.persistence.authoring_store import AuthoringStore, story_input_version
from novel_forge.persistence.repair_shadow import RepairShadowLedger, RepairShadowPolicy
from novel_forge.persistence.semantic_consistency import (
    SEMANTIC_STAGE_SPECS,
    clear_semantic_compile_failure,
    load_semantic_source_inputs,
    record_semantic_compile_failure,
    semantic_affected_chapters,
    semantic_consistency_view,
    semantic_source_fingerprint,
    semantic_stage_inputs,
)
from novel_forge.pipeline.long.services.init.init_coherence_profile import (
    load_reusable_init_coherence_profile,
    refine_init_coherence_profile,
)
from novel_forge.pipeline.long.services.init.init_coherence_v2 import (
    run_init_coherence_v2_gate,
    semantic_compiler_runtime_fingerprint,
)
from novel_forge.pipeline.long.services.init.init_context import build_init_context
from novel_forge.pipeline.long.services.init.init_source_resume import (
    _seed_init_coherence_profile,
)
from novel_forge.pipeline.long.services.upstream_compass import (
    find_outline_plan_duration_conflicts,
)
from novel_forge.workspace.authoring_control import planning_authority
from novel_forge.workspace.contracts import SemanticConsistencyRefreshRequest
from novel_forge.workspace.runtime import RuntimeServices


async def refresh_semantic_consistency(
    runtime: RuntimeServices,
    request: SemanticConsistencyRefreshRequest,
    *,
    on_step_progress: Any = None,
) -> dict[str, Any]:
    """Compile current sources using existing prompts, ledger and repair cases."""

    root = runtime.storage.existing_project_dir(request.project_id)
    store = AuthoringStore(root)
    policy = store.policy()
    if policy is None:
        raise ValueError("请先选择协作模式，再刷新语义一致性")
    if policy.version != request.expected_policy_version:
        raise ValueError("授权版本已变化；旧刷新请求未执行")
    if story_input_version(root) != request.expected_story_version:
        raise ValueError("故事来源已变化；请基于当前版本重新刷新")
    store.require_enabled()

    runner = runtime.chapter_runner(
        on_step_progress=on_step_progress,
        warn_missing_memory_context=False,
    )
    ctx = build_init_context(runner, request.project_id)
    sources, _stored_profile = load_semantic_source_inputs(runtime.storage, ctx.layout)
    affected_chapters = semantic_affected_chapters(sources)
    if not affected_chapters:
        raise ValueError("当前来源没有可编译的章节范围")
    required = {
        "spec",
        "story_bible",
        "character_bible",
        "blueprint",
        "outline",
        "narrative_contract",
        "chapter_contracts",
    }
    missing = sorted(required - set(sources))
    if missing:
        raise ValueError("缺少语义编译来源：" + "、".join(missing))

    shadow_only = not bool(
        getattr(runtime.settings, "long_semantic_consistency_blocking", False)
    )
    shadow_ledger: RepairShadowLedger | None = None
    shadow_logical_id = ""
    initial_source_fingerprint = semantic_source_fingerprint(sources, _stored_profile)
    legacy_fallback_verdict = (
        "conflict"
        if find_outline_plan_duration_conflicts(
            outline=sources.get("outline", {}),
            plan={},
        )
        else "no_conflict"
    )
    main_evaluation_id = f"main:{initial_source_fingerprint}:{policy.version}"
    if shadow_only:
        shadow_ledger = RepairShadowLedger(root)
        admission = shadow_ledger.admit(
            project_id=request.project_id,
            case_id=f"semantic-source:{request.expected_story_version}",
            candidate_version=policy.version,
            policy=RepairShadowPolicy.from_settings(runtime.settings),
        )
        if not admission.admitted:
            if callable(on_step_progress):
                on_step_progress(
                    "semantic_consistency_shadow_skipped",
                    {
                        "reason": admission.reason,
                        "sample_value": admission.sample_value,
                        "shadow_only": True,
                    },
                )
            return {
                "project_id": request.project_id,
                "shadow_only": True,
                "shadow_skipped": True,
                "reason": admission.reason,
                "semantic_consistency": semantic_consistency_view(
                    runtime.storage,
                    ctx.layout,
                    expected_runtime_fingerprint=semantic_compiler_runtime_fingerprint(ctx),
                ).model_dump(mode="json"),
                "reports": {},
            }
        shadow_logical_id = admission.logical_id

    # R7a shadow execution may persist reusable compiler evidence, but it may
    # not create/transition RepairCases.  Blocking runs retain the normal SC2
    # reconciliation path.
    ctx._semantic_repair_cases_disabled = shadow_only

    reports: dict[str, dict[str, Any]] = {}
    runtime_fingerprint = semantic_compiler_runtime_fingerprint(ctx)
    try:
        profile = load_reusable_init_coherence_profile(ctx, require_refined=True) or {}
        if not profile:
            spec = StorySpec.model_validate(runtime.storage.load_json(ctx.layout.spec_path))
            story_bible = StoryBible.model_validate(
                runtime.storage.load_json(ctx.layout.bible_path)
            )
            character_bible = ctx.coerce_character_bible(
                runtime.storage.load_json(ctx.layout.characters_path)
            )
            profile = await refine_init_coherence_profile(
                ctx,
                current_profile=_seed_init_coherence_profile(spec),
                spec=spec.model_dump(mode="json"),
                story_bible=dump_story_bible_for_prompt(story_bible, mode="json"),
                character_bible=character_bible.model_dump(mode="json"),
                creative_director_packet=None,
                blueprint=sources["blueprint"],
            )

        stage_inputs = semantic_stage_inputs(sources)
        with planning_authority(runtime, request.project_id, affected_chapters):
            for stage, repair_artifact, keys in SEMANTIC_STAGE_SPECS:
                artifacts = stage_inputs[stage]
                if set(artifacts) != set(keys):
                    raise ValueError(f"{stage} 语义来源不完整")
                if callable(on_step_progress):
                    on_step_progress(
                        "semantic_consistency_stage_started",
                        {"stage": stage, "affected_chapters": affected_chapters},
                    )
                reports[stage] = await run_init_coherence_v2_gate(
                    ctx,
                    stage=stage,
                    repair_artifact=repair_artifact,
                    profile=profile,
                    artifacts=artifacts,
                )
                if callable(on_step_progress):
                    on_step_progress(
                        "semantic_consistency_stage_completed",
                        {
                            "stage": stage,
                            "verdict": reports[stage].get("verdict"),
                            "issue_count": len(reports[stage].get("issues", []) or []),
                        },
                    )
    except Exception as exc:
        if shadow_ledger is not None and shadow_logical_id:
            shadow_ledger.record_semantic_judgment(
                shadow_logical_id,
                source_hash=initial_source_fingerprint,
                verdict="failed",
                structured_success=False,
                routed_to_human=False,
                model_call_id=shadow_logical_id,
                legacy_fallback_verdict=legacy_fallback_verdict,
            )
        elif not shadow_only:
            RepairShadowLedger(root).record_semantic_main_gate_judgment(
                main_evaluation_id,
                source_hash=initial_source_fingerprint,
                verdict="failed",
                structured_success=False,
                routed_to_human=False,
                model_call_id=main_evaluation_id,
                legacy_fallback_verdict=legacy_fallback_verdict,
            )
        if story_input_version(root) == request.expected_story_version:
            failure_sources, failure_profile = load_semantic_source_inputs(
                runtime.storage, ctx.layout
            )
            record_semantic_compile_failure(
                runtime.storage,
                ctx.layout,
                source_fingerprint=semantic_source_fingerprint(
                    failure_sources, failure_profile
                ),
                runtime_fingerprint=runtime_fingerprint,
                reason=f"语义编译失败：{exc}",
            )
        raise

    if story_input_version(root) != request.expected_story_version:
        raise ValueError("语义编译期间来源已变化；本次结果不得作为章节门禁")
    current_policy = store.policy()
    if current_policy is None or current_policy.version != request.expected_policy_version:
        raise ValueError("语义编译期间授权已变化；本次结果不得作为章节门禁")
    clear_semantic_compile_failure(runtime.storage, ctx.layout)
    projection = semantic_consistency_view(
        runtime.storage,
        ctx.layout,
        expected_runtime_fingerprint=runtime_fingerprint,
    )
    if callable(on_step_progress):
        on_step_progress(
            "semantic_consistency_completed",
            projection.model_dump(mode="json"),
        )
    verdict = {
        "clean": "accept",
        "conflict": "needs_repair",
        "review_required": "ambiguous",
    }.get(projection.status, "failed")
    if shadow_ledger is not None and shadow_logical_id:
        shadow_ledger.record_semantic_judgment(
            shadow_logical_id,
            source_hash=projection.source_fingerprint or initial_source_fingerprint,
            verdict=verdict,
            structured_success=verdict != "failed",
            routed_to_human=verdict == "ambiguous",
            model_call_id=shadow_logical_id,
            report_id=projection.report_id,
            legacy_fallback_verdict=legacy_fallback_verdict,
        )
    elif not shadow_only:
        RepairShadowLedger(root).record_semantic_main_gate_judgment(
            main_evaluation_id,
            source_hash=projection.source_fingerprint or initial_source_fingerprint,
            verdict=verdict,
            structured_success=verdict != "failed",
            routed_to_human=verdict == "ambiguous",
            model_call_id=main_evaluation_id,
            report_id=projection.report_id,
            legacy_fallback_verdict=legacy_fallback_verdict,
        )
    return {
        "project_id": request.project_id,
        "shadow_only": shadow_only,
        "semantic_consistency": projection.model_dump(mode="json"),
        "reports": {
            stage: {
                "verdict": report.get("verdict"),
                "blocked": bool(report.get("blocked")),
                "issue_count": len(report.get("issues", []) or []),
            }
            for stage, report in reports.items()
        },
    }


__all__ = ["refresh_semantic_consistency"]
