from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path

import pytest

from novel_forge.core.authoring import AuthoringPolicy, authoring_permission
from novel_forge.core.user_intent import build_persisted_user_intent_card
from novel_forge.persistence.authoring_store import (
    AuthoringDeniedError,
    AuthoringStore,
    story_input_version,
)
from novel_forge.persistence.filesystem import atomic_write_json
from novel_forge.persistence.planning_revision import PlanningRevision
from novel_forge.pipeline.long.human_decision import (
    AutoDecisionProvider,
    CallbackDecisionProvider,
    HumanDecisionOption,
    HumanDecisionRequest,
)


def active_store(tmp_path: Path, mode: str = "coauthor") -> AuthoringStore:
    store = AuthoringStore(tmp_path)
    policy = store.set_policy(AuthoringPolicy(mode=mode, end_chapter=100), expected_version=0)
    assert policy.stopped
    store.start(expected_version=policy.version, input_version=story_input_version(tmp_path))
    return store


@pytest.mark.parametrize("mode", ["manual", "coauthor", "authorized_auto"])
def test_major_changes_require_versioned_approval(mode: str):
    policy = AuthoringPolicy(mode=mode, stopped=False)
    for action in ("revise", "extend", "archive", "publish_planning"):
        result = authoring_permission(policy, action, 1, explicit=True, major_change=True)
        assert not result.allowed
        assert result.requires_approval


def test_coauthor_plan_approval_does_not_approve_archive(tmp_path: Path):
    store = active_store(tmp_path)
    approval = store.approve(
        action="generate",
        chapter=1,
        candidate_version="plan-a",
        input_version=story_input_version(tmp_path),
        policy_version=2,
    )
    store.require("generate", 1, approval_id=approval, candidate_version="plan-a")
    with pytest.raises(AuthoringDeniedError):
        store.require("archive", 1, approval_id=approval, candidate_version="plan-a")
    with pytest.raises(AuthoringDeniedError):
        store.require("generate", 1, approval_id=approval, candidate_version="plan-b")


def test_chat_does_not_invalidate_approval_but_author_edit_and_stop_do(tmp_path: Path):
    store = active_store(tmp_path)
    approval = store.approve(
        action="archive",
        chapter=1,
        candidate_version="draft-a",
        input_version=story_input_version(tmp_path),
        policy_version=2,
    )
    atomic_write_json(store.directory / "conversation.json", {"message": "聊聊节奏"})
    store.require("archive", 1, approval_id=approval, candidate_version="draft-a")
    atomic_write_json(tmp_path / "spec.json", {"ending_style": "开放结局"})
    with pytest.raises(AuthoringDeniedError):
        store.require("archive", 1, approval_id=approval, candidate_version="draft-a")
    store.stop()
    with pytest.raises(AuthoringDeniedError):
        store.require("prepare", 1, explicit=True)


def test_scope_budget_and_policy_cas(tmp_path: Path):
    store = active_store(tmp_path)
    with pytest.raises(AuthoringDeniedError):
        store.set_policy(AuthoringPolicy(), expected_version=1)
    assert not authoring_permission(
        AuthoringPolicy(stopped=False, budget_usd=0), "prepare", 1, explicit=True
    ).allowed
    assert not authoring_permission(
        AuthoringPolicy(stopped=False), "prepare", 2, explicit=True
    ).allowed
    assert not authoring_permission(
        AuthoringPolicy(stopped=False), "write_arbitrary_file", 1, explicit=True
    ).allowed


def test_authoring_chapter_cannot_skip_unaccepted_predecessor(tmp_path: Path):
    from types import SimpleNamespace

    from novel_forge.app_service.authoring import authoring_session
    from novel_forge.persistence.filesystem import FileSystemStorage
    from novel_forge.workspace.authoring_control import authoring_operation

    store = active_store(tmp_path, "authorized_auto")
    runtime = SimpleNamespace(storage=FileSystemStorage(tmp_path.parent))
    request = SimpleNamespace(project_id=tmp_path.name, chapter_number=2)
    for action in ("prepare", "generate", "archive"):
        with pytest.raises(AuthoringDeniedError, match="第 1 章"):
            with authoring_operation(runtime, request, action):
                pytest.fail("must not dispatch")
    # Future outline discussion/candidates are distinct from chapter execution.
    store.require("plan_candidates", 2, explicit=True)
    view = authoring_session(tmp_path, tmp_path.name, 2)
    permissions = {item.action: item for item in view.allowed_actions}
    assert not permissions["prepare"].allowed
    assert "第 1 章" in permissions["prepare"].reason
    assert permissions["discuss"].allowed and permissions["plan_candidates"].allowed


