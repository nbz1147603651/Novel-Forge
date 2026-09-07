from __future__ import annotations

from types import SimpleNamespace

import pytest

from novel_forge.app_service.authoring_proposals import apply_proposal
from novel_forge.core.authoring import (
    AuthoringPolicy,
    AuthoringProposalDecision,
    AuthoringProposalRequest,
)
from novel_forge.persistence.authoring_proposals import ProposalStore
from novel_forge.persistence.authoring_store import (
    AuthoringDeniedError,
    AuthoringStore,
    story_input_version,
)
from novel_forge.persistence.filesystem import (
    FileSystemStorage,
    atomic_write_json,
    atomic_write_text,
)
from novel_forge.persistence.models import ProjectLayout
from novel_forge.workspace.authoring_proposals import create_proposal, decide_proposal


@pytest.fixture
def book(tmp_path):
    storage = FileSystemStorage(tmp_path)
    root = storage.ensure_project_dir("book")
    layout = ProjectLayout(root)
    atomic_write_text(layout.chapter_path(1), "原来的正文。作者的声音。")
    atomic_write_text(layout.chapter_path(2), "后来新增的第二章。")
    store = AuthoringStore(root)
    policy = store.set_policy(AuthoringPolicy(mode="coauthor", end_chapter=10), expected_version=0)
    store.start(expected_version=policy.version, input_version=story_input_version(root))
    return SimpleNamespace(storage=storage), layout


def decision(view, action, **kwargs):
    return AuthoringProposalDecision(
        decision=action,
        candidate_version=view.candidate_version,
        input_version=view.input_version,
        policy_version=view.policy_version,
        **kwargs,
    )


async def proposal(runtime):
    return await create_proposal(
        runtime,
        "book",
        AuthoringProposalRequest(
            command="revise_chapter",
            chapter_number=1,
            candidate="经作者审阅的新正文。保留声音。",
            title="修订本章",
            evidence=["按作者指令调整表述"],
        ),
    )


async def test_pending_rejected_deferred_never_modify_story_or_call_models(book):
    runtime, layout = book
    view = await proposal(runtime)
    before = layout.chapter_path(1).read_bytes()
    with pytest.raises(AuthoringDeniedError):
        await apply_proposal(runtime, None, "book", view.id)
    held = decide_proposal(layout.root, view.id, decision(view, "defer"))
    assert held.status == "deferred"
    rejected = decide_proposal(layout.root, view.id, decision(view, "reject"))
    assert rejected.status == "rejected"
    assert layout.chapter_path(1).read_bytes() == before
    assert ProposalStore(layout.root).path(view.id).exists()
    with pytest.raises(AuthoringDeniedError):
        decide_proposal(layout.root, view.id, decision(view, "accept"))


async def test_edit_requires_new_candidate_acceptance_and_application_preserves_later_chapters(
    book,
):
    runtime, layout = book
    view = await proposal(runtime)
    edited = decide_proposal(
        layout.root, view.id, decision(view, "edit", edited_candidate="作者编辑后的版本。")
    )
    assert edited.candidate_version != view.candidate_version
    with pytest.raises(AuthoringDeniedError):
        decide_proposal(layout.root, view.id, decision(view, "accept"))
    approved = decide_proposal(layout.root, view.id, decision(edited, "accept"))
    assert approved.status == "approved"
    applied = await apply_proposal(runtime, None, "book", view.id)
    assert applied.status == "applied"
    assert layout.chapter_path(1).read_text() == "作者编辑后的版本。"
    assert layout.chapter_path(2).read_text() == "后来新增的第二章。"
    versions = list((layout.states_dir / "final_revision_versions").rglob("*_before.md"))
    assert len(versions) == 1
    assert "原来的正文" in versions[0].read_text()
    await apply_proposal(runtime, None, "book", view.id)
    assert len(list((layout.states_dir / "final_revision_versions").rglob("*_before.md"))) == 1


async def test_old_approval_cannot_override_concurrent_manual_edit(book):
    runtime, layout = book
    view = await proposal(runtime)
    decide_proposal(layout.root, view.id, decision(view, "accept"))
    atomic_write_text(layout.chapter_path(1), "另一个窗口的人工编辑。")
    with pytest.raises(AuthoringDeniedError, match="过期"):
        await apply_proposal(runtime, None, "book", view.id)
    assert layout.chapter_path(1).read_text() == "另一个窗口的人工编辑。"
    assert ProposalStore(layout.root).views()[0].status == "stale"


