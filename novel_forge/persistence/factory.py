"""Factory helpers for assembling the configured storage backend."""

from __future__ import annotations

from novel_forge.core.config import Settings
from novel_forge.persistence.filesystem import CachedFileSystemStorage, FileSystemStorage


def create_storage_backend(settings: Settings) -> FileSystemStorage:
    """Create the storage backend configured by the current settings."""
    if settings.storage_cache_enabled:
        return CachedFileSystemStorage(
            settings.storage_root,
            max_entries=settings.storage_cache_max_entries,
        )
    return FileSystemStorage(settings.storage_root)
