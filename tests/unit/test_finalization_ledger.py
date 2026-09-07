"""Crash-recovery invariants for the chapter finalization manifest."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from novel_forge.core.exceptions import StateError
from novel_forge.core.utils.text_hash import source_text_hash
from novel_forge.narrative_state.schemas import AdjudicationDecision, StateLedgerEntry
from novel_forge.narrative_state.store import NarrativeStateStore
from novel_forge.persistence.filesystem import FileSystemStorage
from novel_forge.persistence.models import ProjectLayout
from novel_forge.pipeline.artifact_manifest import ArtifactManifest
from novel_forge.pipeline.finalization_manifest import (
    matching_finalization_phase,
    record_finalization_success,
)
from novel_forge.workspace.helpers.execution_runners import (
    _ensure_required_finalization_phases,
)
from novel_forge.workspace.publication import reconcile_pending_publication_projections


def _record_phase(
    manifest: ArtifactManifest,
    *,
    chapter_number: int,
    phase: str,
    text_hash: str,
) -> None:
    record_finalization_success(
        manifest,
        chapter_number=chapter_number,
        phase=phase,
        text_hash=text_hash,
    )


def _write_signed_final(layout: ProjectLayout, text_hash: str) -> None:
    layout.chapter_artifact_path(1, "final").parent.mkdir(parents=True, exist_ok=True)
    FileSystemStorage(layout.root.parent).save_json(
        layout.chapter_artifact_path(1, "final"),
        {
            "artifact_type": "final",
            "input_signature": f"signed-{text_hash}",
            "payload": {"text_hash": text_hash},
            "execution_quality_status": "actual",
            "derivation_status": "fresh",
            "quality_status": "pass",
        },
    )


def test_narrative_state_replay_deduplicates_stable_entry_id(tmp_path) -> None:
    store = NarrativeStateStore(tmp_path)
    entry = StateLedgerEntry(
        entry_id="state_1_replay_accept",
        chapter_number=1,
        candidate_id="replay",
        summary="唯一状态写入",
        decision=AdjudicationDecision(candidate_id="replay", verdict="accept"),
    )

    store.append_entries([entry])
    store.append_entries([entry])

    assert [item.entry_id for item in store.load_ledger_entries()] == [entry.entry_id]


def test_workspace_success_gate_requires_configured_state_phases(tmp_path) -> None:
    storage = FileSystemStorage(tmp_path)
    project_id = "required-state"
    layout = ProjectLayout(storage.ensure_project_dir(project_id))
    layout.ensure_dirs()
    text = "终稿已经落盘。"
    layout.chapter_path(1).write_text(text, encoding="utf-8")
    text_hash = source_text_hash(text)
    manifest = ArtifactManifest(storage, layout)
    for phase in ("final_text", "reports"):
        _record_phase(manifest, chapter_number=1, phase=phase, text_hash=text_hash)
    runtime = SimpleNamespace(
        storage=storage,
        settings=SimpleNamespace(narrative_state_required=True),
    )

    with pytest.raises(StateError, match="narrative_state,story_kernel"):
        _ensure_required_finalization_phases(
            runtime,
            project_id=project_id,
            chapter_number=1,
        )

    for phase in ("narrative_state", "story_kernel"):
        _record_phase(manifest, chapter_number=1, phase=phase, text_hash=text_hash)
    _ensure_required_finalization_phases(runtime, project_id=project_id, chapter_number=1)


def test_startup_rebuilds_missing_publication_projection(tmp_path) -> None:
    storage = FileSystemStorage(tmp_path)
    project_id = "publication-recovery"
    layout = ProjectLayout(storage.ensure_project_dir(project_id))
    layout.ensure_dirs()
    text = "终稿提交后，出版投影在重启时补齐。"
    layout.chapter_path(1).write_text(text, encoding="utf-8")
    text_hash = source_text_hash(text)
    _write_signed_final(layout, text_hash)
    manifest = ArtifactManifest(storage, layout)
    _record_phase(manifest, chapter_number=1, phase="final_text", text_hash=text_hash)

    stats = reconcile_pending_publication_projections(tmp_path)

    assert stats == {"projects": 1, "scanned": 1, "recovered": 1, "failed": 0}
    assert layout.chapter_publication_path(1).is_file()
    assert (
        matching_finalization_phase(
            ArtifactManifest(storage, layout),
            chapter_number=1,
            phase="publication_projection",
            text_hash=text_hash,
        )
        is not None
    )
