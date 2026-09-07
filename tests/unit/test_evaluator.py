"""Tests for DraftEvaluator structured-output fallback behavior."""

from __future__ import annotations

import pytest

from novel_forge.core.config import Settings
from novel_forge.core.constants import TaskType
from novel_forge.core.exceptions import ModelGatewayError
from novel_forge.eval.evaluator import DraftEvaluator
from novel_forge.gateway.types import ModelRequest, ModelResponse


class _FakeBuilder:
    def build(
        self,
        task_type: TaskType,
        context: dict[str, str],
        *,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        top_p: float | None = None,
        thinking: bool = False,
        multi_turn: bool = False,
        prior_messages: list[dict[str, str]] | None = None,
    ) -> ModelRequest:
        messages = [{"role": "user", "content": context.get("draft_text", "")}]
        if prior_messages:
            messages = prior_messages + messages
        return ModelRequest(
            task_type=task_type,
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
            thinking=thinking,
            multi_turn=multi_turn,
        )


class _SequencedRouter:
    def __init__(self, responses: list[ModelResponse]) -> None:
        self._responses = list(responses)
        self.calls: list[tuple[ModelRequest, str | None]] = []

    async def route(
        self,
        request: ModelRequest,
        *,
        provider: str | None = None,
    ) -> ModelResponse:
        self.calls.append((request, provider))
        return self._responses.pop(0)


class _UnavailableRouter:
    async def route(self, request: ModelRequest, *, provider: str | None = None) -> ModelResponse:
        del request, provider
        raise ModelGatewayError(
            "all route attempts failed",
            is_transient=True,
            failure_categories=["timeout", "circuit_open"],
        )


class _MisconfiguredRouter:
    async def route(self, request: ModelRequest, *, provider: str | None = None) -> ModelResponse:
        del request, provider
        raise ModelGatewayError("authentication failed", is_transient=False)


@pytest.mark.asyncio
async def test_draft_evaluator_marks_gateway_failure_as_untrusted_fallback() -> None:
    evaluator = DraftEvaluator(
        _UnavailableRouter(),  # type: ignore[arg-type]
        _FakeBuilder(),  # type: ignore[arg-type]
        settings=Settings(_env_file=None),
    )

    report = await evaluator.evaluate("测试正文")

    assert report.is_fallback is True
    assert report.evaluation_status == "fallback"
    assert report.fallback_reason == "model_gateway_unavailable"
    assert report.score_confidence == "fallback"
    assert report.passed is False


@pytest.mark.asyncio
async def test_draft_evaluator_does_not_hide_non_transient_gateway_failure() -> None:
    evaluator = DraftEvaluator(
        _MisconfiguredRouter(),  # type: ignore[arg-type]
        _FakeBuilder(),  # type: ignore[arg-type]
        settings=Settings(_env_file=None),
    )

    with pytest.raises(ModelGatewayError, match="authentication failed"):
        await evaluator.evaluate("测试正文")


@pytest.mark.asyncio
async def test_draft_evaluator_retries_tencent_thinking_parse_failures_with_instruct() -> None:
    router = _SequencedRouter(
        [
            ModelResponse(
                content="<think>这是一段被截断的思考，没有最终 JSON",
                thinking_content="",
                model_id="hunyuan-2.0-thinking-20251109",
            ),
            ModelResponse(
                content="generic retry also returns non-JSON",
                model_id="hunyuan-2.0-thinking-20251109",
            ),
            ModelResponse(
                content=(
                    '{"scores":[{"dimension":"coherence","score":8.0,"comment":"A满足/B满足/C满足/D满足/E满足"},'
                    '{"dimension":"style","score":8.5,"comment":"A满足/B满足/C满足/D满足/E满足/F满足"},'
                    '{"dimension":"engagement","score":8.0,"comment":"A满足/B满足/C满足/D满足/E满足/F满足"},'
                    '{"dimension":"tech_density","score":7.5,"comment":"A满足/B满足/C满足/D满足"}],'
                    '"overall_score":8.0,"passed":true,"threshold":6.0,'
                    '"summary":"整体完成度高。","repair_suggestions":[]}'
                ),
                model_id="hunyuan-2.0-instruct-20251111",
            ),
        ]
    )
    evaluator = DraftEvaluator(
        router,  # type: ignore[arg-type]
        _FakeBuilder(),  # type: ignore[arg-type]
        settings=Settings(_env_file=None),
    )

    report = await evaluator.evaluate("测试正文")

    assert report.summary == "整体完成度高。"
    assert report.passed is True
    assert [score.dimension for score in report.scores] == [
        "coherence",
        "style",
        "engagement",
        "tech_density",
    ]
    assert len(router.calls) == 3
    tencent_request, tencent_provider = router.calls[2]
    assert tencent_provider == "tencent"
    assert tencent_request.model_id == "hunyuan-2.0-instruct-20251111"
    assert tencent_request.thinking is False
    assert tencent_request.temperature == 0.0


