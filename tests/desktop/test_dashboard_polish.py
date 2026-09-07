"""Unit tests for Dashboard visual polish (Task 18).

Locks down the new micro-interactions on the dashboard page:
- ProjectCard hover: 200ms scale 1.0 → 1.02 (D1-safe on macOS).
- FilterChip active state change: 150ms scale pop feedback.
- Hero panel: fade-in 200ms + scale 0.98 → 1.0 over 300ms (D1/D10 safe).
- Card grid: stagger entrance (50ms delay between each card's fade-in).
- Empty state: when the project list is empty, the EmptyState surface is
  the canonical "no projects" affordance — verified without binding a
  workspace.

The tests run under ``QT_QPA_PLATFORM=offscreen`` so no real display
window is opened. The Motion library runs the full animation timeline
in a single ``processEvents()`` call in offscreen mode, so the QA
helper asserts animation *durations* (which are set at construction)
rather than transient state.
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPointF, QPropertyAnimation, QTimer  # noqa: E402
from PySide6.QtGui import QEnterEvent  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402
from shiboken6 import delete  # noqa: E402

from novel_forge.desktop.components.primitives import FilterChip  # noqa: E402
from novel_forge.desktop.pages.standalone.dashboard_page import (  # noqa: E402
    DashboardPage,
    ProjectCard,
)
from novel_forge.desktop.theme import get_stylesheet  # noqa: E402
from novel_forge.desktop.workspace import DesktopProjectItem  # noqa: E402

# ── Fixtures ─────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    app.setQuitOnLastWindowClosed(False)
    return app


def _make_enter_event() -> QEnterEvent:
    return QEnterEvent(QPointF(0, 0), QPointF(0, 0), QPointF(0, 0))


def _make_project(
    project_id: str = "p1",
    *,
    mode: str = "long",
    status: str = "writing",
    title: str = "Test 卷",
) -> DesktopProjectItem:
    return DesktopProjectItem(
        project_id=project_id,
        title=title,
        mode=mode,
        mode_label="长篇" if mode == "long" else "短篇",
        status=status,
        status_label={"writing": "连载中", "planning": "筹备中", "completed": "已完成"}.get(
            status, status
        ),
        progress_label="30%",
        progress_percent=30,
        last_updated_label="今日",
        headline="测试卷摘要",
        next_action="续写",
        genre="mystery",
        tone="suspenseful",
        completed_chapters=3,
        total_chapters=10,
        next_chapter=4,
        has_outline=True,
        has_canon=True,
    )


# ── ProjectCard hover animation ──────────────────────────────────


class TestProjectCardHover:
    """ProjectCard hover: 200ms scale 1.0 → 1.02 via Motion library."""

    def test_hover_constant_scale_is_1_02(self, qapp: QApplication) -> None:
        assert ProjectCard.HOVER_SCALE == pytest.approx(1.02, abs=1e-6)

    def test_hover_constant_duration_is_200ms(self, qapp: QApplication) -> None:
        assert ProjectCard.HOVER_DURATION_MS == 200

    def test_hover_creates_animation_with_200ms(self, qapp: QApplication) -> None:
        project = _make_project()
        card = ProjectCard(project)
        card.show()
        card.enterEvent(_make_enter_event())
        qapp.processEvents()
        assert card._hover_anim is not None, "Hover animation must be created on enterEvent"
        assert card._hover_anim.duration() == 200, (
            f"Hover animation must be 200ms (Task 18 spec), got {card._hover_anim.duration()}ms"
        )

    def test_hover_animation_target_scale_1_02(self, qapp: QApplication) -> None:
        project = _make_project()
        card = ProjectCard(project)
        card.show()
        card.enterEvent(_make_enter_event())
        qapp.processEvents()
        assert card._hover_anim is not None
        assert card._hover_anim.endValue() == pytest.approx(1.02, abs=1e-6)

    def test_leave_returns_to_scale_1_0(self, qapp: QApplication) -> None:
        project = _make_project()
        card = ProjectCard(project)
        card.show()
        card.enterEvent(_make_enter_event())
        qapp.processEvents()
        # On leave, target flips back to 1.0
        card.leaveEvent(_make_enter_event())
        qapp.processEvents()
        assert card._hover_anim is not None
        # endValue is 1.0 because the leave animation just started
        assert card._hover_anim.endValue() == pytest.approx(1.0, abs=1e-6)

    def test_rapid_hover_no_crash(self, qapp: QApplication) -> None:
        project = _make_project()
        card = ProjectCard(project)
        card.show()
        for _ in range(20):
            card.enterEvent(_make_enter_event())
            card.leaveEvent(_make_enter_event())
            qapp.processEvents()
        # Animation must still be queryable
        assert card._hover_anim is not None


# ── FilterChip active transition ─────────────────────────────────


class TestFilterChipActiveSwitch:
    """FilterChip setChecked that flips the state triggers a 150ms pop."""

    def test_active_duration_constant_is_150ms(self, qapp: QApplication) -> None:
        assert FilterChip.ACTIVE_DURATION_MS == 150

    def test_state_flip_creates_animation(self, qapp: QApplication) -> None:
        chip = FilterChip("全部", active=False)
        chip.show()
        # Flipping False → True must create the active animation
        chip.setChecked(True)
        qapp.processEvents()
        assert chip._active_anim is not None, (
            "Active-state animation must be created on state flip"
        )
        assert chip._active_anim.duration() == 150

    def test_no_animation_on_redundant_set(self, qapp: QApplication) -> None:
        """Setting the same value twice must NOT re-trigger the pop."""
        chip = FilterChip("全部", active=True)
        chip.show()
        # Initial state already True, so this is a no-op state-wise
        chip.setChecked(True)
        qapp.processEvents()
        # _active_anim is None because no flip happened
        assert chip._active_anim is None

    def test_target_scale_is_pop_on_activation(self, qapp: QApplication) -> None:
        chip = FilterChip("全部", active=False)
        chip.show()
        chip.setChecked(True)
        qapp.processEvents()
        assert chip._active_anim is not None
        assert chip._active_anim.endValue() == pytest.approx(
            FilterChip.ACTIVE_POP_SCALE, abs=1e-6
        )

    def test_deleted_active_animation_reference_no_crash(self, qapp: QApplication) -> None:
        chip = FilterChip("全部", active=False)
        chip.show()
        stale = QPropertyAnimation(chip, b"scale")
        delete(stale)
        chip._active_anim = stale

        chip.setChecked(True)
        qapp.processEvents()

        assert chip._active_anim is not stale

    def test_dashboard_filter_chips_present(self, qapp: QApplication) -> None:
        page = DashboardPage()
        try:
            assert set(page._filter_buttons.keys()) == {
                "all", "writing", "planning", "completed", "long", "short"
            }
            for chip in page._filter_buttons.values():
                assert isinstance(chip, FilterChip)
        finally:
            page.close()
            page.deleteLater()
            qapp.processEvents()


# ── Hero fade-in + scale ──────────────────────────────────────────


class TestHeroFadeIn:
    """Hero panel fade-in (200ms) + scale 0.98→1.0 (300ms) on show."""

    def test_hero_constants(self, qapp: QApplication) -> None:
        assert DashboardPage.HERO_FADE_DURATION_MS == 200
        assert DashboardPage.HERO_SCALE_DURATION_MS == 300

    def test_hero_is_animated_subclass(self, qapp: QApplication) -> None:
        page = DashboardPage()
        try:
            from novel_forge.desktop.pages.standalone.dashboard_page import _AnimatedHero
            assert isinstance(page._hero_panel, _AnimatedHero), (
                "Hero panel must be _AnimatedHero for scale support"
            )
        finally:
            page.close()
            page.deleteLater()
            qapp.processEvents()

    def test_hero_panel_has_scale_property(self, qapp: QApplication) -> None:
        page = DashboardPage()
        try:
            # The _AnimatedHero class exposes a Qt Property named "scale"
            meta = page._hero_panel.metaObject()
            found = False
            for i in range(meta.propertyCount()):
                if meta.property(i).name() == "scale":
                    found = True
                    break
            assert found, "Hero panel must expose a 'scale' property for Motion.scale"
        finally:
            page.close()
            page.deleteLater()
            qapp.processEvents()

    def test_show_triggers_hero_animation(self, qapp: QApplication) -> None:
        page = DashboardPage()
        try:
            assert getattr(page, "_hero_intro_done", False) is False, (
                "Sanity: hero intro flag must start False"
            )
            page.show()
            qapp.processEvents()
            assert page._hero_intro_done is True, (
                "showEvent must set _hero_intro_done after running the animation"
            )
        finally:
            page.close()
            page.deleteLater()
            qapp.processEvents()

    def test_hero_animation_creates_running_anim(self, qapp: QApplication) -> None:
        page = DashboardPage()
        try:
            # Patch Motion.scale to capture the created animation so we can
            # inspect its duration. This avoids the use-after-free that
            # would happen if we read ``_motion_anim`` after the animation
            # has been GC'd by ``DeleteWhenStopped``.
            from novel_forge.desktop import motion as motion_mod
            captured: list = []
            real_scale = motion_mod.Motion.scale

            def _capture_scale(widget, **kwargs):
                anim = real_scale(widget, **kwargs)
                captured.append(anim)
                return anim

            motion_mod.Motion.scale = staticmethod(_capture_scale)
            try:
                page.show()
                qapp.processEvents()
            finally:
                motion_mod.Motion.scale = real_scale

            from PySide6.QtCore import QPropertyAnimation
            # We expect at least one 300ms scale animation (the hero entrance).
            durations = [a.duration() for a in captured if isinstance(a, QPropertyAnimation)]
            assert 300 in durations, (
                f"Expected a 300ms hero scale animation, captured durations: {durations}"
            )
        finally:
            page.close()
            page.deleteLater()
            qapp.processEvents()

    def test_animate_hero_runs_safely_when_called_twice(self, qapp: QApplication) -> None:
        page = DashboardPage()
        try:
            page.show()
            qapp.processEvents()
            # Re-invoking must not raise (idempotent entrance)
            page._animate_hero()
            qapp.processEvents()
        finally:
            page.close()
            page.deleteLater()
            qapp.processEvents()


# ── Stagger card entrance ────────────────────────────────────────


class TestStaggerEntrance:
    """Card grid uses 50ms stagger between each card's fade-in."""

    def test_stagger_delay_constant(self, qapp: QApplication) -> None:
        assert DashboardPage.CARD_STAGGER_DELAY_MS == 50
        assert DashboardPage.CARD_STAGGER_FADE_MS == 220

    def test_animate_project_cards_queues_stagger(self, qapp: QApplication) -> None:
        page = DashboardPage()
        try:
            page._animated_card_ids = set()
            page._stagger_pending = set()
            page.show()
            qapp.processEvents()
            # Patch QTimer.singleShot to invoke the slot immediately
            # (the test is about queueing logic, not real timer firing —
            # offscreen mode does not advance virtual time enough to drain
            # 50ms-staggered timers in one processEvents call).
            from novel_forge.desktop.pages import dashboard_page as dp
            captured: list = []
            real_single_shot = dp.QTimer.singleShot

            def _fake_single_shot(delay_ms, slot):
                captured.append(delay_ms)
                slot()

            dp.QTimer.singleShot = staticmethod(_fake_single_shot)
            try:
                page._all_projects = [_make_project(f"pid{i}") for i in range(3)]
                page._render_cards()
            finally:
                dp.QTimer.singleShot = real_single_shot

            # Verify the stagger pattern: 0ms, 50ms, 100ms
            assert captured == [0, 50, 100], (
                f"Expected stagger delays [0, 50, 100], got {captured}"
            )
            # All 3 cards should have moved from _stagger_pending to
            # _animated_card_ids via the immediate-fire fake.
            assert len(page._animated_card_ids) == 3, (
                f"Expected 3 animated cards, got {len(page._animated_card_ids)}"
            )
            assert page._stagger_pending == set()
        finally:
            page.close()
            page.deleteLater()
            qapp.processEvents()

    def test_already_animated_cards_are_skipped(self, qapp: QApplication) -> None:
        page = DashboardPage()
        try:
            page._animated_card_ids = {"p1"}
            page._stagger_pending = set()
            cards = [ProjectCard(_make_project("p1"))]
            cards[0].show()
            page._animate_project_cards(cards)
            qapp.processEvents()
            assert len(page._stagger_pending) == 0, (
                "Already-animated card must not be re-queued"
            )
        finally:
            for c in cards:
                c.close()
                c.deleteLater()
            page.close()
            page.deleteLater()
            qapp.processEvents()

    def test_qtimer_pending_for_3_cards(self, qapp: QApplication) -> None:
        """Each new card should schedule a QTimer.singleShot (50ms stagger)."""
        page = DashboardPage()
        try:
            page._animated_card_ids = set()
            page._stagger_pending = set()
            cards = [ProjectCard(_make_project(f"p{i}")) for i in range(3)]
            for c in cards:
                c.show()
            page._animate_project_cards(cards)
            # Before processEvents, the QTimer.singleShot timers are pending
            timers = page.findChildren(QTimer)
            assert len(timers) >= 1, "At least one QTimer should be present"
        finally:
            for c in cards:
                c.close()
                c.deleteLater()
            page.close()
            page.deleteLater()
            qapp.processEvents()


