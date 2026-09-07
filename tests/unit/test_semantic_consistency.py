from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import ValidationError

from novel_forge.app_service.authoring_commands import AuthoringCommands
from novel_forge.app_service.contracts import JobCommand, JobKind, JobRecord, JobState
from novel_forge.core.authoring import AuthoringPolicy
from novel_forge.core.exceptions import ConsistencyViolationError, RecoveryTarget
from novel_forge.core.schemas.continuity import ChapterPlan
from novel_forge.core.semantic_consistency import semantic_payload_hash
from novel_forge.persistence.authoring_store import AuthoringStore, story_input_version
from novel_forge.persistence.filesystem import FileSystemStorage
from novel_forge.persistence.models import ProjectLayout
from novel_forge.persistence.semantic_consistency import (
    SEMANTIC_STAGE_SPECS,
    clear_semantic_compile_failure,
    load_semantic_source_inputs,
    record_semantic_compile_failure,
    semantic_consistency_view,
    semantic_source_fingerprint,
    semantic_stage_inputs,
)
from novel_forge.pipeline.long.chapter_flow_orchestrate import (
    _run_semantic_source_preflight,
)
from novel_forge.pipeline.long.stages.planning import _run_plan_semantic_consistency
from novel_forge.workspace.contracts import SemanticConsistencyRefreshRequest
from novel_forge.workspace.semantic_consistency import refresh_semantic_consistency


def _semantic_project(
    tmp_path: Any,
    *,
    status: str = "clean",
    runtime_fingerprint: str = "runtime-v1",
) -> tuple[FileSystemStorage, ProjectLayout]:
    storage = FileSystemStorage(tmp_path)
    layout = ProjectLayout(storage.ensure_project_dir("book"))
    layout.ensure_dirs()
    storage.save_json(layout.spec_path, {"genre": "悬疑", "premise": "制度谜案"})
    storage.save_json(layout.bible_path, {"world_rules": ["承诺必须兑现"]})
    storage.save_json(layout.characters_path, {"characters": [{"name": "沈昭"}]})
    storage.save_json(layout.blueprint_path, {"acts": [{"goal": "查明真相"}]})
    storage.save_json(
        layout.outline_path,
        {"chapters": [{"chapter_number": 1, "goal": "核验制度"}]},
    )
    storage.save_json(
        layout.narrative_contract_path,
        {"llm_contract": {"rules": ["不得改变作者锁定结局"]}},
    )
    storage.save_json(
        layout.plans_dir / "chapter_contracts.json",
        {"chapter_contracts": [{"chapter_number": 1, "required_events": ["完成核验"]}]},
    )
    storage.save_json(
        layout.reports_dir / "init_coherence_profile.json",
        {"project_ontology": {"entities": ["沈昭"]}, "summary": "制度悬疑"},
    )
    sources, _profile = load_semantic_source_inputs(storage, layout)
    stage_inputs = semantic_stage_inputs(sources)
    stages: dict[str, Any] = {}
    for stage, _repair_artifact, keys in SEMANTIC_STAGE_SPECS:
        stages[stage] = {
            "artifact_hashes": {
                key: semantic_payload_hash(stage_inputs[stage][key]) for key in keys
            },
            "compiler_runtime_fingerprint": runtime_fingerprint,
            "claim_coverage": {"complete": True, "status": "complete"},
            "pair_coverage": {"complete": True, "status": "complete"},
            "semantic_status": status,
        }
        storage.save_json(
            layout.reports_dir / f"{stage}.json",
            {
                "stage": stage,
                "verdict": "accept" if status == "clean" else "needs_repair",
                "issues": [] if status == "clean" else [{"id": f"{stage}-issue"}],
            },
        )
    storage.save_json(
        layout.memory_dir / "init_coherence_claim_ledger.json",
        {
            "schema_version": 1,
            "claims_by_id": {},
            "active_claim_ids": [],
            "stages": stages,
            "history": [],
        },
    )
    return storage, layout


def _configure_project(layout: ProjectLayout, *, mode: str = "coauthor") -> None:
    store = AuthoringStore(layout.root)
    policy = store.set_policy(
        AuthoringPolicy(mode=mode, start_chapter=1, end_chapter=1),
        expected_version=0,
    )
    store.start(
        expected_version=policy.version,
        input_version=story_input_version(layout.root),
    )


