"""Request / response types for the model gateway."""

from __future__ import annotations

try:
    from typing_extensions import NotRequired, TypedDict
except ImportError:
    from typing import TypedDict

    from typing_extensions import NotRequired

from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, Field

from novel_forge.common.constants import TaskType


class Message(TypedDict):
    """OpenAI-style message structure."""

    role: str
    content: str
    name: NotRequired[str | None]
    tool_calls: NotRequired[list[dict[str, Any]] | None]


class ModelRequest(BaseModel):
    """A single request to an LLM provider."""

    task_type: TaskType
    messages: list[Message] = Field(
        description="OpenAI-style messages: [{'role': 'system', 'content': ...}, ...]"
    )
    max_tokens: int = Field(default=4096, ge=1)
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    temperature_jitter_allowed: bool = Field(
        default=True,
        description="Whether router-level creative temperature jitter may alter this request.",
    )
    top_p: float = Field(default=1.0, ge=0.0, le=1.0)
    model_id: str | None = Field(
        default=None,
        description="Override model selection (normally set by Router).",
    )
    provider_id: str = Field(
        default="",
        description="Resolved provider/profile route id set by Router for cache identity.",
    )
    thinking: bool = Field(
        default=False,
        description=(
            "Enable extended thinking/reasoning mode. "
            "Adapter will inject provider-specific parameters and strip "
            "<think> blocks (Tongyi) or capture reasoning_content (DeepSeek) "
            "so that ModelResponse.content always contains only the final answer."
        ),
    )
    thinking_mode: str = Field(
        default="",
        description=(
            "Provider-neutral reasoning selection. Supported normalized values include "
            "off, on, adaptive, minimal, low, medium, high, xhigh, max, and forced. "
            "When empty, the legacy thinking boolean is used."
        ),
    )
    multi_turn: bool = Field(
        default=False,
        description=(
            "Enable task-level multi-turn mode. "
            "This flag is reserved as a capability switch so pipeline modules "
            "can decide whether to inject prior conversation context."
        ),
    )
    response_json_schema: dict[str, Any] | None = Field(
        default=None,
        description="Optional provider-native JSON Schema for structured output.",
    )
    response_schema_name: str = Field(
        default="",
        description="Stable schema name used by providers that support structured output.",
    )
    response_schema_strict: bool = Field(
        default=True,
        description="Whether provider-native structured output should request strict validation.",
    )
    require_native_structured_output: bool = Field(
        default=False,
        description=(
            "Reject prompt-only routes for this request. Used for strict, large JSON "
            "artifacts that must be protected by a provider-native JSON mode."
        ),
    )
    output_language: str = Field(
        default="zh",
        description="Requested output language tag, e.g. zh, zh-Hant, en-US.",
    )
    prompt_locale: str = Field(
        default="zh",
        description="Prompt pack locale used to render the request, e.g. zh, en, ja.",
    )
    prompt_pack_version: str = Field(
        default="",
        description="Prompt pack source revision or version used for this request.",
    )


class ModelResponse(BaseModel):
    """Standardized response from any provider."""

    content: str = Field(description="Generated text (thinking blocks already removed).")
    finish_reason: str = Field(
        default="stop",
        description=(
            "Why the model stopped generating. "
            "'stop' = natural end, 'length' = hit max_tokens limit."
        ),
    )
    thinking_content: str = Field(
        default="",
        description=(
            "Raw thinking/reasoning text stripped from the response. "
            "For DeepSeek-R1 this is reasoning_content; "
            "for Tongyi it is the text inside <think>…</think> tags."
        ),
    )
    model_id: str = Field(default="", description="Actual model used.")
    prompt_tokens: int = Field(default=0, ge=0)
    completion_tokens: int = Field(default=0, ge=0)
    total_tokens: int = Field(default=0, ge=0)
    latency_ms: float = Field(default=0.0, ge=0.0)
    cost_usd: float = Field(default=0.0, ge=0.0)
    structured_output_mode: str = Field(
        default="",
        description="Resolved provider structured-output mode used for this call.",
    )
    structured_output_downgraded_from: str = Field(
        default="",
        description="Previous structured-output mode when the call downgraded.",
    )
    structured_output_reason: str = Field(
        default="",
        description="Policy/runtime reason for the structured-output mode.",
    )
    requested_max_tokens: int = Field(
        default=0,
        ge=0,
        description="Completion budget supplied by the original caller before router adaptation.",
    )
    effective_max_tokens: int = Field(
        default=0,
        ge=0,
        description="Completion budget used by the successful provider attempt.",
    )
    length_retry_count: int = Field(
        default=0,
        ge=0,
        description="Number of router-level output-length expansions before this response.",
    )

    @property
    def tokens(self) -> int:
        return self.total_tokens or (self.prompt_tokens + self.completion_tokens)


@dataclass(frozen=True)
class StreamChunk:
    """One streaming delta from a provider adapter.

    A chunk may carry content (final answer text), reasoning (thinking /
    chain-of-thought text), or both.  Adapters that do not support reasoning
    leave ``reasoning`` empty.  The ``content`` field is always the clean
    answer text — thinking blocks (e.g. ``<think>…</think>``) are stripped
    before being placed here and exposed via ``reasoning`` instead.

    ``reset`` is an ordered transport boundary.  It tells observers that
    content/reasoning already emitted for the current logical stream belongs
    to a failed provider attempt and must be discarded before later chunks
    are appended.  This keeps live previews truthful when an adapter retries
    internally or the router changes to a fallback route.

    The ordering of chunks yielded by :meth:`ProviderAdapter.stream` is
    significant: it reflects the real interleaving of reasoning and content
    as produced by the model.  Downstream consumers (router → llm_service →
    desktop observation) preserve this order so the UI can render a faithful
    interleaved view.
    """

    content: str = ""
    reasoning: str = ""
    reset: bool = False
    reset_reason: str = ""

    def is_empty(self) -> bool:
        """Return True if this chunk carries neither text nor a reset boundary."""
        return not (self.content or self.reasoning or self.reset)


def to_stream_chunk(chunk: StreamChunk | str) -> StreamChunk:
    """Normalize a streamed item to :class:`StreamChunk`.

    Adapters and test fakes may yield either ``StreamChunk`` or plain ``str``
    (treated as content-only).  This helper lets the router/llm_service handle
    both uniformly without isinstance branching at every call site.
    """
    if isinstance(chunk, StreamChunk):
        return chunk
    if isinstance(chunk, str):
        return StreamChunk(content=chunk)
    raise TypeError(f"Unsupported stream chunk type: {type(chunk)!r}")
