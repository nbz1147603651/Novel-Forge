"""Abstract base for all model provider adapters."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Callable

from novel_forge.gateway.types import ModelRequest, ModelResponse, StreamChunk


class ProviderAdapter(ABC):
    """Interface that every model provider must implement."""

    _last_stream_response: ModelResponse | None = None

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Human-readable provider identifier, e.g. 'openai', 'anthropic', 'mock'."""

    @abstractmethod
    async def complete(self, request: ModelRequest) -> ModelResponse:
        """Send a completion request and return a standardized response."""

    async def stream(
        self,
        request: ModelRequest,
        *,
        on_final: Callable[[ModelResponse], None] | None = None,
    ) -> AsyncIterator[StreamChunk | str]:
        """Yield streaming deltas as they arrive from the model.

        Adapters should yield :class:`StreamChunk` objects.  For backward
        compatibility with test fakes, yielding plain ``str`` is also
        accepted (treated as content-only).  Callers normalize via
        :func:`novel_forge.gateway.types.to_stream_chunk`.

        Each chunk may carry ``content`` (the final answer text), ``reasoning``
        (thinking / chain-of-thought), or both.  The ordering of yielded
        chunks is significant: it reflects the real interleaving of
        reasoning and content as produced by the model, and downstream
        consumers preserve this order so the UI can render a faithful
        interleaved view.

        ``on_final`` is invoked exactly once with the aggregated
        :class:`ModelResponse` after the stream is fully consumed.  Callers
        that need token counts / cost should prefer ``on_final`` over the
        shared ``_last_stream_response`` instance attribute, which is unsafe
        under concurrent stream calls on the same adapter instance.

        The default implementation falls back to ``complete()`` and yields
        the entire content as a single chunk.
        """
        response = await self.complete(request)
        yield StreamChunk(content=response.content)
        # Keep the legacy instance attribute for backward compatibility, but
        # prefer the per-call ``on_final`` callback — the instance attribute
        # is shared across concurrent calls and subject to data races.
        self._last_stream_response = response
        if on_final is not None:
            on_final(response)

    @property
    def default_model(self) -> str | None:
        """Default model id used by the adapter when request.model_id is missing."""
        return None

    @property
    def last_health_error(self) -> str:
        """Last health-check error message for diagnostics."""
        return ""

    async def health_check(self) -> bool:
        """Return True if the provider is reachable."""
        return True

    async def shutdown(self) -> None:
        """Release any async resources held by this adapter."""
        return None

    async def aclose(self) -> None:
        """Alias for ``shutdown`` to support generic async cleanup flows."""
        await self.shutdown()
