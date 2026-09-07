"""Tests for configurable motif repetition windows."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from novel_forge.memory.base import MotifOccurrence
from novel_forge.memory.motif import Motif, MotifTracker


def _make_tracker() -> MotifTracker:
    tracker = MotifTracker(router=AsyncMock(), builder=AsyncMock())
    tracker._motifs["m-rain"] = Motif(
        motif_id="m-rain",
        name="雨声",
        category="意象",
        first_appearance_chapter=1,
        last_appearance_chapter=1,
        occurrence_count=1,
        is_intentional=False,
    )
    return tracker


async def test_repetition_lookback_is_chapter_window() -> None:
    tracker = _make_tracker()
    tracker._recent_usage["m-rain"] = [1]
    tracker._llm_extract_motifs = AsyncMock(  # type: ignore[method-assign]
        return_value=[
            MotifOccurrence(
                motif_id="m-rain",
                chapter_number=20,
                text_snippet="雨声贴着窗沿滑下来",
            )
        ]
    )

    warnings = await tracker.check_unintentional_repetition(
        chapter_number=20,
        chapter_text="雨声贴着窗沿滑下来",
        lookback_chapters=5,
        repetition_gap_chapters=25,
    )

    assert warnings == []


async def test_repetition_gap_is_configurable() -> None:
    tracker = _make_tracker()
    tracker._recent_usage["m-rain"] = [10]
    tracker._llm_extract_motifs = AsyncMock(  # type: ignore[method-assign]
        return_value=[
            MotifOccurrence(
                motif_id="m-rain",
                chapter_number=20,
                text_snippet="雨声贴着窗沿滑下来",
            )
        ]
    )

    default_gap = await tracker.check_unintentional_repetition(
        chapter_number=20,
        chapter_text="雨声贴着窗沿滑下来",
        lookback_chapters=15,
    )
    wider_gap = await tracker.check_unintentional_repetition(
        chapter_number=20,
        chapter_text="雨声贴着窗沿滑下来",
        lookback_chapters=15,
        repetition_gap_chapters=11,
    )

    assert default_gap == []
    assert [warning.motif_name for warning in wider_gap] == ["雨声"]


@pytest.mark.parametrize("history", [[9], [7, 8, 9], [5, 6, 7, 8, 9]])
@pytest.mark.parametrize("intentional", [False, True])
async def test_frequency_and_model_intent_cannot_hide_repetition(history, intentional) -> None:
    tracker = _make_tracker()
    tracker._motifs["m-rain"].is_intentional = intentional
    tracker._motifs["m-rain"].occurrence_count = len(history)
    tracker._recent_usage["m-rain"] = history
    tracker._llm_extract_motifs = AsyncMock(
        return_value=[
            MotifOccurrence(
                motif_id="m-rain",
                chapter_number=10,
                text_snippet="雨声贴着窗沿滑下来",
            )
        ]
    )
    warnings = await tracker.check_unintentional_repetition(10, "雨声贴着窗沿滑下来")
    assert len(warnings) == 1


@pytest.mark.parametrize(
    "relation, evidence, expected",
    [
        ("new_function", "雨声掩住脚步，他逃出了门", 0),
        ("new_function", "不存在的证据", 1),
        ("redundant", "雨声掩住脚步，他逃出了门", 1),
    ],
)
async def test_functional_change_requires_current_prose_evidence(relation, evidence, expected):
    tracker = _make_tracker()
    tracker._recent_usage["m-rain"] = [7, 8, 9]
    tracker._llm_extract_motifs = AsyncMock(
        return_value=[
            MotifOccurrence(
                motif_id="m-rain",
                chapter_number=10,
                narrative_function="掩护逃跑",
                function_relation=relation,
                function_evidence=evidence,
            )
        ]
    )
    warnings = await tracker.check_unintentional_repetition(10, "雨声掩住脚步，他逃出了门")
    assert len(warnings) == expected


async def test_user_requested_refrain_is_protected():
    tracker = _make_tracker()
    tracker._motifs["m-rain"].metadata["repetition_source"] = "user_explicit"
    tracker._recent_usage["m-rain"] = [9]
    tracker._llm_extract_motifs = AsyncMock(
        return_value=[
            MotifOccurrence(
                motif_id="m-rain",
                chapter_number=10,
                text_snippet="雨声又起",
            )
        ]
    )
    assert await tracker.check_unintentional_repetition(10, "雨声又起") == []


def test_incidental_motif_without_current_need_is_not_recommended():
    tracker = _make_tracker()
    assert tracker.get_suggestions_for_chapter(20) == []
