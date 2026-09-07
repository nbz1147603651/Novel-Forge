"""Workspace domain mixin - consolidates workspace-related mixins.

Combines :class:`WorkspaceRefreshMixin` and :class:`PageBindingMixin` into
a single domain-level mixin.  These two are tightly coupled: both operate
on ``_snapshot`` / ``_workspace_revision`` / ``_page_workspace_revision``
and the refresh logic calls into page binding.  Grouping them reduces the
MRO depth of ``NovelForgeDesktopWindow`` and makes the workspace dependency
graph explicit.

The individual mixin modules are still importable for backward compatibility.
"""

from __future__ import annotations

from novel_forge.desktop.window.page_binding import PageBindingMixin
from novel_forge.desktop.window.workspace_refresh import WorkspaceRefreshMixin


class WorkspaceDomainMixin(WorkspaceRefreshMixin, PageBindingMixin):
    """Unified workspace domain: snapshot refresh + page binding."""

    pass
