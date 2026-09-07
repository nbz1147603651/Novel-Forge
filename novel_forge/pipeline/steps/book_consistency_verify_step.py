"""BookConsistencyVerifyStep — focused per-chapter issue verification."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from novel_forge.core.constants import TaskType
from novel_forge.core.schemas.book_consistency_verify import BookConsistencyVerifyResult
from novel_forge.pipeline.steps.base import PipelineStep


@dataclass
class BookConsistencyVerifyInput:
    """Input for verifying claimed whole-book audit issues in one chapter."""

    chapter_number: int
    numbered_text: str
    paragraph_count: int
    claimed_issues: list[dict[str, Any]] = field(default_factory=list)
    chapter_summary: str = ""
    related_chapters_context: list[dict[str, Any]] = field(default_factory=list)
    canon_characters: dict[str, Any] = field(default_factory=dict)
    max_tokens: int = 4096
    temperature: float = 0.2


class BookConsistencyVerifyStep(PipelineStep[BookConsistencyVerifyInput, BookConsistencyVerifyResult]):
    """Focused LLM step for validating and re-locating audit issues."""

    @property
    def step_name(self) -> str:
        return "book_consistency_verify"

    async def _execute(self, input_data: BookConsistencyVerifyInput) -> BookConsistencyVerifyResult:
        chapter_text = input_data.numbered_text or ""
        issue_count = len(input_data.claimed_issues) or 1
        max_cap = max(1024, int(input_data.max_tokens or 4096))

        context: dict[str, Any] = {
            "chapter_number": input_data.chapter_number,
            "numbered_text": input_data.numbered_text,
            "paragraph_count": input_data.paragraph_count,
            "chapter_summary": input_data.chapter_summary,
            "related_chapters_context": input_data.related_chapters_context,
            "canon_characters": input_data.canon_characters,
            "claimed_issues": input_data.claimed_issues,
        }

        raw_result: dict[str, Any] = await self._call_with_retry(
            TaskType.BOOK_CONSISTENCY_VERIFY,
            context,
            max_tokens=self._dynamic_max_tokens(
                TaskType.BOOK_CONSISTENCY_VERIFY,
                max(1800, len(chapter_text) // 3, issue_count * 350),
                prompt_overhead=2600,
                min_tokens=min(2048, max_cap),
                max_cap=max_cap,
            ),
            temperature=input_data.temperature,
            required_keys=("verified_issues",),
        )
        return BookConsistencyVerifyResult.model_validate(raw_result)
