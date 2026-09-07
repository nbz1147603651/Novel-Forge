"""Unit tests for the status-bar visual refresh (task 17).

Covers the refactor that:
- Replaces the legacy ``rgba(255, 250, 243, 0.74)`` QStatusBar background
  with the ``{{bg.surface}}`` design token.
- Adds ``padding: 0 {{space-2}}`` for the ``#statusStepLabel`` and
  ``#statusCostLabel`` permanent widgets.
- Animates the cost label with a 150ms Motion fade-in on every
  ``token_update`` signal (gated by ``motion_animations_supported("opacity")``).
- Animates step-name changes through a fade-out → setText → fade-in
  cycle (100ms each) with a short-circuit guard for no-op text changes.

Runs under ``QT_QPA_PLATFORM=offscreen`` (set globally in
``tests/conftest.py``).

Construction strategy:
    Instantiating the real ``NovelForgeDesktopWindow`` pulls in
    ~50 page modules and triggers unrelated pre-existing bugs (e.g.
    ``primitives.py`` calls ``QGraphicsDropShadowEffect.setOptimizationFlags``
    which doesn't exist on the current PySide6).  We instead build a
    lightweight stub with the three attributes the methods touch
    (``_status_step_label``, ``_status_cost_label``, ``_last_step_text``)
    and bind the real methods to the stub.  This gives us full coverage
    of the production code path without the heavy import graph.
"""

from __future__ import annotations

import types
from typing import Any

from PySide6.QtCore import QAbstractAnimation, QPropertyAnimation
from PySide6.QtWidgets import QApplication, QGraphicsOpacityEffect, QLabel

from novel_forge.desktop import constants
from novel_forge.desktop.motion import Motion
from novel_forge.desktop.theme import get_stylesheet
from novel_forge.desktop.theme._globals import CONTENT as GLOBALS_QSS_CONTENT

# ── 1. QSS tokens ──────────────────────────────────────────────────────────


class TestStatusBarQss:
    """The global QSS must drive the status bar from design tokens."""

    def test_qstatusbar_rule_present_in_globals(self) -> None:
        """``theme/_globals.py`` must declare a ``QStatusBar { ... }`` block."""
        assert "QStatusBar" in GLOBALS_QSS_CONTENT
        assert "QStatusBar {" in GLOBALS_QSS_CONTENT

    def test_qstatusbar_uses_bg_surface_token(self) -> None:
        """The QStatusBar background must reference ``{{bg.surface}}``."""
        start = GLOBALS_QSS_CONTENT.index("QStatusBar {")
        depth = 0
        end = start
        for idx in range(start, len(GLOBALS_QSS_CONTENT)):
            ch = GLOBALS_QSS_CONTENT[idx]
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    end = idx
                    break
        block = GLOBALS_QSS_CONTENT[start : end + 1]
        assert "{{bg.surface}}" in block, (
            f"QStatusBar block must use {{bg.surface}} token, got: {block!r}"
        )

    def test_qstatusbar_label_padding_rule(self) -> None:
        """``#statusStepLabel`` and ``#statusCostLabel`` must have padding via ``{{space-2}}``."""
        assert "#statusStepLabel" in GLOBALS_QSS_CONTENT
        assert "#statusCostLabel" in GLOBALS_QSS_CONTENT
        assert "padding: 0 {{space-2}}px" in GLOBALS_QSS_CONTENT

    def test_get_stylesheet_resolves_statusbar_tokens(self, qapp: QApplication) -> None:
        """``get_stylesheet()`` resolves ``{{bg.surface}}`` → ``#fffaf3`` for QStatusBar."""
        qss = get_stylesheet()
        assert "#fffaf3" in qss
        assert "QStatusBar" in qss
        assert "padding: 0 8px" in qss, (
            f"space-2 token should resolve to 8px, got: {qss!r}"
        )
        assert "{{" not in qss, "get_stylesheet() left token placeholders unresolved"


# ── 2. Lightweight window stub ─────────────────────────────────────────────


