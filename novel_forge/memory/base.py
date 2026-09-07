"""Base schemas for memory module."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import Field

from novel_forge.core.parsing.token_utils import estimate_text_length_tokens
from novel_forge.core.schemas.base import VersionedSchema


class EpisodicResult(VersionedSchema):
    """Result from episodic memory search."""

    chapter_number: int
    event_summary: str
    scene_index: int = 0  # 0 = chapter-level, >0 = specific scene
    relevance_score: float = 1.0
    text_snippet: str = ""
    characters_involved: list[str] = Field(default_factory=list)
    timestamp_in_story: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class MotifOccurrence(VersionedSchema):
    """Records a single occurrence of a motif in text."""

    motif_id: str
    chapter_number: int
    paragraph_index: int = 0
    text_snippet: str = ""
    context: str = ""  # Surrounding context for understanding
    associated_characters: list[str] = Field(default_factory=list)
    emotional_tone: str = ""
    narrative_function: str = ""
    function_relation: Literal["unknown", "new_function", "redundant"] = "unknown"
    function_evidence: str = ""


class CompressionResult(VersionedSchema):
    """Result from adaptive compression."""

    compressed_text: str
    quality_score: float = 1.0
    original_length: int
    compressed_length: int
    compression_ratio: float = 1.0
    retained_facts: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


@dataclass
class CompressionStats:
    """Statistics for compression quality tracking."""

    task_type: str
    total_compressions: int = 0
    avg_quality_score: float = 1.0
    avg_compression_ratio: float = 1.0
    low_quality_count: int = 0

    def record(self, quality_score: float, compression_ratio: float) -> None:
        """Record a new compression result."""
        self.total_compressions += 1
        # Running average
        self.avg_quality_score = (
            self.avg_quality_score * (self.total_compressions - 1) + quality_score
        ) / self.total_compressions
        self.avg_compression_ratio = (
            self.avg_compression_ratio * (self.total_compressions - 1) + compression_ratio
        ) / self.total_compressions
        if quality_score < 0.7:
            self.low_quality_count += 1


@dataclass
class SearchQuery:
    """Structured search query for memory retrieval."""

    query_text: str
    chapter_range: tuple[int, int] | None = None  # (start, end) inclusive
    event_types: list[str] = field(default_factory=list)
    characters: list[str] = field(default_factory=list)
    locations: list[str] = field(default_factory=list)
    top_k: int = 5
    min_relevance: float = 0.5


@dataclass
class MemoryRetrievalResult:
    """Unified result from memory retrieval operations."""

    results: list[EpisodicResult]
    query: SearchQuery
    retrieval_method: Literal["semantic", "temporal", "hybrid"]
    total_candidates: int = 0
    retrieval_time_ms: float = 0.0


# ---------------------------------------------------------------------------
# Token-budget-aware memory layering (inspired by MemPalace L0-L3 stack)
# ---------------------------------------------------------------------------


@dataclass
class MemoryLayerBudget:
    """Pressure thresholds for a single memory layer.

    Soft/hard values trigger diagnostics and lossless overflow handling. They
    never authorize prefix truncation of selected narrative evidence.
    """

    name: str
    soft_limit_chars: int
    hard_limit_chars: int
    enabled: bool = True

    @property
    def soft_limit_tokens(self) -> int:
        """Conservatively estimate a CJK-heavy character-only threshold."""

        return max(1, estimate_text_length_tokens(self.soft_limit_chars))

    @property
    def hard_limit_tokens(self) -> int:
        return max(1, estimate_text_length_tokens(self.hard_limit_chars))


@dataclass
class MemoryBudgetConfig:
    """Explicit token budgets for layered memory injection.

    Inspired by MemPalace's 4-layer MemoryStack (L0-L3), this config
    defines per-layer character budgets so context assembly is
    token-aware rather than loading all available memory indiscriminately.

    Default budgets target a ~32K context window, leaving ~24K for
    prompt instructions + generated output.

    Layers:
        L0 (identity):      Project identity — title, genre, style, core premise
        L1 (core_memory):   Recent chapter summaries + key character states
        L2 (on_demand):     Semantically relevant historical events
        L3 (deep_search):   Full semantic search for specific queries
    """

    l0_identity: MemoryLayerBudget = field(
        default_factory=lambda: MemoryLayerBudget(
            "L0_identity", soft_limit_chars=800, hard_limit_chars=1600
        )
    )
    l1_core_memory: MemoryLayerBudget = field(
        default_factory=lambda: MemoryLayerBudget(
            "L1_core_memory", soft_limit_chars=2000, hard_limit_chars=4000
        )
    )
    l2_on_demand: MemoryLayerBudget = field(
        default_factory=lambda: MemoryLayerBudget(
            "L2_on_demand", soft_limit_chars=1200, hard_limit_chars=2400
        )
    )
    l3_deep_search: MemoryLayerBudget = field(
        default_factory=lambda: MemoryLayerBudget(
            "L3_deep_search", soft_limit_chars=1600, hard_limit_chars=3200
        )
    )

    @property
    def total_soft_chars(self) -> int:
        return (
            self.l0_identity.soft_limit_chars
            + self.l1_core_memory.soft_limit_chars
            + self.l2_on_demand.soft_limit_chars
            + self.l3_deep_search.soft_limit_chars
        )

    @property
    def total_hard_chars(self) -> int:
        return (
            self.l0_identity.hard_limit_chars
            + self.l1_core_memory.hard_limit_chars
            + self.l2_on_demand.hard_limit_chars
            + self.l3_deep_search.hard_limit_chars
        )

    def get_layer(self, layer_name: str) -> MemoryLayerBudget | None:
        """Get budget by layer name (e.g. 'L0_identity', 'L1_core_memory')."""
        return {
            "L0_identity": self.l0_identity,
            "L1_core_memory": self.l1_core_memory,
            "L2_on_demand": self.l2_on_demand,
            "L3_deep_search": self.l3_deep_search,
        }.get(layer_name)

    def scale(self, factor: float) -> "MemoryBudgetConfig":
        """Return a new config with all budgets scaled by *factor* (0.0-1.0)."""
        factor = max(0.1, min(1.0, factor))

        def _scale(budget: MemoryLayerBudget) -> MemoryLayerBudget:
            return MemoryLayerBudget(
                name=budget.name,
                soft_limit_chars=max(200, int(budget.soft_limit_chars * factor)),
                hard_limit_chars=max(400, int(budget.hard_limit_chars * factor)),
                enabled=budget.enabled,
            )

        return MemoryBudgetConfig(
            l0_identity=_scale(self.l0_identity),
            l1_core_memory=_scale(self.l1_core_memory),
            l2_on_demand=_scale(self.l2_on_demand),
            l3_deep_search=_scale(self.l3_deep_search),
        )
