"""ExtractKnowledgeDeltasStep — extract knowledge deltas from chapter text.

Identifies what characters learned or revealed in the current chapter,
producing structured deltas for the knowledge_ledger.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from novel_forge.common.constants import TaskType
from novel_forge.obs.logger import get_logger
from novel_forge.pipeline.steps.base import PipelineStep

_logger = get_logger("extract_knowledge_deltas")


@dataclass(frozen=True)
class KnowledgeDeltaInput:
    """Input for knowledge delta extraction."""

    chapter_number: int
    chapter_text: str
    pov_character: str = ""
    existing_knowledge_summaries: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class KnowledgeDeltaOutput:
    """Output from knowledge delta extraction."""

    knowledge_deltas: list[dict[str, Any]]
    raw_payload: dict[str, Any]


class ExtractKnowledgeDeltasStep(PipelineStep[KnowledgeDeltaInput, KnowledgeDeltaOutput]):
    """Extract knowledge deltas: what characters learned or revealed."""

    _REQUIRED_FIELDS = ("knowledge_deltas",)

    @property
    def step_name(self) -> str:
        return "extract_knowledge_deltas"

    def _build_context(self, input_data: KnowledgeDeltaInput) -> dict[str, Any]:
        ctx: dict[str, Any] = {
            "chapter_number": input_data.chapter_number,
            "chapter_text": input_data.chapter_text,
            "pov_character": input_data.pov_character,
        }
        if input_data.existing_knowledge_summaries:
            ctx["existing_knowledge_summaries"] = input_data.existing_knowledge_summaries
        return ctx

    def _estimate_max_tokens(self, chapter_length: int) -> int:
        """Estimate output token budget for knowledge delta extraction."""
        settings = self.settings
        base = int(getattr(settings, "extract_knowledge_deltas_base_tokens", 2048) or 2048)
        length_budget = int(
            getattr(settings, "extract_knowledge_deltas_tokens_per_2500_chars", 384) or 0
        )
        max_budget = int(getattr(settings, "extract_knowledge_deltas_max_tokens", 6144) or 6144)
        length_cost = int(chapter_length / 2500 * length_budget)
        estimated_tokens = base + length_cost
        target_output_chars = max(800, chapter_length // 4, int(estimated_tokens / 2.2))
        return self._dynamic_max_tokens(
            TaskType.EXTRACT_KNOWLEDGE_DELTAS,
            target_output_chars,
            prompt_overhead=2500,
            safety_margin=0.85,
            min_tokens=base,
            max_cap=max_budget,
        )

    @staticmethod
    def _normalize_deltas(
        raw: dict[str, Any],
        chapter_number: int,
    ) -> list[dict[str, Any]]:
        """Normalize the extracted knowledge deltas."""
        deltas_raw = raw.get("knowledge_deltas", [])
        if not isinstance(deltas_raw, list):
            return []

        normalized: list[dict[str, Any]] = []
        for item in deltas_raw:
            if not isinstance(item, dict):
                continue
            entity_id = str(item.get("entity_id", "")).strip()
            fact = str(item.get("fact", "")).strip()
            if not entity_id or not fact:
                continue

            knowledge_type = str(item.get("knowledge_type", "")).strip().lower()
            valid_types = {"known", "suspected", "misbelief", "secret_kept"}
            if knowledge_type not in valid_types:
                knowledge_type = "known"

            visibility = str(item.get("visibility", "")).strip().lower()
            valid_visibility = {"public", "private", "secret"}
            if visibility not in valid_visibility:
                visibility = "private"

            source_chapter = item.get("source_chapter", chapter_number)
            try:
                source_chapter = int(source_chapter)
            except (TypeError, ValueError):
                source_chapter = chapter_number

            normalized.append(
                {
                    "entity_id": entity_id,
                    "fact": fact,
                    "knowledge_type": knowledge_type,
                    "visibility": visibility,
                    "source_chapter": source_chapter,
                }
            )
        return normalized

    async def _execute(self, input_data: KnowledgeDeltaInput) -> KnowledgeDeltaOutput:
        ctx = self._build_context(input_data)
        chapter_length = len(input_data.chapter_text)
        max_tokens = self._estimate_max_tokens(chapter_length)

        try:
            data = await self._call_with_retry(
                TaskType.EXTRACT_KNOWLEDGE_DELTAS,
                ctx,
                max_tokens=max_tokens,
                temperature=float(
                    getattr(
                        self._settings,
                        "temp_extract_knowledge_deltas",
                        min(0.15, max(0.0, self._settings.temp_extract_canon)),
                    )
                ),
                required_keys=self._REQUIRED_FIELDS,
                max_retries=2,
                thinking=False,
            )
        except Exception as exc:
            _logger.warning(
                "extract_knowledge_deltas_failed | chapter=%d | error=%s",
                input_data.chapter_number,
                exc,
            )
            return KnowledgeDeltaOutput(knowledge_deltas=[], raw_payload={})

        deltas = self._normalize_deltas(data, input_data.chapter_number)
        if deltas:
            _logger.info(
                "extract_knowledge_deltas | chapter=%d | count=%d",
                input_data.chapter_number,
                len(deltas),
            )
        return KnowledgeDeltaOutput(knowledge_deltas=deltas, raw_payload=data)
