"""Regression tests for script-fidelity and sound-degradation fixes.

Covers the 2026-07-31 production incident chain:
1. generate-script failure (``dubbing script does not preserve the chapter
   source text``) caused by normalize dropping punctuation-only separator
   segments while ``_normalized_source_text`` still counted them as source
   text;
2. rule-based fallback segments now carry sanitized ``spoken_text`` so the
   completeness gate can pass;
3. sound-generation failures degrade to approved same-kind library assets
   instead of blocking the whole chapter delivery;
4. audition lock contention returns a structured rejection (``error_code``)
   instead of a raw 500 that the UI misreported as an engine/runtime fault.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from novel_forge.api.routes import engine as engine_routes
from novel_forge.core.config import Settings
from novel_forge.persistence.models import ProjectLayout
from novel_forge.tts.assets.sound_library import resolve_sound_cues, save_sound_library
from novel_forge.tts.pipeline.generate_script_step import GenerateDubbingScriptStep
from novel_forge.tts.pipeline.script_completeness_gate import validate_script_completeness
from novel_forge.tts.schemas import (
    DubbingScript,
    DubbingSegment,
    SegmentType,
    SoundAsset,
    SoundLibraryManifest,
    VoiceTeamContract,
)
from novel_forge.tts.spoken_text_rewrite import sanitize_for_speech


def _bare_step() -> GenerateDubbingScriptStep:
    """Build a step instance without running the heavy ``__init__``."""
    step = GenerateDubbingScriptStep.__new__(GenerateDubbingScriptStep)
    step._scene_intents = []
    step._character_names = {}
    step._narrator_distance = ""
    step._chapter_number = 1
    return step


def test_normalized_source_text_strips_punctuation_separator_lines() -> None:
    step = _bare_step()
    src = "第一段正文。\n---\n第二段——继续。\n……\n第三段。"
    norm = step._normalized_source_text(src)
    assert "---" not in norm
    assert "……" not in norm
    assert "第一段正文" in norm
    assert "第二段——继续" in norm  # em-dash inside prose is preserved
    assert "第三段" in norm


def test_normalized_source_text_keeps_quoted_and_cjk_lines() -> None:
    step = _bare_step()
    # Quoted dialogue lines and CJK stage hints must survive normalization;
    # single-char lines stay untouched so character-level offset mapping works.
    norm = step._normalized_source_text("“……我睡着了？”\n（脚步声渐近）\n-")
    assert "我睡着了" in norm
    assert "脚步声渐近" in norm
    assert "-" in norm


def test_fidelity_passes_after_separator_drop_incident() -> None:
    """Reproduce the 2026-07-31 failure: normalize drops '---'/'……' segments,
    fidelity validation must still pass."""
    step = _bare_step()
    src = "第一段正文。\n---\n第二段——继续。\n……\n第三段。"
    script = DubbingScript(
        chapter_number=1,
        segments=[
            DubbingSegment(
                segment_index=0,
                segment_type=SegmentType.NARRATION,
                text="第一段正文。",
                source_paragraph=0,
            ),
            DubbingSegment(
                segment_index=1,
                segment_type=SegmentType.NARRATION,
                text="---",
                source_paragraph=1,
            ),
            DubbingSegment(
                segment_index=2,
                segment_type=SegmentType.NARRATION,
                text="第二段——继续。",
                source_paragraph=2,
            ),
            DubbingSegment(
                segment_index=3,
                segment_type=SegmentType.NARRATION,
                text="……",
                source_paragraph=3,
            ),
            DubbingSegment(
                segment_index=4,
                segment_type=SegmentType.NARRATION,
                text="第三段。",
                source_paragraph=4,
            ),
        ],
    )
    merged, _index_map, dropped = step._merge_narration_continuations(script)
    assert dropped == 2
    # This used to raise ValueError("dubbing script does not preserve the
    # chapter source text") because '---'/'……' were dropped from the script
    # while the expected source still counted them.
    step._validate_script_text_fidelity(merged, src)


def test_rule_fallback_segments_carry_sanitized_spoken_text() -> None:
    step = _bare_step()
    script = asyncio.run(
        step._generate_script_rule_based(
            "指尖拂过焦黑的痕迹——烟头烫的。\n“呃——！”",
            1,
            {},
        )
    )
    narration = next(
        segment
        for segment in script.segments
        if segment.segment_type == SegmentType.NARRATION
    )
    dialogue = next(
        segment
        for segment in script.segments
        if segment.segment_type == SegmentType.DIALOGUE
    )
    assert narration.spoken_text  # em-dash unfolded into a comma pause
    assert "——" not in narration.spoken_text
    assert dialogue.spoken_text  # fallback no longer leaves spoken_text empty
    # Pure punctuation paragraphs sanitize to empty (never read aloud).
    assert sanitize_for_speech("---") == ""


def test_rule_fallback_pipeline_passes_gate_on_separator_heavy_source() -> None:
    """End-to-end fallback chain on a source with separators, em-dashes and
    ellipses: rule generation -> normalize -> fidelity -> completeness gate."""
    step = _bare_step()
    src = (
        "梅雨季的潮气裹着老城的砖。\n"
        "---\n"
        "沈岸拂过焦黑的痕迹——烟头烫的，苏晚留下的。\n"
        "“试用期，三天。”他开口，“不准——”\n"
        "……\n"
        "他停住，倒计时还在继续。"
    )
    script = asyncio.run(step._generate_script_rule_based(src, 1, {}))
    assert any(segment.spoken_text for segment in script.segments)
    script, _index_map, dropped = step._merge_narration_continuations(script)
    assert dropped == 2  # '---' and '……' separators are dropped
    script, _folded = step._fold_single_char_dialogue(script)
    step._validate_script_source_fidelity(script, src, {})
    report = validate_script_completeness(
        script,
        VoiceTeamContract(),
        Settings(tts_script_gate_enabled=True),
    )
    assert report.spoken_text_coverage > 0


def test_sound_resolution_degrades_failed_cue_to_same_kind_asset(tmp_path) -> None:
    layout = ProjectLayout(tmp_path / "proj")
    layout.ensure_dirs()
    asset = SoundAsset(
        asset_id="lib-room-tone",
        kind="soundscape",
        display_name="房间底噪",
        tags=["环境", "底噪"],
        approval_status="approved",
        relative_path="room_tone.wav",
    )
    save_sound_library(layout, SoundLibraryManifest(assets=[asset]))
    (layout.tts_sound_assets_dir / "room_tone.wav").write_bytes(b"RIFFxxxxWAVE")
    script = DubbingScript(
        chapter_number=1,
        soundscapes=[{"name": "场景环境底床", "asset_hint": "场景环境底床"}],
    )
    plain = resolve_sound_cues(layout=layout, script=script)
    assert plain.unresolved_count == 1
    degraded = resolve_sound_cues(
        layout=layout,
        script=script,
        fallback_cue_keys={("soundscape", 0)},
    )
    assert degraded.unresolved_count == 0
    assert degraded.resolutions[0].status == "matched"
    assert "降级" in degraded.resolutions[0].reason


def test_preview_lock_timeout_returns_structured_rejection() -> None:
    """Audition lock contention must surface as rejected + error_code, not 500."""

    async def scenario() -> None:
        body = SimpleNamespace(
            project_id="demo",
            character_id="c1",
            sample_text="",
            provider="",
        )
        runtime = SimpleNamespace(settings=SimpleNamespace())
        storage = SimpleNamespace(
            existing_project_dir=lambda project_id: Path("/tmp/tts-demo")
        )
        with patch.object(
            engine_routes,
            "execute_preview_character_voice",
            new=AsyncMock(
                side_effect=TimeoutError(
                    "当前项目的自动配音仍在处理，试听等待超过 8 秒；请稍后重试。"
                )
            ),
        ):
            response = await engine_routes.engine_preview_character_voice(
                body=body,
                runtime=runtime,
                storage=storage,
            )
        assert response.status == "rejected"
        assert response.error_code == "tts_preview_lock_busy"
        assert "自动配音" in response.message

    asyncio.run(scenario())
