"""Main window rebuilt around the 0310 navigation shell.

This package was created as part of the M3 refactor (giant file splits).
The original 4728-line ``window.py`` has been decomposed into per-domain
mixin classes living in the sub-modules below. The
:data:`NovelForgeDesktopWindow` symbol is composed here from those mixins
and remains import-compatible with the old monolith.
"""

from __future__ import annotations

from PySide6.QtWidgets import QMainWindow

# Re-exports of common imports so test ``patch("novel_forge.desktop.window.X")``
# calls (which rely on the name being an attribute of this module) keep working.
from novel_forge.core.config import get_settings, reset_settings  # noqa: E402, F401
from novel_forge.desktop.book_consistency_dialog import (  # noqa: E402, F401
    book_dialog_chapter_label as _book_dialog_chapter_label,
)
from novel_forge.desktop.components.dialogs import (  # noqa: E402, F401
    show_structured_result_dialog,
)
from novel_forge.desktop.components.toast import ToastManager  # noqa: E402, F401
from novel_forge.desktop.motion import (
    Motion,  # noqa: E402, F401
    animations_supported,  # noqa: E402, F401
)
from novel_forge.desktop.motion import (  # noqa: E402, F401
    animations_supported as motion_animations_supported,
)
from novel_forge.desktop.notification_sounds import (  # noqa: E402, F401
    DesktopNotificationSoundPlayer,
)
from novel_forge.desktop.registry import page_registry  # noqa: E402, F401
from novel_forge.desktop.state.store import WindowState, get_ui_store  # noqa: E402, F401
from novel_forge.desktop.strings import UIStrings  # noqa: E402, F401
from novel_forge.desktop.task_observation import (  # noqa: E402, F401
    TaskFocusScope,
    TaskObservationStore,
)
from novel_forge.desktop.thread_pools import desktop_thread_pools  # noqa: E402, F401
from novel_forge.desktop.ui_perf import ui_perf_span  # noqa: E402, F401

# Helper modules now co-located inside this package. Re-exported here so test
# code and downstream callers can keep using ``from novel_forge.desktop.window
# import X`` for these names. (P4 consolidation.)
from novel_forge.desktop.window._runnables import (  # noqa: E402, F401
    _ChapterContextRefreshRunnable,
    _RefreshSignals,
    _WorkspaceRefreshRunnable,
)
from novel_forge.desktop.window._widgets import (  # noqa: E402, F401
    CachedGradientFrame,
    CachedGradientWidget,
    NavigationButton,
    PageMeta,
    SectionBindablePage,
)
from novel_forge.desktop.workspace import (  # noqa: E402, F401
    DesktopWorkspaceService,
    DesktopWorkspaceSnapshot,
)

__all__ = [
    "DesktopNotificationSoundPlayer",
    "DesktopWorkspaceService",
    "DesktopWorkspaceSnapshot",
    "Motion",
    "NavigationButton",
    "NovelForgeDesktopWindow",
    "PageMeta",
    "SectionBindablePage",
    "TaskFocusScope",
    "TaskObservationStore",
    "ToastManager",
    "UIStrings",
    "WindowState",
    "_ChapterContextRefreshRunnable",
    "_RefreshSignals",
    "_WorkspaceRefreshRunnable",
    "_book_dialog_chapter_label",
    "animations_supported",
    "desktop_thread_pools",
    "get_settings",
    "get_ui_store",
    "motion_animations_supported",
    "page_registry",
    "reset_settings",
    "show_structured_result_dialog",
    "ui_perf_span",
]

# Mixin sub-modules (each adds a logical slice of methods onto the class).
from novel_forge.desktop.window.autorun import AutorunMixin  # noqa: F401
from novel_forge.desktop.window.chapter_focus import ChapterFocusMixin  # noqa: F401
from novel_forge.desktop.window.core import CoreMixin  # noqa: F401
from novel_forge.desktop.window.dispatch import DispatchMixin  # noqa: F401
from novel_forge.desktop.window.jobs_binding import JobsBindingMixin  # noqa: F401
from novel_forge.desktop.window.jobs_domain import JobsDomainMixin  # noqa: F401
from novel_forge.desktop.window.navigation import NavigationMixin  # noqa: F401
from novel_forge.desktop.window.navigation_domain import NavigationDomainMixin  # noqa: F401
from novel_forge.desktop.window.navigation_handlers import NavigationHandlersMixin  # noqa: F401
from novel_forge.desktop.window.notifications import NotificationsMixin  # noqa: F401
from novel_forge.desktop.window.page_binding import PageBindingMixin  # noqa: F401
from novel_forge.desktop.window.page_loading import PageLoadingMixin  # noqa: F401
from novel_forge.desktop.window.panels.content import ContentMixin  # noqa: F401
from novel_forge.desktop.window.panels.side_rail import SideRailMixin  # noqa: F401
from novel_forge.desktop.window.settings_panel import SettingsPanelMixin  # noqa: F401
from novel_forge.desktop.window.shutdown import ShutdownMixin  # noqa: F401
from novel_forge.desktop.window.signals import SignalsMixin  # noqa: F401
from novel_forge.desktop.window.skeleton_overlay import SkeletonOverlayMixin  # noqa: F401
from novel_forge.desktop.window.task_companion import TaskCompanionMixin  # noqa: F401
from novel_forge.desktop.window.task_focus_dialog import TaskFocusDialogMixin  # noqa: F401
from novel_forge.desktop.window.workspace_domain import WorkspaceDomainMixin  # noqa: F401
from novel_forge.desktop.window.workspace_refresh import WorkspaceRefreshMixin  # noqa: F401


class NovelForgeDesktopWindow(CoreMixin, SideRailMixin, ContentMixin, TaskCompanionMixin, TaskFocusDialogMixin, SignalsMixin, NavigationDomainMixin, WorkspaceDomainMixin, JobsDomainMixin, AutorunMixin, NotificationsMixin, ChapterFocusMixin, SettingsPanelMixin, ShutdownMixin, SkeletonOverlayMixin, QMainWindow):
    """Top-level desktop shell.

    Method implementations are inherited from per-domain mixin modules under
    this package. The split was performed in the M3.1 refactor; see
    ``docs/superpowers/plans/2026-07-02-ui-architecture-refactor-plan.md``.
    """

    pass


# Backward-compatibility alias used by older imports / type hints.
MainWindow = NovelForgeDesktopWindow
