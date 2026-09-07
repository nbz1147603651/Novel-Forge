"""Mixin module: navigation methods for ``NovelForgeDesktopWindow``.

Auto-extracted in the M3.1 giant-file-split refactor. Methods live here so the
main :file:`window/__init__.py` facade can compose them via mixin inheritance.
"""

from __future__ import annotations

import logging
import sys
from typing import cast

import shiboken6
from PySide6.QtCore import (
    QAbstractAnimation,
    QEasingCurve,
    QObject,
    QPoint,
    QPropertyAnimation,
    QRect,
    Qt,
)
from PySide6.QtGui import QWindow
from PySide6.QtWidgets import (
    QGraphicsEffect,
    QWidget,
)

import novel_forge.desktop.page_registrations as _page_registrations  # noqa: F401
from novel_forge.desktop.constants import (
    COMPACT_HEIGHT_THRESHOLD,
    COMPACT_WIDTH_THRESHOLD,
    WINDOW_DEFAULT_MIN_HEIGHT,
    WINDOW_DEFAULT_MIN_WIDTH,
    WINDOW_DEFAULT_START_HEIGHT,
    WINDOW_DEFAULT_START_WIDTH,
    WINDOW_SCREEN_HEIGHT_RATIO,
    WINDOW_SCREEN_WIDTH_RATIO,
)
from novel_forge.desktop.motion import Motion
from novel_forge.desktop.motion import animations_supported as motion_animations_supported
from novel_forge.desktop.registry import page_registry
from novel_forge.desktop.ui_perf import ui_perf_span
from novel_forge.desktop.window._navigation import switch_page as _navigation_switch_page
from novel_forge.desktop.window._post_switch import _PostSwitchSequencer

_logger = logging.getLogger(__name__)

_WINDOW_DEFAULT_MIN_WIDTH = WINDOW_DEFAULT_MIN_WIDTH
_WINDOW_DEFAULT_MIN_HEIGHT = WINDOW_DEFAULT_MIN_HEIGHT
_WINDOW_DEFAULT_START_WIDTH = WINDOW_DEFAULT_START_WIDTH
_WINDOW_DEFAULT_START_HEIGHT = WINDOW_DEFAULT_START_HEIGHT
_WINDOW_SCREEN_WIDTH_RATIO = WINDOW_SCREEN_WIDTH_RATIO
_WINDOW_SCREEN_HEIGHT_RATIO = WINDOW_SCREEN_HEIGHT_RATIO
_COMPACT_WIDTH_THRESHOLD = COMPACT_WIDTH_THRESHOLD
_COMPACT_HEIGHT_THRESHOLD = COMPACT_HEIGHT_THRESHOLD


def _animations_supported(kind: str = "any") -> bool:
    import novel_forge.desktop.window as window_facade

    checker = getattr(window_facade, "animations_supported", motion_animations_supported)
    try:
        return bool(checker(kind=kind))
    except TypeError:
        try:
            return bool(checker(kind))
        except TypeError:
            return bool(checker())


