from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from novel_forge.core.schemas.future_planning import FutureOutlineDecision
from novel_forge.core.schemas.outline import ChapterOutline, StoryOutline, VolumeOutline
from novel_forge.persistence.filesystem import FileSystemStorage
from novel_forge.persistence.models import ProjectLayout
from novel_forge.pipeline.long.services.future_planning import (
    FutureOutlineReviewStep,
    exploration_has_budget,
    planning_boundary,
    validate_candidate_boundaries,
    validate_protected_contracts,
)
from novel_forge.workspace import execution_future_planning as execution
from novel_forge.workspace.helpers.execution_manifest import wrap_manifest_step_callback


async def test_future_review_real_step_uses_mock_and_keeps_original(
    router, builder, runtime_settings
):
    decision = await FutureOutlineReviewStep(router, builder, settings=runtime_settings).run(
        {
            "original": {},
            "candidate": {},
            "accepted_context": {},
            "candidate_contracts": {},
        }
    )
    assert decision.selected == "original"
    assert not decision.permits_publication


def _outline():
    return StoryOutline(
        total_chapters=2,
        chapters=[
            ChapterOutline(chapter_number=n, title=f"第{n}章", goal="寻找线索") for n in (1, 2)
        ],
        volumes=[VolumeOutline(volume_number=1, start_chapter=1, end_chapter=1)],
    )


def _decision(selected="candidate", *, failed_check=None):
    return FutureOutlineDecision.model_validate(
        {
            "selected": selected,
            "creative_gain": "改变调查路径，保留已许诺的线索兑现",
            "confidence": 0.9,
            **{
                key: {"passed": key != failed_check, "evidence": f"{key}: 比较输入证据"}
                for key in (
                    "facts",
                    "user_intent",
                    "motivation",
                    "causality",
                    "promises",
                    "contracts",
                )
            },
        }
    )


def test_future_candidate_preserves_published_and_locked_chapters():
    original = _outline()
    candidate = original.model_copy(deep=True)
    candidate.chapters[0].goal = "改写过去"
    with pytest.raises(ValueError, match="published or human-locked"):
        validate_candidate_boundaries(original, candidate, protected={1})
    candidate = original.model_copy(deep=True)
    candidate.total_chapters = 3
    with pytest.raises(ValueError, match="numbering/count"):
        validate_candidate_boundaries(original, candidate, protected=set())


def test_contract_cascade_cannot_change_published_chapters():
    with pytest.raises(ValueError, match="protected chapter"):
        validate_protected_contracts(
            {"chapter_contracts": [{"chapter_number": 1, "required_events": ["事实"]}]},
            {"chapter_contracts": [{"chapter_number": 1, "required_events": ["改写"]}]},
            {1},
        )


@pytest.mark.parametrize(
    "failed_check", ["facts", "user_intent", "motivation", "causality", "promises", "contracts"]
)
def test_every_impact_dimension_must_pass(failed_check):
    assert not _decision(failed_check=failed_check).permits_publication
    assert not _decision(selected="original").permits_publication


def test_budget_and_turning_point_gates():
    settings = SimpleNamespace(budget_daily_usd=2, future_planning_budget_reserve_usd=1)
    router = SimpleNamespace(spending_tracker=SimpleNamespace(daily_spent=1.5))
    assert not exploration_has_budget(settings, router)
    router.spending_tracker.daily_spent = 0.2
    assert exploration_has_budget(settings, router)
    assert planning_boundary(_outline(), 1)
    assert not planning_boundary(_outline(), 2)


