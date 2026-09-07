"""ComplianceGuard — blocks copyright-infringing prompts and outputs."""

from __future__ import annotations

import re

from novel_forge.core.exceptions import ComplianceViolationError

# Patterns that indicate requests to copy / closely imitate copyrighted work
_FORBIDDEN_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"(复制|抄袭|照搬|原封不动).{0,10}(原文|原作|原著)", re.IGNORECASE),
    re.compile(
        r"模仿.{0,6}(风格|文风|笔法)"
        r".{0,10}(到|达到)"
        r".{0,6}(可识别|难以区分|一模一样)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(write\s+exactly\s+like|copy\s+the\s+style\s+of|replicate\s+the\s+prose\s+of)",
        re.IGNORECASE,
    ),
    re.compile(r"(plagiari[sz]e|verbatim\s+copy)", re.IGNORECASE),
]


class ComplianceGuard:
    """Checks prompts and outputs for copyright / compliance violations."""

    DEFAULT_SKIP_TASK_TYPES: frozenset[str] = frozenset({
        "book_consistency",
        "book_consistency_naming",
        "book_consistency_timeline",
        "book_consistency_world_rule",
        "book_consistency_character_state",
        "book_consistency_plot_thread",
        "book_consistency_narrative_drift",
        "book_consistency_verify",
        "knowledge_boundary_audit",
    })

    def __init__(
        self,
        *,
        extra_patterns: list[re.Pattern[str]] | None = None,
        enabled: bool = True,
        skip_task_types: set[str] | None = None,
    ) -> None:
        self._enabled = enabled
        self._patterns = list(_FORBIDDEN_PATTERNS)
        if extra_patterns:
            self._patterns.extend(extra_patterns)
        self._skip_task_types = (
            skip_task_types if skip_task_types is not None else set(self.DEFAULT_SKIP_TASK_TYPES)
        )

    def check_prompt(self, text: str, *, task_type: str | None = None) -> None:
        """Raise ComplianceViolationError if the prompt contains forbidden patterns."""
        if not self._enabled:
            return
        if task_type is not None and task_type in self._skip_task_types:
            return
        for pattern in self._patterns:
            match = pattern.search(text)
            if match:
                raise ComplianceViolationError(
                    f"Prompt contains forbidden pattern: '{match.group()}'"
                )

    def check_output(self, text: str, *, task_type: str | None = None) -> None:
        """Raise when model output contains obvious prompt/system leakage."""
        if not self._enabled:
            return
        from novel_forge.common.constants import TaskType
        from novel_forge.core.format_contracts import find_text_output_contract_violations

        resolved_task = TaskType.DRAFT_CHAPTER
        if task_type:
            for candidate in TaskType:
                if candidate.value == task_type:
                    resolved_task = candidate
                    break
        violations = find_text_output_contract_violations(resolved_task, text)
        if violations:
            raise ComplianceViolationError(
                "Output contains forbidden prompt/system artifacts: "
                + " | ".join(violations[:6])
            )