async def test_plain_chat_does_not_change_proposal_authority(book):
    runtime, layout = book
    view = await proposal(runtime)
    atomic_write_json(layout.root / ".authoring" / "messages.json", {"chat": "解释一下"})
    assert decide_proposal(layout.root, view.id, decision(view, "accept")).status == "approved"


async def test_revision_failure_after_prose_recovers_receipt_without_reinvalidating(
    book, monkeypatch
):
    from novel_forge.workspace import execution_manual_revision as module

    runtime, layout = book
    view = await proposal(runtime)
    decide_proposal(layout.root, view.id, decision(view, "accept"))
    finish = module._finish_revision_commit
    monkeypatch.setattr(
        module,
        "_finish_revision_commit",
        lambda *a, **kw: (_ for _ in ()).throw(OSError("crash after prose")),
    )
    with pytest.raises(OSError, match="crash after prose"):
        await apply_proposal(runtime, None, "book", view.id)
    assert layout.chapter_path(1).read_text() == view.candidate
    atomic_write_text(layout.chapter_path(3), "中断之后新增的第三章")
    checkpoint = layout.chapter_checkpoint_path(3)
    atomic_write_json(checkpoint, {"later": True})
    AuthoringStore(layout.root).stop()
    monkeypatch.setattr(module, "_finish_revision_commit", finish)
    recovered = await apply_proposal(runtime, None, "book", view.id)
    assert recovered.status == "applied"
    assert checkpoint.exists()
    assert layout.chapter_path(3).read_text() == "中断之后新增的第三章"
    assert len(list((layout.states_dir / "final_revision_versions").rglob("*_before.md"))) == 1


async def test_recovery_never_rewrites_new_manual_text(book, monkeypatch):
    from novel_forge.workspace import execution_manual_revision as module

    runtime, layout = book
    view = await proposal(runtime)
    decide_proposal(layout.root, view.id, decision(view, "accept"))
    monkeypatch.setattr(
        module,
        "_finish_revision_commit",
        lambda *a, **kw: (_ for _ in ()).throw(OSError("interrupted")),
    )
    with pytest.raises(OSError):
        await apply_proposal(runtime, None, "book", view.id)
    atomic_write_text(layout.chapter_path(1), "后来亲笔写的新版本")
    with pytest.raises(AuthoringDeniedError):
        await apply_proposal(runtime, None, "book", view.id)
    assert layout.chapter_path(1).read_text() == "后来亲笔写的新版本"


async def test_restore_is_new_revision_preserves_dependencies_and_later_chapters(book):
    from novel_forge.persistence.final_revision_journal import revision_history, revision_text
    from novel_forge.workspace.publication import build_chapter_publication_view

    runtime, layout = book
    previous = layout.chapter_path(1).read_text()
    report = layout.reports_dir / "chapter_002_eval.json"
    atomic_write_json(report, {"score": 9})
    view = await proposal(runtime)
    decide_proposal(layout.root, view.id, decision(view, "accept"))
    await apply_proposal(runtime, None, "book", view.id)
    meta = revision_history(layout, 1)[0]
    assert (layout.root / meta["dependency_snapshot"] / report.relative_to(layout.root)).exists()
    assert revision_text(layout, 1, meta["timestamp"]) == previous
    restore = await create_proposal(
        runtime,
        "book",
        AuthoringProposalRequest(
            command="restore_chapter", chapter_number=1, revision_id=meta["timestamp"]
        ),
    )
    assert layout.chapter_path(1).read_text() == view.candidate
    decide_proposal(layout.root, restore.id, decision(restore, "accept"))
    await apply_proposal(runtime, None, "book", restore.id)
    assert layout.chapter_path(1).read_text() == previous
    assert layout.chapter_path(2).read_text() == "后来新增的第二章。"
    assert len(revision_history(layout, 1)) == 2
    assert (
        build_chapter_publication_view(layout, "book", 1).publication_status
        == "blocked_pending_finalize"
    )
    assert (
        "upstream_revision_requires_review"
        in build_chapter_publication_view(layout, "book", 2).blocking_reasons
    )
    with pytest.raises(ValueError):
        revision_text(layout, 1, "../../spec")


async def test_revision_stop_during_evidence_copy_cannot_commit(book, monkeypatch):
    from novel_forge.workspace import execution_manual_revision as module

    runtime, layout = book
    view = await proposal(runtime)
    decide_proposal(layout.root, view.id, decision(view, "accept"))
    original = layout.chapter_path(1).read_text()
    monkeypatch.setattr(
        module, "preserve_revision_dependencies", lambda *args: AuthoringStore(layout.root).stop()
    )
    with pytest.raises(AuthoringDeniedError):
        await apply_proposal(runtime, None, "book", view.id)
    assert layout.chapter_path(1).read_text() == original


