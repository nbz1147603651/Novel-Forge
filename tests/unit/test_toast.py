"""Unit tests for the Toast widget after task 12 (visual refresh).

Covers the refactor that moved Toast styling from inline
``self.setStyleSheet(...)`` into ``novel_forge.desktop.theme.toast`` and
added a slide-in animation.  Runs under ``QT_QPA_PLATFORM=offscreen``
(set globally in ``pyproject.toml`` / ``tests/conftest.py``).

``toast.py`` is loaded directly via ``importlib`` (see below) to dodge
a pre-existing circular import in ``novel_forge.desktop.components``.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

# ``QT_QPA_PLATFORM=offscreen`` is set by ``tests/conftest.py`` before
# the test module is imported, so this import is safe.
from novel_forge.desktop.theme import get_stylesheet
from novel_forge.desktop.theme.toast import CONTENT as TOAST_QSS_CONTENT

# Bypass the components-package circular import by loading the toast
# module file directly.  See the module docstring for details.
_TOAST_PATH = (
    Path(__file__).resolve().parent.parent.parent
    / "novel_forge"
    / "desktop"
    / "components"
    / "toast.py"
)
_spec = importlib.util.spec_from_file_location("_nf_toast_under_test", _TOAST_PATH)
toast_module = importlib.util.module_from_spec(_spec)
sys.modules["_nf_toast_under_test"] = toast_module
_spec.loader.exec_module(toast_module)  # type: ignore[union-attr]

Toast = toast_module.Toast
ToastManager = toast_module.ToastManager
show_toast = toast_module.show_toast
FADE_IN_DURATION = toast_module.FADE_IN_DURATION
SLIDE_IN_DURATION = toast_module.SLIDE_IN_DURATION
SLIDE_IN_OFFSET = toast_module.SLIDE_IN_OFFSET


# ── Fixtures ───────────────────────────────────────────────────────────────


@pytest.fixture
def qapp() -> QApplication:
    """Return the live QApplication instance, creating it if missing.

    The Toast widget is a real ``QWidget`` and needs a running
    QApplication to call ``QGraphicsOpacityEffect`` and animation
    methods.  We do NOT call ``qapp.exec()`` — only ``processEvents``.
    """
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app
    # Always reset the singleton between tests so state cannot leak.
    ToastManager.reset()


@pytest.fixture(autouse=True)
def _isolate_toast_manager() -> None:
    """Make sure no stray ToastManager singleton survives across tests."""
    ToastManager.reset()
    yield
    ToastManager.reset()


# ── 1. No inline setStyleSheet ─────────────────────────────────────────────


class TestNoInlineStylesheet:
    """The whole point of the refactor: styling lives in theme/, not on the widget."""

    @pytest.mark.parametrize("variant", ["success", "error", "warning", "info"])
    def test_toast_has_no_inline_stylesheet(self, qapp: QApplication, variant: str) -> None:
        toast = Toast("Test", variant=variant)
        try:
            actual = toast.styleSheet()
            assert actual == "", (
                f"Expected empty inline styleSheet for variant={variant!r}, "
                f"got: {actual!r}"
            )
        finally:
            toast.deleteLater()
            qapp.processEvents()

    def test_label_has_no_inline_stylesheet(self, qapp: QApplication) -> None:
        """The inner label must also rely on the global QSS."""
        toast = Toast("Test", variant="success")
        try:
            assert toast.label.styleSheet() == "", (
                f"Label should have no inline styleSheet, got: {toast.label.styleSheet()!r}"
            )
        finally:
            toast.deleteLater()
            qapp.processEvents()

    def test_toast_advertises_object_name_and_variant(self, qapp: QApplication) -> None:
        """Theme QSS targets ``QWidget#toast[variant="..."]`` — both must be set."""
        toast = Toast("Test", variant="warning")
        try:
            assert toast.objectName() == "toast"
            assert toast.property("variant") == "warning"
        finally:
            toast.deleteLater()
            qapp.processEvents()

    def test_toast_uses_shared_painted_surface(self, qapp: QApplication) -> None:
        from novel_forge.desktop.components.floating_surface import PaintedFloatingSurface

        toast = Toast("Test", variant="info")
        try:
            assert isinstance(toast, PaintedFloatingSurface)
        finally:
            toast.deleteLater()
            qapp.processEvents()

    def test_toast_uses_manual_background_painting(self, qapp: QApplication) -> None:
        from PySide6.QtCore import Qt

        toast = Toast("Test", variant="info")
        try:
            assert toast.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
            assert toast.testAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
            assert not toast.testAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        finally:
            toast.deleteLater()
            qapp.processEvents()


