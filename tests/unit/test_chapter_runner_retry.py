"""Tests for ChapterRunner retry behavior on transient provider failures."""

from __future__ import annotations

import asyncio
import json
from typing import Any, cast

import pytest

from novel_forge.core.config import Settings
from novel_forge.core.constants import TaskType
from novel_forge.core.exceptions import ModelGatewayError
from novel_forge.core.response_repair.orchestrator import (
    FormatRepairContext,
    RepairCandidate,
    RepairRisk,
    RepairSource,
    _classify_local_repair_risk,
)
from novel_forge.gateway.router import ModelRouter
from novel_forge.gateway.types import ModelRequest, ModelResponse
from novel_forge.pipeline.chapter_runner import ChapterRunner
from novel_forge.pipeline.long.services.blueprint import outline_helpers as outline_h
from novel_forge.pipeline.long.services.generation import llm_helpers as llm_h
from novel_forge.pipeline.long.services.generation.llm_service import (
    _accept_partial_chapter_contract_repair,
    _retry_needs_more_output_budget,
)
from novel_forge.prompts.builder import PromptBuilder

_VALID_BEATS_ONE = {
    "beats": [{"sequence": 1, "summary": "开场", "tension_level": 3}]
}
_VALID_BEATS_TWO = {
    "beats": [
        {"sequence": 1, "summary": "开场", "tension_level": 3},
        {"sequence": 2, "summary": "转折", "tension_level": 6},
    ]
}
_VALID_BEATS_ONE_JSON = json.dumps(_VALID_BEATS_ONE, ensure_ascii=False)
_VALID_BEATS_TWO_JSON = json.dumps(_VALID_BEATS_TWO, ensure_ascii=False)


def test_stopped_malformed_json_does_not_escalate_output_budget() -> None:
    error = json.JSONDecodeError("Expecting property name", '{"queries": [{', 14)

    assert _retry_needs_more_output_budget(error, finish_reason="stop") is False


def test_unknown_finish_reason_only_escalates_likely_truncated_json() -> None:
    truncated = '{"queries": [{"query": "unfinished"}'
    error = json.JSONDecodeError("Expecting ',' delimiter", truncated, len(truncated))

    assert _retry_needs_more_output_budget(error, finish_reason=None) is True
    assert (
        _retry_needs_more_output_budget(ValueError("schema mismatch"), finish_reason=None) is False
    )


class _StubBuilder:
    def build(
        self,
        task_type: TaskType,
        context: dict[str, Any],
        *,
        max_tokens: int,
        temperature: float,
        top_p: float | None = None,
        prior_messages: list[dict[str, str]] | None = None,
        thinking: bool = False,
        multi_turn: bool = False,
    ) -> ModelRequest:
        return ModelRequest(
            task_type=task_type,
            messages=[{"role": "user", "content": "ping"}],
            max_tokens=max_tokens,
            temperature=temperature,
            top_p=top_p if top_p is not None else 1.0,
            thinking=thinking,
            multi_turn=multi_turn,
        )


class _RecordingBuilder(_StubBuilder):
    def __init__(self) -> None:
        self.contexts: list[dict[str, Any]] = []

    def build(
        self,
        task_type: TaskType,
        context: dict[str, Any],
        *,
        max_tokens: int,
        temperature: float,
        top_p: float | None = None,
        prior_messages: list[dict[str, str]] | None = None,
        thinking: bool = False,
        multi_turn: bool = False,
    ) -> ModelRequest:
        self.contexts.append(dict(context))
        return super().build(
            task_type,
            context,
            max_tokens=max_tokens,
            temperature=temperature,
            top_p=top_p,
            prior_messages=prior_messages,
            thinking=thinking,
            multi_turn=multi_turn,
        )


class _RouterContractStub:
    def resolve_model_id_for_task(
        self,
        task_type: TaskType,
        *,
        provider: str | None = None,
        model_id: str | None = None,
    ) -> str:
        return model_id or "gpt-4o"

    def output_limit_for_task(
        self,
        task_type: TaskType,
        *,
        provider: str | None = None,
        model_id: str | None = None,
    ) -> int:
        return 8192


class _FlakyRouter(_RouterContractStub):
    def __init__(self) -> None:
        self.calls = 0

    async def route(self, request: ModelRequest) -> ModelResponse:
        self.calls += 1
        if self.calls == 1:
            raise ModelGatewayError(
                "InternalServerError: Error code: 500 - {'error': {'message': 'Request timed out, please try again later.', 'type': 'RequestTimeOut'}}",
                is_transient=True,
            )
        return ModelResponse(
            content='{"ok": true}',
            model_id=request.model_id or "mock-model",
            prompt_tokens=1,
            completion_tokens=1,
            total_tokens=2,
            latency_ms=10.0,
            cost_usd=0.0,
        )


class _LengthThenStopRouter:
    def __init__(self) -> None:
        self.calls: list[ModelRequest] = []

    def resolve_model_id_for_task(
        self,
        task_type: TaskType,
        *,
        provider: str | None = None,
        model_id: str | None = None,
    ) -> str:
        return model_id or "gpt-4o"

    def output_limit_for_task(
        self,
        task_type: TaskType,
        *,
        provider: str | None = None,
        model_id: str | None = None,
    ) -> int:
        return 16384

    async def route(
        self,
        request: ModelRequest,
        *,
        provider: str | None = None,
    ) -> ModelResponse:
        self.calls.append(request)
        if len(self.calls) == 1:
            return ModelResponse(
                content=_VALID_BEATS_ONE_JSON,
                model_id="gpt-4o",
                prompt_tokens=1,
                completion_tokens=request.max_tokens,
                total_tokens=request.max_tokens + 1,
                latency_ms=10.0,
                cost_usd=0.0,
                finish_reason="length",
            )
        return ModelResponse(
            content=_VALID_BEATS_TWO_JSON,
            model_id="gpt-4o",
            prompt_tokens=1,
            completion_tokens=128,
            total_tokens=129,
            latency_ms=10.0,
            cost_usd=0.0,
            finish_reason="stop",
        )


class _TextRouter:
    def __init__(self, content: str) -> None:
        self.content = content
        self.calls: list[ModelRequest] = []

    def resolve_model_id_for_task(
        self,
        task_type: TaskType,
        *,
        provider: str | None = None,
        model_id: str | None = None,
    ) -> str:
        return model_id or "gpt-4o"

    def output_limit_for_task(
        self,
        task_type: TaskType,
        *,
        provider: str | None = None,
        model_id: str | None = None,
    ) -> int:
        return 8192

    async def route(
        self,
        request: ModelRequest,
        *,
        provider: str | None = None,
    ) -> ModelResponse:
        self.calls.append(request)
        return ModelResponse(
            content=self.content,
            model_id="gpt-4o",
            prompt_tokens=1,
            completion_tokens=128,
            total_tokens=129,
            latency_ms=10.0,
            cost_usd=0.0,
            finish_reason="stop",
        )

    async def stream_route(
        self,
        request: ModelRequest,
        *,
        provider: str | None = None,
        on_chunk=None,
        on_final=None,
    ):
        self.calls.append(request)
        from novel_forge.gateway.types import StreamChunk

        if on_chunk is not None and self.content:
            on_chunk(StreamChunk(content=self.content))
        response = ModelResponse(
            content=self.content,
            model_id="gpt-4o",
            prompt_tokens=1,
            completion_tokens=128,
            total_tokens=129,
            latency_ms=10.0,
            cost_usd=0.0,
            finish_reason="stop",
        )
        if on_final is not None:
            on_final(response)
        return response


