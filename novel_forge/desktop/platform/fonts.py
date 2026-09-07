"""Per-platform font fallback tables."""

from __future__ import annotations

import sys


def font_fallback() -> list[str]:
    """List of font families to try, in order, for the current platform."""
    if sys.platform == "darwin":
        return ["PingFang SC", "Hiragino Sans GB", "STHeiti", "Helvetica Neue"]
    if sys.platform == "win32":
        return ["Microsoft YaHei", "SimHei", "Segoe UI"]
    return ["Noto Sans CJK SC", "Source Han Sans SC", "DejaVu Sans"]


__all__ = ["font_fallback"]
