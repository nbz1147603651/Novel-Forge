"""Shared constants for the desktop UI layer.

This module centralizes all magic numbers, status text mappings, and tone
mappings that are used across multiple desktop UI modules. By extracting
these into a single location, we:
1. Eliminate code duplication
2. Make it easy to change status text or tone mappings in one place
3. Ensure consistency across all UI components

Note: Job status mappings use string keys ("queued", "running", etc.) to avoid
circular imports with the jobs module.
"""

from __future__ import annotations

import re
import sys
from typing import Final

JOB_STATUS_KEYS: Final[frozenset[str]] = frozenset(
    {
        "queued",
        "running",
        "paused",
        "succeeded",
        "failed",
    }
)

JOB_STATUS_TEXT: Final[dict[str, str]] = {
    "queued": "排队中",
    "running": "执行中",
    "paused": "等待决策",
    "succeeded": "已完成",
    "failed": "失败",
}

JOB_STATUS_TEXT_AUTO: Final[dict[str, str]] = {
    "queued": "自动中",
    "running": "自动中",
    "paused": "自动中",
    "succeeded": "已完成",
    "failed": "失败",
}

JOB_STATUS_TONE: Final[dict[str, str]] = {
    "queued": "muted",
    "paused": "muted",
    "succeeded": "success",
    "failed": "danger",
    "running": "warning",
}

SEARCH_DEBOUNCE_MS: Final[int] = 120
CONTEXT_REQUEST_DEBOUNCE_MS: Final[int] = 250
WORKSPACE_REFRESH_INTERVAL_MS: Final[int] = 300_000
REFRESH_DEBOUNCE_MS: Final[int] = 5_000
JOB_BIND_DEBOUNCE_MS: Final[int] = 45
JOB_BIND_COALESCE_MS: Final[int] = 120
JOBS_PANEL_RENDER_COALESCE_MS: Final[int] = 16
FS_WATCHER_DEBOUNCE_MS: Final[int] = 3000
DENSITY_RESIZE_DEBOUNCE_MS: Final[int] = 500
ANIMATIONS_ENABLED: Final[bool] = True


def animations_supported() -> bool:
    """Return True if UI animations should be enabled on this platform.

    Animations are disabled on macOS (Darwin) because the combination of
    nested translucent widgets + animated height changes causes compositor
    trails and visual artifacts.
    """
    return ANIMATIONS_ENABLED and sys.platform != "darwin"


MAX_JOB_EVENTS: Final[int] = 36
MAX_DISPLAYED_JOBS: Final[int] = 4

DEFAULT_GRID_COLUMNS: Final[int] = 2
MODEL_CARD_GRID_COLUMNS: Final[int] = 3
MAX_RECENT_CHAPTERS_DISPLAY: Final[int] = 4
MAX_RECENT_FILES_DISPLAY: Final[int] = 5
MAX_CHAPTER_JOBS_DISPLAY: Final[int] = 10

# Extract chapter number from Chinese job labels like "第 3 章"
LABEL_CHAPTER_RE: Final[re.Pattern[str]] = re.compile(r"第\s*(\d+)\s*章")

# ── Window layout constants (used by CoreMixin, SideRailMixin, ContentMixin, NavigationMixin) ──

WINDOW_DEFAULT_MIN_WIDTH: Final[int] = 1180
WINDOW_DEFAULT_MIN_HEIGHT: Final[int] = 760
WINDOW_DEFAULT_START_WIDTH: Final[int] = 1440
WINDOW_DEFAULT_START_HEIGHT: Final[int] = 900
WINDOW_SCREEN_WIDTH_RATIO: Final[float] = 0.92
WINDOW_SCREEN_HEIGHT_RATIO: Final[float] = 0.90
COMPACT_WIDTH_THRESHOLD: Final[int] = 1360
COMPACT_HEIGHT_THRESHOLD: Final[int] = 820

# Side rail dimensions
SIDE_RAIL_EXPANDED_WIDTH: Final[int] = 300
SIDE_RAIL_COMPACT_WIDTH: Final[int] = 264
SIDE_RAIL_COLLAPSED_WIDTH: Final[int] = 52
SIDE_RAIL_ANIMATION_MS: Final[int] = 180
