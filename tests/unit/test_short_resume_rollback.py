"""Regression tests for short-mode resume rollback."""

from __future__ import annotations

from novel_forge.core.config import Settings
from novel_forge.core.schemas.beats import Beat, StoryBeats
from novel_forge.core.schemas.short_blueprint import ShortBlueprint
from novel_forge.core.schemas.spec import StorySpec
from novel_forge.gateway.adapters.mock import MockAdapter
from novel_forge.gateway.router import ModelRouter
from novel_forge.persistence.filesystem import FileSystemStorage
from novel_forge.persistence.models import ProjectLayout
from novel_forge.pipeline.short_runner import ShortStoryRunner
from novel_forge.prompts.builder import PromptBuilder


def _runner(tmp_path, events: list[tuple[str, object]]) -> ShortStoryRunner:
    storage = FileSystemStorage(tmp_path)
    router = ModelRouter(adapters={"mock": MockAdapter()}, default_provider="mock")
    return ShortStoryRunner(
        router,
        PromptBuilder(),
        storage,
        settings=Settings(_env_file=None),
        on_step_progress=lambda step, data: events.append((step, data)),
    )


def test_short_resume_rolls_back_invalid_blueprint_and_downstream(tmp_path) -> None:
    events: list[tuple[str, object]] = []
    runner = _runner(tmp_path, events)
    layout = ProjectLayout(runner._storage.ensure_project_dir("short_resume_rollback"))
    layout.ensure_dirs()

    runner._storage.save_json(
        layout.spec_path,
        StorySpec(theme="雨夜归人", genre="romance").model_dump(mode="json"),
    )
    runner._storage.save_json(
        layout.short_blueprint_path(),
        {"synopsis": "坏蓝图", "narrative_phases": "not-a-list"},
    )
    runner._storage.save_json(
        layout.short_beats_path(),
        StoryBeats(beats=[Beat(sequence=1, summary="开场")]).model_dump(mode="json"),
    )
    runner._storage.save_text(layout.short_draft_path(0), "已经生成的草稿")
    runner._storage.save_text(layout.chapters_dir / "short_story.md", "旧成稿")

    checkpoint = runner._detect_checkpoint(layout)

    assert "spec" in checkpoint
    assert "blueprint" not in checkpoint
    assert "beats" not in checkpoint
    assert "draft_text" not in checkpoint
    assert layout.spec_path.exists()
    assert not layout.short_blueprint_path().exists()
    assert not layout.short_beats_path().exists()
    assert not layout.short_draft_path(0).exists()
    assert not (layout.chapters_dir / "short_story.md").exists()
    assert events and events[0][0] == "short_resume_rollback"

    rollback_dirs = list((layout.states_dir / "short_resume_rollbacks").glob("*_blueprint"))
    assert len(rollback_dirs) == 1
    rollback_root = rollback_dirs[0]
    assert (rollback_root / "plans" / "short_blueprint.json").exists()
    assert (rollback_root / "beats.json").exists()
    assert (rollback_root / "drafts" / "v0_draft.md").exists()
    assert (rollback_root / "chapters" / "short_story.md").exists()


def test_short_resume_rolls_back_empty_draft_only_after_beats(tmp_path) -> None:
    events: list[tuple[str, object]] = []
    runner = _runner(tmp_path, events)
    layout = ProjectLayout(runner._storage.ensure_project_dir("short_resume_empty_draft"))
    layout.ensure_dirs()

    runner._storage.save_json(
        layout.spec_path,
        StorySpec(theme="空草稿", genre="suspense").model_dump(mode="json"),
    )
    runner._storage.save_json(
        layout.short_blueprint_path(),
        ShortBlueprint(synopsis="有效蓝图").model_dump(mode="json"),
    )
    runner._storage.save_json(
        layout.short_beats_path(),
        StoryBeats(beats=[Beat(sequence=1, summary="开场")]).model_dump(mode="json"),
    )
    runner._storage.save_text(layout.short_draft_path(0), "   ")
    runner._storage.save_text(layout.chapters_dir / "short_story.md", "旧成稿")

    checkpoint = runner._detect_checkpoint(layout)

    assert "spec" in checkpoint
    assert "blueprint" in checkpoint
    assert "beats" in checkpoint
    assert "draft_text" not in checkpoint
    assert layout.spec_path.exists()
    assert layout.short_blueprint_path().exists()
    assert layout.short_beats_path().exists()
    assert not layout.short_draft_path(0).exists()
    assert not (layout.chapters_dir / "short_story.md").exists()
    assert events and events[0][0] == "short_resume_rollback"
