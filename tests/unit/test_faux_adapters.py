"""Demonstration tests for the scriptable FauxAdapter / FauxTTSAdapter mocks.

These tests exist both as regression coverage for the faux helpers themselves
and as a reference for the deterministic multi-turn mock pattern they enable.
They replace the hand-rolled ``_SequenceRouter`` / ``_FlakyRouter`` /
``AsyncMock(side_effect=...)`` stubs scattered across the unit suite with a
single declarative ``FauxStep`` list.

See :mod:`tests.helpers.faux_adapter` and :mod:`tests.helpers.faux_tts_adapter`.
"""

from __future__ import annotations

import pytest

from novel_forge.core.constants import TaskType
from novel_forge.core.exceptions import AuthenticationError, RateLimitError
from novel_forge.gateway.router import ModelRouter
from novel_forge.gateway.types import ModelRequest, ModelResponse, StreamChunk
from novel_forge.tts.schemas import TTSProvider, TTSRequest
from tests.helpers.faux_adapter import FauxAdapter, FauxStep
from tests.helpers.faux_tts_adapter import FauxTTSAdapter, FauxTTSStep


def _request(*, task_type: TaskType = TaskType.DRAFT_CHAPTER) -> ModelRequest:
    return ModelRequest(
        task_type=task_type,
        messages=[{"role": "user", "content": "写一段"}],
        max_tokens=1024,
        temperature=0.7,
    )


# ─── FauxAdapter (LLM) ───────────────────────────────────────────────────────


async def test_faux_adapter_returns_scripted_responses_in_order(
    faux_adapter: type[FauxAdapter],
) -> None:
    """The Nth complete() call returns the Nth scripted step."""
    adapter = faux_adapter(
        [
            FauxStep(response=ModelResponse(content="第一次")),
            FauxStep(response=ModelResponse(content="第二次")),
        ]
    )

    first = await adapter.complete(_request())
    second = await adapter.complete(_request())

    assert first.content == "第一次"
    assert second.content == "第二次"
    assert len(adapter.calls) == 2


async def test_faux_adapter_injects_error_then_recovers(
    faux_adapter: type[FauxAdapter],
) -> None:
    """A RateLimitError on call 1, then a success on call 2."""
    adapter = faux_adapter(
        [
            FauxStep(error=RateLimitError("429 too many requests")),
            FauxStep(response=ModelResponse(content="recovered")),
        ]
    )

    with pytest.raises(RateLimitError):
        await adapter.complete(_request())
    recovered = await adapter.complete(_request())

    assert recovered.content == "recovered"


async def test_faux_adapter_raises_when_script_exhausted(
    faux_adapter: type[FauxAdapter],
) -> None:
    """Past the last scripted step, FauxAdapter raises (catches under-mocking)."""
    adapter = faux_adapter([FauxStep(response=ModelResponse(content="only"))])

    await adapter.complete(_request())
    with pytest.raises(RuntimeError, match="exhausted at call 1"):
        await adapter.complete(_request())


async def test_faux_adapter_streams_scripted_content(
    faux_adapter: type[FauxAdapter],
) -> None:
    """stream() honours the same script and finalises via on_final."""
    adapter = faux_adapter([FauxStep(response=ModelResponse(content="流式正文"))])
    captured: list[ModelResponse] = []

    chunks = [
        c
        async for c in adapter.stream(
            _request(),
            on_final=captured.append,
        )
    ]

    assert len(chunks) == 1
    assert chunks[0].content == "流式正文"
    assert captured and captured[-1].content == "流式正文"


async def test_faux_adapter_works_through_real_router(
    faux_adapter: type[FauxAdapter],
) -> None:
    """A FauxAdapter wrapped in a real ModelRouter drives route() as expected."""
    adapter = faux_adapter(
        [FauxStep(response=ModelResponse(content="via router", model_id="faux-model"))]
    )
    router = ModelRouter(adapters={"faux": adapter}, default_provider="faux")

    response = await router.route(_request())

    assert response.content == "via router"


