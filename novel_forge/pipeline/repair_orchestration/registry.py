"""Handler registry for Repair Orchestration v2."""

from __future__ import annotations

from dataclasses import dataclass, field

from novel_forge.pipeline.repair_orchestration.handlers import RepairHandler
from novel_forge.pipeline.repair_orchestration.models import RepairTarget


@dataclass
class RepairHandlerRegistry:
    """Ordered handler registry with first-match resolution."""

    _handlers: list[RepairHandler] = field(default_factory=list)

    def register(self, handler: RepairHandler) -> None:
        self._handlers.append(handler)

    def resolve(self, target: RepairTarget) -> RepairHandler | None:
        for handler in self._handlers:
            if handler.supports(target):
                return handler
        return None

    def handlers(self) -> tuple[RepairHandler, ...]:
        return tuple(self._handlers)