class _ClaimsResponseRouter:
    def __init__(self, contents: list[str]) -> None:
        self.contents = contents
        self.calls: list[ModelRequest] = []

    def resolve_model_id_for_task(
        self,
        task_type: TaskType,
        *,
        provider: str | None = None,
        model_id: str | None = None,
    ) -> str:
        return model_id or "gpt-4o"

    def output_limit_for_task(
        self,
        task_type: TaskType,
        *,
        provider: str | None = None,
        model_id: str | None = None,
    ) -> int:
        return 8192

    async def route(
        self,
        request: ModelRequest,
        *,
        provider: str | None = None,
    ) -> ModelResponse:
        self.calls.append(request)
        index = min(len(self.calls) - 1, len(self.contents) - 1)
        return ModelResponse(
            content=self.contents[index],
            model_id="gpt-4o",
            prompt_tokens=1,
            completion_tokens=128,
            total_tokens=129,
            latency_ms=10.0,
            cost_usd=0.0,
            finish_reason="stop",
        )


def _test_settings(**overrides: Any) -> Settings:
    return Settings.model_validate({"long_book_audit_auto_repair": False, **overrides})


def _init_claim_response_text(
    *,
    evidence: str | None = "玄慧在朱阙前迎辇时重复摩挲腰间铁符。",
    duplicate_evidence: tuple[str, str] | None = None,
) -> str:
    claim_fields = [
        '"claim_id":"outline_1_2_001"',
        '"artifact":"outline"',
        '"source_path":"/chapters/0:5#part1_1_2/chapter_1"',
        '"subject_ids":["char_e48458705aa0"]',
        '"axis":"玄慧人格裂隙状态"',
        '"claim_type":"state"',
        '"claim_text":"玄慧在婚礼上维持沉默"',
        '"state_before":"物品出现引发追问风险"',
        '"state_after":"双方均选择不追问"',
    ]
    if duplicate_evidence is None:
        claim_fields.append(f'"evidence":{json.dumps(evidence, ensure_ascii=False)}')
    else:
        first, second = duplicate_evidence
        claim_fields.extend(
            [
                f'"evidence":{json.dumps(first, ensure_ascii=False)}',
                f'"evidence":{json.dumps(second, ensure_ascii=False)}',
            ]
        )
    claim_fields.extend(
        [
            '"payoff_id":""',
            '"payoff_kind":""',
            '"irreversible":false',
            '"temporality":"actual"',
            '"cognitive_subjects":[]',
            '"cognitive_object":""',
            '"cognitive_level":"unaware"',
            '"action_level":"none"',
            '"reader_awareness":"full"',
            '"character_knowledge_coverage":{}',
            '"cognitive_chapter":null',
            '"public_reveal_chapter":null',
            '"foreshadow_chapters":[]',
            '"confidence":0.9',
            '"metadata":{}',
        ]
    )
    return (
        '{"claims":[{'
        + ",".join(claim_fields)
        + '}],"coverage_status":"complete","unprocessed_source_refs":[],"summary":"ok"}'
    )


def test_call_with_retry_retries_length_marked_json_even_when_parseable(tmp_storage) -> None:
    router = _LengthThenStopRouter()
    events: list[tuple[str, dict[str, Any]]] = []
    runner = ChapterRunner(
        cast(ModelRouter, router),
        cast(PromptBuilder, _StubBuilder()),
        tmp_storage,
        settings=_test_settings(llm_format_retry_attempts=2),
        on_step_progress=lambda step, data: events.append((step, data)),
    )

    result = asyncio.run(
        runner._call_with_retry(
            TaskType.BEATS,
            {},
            max_tokens=1024,
            temperature=0.3,
            required_keys=("beats",),
            max_retries=2,
        )
    )

    assert result == _VALID_BEATS_TWO
    assert [request.max_tokens for request in router.calls] == [8192, 16384]
    assert any(step == "token_budget_normalized" for step, _ in events)
    assert any(step == "token_escalation" for step, _ in events)


def test_call_with_retry_returns_text_only_tasks_without_json_repair(tmp_storage) -> None:
    content = (
        "夜色压低了城门的影子，玄昱停在阶前，没有立刻开口。"
        "他把那枚旧玉扣进掌心，像把一句迟来的承诺重新握紧。"
    )
    router = _TextRouter(content)
    events: list[tuple[str, dict[str, Any]]] = []
    runner = ChapterRunner(
        cast(ModelRouter, router),
        cast(PromptBuilder, _StubBuilder()),
        tmp_storage,
        settings=_test_settings(llm_format_retry_attempts=2),
        on_step_progress=lambda step, data: events.append((step, data)),
    )

    raw_capture: list[str] = []
    result = asyncio.run(
        runner._call_with_retry(
            TaskType.POLISH_CHAPTER,
            {"chapter_text": content},
            max_tokens=1024,
            temperature=0.3,
            max_retries=2,
            _capture_raw=raw_capture,
        )
    )

    assert result == content
    assert raw_capture == [content]
    assert len(router.calls) == 1
    assert not any(step in {"format_retry", "token_escalation"} for step, _ in events)


def test_call_with_retry_repairs_init_claim_duplicate_empty_evidence_locally(
    tmp_storage,
) -> None:
    router = _ClaimsResponseRouter(
        [
            _init_claim_response_text(
                duplicate_evidence=(
                    "玄慧在朱阙前迎辇时重复摩挲腰间铁符。",
                    "",
                )
            )
        ]
    )
    events: list[tuple[str, dict[str, Any]]] = []
    runner = ChapterRunner(
        cast(ModelRouter, router),
        cast(PromptBuilder, _StubBuilder()),
        tmp_storage,
        settings=_test_settings(llm_format_retry_attempts=2),
        on_step_progress=lambda step, data: events.append((step, data)),
    )

    result = asyncio.run(
        runner._call_with_retry(
            TaskType.EXTRACT_INIT_COHERENCE_CLAIMS,
            {},
            max_tokens=256,
            temperature=0.2,
            required_keys=("claims",),
            max_retries=2,
        )
    )

    assert len(router.calls) == 1
    assert result["claims"][0]["evidence"] == "玄慧在朱阙前迎辇时重复摩挲腰间铁符。"
    assert not any(step == "format_retry" for step, _ in events)
    repaired_event = next(data for step, data in events if step == "format_repaired")
    assert repaired_event["repair_strategy"] == "init_claim_duplicate_key_preserve_non_empty"
    assert repaired_event["repair_diagnostics"]["duplicate_key_locations"] == ["claims[0].evidence"]
    assert repaired_event["repair_diagnostics"]["duplicate_key_claim_ids"] == ["outline_1_2_001"]
    assert (
        repaired_event["repair_diagnostics"]["duplicate_key_actions"][0]["action"]
        == "preserved_non_empty"
    )


def test_call_with_retry_fills_claim_transport_metadata_without_model_retry(
    tmp_storage,
) -> None:
    content = _init_claim_response_text().replace(',"metadata":{}', "")
    router = _ClaimsResponseRouter([content])
    events: list[tuple[str, dict[str, Any]]] = []
    runner = ChapterRunner(
        cast(ModelRouter, router),
        cast(PromptBuilder, _StubBuilder()),
        tmp_storage,
        settings=_test_settings(llm_format_retry_attempts=2),
        on_step_progress=lambda step, data: events.append((step, data)),
    )

    result = asyncio.run(
        runner._call_with_retry(
            TaskType.EXTRACT_INIT_COHERENCE_CLAIMS,
            {},
            max_tokens=256,
            temperature=0.2,
            required_keys=("claims",),
            max_retries=2,
        )
    )

    assert len(router.calls) == 1
    assert result["claims"][0]["metadata"] == {}
    assert any(step == "task_output_normalized" for step, _ in events)
    assert not any(step in {"format_retry", "token_escalation"} for step, _ in events)


