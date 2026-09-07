"""Backward-compatible navigation imports for the desktop window shell.

The implementation lives in :mod:`novel_forge.desktop.window._navigation`.
Keep this module as a stable import surface for extensions and older tests
while callers migrate to the window package.
"""

from __future__ import annotations

from novel_forge.desktop.window._navigation import (
    connect_page_signals,
    ensure_page,
    switch_page,
)

__all__ = ("connect_page_signals", "ensure_page", "switch_page")
