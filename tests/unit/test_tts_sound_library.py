"""Regression coverage for project-owned environmental sound assets."""

from __future__ import annotations

import novel_forge.tts.assets.sound_library as sound_library
from novel_forge.persistence.models import ProjectLayout
from novel_forge.tts.assets.sound_library import (
    application_sound_assets_dir,
    commercial_rights_issues,
    resolve_sound_cues,
    resolved_asset_paths,
    save_application_sound_library,
    save_sound_library,
    set_sound_asset_approval,
)
from novel_forge.tts.schemas import (
    BGMTiming,
    DubbingScript,
    SFXCue,
    SoundAsset,
    SoundLibraryManifest,
    SoundscapeCue,
)


def test_sound_library_resolves_ambient_bgm_and_sfx_without_guessing_paths(tmp_path) -> None:
    layout = ProjectLayout(tmp_path / "demo")
    layout.ensure_dirs()
    for name in ("rain_city.mp3", "noir_bed.mp3", "door_knock.wav"):
        (layout.tts_sound_assets_dir / name).write_bytes(b"audio")
    library = SoundLibraryManifest(
        assets=[
            SoundAsset(
                asset_id="rain_city_night",
                kind="soundscape",
                display_name="夜雨与远处车流",
                relative_path="rain_city.mp3",
                tags=["夜雨", "城市", "雨声"],
                loopable=True,
            ),
            SoundAsset(
                asset_id="noir_bed",
                kind="bgm",
                display_name="悬疑底乐",
                relative_path="noir_bed.mp3",
                tags=["悬疑", "紧张"],
            ),
            SoundAsset(
                asset_id="door_knock",
                kind="sfx",
                display_name="敲门",
                relative_path="door_knock.wav",
                tags=["敲门", "门响"],
            ),
        ]
    )
    script = DubbingScript(
        chapter_number=1,
        soundscapes=[SoundscapeCue(name="夜雨", asset_hint="rain_city_night")],
        bgm_suggestions=[BGMTiming(track_name="悬疑底乐", mood="紧张")],
        sfx_cues=[SFXCue(effect_name="敲门")],
    )

    report = resolve_sound_cues(layout=layout, script=script, library=library)
    paths = resolved_asset_paths(layout, report)

    assert report.matched_count == 3
    assert report.unresolved_count == 0
    assert paths[("soundscape", 0)].name == "rain_city.mp3"
    assert paths[("bgm", 0)].name == "noir_bed.mp3"
    assert paths[("sfx", 0)].name == "door_knock.wav"
    assert len(commercial_rights_issues(layout, report, library=library)) == 3


def test_commercial_rights_gate_accepts_only_explicitly_cleared_matched_assets(tmp_path) -> None:
    layout = ProjectLayout(tmp_path / "rights")
    layout.ensure_dirs()
    (layout.tts_sound_assets_dir / "bed.wav").write_bytes(b"audio")
    library = SoundLibraryManifest(
        assets=[
            SoundAsset(
                asset_id="licensed_bed",
                kind="bgm",
                display_name="已授权底乐",
                relative_path="bed.wav",
                tags=["宁静"],
                commercial_use_status="cleared",
                license_note="商业素材订单 NF-2026-001",
            )
        ]
    )
    report = resolve_sound_cues(
        layout=layout,
        script=DubbingScript(
            chapter_number=1,
            bgm_suggestions=[BGMTiming(track_name="已授权底乐", mood="宁静")],
        ),
        library=library,
    )

    assert commercial_rights_issues(layout, report, library=library) == []


def test_sound_library_reports_missing_cues_for_author_follow_up(tmp_path) -> None:
    layout = ProjectLayout(tmp_path / "demo")
    layout.ensure_dirs()
    script = DubbingScript(
        chapter_number=1,
        soundscapes=[SoundscapeCue(name="密林虫鸣", asset_hint="forest_night")],
    )

    report = resolve_sound_cues(layout=layout, script=script)

    assert report.matched_count == 0
    assert report.unresolved_count == 1
    assert report.resolutions[0].status == "missing"


def test_distinct_bgm_cues_do_not_repeat_one_asset_within_a_chapter(tmp_path) -> None:
    layout = ProjectLayout(tmp_path / "demo")
    layout.ensure_dirs()
    for name in ("shared.mp3", "memory.mp3"):
        (layout.tts_sound_assets_dir / name).write_bytes(b"audio")
    library = SoundLibraryManifest(
        assets=[
            SoundAsset(
                asset_id="shared-score",
                kind="bgm",
                display_name="悬疑与回忆共用配乐",
                relative_path="shared.mp3",
                tags=["悬疑底乐", "紧张", "温柔回忆"],
            ),
            SoundAsset(
                asset_id="memory-score",
                kind="bgm",
                display_name="温柔回忆",
                relative_path="memory.mp3",
                tags=["温柔回忆", "怀旧"],
            ),
        ]
    )
    script = DubbingScript(
        chapter_number=1,
        bgm_suggestions=[
            BGMTiming(track_name="悬疑底乐", mood="紧张"),
            BGMTiming(track_name="温柔回忆", mood="怀旧"),
        ],
    )

    report = resolve_sound_cues(layout=layout, script=script, library=library)

    assert [item.asset_id for item in report.resolutions] == [
        "shared-score",
        "memory-score",
    ]


