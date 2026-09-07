"""Tests for theme.py shim migration and token substitution system.

Verifies:
- Backward-compatible imports from ``novel_forge.desktop.theme``
- ``get_stylesheet()`` and ``get_stylesheet({})`` produce byte-identical output
- Token override via ``get_stylesheet(tokens)`` works correctly
- Bad token keys don't crash (graceful fallback)
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _clear_theme_cache() -> None:
    """Clear lru_cache on _raw_combined_qss between tests."""
    from novel_forge.desktop.theme import _default_stylesheet, _raw_combined_qss

    _raw_combined_qss.cache_clear()
    _default_stylesheet.cache_clear()
    yield
    _raw_combined_qss.cache_clear()
    _default_stylesheet.cache_clear()


class TestThemeShimImport:
    """Backward-compatible imports from theme.py shim."""

    def test_get_stylesheet_importable_from_theme_module(self) -> None:
        """``from novel_forge.desktop.theme import get_stylesheet`` works."""
        from novel_forge.desktop.theme import get_stylesheet

        assert callable(get_stylesheet)

    def test_color_constants_importable(self) -> None:
        """All legacy color constants are importable from the shim."""
        from novel_forge.desktop.theme import (
            ACCENT_PRIMARY,
            ACCENT_PRIMARY_HOVER,
            BG_SURFACE,
            BG_WORKSPACE,
            COLOR_MAP,
            TEXT_PRIMARY,
            TEXT_SECONDARY,
            get_color,
        )

        assert ACCENT_PRIMARY == (182, 86, 52)
        assert ACCENT_PRIMARY_HOVER == (199, 100, 66)
        assert BG_WORKSPACE == (247, 240, 231)
        assert BG_SURFACE == (255, 250, 243)
        assert TEXT_PRIMARY == (44, 36, 30)
        assert TEXT_SECONDARY == (102, 87, 75)
        assert isinstance(COLOR_MAP, dict)
        assert callable(get_color)
        assert get_color("accent_primary") == ACCENT_PRIMARY

    def test_get_color_returns_none_for_unknown(self) -> None:
        """``get_color()`` returns None for unknown names (no crash)."""
        from novel_forge.desktop.theme import get_color

        assert get_color("nonexistent_color") is None

    def test_submodule_imports(self) -> None:
        """Theme submodules are importable from the package."""
        from novel_forge.desktop.theme import (
            _globals,
            components,
            dialogs,
            forms,
            memory,
            navigation,
            toast,
        )

        assert hasattr(_globals, "CONTENT")
        assert hasattr(components, "CONTENT")
        assert hasattr(dialogs, "CONTENT")
        assert hasattr(forms, "CONTENT")
        assert hasattr(memory, "CONTENT")
        assert hasattr(navigation, "CONTENT")
        assert hasattr(toast, "CONTENT")


class TestGetStylesheetByteIdentity:
    """``get_stylesheet({})`` MUST return byte-identical QSS to ``get_stylesheet()``."""

    def test_no_args_vs_empty_dict(self) -> None:
        """``get_stylesheet()`` == ``get_stylesheet({})`` — pixel-identical."""
        from novel_forge.desktop.theme import get_stylesheet

        qss_default = get_stylesheet()
        qss_empty = get_stylesheet({})
        assert qss_default == qss_empty, (
            f"get_stylesheet() and get_stylesheet({{}}) must be byte-identical. "
            f"Lengths: {len(qss_default)} vs {len(qss_empty)}"
        )
        assert qss_default is qss_empty

    def test_stylesheet_not_empty(self) -> None:
        """Stylesheet is non-trivial (contains QSS content)."""
        from novel_forge.desktop.theme import get_stylesheet

        qss = get_stylesheet()
        assert len(qss) > 10000, f"Stylesheet too short: {len(qss)} chars"
        assert "QWidget" in qss
        assert "QPushButton" in qss

    def test_resource_paths_resolved(self) -> None:
        """``__ARROW_DOWN__`` and ``__ARROW_UP__`` placeholders are resolved."""
        from novel_forge.desktop.theme import get_stylesheet

        qss = get_stylesheet()
        assert "__ARROW_DOWN__" not in qss
        assert "__ARROW_UP__" not in qss
        assert "arrow_down.svg" in qss
        assert "arrow_up.svg" in qss

    def test_no_unresolved_token_placeholders(self) -> None:
        """Default stylesheet has no remaining ``{{…}}`` placeholders."""
        import re

        from novel_forge.desktop.theme import get_stylesheet

        qss = get_stylesheet()
        remaining = re.findall(r"\{\{[^}]+\}\}", qss)
        assert not remaining, f"Unresolved placeholders: {remaining[:5]}"


class TestTokenSubstitution:
    """Token override and edge-case behavior."""

    def test_override_known_token(self) -> None:
        """Overriding a known token replaces its value in the output."""
        from novel_forge.desktop.theme import get_stylesheet

        qss = get_stylesheet({"accent.primary": "#ff0000"})
        assert "#ff0000" in qss
        # The original hex should be gone (all occurrences replaced)
        assert "#b65634" not in qss

    def test_bad_token_key_does_not_crash(self) -> None:
        """Unknown token keys are silently ignored (no crash)."""
        from novel_forge.desktop.theme import get_stylesheet

        # Should not raise
        qss = get_stylesheet({"totally.fake.token": "#000000"})
        assert len(qss) > 10000
        # The fake token should not appear in output (it's not in any CONTENT)
        assert "totally.fake.token" not in qss

    def test_partial_override_preserves_defaults(self) -> None:
        """Overriding one token doesn't affect other tokens."""
        from novel_forge.desktop.theme import get_stylesheet

        get_stylesheet()
        qss_override = get_stylesheet({"accent.primary": "#ff0000"})

        # Other tokens should still resolve to their defaults
        # text.primary (#2c241e) should still be present
        assert "#2c241e" in qss_override
        # But accent.primary should be overridden
        assert "#ff0000" in qss_override
        assert "#b65634" not in qss_override

    def test_multiple_overrides(self) -> None:
        """Multiple token overrides work simultaneously."""
        from novel_forge.desktop.theme import get_stylesheet

        qss = get_stylesheet({
            "accent.primary": "#111111",
            "text.primary": "#222222",
            "bg.workspace": "#333333",
        })
        assert "#111111" in qss
        assert "#222222" in qss
        assert "#333333" in qss

    def test_rgba_token_placeholder_resolves_to_components(self) -> None:
        """``rgba({{token}}, alpha)`` resolves to Qt-compatible RGBA."""
        from novel_forge.desktop.theme import get_stylesheet

        qss = get_stylesheet({"bg.hover.secondary": "#010203"})
        assert "rgba(1, 2, 3, 0.7)" in qss
        assert "rgba(#010203, 0.7)" not in qss


