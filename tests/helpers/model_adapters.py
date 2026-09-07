"""Schema-neutral provider adapter doubles used by integration tests."""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable

from novel_forge.gateway.base import ProviderAdapter
from novel_forge.gateway.types import ModelRequest, ModelResponse

ResponseHandler = Callable[
    [ModelRequest],
    ModelResponse | Awaitable[ModelResponse],
]


class HandlerModelAdapter(ProviderAdapter):
    """Delegate completion to a handler while retaining the real stream contract."""

    def __init__(self, handler: ResponseHandler, *, name: str = "test") -> None:
        self._handler = handler
        self._name = name
        self.requests: list[ModelRequest] = []

    @property
    def provider_name(self) -> str:
        return self._name

    async def complete(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        response = self._handler(request)
        if inspect.isawaitable(response):
            return await response
        return response