def test_call_with_retry_routes_conflicting_init_claim_duplicate_key_to_retry(
    tmp_storage,
    monkeypatch,
) -> None:
    async def _no_sleep(_: float) -> None:
        return None

    monkeypatch.setattr("novel_forge.pipeline.long.services.generation.llm_service.asyncio.sleep", _no_sleep)

    router = _ClaimsResponseRouter(
        [
            _init_claim_response_text(
                duplicate_evidence=(
                    "玄慧在朱阙前摩挲铁符。",
                    "沈清漪按住袖中残玉。",
                )
            ),
            _init_claim_response_text(evidence="玄慧在朱阙前摩挲铁符。"),
        ]
    )
    builder = _RecordingBuilder()
    events: list[tuple[str, dict[str, Any]]] = []
    runner = ChapterRunner(
        cast(ModelRouter, router),
        cast(PromptBuilder, builder),
        tmp_storage,
        settings=_test_settings(llm_format_retry_attempts=2),
        on_step_progress=lambda step, data: events.append((step, data)),
    )

    result = asyncio.run(
        runner._call_with_retry(
            TaskType.EXTRACT_INIT_COHERENCE_CLAIMS,
            {},
            max_tokens=256,
            temperature=0.2,
            required_keys=("claims",),
            max_retries=2,
        )
    )

    assert len(router.calls) == 2
    assert result["claims"][0]["evidence"] == "玄慧在朱阙前摩挲铁符。"
    retry_event = next(data for step, data in events if step == "format_retry")
    assert retry_event["error"] == "Duplicate key conflict at claims[0].evidence"
    assert "claims[0].evidence" in builder.contexts[1]["_format_retry_instruction"]
    assert "重复输出 `evidence`" in builder.contexts[1]["_format_retry_instruction"]


def test_call_with_retry_repairs_invalid_claim_semantics_by_exact_path(
    tmp_storage,
    monkeypatch,
) -> None:
    async def _no_sleep(_: float) -> None:
        return None

    monkeypatch.setattr("novel_forge.pipeline.long.services.generation.llm_service.asyncio.sleep", _no_sleep)
    invalid = _init_claim_response_text().replace(
        '"character_knowledge_coverage":{}',
        '"character_knowledge_coverage":{"林小满":"confirmed"}',
    )
    patch = json.dumps(
        {
            "repairs": [
                {
                    "claim_index": 0,
                    "claim_id": "outline_1_2_001",
                    "field": "character_knowledge_coverage",
                    "key": "林小满",
                    "value": "full",
                    "evidence": "Claim 证据表明林小满已完整知情。",
                }
            ]
        },
        ensure_ascii=False,
    )
    router = _ClaimsResponseRouter([invalid, patch])
    builder = _RecordingBuilder()
    events: list[tuple[str, dict[str, Any]]] = []
    runner = ChapterRunner(
        cast(ModelRouter, router),
        cast(PromptBuilder, builder),
        tmp_storage,
        settings=_test_settings(llm_format_retry_attempts=2),
        on_step_progress=lambda step, data: events.append((step, data)),
    )

    result = asyncio.run(
        runner._call_with_retry(
            TaskType.EXTRACT_INIT_COHERENCE_CLAIMS,
            {},
            max_tokens=256,
            temperature=0.2,
            required_keys=("claims",),
            max_retries=2,
        )
    )

    assert len(router.calls) == 2
    assert result["claims"][0]["character_knowledge_coverage"] == {"林小满": "full"}
    assert len(builder.contexts) == 1
    assert router.calls[1].response_schema_name == "init_claim_semantic_patch"
    assert router.calls[1].require_native_structured_output is False
    assert any(step == "claim_semantic_repair_succeeded" for step, _ in events)
    assert not any(step in {"format_retry", "token_escalation"} for step, _ in events)


def test_failed_claim_semantic_patch_reextracts_without_token_escalation(
    tmp_storage,
    monkeypatch,
) -> None:
    async def _no_sleep(_: float) -> None:
        return None

    monkeypatch.setattr("novel_forge.pipeline.long.services.generation.llm_service.asyncio.sleep", _no_sleep)
    valid = _init_claim_response_text()
    invalid = valid.replace(
        '"character_knowledge_coverage":{}',
        '"character_knowledge_coverage":{"林小满":"confirmed"}',
    )
    router = _ClaimsResponseRouter([invalid, '{"repairs":[]}', '{"repairs":[]}', valid])
    builder = _RecordingBuilder()
    events: list[tuple[str, dict[str, Any]]] = []
    runner = ChapterRunner(
        cast(ModelRouter, router),
        cast(PromptBuilder, builder),
        tmp_storage,
        settings=_test_settings(llm_format_retry_attempts=2),
        on_step_progress=lambda step, data: events.append((step, data)),
    )

    result = asyncio.run(
        runner._call_with_retry(
            TaskType.EXTRACT_INIT_COHERENCE_CLAIMS,
            {},
            max_tokens=256,
            temperature=0.2,
            required_keys=("claims",),
            max_retries=2,
        )
    )

    assert len(router.calls) == 4
    assert len(builder.contexts) == 2
    assert result["claims"][0]["character_knowledge_coverage"] == {}
    assert any(step == "claim_semantic_repair_failed" for step, _ in events)
    assert any(step == "format_retry_budget_held" for step, _ in events)
    assert not any(step == "token_escalation" for step, _ in events)
    assert router.calls[0].max_tokens == router.calls[3].max_tokens


def test_invalid_claim_semantic_patch_is_retried_before_full_reextraction(
    tmp_storage,
    monkeypatch,
) -> None:
    async def _no_sleep(_: float) -> None:
        return None

    monkeypatch.setattr("novel_forge.pipeline.long.services.generation.llm_service.asyncio.sleep", _no_sleep)
    invalid = _init_claim_response_text().replace(
        '"character_knowledge_coverage":{}',
        '"character_knowledge_coverage":{"林小满":"confirmed"}',
    )
    invalid_patch = json.dumps(
        {
            "repairs": [
                {
                    "claim_index": 0,
                    "claim_id": "outline_1_2_001",
                    "field": "character_knowledge_coverage",
                    "key": "林小满",
                    "value": "confirmed",
                    "evidence": "错误地复用了认知阶段枚举。",
                }
            ]
        },
        ensure_ascii=False,
    )
    valid_patch = invalid_patch.replace('"value": "confirmed"', '"value": "full"')
    router = _ClaimsResponseRouter([invalid, invalid_patch, valid_patch])
    builder = _RecordingBuilder()
    events: list[tuple[str, dict[str, Any]]] = []
    runner = ChapterRunner(
        cast(ModelRouter, router),
        cast(PromptBuilder, builder),
        tmp_storage,
        settings=_test_settings(llm_format_retry_attempts=2),
        on_step_progress=lambda step, data: events.append((step, data)),
    )

    result = asyncio.run(
        runner._call_with_retry(
            TaskType.EXTRACT_INIT_COHERENCE_CLAIMS,
            {},
            max_tokens=256,
            temperature=0.2,
            required_keys=("claims",),
            max_retries=2,
        )
    )

    assert len(router.calls) == 3
    assert len(builder.contexts) == 1
    assert result["claims"][0]["character_knowledge_coverage"] == {"林小满": "full"}
    assert any(step == "claim_semantic_repair_retry" for step, _ in events)
    assert any(step == "claim_semantic_repair_succeeded" for step, _ in events)
    assert not any(step == "format_retry" for step, _ in events)


def test_call_with_retry_retries_on_transient_provider_timeout(
    tmp_storage,
    monkeypatch,
) -> None:
    async def _no_sleep(_: float) -> None:
        return None

    monkeypatch.setattr("novel_forge.pipeline.long.services.generation.llm_service.asyncio.sleep", _no_sleep)

    flaky_router = _FlakyRouter()
    runner = ChapterRunner(
        cast(ModelRouter, flaky_router),
        cast(PromptBuilder, _StubBuilder()),
        tmp_storage,
        settings=_test_settings(),
    )

    result = asyncio.run(
        runner._call_with_retry(
            TaskType.SPEC_ENRICH,
            {},
            max_tokens=256,
            temperature=0.1,
            required_keys=("ok",),
            max_retries=2,
            include_contract_required_keys=False,
        )
    )

    assert result["ok"] is True
    assert flaky_router.calls == 2


