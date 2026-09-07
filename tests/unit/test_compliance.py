"""Tests for ComplianceGuard."""

from __future__ import annotations

import pytest

from novel_forge.core.exceptions import ComplianceViolationError
from novel_forge.prompts.compliance import ComplianceGuard


class TestComplianceGuard:
    def setup_method(self) -> None:
        self.guard = ComplianceGuard()

    def test_clean_prompt_passes(self) -> None:
        self.guard.check_prompt("请写一个关于勇气的短篇故事")

    def test_copy_pattern_blocked(self) -> None:
        with pytest.raises(ComplianceViolationError):
            self.guard.check_prompt("请复制原文中的段落")

    def test_imitate_to_identifiable_blocked(self) -> None:
        with pytest.raises(ComplianceViolationError):
            self.guard.check_prompt("模仿鲁迅的文风到可识别的程度")

    def test_english_plagiarize_blocked(self) -> None:
        with pytest.raises(ComplianceViolationError):
            self.guard.check_prompt("plagiarize this text")

    def test_english_replicate_blocked(self) -> None:
        with pytest.raises(ComplianceViolationError):
            self.guard.check_prompt("replicate the prose of Hemingway")

    def test_disabled_guard_passes_all(self) -> None:
        guard = ComplianceGuard(enabled=False)
        guard.check_prompt("复制原文中的段落")  # should not raise

    def test_output_check_accepts_clean_prose(self) -> None:
        self.guard.check_output("她推开门，走廊尽头的风压低了灯影。")

    def test_output_check_blocks_planning_terms(self) -> None:
        with pytest.raises(ComplianceViolationError):
            self.guard.check_output("scene_intent: 这里需要承接 opening_contract")
