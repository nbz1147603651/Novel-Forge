"""Adaptive Compression Service.

Provides context compression with quality verification, dynamic threshold
adjustment, and fact preservation to ensure critical information is retained.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from novel_forge.core.constants import TaskType
from novel_forge.gateway.router import ModelRouter
from novel_forge.memory.base import CompressionResult, CompressionStats
from novel_forge.obs.logger import get_logger
from novel_forge.pipeline.token_budget import calculate_route_aware_max_tokens
from novel_forge.prompts.builder import PromptBuilder

if TYPE_CHECKING:
    from novel_forge.memory.episodic import EpisodicMemory

_log = get_logger("memory.compression")


@dataclass
class CompressionConfig:
    """Configuration for adaptive compression."""

    # Quality thresholds
    min_quality_score: float = 0.7
    aggressive_quality_threshold: float = 0.5  # Below this, use fact-priority mode

    # Token budgets
    soft_limit_chars: int = 2200
    hard_limit_chars: int = 5200

    # Dynamic adjustment
    enable_dynamic_thresholds: bool = True
    context_window_tokens: int = 32000  # Model's context window

    # Fact preservation
    preserve_facts_priority: bool = True
    preserve_causal_links: bool = True
    preserve_character_states: bool = True

    # Compression strategies
    strategy: Literal["balanced", "aggressive", "conservative"] = "balanced"

    def get_target_compression_ratio(self, estimated_chars: int) -> float:
        """Calculate target compression ratio based on budget pressure."""
        if estimated_chars <= self.soft_limit_chars:
            # No compression needed
            return 1.0

        # Calculate pressure (0.0 - 1.0)
        overflow_ratio = min(
            1.0,
            (estimated_chars - self.soft_limit_chars)
            / max(1, self.hard_limit_chars - self.soft_limit_chars),
        )

        # Map pressure to compression ratio
        if self.strategy == "conservative":
            # Compress less, preserve more
            return max(0.5, 1.0 - overflow_ratio * 0.5)
        elif self.strategy == "aggressive":
            # Compress more
            return max(0.2, 1.0 - overflow_ratio * 0.8)
        else:  # balanced
            return max(0.3, 1.0 - overflow_ratio * 0.7)


class AdaptiveCompressionService:
    """Adaptive compression with quality verification.

    Features:
    - Dynamic threshold adjustment based on model context window
    - Quality verification after compression
    - Fact preservation priority mode
    - Compression history tracking for optimization
    """

    def __init__(
        self,
        router: ModelRouter,
        builder: PromptBuilder,
        config: CompressionConfig | None = None,
        episodic_memory: "EpisodicMemory | None" = None,
    ) -> None:
        self._router = router
        self._builder = builder
        self._config = config or CompressionConfig()
        self._episodic_memory = episodic_memory

        # Compression history for optimization
        self._stats: dict[str, CompressionStats] = {}

        _log.info(
            "AdaptiveCompressionService initialized | config=%s",
            {
                "soft_limit": self._config.soft_limit_chars,
                "hard_limit": self._config.hard_limit_chars,
                "min_quality": self._config.min_quality_score,
                "strategy": self._config.strategy,
            },
        )

    async def _enrich_context_facts(
        self,
        query: str,
        current_chapter: int,
        existing_facts: list[str] | None,
    ) -> list[str]:
        if self._episodic_memory is None:
            return existing_facts or []

        try:
            from novel_forge.memory.compression_enricher import EpisodicFactEnricher

            enricher = EpisodicFactEnricher(
                episodic_memory=self._episodic_memory,
                max_enrichment_facts=10,
            )
            return await enricher.enrich_facts(
                query=query,
                current_chapter=current_chapter,
                existing_facts=existing_facts or [],
            )
        except Exception as exc:
            _log.warning(
                "fact_enrichment_failed | query=%s | error=%s | falling_back", query[:50], exc
            )
            return existing_facts or []

    async def compress_with_quality_check(
        self,
        original_text: str,
        target_chars: int,
        task_type: TaskType,
        context_facts: list[str] | None = None,
        current_chapter: int = 0,
    ) -> CompressionResult:
        """Compress text with quality verification.

        Args:
            original_text: Original text to compress
            target_chars: Target character count
            task_type: Task type (for routing and stats)
            context_facts: Critical facts that must be preserved
            current_chapter: Current chapter number for episodic enrichment

        Returns:
            CompressionResult with quality score
        """
        start_time = time.monotonic()

        enriched_facts = await self._enrich_context_facts(
            query=original_text,
            current_chapter=current_chapter,
            existing_facts=context_facts,
        )

        _log.info(
            "compression_start | task_type=%s | original_length=%d | target_chars=%d | facts=%d",
            task_type.value,
            len(original_text),
            target_chars,
            len(enriched_facts),
        )

        try:
            # Step 1: Initial compression
            compressed = await self._llm_compress(
                text=original_text,
                target_chars=target_chars,
                task_type=task_type,
            )

            # Step 2: Quality verification
            quality_score = await self._verify_compression_quality(
                original=original_text,
                compressed=compressed,
                task_type=task_type,
                required_facts=enriched_facts,
            )

            # Step 3: If quality is low, retry with fact-priority mode
            if quality_score < self._config.min_quality_score:
                _log.info(
                    "compression_low_quality | task_type=%s | quality=%.2f | min_required=%.2f | retrying",
                    task_type.value,
                    quality_score,
                    self._config.min_quality_score,
                )

                compressed = await self._compress_with_facts_priority(
                    text=original_text,
                    target_chars=target_chars,
                    required_facts=enriched_facts,
                )

                # Re-verify quality
                quality_score = await self._verify_compression_quality(
                    original=original_text,
                    compressed=compressed,
                    task_type=task_type,
                    required_facts=enriched_facts,
                )

            # Step 4: Calculate metrics
            compression_ratio = len(compressed) / len(original_text) if original_text else 1.0
            elapsed_ms = (time.monotonic() - start_time) * 1000

            # Step 5: Record stats
            self._record_stats(task_type.value, quality_score, compression_ratio)

            # Step 6: Extract retained facts
            retained_facts = self._extract_retained_facts(compressed, enriched_facts)

            _log.info(
                "compression_done | task_type=%s | original=%d | compressed=%d | ratio=%.2f | quality=%.2f | facts_retained=%d/%d | elapsed_ms=%.2f",
                task_type.value,
                len(original_text),
                len(compressed),
                compression_ratio,
                quality_score,
                len(retained_facts),
                len(enriched_facts),
                elapsed_ms,
            )

            if quality_score < self._config.min_quality_score:
                _log.warning(
                    "compression_quality_warning | task_type=%s | quality=%.2f | below_threshold=%.2f",
                    task_type.value,
                    quality_score,
                    self._config.min_quality_score,
                )

            return CompressionResult(
                compressed_text=compressed,
                quality_score=round(quality_score, 3),
                original_length=len(original_text),
                compressed_length=len(compressed),
                compression_ratio=round(compression_ratio, 3),
                retained_facts=retained_facts,
                warnings=self._generate_warnings(
                    quality_score,
                    compression_ratio,
                    enriched_facts,
                    retained_facts=retained_facts,
                ),
            )

        except Exception as exc:
            elapsed_ms = (time.monotonic() - start_time) * 1000
            _log.error(
                "compression_failed | task_type=%s | error=%s | elapsed_ms=%.2f",
                task_type.value,
                exc,
                elapsed_ms,
                exc_info=True,
            )
            raise

    def get_dynamic_thresholds(
        self,
        model_context_window: int,
    ) -> CompressionConfig:
        """Get compression thresholds adjusted for model's context window.

        Args:
            model_context_window: Model's total context window in tokens

        Returns:
            Adjusted compression config
        """
        if not self._config.enable_dynamic_thresholds:
            return self._config

        # Reference: 32K context window
        reference = 32000
        ratio = max(0.3, min(2.0, model_context_window / reference))

        # Adjust limits based on model capacity
        adjusted = CompressionConfig(
            min_quality_score=self._config.min_quality_score,
            soft_limit_chars=int(self._config.soft_limit_chars * ratio),
            hard_limit_chars=int(self._config.hard_limit_chars * ratio),
            context_window_tokens=model_context_window,
            strategy=self._config.strategy,
        )

        return adjusted

    def get_compression_stats(self, task_type: str) -> CompressionStats | None:
        """Get compression statistics for a task type."""
        return self._stats.get(task_type)

    def should_compress(
        self,
        estimated_chars: int,
        budget_pressure: float,
    ) -> bool:
        """Determine if compression should be applied.

        Args:
            estimated_chars: Estimated character count of full context
            budget_pressure: Budget pressure score (0.0 - 1.0)

        Returns:
            True if compression should be applied
        """
        if not self._config.enable_dynamic_thresholds:
            should = estimated_chars > self._config.soft_limit_chars
            _log.debug(
                "should_compress_static | chars=%d | soft_limit=%d | result=%s",
                estimated_chars,
                self._config.soft_limit_chars,
                should,
            )
            return should

        # Dynamic decision based on pressure
        if budget_pressure < 0.35:
            # Low pressure: no compression
            _log.debug(
                "should_compress | pressure=%.2f | decision=no_compression (low_pressure)",
                budget_pressure,
            )
            return False
        elif budget_pressure < 0.6:
            # Medium pressure: compress if over soft limit
            should = estimated_chars > self._config.soft_limit_chars
            _log.debug(
                "should_compress | pressure=%.2f | chars=%d | soft_limit=%d | result=%s",
                budget_pressure,
                estimated_chars,
                self._config.soft_limit_chars,
                should,
            )
            return should
        else:
            # High pressure: always compress
            _log.debug(
                "should_compress | pressure=%.2f | decision=compress (high_pressure)",
                budget_pressure,
            )
            return True

    async def _llm_compress(
        self,
        text: str,
        target_chars: int,
        task_type: TaskType,
        *,
        priority_facts: list[str] | None = None,
        _depth: int = 0,
    ) -> str:
        """Use LLM to compress text.

        Args:
            text: Text to compress
            target_chars: Target character count
            task_type: Task type for routing

        Returns:
            Compressed text
        """
        _log.debug(
            "llm_compress_start | task_type=%s | input_length=%d | target_chars=%d",
            task_type.value,
            len(text),
            target_chars,
        )

        chunks = self._complete_text_chunks(text)
        if not chunks:
            return ""
        total_chars = max(1, sum(len(chunk) for chunk in chunks))
        compressed_parts: list[str] = []
        for index, chunk in enumerate(chunks, start=1):
            chunk_target = max(80, round(target_chars * len(chunk) / total_chars))
            compressed_parts.append(
                await self._compress_chunk(
                    chunk,
                    chunk_target,
                    task_type,
                    block_id=f"part_{index}_of_{len(chunks)}",
                    priority_facts=priority_facts,
                )
            )
        compressed = (
            "".join(compressed_parts)
            if compressed_parts == chunks
            else "\n".join(part for part in compressed_parts if part)
        )
        # A second LLM pass merges the complete first-level summaries.  Recursion is
        # bounded structurally, not by dropping source chunks.
        if (
            len(chunks) > 1
            and _depth < 2
            and len(compressed) > max(target_chars * 2, target_chars + 600)
            and len(compressed) < len(text)
        ):
            compressed = await self._llm_compress(
                compressed,
                target_chars,
                task_type,
                priority_facts=priority_facts,
                _depth=_depth + 1,
            )
        _log.debug(
            "llm_compress_done | task_type=%s | chunks=%d | compressed_length=%d",
            task_type.value,
            len(chunks),
            len(compressed),
        )
        return compressed

    def _complete_text_chunks(self, text: str) -> list[str]:
        """Split by provider-safe capacity while preserving every source character."""
        if not text:
            return []
        chunk_chars = max(
            4000,
            min(24000, int(self._config.context_window_tokens or 32000) * 2),
        )
        return [text[start : start + chunk_chars] for start in range(0, len(text), chunk_chars)]

    async def _compress_chunk(
        self,
        text: str,
        target_chars: int,
        task_type: TaskType,
        *,
        block_id: str,
        priority_facts: list[str] | None,
    ) -> str:
        import json

        context: dict[str, object] = {
            "blocks": [{"id": block_id, "text": text, "max_chars": target_chars}],
            "target_chars": target_chars,
        }
        if priority_facts:
            context.update(
                {
                    "text": text,
                    "priority_facts": "\n".join(f"- {fact}" for fact in priority_facts),
                    "mode": "fact_priority",
                }
            )
        try:
            response = await self._router.route(
                self._builder.build(
                    TaskType.CONTEXT_COMPRESS,
                    context,
                    max_tokens=calculate_route_aware_max_tokens(
                        self._router,
                        task_type,
                        max(300, target_chars),
                        prompt_overhead=1600 if priority_facts else 1400,
                        min_tokens=512,
                    ),
                    temperature=0.2,
                )
            )
            data = json.loads(response.content)
            items = data.get("items", [])
            if items and isinstance(items[0], dict) and items[0].get("compressed"):
                return str(items[0]["compressed"])
            if data.get("compressed"):
                return str(data["compressed"])
        except Exception as exc:
            _log.warning(
                "compression_chunk_failed | block=%s | error=%s | preserving_source",
                block_id,
                exc,
            )
        return text

    async def _verify_compression_quality(
        self,
        original: str,
        compressed: str,
        task_type: TaskType,
        required_facts: list[str] | None = None,
    ) -> float:
        """Verify compression quality using LLM judge.

        Args:
            original: Original text
            compressed: Compressed text
            task_type: Task type
            required_facts: Facts that must be preserved

        Returns:
            Quality score (0.0 - 1.0)
        """
        try:
            import json

            scores: list[tuple[float, int]] = []
            for index, chunk in enumerate(self._complete_text_chunks(original), start=1):
                response = await self._router.route(
                    self._builder.build(
                        TaskType.VERIFY_COMPRESSION,
                        {
                            "original": chunk,
                            "compressed": compressed,
                            "required_facts": required_facts or [],
                            "verification_part": index,
                        },
                        max_tokens=calculate_route_aware_max_tokens(
                            self._router,
                            TaskType.VERIFY_COMPRESSION,
                            300,
                            prompt_overhead=1200,
                            min_tokens=256,
                        ),
                        temperature=0.2,
                    )
                )
                data = json.loads(response.content)
                scores.append((float(data.get("quality_score", 0.5)), len(chunk)))
            if scores:
                return sum(score * size for score, size in scores) / sum(
                    size for _, size in scores
                )
        except Exception:
            pass
        # Availability fallback only measures; it never selects or removes story facts.
        return self._heuristic_quality_score(original, compressed, required_facts)

    def _heuristic_quality_score(
        self,
        original: str,
        compressed: str,
        required_facts: list[str] | None,
    ) -> float:
        """Calculate quality score using heuristics.

        Args:
            original: Original text
            compressed: Compressed text
            required_facts: Facts that must be preserved

        Returns:
            Quality score (0.0 - 1.0)
        """
        score = 1.0

        # Penalty for excessive compression
        ratio = len(compressed) / len(original) if original else 1.0
        if ratio < 0.2:
            score -= 0.3  # Too aggressive
        elif ratio < 0.4:
            score -= 0.1

        # Bonus for preserving required facts
        if required_facts:
            preserved = sum(1 for fact in required_facts if fact in compressed)
            fact_ratio = preserved / len(required_facts)
            score = score * 0.5 + fact_ratio * 0.5

        # Penalty for losing key information markers
        key_markers = ["因为", "所以", "但是", "如果", "决定", "发现"]
        original_markers = sum(original.count(m) for m in key_markers)
        compressed_markers = sum(compressed.count(m) for m in key_markers)
        if original_markers > 0:
            marker_ratio = compressed_markers / original_markers
            score = score * 0.7 + marker_ratio * 0.3

        return max(0.0, min(1.0, score))

    async def _compress_with_facts_priority(
        self,
        text: str,
        target_chars: int,
        required_facts: list[str],
    ) -> str:
        """Compress with priority on preserving facts.

        Args:
            text: Original text
            target_chars: Target character count
            required_facts: Facts that must be preserved

        Returns:
            Compressed text with facts preserved
        """
        if not required_facts:
            return await self._llm_compress(text, target_chars, TaskType.CONTEXT_COMPRESS)

        return await self._llm_compress(
            text,
            target_chars,
            TaskType.CONTEXT_COMPRESS,
            priority_facts=required_facts,
        )

    def _manual_fact_preservation(
        self,
        text: str,
        target_chars: int,
        required_facts: list[str],
    ) -> str:
        """Manually preserve facts when LLM compression fails.

        Args:
            text: Original text
            target_chars: Target character count
            required_facts: Facts to preserve

        Returns:
            Text with facts preserved
        """
        # Local fallback is deliberately lossless.  Semantic selection belongs to
        # the LLM; callers may keep the uncompressed block or retry another route.
        return text

    def _extract_retained_facts(
        self,
        compressed: str,
        required_facts: list[str],
    ) -> list[str]:
        """Extract which required facts were retained in compression."""
        if not required_facts:
            return []
        return [fact for fact in required_facts if fact in compressed]

    def _generate_warnings(
        self,
        quality_score: float,
        compression_ratio: float,
        required_facts: list[str] | None,
        retained_facts: list[str] | None = None,
    ) -> list[str]:
        """Generate warnings for compression result."""
        warnings: list[str] = []

        if quality_score < self._config.min_quality_score:
            warnings.append(f"Quality score ({quality_score:.2f}) below threshold")

        if compression_ratio < 0.2:
            warnings.append("Aggressive compression may lose important context")

        if required_facts:
            retained = len(retained_facts or [])
            if retained < len(required_facts):
                warnings.append(f"Lost {len(required_facts) - retained} required facts")

        return warnings

    def _record_stats(
        self,
        task_type: str,
        quality_score: float,
        compression_ratio: float,
    ) -> None:
        """Record compression statistics."""
        if task_type not in self._stats:
            self._stats[task_type] = CompressionStats(task_type=task_type)

        self._stats[task_type].record(quality_score, compression_ratio)


__all__ = [
    "AdaptiveCompressionService",
    "CompressionResult",
    "CompressionStats",
]
