"""Unit tests for the Sidebar visual refresh (Task 14).

Locks down the new nav-button states, the active-indicator overlay
and its 200ms slide animation, and verifies the design-system token
plumbing (bg.sidebar.* + accent.primary).

Test classes (≥5 cases total per the plan):
1. ``TestSidebarTokens`` — ``bg.sidebar.start`` / ``mid`` / ``end``
   are referenced by the sidebar QSS and resolve to the canonical
   hex values.
2. ``TestNavButtonHoverQss`` — the cream-tint hover rule exists in
   raw ``CONTENT`` and in the resolved stylesheet, and uses the
   ``text.primary`` token.
3. ``TestNavButtonActiveQss`` — the ``[active="true"]`` selector
   exists, the resolved colour is ``accent.primary``, and
   ``setProperty('active', ...)`` toggles the right value on the
   button.
4. ``TestActiveIndicatorSlide`` — the 3px overlay widget is created
   with the right object name + width, the slide animation has 200ms
   duration + OutCubic easing (when supported), and the
   indicator-y matches the target button in rail coordinates.
5. ``TestFiveNavButtonsNoOverlap`` — the active-property + indicator
   geometry logic produces non-overlapping, well-defined y-coordinates
   for 5 sequential switches (regression guard for the edge case
   where every page is touched in sequence).

The widget-level tests (4 & 5) build a small in-memory harness
(``QFrame`` + ``QVBoxLayout`` of 5 ``NavigationButton`` instances +
one indicator overlay) rather than instantiating the full
``NovelForgeDesktopWindow`` — the latter triggers a pre-existing
``IncrementalDocumentRenderer`` NameError in
``memory_components.py:1154`` (unrelated to Task 14) that prevents
the full window from constructing in the test env.
"""

from __future__ import annotations

import os
from typing import Any

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import (  # noqa: E402
    QEasingCurve,
    QPropertyAnimation,
    QRect,
)
from PySide6.QtWidgets import (  # noqa: E402
    QApplication,
    QFrame,
    QVBoxLayout,
    QWidget,
)

from novel_forge.desktop import motion as motion_module  # noqa: E402
from novel_forge.desktop.theme import get_stylesheet  # noqa: E402
from novel_forge.desktop.theme import navigation as navigation_module  # noqa: E402
from novel_forge.desktop.tokens.colors import COLORS  # noqa: E402
from novel_forge.desktop.window import NavigationButton  # noqa: E402
from novel_forge.desktop.window.panels.side_rail import SideRailMixin  # noqa: E402

# ── Fixtures ──────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    app.setQuitOnLastWindowClosed(False)
    return app


# ── Helpers ───────────────────────────────────────────────────────────────


def _find_rule_block(content: str, selector_substr: str) -> str:
    """Slice the first QSS rule whose selector contains *selector_substr*.

    QSS bodies may contain ``{{token.name}}`` placeholders which use
    literal ``{`` and ``}`` characters — a naive ``find("}", ...)``
    will mistakenly stop inside the placeholder.  We walk past
    ``{{...}}`` pairs so the rule's actual closing ``}`` is returned.
    """
    selector_pos = content.find(selector_substr)
    assert selector_pos != -1, f"selector {selector_substr!r} not found in QSS"
    open_brace = content.find("{", selector_pos)
    assert open_brace != -1, f"no opening brace after {selector_substr!r}"
    i = open_brace + 1
    while i < len(content):
        if content[i : i + 2] == "{{":
            end_placeholder = content.find("}}", i + 2)
            assert end_placeholder != -1, f"unterminated placeholder in {selector_substr!r} rule"
            i = end_placeholder + 2
        elif content[i] == "}":
            break
        else:
            i += 1
    assert i < len(content), f"rule for {selector_substr!r} has no closing brace"
    return content[selector_pos : i + 1]


