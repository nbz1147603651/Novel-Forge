from __future__ import annotations

import json
from pathlib import Path

from novel_forge.core.utils.text_hash import source_text_hash
from novel_forge.film.node_catalog import default_film_graph
from novel_forge.film.workflow_graph import input_signature
from novel_forge.persistence.models import ProjectLayout
from novel_forge.tts.script_integrity import compute_source_text_hash
from novel_forge.workspace.publication import (
    build_chapter_publication_view,
    persist_chapter_publication_view,
    record_cross_media_freshness,
)
from novel_forge.workspace.tts_ops.execution_script import tts_artifact_source_mismatch


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _write_signed_final(
    layout: ProjectLayout,
    *,
    project_id: str,
    chapter_number: int,
    text: str,
    output_version: int = 1,
) -> None:
    final_hash = source_text_hash(text)
    _write_json(
        layout.chapter_artifact_path(chapter_number, "final"),
        {
            "artifact_type": "final",
            "project_id": project_id,
            "artifact_id": f"final-{chapter_number}-{output_version}",
            "source_artifact_ids": [f"humanize-{chapter_number}-{output_version}"],
            "previous_artifact_id": f"humanize-{chapter_number}-{output_version}",
            "payload": {"text_hash": final_hash},
            "workflow_version": "novel.chapter.v2",
            "artifact_schema_version": 2,
            "input_signature": f"signed-{final_hash}",
            "output_version": output_version,
            "parent_artifact_versions": {
                f"humanize-{chapter_number}-{output_version}": output_version
            },
            "execution_quality_status": "actual",
            "derivation_status": "fresh",
            "quality_status": "pass",
        },
    )


def test_publication_projection_blocks_revision_until_normal_finalizer_completes(
    tmp_path: Path,
) -> None:
    project_id = "publication"
    layout = ProjectLayout(tmp_path / project_id)
    layout.ensure_dirs()
    text = "雨声越过窗棂。沈鹿溪按下通话键，旧电台终于重新有了呼吸。"
    layout.chapter_path(1).write_text(text, encoding="utf-8")
    _write_signed_final(layout, project_id=project_id, chapter_number=1, text=text)

    ready = persist_chapter_publication_view(layout, project_id, 1, finalized=True)
    assert ready.publication_status == "ready"
    assert ready.deliverable is True
    assert ready.final_text_hash == source_text_hash(text)

    revised = text + "她没有立刻说话。"
    layout.chapter_path(1).write_text(revised, encoding="utf-8")
    _write_json(
        layout.states_dir / "final_revision_status" / "chapter_001.json",
        {
            "current_hash": source_text_hash(revised),
            "requires_humanize": True,
            "requires_final_verification": True,
            "requires_state_reextract": True,
            "requires_canon_reextract": True,
            "publication_status": "blocked_pending_finalize",
        },
    )
    blocked = persist_chapter_publication_view(layout, project_id, 1)
    assert blocked.publication_status == "blocked_pending_finalize"
    assert blocked.deliverable is False
    assert "humanize_required" in blocked.blocking_reasons
    assert "final_artifact_hash_mismatch" in blocked.blocking_reasons

    _write_signed_final(
        layout,
        project_id=project_id,
        chapter_number=1,
        text=revised,
        output_version=2,
    )
    finalized = persist_chapter_publication_view(layout, project_id, 1, finalized=True)
    assert finalized.publication_status == "ready"
    assert finalized.deliverable is True
    status = json.loads(
        (layout.states_dir / "final_revision_status" / "chapter_001.json").read_text(
            encoding="utf-8"
        )
    )
    assert status["requires_humanize"] is False
    assert status["requires_final_verification"] is False


def test_tts_lineage_marks_old_derivatives_stale_without_deleting_them(tmp_path: Path) -> None:
    project_id = "voice-lineage"
    layout = ProjectLayout(tmp_path / project_id)
    layout.ensure_dirs()
    current_text = "新的终稿已经完成。"
    layout.chapter_path(1).write_text(current_text, encoding="utf-8")
    _write_signed_final(layout, project_id=project_id, chapter_number=1, text=current_text)
    publication = persist_chapter_publication_view(layout, project_id, 1, finalized=True)

    old_hash = source_text_hash("旧版正文")
    _write_json(
        layout.tts_dubbing_script_path(1),
        {
            "chapter_number": 1,
            "segments": [],
            "script_hash": "old-script-hash",
            "source_text_hash": old_hash,
        },
    )
    _write_json(
        layout.tts_audio_result_path(1),
        {
            "metadata": {
                "source_text_hash": old_hash,
                "script_hash": "old-script-hash",
            },
            "script": {
                "chapter_number": 1,
                "segments": [],
                "script_hash": "old-script-hash",
                "source_text_hash": old_hash,
            },
        },
    )
    audio_dir = layout.tts_audio_dir(1)
    audio_dir.mkdir(parents=True, exist_ok=True)
    old_audio = audio_dir / "segment_000.wav"
    old_audio.write_bytes(b"old-audio")

    lineage = record_cross_media_freshness(
        layout,
        publication,
        reason="novel_chapter_finalized",
    )

    assert lineage["tts"]["status"] == "stale"
    assert lineage["tts"]["stages"]["script"]["status"] == "stale"
    assert lineage["tts"]["stages"]["synthesis"]["status"] == "stale"
    assert lineage["tts"]["delivery_blocked"] is True
    assert old_audio.exists()
    assert layout.tts_dubbing_script_path(1).exists()
    mismatch = tts_artifact_source_mismatch(layout, 1, old_hash)
    assert mismatch is not None
    assert mismatch["error_code"] == "stale_tts_artifact"