def test_escalate_retry_tokens_never_decreases_on_low_limit_fallback() -> None:
    assert (
        llm_h.escalate_retry_tokens(
            33880,
            TaskType.PLAN_CHAPTER_CONTRACTS.value,
            model_max_tokens=16384,
        )
        == 33880
    )


def test_call_with_retry_rejects_missing_contract_required_keys(tmp_storage) -> None:
    """Missing required keys are format-contract failures, not local defaults."""

    class _ContractRouter(_RouterContractStub):
        def __init__(self) -> None:
            self.calls = 0

        async def route(self, request: ModelRequest) -> ModelResponse:
            self.calls += 1
            return ModelResponse(
                content='{"ok": true}',
                model_id=request.model_id or "mock-model",
                prompt_tokens=1,
                completion_tokens=1,
                total_tokens=2,
                latency_ms=10.0,
                cost_usd=0.0,
            )

    contract_router = _ContractRouter()
    runner = ChapterRunner(
        cast(ModelRouter, contract_router),
        cast(PromptBuilder, _StubBuilder()),
        tmp_storage,
        settings=_test_settings(),
    )

    with pytest.raises(KeyError, match="synopsis"):
        asyncio.run(
            runner._call_with_retry(
                TaskType.PLAN_OUTLINE,
                {},
                max_tokens=256,
                temperature=0.1,
                required_keys=(),
                max_retries=1,
            )
        )

    assert contract_router.calls >= 1


def test_call_with_retry_applies_plan_outline_task_output_adapter(tmp_storage) -> None:
    class _MisnestedBlueprintRouter(_RouterContractStub):
        async def route(self, request: ModelRequest) -> ModelResponse:
            return ModelResponse(
                content=(
                    '{"subplot_plan":[{"name":"陈默的守护与银杏道见证",'
                    '"involved_chapters":[32],"chapter_events":[{'
                    '"chapter_number":32,"source_type":"main_plot",'
                    '"source_ref":"第32章沈知微发现对话日志异常",'
                    '"target_subplot":"陈默的守护与银杏道见证",'
                    '"trigger_chapter":32,"link_type":"create_tension",'
                    '"description":"陈默隐约察觉沈知微的异常，却选择先保护她。"}]}]}'
                ),
                model_id=request.model_id or "mock-model",
                prompt_tokens=1,
                completion_tokens=1,
                total_tokens=2,
                latency_ms=10.0,
                cost_usd=0.0,
            )

    events: list[tuple[str, dict[str, Any]]] = []
    runner = ChapterRunner(
        cast(ModelRouter, _MisnestedBlueprintRouter()),
        cast(PromptBuilder, _StubBuilder()),
        tmp_storage,
        settings=_test_settings(),
        on_step_progress=lambda step, data: events.append((step, data)),
    )

    result = asyncio.run(
        runner._call_with_retry(
            TaskType.PLAN_OUTLINE,
            {"total_chapters": 61},
            max_tokens=256,
            temperature=0.1,
            required_keys=("subplot_plan",),
            max_retries=1,
            include_contract_required_keys=False,
        )
    )

    subplot = result["subplot_plan"][0]
    event = subplot["chapter_events"][0]
    assert event == {
        "chapter_number": 32,
        "event": "陈默隐约察觉沈知微的异常，却选择先保护她。",
    }
    assert subplot["weave_links"][0]["source_type"] == "main_plot"
    assert subplot["weave_links"][0]["link_type"] == "create_tension"
    normalized_event = next(data for step, data in events if step == "task_output_normalized")
    assert normalized_event["task"] == TaskType.PLAN_OUTLINE.value
    assert normalized_event["adapter"] == "plan_outline_blueprint_normalizer"
    assert "subplot_plan" in normalized_event["changed_keys"]


def test_call_with_retry_logs_format_retry_and_injects_retry_prompt(
    tmp_storage,
    monkeypatch,
) -> None:
    async def _no_sleep(_: float) -> None:
        return None

    class _MalformedThenValidRouter(_RouterContractStub):
        def __init__(self) -> None:
            self.calls = 0

        async def route(self, request: ModelRequest) -> ModelResponse:
            self.calls += 1
            if self.calls == 1:
                content = "模型说明：这不是 JSON"
            else:
                content = '{"chapter_contracts": []}'
            return ModelResponse(
                content=content,
                model_id="mock-model",
                prompt_tokens=1,
                completion_tokens=1,
                total_tokens=2,
                latency_ms=10.0,
                cost_usd=0.0,
            )

    monkeypatch.setattr("novel_forge.pipeline.long.services.generation.llm_service.asyncio.sleep", _no_sleep)

    router = _MalformedThenValidRouter()
    builder = _RecordingBuilder()
    events: list[tuple[str, dict[str, Any]]] = []
    runner = ChapterRunner(
        cast(ModelRouter, router),
        cast(PromptBuilder, builder),
        tmp_storage,
        settings=_test_settings(),
        on_step_progress=lambda step, data: events.append((step, data)),
    )

    result = asyncio.run(
        runner._call_with_retry(
            TaskType.PLAN_CHAPTER_CONTRACTS,
            {},
            max_tokens=256,
            temperature=0.3,
            required_keys=("chapter_contracts",),
            max_retries=2,
        )
    )

    assert result == {"chapter_contracts": []}
    assert router.calls == 2
    assert any(step == "format_retry" for step, _ in events)
    retry_event = next(data for step, data in events if step == "format_retry")
    assert retry_event["attempt"] == 1
    assert retry_event["max_attempts"] == 2
    assert retry_event["task"] == TaskType.PLAN_CHAPTER_CONTRACTS.value
    assert retry_event["contract_mode"] == "full_object"
    assert "_format_retry_instruction" in builder.contexts[1]
    assert "契约模式：`full_object`" in builder.contexts[1]["_format_retry_instruction"]
    assert "数组元素" in builder.contexts[1]["_format_retry_instruction"]
    assert "模型说明：这不是 JSON" in builder.contexts[1]["_format_retry_instruction"]
    assert "上一次错误输出原文" in builder.contexts[1]["_format_retry_instruction"]


def test_call_with_retry_logs_schema_issues_for_contract_validation_failure(
    tmp_storage,
    monkeypatch,
) -> None:
    async def _no_sleep(_: float) -> None:
        return None

    class _MissingKeyThenValidRouter(_RouterContractStub):
        def __init__(self) -> None:
            self.calls = 0

        async def route(self, request: ModelRequest) -> ModelResponse:
            self.calls += 1
            content = '{"wrong":[]}' if self.calls == 1 else '{"chapter_contracts":[]}'
            return ModelResponse(
                content=content,
                model_id="mock-model",
                prompt_tokens=1,
                completion_tokens=1,
                total_tokens=2,
                latency_ms=10.0,
                cost_usd=0.0,
            )

    monkeypatch.setattr("novel_forge.pipeline.long.services.generation.llm_service.asyncio.sleep", _no_sleep)

    router = _MissingKeyThenValidRouter()
    builder = _RecordingBuilder()
    events: list[tuple[str, dict[str, Any]]] = []
    runner = ChapterRunner(
        cast(ModelRouter, router),
        cast(PromptBuilder, builder),
        tmp_storage,
        settings=_test_settings(),
        on_step_progress=lambda step, data: events.append((step, data)),
    )

    result = asyncio.run(
        runner._call_with_retry(
            TaskType.PLAN_CHAPTER_CONTRACTS,
            {},
            max_tokens=256,
            temperature=0.3,
            required_keys=("chapter_contracts",),
            max_retries=2,
        )
    )

    retry_event = next(data for step, data in events if step == "format_retry")
    assert result == {"chapter_contracts": []}
    assert router.calls == 2
    assert retry_event["schema_issues"][0]["path"] == "$.chapter_contracts"
    assert retry_event["schema_issues"][0]["issue_type"] == "missing_key"
    assert retry_event["missing_keys"] == ["chapter_contracts"]
    retry_instruction = builder.contexts[1]["_format_retry_instruction"]
    assert "结构化错误定位" in retry_instruction
    assert "path=`$.chapter_contracts`" in retry_instruction
    success_event = next(data for step, data in events if step == "format_validation_success")
    assert success_event["task"] == TaskType.PLAN_CHAPTER_CONTRACTS.value
    assert success_event["parse_source"] == "strict_json"
    assert success_event["schema_issue_count"] == 0


