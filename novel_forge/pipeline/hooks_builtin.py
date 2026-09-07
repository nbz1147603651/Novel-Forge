"""Built-in pipeline hooks (Phase 1.2).

Provides ready-to-use before/after hooks for common cross-cutting concerns:

- TokenBudgetHook (before): validates input token count against model limits.
- PromptLeakDetectorHook (after): detects prompt leakage in LLM output.
- FormatSanitizeHook (after): cleans markdown fences and excess whitespace.

All hooks follow the quality-preservation constraint: they execute BEFORE
downstream quality checks, so any modification is caught by alignment/
continuity/causal evaluation.
"""

from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)


# ── TokenBudgetHook (before) ──────────────────────────────────────────────────


class TokenBudgetHook:
    """Before-hook: validate that input context does not exceed model limits.

    This hook estimates the token count of the input context and logs a
    warning if it approaches the model's context window. It does NOT block
    execution (returns input unchanged) — it's a diagnostic/observability hook.

    To actually block execution on overflow, set ``strict=True``.
    """

    def __init__(
        self,
        *,
        warn_ratio: float = 0.85,
        strict: bool = False,
        default_context_window: int = 128_000,
    ) -> None:
        self._warn_ratio = warn_ratio
        self._strict = strict
        self._default_context_window = default_context_window

    @property
    def hook_name(self) -> str:
        return "token_budget"

    async def __call__(self, step_name: str, input_data: Any) -> Any | None:
        """Estimate tokens and warn/block if over budget."""
        # Only applies to dict-like inputs with a 'context' or 'messages' key
        context: dict[str, Any] | None = None
        if isinstance(input_data, dict):
            context = input_data
        elif hasattr(input_data, "__dict__"):
            context = getattr(input_data, "context", None) or getattr(
                input_data, "prompt_context", None
            )

        if context is None or not isinstance(context, dict):
            return input_data

        # Rough token estimation: ~4 chars per token for mixed CJK/English
        total_chars = sum(
            len(str(v)) for v in context.values() if isinstance(v, (str, int, float))
        )
        estimated_tokens = total_chars // 3  # CJK-heavy: ~3 chars/token

        if estimated_tokens > int(self._default_context_window * self._warn_ratio):
            logger.warning(
                "TokenBudgetHook: step '%s' input ~%d tokens exceeds %.0f%% of %d window",
                step_name,
                estimated_tokens,
                self._warn_ratio * 100,
                self._default_context_window,
            )
            if self._strict:
                return None  # Block execution

        return input_data


# ── PromptLeakDetectorHook (after) ────────────────────────────────────────────

# Patterns that indicate prompt leakage in LLM output
_LEAK_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"```\s*system\s*\n", re.IGNORECASE),
    re.compile(r"\[SYSTEM PROMPT\]", re.IGNORECASE),
    re.compile(r"<\|system\|>", re.IGNORECASE),
    re.compile(r"你是一个.*?助手.*?你的任务是", re.DOTALL),
    re.compile(r"IGNORE PREVIOUS INSTRUCTIONS", re.IGNORECASE),
    re.compile(r"## System Prompt", re.IGNORECASE),
]


class PromptLeakDetectorHook:
    """After-hook: detect prompt leakage in LLM text output.

    Scans string outputs for common prompt-leak patterns. If detected,
    logs a warning and optionally strips the leaked content.

    This is a non-destructive diagnostic by default (``strip=False``).
    Set ``strip=True`` to remove detected leak patterns from output.
    """

    def __init__(self, *, strip: bool = False) -> None:
        self._strip = strip

    @property
    def hook_name(self) -> str:
        return "prompt_leak_detector"

    async def __call__(self, step_name: str, input_data: Any, output: Any) -> Any:
        """Check output for prompt leakage patterns."""
        text = self._extract_text(output)
        if not text:
            return output

        for pattern in _LEAK_PATTERNS:
            match = pattern.search(text)
            if match:
                logger.warning(
                    "PromptLeakDetectorHook: potential prompt leak in step '%s' "
                    "at position %d: '%s...'",
                    step_name,
                    match.start(),
                    match.group()[:50],
                )
                if self._strip:
                    text = pattern.sub("", text)
                    return self._replace_text(output, text)
                break  # Report first match only

        return output

    @staticmethod
    def _extract_text(output: Any) -> str:
        """Extract text content from various output types."""
        if isinstance(output, str):
            return output
        if isinstance(output, dict):
            # Check common text fields
            for key in ("text", "content", "chapter_text", "revised_text"):
                if key in output and isinstance(output[key], str):
                    return output[key]
        if hasattr(output, "text") and isinstance(output.text, str):
            return output.text
        return ""

    @staticmethod
    def _replace_text(output: Any, new_text: str) -> Any:
        """Replace text content in output."""
        if isinstance(output, str):
            return new_text
        if isinstance(output, dict):
            for key in ("text", "content", "chapter_text", "revised_text"):
                if key in output and isinstance(output[key], str):
                    output[key] = new_text
                    return output
        return output


# ── FormatSanitizeHook (after) ────────────────────────────────────────────────

# Markdown fence pattern (```json ... ``` or ``` ... ```)
_FENCE_PATTERN = re.compile(r"^```(?:json|text|markdown)?\s*\n?|\n?```\s*$", re.MULTILINE)
# Excess blank lines (3+ consecutive newlines → 2)
_EXCESS_BLANKS = re.compile(r"\n{4,}")
# Leading/trailing whitespace per line (preserve indentation for dialogue)
_TRAILING_WS = re.compile(r"[ \t]+$", re.MULTILINE)


class FormatSanitizeHook:
    """After-hook: clean common LLM output formatting artifacts.

    Handles:
    - Markdown code fences wrapping JSON/text output
    - Excess blank lines (4+ → 2)
    - Trailing whitespace

    Only applies to string outputs or dict outputs with text fields.
    Does NOT modify semantic content — purely mechanical cleanup.
    """

    def __init__(
        self,
        *,
        strip_fences: bool = True,
        collapse_blanks: bool = True,
        strip_trailing_ws: bool = True,
    ) -> None:
        self._strip_fences = strip_fences
        self._collapse_blanks = collapse_blanks
        self._strip_trailing_ws = strip_trailing_ws

    @property
    def hook_name(self) -> str:
        return "format_sanitize"

    async def __call__(self, step_name: str, input_data: Any, output: Any) -> Any:
        """Sanitize formatting artifacts in output text."""
        if isinstance(output, str):
            return self._sanitize(output)
        if isinstance(output, dict):
            modified = False
            for key in ("text", "content", "chapter_text", "revised_text"):
                if key in output and isinstance(output[key], str):
                    cleaned = self._sanitize(output[key])
                    if cleaned != output[key]:
                        output[key] = cleaned
                        modified = True
            if modified:
                return output
        return output

    def _sanitize(self, text: str) -> str:
        """Apply all enabled sanitization passes."""
        result = text
        if self._strip_fences:
            # Only strip if the entire output is wrapped in fences
            stripped = result.strip()
            if stripped.startswith("```") and stripped.endswith("```"):
                result = _FENCE_PATTERN.sub("", result).strip()
        if self._collapse_blanks:
            result = _EXCESS_BLANKS.sub("\n\n\n", result)
        if self._strip_trailing_ws:
            result = _TRAILING_WS.sub("", result)
        return result
