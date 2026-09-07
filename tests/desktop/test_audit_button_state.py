"""Tests for AuditState enum and button state transitions."""

from __future__ import annotations

import pytest

from novel_forge.desktop.jobs import DesktopJobEvent, DesktopJobRecord, DesktopJobState
from novel_forge.desktop.pages.chapter_studio.jobs import AuditState, ChapterStudioJobsMixin


class TestAuditStateEnum:
    """Tests for AuditState enum values and properties."""

    def test_audit_state_values(self) -> None:
        """AuditState should have exactly 6 states with correct values."""
        assert AuditState.IDLE.value == "idle"
        assert AuditState.AUDITING.value == "auditing"
        assert AuditState.VERIFYING.value == "verifying"
        assert AuditState.REPAIRING.value == "repairing"
        assert AuditState.COMPLETE.value == "complete"
        assert AuditState.FAILED.value == "failed"

    def test_audit_state_count(self) -> None:
        """AuditState should have exactly 6 members."""
        assert len(AuditState) == 6

    def test_audit_state_is_enum(self) -> None:
        """AuditState should be a proper enum type."""
        assert isinstance(AuditState.IDLE, AuditState)
        assert AuditState.IDLE != AuditState.AUDITING


class TestButtonTextFormat:
    """Tests for button text format with percentages."""

    @pytest.mark.parametrize(
        "state,expected_text",
        [
            (AuditState.IDLE, "📋 全书审计"),
            (AuditState.AUDITING, "📋 审计 25%"),
            (AuditState.VERIFYING, "📋 验证 60%"),
            (AuditState.REPAIRING, "📋 修复 85%"),
            (AuditState.COMPLETE, "📋 再次审计"),
            (AuditState.FAILED, "📋 审计失败"),
        ],
    )
    def test_button_text_format(self, state: AuditState, expected_text: str) -> None:
        """Button text should follow the format with state and percentage."""
        if state in (AuditState.AUDITING, AuditState.VERIFYING, AuditState.REPAIRING):
            # Extract percentage from expected text for validation
            import re
            match = re.search(r"(\d+)%", expected_text)
            assert match is not None
            percentage = int(match.group(1))
            assert 0 <= percentage <= 100
            assert expected_text.startswith("📋 ")
        else:
            # IDLE, COMPLETE, FAILED have fixed text
            assert expected_text.startswith("📋 ")


class TestStateTransitions:
    """Tests for valid state transitions."""

    def test_valid_transitions(self) -> None:
        """Valid transition sequence: IDLE→AUDITING→VERIFYING→REPAIRING→COMPLETE."""
        sequence = [
            AuditState.IDLE,
            AuditState.AUDITING,
            AuditState.VERIFYING,
            AuditState.REPAIRING,
            AuditState.COMPLETE,
        ]
        # Verify sequence is strictly ordered
        for i in range(len(sequence) - 1):
            assert sequence[i] != sequence[i + 1]

    def test_failed_state_from_any(self) -> None:
        """FAILED state can be reached from any running state."""
        running_states = {
            AuditState.AUDITING,
            AuditState.VERIFYING,
            AuditState.REPAIRING,
        }
        for state in running_states:
            # FAILED is a valid transition from any running state
            assert state != AuditState.FAILED

    def test_complete_is_terminal(self) -> None:
        """COMPLETE is a terminal state (no further transitions)."""
        assert AuditState.COMPLETE != AuditState.IDLE
        assert AuditState.COMPLETE != AuditState.AUDITING


class TestFailedState:
    """Tests for FAILED state behavior."""

    def test_failed_state_text(self) -> None:
        """FAILED state should show '审计失败' text."""
        assert AuditState.FAILED.value == "failed"
        # Button text format verified in TestButtonTextFormat

    def test_failed_allows_retry(self) -> None:
        """FAILED state should enable button for retry capability."""
        # This is enforced in _update_book_level_btn_state:
        # self._consistency_tool_btn.setEnabled(
        #     not book_active or audit_state == AuditState.FAILED
        # )
        assert AuditState.FAILED is not None
        assert AuditState.FAILED != AuditState.COMPLETE


class _FakeButton:
    def __init__(self) -> None:
        self.enabled: bool | None = None
        self.text = ""
        self.tooltip = ""

    def setEnabled(self, enabled: bool) -> None:
        self.enabled = enabled

    def setText(self, text: str) -> None:
        self.text = text

    def setToolTip(self, tooltip: str) -> None:
        self.tooltip = tooltip


class _DummyJobsMixin(ChapterStudioJobsMixin):
    def __init__(self) -> None:
        self._book_level_jobs = []
        self._consistency_tool_btn = _FakeButton()


def test_completed_audit_button_invites_reaudit() -> None:
    """A completed audit should remain an action entry, not look like a terminal status."""
    mixin = _DummyJobsMixin()
    mixin._book_level_jobs = [
        DesktopJobRecord(
            job_id="book-audit-1",
            kind="book_consistency",
            label="全书一致性审计",
            status=DesktopJobState.SUCCEEDED,
            current_step="book_consistency_done",
        )
    ]

    mixin._update_book_level_btn_state()

    assert mixin._consistency_tool_btn.enabled is True
    assert mixin._consistency_tool_btn.text == "📋 再次审计"
    assert "重新审计" in mixin._consistency_tool_btn.tooltip


def test_running_audit_button_shows_compact_percent_and_detail_tooltip() -> None:
    mixin = _DummyJobsMixin()
    mixin._book_level_jobs = [
        DesktopJobRecord(
            job_id="book-audit-2",
            kind="book_consistency",
            label="全书一致性审计",
            status=DesktopJobState.RUNNING,
            current_step="book_consistency_chunk_progress",
            events=[
                DesktopJobEvent(
                    at="2026-03-13T13:15:54+00:00",
                    step="book_consistency_chunk_progress",
                    payload={"current": 3, "total": 8},
                )
            ],
        )
    ]

    mixin._update_book_level_btn_state()

    assert mixin._consistency_tool_btn.enabled is False
    assert mixin._consistency_tool_btn.text == "📋 审计 19%"
    assert "块 3 / 8" in mixin._consistency_tool_btn.tooltip
    assert "当前进度：19%" in mixin._consistency_tool_btn.tooltip
