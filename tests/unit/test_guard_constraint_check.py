from __future__ import annotations

import asyncio
from types import SimpleNamespace

from novel_forge.common.constants import TaskType
from novel_forge.gateway.types import ModelRequest, ModelResponse
from novel_forge.pipeline.long.stages import quality_checks_runner as qc
from novel_forge.pipeline.long.stages.quality_checks_runner import (
    _evaluate_single_constraint_compliance,
)


class _Builder:
    def build(
        self,
        task_type: TaskType,
        context: dict,
        *,
        max_tokens: int,
        temperature: float,
        prior_messages=None,
        thinking: bool = False,
        multi_turn: bool = False,
    ) -> ModelRequest:
        return ModelRequest(
            task_type=task_type,
            messages=[{"role": "user", "content": "check"}],
            max_tokens=max_tokens,
            temperature=temperature,
            thinking=thinking,
            multi_turn=multi_turn,
        )


class _Router:
    def __init__(self) -> None:
        self.calls = 0

    async def route(self, request: ModelRequest) -> ModelResponse:
        self.calls += 1
        if self.calls == 1:
            content = '{"status":"partial","confidence":0.4,"evidence":"片段"}'
        else:
            content = '{"status":"compliant","confidence":0.82,"evidence":"片段","notes":"已遵守"}'
        return ModelResponse(
            content=content,
            model_id=request.model_id or "mock-model",
            prompt_tokens=10,
            completion_tokens=10,
            total_tokens=20,
            latency_ms=1.0,
            cost_usd=0.0,
        )


def test_guard_constraint_check_retries_after_missing_required_key() -> None:
    runner = SimpleNamespace(_builder=_Builder(), _router=_Router())

    result = asyncio.run(
        _evaluate_single_constraint_compliance(
            runner,
            constraint="必须保留案件线索",
            chapter_text="正文片段",
            chapter_number=5,
        )
    )

    assert runner._router.calls == 2
    assert result["status"] == "compliant"
    assert result["notes"] == "已遵守"


class _LengthThenValidRouter:
    def __init__(self) -> None:
        self.calls = 0
        self.max_tokens: list[int] = []

    async def route(self, request: ModelRequest) -> ModelResponse:
        self.calls += 1
        self.max_tokens.append(request.max_tokens)
        if self.calls <= 2:
            return ModelResponse(
                content="",
                finish_reason="length",
                model_id="mock-model",
                prompt_tokens=10,
                completion_tokens=request.max_tokens,
                total_tokens=10 + request.max_tokens,
                latency_ms=1.0,
                cost_usd=0.0,
            )
        return ModelResponse(
            content='{"status":"compliant","confidence":0.9,"evidence":"片段","notes":"已遵守"}',
            model_id="mock-model",
            prompt_tokens=10,
            completion_tokens=10,
            total_tokens=20,
            latency_ms=1.0,
            cost_usd=0.0,
        )


def test_guard_constraint_check_escalates_tokens_after_empty_length_response() -> None:
    runner = SimpleNamespace(_builder=_Builder(), _router=_LengthThenValidRouter())

    result = asyncio.run(
        _evaluate_single_constraint_compliance(
            runner,
            constraint="必须保留案件线索",
            chapter_text="正文片段",
            chapter_number=5,
        )
    )

    assert runner._router.calls == 3
    assert runner._router.max_tokens[0] >= 1024
    assert runner._router.max_tokens[1] == runner._router.max_tokens[0]
    assert runner._router.max_tokens[2] > runner._router.max_tokens[0]
    assert result["status"] == "compliant"
    assert result["check_error"] is False


class _AlwaysLengthRouter:
    async def route(self, request: ModelRequest) -> ModelResponse:
        return ModelResponse(
            content="",
            finish_reason="length",
            model_id="mock-model",
            prompt_tokens=10,
            completion_tokens=request.max_tokens,
            total_tokens=10 + request.max_tokens,
            latency_ms=1.0,
            cost_usd=0.0,
        )


def test_guard_constraint_check_marks_final_parse_failure_non_repairable() -> None:
    runner = SimpleNamespace(_builder=_Builder(), _router=_AlwaysLengthRouter())

    result = asyncio.run(
        _evaluate_single_constraint_compliance(
            runner,
            constraint="必须保留案件线索",
            chapter_text="正文片段",
            chapter_number=5,
        )
    )

    assert result["status"] == "check_error"
    assert result["check_error"] is True
    assert result["repairable"] is False


def test_guard_compliance_report_excludes_check_failures_from_rate(monkeypatch) -> None:
    async def _fake_evaluate(**_kwargs):
        return {
            "constraint": "必须保留案件线索",
            "status": "check_error",
            "confidence": 0.0,
            "evidence": "",
            "notes": "检查过程出错",
            "check_error": True,
            "repairable": False,
        }

    monkeypatch.setattr(qc, "_evaluate_single_constraint_compliance", _fake_evaluate)
    packet = SimpleNamespace(guard_constraints=["必须保留案件线索", "必须回应悬念"])

    report = asyncio.run(
        qc.check_guard_constraint_compliance(
            runner=SimpleNamespace(),
            bundle=SimpleNamespace(),
            packet=packet,
            current_text="正文片段",
            chapter_number=5,
        )
    )

    assert report["overall_compliance_rate"] is None
    assert report["checked_count"] == 0
    assert report["check_failed_count"] == 2
    assert report["actionable_violation_count"] == 0
    assert "检查失败" in report["summary"]


def test_guard_compliance_includes_chapter_contract_forbidden_changes(monkeypatch) -> None:
    async def _fail_evaluate(**_kwargs):
        raise AssertionError("local forbidden-appearance guard should short-circuit LLM")

    monkeypatch.setattr(qc, "_evaluate_single_constraint_compliance", _fail_evaluate)
    packet = SimpleNamespace(
        guard_constraints=[],
        chapter_contract={"forbidden_changes": ["禁止沈鹤卿在本章出场"]},
    )

    report = asyncio.run(
        qc.check_guard_constraint_compliance(
            runner=SimpleNamespace(),
            bundle=SimpleNamespace(),
            packet=packet,
            current_text="敲门声响起。\n\n是外公沈鹤卿的声音。",
            chapter_number=2,
        )
    )

    assert qc.guard_constraints_available(packet) is True
    assert report["constraints"] == ["禁止沈鹤卿在本章出场"]
    assert report["checked_count"] == 1
    assert report["actionable_violation_count"] == 1
    result = report["compliance_results"][0]
    assert result["status"] == "non_compliant"
    assert result["repairable"] is True
    assert "沈鹤卿" in result["evidence"]


def test_forbidden_appearance_guard_passes_when_subject_absent(monkeypatch) -> None:
    async def _fail_evaluate(**_kwargs):
        raise AssertionError("local forbidden-appearance guard should short-circuit LLM")

    monkeypatch.setattr(qc, "_evaluate_single_constraint_compliance", _fail_evaluate)
    packet = SimpleNamespace(
        guard_constraints=[],
        chapter_contract={"forbidden_changes": ["禁止沈鹤卿在本章出场"]},
    )

    report = asyncio.run(
        qc.check_guard_constraint_compliance(
            runner=SimpleNamespace(),
            bundle=SimpleNamespace(),
            packet=packet,
            current_text="夜色压低，沈念卿把怀表收回抽屉。",
            chapter_number=2,
        )
    )

    assert report["checked_count"] == 1
    assert report["compliant_count"] == 1
    assert report["actionable_violation_count"] == 0
