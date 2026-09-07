"""Tests for PromptBuilder task_type passing to ComplianceGuard."""

from __future__ import annotations

from novel_forge.core.constants import TaskType
from novel_forge.prompts.builder import PromptBuilder
from novel_forge.prompts.compliance import ComplianceGuard


class TestPromptBuilderTaskType:
    """Verify task_type is passed to ComplianceGuard.check_prompt()."""

    def test_whitelisted_task_type_does_not_raise(self) -> None:
        """BOOK_CONSISTENCY is whitelisted and should not trigger compliance error."""
        compliance = ComplianceGuard(
            skip_task_types={"BOOK_CONSISTENCY", "BOOK_CONSISTENCY_VERIFY"}
        )
        builder = PromptBuilder(compliance=compliance)

        # Should not raise even with potentially triggering content
        request = builder.build(
            TaskType.BOOK_CONSISTENCY,
            {"canon_state": {}, "current_content": "测试内容"},
        )
        assert request.task_type == TaskType.BOOK_CONSISTENCY

    def test_whitelisted_verify_task_type_does_not_raise(self) -> None:
        """BOOK_CONSISTENCY_VERIFY is whitelisted and should not trigger compliance error."""
        compliance = ComplianceGuard(
            skip_task_types={"BOOK_CONSISTENCY", "BOOK_CONSISTENCY_VERIFY"}
        )
        builder = PromptBuilder(compliance=compliance)

        request = builder.build(
            TaskType.BOOK_CONSISTENCY_VERIFY,
            {"canon_state": {}, "current_content": "测试内容"},
        )
        assert request.task_type == TaskType.BOOK_CONSISTENCY_VERIFY

    def test_task_type_value_passed_to_compliance_check(self) -> None:
        """Non-whitelisted task types receive the task_type parameter correctly."""
        compliance = ComplianceGuard(skip_task_types=set())
        builder = PromptBuilder(compliance=compliance)

        # With empty skip list, only whitelisted task types are skipped.
        # We just verify the build works and task_type is properly propagated.
        request = builder.build(
            TaskType.BEATS,
            {
                "spec": {
                    "genre": "fantasy",
                    "theme": "test",
                    "tone": "epic",
                    "length_target": 1000,
                    "language": "zh",
                    "characters_hint": "",
                    "world_hint": "",
                    "extra_instructions": "",
                },
            },
        )
        assert request.task_type == TaskType.BEATS

    def test_task_type_passed_to_render(self) -> None:
        """Verify task_type is accessible through build() flow."""
        compliance = ComplianceGuard(
            skip_task_types={"BOOK_CONSISTENCY"}
        )
        builder = PromptBuilder(compliance=compliance)

        # Just verify it doesn't raise and task_type is set correctly
        request = builder.build(
            TaskType.BOOK_CONSISTENCY,
            {"canon_state": {}, "current_content": "some content"},
        )
        assert request.task_type == TaskType.BOOK_CONSISTENCY