async def test_reader_report_and_export_expose_stale_source_versions(book):
    from novel_forge.api.routes.ui_views import _reader_file_artifact, _short_project_reader_tabs
    from novel_forge.workspace.contracts import ExportBookRequest
    from novel_forge.workspace.execution_export import execute_export_book

    runtime, layout = book
    report = "reports/chapter_001_eval.json"
    atomic_write_json(layout.root / report, {"source_text_hash": "old", "score": 10})
    artifact = _reader_file_artifact(runtime.storage, layout, "report", "评估", report)
    assert artifact["freshness"] == "stale"
    assert "历史参考" in artifact["freshness_message"]
    result = (
        await execute_export_book(runtime, ExportBookRequest(project_id="book", format="markdown"))
    ).result
    import json
    from pathlib import Path

    sources = json.loads(Path(result["source_manifest_path"]).read_text())
    assert sources["warnings"]
    assert sources["chapters"][0]["final_text_hash"]
    assert "原来的正文" in Path(result["path"]).read_text()
    atomic_write_json(layout.root / "reports/eval_report.json", {"score": 10})
    short_tabs = _short_project_reader_tabs(runtime.storage, layout)
    assert all("freshness" not in item for tab in short_tabs for item in tab["artifacts"])


def _commit_checkpoint_predecessors(runtime, layout):
    from novel_forge.core.utils.text_hash import source_text_hash
    from novel_forge.pipeline.artifact_manifest import ArtifactManifest
    from novel_forge.pipeline.finalization_manifest import record_finalization_success

    atomic_write_json(layout.canon_dir / "canon_current.json", {"current_chapter": 2})
    manifest = ArtifactManifest(runtime.storage, layout)
    for chapter in (1, 2):
        for phase in ("final_text", "reports", "narrative_state", "story_kernel"):
            record_finalization_success(
                manifest, chapter_number=chapter, phase=phase,
                text_hash=source_text_hash(layout.chapter_path(chapter).read_text()),
            )


async def test_checkpoint_approval_is_bound_to_option_notes_and_candidate(book):
    runtime, layout = book
    _commit_checkpoint_predecessors(runtime, layout)
    atomic_write_json(
        layout.chapter_checkpoint_path(3),
        {"checkpoint_id": "plan-3", "options": [{"option_id": "write_now"}]},
    )
    atomic_write_json(layout.chapter_session_path(3), {"plan": {"scene": "原定场景"}})
    view = await create_proposal(
        runtime,
        "book",
        AuthoringProposalRequest(
            command="checkpoint", chapter_number=3, option_id="write_now", notes="保留开放结局"
        ),
    )
    decide_proposal(layout.root, view.id, decision(view, "accept"))
    from novel_forge.workspace.authoring_control import authoring_operation

    data = ProposalStore(layout.root).read(view.id)
    request = SimpleNamespace(
        project_id="book",
        chapter_number=3,
        option_id="write_now",
        notes="保留开放结局",
        authoring_approval_id=data["approval_id"],
    )
    with authoring_operation(runtime, request, "generate"):
        pass
    request.notes = "改变结局"
    with pytest.raises(AuthoringDeniedError):
        with authoring_operation(runtime, request, "generate"):
            pytest.fail("must not dispatch")


def test_proposal_paths_and_commands_are_allowlisted(book):
    _, layout = book
    with pytest.raises(ValueError):
        AuthoringProposalRequest(command="write_file", chapter_number=1, candidate="anything")
    with pytest.raises(ValueError):
        ProposalStore(layout.root).read("../../spec")