def _make_stub_window(qapp: QApplication) -> Any:
    """Build a minimal stub with the 3 attributes the status-bar methods touch.

    The real ``NovelForgeDesktopWindow`` pulls in the whole page graph and
    triggers unrelated bugs.  We replicate only the contract the methods
    need (``_status_step_label``, ``_status_cost_label``, ``_last_step_text``)
    and bind the *real* methods from ``novel_forge.desktop.window`` onto
    the stub.  This exercises the production code path exactly.
    """
    from novel_forge.desktop import window as window_module

    step_label = QLabel("")
    step_label.setObjectName("statusStepLabel")
    cost_label = QLabel("")
    cost_label.setObjectName("statusCostLabel")

    stub = types.SimpleNamespace(
        _status_step_label=step_label,
        _status_cost_label=cost_label,
        _last_step_text="",
        _cost_fade_anim=None,
        _step_fade_out_anim=None,
        _step_fade_in_anim=None,
        _step_fade_generation=0,
    )
    stub._update_cost_label = types.MethodType(  # type: ignore[attr-defined]
        window_module.NovelForgeDesktopWindow._update_cost_label, stub
    )
    stub._update_step_name = types.MethodType(  # type: ignore[attr-defined]
        window_module.NovelForgeDesktopWindow._update_step_name, stub
    )
    stub._stop_step_fade = types.MethodType(  # type: ignore[attr-defined]
        window_module.NovelForgeDesktopWindow._stop_step_fade, stub
    )
    return stub


def _force_animations(on: bool) -> Any:
    """Force ``motion_animations_supported`` in ``window`` module; return original."""
    from novel_forge.desktop import window as window_module

    original = window_module.motion_animations_supported
    window_module.motion_animations_supported = (  # type: ignore[assignment]
        lambda kind="any": on
    )
    return original


def _restore_animations(original: Any) -> None:
    from novel_forge.desktop import window as window_module

    window_module.motion_animations_supported = original  # type: ignore[assignment]


# ── 3. Cost label fade animation ───────────────────────────────────────────


class TestCostLabelFade:
    """``_update_cost_label`` must trigger a 150ms Motion fade-in."""

    def test_cost_label_setText_runs_first(self, qapp: QApplication) -> None:
        """The new text is always set before any animation starts."""
        original = _force_animations(True)
        try:
            window = _make_stub_window(qapp)
            window._update_cost_label("proj-1", 5000, 0.123)
            assert "5.0k tokens" in window._status_cost_label.text()
            assert "$0.123" in window._status_cost_label.text()
        finally:
            _restore_animations(original)

    def test_cost_label_fade_animation_is_150ms(self, qapp: QApplication) -> None:
        """A QPropertyAnimation with duration=150ms is created on update."""
        original = _force_animations(True)
        try:
            window = _make_stub_window(qapp)
            window._update_cost_label("proj-1", 5000, 0.123)
            anim = window._cost_fade_anim
            assert anim is not None, "Expected _cost_fade_anim to be cached"
            assert isinstance(anim, QPropertyAnimation)
            assert anim.duration() == 150, (
                f"Cost label fade should be 150ms, got {anim.duration()}"
            )
        finally:
            _restore_animations(original)

    def test_cost_label_fade_animates_opacity(self, qapp: QApplication) -> None:
        """The cached anim drives a QGraphicsOpacityEffect (opacity-safe on macOS)."""
        original = _force_animations(True)
        try:
            window = _make_stub_window(qapp)
            window._update_cost_label("proj-1", 5000, 0.123)
            anim = window._cost_fade_anim
            assert anim is not None
            target = anim.targetObject()
            assert isinstance(target, QGraphicsOpacityEffect)
            assert anim.propertyName() == b"opacity"
        finally:
            _restore_animations(original)

    def test_cost_label_fade_ref_clears_after_finish(self, qapp: QApplication) -> None:
        """The status-bar cost fade must not keep stale animation wrappers."""
        original = _force_animations(True)
        try:
            window = _make_stub_window(qapp)
            window._update_cost_label("proj-1", 5000, 0.123)
            anim = window._cost_fade_anim
            assert anim is not None
            anim.finished.emit()
            assert window._cost_fade_anim is None
        finally:
            _restore_animations(original)

    def test_cost_label_zero_tokens_skips_animation(self, qapp: QApplication) -> None:
        """``total_tokens <= 0`` clears the label and does not start an animation."""
        original = _force_animations(True)
        try:
            window = _make_stub_window(qapp)
            window._update_cost_label("proj-1", 0, 0.0)
            assert window._status_cost_label.text() == ""
            assert window._cost_fade_anim is None
        finally:
            _restore_animations(original)

    def test_cost_label_fade_skipped_when_animations_disabled(
        self, qapp: QApplication
    ) -> None:
        """When ``motion_animations_supported`` returns False, no anim is created."""
        original = _force_animations(False)
        try:
            window = _make_stub_window(qapp)
            window._update_cost_label("proj-1", 5000, 0.123)
            assert "5.0k tokens" in window._status_cost_label.text()
            assert window._cost_fade_anim is None
        finally:
            _restore_animations(original)


