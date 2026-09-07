from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import ValidationError

from novel_forge.core.schemas.bible import CharacterBible, CharacterProfile, StoryBible
from novel_forge.core.schemas.outline import (
    ChapterOutline,
    NarrativeBlueprint,
    StoryOutline,
    VolumeOutline,
)
from novel_forge.core.schemas.spec import StorySpec
from novel_forge.persistence.filesystem import FileSystemStorage
from novel_forge.persistence.models import ProjectLayout
from novel_forge.pipeline.long.services.chapter_position import (
    PREVIOUS_FINAL_MARKER,
    build_chapter_position,
)
from novel_forge.pipeline.long.services.constraints.constraint_router import build_stage_cards
from novel_forge.workspace import (
    execution_extend_outline,
    execution_planning_horizon,
    planning_horizon,
)
from novel_forge.workspace.contracts import ExtendOutlineRequest
from novel_forge.workspace.execution_result import ExecutionResult


class _Runner:
    def __init__(self, storage: FileSystemStorage) -> None:
        self._storage = storage
        self._router = object()
        self._builder = object()
        self._settings = SimpleNamespace()
        self._config = SimpleNamespace(default_chapters_per_volume=3)
        self._on_step = None

    async def _call_with_retry(self, *args: Any, **kwargs: Any) -> Any:
        raise AssertionError("outline extension tests patch the LLM outline generator")

    def _coerce_character_bible(self, payload: Any) -> Any:
        return payload

    def _is_outline_option_enabled_for_task(self, *args: Any, **kwargs: Any) -> bool:
        return False


class _Runtime:
    def __init__(self, storage: FileSystemStorage) -> None:
        self.storage = storage
        self.runner = _Runner(storage)

    def chapter_runner(self, **kwargs: Any) -> _Runner:
        return self.runner


@pytest.fixture
def progressive_project(tmp_path, monkeypatch):
    storage, layout = _seed_project(tmp_path)
    outline = StoryOutline(
        total_chapters=80,
        hard_through_chapter=5,
        planned_through_chapter=10,
        chapters=[_chapter(number) for number in range(1, 11)],
        synopsis="Original 80-chapter story",
    )
    storage.save_json(layout.outline_path, outline.model_dump(mode="json"))
    storage.save_json(
        layout.plans_dir / "chapter_contracts.json",
        {
            "chapter_contracts": [{"chapter_number": n, "goal": f"keep-{n}"} for n in range(1, 6)],
        },
    )
    runtime = _Runtime(storage)
    captured = {}

    async def generate(ctx, **kwargs):
        assert ctx.storage is not storage
        assert ctx.layout.root != layout.root
        assert kwargs["preserve_committed_chapters"] is True
        captured["generation"] = kwargs
        result = _extended_outline(kwargs["existing_outline"], kwargs["target_end_chapter"])
        result = result.model_copy(update={"total_chapters": kwargs["total_chapters"]})
        # Real batching also writes checkpoints and outline before returning.
        ctx.storage.save_json(ctx.layout.outline_path, result.model_dump(mode="json"))
        ctx.storage.save_json(ctx.layout.plans_dir / "chapter_design_matrix.json", {"new": True})
        return result

    async def synchronize(runtime_arg, **kwargs):
        captured["sync"] = kwargs
        revision = kwargs["revision"]
        contracts = revision.storage.load_json(revision.project / "plans/chapter_contracts.json")
        contracts["chapter_contracts"].extend(
            {"chapter_number": n} for n in kwargs["changed_chapters"]
        )
        revision.storage.save_json(revision.project / "plans/chapter_contracts.json", contracts)
        return {"status": "completed"}

    def refresh(storage_arg, layout_arg, *, chapter_numbers, **kwargs):
        assert storage_arg is not storage
        for number in chapter_numbers:
            storage_arg.save_json(layout_arg.chapter_source_slice_path(number), {"number": number})
        return len(chapter_numbers), []

    monkeypatch.setattr(planning_horizon, "_batched_generate_outline", generate)
    monkeypatch.setattr(execution_planning_horizon, "prepare_outline_revision", synchronize)
    monkeypatch.setattr(execution_extend_outline, "_refresh_new_source_slices", refresh)
    return runtime, layout, captured