def test_authoring_next_chapter_waits_for_same_text_required_state(tmp_path: Path):
    from novel_forge.core.utils.text_hash import source_text_hash
    from novel_forge.persistence.filesystem import FileSystemStorage
    from novel_forge.persistence.models import ProjectLayout
    from novel_forge.pipeline.artifact_manifest import ArtifactManifest
    from novel_forge.pipeline.finalization_manifest import (
        record_finalization_success,
        require_authoring_chapter_predecessor,
    )

    active_store(tmp_path, "authorized_auto")
    layout = ProjectLayout(tmp_path)
    storage = FileSystemStorage(tmp_path.parent)
    storage.save_text(layout.chapter_path(1), "已验收的正文")
    storage.save_json(layout.canon_dir / "canon_current.json", {"current_chapter": 1})
    manifest = ArtifactManifest(storage, layout)
    text_hash = source_text_hash("已验收的正文")
    for phase in ("final_text", "reports"):
        record_finalization_success(manifest, chapter_number=1, phase=phase, text_hash=text_hash)
    with pytest.raises(AuthoringDeniedError, match="必需状态"):
        require_authoring_chapter_predecessor(tmp_path, 2, "prepare")
    for phase in ("narrative_state", "story_kernel"):
        record_finalization_success(manifest, chapter_number=1, phase=phase, text_hash=text_hash)
    require_authoring_chapter_predecessor(tmp_path, 2, "prepare")
    storage.save_text(layout.chapter_path(1), "作者又修改了正文")
    with pytest.raises(AuthoringDeniedError):
        require_authoring_chapter_predecessor(tmp_path, 2, "prepare")


def test_planning_chat_exclusion_still_checks_policy(tmp_path: Path):
    project = tmp_path / "book"
    project.mkdir()
    atomic_write_json(project / "outline.json", {"total": 100})
    active_store(project)
    revision = PlanningRevision(project, "book")
    atomic_write_json(project / ".authoring" / "conversation.json", {"message": "解释一下"})
    revision.publish()
    other = PlanningRevision(project, "book")
    AuthoringStore(project).stop()
    with pytest.raises(ValueError, match="source changed"):
        other.publish()


def test_intent_snapshot_keeps_provenance_and_does_not_promote_model_content():
    card = build_persisted_user_intent_card(
        {
            "request": {
                "init_input": {
                    "ending_style": "开放结局",
                    "pov_hint": "单视角",
                    "extra_instructions": "主角不能死亡",
                }
            }
        },
        {"theme": "AI 补充的设定"},
    )
    assert len(card["explicit_intents"]) == 3
    assert card["source"] == "init_request_snapshot"
    assert card["accepted_project_facts"]["theme"] == "AI 补充的设定"
    legacy = build_persisted_user_intent_card({}, {"theme": "来源不明"})
    assert legacy["explicit_intents"] == []


def required_decision() -> HumanDecisionRequest:
    return HumanDecisionRequest(
        "candidate-a",
        "init_copilot_gate",
        "book",
        0,
        "确认",
        "候选",
        (HumanDecisionOption("accept", "接受"),),
        "accept",
        timeout_seconds=1,
    )


async def test_required_decision_no_default_no_cross_candidate_replay():
    request = required_decision()
    with pytest.raises(ValueError, match="明确批准"):
        await AutoDecisionProvider().request_decision(request)
    provider = CallbackDecisionProvider(
        lambda _: None,
        preseeded_decisions=[
            {
                "kind": request.kind,
                "chapter_number": 0,
                "choice": "accept",
                "decision_id": request.decision_id,
                "approval_version": "old",
            }
        ],
    )
    assert provider._match_preseeded(request) is None
    task = asyncio.create_task(provider.request_decision(request))
    await asyncio.sleep(0)
    assert not provider.provide_decision(request.decision_id, "not-an-option")
    assert not provider.provide_decision(request.decision_id, "accept", timed_out=True)
    assert not task.done()
    assert not provider.provide_decision(request.decision_id, "accept")
    assert not provider.provide_decision(request.decision_id, "accept", approval_version="stale")
    assert provider.provide_decision(
        request.decision_id, "accept", approval_version=request.approval_version
    )
    assert (await task).choice == "accept"


async def test_required_decision_replay_requires_same_candidate_and_options():
    request = required_decision()
    seed = {**request.to_payload(), "choice": "accept"}
    provider = CallbackDecisionProvider(
        lambda _: pytest.fail("unexpected prompt"), preseeded_decisions=[seed]
    )
    assert (await provider.request_decision(request)).choice == "accept"
    assert provider._match_preseeded(replace(request, metadata={"input": "edited"})) is None


