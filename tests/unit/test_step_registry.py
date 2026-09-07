"""Tests for the step registry pattern."""
from __future__ import annotations

import pytest


class TestStepRegistry:
    def test_list_steps_returns_registered_steps(self) -> None:
        # Import step modules to trigger @register_step decorators
        import novel_forge.pipeline.steps.beats_step  # noqa: F401
        import novel_forge.pipeline.steps.bridge_step  # noqa: F401
        import novel_forge.pipeline.steps.draft_step  # noqa: F401
        import novel_forge.pipeline.steps.edit_step  # noqa: F401
        import novel_forge.pipeline.steps.evaluate_step  # noqa: F401
        import novel_forge.pipeline.steps.polish_step  # noqa: F401
        import novel_forge.pipeline.steps.short_blueprint_step  # noqa: F401
        import novel_forge.pipeline.steps.short_creative_step  # noqa: F401
        import novel_forge.pipeline.steps.spec_step  # noqa: F401
        from novel_forge.pipeline.steps.step_registry import StepRegistry
        steps = StepRegistry.list_steps()
        # 9 steps currently registered
        assert len(steps) >= 9, f"Expected >=9 steps, got {len(steps)}: {steps}"

    def test_get_step_returns_correct_class(self) -> None:
        import novel_forge.pipeline.steps.draft_step  # noqa: F401
        from novel_forge.pipeline.steps.step_registry import StepRegistry
        step_cls = StepRegistry.get_step("draft")
        assert step_cls.__name__ == "DraftStep"

    def test_get_step_raises_keyerror_for_unknown(self) -> None:
        import novel_forge.pipeline.steps.draft_step  # noqa: F401
        from novel_forge.pipeline.steps.step_registry import StepRegistry
        with pytest.raises(KeyError, match="nonexistent"):
            StepRegistry.get_step("nonexistent")

    def test_step_count_matches_list(self) -> None:
        import novel_forge.pipeline.steps.draft_step  # noqa: F401
        from novel_forge.pipeline.steps.step_registry import StepRegistry
        assert StepRegistry.step_count() == len(StepRegistry.list_steps())
        assert StepRegistry.step_count() > 0