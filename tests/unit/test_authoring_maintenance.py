from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from novel_forge.core.authoring import AuthoringPolicy, AuthoringProposalDecision
from novel_forge.persistence.authoring_proposals import ProposalStore
from novel_forge.persistence.authoring_store import (
    AuthoringDeniedError,
    AuthoringStore,
    assert_authoring_current,
    story_input_version,
)
from novel_forge.persistence.filesystem import atomic_write_json, atomic_write_text
from novel_forge.persistence.planning_revision import PlanningRevision
from novel_forge.persistence.project_staleness import regenerate_from_chapter_async
from novel_forge.pipeline.steps.polish_step import PolishResult
from novel_forge.workspace.authoring_control import authoring_operation, authoring_review
from novel_forge.workspace.authoring_proposals import decide_proposal
from novel_forge.workspace.contracts import (
    InitLongRequest,
    PolishChapterRequest,
    RebuildMemoryVectorsRequest,
    RepairMotifHistoryRequest,
    SyncChapterContractsRequest,
)
from novel_forge.workspace.execution_polish import execute_polish_chapter
from novel_forge.workspace.helpers.execution_runners import (
    execute_init_long,
    execute_sync_chapter_contracts,
)
from tests.unit.test_workspace_execution import _build_runtime, _write_minimal_chapter_state


@pytest.fixture
def book(tmp_path, runtime_settings):
    runtime = _build_runtime(tmp_path, runtime_settings)
    layout = _write_minimal_chapter_state(runtime.storage, "book", 1)
    atomic_write_text(layout.chapter_path(1), "已经归档的正文")
    store = AuthoringStore(layout.root)
    policy = store.set_policy(AuthoringPolicy(mode="coauthor", end_chapter=2), expected_version=0)
    store.start(expected_version=policy.version, input_version=story_input_version(layout.root))
    return runtime, layout, store


async def test_polish_only_proposes_and_preserves_report_evidence(book, monkeypatch):
    runtime, layout, _ = book
    atomic_write_json(layout.eval_report_path(1), {"source_text_hash": "old", "score": 7})
    before = layout.eval_report_path(1).read_bytes()

    async def polish(*args, **kwargs):
        return PolishResult(
            polished_text="新的文学候选", original_word_count=7, polished_word_count=6
        )

    monkeypatch.setattr("novel_forge.pipeline.steps.polish_step.PolishStep.run", polish)
    result = await execute_polish_chapter(
        runtime, PolishChapterRequest(project_id="book", chapter_number=1)
    )
    assert result.result["status"] == "candidate"
    assert layout.chapter_path(1).read_text() == "已经归档的正文"
    assert layout.eval_report_path(1).read_bytes() == before
    view = ProposalStore(layout.root).views()[0]
    assert view.candidate == "新的文学候选" and view.status == "pending"
    decide_proposal(
        layout.root,
        view.id,
        AuthoringProposalDecision(
            decision="reject",
            candidate_version=view.candidate_version,
            input_version=view.input_version,
            policy_version=view.policy_version,
        ),
    )
    assert layout.chapter_path(1).read_text() == "已经归档的正文"


async def test_polish_concurrent_human_edit_invalidates_generated_candidate(book, monkeypatch):
    runtime, layout, _ = book

    async def polish(*args, **kwargs):
        atomic_write_text(layout.chapter_path(1), "作者新改")
        return PolishResult(
            polished_text="旧输入下的建议", original_word_count=7, polished_word_count=7
        )

    monkeypatch.setattr("novel_forge.pipeline.steps.polish_step.PolishStep.run", polish)
    with pytest.raises(AuthoringDeniedError, match="改变"):
        await execute_polish_chapter(
            runtime, PolishChapterRequest(project_id="book", chapter_number=1)
        )
    assert layout.chapter_path(1).read_text() == "作者新改"
    assert not ProposalStore(layout.root).views()


async def test_reinitialize_and_destructive_regeneration_cannot_bypass_authority(book):
    runtime, layout, _ = book
    with pytest.raises(AuthoringDeniedError):
        await execute_init_long(
            runtime, InitLongRequest(project_id="book", premise="覆盖旧书", regenerate_outline=True)
        )
    with pytest.raises(AuthoringDeniedError):
        await regenerate_from_chapter_async(runtime.storage, layout, 1)
    assert layout.chapter_path(1).read_text() == "已经归档的正文"


