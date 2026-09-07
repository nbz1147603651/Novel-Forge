"""Prompts layer — template management, building, and compliance."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from novel_forge.prompts.builder import PromptBuilder
    from novel_forge.prompts.compliance import ComplianceGuard
    from novel_forge.prompts.registry import PromptRegistry

__all__ = ["PromptRegistry", "PromptBuilder", "ComplianceGuard"]


def __getattr__(name: str) -> Any:
    """Load prompt entry points lazily to avoid package import cycles."""
    if name == "PromptBuilder":
        from novel_forge.prompts.builder import PromptBuilder

        return PromptBuilder
    if name == "ComplianceGuard":
        from novel_forge.prompts.compliance import ComplianceGuard

        return ComplianceGuard
    if name == "PromptRegistry":
        from novel_forge.prompts.registry import PromptRegistry

        return PromptRegistry
    raise AttributeError(name)
