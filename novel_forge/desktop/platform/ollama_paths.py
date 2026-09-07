"""Locate ollama binary per-platform."""

from __future__ import annotations

import shutil
import sys


def ollama_executable() -> str:
    """Return the ollama executable name/path for the current platform."""
    name = "ollama.exe" if sys.platform == "win32" else "ollama"
    return shutil.which(name) or name


__all__ = ["ollama_executable"]