def test_call_with_retry_rejects_lossy_plan_outline_local_repair(
    tmp_storage,
    monkeypatch,
) -> None:
    async def _no_sleep(_: float) -> None:
        return None

    class _TruncatedThenValidRouter(_RouterContractStub):
        def __init__(self) -> None:
            self.calls = 0

        async def route(self, request: ModelRequest) -> ModelResponse:
            self.calls += 1
            if self.calls == 1:
                return ModelResponse(
                    content=(
                        '{"synopsis":"ok","volumes":[],"narrative_phases":[],'
                        '"key_turning_points":[],"subplot_plan":[],'
                        '"chapter_hooks":[{"chapter":1,"expected_hook":{"type":"crisis"'
                    ),
                    finish_reason="length",
                    model_id=request.model_id or "mock-model",
                    prompt_tokens=1,
                    completion_tokens=1,
                    total_tokens=2,
                    latency_ms=10.0,
                    cost_usd=0.0,
                )
            return ModelResponse(
                content=(
                    '{"synopsis":"ok","volumes":[],"narrative_phases":[],'
                    '"key_turning_points":[],"character_arcs":[],"subplot_plan":[],'
                    '"suspense_schedule":[],"ending_strategy":"ok","volume_mode":false,'
                    '"emotional_arcs":[],"causal_chains":[],'
                    '"subplot_collisions":[],"subversion_points":[],'
                    '"chapter_rhythm_curve":[]}'
                ),
                model_id=request.model_id or "mock-model",
                prompt_tokens=1,
                completion_tokens=1,
                total_tokens=2,
                latency_ms=10.0,
                cost_usd=0.0,
            )

    monkeypatch.setattr("novel_forge.pipeline.long.services.generation.llm_service.asyncio.sleep", _no_sleep)

    events: list[tuple[str, dict[str, Any]]] = []
    router = _TruncatedThenValidRouter()
    runner = ChapterRunner(
        cast(ModelRouter, router),
        cast(PromptBuilder, _StubBuilder()),
        tmp_storage,
        settings=_test_settings(),
        on_step_progress=lambda step, data: events.append((step, data)),
    )

    result = asyncio.run(
        runner._call_with_retry(
            TaskType.PLAN_OUTLINE,
            {},
            max_tokens=256,
            temperature=0.1,
            required_keys=(),
            max_retries=2,
        )
    )

    assert router.calls == 2
    assert result["synopsis"] == "ok"
    assert "chapter_hooks" not in result
    assert any(step == "format_retry" for step, _ in events)
    assert not any(step == "format_repaired" for step, _ in events)


def test_call_with_retry_requests_llm_semantics_instead_of_local_backfill(
    tmp_storage,
    monkeypatch,
) -> None:
    async def _no_sleep(_: float) -> None:
        return None

    chapter = {
        "chapter_number": 4,
        "title": "裂缝之外",
        "goal": "查明裂缝扩散的代价。",
        "beats_summary": ["林远发现裂缝扩大。"],
        "main_plot_points": ["裂缝威胁小镇。"],
        "subplot_points": [],
        "subplot_focus": "",
        "element_focus": ["时间裂缝"],
        "pov_character_id": "char_lin_yuan",
        "pov_character_name": "林远",
        "pov_character": "林远",
        "pov_switch": False,
        "setting": "小镇东侧废墟",
        "expected_word_count": 3000,
        "involved_character_ids": ["char_lin_yuan"],
        "required_character_ids": ["char_lin_yuan"],
        "support_character_ids": [],
        "involved_character_names": ["林远"],
        "involved_characters": ["林远"],
        "cast_plan": {
            "pov_entity_id": "char_lin_yuan",
            "required_character_ids": ["char_lin_yuan"],
            "support_character_ids": [],
            "mention_only_entity_ids": [],
            "forbidden_active_character_ids": [],
        },
        "emotional_plan": {
            "subject_entity_id": "char_lin_yuan",
            "entry_state": "警惕",
            "pressure_source": "裂缝继续扩大",
            "relationship_choice": "是否相信守夜人",
            "turning_emotion": "从戒备转为有限合作",
            "exit_aftertaste": "合作仍有隐患",
            "expression_channels": ["action"],
        },
        "scene_design_goals": ["确认裂缝规模", "迫使林远作出合作选择"],
        "notes": "",
        "expected_hook": {
            "hook_type": "mystery",
            "hook_strength": "strong",
            "hook_description": "裂缝中出现熟悉的人影。",
        },
        "expected_payoffs": [{"payoff_type": "clue", "description": "守夜人的警告得到证实。"}],
    }

    class _MissingSemanticsThenValidRouter(_RouterContractStub):
        def __init__(self) -> None:
            self.calls = 0

        async def route(self, request: ModelRequest) -> ModelResponse:
            self.calls += 1
            response_chapter = dict(chapter)
            if self.calls == 1:
                response_chapter["goal"] = ""
                response_chapter["expected_payoffs"] = []
            return ModelResponse(
                content=json.dumps({"chapters": [response_chapter]}, ensure_ascii=False),
                model_id=request.model_id or "mock-model",
                prompt_tokens=1,
                completion_tokens=1,
                total_tokens=2,
                latency_ms=10.0,
                cost_usd=0.0,
            )

    monkeypatch.setattr("novel_forge.pipeline.long.services.generation.llm_service.asyncio.sleep", _no_sleep)
    router = _MissingSemanticsThenValidRouter()
    builder = _RecordingBuilder()
    events: list[tuple[str, dict[str, Any]]] = []
    runner = ChapterRunner(
        cast(ModelRouter, router),
        cast(PromptBuilder, builder),
        tmp_storage,
        settings=_test_settings(),
        on_step_progress=lambda step, data: events.append((step, data)),
    )

    result = asyncio.run(
        runner._call_with_retry(
            TaskType.PLAN_OUTLINE_BATCH,
            {},
            max_tokens=1024,
            temperature=0.3,
            max_retries=2,
        )
    )

    assert router.calls == 2
    assert result["chapters"][0]["goal"] == chapter["goal"]
    retry_event = next(data for step, data in events if step == "format_retry")
    issue_paths = {issue["path"] for issue in retry_event["schema_issues"]}
    assert "$.chapters[0].goal" in issue_paths
    assert "$.chapters[0].expected_payoffs" in issue_paths
    assert "missing_semantics" in builder.contexts[1]["_format_retry_instruction"]


def test_plan_outline_nested_chapter_numbers_do_not_mark_repair_unsafe() -> None:
    risk = _classify_local_repair_risk(
        context=FormatRepairContext(
            task_type=TaskType.PLAN_OUTLINE,
            raw_content=(
                '{"synopsis":"ok","key_turning_points":[{"chapter_number":12,"event":"turn"}]}'
            ),
            error=None,
        ),
        data={
            "synopsis": "ok",
            "key_turning_points": [{"chapter_number": 12, "event": "turn"}],
        },
    )

    assert risk == RepairRisk.SAFE