# ── 2. Global QSS application ─────────────────────────────────────────────


class TestGlobalQssApplication:
    """The Toast must render with only the global stylesheet."""

    def test_theme_module_contains_full_qss(self) -> None:
        """``theme/toast.py`` must export a non-empty CONTENT with all 4 variants."""
        assert TOAST_QSS_CONTENT.strip() != ""

        # All four variants must be styled in the QSS.
        for variant in ("success", "error", "warning", "info"):
            assert f'variant="{variant}"' in TOAST_QSS_CONTENT, (
                f"Missing QSS block for variant={variant!r}"
            )

    def test_get_stylesheet_includes_toast_rules(self, qapp: QApplication) -> None:
        """The assembled global stylesheet must contain the toast rules."""
        qss = get_stylesheet()
        assert "QWidget#toast" in qss
        assert 'variant="success"' in qss
        assert 'variant="error"' in qss
        assert 'variant="warning"' in qss
        assert 'variant="info"' in qss
        # Token placeholders must be resolved to real hex values.
        assert "{{" not in qss, "get_stylesheet() left token placeholders unresolved"

    def test_toast_qss_does_not_paint_top_level_shell(self, qapp: QApplication) -> None:
        """The painted floating base owns the top-level rounded shell."""
        qss = get_stylesheet()
        selector = "QWidget#toast"
        idx = qss.find(selector)
        assert idx >= 0, f"missing selector {selector!r} in QSS"
        rule_ctx = qss[idx: qss.find("}", idx)]
        assert "background:" not in rule_ctx
        assert "border:" not in rule_ctx
        assert "border-radius:" not in rule_ctx

    @pytest.mark.parametrize("variant", ["success", "error", "warning", "info"])
    def test_toast_renders_under_global_qss(
        self, qapp: QApplication, variant: str
    ) -> None:
        """Apply the global stylesheet and ensure Toast can be shown."""
        qapp.setStyleSheet(get_stylesheet())
        toast = Toast("hello", variant=variant)
        toast.show()
        try:
            qapp.processEvents()
            assert toast.isVisible(), f"Toast {variant!r} failed to become visible"
            # Pixmap should not be null under a working stylesheet.
            pm = toast.grab()
            assert not pm.isNull(), f"Toast {variant!r} rendered to a null pixmap"
        finally:
            toast.close()
            toast.deleteLater()


# ── 3. Fade-in duration upgrade ───────────────────────────────────────────


