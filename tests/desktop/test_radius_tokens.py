"""Tests for radius design tokens (Task 5 of ui-rounded-corners-polish).

Verifies:
- All 6 radius constants have the correct integer values.
- ``get_stylesheet()`` contains each constant's ``border-radius: Npx`` value.
- No bare ``__RADIUS__`` placeholder tokens remain (proving .replace() worked).
"""

from __future__ import annotations

from novel_forge.desktop.theme import get_stylesheet
from novel_forge.desktop.tokens.radius import (
    CHIP_RADIUS,
    COMPACT_CARD_RADIUS,
    DIALOG_RADIUS,
    SKELETON_RADIUS,
    SURFACE_RADIUS,
    TOOLTIP_RADIUS,
)

# ── Constant → expected value ──────────────────────────────────────

CONSTANT_VALUES: list[tuple[str, int, int]] = [
    ("SURFACE_RADIUS", SURFACE_RADIUS, 16),
    ("DIALOG_RADIUS", DIALOG_RADIUS, 14),
    ("CHIP_RADIUS", CHIP_RADIUS, 8),
    ("TOOLTIP_RADIUS", TOOLTIP_RADIUS, 8),
    ("SKELETON_RADIUS", SKELETON_RADIUS, 12),
    ("COMPACT_CARD_RADIUS", COMPACT_CARD_RADIUS, 12),
]


# ── Constant value tests ───────────────────────────────────────────


class TestRadiusConstantValues:
    """Each constant must hold its intended integer value."""

    def test_all_constants_have_correct_values(self) -> None:
        for name, val, expected in CONSTANT_VALUES:
            assert val == expected, f"{name} = {val}, expected {expected}"
            assert isinstance(val, int), f"{name} must be int, got {type(val).__name__}"


# ── QSS integration tests ─────────────────────────────────────────


class TestRadiusInQss:
    """``get_stylesheet()`` must contain each radius value at least once."""

    def test_qss_contains_all_radii(self) -> None:
        qss = get_stylesheet()
        for name, val, _ in CONSTANT_VALUES:
            assert f"border-radius: {val}px" in qss, (
                f"{name} ({val}px) not found in QSS output"
            )

    def test_qss_contains_all_radii_custom_tokens(self) -> None:
        """Same check passing an explicit empty-tokens dict."""
        qss = get_stylesheet(tokens={})
        for name, val, _ in CONSTANT_VALUES:
            assert f"border-radius: {val}px" in qss, (
                f"{name} ({val}px) not found in QSS output with tokens={{}}"
            )

    def test_qss_no_placeholder_tokens(self) -> None:
        """Ensure .replace() worked — no __TOKEN__ placeholders remain."""
        qss = get_stylesheet()
        for token in [
            "__SURFACE_RADIUS__",
            "__DIALOG_RADIUS__",
            "__CHIP_RADIUS__",
            "__TOOLTIP_RADIUS__",
            "__SKELETON_RADIUS__",
            "__COMPACT_CARD_RADIUS__",
        ]:
            assert token not in qss, f"Placeholder {token} still present in QSS"

    def test_qss_no_placeholder_tokens_custom_tokens(self) -> None:
        """Same check with explicit empty-tokens dict."""
        qss = get_stylesheet(tokens={})
        for token in [
            "__SURFACE_RADIUS__",
            "__DIALOG_RADIUS__",
            "__CHIP_RADIUS__",
            "__TOOLTIP_RADIUS__",
            "__SKELETON_RADIUS__",
            "__COMPACT_CARD_RADIUS__",
        ]:
            assert token not in qss, (
                f"Placeholder {token} still present in QSS with tokens={{}}"
            )


# ── Smoke test ─────────────────────────────────────────────────────


class TestRadiusSmoke:
    """Basic import and type checks."""

    def test_all_constants_are_exported(self) -> None:
        """All 6 constants should be accessible from the module."""
        assert SURFACE_RADIUS == 16
        assert DIALOG_RADIUS == 14
        assert CHIP_RADIUS == 8
        assert TOOLTIP_RADIUS == 8
        assert SKELETON_RADIUS == 12
        assert COMPACT_CARD_RADIUS == 12

    def test_all_constants_are_ints(self) -> None:
        for val in [
            SURFACE_RADIUS,
            DIALOG_RADIUS,
            CHIP_RADIUS,
            TOOLTIP_RADIUS,
            SKELETON_RADIUS,
            COMPACT_CARD_RADIUS,
        ]:
            assert isinstance(val, int)


class TestRadiusComponentIntegration:
    """Components with custom painting should consume the same radius tokens."""

    def test_skeleton_components_use_skeleton_radius(self) -> None:
        from novel_forge.desktop.components.skeleton import SkeletonCard, SkeletonLine

        assert SkeletonCard._BORDER_RADIUS == SKELETON_RADIUS
        assert SkeletonLine._BORDER_RADIUS == SKELETON_RADIUS