async def test_complete_80_chapter_book_without_extending_or_rewriting_existing(
    progressive_project,
):
    runtime, layout, captured = progressive_project
    original = runtime.storage.load_json(layout.outline_path)
    spec_before = layout.spec_path.read_bytes()
    bible_before = layout.bible_path.read_bytes()
    events = []
    result = await execution_extend_outline.execute_extend_outline(
        runtime,
        ExtendOutlineRequest(project_id="project", target_total=80),
        on_step_progress=lambda step, payload: events.append(step),
    )
    saved = runtime.storage.load_json(layout.outline_path)
    assert (
        saved["total_chapters"]
        == saved["hard_through_chapter"]
        == saved["planned_through_chapter"]
        == 80
    )
    assert [c["chapter_number"] for c in saved["chapters"]] == list(range(1, 81))
    assert saved["chapters"][:5] == original["chapters"][:5]
    assert saved["synopsis"] == original["synopsis"]
    assert layout.spec_path.read_bytes() == spec_before
    assert layout.bible_path.read_bytes() == bible_before
    assert layout.chapter_path(3).read_text() == "chapter-3-body"
    assert result.result["added_chapters"] == 0
    assert result.result["new_chapters"] == list(range(11, 81))
    assert captured["generation"]["target_start_chapter"] == 6
    assert captured["sync"]["changed_chapters"] == list(range(6, 81))
    assert layout.chapter_source_slice_path(80).exists()
    assert events[-2:] == ["planning_horizon_advanced", "extend_outline_done"]
    # Repeating completion is idempotent and does not submit more model calls.
    captured.clear()
    await execution_extend_outline.execute_extend_outline(
        runtime,
        ExtendOutlineRequest(project_id="project", target_total=80),
    )
    assert not captured


@pytest.mark.parametrize(
    "failure", ["generation", "sync", "slice", "protected_contract", "concurrent_edit"]
)
async def test_failed_completion_never_publishes_partial_planning(
    progressive_project, monkeypatch, failure
):
    runtime, layout, _ = progressive_project
    original = layout.outline_path.read_bytes()
    old_contracts = (layout.plans_dir / "chapter_contracts.json").read_bytes()
    original_sync = execution_planning_horizon.prepare_outline_revision

    async def bad_generation(ctx, **kwargs):
        ctx.storage.save_json(ctx.layout.outline_path, {"corrupt": True})
        raise RuntimeError("generation failed")

    async def bad_sync(runtime_arg, **kwargs):
        await original_sync(runtime_arg, **kwargs)
        if failure == "protected_contract":
            revision = kwargs["revision"]
            revision.storage.save_json(
                revision.project / "plans/chapter_contracts.json", {"chapter_contracts": []}
            )
        elif failure == "concurrent_edit":
            runtime.storage.save_json(layout.spec_path, {"new_user_edit": True})
        else:
            raise RuntimeError("sync failed")

    if failure == "generation":
        monkeypatch.setattr(planning_horizon, "_batched_generate_outline", bad_generation)
    elif failure == "slice":
        monkeypatch.setattr(
            execution_extend_outline, "_refresh_new_source_slices", lambda *a, **k: (0, ["failed"])
        )
    else:
        monkeypatch.setattr(execution_planning_horizon, "prepare_outline_revision", bad_sync)
    with pytest.raises((RuntimeError, ValueError)):
        await execution_extend_outline.execute_extend_outline(
            runtime,
            ExtendOutlineRequest(project_id="project", target_total=80),
        )
    assert layout.outline_path.read_bytes() == original
    assert (layout.plans_dir / "chapter_contracts.json").read_bytes() == old_contracts
    assert not layout.chapter_source_slice_path(80).exists()
    assert not (layout.plans_dir / "chapter_design_matrix.json").exists()
    assert layout.chapter_path(3).read_text() == "chapter-3-body"


