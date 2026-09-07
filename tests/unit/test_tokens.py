"""Unit tests for the design token system (colors + spacing).

Tests cover:
1. Color token values match theme/core.py constants (pixel-perfect)
2. Hex coverage of existing QSS values ≥ 80%
3. Missing-key edge case: COLORS.get('nonexistent') returns None
4. Spacing scale coverage ≥ 70%
5. Frozen immutability of token containers
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

# ── Imports under test ──────────────────────────────────────────────────────
from novel_forge.desktop.theme.core import (
    ACCENT_PRIMARY,
    ACCENT_PRIMARY_HOVER,
    BG_SURFACE,
    BG_WORKSPACE,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
)
from novel_forge.desktop.tokens.colors import (
    COLORS,
    _ColorTokens,
    all_hex_values,
    hex_to_tokens,
)
from novel_forge.desktop.tokens.spacing import SPACING, _SpacingTokens

# ── Helpers ─────────────────────────────────────────────────────────────────


def _rgb_to_hex(r: int, g: int, b: int) -> str:
    return f"#{r:02x}{g:02x}{b:02x}"


def _theme_dir() -> Path:
    """Return the path to novel_forge/desktop/theme/."""
    return Path(__file__).resolve().parent.parent.parent / "novel_forge" / "desktop" / "theme"


# ── Test: 7 core constants pixel-perfect match ──────────────────────────────


class TestCoreConstantPixelPerfect:
    """Verify the 7 core theme constants match token values exactly."""

    @pytest.mark.parametrize(
        "constant_rgb,token_key",
        [
            (ACCENT_PRIMARY, "accent.primary"),
            (ACCENT_PRIMARY_HOVER, "accent.primary.hover"),
            (BG_WORKSPACE, "bg.workspace"),
            (BG_SURFACE, "bg.surface"),
            (TEXT_PRIMARY, "text.primary"),
            (TEXT_SECONDARY, "text.secondary"),
        ],
    )
    def test_core_constant_matches_token(self, constant_rgb, token_key):
        expected_hex = _rgb_to_hex(*constant_rgb[:3])
        token = COLORS.get(token_key)
        assert token is not None, f"Token '{token_key}' not found in COLORS"
        assert token[0].lower() == expected_hex.lower(), (
            f"Token '{token_key}' hex {token[0]} != core constant {expected_hex}"
        )

    def test_accent_primary_pressed_exists(self):
        """accent.primary.pressed is the 7th pixel-perfect constant."""
        token = COLORS.get("accent.primary.pressed")
        assert token is not None
        assert token[0] == "#a34a2a"


# ── Test: Missing-key edge case ─────────────────────────────────────────────


class TestMissingKeyEdgeCase:
    """COLORS.get('nonexistent.token') must return None, not raise KeyError."""

    def test_get_missing_returns_none(self):
        result = COLORS.get("nonexistent.token")
        assert result is None

    def test_get_missing_with_default(self):
        result = COLORS.get("nonexistent.token", "fallback")
        assert result == "fallback"

    def test_getitem_missing_raises_keyerror(self):
        """Direct dict access with [] should raise KeyError (standard dict behavior)."""
        with pytest.raises(KeyError):
            _ = COLORS["nonexistent.token"]

    def test_spacing_get_missing_returns_none(self):
        result = SPACING.get("nonexistent.space")
        assert result is None


# ── Test: Color token count and structure ───────────────────────────────────


class TestColorTokenStructure:
    """Verify COLORS dict has ≥30 tokens with correct tuple structure."""

    def test_minimum_token_count(self):
        assert len(COLORS) >= 30, f"Expected ≥30 tokens, got {len(COLORS)}"

    def test_actual_token_count(self):
        """We defined 129 tokens covering all theme hex values."""
        assert len(COLORS) >= 100

    def test_all_values_are_tuples(self):
        for key, val in COLORS.items():
            assert isinstance(val, tuple), f"COLORS['{key}'] is {type(val)}, expected tuple"
            assert len(val) == 2, f"COLORS['{key}'] has {len(val)} elements, expected 2"

    def test_all_hex_values_are_valid(self):
        hex_pattern = re.compile(r"^#[0-9a-f]{6}$")
        for key, (hex_val, _desc) in COLORS.items():
            assert hex_pattern.match(hex_val), (
                f"COLORS['{key}'] hex '{hex_val}' is not a valid 6-digit lowercase hex"
            )

    def test_all_descriptions_are_nonempty_strings(self):
        for key, (_, desc) in COLORS.items():
            assert isinstance(desc, str) and len(desc) > 0, (
                f"COLORS['{key}'] description is empty"
            )

    def test_frozen_dataclass_immutable(self):
        tokens = _ColorTokens()
        with pytest.raises(AttributeError):
            tokens.accent_primary = ("#000000", "hacked")  # type: ignore[misc]


# ── Test: Hex coverage ≥ 80% ───────────────────────────────────────────────


class TestHexCoverage:
    """Verify that ≥80% of hex values in theme/*.py are covered by tokens."""

    def test_coverage_at_least_80_percent(self):
        token_hexes = all_hex_values()
        theme_dir = _theme_dir()

        found_hexes: set[str] = set()
        for qss_file in theme_dir.glob("*.py"):
            content = qss_file.read_text()
            for m in re.findall(r"#[0-9a-fA-F]{6}", content):
                found_hexes.add(m.lower())

        unmatched = found_hexes - token_hexes
        coverage = (len(found_hexes) - len(unmatched)) / max(len(found_hexes), 1) * 100

        assert coverage >= 80, (
            f"Coverage too low: {coverage:.1f}% "
            f"({len(found_hexes) - len(unmatched)}/{len(found_hexes)}). "
            f"Unmatched: {sorted(unmatched)[:20]}"
        )


# ── Test: Spacing scale ────────────────────────────────────────────────────


class TestSpacingScale:
    """Verify spacing tokens cover ≥70% of QSS padding values."""

    def test_minimum_spacing_count(self):
        assert len(SPACING) >= 10, f"Expected ≥10 spacing tokens, got {len(SPACING)}"

    def test_spacing_values_are_integers(self):
        for key, val in SPACING.items():
            assert isinstance(val, int), f"SPACING['{key}'] is {type(val)}, expected int"

    def test_spacing_zero_exists(self):
        assert SPACING.get("space-0") == 0

    def test_spacing_scale_ordered(self):
        values = sorted(SPACING.values())
        assert values[0] == 0
        assert values[-1] == 48

    def test_spacing_coverage_at_least_70_percent(self):
        values = set(SPACING.values())
        theme_dir = _theme_dir()

        found_paddings: set[int] = set()
        for qss_file in theme_dir.glob("*.py"):
            for m in re.findall(r"padding:\s*(\d+)px", qss_file.read_text()):
                found_paddings.add(int(m))

        coverage = len(found_paddings & values) / max(len(found_paddings), 1) * 100
        assert coverage >= 70, (
            f"Spacing coverage too low: {coverage:.1f}%. "
            f"Found paddings: {sorted(found_paddings)}, "
            f"Token values: {sorted(values)}"
        )

    def test_frozen_dataclass_immutable(self):
        tokens = _SpacingTokens()
        with pytest.raises(AttributeError):
            tokens.space_0 = 999  # type: ignore[misc]


# ── Test: hex_to_tokens reverse lookup ──────────────────────────────────────


class TestHexToTokens:
    """Verify reverse lookup from hex value to token names."""

    def test_known_hex(self):
        tokens = hex_to_tokens("#b65634")
        assert "accent.primary" in tokens

    def test_unknown_hex_returns_empty(self):
        tokens = hex_to_tokens("#000000")
        assert tokens == []

    def test_case_insensitive(self):
        tokens_lower = hex_to_tokens("#b65634")
        tokens_upper = hex_to_tokens("#B65634")
        assert tokens_lower == tokens_upper


# ── Test: Module-level re-exports ───────────────────────────────────────────


class TestReExports:
    """Verify __init__.py re-exports work correctly."""

    def test_import_colors(self):
        from novel_forge.desktop.tokens import COLORS as C
        assert "accent.primary" in C

    def test_import_spacing(self):
        from novel_forge.desktop.tokens import SPACING as S
        assert "space-4" in S
