"""Tests for Workflow (机杼) visual polish (task 21).

Covers the three polish changes:

1. ``ModeBar`` switch transition: the active mode button gets a 200ms
   opacity fade-in via ``Motion.fade_in`` (D1-safe).
2. Form fields (QLineEdit, QComboBox, QSpinBox) get a 2px focus ring
   outline in the global QSS — matches the pattern established for
   ``QPushButton#actionButton:focus`` (task 9).
3. ``HeroPanel`` exposes a ``fade_in()`` method (200ms) called once
   when ``WorkflowPage._build_ui`` runs.

Plus regression / edge tests:

- Empty ``ShortForm`` / ``LongInitForm`` ``_build_submit_request`` raises
  a Chinese-localized ``ValueError`` (clean error, no crash) so the
  caller's ``try/except`` in ``_submit`` / ``_submit_autorun`` surfaces
  a user-friendly warning dialog.
- ``WorkflowPage`` at 400px width has no child wider than the page
  itself (no horizontal overflow).

Runs under ``QT_QPA_PLATFORM=offscreen`` (set globally in
``pyproject.toml`` / ``tests/conftest.py``).
"""

from __future__ import annotations

import os
import re

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QPropertyAnimation
from PySide6.QtWidgets import QComboBox, QLineEdit, QSpinBox

from novel_forge.desktop.workflow_requests import (
    build_init_long_request,
    build_short_request,
)

# ── Helpers ────────────────────────────────────────────────────────────────


