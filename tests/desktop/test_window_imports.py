"""Smoke-test that desktop.window re-exports the public API after split."""

from __future__ import annotations

REQUIRED_NAMES = ("NovelForgeDesktopWindow",)


def test_window_public_api_importable():
    from novel_forge.desktop import window
    for name in REQUIRED_NAMES:
        assert hasattr(window, name), f"missing: {name}"
