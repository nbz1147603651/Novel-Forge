"""Outline and blueprint orchestration helpers extracted from init_service.py."""

from __future__ import annotations

from typing import Any

from novel_forge.pipeline.long.services.init.init_common import (
    BlueprintElementSelection,
    InitLongServiceContext,
    InitV2BlockCache,
    NarrativeBlueprint,
    StorySpec,
    TaskType,
    _load_cached_json_or_rollback,
    _rollback_cached_init_step,
    assemble_blueprint_from_fragments,
    blueprint_to_fragments,
    calculate_route_aware_max_tokens,
    hash_payload,
    pre_normalize_blueprint_payload,
    validate_plan_outline_context,
)
from novel_forge.pipeline.long.services.init.init_story_bible import (
    _BLUEPRINT_FULL_KEYS,
    _blueprint_spine_budget,
    _compact_blueprint_prompt_snapshot,
    _partial_blueprint_validation_report,
)


async def generate_or_resume_blueprint(
    *,
    ctx: InitLongServiceContext,
    layout: Any,
    storage: Any,
    total_chapters: int,
    element_selection: BlueprintElementSelection,
    init_v2_cache: InitV2BlockCache,
    request_fingerprint: str,
    enriched_spec: StorySpec,
    character_system: Any,
    entity_graph: Any,
    outline_ctx: dict[str, Any],
    creative_packet: Any,
    settings: Any,
    outline_thinking_blueprint: bool,
    use_volume_mode: bool,
    on_step: Any,
) -> NarrativeBlueprint:
    raw_blueprint = _load_cached_json_or_rollback(ctx, "blueprint", layout.blueprint_path)
    if raw_blueprint is not None:
        try:
            _norm_blueprint = pre_normalize_blueprint_payload(
                raw_blueprint, total_chapters=total_chapters
            )
            bp = NarrativeBlueprint.model_validate(_norm_blueprint)
            if bp.element_selection is None:
                bp = bp.model_copy(update={"element_selection": element_selection})
                storage.save_json(layout.blueprint_path, bp.model_dump(mode="json"))
            on_step("plan_blueprint_resumed", {"blueprint": bp})
            return bp
        except Exception as exc:
            _rollback_cached_init_step(ctx, "blueprint", exc)

    from novel_forge.core.schemas.init_v2 import BlueprintFragments

    narrative_complexity = str(
        getattr(enriched_spec, "narrative_complexity", "standard") or "standard"
    )
    full_blueprint_keys = _BLUEPRINT_FULL_KEYS
    blueprint_mode = "quality_first"

    def _blueprint_context_hashes(
        *,
        mode: str,
        prior_fragments: dict[str, Any] | None = None,
    ) -> dict[str, str]:
        payload: dict[str, Any] = {
            "spec": enriched_spec,
            "story_bible": outline_ctx.get("story_bible"),
            "character_bible": outline_ctx.get("character_bible"),
            "character_system": character_system,
            "entity_graph": entity_graph,
            "style_profile": outline_ctx.get("style_profile"),
            "blueprint_element_selection": element_selection,
            "creative_director_packet": creative_packet,
            "relationship_overview": outline_ctx.get("relationship_overview"),
            "blueprint_mode": mode,
        }
        hashes = {"outline_context": hash_payload(payload)}
        if prior_fragments is not None:
            hashes["prior_fragments"] = hash_payload(prior_fragments)
        return hashes

    def _save_blueprint_fragments(bp: NarrativeBlueprint, *, assembly_mode: str) -> None:
        fragments = blueprint_to_fragments(bp)
        fragments.assembly_mode = assembly_mode
        storage.save_json(
            layout.plans_dir / "narrative_blueprint_fragments.json",
            fragments.model_dump(mode="json"),
        )

    fragment_specs: dict[str, dict[str, Any]] = {
        "overview": {
            "title": "全书概述与分卷",
            "required_keys": ("synopsis", "volume_mode", "volumes"),
            "token_hint": 1800,
            "instructions": [
                "输出全书概述、是否分卷、分卷范围和每卷叙事目标。",
                "synopsis 必须明确主线冲突、情感/主题承诺和终局方向。",
                "若 use_volume_mode=false，volume_mode=false 且 volumes=[]。",
            ],
        },
        "phases": {
            "title": "叙事阶段",
            "required_keys": ("narrative_phases",),
            "token_hint": max(2200, total_chapters * 60),
            "instructions": [
                f"把全书拆为 3-6 个阶段，必须连续覆盖 1..{total_chapters} 章。",
                "每个阶段写清目标、关键事件、张力水平、时间上下文、核心场景与核心人物。",
            ],
        },
        "turning_points": {
            "title": "关键转折与因果链",
            "required_keys": ("key_turning_points", "causal_chains", "subversion_points"),
            "token_hint": max(3400, total_chapters * 35 + 1800),
            "instructions": [
                "规划 5-10 个关键转折，章节号必须落在有效章节内。",
                "转折应改变目标、信息、关系或局势，避免只有氛围变化。",
                "规划 2-4 条因果链，描述转折如何引发跨章节的连锁反应。",
                "规划 2-3 个反套路点，打破读者预期但逻辑自洽。",
                "反套路点必须有 setup_chapters 铺垫，justification 说明为什么合理。",
            ],
        },
        "character_arcs": {
            "title": "角色弧光与情感弧线",
            "required_keys": ("character_arcs", "emotional_arcs"),
            "token_hint": 6200,
            "instructions": [
                "为核心角色规划弧光与阶段性里程碑。",
                "里程碑必须落在叙事阶段范围内，并与人物关系/身份链接保持一致。",
                "milestones 每项只允许 chapter_start、chapter_end、description 三个字段。",
                "规划 2-4 条情感弧线，定义故事的情绪节奏。",
                "每条情感弧线必须有 peak_chapters (高峰) 和 valley_chapters (低谷)。",
                "情感弧线应覆盖全书章节，与角色弧光和叙事阶段对齐。",
            ],
        },
        "subplots": {
            "title": "支线规划与碰撞点",
            "required_keys": ("subplot_plan", "subplot_collisions"),
            "token_hint": max(4400, total_chapters * 85 + 1200),
            "instructions": [
                "规划可执行支线，每条支线必须有 involved_chapters、chapter_events、weave_links。",
                "每条支线至少 3 个 chapter_events，且事件章节必须属于 involved_chapters。",
                "每条支线至少 2 个 weave_links，必须同时包含主线触发支线与支线反哺主线。",
                "weave_links 中每个 trigger_chapter 必须包含在 involved_chapters 中；若反哺发生在某章，该章必须同时出现在 involved_chapters。",
                "weave_links 必须是 JSON 对象数组；禁止空字符串占位，禁止把 source_type 等字段摊平在数组内。",
                "chapter_events[].depends_on 必须始终是 JSON 数组。",
                "规划 3-5 个支线碰撞点，描述多条支线在同一章节的交互。",
                "每个碰撞点必须有 involved_subplots (至少2条)、collision_chapter、collision_type。",
                "collision_type 必须是：冲突、互助、误导、融合、竞争。",
                "碰撞点应有 ripple_effects 描述对其他支线的影响。",
            ],
        },
        "suspense": {
            "title": "悬念规划",
            "required_keys": ("suspense_schedule",),
            "token_hint": 2200,
            "instructions": [
                "规划 3-8 条核心悬念，必须绑定 related_subplot。",
                "每条悬念必须包含 suspense_type、introduce_chapter、resolve_chapter、urgency_level、strand_affinity。",
            ],
        },
        "ending": {
            "title": "收束策略",
            "required_keys": ("ending_strategy",),
            "token_hint": 1200,
            "instructions": [
                "输出最终收束策略，说明主线、情感线、主题承诺和关键悬念如何落地。",
                "不要新增 JSON 字段，只返回 ending_strategy。",
            ],
        },
    }

    fragment_order = tuple(fragment_specs.keys())

    async def _call_blueprint_fragment(
        block_key: str,
        *,
        prior_fragments: dict[str, Any],
    ) -> dict[str, Any]:
        spec = fragment_specs[block_key]
        required_keys = tuple(spec["required_keys"])
        block_index = fragment_order.index(block_key) + 1
        block_total = len(fragment_order)
        block_title = str(spec["title"])
        prior_snapshot = dict(prior_fragments)
        prompt_snapshot = _compact_blueprint_prompt_snapshot(
            prior_snapshot,
            fragment_key=block_key,
        )
        upstream_hashes = _blueprint_context_hashes(
            mode=blueprint_mode,
            prior_fragments=prior_snapshot,
        )
        cached = init_v2_cache.load_success(
            f"blueprint_{block_key}",
            request_fingerprint=request_fingerprint,
            upstream_hashes=upstream_hashes,
        )
        if cached is not None:
            on_step(
                f"plan_blueprint_{block_key}_resumed",
                {
                    "block": block_key,
                    "block_title": block_title,
                    "block_index": block_index,
                    "block_total": block_total,
                    "keys": list(required_keys),
                },
            )
            payload = {key: cached.get(key) for key in required_keys if key in cached}
            normalized_payload = pre_normalize_blueprint_payload(
                payload,
                total_chapters=total_chapters,
            )
            if isinstance(normalized_payload, dict):
                return {
                    key: normalized_payload.get(key)
                    for key in required_keys
                    if key in normalized_payload
                }
            return payload

        fragment_ctx = {
            **outline_ctx,
            "blueprint_generation_mode": "quality_first_expansion",
            "blueprint_fragment_request": {
                "block_key": block_key,
                "title": spec["title"],
                "required_keys": list(required_keys),
                "instructions": spec["instructions"],
            },
            "blueprint_fragments_so_far": prompt_snapshot,
        }
        validate_plan_outline_context(
            fragment_ctx,
            source=f"init_service.plan_blueprint_{block_key}",
        )
        try:
            with ctx.trace.step(f"plan_blueprint_{block_key}"):
                max_tokens = calculate_route_aware_max_tokens(
                    ctx.router,
                    TaskType.PLAN_OUTLINE,
                    int(spec["token_hint"]),
                    prompt_overhead=5000,
                    min_tokens=2048,
                )
                data = await ctx.call_with_retry(
                    TaskType.PLAN_OUTLINE,
                    fragment_ctx,
                    max_tokens=max_tokens,
                    temperature=settings.temp_plan_outline,
                    required_keys=required_keys,
                    thinking=outline_thinking_blueprint,
                    include_contract_required_keys=False,
                )
        except Exception as exc:
            init_v2_cache.save_error(
                f"blueprint_{block_key}",
                request_fingerprint=request_fingerprint,
                upstream_hashes=upstream_hashes,
                error=exc,
            )
            raise

        payload = {key: data.get(key) for key in required_keys if key in data}
        normalized_payload = pre_normalize_blueprint_payload(
            payload,
            total_chapters=total_chapters,
        )
        if isinstance(normalized_payload, dict):
            payload = {
                key: normalized_payload.get(key)
                for key in required_keys
                if key in normalized_payload
            }
        init_v2_cache.save_success(
            f"blueprint_{block_key}",
            request_fingerprint=request_fingerprint,
            upstream_hashes=upstream_hashes,
            payload=payload,
        )
        on_step(
            f"plan_blueprint_{block_key}",
            {
                "block": block_key,
                "block_title": block_title,
                "block_index": block_index,
                "block_total": block_total,
                "keys": list(required_keys),
            },
        )
        return payload

    async def _call_blueprint_spine() -> dict[str, Any]:
        """Generate the quality-first global causal spine before block expansion."""
        required_keys = full_blueprint_keys
        upstream_hashes = _blueprint_context_hashes(mode="quality_first_spine")
        cached = init_v2_cache.load_success(
            "blueprint_spine",
            request_fingerprint=request_fingerprint,
            upstream_hashes=upstream_hashes,
        )
        if cached is not None:
            on_step(
                "plan_blueprint_spine_resumed",
                {"block": "spine", "keys": list(required_keys)},
            )
            normalized_cached = pre_normalize_blueprint_payload(
                cached,
                total_chapters=total_chapters,
            )
            if isinstance(normalized_cached, dict):
                return {
                    key: normalized_cached.get(key)
                    for key in required_keys
                    if key in normalized_cached
                }
            return {key: cached.get(key) for key in required_keys if key in cached}

        spine_ctx = {
            **outline_ctx,
            "blueprint_generation_mode": "quality_first_spine",
            "blueprint_fragment_request": {
                "block_key": "spine",
                "title": "全局叙事骨架",
                "required_keys": list(required_keys),
                "instructions": [
                    "先整体决定主线、转折、人物变化、支线入口/反哺/收束的因果关系。",
                    "输出所有蓝图顶层字段，但保持骨架级紧凑，不展开成章节大纲。",
                    "支线必须绑定主线转折或人物命运节点，避免独立平行推进。",
                    "关键转折至少规划 5 个；长篇复杂度越高，越要覆盖开局触发、中段升级、低谷反转、终局收束。",
                    "每条 primary/normal 支线必须先写清主线触发、反哺主线与最终收束目标。",
                    "每条悬念必须预先绑定主线或支线，并说明引入与兑现章节，避免后续扩展时悬念漂移。",
                ],
            },
            "blueprint_fragments_so_far": {},
        }
        validate_plan_outline_context(
            spine_ctx,
            source="init_service.plan_blueprint_spine",
        )
        try:
            with ctx.trace.step("plan_blueprint_spine"):
                spine_target_chars, spine_min_tokens = _blueprint_spine_budget(
                    total_chapters=total_chapters,
                    narrative_complexity=narrative_complexity,
                    volume_mode=use_volume_mode,
                )
                max_tokens = calculate_route_aware_max_tokens(
                    ctx.router,
                    TaskType.PLAN_OUTLINE,
                    spine_target_chars,
                    prompt_overhead=6000,
                    min_tokens=spine_min_tokens,
                )
                data = await ctx.call_with_retry(
                    TaskType.PLAN_OUTLINE,
                    spine_ctx,
                    max_tokens=max_tokens,
                    temperature=settings.temp_plan_outline,
                    required_keys=required_keys,
                    thinking=outline_thinking_blueprint,
                    include_contract_required_keys=False,
                )
        except Exception as exc:
            init_v2_cache.save_error(
                "blueprint_spine",
                request_fingerprint=request_fingerprint,
                upstream_hashes=upstream_hashes,
                error=exc,
            )
            raise

        payload = {key: data.get(key) for key in required_keys if key in data}
        normalized_payload = pre_normalize_blueprint_payload(
            payload,
            total_chapters=total_chapters,
        )
        if isinstance(normalized_payload, dict):
            payload = {
                key: normalized_payload.get(key)
                for key in required_keys
                if key in normalized_payload
            }
        init_v2_cache.save_success(
            "blueprint_spine",
            request_fingerprint=request_fingerprint,
            upstream_hashes=upstream_hashes,
            payload=payload,
        )
        storage.save_json(layout.plans_dir / "narrative_blueprint_spine.json", payload)
        on_step("plan_blueprint_spine", {"block": "spine", "keys": list(required_keys)})
        return payload

    def _assemble_and_persist_fragments(
        fragments_data: dict[str, Any],
        *,
        assembly_mode: str,
    ) -> NarrativeBlueprint:
        normalized_fragments_data = pre_normalize_blueprint_payload(
            fragments_data,
            total_chapters=total_chapters,
        )
        if isinstance(normalized_fragments_data, dict):
            fragments_data = normalized_fragments_data
        fragments = BlueprintFragments.model_validate(fragments_data)
        fragments.assembly_mode = assembly_mode
        storage.save_json(
            layout.plans_dir / "narrative_blueprint_fragments.json",
            fragments.model_dump(mode="json"),
        )
        normalized_blueprint_data = pre_normalize_blueprint_payload(
            fragments.model_dump(mode="json"),
            total_chapters=total_chapters,
        )
        if not isinstance(normalized_blueprint_data, dict):
            normalized_blueprint_data = fragments.model_dump(mode="json")
        bp = assemble_blueprint_from_fragments(
            normalized_blueprint_data,
            element_selection=element_selection,
        )
        storage.save_json(layout.blueprint_path, bp.model_dump(mode="json"))
        return bp

    async def _generate_quality_first_blueprint() -> NarrativeBlueprint:
        fragments_data = await _call_blueprint_spine()
        for block_key in fragment_order:
            fragments_data.update(
                await _call_blueprint_fragment(block_key, prior_fragments=fragments_data)
            )
            precheck_report = _partial_blueprint_validation_report(
                fragments_data,
                block_key=block_key,
                total_chapters=total_chapters,
                narrative_complexity=narrative_complexity,
            )
            if precheck_report:
                storage.save_json(
                    layout.reports_dir / f"blueprint_{block_key}_precheck.json",
                    precheck_report,
                )
                on_step(
                    "plan_blueprint_fragment_precheck",
                    {
                        "block": block_key,
                        "status": precheck_report.get("status", "issues_found"),
                        "errors": len(precheck_report.get("errors", [])),
                        "warnings": len(precheck_report.get("warnings", [])),
                        "suggestions": len(precheck_report.get("suggestions", [])),
                    },
                )
        bp = _assemble_and_persist_fragments(
            fragments_data,
            assembly_mode="quality_first_serial_expansion",
        )
        on_step("plan_blueprint", {"blueprint": bp, "source": "quality_first"})
        return bp

    return await _generate_quality_first_blueprint()
