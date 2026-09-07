from __future__ import annotations

import asyncio
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

import pytest

from novel_forge.core.exceptions import ModelGatewayError
from novel_forge.pipeline.long.repair_safety import (
    RepairDimension,
    RepairFailureKind,
    RepairFailureOperation,
    RepairFailurePolicy,
    RepairRoundSnapshot,
)
from novel_forge.pipeline.repair_orchestration.loop_runner import (
    RepairLoopConfig,
    _RepairRoundKernel,
)


def test_repair_failure_policy_classifies_and_emits_events() -> None:
    events: list[tuple[str, dict[str, Any]]] = []
    policy = RepairFailurePolicy(lambda step, payload: events.append((step, payload)))
    snapshot = RepairRoundSnapshot(
        stage=RepairDimension.CAUSAL,
        chapter_number=3,
        round_number=2,
        text="上一份已验证正文",
        report={"score": 4},
    )

    gateway = policy.repair_failed(snapshot=snapshot, exc=ModelGatewayError("timeout"))
    internal = policy.recheck_failed(snapshot=snapshot, exc=RuntimeError("boom"))

    assert gateway.event_name == "causal_repair_gateway_error"
    assert gateway.operation is RepairFailureOperation.REPAIR
    assert gateway.error_kind is RepairFailureKind.GATEWAY
    assert gateway.current_text == "上一份已验证正文"
    assert gateway.report == {"score": 4}
    assert gateway.is_gateway_error is True
    assert internal.event_name == "causal_recheck_internal_error"
    assert internal.operation is RepairFailureOperation.RECHECK
    assert internal.error_kind is RepairFailureKind.INTERNAL
    assert internal.action == "rollback_to_pre_repair_text"
    assert [event[0] for event in events] == [
        "causal_repair_gateway_error",
        "causal_recheck_internal_error",
    ]
    assert events[0][1]["round"] == 2
    assert events[1][1]["error_type"] == "RuntimeError"


def test_repair_failure_policy_supports_contract_dimension() -> None:
    events: list[tuple[str, dict[str, Any]]] = []
    policy = RepairFailurePolicy(lambda step, payload: events.append((step, payload)))
    snapshot = RepairRoundSnapshot(
        stage=RepairDimension.CONTRACT,
        chapter_number=5,
        text="",
        report={"artifact": "chapter_contracts"},
    )

    outcome = policy.repair_failed(snapshot=snapshot, exc=RuntimeError("bad contract"))

    assert RepairDimension.CONTRACT.value == "contract"
    assert outcome.event_name == "contract_repair_internal_error"
    assert outcome.error_kind is RepairFailureKind.INTERNAL
    assert events[0][0] == "contract_repair_internal_error"
    assert events[0][1]["chapter"] == 5


def test_repair_failure_policy_supports_custom_post_repair_events() -> None:
    events: list[tuple[str, dict[str, Any]]] = []
    policy = RepairFailurePolicy(lambda step, payload: events.append((step, payload)))
    snapshot = RepairRoundSnapshot(
        stage=RepairDimension.READING_POWER,
        chapter_number=4,
        round_number=1,
        text="已通过追读力复查的正文",
        report={"overall_score": 8},
    )

    outcome = policy.post_repair_check_failed(
        snapshot=snapshot,
        exc=RuntimeError("cross check failed"),
        event_name="reading_power_post_repair_checks_failed",
        action="skip_cross_dimension_checks_keep_current_text",
        repair_exhausted=False,
    )

    assert outcome.event_name == "reading_power_post_repair_checks_failed"
    assert outcome.operation is RepairFailureOperation.POST_REPAIR
    assert outcome.error_kind is RepairFailureKind.INTERNAL
    assert outcome.current_text == "已通过追读力复查的正文"
    assert outcome.report == {"overall_score": 8}
    assert outcome.repair_exhausted is False
    assert events == [
        (
            "reading_power_post_repair_checks_failed",
            {
                "chapter": 4,
                "error": "cross check failed",
                "error_kind": "internal",
                "error_type": "RuntimeError",
                "action": "skip_cross_dimension_checks_keep_current_text",
                "round": 1,
            },
        )
    ]


@dataclass
class _Issue:
    issue_type: str = "missing_cause"
    severity: str = "critical"
    summary: str = "缺少因果触发"


@dataclass
class _Report:
    score: float
    issues: list[_Issue]


class GenericRepairRunner(_RepairRoundKernel[_Report]):
    def __init__(self) -> None:
        self.events: list[tuple[str, Any]] = []
        self._settings = SimpleNamespace()
        super().__init__(
            RepairLoopConfig(max_rounds=1, change_budget=1.0, hard_floor=0.0),
            lambda step, payload: self.events.append((step, payload)),
            chapter_number=2,
        )

    async def execute_repair(self, ctx):
        return ctx.current_text + "。"

    async def evaluate(self, text: str) -> _Report:
        raise RuntimeError("recheck boom")

    def extract_issues(self, report: _Report) -> list[Any]:
        return list(report.issues)

    def compute_score(self, report: _Report) -> float:
        return report.score


@pytest.mark.asyncio
async def test_repair_loop_runner_rolls_back_when_recheck_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "novel_forge.pipeline.long.stages.dedup_pronoun.run_self_repetition_check",
        lambda _runner, text: (text, {}),
    )
    runner = GenericRepairRunner()

    result = await runner.run(
        current_text="原正文",
        initial_report=_Report(score=3.0, issues=[_Issue()]),
    )

    assert result.current_text == "原正文"
    assert result.report == _Report(score=3.0, issues=[_Issue()])
    assert result.repair_exhausted is True
    assert any(step == "generic_recheck_internal_error" for step, _ in runner.events)


class SlowRepairRunner(GenericRepairRunner):
    def __init__(self) -> None:
        self.events: list[tuple[str, Any]] = []
        self._settings = SimpleNamespace()
        _RepairRoundKernel.__init__(
            self,
            RepairLoopConfig(
                max_rounds=1,
                change_budget=1.0,
                hard_floor=0.0,
                max_execution_seconds=0.01,
            ),
            lambda step, payload: self.events.append((step, payload)),
            chapter_number=2,
        )

    async def execute_repair(self, ctx):
        await asyncio.sleep(1.0)
        return ctx.current_text + "。"

    async def evaluate(self, text: str) -> _Report:
        return _Report(score=10.0, issues=[])


@pytest.mark.asyncio
async def test_repair_loop_runner_times_out_single_repair_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "novel_forge.pipeline.long.stages.dedup_pronoun.run_self_repetition_check",
        lambda _runner, text: (text, {}),
    )
    runner = SlowRepairRunner()

    result = await runner.run(
        current_text="原正文",
        initial_report=_Report(score=3.0, issues=[_Issue()]),
    )

    assert result.current_text == "原正文"
    assert result.repair_exhausted is True
    assert any(
        step.endswith("_repair_internal_error") and payload["error_type"] == "TimeoutError"
        for step, payload in runner.events
    )