# ── 4. Step name fade transitions ──────────────────────────────────────────


class TestStepNameFade:
    """``_update_step_name`` must run a fade-out → setText → fade-in cycle."""

    def test_step_name_same_text_is_noop(self, qapp: QApplication) -> None:
        """Calling with the same text twice must not start any animation."""
        original = _force_animations(True)
        try:
            window = _make_stub_window(qapp)
            window._update_step_name("生成中 · 50%")
            anim_first = window._step_fade_out_anim
            window._update_step_name("生成中 · 50%")
            assert window._step_fade_out_anim is anim_first, (
                "No-op update must not restart the fade-out animation"
            )
        finally:
            _restore_animations(original)

    def test_step_name_change_starts_fade_out(self, qapp: QApplication) -> None:
        """A text change starts a 100ms fade-out animation."""
        original = _force_animations(True)
        try:
            window = _make_stub_window(qapp)
            window._update_step_name("准备中 · 0%")
            window._update_step_name("生成中 · 10%")
            anim_out = window._step_fade_out_anim
            assert anim_out is not None
            assert isinstance(anim_out, QPropertyAnimation)
            assert anim_out.duration() == 100, (
                f"Step fade-out should be 100ms, got {anim_out.duration()}"
            )
            assert isinstance(anim_out.targetObject(), QGraphicsOpacityEffect)
            assert anim_out.propertyName() == b"opacity"
        finally:
            _restore_animations(original)

    def test_step_name_change_skipped_when_animations_disabled(
        self, qapp: QApplication
    ) -> None:
        """When animations are off, the new text is applied immediately."""
        original = _force_animations(False)
        try:
            window = _make_stub_window(qapp)
            window._update_step_name("生成中 · 10%")
            assert window._status_step_label.text() == "生成中 · 10%"
            assert window._step_fade_out_anim is None
            assert window._step_fade_in_anim is None
        finally:
            _restore_animations(original)

    def test_stop_step_fade_ignores_deleted_qt_animation_refs(
        self, qapp: QApplication
    ) -> None:
        """A stale PySide wrapper must not crash the status-bar refresh."""

        class DeletedQtAnimation:
            def stop(self) -> None:
                raise RuntimeError(
                    "libshiboken: Internal C++ object "
                    "(PySide6.QtCore.QPropertyAnimation) already deleted."
                )

        window = _make_stub_window(qapp)
        window._step_fade_out_anim = DeletedQtAnimation()
        window._step_fade_in_anim = DeletedQtAnimation()

        window._stop_step_fade()

        assert window._step_fade_out_anim is None
        assert window._step_fade_in_anim is None

    def test_step_name_fade_in_starts_after_fade_out_finishes(
        self, qapp: QApplication
    ) -> None:
        """The fade-in anim is wired to the fade-out's ``finished`` signal."""
        original = _force_animations(True)
        try:
            window = _make_stub_window(qapp)
            window._update_step_name("准备中 · 0%")
            window._update_step_name("生成中 · 20%")
            anim_out = window._step_fade_out_anim
            assert anim_out is not None
            # Simulate the fade-out finishing.
            anim_out.finished.emit()
            anim_in = window._step_fade_in_anim
            assert anim_in is not None
            assert isinstance(anim_in, QPropertyAnimation)
            assert anim_in.duration() == 100
            assert window._status_step_label.text() == "生成中 · 20%"
        finally:
            _restore_animations(original)

    def test_step_name_fade_refs_clear_after_finish(self, qapp: QApplication) -> None:
        """Finished status-bar animations clear window-held references."""
        original = _force_animations(True)
        try:
            window = _make_stub_window(qapp)
            window._update_step_name("准备中 · 0%")
            window._update_step_name("生成中 · 30%")
            anim_out = window._step_fade_out_anim
            assert anim_out is not None

            anim_out.finished.emit()
            assert window._step_fade_out_anim is None
            anim_in = window._step_fade_in_anim
            assert anim_in is not None

            anim_in.finished.emit()
            assert window._step_fade_in_anim is None
        finally:
            _restore_animations(original)

    def test_stale_fade_out_finish_does_not_overwrite_newer_step(
        self, qapp: QApplication
    ) -> None:
        """A late ``finished`` signal from an old fade-out must be ignored."""
        original = _force_animations(True)
        try:
            window = _make_stub_window(qapp)
            window._update_step_name("准备中 · 0%")
            window._update_step_name("生成中 · 10%")
            old_anim = window._step_fade_out_anim
            assert old_anim is not None

            window._update_step_name("生成中 · 20%")
            new_anim = window._step_fade_out_anim
            assert new_anim is not None
            assert new_anim is not old_anim

            old_anim.finished.emit()
            assert window._status_step_label.text() != "生成中 · 10%"
            assert window._step_fade_in_anim is None

            new_anim.finished.emit()
            assert window._status_step_label.text() == "生成中 · 20%"
        finally:
            _restore_animations(original)