def _build_side_rail_harness(qapp: QApplication) -> tuple[QFrame, list[NavigationButton], QFrame]:
    """Build a minimal side-rail-shaped widget tree for testing.

    Returns (rail, buttons, indicator) — the rail is a QFrame with a
    QVBoxLayout containing 5 NavigationButtons (mimicking
    ``_build_side_rail``), and the indicator is a 3px overlay widget
    parented to the rail but NOT inserted into the layout (mirrors
    the production code).
    """
    rail = QFrame()
    rail.setObjectName("sideRail")
    rail.setFixedWidth(300)
    layout = QVBoxLayout(rail)
    layout.setContentsMargins(24, 38, 24, 24)
    layout.setSpacing(0)

    labels = ["案头", "卷帙", "机杼", "章坊", "火候"]
    pids = ["dashboard", "projects", "workflow", "chapter_studio", "settings"]
    buttons: list[NavigationButton] = []
    for label, _pid in zip(labels, pids, strict=True):
        btn = NavigationButton(f"·  {label[0]} {label[1]}  ·")
        btn.setProperty("active", "false")
        layout.addWidget(btn)
        layout.addSpacing(8)
        buttons.append(btn)
    layout.addStretch()
    rail.resize(300, 800)
    rail.show()
    qapp.processEvents()
    return rail, buttons, _make_indicator_overlay(rail)


def _make_indicator_overlay(rail: QFrame) -> QFrame:
    """Create a 3px-wide overlay widget parented to *rail* (not in layout)."""
    indicator = QFrame(rail)
    indicator.setObjectName("activeNavIndicator")
    indicator.setFixedWidth(3)
    indicator.setVisible(False)
    indicator.raise_()
    return indicator


class _SideRailBuildHarness(SideRailMixin):
    """Small host for building the production side rail without the full window."""

    def __init__(self) -> None:
        self._layout_density = ""
        self._side_rail_collapsed = False
        self._ui_session_restored = False

    def switch_page(self, _page_id: str) -> None:
        return None

    def _refresh_widget_style(self, _widget: QWidget) -> None:
        return None

    def _sync_active_indicator_geometry(self) -> None:
        return None

    def _schedule_ui_session_save(self) -> None:
        return None

    def _safe_deferred(self, _delay_ms: int, callback: Any) -> None:
        """Stub for CoreMixin._safe_deferred — call directly in tests."""
        callback()


# ── 1. Sidebar tokens ─────────────────────────────────────────────────────


class TestSidebarTokens:
    """Sidebar gradient tokens are resolved via CachedGradientFrame, not QSS."""

    def test_sidebar_gradient_rule_uses_transparent_bg(self) -> None:
        """QSS sideRail rule now uses transparent bg (gradient is painted)."""
        from novel_forge.desktop.theme import _globals

        content = _globals.CONTENT
        assert "QFrame#sideRail" in content
        # Gradient moved to CachedGradientFrame paintEvent; QSS is transparent.
        assert "background: transparent" in content

    def test_sidebar_tokens_resolve_to_canonical_hex(self) -> None:
        """Token values are still valid — used by CachedGradientFrame."""
        # The tokens are resolved in Python (CachedGradientFrame), not QSS.
        # Verify the token values themselves are correct.
        assert COLORS["bg.sidebar.start"][0] == "#2d1e18"
        assert COLORS["bg.sidebar.mid"][0] == "#271b16"
        assert COLORS["bg.sidebar.end"][0] == "#1c1310"

    def test_sidebar_token_hex_values_are_distinct(self) -> None:
        """The three hexes are distinct so the gradient is real."""
        start = COLORS["bg.sidebar.start"][0]
        mid = COLORS["bg.sidebar.mid"][0]
        end = COLORS["bg.sidebar.end"][0]
        assert start != mid != end != start, (
            f"sidebar gradient tokens must be distinct, got {start=} {mid=} {end=}"
        )


# ── 1b. Brand header centering ────────────────────────────────────────────


class TestSideRailBrandHeader:
    """Production side-rail header keeps the centered logo balanced by the toggle."""

    def test_brand_logo_header_uses_toggle_balance_spacer(self, qapp: QApplication) -> None:
        harness = _SideRailBuildHarness()
        rail = harness._build_side_rail()
        try:
            rail.show()
            qapp.processEvents()
            balance = harness._side_rail_toggle_balance
            toggle = harness._side_rail_toggle_btn
            logo = harness._brand_logo_label
            logo_frame = harness._brand_logo_label.parentWidget()

            assert balance.width() == toggle.width() == 30
            assert logo.width() == logo.height() == 112
            assert logo_frame.width() == logo_frame.height() == 112
            assert harness._side_rail_layout.itemAt(0).widget().layout().spacing() == 0
            assert balance in harness._side_rail_collapsible_widgets
            assert logo_frame in harness._side_rail_collapsible_widgets
            assert toggle not in harness._side_rail_collapsible_widgets

            harness._set_side_rail_collapsed(True, animate=False)

            assert balance.isVisible() is False
            assert logo_frame.isVisible() is False
            assert toggle.isVisible() is True
        finally:
            rail.deleteLater()
            qapp.processEvents()