async def test_approved_checkpoint_reaches_existing_autorun_without_granting_archive(book):
    from novel_forge.app_service.book_autorun import BookAutorunCoordinator, BookAutorunState
    from novel_forge.app_service.contracts import JobRecord

    runtime, layout = book
    _commit_checkpoint_predecessors(runtime, layout)
    atomic_write_json(
        layout.chapter_checkpoint_path(3),
        {"checkpoint_id": "p3", "options": [{"option_id": "write_now"}]},
    )
    atomic_write_json(layout.chapter_session_path(3), {"plan": {"scene": "确定的场景"}})
    view = await create_proposal(
        runtime,
        "book",
        AuthoringProposalRequest(command="checkpoint", chapter_number=3, option_id="write_now"),
    )
    decide_proposal(layout.root, view.id, decision(view, "accept"))
    commands = []
    coordinator = BookAutorunCoordinator(
        storage_root=layout.root.parent,
        submit_command=lambda cmd: (
            commands.append(cmd)
            or JobRecord(job_id=cmd.job_id, kind=cmd.kind, label="resolve", project_id="book")
        ),
        resume_job=lambda *_: None,
        get_job=lambda *_: None,
    )
    coordinator._states["book"] = BookAutorunState(
        project_id="book", current_chapter=3, checkpoint_id="p3", end_chapter=10
    )
    service = SimpleNamespace(resolve_book_autorun_checkpoint=coordinator.resolve_checkpoint)
    result = await apply_proposal(runtime, service, "book", view.id)
    assert result.application_result["task_id"]
    assert commands[0].payload["authoring_approval_id"]
    with pytest.raises(AuthoringDeniedError):
        AuthoringStore(layout.root).require(
            "archive",
            3,
            approval_id=commands[0].payload["authoring_approval_id"],
            candidate_version=view.candidate_version,
        )


async def checkpoint_proposal(book):
    runtime, layout = book
    _commit_checkpoint_predecessors(runtime, layout)
    atomic_write_json(
        layout.chapter_checkpoint_path(3),
        {
            "checkpoint_id": "p3",
            "options": [{"option_id": "write_now"}],
        },
    )
    atomic_write_json(layout.chapter_session_path(3), {"plan": {"scene": "作者确认的方案"}})
    view = await create_proposal(
        runtime,
        "book",
        AuthoringProposalRequest(
            command="checkpoint",
            chapter_number=3,
            option_id="write_now",
        ),
    )
    decide_proposal(layout.root, view.id, decision(view, "accept"))
    return view


@pytest.mark.parametrize("next_checkpoint", [False, True])
async def test_fast_checkpoint_completion_is_not_overwritten_by_submission(book, next_checkpoint):
    from novel_forge.app_service.authoring_proposals import reconcile_checkpoint_job
    from novel_forge.app_service.contracts import JobRecord, JobState

    runtime, layout = book
    view = await checkpoint_proposal(book)
    commands = []

    def submit(command):
        commands.append(command)
        persisted = ProposalStore(layout.root).read(view.id)["view"]["application_result"]
        assert persisted["task_id"] == command.job_id
        assert persisted["status"] == "applying"
        record = JobRecord(
            job_id=command.job_id,
            kind=command.kind,
            label="即时结果",
            project_id="book",
            status=JobState.PAUSED if next_checkpoint else JobState.SUCCEEDED,
            result={"status": "needs_decision", "checkpoint": {"checkpoint_id": "final-3"}}
            if next_checkpoint
            else {"status": "completed"},
        )
        reconcile_checkpoint_job(layout.root, record)
        # The submitter may return an earlier snapshot after the callback finished.
        return record.model_copy(update={"status": JobState.QUEUED, "result": {}})

    service = SimpleNamespace(resolve_book_autorun_checkpoint=lambda **kw: None, submit=submit)
    result = await apply_proposal(runtime, service, "book", view.id)
    assert result.status == "applied"
    assert result.application_result["status"] == (
        "needs_decision" if next_checkpoint else "completed"
    )
    await apply_proposal(runtime, service, "book", view.id)
    assert len(commands) == 1
    if next_checkpoint:
        assert "单独确认" in result.application_result["message"]
    assert not layout.chapter_path(3).exists()  # No archive authority was manufactured.


@pytest.mark.parametrize("reject", [False, True])
async def test_failed_checkpoint_requires_fresh_approval_or_can_be_rejected(book, reject):
    from novel_forge.app_service.authoring_proposals import reconcile_checkpoint_job
    from novel_forge.app_service.contracts import JobRecord, JobState

    runtime, layout = book
    view = await checkpoint_proposal(book)
    records = []

    def submit(command):
        if records:
            reconcile_checkpoint_job(
                layout.root, records[0]
            )  # Late old callback must not revoke retry.
        record = JobRecord(
            job_id=command.job_id,
            kind=command.kind,
            label="结果",
            project_id="book",
            status=JobState.SUCCEEDED if records else JobState.FAILED,
            result={"status": "completed"} if records else {},
            error="" if records else "offline",
        )
        records.append(record)
        return record

    service = SimpleNamespace(resolve_book_autorun_checkpoint=lambda **kw: None, submit=submit)
    pending = await apply_proposal(runtime, service, "book", view.id)
    assert pending.status == "pending"
    data = ProposalStore(layout.root).read(view.id)
    assert not AuthoringStore(layout.root).approval_matches(
        data["approval_id"], "generate", 3, view.candidate_version
    )
    with pytest.raises(AuthoringDeniedError):
        await apply_proposal(runtime, service, "book", view.id)
    decide_proposal(layout.root, view.id, decision(view, "reject" if reject else "accept"))
    if not reject:
        assert (await apply_proposal(runtime, service, "book", view.id)).status == "applied"
        assert records[0].job_id != records[1].job_id
    assert len(records) == (1 if reject else 2)