def test_call_with_retry_retries_entity_registry_nested_schema(
    tmp_storage,
    monkeypatch,
) -> None:
    async def _no_sleep(_: float) -> None:
        return None

    class _MissingEntityIdThenValidRouter(_RouterContractStub):
        def __init__(self) -> None:
            self.calls = 0

        async def route(self, request: ModelRequest) -> ModelResponse:
            self.calls += 1
            if self.calls == 1:
                content = '{"entities":[{"name":"医学论文场","entity_type":"concept"}]}'
            else:
                content = (
                    '{"entities":[{"entity_id":"concept_medical_journal",'
                    '"name":"医学论文场","entity_type":"concept"}]}'
                )
            return ModelResponse(
                content=content,
                model_id="mock-model",
                prompt_tokens=1,
                completion_tokens=1,
                total_tokens=2,
                latency_ms=10.0,
                cost_usd=0.0,
            )

    monkeypatch.setattr("novel_forge.pipeline.long.services.generation.llm_service.asyncio.sleep", _no_sleep)

    router = _MissingEntityIdThenValidRouter()
    builder = _RecordingBuilder()
    events: list[tuple[str, dict[str, Any]]] = []
    runner = ChapterRunner(
        cast(ModelRouter, router),
        cast(PromptBuilder, builder),
        tmp_storage,
        settings=_test_settings(),
        on_step_progress=lambda step, data: events.append((step, data)),
    )

    result = asyncio.run(
        runner._call_with_retry(
            TaskType.INIT_ENTITY_REGISTRY,
            {},
            max_tokens=256,
            temperature=0.2,
            required_keys=("entities",),
            max_retries=2,
        )
    )

    assert router.calls == 2
    assert result["entities"][0]["entity_id"] == "concept_medical_journal"
    retry_event = next(data for step, data in events if step == "format_retry")
    assert retry_event["task"] == TaskType.INIT_ENTITY_REGISTRY.value
    assert "entity_id" in builder.contexts[1]["_format_retry_instruction"]


def test_call_with_retry_normalizes_entity_registry_dynamic_key(tmp_storage) -> None:
    class _DynamicEntityKeyRouter(_RouterContractStub):
        async def route(self, request: ModelRequest) -> ModelResponse:
            return ModelResponse(
                content=(
                    '{"entities":[{"item_acupuncture_needle":"银针",'
                    '"entity_type":"item","aliases":[],"source":"init",'
                    '"notes":"沈知微施针工具"}]}'
                ),
                model_id="mock-model",
                prompt_tokens=1,
                completion_tokens=1,
                total_tokens=2,
                latency_ms=10.0,
                cost_usd=0.0,
            )

    events: list[tuple[str, dict[str, Any]]] = []
    runner = ChapterRunner(
        cast(ModelRouter, _DynamicEntityKeyRouter()),
        cast(PromptBuilder, _StubBuilder()),
        tmp_storage,
        settings=_test_settings(),
        on_step_progress=lambda step, data: events.append((step, data)),
    )

    result = asyncio.run(
        runner._call_with_retry(
            TaskType.INIT_ENTITY_REGISTRY,
            {},
            max_tokens=256,
            temperature=0.2,
            required_keys=("entities",),
            max_retries=2,
        )
    )

    assert result["entities"] == [
        {
            "entity_id": "item_acupuncture_needle",
            "name": "银针",
            "entity_type": "item",
            "aliases": [],
            "source": "init",
            "notes": "沈知微施针工具",
        }
    ]
    assert not any(step == "format_retry" for step, _ in events)


def test_call_with_retry_logs_locally_repaired_format_errors(tmp_storage) -> None:
    class _RepairableRouter(_RouterContractStub):
        def __init__(self) -> None:
            self.calls = 0

        async def route(self, request: ModelRequest) -> ModelResponse:
            self.calls += 1
            return ModelResponse(
                content=(
                    '{"chapter_contracts":[{"chapter_number":22,'
                    '"entry_state_requirements":["陆云峥痛苦撕裂"\n'
                    '"沈念卿保持距离"],'
                    '"required_events":[],"allowed_changes":[]}]}'
                ),
                model_id="mock-model",
                prompt_tokens=1,
                completion_tokens=1,
                total_tokens=2,
                latency_ms=10.0,
                cost_usd=0.0,
            )

    router = _RepairableRouter()
    events: list[tuple[str, dict[str, Any]]] = []
    runner = ChapterRunner(
        cast(ModelRouter, router),
        cast(PromptBuilder, _StubBuilder()),
        tmp_storage,
        settings=_test_settings(),
        on_step_progress=lambda step, data: events.append((step, data)),
    )

    result = asyncio.run(
        runner._call_with_retry(
            TaskType.PLAN_CHAPTER_CONTRACTS,
            {},
            max_tokens=256,
            temperature=0.3,
            required_keys=("chapter_contracts",),
            max_retries=2,
        )
    )

    assert router.calls == 1
    assert result["chapter_contracts"][0]["entry_state_requirements"] == [
        "陆云峥痛苦撕裂",
        "沈念卿保持距离",
    ]
    repaired_event = next(data for step, data in events if step == "format_repaired")
    assert repaired_event["repaired"] is True
    assert repaired_event["repair_action"] == "local_parse_repair"
    assert repaired_event["attempt"] == 1
    assert repaired_event["task"] == TaskType.PLAN_CHAPTER_CONTRACTS.value


def test_partial_chapter_contract_repair_is_accepted_only_with_batch_scaffold() -> None:
    candidate = RepairCandidate(
        data={
            "chapter_contracts": [
                {"chapter_number": 5, "title": "指尖记忆"},
            ]
        },
        source=RepairSource.LOCAL,
        strategy="safe_parse_json",
        risk=RepairRisk.LOSSY,
    )
    context = {
        "outline": {
            "chapters": [{"chapter_number": 5}, {"chapter_number": 6}],
            "contract_scaffold": [{"chapter_number": 5}, {"chapter_number": 6}],
        }
    }

    assert _accept_partial_chapter_contract_repair(
        candidate,
        current_context=context,
        finish_reason="stop",
    )
    assert candidate.data["coverage"]["local_fallback_accepted"] is True
    assert candidate.data["coverage"]["partial_format_repair_accepted"] is True
    assert not _accept_partial_chapter_contract_repair(
        candidate,
        current_context=context,
        finish_reason="length",
    )
    assert not _accept_partial_chapter_contract_repair(
        candidate,
        current_context={"outline": {"chapters": [{"chapter_number": 5}]}},
        finish_reason="stop",
    )


def test_length_chapter_contract_repair_is_accepted_when_expected_batch_is_complete() -> None:
    candidate = RepairCandidate(
        data={
            "chapter_contracts": [
                {"chapter_number": 34, "title": "暗流涌动"},
                {"chapter_number": 35, "title": "证据交锋"},
                {"chapter_number": 36, "title": "钟楼钥匙"},
                {"chapter_number": 37, "title": "信任危机"},
                {"chapter_number": 38, "title": "批次外多余章节"},
            ]
        },
        source=RepairSource.LOCAL,
        strategy="safe_parse_json",
        risk=RepairRisk.LOSSY,
    )
    context = {
        "outline": {
            "contract_batch": {"chapter_numbers": [34, 35, 36, 37]},
            "chapters": [
                {"chapter_number": 34},
                {"chapter_number": 35},
                {"chapter_number": 36},
                {"chapter_number": 37},
            ],
            "contract_scaffold": [
                {"chapter_number": 34},
                {"chapter_number": 35},
                {"chapter_number": 36},
                {"chapter_number": 37},
            ],
        }
    }

    assert _accept_partial_chapter_contract_repair(
        candidate,
        current_context=context,
        finish_reason="length",
    )
    assert candidate.data["coverage"]["local_fallback_accepted"] is True
    assert candidate.data["coverage"]["partial_format_repair_accepted"] is True


