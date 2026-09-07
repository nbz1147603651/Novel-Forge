"""Context transform layer (Pi-inspired context engineering).

Provides a composable transform pipeline that executes before each LLM call
to enforce token budgets while protecting critical context fields.

Design constraints (quality preservation):
- P0 protected fields are NEVER compressed or truncated.
- Transform only applies to historical messages, not current task instructions.
- Default OFF — must be explicitly enabled via configuration.
- Output must pass VERIFY_COMPRESSION validation.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

logger = logging.getLogger(__name__)


# ── Token Budget ──────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class TokenBudget:
    """Token budget specification for context transforms.

    Attributes:
        total_limit: Maximum total tokens allowed for the context.
        protected_tokens: Tokens reserved for P0 protected fields (never compressed).
        compressible_tokens: Tokens available for compressible content.
        reserve_output: Tokens reserved for model output (not part of context budget).
    """

    total_limit: int = 128_000
    protected_tokens: int = 64_000
    compressible_tokens: int = 48_000
    reserve_output: int = 16_000

    @property
    def effective_context_limit(self) -> int:
        """Available tokens for context (total - output reserve)."""
        return self.total_limit - self.reserve_output

    @property
    def compression_target(self) -> int:
        """Target tokens for compressible content after transform."""
        return max(0, self.effective_context_limit - self.protected_tokens)


# ── Transform Protocol ────────────────────────────────────────────────────────


@runtime_checkable
class ContextTransform(Protocol):
    """Protocol for a single context transform step."""

    @property
    def transform_name(self) -> str:
        """Human-readable transform identifier."""
        ...

    async def transform(
        self,
        messages: list[dict[str, Any]],
        budget: TokenBudget,
    ) -> list[dict[str, Any]]:
        """Transform messages to fit within budget.

        Args:
            messages: The current message list (may include protected fields).
            budget: Token budget specification.

        Returns:
            Transformed message list (protected fields unchanged).
        """
        ...


# ── Transform Pipeline ────────────────────────────────────────────────────────


class TransformPipeline:
    """Chains multiple ContextTransform steps in order.

    Usage::

        pipeline = TransformPipeline()
        pipeline.add(SlidingWindowTransform(max_history=10))
        pipeline.add(ToolOutputTruncateTransform(max_chars=2000))

        result = await pipeline.execute(messages, budget)
    """

    def __init__(self, *, enabled: bool = False) -> None:
        self._transforms: list[ContextTransform] = []
        self._enabled = enabled

    @property
    def enabled(self) -> bool:
        return self._enabled

    @enabled.setter
    def enabled(self, value: bool) -> None:
        self._enabled = value

    def add(self, transform: ContextTransform) -> None:
        """Append a transform to the pipeline."""
        self._transforms.append(transform)

    def remove(self, transform_name: str) -> bool:
        """Remove a transform by name."""
        before = len(self._transforms)
        self._transforms = [
            t for t in self._transforms if t.transform_name != transform_name
        ]
        return len(self._transforms) < before

    async def execute(
        self,
        messages: list[dict[str, Any]],
        budget: TokenBudget,
    ) -> list[dict[str, Any]]:
        """Execute all transforms in order.

        If disabled, returns messages unchanged (zero overhead).
        """
        if not self._enabled or not self._transforms:
            return messages

        current = messages
        for transform in self._transforms:
            try:
                current = await transform.transform(current, budget)
            except Exception:
                logger.warning(
                    "TransformPipeline: '%s' failed — skipping",
                    transform.transform_name,
                    exc_info=True,
                )
        return current

    @property
    def transform_count(self) -> int:
        return len(self._transforms)

    def list_transforms(self) -> list[str]:
        return [t.transform_name for t in self._transforms]


# ── Built-in Transforms ───────────────────────────────────────────────────────


class SlidingWindowTransform:
    """Keep only the most recent N messages (plus system/protected messages)."""

    def __init__(self, *, max_history: int = 20) -> None:
        self._max_history = max_history

    @property
    def transform_name(self) -> str:
        return "sliding_window"

    async def transform(
        self,
        messages: list[dict[str, Any]],
        budget: TokenBudget,
    ) -> list[dict[str, Any]]:
        if len(messages) <= self._max_history:
            return messages

        # Always keep system messages and messages marked as protected
        protected = [
            m for m in messages
            if m.get("role") == "system" or m.get("_protected", False)
        ]
        non_protected = [
            m for m in messages
            if m.get("role") != "system" and not m.get("_protected", False)
        ]

        # Keep the most recent N non-protected messages
        kept = non_protected[-self._max_history:]
        return protected + kept


class ToolOutputTruncateTransform:
    """Truncate overly long tool/assistant outputs to a character limit."""

    def __init__(self, *, max_chars: int = 4000, suffix: str = "\n...[truncated]") -> None:
        self._max_chars = max_chars
        self._suffix = suffix

    @property
    def transform_name(self) -> str:
        return "tool_output_truncate"

    async def transform(
        self,
        messages: list[dict[str, Any]],
        budget: TokenBudget,
    ) -> list[dict[str, Any]]:
        result = []
        for msg in messages:
            if msg.get("_protected", False):
                result.append(msg)
                continue
            content = msg.get("content", "")
            if isinstance(content, str) and len(content) > self._max_chars:
                truncated = dict(msg)
                truncated["content"] = content[: self._max_chars] + self._suffix
                truncated["_truncated"] = True
                result.append(truncated)
            else:
                result.append(msg)
        return result


class SummaryCompressTransform:
    """Placeholder for LLM-based summary compression of old messages.

    In production, this would call a cheap model to summarize old conversation
    history. For now, it simply drops messages beyond the window (equivalent
    to SlidingWindowTransform but intended to be replaced with real summarization).
    """

    def __init__(self, *, keep_recent: int = 5) -> None:
        self._keep_recent = keep_recent

    @property
    def transform_name(self) -> str:
        return "summary_compress"

    async def transform(
        self,
        messages: list[dict[str, Any]],
        budget: TokenBudget,
    ) -> list[dict[str, Any]]:
        # TODO: Replace with actual LLM-based summarization
        # For now, acts as a secondary sliding window
        protected = [
            m for m in messages
            if m.get("role") == "system" or m.get("_protected", False)
        ]
        non_protected = [
            m for m in messages
            if m.get("role") != "system" and not m.get("_protected", False)
        ]
        if len(non_protected) <= self._keep_recent:
            return messages
        return protected + non_protected[-self._keep_recent:]
