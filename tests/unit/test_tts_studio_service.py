"""Tests for the Qt-independent Voice Studio project facade."""

from __future__ import annotations

import json

import pytest

import novel_forge.tts.assets.sound_library as sound_library
from novel_forge.persistence.models import ProjectLayout
from novel_forge.tts.schemas import (
    BGMTiming,
    ChapterAudioResult,
    DubbingScript,
    DubbingSegment,
    SegmentTakeVersion,
    SegmentType,
    SFXCue,
    SoundscapeCue,
    SynthesisResult,
    SynthesisStatus,
    TakeReviewStatus,
)
from novel_forge.tts.services.studio_service import (
    SoundLibraryQuery,
    VoiceStudioProjectService,
)


def test_studio_service_owns_sound_library_mutations_and_filters(tmp_path) -> None:
    layout = ProjectLayout(tmp_path / "project")
    layout.ensure_dirs()
    source = tmp_path / "night rain.wav"
    source.write_bytes(b"audio" * 100)
    service = VoiceStudioProjectService(layout, project_id="demo")

    first = service.import_sound_assets(
        [source],
        kind="soundscape",
        tags=["夜雨", "悬疑", "夜雨"],
    )
    second = service.import_sound_assets([source], kind="soundscape", tags=["备用"])

    assert first.count == 1
    assert second.count == 1
    assert first.assets[0].asset_id != second.assets[0].asset_id
    assert service.sound_asset_path(first.assets[0]).is_file()
    assert service.sound_asset_path(second.assets[0]).is_file()

    approved = service.set_sound_asset_status(first.assets[0].asset_id, "approved")
    service.set_sound_asset_status(second.assets[0].asset_id, "rejected")
    updated = service.update_sound_asset_tags(approved.asset_id, ["城市", "夜雨", "城市"])

    assert updated.tags == ["城市", "夜雨"]
    assert [
        asset.asset_id for asset in service.list_sound_assets(SoundLibraryQuery(status="approved"))
    ] == [first.assets[0].asset_id]
    assert len(service.list_sound_assets(SoundLibraryQuery(kind="soundscape"))) == 2


