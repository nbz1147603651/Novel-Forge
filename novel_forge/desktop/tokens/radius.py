"""圆角设计令牌 — 6 级圆角层级系统。

本模块定义了桌面应用 UI 组件的圆角半径常量，形成从大到小的 6 级层级体系：

- ``SURFACE_RADIUS`` (16) — 主卡片、容器表面，最大的通用圆角
- ``DIALOG_RADIUS`` (14) — 对话框、模态窗口
- ``COMPACT_CARD_RADIUS`` (12) — 紧凑卡片、折叠面板
- ``SKELETON_RADIUS`` (12) — 加载占位骨架屏
- ``CHIP_RADIUS`` (8) — 小型元素、chip、badge
- ``TOOLTIP_RADIUS`` (8) — 浮动提示、弹出层

Usage::

    from novel_forge.desktop.tokens.radius import SURFACE_RADIUS

    qss = f"border-radius: {SURFACE_RADIUS}px;"
"""

from __future__ import annotations

# ── Radius Tokens ──────────────────────────────────────────────────────────

SURFACE_RADIUS: int = 16
"""主卡片 / 容器表面圆角 — 最大的通用表面半径。"""

DIALOG_RADIUS: int = 14
"""对话框 / 模态窗口圆角 — 比表面略小，保持视觉层次。"""

COMPACT_CARD_RADIUS: int = 12
"""紧凑卡片 / 折叠面板圆角 — 用于次级容器。"""

SKELETON_RADIUS: int = 12
"""加载占位骨架屏圆角 — 与紧凑卡片同级。"""

CHIP_RADIUS: int = 8
"""小型元素 / chip / badge 圆角 — 最小一级圆角。"""

TOOLTIP_RADIUS: int = 8
"""浮动提示 / 弹出层圆角 — 与 chip 同级。"""

# ── Public API ────────────────────────────────────────────────────────────

__all__ = [
    "CHIP_RADIUS",
    "COMPACT_CARD_RADIUS",
    "DIALOG_RADIUS",
    "SKELETON_RADIUS",
    "SURFACE_RADIUS",
    "TOOLTIP_RADIUS",
]
