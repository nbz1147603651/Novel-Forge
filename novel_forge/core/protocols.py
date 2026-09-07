"""Protocol interfaces for runtime dependency injection (Phase 8).

These Protocols define the minimal interfaces that RuntimeServices exposes,
allowing consumers to declare exactly which capabilities they need rather
than depending on the full RuntimeServices god-object.

Usage::

    from novel_forge.core.protocols import ModelRouting, StorageBackend

    async def my_step(runtime: ModelRouting & StorageBackend) -> None:
        ...
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:

    from novel_forge.core.config import Settings


# ── Model Routing ─────────────────────────────────────────────────────────────


@runtime_checkable
class ModelRouting(Protocol):
    """Access to the LLM model router for dispatching generation requests."""

    @property
    def router(self) -> Any:
        """Return the ModelRouter instance."""
        ...

    @property
    def settings(self) -> "Settings":
        """Return the active Settings."""
        ...


# ── Prompt Building ───────────────────────────────────────────────────────────


@runtime_checkable
class PromptBuilding(Protocol):
    """Access to the prompt builder for constructing LLM prompts."""

    @property
    def builder(self) -> Any:
        """Return the PromptBuilder instance."""
        ...


# ── Storage Backend ───────────────────────────────────────────────────────────


@runtime_checkable
class StorageBackend(Protocol):
    """Access to the filesystem storage backend."""

    @property
    def storage(self) -> Any:
        """Return the FileSystemStorage instance."""
        ...


# ── Control Plane ─────────────────────────────────────────────────────────────


@runtime_checkable
class ControlPlaneAccess(Protocol):
    """Access to the optional runtime control plane."""

    @property
    def control_plane(self) -> Any | None:
        """Return the RuntimeControlPlane or None."""
        ...


# ── Memory Context Provider ───────────────────────────────────────────────────


@runtime_checkable
class MemoryContextProvider(Protocol):
    """Access to per-project memory contexts."""

    async def get_memory_context(self, project_id: str) -> Any:
        """Return the MemoryContext for the given project."""
        ...


# ── Event Bus Provider ────────────────────────────────────────────────────────


@runtime_checkable
class EventBusProvider(Protocol):
    """Access to the project event bus."""

    @property
    def event_bus(self) -> Any:
        """Return the EventBus instance."""
        ...


__all__ = [
    "ModelRouting",
    "PromptBuilding",
    "StorageBackend",
    "ControlPlaneAccess",
    "MemoryContextProvider",
    "EventBusProvider",
]