class TestTokenPlaceholdersInContent:
    """Verify CONTENT strings use token placeholders correctly."""

    def test_globals_has_placeholders(self) -> None:
        """``_globals.CONTENT`` contains token placeholders."""
        import re

        from novel_forge.desktop.theme import _globals

        placeholders = re.findall(r"\{\{[^}]+\}\}", _globals.CONTENT)
        assert len(placeholders) > 0, "_globals.CONTENT should have token placeholders"

    def test_components_has_placeholders(self) -> None:
        """``components.CONTENT`` contains token placeholders."""
        import re

        from novel_forge.desktop.theme import components

        placeholders = re.findall(r"\{\{[^}]+\}\}", components.CONTENT)
        assert len(placeholders) > 100, (
            f"components.CONTENT should have many placeholders, got {len(placeholders)}"
        )

    def test_no_raw_hex_in_content(self) -> None:
        """No 6-digit hex values remain in CONTENT strings (all replaced)."""
        import re

        from novel_forge.desktop.theme import (
            _globals,
            components,
            dialogs,
            forms,
            memory,
            navigation,
        )

        hex_pattern = re.compile(r"#[0-9a-fA-F]{6}\b")
        for mod_name, mod in [
            ("_globals", _globals),
            ("components", components),
            ("dialogs", dialogs),
            ("forms", forms),
            ("memory", memory),
            ("navigation", navigation),
        ]:
            hexes = hex_pattern.findall(mod.CONTENT)
            assert not hexes, (
                f"{mod_name}.CONTENT still has raw hex values: {hexes[:5]}"
            )