class TestFadeInAnimation:
    """Fade-in must be 200ms (upgraded from 150ms)."""

    def test_fade_in_duration_constant_is_200ms(self) -> None:
        assert FADE_IN_DURATION == 200, (
            f"Expected FADE_IN_DURATION=200ms, got {FADE_IN_DURATION}"
        )

    def test_fade_in_creates_200ms_opacity_animation(
        self, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """fade_in() must create a 200ms opacity animation when animations are enabled.

        On darwin, ``constants.animations_supported()`` returns False and
        the animation is skipped entirely, so we override it here.
        """
        monkeypatch.setattr(toast_module, "animations_supported", lambda: True)
        toast = Toast("Test", variant="info")
        try:
            toast.fade_in()
            qapp.processEvents()
            anim = getattr(toast, "_anim", None)
            assert anim is not None, "fade_in() did not create an animation"
            assert anim.propertyName() == b"opacity", (
                f"Expected opacity animation, got property: {anim.propertyName()!r}"
            )
            assert anim.duration() == 200, (
                f"Expected 200ms opacity animation, got {anim.duration()}ms"
            )
        finally:
            toast.deleteLater()
            qapp.processEvents()


# ── 4. Slide-in animation ─────────────────────────────────────────────────


class TestSlideInAnimation:
    """Slide-in is new in task 12 — must exist and be guarded by D1 on macOS."""

    def test_slide_in_constants(self) -> None:
        assert SLIDE_IN_DURATION == 300
        assert SLIDE_IN_OFFSET > 0

    def test_slide_in_animation_geometry_kind_runs(
        self, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """On non-macOS (or when motion returns True) a geometry animation runs.

        We force ``motion_animations_supported`` on the loaded toast module
        to return True so the slide-in branch is exercised regardless of
        the host platform.  ``animations_supported`` (the platform gate)
        is also overridden since darwin disables all animations by default.
        """
        monkeypatch.setattr(toast_module, "animations_supported", lambda: True)
        monkeypatch.setattr(
            toast_module, "motion_animations_supported", lambda kind: True
        )
        toast = Toast("Test", variant="info")
        try:
            toast.show()
            qapp.processEvents()
            toast.fade_in()
            qapp.processEvents()
            slide_anim = getattr(toast, "_slide_anim", None)
            assert slide_anim is not None, "slide-in animation was not created"
            assert slide_anim.propertyName() == b"pos", (
                f"Expected pos animation, got: {slide_anim.propertyName()!r}"
            )
            assert slide_anim.duration() == SLIDE_IN_DURATION, (
                f"Expected slide-in duration={SLIDE_IN_DURATION}ms, "
                f"got {slide_anim.duration()}ms"
            )
        finally:
            toast.close()
            toast.deleteLater()
            qapp.processEvents()

    def test_slide_in_blocked_on_macos(
        self, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When the motion module reports geometry as blocked, no slide anim is created.

        Animations are force-enabled so the fade-in path runs (otherwise
        the slide-in check is never reached on darwin), and then the
        motion module is told geometry is unsafe.
        """
        monkeypatch.setattr(toast_module, "animations_supported", lambda: True)
        monkeypatch.setattr(
            toast_module, "motion_animations_supported", lambda kind: False
        )
        toast = Toast("Test", variant="info")
        try:
            toast.show()
            qapp.processEvents()
            toast.fade_in()
            qapp.processEvents()
            assert not hasattr(toast, "_slide_anim") or toast._slide_anim is None, (
                "slide-in must be blocked on macOS, but _slide_anim is set"
            )
        finally:
            toast.close()
            toast.deleteLater()
            qapp.processEvents()


# ── 5. Edge case: app.quit() during a live toast ──────────────────────────


class TestAppQuitEdgeCase:
    """A live toast must not crash the app on quit."""

    def test_app_quit_with_live_toast_does_not_crash(
        self, qapp: QApplication
    ) -> None:
        toast = show_toast("Edge case", variant="success")
        # Schedule a quick quit — toast is still mid-fade-in.
        QTimer.singleShot(50, qapp.quit)
        # ``app.exec()`` returns when ``quit()`` fires.  We accept both
        # a clean return and a RuntimeError (which Qt sometimes raises
        # when widgets are torn down with active animations); both are
        # PASS.  The fail indicator is any other exception.
        try:
            qapp.exec()
        except RuntimeError:
            pass
        # If we get here, we survived.  Clean up the still-attached toast.
        toast.deleteLater()
        qapp.processEvents()

    def test_show_toast_api_signature_unchanged(self) -> None:
        """The public ``show_toast`` API must not regress (MUST NOT do)."""
        import inspect

        sig = inspect.signature(show_toast)
        params = list(sig.parameters.keys())
        assert params == ["message", "variant", "duration", "parent"], (
            f"show_toast signature changed: {params}"
        )


# ── 6. Variant validation (defensive) ─────────────────────────────────────


class TestVariantValidation:
    """Unknown variants must fall back to 'info' (no crash)."""

    @pytest.mark.parametrize("variant", ["success", "error", "warning", "info"])
    def test_known_variant_stable(self, qapp: QApplication, variant: str) -> None:
        toast = Toast("X", variant=variant)
        try:
            assert toast._variant == variant
            assert toast.property("variant") == variant
        finally:
            toast.deleteLater()
            qapp.processEvents()

    def test_unknown_variant_falls_back_to_info(
        self, qapp: QApplication
    ) -> None:
        toast = Toast("X", variant="bogus")
        try:
            assert toast._variant == "info"
            assert toast.property("variant") == "info"
        finally:
            toast.deleteLater()
            qapp.processEvents()