def test_available_authoring_does_not_adopt_or_start_legacy_projects(tmp_path: Path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from novel_forge.api.deps import get_storage
    from novel_forge.api.routes.authoring import router
    from novel_forge.app_service.engine_views import engine_capabilities
    from novel_forge.persistence.filesystem import FileSystemStorage

    storage = FileSystemStorage(tmp_path)
    storage.ensure_project_dir("book")
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_storage] = lambda: storage
    with TestClient(app) as client:
        response = client.get("/authoring/book")
        assert response.status_code == 200
        view = response.json()
        assert view["configured"] is False
        assert view["policy"]["mode"] == "manual"
        assert view["policy"]["stopped"] is True
        assert client.get("/authoring/book?chapter=0").status_code == 422
    assert engine_capabilities().features["authoring_coauthor"] is True
    assert not (storage.existing_project_dir("book") / "authoring_policy.json").exists()


def test_running_policy_tightening_stops_dispatch_and_archive(tmp_path: Path):
    from types import SimpleNamespace

    from novel_forge.core.authoring_context import check_authoring_authority
    from novel_forge.persistence.authoring_store import require_archive_authority
    from novel_forge.persistence.filesystem import FileSystemStorage
    from novel_forge.workspace.authoring_control import authoring_operation

    storage = FileSystemStorage(tmp_path)
    root = storage.ensure_project_dir("book")
    store = active_store(root, "authorized_auto")
    request = SimpleNamespace(project_id="book", chapter_number=1)
    with authoring_operation(SimpleNamespace(storage=storage), request, "prepare"):
        check_authoring_authority()
        store.stop()
        with pytest.raises(AuthoringDeniedError):
            check_authoring_authority()
        with pytest.raises(AuthoringDeniedError):
            require_archive_authority(root, 1)


def test_exact_final_prose_needs_fresh_acceptance(tmp_path: Path):
    from novel_forge.core.utils.text_hash import source_text_hash
    from novel_forge.persistence.authoring_store import (
        AuthoringAcceptanceRequired,
        AuthoringExecution,
        active_authoring,
        require_archive_authority,
    )

    store = active_store(tmp_path)
    approval = store.approve(
        action="archive",
        chapter=1,
        candidate_version="candidate",
        input_version=story_input_version(tmp_path),
        policy_version=2,
    )
    token = active_authoring.set(
        AuthoringExecution(
            tmp_path, 2, 1, approval, "candidate", source_text_hash("作者验收的文本")
        )
    )
    try:
        require_archive_authority(tmp_path, 1, "作者验收的文本")
        with pytest.raises(AuthoringAcceptanceRequired) as result:
            require_archive_authority(tmp_path, 1, "必要检查后的新文本")
        assert result.value.text == "必要检查后的新文本"
        store.stop()
        with pytest.raises(AuthoringDeniedError):
            require_archive_authority(tmp_path, 1, "作者验收的文本")
    finally:
        active_authoring.reset(token)


def test_live_decision_projection_and_missing_version_cannot_resume(tmp_path: Path):
    from novel_forge.app_service.contracts import JobKind, JobRecord, JobState
    from novel_forge.app_service.engine_views import project_job_view
    from novel_forge.app_service.job_service import JobService

    service = JobService(storage_root=tmp_path, load_persisted_history=False)
    record = JobRecord(
        job_id="decision-job",
        kind=JobKind.INIT_LONG,
        label="确认",
        project_id="book",
        status=JobState.RUNNING,
        pending_decision=required_decision().to_payload(),
    )
    service._jobs[record.job_id] = record
    view = project_job_view(record)
    assert view.state == "paused"
    assert view.decisions[0]["approval_version"] == required_decision().approval_version
    with pytest.raises(ValueError, match="批准版本"):
        service.provide_decision(record.job_id, {"decision_id": "candidate-a", "choice": "accept"})
    service.shutdown()


def test_autorun_checkpoint_callback_does_not_bypass_coauthor_approval(tmp_path: Path):
    from novel_forge.app_service.book_autorun import BookAutorunCoordinator, BookAutorunState

    root = tmp_path / "book"
    root.mkdir()
    active_store(root)
    dispatched = []
    coordinator = BookAutorunCoordinator(
        storage_root=tmp_path,
        submit_command=dispatched.append,
        resume_job=lambda *args: None,
        get_job=lambda _: None,
    )
    state = BookAutorunState(project_id="book", checkpoint_id="guard-1")
    assert coordinator._dispatch_checkpoint_locked(state, option_id="accept_and_finalize") is None
    assert not dispatched
    assert state.status == "paused"
    assert state.checkpoint_attempts == {}
    assert (
        coordinator._recommended_option(
            {
                "options": [
                    {"option_id": "pause_for_human", "is_recommended": True},
                    {"option_id": "accept_and_finalize"},
                ]
            }
        )
        == ""
    )
