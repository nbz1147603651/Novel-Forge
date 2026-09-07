"""POV knowledge constraint builder — derives per-scene POV constraints from knowledge_ledger."""

from __future__ import annotations

from typing import Any

from novel_forge.core.schemas.continuity import PovKnowledgeConstraints


def _entry_str(value: Any) -> str:
    """Safely coerce a ledger field to a stripped string."""
    return str(value or "").strip()


def _entity_display_name(entry: dict[str, Any]) -> str:
    """Return the best human-readable name for a ledger entry's entity."""
    return (
        _entry_str(entry.get("entity_name"))
        or _entry_str(entry.get("character"))
        or _entry_str(entry.get("entity_id"))
        or "未知角色"
    )


def _is_forbidden_entry(
    entry: dict[str, Any],
    pov_character_id: str,
    chapter_number: int,
) -> bool:
    """Return True if this ledger entry represents knowledge the POV character should not have."""
    entity_id = _entry_str(entry.get("entity_id"))
    knowledge_type = _entry_str(entry.get("knowledge_type")).lower()
    visibility = _entry_str(entry.get("visibility")).lower()

    try:
        source_chapter = int(entry.get("source_chapter", 0) or 0)
    except (TypeError, ValueError):
        source_chapter = 0
    try:
        revealed_in_chapter = int(entry.get("revealed_in_chapter", 0) or 0)
    except (TypeError, ValueError):
        revealed_in_chapter = 0

    # Future knowledge — acquired after the current chapter
    if source_chapter > chapter_number:
        return True
    if revealed_in_chapter > chapter_number:
        return True

    # Other entity's private/secret knowledge
    if entity_id and entity_id != pov_character_id:
        if visibility in ("private", "secret"):
            return True
        if knowledge_type in ("secret_kept", "misbelief"):
            return True

    return False


def _build_abstract_description(
    entry: dict[str, Any],
    chapter_number: int,
) -> str:
    """Build an ABSTRACT description of forbidden knowledge — never the fact text itself.

    IMPORTANT: This prevents leaking secrets to the LLM via the prompt.
    """
    entity_name = _entity_display_name(entry)
    knowledge_type = _entry_str(entry.get("knowledge_type")).lower()
    visibility = _entry_str(entry.get("visibility")).lower()

    try:
        source_chapter = int(entry.get("source_chapter", 0) or 0)
    except (TypeError, ValueError):
        source_chapter = 0
    try:
        revealed_in_chapter = int(entry.get("revealed_in_chapter", 0) or 0)
    except (TypeError, ValueError):
        revealed_in_chapter = 0

    # Future knowledge
    if source_chapter > chapter_number:
        return f"来自第{source_chapter}章的后续发展信息（{entity_name}）"
    if revealed_in_chapter > chapter_number:
        return f"第{revealed_in_chapter}章才可公开揭示的信息（{entity_name}）"

    # Type-specific abstract descriptions
    if knowledge_type == "secret_kept":
        return f"【{entity_name}】主动隐瞒的信息"
    if knowledge_type == "misbelief":
        return f"【{entity_name}】的错误认知"
    if knowledge_type == "suspected" and visibility in ("private", "secret"):
        return f"【{entity_name}】的私密怀疑"
    if knowledge_type == "known" and visibility in ("private", "secret"):
        return f"【{entity_name}】的私密已知事实"

    # Fallback for other private/secret entries from other entities
    return f"【{entity_name}】的私密信息"


def compute_pov_constraints(
    pov_character_id: str,
    pov_character_name: str,
    chapter_number: int,
    knowledge_ledger: list[dict[str, Any]],
    entity_sensory_rules: dict[str, list[str]] | None = None,
    *,
    scope_label: str = "limited",
    max_forbidden: int = 12,
) -> PovKnowledgeConstraints:
    """Compute POV knowledge constraints from the knowledge_ledger.

    This is a pure function — no LLM calls, no side effects.

    Strategy:
    - Filter knowledge_ledger for entries the POV character should NOT know:
      - entity_id != pov_character_id AND visibility in ("private", "secret")
      - OR source_chapter > chapter_number (future knowledge)
      - OR knowledge_type in ("secret_kept", "misbelief") from other entities
    - For each forbidden entry, generate an ABSTRACT description (not the fact text itself)
    - Include sensory_access_rules from entity_registry for the POV character

    IMPORTANT: The ``forbidden_knowledge`` field MUST contain abstract descriptions
    (e.g., "玄昱的私密动作细节") NOT the actual fact text (e.g., "残玉断口严丝合缝").
    This prevents leaking secrets to the LLM via the prompt.
    """
    if not pov_character_id:
        return PovKnowledgeConstraints(scope_label=scope_label)

    forbidden: list[str] = []
    seen: set[str] = set()

    for entry in knowledge_ledger or []:
        if not isinstance(entry, dict):
            continue
        if not _is_forbidden_entry(entry, pov_character_id, chapter_number):
            continue

        description = _build_abstract_description(entry, chapter_number)
        if description and description not in seen:
            seen.add(description)
            forbidden.append(description)
            if len(forbidden) >= max_forbidden:
                break

    # Sensory limits for the POV character
    sensory_limits: list[str] = []
    if entity_sensory_rules:
        raw_limits = entity_sensory_rules.get(pov_character_id, [])
        if isinstance(raw_limits, list):
            sensory_limits = [_entry_str(rule) for rule in raw_limits if _entry_str(rule)]

    return PovKnowledgeConstraints(
        forbidden_knowledge=forbidden,
        sensory_limits=sensory_limits,
        scope_label=scope_label,
    )