# ── 5. Edge: cost label token_update storm has no character loss ───────────


class TestCostStormEdge:
    """100 ``token_update`` calls in rapid succession must preserve the final text."""

    def test_cost_storm_preserves_final_text(self, qapp: QApplication) -> None:
        """After 100 cost updates, the label text equals the last formatted string."""
        original = _force_animations(True)
        original_enabled = constants.ANIMATIONS_ENABLED
        constants.ANIMATIONS_ENABLED = True
        try:
            window = _make_stub_window(qapp)
            last_label_text = ""
            for i in range(100):
                tokens = (i + 1) * 1234
                cost = round((i + 1) * 0.001, 4)
                window._update_cost_label("proj-storm", tokens, cost)
                last_label_text = f"{tokens / 1000:.1f}k tokens  ·  ${cost:.3f}"
            # Final text must be the last formatted value, character for
            # character — animations must never have caused the setText
            # to be lost or overwritten with stale data.
            assert window._status_cost_label.text() == last_label_text
            assert window._status_cost_label.text() == "123.4k tokens  ·  $0.100"
        finally:
            _restore_animations(original)
            constants.ANIMATIONS_ENABLED = original_enabled

    def test_cost_storm_does_not_crash(self, qapp: QApplication) -> None:
        """The 100-call storm must not raise or leave the stub in a bad state."""
        original = _force_animations(True)
        try:
            window = _make_stub_window(qapp)
            for i in range(100):
                window._update_cost_label("proj-storm", i * 100, i * 0.01)
            assert window._cost_fade_anim is not None
            window._update_cost_label("proj-storm", 0, 0.0)
            assert window._status_cost_label.text() == ""
        finally:
            _restore_animations(original)

    def test_cost_storm_with_alternating_zero_tokens_does_not_lose_text(
        self, qapp: QApplication
    ) -> None:
        """Alternating zero/non-zero cost updates end with the last non-empty text."""
        original = _force_animations(True)
        try:
            window = _make_stub_window(qapp)
            for i in range(100):
                if i % 2 == 0:
                    window._update_cost_label("proj", 5000, 0.5)
                else:
                    window._update_cost_label("proj", 0, 0.0)
            # The 100th call (i=99, odd) is zero-tokens, so the label
            # is empty.
            assert window._status_cost_label.text() == ""
            # One more non-zero call should restore text correctly.
            window._update_cost_label("proj", 7777, 0.777)
            assert window._status_cost_label.text() == "7.8k tokens  ·  $0.777"
        finally:
            _restore_animations(original)


# ── 6. Motion library duration contract (sanity) ───────────────────────────


class TestMotionDurationContract:
    """The 150ms cost-fade and 100ms step-fade durations match the plan spec."""

    def test_motion_focus_duration_is_150ms(self) -> None:
        """``Motion.DURATIONS["focus"]`` (the spec's "focus" preset) is 150ms."""
        assert Motion.DURATIONS["focus"] == 150

    def test_motion_hover_duration_is_100ms(self) -> None:
        """``Motion.DURATIONS["hover"]`` matches the 100ms step-fade duration."""
        assert Motion.DURATIONS["hover"] == 100


# ── 7. QAbstractAnimation state check (cross-platform safety sanity) ────────


class TestAnimationStateTransitions:
    """Sanity: the cached anims reach a terminal state on completion."""

    def test_cost_fade_animates_and_stops_cleanly(
        self, qapp: QApplication
    ) -> None:
        """A single cost fade reaches a non-running state after stop()."""
        original = _force_animations(True)
        try:
            window = _make_stub_window(qapp)
            window._update_cost_label("proj", 5000, 0.5)
            anim = window._cost_fade_anim
            assert anim is not None
            anim.stop()
            assert anim.state() == QAbstractAnimation.State.Stopped
        finally:
            _restore_animations(original)
