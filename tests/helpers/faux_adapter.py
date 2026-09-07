"""Scriptable multi-turn mock LLM adapter for deterministic test sequences.

``FauxAdapter`` consumes an ordered list of :class:`FauxStep` responses, one
per ``complete()`` call. Each step may return a canned :class:`ModelResponse`,
raise a canned exception (for retry/failover tests), or both after an optional
delay. All received requests are recorded in ``calls`` for assertions.

This replaces the ~40 hand-rolled ``_SequenceRouter`` / ``_FlakyRouter`` /
``_FailingAdapter`` stubs scattered across ``tests/unit/`` that each
re-implement "pop the next response from a list, maybe raise". Use it for any
test that needs to drive the router/step through an ordered, deterministic
response sequence with error injection.

Unlike the production :class:`~novel_forge.gateway.adapters.mock.MockAdapter`
(which is stateless and dispatches purely on ``task_type``), ``FauxAdapter``
is call-sequence-sensitive: the Nth ``complete()`` returns the Nth step.

Example::

    adapter = FauxAdapter([
        FauxStep(error=RateLimitError("429", is_transient=True)),
        FauxStep(response=ModelResponse(content="recovered")),
    ])
    router = ModelRouter(adapters={"faux": adapter}, default_provider="faux")
    # first call raises RateLimitError; second returns "recovered"
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field

from novel_forge.gateway.base import ProviderAdapter
from novel_forge.gateway.types import ModelRequest, ModelResponse, StreamChunk


@dataclass
class FauxStep:
    """One scripted response in a FauxAdapter sequence.

    Exactly one of ``response`` / ``error`` should be set. If both are set the
    error takes precedence (raised before the response is returned). ``delay``
    simulates network latency and is applied before either branch.

    ``chunks`` (optional): when set and the step is consumed via
    :meth:`FauxAdapter.stream`, these :class:`StreamChunk` objects are yielded
    one-by-one instead of the single-chunk default derived from ``response``.
    Use this to test multi-chunk interleaved streaming (e.g. reasoning +
    content chunks).
    """

    response: ModelResponse | None = None
    error: Exception | None = None
    delay: float = 0.0
    chunks: list[StreamChunk] | None = None


@dataclass
class FauxAdapter(ProviderAdapter):
    """Scriptable multi-turn LLM mock consuming an ordered ``FauxStep`` list.

    Attributes
    ----------
    calls:
        Every :class:`ModelRequest` received by ``complete()`` / ``stream()``,
        in arrival order. Use this to assert call counts and inspect inputs.
    exhausted_error:
        Exception class raised when a call exceeds the scripted sequence.
        Defaults to :class:`RuntimeError`; override for stricter tests.
    """

    steps: list[FauxStep]
    name: str = "faux"
    exhausted_error: type[Exception] = RuntimeError
    healthy: bool = True
    calls: list[ModelRequest] = field(default_factory=list)
    _index: int = field(default=0, repr=False)

    @property
    def provider_name(self) -> str:
        return self.name

    @property
    def default_model(self) -> str | None:
        return "faux-model"

    async def health_check(self) -> bool:
        """Return the scripted ``healthy`` flag (defaults to ``True``)."""
        return self.healthy

    def _next_step(self, request: ModelRequest) -> FauxStep:
        self.calls.append(request)
        if self._index >= len(self.steps):
            raise self.exhausted_error(
                f"FauxAdapter({self.name}) exhausted at call {self._index} "
                f"(scripted {len(self.steps)} step(s))"
            )
        step = self.steps[self._index]
        self._index += 1
        return step

    async def complete(self, request: ModelRequest) -> ModelResponse:
        step = self._next_step(request)
        if step.delay:
            await asyncio.sleep(step.delay)
        if step.error is not None:
            raise step.error
        if step.response is None:
            # Allow a bare FauxStep() to mean "empty success".
            return ModelResponse(content="")
        return step.response

    async def stream(
        self,
        request: ModelRequest,
        *,
        on_final: Callable[[ModelResponse], None] | None = None,
    ) -> AsyncIterator[StreamChunk | str]:
        """Scripted streaming: yields chunks from the step, or one default chunk.

        When the step's ``chunks`` field is set, each :class:`StreamChunk` is
        yielded individually (supporting multi-chunk / reasoning+content
        interleaved tests). Otherwise the step's ``response.content`` is
        yielded as a single chunk (the legacy behaviour).

        Honours ``error`` (raised before yielding) and ``delay`` like
        :meth:`complete`. A step with no ``response`` yields nothing and
        finalises an empty response.
        """
        step = self._next_step(request)
        if step.delay:
            await asyncio.sleep(step.delay)
        if step.error is not None:
            raise step.error
        if step.chunks is not None:
            for chunk in step.chunks:
                yield chunk
        else:
            content = step.response.content if step.response is not None else ""
            if content:
                yield StreamChunk(content=content)
        final = step.response if step.response is not None else ModelResponse(content="")
        self._last_stream_response = final
        if on_final is not None:
            on_final(final)
