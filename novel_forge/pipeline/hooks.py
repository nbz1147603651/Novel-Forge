"""Pipeline step hook chain (Pi-inspired before/after interceptors).

This module provides a composable hook system for PipelineStep.run():

- ``BeforeHook``: invoked before step execution. Can inspect/validate input
  or return None to skip execution entirely.
- ``AfterHook``: invoked after step execution. Can post-process output
  (format sanitize, leak detection, etc.).
- ``HookChain``: manages an ordered list of hooks with add/remove/filter.

Design constraints (quality preservation):
- After hooks execute BEFORE downstream quality checks (step output → hooks →
  quality pipeline). Any text modification by hooks will be caught by alignment/
  continuity/causal checks downstream.
- Hook exceptions never break the pipeline (caught, logged, skipped).
- Hooks cannot modify format_contracts or bypass enforce_required_keys.
- When no hooks are registered, behavior is identical to unhooked execution.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Protocol, TypeVar, runtime_checkable

logger = logging.getLogger(__name__)

InputT = TypeVar("InputT", contravariant=True)
OutputT = TypeVar("OutputT")


# ── Hook Protocols ────────────────────────────────────────────────────────────


@runtime_checkable
class BeforeHook(Protocol):
    """Protocol for pre-execution hooks.

    Return the (possibly modified) input to continue, or None to skip execution.
    """

    @property
    def hook_name(self) -> str:
        """Human-readable hook identifier for logging."""
        ...

    async def __call__(self, step_name: str, input_data: Any) -> Any | None:
        """Inspect/validate input before step execution.

        Args:
            step_name: The step's ``step_name`` property.
            input_data: The input that will be passed to ``_execute()``.

        Returns:
            The input (possibly modified) to continue execution,
            or None to skip execution (step returns None).
        """
        ...


@runtime_checkable
class AfterHook(Protocol):
    """Protocol for post-execution hooks.

    Receives the step output and returns the (possibly modified) output.
    """

    @property
    def hook_name(self) -> str:
        """Human-readable hook identifier for logging."""
        ...

    async def __call__(self, step_name: str, input_data: Any, output: Any) -> Any:
        """Post-process output after step execution.

        Args:
            step_name: The step's ``step_name`` property.
            input_data: The original input (for context).
            output: The output from ``_execute()``.

        Returns:
            The (possibly modified) output.
        """
        ...


# ── Hook Registration Entry ───────────────────────────────────────────────────


@dataclass
class HookEntry:
    """Internal registration entry for a hook with optional step filter."""

    hook: Any  # BeforeHook | AfterHook
    step_filter: frozenset[str] | None = None  # None = applies to all steps
    enabled: bool = True


# ── HookChain ─────────────────────────────────────────────────────────────────


class HookChain:
    """Manages an ordered chain of before/after hooks for pipeline steps.

    Usage::

        chain = HookChain()
        chain.add_before(TokenBudgetHook())
        chain.add_after(FormatSanitizeHook())
        chain.add_after(PromptLeakDetectorHook(), steps={"draft_chapter", "wave_chapter"})

        # In PipelineStep.run():
        input_data = await chain.run_before("draft_chapter", input_data)
        if input_data is None:
            return None  # execution skipped by hook
        output = await self._execute(input_data)
        output = await chain.run_after("draft_chapter", input_data, output)
    """

    def __init__(self) -> None:
        self._before_hooks: list[HookEntry] = []
        self._after_hooks: list[HookEntry] = []

    # ── Registration ──────────────────────────────────────────────────────

    def add_before(
        self,
        hook: Any,
        *,
        steps: set[str] | None = None,
    ) -> None:
        """Register a before-hook. Optionally filter to specific step names."""
        entry = HookEntry(
            hook=hook,
            step_filter=frozenset(steps) if steps else None,
        )
        self._before_hooks.append(entry)

    def add_after(
        self,
        hook: Any,
        *,
        steps: set[str] | None = None,
    ) -> None:
        """Register an after-hook. Optionally filter to specific step names."""
        entry = HookEntry(
            hook=hook,
            step_filter=frozenset(steps) if steps else None,
        )
        self._after_hooks.append(entry)

    def remove(self, hook_name: str) -> bool:
        """Remove a hook by name. Returns True if found and removed."""
        removed = False
        self._before_hooks = [
            e for e in self._before_hooks
            if getattr(e.hook, "hook_name", "") != hook_name or not (removed := True)
        ]
        self._after_hooks = [
            e for e in self._after_hooks
            if getattr(e.hook, "hook_name", "") != hook_name or not (removed := True)
        ]
        return removed

    def disable(self, hook_name: str) -> None:
        """Temporarily disable a hook without removing it."""
        for entry in self._before_hooks + self._after_hooks:
            if getattr(entry.hook, "hook_name", "") == hook_name:
                entry.enabled = False

    def enable(self, hook_name: str) -> None:
        """Re-enable a previously disabled hook."""
        for entry in self._before_hooks + self._after_hooks:
            if getattr(entry.hook, "hook_name", "") == hook_name:
                entry.enabled = True

    # ── Execution ─────────────────────────────────────────────────────────

    async def run_before(self, step_name: str, input_data: Any) -> Any | None:
        """Run all matching before-hooks in order.

        Returns the (possibly modified) input, or None if any hook
        signals to skip execution.
        """
        current = input_data
        for entry in self._before_hooks:
            if not entry.enabled:
                continue
            if entry.step_filter is not None and step_name not in entry.step_filter:
                continue
            try:
                result = await entry.hook(step_name, current)
                if result is None:
                    hook_name = getattr(entry.hook, "hook_name", type(entry.hook).__name__)
                    logger.info(
                        "BeforeHook '%s' skipped execution of step '%s'",
                        hook_name,
                        step_name,
                    )
                    return None
                current = result
            except Exception:
                hook_name = getattr(entry.hook, "hook_name", type(entry.hook).__name__)
                logger.warning(
                    "BeforeHook '%s' failed for step '%s' — skipping hook",
                    hook_name,
                    step_name,
                    exc_info=True,
                )
        return current

    async def run_after(self, step_name: str, input_data: Any, output: Any) -> Any:
        """Run all matching after-hooks in order.

        Returns the (possibly modified) output.
        """
        current = output
        for entry in self._after_hooks:
            if not entry.enabled:
                continue
            if entry.step_filter is not None and step_name not in entry.step_filter:
                continue
            try:
                current = await entry.hook(step_name, input_data, current)
            except Exception:
                hook_name = getattr(entry.hook, "hook_name", type(entry.hook).__name__)
                logger.warning(
                    "AfterHook '%s' failed for step '%s' — skipping hook",
                    hook_name,
                    step_name,
                    exc_info=True,
                )
        return current

    # ── Introspection ─────────────────────────────────────────────────────

    @property
    def before_hook_count(self) -> int:
        return len(self._before_hooks)

    @property
    def after_hook_count(self) -> int:
        return len(self._after_hooks)

    @property
    def is_empty(self) -> bool:
        """True when no hooks are registered (fast-path in run())."""
        return not self._before_hooks and not self._after_hooks

    def list_hooks(self) -> list[dict[str, Any]]:
        """Return a summary of all registered hooks for debugging."""
        result = []
        for entry in self._before_hooks:
            result.append({
                "name": getattr(entry.hook, "hook_name", type(entry.hook).__name__),
                "phase": "before",
                "enabled": entry.enabled,
                "step_filter": sorted(entry.step_filter) if entry.step_filter else None,
            })
        for entry in self._after_hooks:
            result.append({
                "name": getattr(entry.hook, "hook_name", type(entry.hook).__name__),
                "phase": "after",
                "enabled": entry.enabled,
                "step_filter": sorted(entry.step_filter) if entry.step_filter else None,
            })
        return result


# ── Module-level default chain (singleton) ────────────────────────────────────

_default_chain: HookChain | None = None


def get_hook_chain() -> HookChain:
    """Get or create the module-level default HookChain singleton."""
    global _default_chain  # noqa: PLW0603
    if _default_chain is None:
        _default_chain = HookChain()
    return _default_chain