async def test_legacy_repair_does_not_touch_formal_artifacts_or_trust_protocol_flag(book):
    from novel_forge.pipeline.repair_orchestration import RepairMission, RepairTarget
    from novel_forge.workspace.repair_ops.execution_repair_v2 import execute_repair

    runtime, layout, _ = book
    with pytest.raises(AuthoringDeniedError, match="专项批准"):
        await execute_repair(
            runtime,
            RepairMission(
                project_id="book",
                targets=[
                    RepairTarget(domain="continuity", surface="chapter_text", chapter_number=1)
                ],
                source_context={"protocol_only": True},
            ),
        )
    assert not (layout.states_dir / "repair_v2_snapshots").exists()
    assert layout.chapter_path(1).read_text() == "已经归档的正文"


def test_disabled_marker_blocks_dispatch_even_if_policy_stop_write_failed(book):
    runtime, layout, _ = book
    request = SimpleNamespace(project_id="book", chapter_number=1)
    with authoring_operation(runtime, request, "repair"):
        atomic_write_json(layout.root / ".authoring/disabled.json", {"disabled": True})
        with pytest.raises(AuthoringDeniedError, match="停用"):
            assert_authoring_current()


def test_audit_scope_uses_actual_archived_chapters(book):
    runtime, layout, _ = book
    atomic_write_text(layout.chapter_path(3), "第三章")
    with pytest.raises(AuthoringDeniedError, match="章段"):
        with authoring_review(runtime, SimpleNamespace(project_id="book", chapter_range=[])):
            pytest.fail("must not run")


async def test_contract_sync_prepares_one_candidate_without_recursive_rerouting(book, monkeypatch):
    from novel_forge.workspace import execution_outline_polish
    from novel_forge.workspace.execution_result import ExecutionResult

    runtime, layout, _ = book
    calls = []

    async def sync(candidate_runtime, request, **kwargs):
        from novel_forge.persistence.foundation_guard import is_isolated_planning_candidate

        candidate = candidate_runtime.storage.existing_project_dir("book")
        assert candidate != layout.root and is_isolated_planning_candidate(candidate)
        assert kwargs["strict"] is True
        calls.append(candidate)
        candidate_runtime.storage.save_json(
            candidate / "plans/chapter_contracts.json",
            {"chapter_contracts": [{"chapter_number": 1, "required_events": ["新契约"]}]},
        )
        return ExecutionResult(project_id="book", result={"status": "completed"})

    monkeypatch.setattr(execution_outline_polish, "execute_sync_chapter_contracts", sync)
    original = layout.outline_path.read_bytes()
    result = await execute_sync_chapter_contracts(
        runtime, SyncChapterContractsRequest(project_id="book", affected_chapter_numbers=[1])
    )
    assert result.result["status"] == "candidate" and len(calls) == 1
    assert layout.outline_path.read_bytes() == original
    view = ProposalStore(layout.root).views()[0]
    assert view.status == "pending"
    revision = PlanningRevision.load(layout.root, "book", result.result["revision_id"])
    assert revision.validation()


@pytest.mark.parametrize("force_reextract", [False, True])
async def test_whole_memory_maintenance_cannot_bypass_authority(book, force_reextract):
    from novel_forge.workspace.execution_memory import execute_rebuild_memory_vectors
    from novel_forge.workspace.repair_ops.execution_repair_continuity import (
        execute_repair_motif_history,
    )

    runtime, layout, _ = book
    runtime.get_memory_context = AsyncMock(side_effect=AssertionError("must not load/write memory"))
    with pytest.raises(AuthoringDeniedError, match="专项批准"):
        await execute_rebuild_memory_vectors(
            runtime, RebuildMemoryVectorsRequest(project_id="book")
        )
    with pytest.raises(AuthoringDeniedError, match="专项批准"):
        await execute_repair_motif_history(
            runtime,
            RepairMotifHistoryRequest(
                project_id="book", chapter_number=1, force_re_extract=force_reextract
            ),
        )
    runtime.get_memory_context.assert_not_called()
    assert layout.chapter_path(1).read_text() == "已经归档的正文"