def _patch_motion_supported(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force ``Motion.animations_supported`` to True so the test exercises
    the real fade-in branch on every platform (including macOS CI).
    """
    from novel_forge.desktop import motion

    monkeypatch.setattr(motion, "animations_supported", lambda kind="any": True)


# ── 1. ModeBar 200ms fade-in ──────────────────────────────────────────────


class TestModeBarFadeIn:
    """``ModeBar.select`` triggers a 200ms ``Motion.fade_in`` on the active button."""

    def test_active_button_gets_200ms_fade_in(
        self,
        qtbot,
        desktop_app,
        monkeypatch,
    ) -> None:
        _patch_motion_supported(monkeypatch)
        from novel_forge.desktop.pages.workflow.jobs import ModeBar

        bar = ModeBar()
        qtbot.addWidget(bar)

        # Switch to "long" — inspect the anim BEFORE processEvents();
        # DeleteWhenStopped would otherwise reap the C++ object and
        # PySide6 would crash on the dangling property access.
        bar.select("long")
        anim = bar._btn_long.property("_motion_anim")

        assert anim is not None, "Active button should have a cached _motion_anim"
        assert isinstance(anim, QPropertyAnimation), (
            f"Expected QPropertyAnimation, got {type(anim).__name__}"
        )
        # 200ms = the spec'd duration; Motion.DURATIONS['toggle'] == 200
        assert anim.duration() == 200, (
            f"Expected 200ms duration, got {anim.duration()}ms"
        )
        # Target: opacity 0 → 1
        assert anim.startValue() == 0.0
        assert anim.endValue() == 1.0
        # Property name should be "opacity"
        assert anim.propertyName() == b"opacity"

    def test_short_button_fades_when_switching_to_short(
        self,
        qtbot,
        desktop_app,
        monkeypatch,
    ) -> None:
        _patch_motion_supported(monkeypatch)
        from novel_forge.desktop.pages.workflow.jobs import ModeBar

        bar = ModeBar()
        qtbot.addWidget(bar)

        # Initial select("short") in __init__ is suppressed, so
        # btn_short has no anim yet; the user-driven select must create one.
        bar.select("short")
        anim_after = bar._btn_short.property("_motion_anim")
        assert anim_after is not None
        assert anim_after.duration() == 200

    def test_mode_changed_signal_unchanged(
        self,
        qtbot,
        desktop_app,
    ) -> None:
        """The fade-in must NOT alter the existing mode_changed emission."""
        from novel_forge.desktop.pages.workflow.jobs import ModeBar

        bar = ModeBar()
        qtbot.addWidget(bar)

        emitted: list[str] = []
        bar.mode_changed.connect(emitted.append)

        bar.select("long")
        bar.select("short")
        bar.select("long")

        assert emitted == ["long", "short", "long"], (
            f"Unexpected emissions: {emitted!r}"
        )


# ── 2. Focus outline QSS ───────────────────────────────────────────────────


class TestFocusOutlineQss:
    """The 2px focus ring outline QSS exists in the global stylesheet."""

    def test_outline_rule_in_theme_forms_raw_content(self) -> None:
        """The outline rule must be present in the raw ``theme/forms.py`` CONTENT."""
        from novel_forge.desktop.theme import forms

        outline_pat = re.compile(
            r"QLineEdit:focus[^}]*outline:\s*2px\s+solid\s+"
            r"rgba\(\{\{accent\.primary\}\},\s*0\.6\)",
            re.DOTALL,
        )
        assert outline_pat.search(forms.CONTENT), (
            "Expected theme-token focus outline in "
            "QLineEdit:focus / QComboBox:focus / QSpinBox:focus rules"
        )

    def test_outline_rule_in_resolved_stylesheet(self) -> None:
        """``get_stylesheet()`` must produce a stylesheet that contains the focus outline."""
        from novel_forge.desktop.theme import get_stylesheet

        qss = get_stylesheet()
        # The outline rule is inside a :focus block — confirm both the
        # focus selectors and the outline declaration are present.
        assert "QLineEdit:focus" in qss
        assert "QComboBox:focus" in qss
        assert "QSpinBox:focus" in qss
        assert "outline: 2px solid rgba(182, 86, 52, 0.6)" in qss
        assert "outline-offset: 2px" in qss


# ── 3. Empty form dispatch — clean error ───────────────────────────────────


class TestEmptyFormDispatch:
    """Empty form ``_build_submit_request`` raises a clean ``ValueError``.

    The forms wrap the call in ``try/except`` in ``_submit`` /
    ``_submit_autorun`` and surface the error via
    ``show_warning_message``.  We test the request builders directly
    because that's the source of the error message and the contract
    for the warning dialog.
    """

    def test_build_short_request_raises_for_empty_theme(self) -> None:
        """Empty ``theme`` must raise ``ValueError`` with a Chinese message."""
        with pytest.raises(ValueError, match="故事主题"):
            build_short_request(
                project_id="",
                theme="",
                genre="",
                tone="",
                length_target=5000,
                max_edit_rounds=1,
                title="",
                language="",
                characters_hint="",
                world_hint="",
                conflict_hint="",
                pov_hint="",
                opening_style="",
                ending_style="",
                extra_instructions="",
            )

    def test_build_init_long_request_raises_for_empty_premise(self) -> None:
        """Empty ``premise`` must raise ``ValueError`` with a Chinese message."""
        with pytest.raises(ValueError, match="故事前提"):
            build_init_long_request(
                project_id="",
                premise="",
                genre="",
                tone="",
                total_chapters=10,
                words_per_chapter=4500,
                volume_mode="auto",
                chapters_per_volume=5,
                title="",
                language="",
                characters_hint="",
                world_hint="",
                conflict_hint="",
                pov_hint="",
                opening_style="",
                ending_style="",
                extra_instructions="",
            )

    def test_build_init_long_request_passes_research_fields(self) -> None:
        request = build_init_long_request(
            project_id="demo",
            premise="一名记者调查旧城档案",
            genre="现实悬疑",
            tone="冷峻",
            total_chapters=12,
            words_per_chapter=3500,
            volume_mode="auto",
            chapters_per_volume=0,
            title="",
            language="zh",
            characters_hint="",
            world_hint="",
            conflict_hint="",
            pov_hint="",
            opening_style="",
            ending_style="",
            extra_instructions="",
            research_enabled=True,
            research_provider="brave",
            research_query_hint="城市更新 档案制度",
        )

        assert request.research_enabled is True
        assert request.research_provider == "brave"
        assert request.research_query_hint == "城市更新 档案制度"

    @pytest.mark.parametrize("volume_mode", ["auto", "off"])
    def test_build_init_long_request_discards_irrelevant_volume_size(
        self, volume_mode: str
    ) -> None:
        request = build_init_long_request(
            project_id="demo",
            premise="一名记者调查旧城档案",
            genre="现实悬疑",
            tone="冷峻",
            total_chapters=12,
            words_per_chapter=3500,
            volume_mode=volume_mode,
            chapters_per_volume=12,
            title="",
            language="zh",
            characters_hint="",
            world_hint="",
            conflict_hint="",
            pov_hint="",
            opening_style="",
            ending_style="",
            extra_instructions="",
        )

        assert request.volume_mode == volume_mode
        assert request.chapters_per_volume == 0


class TestLongInitVolumeControls:
    """The dependent volume-size control mirrors the pipeline's three modes."""

    def test_auto_and_off_clear_and_disable_volume_size(self, qtbot, desktop_app) -> None:
        from novel_forge.desktop.pages.workflow.forms import LongInitForm

        form = LongInitForm()
        qtbot.addWidget(form)
        form._volume_mode.setCurrentText("on")  # noqa: SLF001 - UI contract
        form._chapters_per_volume.setValue(12)  # noqa: SLF001 - UI contract

        form._volume_mode.setCurrentText("auto")  # noqa: SLF001 - UI contract
        assert form._chapters_per_volume.value() == 0  # noqa: SLF001
        assert not form._chapters_per_volume.isEnabled()  # noqa: SLF001
        assert form._chapters_per_volume.specialValueText() == "自动"  # noqa: SLF001

        form._volume_mode.setCurrentText("on")  # noqa: SLF001 - UI contract
        assert form._chapters_per_volume.isEnabled()  # noqa: SLF001

        form._chapters_per_volume.setValue(12)  # noqa: SLF001
        form._volume_mode.setCurrentText("off")  # noqa: SLF001 - UI contract
        assert form._chapters_per_volume.value() == 0  # noqa: SLF001
        assert not form._chapters_per_volume.isEnabled()  # noqa: SLF001
        assert form._chapters_per_volume.specialValueText() == "不适用"  # noqa: SLF001

    def test_restoring_legacy_auto_draft_clears_volume_size(self, qtbot, desktop_app) -> None:
        from novel_forge.desktop.pages.workflow.forms import LongInitForm

        form = LongInitForm()
        qtbot.addWidget(form)
        form._fill({"premise": "旧草稿", "volume_mode": "auto", "chapters_per_volume": 12})  # noqa: SLF001

        assert form._chapters_per_volume.value() == 0  # noqa: SLF001
        assert not form._chapters_per_volume.isEnabled()  # noqa: SLF001


# ── 4. Narrow-window edge case — no form overflow ─────────────────────────


class TestNarrowWindowNoOverflow:
    """``WorkflowPage`` does not horizontally overflow at 400px width."""

    def test_no_horizontal_overflow_at_400px_width(
        self,
        qtbot,
        desktop_app,
    ) -> None:
        from novel_forge.desktop.pages.workflow.page import WorkflowPage

        page = WorkflowPage()
        qtbot.addWidget(page)
        try:
            page.resize(400, 600)
            page.show()
            desktop_app.processEvents()

            page_width = page.width()
            assert page_width == 400, f"Expected width=400, got {page_width}"

            # Skip Qt-internal sub-widgets (qt_spinbox_lineedit, etc.)
            # — they're not part of the form's own geometry.
            for widget in page.findChildren(QLineEdit):
                _assert_no_overflow(widget, page, page_width)
            for widget in page.findChildren(QComboBox):
                _assert_no_overflow(widget, page, page_width)
            for widget in page.findChildren(QSpinBox):
                _assert_no_overflow(widget, page, page_width)
        finally:
            page.shutdown()

    def test_mode_bar_buttons_clickable_at_narrow_width(
        self,
        qtbot,
        desktop_app,
    ) -> None:
        """``ModeBar`` buttons are still clickable (not zero-width) at 400px."""
        from novel_forge.desktop.pages.workflow.jobs import ModeBar

        bar = ModeBar()
        qtbot.addWidget(bar)
        bar.resize(400, 80)
        bar.show()
        desktop_app.processEvents()

        assert bar._btn_short.width() > 0
        assert bar._btn_long.width() > 0
        assert bar._btn_short.isVisible()
        assert bar._btn_long.isVisible()


def _assert_no_overflow(widget, page, page_width: int) -> None:
    """Assert that a user-facing form widget fits within the page width."""
    name = widget.objectName() or ""
    if name.startswith("qt_"):
        return
    if not widget.isVisible():
        return
    assert widget.width() <= page_width + 1, (
        f"{type(widget).__name__} {name!r} is wider than the page: "
        f"{widget.width()} > {page_width}"
    )


def _is_in_body(widget, page) -> bool:
    """Return True if *widget* is a descendant of *page*'s body layout.

    Used to limit the overflow scan to widgets that actually live
    inside the page body, not the page's own meta widgets.
    """
    ancestor = widget.parent()
    while ancestor is not None and ancestor is not page:
        ancestor = ancestor.parent()
    return ancestor is page