# ── Edge case: empty project list ────────────────────────────────


class TestEmptyStateEdgeCase:
    """When the project list is empty, EmptyState is the visible affordance."""

    def test_empty_state_widget_present(self, qapp: QApplication) -> None:
        page = DashboardPage()
        try:
            assert page._empty is not None, "EmptyState widget must exist"
            # Default state: hidden (until render_cards sees an empty filter)
            assert page._empty.isHidden() is True
        finally:
            page.close()
            page.deleteLater()
            qapp.processEvents()

    def test_empty_state_shown_when_no_projects(self, qapp: QApplication) -> None:
        page = DashboardPage()
        try:
            page.show()
            qapp.processEvents()
            page._all_projects = []
            page._render_cards()
            qapp.processEvents()
            assert page._empty.isHidden() is False, (
                "EmptyState must be unhidden when the project list is empty"
            )
        finally:
            page.close()
            page.deleteLater()
            qapp.processEvents()

    def test_empty_state_shown_when_filter_excludes_all(self, qapp: QApplication) -> None:
        page = DashboardPage()
        try:
            page.show()
            qapp.processEvents()
            page._all_projects = [_make_project("p1", status="writing")]
            page._active_filter = "completed"  # Nothing matches
            page._render_cards()
            qapp.processEvents()
            assert page._empty.isHidden() is False
            # No project cards should exist
            assert len(page._project_card_widgets) == 0
        finally:
            page.close()
            page.deleteLater()
            qapp.processEvents()

    def test_unbound_dashboard_does_not_crash(self, qapp: QApplication) -> None:
        """An unbound dashboard (no bind_workspace call) must construct and
        show without exceptions.  This is the canonical "edge case: empty
        project list" the spec requires."""
        page = DashboardPage()
        try:
            page.show()
            qapp.processEvents()
            assert page._empty is not None
            # No crash, page is alive
            assert page.isVisible() is True
        finally:
            page.close()
            page.deleteLater()
            qapp.processEvents()


