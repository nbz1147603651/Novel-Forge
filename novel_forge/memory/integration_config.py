"""Configuration model for memory integration facade."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from novel_forge.core.config import Settings


@dataclass
class MemoryIntegrationConfig:
    """Configuration for memory system integration."""

    enable_semantic_search: bool = True
    semantic_search_relevance_threshold: float = 0.6
    episodic_lookback_chapters: int = 20

    enable_auto_summarization: bool = True
    summary_after_each_chapter: bool = True
    volume_summary_on_volume_end: bool = True

    enable_adaptive_compression: bool = True
    compression_quality_check: bool = True
    compression_fallback_to_direct: bool = True

    enable_motif_extraction: bool = True
    enable_repetition_checking: bool = True
    enable_motif_suggestions: bool = True

    enable_critic_validation: bool = True
    critic_run_async: bool = False
    critic_block_on_critical: bool = True

    summary_token_budget_per_chapter: int = 200
    motif_token_budget_per_chapter: int = 100
    critic_token_budget_per_n_chapters: int = 5

    @classmethod
    def from_settings(cls, settings: Settings) -> MemoryIntegrationConfig:
        """Create config from settings."""
        return cls(
            enable_semantic_search=settings.memory_semantic_search_enabled,
            enable_adaptive_compression=settings.memory_adaptive_compression_enabled,
            enable_motif_extraction=settings.memory_motif_tracking_enabled,
            enable_repetition_checking=settings.memory_motif_check_repetition,
            enable_critic_validation=settings.memory_critic_agent_enabled,
            critic_run_async=settings.memory_critic_agent_run_async,
        )


__all__ = ("MemoryIntegrationConfig",)