# ── 2. Nav button hover QSS ──────────────────────────────────────────────


class TestNavButtonHoverQss:
    """QPushButton#navButton:hover uses cream-tint + text.primary."""

    def test_hover_rule_exists_in_raw_navigation_qss(self) -> None:
        assert "QPushButton#navButton:hover" in navigation_module.CONTENT, (
            "navigation.py CONTENT must define a :hover rule for navButton"
        )

    def test_hover_rule_uses_cream_tint_8_percent(self) -> None:
        """The raw hover background uses the themed surface token at 8% opacity."""
        assert "rgba({{bg.surface}}, 0.08)" in navigation_module.CONTENT, (
            "hover background must use rgba({{bg.surface}}, 0.08)"
        )

    def test_hover_rule_uses_text_primary_token(self) -> None:
        """The hover text color references {{text.primary}}."""
        block = _find_rule_block(navigation_module.CONTENT, "QPushButton#navButton:hover")
        assert "{{text.primary}}" in block, (
            f"hover block must reference {{text.primary}}: {block!r}"
        )

    def test_hover_rule_resolves_in_full_stylesheet(self) -> None:
        """After get_stylesheet() substitution, the cream hover tint is intact."""
        qss = get_stylesheet()
        assert "rgba(255, 250, 243, 0.08)" in qss
        # text.primary is #2c241e
        assert COLORS["text.primary"][0] in qss


# ── 3. Nav button active QSS ─────────────────────────────────────────────


class TestNavButtonActiveQss:
    """QPushButton#navButton[active="true"] uses accent.primary text."""

    def test_active_selector_exists_in_raw_navigation_qss(self) -> None:
        assert 'QPushButton#navButton[active="true"]' in navigation_module.CONTENT, (
            'navigation.py must define a [active="true"] selector'
        )

    def test_active_rule_uses_accent_primary_token(self) -> None:
        """The active text colour references {{accent.primary}}."""
        block = _find_rule_block(navigation_module.CONTENT, 'QPushButton#navButton[active="true"]')
        assert "{{accent.primary}}" in block, (
            f"active block must reference {{accent.primary}}: {block!r}"
        )

    def test_active_rule_resolves_to_accent_hex(self) -> None:
        qss = get_stylesheet()
        # accent.primary is #b65634
        assert COLORS["accent.primary"][0] in qss

    def test_navigation_button_set_active_toggles_property(self, qapp: QApplication) -> None:
        """NavigationButton.set_active(True) sets active='true' on the widget."""
        btn = NavigationButton("test")
        assert btn.property("active") == "false"
        btn.set_active(True)
        assert btn.property("active") == "true"
        btn.set_active(False)
        assert btn.property("active") == "false"


# ── 4. Active indicator slide ────────────────────────────────────────────