@pytest.mark.asyncio
async def test_draft_evaluator_parses_valid_json_response() -> None:
    valid_json = (
        '{"scores":['
        '{"dimension":"consistency","score":8.0,"comment":"A-E均满足"},'
        '{"dimension":"continuity","score":7.5,"comment":"A-D满足"},'
        '{"dimension":"character","score":8.0,"comment":"A-D均满足"},'
        '{"dimension":"style","score":8.5,"comment":"A-F均满足"},'
        '{"dimension":"engagement","score":7.0,"comment":"A-E均满足"},'
        '{"dimension":"pacing","score":8.0,"comment":"A-D均满足"}'
        '],"overall_score":7.8,"passed":true,"threshold":6.0,'
        '"summary":"整体质量良好。","repair_suggestions":[]}'
    )
    router = _SequencedRouter([
        ModelResponse(content=valid_json, model_id="minimax-m2"),
    ])
    evaluator = DraftEvaluator(
        router,  # type: ignore[arg-type]
        _FakeBuilder(),  # type: ignore[arg-type]
        settings=Settings(_env_file=None),
    )

    report = await evaluator.evaluate("测试正文")

    assert report.summary == "整体质量良好。"
    assert report.passed is True
    assert report.overall_score == pytest.approx(7.83, rel=1e-2)
    assert len(report.scores) == 6
    assert [s.dimension for s in report.scores] == [
        "consistency", "continuity", "character", "style", "engagement", "pacing",
    ]
    assert len(router.calls) == 1


@pytest.mark.asyncio
async def test_draft_evaluator_generic_retry_on_parse_failure() -> None:
    router = _SequencedRouter([
        ModelResponse(
            content="这是一段无法解析的纯文本",
            model_id="minimax-m2",
        ),
        ModelResponse(
            content=(
                '{"scores":['
                '{"dimension":"consistency","score":8.0,"comment":"OK"},'
                '{"dimension":"continuity","score":7.0,"comment":"OK"},'
                '{"dimension":"character","score":8.0,"comment":"OK"},'
                '{"dimension":"style","score":7.5,"comment":"OK"},'
                '{"dimension":"engagement","score":8.0,"comment":"OK"},'
                '{"dimension":"pacing","score":7.0,"comment":"OK"}'
                '],"overall_score":7.6,"passed":true,"threshold":6.0,'
                '"summary":"重试后成功。","repair_suggestions":[]}'
            ),
            model_id="minimax-m2",
        ),
    ])
    evaluator = DraftEvaluator(
        router,  # type: ignore[arg-type]
        _FakeBuilder(),  # type: ignore[arg-type]
        settings=Settings(_env_file=None),
    )

    report = await evaluator.evaluate("测试正文")

    assert report.summary == "重试后成功。"
    assert report.passed is True
    assert len(router.calls) == 2
    retry_request, _ = router.calls[1]
    assert retry_request.temperature == 0.0
    assert "只输出纯JSON文本" in retry_request.messages[-1]["content"]
