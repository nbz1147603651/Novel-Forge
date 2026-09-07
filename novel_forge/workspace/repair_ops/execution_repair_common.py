"""Shared utilities for repair execution helpers."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from typing import Any

from novel_forge.obs.logger import get_logger
from novel_forge.workspace.async_context import sync_to_async_context

_log = get_logger("workspace.execution_repair")


@asynccontextmanager
async def _project_lock(runtime: Any, project_id: str) -> AsyncIterator[None]:
    storage = getattr(runtime, "storage", None)
    owner_id = f"async-task:{id(asyncio.current_task())}"

    from novel_forge.persistence.filesystem import FileSystemStorage, project_file_lock_owner

    with project_file_lock_owner(owner_id):
        async with AsyncExitStack() as stack:
            try:
                from novel_forge.core.infra.resource_locks import (
                    ResourceLockType,
                    ResourceName,
                    get_resource_lock_manager,
                )

                lock_mgr = get_resource_lock_manager()
                await stack.enter_async_context(
                    lock_mgr.lock(
                        ResourceName.CANON,
                        ResourceLockType.EXCLUSIVE,
                        project_id=project_id,
                    )
                )
            except Exception:
                pass

            if isinstance(storage, FileSystemStorage):
                sync_ctx = storage.project_lock(project_id)
                await stack.enter_async_context(sync_to_async_context(sync_ctx))

            yield


def _sig_ngram_jaccard(a: str, b: str, n: int = 3) -> float:
    """Return Jaccard similarity of n-gram sets for two normalized strings."""
    if len(a) < n or len(b) < n:
        return 0.0
    grams_a = {a[i : i + n] for i in range(len(a) - n + 1)}
    grams_b = {b[i : i + n] for i in range(len(b) - n + 1)}
    union = len(grams_a | grams_b)
    return len(grams_a & grams_b) / union if union else 0.0


def _recheck_sigs_with_fuzzy_match(
    recheck_issues: list[Any],
    selected_issue_signatures: set[str],
    *,
    jaccard_threshold: float = 0.12,
) -> set[str]:
    """Map recheck issues back to *selected* issue signatures.

    For each recheck issue we first try an exact signature match (fast path).
    If that fails, we try:
      1. Paragraph-number match: same issue_type + same "pN" anchor.
      2. N-gram Jaccard fallback for old-format (summary-prefix) anchors.

    This handles LLM paraphrasing between repair and recheck passes: the same
    unresolved issue may be described with different wording, making exact
    signature matches fail and silently preventing escalation counters from
    incrementing.
    """
    import re

    from novel_forge.pipeline.steps.causal_repair_step import CausalRepairStep

    unresolved: set[str] = set()
    for iss in recheck_issues:
        recheck_sig = CausalRepairStep.issue_signature(iss)

        # Fast path: exact match.
        if recheck_sig in selected_issue_signatures:
            unresolved.add(recheck_sig)
            continue

        # Fuzzy path: same issue_type + paragraph number or n-gram overlap.
        recheck_type = CausalRepairStep._norm_issue_text(getattr(iss, "issue_type", "") or "")
        recheck_location = getattr(iss, "location", "") or ""
        recheck_summary = CausalRepairStep._norm_issue_text(getattr(iss, "summary", "") or "")

        # Extract paragraph number from recheck location for paragraph-based matching.
        recheck_para: str | None = None
        _para_m = re.search(r"第\s*(\d+)", recheck_location)
        if _para_m:
            recheck_para = _para_m.group(1)

        for sel_sig in selected_issue_signatures:
            parts = sel_sig.split("|", 1)
            if len(parts) != 2 or parts[0] != recheck_type:
                continue
            sel_anchor = parts[1]

            # Paragraph-number match: both use "pN" format.
            if recheck_para and sel_anchor.startswith("p") and sel_anchor[1:].isdigit():
                if recheck_para == sel_anchor[1:]:
                    unresolved.add(sel_sig)
                    break
                continue  # Same type but different paragraph — not the same issue.

            # N-gram fallback for old-format (summary-prefix) anchors.
            if len(sel_anchor) > 5:
                if _sig_ngram_jaccard(recheck_summary, sel_anchor, n=2) >= jaccard_threshold:
                    unresolved.add(sel_sig)
                    break

    return unresolved
