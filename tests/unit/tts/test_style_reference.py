"""Reference dubbing-script style analysis and projection tests."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from novel_forge.core.config import Settings
from novel_forge.tts.schemas import DubbingSegment, SegmentType
from novel_forge.tts.script_stage_context import (
    ContextProjectionLimits,
    ScriptContextStage,
    ScriptStageContext,
)
from novel_forge.tts.style_reference import (
    analyze_dubbing_style_reference,
    normalize_reference_script,
)


def _reference_script() -> str:
    return "\n".join(
        [
            "WEBVTT",
            "00:00:01.000 --> 00:00:04.000",
            "【环境：夜雨，远处车流】",
            "旁白：雨落得很轻。灯影沿着玻璃慢慢滑下去……",
            "沈岸：你听见了吗？",
            "旁白：他没有立刻回答，只把呼吸压低。",
            "【BGM：低频弦乐缓慢进入】",
        ]
        * 12
    )


def test_normalize_reference_script_removes_subtitle_transport_noise() -> None:
    normalized = normalize_reference_script(_reference_script(), max_chars=20_000)

    assert "WEBVTT" not in normalized
    assert "-->" not in normalized
    assert "夜雨" in normalized


@pytest.mark.asyncio
async def test_deterministic_style_profile_never_persists_reference_text() -> None:
    settings = Settings(
        _env_file=None,
        tts_style_reference_llm_enabled=False,
        tts_style_reference_min_chars=80,
    )

    profile = await analyze_dubbing_style_reference(
        _reference_script(),
        source_name="参考稿.vtt",
        router=MagicMock(),
        builder=MagicMock(),
        settings=settings,
    )

    payload = profile.model_dump(mode="json")
    assert profile.analysis_mode == "deterministic"
    assert profile.source_name == "参考稿.vtt"
    assert len(profile.source_hash) == 64
    assert profile.sound_design_rules
    assert "reference_script" not in payload
    assert "雨落得很轻" not in str(payload)


@pytest.mark.asyncio
async def test_too_short_reference_is_rejected_before_llm_call() -> None:
    settings = Settings(
        _env_file=None,
        tts_style_reference_llm_enabled=False,
        tts_style_reference_min_chars=160,
    )

    with pytest.raises(ValueError, match="有效内容不足"):
        await analyze_dubbing_style_reference(
            "太短了。",
            source_name="short.txt",
            router=MagicMock(),
            builder=MagicMock(),
            settings=settings,
        )


@pytest.mark.asyncio
async def test_reference_style_projection_contains_rules_but_no_source_identity() -> None:
    settings = Settings(
        _env_file=None,
        tts_style_reference_llm_enabled=False,
        tts_style_reference_min_chars=80,
    )
    profile = await analyze_dubbing_style_reference(
        _reference_script(),
        source_name="商业样稿.md",
        router=MagicMock(),
        builder=MagicMock(),
        settings=settings,
    )
    context = ScriptStageContext(
        reference_style_profile=profile,
        reference_style_strength=0.7,
    )

    cards = context.project(
        ScriptContextStage.SPOKEN_REWRITE,
        [
            DubbingSegment(
                segment_index=0,
                segment_type=SegmentType.NARRATION,
                text="当前作品的权威原文。",
            )
        ],
        limits=ContextProjectionLimits(max_items=8, max_text_chars=240),
    )

    style = cards["reference_dubbing_style"]
    assert style["strength"] == 0.7
    assert style["rhythm_rules"]
    assert "source_name" not in style
    assert "source_hash" not in style