def test_studio_service_projects_story_context_without_qt(tmp_path) -> None:
    layout = ProjectLayout(tmp_path / "project")
    layout.ensure_dirs()
    layout.spec_path.write_text(
        json.dumps(
            {
                "title": "青瓦梦魇",
                "genre": "古风悬疑",
                "theme": "记忆与真相",
                "tone": "幽冷",
                "audio_aesthetic_hint": "近距旁白",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    layout.bible_path.write_text(
        json.dumps(
            {
                "title": "青瓦梦魇",
                "premise": "仵作追查旧城谜案。",
                "era": "架空古代",
                "geography": "临水旧城",
                "culture": "宗族社会",
                "themes": ["真相", "记忆"],
                "audio_aesthetic": "克制的近距叙事",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    context = VoiceStudioProjectService(layout).story_sound_context()

    assert context["premise"] == "仵作追查旧城谜案。"
    assert context["genre"] == "古风悬疑"
    assert context["world"] == "架空古代；临水旧城；宗族社会"
    assert context["audio_aesthetic"] == "克制的近距叙事"


def test_studio_service_publishes_an_approved_project_asset_to_the_application_library(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(
        sound_library,
        "application_sound_library_root",
        lambda: tmp_path / "application-assets" / "audio",
    )
    layout = ProjectLayout(tmp_path / "project")
    layout.ensure_dirs()
    source = tmp_path / "rain.wav"
    source.write_bytes(b"audio" * 64)
    service = VoiceStudioProjectService(layout)
    imported = service.import_sound_assets([source], kind="soundscape", tags=["夜雨"])

    assert imported.assets[0].commercial_use_status == "review_required"
    with pytest.raises(ValueError, match="商用授权"):
        service.publish_sound_asset(imported.assets[0].asset_id)
    cleared = service.set_sound_asset_commercial_rights(
        imported.assets[0].asset_id,
        status="cleared",
        license_note="自有现场录音，录音人已签署商业授权",
    )
    assert cleared.commercial_use_status == "cleared"
    assert cleared.commercial_use_reviewed_at is not None

    application_asset = service.publish_sound_asset(imported.assets[0].asset_id)

    assert application_asset.scope == "application"
    assert application_asset.commercial_use_status == "cleared"
    assert service.sound_asset_path(application_asset).is_file()
    assert application_asset.asset_id in {asset.asset_id for asset in service.all_sound_assets()}
    assert [
        asset.asset_id
        for asset in service.list_sound_assets(SoundLibraryQuery(scope="application"))
    ] == [application_asset.asset_id]


def test_studio_service_returns_structured_segment_sound_context(tmp_path) -> None:
    layout = ProjectLayout(tmp_path / "project")
    service = VoiceStudioProjectService(layout)
    segment = DubbingSegment(
        segment_index=2,
        segment_type=SegmentType.NARRATION,
        text="门外传来脚步声。",
        scene_context="深夜书房",
    )
    script = DubbingScript(
        chapter_number=1,
        segments=[segment],
        soundscapes=[SoundscapeCue(name="夜雨", start_segment_index=0, end_segment_index=4)],
        bgm_suggestions=[
            BGMTiming(track_name="暗线低鸣", start_segment_index=1, end_segment_index=3)
        ],
        sfx_cues=[SFXCue(effect_name="脚步逼近", trigger_segment_index=2, offset_ms=180)],
    )

    context = service.segment_sound_context(script, segment)

    assert context.scene == "深夜书房"
    assert context.ambience == ("夜雨",)
    assert context.music == ("暗线低鸣",)
    assert context.effects == (("脚步逼近", 180),)
    assert context.has_cues is True


def test_take_cleanup_preserves_only_formally_referenced_accepted_audio(tmp_path) -> None:
    layout = ProjectLayout(tmp_path / "project")
    layout.ensure_dirs()
    service = VoiceStudioProjectService(layout)
    segment = DubbingSegment(
        segment_index=0,
        segment_type=SegmentType.DIALOGUE,
        text="坐。",
    )
    approved = layout.tts_approved_take_dir(1) / "seg_0000_keep.mp3"
    candidate = layout.tts_candidate_take_dir(1, "candidate") / "seg_0000.mp3"
    rejected = layout.tts_candidate_take_dir(1, "rejected") / "seg_0000.mp3"
    for path, payload in (
        (approved, b"approved"),
        (candidate, b"candidate"),
        (rejected, b"rejected"),
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)

    takes = [
        SegmentTakeVersion(
            take_id="accepted",
            chapter_number=1,
            segment_index=0,
            status=TakeReviewStatus.ACCEPTED,
            segment=segment,
            segment_result=SynthesisResult(
                segment_index=0,
                status=SynthesisStatus.COMPLETED,
                audio_path=str(approved),
            ),
        ),
        SegmentTakeVersion(
            take_id="candidate",
            chapter_number=1,
            segment_index=0,
            status=TakeReviewStatus.CANDIDATE,
            segment=segment,
            segment_result=SynthesisResult(
                segment_index=0,
                status=SynthesisStatus.COMPLETED,
                audio_path=str(candidate),
            ),
        ),
        SegmentTakeVersion(
            take_id="rejected",
            chapter_number=1,
            segment_index=0,
            status=TakeReviewStatus.REJECTED,
            segment=segment,
            segment_result=SynthesisResult(
                segment_index=0,
                status=SynthesisStatus.COMPLETED,
                audio_path=str(rejected),
            ),
        ),
    ]
    manifest = service.load_take_manifest(1)
    manifest.takes = takes
    manifest.drafts = {"0": segment}
    service.save_take_manifest(manifest)
    result = ChapterAudioResult(
        chapter_number=1,
        script=DubbingScript(chapter_number=1, segments=[segment]),
        segment_results=[takes[0].segment_result],
    )
    result_path = layout.tts_audio_result_path(1)
    result_path.parent.mkdir(parents=True, exist_ok=True)
    result_path.write_text(result.model_dump_json(), encoding="utf-8")

    cleanup = service.cleanup_redundant_takes(1)

    assert cleanup.removed_files == 2
    assert approved.is_file()
    assert not candidate.exists()
    assert not rejected.exists()
    cleaned = service.load_take_manifest(1)
    assert [take.take_id for take in cleaned.takes] == ["accepted"]
    assert cleaned.drafts == {}


def test_new_candidate_retires_previous_pending_take_for_same_segment(tmp_path) -> None:
    layout = ProjectLayout(tmp_path / "project")
    layout.ensure_dirs()
    service = VoiceStudioProjectService(layout)
    segment = DubbingSegment(
        segment_index=0,
        segment_type=SegmentType.DIALOGUE,
        text="坐。",
    )

    for take_id in ("first", "second"):
        path = layout.tts_candidate_take_dir(1, take_id) / "seg_0000.mp3"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(take_id.encode())
        service.register_candidate_take(
            SegmentTakeVersion(
                take_id=take_id,
                chapter_number=1,
                segment_index=0,
                segment=segment,
                segment_result=SynthesisResult(
                    segment_index=0,
                    status=SynthesisStatus.COMPLETED,
                    audio_path=str(path),
                ),
                source_script_hash="script-v1",
            )
        )

    manifest = service.load_take_manifest(1)
    assert [take.status for take in manifest.takes] == [
        TakeReviewStatus.REJECTED,
        TakeReviewStatus.CANDIDATE,
    ]


def test_saving_guidance_batch_retires_candidates_and_persists_every_edit(tmp_path) -> None:
    layout = ProjectLayout(tmp_path / "project")
    layout.ensure_dirs()
    service = VoiceStudioProjectService(layout)
    source = DubbingSegment(
        segment_index=0,
        segment_type=SegmentType.DIALOGUE,
        character_name="沈青",
        text="别回头。",
    )
    candidate = SegmentTakeVersion(
        take_id="candidate-1",
        chapter_number=1,
        segment_index=0,
        segment=source,
        segment_result=SynthesisResult(
            segment_index=0,
            status=SynthesisStatus.COMPLETED,
        ),
        source_script_hash="script-v1",
    )
    service.register_candidate_take(candidate)
    revised = source.model_copy(update={"tone_hint": "压低声音", "emotion_intensity": 0.7})
    narration = DubbingSegment(
        segment_index=1,
        segment_type=SegmentType.NARRATION,
        text="雨声盖过了脚步。",
        tone_hint="克制",
    )

    manifest = service.save_take_drafts(
        1,
        script_hash="script-v1",
        segments=(revised, narration),
    )

    assert manifest.takes[0].status == TakeReviewStatus.REJECTED
    assert manifest.drafts["0"].tone_hint == "压低声音"
    assert manifest.drafts["1"].tone_hint == "克制"
    persisted = service.load_take_manifest(1)
    assert set(persisted.drafts) == {"0", "1"}