def test_pending_generated_asset_is_visible_but_not_mixed(tmp_path) -> None:
    layout = ProjectLayout(tmp_path / "demo")
    layout.ensure_dirs()
    (layout.tts_sound_assets_dir / "rain.wav").write_bytes(b"audio")
    script = DubbingScript(
        chapter_number=1,
        soundscapes=[SoundscapeCue(name="夜雨", description="细密雨声")],
    )
    library = SoundLibraryManifest(
        assets=[
            SoundAsset(
                asset_id="generated_rain",
                kind="soundscape",
                display_name="夜雨",
                relative_path="rain.wav",
                tags=["夜雨", "细密雨声"],
                source="generated",
                approval_status="pending",
            )
        ]
    )

    report = resolve_sound_cues(layout=layout, script=script, library=library)

    assert report.unresolved_count == 1
    assert "待作者审核" in report.resolutions[0].reason


def test_approved_generated_asset_becomes_available_to_the_mixer(tmp_path) -> None:
    layout = ProjectLayout(tmp_path / "demo")
    layout.ensure_dirs()
    (layout.tts_sound_assets_dir / "wind.wav").write_bytes(b"audio")
    library = SoundLibraryManifest(
        assets=[
            SoundAsset(
                asset_id="generated_wind",
                kind="soundscape",
                display_name="山风",
                relative_path="wind.wav",
                tags=["山风"],
                source="generated",
                approval_status="pending",
            )
        ]
    )
    save_sound_library(layout, library)

    asset = set_sound_asset_approval(layout, "generated_wind", status="approved")
    report = resolve_sound_cues(
        layout=layout,
        script=DubbingScript(
            chapter_number=1,
            soundscapes=[SoundscapeCue(name="山风")],
        ),
    )

    assert asset.approval_status == "approved"
    assert report.matched_count == 1


def test_application_assets_are_reused_without_copying_weights_into_a_project(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(
        sound_library,
        "application_sound_library_root",
        lambda: tmp_path / "application-assets" / "audio",
    )
    layout = ProjectLayout(tmp_path / "project")
    layout.ensure_dirs()
    app_assets = application_sound_assets_dir()
    (app_assets / "soundscape").mkdir(parents=True)
    (app_assets / "soundscape" / "rain.wav").write_bytes(b"audio")
    application_library = SoundLibraryManifest(
        assets=[
            SoundAsset(
                asset_id="app-soundscape-rain",
                kind="soundscape",
                display_name="夜雨",
                relative_path="soundscape/rain.wav",
                tags=["夜雨", "雨声"],
                scope="application",
            )
        ]
    )
    save_application_sound_library(application_library)

    report = resolve_sound_cues(
        layout=layout,
        script=DubbingScript(
            chapter_number=1,
            soundscapes=[SoundscapeCue(name="夜雨")],
        ),
    )
    paths = resolved_asset_paths(layout, report)

    assert report.resolutions[0].asset_scope == "application"
    assert paths[("soundscape", 0)] == app_assets / "soundscape" / "rain.wav"
    assert not (layout.tts_sound_assets_dir / "soundscape" / "rain.wav").exists()


def test_sound_asset_license_provenance_fields_round_trip(tmp_path) -> None:
    """generation_repository_id + aigc_watermark persist into sound_library.json."""
    layout = ProjectLayout(tmp_path / "prov")
    layout.ensure_dirs()
    asset = SoundAsset(
        asset_id="bgm_tense_001",
        kind="bgm",
        display_name="紧张暗流",
        relative_path="generated/bgm/abc123.mp3",
        source="generated",
        generation_provider="minimax_music",
        generation_model="music-2.6",
        generation_repository_id="MiniMax/Music-2.6",
        aigc_watermark=True,
    )
    save_sound_library(layout, SoundLibraryManifest(assets=[asset]))
    reloaded = sound_library.load_sound_library(layout)
    assert reloaded.assets[0].generation_repository_id == "MiniMax/Music-2.6"
    assert reloaded.assets[0].aigc_watermark is True
    assert reloaded.assets[0].commercial_use_status == "review_required"


def test_sound_asset_legacy_payload_without_provenance_fields_loads(tmp_path) -> None:
    """Pre-existing sound_library.json (no new fields) must still hydrate (extra=forbid)."""
    import json

    layout = ProjectLayout(tmp_path / "legacy")
    layout.ensure_dirs()
    # Hand-written legacy payload exactly as written before this change:
    # no generation_repository_id / aigc_watermark keys.
    legacy_payload = {
        "schema_version": "2.0",
        "created_at": "2026-01-01T00:00:00Z",
        "assets": [
            {
                "schema_version": "2.0",
                "created_at": "2026-01-01T00:00:00Z",
                "asset_id": "legacy_bgm",
                "kind": "bgm",
                "display_name": "遗留 BGM",
                "relative_path": "legacy/bgm.mp3",
            }
        ],
    }
    layout.tts_sound_library_path.write_text(json.dumps(legacy_payload, ensure_ascii=False))
    reloaded = sound_library.load_sound_library(layout)
    # New fields default cleanly on legacy payloads.
    assert reloaded.assets[0].generation_repository_id == ""
    assert reloaded.assets[0].aigc_watermark is False
