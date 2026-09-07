"""Episodic fact enrichment for compression service."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from novel_forge.memory.episodic import EpisodicMemory


@dataclass
class EpisodicFactEnricher:
    episodic_memory: "EpisodicMemory | None" = None
    max_enrichment_facts: int = 10

    async def enrich_facts(
        self,
        query: str,
        current_chapter: int,
        existing_facts: list[str],
        top_k: int = 8,
    ) -> list[str]:
        if self.episodic_memory is None:
            return existing_facts

        semantic_results = await self.episodic_memory.search_by_semantic(
            query=query,
            chapter_range=(1, current_chapter - 1),
            top_k=top_k,
        )

        new_facts = [result.event_summary for result in semantic_results]
        all_facts = list(dict.fromkeys(existing_facts + new_facts))

        return all_facts[: self.max_enrichment_facts]
