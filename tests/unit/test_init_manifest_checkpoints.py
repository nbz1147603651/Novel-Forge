"""Manifest validation for initialization checkpoints outside final artifacts."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from novel_forge.core.schemas.outline import ChapterOutline
from novel_forge.persistence.filesystem import FileSystemStorage
from novel_forge.persistence.models import ProjectLayout
from novel_forge.pipeline.artifact_manifest import ArtifactManifest
from novel_forge.pipeline.long.services.init.init_orchestrator import (
    _record_character_system_manifest,
)
from novel_forge.pipeline.long.services.init.init_outline_helpers import (
    _load_outline_batch_checkpoint_chapters,
    _save_outline_batch_checkpoint,
)


def _project(tmp_path: Path) -> tuple[FileSystemStorage, ProjectLayout]:
    storage = FileSystemStorage(tmp_path)
    layout = ProjectLayout(storage.ensure_project_dir("init-manifest"))
    layout.ensure_dirs()
    return storage, layout


def test_outline_batch_manifest_mismatch_invalidates_checkpoint(tmp_path: Path) -> None:
    storage, layout = _project(tmp_path)
    chapter = ChapterOutline(
        chapter_number=1,
        title="第一章",
        goal="建立冲突",
        beats_summary=["冲突出现。"],
        main_plot_points=["主线启动。"],
    )
    _save_outline_batch_checkpoint(
        storage,
        layout,
        total_chapters=1,
        batch_start=1,
        batch_end=1,
        chapters=[chapter],
        missing_chapters=[],
        status="accepted",
        source_hashes={"outline": "source-a"},
    )
    assert _load_outline_batch_checkpoint_chapters(storage, layout, total_chapters=1)

    manifest_path = layout.states_dir / "artifact_manifest.json"
    payload = storage.load_json(manifest_path)
    payload["artifacts"]["outline_batch:1:1"]["output_hashes"]["chapters"] = "tampered"
    storage.save_json(manifest_path, payload)

    assert _load_outline_batch_checkpoint_chapters(storage, layout, total_chapters=1) == []


def test_character_system_checkpoint_records_manifest_signature(tmp_path: Path) -> None:
    storage, layout = _project(tmp_path)
    ctx = SimpleNamespace(storage=storage, layout=layout)
    cache = SimpleNamespace(
        path_for=lambda name: layout.states_dir / "init_v2" / f"{name}.json"
    )
    upstreams = {"story_bible": "story-a", "character_bible": "characters-a"}

    _record_character_system_manifest(
        ctx,
        cache=cache,
        upstream_hashes=upstreams,
        character_system={"characters": [], "relationship_edges": []},
        source="generated",
    )

    record = ArtifactManifest(storage, layout).get("character_system")
    assert record is not None
    assert record.status == "succeeded"
    assert record.input_hashes == upstreams
    assert record.input_signature != "legacy_unknown"