async def test_automatic_horizon_advances_beyond_ten_chapters(progressive_project):
    runtime, layout, captured = progressive_project
    await execution_planning_horizon.advance_planning_horizon(
        runtime, project_id="project", current_chapter=3
    )
    assert runtime.storage.load_json(layout.outline_path)["hard_through_chapter"] == 10
    await execution_planning_horizon.advance_planning_horizon(
        runtime, project_id="project", current_chapter=8
    )
    saved = runtime.storage.load_json(layout.outline_path)
    assert saved["hard_through_chapter"] == 15
    assert saved["total_chapters"] == 80
    assert len(saved["chapters"]) == 15
    assert captured["generation"]["target_start_chapter"] == 11


async def test_chapter_entry_recovers_a_previously_failed_horizon(progressive_project):
    runtime, layout, captured = progressive_project
    await execution_planning_horizon.ensure_chapter_planning(
        runtime,
        project_id="project",
        chapter_number=5,
    )
    assert not captured
    await execution_planning_horizon.ensure_chapter_planning(
        runtime,
        project_id="project",
        chapter_number=6,
    )
    assert runtime.storage.load_json(layout.outline_path)["hard_through_chapter"] == 10
    await execution_planning_horizon.ensure_chapter_planning(
        runtime,
        project_id="project",
        chapter_number=11,
    )
    assert runtime.storage.load_json(layout.outline_path)["hard_through_chapter"] == 15


def _chapter(number: int, *, notes: str = "", goal: str | None = None) -> ChapterOutline:
    return ChapterOutline(
        chapter_number=number,
        title=f"Chapter {number}",
        goal=goal or f"Goal {number}",
        beats_summary=[f"Beat {number}"],
        main_plot_points=[f"Plot {number}"],
        expected_word_count=3000,
        notes=notes,
    )


def _outline(total: int = 3, *, chapter_3_notes: str = "") -> StoryOutline:
    return StoryOutline(
        total_chapters=total,
        volume_mode=False,
        volumes=[
            VolumeOutline(
                volume_number=1,
                title="Volume",
                start_chapter=1,
                end_chapter=total,
            )
        ],
        chapters=[_chapter(1), _chapter(2), _chapter(3, notes=chapter_3_notes)],
        synopsis="Synopsis",
    )


def _extended_outline(existing_outline: StoryOutline, target_total: int) -> StoryOutline:
    chapters = list(existing_outline.chapters)
    for number in range(len(chapters) + 1, target_total + 1):
        chapters.append(_chapter(number, goal=f"New goal {number}"))
    return existing_outline.model_copy(
        update={
            "total_chapters": target_total,
            "volumes": [
                VolumeOutline(
                    volume_number=1,
                    title="Volume",
                    start_chapter=1,
                    end_chapter=target_total,
                )
            ],
            "chapters": chapters,
        }
    )


def _seed_project(
    tmp_path, *, project_id: str = "project"
) -> tuple[FileSystemStorage, ProjectLayout]:
    storage = FileSystemStorage(tmp_path)
    layout = ProjectLayout(storage.ensure_project_dir(project_id))
    layout.ensure_dirs()

    storage.save_json(
        layout.spec_path,
        StorySpec(
            title="Test Book",
            genre="fantasy",
            theme="A test premise",
            length_target=9000,
        ).model_dump(mode="json"),
    )
    storage.save_json(
        layout.bible_path,
        StoryBible(premise="Premise", notes="seed note").model_dump(mode="json"),
    )
    storage.save_json(
        layout.characters_path,
        CharacterBible(characters=[CharacterProfile(name="Ada", role="protagonist")]).model_dump(
            mode="json"
        ),
    )
    storage.save_json(layout.style_profile_path, {"voice": "plain"})
    storage.save_json(
        layout.outline_path,
        _outline().model_dump(mode="json"),
    )
    storage.save_json(
        layout.blueprint_path,
        NarrativeBlueprint(
            synopsis="Blueprint",
            volume_mode=False,
            volumes=[
                VolumeOutline(
                    volume_number=1,
                    title="Volume",
                    start_chapter=1,
                    end_chapter=3,
                )
            ],
        ).model_dump(mode="json"),
    )
    storage.save_json(
        layout.plans_dir / "chapter_contracts.json",
        {"chapter_contracts": [{"chapter_number": number} for number in range(1, 4)]},
    )
    storage.save_json(layout.narrative_contract_path, {"rules": []})
    storage.save_json(layout.editorial_contract_path, {"rules": []})

    for number in range(1, 4):
        storage.save_text(layout.chapter_path(number), f"chapter-{number}-body")
    old_slice = layout.chapter_source_slice_path(3)
    old_slice.parent.mkdir(parents=True, exist_ok=True)
    old_slice.write_text("old slice", encoding="utf-8")
    return storage, layout


