"""Stable public facade for memory integration services."""

from __future__ import annotations

from novel_forge.memory.integration import (
    MemoryContext,
    MemoryIntegrationConfig,
    get_embedding_config_from_profiles,
)

__all__ = (
    "MemoryContext",
    "MemoryIntegrationConfig",
    "get_embedding_config_from_profiles",
)