def test_length_chapter_contract_repair_rejects_missing_expected_batch_row() -> None:
    candidate = RepairCandidate(
        data={
            "chapter_contracts": [
                {"chapter_number": 34, "title": "暗流涌动"},
                {"chapter_number": 35, "title": "证据交锋"},
                {"chapter_number": 36, "title": "钟楼钥匙"},
            ]
        },
        source=RepairSource.LOCAL,
        strategy="safe_parse_json",
        risk=RepairRisk.LOSSY,
    )
    context = {
        "outline": {
            "contract_batch": {"chapter_numbers": [34, 35, 36, 37]},
            "chapters": [
                {"chapter_number": 34},
                {"chapter_number": 35},
                {"chapter_number": 36},
                {"chapter_number": 37},
            ],
            "contract_scaffold": [
                {"chapter_number": 34},
                {"chapter_number": 35},
                {"chapter_number": 36},
                {"chapter_number": 37},
            ],
        }
    }

    assert not _accept_partial_chapter_contract_repair(
        candidate,
        current_context=context,
        finish_reason="length",
    )


def test_call_with_retry_uses_dedicated_llm_format_repair_on_final_failure(
    tmp_storage,
) -> None:
    class _DedicatedRepairRouter(_RouterContractStub):
        def __init__(self) -> None:
            self.calls = 0
            self.repair_prompt = ""

        async def route(self, request: ModelRequest) -> ModelResponse:
            self.calls += 1
            if self.calls == 1:
                return ModelResponse(
                    content='{"beatzz":["开场","转折"]}',
                    model_id=request.model_id or "mock-model",
                    prompt_tokens=1,
                    completion_tokens=1,
                    total_tokens=2,
                    latency_ms=10.0,
                    cost_usd=0.0,
                )
            self.repair_prompt = request.messages[-1]["content"]
            return ModelResponse(
                content=_VALID_BEATS_TWO_JSON,
                model_id=request.model_id or "mock-model",
                prompt_tokens=1,
                completion_tokens=1,
                total_tokens=2,
                latency_ms=10.0,
                cost_usd=0.0,
            )

    router = _DedicatedRepairRouter()
    events: list[tuple[str, dict[str, Any]]] = []
    runner = ChapterRunner(
        cast(ModelRouter, router),
        cast(PromptBuilder, _StubBuilder()),
        tmp_storage,
        settings=_test_settings(
            llm_format_retry_attempts=1,
            llm_format_repair_enabled=True,
        ),
        on_step_progress=lambda step, data: events.append((step, data)),
    )

    result = asyncio.run(
        runner._call_with_retry(
            TaskType.BEATS,
            {},
            max_tokens=256,
            temperature=0.3,
            required_keys=("beats",),
            max_retries=1,
        )
    )

    assert result == _VALID_BEATS_TWO
    assert router.calls == 2
    assert '"beatzz"' in router.repair_prompt
    assert any(step == "format_repair_strategy_miss" for step, _ in events)
    repaired_event = next(data for step, data in events if step == "format_repaired")
    assert repaired_event["repair_action"] == "llm_format_repair"
    assert repaired_event["repair_source"] == "llm_repair"


def test_call_with_retry_does_not_fabricate_json_from_blank_response(tmp_storage) -> None:
    class _BlankRouter(_RouterContractStub):
        def __init__(self) -> None:
            self.calls = 0

        async def route(self, request: ModelRequest) -> ModelResponse:
            self.calls += 1
            return ModelResponse(
                content="\n\n",
                finish_reason="abort",
                model_id=request.model_id or "mock-model",
                prompt_tokens=1,
                completion_tokens=1,
                total_tokens=2,
                latency_ms=10.0,
                cost_usd=0.0,
            )

    router = _BlankRouter()
    events: list[tuple[str, dict[str, Any]]] = []
    runner = ChapterRunner(
        cast(ModelRouter, router),
        cast(PromptBuilder, _StubBuilder()),
        tmp_storage,
        settings=_test_settings(
            llm_format_retry_attempts=1,
            llm_format_repair_enabled=True,
        ),
        on_step_progress=lambda step, data: events.append((step, data)),
    )

    with pytest.raises(json.JSONDecodeError):
        asyncio.run(
            runner._call_with_retry(
                TaskType.BEATS,
                {},
                max_tokens=256,
                temperature=0.3,
                required_keys=("beats",),
                max_retries=1,
            )
        )

    assert router.calls == 1
    assert any(
        step == "format_repair_skipped" and data.get("reason") == "empty_or_unstructured_source"
        for step, data in events
    )
    assert not any(step == "format_repair_strategy_miss" for step, _data in events)


def test_dedicated_llm_format_repair_keeps_escalated_token_budget(tmp_storage) -> None:
    class _RecordingRepairRouter(_RouterContractStub):
        def __init__(self) -> None:
            self.calls = 0
            self.repair_max_tokens = 0

        async def route(self, request: ModelRequest) -> ModelResponse:
            self.calls += 1
            if self.calls == 1:
                return ModelResponse(
                    content='{"beatzz":["开场"]}',
                    model_id=request.model_id or "gpt-4o",
                    prompt_tokens=1,
                    completion_tokens=1,
                    total_tokens=2,
                    latency_ms=10.0,
                    cost_usd=0.0,
                )
            self.repair_max_tokens = request.max_tokens
            return ModelResponse(
                content=_VALID_BEATS_ONE_JSON,
                model_id=request.model_id or "mock-model",
                prompt_tokens=1,
                completion_tokens=1,
                total_tokens=2,
                latency_ms=10.0,
                cost_usd=0.0,
            )

    router = _RecordingRepairRouter()
    runner = ChapterRunner(
        cast(ModelRouter, router),
        cast(PromptBuilder, _StubBuilder()),
        tmp_storage,
        settings=_test_settings(
            llm_format_retry_attempts=1,
            llm_format_repair_enabled=True,
            llm_format_repair_max_tokens=512,
        ),
    )

    result = asyncio.run(
        runner._call_with_retry(
            TaskType.BEATS,
            {},
            max_tokens=8192,
            temperature=0.3,
            required_keys=("beats",),
            max_retries=1,
        )
    )

    assert result == _VALID_BEATS_ONE
    assert router.repair_max_tokens == 8192


def test_dedicated_llm_format_repair_escalates_when_repair_output_is_truncated(
    tmp_storage,
    monkeypatch,
) -> None:
    async def _no_sleep(_: float) -> None:
        return None

    class _TruncatedRepairRouter(_RouterContractStub):
        def __init__(self) -> None:
            self.calls = 0
            self.repair_max_tokens: list[int] = []

        async def route(self, request: ModelRequest) -> ModelResponse:
            self.calls += 1
            if self.calls == 1:
                return ModelResponse(
                    content='{"beatzz":["开场"]}',
                    model_id=request.model_id or "mock-model",
                    prompt_tokens=1,
                    completion_tokens=1,
                    total_tokens=2,
                    latency_ms=10.0,
                    cost_usd=0.0,
                )

            self.repair_max_tokens.append(request.max_tokens)
            if self.calls == 2:
                return ModelResponse(
                    content='{"beats":[{"sequence":1,"summary":"开场"',
                    finish_reason="length",
                    model_id=request.model_id or "gpt-4o",
                    prompt_tokens=1,
                    completion_tokens=1,
                    total_tokens=2,
                    latency_ms=10.0,
                    cost_usd=0.0,
                )
            return ModelResponse(
                content=_VALID_BEATS_ONE_JSON,
                model_id=request.model_id or "gpt-4o",
                prompt_tokens=1,
                completion_tokens=1,
                total_tokens=2,
                latency_ms=10.0,
                cost_usd=0.0,
            )

    monkeypatch.setattr("novel_forge.pipeline.long.services.generation.llm_service.asyncio.sleep", _no_sleep)

    router = _TruncatedRepairRouter()
    events: list[tuple[str, dict[str, Any]]] = []
    runner = ChapterRunner(
        cast(ModelRouter, router),
        cast(PromptBuilder, _StubBuilder()),
        tmp_storage,
        settings=_test_settings(
            llm_format_retry_attempts=1,
            llm_format_repair_enabled=True,
            llm_format_repair_max_tokens=512,
        ),
        on_step_progress=lambda step, data: events.append((step, data)),
    )

    result = asyncio.run(
        runner._call_with_retry(
            TaskType.BEATS,
            {},
            max_tokens=256,
            temperature=0.3,
            required_keys=("beats",),
            max_retries=1,
        )
    )

    assert result == _VALID_BEATS_ONE
    assert router.repair_max_tokens == [8192, 16384]
    escalation_event = next(
        data
        for step, data in events
        if step == "token_escalation" and data.get("repair_action") == "llm_format_repair"
    )
    assert escalation_event["old_max_tokens"] == 8192
    assert escalation_event["new_max_tokens"] == 16384


