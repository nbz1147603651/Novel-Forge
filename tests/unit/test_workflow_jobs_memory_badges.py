"""Tests for workflow memory badge and diagnostics helpers."""

from __future__ import annotations

from novel_forge.desktop.jobs import DesktopJobEvent, DesktopJobRecord
from novel_forge.desktop.pages.workflow.jobs import (
    _memory_context_badge_spec,
    _memory_context_detail_line,
    _memory_stage_badge_spec,
    _stage_memory_context_payloads,
)


def test_memory_stage_badge_pending_defaults_to_muted() -> None:
    text, tone = _memory_stage_badge_spec("summary", "pending")
    assert text == "摘要待命"
    assert tone == "muted"


def test_memory_stage_badge_pending_uses_warning_during_parallel_phase() -> None:
    text, tone = _memory_stage_badge_spec("summary", "pending", parallel_running=True)
    assert text == "摘要待命"
    assert tone == "warning"


def test_memory_stage_badge_running_done_failed_keep_existing_tones() -> None:
    assert _memory_stage_badge_spec("indexing", "running") == ("索引中", "warning")
    assert _memory_stage_badge_spec("motif", "done") == ("母题完成", "success")
    assert _memory_stage_badge_spec("motif", "failed") == ("母题失败", "danger")


def test_stage_memory_context_payloads_keep_latest_per_stage() -> None:
    job = DesktopJobRecord(job_id="job-1", kind="run_chapter", label="章节续写")
    job.events = [
        DesktopJobEvent(
            at="2026-04-22T10:00:00+00:00",
            step="memory_planning_context",
            payload={
                "requested_layers": ["L0_identity", "L1_core_memory", "L2_on_demand"],
                "resolved_layers": ["L0_identity", "L1_core_memory"],
            },
        ),
        DesktopJobEvent(
            at="2026-04-22T10:01:00+00:00",
            step="memory_draft_context",
            payload={
                "requested_layers": ["L0_identity", "L1_core_memory", "L2_on_demand", "L3_deep_search"],
                "resolved_layers": ["L0_identity", "L1_core_memory", "L2_on_demand"],
                "counts": {"relevant_history": 2},
                "flags": {"has_critique_context": True},
            },
        ),
        DesktopJobEvent(
            at="2026-04-22T10:02:00+00:00",
            step="memory_planning_context",
            payload={
                "requested_layers": ["L0_identity", "L1_core_memory", "L2_on_demand"],
                "resolved_layers": ["L0_identity", "L1_core_memory", "L2_on_demand"],
            },
        ),
    ]

    payloads = _stage_memory_context_payloads(job)

    assert list(payloads.keys()) == ["planning", "draft"]
    assert payloads["planning"]["resolved_layers"] == [
        "L0_identity",
        "L1_core_memory",
        "L2_on_demand",
    ]
    assert payloads["draft"]["counts"]["relevant_history"] == 2


def test_memory_context_badge_and_detail_line_are_compact_and_stage_aware() -> None:
    payload = {
        "requested_layers": ["L1_core_memory", "L2_on_demand"],
        "resolved_layers": ["L1_core_memory", "L2_on_demand"],
        "counts": {
            "relevant_history": 1,
            "previous_chapter_events": 1,
            "motif_suggestions": 2,
        },
        "flags": {
            "has_outline_context": True,
            "has_critique_context": True,
        },
        "history_reused": True,
    }

    assert _memory_context_badge_spec("finalize", payload) == ("收束 2/2 层", "success")
    assert (
        _memory_context_detail_line("finalize", payload)
        == "收束：L1/L2 · 历史 1 · 上章 1 · 母题 2 · 大纲"
    )
