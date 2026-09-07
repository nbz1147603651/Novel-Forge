"""Expression channel profile management for MemoryContext.

Extracted from ``integration.py`` to reduce its size. These functions handle
loading, refreshing, and indexing expression channel observations.

All functions take ``ctx`` (the MemoryContext instance) as first argument,
following the same pattern as ``window_navigation.py``'s ``owner`` parameter.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any, cast

from novel_forge.memory.integration_utils import plan_field as _plan_field
from novel_forge.memory.integration_utils import plan_list_field as _plan_list_field
from novel_forge.memory.integration_utils import safe_load_json as _safe_load_json
from novel_forge.obs.logger import get_logger

if TYPE_CHECKING:
    from novel_forge.memory.integration import MemoryContext

_log = get_logger("memory.expression")


async def index_expression_observations_for_finalize(
    ctx: "MemoryContext",
    *,
    chapter_number: int,
    text: str,
    profiles: list[dict[str, Any]],
    chapter_plan: Any | None,
) -> dict[str, Any]:
    if ctx._expression_memory is None or ctx._router is None or ctx._builder is None:
        return {"observations": 0, "vectors": 0, "saved": False, "skipped": "unavailable"}
    from novel_forge.core.review.review_contracts import source_text_hash
    from novel_forge.pipeline.steps.expression_observation_step import (
        ExpressionObservationInput,
        ExpressionObservationStep,
    )

    plan_payload = chapter_plan or ctx._load_chapter_plan_for_memory(chapter_number)
    scene_intents = _plan_list_field(plan_payload, "scene_intents")[:8]
    pov_character = str(_plan_field(plan_payload, "pov_character", "") or "")
    step = ExpressionObservationStep(
        ctx._router,
        ctx._builder,
        settings=ctx.settings,
    )
    result = await step.run(
        ExpressionObservationInput(
            chapter_number=chapter_number,
            chapter_text=text,
            expression_channel_profiles=profiles,
            scene_intents=scene_intents,
            pov_character=pov_character,
        )
    )
    index_result = cast(
        dict[str, Any],
        await ctx._expression_memory.replace_chapter_observations(
            chapter_number=chapter_number,
            source_text_hash=source_text_hash(text),
            observations=list(result.observations),
        ),
    )
    index_result["skipped_reason"] = result.skipped_reason
    return index_result


def load_expression_profiles(ctx: "MemoryContext") -> list[dict[str, Any]]:
    if not ctx._storage or not ctx._project_id:
        return []
    try:
        from novel_forge.editorial.schemas import EditorialContract
        from novel_forge.persistence.models import ProjectLayout

        layout = ProjectLayout(ctx._storage.ensure_project_dir(ctx._project_id))
        if not ctx._storage.exists(layout.editorial_contract_path):
            return []
        payload = ctx._storage.load_json(layout.editorial_contract_path)
        contract = EditorialContract.model_validate(payload)
        return [
            item.model_dump(mode="json")
            for item in list(contract.expression_channel_profiles or [])[:8]
        ]
    except Exception as exc:
        _log.debug(
            "expression_profiles_load_failed | project=%s | error=%s",
            ctx._project_id,
            exc,
        )
        return []


async def refresh_expression_profiles_from_existing_project(
    ctx: "MemoryContext",
    layout: Any,
) -> list[dict[str, Any]]:
    if ctx._router is None or ctx._builder is None or ctx._storage is None:
        return []
    try:
        from novel_forge.core.constants import TaskType
        from novel_forge.editorial.schemas import (
            EditorialContract,
            normalize_expression_channel_profiles,
        )
        from novel_forge.model_runtime import StructuredModelService
        from novel_forge.pipeline.token_budget import route_max_output_budget

        if not ctx._storage.exists(layout.editorial_contract_path):
            return []
        contract_payload = ctx._storage.load_json(layout.editorial_contract_path)
        contract = EditorialContract.model_validate(contract_payload)
        chapter_samples: list[dict[str, Any]] = []
        for path in sorted(layout.chapters_dir.glob("chapter_*.md"))[:6]:
            match = re.search(r"chapter_(\d+)\.md$", path.name)
            if not match:
                continue
            text = path.read_text(encoding="utf-8")
            chapter_samples.append(
                {
                    "chapter_number": int(match.group(1)),
                    "sample": text[:1200],
                }
            )
        service = StructuredModelService(
            router=ctx._router,
            builder=ctx._builder,
            on_step=lambda _event, _data: None,
            settings=ctx.settings,
        )
        payload = await service.call_with_retry(
            TaskType.DERIVE_EDITORIAL_STYLE_CONSTRAINTS,
            {
                "project_brief": {"project_id": ctx._project_id},
                "story_bible": _safe_load_json(ctx._storage, layout.bible_path),
                "character_bible": _safe_load_json(ctx._storage, layout.characters_path),
                "style_profile": _safe_load_json(ctx._storage, layout.style_profile_path),
                "blueprint": {
                    "outline": _safe_load_json(ctx._storage, layout.outline_path),
                    "existing_chapter_samples": chapter_samples,
                },
                "blueprint_elements": _safe_load_json(
                    ctx._storage,
                    layout.blueprint_elements_path,
                ),
            },
            max_tokens=route_max_output_budget(
                ctx._router,
                TaskType.DERIVE_EDITORIAL_STYLE_CONSTRAINTS,
                min_tokens=6144,
            ),
            temperature=0.2,
            required_keys=(
                "theme_policies",
                "symbol_policies",
                "scene_resistance_rules",
                "expression_channel_budget",
                "expression_channel_profiles",
                "body_signal_budget_per_high_emotion_scene",
                "forbidden_confirmation_phrases",
                "revision_priorities",
            ),
        )
        profiles = normalize_expression_channel_profiles(
            (payload if isinstance(payload, dict) else {}).get("expression_channel_profiles", []),
            max_items=8,
            min_confidence=0.55,
            provenance="chapter_refresh",
        )
        if not profiles:
            return []
        updated_payload = contract.model_dump(mode="json")
        updated_payload["expression_channel_profiles"] = profiles
        updated_contract = EditorialContract.model_validate(updated_payload)
        ctx._storage.save_json(
            layout.editorial_contract_path,
            updated_contract.model_dump(mode="json"),
        )
        return [
            item.model_dump(mode="json")
            for item in updated_contract.expression_channel_profiles
        ]
    except Exception as exc:
        _log.warning(
            "expression_profiles_refresh_failed | project=%s | error=%s",
            ctx._project_id,
            exc,
        )
        return []
