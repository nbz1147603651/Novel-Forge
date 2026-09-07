"""Standalone pages — pages that don't belong to a larger page group.

Auto-discovered by ``pages/__init__.py`` via pkgutil.iter_modules.

Includes dashboard, projects, character profile, relationship network,
humanize library, token analytics, outline editor, and other pages that
each have their own flat sibling set.

The actual page classes (DashboardPage, ProjectsPage, …) are imported
lazily by ``page_registrations.py`` to avoid triggering deep standalone
import chains on package init.
"""

from __future__ import annotations