@pytest.mark.parametrize("endpoint", ["reset", "rebuild", "expression/rebuild"])
def test_memory_http_maintenance_returns_explanation_without_side_effects(book, endpoint):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from novel_forge.api.deps import get_runtime_services
    from novel_forge.api.routes.memory import router

    runtime, layout, _ = book
    runtime.get_memory_context = AsyncMock(side_effect=AssertionError("must not load/write memory"))
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_runtime_services] = lambda: runtime
    with TestClient(app) as client:
        response = client.post(f"/{endpoint}/book")
    assert response.status_code == 409 and "专项批准" in response.text
    runtime.get_memory_context.assert_not_called()
    assert layout.chapter_path(1).read_text() == "已经归档的正文"


@pytest.mark.parametrize("kind", ["compress", "critique"])
@pytest.mark.parametrize("stop_during_call", [False, True])
async def test_memory_model_routes_share_authority_and_cost_ledger(book, kind, stop_during_call):
    from fastapi import HTTPException

    from novel_forge.api.routes import memory
    from novel_forge.core.authoring_context import begin_authoring_call, settle_authoring_call
    from novel_forge.persistence.authoring_budget import AuthoringBudget
    from novel_forge.persistence.authoring_store import active_authoring

    runtime, layout, store = book

    async def model_result(**kwargs):
        execution = active_authoring.get()
        assert execution.root == layout.root and execution.chapter == 1
        begin_authoring_call("offline-memory-call", 0.25, {})
        if stop_during_call:
            store.stop()
        settle_authoring_call("offline-memory-call", 0.25)
        if kind == "compress":
            assert kwargs["current_chapter"] == 0  # scope stays 1 even without enrichment
            return SimpleNamespace(
                compressed_text="建议摘要",
                quality_score=0.9,
                original_length=100,
                compressed_length=10,
                compression_ratio=0.1,
                retained_facts=[],
                warnings=[],
            )
        return SimpleNamespace(
            overall_score=8,
            has_critical_issues=False,
            requires_revision=False,
            issues=[],
            strengths=[],
        )

    runtime.get_memory_context = AsyncMock(
        return_value=SimpleNamespace(
            episodic_memory=None,
            compression_service=SimpleNamespace(compress_with_quality_check=model_result),
            critic_agent=SimpleNamespace(critique_chapter=model_result),
        )
    )
    call = (
        memory.compress_context(
            "book",
            memory.CompressionRequest(
                original_text="正文", current_chapter=1, use_semantic_enrichment=False
            ),
            runtime,
        )
        if kind == "compress"
        else memory.critique_chapter(
            "book", memory.CritiqueRequest(chapter_number=1, chapter_text="正文"), runtime
        )
    )
    if stop_during_call:
        with pytest.raises(HTTPException) as caught:
            await call
        assert caught.value.status_code == 409
    else:
        await call
    assert active_authoring.get() is None
    assert AuthoringBudget(layout.root).totals()["spent_usd"] == 0.25
    assert layout.chapter_path(1).read_text() == "已经归档的正文"


@pytest.mark.parametrize("kind", ["compress", "critique"])
@pytest.mark.parametrize("reason", ["stopped", "out_of_scope"])
async def test_memory_model_routes_block_before_loading_context(book, kind, reason):
    from fastapi import HTTPException

    from novel_forge.api.routes import memory

    runtime, _, store = book
    runtime.get_memory_context = AsyncMock(side_effect=AssertionError("must not run"))
    chapter = 1 if reason == "stopped" else 3
    if reason == "stopped":
        store.stop()
    with pytest.raises(HTTPException) as caught:
        if kind == "compress":
            await memory.compress_context(
                "book",
                memory.CompressionRequest(original_text="正文", current_chapter=chapter),
                runtime,
            )
        else:
            await memory.critique_chapter(
                "book", memory.CritiqueRequest(chapter_number=chapter, chapter_text="正文"), runtime
            )
    assert caught.value.status_code == 409
    runtime.get_memory_context.assert_not_called()


async def test_memory_compression_cannot_infer_missing_author_scope(book):
    from fastapi import HTTPException

    from novel_forge.api.routes import memory

    runtime, _, _ = book
    runtime.get_memory_context = AsyncMock(side_effect=AssertionError("must not infer a chapter"))
    with pytest.raises(HTTPException) as caught:
        await memory.compress_context(
            "book", memory.CompressionRequest(original_text="正文"), runtime
        )
    assert caught.value.status_code == 409
    runtime.get_memory_context.assert_not_called()