def test_extend_outline_request_requires_exactly_one_target_mode() -> None:
    assert ExtendOutlineRequest(project_id="p", additional_chapters=2).additional_chapters == 2
    assert ExtendOutlineRequest(project_id="p", target_total=12).target_total == 12

    with pytest.raises(ValidationError):
        ExtendOutlineRequest(project_id="p")
    with pytest.raises(ValidationError):
        ExtendOutlineRequest(project_id="p", additional_chapters=2, target_total=12)


def test_chapter_position_flips_old_final_after_extension() -> None:
    before = _outline(total=10)
    assert build_chapter_position(before, 10)["is_last_chapter"] is True
    assert build_chapter_position(before, 9)["position_label"] == "penultimate"

    chapters = [_chapter(number) for number in range(1, 16)]
    chapters[9] = _chapter(10, notes=f"{PREVIOUS_FINAL_MARKER} previous_total=10")
    after = StoryOutline(total_chapters=15, chapters=chapters, synopsis="After")

    old_final = build_chapter_position(after, 10)
    assert old_final["is_last_chapter"] is False
    assert old_final["is_previously_final_chapter"] is True
    assert build_chapter_position(after, 14)["position_label"] == "penultimate"
    assert build_chapter_position(after, 15)["is_last_chapter"] is True


def test_stage_chapter_card_uses_explicit_chapter_position() -> None:
    outline = _outline(total=5)
    position = build_chapter_position(outline, 3)
    cards = build_stage_cards(
        stage="draft",
        chapter_outline=outline.chapters[2],
        chapter_position=position,
    )

    assert cards["chapter"]["total_chapters"] == 5
    assert cards["chapter"]["is_last_chapter"] is False
    assert cards["chapter"]["chapter_position"]["remaining_chapters"] == 2


