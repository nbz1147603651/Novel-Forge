from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from novel_forge.core.schemas.outline import ChapterOutline, StoryOutline
from novel_forge.persistence.filesystem import FileSystemStorage
from novel_forge.persistence.models import ProjectLayout
from novel_forge.persistence.planning_revision import PlanningRevision, recover_planning_publish
from novel_forge.workspace import execution_outline_polish
from novel_forge.workspace.contracts import PolishOutlineRequest
from novel_forge.workspace.execution_result import ExecutionResult


async def test_failed_sync_keeps_live_outline_and_contracts(tmp_path, monkeypatch):
    storage, layout = _seed_project(tmp_path)
    storage.save_json(layout.plans_dir / "chapter_contracts.json", {"chapter_contracts": []})
    before = layout.outline_path.read_bytes()
    revision = PlanningRevision(layout.root, "project")
    revision.storage.save_json(
        revision.project / "outline.json", _outline(second_goal="新路线").model_dump(mode="json")
    )

    async def fail_sync(runtime, request, **kwargs):
        # Simulate a partial write inside the candidate, then failure.
        runtime.storage.save_json(
            runtime.storage.project_dir("project") / "plans/chapter_contracts.json", {"bad": True}
        )
        return ExecutionResult(project_id="project", result={"status": "failed"})

    monkeypatch.setattr(execution_outline_polish, "execute_sync_chapter_contracts", fail_sync)
    with pytest.raises(RuntimeError, match="synchronization failed"):
        await execution_outline_polish.publish_outline_revision(
            _Runtime(storage),
            project_id="project",
            revision=revision,
            changed_chapters=[2],
            previous_hash="old",
        )
    assert layout.outline_path.read_bytes() == before
    assert storage.load_json(layout.plans_dir / "chapter_contracts.json") == {
        "chapter_contracts": []
    }


def test_planning_publish_rolls_back_all_files_on_write_failure(tmp_path, monkeypatch):
    from novel_forge.persistence import planning_revision

    storage, layout = _seed_project(tmp_path)
    original = layout.outline_path.read_bytes()
    revision = PlanningRevision(layout.root, "project")
    revision.storage.save_json(revision.project / "outline.json", {"candidate": True})
    revision.storage.save_json(revision.project / "plans/new_contracts.json", {"new": True})
    original_write = planning_revision.atomic_write_text
    failed = False

    def fail_once(path, text, **kwargs):
        nonlocal failed
        if path == layout.plans_dir / "new_contracts.json" and not failed:
            failed = True
            raise OSError("disk failure")
        return original_write(path, text, **kwargs)

    monkeypatch.setattr(planning_revision, "atomic_write_text", fail_once)
    with pytest.raises(OSError, match="disk failure"):
        revision.publish()
    assert layout.outline_path.read_bytes() == original
    assert not (layout.plans_dir / "new_contracts.json").exists()
    assert storage.load_json(layout.root / "planning_publish.json")["status"] == "rolled_back"


def test_planning_publish_rejects_concurrent_source_edits(tmp_path):
    storage, layout = _seed_project(tmp_path)
    revision = PlanningRevision(layout.root, "project")
    revision.storage.save_json(revision.project / "outline.json", {"candidate": True})
    storage.save_json(layout.outline_path, {"human_edit": True})
    with pytest.raises(ValueError, match="source changed"):
        revision.publish()
    assert storage.load_json(layout.outline_path) == {"human_edit": True}


def test_planning_publish_includes_future_cache_invalidation(tmp_path):
    storage, layout = _seed_project(tmp_path)
    storage.save_json(layout.chapter_source_slice_path(2), {"old_plan": True})
    revision = PlanningRevision(layout.root, "project")
    candidate_path = ProjectLayout(revision.project).chapter_source_slice_path(2)
    candidate_path.unlink()
    revision.publish()
    assert not layout.chapter_source_slice_path(2).exists()


@pytest.mark.parametrize("change", ["new_input", "archived_prose"])
def test_planning_publish_rejects_new_inputs_and_changed_prose(tmp_path, change):
    storage, layout = _seed_project(tmp_path)
    storage.save_text(layout.chapter_path(1), "已接受正文")
    original = layout.outline_path.read_bytes()
    revision = PlanningRevision(layout.root, "project")
    revision.storage.save_json(revision.project / "outline.json", {"candidate": True})
    if change == "new_input":
        storage.save_json(layout.plans_dir / "planning_policy.json", {"locked_chapters": [2]})
    else:
        storage.save_text(layout.chapter_path(1), "人工修改正文")
    with pytest.raises(ValueError, match="while evaluating"):
        revision.publish()
    assert layout.outline_path.read_bytes() == original


