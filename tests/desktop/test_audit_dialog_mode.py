"""Tests for BookAuditDialog simple/advanced mode toggle."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from novel_forge.desktop.pages.chapter_studio.dialogs import BookAuditDialog


def _make_dialog() -> BookAuditDialog:
    QApplication.instance() or QApplication([])
    qs = QSettings("NovelForge", "BookAuditDialog")
    qs.clear()
    dlg = BookAuditDialog(completed_chapters=list(range(1, 13)))
    dlg.show()
    return dlg


class TestModeSwitch:
    def test_mode_switch(self) -> None:
        dlg = _make_dialog()
        assert not dlg._is_advanced_mode

        dlg._on_mode_toggle()
        assert dlg._is_advanced_mode

        dlg._on_mode_toggle()
        assert not dlg._is_advanced_mode

    def test_simple_mode_3_params(self) -> None:
        dlg = _make_dialog()
        dlg._apply_mode_state()

        assert not dlg._is_advanced_mode
        assert dlg._mode_toggle_btn.text() == "高级模式"

        core_widgets = [
            dlg._analysis_mode,
            dlg._two_phase_enabled,
            dlg._two_phase_max_target_chapters,
            dlg._repair_mode,
            dlg._audit_max_chapters_per_batch,
        ]
        for w in core_widgets:
            assert w.isVisible(), f"Core widget {w} should be visible in simple mode"

        for w in dlg._advanced_widgets:
            assert not w.isVisible(), f"Advanced widget {w} should be hidden in simple mode"

    def test_repair_options_remain_disabled_in_audit_queue_mode(self) -> None:
        dlg = _make_dialog()
        audit_only_idx = dlg._repair_mode.findData("off")
        dlg._repair_mode.setCurrentIndex(audit_only_idx)
        dlg._on_repair_mode_changed()

        assert dlg._repair_mode.count() == 1
        assert dlg.get_repair_mode() == "off"
        assert not dlg._auto_continue.isEnabled()
        assert not dlg._auto_continue.isVisible()
        assert not dlg._repair_guard_enabled.isEnabled()
        assert not dlg._repair_guard_enabled.isVisible()
        assert dlg.get_auto_continue() is False

    def test_advanced_mode_all_params(self) -> None:
        dlg = _make_dialog()
        dlg._is_advanced_mode = True
        dlg._apply_mode_state()

        assert dlg._is_advanced_mode
        assert dlg._mode_toggle_btn.text() == "简单模式"

        all_visible_widgets = [
            dlg._analysis_mode,
            dlg._two_phase_enabled,
            dlg._two_phase_max_target_chapters,
            dlg._repair_mode,
            dlg._audit_max_chapters_per_batch,
        ] + dlg._advanced_widgets

        for w in all_visible_widgets:
            assert w.isVisible(), f"Widget {w} should be visible in advanced mode"
