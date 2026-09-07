"""Tests for ComplianceGuard task_type whitelist mechanism."""

from __future__ import annotations

import pytest

from novel_forge.core.exceptions import ComplianceViolationError
from novel_forge.prompts.compliance import ComplianceGuard


class TestComplianceGuardTaskTypeWhitelist:
    """Test that whitelisted task types skip forbidden pattern checks."""

    FORBIDDEN_TEXT = "请复制原文到新章节"

    def test_whitelisted_task_type_skips_check(self) -> None:
        """When task_type is in skip_task_types, no error is raised."""
        guard = ComplianceGuard()
        # No error should be raised for whitelisted task types
        guard.check_prompt(self.FORBIDDEN_TEXT, task_type="book_consistency")
        guard.check_prompt(self.FORBIDDEN_TEXT, task_type="book_consistency_verify")

    def test_non_whitelisted_task_type_raises_error(self) -> None:
        """When task_type is NOT in skip_task_types, ComplianceViolationError is raised."""
        guard = ComplianceGuard()
        with pytest.raises(ComplianceViolationError):
            guard.check_prompt(self.FORBIDDEN_TEXT, task_type="draft_chapter")

    def test_task_type_not_provided_raises_error(self) -> None:
        """When task_type is None, the check runs normally."""
        guard = ComplianceGuard()
        with pytest.raises(ComplianceViolationError):
            guard.check_prompt(self.FORBIDDEN_TEXT)

    def test_custom_skip_task_types(self) -> None:
        """Custom skip_task_types override the default whitelist."""
        guard = ComplianceGuard(skip_task_types={"custom_task"})
        # custom_task is whitelisted, should not raise
        guard.check_prompt(self.FORBIDDEN_TEXT, task_type="custom_task")
        # Other tasks should still raise
        with pytest.raises(ComplianceViolationError):
            guard.check_prompt(self.FORBIDDEN_TEXT, task_type="draft_chapter")

    def test_empty_skip_task_types(self) -> None:
        """Empty skip_task_types means no task types are skipped."""
        guard = ComplianceGuard(skip_task_types=set())
        with pytest.raises(ComplianceViolationError):
            guard.check_prompt(self.FORBIDDEN_TEXT, task_type="book_consistency")

    def test_disabled_guard_skips_check(self) -> None:
        """Disabled guard always skips checks regardless of task_type."""
        guard = ComplianceGuard(enabled=False)
        guard.check_prompt(self.FORBIDDEN_TEXT, task_type="draft_chapter")