def test_semantic_projection_is_content_addressed_and_failure_is_not_clean(tmp_path) -> None:
    storage, layout = _semantic_project(tmp_path)

    clean = semantic_consistency_view(
        storage, layout, expected_runtime_fingerprint="runtime-v1"
    )
    assert clean.status == "clean"
    assert clean.affected_chapters == [1]
    assert clean.ledger_hash and clean.report_id and clean.source_fingerprint

    outline = storage.load_json(layout.outline_path)
    outline["chapters"][0]["goal"] = "改为另一项核验"
    storage.save_json(layout.outline_path, outline)
    assert semantic_consistency_view(
        storage, layout, expected_runtime_fingerprint="runtime-v1"
    ).status == "stale"

    sources, profile = load_semantic_source_inputs(storage, layout)
    record_semantic_compile_failure(
        storage,
        layout,
        source_fingerprint=semantic_source_fingerprint(sources, profile),
        runtime_fingerprint="runtime-v1",
        reason="provider schema failed",
    )
    failed = semantic_consistency_view(
        storage, layout, expected_runtime_fingerprint="runtime-v1"
    )
    assert failed.status == "failed"
    assert "schema failed" in failed.reason
    clear_semantic_compile_failure(storage, layout)
    assert semantic_consistency_view(
        storage, layout, expected_runtime_fingerprint="runtime-v1"
    ).status == "stale"