# ─── FauxTTSAdapter ──────────────────────────────────────────────────────────


async def test_faux_tts_adapter_fails_once_then_falls_back(
    faux_tts_adapter: type[FauxTTSAdapter],
) -> None:
    """One RateLimitError, then the inherited mock-audio happy path."""
    adapter = faux_tts_adapter(
        synthesize_steps=[FauxTTSStep(error=RateLimitError("provider rate limit"))],
        latency_ms=0,
        fallback_after_exhausted=True,
    )
    req = TTSRequest(text="你好", provider=TTSProvider.MOCK)

    with pytest.raises(RateLimitError):
        await adapter.synthesize(req)
    recovered = await adapter.synthesize(req)

    assert recovered.audio_data  # inherited MockTTSAdapter produced real frames
    assert len(adapter.synthesize_calls) == 2


async def test_faux_tts_adapter_strict_exhaustion_raises(
    faux_tts_adapter: type[FauxTTSAdapter],
) -> None:
    """Without fallback_after_exhausted, over-calling a scripted channel raises."""
    adapter = faux_tts_adapter(
        synthesize_steps=[FauxTTSStep()],
        latency_ms=0,
    )
    req = TTSRequest(text="你好", provider=TTSProvider.MOCK)

    await adapter.synthesize(req)  # scripted success
    with pytest.raises(RuntimeError, match="channel exhausted"):
        await adapter.synthesize(req)  # past the script


async def test_faux_tts_adapter_unscripted_channel_uses_inherited_path(
    faux_tts_adapter: type[FauxTTSAdapter],
) -> None:
    """A channel with no script delegates to MockTTSAdapter's happy path."""
    adapter = faux_tts_adapter(latency_ms=0)
    req = TTSRequest(text="你好世界", provider=TTSProvider.MOCK)

    response = await adapter.synthesize(req)

    assert response.audio_data
    assert response.duration_ms >= 500
    assert len(adapter.synthesize_calls) == 1


async def test_faux_tts_adapter_injects_non_retriable_error(
    faux_tts_adapter: type[FauxTTSAdapter],
) -> None:
    """AuthenticationError is injected deterministically (no randomness)."""
    adapter = faux_tts_adapter(
        synthesize_steps=[
            FauxTTSStep(error=AuthenticationError("invalid api key")),
            FauxTTSStep(error=AuthenticationError("invalid api key")),
        ],
        latency_ms=0,
        fallback_after_exhausted=True,
    )
    req = TTSRequest(text="你好", provider=TTSProvider.MOCK)

    with pytest.raises(AuthenticationError):
        await adapter.synthesize(req)
    with pytest.raises(AuthenticationError):
        await adapter.synthesize(req)
    assert len(adapter.synthesize_calls) == 2


# ─── Extended features ─────────────────────────────────────────────────────────


async def test_faux_adapter_streams_multi_chunk(
    faux_adapter: type[FauxAdapter],
) -> None:
    """FauxStep.chunks yields each StreamChunk individually (interleaved stream)."""
    adapter = faux_adapter(
        [
            FauxStep(
                response=ModelResponse(content="full"),
                chunks=[
                    StreamChunk(content="reasoning", reasoning="thinking..."),
                    StreamChunk(content="answer"),
                ],
            )
        ]
    )

    chunks = [c async for c in adapter.stream(_request())]

    assert len(chunks) == 2
    assert chunks[0].reasoning == "thinking..."
    assert chunks[1].content == "answer"


async def test_faux_adapter_health_check_default_healthy(
    faux_adapter: type[FauxAdapter],
) -> None:
    """health_check() returns True by default."""
    adapter = faux_adapter([])
    assert await adapter.health_check() is True


async def test_faux_adapter_health_check_unhealthy(
    faux_adapter: type[FauxAdapter],
) -> None:
    """health_check() returns False when healthy=False."""
    adapter = faux_adapter([], healthy=False)
    assert await adapter.health_check() is False