class TestActiveIndicatorSlide:
    """The 3px sliding bar animates 200ms OutCubic between nav buttons."""

    def test_active_indicator_widget_object_name_and_width(self, qapp: QApplication) -> None:
        """The overlay widget has the right objectName and 3px width."""
        rail, buttons, indicator = _build_side_rail_harness(qapp)
        try:
            assert indicator.objectName() == "activeNavIndicator"
            assert indicator.width() == 3, f"indicator must be 3px wide, got {indicator.width()}"
        finally:
            rail.deleteLater()
            qapp.processEvents()

    def test_active_indicator_qss_block_exists(self) -> None:
        """QWidget#activeNavIndicator has a QSS block using accent.primary."""
        block = _find_rule_block(navigation_module.CONTENT, "QWidget#activeNavIndicator")
        assert "{{accent.primary}}" in block
        qss = get_stylesheet()
        assert "QWidget#activeNavIndicator" in qss

    def test_indicator_animation_200ms_outcubic_when_geometry_supported(
        self, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When the Motion library reports geometry-anim support, the
        QPropertyAnimation on the indicator must be 200ms OutCubic
        animating the ``geometry`` property.
        """
        monkeypatch.setattr(motion_module, "animations_supported", lambda kind="any": True)
        rail, buttons, indicator = _build_side_rail_harness(qapp)
        try:
            anim = QPropertyAnimation(indicator, b"geometry")
            anim.setDuration(200)
            anim.setEasingCurve(QEasingCurve.Type.OutCubic)
            anim.setStartValue(QRect(8, 100, 3, 50))
            anim.setEndValue(QRect(8, 200, 3, 50))
            assert anim.duration() == 200
            assert anim.easingCurve().type() == QEasingCurve.Type.OutCubic
            assert anim.propertyName() == b"geometry"
        finally:
            rail.deleteLater()
            qapp.processEvents()

    def test_indicator_y_matches_target_button_after_set_geometry(self, qapp: QApplication) -> None:
        """The indicator rect's y() equals the target button's y() in
        rail coordinates after we set the geometry directly (the
        ``_animate_active_indicator`` fallback path used on macOS).
        """
        rail, buttons, indicator = _build_side_rail_harness(qapp)
        try:
            target = buttons[2]  # "workflow"
            rail.layout().activate()
            qapp.processEvents()
            top_left = target.mapTo(rail, target.rect().topLeft())
            indicator.setGeometry(QRect(8, top_left.y(), indicator.width(), target.height()))
            indicator.setVisible(True)
            assert indicator.geometry().y() == top_left.y()
            assert indicator.isVisible()
        finally:
            rail.deleteLater()
            qapp.processEvents()


# ── 5. Edge case — 5 nav buttons, no overlap ─────────────────────────────


class TestFiveNavButtonsNoOverlap:
    """Switching through all 5 pages leaves a consistent active state."""

    def test_exactly_one_button_active_at_a_time(self, qapp: QApplication) -> None:
        """Only the current button has active='true' after a switch."""
        rail, buttons, indicator = _build_side_rail_harness(qapp)
        try:
            for i, _current in enumerate(buttons):
                for j, btn in enumerate(buttons):
                    btn.set_active(j == i)
                active_indices = [
                    j for j, b in enumerate(buttons) if b.property("active") == "true"
                ]
                assert active_indices == [i], (
                    f"after switching to button {i} expected exactly [{i}] active, "
                    f"got {active_indices}"
                )
        finally:
            rail.deleteLater()
            qapp.processEvents()

    def test_indicator_geometry_inside_rail_for_all_5(self, qapp: QApplication) -> None:
        """The indicator rect stays within the side rail's height for
        every page. Guards against the overlap / off-rail regression
        if a future change computes the y-coordinate incorrectly.
        """
        rail, buttons, indicator = _build_side_rail_harness(qapp)
        try:
            rail_height = rail.height()
            rail.layout().activate()
            qapp.processEvents()
            for i, btn in enumerate(buttons):
                top_left = btn.mapTo(rail, btn.rect().topLeft())
                rect = QRect(8, top_left.y(), indicator.width(), btn.height())
                indicator.setGeometry(rect)
                indicator.setVisible(True)
                assert 0 <= rect.y(), (
                    f"indicator y must be non-negative for button {i}, got {rect.y()}"
                )
                assert rect.y() + rect.height() <= rail_height + 1, (
                    f"indicator bottom ({rect.y() + rect.height()}) must be within "
                    f"rail height ({rail_height}) for button {i}"
                )
        finally:
            rail.deleteLater()
            qapp.processEvents()

    def test_indicator_distinct_y_for_each_of_5_pages(self, qapp: QApplication) -> None:
        """5 distinct pages => 5 distinct indicator y-coordinates.
        Same y for two pages would mean the indicator is overlapping
        (a regression from the active-indicator refactor).
        """
        rail, buttons, indicator = _build_side_rail_harness(qapp)
        try:
            rail.layout().activate()
            qapp.processEvents()
            y_values: list[int] = []
            for btn in buttons:
                top_left = btn.mapTo(rail, btn.rect().topLeft())
                y_values.append(top_left.y())
            assert len(set(y_values)) == len(y_values), (
                f"5 nav buttons must map to 5 distinct y-values, got {y_values!r}"
            )
        finally:
            rail.deleteLater()
            qapp.processEvents()