# ── QSS contract ─────────────────────────────────────────────────


class TestQss:
    """Verify the QSS additions: card hover border, search focus outline."""

    def test_card_hover_qss_exists(self) -> None:
        qss = get_stylesheet()
        assert 'QFrame#surface[tone="card"]:hover' in qss, (
            "Card hover QSS selector must be present for border accent"
        )

    def test_search_focus_qss_exists(self) -> None:
        qss = get_stylesheet()
        assert "QLineEdit#dashboardSearch" in qss
        assert "QLineEdit#dashboardSearch:focus" in qss
        assert "outline" in qss


# ─────────────────────────────────────────────────────────────────────
# 在库卷册卡片 fade-out 遮罩 + 完整描述 tooltip (Task: 优化卷帙在库卷册硬截断)
# ─────────────────────────────────────────────────────────────────────


class TestProjectCardFadeBodyAndTooltip:
    """验证“描述被硬截断”优化的三项表面行为。"""

    @staticmethod
    def _project_with(**overrides) -> DesktopProjectItem:
        """Build a project item with optional field overrides via dataclasses.replace."""
        import dataclasses

        base = _make_project()
        return dataclasses.replace(base, **overrides)

    def test_body_uses_fade_label_with_word_wrap_and_card_body_object_name(
        self, qapp: QApplication
    ) -> None:
        from novel_forge.desktop.pages.standalone.dashboard_page import _FadeBodyLabel

        project = self._project_with(headline="这是用于测试的简短摘要。")
        card = ProjectCard(project)
        try:
            assert isinstance(card._body, _FadeBodyLabel)
            assert card._body.objectName() == "cardBody"
            assert card._body.wordWrap() is True
        finally:
            card.deleteLater()
            qapp.processEvents()

    def test_body_paint_event_does_not_raise(self, qapp: QApplication) -> None:
        """调用 paintEvent 必须不抛异常（尤其在背景透明度为 0 的 offscreen 环境）。"""
        from PySide6.QtGui import QPaintEvent

        from novel_forge.desktop.pages.standalone.dashboard_page import _FadeBodyLabel

        label = _FadeBodyLabel("这是用于测试的文字，包含中文标点、，、。！测试。")
        label.resize(180, 60)
        try:
            event = QPaintEvent(label.rect())
            # 不期望任何异常被抛出。
            label.paintEvent(event)
            qapp.processEvents()
        finally:
            label.deleteLater()
            qapp.processEvents()

    def test_card_tooltip_shows_full_preview_when_headline_truncated(
        self, qapp: QApplication
    ) -> None:
        """headline 截断时，tooltip 必须保留完整 headline_full 原文。"""
        long_full = (
            "沈岸是南方临海老城醒梦事务所的老板，靠已故恋人苏晚留下的神经校准坏表，"
            "以神经同步技术进入访客的三层流速递增梦境心结，一旦坏表校准失效便会沦"
            "为脑死亡的梦傻。客户端梦境深处不断发现当年害死苏晚的墓后势力残存的神经数据碎片。"
        )
        project = self._project_with(
            headline="沈岸是南方临海老城醒梦事务所的老板…",
            headline_full=long_full,
        )
        card = ProjectCard(project)
        try:
            assert card.toolTip() == long_full
        finally:
            card.deleteLater()
            qapp.processEvents()

    def test_card_tooltip_falls_back_to_headline_when_no_full_text(
        self, qapp: QApplication
    ) -> None:
        """headline_full 为空时，tooltip 不设置（避免只显示截断后的残文）。"""
        project = self._project_with(headline="测试卷摘要")
        # headline_full 默认空串
        card = ProjectCard(project)
        try:
            assert card.toolTip() == ""  # tooltip 未设置
        finally:
            card.deleteLater()
            qapp.processEvents()

    def test_card_tooltip_skipped_when_headline_already_full(
        self, qapp: QApplication
    ) -> None:
        """headline 与 headline_full 相同时，不设置重复 tooltip。"""
        full = "完整原文在此，未截断。"
        project = self._project_with(headline=full, headline_full=full)
        card = ProjectCard(project)
        try:
            assert card.toolTip() == ""
        finally:
            card.deleteLater()
            qapp.processEvents()

    # ── 完善性修复 1：fade 遮罩只在文字真的被裁掉时绘制 ────────────

    def test_fade_label_skips_mask_when_text_fits(self, qapp: QApplication) -> None:
        """短文本完整可见时 _text_is_clipped 必须为 False（不画遮罩）。"""
        from novel_forge.desktop.pages.standalone.dashboard_page import _FadeBodyLabel

        label = _FadeBodyLabel("梦境探险")
        label.resize(300, 120)  # 足够高，单行文本完整可见
        try:
            qapp.processEvents()
            assert label._text_is_clipped() is False
        finally:
            label.deleteLater()
            qapp.processEvents()

    def test_fade_label_draws_mask_when_text_clipped(self, qapp: QApplication) -> None:
        """长文本在矮控件中被裁掉时 _text_is_clipped 必须为 True（画遮罩）。"""
        from novel_forge.desktop.pages.standalone.dashboard_page import _FadeBodyLabel

        long_text = (
            "沈岸是南方临海老城醒梦事务所的老板，靠已故恋人苏晚留下的神经校准坏表，"
            "以神经同步技术进入访客的三层流速递增梦境心结，一旦坏表校准失效便会沦"
            "为脑死亡的梦傻。"
        )
        label = _FadeBodyLabel(long_text)
        label.resize(180, 40)  # 矮控件，文字必然被裁
        try:
            qapp.processEvents()
            assert label._text_is_clipped() is True
        finally:
            label.deleteLater()
            qapp.processEvents()

    # ── 完善性修复 2：update_project 同步刷新 tooltip ───────────────

    def test_update_project_refreshes_tooltip(self, qapp: QApplication) -> None:
        """项目数据刷新后 tooltip 必须同步更新，不残留旧全文。"""
        old_full = "旧的完整描述，很长很长很长很长很长很长很长很长很长很长很长很长。"
        new_full = "新的完整描述，同样很长很长很长很长很长很长很长很长很长很长。"
        project = self._project_with(
            headline="旧的完整描述，很长很长…",
            headline_full=old_full,
        )
        card = ProjectCard(project)
        try:
            assert card.toolTip() == old_full
            updated = self._project_with(
                headline="新的完整描述，同样很…",
                headline_full=new_full,
            )
            card.update_project(updated)
            assert card.toolTip() == new_full
        finally:
            card.deleteLater()
            qapp.processEvents()

    # ── 完善性修复 3：搜索匹配使用完整 headline_full ───────────────

    def test_search_matches_full_headline_beyond_truncation_limit(
        self, qapp: QApplication
    ) -> None:
        """搜索词位于 80 字符截断点之后时，仍应能命中（用 headline_full 匹配）。"""
        from novel_forge.desktop.pages.standalone.dashboard_page import DashboardPage

        tail_keyword = "神经数据碎片"  # 位于长文本尾部，超出 80 字符截断点
        long_full = (
            "沈岸是南方临海老城醒梦事务所的老板，靠已故恋人苏晚留下的神经校准坏表，"
            "以神经同步技术进入访客的三层流速递增梦境心结，一旦坏表校准失效便会沦"
            "为脑死亡的梦傻。客户梦境深处不断发现当年害死苏晚的墓后势力残存的"
            "神经数据碎片。"
        )
        project = self._project_with(
            title="梦侦探",
            headline="沈岸是南方临海老城醒梦事务所的老板，靠已故恋人苏晚留下…",
            headline_full=long_full,
        )
        page = DashboardPage()
        try:
            page._all_projects = [project]
            page._search.setText(tail_keyword)
            result = page._filtered_projects()
            assert [item.project_id for item in result] == [project.project_id]
        finally:
            page.deleteLater()
            qapp.processEvents()
