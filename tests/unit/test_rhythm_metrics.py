"""Tests for chapter rhythm signatures and rhythm-curve quality checks."""

from __future__ import annotations

from novel_forge.core.schemas.outline import ChapterRhythmPoint
from novel_forge.core.utils.rhythm_metrics import (
    compute_chapter_rhythm_signature,
    compute_structure_similarity,
)
from novel_forge.pipeline.quality_gate import QualityGate, QualityVerdict


def test_chapter_rhythm_signature_and_similarity() -> None:
    text = "她停下。风穿过门缝，灯影晃了一下。\n\n他没有回头。雨声压住脚步。"

    sig = compute_chapter_rhythm_signature(text)
    same = compute_chapter_rhythm_signature(text)

    assert sig.sentence_count == 4
    assert sig.paragraph_count == 2
    assert compute_structure_similarity(sig, same) == 1.0


def test_chapter_rhythm_point_coerces_legacy_labels() -> None:
    point = ChapterRhythmPoint(
        chapter_number=7,
        target_pacing="intense",
        target_tension="high",
        beat_pattern=["舒缓", "窒息", "释放"],
    )

    assert point.target_pacing == 5
    assert point.target_tension == 5
    assert point.beat_pattern == "舒缓；窒息；释放"


def test_quality_gate_rhythm_curve_warns_without_hard_fail() -> None:
    slow_text = "她想了很久很久，直到窗外的光一点点沉下去，旧日的回声仍在屋梁之间缓慢回旋。"
    actual = compute_chapter_rhythm_signature(slow_text)
    target = {
        "chapter_number": 3,
        "target_pacing": 5,
        "target_tension": 4,
        "beat_pattern": "追问-压迫-悬停",
    }

    gate = QualityGate()
    result = gate.check_rhythm_curve(actual, target)
    report = gate.report()

    assert result.dimension == "rhythm_curve"
    assert not result.passed
    assert result.details["hard_fail"] is False
    assert report.verdict == QualityVerdict.WARN
