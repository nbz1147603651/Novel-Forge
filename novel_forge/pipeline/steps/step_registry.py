"""Step registry — central registration for all pipeline steps.

Provides:
- @register_step(name) decorator for step classes
- StepRegistry.get_step(name) to retrieve step class by name
- StepRegistry.list_steps() to get all registered step names
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    pass


_REGISTRY: dict[str, type] = {}


def register_step(name: str) -> Callable[[type[Any]], type[Any]]:
    """Decorator to register a step class under a canonical name.

    Usage:
        @register_step("draft")
        class DraftStep(PipelineStep[DraftInput, Draft]):
            ...
    """

    def decorator(cls: type) -> type:
        _REGISTRY[name] = cls
        return cls

    return decorator


class StepRegistry:
    """Central registry for all pipeline steps.

    Provides lookup by canonical step name.
    """

    @staticmethod
    def get_step(name: str) -> type:
        """Return the step class registered under *name*.

        Raises KeyError if not found.
        """
        if name not in _REGISTRY:
            available = sorted(_REGISTRY.keys())
            raise KeyError(
                f"Step {name!r} not found. Available steps: {available}"
            )
        return _REGISTRY[name]

    @staticmethod
    def list_steps() -> list[str]:
        """Return sorted list of all registered step names."""
        return sorted(_REGISTRY.keys())

    @staticmethod
    def step_count() -> int:
        """Return the number of registered steps."""
        return len(_REGISTRY)

    @staticmethod
    def is_registered(name: str) -> bool:
        """Return True if a step is registered under *name*."""
        return name in _REGISTRY
