"""Ollama provider adapter for local model inference."""
from __future__ import annotations

from collections.abc import AsyncIterator, Callable

from novel_forge.core.local_model_resources import (
    LocalMemoryClass,
    LocalResourcePriority,
    LocalResourceRequest,
    get_local_model_resource_broker,
)
from novel_forge.gateway.adapters.openai_compat import OpenAICompatibleAdapter
from novel_forge.gateway.client_lifecycle import close_async_client
from novel_forge.gateway.reasoning import normalize_thinking_mode, reasoning_effort_for_mode
from novel_forge.gateway.types import ModelRequest, ModelResponse, StreamChunk

_OLLAMA_BASE_URL = "http://localhost:11434/v1"
_OLLAMA_DEFAULT_MODEL = "llama3.2"


class OllamaAdapter(OpenAICompatibleAdapter):
    """Adapter for Ollama API (local model inference)."""

    def __init__(
        self,
        *,
        base_url: str = _OLLAMA_BASE_URL,
        default_model: str = _OLLAMA_DEFAULT_MODEL,
        connect_timeout_s: float = 10.0,
        read_timeout_s: float = 1200.0,
        resource_wait_timeout_s: float = 900.0,
    ) -> None:
        self._resource_wait_timeout_s = max(10.0, resource_wait_timeout_s)
        super().__init__(
            api_key="ollama",
            base_url=base_url,
            provider="ollama",
            default_model=default_model,
            connect_timeout_s=connect_timeout_s,
            read_timeout_s=read_timeout_s,
        )

    def _thinking_request_kwargs(
        self,
        request: ModelRequest,
        model_id: str,
    ) -> dict[str, object]:
        del model_id
        mode = normalize_thinking_mode(request.thinking_mode, thinking=request.thinking)
        effort = reasoning_effort_for_mode(mode)
        return {"extra_body": {"reasoning_effort": effort or "none"}}

    async def complete(self, request: ModelRequest) -> ModelResponse:
        async with get_local_model_resource_broker().lease(
            self._resource_request(request)
        ):
            response = await super().complete(request)
            return response.model_copy(update={"cost_usd": 0.0})

    async def stream(
        self,
        request: ModelRequest,
        *,
        on_final: Callable[[ModelResponse], None] | None = None,
    ) -> AsyncIterator[StreamChunk | str]:
        async with get_local_model_resource_broker().lease(
            self._resource_request(request)
        ):
            async for chunk in super().stream(request, on_final=on_final):
                yield chunk

    def _resource_request(self, request: ModelRequest) -> LocalResourceRequest:
        model_id = (request.model_id or _OLLAMA_DEFAULT_MODEL).strip()
        return LocalResourceRequest(
            workload="local_llm",
            label=f"Ollama · {model_id}",
            memory_class=LocalMemoryClass.HIGH,
            accelerator=True,
            cpu_heavy=True,
            priority=LocalResourcePriority.FOREGROUND,
            timeout_s=self._resource_wait_timeout_s,
        )

    async def list_models(self) -> list[str]:
        import openai

        client = openai.AsyncOpenAI(
            api_key="ollama",
            base_url=self._base_url,
            timeout=10.0,
        )
        try:
            resp = await client.models.list()
            return [model.id for model in resp.data]
        except Exception:
            return []
        finally:
            await close_async_client(client)