def test_failed_commit_restores_deleted_future_cache(tmp_path, monkeypatch):
    from novel_forge.persistence import planning_revision

    storage, layout = _seed_project(tmp_path)
    storage.save_json(layout.chapter_source_slice_path(2), {"old_plan": True})
    revision = PlanningRevision(layout.root, "project")
    ProjectLayout(revision.project).chapter_source_slice_path(2).unlink()
    original_write = planning_revision.atomic_write_text

    def fail_commit(path, text, **kwargs):
        if path.name == "planning_publish.json" and '"committed"' in text:
            raise OSError("commit failure")
        return original_write(path, text, **kwargs)

    monkeypatch.setattr(planning_revision, "atomic_write_text", fail_commit)
    with pytest.raises(OSError, match="commit failure"):
        revision.publish()
    assert storage.load_json(layout.chapter_source_slice_path(2)) == {"old_plan": True}


async def test_sync_uses_normal_runtime_without_test_only_service_context(tmp_path, monkeypatch):
    from novel_forge.pipeline.long.services.generation.llm_service import LLMService
    from novel_forge.workspace.contracts import SyncChapterContractsRequest
    from novel_forge.workspace.helpers.execution_runners import execute_sync_chapter_contracts

    storage, layout = _seed_project(tmp_path)
    runtime = _Runtime(storage)
    calls = []

    async def contracts(self, task_type, payload, **kwargs):
        calls.append(payload)
        return {"chapter_contracts": [{"chapter_number": 2, "required_events": ["调查证据"]}]}

    monkeypatch.setattr(LLMService, "call_with_retry", contracts)
    result = await execute_sync_chapter_contracts(
        runtime,
        SyncChapterContractsRequest(
            project_id="project",
            affected_chapter_numbers=[2],
            rebuild_milestones=False,
        ),
    )
    assert result.result["status"] == "completed"
    assert len(calls) == 1


async def test_strict_sync_cannot_fill_missing_model_contracts_locally(tmp_path, monkeypatch):
    from novel_forge.pipeline.long.services.generation.llm_service import LLMService
    from novel_forge.workspace.contracts import SyncChapterContractsRequest
    from novel_forge.workspace.helpers.execution_runners import execute_sync_chapter_contracts

    storage, layout = _seed_project(tmp_path)
    storage.save_json((layout.plans_dir / "chapter_contracts.json"), {"chapter_contracts": []})
    before = (layout.plans_dir / "chapter_contracts.json").read_bytes()

    async def incomplete(self, task_type, payload, **kwargs):
        return {"chapter_contracts": [{"chapter_number": 1}]}

    monkeypatch.setattr(LLMService, "call_with_retry", incomplete)
    with pytest.raises(ValueError, match="omitted chapter contracts"):
        await execute_sync_chapter_contracts(
            _Runtime(storage),
            SyncChapterContractsRequest(
                project_id="project",
                affected_chapter_numbers=[2],
                rebuild_milestones=False,
            ),
            strict=True,
        )
    assert (layout.plans_dir / "chapter_contracts.json").read_bytes() == before


def test_interrupted_publication_is_recovered_before_reuse(tmp_path):
    storage, layout = _seed_project(tmp_path)
    original = layout.outline_path.read_text()
    storage.save_json(
        layout.root / "planning_publish.json",
        {
            "status": "publishing",
            "before": {"outline.json": original, "plans/new.json": None},
        },
    )
    storage.save_json(layout.outline_path, {"partial": True})
    storage.save_json(layout.plans_dir / "new.json", {"partial": True})
    assert recover_planning_publish(layout.root)
    assert layout.outline_path.read_text() == original
    assert not (layout.plans_dir / "new.json").exists()


class _Runtime:
    def __init__(self, storage: FileSystemStorage) -> None:
        self.storage = storage
        self.router = object()
        self.builder = object()
        self.settings = SimpleNamespace()


def _chapter(number: int, *, goal: str | None = None) -> ChapterOutline:
    return ChapterOutline(
        chapter_number=number,
        title=f"Chapter {number}",
        goal=goal or f"Goal {number}",
        beats_summary=[f"Beat {number}"],
        main_plot_points=[f"Plot {number}"],
        expected_word_count=3000,
    )


def _outline(*, second_goal: str = "Goal 2") -> StoryOutline:
    return StoryOutline(
        total_chapters=2,
        chapters=[_chapter(1), _chapter(2, goal=second_goal)],
        synopsis="Synopsis",
    )


def _seed_project(tmp_path: Path) -> tuple[FileSystemStorage, ProjectLayout]:
    storage = FileSystemStorage(tmp_path)
    layout = ProjectLayout(storage.ensure_project_dir("project"))
    layout.ensure_dirs()
    storage.save_json(layout.outline_path, _outline().model_dump(mode="json"))
    return storage, layout


