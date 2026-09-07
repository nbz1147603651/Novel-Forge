"""Tests for long-chapter multi-chunk splitting in book_consistency_step."""

from __future__ import annotations

import json
import re
from typing import Any

from novel_forge.pipeline.steps.book_consistency_step import (
    BookConsistencyInput,
    BookConsistencyStep,
)
from novel_forge.prompts.builder import PromptBuilder
from tests.helpers.book_audit_payloads import canonical_book_issue


class _CapturingRouter:
    """Router that captures chapter text lengths and metadata from prompts."""

    def __init__(self, max_prompt_chars: int) -> None:
        self.max_prompt_chars = max_prompt_chars
        self.chapter_texts_seen: list[dict[str, Any]] = []

    def resolve_model_id_for_task(self, task_type, *, provider=None, model_id=None):
        return model_id or "mock-test"

    async def route(self, request):
        user_prompt = request.messages[-1]["content"]
        # Extract chapter text metadata from the prompt context.
        # The prompt contains chapter_texts as JSON-like structures.
        self.chapter_texts_seen.append(
            {
                "prompt_length": len(user_prompt),
                "prompt": user_prompt,
            }
        )
        if len(user_prompt) > self.max_prompt_chars:
            raise RuntimeError("context window exceeds limit")
        return type(
            "ModelResponse",
            (),
            {
                "content": json.dumps(
                    {
                        "issues": [],
                        "repair_plan": [],
                        "summary": "未发现问题",
                        "consistency_score": 9.5,
                    },
                    ensure_ascii=False,
                ),
                "model_id": "fake-model",
            },
        )()


def test_summary_batching_preserves_every_summary_and_key_event(runtime_settings: Any) -> None:
    step = BookConsistencyStep(
        _CapturingRouter(max_prompt_chars=100_000),  # type: ignore[arg-type]
        PromptBuilder(),
        settings=runtime_settings,
    )
    long_summary = "完整摘要" * 180
    summaries = [
        {
            "chapter_number": index,
            "summary": f"{long_summary}-{index}",
            "key_events": [f"事件{index}-{event}" for event in range(16)],
        }
        for index in range(1, 11)
    ]
    compacted = step._compact_chapter_summaries(summaries)
    step._rendered_prompt_len = lambda _ctx: 0  # type: ignore[method-assign]

    chunks = step._summary_context_chunks(
        {"chapter_summaries": compacted},
        max_chapters_per_batch=3,
    )
    restored = [item for chunk in chunks for item in chunk["chapter_summaries"]]

    assert restored == summaries
    assert len(chunks) == 4


async def test_long_chapter_chunking(runtime_settings: Any) -> None:
    """A 100,000 char chapter should be split into 2-3 sub-chunks, each <=50,000 chars."""
    runtime_settings.long_book_audit_prompt_char_budget = 48_000

    # Create a 100,000 character chapter.
    long_text = "A" * 100_000
    chapter_texts = [
        {
            "chapter_number": 5,
            "numbered_text": long_text,
            "paragraph_count": 1,
            "paragraphs": [long_text],
            "source_chars": len(long_text),
            "truncated": False,
        }
    ]

    router = _CapturingRouter(max_prompt_chars=52_000)
    step = BookConsistencyStep(
        router,  # type: ignore[arg-type]
        PromptBuilder(),
        settings=runtime_settings,
    )

    _ = await step.run(
        BookConsistencyInput(
            chapter_summaries=[{"chapter_number": 5, "summary": "长章节测试", "key_events": []}],
            canon_state_snapshot={},
            character_bible={},
            outline={},
            chapter_texts=chapter_texts,
            analysis_mode="full_text",
            max_tokens=2048,
            temperature=0.2,
        )
    )

    # Verify the chapter was split into multiple chunks.
    assert len(router.chapter_texts_seen) >= 2, (
        f"Expected at least 2 LLM calls for 100k char chapter, got {len(router.chapter_texts_seen)}"
    )
    assert len(router.chapter_texts_seen) <= 3, (
        f"Expected at most 3 LLM calls for 100k char chapter, got {len(router.chapter_texts_seen)}"
    )

    # Verify each prompt stays within budget.
    for seen in router.chapter_texts_seen:
        assert seen["prompt_length"] <= 52_000, (
            f"Prompt length {seen['prompt_length']} exceeds budget"
        )


