"""Navigation domain mixin — consolidates navigation-related mixins.

Combines :class:`NavigationMixin`, :class:`NavigationHandlersMixin`, and
:class:`PageLoadingMixin` into a single domain-level mixin.  This reduces
the MRO depth of ``NovelForgeDesktopWindow`` and makes the navigation
dependency graph explicit.

The individual mixin modules are still importable for backward compatibility.
"""

from __future__ import annotations

from novel_forge.desktop.window.navigation import NavigationMixin
from novel_forge.desktop.window.navigation_handlers import NavigationHandlersMixin
from novel_forge.desktop.window.page_loading import PageLoadingMixin


class NavigationDomainMixin(NavigationMixin, NavigationHandlersMixin, PageLoadingMixin):
    """Unified navigation domain: page switching, handlers, and loading.

    Replaces the three separate navigation mixins in the class hierarchy::

        # Before (19 bases):
        class W(CoreMixin, ..., NavigationMixin, ..., NavigationHandlersMixin,
                PageLoadingMixin, ...): ...

        # After (17 bases):
        class W(CoreMixin, ..., NavigationDomainMixin, ...): ...
    """

    pass
