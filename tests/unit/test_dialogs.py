"""Unit tests for the Dialog/MessageBox visual refresh (task 13).

Covers:
1. Fade-in animation (200ms OutCubic) is scheduled on every built dialog.
2. The ``QMessageBox`` QSS block is token-based and button sizes are
   consistent (min-width 80, min-height 28).
3. Public API signatures are unchanged (``show_message_box``,
   ``show_info_message``, ``ask_confirmation``, ``MessageBoxAction``).
4. Edge case: a 1000-character message text does not break the dialog
   layout (positive width/height after construction).

Runs under ``QT_QPA_PLATFORM=offscreen`` (set by ``tests/conftest.py``).
"""

from __future__ import annotations

import importlib.util
import inspect
import sys
from pathlib import Path

import pytest
from PySide6.QtCore import QPropertyAnimation
from PySide6.QtWidgets import QApplication, QGraphicsOpacityEffect

# The dialog module is loaded directly to dodge a pre-existing circular
# import in ``novel_forge.desktop.components`` (same trick used by
# ``test_toast.py``).
_DIALOGS_PATH = (
    Path(__file__).resolve().parent.parent.parent
    / "novel_forge"
    / "desktop"
    / "components"
    / "dialogs.py"
)
_spec = importlib.util.spec_from_file_location("_nf_dialogs_under_test", _DIALOGS_PATH)
dialogs_module = importlib.util.module_from_spec(_spec)
sys.modules["_nf_dialogs_under_test"] = dialogs_module
_spec.loader.exec_module(dialogs_module)  # type: ignore[union-attr]

show_message_box = dialogs_module.show_message_box
show_info_message = dialogs_module.show_info_message
show_warning_message = dialogs_module.show_warning_message
ask_confirmation = dialogs_module.ask_confirmation
MessageBoxAction = dialogs_module.MessageBoxAction
_build_message_box_dialog = dialogs_module._build_message_box_dialog
DIALOG_FADE_IN_DURATION_MS = dialogs_module.DIALOG_FADE_IN_DURATION_MS
_apply_dialog_fade_in = dialogs_module._apply_dialog_fade_in

from novel_forge.desktop.theme import get_stylesheet  # noqa: E402
from novel_forge.desktop.theme.dialogs import CONTENT as DIALOGS_QSS_CONTENT  # noqa: E402

# ── Fixtures ───────────────────────────────────────────────────────────────


@pytest.fixture
def qapp() -> QApplication:
    """Return the live QApplication instance, creating it if missing."""
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app


# ── 1. Fade-in animation (200ms OutCubic) ──────────────────────────────────


