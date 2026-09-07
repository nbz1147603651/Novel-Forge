"""Tests for alignment-repair subplot summary generation."""

from __future__ import annotations

from novel_forge.core.schemas.chapter import AlignmentReport
from novel_forge.pipeline.chapter_runner import ChapterRunner


def test_alignment_repair_summary_reports_preserved_and_converged_items() -> None:
    before = AlignmentReport(
        alignment_score=6.9,
        risk_level="medium",
        summary="before",
        missing_main_points=[],
        supportive_subplot_points=["塔玛拉鼓励主角继续训练"],
        weak_subplot_points=["支线A占比过高", "支线B偏离主目标"],
        repair_actions=[],
    )
    after = AlignmentReport(
        alignment_score=8.1,
        risk_level="low",
        summary="after",
        missing_main_points=[],
        supportive_subplot_points=["塔玛拉鼓励主角继续训练", "老陈桥段强化文化融合"],
        weak_subplot_points=["支线B偏离主目标"],
        repair_actions=[],
    )

    summary = ChapterRunner._build_alignment_repair_subplot_summary(before, after)

    assert summary["preserved_supportive_subplots"] == ["塔玛拉鼓励主角继续训练"]
    assert summary["newly_supported_subplots"] == ["老陈桥段强化文化融合"]
    assert summary["converged_disruptive_subplots"] == ["支线A占比过高"]
    assert summary["remaining_disruptive_subplots"] == ["支线B偏离主目标"]
    assert summary["newly_detected_disruptive_subplots"] == []


def test_alignment_repair_summary_accepts_dict_payloads() -> None:
    before = {
        "supportive_subplot_points": [],
        "weak_subplot_points": ["旧问题"],
    }
    after = {
        "supportive_subplot_points": ["新识别的赋能支线"],
        "weak_subplot_points": ["旧问题", "新问题"],
    }

    summary = ChapterRunner._build_alignment_repair_subplot_summary(before, after)

    assert summary["preserved_supportive_subplots"] == []
    assert summary["newly_supported_subplots"] == ["新识别的赋能支线"]
    assert summary["converged_disruptive_subplots"] == []
    assert summary["remaining_disruptive_subplots"] == ["旧问题"]
    assert summary["newly_detected_disruptive_subplots"] == ["新问题"]