@pytest.mark.parametrize("configured,sync_failure", [(False, False), (True, False), (True, True)])
async def test_execute_extend_outline_appends_chapters_and_syncs_contracts(
    tmp_path, monkeypatch, configured, sync_failure
) -> None:
    storage, layout = _seed_project(tmp_path)
    runtime = _Runtime(storage)
    if configured:
        from novel_forge.core.authoring import AuthoringPolicy
        from novel_forge.persistence.authoring_store import AuthoringStore, story_input_version

        store = AuthoringStore(layout.root)
        policy = store.set_policy(
            AuthoringPolicy(mode="authorized_auto", end_chapter=5), expected_version=0
        )
        store.start(expected_version=policy.version, input_version=story_input_version(layout.root))
    captured: dict[str, Any] = {}

    async def fake_batched_generate_outline(ctx: Any, **kwargs: Any) -> StoryOutline:
        if configured:
            assert ctx.storage is not storage
            assert ctx.layout.root != layout.root
        existing_outline = kwargs["existing_outline"]
        assert existing_outline.total_chapters == 5
        assert PREVIOUS_FINAL_MARKER in existing_outline.chapters[2].notes
        assert kwargs["total_chapters"] == 5
        return _extended_outline(existing_outline, 5)

    async def fake_sync(
        runtime_arg: Any, request: Any, **kwargs: Any
    ) -> ExecutionResult[dict[str, Any]]:
        captured["sync_request"] = request
        return ExecutionResult(
            project_id=request.project_id,
            result={
                "status": "failed" if sync_failure else "completed",
                "affected": request.affected_chapter_numbers,
            },
        )

    def fake_refresh(
        storage_arg: Any,
        layout_arg: Any,
        *,
        project_id: str,
        chapter_numbers: list[int],
    ) -> tuple[int, list[str]]:
        captured["refreshed"] = list(chapter_numbers)
        return len(chapter_numbers), []

    monkeypatch.setattr(
        execution_extend_outline,
        "_batched_generate_outline",
        fake_batched_generate_outline,
    )
    monkeypatch.setattr(execution_extend_outline, "execute_sync_chapter_contracts", fake_sync)
    monkeypatch.setattr(execution_extend_outline, "_refresh_new_source_slices", fake_refresh)

    if sync_failure:
        before = {
            path: path.read_bytes()
            for path in (layout.outline_path, layout.spec_path, layout.bible_path)
        }
        with pytest.raises(RuntimeError, match="原规划未改变"):
            await execution_extend_outline.execute_extend_outline(
                runtime, ExtendOutlineRequest(project_id="project", target_total=5)
            )
        assert all(path.read_bytes() == content for path, content in before.items())
        return
    result = await execution_extend_outline.execute_extend_outline(
        runtime,
        ExtendOutlineRequest(project_id="project", target_total=5),
    )

    payload = result.result
    assert payload["status"] == ("candidate" if configured else "completed")
    if configured:
        from novel_forge.app_service.authoring_proposals import apply_proposal
        from novel_forge.core.authoring import AuthoringProposalDecision
        from novel_forge.persistence.authoring_proposals import ProposalStore
        from novel_forge.workspace.authoring_proposals import decide_proposal

        assert storage.load_json(layout.outline_path)["total_chapters"] == 3
        proposal = ProposalStore(layout.root).views()[0]
        assert proposal.action == "extend"
        assert proposal.lock_conflicts  # Includes the already-archived previous ending.
        assert proposal.id == payload["proposal_id"]
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
        applied = await apply_proposal(runtime, None, "project", proposal.id)
        assert applied.status == "applied"
    assert payload["previous_total"] == 3
    assert payload["target_total"] == 5
    assert payload["new_chapters"] == [4, 5]
    assert payload["source_slices_refreshed"] == 2

    saved_outline = StoryOutline.model_validate(storage.load_json(layout.outline_path))
    assert saved_outline.total_chapters == 5
    assert [chapter.chapter_number for chapter in saved_outline.chapters] == [1, 2, 3, 4, 5]
    assert saved_outline.chapters[0].goal == "Goal 1"
    assert PREVIOUS_FINAL_MARKER in saved_outline.chapters[2].notes

    saved_spec = StorySpec.model_validate(storage.load_json(layout.spec_path))
    assert saved_spec.length_target == 15000
    saved_bible = StoryBible.model_validate(storage.load_json(layout.bible_path))
    assert "[extend_outline]" in saved_bible.notes
    assert set(saved_bible.model_dump(mode="json")) <= set(StoryBible.model_fields)

    sync_request = captured["sync_request"]
    assert sync_request.affected_chapter_numbers == [3, 4, 5]
    assert sync_request.cascade_downstream is True
    assert sync_request.rebuild_milestones is True
    assert sync_request.mark_stale is True
    assert sync_request.prose_untouched is True
    assert captured["refreshed"] == [4, 5]
    assert not layout.chapter_source_slice_path(3).exists()
    assert storage.load_text(layout.chapter_path(1)) == "chapter-1-body"
    assert storage.load_text(layout.chapter_path(2)) == "chapter-2-body"
    assert storage.load_text(layout.chapter_path(3)) == "chapter-3-body"


async def test_execute_extend_outline_can_keep_old_final_unmodified(tmp_path, monkeypatch) -> None:
    storage, layout = _seed_project(tmp_path)
    runtime = _Runtime(storage)
    captured: dict[str, Any] = {}

    async def fake_batched_generate_outline(ctx: Any, **kwargs: Any) -> StoryOutline:
        existing_outline = kwargs["existing_outline"]
        assert PREVIOUS_FINAL_MARKER not in existing_outline.chapters[2].notes
        return _extended_outline(existing_outline, 4)

    async def fake_sync(
        runtime_arg: Any, request: Any, **kwargs: Any
    ) -> ExecutionResult[dict[str, Any]]:
        captured["sync_request"] = request
        return ExecutionResult(project_id=request.project_id, result={"status": "completed"})

    monkeypatch.setattr(
        execution_extend_outline,
        "_batched_generate_outline",
        fake_batched_generate_outline,
    )
    monkeypatch.setattr(execution_extend_outline, "execute_sync_chapter_contracts", fake_sync)
    monkeypatch.setattr(
        execution_extend_outline,
        "_refresh_new_source_slices",
        lambda *args, **kwargs: (1, []),
    )

    result = await execution_extend_outline.execute_extend_outline(
        runtime,
        ExtendOutlineRequest(
            project_id="project",
            additional_chapters=1,
            decommission_old_ending=False,
        ),
    )

    assert result.result["status"] == "completed"
    saved_outline = StoryOutline.model_validate(storage.load_json(layout.outline_path))
    assert PREVIOUS_FINAL_MARKER not in saved_outline.chapters[2].notes
    assert captured["sync_request"].affected_chapter_numbers == [4]
    assert layout.chapter_source_slice_path(3).exists()


