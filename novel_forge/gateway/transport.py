"""Transport layer abstraction (Pi-inspired Direct vs Proxy mode).

Decouples LLM calls from the physical transport, enabling:
- DirectTransport: default, calls adapter directly (zero overhead).
- ProxyTransport: forwards requests to a remote worker via HTTP/WebSocket.

Quality constraints:
- DirectTransport behavior is identical to existing adapter calls.
- ProxyTransport only forwards request/response — never modifies payload.
- Transport errors are classified as gateway errors for RepairFailurePolicy.
"""

from __future__ import annotations

import logging
from typing import Any, AsyncIterator, Protocol, runtime_checkable

logger = logging.getLogger(__name__)


# ── Transport Protocol ────────────────────────────────────────────────────────


@runtime_checkable
class TransportProxy(Protocol):
    """Protocol for LLM request transport."""

    async def send(self, request: Any) -> Any:
        """Send a request and return the response.

        Args:
            request: The LLM request object (adapter-specific format).

        Returns:
            The LLM response object.

        Raises:
            TransportError: On communication failure.
        """
        ...

    async def send_stream(self, request: Any) -> AsyncIterator[Any]:
        """Send a request and stream the response chunks.

        Args:
            request: The LLM request object.

        Yields:
            Response chunks as they arrive.
        """
        ...


# ── Transport Error ───────────────────────────────────────────────────────────


class TransportError(Exception):
    """Raised when transport-level communication fails."""

    def __init__(self, message: str, *, is_transient: bool = True) -> None:
        super().__init__(message)
        self.is_transient = is_transient


# ── DirectTransport ───────────────────────────────────────────────────────────


class DirectTransport:
    """Default transport: calls the adapter directly (no indirection).

    This is equivalent to the existing behavior where PipelineStep calls
    router.route() which calls adapter.complete() directly.
    """

    def __init__(self, adapter: Any) -> None:
        self._adapter = adapter

    async def send(self, request: Any) -> Any:
        """Direct adapter call."""
        if hasattr(self._adapter, "complete"):
            return await self._adapter.complete(request)
        if hasattr(self._adapter, "generate"):
            return await self._adapter.generate(request)
        raise TransportError(
            f"Adapter {type(self._adapter).__name__} has no complete/generate method",
            is_transient=False,
        )

    async def send_stream(self, request: Any) -> AsyncIterator[Any]:
        """Direct adapter stream call."""
        if hasattr(self._adapter, "stream"):
            async for chunk in self._adapter.stream(request):
                yield chunk
        else:
            # Fallback: single response as one chunk
            result = await self.send(request)
            yield result


# ── ProxyTransport ────────────────────────────────────────────────────────────


class ProxyTransport:
    """Transport that forwards requests to a remote worker via HTTP.

    The remote worker runs the actual adapter and returns results.
    This enables distributed execution (e.g. GPU server for local models,
    or remote TTS synthesis workers).

    Configuration:
        NOVEL_FORGE_TRANSPORT_PROXY_URL: Remote worker URL.
        NOVEL_FORGE_TRANSPORT_TIMEOUT_S: Request timeout (default 300s).
    """

    def __init__(
        self,
        *,
        proxy_url: str,
        timeout_s: float = 300.0,
        headers: dict[str, str] | None = None,
    ) -> None:
        self._proxy_url = proxy_url.rstrip("/")
        self._timeout_s = timeout_s
        self._headers = headers or {}
        self._client: Any | None = None  # httpx.AsyncClient, lazily created

    async def _get_client(self) -> Any:
        """Get or create the shared httpx.AsyncClient for connection reuse."""
        if self._client is None:
            import httpx

            self._client = httpx.AsyncClient(
                timeout=self._timeout_s,
                limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
            )
        return self._client

    async def send(self, request: Any) -> Any:
        """Forward request to remote worker and return response."""
        try:
            client = await self._get_client()
            payload = self._serialize_request(request)
            response = await client.post(
                f"{self._proxy_url}/v1/complete",
                json=payload,
                headers=self._headers,
            )
            response.raise_for_status()
            return response.json()
        except ImportError:
            raise TransportError(
                "httpx not installed — ProxyTransport requires httpx",
                is_transient=False,
            ) from None
        except Exception as exc:
            raise TransportError(
                f"ProxyTransport request failed: {exc}",
                is_transient=True,
            ) from exc

    async def send_stream(self, request: Any) -> AsyncIterator[Any]:
        """Forward request and stream response chunks."""
        # For now, fall back to non-streaming
        result = await self.send(request)
        yield result

    async def aclose(self) -> None:
        """Close the underlying HTTP client and release connections."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    @staticmethod
    def _serialize_request(request: Any) -> dict[str, Any]:
        """Serialize a request object to JSON-compatible dict."""
        if isinstance(request, dict):
            return request
        if hasattr(request, "model_dump"):
            return request.model_dump(mode="json")
        if hasattr(request, "__dict__"):
            return {k: str(v) for k, v in request.__dict__.items()}
        return {"raw": str(request)}
