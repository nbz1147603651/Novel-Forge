"""StorageBackend ABC for persistence."""

from __future__ import annotations

from abc import ABC, abstractmethod
from contextlib import nullcontext
from pathlib import Path
from typing import Any


class StorageBackend(ABC):
    """Abstract storage interface for project artifacts."""

    @abstractmethod
    def save_json(self, path: Path, data: dict[str, Any]) -> None:
        """Save a JSON-serializable dict."""

    @abstractmethod
    def load_json(self, path: Path) -> dict[str, Any]:
        """Load a JSON file and return as dict."""

    @abstractmethod
    def save_text(self, path: Path, text: str) -> None:
        """Save plain text."""

    @abstractmethod
    def load_text(self, path: Path) -> str:
        """Load plain text."""

    @abstractmethod
    def exists(self, path: Path) -> bool:
        """Check if a path exists."""

    @abstractmethod
    def list_dir(self, path: Path) -> list[Path]:
        """List contents of a directory."""

    def project_lock(self, project_id: str):  # type: ignore[no-untyped-def]
        """Optional cross-process project lock; defaults to a no-op context."""
        return nullcontext()
