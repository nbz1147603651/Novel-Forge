"""Tests for RepairFailurePolicy consuming StoryKernel field slices.

Verifies that:
1. RepairRoundSnapshot accepts kernel_context field
2. kernel_context fields are included in failure event payloads
3. Backward compatibility (no kernel_context) still works
4. ContextComposer integration with repair safety
"""

from __future__ import annotations

from typing import Any

from novel_forge.pipeline.long.repair_safety import (
    RepairDimension,
    RepairFailurePolicy,
    RepairRoundSnapshot,
)
from novel_forge.story_kernel.composer import ContextComposer
from novel_forge.story_kernel.schemas import (
    Entity,
    StoryKernel,
    WorldRule,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_kernel(**overrides: Any) -> StoryKernel:
    """Create a minimal StoryKernel with sample data for testing."""
    defaults: dict[str, Any] = {
        "project_id": "test-project",
        "current_chapter": 3,
        "active_volume": 1,
        "title": "测试小说",
        "premise": "一个关于测试的故事",
        "world_rules": [
            WorldRule(rule_id="wr-1", content="魔法需要消耗生命力", category="magic"),
        ],
        "entities": [
            Entity(entity_id="e-1", name="李明", entity_type="character"),
        ],
        "relationships": [],
        "timeline": [],
        "object_ledger": [],
        "knowledge_ledger": [],
        "access_ledger": [],
        "promise_ledger": [],
        "motif_protocols": [],
        "business_dependencies": [],
        "chapter_summaries": {1: "第一章摘要"},
        "banned_phrases": [],
        "notes": "",
    }
    defaults.update(overrides)
    return StoryKernel(**defaults)


class _MockStore:
    """Mock StoryKernelStore for testing."""

    def __init__(self, kernel: StoryKernel) -> None:
        self._kernel = kernel

    def load_kernel(self, project_id: str) -> StoryKernel:
        return self._kernel


# ---------------------------------------------------------------------------
# RepairRoundSnapshot kernel_context tests
# ---------------------------------------------------------------------------


class TestRepairRoundSnapshotKernelContext:
    """Test RepairRoundSnapshot with kernel_context field."""

    def test_snapshot_accepts_kernel_context(self) -> None:
        """RepairRoundSnapshot should accept kernel_context parameter."""
        kernel_ctx = {"entities": [{"name": "李明"}], "world_rules": []}
        snapshot = RepairRoundSnapshot(
            stage=RepairDimension.CAUSAL,
            chapter_number=3,
            round_number=1,
            text="正文",
            kernel_context=kernel_ctx,
        )
        assert snapshot.kernel_context == kernel_ctx

    def test_snapshot_kernel_context_optional(self) -> None:
        """kernel_context should default to None."""
        snapshot = RepairRoundSnapshot(
            stage=RepairDimension.CAUSAL,
            chapter_number=3,
            text="正文",
        )
        assert snapshot.kernel_context is None


# ---------------------------------------------------------------------------
# Failure event payload enrichment tests
# ---------------------------------------------------------------------------


class TestFailureEventKernelContext:
    """Test that failure events include kernel_context data."""

    def test_repair_failed_includes_kernel_context_in_event(self) -> None:
        """repair_failed event payload should include kernel_context fields."""
        events: list[tuple[str, dict[str, Any]]] = []
        policy = RepairFailurePolicy(lambda step, payload: events.append((step, payload)))

        kernel_ctx = {
            "entities": [{"entity_id": "e-1", "name": "李明"}],
            "world_rules": [{"rule_id": "wr-1", "content": "魔法规则"}],
        }
        snapshot = RepairRoundSnapshot(
            stage=RepairDimension.CAUSAL,
            chapter_number=3,
            round_number=1,
            text="正文",
            kernel_context=kernel_ctx,
        )

        policy.repair_failed(snapshot=snapshot, exc=RuntimeError("boom"))

        assert len(events) == 1
        event_name, payload = events[0]
        assert event_name == "causal_repair_internal_error"
        # kernel_context fields should be merged into the payload
        assert "entities" in payload
        assert payload["entities"] == [{"entity_id": "e-1", "name": "李明"}]
        assert "world_rules" in payload
        assert payload["world_rules"] == [{"rule_id": "wr-1", "content": "魔法规则"}]

    def test_recheck_failed_includes_kernel_context_in_event(self) -> None:
        """recheck_failed event payload should include kernel_context fields."""
        events: list[tuple[str, dict[str, Any]]] = []
        policy = RepairFailurePolicy(lambda step, payload: events.append((step, payload)))

        kernel_ctx = {"entities": [{"name": "王芳"}]}
        snapshot = RepairRoundSnapshot(
            stage=RepairDimension.CONTINUITY,
            chapter_number=4,
            round_number=2,
            text="正文",
            kernel_context=kernel_ctx,
        )

        policy.recheck_failed(snapshot=snapshot, exc=RuntimeError("fail"))

        assert len(events) == 1
        _, payload = events[0]
        assert "entities" in payload
        assert payload["entities"] == [{"name": "王芳"}]

    def test_validation_failed_includes_kernel_context_in_event(self) -> None:
        """validation_failed event payload should include kernel_context fields."""
        events: list[tuple[str, dict[str, Any]]] = []
        policy = RepairFailurePolicy(lambda step, payload: events.append((step, payload)))

        kernel_ctx = {"world_rules": [{"content": "规则"}]}
        snapshot = RepairRoundSnapshot(
            stage=RepairDimension.READING_POWER,
            chapter_number=5,
            round_number=1,
            text="正文",
            kernel_context=kernel_ctx,
        )

        policy.validation_failed(snapshot=snapshot, exc=RuntimeError("val fail"))

        assert len(events) == 1
        _, payload = events[0]
        assert "world_rules" in payload
        assert payload["world_rules"] == [{"content": "规则"}]

    def test_post_repair_check_failed_includes_kernel_context(self) -> None:
        """post_repair_check_failed event payload should include kernel_context."""
        events: list[tuple[str, dict[str, Any]]] = []
        policy = RepairFailurePolicy(lambda step, payload: events.append((step, payload)))

        kernel_ctx = {"entities": [{"name": "李明"}], "world_rules": []}
        snapshot = RepairRoundSnapshot(
            stage=RepairDimension.READING_POWER,
            chapter_number=4,
            round_number=1,
            text="正文",
            kernel_context=kernel_ctx,
        )

        policy.post_repair_check_failed(
            snapshot=snapshot,
            exc=RuntimeError("cross check failed"),
            event_name="reading_power_post_repair_checks_failed",
            action="skip_cross_dimension_checks_keep_current_text",
        )

        assert len(events) == 1
        _, payload = events[0]
        assert "entities" in payload
        assert "world_rules" in payload


# ---------------------------------------------------------------------------
# Backward compatibility tests
# ---------------------------------------------------------------------------


class TestBackwardCompatibility:
    """Test that existing behavior is preserved when kernel_context is absent."""

    def test_no_kernel_context_backward_compat(self) -> None:
        """Without kernel_context, events should not contain kernel fields."""
        events: list[tuple[str, dict[str, Any]]] = []
        policy = RepairFailurePolicy(lambda step, payload: events.append((step, payload)))

        snapshot = RepairRoundSnapshot(
            stage=RepairDimension.CAUSAL,
            chapter_number=3,
            round_number=1,
            text="正文",
            # No kernel_context
        )

        policy.repair_failed(snapshot=snapshot, exc=RuntimeError("boom"))

        assert len(events) == 1
        _, payload = events[0]
        assert "entities" not in payload
        assert "world_rules" not in payload
        # Standard fields should still be present
        assert "chapter" in payload
        assert "error" in payload
        assert "error_kind" in payload

    def test_kernel_context_does_not_override_standard_fields(self) -> None:
        """kernel_context should not override standard event fields."""
        events: list[tuple[str, dict[str, Any]]] = []
        policy = RepairFailurePolicy(lambda step, payload: events.append((step, payload)))

        kernel_ctx = {
            "chapter": 999,  # Should NOT override
            "error": "fake",  # Should NOT override
            "entities": [{"name": "李明"}],
        }
        snapshot = RepairRoundSnapshot(
            stage=RepairDimension.CAUSAL,
            chapter_number=3,
            round_number=1,
            text="正文",
            kernel_context=kernel_ctx,
        )

        policy.repair_failed(snapshot=snapshot, exc=RuntimeError("boom"))

        assert len(events) == 1
        _, payload = events[0]
        # Standard fields should NOT be overridden
        assert payload["chapter"] == 3
        assert payload["error"] == "boom"
        # kernel_context fields should be merged
        assert payload["entities"] == [{"name": "李明"}]


# ---------------------------------------------------------------------------
# ContextComposer integration tests
# ---------------------------------------------------------------------------


class TestContextComposerForRepairSafety:
    """Test ContextComposer integration with repair safety."""

    def test_compose_for_patch_step(self) -> None:
        """ContextComposer should provide patch-compatible field slices."""
        kernel = _make_kernel()
        store = _MockStore(kernel)
        composer = ContextComposer(store, project_id="test-project")

        result = composer.compose_for_step("patch", 3)
        assert isinstance(result, dict)
        assert "entities" in result
        assert "world_rules" in result

    def test_compose_for_continuity_repair_step(self) -> None:
        """ContextComposer should provide continuity_repair-compatible field slices."""
        kernel = _make_kernel()
        store = _MockStore(kernel)
        composer = ContextComposer(store, project_id="test-project")

        result = composer.compose_for_step("continuity_repair", 3)
        assert isinstance(result, dict)
        assert "entities" in result
        assert "world_rules" in result
        assert "timeline" in result

    def test_compose_for_causal_repair_step(self) -> None:
        """ContextComposer should provide causal_repair-compatible field slices."""
        kernel = _make_kernel()
        store = _MockStore(kernel)
        composer = ContextComposer(store, project_id="test-project")

        result = composer.compose_for_step("causal_repair", 3)
        assert isinstance(result, dict)
        assert "entities" in result
        assert "knowledge_ledger" in result

    def test_compose_for_reading_power_repair_step(self) -> None:
        """ContextComposer should provide reading_power_repair-compatible field slices."""
        kernel = _make_kernel()
        store = _MockStore(kernel)
        composer = ContextComposer(store, project_id="test-project")

        result = composer.compose_for_step("reading_power_repair", 3)
        assert isinstance(result, dict)
        assert "entities" in result
        assert "world_rules" in result

    def test_kernel_context_can_be_passed_to_snapshot(self) -> None:
        """Composer output can be used as kernel_context in RepairRoundSnapshot."""
        kernel = _make_kernel()
        store = _MockStore(kernel)
        composer = ContextComposer(store, project_id="test-project")

        kernel_ctx = composer.compose_for_step("patch", 3)
        snapshot = RepairRoundSnapshot(
            stage=RepairDimension.CAUSAL,
            chapter_number=3,
            round_number=1,
            text="正文",
            kernel_context=kernel_ctx,
        )

        assert snapshot.kernel_context is not None
        assert "entities" in snapshot.kernel_context
        assert "world_rules" in snapshot.kernel_context
