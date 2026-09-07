"""Component styles: Surface, Badge, labels, cards, tabs, step indicators, progress.

QSS content is split into fragment modules under ``theme/qss/``.
This module re-assembles them and applies radius token substitutions.
"""

from __future__ import annotations

from novel_forge.desktop.theme.qss import CONTENT as _RAW_CONTENT
from novel_forge.desktop.tokens.radius import (
    CHIP_RADIUS,
    COMPACT_CARD_RADIUS,
    SURFACE_RADIUS,
    TOOLTIP_RADIUS,
)

CONTENT = (
    _RAW_CONTENT.replace("__SURFACE_RADIUS__", str(SURFACE_RADIUS))
    .replace("__COMPACT_CARD_RADIUS__", str(COMPACT_CARD_RADIUS))
    .replace("__CHIP_RADIUS__", str(CHIP_RADIUS))
    .replace("__TOOLTIP_RADIUS__", str(TOOLTIP_RADIUS))
)
