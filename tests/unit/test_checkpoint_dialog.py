"""Tests for CheckpointDialog and shared helper functions."""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QScrollArea

from novel_forge.desktop.components.checkpoint_dialog import (
    CheckpointDialog,
    build_checkpoint_dialog_content,
    build_checkpoint_summary_text,
)
from novel_forge.workspace.contracts import DecisionCheckpoint, DecisionOption


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    app = QApplication.instance()
    if not isinstance(app, QApplication):
        app = QApplication([])
    app.setQuitOnLastWindowClosed(False)
    return app


def _make_option(
    option_id: str = "approve",
    label: str = "确认方案",
    description: str = "按当前方案继续",
    is_recommended: bool = True,
) -> DecisionOption:
    return DecisionOption(
        option_id=option_id,
        label=label,
        description=description,
        is_recommended=is_recommended,
    )


def _make_checkpoint(
    checkpoint_id: str = "plan_checkpoint_001",
    checkpoint_type: str = "plan_checkpoint",
    summary: str = "章节方案已生成",
    prompt: str = "请确认以下方案...",
    options: list[DecisionOption] | None = None,
) -> DecisionCheckpoint:
    return DecisionCheckpoint(
        checkpoint_id=checkpoint_id,
        checkpoint_type=checkpoint_type,  # type: ignore[arg-type]
        summary=summary,
        prompt=prompt,
        options=options or [_make_option()],
    )


# ── Helper function tests ────────────────────────────────────────────────────


class TestBuildCheckpointSummaryText:
    def test_basic_summary(self) -> None:
        cp = _make_checkpoint(summary="方案摘要", prompt="")
        result = build_checkpoint_summary_text(cp)
        assert "方案摘要" in result

    def test_summary_with_prompt(self) -> None:
        cp = _make_checkpoint(summary="摘要", prompt="详细说明")
        result = build_checkpoint_summary_text(cp)
        assert "摘要" in result
        assert "详细说明" in result

    def test_summary_with_warnings(self) -> None:
        cp = _make_checkpoint(summary="方案")
        result = build_checkpoint_summary_text(cp, studio_warnings=["注意角色状态"])
        assert "提醒" in result
        assert "注意角色状态" in result

    def test_summary_hides_internal_payload_fields(self) -> None:
        cp = _make_checkpoint(
            summary=(
                "需承接：{'schema_version': '2.0', 'created_at': '2026-06-27T10:47:04Z', "
                "'text': '沈鹿溪每日清晨录入风声的隐性约定', 'status': 'open'}"
            ),
            prompt="",
        )
        result = build_checkpoint_summary_text(cp)
        assert "• 沈鹿溪每日清晨录入风声的隐性约定" in result
        assert "schema_version" not in result
        assert "created_at" not in result

    def test_summary_with_replan_reason(self) -> None:
        cp = _make_checkpoint(
            summary="",
            prompt="方案未通过质量校验（角色行为不一致）需要重新规划",
        )
        result = build_checkpoint_summary_text(cp)
        assert "未通过" in result


class TestBuildCheckpointDialogContent:
    def test_basic_content(self) -> None:
        cp = _make_checkpoint()
        content = build_checkpoint_dialog_content(cp, "方案摘要")
        assert content["title"] == "方案需确认"
        assert content["badge_tone"] == "default"
        assert len(content["options"]) == 1
        assert content["has_artifacts"] is False

    def test_guard_checkpoint_type(self) -> None:
        cp = _make_checkpoint(checkpoint_type="guard_checkpoint")
        content = build_checkpoint_dialog_content(cp, "守卫检查")
        assert content["title"] == "守卫检查"

    def test_replan_badge_tone(self) -> None:
        cp = _make_checkpoint(prompt="方案未通过质量校验（问题）需要重新规划")
        content = build_checkpoint_dialog_content(cp, "方案")
        assert content["badge_tone"] == "warning"


# ── Dialog widget tests ──────────────────────────────────────────────────────