async def test_long_chapter_no_content_loss(runtime_settings: Any) -> None:
    """All sub-chunks together should cover the entire original chapter text."""
    runtime_settings.long_book_audit_prompt_char_budget = 48_000

    long_text = "B" * 100_000
    chapter_texts = [
        {
            "chapter_number": 3,
            "numbered_text": long_text,
            "paragraph_count": 1,
            "paragraphs": [long_text],
            "source_chars": len(long_text),
            "truncated": False,
        }
    ]

    router = _CapturingRouter(max_prompt_chars=52_000)
    step = BookConsistencyStep(
        router,  # type: ignore[arg-type]
        PromptBuilder(),
        settings=runtime_settings,
    )

    await step.run(
        BookConsistencyInput(
            chapter_summaries=[{"chapter_number": 3, "summary": "测试", "key_events": []}],
            canon_state_snapshot={},
            character_bible={},
            outline={},
            chapter_texts=chapter_texts,
            analysis_mode="full_text",
            max_tokens=2048,
            temperature=0.2,
        )
    )

    # Extract all chapter texts from the prompts and verify total coverage.
    total_chars_audited = 0
    for seen in router.chapter_texts_seen:
        prompt = seen["prompt"]
        # The prompt contains the chapter text; find the longest continuous
        # run of 'B' characters as a proxy for the chapter text length.
        b_runs = re.findall(r"B+", prompt)
        if b_runs:
            total_chars_audited += max(len(run) for run in b_runs)

    # All 100,000 chars should be covered across sub-chunks.
    assert total_chars_audited >= 100_000, (
        f"Content loss detected: only {total_chars_audited}/100,000 chars audited"
    )


async def test_long_chapter_part_metadata(runtime_settings: Any) -> None:
    """Split chapters should include part_number and total_parts metadata."""
    runtime_settings.long_book_audit_prompt_char_budget = 48_000

    long_text = "C" * 100_000
    chapter_texts = [
        {
            "chapter_number": 7,
            "numbered_text": long_text,
            "paragraph_count": 1,
            "paragraphs": [long_text],
            "source_chars": len(long_text),
            "truncated": False,
        }
    ]

    router = _CapturingRouter(max_prompt_chars=52_000)
    step = BookConsistencyStep(
        router,  # type: ignore[arg-type]
        PromptBuilder(),
        settings=runtime_settings,
    )

    await step.run(
        BookConsistencyInput(
            chapter_summaries=[{"chapter_number": 7, "summary": "测试", "key_events": []}],
            canon_state_snapshot={},
            character_bible={},
            outline={},
            chapter_texts=chapter_texts,
            analysis_mode="full_text",
            max_tokens=2048,
            temperature=0.2,
        )
    )

    # Verify the audit_chunk_notice includes part position info.
    notices_with_parts = [
        seen
        for seen in router.chapter_texts_seen
        if "子块" in seen["prompt"] or "part" in seen["prompt"].lower()
    ]
    # At least some prompts should indicate part splitting.
    assert len(notices_with_parts) >= 1, "Expected audit_chunk_notice to include part position info"


class _IssueReturningRouter:
    """Router that returns configurable issues per call and can simulate context errors."""

    def __init__(
        self,
        issues_per_call: list[list[dict[str, Any]]] | None = None,
        max_prompt_chars: int = 200_000,
        fail_on_first_call: bool = False,
    ) -> None:
        self.calls: list[list[int]] = []
        self.successful_prompts: list[str] = []
        self.issues_per_call = issues_per_call or []
        self.max_prompt_chars = max_prompt_chars
        self._call_index = 0
        self.fail_on_first_call = fail_on_first_call

    def resolve_model_id_for_task(self, task_type, *, provider=None, model_id=None):
        return model_id or "mock-test"

    async def route(self, request):
        user_prompt = request.messages[-1]["content"]

        chapter_numbers: list[int] = []
        for line in user_prompt.split("\n"):
            stripped = line.strip()
            if stripped.startswith("### 第 ") and " 章" in stripped:
                try:
                    num = int(stripped.split("第 ")[1].split(" 章")[0])
                    chapter_numbers.append(num)
                except (ValueError, IndexError):
                    pass
        self.calls.append(chapter_numbers)

        if self.fail_on_first_call and self._call_index == 0:
            self._call_index += 1
            raise RuntimeError("context_length_exceeded")

        self.successful_prompts.append(user_prompt)

        issues = []
        if self.issues_per_call and self._call_index < len(self.issues_per_call):
            issues = self.issues_per_call[self._call_index]
        self._call_index += 1

        return type(
            "ModelResponse",
            (),
            {
                "content": json.dumps(
                    {
                        "issues": issues,
                        "repair_plan": [],
                        "summary": f"审计块 {self._call_index} 完成",
                        "consistency_score": 8.0 if issues else 9.5,
                    },
                    ensure_ascii=False,
                ),
                "model_id": "fake-model",
            },
        )()