def test_call_with_retry_retries_truncated_contract_repair(tmp_storage, monkeypatch) -> None:
    async def _no_sleep(_: float) -> None:
        return None

    class _TruncatedThenValidRouter(_RouterContractStub):
        def __init__(self) -> None:
            self.calls = 0

        async def route(self, request: ModelRequest) -> ModelResponse:
            self.calls += 1
            if self.calls == 1:
                return ModelResponse(
                    content='{"chapter_contracts":[{"chapter_number":1,"title":"逆光重逢"}',
                    finish_reason="length",
                    model_id="mock-model",
                    prompt_tokens=1,
                    completion_tokens=1,
                    total_tokens=2,
                    latency_ms=10.0,
                    cost_usd=0.0,
                )
            return ModelResponse(
                content='{"chapter_contracts":[{"chapter_number":1,"title":"逆光重逢"}]}',
                model_id="mock-model",
                prompt_tokens=1,
                completion_tokens=1,
                total_tokens=2,
                latency_ms=10.0,
                cost_usd=0.0,
            )

    monkeypatch.setattr("novel_forge.pipeline.long.services.generation.llm_service.asyncio.sleep", _no_sleep)

    router = _TruncatedThenValidRouter()
    events: list[tuple[str, dict[str, Any]]] = []
    runner = ChapterRunner(
        cast(ModelRouter, router),
        cast(PromptBuilder, _StubBuilder()),
        tmp_storage,
        settings=_test_settings(),
        on_step_progress=lambda step, data: events.append((step, data)),
    )

    result = asyncio.run(
        runner._call_with_retry(
            TaskType.PLAN_CHAPTER_CONTRACTS,
            {},
            max_tokens=256,
            temperature=0.3,
            required_keys=("chapter_contracts",),
            max_retries=2,
        )
    )

    assert router.calls == 2
    assert result["chapter_contracts"][0]["title"] == "逆光重逢"
    assert any(step == "format_retry" for step, _ in events)
    assert not any(step == "format_repaired" for step, _ in events)


def test_contract_coherence_missing_fields_trigger_llm_retry(tmp_storage) -> None:
    class _MissingSummaryRouter(_RouterContractStub):
        def __init__(self) -> None:
            self.calls = 0

        async def route(self, request: ModelRequest) -> ModelResponse:
            self.calls += 1
            if self.calls == 1:
                content = '{"verdict":"reject","issues":[{"severity":"high","description":"冲突"}]}'
            else:
                content = json.dumps(
                    {
                        "verdict": "reject",
                        "issues": [{"severity": "high", "description": "冲突"}],
                        "source_refs": ["chapter_contracts:1"],
                        "repair_scope": [{"artifact": "chapter_contracts", "chapters": [1]}],
                        "preserve": ["其余章节契约"],
                        "change_intent": "修复第一章契约冲突。",
                        "blocked": True,
                        "summary": "第一章契约存在高风险冲突。",
                    },
                    ensure_ascii=False,
                )
            return ModelResponse(
                content=content,
                model_id="mock-model",
                prompt_tokens=1,
                completion_tokens=1,
                total_tokens=2,
                latency_ms=10.0,
                cost_usd=0.0,
            )

    router = _MissingSummaryRouter()
    runner = ChapterRunner(
        cast(ModelRouter, router),
        cast(PromptBuilder, _StubBuilder()),
        tmp_storage,
        settings=_test_settings(),
    )

    result = asyncio.run(
        runner._call_with_retry(
            TaskType.ADJUDICATE_CONTRACT_COHERENCE,
            {},
            max_tokens=256,
            temperature=0.1,
            required_keys=("verdict", "issues", "summary"),
            max_retries=2,
        )
    )

    assert router.calls == 2
    assert result["summary"] == "第一章契约存在高风险冲突。"
    assert result["blocked"] is True


def test_call_with_retry_aborts_on_severely_damaged_extract_payload(tmp_storage) -> None:
    class _DamagedExtractRouter(_RouterContractStub):
        async def route(self, request: ModelRequest) -> ModelResponse:
            return ModelResponse(
                content=(
                    "preface "
                    '{"canon_delta":{},"creative_report":{},"chapter_exit_state":{}} '
                    'broken_tail "relationship_deltas":[],"plot_thread_deltas":[],'
                    '"structured_summary":"摘要"'
                ),
                model_id=request.model_id or "mock-model",
                prompt_tokens=1,
                completion_tokens=1,
                total_tokens=2,
                latency_ms=10.0,
                cost_usd=0.0,
            )

    runner = ChapterRunner(
        cast(ModelRouter, _DamagedExtractRouter()),
        cast(PromptBuilder, _StubBuilder()),
        tmp_storage,
        settings=_test_settings(
            extract_canon_abort_on_severe_damage=True,
            extract_canon_severe_damage_missing_section_threshold=2,
            llm_format_repair_enabled=False,
        ),
    )

    with pytest.raises(ValueError, match="Severely damaged EXTRACT_CANON payload"):
        asyncio.run(
            runner._call_with_retry(
                TaskType.EXTRACT_CANON,
                {},
                max_tokens=256,
                temperature=0.1,
                required_keys=("canon_delta", "creative_report", "chapter_exit_state"),
                max_retries=1,
            )
        )


def test_extract_canon_damage_detector_accepts_nested_repairable_sections() -> None:
    raw = (
        '{"canon_delta":{},"creative_report":{},'
        '"chapter_exit_state":{"character_state_deltas":[],"relationship_deltas":[],'
        '"plot_thread_deltas":[],"structured_summary":"摘要"}}'
    )
    data = {
        "canon_delta": {},
        "creative_report": {},
        "chapter_exit_state": {
            "character_state_deltas": [],
            "relationship_deltas": [],
            "plot_thread_deltas": [],
            "structured_summary": "摘要",
        },
    }

    assessment = llm_h.assess_extract_canon_response_damage(raw, data, finish_reason="stop")

    assert assessment.is_severe is False
    assert assessment.missing_optional_sections == ()
    assert assessment.raw_but_missing_sections == ()


def test_build_outline_batches_respects_non_contiguous_gaps() -> None:
    batches = outline_h.build_outline_batches([2, 3, 4, 5, 27, 28], batch_size=5)
    assert batches == [(2, 5), (27, 28)]


def test_parse_csv_items_normalizes_case_and_whitespace() -> None:
    parsed = outline_h.parse_csv_items(" tongyi, DeepSeek , ,qwen ")
    assert parsed == {"tongyi", "deepseek", "qwen"}


def test_is_target_allowed_respects_provider_and_model_allowlists() -> None:
    assert outline_h.is_target_allowed(
        "tongyi",
        "qwen-max",
        allowed_providers={"tongyi", "deepseek"},
        allowed_models=set(),
    )
    assert not outline_h.is_target_allowed(
        "kimi",
        "moonshot-v1-8k",
        allowed_providers={"tongyi", "deepseek"},
        allowed_models=set(),
    )
    assert outline_h.is_target_allowed(
        "tongyi",
        "qwen3-30b-a3b",
        allowed_providers={"tongyi"},
        allowed_models={"qwen3-30b-a3b", "qwen3-235b-a22b"},
    )
    assert not outline_h.is_target_allowed(
        "tongyi",
        "qwen-max",
        allowed_providers={"tongyi"},
        allowed_models={"qwen3-30b-a3b", "qwen3-235b-a22b"},
    )
