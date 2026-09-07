"""Tests for pipeline.progress.format_step_label (engine-facing step labels)."""

from __future__ import annotations

from novel_forge.pipeline.progress import format_step_label


def test_format_step_label_adjudication_batch_detail() -> None:
    label = format_step_label(
        "init_long",
        "adjudicate_init_conflict_candidates_73_80",
        {
            "stage": "contract_coherence",
            "batch": 7,
            "batch_total": 7,
            "issues": 0,
            "verdict": "accept",
            "max_parallel": 2,
        },
    )

    assert label == (
        "冲突候选裁判  ·  当前层：章节契约  ·  批次 7 / 7  ·  并发 2  ·  问题 0  ·  判定：通过"
    )


def test_format_step_label_claims_extraction_detail() -> None:
    label = format_step_label(
        "init_long",
        "extract_init_coherence_claims",
        {
            "artifact": "chapter_contracts",
            "claims": 80,
            "extraction_mode": "stream",
            "max_parallel": 2,
        },
    )

    assert label == "初始化一致性 Claims 抽取  ·  当前检查：章节契约  ·  本批抽取 80 条  ·  并发 2"


def test_format_step_label_candidate_retrieval_detail() -> None:
    label = format_step_label(
        "init_long",
        "retrieve_init_conflict_candidates",
        {
            "stage": "contract_coherence",
            "active_claims": 109,
            "extracted_claims": 120,
            "candidates": 75,
        },
    )

    assert label == (
        "初始化冲突候选检索  ·  当前层：章节契约  ·  活跃一致性 Claims 109 / 抽取 120  ·  候选 75"
    )


def test_format_step_label_recheck_chunk_detail() -> None:
    label = format_step_label(
        "init_long",
        "init_coherence_recheck_chunk_start",
        {
            "stage": "contract_coherence",
            "chunk_index": 6,
            "chunk_count": 12,
            "focus_chapters": [25],
        },
    )

    assert label == (
        "init_coherence_recheck_chunk_start  ·  分块 6 / 12  ·  当前层：章节契约  ·  聚焦第 25 章"
    )


def test_format_step_label_recheck_focus_chapter_range() -> None:
    label = format_step_label(
        "init_long",
        "init_coherence_recheck_chunk_start",
        {"chunk_index": 8, "chunk_count": 12, "focus_chapters": [33, 34, 35, 36]},
    )

    assert label == "init_coherence_recheck_chunk_start  ·  分块 8 / 12  ·  聚焦第 33、34、35、36 章"


def test_format_step_label_generic_step_falls_back_to_display_name() -> None:
    assert format_step_label("init_long", "plan_outline", {}) == "章节大纲"
    assert format_step_label("run_chapter", "draft", {}) == "DRAFT 草稿 · 草稿生成"


def test_format_step_label_empty_step_returns_empty() -> None:
    assert format_step_label("init_long", "", None) == ""
    assert format_step_label("init_long", "  ", {"x": 1}) == ""


def test_format_step_label_payload_ignores_non_dict() -> None:
    label = format_step_label(
        "init_long",
        "adjudicate_init_conflict_candidates_1_12",
        None,
    )

    assert label == "冲突候选裁判"


def test_format_step_label_verdict_labels() -> None:
    for verdict, expected in (
        ("defer", "延后"),
        ("ambiguous", "需确认"),
        ("needs_repair", "需修复"),
        ("reject", "未通过"),
    ):
        label = format_step_label(
            "init_long",
            "adjudicate_init_conflict_candidates_1_12",
            {"batch": 1, "batch_total": 3, "verdict": verdict},
        )
        assert label == f"冲突候选裁判  ·  批次 1 / 3  ·  判定：{expected}"