def test_semantic_preflight_reads_projection_without_provider_call(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    storage, layout = _semantic_project(tmp_path, status="conflict")
    _configure_project(layout)
    monkeypatch.setattr(
        "novel_forge.pipeline.long.services.init.init_coherence_v2.semantic_compiler_runtime_fingerprint",
        lambda _context: "runtime-v1",
    )
    route = SimpleNamespace(call=pytest.fail)
    events: list[tuple[str, dict[str, Any]]] = []
    context = SimpleNamespace(
        settings=SimpleNamespace(
            long_semantic_consistency_enabled=True,
            long_semantic_consistency_blocking=True,
        ),
        storage=storage,
        router=route,
        on_step=lambda step, payload: events.append((step, payload)),
    )
    with pytest.raises(ConsistencyViolationError) as exc_info:
        _run_semantic_source_preflight(
            context,
            bundle=SimpleNamespace(layout=layout),
            chapter_number=1,
        )
    assert exc_info.value.violation_kind == "upstream_source_conflict"
    assert exc_info.value.replan_target == RecoveryTarget.SEMANTIC
    assert events[-1][0] == "semantic_source_preflight"

    context.settings.long_semantic_consistency_blocking = False
    payload = _run_semantic_source_preflight(
        context,
        bundle=SimpleNamespace(layout=layout),
        chapter_number=1,
    )
    assert payload is not None and payload["shadow_only"] is True


@pytest.mark.asyncio
async def test_plan_semantic_check_reuses_compiler_and_only_replans_plan(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    storage, layout = _semantic_project(tmp_path)
    _configure_project(layout)
    gate_calls: list[dict[str, Any]] = []

    async def _gate(_context: Any, **kwargs: Any) -> dict[str, Any]:
        gate_calls.append(kwargs)
        return {
            "verdict": "needs_repair",
            "blocked": True,
            "issues": [{"id": "plan-source-conflict"}],
            "summary": "Plan 偏离权威来源",
        }

    monkeypatch.setattr(
        "novel_forge.pipeline.long.services.init.init_coherence_v2.semantic_compiler_runtime_fingerprint",
        lambda _context: "runtime-v1",
    )
    monkeypatch.setattr(
        "novel_forge.pipeline.long.services.init.init_coherence_v2.load_reusable_init_coherence_profile",
        lambda _context, require_refined: {"project_ontology": {}},
    )
    monkeypatch.setattr(
        "novel_forge.pipeline.long.services.init.init_coherence_v2.run_init_coherence_v2_gate",
        _gate,
    )
    runner = SimpleNamespace(
        _storage=storage,
        _settings=SimpleNamespace(
            long_semantic_plan_check_enabled=True,
            long_semantic_consistency_blocking=True,
        ),
        _router=SimpleNamespace(),
        _builder=SimpleNamespace(),
        _on_step=lambda *_args: None,
        _call_with_retry=None,
    )
    source_payload = {
        "chapter_outline": {"chapter_number": 1, "goal": "核验制度"},
        "chapter_contract": {"chapter_number": 1, "required_events": ["完成核验"]},
    }
    plan = ChapterPlan.model_validate(
        {
            "opening_contract": "偏离来源",
            "scene_intents": [],
        }
    )
    with pytest.raises(ConsistencyViolationError) as exc_info:
        await _run_plan_semantic_consistency(
            runner,
            SimpleNamespace(
                layout=layout,
                chapter_source_slice=SimpleNamespace(payload=source_payload),
            ),
            plan,
            1,
        )
    assert exc_info.value.replan_target == RecoveryTarget.PLAN
    assert gate_calls[0]["repair_artifact"] == "chapter_plan"
    assert gate_calls[0]["artifacts"]["chapter_plan"]["opening_contract"] == "偏离来源"


@pytest.mark.asyncio
async def test_plan_shadow_without_r7a_admission_makes_zero_model_calls(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    storage, layout = _semantic_project(tmp_path)
    _configure_project(layout)
    monkeypatch.setattr(
        "novel_forge.pipeline.long.services.init.init_coherence_v2.semantic_compiler_runtime_fingerprint",
        lambda _context: "runtime-v1",
    )
    monkeypatch.setattr(
        "novel_forge.pipeline.long.services.init.init_coherence_v2.load_reusable_init_coherence_profile",
        lambda _context, require_refined: {"project_ontology": {}},
    )
    monkeypatch.setattr(
        "novel_forge.pipeline.long.services.init.init_coherence_v2.run_init_coherence_v2_gate",
        pytest.fail,
    )
    events: list[tuple[str, dict[str, Any]]] = []
    runner = SimpleNamespace(
        _storage=storage,
        _settings=SimpleNamespace(
            long_semantic_plan_check_enabled=True,
            long_semantic_consistency_blocking=False,
            repair_real_shadow_enabled=False,
        ),
        _router=SimpleNamespace(),
        _builder=SimpleNamespace(),
        _on_step=lambda step, payload: events.append((step, payload)),
        _call_with_retry=None,
    )
    report = await _run_plan_semantic_consistency(
        runner,
        SimpleNamespace(
            project_id="book",
            layout=layout,
            chapter_source_slice=SimpleNamespace(
                payload={
                    "chapter_outline": {"chapter_number": 1},
                    "chapter_contract": {"chapter_number": 1},
                }
            ),
        ),
        ChapterPlan.model_validate({"opening_contract": "按来源开场", "scene_intents": []}),
        1,
    )

    assert report is None
    assert events[-1][0] == "semantic_plan_shadow_skipped"
    assert events[-1][1]["reason"] == "disabled"
    assert not (layout.root / ".authoring" / "repair_cases" / "shadow_runs.jsonl").exists()


@pytest.mark.asyncio
async def test_admitted_plan_shadow_disables_repair_case_reconciliation(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    storage, layout = _semantic_project(tmp_path)
    _configure_project(layout)
    observed: dict[str, Any] = {}

    async def _gate(context: Any, **_kwargs: Any) -> dict[str, Any]:
        observed["repair_cases_disabled"] = context._semantic_repair_cases_disabled
        return {"verdict": "accept", "blocked": False, "issues": []}

    monkeypatch.setattr(
        "novel_forge.pipeline.long.services.init.init_coherence_v2.semantic_compiler_runtime_fingerprint",
        lambda _context: "runtime-v1",
    )
    monkeypatch.setattr(
        "novel_forge.pipeline.long.services.init.init_coherence_v2.load_reusable_init_coherence_profile",
        lambda _context, require_refined: {"project_ontology": {}},
    )
    monkeypatch.setattr(
        "novel_forge.pipeline.long.services.init.init_coherence_v2.run_init_coherence_v2_gate",
        _gate,
    )
    monkeypatch.setattr(
        "novel_forge.persistence.repair_shadow.RepairShadowLedger.admit",
        lambda *_args, **_kwargs: SimpleNamespace(
            admitted=True,
            logical_id="semantic-shadow-1",
            reason="admitted",
            sample_value=0.0,
        ),
    )
    monkeypatch.setattr(
        "novel_forge.persistence.repair_shadow.RepairShadowLedger.record_semantic_judgment",
        lambda _self, _logical_id, **payload: observed.update(payload),
    )
    runner = SimpleNamespace(
        _storage=storage,
        _settings=SimpleNamespace(
            long_semantic_plan_check_enabled=True,
            long_semantic_consistency_blocking=False,
            repair_real_shadow_enabled=True,
            repair_shadow_budget_usd_30d=1.0,
        ),
        _router=SimpleNamespace(),
        _builder=SimpleNamespace(),
        _on_step=lambda *_args: None,
        _call_with_retry=None,
    )

    report = await _run_plan_semantic_consistency(
        runner,
        SimpleNamespace(
            project_id="book",
            layout=layout,
            chapter_source_slice=SimpleNamespace(
                payload={
                    "chapter_outline": {"chapter_number": 1},
                    "chapter_contract": {"chapter_number": 1},
                }
            ),
        ),
        ChapterPlan.model_validate(
            {"opening_contract": "按来源开场", "scene_intents": []}
        ),
        1,
    )

    assert report is not None and report["verdict"] == "accept"
    assert observed["repair_cases_disabled"] is True
    assert observed["verdict"] == "accept"


@pytest.mark.asyncio
async def test_source_shadow_without_r7a_admission_makes_zero_model_calls(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    storage, layout = _semantic_project(tmp_path)
    _configure_project(layout)
    policy = AuthoringStore(layout.root).policy()
    assert policy is not None
    monkeypatch.setattr(
        "novel_forge.workspace.semantic_consistency.build_init_context",
        lambda _runner, _project_id: SimpleNamespace(layout=layout),
    )
    monkeypatch.setattr(
        "novel_forge.workspace.semantic_consistency.semantic_compiler_runtime_fingerprint",
        lambda _context: "runtime-v1",
    )
    monkeypatch.setattr(
        "novel_forge.workspace.semantic_consistency.refine_init_coherence_profile",
        pytest.fail,
    )
    monkeypatch.setattr(
        "novel_forge.workspace.semantic_consistency.run_init_coherence_v2_gate",
        pytest.fail,
    )
    events: list[tuple[str, dict[str, Any]]] = []
    result = await refresh_semantic_consistency(
        SimpleNamespace(
            storage=storage,
            settings=SimpleNamespace(
                long_semantic_consistency_blocking=False,
                repair_real_shadow_enabled=False,
            ),
            chapter_runner=lambda **_kwargs: SimpleNamespace(),
        ),
        SemanticConsistencyRefreshRequest(
            project_id="book",
            expected_story_version=story_input_version(layout.root),
            expected_policy_version=policy.version,
        ),
        on_step_progress=lambda step, payload: events.append((step, payload)),
    )

    assert result["shadow_skipped"] is True
    assert result["reason"] == "disabled"
    assert events[-1][0] == "semantic_consistency_shadow_skipped"
    assert not (layout.root / ".authoring" / "repair_cases" / "shadow_runs.jsonl").exists()


class _SemanticJobService:
    def __init__(self) -> None:
        self.records: list[JobRecord] = []
        self.commands: list[JobCommand] = []

    def list(self, project_id: str | None = None) -> list[JobRecord]:
        return [
            item for item in reversed(self.records) if project_id is None or item.project_id == project_id
        ]

    def submit(self, command: JobCommand) -> JobRecord:
        self.commands.append(command)
        record = JobRecord(
            job_id=command.job_id,
            kind=command.kind,
            label="刷新语义一致性",
            project_id=command.project_id,
        )
        self.records.append(record)
        return record


def test_semantic_refresh_command_is_path_free_deduplicated_and_retryable(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    storage, layout = _semantic_project(tmp_path)
    _configure_project(layout)
    monkeypatch.setattr(
        "novel_forge.app_service.authoring_commands.require_authoring_rollout",
        lambda: None,
    )
    service = _SemanticJobService()
    commands = AuthoringCommands(storage, service)
    policy = AuthoringStore(layout.root).policy()
    assert policy is not None
    source_version = story_input_version(layout.root)

    first = commands.refresh_semantic_consistency(
        "book",
        expected_story_version=source_version,
        expected_policy_version=policy.version,
    )
    duplicate = commands.refresh_semantic_consistency(
        "book",
        expected_story_version=source_version,
        expected_policy_version=policy.version,
    )
    assert duplicate.job_id == first.job_id
    assert len(service.commands) == 1
    assert set(service.commands[0].payload) == {
        "project_id",
        "expected_story_version",
        "expected_policy_version",
    }

    service.records[0].status = JobState.FAILED
    retry = commands.refresh_semantic_consistency(
        "book",
        expected_story_version=source_version,
        expected_policy_version=policy.version,
    )
    assert retry.job_id.endswith("-attempt-2")
    assert len(service.commands) == 2

    with pytest.raises(ValidationError):
        SemanticConsistencyRefreshRequest.model_validate(
            {
                "project_id": "book",
                "expected_story_version": source_version,
                "expected_policy_version": policy.version,
                "source_path": "/tmp/forbidden.json",
            }
        )
    assert service.commands[-1].kind == JobKind.SEMANTIC_CONSISTENCY
