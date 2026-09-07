"""Jobs domain mixin - consolidates job dispatch mixins.

Combines :class:`JobsBindingMixin` and :class:`DispatchMixin` into a single
domain-level mixin.  Both handle job lifecycle: binding job lists to pages
and dispatching job requests to the job manager.  Grouping them reduces the
MRO depth of ``NovelForgeDesktopWindow``.

The individual mixin modules are still importable for backward compatibility.
"""

from __future__ import annotations

from novel_forge.desktop.window.dispatch import DispatchMixin
from novel_forge.desktop.window.jobs_binding import JobsBindingMixin


class JobsDomainMixin(JobsBindingMixin, DispatchMixin):
    """Unified jobs domain: job binding + request dispatch."""

    pass