async def test_extending_a_progressive_book_also_fills_its_existing_goal(tmp_path, monkeypatch):
    storage, layout = _seed_project(tmp_path)
    original = StoryOutline(
        total_chapters=80,
        hard_through_chapter=5,
        planned_through_chapter=10,
        chapters=[_chapter(n) for n in range(1, 11)],
    )
    storage.save_json(layout.outline_path, original.model_dump(mode="json"))
    storage.save_json(
        layout.editorial_contract_path, {"revelation_ladder": [{"thread": "identity"}]}
    )
    captured = {}

    async def generate(ctx, **kwargs):
        assert kwargs["target_start_chapter"] == 6
        assert kwargs["target_end_chapter"] == 82
        assert kwargs["preserve_committed_chapters"] is True
        return _extended_outline(kwargs["existing_outline"], 82)

    async def sync(runtime_arg, request, **kwargs):
        captured["affected"] = request.affected_chapter_numbers
        return ExecutionResult(project_id="project", result={"status": "completed"})

    monkeypatch.setattr(execution_extend_outline, "_batched_generate_outline", generate)
    monkeypatch.setattr(execution_extend_outline, "execute_sync_chapter_contracts", sync)
    monkeypatch.setattr(
        execution_extend_outline, "_refresh_new_source_slices", lambda *a, **k: (77, [])
    )
    await execution_extend_outline.execute_extend_outline(
        _Runtime(storage),
        ExtendOutlineRequest(project_id="project", additional_chapters=2),
    )
    saved = storage.load_json(layout.outline_path)
    assert saved["total_chapters"] == 82
    assert [ch["chapter_number"] for ch in saved["chapters"]] == list(range(1, 83))
    assert saved["chapters"][:5] == original.model_dump(mode="json")["chapters"][:5]
    assert captured["affected"] == list(range(6, 83))


async def test_execute_extend_outline_returns_partial_when_contract_sync_fails(
    tmp_path, monkeypatch
) -> None:
    storage, _layout = _seed_project(tmp_path)
    runtime = _Runtime(storage)

    async def fake_batched_generate_outline(ctx: Any, **kwargs: Any) -> StoryOutline:
        return _extended_outline(kwargs["existing_outline"], 4)

    async def fake_sync(
        runtime_arg: Any, request: Any, **kwargs: Any
    ) -> ExecutionResult[dict[str, Any]]:
        return ExecutionResult(project_id=request.project_id, result={"status": "failed"})

    monkeypatch.setattr(
        execution_extend_outline,
        "_batched_generate_outline",
        fake_batched_generate_outline,
    )
    monkeypatch.setattr(execution_extend_outline, "execute_sync_chapter_contracts", fake_sync)

    result = await execution_extend_outline.execute_extend_outline(
        runtime,
        ExtendOutlineRequest(project_id="project", additional_chapters=1),
    )

    assert result.result["status"] == "partial"
    assert result.result["source_slices_refreshed"] == 0
    assert "chapter contract sync did not complete" in result.result["warnings"][0]


