"""Tests for categorized TTS file cleanup.

Each test asserts both that orphans ARE removed and that protected artifacts
(voice_team.json, approved takes, referenced sound assets, live previews) are
NOT touched.
"""

from __future__ import annotations

import json
from pathlib import Path

from novel_forge.persistence.models import ProjectLayout
from novel_forge.tts.runtime.cleanup import (
    ALL_CATEGORIES,
    CAT_COMPLETED_CHECKPOINTS,
    CAT_ORPHAN_CANDIDATES,
    CAT_ORPHAN_CHAPTER_REPORTS,
    CAT_ORPHAN_SOUND_ASSETS,
    CAT_STALE_PREVIEWS,
    execute_cleanup_tts_files,
    execute_reset_project_tts_artifacts,
    preview_cleanup_tts_files,
    preview_reset_project_tts_artifacts,
)


def _layout(tmp_path: Path) -> ProjectLayout:
    layout = ProjectLayout(tmp_path / "demo")
    layout.ensure_dirs()
    layout.tts_audio_dir(0).mkdir(parents=True, exist_ok=True)
    (layout.states_dir / "tts_progress").mkdir(parents=True, exist_ok=True)
    return layout


def test_stale_previews_removes_orphan_keeps_referenced(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    preview_root = layout.tts_audio_dir(0)
    stale = preview_root / "preview_stale.mp3"
    live = preview_root / "preview_live.mp3"
    stale.write_bytes(b"stale")
    live.write_bytes(b"live")
    # Reference the live one from voice_team.json.
    layout.tts_voice_team_path.write_text(
        json.dumps(
            {"entries": [{"character_id": "c1", "preview_audio_path": str(live)}]},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    report = execute_cleanup_tts_files(layout, categories={CAT_STALE_PREVIEWS})
    cr = report.categories[CAT_STALE_PREVIEWS]
    assert str(stale) in cr.removed_files
    assert not stale.exists()
    assert live.exists(), "referenced preview must be preserved"


def test_orphan_candidates_removes_unreferenced_keeps_approved(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    cand_dir = layout.tts_dir / "takes" / "chapter_001" / "candidates" / "take1"
    cand_dir.mkdir(parents=True)
    orphan_audio = cand_dir / "seg_0000.mp3"
    orphan_audio.write_bytes(b"orphan")
    # An approved take that IS referenced by the chapter result.
    approved_dir = layout.tts_dir / "takes" / "chapter_001" / "approved"
    approved_dir.mkdir(parents=True)
    approved_audio = approved_dir / "seg_0000.mp3"
    approved_audio.write_bytes(b"approved")
    layout.tts_audio_result_path(1).parent.mkdir(parents=True, exist_ok=True)
    layout.tts_audio_result_path(1).write_text(
        json.dumps(
            {"segment_results": [{"audio_path": str(approved_audio)}]},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    execute_cleanup_tts_files(layout, categories={CAT_ORPHAN_CANDIDATES})
    assert not orphan_audio.parent.exists(), "orphan candidate dir removed"
    assert approved_audio.exists(), "approved take must be preserved"


def test_completed_checkpoints_removes_when_complete_keeps_in_progress(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    # Chapter 1: completed -> checkpoint stale.
    layout.tts_audio_result_path(1).parent.mkdir(parents=True, exist_ok=True)
    layout.tts_audio_result_path(1).write_text(
        json.dumps({"is_complete": True}, ensure_ascii=False), encoding="utf-8"
    )
    done_ckpt = layout.states_dir / "tts_progress" / "chapter_001.json"
    done_ckpt.write_text("{}", encoding="utf-8")
    # Chapter 2: incomplete -> checkpoint must be kept.
    layout.tts_audio_result_path(2).parent.mkdir(parents=True, exist_ok=True)
    layout.tts_audio_result_path(2).write_text(
        json.dumps({"is_complete": False}, ensure_ascii=False), encoding="utf-8"
    )
    active_ckpt = layout.states_dir / "tts_progress" / "chapter_002.json"
    active_ckpt.write_text("{}", encoding="utf-8")
    report = execute_cleanup_tts_files(layout, categories={CAT_COMPLETED_CHECKPOINTS})
    cr = report.categories[CAT_COMPLETED_CHECKPOINTS]
    assert str(done_ckpt) in cr.removed_files
    assert not done_ckpt.exists()
    assert active_ckpt.exists(), "in-progress checkpoint must be preserved"


def test_orphan_sound_assets_removes_untracked_keeps_tracked(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    gen_dir = layout.tts_generated_sound_assets_dir / "bgm"
    gen_dir.mkdir(parents=True)
    orphan = gen_dir / "orphan_fingerprint.mp3"
    tracked = gen_dir / "tracked_fingerprint.mp3"
    orphan.write_bytes(b"orphan")
    tracked.write_bytes(b"tracked")
    # sound_library.json tracks only the tracked one.
    layout.tts_sound_library_path.write_text(
        json.dumps(
            {
                "assets": [
                    {
                        "asset_id": "a1",
                        "kind": "bgm",
                        "relative_path": "generated/bgm/tracked_fingerprint.mp3",
                        "approval_status": "approved",
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    report = execute_cleanup_tts_files(layout, categories={CAT_ORPHAN_SOUND_ASSETS})
    cr = report.categories[CAT_ORPHAN_SOUND_ASSETS]
    assert str(orphan) in cr.removed_files
    assert not orphan.exists()
    assert tracked.exists(), "tracked sound asset must be preserved"
    assert layout.tts_sound_library_path.exists(), "library file itself must never be deleted"


def test_orphan_chapter_reports_removes_when_result_gone_keeps_when_present(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    timelines = layout.tts_dir / "timelines"
    timelines.mkdir(parents=True, exist_ok=True)
    orphan_report = timelines / "chapter_001.json"
    orphan_report.write_text("{}", encoding="utf-8")
    # Chapter 2 still has a result -> its report must be kept.
    layout.tts_audio_result_path(2).parent.mkdir(parents=True, exist_ok=True)
    layout.tts_audio_result_path(2).write_text(
        json.dumps({"is_complete": True}, ensure_ascii=False), encoding="utf-8"
    )
    live_report = timelines / "chapter_002.json"
    live_report.write_text("{}", encoding="utf-8")
    report = execute_cleanup_tts_files(layout, categories={CAT_ORPHAN_CHAPTER_REPORTS})
    cr = report.categories[CAT_ORPHAN_CHAPTER_REPORTS]
    assert str(orphan_report) in cr.removed_files
    assert not orphan_report.exists()
    assert live_report.exists(), "report for chapter with a live result must be preserved"


def test_voice_team_json_is_never_deleted(tmp_path: Path) -> None:
    """The project voice contract must survive any cleanup pass."""
    layout = _layout(tmp_path)
    layout.tts_voice_team_path.write_text(
        json.dumps({"entries": []}, ensure_ascii=False), encoding="utf-8"
    )
    execute_cleanup_tts_files(layout, categories=set(ALL_CATEGORIES))
    assert layout.tts_voice_team_path.exists()


def test_empty_categories_removes_nothing(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    (layout.tts_audio_dir(0) / "preview_keep.mp3").write_bytes(b"keep")
    report = execute_cleanup_tts_files(layout, categories=set())
    assert report.removed_count == 0
    assert (layout.tts_audio_dir(0) / "preview_keep.mp3").exists()


def test_cleanup_preview_reports_candidates_without_deleting_them(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    stale = layout.tts_audio_dir(0) / "preview_stale.mp3"
    stale.write_bytes(b"preview-bytes")

    report = preview_cleanup_tts_files(layout, categories={CAT_STALE_PREVIEWS})

    assert report.removed_count == 1
    assert report.reclaimed_bytes == len(b"preview-bytes")
    assert str(stale) in report.categories[CAT_STALE_PREVIEWS].removed_files
    assert stale.exists(), "opening the cleanup chooser must never delete assets"


def test_project_reset_removes_all_regenerable_outputs_and_preserves_contracts(
    tmp_path: Path,
) -> None:
    layout = _layout(tmp_path)
    protected = {
        layout.tts_voice_team_path: b'{"entries": []}',
        layout.tts_narrator_profile_path: b'{"voice_id": "narrator"}',
        layout.tts_sound_library_path: b'{"assets": []}',
        layout.tts_audio_creative_bible_path: b"{}",
        layout.tts_execution_plan_path: b"{}",
        layout.tts_model_scorecards_path: b"{}",
    }
    for path, content in protected.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    reusable_asset = layout.tts_sound_assets_dir / "custom" / "door.wav"
    reusable_asset.parent.mkdir(parents=True, exist_ok=True)
    reusable_asset.write_bytes(b"reusable")

    outputs = [
        layout.tts_dubbing_script_path(1),
        layout.tts_audio_result_path(1),
        layout.tts_audio_dir(1) / "segment_0000.mp3",
        layout.tts_dir / "takes" / "chapter_001" / "candidate.mp3",
        layout.tts_dir / "timelines" / "chapter_001.json",
        layout.states_dir / "tts_progress" / "chapter_001.json",
        layout.reports_dir / "chapter_001_tts_metadata.json",
    ]
    for output in outputs:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"regenerable")

    preview = preview_reset_project_tts_artifacts(layout)

    assert preview.removed_count == len(outputs)
    assert all(path.exists() for path in outputs)
    assert reusable_asset.exists()

    report = execute_reset_project_tts_artifacts(layout)

    assert report.removed_count == len(outputs)
    assert all(not path.exists() for path in outputs)
    assert all(path.read_bytes() == content for path, content in protected.items())
    assert reusable_asset.read_bytes() == b"reusable"
    assert (layout.tts_dir / "audio").is_dir(), (
        "standard output roots stay ready for regeneration"
    )
