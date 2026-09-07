"""Translate worker pool names to QThreadPool instances.

Centralizes the 'job' / 'ui_io' / 'aux' ↔ pool mapping so BaseJobWorker
subclasses only need to set pool='ui_io' etc.
"""

from __future__ import annotations

from PySide6.QtCore import QThreadPool

from novel_forge.desktop.thread_pools import desktop_thread_pools

_POOL_NAMES = ("job", "ui_io", "aux")


def pool_for(name: str) -> QThreadPool:
    """Return the QThreadPool instance for the given worker pool name.

    Args:
        name: one of 'job' | 'ui_io' | 'aux'.

    Raises:
        ValueError: if name is not a recognized pool.
    """
    if name not in _POOL_NAMES:
        raise ValueError(f"unknown worker pool: {name!r}; expected one of {_POOL_NAMES}")
    pools = desktop_thread_pools()
    return pools.by_name(name)


def by_name(name: str) -> QThreadPool:
    """Alias for pool_for — kept for naming consistency with DesktopThreadPools."""
    return pool_for(name)


__all__ = ["pool_for", "by_name"]