async def test_execute_extend_outline_aborts_on_reveal_guard_mismatch(tmp_path) -> None:
    """Extension must abort (not silently discard) when reveal-guard session is stale."""
    storage, layout = _seed_project(tmp_path)
    runtime = _Runtime(storage)

    storage.save_json(
        layout.editorial_contract_path,
        {"revelation_ladder": [{"phase": "act1", "reveal": "protagonist_identity"}]},
    )
    # An existing stale session must still fail; a cleaned successful session is normal.
    storage.save_json(layout.outline_session_path, {"reveal_guard_input_hashes": {"stale": "old"}})

    with pytest.raises(RuntimeError, match="reveal-guard session mismatch"):
        await execution_extend_outline.execute_extend_outline(
            runtime,
            ExtendOutlineRequest(project_id="project", additional_chapters=1),
        )

    saved_outline = StoryOutline.model_validate(storage.load_json(layout.outline_path))
    assert saved_outline.total_chapters == 3


async def test_execute_extend_outline_preserves_original_chapter_fields(
    tmp_path, monkeypatch
) -> None:
    """Batch normalization of old chapters must be reverted; only new chapters accepted."""
    storage, layout = _seed_project(tmp_path)
    runtime = _Runtime(storage)

    async def fake_batched_generate_outline(ctx: Any, **kwargs: Any) -> StoryOutline:
        existing_outline = kwargs["existing_outline"]
        tampered_old = existing_outline.chapters[0].model_copy(
            update={"pov_character": "TAMPERED", "pov_character_name": "TAMPERED"}
        )
        chapters = [tampered_old, *list(existing_outline.chapters[1:])]
        for number in range(kwargs["target_start_chapter"], kwargs["target_end_chapter"] + 1):
            chapters.append(_chapter(number, goal=f"New goal {number}"))
        return existing_outline.model_copy(
            update={"total_chapters": kwargs["total_chapters"], "chapters": chapters}
        )

    async def fake_sync(
        runtime_arg: Any, request: Any, **kwargs: Any
    ) -> ExecutionResult[dict[str, Any]]:
        return ExecutionResult(project_id=request.project_id, result={"status": "completed"})

    monkeypatch.setattr(
        execution_extend_outline,
        "_batched_generate_outline",
        fake_batched_generate_outline,
    )
    monkeypatch.setattr(execution_extend_outline, "execute_sync_chapter_contracts", fake_sync)
    monkeypatch.setattr(
        execution_extend_outline,
        "_refresh_new_source_slices",
        lambda *args, **kwargs: (0, []),
    )

    result = await execution_extend_outline.execute_extend_outline(
        runtime,
        ExtendOutlineRequest(project_id="project", additional_chapters=1),
    )

    assert result.result["status"] == "completed"
    saved_outline = StoryOutline.model_validate(storage.load_json(layout.outline_path))
    assert saved_outline.chapters[0].pov_character != "TAMPERED"
    assert saved_outline.chapters[0].pov_character_name != "TAMPERED"


async def test_execute_extend_outline_creates_backup(tmp_path, monkeypatch) -> None:
    """Extension must snapshot the four overwritten artifacts for undo."""
    storage, layout = _seed_project(tmp_path)
    runtime = _Runtime(storage)

    async def fake_batched_generate_outline(ctx: Any, **kwargs: Any) -> StoryOutline:
        return _extended_outline(kwargs["existing_outline"], 4)

    async def fake_sync(
        runtime_arg: Any, request: Any, **kwargs: Any
    ) -> ExecutionResult[dict[str, Any]]:
        return ExecutionResult(project_id=request.project_id, result={"status": "completed"})

    monkeypatch.setattr(
        execution_extend_outline,
        "_batched_generate_outline",
        fake_batched_generate_outline,
    )
    monkeypatch.setattr(execution_extend_outline, "execute_sync_chapter_contracts", fake_sync)
    monkeypatch.setattr(
        execution_extend_outline,
        "_refresh_new_source_slices",
        lambda *args, **kwargs: (1, []),
    )

    result = await execution_extend_outline.execute_extend_outline(
        runtime,
        ExtendOutlineRequest(project_id="project", additional_chapters=1),
    )

    backup_dir = result.result.get("backup_dir")
    assert backup_dir
    backup_path = Path(backup_dir)
    assert backup_path.exists()
    for name in ("spec.json", "outline.json", "story_bible.json", "narrative_blueprint.json"):
        assert (backup_path / name).exists()
    backup_outline = StoryOutline.model_validate(storage.load_json(backup_path / "outline.json"))
    assert backup_outline.total_chapters == 3
