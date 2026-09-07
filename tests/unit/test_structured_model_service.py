from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from novel_forge.common.constants import TaskType
from novel_forge.gateway.types import ModelRequest, ModelResponse
from novel_forge.model_runtime import StructuredModelService


class _Builder:
    def __init__(self) -> None:
        self.max_tokens: list[int] = []

    def build(self, task_type: TaskType, context: dict[str, Any], **kwargs: Any) -> ModelRequest:
        del context
        self.max_tokens.append(int(kwargs["max_tokens"]))
        return ModelRequest(
            task_type=task_type,
            messages=[{"role": "user", "content": "label"}],
            max_tokens=kwargs["max_tokens"],
            temperature=kwargs["temperature"],
        )


class _Router:
    def __init__(self, responses: list[ModelResponse]) -> None:
        self.responses = list(responses)
        self.requests: list[ModelRequest] = []

    async def route(self, request: ModelRequest, **_kwargs: Any) -> ModelResponse:
        self.requests.append(request)
        return self.responses.pop(0)


async def test_structured_service_validates_core_contract() -> None:
    router = _Router(
        [
            ModelResponse(
                content=(
                    '{"emotions":[{"segment_index":1,"emotion":"happy",'
                    '"sub_emotion":null,"intensity":0.8}]}'
                )
            )
        ]
    )
    service = StructuredModelService(
        router=router,  # type: ignore[arg-type]
        builder=_Builder(),  # type: ignore[arg-type]
        settings=SimpleNamespace(
            long_streaming_json_observation_enabled=False,
            llm_format_retry_attempts=1,
        ),
    )

    result = await service.call_with_retry(
        TaskType.TTS_EMOTION_LABEL,
        {"stage_cards": {}},
        required_keys=("emotions",),
        max_retries=1,
    )

    assert result["emotions"][0]["emotion"] == "happy"
    assert len(router.requests) == 1


async def test_structured_service_retries_truncated_json_with_more_tokens() -> None:
    router = _Router(
        [
            ModelResponse(content='{"emotions":[', finish_reason="length", model_id="mock"),
            ModelResponse(
                content=(
                    '{"emotions":[{"segment_index":2,"emotion":"calm",'
                    '"sub_emotion":null,"intensity":0.4}]}'
                )
            ),
        ]
    )
    builder = _Builder()
    service = StructuredModelService(
        router=router,  # type: ignore[arg-type]
        builder=builder,  # type: ignore[arg-type]
        settings=SimpleNamespace(
            long_streaming_json_observation_enabled=False,
            llm_format_retry_attempts=2,
        ),
    )

    result = await service.call_with_retry(
        TaskType.TTS_EMOTION_LABEL,
        {"stage_cards": {}},
        max_tokens=128,
        required_keys=("emotions",),
        max_retries=2,
    )

    assert result["emotions"][0]["segment_index"] == 2
    assert builder.max_tokens == [128, 256]
