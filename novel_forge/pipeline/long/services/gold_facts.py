"""Gold facts extractor — extracts hard facts from StoryKernel + NarrativeState.

These facts serve as the ground-truth baseline for summary drift detection.
The LLM judge compares summaries against these facts to detect omissions
or contradictions.

Input limits:
- Gold facts ≤ 50 entries
- Each fact ≤ 200 chars
"""

from __future__ import annotations

import logging

from novel_forge.story_kernel.schemas import StoryKernel

_log = logging.getLogger(__name__)

_MAX_GOLD_FACTS = 50
_MAX_FACT_CHARS = 200


def extract_gold_facts(
    kernel: StoryKernel,
    *,
    volume_start: int = 1,
    volume_end: int = 9999,
) -> list[str]:
    """Extract gold facts from StoryKernel for drift detection.

    Sources:
    - ``world_rules``: all active world rules
    - ``entities``: character status + location (active characters only)
    - ``knowledge_ledger``: secret_kept entries (secrets must not be lost)
    - ``promise_ledger``: planted/hinted/partially_paid promises
    - ``timeline``: major events in the current volume

    Returns a list of fact strings, capped at ``_MAX_GOLD_FACTS``.
    """
    facts: list[str] = []

    # --- 1. World rules (immutable, always critical) ---
    for rule in kernel.world_rules:
        fact = f"[世界规则:{rule.category}] {rule.rule_id}: {rule.content}"
        facts.append(fact[:_MAX_FACT_CHARS])

    # --- 2. Active character states ---
    for entity in kernel.entities:
        if entity.entity_type != "character":
            continue
        status = entity.attributes.get("status", entity.status)
        location = entity.attributes.get("location", "")
        fact = f"[角色状态] {entity.name}: status={status}"
        if location:
            fact += f", location={location}"
        facts.append(fact[:_MAX_FACT_CHARS])

    # --- 3. Secrets (knowledge_type = secret_kept) ---
    for entry in kernel.knowledge_ledger:
        if entry.knowledge_type in ("secret_kept", "secret"):
            fact = f"[秘密] {entry.entity_id} 知道: {entry.fact}"
            facts.append(fact[:_MAX_FACT_CHARS])

    # --- 4. Active promises (planted/hinted/partially_paid) ---
    for promise in kernel.promise_ledger:
        if promise.status in ("planted", "hinted", "partially_paid"):
            fact = f"[伏笔:{promise.status}] Ch{promise.planted_chapter}: {promise.description}"
            facts.append(fact[:_MAX_FACT_CHARS])

    # --- 5. Major timeline events in current volume ---
    for anchor in kernel.timeline:
        if volume_start <= anchor.chapter <= volume_end:
            if anchor.significance in ("major", "critical"):
                fact = f"[重大事件] Ch{anchor.chapter}: {anchor.event}"
                facts.append(fact[:_MAX_FACT_CHARS])

    # Cap at _MAX_GOLD_FACTS
    result = facts[:_MAX_GOLD_FACTS]
    _log.info(
        "extract_gold_facts | volume=%d-%d | total=%d (capped=%s)",
        volume_start,
        volume_end,
        len(facts),
        len(facts) > _MAX_GOLD_FACTS,
    )
    return result


__all__ = ["extract_gold_facts"]