@pytest.mark.parametrize("configured", [False, True])
async def test_execute_polish_outline_persists_outline_and_syncs_contracts(
    tmp_path: Path,
    monkeypatch: Any,
    configured: bool,
) -> None:
    storage, layout = _seed_project(tmp_path)
    runtime = _Runtime(storage)
    if configured:
        from novel_forge.core.authoring import AuthoringPolicy
        from novel_forge.persistence.authoring_store import AuthoringStore, story_input_version

        store = AuthoringStore(layout.root)
        policy = store.set_policy(
            AuthoringPolicy(mode="coauthor", end_chapter=2), expected_version=0
        )
        store.start(expected_version=policy.version, input_version=story_input_version(layout.root))
    captured: dict[str, Any] = {}
    updated_outline = _outline(second_goal="Goal 2, refined")

    class _FakePolishStep:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            self._project_id = ""

        async def run(self, polish_input: Any) -> Any:
            captured["input"] = polish_input
            captured["project_id"] = self._project_id
            return SimpleNamespace(
                polish_suggestions=["Strengthen the climax"],
                warnings=["Keep chapter 1 stable"],
                changed_chapters=[2, 1, 2],
                adjusted_outline=updated_outline,
            )

    async def fake_sync(
        _runtime: Any,
        request: Any,
        **_kwargs: Any,
    ) -> ExecutionResult[dict[str, Any]]:
        captured["sync_request"] = request
        return ExecutionResult(project_id=request.project_id, result={"status": "completed"})

    monkeypatch.setattr(execution_outline_polish, "PolishOutlineStep", _FakePolishStep)
    monkeypatch.setattr(execution_outline_polish, "execute_sync_chapter_contracts", fake_sync)

    events: list[str] = []
    result = await execution_outline_polish.execute_polish_outline(
        runtime,
        PolishOutlineRequest(
            project_id="project",
            user_hint="Raise the stakes",
            focus_fields=["goal"],
            chapter_range="1-2",
        ),
        on_step_progress=lambda step, _payload: events.append(step),
    )

    assert result.result["changed_chapters"] == [1, 2]
    assert result.result["polish_suggestions"] == ["Strengthen the climax"]
    from novel_forge.persistence.polish_history import PolishHistoryRecorder

    assert PolishHistoryRecorder(layout.root).load_outline_history()[0].result_type == "candidate"
    assert result.result["contracts"]["status"] == ("candidate" if configured else "completed")
    if configured:
        from novel_forge.app_service.authoring_proposals import apply_proposal
        from novel_forge.core.authoring import AuthoringProposalDecision
        from novel_forge.persistence.authoring_proposals import ProposalStore
        from novel_forge.workspace.authoring_proposals import decide_proposal

        assert storage.load_json(layout.outline_path)["chapters"][1]["goal"] != "Goal 2, refined"
        proposal = ProposalStore(layout.root).views()[0]
        decide_proposal(
            layout.root,
            proposal.id,
            AuthoringProposalDecision(
                decision="accept",
                candidate_version=proposal.candidate_version,
                input_version=proposal.input_version,
                policy_version=proposal.policy_version,
            ),
        )
        assert (await apply_proposal(runtime, None, "project", proposal.id)).status == "applied"
    assert captured["project_id"] == "project"
    assert captured["input"].chapter_range == "1-2"
    assert captured["sync_request"].affected_chapter_numbers == [1, 2]
    assert captured["sync_request"].cascade_downstream is True
    assert "outline_polish_start" in events
    assert ("outline_polish_persisted" in events) is not configured
    saved_outline = StoryOutline.model_validate(storage.load_json(layout.outline_path))
    assert saved_outline.chapters[1].goal == "Goal 2, refined"


async def test_execute_polish_outline_analysis_does_not_write_or_sync(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    storage, layout = _seed_project(tmp_path)
    runtime = _Runtime(storage)
    original_outline = storage.load_json(layout.outline_path)
    sync_called = False

    class _FakePolishStep:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            self._project_id = ""

        async def run(self, _polish_input: Any) -> Any:
            return SimpleNamespace(
                polish_suggestions=["Strengthen the climax"],
                warnings=[],
                changed_chapters=[2],
                adjusted_outline=_outline(second_goal="Must not persist"),
            )

    async def fake_sync(*_args: Any, **_kwargs: Any) -> ExecutionResult[dict[str, Any]]:
        nonlocal sync_called
        sync_called = True
        raise AssertionError("analysis-only requests must not sync contracts")

    monkeypatch.setattr(execution_outline_polish, "PolishOutlineStep", _FakePolishStep)
    monkeypatch.setattr(execution_outline_polish, "execute_sync_chapter_contracts", fake_sync)

    result = await execution_outline_polish.execute_polish_outline(
        runtime,
        PolishOutlineRequest(project_id="project", analysis_only=True),
    )

    assert result.result["changed_chapters"] == [2]
    assert result.result["contracts"] is None
    assert sync_called is False
    assert storage.load_json(layout.outline_path) == original_outline