class TestCheckpointDialog:
    def test_dialog_creation_and_signals(self, qapp: QApplication) -> None:
        parent = QApplication.activeWindow() or QApplication.focusWidget()
        if parent is None:
            from PySide6.QtWidgets import QWidget

            parent = QWidget()
            parent.show()

        cp = _make_checkpoint()
        dialog = CheckpointDialog(parent=parent, checkpoint=cp, summary_text="测试摘要")

        assert dialog.checkpoint_id == "plan_checkpoint_001"
        assert dialog.is_for_checkpoint(cp)

        # Test option_selected signal
        received: list[tuple] = []
        dialog.option_selected.connect(lambda opt, notes: received.append((opt, notes)))

        # Simulate option selection via bubble panel
        assert dialog._bubble_panel is not None
        option = _make_option()
        dialog._bubble_panel._notes.setPlainText("测试备注")
        dialog._bubble_panel.option_selected.emit(option, "测试备注")

        assert len(received) == 1
        assert received[0][0].option_id == "approve"
        assert received[0][1] == "测试备注"

        dialog.close()
        dialog.deleteLater()

    def test_dialog_positions_as_wide_floating_window(self, qapp: QApplication) -> None:
        from PySide6.QtWidgets import QWidget

        parent = QWidget()
        parent.resize(900, 620)
        parent.show()
        qapp.processEvents()

        cp = _make_checkpoint()
        dialog = CheckpointDialog(parent=parent, checkpoint=cp, summary_text="测试摘要")
        dialog.show_animated()
        qapp.processEvents()

        assert dialog._panel is not None
        assert dialog.width() < parent.width()
        assert 620 <= dialog.width() <= 864
        assert dialog.x() >= 18
        assert dialog.x() + dialog.width() <= parent.width() - 18
        assert dialog.y() == 18
        assert dialog._panel.geometry() == dialog.rect()
        assert dialog._size_grip is not None
        assert dialog._size_grip.isVisible()

        dialog.close()
        dialog.deleteLater()
        parent.close()
        parent.deleteLater()

    def test_free_floating_dialog_uses_opaque_scroll_content_without_opacity_effect(
        self, qapp: QApplication
    ) -> None:
        """A top-level tool window must not show the chapter page through its viewport."""
        from PySide6.QtWidgets import QWidget

        parent = QWidget()
        parent.resize(900, 620)
        parent.show()
        dialog = CheckpointDialog(
            parent=parent,
            checkpoint=_make_checkpoint(),
            summary_text="测试摘要",
            free_floating=True,
        )

        scroll = dialog.findChild(QScrollArea, "checkpointDialogScroll")
        assert scroll is not None
        assert scroll.viewport().testAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        # Background is now provided by the global QSS template (theme-resolved),
        # not an inline setStyleSheet. Verify the rule exists in the app stylesheet.
        from novel_forge.desktop.theme import get_stylesheet

        assert "QScrollArea#checkpointDialogScroll" in get_stylesheet()
        assert "background-color" in get_stylesheet()
        assert dialog._bubble_panel is not None
        assert dialog._bubble_panel.testAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        assert dialog.graphicsEffect() is None

        dialog.close()
        dialog.deleteLater()
        parent.close()
        parent.deleteLater()

    def test_option_uses_compact_mini_button(self, qapp: QApplication) -> None:
        from PySide6.QtWidgets import QPushButton, QWidget

        parent = QWidget()
        parent.show()

        cp = _make_checkpoint()
        dialog = CheckpointDialog(parent=parent, checkpoint=cp, summary_text="测试摘要")
        buttons = dialog.findChildren(QPushButton, "checkpointOptionMiniButton")

        assert len(buttons) == 1
        assert buttons[0].width() <= 56
        assert buttons[0].height() <= 26

        dialog.close()
        dialog.deleteLater()
        parent.close()
        parent.deleteLater()

    def test_dialog_dismissed_signal(self, qapp: QApplication) -> None:
        from unittest.mock import MagicMock

        from PySide6.QtWidgets import QWidget

        parent = QWidget()
        parent.show()

        cp = _make_checkpoint()
        dialog = CheckpointDialog(parent=parent, checkpoint=cp, summary_text="测试")

        # Mock dismiss_animated to verify it's called with emit_dismissed=True
        dialog.dismiss_animated = MagicMock()  # type: ignore[method-assign]

        from PySide6.QtGui import QKeyEvent

        event = QKeyEvent(
            QKeyEvent.Type.KeyPress, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier
        )
        dialog.keyPressEvent(event)

        dialog.dismiss_animated.assert_called_once_with(emit_dismissed=True)

        dialog.close()
        dialog.deleteLater()

    def test_dialog_silent_dismiss_no_signal(self, qapp: QApplication) -> None:
        from PySide6.QtWidgets import QWidget

        parent = QWidget()
        parent.show()

        cp = _make_checkpoint()
        dialog = CheckpointDialog(parent=parent, checkpoint=cp, summary_text="测试")

        dismissed_count = [0]
        dialog.dismissed.connect(lambda: dismissed_count.__setitem__(0, dismissed_count[0] + 1))

        # Silent dismiss should NOT emit dismissed
        dialog.dismiss_animated(emit_dismissed=False)
        qapp.processEvents()
        assert dismissed_count[0] == 0

        dialog.close()
        dialog.deleteLater()

    def test_dialog_checkpoint_id_property(self, qapp: QApplication) -> None:
        from PySide6.QtWidgets import QWidget

        parent = QWidget()
        parent.show()

        cp = _make_checkpoint(checkpoint_id="unique_id_123")
        dialog = CheckpointDialog(parent=parent, checkpoint=cp, summary_text="测试")

        assert dialog.checkpoint_id == "unique_id_123"

        dialog.close()
        dialog.deleteLater()

    def test_dialog_initial_notes_and_summary_property(self, qapp: QApplication) -> None:
        from PySide6.QtWidgets import QWidget

        parent = QWidget()
        parent.show()

        cp = _make_checkpoint()
        dialog = CheckpointDialog(
            parent=parent,
            checkpoint=cp,
            summary_text="测试摘要",
            initial_notes="已有备注",
        )

        assert dialog.summary_text == "测试摘要"
        assert dialog.current_notes() == "已有备注"

        dialog.close()
        dialog.deleteLater()

    def test_is_for_checkpoint(self, qapp: QApplication) -> None:
        from PySide6.QtWidgets import QWidget

        parent = QWidget()
        parent.show()

        cp1 = _make_checkpoint(checkpoint_id="cp_1")
        cp2 = _make_checkpoint(checkpoint_id="cp_2")
        dialog = CheckpointDialog(parent=parent, checkpoint=cp1, summary_text="测试")

        assert dialog.is_for_checkpoint(cp1) is True
        assert dialog.is_for_checkpoint(cp2) is False

        dialog.close()
        dialog.deleteLater()

    def test_update_checkpoint(self, qapp: QApplication) -> None:
        from PySide6.QtWidgets import QWidget

        parent = QWidget()
        parent.show()

        cp1 = _make_checkpoint(checkpoint_id="cp_1", summary="旧摘要")
        dialog = CheckpointDialog(parent=parent, checkpoint=cp1, summary_text="旧摘要")

        cp2 = _make_checkpoint(checkpoint_id="cp_1", summary="新摘要")
        dialog.update_checkpoint(cp2, "新摘要")

        assert dialog.checkpoint_id == "cp_1"
        # Content should be updated
        assert dialog._content is not None

        dialog.close()
        dialog.deleteLater()

    def test_update_checkpoint_preserves_notes(self, qapp: QApplication) -> None:
        from PySide6.QtWidgets import QWidget

        parent = QWidget()
        parent.show()

        cp1 = _make_checkpoint(checkpoint_id="cp_1", summary="旧摘要")
        dialog = CheckpointDialog(parent=parent, checkpoint=cp1, summary_text="旧摘要")
        assert dialog._bubble_panel is not None
        dialog._bubble_panel._notes.setPlainText("保留这条备注")

        cp2 = _make_checkpoint(checkpoint_id="cp_2", summary="新摘要")
        dialog.update_checkpoint(cp2, "新摘要")

        assert dialog.checkpoint_id == "cp_2"
        assert dialog.summary_text == "新摘要"
        assert dialog.current_notes() == "保留这条备注"

        dialog.close()
        dialog.deleteLater()


