"""Legacy settings sections module — re-exports from settings_forms.

This module is kept for backward compatibility. All new code should
import directly from `novel_forge.desktop.pages.settings_forms`.
"""

from __future__ import annotations

from novel_forge.core.task_catalog import (
    MULTI_TURN_TASK_KEYS,
    ROUTING_GROUPS,
)
from novel_forge.desktop.pages.settings.components import (
    _ConnectionTestWorker,
    _ModelDialog,
    _ModelManageCard,
    _ModelStatusCard,
    _RuntimeCard,
    _TaskRouteRow,
)

# Legacy aliases for backward compatibility
_ROUTING_GROUPS = ROUTING_GROUPS
_MULTI_TURN_TASKS = MULTI_TURN_TASK_KEYS

# Re-export for backward compatibility
__all__ = [
    "_ROUTING_GROUPS",
    "_MULTI_TURN_TASKS",
    "_TaskRouteRow",
    "_ModelManageCard",
    "_ModelStatusCard",
    "_RuntimeCard",
    "_ModelDialog",
    "_ConnectionTestWorker",
]