@pytest.mark.parametrize(
    "mode, selected, expected",
    [
        ("fixed", "candidate", "fixed"),
        ("proposal", "candidate", "proposal"),
        ("adaptive", "original", "kept_original"),
        ("adaptive", "candidate", "published"),
        ("adaptive", "boundary_change", "requires_confirmation"),
        ("adaptive", "protected_contract", "requires_confirmation"),
    ],
)
async def test_future_planning_modes_use_isolated_validated_candidate(
    tmp_path, monkeypatch, mode, selected, expected
):
    storage = FileSystemStorage(tmp_path)
    layout = ProjectLayout(storage.ensure_project_dir("book"))
    layout.ensure_dirs()
    outline = _outline()
    storage.save_json(layout.outline_path, outline.model_dump(mode="json"))
    storage.save_text(layout.chapter_path(1), "已经成稿的事实。")
    storage.save_json(
        layout.plans_dir / "chapter_contracts.json", {"chapter_contracts": [{"chapter_number": 1}]}
    )
    progress = wrap_manifest_step_callback(storage, "book", "run_chapter", None)
    assert progress is not None
    progress("chapter_accepted", {})
    candidate = outline.model_copy(deep=True)
    candidate.chapters[1].goal = "以线索交换同伴信任"
    if selected == "boundary_change":
        candidate.chapters[0].goal = "不应自动改写的已成稿章节"
    explore = AsyncMock(return_value=candidate)
    monkeypatch.setattr(execution, "explore_future_candidate", explore)
    monkeypatch.setattr(
        execution, "_accepted_kernel", AsyncMock(return_value={"current_chapter": 1})
    )

    async def prepare(runtime, *, revision, **kwargs):
        # Nothing has been published while the candidate is being synchronized.
        assert storage.load_json(layout.outline_path)["chapters"][1]["goal"] == "寻找线索"
        progress("sync_candidate_contracts", {"status": "running"})
        revision.storage.save_json(
            revision.project / "plans/chapter_contracts.json",
            {
                "chapter_contracts": [
                    {
                        "chapter_number": 1,
                        **(
                            {"required_events": ["不应更改的过去"]}
                            if selected == "protected_contract"
                            else {}
                        ),
                    },
                    {"chapter_number": 2, "required_events": ["交换信任"]},
                ],
            },
        )

    class Review:
        def __init__(self, *args, **kwargs):
            pass

        async def run(self, context):
            assert context["candidate_contracts"]["chapter_contracts"][1]["required_events"] == [
                "交换信任"
            ]
            return _decision(selected)

    monkeypatch.setattr(execution, "prepare_outline_revision", prepare)
    monkeypatch.setattr(execution, "FutureOutlineReviewStep", Review)
    runtime = SimpleNamespace(
        storage=storage,
        router=object(),
        builder=object(),
        settings=SimpleNamespace(future_planning_mode=mode),
    )
    result = await execution.execute_future_planning(
        runtime, project_id="book", completed_chapter=1, on_step_progress=progress
    )
    assert result["status"] == expected
    assert storage.load_text(layout.chapter_path(1)) == "已经成稿的事实。"
    saved_goal = storage.load_json(layout.outline_path)["chapters"][1]["goal"]
    assert saved_goal == (candidate.chapters[1].goal if expected == "published" else "寻找线索")
    if mode == "fixed":
        explore.assert_not_called()
    else:
        assert (
            storage.load_json(layout.states_dir / "artifact_manifest.json")["artifacts"][
                "workflow:run_chapter:latest_step"
            ]["step"]
            == "future_planning"
        )
        if expected == "requires_confirmation":
            assert (
                layout.root
                / ".planning_revisions"
                / result["revision_id"]
                / "candidate"
                / "book"
                / "outline.json"
            ).exists()
        # A repeated finalization notification must not spend another exploration.
        repeated = await execution.execute_future_planning(
            runtime, project_id="book", completed_chapter=1
        )
        assert repeated["status"] == expected
        assert explore.await_count == 1


async def test_insufficient_budget_skips_exploration_without_blocking(tmp_path, monkeypatch):
    storage = FileSystemStorage(tmp_path)
    layout = ProjectLayout(storage.ensure_project_dir("book"))
    layout.ensure_dirs()
    storage.save_json(layout.outline_path, _outline().model_dump(mode="json"))
    explore = AsyncMock()
    monkeypatch.setattr(execution, "explore_future_candidate", explore)
    runtime = SimpleNamespace(
        storage=storage,
        router=SimpleNamespace(spending_tracker=SimpleNamespace(daily_spent=1.5)),
        settings=SimpleNamespace(future_planning_mode="adaptive", budget_daily_usd=2),
    )
    result = await execution.execute_future_planning(
        runtime, project_id="book", completed_chapter=1
    )
    assert result["status"] == "budget_skipped"
    explore.assert_not_called()
