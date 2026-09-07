from __future__ import annotations

import pytest

from novel_forge.common.constants import TaskType
from novel_forge.core.exceptions import ModelGatewayError, RateLimitError
from novel_forge.gateway import text_retry
from novel_forge.gateway.text_retry import call_text_with_retry
from novel_forge.gateway.types import ModelRequest, ModelResponse


class _SequenceRouter:
    def __init__(self, *items: ModelResponse | Exception) -> None:
        self._items = list(items)
        self.max_tokens: list[int] = []

    async def route(self, request: ModelRequest) -> ModelResponse:
        self.max_tokens.append(request.max_tokens)
        item = self._items.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class _WrappedTransientRouter:
    def __init__(self) -> None:
        self.max_tokens: list[int] = []

    async def route(self, request: ModelRequest) -> ModelResponse:
        self.max_tokens.append(request.max_tokens)
        if len(self.max_tokens) == 1:
            try:
                raise RateLimitError("429 too many requests")
            except RateLimitError as exc:
                raise ModelGatewayError("all route attempts failed") from exc
        return ModelResponse(content="完整正文", finish_reason="stop", model_id="")


def _request(
    *,
    task_type: TaskType = TaskType.POLISH_CHAPTER,
    max_tokens: int = 1024,
) -> ModelRequest:
    return ModelRequest(
        task_type=task_type,
        messages=[{"role": "user", "content": "请返回正文"}],
        max_tokens=max_tokens,
        temperature=0.2,
    )


@pytest.fixture(autouse=True)
def _no_retry_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(text_retry, "compute_retry_backoff", lambda _attempt: 0.0)


async def test_call_text_with_retry_escalates_length_then_returns_complete() -> None:
    router = _SequenceRouter(
        ModelResponse(content="半截正文", finish_reason="length", model_id=""),
        ModelResponse(content="完整正文", finish_reason="stop", model_id=""),
    )

    response = await call_text_with_retry(router, _request(max_tokens=1024))

    assert response.content == "完整正文"
    assert router.max_tokens == [1024, 2048]


async def test_call_text_with_retry_rejects_truncation_at_token_cap() -> None:
    router = _SequenceRouter(ModelResponse(content="半截正文", finish_reason="length", model_id=""))

    with pytest.raises(ValueError, match="模型输出被截断.*max_tokens=8192"):
        await call_text_with_retry(router, _request(max_tokens=8192))

    assert router.max_tokens == [8192]


async def test_call_text_with_retry_rejects_truncation_on_final_attempt() -> None:
    router = _SequenceRouter(
        ModelResponse(content="半截正文1", finish_reason="length", model_id=""),
        ModelResponse(content="半截正文2", finish_reason="length", model_id=""),
        ModelResponse(content="半截正文3", finish_reason="length", model_id=""),
    )

    with pytest.raises(ValueError, match="模型输出被截断.*attempt=3/3"):
        await call_text_with_retry(
            router,
            _request(task_type=TaskType.ADJUST_OUTLINE, max_tokens=1024),
            max_retries=2,
        )

    assert router.max_tokens == [1024, 2048, 4096]


async def test_call_text_with_retry_retries_wrapped_transient_error() -> None:
    router = _WrappedTransientRouter()

    response = await call_text_with_retry(router, _request(max_tokens=1024))

    assert response.content == "完整正文"
    assert router.max_tokens == [1024, 1024]


async def test_call_text_with_retry_rejects_empty_non_truncated_response() -> None:
    router = _SequenceRouter(ModelResponse(content="", finish_reason="stop", model_id=""))

    with pytest.raises(ValueError, match="Empty response"):
        await call_text_with_retry(router, _request(max_tokens=1024))

    assert router.max_tokens == [1024]