@pytest.mark.parametrize("completed", [False, True])
async def test_restart_reconciles_checkpoint_receipt_without_dispatch(book, completed):
    from novel_forge.app_service.contracts import JobRecord, JobState
    from novel_forge.app_service.job_service import JobService

    _, layout = book
    view = await checkpoint_proposal(book)
    store = ProposalStore(layout.root)
    data = store.read(view.id)
    task_id = f"proposal-{view.id}-1"
    data["view"]["application_result"] = {"status": "applying", "task_id": task_id, "attempt": 1}
    store.write(data)
    if completed:
        record = JobRecord(
            job_id=task_id,
            kind="resolve_chapter_checkpoint",
            project_id="book",
            label="已完成",
            status=JobState.SUCCEEDED,
            result={"status": "completed"},
        )
        atomic_write_json(
            layout.states_dir / "task_flow_history.json", [record.model_dump(mode="json")]
        )
    service = JobService(storage_root=layout.root.parent)
    try:
        result = store.read(view.id)["view"]
        assert result["status"] == ("applied" if completed else "pending")
        assert not service._workers and not service._pending_jobs
        if not completed:
            assert "重新批准" in result["application_result"]["error"]
    finally:
        service.shutdown(wait_s=0)


@pytest.mark.parametrize("crash_after_commit", [False, True])
async def test_only_validated_planning_candidate_can_be_approved_and_published(
    book, monkeypatch, crash_after_commit
):
    from novel_forge.persistence.authoring_store import content_version
    from novel_forge.persistence.planning_revision import PlanningRevision
    from novel_forge.workspace.planning_jobs import planning_job_path

    runtime, layout = book
    atomic_write_json(layout.outline_path, {"total_chapters": 100, "hard_through_chapter": 5})
    revision = PlanningRevision(layout.root, "book")
    atomic_write_json(
        revision.project / "outline.json", {"total_chapters": 100, "hard_through_chapter": 10}
    )
    request = AuthoringProposalRequest(
        command="publish_planning", chapter_number=6, revision_id=revision.revision_id
    )
    with pytest.raises(ValueError, match="严格同步"):
        await create_proposal(runtime, "book", request)
    atomic_write_json(
        planning_job_path(layout.root),
        {
            "status": "candidate",
            "request_id": "planning",
            "result": {
                "revision_id": revision.revision_id,
                "candidate_version": content_version(revision.changes()),
            },
        },
    )
    view = await create_proposal(runtime, "book", request)
    with pytest.raises(AuthoringDeniedError):
        revision.publish()
    decide_proposal(layout.root, view.id, decision(view, "accept"))
    if crash_after_commit:
        from novel_forge.persistence import planning_revision as module

        preserve = module._preserve_receipt
        monkeypatch.setattr(
            module,
            "_preserve_receipt",
            lambda *a: (_ for _ in ()).throw(OSError("receipt interrupted")),
        )
        with pytest.raises(OSError, match="receipt interrupted"):
            await apply_proposal(runtime, None, "book", view.id)
        monkeypatch.setattr(module, "_preserve_receipt", preserve)
        AuthoringStore(layout.root).stop()
        atomic_write_text(layout.chapter_path(3), "后来新增正文")
        assert ProposalStore(layout.root).views()[0].application_result["recoverable_receipt"]
    result = await apply_proposal(runtime, None, "book", view.id)
    assert result.status == "applied"
    assert runtime.storage.load_json(layout.outline_path) == {
        "total_chapters": 100,
        "hard_through_chapter": 10,
    }


def test_authoritative_blueprint_edits_invalidate_approval_inputs(book):
    _, layout = book
    old = story_input_version(layout.root)
    atomic_write_json(layout.plans_dir / "narrative_blueprint.json", {"ending": "开放结局"})
    assert story_input_version(layout.root) != old
