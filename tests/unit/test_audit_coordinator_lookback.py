"""Tests for AuditCoordinator lookback_chapters configuration."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from novel_forge.memory.audit_coordinator import AuditCoordinator


class MockSettings:
    """Mock settings with configurable lookback."""

    def __init__(self, lookback_value: int | None = None) -> None:
        self.memory_motif_related_lookback_chapters = lookback_value


class MockMotifTracker:
    """Mock motif tracker for testing."""

    def __init__(self) -> None:
        self.check_unintentional_repetition = AsyncMock(
            return_value=[
                MagicMock(
                    motif_name="test_motif",
                    chapter_number=2,
                    previous_chapters=[1],
                    severity="medium",
                    suggestion="Consider varying your motif usage.",
                )
            ]
        )

    def get_motifs_for_prompt(
        self,
        current_chapter: int,
        related_lookback_chapters: int = 2,
        chapter_text: str = "",
    ) -> dict[str, Any]:
        return {
            "active_motifs": [],
            "recent_motifs": [],
        }


class MockMemoryContext:
    """Mock memory context for testing."""

    def __init__(self, settings: MockSettings | None = None) -> None:
        self.settings = settings or MockSettings(lookback_value=None)
        self.motif_tracker = MockMotifTracker()
        self.project_id = "test_project"

    def get_cached_summary(self, chapter_number: int) -> str | None:
        return None

    def get_chapter_exit_state(self, chapter_number: int) -> dict[str, Any] | None:
        return None

    async def search_relevant_history(
        self,
        query: str,
        current_chapter: int,
        lookback: int = 5,
        top_k: int = 5,
        **kwargs: Any,
    ) -> list[dict[str, Any]]:
        return []

    def get_status_summary(self) -> dict[str, Any]:
        return {}


@pytest.fixture
def memory_context_with_settings() -> MockMemoryContext:
    """Provides a mock MemoryContext with explicit settings."""
    return MockMemoryContext(settings=MockSettings(lookback_value=7))


@pytest.fixture
def memory_context_default_settings() -> MockMemoryContext:
    """Provides a mock MemoryContext with default settings (no lookback set)."""
    return MockMemoryContext(settings=MockSettings(lookback_value=None))


@pytest.fixture
def coordinator(memory_context_with_settings: MockMemoryContext) -> AuditCoordinator:
    """Creates an AuditCoordinator with mocked dependencies."""
    return AuditCoordinator(memory_context_with_settings)


class TestLookbackFromSettings:
    """Test that lookback_chapters is read from settings."""

    @pytest.mark.asyncio
    async def test_lookback_uses_settings_value(self) -> None:
        """When settings specifies lookback, it should be passed to check_unintentional_repetition."""
        memory_ctx = MockMemoryContext(settings=MockSettings(lookback_value=7))
        coordinator = AuditCoordinator(memory_ctx)

        await coordinator.prepare_audit_context(
            chapter_number=2,
            chapter_text="x" * 600,  # >= 500 to trigger the check
        )

        # Verify the mock was called with lookback_chapters=7
        memory_ctx.motif_tracker.check_unintentional_repetition.assert_called_once()
        call_kwargs = memory_ctx.motif_tracker.check_unintentional_repetition.call_args
        assert call_kwargs is not None
        assert call_kwargs.kwargs.get("lookback_chapters") == 7

    @pytest.mark.asyncio
    async def test_lookback_default_fallback(self) -> None:
        """When settings has no lookback value, defaults to 5."""
        memory_ctx = MockMemoryContext(settings=MockSettings(lookback_value=None))
        coordinator = AuditCoordinator(memory_ctx)

        await coordinator.prepare_audit_context(
            chapter_number=2,
            chapter_text="x" * 600,
        )

        memory_ctx.motif_tracker.check_unintentional_repetition.assert_called_once()
        call_kwargs = memory_ctx.motif_tracker.check_unintentional_repetition.call_args
        assert call_kwargs is not None
        assert call_kwargs.kwargs.get("lookback_chapters") == 5

    @pytest.mark.asyncio
    async def test_lookback_custom_value(self) -> None:
        """Verify a custom lookback value (3) is correctly passed."""
        memory_ctx = MockMemoryContext(settings=MockSettings(lookback_value=3))
        coordinator = AuditCoordinator(memory_ctx)

        await coordinator.prepare_audit_context(
            chapter_number=5,
            chapter_text="x" * 700,
        )

        memory_ctx.motif_tracker.check_unintentional_repetition.assert_called_once()
        call_kwargs = memory_ctx.motif_tracker.check_unintentional_repetition.call_args
        assert call_kwargs is not None
        assert call_kwargs.kwargs.get("lookback_chapters") == 3

    @pytest.mark.asyncio
    async def test_short_chapter_skips_repetition_check(self) -> None:
        """When chapter_text is < 500 chars, check_unintentional_repetition should not be called."""
        memory_ctx = MockMemoryContext(settings=MockSettings(lookback_value=7))
        coordinator = AuditCoordinator(memory_ctx)

        await coordinator.prepare_audit_context(
            chapter_number=2,
            chapter_text="x" * 100,  # < 500
        )

        memory_ctx.motif_tracker.check_unintentional_repetition.assert_not_called()
