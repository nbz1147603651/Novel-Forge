"""Common interfaces to break circular dependencies between modules."""

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from novel_forge.gateway.types import ModelRequest, ModelResponse


@runtime_checkable
class ModelRouterProtocol(Protocol):
    """Protocol defining the interface for ModelRouter to break circular dependencies."""

    @abstractmethod
    async def route(
        self,
        request: "ModelRequest",
        *,
        provider: str | None = None,
    ) -> "ModelResponse":
        """Route a request through adapter selection → call."""
        pass


class BaseModelRouter(ABC):
    """Abstract base class for ModelRouter to break circular dependencies."""

    @abstractmethod
    async def route(
        self,
        request: "ModelRequest",
        *,
        provider: str | None = None,
    ) -> "ModelResponse":
        """Route a request through adapter selection → call."""
        pass
