"""Canon-state helper functions for MemoryContext.

Extracted from ``integration.py`` to reduce its size. Handles loading
forbidden element seeds and extracting chapter outline data from canon state.

All functions take ``ctx`` (the MemoryContext instance) as first argument.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from novel_forge.obs.logger import get_logger

if TYPE_CHECKING:
    from novel_forge.memory.integration import MemoryContext

_log = get_logger("memory.canon_helpers")


def load_forbidden_seeds(ctx: "MemoryContext") -> dict[str, list[str]] | None:
    """Load forbidden element seeds from project config for motif warmup."""
    if not ctx._storage or not ctx._project_id:
        return None
    try:
        from novel_forge.core.domain.forbidden_element_registry import ForbiddenElementRegistry

        project_path = ctx._storage.project_path(ctx._project_id)
        registry = ForbiddenElementRegistry(project_path)
        merged = registry.merge_sources()
        return {
            "意象": merged.get("rhetorical_imagery_hints", []),
            "符号": merged.get("kinship_and_address_terms", []),
        }
    except Exception as exc:
        _log.debug("forbidden_seeds_load_failed | error=%s", exc)
        return None


def extract_chapter_outline_from_canon(
    ctx: "MemoryContext",
    chapter_number: int,
) -> dict[str, Any] | None:
    """Extract chapter outline data from canon state for motif extraction context."""
    if not ctx._storage or not ctx._project_id:
        return None
    try:
        canon_path = (
            ctx._storage.project_path(ctx._project_id) / "canon" / "canon_current.json"
        )
        if not ctx._storage.exists(canon_path):
            return None
        canon_state = ctx._storage.load_json(canon_path)
        chapter_plans = canon_state.get("chapter_plans", {})
        chapter_key = str(chapter_number)
        if chapter_key not in chapter_plans:
            return None
        plan = chapter_plans[chapter_key]
        if not isinstance(plan, dict):
            return None
        outline = {
            "goal": plan.get("chapter_goal", "") or plan.get("goal", ""),
            "pov_character": plan.get("pov_character", "") or plan.get("pov", ""),
            "element_focus": plan.get("element_focus", []) or [],
        }
        optional_keys = (
            "title",
            "chapter_title",
            "summary",
            "chapter_summary",
            "synopsis",
            "chapter_contract",
            "contract",
            "narrative_contract",
            "required_outcomes",
            "must_include",
            "must_happen",
            "scene_intents",
            "scene_goals",
            "beats",
            "relevant_entities",
            "entities",
            "characters",
            "motif_requirements",
            "motifs",
            "symbolic_requirements",
        )
        for key in optional_keys:
            if key in plan and plan.get(key):
                outline[key] = plan.get(key)
        return outline
    except Exception as exc:
        _log.debug(
            "chapter_outline_extract_failed | chapter=%d | error=%s", chapter_number, exc
        )
        return None