def _make_chapter_texts(count: int, text_template: str | None = None) -> list[dict[str, Any]]:
    result = []
    for i in range(1, count + 1):
        text = text_template.format(chapter_number=i) if text_template else f"[P1] 第 {i} 章正文。"
        result.append(
            {
                "chapter_number": i,
                "numbered_text": text,
                "paragraph_count": 1,
                "paragraphs": [text],
                "source_chars": len(text),
                "truncated": False,
            }
        )
    return result


async def test_single_chapter_context_retry_partitions_without_content_loss(
    runtime_settings: Any,
) -> None:
    runtime_settings.long_book_audit_prompt_char_budget = 200_000
    runtime_settings.long_book_audit_parallel_dimensions = False
    source_text = "D" * 12_000
    router = _IssueReturningRouter(fail_on_first_call=True)
    step = BookConsistencyStep(
        router,  # type: ignore[arg-type]
        PromptBuilder(),
        settings=runtime_settings,
    )

    await step.run(
        BookConsistencyInput(
            chapter_summaries=[{"chapter_number": 1, "summary": "递归分片测试", "key_events": []}],
            canon_state_snapshot={},
            character_bible={},
            outline={},
            chapter_texts=_make_chapter_texts(1, source_text),
            analysis_mode="full_text",
            max_chapters_per_batch=1,
            max_tokens=2048,
            temperature=0.2,
        )
    )

    audited_chars = 0
    for prompt in router.successful_prompts:
        runs = re.findall(r"D+", prompt)
        audited_chars += max((len(run) for run in runs), default=0)
    assert len(router.successful_prompts) >= 2
    assert audited_chars >= len(source_text)


async def test_cross_chapter_naming_boundary_detection(runtime_settings: Any) -> None:
    """20 chapters with a naming inconsistency spanning the chunk boundary."""
    runtime_settings.long_book_audit_prompt_char_budget = 200_000

    boundary_issue = [
        canonical_book_issue(
            "naming_boundary_01",
            category="naming",
            severity="warning",
            chapters_involved=[9, 11],
            primary_chapter=10,
            description="第9章称'林远'为'阿远'，第11章又称'林远'，名称不统一。",
            paragraph_index=1,
            paragraph_span=[1, 1],
        )
    ]

    router = _IssueReturningRouter(
        issues_per_call=[boundary_issue, []],
        max_prompt_chars=200_000,
    )
    step = BookConsistencyStep(
        router,  # type: ignore[arg-type]
        PromptBuilder(),
        settings=runtime_settings,
    )

    chapter_texts = _make_chapter_texts(20)

    result = await step.run(
        BookConsistencyInput(
            chapter_summaries=[
                {"chapter_number": i, "summary": f"第{i}章摘要", "key_events": []}
                for i in range(1, 21)
            ],
            canon_state_snapshot={},
            character_bible={},
            outline={},
            chapter_texts=chapter_texts,
            analysis_mode="full_text",
            max_chapters_per_batch=20,
            max_tokens=2048,
            temperature=0.2,
        )
    )

    assert any(issue.issue_id == "naming_boundary_01" for issue in result.issues), (
        "Boundary naming issue should be detected"
    )


async def test_overlap_dedup_no_duplicates(runtime_settings: Any) -> None:
    """Overlapping chapters should not produce duplicate issues."""
    runtime_settings.long_book_audit_prompt_char_budget = 200_000

    same_issue = [
        canonical_book_issue(
            "dup_test_01",
            category="timeline",
            severity="warning",
            chapters_involved=[4, 5],
            primary_chapter=5,
            description="时间线在第四到五章之间存在跳跃，缺少过渡说明。",
            paragraph_index=1,
            paragraph_span=[1, 1],
        )
    ]

    router = _IssueReturningRouter(
        issues_per_call=[same_issue, same_issue],
        max_prompt_chars=200_000,
    )
    step = BookConsistencyStep(
        router,  # type: ignore[arg-type]
        PromptBuilder(),
        settings=runtime_settings,
    )

    chapter_texts = _make_chapter_texts(10)

    result = await step.run(
        BookConsistencyInput(
            chapter_summaries=[
                {"chapter_number": i, "summary": f"第{i}章摘要", "key_events": []}
                for i in range(1, 11)
            ],
            canon_state_snapshot={},
            character_bible={},
            outline={},
            chapter_texts=chapter_texts,
            analysis_mode="full_text",
            max_chapters_per_batch=5,
            max_tokens=2048,
            temperature=0.2,
        )
    )

    dup_count = sum(1 for issue in result.issues if issue.issue_id == "dup_test_01")
    assert dup_count == 1, f"Expected exactly 1 deduplicated issue, got {dup_count}"
