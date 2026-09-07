"""Tests for shared workspace result payload builders."""

from __future__ import annotations

from types import SimpleNamespace

from novel_forge.workspace.result_payloads import (
    build_run_chapter_result_payload,
    build_run_short_result_payload,
    normalize_chapter_session_payload,
)


def test_build_run_short_result_payload_includes_warnings() -> None:
    result = SimpleNamespace(
        final_text="正文" * 200,
        eval_report=SimpleNamespace(overall_score=8.6, passed=True),
        creative_summary=None,
        warnings=["短篇叙事蓝图信息不足，本次将按精简蓝图继续生成，结构稳定性可能下降。"],
    )

    payload = build_run_short_result_payload("short_demo", result)

    assert payload["project_id"] == "short_demo"
    assert payload["overall_score"] == 8.6
    assert payload["passed"] is True
    assert payload["warnings"] == result.warnings


def test_build_run_short_result_payload_uses_canonical_word_count() -> None:
    result = SimpleNamespace(
        final_text="你好，world 123！\n“世界”",
        eval_report=None,
        creative_summary=None,
        warnings=[],
    )

    payload = build_run_short_result_payload("short_demo", result)

    assert payload["word_count"] == 4


def test_build_run_chapter_result_payload_includes_causal_metrics_and_warnings() -> None:
    result = SimpleNamespace(
        meta=SimpleNamespace(chapter_number=2, word_count=1234),
        text="章节正文" * 200,
        eval_report=SimpleNamespace(overall_score=8.8),
        continuity_report=SimpleNamespace(
            continuity_score=7.9,
            issues=[SimpleNamespace(summary="线索顺序错位")],
        ),
        causal_report=SimpleNamespace(
            causal_score=9.1,
            issues=[
                SimpleNamespace(summary="开头承接不足"),
                SimpleNamespace(summary="结尾悬念提前泄露"),
            ],
        ),
        bridge=SimpleNamespace(bridge_summary="上一章冲突继续发酵"),
        chapter_exit_state=SimpleNamespace(must_carry_forward=["继续追查旧案"]),
        warnings=["因果链校验发现 2 个问题，其中高优先级 1 个，建议人工复核。"],
    )

    payload = build_run_chapter_result_payload("long_demo", result)

    assert payload["project_id"] == "long_demo"
    assert payload["chapter_number"] == 2
    assert payload["word_count"] == 800
    assert payload["overall_score"] == 8.8
    assert payload["continuity_score"] == 7.9
    assert payload["continuity_issue_count"] == 1
    assert payload["causal_score"] == 9.1
    assert payload["causal_issue_count"] == 2
    assert payload["bridge_summary"] == "上一章冲突继续发酵"
    assert payload["chapter_exit_summary"] == "继续追查旧案"
    assert payload["warnings"] == result.warnings


def test_build_run_chapter_result_payload_fallback_uses_canonical_word_count() -> None:
    result = SimpleNamespace(
        meta=SimpleNamespace(chapter_number=2, word_count=0),
        text="你好，world 123！\n“世界”",
        eval_report=None,
        continuity_report=None,
        causal_report=None,
        reading_power_report=None,
        bridge=None,
        chapter_exit_state=None,
        warnings=[],
    )

    payload = build_run_chapter_result_payload("long_demo", result)

    assert payload["word_count"] == 4


def test_normalize_chapter_session_payload_promotes_metadata_warning_fields() -> None:
    payload = normalize_chapter_session_payload(
        {
            "project_id": "long_demo",
            "chapter_number": 2,
            "status": "completed",
            "word_count": 1234,
            "overall_score": 8.7,
            "continuity_score": 7.8,
            "continuity_issue_count": 1,
            "bridge_summary": "桥接正常",
            "chapter_exit_summary": "继续追查旧案",
            "preview": "预览",
            "applied_option_id": "accept_and_finalize",
            "metadata": {
                "causal_score": 9.4,
                "causal_issue_count": 2,
                "warnings": ["因果链校验发现 2 个问题，其中高优先级 1 个，建议人工复核。"],
            },
        }
    )

    assert payload["causal_score"] == 9.4
    assert payload["causal_issue_count"] == 2
    assert payload["warnings"] == ["因果链校验发现 2 个问题，其中高优先级 1 个，建议人工复核。"]
