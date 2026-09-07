"""Settings pages.

Auto-discovered by ``pages/__init__.py`` via pkgutil.iter_modules.

Public entry point: :class:`SettingsPage` in :mod:`.page`.
The actual page class is imported lazily by ``page_registrations.py``
to avoid triggering the deep settings import chain on package init.

The ``parameters`` subpackage pre-existed the M4 layout split — it owns
the per-task parameter tabs (temperature, ollama, memory, …).
"""

from __future__ import annotations