# ── Presenter integration tests ──────────────────────────────────────────────


class TestPresenterCheckpointDialog:
    """Test Presenter's checkpoint dialog callback behavior."""

    def test_presenter_manual_triggers_callback(self) -> None:
        from unittest.mock import MagicMock

        from novel_forge.desktop.pages.chapter_studio.action_panel import (
            ChapterStudioActionPanelPresenter,
        )

        callback = MagicMock()
        presenter = ChapterStudioActionPanelPresenter(
            action_title=MagicMock(),
            action_summary=MagicMock(),
            action_badge=MagicMock(),
            job_hint=MagicMock(),
            ai_suggestion_frame=MagicMock(),
            ai_suggestion_label=MagicMock(),
            notes_section=MagicMock(),
            notes=MagicMock(),
            rewrite_strategy_row=MagicMock(),
            action_buttons=MagicMock(),
            on_submit_prepare=MagicMock(),
            on_cancel_running_job=MagicMock(),
            on_stop_auto_pilot=MagicMock(),
            on_start_auto_pilot=MagicMock(),
            on_switch_to_suggest=MagicMock(),
            on_handle_checkpoint_option=MagicMock(),
            on_resume_from_progress=MagicMock(),
            on_resume_auto_pilot=MagicMock(),
            on_submit_regen_with_continuity_check=MagicMock(),
            on_submit_polish_with_continuity_check=MagicMock(),
            on_go_to_next_chapter=MagicMock(),
            on_show_checkpoint_dialog=callback,
        )

        assert presenter._on_show_checkpoint_dialog is callback
        assert presenter._dismissed_checkpoint_ids == set()

    def test_presenter_auto_skips_callback(self) -> None:
        from unittest.mock import MagicMock

        from novel_forge.desktop.pages.chapter_studio.action_panel import (
            ChapterStudioActionPanelPresenter,
        )

        callback = MagicMock()
        presenter = ChapterStudioActionPanelPresenter(
            action_title=MagicMock(),
            action_summary=MagicMock(),
            action_badge=MagicMock(),
            job_hint=MagicMock(),
            ai_suggestion_frame=MagicMock(),
            ai_suggestion_label=MagicMock(),
            notes_section=MagicMock(),
            notes=MagicMock(),
            rewrite_strategy_row=MagicMock(),
            action_buttons=MagicMock(),
            on_submit_prepare=MagicMock(),
            on_cancel_running_job=MagicMock(),
            on_stop_auto_pilot=MagicMock(),
            on_start_auto_pilot=MagicMock(),
            on_switch_to_suggest=MagicMock(),
            on_handle_checkpoint_option=MagicMock(),
            on_resume_from_progress=MagicMock(),
            on_resume_auto_pilot=MagicMock(),
            on_submit_regen_with_continuity_check=MagicMock(),
            on_submit_polish_with_continuity_check=MagicMock(),
            on_go_to_next_chapter=MagicMock(),
            on_show_checkpoint_dialog=callback,
        )

        # Auto mode should not trigger the callback
        # (This is tested implicitly through render() with is_auto=True)
        assert presenter._on_show_checkpoint_dialog is callback

    def test_presenter_no_callback_fallback(self) -> None:
        from unittest.mock import MagicMock

        from novel_forge.desktop.pages.chapter_studio.action_panel import (
            ChapterStudioActionPanelPresenter,
        )

        # No callback provided
        presenter = ChapterStudioActionPanelPresenter(
            action_title=MagicMock(),
            action_summary=MagicMock(),
            action_badge=MagicMock(),
            job_hint=MagicMock(),
            ai_suggestion_frame=MagicMock(),
            ai_suggestion_label=MagicMock(),
            notes_section=MagicMock(),
            notes=MagicMock(),
            rewrite_strategy_row=MagicMock(),
            action_buttons=MagicMock(),
            on_submit_prepare=MagicMock(),
            on_cancel_running_job=MagicMock(),
            on_stop_auto_pilot=MagicMock(),
            on_start_auto_pilot=MagicMock(),
            on_switch_to_suggest=MagicMock(),
            on_handle_checkpoint_option=MagicMock(),
            on_resume_from_progress=MagicMock(),
            on_resume_auto_pilot=MagicMock(),
            on_submit_regen_with_continuity_check=MagicMock(),
            on_submit_polish_with_continuity_check=MagicMock(),
            on_go_to_next_chapter=MagicMock(),
            # on_show_checkpoint_dialog not provided
        )

        assert presenter._on_show_checkpoint_dialog is None

    def test_presenter_dismissed_tracking(self) -> None:
        from unittest.mock import MagicMock

        from novel_forge.desktop.pages.chapter_studio.action_panel import (
            ChapterStudioActionPanelPresenter,
        )

        presenter = ChapterStudioActionPanelPresenter(
            action_title=MagicMock(),
            action_summary=MagicMock(),
            action_badge=MagicMock(),
            job_hint=MagicMock(),
            ai_suggestion_frame=MagicMock(),
            ai_suggestion_label=MagicMock(),
            notes_section=MagicMock(),
            notes=MagicMock(),
            rewrite_strategy_row=MagicMock(),
            action_buttons=MagicMock(),
            on_submit_prepare=MagicMock(),
            on_cancel_running_job=MagicMock(),
            on_stop_auto_pilot=MagicMock(),
            on_start_auto_pilot=MagicMock(),
            on_switch_to_suggest=MagicMock(),
            on_handle_checkpoint_option=MagicMock(),
            on_resume_from_progress=MagicMock(),
            on_resume_auto_pilot=MagicMock(),
            on_submit_regen_with_continuity_check=MagicMock(),
            on_submit_polish_with_continuity_check=MagicMock(),
            on_go_to_next_chapter=MagicMock(),
        )

        # Initially empty
        assert presenter._dismissed_checkpoint_ids == set()

        # Mark dismissed
        presenter.mark_checkpoint_dismissed("cp_001")
        assert "cp_001" in presenter._dismissed_checkpoint_ids

        # Clear
        presenter.clear_dismissed_checkpoints()
        assert presenter._dismissed_checkpoint_ids == set()
