"""Tests for Repair Orchestration v2 control modes and safety gates."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from novel_forge.common.constants import TaskType
from novel_forge.core.response_repair.orchestrator import FormatRepairContext
from novel_forge.persistence.models import ProjectLayout
from novel_forge.persistence.repair_case_store import repair_content_hash
from novel_forge.pipeline.repair_orchestration import (
    BookConsistencyRepairHandler,
    CausalRepairHandler,
    ContinuityRepairHandler,
    GuardrailRepairHandler,
    InitArtifactRepairHandler,
    RepairArtifactChange,
    RepairCapability,
    RepairControlMode,
    RepairDomain,
    RepairExecutionResult,
    RepairHandlerRegistry,
    RepairMission,
    RepairOrchestrator,
    RepairOutcome,
    RepairPlanCandidate,
    RepairPolicyEngine,
    RepairStrategy,
    RepairSurface,
    RepairTarget,
    RepairVerificationResult,
    StateAdjudicationRepairHandler,
    audit_repair_partitions,
    book_consistency_repair_mission,
    guardrail_repair_mission,
    init_artifact_repair_mission,
    issues_repair_mission,
    repair_domain_capabilities,
    run_causal_repair_v2,
    run_continuity_repair_v2,
    run_knowledge_boundary_repair_v2,
    run_prompt_leak_repair_v2,
    run_reading_power_repair_v2,
    run_runtime_contract_repair_v2,
    run_short_story_repair_v2,
    state_adjudication_repair_mission,
)
from novel_forge.pipeline.repair_orchestration.domains.reading_power import (
    ReadingPowerRepairHandler,
)
from novel_forge.pipeline.repair_orchestration.loop_runner import (
    RepairLoopConfig,
    _RepairRoundKernel,
)
from novel_forge.workspace.execution_result import ExecutionResult
from novel_forge.workspace.repair_ops.execution_repair_v2 import (
    build_default_repair_registry,
    execute_repair,
)


def _target() -> RepairTarget:
    return RepairTarget(
        domain=RepairDomain.CONTINUITY,
        surface=RepairSurface.CHAPTER_TEXT,
        chapter_number=1,
        issue_ref="issue-a",
        severity="critical",
        summary="角色状态不一致",
    )


def _plan(
    strategy: RepairStrategy,
    *,
    change_ratio: float = 0.0,
    crosses_artifact: bool = False,
    verification_required: bool = True,
) -> RepairPlanCandidate:
    return RepairPlanCandidate(
        target_id="target",
        strategy=strategy,
        summary="repair plan",
        preview="preview diff",
        estimated_change_ratio=change_ratio,
        crosses_artifact_boundary=crosses_artifact,
        verification_required=verification_required,
    )


class _FakeHandler:
    name = "fake"

    def __init__(
        self,
        *,
        strategy: RepairStrategy = RepairStrategy.LOCAL_PATCH,
        estimate: float = 0.0,
        actual_change: float = 0.0,
        verified: bool = True,
        verified_sequence: list[bool] | None = None,
        crosses_artifact: bool = False,
        fail_until: int = 0,
    ) -> None:
        self.strategy = strategy
        self.estimate = estimate
        self.actual_change = actual_change
        self.verified = verified
        self.verified_sequence = list(verified_sequence or [])
        self.crosses_artifact = crosses_artifact
        self.fail_until = fail_until
        self.executed = 0
        self.committed = 0
        self.rolled_back = 0

    def supports(self, target: RepairTarget) -> bool:
        return target.domain == RepairDomain.CONTINUITY

    async def plan(
        self,
        mission: RepairMission,
        target: RepairTarget,
    ) -> RepairPlanCandidate:
        del mission
        return RepairPlanCandidate(
            target_id=target.id,
            strategy=self.strategy,
            summary="fake plan",
            preview="fake preview",
            estimated_change_ratio=self.estimate,
            crosses_artifact_boundary=self.crosses_artifact,
        )

    async def snapshot(
        self,
        mission: RepairMission,
        target: RepairTarget,
        plan: RepairPlanCandidate,
    ) -> dict[str, Any]:
        del mission, plan
        return {"target_id": target.id, "text": "before"}

    async def execute(
        self,
        mission: RepairMission,
        target: RepairTarget,
        plan: RepairPlanCandidate,
    ) -> RepairExecutionResult:
        del mission, target, plan
        self.executed += 1
        if self.executed <= self.fail_until:
            raise RuntimeError("transient repair failure")
        return RepairExecutionResult(
            applied=True,
            payload={"text": "after"},
            changed_artifacts=["chapter.md"],
            change_ratio=self.actual_change,
        )

    async def verify(
        self,
        mission: RepairMission,
        target: RepairTarget,
        plan: RepairPlanCandidate,
        result: RepairExecutionResult,
    ) -> RepairVerificationResult:
        del mission, target, plan, result
        verified = self.verified
        if self.verified_sequence:
            index = min(max(self.executed - 1, 0), len(self.verified_sequence) - 1)
            verified = self.verified_sequence[index]
        return RepairVerificationResult(
            verified=verified,
            confidence=0.9 if verified else 0.2,
            reason="" if verified else "still broken",
        )

    async def rollback(
        self,
        mission: RepairMission,
        target: RepairTarget,
        snapshot_payload: dict[str, Any],
    ) -> None:
        del mission, target, snapshot_payload
        self.rolled_back += 1

    async def commit(
        self,
        mission: RepairMission,
        target: RepairTarget,
        plan: RepairPlanCandidate,
        result: RepairExecutionResult,
    ) -> list[RepairArtifactChange]:
        del mission, target, plan, result
        self.committed += 1
        return [RepairArtifactChange(artifact="chapter.md")]


def _registry(handler: Any) -> RepairHandlerRegistry:
    registry = RepairHandlerRegistry()
    registry.register(handler)
    return registry


class _ModeRunner(_RepairRoundKernel[Any]):
    def __init__(self, settings: Any) -> None:
        super().__init__(
            RepairLoopConfig(),
            lambda _event, _payload: None,
            chapter_number=1,
        )
        self._settings = settings

    async def execute_repair(self, ctx: Any) -> str:
        return ctx.current_text

    async def evaluate(self, text: str) -> Any:
        del text
        return SimpleNamespace(score=10.0, issues=[])

    def extract_issues(self, report: Any) -> list[Any]:
        return list(getattr(report, "issues", []) or [])

    def compute_score(self, report: Any) -> float:
        return float(getattr(report, "score", 0.0) or 0.0)


def test_policy_matrix_for_control_modes() -> None:
    engine = RepairPolicyEngine()

    manual = engine.decide(
        mode=RepairControlMode.MANUAL,
        plan=_plan(RepairStrategy.LOCAL_PATCH),
        change_budget=0.15,
    )
    assert manual.action == "preview_only"
    assert manual.requires_human is True

    assisted_low = engine.decide(
        mode=RepairControlMode.AI_ASSISTED,
        plan=_plan(RepairStrategy.LOCAL_PATCH),
        change_budget=0.15,
    )
    assert assisted_low.action == "execute"

    assisted_high = engine.decide(
        mode=RepairControlMode.AI_ASSISTED,
        plan=_plan(RepairStrategy.FULLTEXT_REWRITE),
        change_budget=0.15,
    )
    assert assisted_high.action == "request_approval"

    auto_high = engine.decide(
        mode=RepairControlMode.AI_AUTO,
        plan=_plan(RepairStrategy.FULLTEXT_REWRITE),
        change_budget=0.15,
    )
    assert auto_high.action == "execute"

    auto_unverified = engine.decide(
        mode=RepairControlMode.AI_AUTO,
        plan=_plan(RepairStrategy.LOCAL_PATCH, verification_required=False),
        change_budget=0.15,
        verification_available=False,
    )
    assert auto_unverified.action == "request_approval"


def test_legacy_repair_loop_human_decision_mode_uses_repair_control_mode() -> None:
    assert _ModeRunner(SimpleNamespace(repair_control_mode="manual"))._human_decision_mode() == (
        "ask_high_risk"
    )
    assert (
        _ModeRunner(SimpleNamespace(repair_control_mode="ai_assisted"))._human_decision_mode()
        == "ask_high_risk"
    )
    assert _ModeRunner(SimpleNamespace(repair_control_mode="ai_auto"))._human_decision_mode() == (
        "auto_allow"
    )


def test_repair_capability_audit_covers_all_domains() -> None:
    capabilities = repair_domain_capabilities()
    audit = audit_repair_partitions()

    assert set(capabilities) == set(RepairDomain)
    assert all(isinstance(item, RepairCapability) for item in capabilities.values())
    assert audit["all_domains_classified"] is True
    assert audit["all_domains_have_audit_contract"] is True
    assert audit["missing_domains"] == []
    assert audit["missing_audit_contract_domains"] == []
    assert audit["domains_by_chain"]["initialization"] == ["init_artifact"]
    assert "continuity" in audit["domains_by_chain"]["chapter_generation"]
    assert capabilities[RepairDomain.INIT_ARTIFACT].chain == "initialization"
    assert capabilities[RepairDomain.CONTINUITY].chain == "chapter_generation"
    assert "manual selected issue" in capabilities[RepairDomain.CONTINUITY].supported_modes
    for capability in capabilities.values():
        assert capability.identity_mode
        assert capability.location_mode
        assert capability.log_support
        assert capability.verification_support


def test_repair_capability_audit_flags_non_point_repair_domains() -> None:
    audit = audit_repair_partitions()

    assert "reading_power" not in audit["coarse_domains"]
    assert "reading_power" in audit["point_repair_ready_domains"]
    assert "reading_power" in audit["needs_partition_work_domains"]
    assert "short_story" in audit["coarse_domains"]
    assert "continuity" in audit["needs_partition_work_domains"]
    assert "causal" in audit["needs_partition_work_domains"]
    assert "init_artifact" in audit["needs_partition_work_by_chain"]["initialization"]
    assert "v2 wrapper mission target" in " ".join(audit["gaps"]["continuity"])
    assert audit["audit_contracts"]["init_artifact"]["location_mode"] == "artifact_json_path"
    assert audit["audit_contracts"]["book_consistency"]["location_mode"] == "book_queue"
    assert audit["audit_contracts"]["state_adjudication"]["identity_mode"] == "state_target"


def test_issues_repair_mission_only_includes_requested_domains() -> None:
    mission = issues_repair_mission(
        SimpleNamespace(
            project_id="p",
            chapter_number=3,
            continuity_issue_indices=[],
            continuity_issue_signatures=[],
            continuity_synthetic_issues=[],
            causal_issue_indices=[2],
            causal_issue_signatures=[],
            causal_synthetic_issues=[],
            allow_exhausted_retry=True,
        ),
        settings=SimpleNamespace(repair_control_mode="ai_auto", long_repair_max_change_ratio=0.5),
    )

    assert len(mission.targets) == 1
    assert mission.targets[0].domain == RepairDomain.CAUSAL
    assert mission.targets[0].payload["issue_indices"] == [2]
    assert mission.targets[0].payload["allow_exhausted_retry"] is True


def test_issues_repair_mission_splits_selected_issues_into_point_targets() -> None:
    mission = issues_repair_mission(
        SimpleNamespace(
            project_id="p",
            chapter_number=3,
            continuity_issue_indices=[0, 2],
            continuity_issue_signatures=["sig-a"],
            continuity_synthetic_issues=[
                {"issue_type": "opening_gap", "summary": "开头承接断裂。"}
            ],
            causal_issue_indices=[1],
            causal_issue_signatures=["sig-c"],
            causal_synthetic_issues=[{"issue_type": "causal_chain_break"}],
            allow_exhausted_retry=True,
        ),
        settings=SimpleNamespace(repair_control_mode="ai_auto", long_repair_max_change_ratio=0.5),
    )

    assert [target.domain for target in mission.targets] == [
        RepairDomain.CAUSAL,
        RepairDomain.CAUSAL,
        RepairDomain.CAUSAL,
        RepairDomain.CONTINUITY,
        RepairDomain.CONTINUITY,
        RepairDomain.CONTINUITY,
        RepairDomain.CONTINUITY,
    ]
    assert [target.payload for target in mission.targets[:3]] == [
        {"allow_exhausted_retry": True, "issue_indices": [1], "point_repair": True},
        {"allow_exhausted_retry": True, "issue_signatures": ["sig-c"], "point_repair": True},
        {
            "allow_exhausted_retry": True,
            "synthetic_issues": [{"issue_type": "causal_chain_break"}],
            "point_repair": True,
        },
    ]
    assert [target.payload for target in mission.targets[3:]] == [
        {"issue_indices": [0], "point_repair": True},
        {"issue_indices": [2], "point_repair": True},
        {"issue_signatures": ["sig-a"], "point_repair": True},
        {
            "synthetic_issues": [{"issue_type": "opening_gap", "summary": "开头承接断裂。"}],
            "point_repair": True,
        },
    ]


def test_issues_repair_mission_empty_request_has_no_targets() -> None:
    mission = issues_repair_mission(
        SimpleNamespace(
            project_id="p",
            chapter_number=3,
            continuity_issue_indices=[],
            continuity_issue_signatures=[],
            continuity_synthetic_issues=[],
            causal_issue_indices=[],
            causal_issue_signatures=[],
            causal_synthetic_issues=[],
            allow_exhausted_retry=True,
        ),
        settings=SimpleNamespace(
            repair_control_mode="ai_assisted", long_repair_max_change_ratio=0.5
        ),
    )

    assert mission.targets == []


def test_issues_repair_mission_can_keep_empty_book_causal_placeholder() -> None:
    mission = issues_repair_mission(
        SimpleNamespace(
            project_id="p",
            chapter_number=3,
            continuity_issue_indices=[],
            continuity_issue_signatures=[],
            continuity_synthetic_issues=[],
            causal_issue_indices=[],
            causal_issue_signatures=[],
            causal_synthetic_issues=[],
            allow_exhausted_retry=True,
        ),
        settings=SimpleNamespace(repair_control_mode="ai_auto", long_repair_max_change_ratio=0.5),
        include_empty_causal_target=True,
    )

    assert [target.domain for target in mission.targets] == [RepairDomain.CAUSAL]
    assert mission.targets[0].payload["skip_empty_target"] is True
    assert mission.targets[0].payload["strategy"] == RepairStrategy.VERIFY_ONLY.value


def test_issues_repair_mission_runs_causal_before_opening_boundary_continuity() -> None:
    mission = issues_repair_mission(
        SimpleNamespace(
            project_id="p",
            chapter_number=3,
            continuity_issue_indices=[],
            continuity_issue_signatures=[],
            continuity_synthetic_issues=[
                {"issue_type": "opening_gap", "summary": "开头承接断裂。"}
            ],
            causal_issue_indices=[],
            causal_issue_signatures=[],
            causal_synthetic_issues=[{"issue_type": "causal_chain_break"}],
            allow_exhausted_retry=False,
        ),
        settings=SimpleNamespace(repair_control_mode="ai_auto", long_repair_max_change_ratio=0.5),
    )

    assert [target.domain for target in mission.targets] == [
        RepairDomain.CAUSAL,
        RepairDomain.CONTINUITY,
    ]


@pytest.mark.asyncio
async def test_manual_mode_only_produces_preview() -> None:
    handler = _FakeHandler()
    mission = RepairMission(
        project_id="p",
        control_mode=RepairControlMode.MANUAL,
        targets=[_target()],
    )

    outcome = await RepairOrchestrator(registry=_registry(handler)).run(mission)

    assert isinstance(outcome, RepairOutcome)
    assert outcome.applied is False
    assert outcome.needs_human_review is True
    assert handler.executed == 0
    assert outcome.attempts[0].status.value == "pending_approval"


@pytest.mark.asyncio
async def test_ai_assisted_executes_low_risk_repair() -> None:
    handler = _FakeHandler(strategy=RepairStrategy.LOCAL_PATCH, actual_change=0.05)
    mission = RepairMission(
        project_id="p",
        control_mode=RepairControlMode.AI_ASSISTED,
        targets=[_target()],
        policy={"change_budget": 0.15},
    )

    outcome = await RepairOrchestrator(registry=_registry(handler)).run(mission)

    assert outcome.applied is True
    assert outcome.targets_resolved == [mission.targets[0].id]
    assert handler.executed == 1
    assert handler.committed == 1
    assert handler.rolled_back == 0


@pytest.mark.asyncio
async def test_ai_assisted_requests_approval_for_high_risk_repair() -> None:
    handler = _FakeHandler(strategy=RepairStrategy.FULLTEXT_REWRITE)
    mission = RepairMission(
        project_id="p",
        control_mode=RepairControlMode.AI_ASSISTED,
        targets=[_target()],
    )

    outcome = await RepairOrchestrator(registry=_registry(handler)).run(mission)

    assert outcome.applied is False
    assert outcome.needs_human_review is True
    assert handler.executed == 0
    assert outcome.attempts[0].decision == "request_approval"


@pytest.mark.asyncio
async def test_ai_auto_rolls_back_when_verification_fails() -> None:
    handler = _FakeHandler(
        strategy=RepairStrategy.FULLTEXT_REWRITE,
        actual_change=0.05,
        verified=False,
    )
    mission = RepairMission(
        project_id="p",
        control_mode=RepairControlMode.AI_AUTO,
        targets=[_target()],
        policy={"change_budget": 0.15},
        max_rounds=1,
    )

    outcome = await RepairOrchestrator(registry=_registry(handler)).run(mission)

    assert outcome.applied is False
    assert outcome.needs_human_review is True
    assert handler.executed == 1
    assert handler.committed == 0
    assert handler.rolled_back == 1
    assert outcome.attempts[0].rollback_reason == "still broken"


@pytest.mark.asyncio
async def test_ai_auto_retries_verification_failure_until_success() -> None:
    handler = _FakeHandler(
        actual_change=0.05,
        verified_sequence=[False, True],
    )
    events: list[tuple[str, dict[str, Any]]] = []
    mission = RepairMission(
        project_id="p",
        control_mode=RepairControlMode.AI_AUTO,
        targets=[_target()],
        policy={"change_budget": 0.15},
        max_rounds=3,
    )

    outcome = await RepairOrchestrator(
        registry=_registry(handler),
        on_step=lambda event, payload: events.append((event, payload)),
    ).run(mission)

    assert outcome.applied is True
    assert outcome.targets_resolved == [mission.targets[0].id]
    assert handler.executed == 2
    assert handler.rolled_back == 1
    assert handler.committed == 1
    assert [attempt.round_number for attempt in outcome.attempts] == [1, 2]
    assert outcome.attempts[0].status.value == "rolled_back"
    assert outcome.attempts[1].status.value == "verified"
    retry_payload = next(
        payload for event, payload in events if event == "repair_v2_retry_scheduled"
    )
    assert retry_payload["trace_id"] == mission.trace_id
    assert retry_payload["mode"] == "ai_auto"
    assert retry_payload["domain"] == "continuity"
    assert retry_payload["surface"] == "chapter_text"
    assert retry_payload["strategy"] == "local_patch"
    assert retry_payload["round"] == 1
    assert retry_payload["snapshot_id"]


@pytest.mark.asyncio
async def test_ai_auto_retries_handler_exception_until_success() -> None:
    handler = _FakeHandler(actual_change=0.05, fail_until=1)
    mission = RepairMission(
        project_id="p",
        control_mode=RepairControlMode.AI_AUTO,
        targets=[_target()],
        policy={"change_budget": 0.15},
        max_rounds=3,
    )

    outcome = await RepairOrchestrator(registry=_registry(handler)).run(mission)

    assert outcome.applied is True
    assert handler.executed == 2
    assert handler.rolled_back == 1
    assert handler.committed == 1
    assert outcome.attempts[0].status.value == "failed"
    assert outcome.attempts[1].status.value == "verified"


@pytest.mark.asyncio
async def test_ai_auto_stops_retries_at_doom_loop_threshold() -> None:
    handler = _FakeHandler(actual_change=0.05, verified=False)
    mission = RepairMission(
        project_id="p",
        control_mode=RepairControlMode.AI_AUTO,
        targets=[_target()],
        policy={"change_budget": 0.15, "doom_loop_threshold": 2},
        max_rounds=5,
    )

    outcome = await RepairOrchestrator(registry=_registry(handler)).run(mission)

    assert outcome.applied is False
    assert outcome.needs_human_review is True
    assert handler.executed == 2
    assert handler.rolled_back == 2
    assert handler.committed == 0
    assert len(outcome.attempts) == 2
    assert any(w.startswith("doom_loop_guard:") for w in outcome.warnings)


@pytest.mark.asyncio
async def test_ai_auto_rolls_back_when_change_budget_is_exceeded() -> None:
    handler = _FakeHandler(
        strategy=RepairStrategy.LOCAL_PATCH,
        actual_change=0.3,
        verified=True,
    )
    mission = RepairMission(
        project_id="p",
        control_mode=RepairControlMode.AI_AUTO,
        targets=[_target()],
        policy={"change_budget": 0.15},
    )

    outcome = await RepairOrchestrator(registry=_registry(handler)).run(mission)

    assert outcome.applied is False
    assert outcome.needs_human_review is True
    assert handler.executed == 1
    assert handler.committed == 0
    assert handler.rolled_back == 1
    assert outcome.attempts[0].rollback_reason == "change_budget_exceeded"


class _WorkspaceStorage:
    def __init__(self, root: Any) -> None:
        self._root = root

    def existing_project_dir(self, project_id: str) -> Any:
        return self._root / project_id


def _workspace_runtime(tmp_path: Any) -> Any:
    return SimpleNamespace(storage=_WorkspaceStorage(tmp_path))


def _prepare_workspace_project(tmp_path: Any, project_id: str, text: str = "原文") -> ProjectLayout:
    layout = ProjectLayout(tmp_path / project_id)
    layout.chapters_dir.mkdir(parents=True, exist_ok=True)
    layout.reports_dir.mkdir(parents=True, exist_ok=True)
    layout.chapter_path(1).write_text(text, encoding="utf-8")
    return layout


@pytest.mark.asyncio
async def test_execute_repair_v2_runs_default_continuity_handler(
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_id = "p"
    layout = _prepare_workspace_project(tmp_path, project_id, text="原文")
    runtime = _workspace_runtime(tmp_path)
    events: list[str] = []

    async def _fake_legacy_repair(
        runtime_arg: Any,
        request: Any,
        *,
        on_step_progress: Any = None,
        on_audit_update: Any = None,
    ) -> ExecutionResult[Any]:
        del runtime_arg, on_audit_update
        assert request.project_id == project_id
        assert request.chapter_number == 1
        assert request.issue_indices == [2]
        if on_step_progress is not None:
            on_step_progress("legacy_continuity_called", {})
        layout.chapter_path(1).write_text("修复后", encoding="utf-8")
        return ExecutionResult(
            project_id=project_id,
            result=SimpleNamespace(applied=True, warnings=[], failure_reason=""),
        )

    monkeypatch.setattr(
        "novel_forge.workspace.repair_ops.execution_repair_continuity._execute_chapter_continuity_repair_impl",
        _fake_legacy_repair,
    )

    target = RepairTarget(
        domain=RepairDomain.CONTINUITY,
        surface=RepairSurface.CHAPTER_TEXT,
        chapter_number=1,
        issue_ref="cont-1",
        payload={"issue_indices": [2]},
    )
    mission = RepairMission(
        project_id=project_id,
        control_mode=RepairControlMode.AI_AUTO,
        targets=[target],
        policy={"change_budget": 1.0},
    )
    execution = await execute_repair(
        runtime,
        mission,
        on_step_progress=lambda event, _payload: events.append(event),
    )

    assert execution.result.applied is True
    assert execution.result.targets_resolved == [target.id]
    assert layout.chapter_path(1).read_text(encoding="utf-8") == "修复后"
    assert "legacy_continuity_called" in events
    assert "repair_v2_target_committed" in events
    ledger_line = (layout.states_dir / "repair_v2_ledger.jsonl").read_text(encoding="utf-8").strip()
    ledger = json.loads(ledger_line)
    assert ledger["trace_id"] == mission.trace_id
    assert ledger["status"] == "completed"
    assert ledger["mode"] == "ai_auto"
    assert list((layout.states_dir / "repair_v2_snapshots").glob("*.json"))


@pytest.mark.asyncio
async def test_execute_repair_v2_rolls_back_legacy_continuity_on_budget_excess(
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_id = "p"
    layout = _prepare_workspace_project(tmp_path, project_id, text="短")
    runtime = _workspace_runtime(tmp_path)

    async def _fake_large_repair(
        runtime_arg: Any,
        request: Any,
        *,
        on_step_progress: Any = None,
        on_audit_update: Any = None,
    ) -> ExecutionResult[Any]:
        del runtime_arg, request, on_step_progress, on_audit_update
        layout.chapter_path(1).write_text("很长" * 100, encoding="utf-8")
        return ExecutionResult(
            project_id=project_id,
            result=SimpleNamespace(applied=True, warnings=[], failure_reason=""),
        )

    monkeypatch.setattr(
        "novel_forge.workspace.repair_ops.execution_repair_continuity._execute_chapter_continuity_repair_impl",
        _fake_large_repair,
    )

    target = RepairTarget(
        domain=RepairDomain.CONTINUITY,
        surface=RepairSurface.CHAPTER_TEXT,
        chapter_number=1,
        issue_ref="cont-1",
    )
    mission = RepairMission(
        project_id=project_id,
        control_mode=RepairControlMode.AI_AUTO,
        targets=[target],
        policy={"change_budget": 0.01},
    )
    execution = await execute_repair(runtime, mission)

    assert execution.result.applied is False
    assert execution.result.needs_human_review is True
    assert execution.result.attempts[0].rollback_reason == "change_budget_exceeded"
    assert layout.chapter_path(1).read_text(encoding="utf-8") == "短"
    ledger_line = (layout.states_dir / "repair_v2_ledger.jsonl").read_text(encoding="utf-8").strip()
    ledger = json.loads(ledger_line)
    assert ledger["status"] == "needs_review"
    assert ledger["attempts"][0]["rollback_reason"] == "change_budget_exceeded"


@pytest.mark.asyncio
async def test_execute_repair_format_response_is_protocol_only() -> None:
    class _NoProjectStorage:
        def existing_project_dir(self, project_id: str) -> Any:
            raise AssertionError(f"protocol repair touched project storage: {project_id}")

    def _validator(data: dict[str, Any]) -> None:
        assert data["ok"] is True

    runtime = SimpleNamespace(storage=_NoProjectStorage())
    target = RepairTarget(
        domain=RepairDomain.FORMAT_RESPONSE,
        surface=RepairSurface.RESPONSE_JSON,
        issue_ref="format",
    )
    mission = RepairMission(
        project_id="protocol:format_response",
        control_mode=RepairControlMode.AI_AUTO,
        targets=[target],
        source_context={
            "protocol_only": True,
            "format_context": FormatRepairContext(
                task_type=TaskType.SPEC_ENRICH,
                raw_content='{"ok": true}',
                error=None,
            ),
            "validator": _validator,
        },
        policy={"change_budget": 1.0},
        max_rounds=1,
    )

    execution = await execute_repair(runtime, mission)

    assert execution.result.applied is True
    assert execution.result.artifacts_changed == []
    assert execution.result.targets_resolved == [target.id]


def test_default_repair_registry_resolves_all_production_domains(tmp_path: Any) -> None:
    registry = build_default_repair_registry(_workspace_runtime(tmp_path))

    cases = [
        (RepairDomain.CONTINUITY, RepairSurface.CHAPTER_TEXT),
        (RepairDomain.CAUSAL, RepairSurface.CHAPTER_TEXT),
        (RepairDomain.READING_POWER, RepairSurface.CHAPTER_TEXT),
        (RepairDomain.KNOWLEDGE_BOUNDARY, RepairSurface.CHAPTER_TEXT),
        (RepairDomain.PROMPT_LEAK, RepairSurface.CHAPTER_TEXT),
        (RepairDomain.RUNTIME_CONTRACT, RepairSurface.CHAPTER_CONTRACTS),
        (RepairDomain.SHORT_STORY, RepairSurface.SHORT_TEXT),
        (RepairDomain.FORMAT_RESPONSE, RepairSurface.RESPONSE_JSON),
        (RepairDomain.GUARDRAIL, RepairSurface.CHAPTER_TEXT),
        (RepairDomain.INIT_ARTIFACT, RepairSurface.BLUEPRINT),
        (RepairDomain.BOOK_CONSISTENCY, RepairSurface.BOOK_CHAPTER_SET),
        (RepairDomain.STATE_ADJUDICATION, RepairSurface.CHAPTER_TEXT),
    ]

    for domain, surface in cases:
        assert registry.resolve(RepairTarget(domain=domain, surface=surface, chapter_number=1)), (
            f"missing handler for {domain.value}/{surface.value}"
        )


def test_public_mission_factories_build_new_domain_missions() -> None:
    settings = SimpleNamespace(repair_control_mode="ai_auto", long_repair_max_change_ratio=0.4)
    request = SimpleNamespace(
        project_id="p", chapter_number=2, source_context={"current_text": "x"}
    )

    missions = [
        guardrail_repair_mission(request, settings=settings),
        init_artifact_repair_mission(
            SimpleNamespace(project_id="p", source_context={"artifact": "blueprint"}),
            settings=settings,
        ),
        book_consistency_repair_mission(
            SimpleNamespace(project_id="p", source_context={"chapter_missions": []}),
            settings=settings,
        ),
        state_adjudication_repair_mission(request, settings=settings),
    ]

    assert [mission.targets[0].domain for mission in missions] == [
        RepairDomain.GUARDRAIL,
        RepairDomain.INIT_ARTIFACT,
        RepairDomain.BOOK_CONSISTENCY,
        RepairDomain.STATE_ADJUDICATION,
    ]
    assert all(mission.control_mode == RepairControlMode.AI_AUTO for mission in missions)


@pytest.mark.parametrize(
    ("handler", "domain", "surface", "source_context"),
    [
        (
            GuardrailRepairHandler(),
            RepairDomain.GUARDRAIL,
            RepairSurface.CHAPTER_TEXT,
            {
                "current_text": "原文",
                "repaired_text": "修复后",
                "artifact": "chapter.md",
                "verification_passed": True,
            },
        ),
        (
            InitArtifactRepairHandler(),
            RepairDomain.INIT_ARTIFACT,
            RepairSurface.BLUEPRINT,
            {
                "artifact": "blueprint.json",
                "repaired_payload": {"ok": True},
                "verification_passed": True,
            },
        ),
        (
            BookConsistencyRepairHandler(),
            RepairDomain.BOOK_CONSISTENCY,
            RepairSurface.BOOK_CHAPTER_SET,
            {
                "artifact": "book_consistency_queue",
                "repaired_payload": {"ok": True},
                "verification_passed": True,
            },
        ),
        (
            StateAdjudicationRepairHandler(),
            RepairDomain.STATE_ADJUDICATION,
            RepairSurface.CHAPTER_TEXT,
            {
                "current_text": "原文",
                "repaired_text": "修复后",
                "artifact": "state_delta.md",
                "verification_passed": True,
            },
        ),
    ],
)
@pytest.mark.asyncio
async def test_concrete_domain_handlers_return_standard_outcome(
    handler: Any,
    domain: RepairDomain,
    surface: RepairSurface,
    source_context: dict[str, Any],
) -> None:
    target = RepairTarget(domain=domain, surface=surface, summary="repair")
    mission = RepairMission(
        project_id="p",
        control_mode=RepairControlMode.AI_AUTO,
        targets=[target],
        policy={"change_budget": 1.0},
        source_context=source_context,
    )

    outcome = await RepairOrchestrator(registry=_registry(handler)).run(mission)

    assert outcome.applied is True
    assert outcome.targets_resolved == [target.id]
    assert outcome.artifacts_changed


@pytest.mark.asyncio
async def test_repair_orchestrator_emits_standard_audit_events_for_artifact_target() -> None:
    events: list[tuple[str, dict[str, Any]]] = []
    target = RepairTarget(
        domain=RepairDomain.INIT_ARTIFACT,
        surface=RepairSurface.BLUEPRINT,
        issue_ref="init-artifact-ticket",
        severity="high",
        summary="blueprint field repair",
        payload={
            "issue_id": "artifact-issue-1",
            "json_path": "$.plot_threads[0].payoff",
            "artifact_path": "blueprint.json",
        },
    )
    mission = RepairMission(
        project_id="p",
        control_mode=RepairControlMode.AI_AUTO,
        targets=[target],
        policy={"change_budget": 1.0},
        source_context={
            "artifact": "blueprint.json",
            "repaired_payload": {"ok": True},
            "verification_passed": True,
        },
    )

    outcome = await RepairOrchestrator(
        registry=_registry(InitArtifactRepairHandler()),
        on_step=lambda event, payload: events.append((event, payload)),
    ).run(mission)

    assert outcome.applied is True
    audit_payloads = [payload for event, payload in events if event == "repair_audit_event"]
    assert any(payload["event_type"] == "selected" for payload in audit_payloads)
    assert any(payload["event_type"] == "verified" for payload in audit_payloads)
    artifact_payload = next(payload for payload in audit_payloads if payload["event_type"] == "selected")
    assert artifact_payload["issue_id"] == "artifact-issue-1"
    assert artifact_payload["artifact_path"] == "blueprint.json"
    assert artifact_payload["json_path"] == "$.plot_threads[0].payoff"
    assert artifact_payload["location_mode"] == "artifact_json_path"

    summary = next(payload for event, payload in events if event == "repair_audit_summary")
    assert summary["selected_issue_ids"] == ["artifact-issue-1"]
    assert summary["verified_issue_ids"] == ["artifact-issue-1"]
    assert summary["remaining_issue_ids"] == []


@pytest.mark.asyncio
async def test_book_consistency_queue_partial_completion_needs_review_without_rollback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent_target = RepairTarget(
        domain=RepairDomain.BOOK_CONSISTENCY,
        surface=RepairSurface.BOOK_CHAPTER_SET,
        summary="book queue",
    )
    child_target_ok = RepairTarget(
        domain=RepairDomain.CONTINUITY, surface=RepairSurface.CHAPTER_TEXT
    )
    child_target_remaining = RepairTarget(
        domain=RepairDomain.CAUSAL,
        surface=RepairSurface.CHAPTER_TEXT,
    )
    child_ok = RepairMission(
        project_id="p",
        control_mode=RepairControlMode.AI_AUTO,
        targets=[child_target_ok],
    )
    child_partial = RepairMission(
        project_id="p",
        control_mode=RepairControlMode.AI_AUTO,
        targets=[child_target_remaining],
    )

    async def _fake_execute_repair(runtime: Any, mission: RepairMission) -> Any:
        del runtime
        if mission is child_ok:
            return SimpleNamespace(
                result=RepairOutcome(
                    project_id="p",
                    trace_id=mission.trace_id,
                    applied=True,
                    targets_resolved=[child_target_ok.id],
                    artifacts_changed=[RepairArtifactChange(artifact="chapter_001.md")],
                )
            )
        return SimpleNamespace(
            result=RepairOutcome(
                project_id="p",
                trace_id=mission.trace_id,
                targets_remaining=[child_target_remaining.id],
                needs_human_review=True,
                warnings=["child_needs_review"],
            )
        )

    mission = RepairMission(
        project_id="p",
        control_mode=RepairControlMode.AI_AUTO,
        targets=[parent_target],
        source_context={"chapter_missions": [child_ok, child_partial]},
        policy={"change_budget": 1.0},
    )

    outcome = await RepairOrchestrator(
        registry=_registry(BookConsistencyRepairHandler(runtime=SimpleNamespace(), repair_executor=_fake_execute_repair))
    ).run(mission)

    assert outcome.applied is True
    assert outcome.needs_human_review is True
    assert outcome.targets_remaining == [parent_target.id]
    assert outcome.attempts[0].status.value == "verified"
    assert outcome.attempts[0].rollback_reason == ""
    assert "book_consistency_targets_remaining" in outcome.warnings


def test_pipeline_domain_handlers_support_active_chapter_text_surface() -> None:
    assert ContinuityRepairHandler().supports(
        RepairTarget(domain=RepairDomain.CONTINUITY, surface=RepairSurface.CHAPTER_TEXT)
    )
    assert CausalRepairHandler().supports(
        RepairTarget(domain=RepairDomain.CAUSAL, surface=RepairSurface.CHAPTER_TEXT)
    )


@pytest.mark.asyncio
async def test_reading_power_fallback_cannot_be_verified_as_success() -> None:
    handler = ReadingPowerRepairHandler()
    target = RepairTarget(
        domain=RepairDomain.READING_POWER,
        surface=RepairSurface.CHAPTER_TEXT,
        chapter_number=2,
    )
    mission = RepairMission(
        project_id="p",
        targets=[target],
        source_context={
            "reading_power_result": SimpleNamespace(
                report=SimpleNamespace(
                    is_fallback=True,
                    evaluation_status="fallback",
                    fallback_reason="llm_error",
                )
            )
        },
    )

    verification = await handler.verify(
        mission,
        target,
        _plan(RepairStrategy.LLM_PATCH),
        RepairExecutionResult(applied=False),
    )

    assert verification.verified is False
    assert verification.confidence == 0.0
    assert verification.reason == "llm_error"


@pytest.mark.asyncio
async def test_causal_unavailable_report_cannot_be_verified_as_success() -> None:
    handler = CausalRepairHandler()
    target = RepairTarget(
        domain=RepairDomain.CAUSAL,
        surface=RepairSurface.CHAPTER_TEXT,
        chapter_number=2,
    )
    mission = RepairMission(
        project_id="p",
        targets=[target],
        source_context={
            "causal_result": SimpleNamespace(
                causal_report=SimpleNamespace(validation_status="unavailable")
            )
        },
    )

    verification = await handler.verify(
        mission,
        target,
        _plan(RepairStrategy.LLM_PATCH),
        RepairExecutionResult(applied=False),
    )

    assert verification.verified is False
    assert verification.reason == "causal_validation_unavailable"


def _reading_power_runner(mode: str = "ai_auto") -> Any:
    events: list[str] = []

    def _on_step(event: str, payload: Any) -> None:
        del payload
        events.append(event)

    return SimpleNamespace(
        _settings=SimpleNamespace(
            repair_control_mode=mode,
            long_causal_repair_enabled=True,
            long_causal_max_repair_rounds=2,
            long_causal_threshold=5.0,
            long_reading_power_repair_max_change_ratio=1.0,
            long_knowledge_boundary_repair_max_change_ratio=1.0,
        ),
        _on_step=_on_step,
        events=events,
    )


@pytest.mark.asyncio
async def test_run_continuity_repair_v2_manual_mode_does_not_execute(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executed = False

    async def _fake_loop(*args: Any, **kwargs: Any) -> Any:
        del args, kwargs
        nonlocal executed
        executed = True

    monkeypatch.setattr(
        "novel_forge.pipeline.long.stages.continuity_repair._execute_continuity_repair_loop",
        _fake_loop,
    )

    result = await run_continuity_repair_v2(
        runner=_reading_power_runner("manual"),
        bundle=SimpleNamespace(project_id="p"),
        packet=SimpleNamespace(),
        bridge=SimpleNamespace(),
        plan=SimpleNamespace(),
        on_step=lambda _event, _payload: None,
        current_text="原文",
        alignment_report=SimpleNamespace(),
        continuity_report=SimpleNamespace(),
        chapter_repair_report=None,
        chapter_number=1,
        trace=SimpleNamespace(),
        repair_thresholds=SimpleNamespace(change_budget=0.5),
        cont_max_rounds=1,
        cont_must_fix_sev="critical",
        cont_threshold=8.5,
    )

    assert executed is False
    assert result.current_text == "原文"
    assert result.needs_human_review is True


@pytest.mark.asyncio
async def test_run_causal_repair_v2_manual_mode_does_not_execute(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executed = False

    async def _fake_loop(*args: Any, **kwargs: Any) -> Any:
        del args, kwargs
        nonlocal executed
        executed = True

    monkeypatch.setattr(
        "novel_forge.pipeline.long.stages.causal_repair._execute_causal_repair_loop",
        _fake_loop,
    )

    result = await run_causal_repair_v2(
        runner=_reading_power_runner("manual"),
        bundle=SimpleNamespace(project_id="p"),
        packet=SimpleNamespace(),
        bridge=SimpleNamespace(),
        plan=SimpleNamespace(),
        on_step=lambda _event, _payload: None,
        current_text="原文",
        alignment_report=SimpleNamespace(),
        continuity_report=SimpleNamespace(),
        chapter_repair_report=None,
        chapter_number=1,
        trace=SimpleNamespace(),
        repair_thresholds=SimpleNamespace(change_budget=0.5),
        prev_chapter_ending="上章结尾",
        initial_causal_report=SimpleNamespace(causal_score=10.0, issues=[]),
    )

    assert executed is False
    assert result.current_text == "原文"
    assert result.needs_human_review is True


@pytest.mark.asyncio
async def test_run_reading_power_repair_v2_executes_legacy_loop(
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _fake_loop(*args: Any, **kwargs: Any) -> Any:
        assert args[5] == "原文"
        assert args[6] == 1
        assert kwargs["max_reading_power_rounds"] == 2
        return SimpleNamespace(
            current_text="修复后",
            report=SimpleNamespace(overall_score=8.8),
            text_hash="hash",
            applied=True,
            rounds_used=1,
            best_effort_accepted=False,
            best_effort_reason="",
            needs_human_review=False,
            repair_exhausted=False,
            verification_evidence={
                "recheck_performed": True,
                "gate_passed": True,
                "candidate_text_hash": repair_content_hash("修复后"),
                "residual_issue_ids": [],
                "regression_issue_ids": [],
            },
        )

    monkeypatch.setattr(
        "novel_forge.pipeline.long.stages.reading_power_repair._execute_reading_power_repair_loop",
        _fake_loop,
    )
    runner = _reading_power_runner("ai_auto")
    layout = SimpleNamespace(states_dir=tmp_path / "states")

    result = await run_reading_power_repair_v2(
        runner=runner,
        bundle=SimpleNamespace(project_id="p", layout=layout),
        packet=SimpleNamespace(),
        bridge=SimpleNamespace(),
        plan=SimpleNamespace(),
        current_text="原文",
        chapter_number=1,
        trace=SimpleNamespace(),
        max_reading_power_rounds=2,
    )

    assert result.current_text == "修复后"
    assert result.rounds_used == 1
    assert "repair_v2_target_committed" in runner.events
    ledger_path = layout.states_dir / "repair_v2_ledger.jsonl"
    ledger = json.loads(ledger_path.read_text(encoding="utf-8").strip())
    assert ledger["status"] == "completed"
    assert ledger["mode"] == "ai_auto"
    assert ledger["attempts"][0]["domain"] == "reading_power"


@pytest.mark.asyncio
async def test_run_reading_power_repair_v2_manual_mode_does_not_execute(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executed = False

    async def _fake_loop(*args: Any, **kwargs: Any) -> Any:
        del args, kwargs
        nonlocal executed
        executed = True

    monkeypatch.setattr(
        "novel_forge.pipeline.long.stages.reading_power_repair._execute_reading_power_repair_loop",
        _fake_loop,
    )

    result = await run_reading_power_repair_v2(
        runner=_reading_power_runner("manual"),
        bundle=SimpleNamespace(project_id="p"),
        packet=SimpleNamespace(),
        bridge=SimpleNamespace(),
        plan=SimpleNamespace(),
        current_text="原文",
        chapter_number=1,
        trace=SimpleNamespace(),
    )

    assert executed is False
    assert result.current_text == "原文"
    assert result.needs_human_review is True


@pytest.mark.asyncio
async def test_run_reading_power_repair_v2_rolls_back_on_budget_excess(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _fake_loop(*args: Any, **kwargs: Any) -> Any:
        del args, kwargs
        return SimpleNamespace(
            current_text="很长" * 100,
            report=None,
            text_hash="hash",
            applied=True,
            rounds_used=1,
            best_effort_accepted=False,
            best_effort_reason="",
            needs_human_review=False,
        )

    monkeypatch.setattr(
        "novel_forge.pipeline.long.stages.reading_power_repair._execute_reading_power_repair_loop",
        _fake_loop,
    )
    runner = _reading_power_runner("ai_auto")
    runner._settings.long_reading_power_repair_max_change_ratio = 0.01

    result = await run_reading_power_repair_v2(
        runner=runner,
        bundle=SimpleNamespace(project_id="p"),
        packet=SimpleNamespace(),
        bridge=SimpleNamespace(),
        plan=SimpleNamespace(),
        current_text="短",
        chapter_number=1,
        trace=SimpleNamespace(),
    )

    assert result.current_text == "短"
    assert result.needs_human_review is True
    assert "repair_v2_rollback" in runner.events


@pytest.mark.asyncio
async def test_run_knowledge_boundary_repair_v2_executes_legacy_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _fake_loop(**kwargs: Any) -> Any:
        assert kwargs["current_text"] == "原文"
        assert kwargs["chapter_number"] == 1
        assert kwargs["max_rounds"] == 1
        return SimpleNamespace(
            current_text="修复后",
            repair_exhausted=False,
            rounds_used=1,
            findings_before=list(kwargs["findings"]),
            findings_after=[],
            repair_attempted=True,
        )

    monkeypatch.setattr(
        "novel_forge.pipeline.long.stages.knowledge_boundary_repair."
        "run_knowledge_boundary_repair_loop",
        _fake_loop,
    )
    runner = _reading_power_runner("ai_auto")

    result = await run_knowledge_boundary_repair_v2(
        runner=runner,
        storage=SimpleNamespace(),
        bundle=SimpleNamespace(project_id="p"),
        packet=SimpleNamespace(),
        current_text="原文",
        chapter_number=1,
        findings=[SimpleNamespace(blocks_finalize=True, severity="high")],
        trace=SimpleNamespace(),
        max_rounds=1,
    )

    assert result.current_text == "修复后"
    assert result.repair_exhausted is False
    assert "repair_v2_target_committed" in runner.events
    assert "repair_audit_event" in runner.events


@pytest.mark.asyncio
async def test_run_knowledge_boundary_repair_v2_manual_mode_does_not_execute(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executed = False

    async def _fake_loop(**kwargs: Any) -> Any:
        del kwargs
        nonlocal executed
        executed = True

    monkeypatch.setattr(
        "novel_forge.pipeline.long.stages.knowledge_boundary_repair."
        "run_knowledge_boundary_repair_loop",
        _fake_loop,
    )

    result = await run_knowledge_boundary_repair_v2(
        runner=_reading_power_runner("manual"),
        storage=SimpleNamespace(),
        bundle=SimpleNamespace(project_id="p"),
        packet=SimpleNamespace(),
        current_text="原文",
        chapter_number=1,
        findings=[SimpleNamespace(blocks_finalize=True, severity="high")],
        trace=SimpleNamespace(),
    )

    assert executed is False
    assert result.current_text == "原文"
    assert result.repair_exhausted is True


@pytest.mark.asyncio
async def test_run_knowledge_boundary_repair_v2_ai_assisted_requests_approval(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executed = False

    async def _fake_loop(**kwargs: Any) -> Any:
        del kwargs
        nonlocal executed
        executed = True

    monkeypatch.setattr(
        "novel_forge.pipeline.long.stages.knowledge_boundary_repair."
        "run_knowledge_boundary_repair_loop",
        _fake_loop,
    )

    result = await run_knowledge_boundary_repair_v2(
        runner=_reading_power_runner("ai_assisted"),
        storage=SimpleNamespace(),
        bundle=SimpleNamespace(project_id="p"),
        packet=SimpleNamespace(),
        current_text="原文",
        chapter_number=1,
        findings=[SimpleNamespace(blocks_finalize=True, severity="high")],
        trace=SimpleNamespace(),
    )

    assert executed is False
    assert result.current_text == "原文"
    assert result.repair_exhausted is True


@pytest.mark.asyncio
async def test_run_knowledge_boundary_repair_v2_rolls_back_on_budget_excess(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _fake_loop(**kwargs: Any) -> Any:
        return SimpleNamespace(
            current_text="很长" * 100,
            repair_exhausted=False,
            rounds_used=1,
            findings_before=list(kwargs["findings"]),
            findings_after=[],
            repair_attempted=True,
        )

    monkeypatch.setattr(
        "novel_forge.pipeline.long.stages.knowledge_boundary_repair."
        "run_knowledge_boundary_repair_loop",
        _fake_loop,
    )
    runner = _reading_power_runner("ai_auto")
    runner._settings.long_knowledge_boundary_repair_max_change_ratio = 0.01

    result = await run_knowledge_boundary_repair_v2(
        runner=runner,
        storage=SimpleNamespace(),
        bundle=SimpleNamespace(project_id="p"),
        packet=SimpleNamespace(),
        current_text="短",
        chapter_number=1,
        findings=[SimpleNamespace(blocks_finalize=True, severity="high")],
        trace=SimpleNamespace(),
    )

    assert result.current_text == "短"
    assert result.repair_exhausted is True
    assert "repair_v2_rollback" in runner.events


@pytest.mark.asyncio
async def test_run_prompt_leak_repair_v2_executes_legacy_patch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _fake_prompt_repair(**kwargs: Any) -> Any:
        assert kwargs["current_text"] == "原文"
        assert kwargs["chapter_number"] == 1
        assert kwargs["allow_deterministic_fallback"] is True
        return SimpleNamespace(
            text="清理后",
            chapter_repair_report=SimpleNamespace(prompt_leaks=[]),
            repaired_leaks=("泄露",),
            remaining_leaks=(),
            applied=True,
            used_deterministic_fallback=False,
            ignored_leaks=(),
            report_updated=True,
            failure_reason="",
        )

    monkeypatch.setattr(
        "novel_forge.pipeline.long.stages.prompt_leak_repair."
        "repair_confirmed_prompt_leaks_with_patch",
        _fake_prompt_repair,
    )
    events: list[str] = []

    result = await run_prompt_leak_repair_v2(
        router=SimpleNamespace(),
        builder=SimpleNamespace(),
        settings=SimpleNamespace(
            repair_control_mode="ai_auto",
            long_prompt_leak_repair_max_change_ratio=1.0,
        ),
        trace=SimpleNamespace(),
        chapter_number=1,
        current_text="原文",
        chapter_repair_report=SimpleNamespace(prompt_leaks=["泄露"]),
        on_step=lambda event, _payload: events.append(event),
    )

    assert result.text == "清理后"
    assert result.repaired_leaks == ("泄露",)
    assert "repair_v2_target_committed" in events
    assert "repair_audit_event" in events


@pytest.mark.asyncio
async def test_run_prompt_leak_repair_v2_manual_mode_does_not_execute(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executed = False

    async def _fake_prompt_repair(**kwargs: Any) -> Any:
        del kwargs
        nonlocal executed
        executed = True

    monkeypatch.setattr(
        "novel_forge.pipeline.long.stages.prompt_leak_repair."
        "repair_confirmed_prompt_leaks_with_patch",
        _fake_prompt_repair,
    )

    result = await run_prompt_leak_repair_v2(
        router=SimpleNamespace(),
        builder=SimpleNamespace(),
        settings=SimpleNamespace(repair_control_mode="manual"),
        trace=SimpleNamespace(),
        chapter_number=1,
        current_text="原文",
        chapter_repair_report=SimpleNamespace(prompt_leaks=["泄露"]),
    )

    assert executed is False
    assert result.text == "原文"
    assert result.failure_reason == "prompt_leak_repair_requires_human_review"


@pytest.mark.asyncio
async def test_run_prompt_leak_repair_v2_rolls_back_when_leaks_remain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _fake_prompt_repair(**kwargs: Any) -> Any:
        return SimpleNamespace(
            text="仍有泄露",
            chapter_repair_report=kwargs["chapter_repair_report"],
            repaired_leaks=(),
            remaining_leaks=("泄露",),
            applied=False,
            used_deterministic_fallback=False,
            ignored_leaks=(),
            report_updated=False,
            failure_reason="仍检测到泄露",
        )

    monkeypatch.setattr(
        "novel_forge.pipeline.long.stages.prompt_leak_repair."
        "repair_confirmed_prompt_leaks_with_patch",
        _fake_prompt_repair,
    )
    events: list[str] = []

    result = await run_prompt_leak_repair_v2(
        router=SimpleNamespace(),
        builder=SimpleNamespace(),
        settings=SimpleNamespace(repair_control_mode="ai_auto"),
        trace=SimpleNamespace(),
        chapter_number=1,
        current_text="原文",
        chapter_repair_report=SimpleNamespace(prompt_leaks=["泄露"]),
        on_step=lambda event, _payload: events.append(event),
    )

    assert result.text == "原文"
    assert result.failure_reason == "prompt_leak_repair_requires_human_review"
    assert "repair_v2_rollback" in events


def _runtime_contract_review(tmp_path: Any) -> Any:
    plans_dir = tmp_path / "plans"
    plans_dir.mkdir(parents=True, exist_ok=True)
    layout = SimpleNamespace(plans_dir=plans_dir)
    bundle = SimpleNamespace(
        project_id="p",
        layout=layout,
        chapter_outline=SimpleNamespace(chapter_number=1),
    )
    return SimpleNamespace(prepared=SimpleNamespace(bundle=bundle))


@pytest.mark.asyncio
async def test_run_runtime_contract_repair_v2_executes_in_ai_auto(
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _FakeRuntimeContractRepairService:
        def __init__(self, context: Any, *, review: Any, trace: Any) -> None:
            del trace
            self.context = context
            self.review = review

        async def repair(self, *, ticket: Any) -> Any:
            assert ticket.ticket_id == "ticket-1"
            return SimpleNamespace(
                applied=True,
                reason="",
                chapter_contracts={"chapters": []},
                chapter_source_slice=SimpleNamespace(ok=True),
            )

    monkeypatch.setattr(
        "novel_forge.pipeline.long.services.runtime_contract_repair.RuntimeContractRepairService",
        _FakeRuntimeContractRepairService,
    )
    events: list[str] = []
    result = await run_runtime_contract_repair_v2(
        context=SimpleNamespace(
            settings=SimpleNamespace(repair_control_mode="ai_auto"),
            storage=SimpleNamespace(exists=lambda _path: False),
            on_step=lambda event, _payload: events.append(event),
        ),
        review=_runtime_contract_review(tmp_path),
        trace=SimpleNamespace(),
        ticket=SimpleNamespace(ticket_id="ticket-1", issue_type="contract_source_error"),
    )

    assert result.applied is True
    assert result.chapter_contracts == {"chapters": []}
    assert "repair_v2_target_committed" in events


@pytest.mark.asyncio
async def test_run_runtime_contract_repair_v2_ai_assisted_requests_approval(
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executed = False

    class _FakeRuntimeContractRepairService:
        def __init__(self, context: Any, *, review: Any, trace: Any) -> None:
            del context, review, trace

        async def repair(self, *, ticket: Any) -> Any:
            del ticket
            nonlocal executed
            executed = True

    monkeypatch.setattr(
        "novel_forge.pipeline.long.services.runtime_contract_repair.RuntimeContractRepairService",
        _FakeRuntimeContractRepairService,
    )

    result = await run_runtime_contract_repair_v2(
        context=SimpleNamespace(
            settings=SimpleNamespace(repair_control_mode="ai_assisted"),
            storage=SimpleNamespace(exists=lambda _path: False),
            on_step=lambda _event, _payload: None,
        ),
        review=_runtime_contract_review(tmp_path),
        trace=SimpleNamespace(),
        ticket=SimpleNamespace(ticket_id="ticket-1", issue_type="contract_source_error"),
    )

    assert executed is False
    assert result.applied is False
    assert result.reason == "runtime_contract_repair_requires_human_review"


@pytest.mark.asyncio
async def test_run_runtime_contract_repair_v2_rolls_back_artifact_on_verify_failure(
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Storage:
        def exists(self, path: Any) -> bool:
            return path.exists()

        def load_json(self, path: Any) -> dict[str, Any]:
            return json.loads(path.read_text(encoding="utf-8"))

        def save_json(self, path: Any, payload: dict[str, Any]) -> None:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    storage = _Storage()
    review = _runtime_contract_review(tmp_path)
    contracts_path = review.prepared.bundle.layout.plans_dir / "chapter_contracts.json"
    storage.save_json(contracts_path, {"old": 1})

    class _FakeRuntimeContractRepairService:
        def __init__(self, context: Any, *, review: Any, trace: Any) -> None:
            del trace
            self.context = context
            self.review = review

        async def repair(self, *, ticket: Any) -> Any:
            del ticket
            path = self.review.prepared.bundle.layout.plans_dir / "chapter_contracts.json"
            self.context.storage.save_json(path, {"new": 1})
            return SimpleNamespace(
                applied=True,
                reason="",
                chapter_contracts=None,
                chapter_source_slice=None,
            )

    monkeypatch.setattr(
        "novel_forge.pipeline.long.services.runtime_contract_repair.RuntimeContractRepairService",
        _FakeRuntimeContractRepairService,
    )
    events: list[str] = []

    result = await run_runtime_contract_repair_v2(
        context=SimpleNamespace(
            settings=SimpleNamespace(repair_control_mode="ai_auto"),
            storage=storage,
            on_step=lambda event, _payload: events.append(event),
        ),
        review=review,
        trace=SimpleNamespace(),
        ticket=SimpleNamespace(ticket_id="ticket-1", issue_type="contract_source_error"),
    )

    assert result.applied is False
    assert result.reason == "runtime_contract_repair_requires_human_review"
    assert storage.load_json(contracts_path) == {"old": 1}
    assert "repair_v2_rollback" in events


def _short_runner(mode: str = "ai_auto") -> Any:
    events: list[str] = []

    def _on_step(event: str, payload: Any) -> None:
        del payload
        events.append(event)

    return SimpleNamespace(
        _settings=SimpleNamespace(
            repair_control_mode=mode,
            short_repair_max_change_ratio=1.0,
        ),
        _on_step=_on_step,
        events=events,
    )


@pytest.mark.asyncio
async def test_run_short_story_repair_v2_executes_completion_in_ai_auto(
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _fake_completion(*args: Any, **kwargs: Any) -> str:
        assert args[6] == "原文"
        assert kwargs["pass_label"] == "short_completion_repair"
        return "完整短篇"

    monkeypatch.setattr(
        "novel_forge.pipeline.short.stages.edit.run_completion_repair",
        _fake_completion,
    )

    runner = _short_runner("ai_auto")
    result = await run_short_story_repair_v2(
        runner=runner,
        layout=SimpleNamespace(short_draft_path=lambda iteration: tmp_path / f"{iteration}.md"),
        spec=SimpleNamespace(),
        beats=SimpleNamespace(),
        blueprint=None,
        execution_plan={},
        current_text="原文",
        issues=["结尾不完整"],
        repair_kind="completion",
        pass_label="short_completion_repair",
        iteration=3,
    )

    assert result == "完整短篇"
    assert "repair_audit_event" in runner.events


@pytest.mark.asyncio
async def test_run_short_story_repair_v2_manual_mode_does_not_execute(
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executed = False

    async def _fake_completion(*args: Any, **kwargs: Any) -> str:
        del args, kwargs
        nonlocal executed
        executed = True
        return "完整短篇"

    monkeypatch.setattr(
        "novel_forge.pipeline.short.stages.edit.run_completion_repair",
        _fake_completion,
    )

    result = await run_short_story_repair_v2(
        runner=_short_runner("manual"),
        layout=SimpleNamespace(short_draft_path=lambda iteration: tmp_path / f"{iteration}.md"),
        spec=SimpleNamespace(),
        beats=SimpleNamespace(),
        blueprint=None,
        execution_plan={},
        current_text="原文",
        issues=["结尾不完整"],
        repair_kind="completion",
        pass_label="short_completion_repair",
        iteration=3,
    )

    assert executed is False
    assert result == "原文"


@pytest.mark.asyncio
async def test_run_short_story_repair_v2_rolls_back_draft_on_budget_excess(
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    draft_path = tmp_path / "3.md"
    draft_path.write_text("旧草稿", encoding="utf-8")

    async def _fake_quality(*args: Any, **kwargs: Any) -> str:
        runner = args[0]
        layout = args[1]
        iteration = int(kwargs["iteration"])
        layout.short_draft_path(iteration).write_text("新草稿", encoding="utf-8")
        runner._on_step("short_quality_repair_after_eval", {})
        return "很长" * 100

    monkeypatch.setattr(
        "novel_forge.pipeline.short.stages.edit.run_quality_repair",
        _fake_quality,
    )
    runner = _short_runner("ai_auto")
    runner._settings.short_repair_max_change_ratio = 0.01

    result = await run_short_story_repair_v2(
        runner=runner,
        layout=SimpleNamespace(short_draft_path=lambda iteration: tmp_path / f"{iteration}.md"),
        spec=SimpleNamespace(),
        beats=SimpleNamespace(),
        blueprint=None,
        execution_plan={},
        current_text="短",
        issues=["质量不足"],
        repair_kind="quality",
        pass_label="short_quality_repair_after_eval",
        iteration=3,
        eval_report=SimpleNamespace(),
    )

    assert result == "短"
    assert draft_path.read_text(encoding="utf-8") == "旧草稿"
    assert "repair_v2_rollback" in runner.events