class TestFadeInAnimation:
    """The dialog must schedule a 200ms opacity fade-in via the Motion library."""

    def test_duration_constant_is_200ms(self) -> None:
        assert DIALOG_FADE_IN_DURATION_MS == 200, (
            f"Expected DIALOG_FADE_IN_DURATION_MS=200, got {DIALOG_FADE_IN_DURATION_MS}"
        )

    def test_dialog_built_with_200ms_fade_in(
        self, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The dialog built by ``_build_message_box_dialog`` has a 200ms
        opacity animation attached.  We force ``animations_supported`` to
        True so the animation is scheduled regardless of host platform
        (Darwin disables animations by default — D1).
        """
        monkeypatch.setattr(dialogs_module, "animations_supported", lambda: True)
        dialog = _build_message_box_dialog(None, "Title", "Hello")
        try:
            anim = getattr(dialog, "_fade_in_anim", None)
            assert anim is not None, (
                "Expected dialog._fade_in_anim to be set after _build_message_box_dialog"
            )
            assert isinstance(anim, QPropertyAnimation), (
                f"Expected QPropertyAnimation, got {type(anim).__name__}"
            )
            assert anim.propertyName() == b"opacity", (
                f"Expected opacity animation, got property {anim.propertyName()!r}"
            )
            assert anim.duration() == 200, (
                f"Expected 200ms fade-in duration, got {anim.duration()}ms"
            )
            # Easing: Motion.DURATIONS / EASINGS defaults use OutCubic
            from PySide6.QtCore import QEasingCurve

            assert anim.easingCurve().type() == QEasingCurve.Type.OutCubic, (
                f"Expected OutCubic easing, got {anim.easingCurve().type()}"
            )
        finally:
            dialog.deleteLater()
            qapp.processEvents()

    def test_dialog_uses_graphics_opacity_effect(
        self, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A ``QGraphicsOpacityEffect`` must be attached to the dialog.

        ``QGraphicsDropShadowEffect`` + ``QGraphicsOpacityEffect`` are
        mutually exclusive on a single widget (D10).  We use the
        QWidget.windowOpacity-safe variant, but a single effect is
        still required.
        """
        monkeypatch.setattr(dialogs_module, "animations_supported", lambda: True)
        dialog = _build_message_box_dialog(None, "Title", "Hello")
        try:
            effect = dialog.graphicsEffect()
            assert isinstance(effect, QGraphicsOpacityEffect), (
                f"Expected QGraphicsOpacityEffect, got {type(effect).__name__ if effect else 'None'}"
            )
        finally:
            dialog.deleteLater()
            qapp.processEvents()

    def test_fade_in_skipped_when_animations_disabled(
        self, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """When the platform disables animations, no fade-in is attached."""
        monkeypatch.setattr(dialogs_module, "animations_supported", lambda: False)
        dialog = _build_message_box_dialog(None, "Title", "Hello")
        try:
            anim = getattr(dialog, "_fade_in_anim", None)
            assert anim is None, (
                f"Expected no animation when animations_supported()=False, got {anim!r}"
            )
        finally:
            dialog.deleteLater()
            qapp.processEvents()

    def test_motion_library_caches_anim(
        self, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Motion's own _motion_anim property must also hold the animation (D11)."""
        monkeypatch.setattr(dialogs_module, "animations_supported", lambda: True)
        dialog = _build_message_box_dialog(None, "Title", "Hello")
        try:
            cached = dialog.property("_motion_anim")
            assert cached is not None, (
                "Expected dialog._motion_anim property to be set by Motion._apply_safety"
            )
            assert cached is dialog._fade_in_anim, (
                "dialog._motion_anim must be the same animation as dialog._fade_in_anim"
            )
        finally:
            dialog.deleteLater()
            qapp.processEvents()


# ── 2. QSS uses tokens + QMessageBox button sizes ──────────────────────────


class TestQss:
    """Theme QSS for dialogs must use design tokens and sized buttons."""

    def test_dialog_qss_module_contains_token_placeholder(self) -> None:
        """The raw QSS must reference a token (verified by ``verify_design_tokens.py``)."""
        assert "{{bg.surface}}" in DIALOGS_QSS_CONTENT, (
            "Expected '{{bg.surface}}' token in dialog QSS (resolved to bg.surface hex)"
        )

    def test_get_stylesheet_resolves_token(self, qapp: QApplication) -> None:
        """The assembled global stylesheet must resolve the bg.surface token."""
        qss = get_stylesheet()
        # The token must be resolved — no placeholder leakage.
        assert "{{bg.surface}}" not in qss, (
            "get_stylesheet() left {{bg.surface}} placeholder unresolved"
        )
        # bg.surface = #fffaf3 (per tokens/colors.py:53)
        assert "#fffaf3" in qss, (
            "Expected #fffaf3 (bg.surface) in resolved QSS"
        )

    def test_qmessagebox_button_min_width_and_min_height(
        self, qapp: QApplication
    ) -> None:
        """``QMessageBox QPushButton`` must specify min-width 80 + min-height 28."""
        qss = get_stylesheet()
        # Token-resolved, so we look for the resolved rules.
        assert "QMessageBox QPushButton" in qss
        assert "min-width: 80px" in qss, (
            "Expected min-width: 80px in QMessageBox QPushButton rule"
        )
        assert "min-height: 28px" in qss, (
            "Expected min-height: 28px in QMessageBox QPushButton rule"
        )

    def test_qmessagebox_background_uses_token(
        self, qapp: QApplication
    ) -> None:
        """The QMessageBox background must come from the bg.surface token."""
        qss = get_stylesheet()
        # Resolved QSS should show QMessageBox background as the bg.surface hex.
        import re

        m = re.search(
            r"QMessageBox\s*\{\s*background:\s*(#[0-9a-fA-F]{3,8})",
            qss,
        )
        assert m is not None, "Expected QMessageBox background rule with hex value"
        assert m.group(1).lower() == "#fffaf3", (
            f"Expected QMessageBox background = #fffaf3 (bg.surface), got {m.group(1)}"
        )


# ── 3. Public API compatibility ───────────────────────────────────────────


class TestPublicApi:
    """Public API signatures must NOT change (MUST NOT do — task 13)."""

    def test_show_message_box_signature_unchanged(self) -> None:
        sig = inspect.signature(show_message_box)
        params = list(sig.parameters.keys())
        assert params == [
            "parent",
            "title",
            "text",
            "informative_text",
            "icon",
            "actions",
            "escape_key",
        ], f"show_message_box signature changed: {params}"
        # Return type is still str (under `from __future__ import annotations`
        # the annotation is stored as a string, so we compare against the
        # string form rather than the class)
        assert sig.return_annotation == "str", (
            f"Expected return annotation 'str', got {sig.return_annotation!r}"
        )

    def test_show_info_message_signature_unchanged(self) -> None:
        sig = inspect.signature(show_info_message)
        params = list(sig.parameters.keys())
        assert params == ["parent", "title", "text", "informative_text"], (
            f"show_info_message signature changed: {params}"
        )
        assert sig.return_annotation == "None", (
            f"Expected return annotation 'None', got {sig.return_annotation!r}"
        )

    def test_ask_confirmation_signature_unchanged(self) -> None:
        sig = inspect.signature(ask_confirmation)
        params = list(sig.parameters.keys())
        assert params == [
            "parent",
            "title",
            "text",
            "informative_text",
            "confirm_text",
            "cancel_text",
            "confirm_variant",
            "escape_key",
        ], f"ask_confirmation signature changed: {params}"
        assert sig.return_annotation == "bool", (
            f"Expected return annotation 'bool', got {sig.return_annotation!r}"
        )

    def test_message_box_action_fields_unchanged(self) -> None:
        """``MessageBoxAction`` is a NamedTuple — field names/order must not change."""
        field_names = MessageBoxAction._fields
        assert field_names == ("key", "text", "role", "variant", "default"), (
            f"MessageBoxAction fields changed: {field_names}"
        )


# ── 4. Edge case: very long message text does not break layout ────────────


class TestEdgeLongText:
    """A 1000-char message must build a dialog with positive dimensions."""

    def test_long_text_builds_valid_dialog(
        self, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(dialogs_module, "animations_supported", lambda: True)
        long_text = "A" * 1000
        dialog = _build_message_box_dialog(None, "Long Title", long_text)
        try:
            qapp.processEvents()
            assert dialog.width() > 0, (
                f"Expected positive dialog width for long text, got {dialog.width()}"
            )
            assert dialog.height() > 0, (
                f"Expected positive dialog height for long text, got {dialog.height()}"
            )
            # Check that some child label has the full long text.
            from PySide6.QtWidgets import QLabel

            labels = dialog.findChildren(QLabel)
            label_texts = [lbl.text() for lbl in labels]
            assert long_text in label_texts, (
                f"Long text not preserved in any label; got sample: {label_texts[:3]}"
            )
        finally:
            dialog.deleteLater()
            qapp.processEvents()

    def test_long_informative_text_builds_valid_dialog(
        self, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Long informative text (1000 chars) is also handled."""
        monkeypatch.setattr(dialogs_module, "animations_supported", lambda: True)
        long_info = "B" * 1000
        dialog = _build_message_box_dialog(
            None, "Title", "Short body", informative_text=long_info
        )
        try:
            qapp.processEvents()
            assert dialog.width() > 0
            assert dialog.height() > 0
        finally:
            dialog.deleteLater()
            qapp.processEvents()

    def test_minimum_width_440_preserved(
        self, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Dialog minimum width (440) is preserved for the long-text path."""
        monkeypatch.setattr(dialogs_module, "animations_supported", lambda: True)
        dialog = _build_message_box_dialog(None, "Title", "X" * 1000)
        try:
            qapp.processEvents()
            assert dialog.minimumWidth() == 440, (
                f"Expected minimumWidth=440, got {dialog.minimumWidth()}"
            )
        finally:
            dialog.deleteLater()
            qapp.processEvents()


# ── 5. Object-name preservation (MUST NOT remove #appDialog etc.) ─────────


class TestObjectNamePreservation:
    """Existing object names must be preserved (MUST NOT do — task 13)."""

    def test_dialog_object_name_is_appdialog(
        self, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(dialogs_module, "animations_supported", lambda: True)
        dialog = _build_message_box_dialog(None, "Title", "msg")
        try:
            assert dialog.objectName() == "appDialog", (
                f"Expected objectName='appDialog', got {dialog.objectName()!r}"
            )
        finally:
            dialog.deleteLater()
            qapp.processEvents()

    def test_text_label_object_name_is_dialogtext(
        self, qapp: QApplication, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(dialogs_module, "animations_supported", lambda: True)
        dialog = _build_message_box_dialog(None, "Title", "msg")
        try:
            from PySide6.QtWidgets import QLabel

            labels = dialog.findChildren(QLabel, "dialogText")
            assert len(labels) == 1, (
                f"Expected exactly 1 QLabel#dialogText, got {len(labels)}"
            )
        finally:
            dialog.deleteLater()
            qapp.processEvents()
