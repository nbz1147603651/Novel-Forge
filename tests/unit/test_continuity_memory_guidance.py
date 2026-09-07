"""Tests for memory-guided continuity repair helpers."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from novel_forge.pipeline.long.stages.continuity_repair import (
    _build_memory_guidance,
    _record_continuity_repair_memory_result,
)


class _FakeEpisodicMemory:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def search_similar_critiques(self, **kwargs):
        self.calls.append(kwargs)
        return [
            {
                "chapter_number": 3,
                "issue_type": "continuity_error",
                "summary": "铜印状态前后矛盾",
                "relevance_score": 0.91,
                "repair_attempts": [
                    {
                        "strategy": "patch",
                        "result": "regression",
                        "new_issues_introduced": ["location_jump"],
                    },
                    {
                        "strategy": "fulltext",
                        "result": "success",
                        "new_issues_introduced": [],
                    },
                ],
                "failure_pattern": "局部修补容易破坏位置承接",
                "lesson_learned": "先统一铜印归属，再补动作承接",
            }
        ]


@pytest.mark.asyncio
async def test_build_memory_guidance_extracts_actionable_history() -> None:
    memory = _FakeEpisodicMemory()
    issue = SimpleNamespace(issue_type="continuity_error", summary="铜印归属矛盾")

    guidance = await _build_memory_guidance(
        episodic_memory=memory,
        current_issues=[issue],
        current_chapter=8,
    )

    assert guidance is not None
    assert guidance["success_rate"] == 0.5
    assert guidance["total_attempts"] == 2
    assert guidance["matched_issues"][0]["chapter"] == 3
    assert "fulltext" in guidance["recommended_strategies"]
    assert "patch" in guidance["avoid_strategies"]
    assert memory.calls[0]["current_chapter"] == 8


def test_record_continuity_repair_memory_result_updates_matching_entries() -> None:
    recorded: list[dict[str, object]] = []
    episodic_memory = SimpleNamespace(
        _critique_index={
            "sig-1": SimpleNamespace(
                chapter_number=8,
                issue_type="continuity_error",
                summary="铜印归属矛盾",
            )
        },
        record_repair_result=lambda signature, **kwargs: recorded.append(
            {"signature": signature, **kwargs}
        )
        is None,
    )
    ledger = SimpleNamespace(
        has_regression=True,
        resolved=[],
        downgraded=[],
        new_high_critical=[SimpleNamespace(issue_type="location_jump")],
    )
    continuity_repair = SimpleNamespace(
        repaired_issue_types=["continuity_error"],
        patch_only=True,
    )

    count = _record_continuity_repair_memory_result(
        episodic_memory=episodic_memory,
        chapter_number=8,
        round_num=2,
        pre_issues=[
            SimpleNamespace(issue_type="continuity_error", summary="铜印归属矛盾")
        ],
        continuity_repair=continuity_repair,
        ledger=ledger,
        score_before=6.0,
        score_after=5.4,
    )

    assert count == 1
    assert recorded[0]["signature"] == "sig-1"
    assert recorded[0]["strategy"] == "patch"
    assert recorded[0]["result"] == "regression"
    assert recorded[0]["new_issues"] == ["location_jump"]