def test_tts_lineage_accepts_compact_hash_for_current_publication(tmp_path: Path) -> None:
    project_id = "voice-current-lineage"
    layout = ProjectLayout(tmp_path / project_id)
    layout.ensure_dirs()
    current_text = "当前终稿与刚生成的配音产物一致。"
    layout.chapter_path(1).write_text(current_text, encoding="utf-8")
    _write_signed_final(layout, project_id=project_id, chapter_number=1, text=current_text)
    publication = persist_chapter_publication_view(layout, project_id, 1, finalized=True)
    compact_hash = compute_source_text_hash(current_text)
    _write_json(
        layout.tts_dubbing_script_path(1),
        {
            "chapter_number": 1,
            "segments": [],
            "script_hash": "current-script-hash",
            "source_text_hash": compact_hash,
        },
    )
    _write_json(
        layout.tts_audio_result_path(1),
        {
            "metadata": {
                "source_text_hash": compact_hash,
                "script_hash": "current-script-hash",
            },
            "script": {
                "chapter_number": 1,
                "segments": [],
                "script_hash": "current-script-hash",
                "source_text_hash": compact_hash,
            },
        },
    )

    lineage = record_cross_media_freshness(
        layout,
        publication,
        reason="novel_chapter_finalized",
    )

    assert lineage["tts"]["status"] == "fresh"
    assert lineage["tts"]["stages"]["script"]["status"] == "fresh"
    assert lineage["tts"]["stages"]["synthesis"]["status"] == "fresh"
    assert lineage["tts"]["delivery_blocked"] is False


def test_film_load_invalidates_only_affected_locked_chapter_dependencies(
    tmp_path: Path,
) -> None:
    from tests.unit.film.test_film_pipeline import _project

    layout, pipeline = _project(tmp_path)
    layout.chapter_path(1).write_text("第一版章节正文。", encoding="utf-8")
    state = pipeline.get_or_bootstrap()
    character_asset = next(
        asset for asset in state.visual_assets if asset.asset_type == "character"
    )
    shot = state.shots[0]
    assets = [
        asset.model_copy(
            update={"selected_url": "file:///character.png", "locked": True}
        )
        if asset.asset_id == character_asset.asset_id
        else asset
        for asset in state.visual_assets
    ]
    shots = [
        item.model_copy(update={"selected_asset_url": "file:///shot.mp4", "locked": True})
        if item.shot_id == shot.shot_id
        else item
        for item in state.shots
    ]
    pipeline.store.save(state.model_copy(update={"visual_assets": assets, "shots": shots}))

    layout.chapter_path(1).write_text("第二版章节正文，增加关键动作。", encoding="utf-8")
    reconciled = pipeline.get_or_bootstrap()
    current_character = next(
        asset for asset in reconciled.visual_assets if asset.asset_id == character_asset.asset_id
    )
    current_shot = next(item for item in reconciled.shots if item.shot_id == shot.shot_id)

    assert current_character.derivation_status == "fresh"
    assert current_character.selected_url == "file:///character.png"
    assert current_shot.derivation_status == "conflict"
    assert current_shot.stale_decision == "pending"
    assert current_shot.selected_asset_url == "file:///shot.mp4"
    assert f"stale_shot_decision:{shot.shot_id}" in reconciled.delivery_blocking_reasons

    rebound = pipeline.resolve_lineage_decision(shot.shot_id, "rebind")
    rebound_shot = next(item for item in rebound.shots if item.shot_id == shot.shot_id)
    assert rebound_shot.derivation_status == "fresh"
    assert rebound_shot.source_signature == rebound.source_signature
    assert f"stale_shot_decision:{shot.shot_id}" not in rebound.delivery_blocking_reasons


def test_film_graph_signatures_invalidate_only_source_dependency_closure() -> None:
    graph = default_film_graph("lineage")
    graph = graph.model_copy(
        update={
            "source_signatures": {
                "story_spec": "spec-v1",
                "character_bible": "characters-v1",
                "novel_chapter:1": "chapter-v1",
            }
        }
    )
    character_before = input_signature(graph, "character-assets")
    scene_before = input_signature(graph, "scene-assets")
    delivery_before = input_signature(graph, "delivery")

    changed = graph.model_copy(
        update={
            "source_signatures": {
                **graph.source_signatures,
                "novel_chapter:1": "chapter-v2",
            }
        }
    )

    assert input_signature(changed, "character-assets") == character_before
    assert input_signature(changed, "scene-assets") != scene_before
    assert input_signature(changed, "delivery") != delivery_before


def test_tts_rejects_a_revision_that_has_not_reentered_humanize(tmp_path: Path) -> None:
    project_id = "blocked-voice"
    layout = ProjectLayout(tmp_path / project_id)
    layout.ensure_dirs()
    text = "这是一份仍待重新 Humanize 的修订稿。"
    layout.chapter_path(1).write_text(text, encoding="utf-8")
    _write_signed_final(layout, project_id=project_id, chapter_number=1, text=text)
    _write_json(
        layout.states_dir / "final_revision_status" / "chapter_001.json",
        {
            "current_hash": source_text_hash(text),
            "requires_humanize": True,
            "requires_final_verification": True,
            "publication_status": "blocked_pending_finalize",
        },
    )

    view = build_chapter_publication_view(layout, project_id, 1)
    mismatch = tts_artifact_source_mismatch(layout, 1, view.final_text_hash)

    assert view.deliverable is False
    assert mismatch is not None
    assert mismatch["error_code"] == "novel_publication_blocked"