class NavigationMixin:
    """Mixin that contributes the **navigation** method group."""

    _RAPID_PAGE_SWITCH_WINDOW_MS = 180

    def switch_page(self, page_id: str) -> None:
        _navigation_switch_page(self, page_id)

    def _schedule_deferred_page_creation(
        self,
        page_id: str,
        generation: int,
        focus_tab: str | None,
    ) -> None:
        def _create_page() -> None:
            if not self._post_switch_current(page_id, generation):
                return
            with ui_perf_span("post_switch.deferred_create", page_id=page_id):
                # Signal to _ensure_page that it should NOT force a synchronous
                # deferred build; the showEvent + timer path handles it.
                self._deferred_page_creation_active = True
                try:
                    page = self._ensure_page(
                        page_id,
                        bind_workspace=False,
                        bind_jobs=False,
                        perf_label="post_switch.ensure_page",
                    )
                finally:
                    self._deferred_page_creation_active = False
                if page is None:
                    return
                self._stack.setCurrentWidget(page)
                self._apply_page_meta(page_id)
            self._schedule_post_switch_work(page_id, generation, focus_tab)

        self._safe_deferred(16, _create_page)

    def _post_switch_current(self, page_id: str, generation: int) -> bool:
        return (
            self._window_callbacks_allowed()
            and generation == self._page_activate_generation
            and self._active_page_id == page_id
        )

    def _window_callbacks_allowed(self) -> bool:
        if getattr(self, "_is_closing", False):
            return False
        try:
            if not shiboken6.isValid(self):
                return False
            stack = getattr(self, "_stack", None)
            if isinstance(stack, QObject) and not shiboken6.isValid(stack):
                return False
        except RuntimeError:
            return False
        return True

    def _schedule_post_switch_work(
        self,
        page_id: str,
        generation: int,
        focus_tab: str | None,
    ) -> None:
        """Defer non-visual switch work so the target page paints first.

        Uses a single :class:`_PostSwitchSequencer` to run workspace_bind →
        jobs_bind → activate in a deterministic order, replacing the previous
        pattern of 3 independent ``QTimer.singleShot`` calls.
        """
        # Cancel any previous sequencer
        prev = getattr(self, "_post_switch_sequencer", None)
        if prev is not None:
            prev.cancel()
        seq = _PostSwitchSequencer(self, page_id, generation, focus_tab)
        self._post_switch_sequencer = seq
        seq.start()
        if self._ui_session_restored:
            self._schedule_ui_session_save()

    def _apply_switch_focus_tab(self, page_id: str, focus_tab: str) -> None:
        if page_id == "projects" and focus_tab.startswith("relationships"):
            # Support "relationships:{project_id}" to pre-select a project.
            rel_parts = focus_tab.split(":", 1)
            projects_page = self._pages.get("projects")
            if projects_page is None:
                return
            if len(rel_parts) > 1 and rel_parts[1]:
                projects_page.load_project(rel_parts[1])
            projects_page.focus_relationship_tab()
            return

        if page_id == "projects" and focus_tab.startswith("project:"):
            projects_page = self._pages.get("projects")
            if projects_page is None:
                return
            payload = focus_tab.removeprefix("project:")
            project_id = payload
            focus_target = ""
            known_targets = {"blueprint", "relationships", "characters", "consistency"}
            if ":" in payload:
                candidate_id, candidate_target = payload.rsplit(":", 1)
                if candidate_target in known_targets:
                    project_id = candidate_id
                    focus_target = candidate_target
            if not project_id:
                return
            projects_page.load_project(project_id)
            focus_method = {
                "blueprint": "focus_narrative_blueprint_tab",
                "relationships": "focus_relationship_tracking_tab",
                "characters": "focus_character_bible_tab",
                "consistency": "focus_book_consistency_tab",
            }.get(focus_target)
            if focus_method:
                callback = getattr(projects_page, focus_method, None)
                if callable(callback):
                    callback()
            return

        if page_id != "chapter_studio":
            return
        # Support "chapter_studio:{project_id}:{chapter_number}" deep-link.
        _cs_parts = focus_tab.split(":", 1)
        _cs_project = _cs_parts[0] if _cs_parts else ""
        _cs_chapter: int | None = None
        if len(_cs_parts) > 1:
            try:
                _cs_chapter = int(_cs_parts[1])
            except (ValueError, IndexError):
                pass
        if not _cs_project:
            return
        chapter_page = self._pages.get("chapter_studio")
        if chapter_page is not None:
            chapter_page.focus_project(_cs_project, _cs_chapter)

    def _schedule_page_activation(self, page_id: str, page: QWidget) -> None:
        """Run expensive page activation after the switched page has painted.

        Any page that implements ``activate()`` or ``schedule_deferred_build()``
        gets deferred activation.  Previously this was settings-only.
        """
        if not hasattr(page, "activate") and not hasattr(page, "schedule_deferred_build"):
            return
        generation = self._page_activate_generation

        def _activate_if_current() -> None:
            if generation != self._page_activate_generation:
                return
            if self._active_page_id != page_id:
                return
            schedule_deferred_build = getattr(page, "schedule_deferred_build", None)
            if callable(schedule_deferred_build):
                schedule_deferred_build()
            if not hasattr(page, "activate"):
                return
            with ui_perf_span("page_activate", page_id=page_id):
                page.activate()

        self._safe_deferred(self._DEFERRED_PAGE_ACTIVATE_MS, _activate_if_current)

    def _compute_indicator_rect(self, page_id: str) -> QRect | None:
        """Target rect for the active indicator, or None pre-show."""
        if getattr(self, "_side_rail_collapsed", False):
            return None
        button = self._nav_buttons.get(page_id)
        rail = self._side_rail
        if button is None or rail is None:
            return None
        rail_layout = rail.layout()
        if rail_layout is not None:
            rail_layout.activate()
        btn_geom = button.geometry()
        if btn_geom.height() <= 0 or btn_geom.width() <= 0:
            return None
        top_left_in_rail = button.mapTo(rail, QPoint(0, 0))
        return QRect(
            self._ACTIVE_INDICATOR_INSET,
            top_left_in_rail.y(),
            self._active_indicator.width(),
            btn_geom.height(),
        )

    def _sync_active_indicator_geometry(self) -> None:
        """Realign the active indicator after resize/density layout changes."""
        if not hasattr(self, "_active_indicator") or not hasattr(self, "_stack"):
            return
        if getattr(self, "_side_rail_collapsed", False):
            self._active_indicator.setVisible(False)
            return

        def _deferred_sync() -> None:
            try:
                if self._active_indicator is None:
                    return
                self._animate_active_indicator(self._current_page_id())
            except RuntimeError:
                return

        self._safe_deferred(0, _deferred_sync)

    def _animate_active_indicator(self, page_id: str) -> None:
        """Slide the active indicator to the page's nav button."""
        target = self._compute_indicator_rect(page_id)
        if target is None:
            return
        indicator = self._active_indicator
        current = indicator.geometry()
        if not indicator.isVisible():
            indicator.setGeometry(target)
            indicator.setVisible(True)
            return
        if current == target:
            return
        if getattr(self, "_rapid_page_switch", False) or not _animations_supported("geometry"):
            indicator.setGeometry(target)
            return
        if self._indicator_anim is not None:
            self._indicator_anim.stop()
        self._indicator_anim = QPropertyAnimation(indicator, b"geometry")
        self._indicator_anim.setDuration(self._ACTIVE_INDICATOR_DURATION_MS)
        self._indicator_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._indicator_anim.setStartValue(current)
        self._indicator_anim.setEndValue(target)
        self._indicator_anim.start(QAbstractAnimation.DeletionPolicy.DeleteWhenStopped)

    def _skip_page_motion_for_window_state(self) -> bool:
        """Avoid Qt/AppKit compositor churn while macOS owns the window state.

        Qt does not report native macOS fullscreen through ``isFullScreen()``
        consistently during Spaces transitions.  Treat maximized and visually
        screen-filling windows as unsafe too: attaching ``QGraphicsOpacityEffect``
        during page switches can cause AppKit to leave the fullscreen Space.
        """
        if sys.platform != "darwin":
            return False
        if self.isFullScreen():
            return True

        fullscreen_states = Qt.WindowState.WindowFullScreen | Qt.WindowState.WindowMaximized
        if bool(self.windowState() & fullscreen_states):
            return True

        window = self.windowHandle()
        if window is not None:
            try:
                if window.visibility() in {
                    QWindow.Visibility.FullScreen,
                    QWindow.Visibility.Maximized,
                }:
                    return True
            except RuntimeError:
                return True

        screen = self.screen()
        if screen is None:
            return False

        frame = self.frameGeometry()
        if frame.isEmpty():
            return False

        def _fills(rect: QRect) -> bool:
            if rect.isEmpty():
                return False
            tolerance = 3
            return (
                abs(frame.x() - rect.x()) <= tolerance
                and abs(frame.y() - rect.y()) <= tolerance
                and abs(frame.width() - rect.width()) <= tolerance
                and abs(frame.height() - rect.height()) <= tolerance
            )

        return _fills(screen.geometry()) or _fills(screen.availableGeometry())

    def _is_native_fullscreen_active(self) -> bool:
        """Return True only for AppKit native fullscreen.

        与 ``_skip_page_motion_for_window_state`` 的区别：
        本函数**不**把 ``WindowMaximized`` / 屏幕几何填充 视为 fullscreen。
        那些是"动画不安全"状态，但**不算**原生 fullscreen。
        原生 fullscreen 掉到 maximized 是常见现象，post-switch fallback 需用
        本函数判断是否需要恢复（``showFullScreen``）。
        """
        if sys.platform != "darwin":
            return False
        if self.isFullScreen():
            return True
        window = self.windowHandle()
        if window is not None:
            try:
                return window.visibility() == QWindow.Visibility.FullScreen
            except RuntimeError:
                return False
        return False

    def _enter_mac_fullscreen_safe_mode(self) -> bool:
        """switch_page 入口钩子：在 ``setCurrentWidget`` 之前调用。"""
        if sys.platform != "darwin":
            return False
        if not self._is_native_fullscreen_active():
            return False
        import time as _time

        self._mac_fullscreen_safe_mode_active = True
        self._mac_fullscreen_at_switch_start_time = _time.monotonic()

        from PySide6.QtWidgets import QGraphicsOpacityEffect

        targets: list = []
        if self._previous_widget is not None:
            targets.append(self._previous_widget)
        new_widget = self._stack.currentWidget()
        if new_widget is not None:
            targets.append(new_widget)
        for label in (self._top_eyebrow, self._top_title, self._top_subtitle):
            if label is not None:
                targets.append(label)
        for w in targets:
            try:
                effect = w.graphicsEffect()
                if isinstance(effect, QGraphicsOpacityEffect):
                    w.setGraphicsEffect(None)
            except RuntimeError:
                pass
        return True

    def _exit_mac_fullscreen_safe_mode(self, page_id: str, generation: int) -> None:
        """post-switch fallback：120 ms 后检查是否被踢出原生 fullscreen。"""
        if not getattr(self, "_mac_fullscreen_safe_mode_active", False):
            return
        import time as _time

        if (_time.monotonic() - getattr(self, "_mac_fullscreen_at_switch_start_time", 0.0)) > 5.0:
            self._mac_fullscreen_safe_mode_active = False
            return
        if not self._post_switch_current(page_id, generation):
            return
        if self._is_native_fullscreen_active():
            self._mac_fullscreen_safe_mode_active = False
            return
        self._mac_fullscreen_safe_mode_active = False
        try:
            if self.isVisible():
                self.showFullScreen()
        except RuntimeError:
            pass

    def _page_transition_profile(self, page_id: str | None) -> str:
        if not page_id:
            return "standard"
        meta = page_registry.metadata(page_id)
        if meta is None:
            return "standard"
        return str(meta.extra.get("transition_profile", "standard") or "standard")

    @staticmethod
    def _widget_tree_has_graphics_effect(widget: QWidget | None) -> bool:
        """Return whether a widget tree owns a QGraphicsEffect.

        The declarative ``_declares_graphics_effect`` attribute provides a
        fast path for known effectful page roots, but dynamically installed
        descendant effects still require a recursive safety check.  Applying a
        page-level opacity effect over any effectful subtree can make Qt
        re-enter the same paint device during effect-source pixmap generation.

        This uses an early-exit DFS over QObject children rather than
        ``findChildren(QWidget)`` so it avoids building a full widget list.
        """
        if widget is None:
            return False
        stack: list[QWidget] = [widget]
        while stack:
            current = stack.pop()
            if getattr(current, "_declares_graphics_effect", False):
                return True
            try:
                if current.graphicsEffect() is not None:
                    return True
                children = current.children()
            except RuntimeError:
                return True
            for child in children:
                if isinstance(child, QWidget):
                    stack.append(child)
        return False

    def _skip_page_motion_for_graphics_effect_tree(
        self,
        old_widget: QWidget | None,
        new_widget: QWidget | None,
    ) -> bool:
        """Avoid page-level opacity effects over effectful page subtrees."""
        tree_has_effect = type(self)._widget_tree_has_graphics_effect
        return tree_has_effect(old_widget) or tree_has_effect(new_widget)

    def _animate_current_page(self) -> None:
        with ui_perf_span("animate_current_page", page_id=self._active_page_id):
            if not _animations_supported(kind="opacity"):
                self._force_instant_page_transition_once = False
                return

            if getattr(self, "_mac_fullscreen_safe_mode_active", False):
                self._clear_page_animation()
                return

            old_widget = self._previous_widget
            new_widget = self._stack.currentWidget()
            self._previous_widget = None

            if new_widget is None:
                self._force_instant_page_transition_once = False
                return

            if (
                self._force_instant_page_transition_once
                or self._page_transition_profile(self._active_page_id) == "instant"
                or self._page_transition_profile(self._previous_page_id) == "instant"
            ):
                self._force_instant_page_transition_once = False
                self._clear_page_animation()
                for widget in (old_widget, new_widget):
                    if widget is None:
                        continue
                    try:
                        widget.setGraphicsEffect(cast(QGraphicsEffect, None))
                    except RuntimeError:
                        pass
                self._previous_page_id = None
                return

            self._force_instant_page_transition_once = False

            if self._skip_page_motion_for_window_state():
                self._clear_page_animation()
                for widget in (old_widget, new_widget):
                    if widget is None:
                        continue
                    try:
                        widget.setGraphicsEffect(cast(QGraphicsEffect, None))
                    except RuntimeError:
                        pass
                self._previous_page_id = None
                return

            self._clear_page_animation()

            if self._skip_page_motion_for_graphics_effect_tree(old_widget, new_widget):
                self._previous_page_id = None
                return

            if old_widget is not None and old_widget is not new_widget:
                # ``QStackedWidget.setCurrentWidget`` has already hidden the
                # previous page.  Fading that hidden widget creates an offscreen
                # QGraphicsOpacityEffect cache with no visual benefit.  On macOS,
                # a busy event loop can leave that cache composited above the new
                # page, exposing a translucent ghost of e.g. the chapter rail in
                # Voice Studio.  Clear a possible stale transition effect and
                # animate the incoming page only.
                try:
                    old_widget.setGraphicsEffect(cast(QGraphicsEffect, None))
                except RuntimeError:
                    pass

            try:
                fade_in_anim = Motion.fade_in(
                    new_widget,
                    duration=300,
                    easing=QEasingCurve.Type.OutCubic,
                )
                if fade_in_anim.parent() is None:
                    fade_in_anim.setParent(self)
                self._page_animation = fade_in_anim
                self._page_animation_widget = new_widget

                def cleanup() -> None:
                    if self._page_animation_widget is new_widget:
                        self._clear_page_animation()

                fade_in_anim.finished.connect(cleanup)
            except (RuntimeError, ValueError):
                return

            if (
                _animations_supported(kind="opacity")
                and old_widget is not None
                and old_widget is not new_widget
            ):
                self._apply_page_slide(new_widget)
            self._previous_page_id = None

    def _apply_page_slide(self, widget: QWidget) -> None:
        """Apply slide-in using platform-safe animation (QTimer on macOS)."""
        try:
            original_pos = widget.pos()
            width = widget.width()
            x_offset = max(60, width // 4) if width > 0 else 60
            start_pos = QPoint(original_pos.x() + x_offset, original_pos.y())
            slide_anim = Motion.slide_in_safe(
                widget,
                start_pos=start_pos,
                end_pos=original_pos,
                duration=300,
                easing=QEasingCurve.Type.OutCubic,
            )
            if isinstance(slide_anim, QPropertyAnimation) and slide_anim.parent() is None:
                slide_anim.setParent(self)
        except (RuntimeError, ValueError):
            pass

    def _clear_page_animation(self) -> None:
        for anim in getattr(self, "_page_transition_animations", []) or []:
            Motion.stop_safely(anim)
        self._page_transition_animations = []
        for widget in getattr(self, "_page_transition_widgets", []) or []:
            try:
                widget.setGraphicsEffect(cast(QGraphicsEffect, None))
            except RuntimeError:
                pass
        self._page_transition_widgets = []

        if self._page_animation is not None:
            try:
                self._page_animation.stop()
            except RuntimeError:
                pass
            self._page_animation = None
        widget = self._page_animation_widget
        self._page_animation_widget = None
        if widget is not None:
            try:
                widget.setGraphicsEffect(cast(QGraphicsEffect, None))
            except RuntimeError:
                pass

    def _animate_top_bar_title(self) -> None:
        """Re-trigger a 200ms OutCubic fade-in on the top-bar title labels.

        Called on every ``switch_page()`` so the eyebrow / title / subtitle
        labels re-fade whenever the user navigates between pages.  Uses
        ``Motion.fade_in`` which is opacity-only and therefore safe on macOS
        (D1 platform guard).  Honours the existing reduced-motion and global
        disable overrides via ``Motion._effective_duration``.

        Stops any prior fade-in (Motion deletes stopped animations under
        ``DeletionPolicy.DeleteWhenStopped``) and caches the new anims on
        the window as ``_top_bar_animations`` so they survive the Qt
        property-store GC race and remain introspectable for tests.
        """
        if not _animations_supported("opacity"):
            return

        if getattr(self, "_mac_fullscreen_safe_mode_active", False):
            for label in (self._top_eyebrow, self._top_title, self._top_subtitle):
                if label is None:
                    continue
                try:
                    label.setGraphicsEffect(None)
                except RuntimeError:
                    pass
            self._top_bar_animations = []
            return

        for prior in getattr(self, "_top_bar_animations", ()) or ():
            try:
                prior.stop()
            except RuntimeError:
                pass

        if self._skip_page_motion_for_window_state():
            for label in (self._top_eyebrow, self._top_title, self._top_subtitle):
                if label is None:
                    continue
                try:
                    label.setGraphicsEffect(cast(QGraphicsEffect, None))
                except RuntimeError:
                    pass
            self._top_bar_animations = []
            return

        anims: list[QAbstractAnimation] = []
        for label in (self._top_eyebrow, self._top_title, self._top_subtitle):
            if label is None:
                continue
            try:
                anims.append(
                    Motion.fade_in(
                        label,
                        duration=200,
                        easing=QEasingCurve.Type.OutCubic,
                    )
                )
            except (RuntimeError, ValueError):
                continue
        self._top_bar_animations = anims

    def _stop_top_bar_animations(self) -> None:
        """Stop transitional title effects during a rapid navigation burst."""
        for animation in getattr(self, "_top_bar_animations", ()) or ():
            Motion.stop_safely(animation)
        self._top_bar_animations = []
        for label in (self._top_eyebrow, self._top_title, self._top_subtitle):
            if label is None:
                continue
            try:
                label.setGraphicsEffect(None)
            except RuntimeError:
                continue
