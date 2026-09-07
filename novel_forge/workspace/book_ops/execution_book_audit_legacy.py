"""Compatibility adapter for the retired composite whole-book audit flow."""

from __future__ import annotations

from typing import Any

from novel_forge.workspace.book_ops.execution_book_audit_runner import (
    run_book_consistency_audit,
)
from novel_forge.workspace.book_ops.execution_book_repair import (
    _run_book_consistency_auto_repair,
)
from novel_forge.workspace.book_ops.execution_book_verify import (
    _run_book_consistency_verify,
)
from novel_forge.workspace.contracts import (
    BookConsistencyRequest,
    GlobalRepairQueueRequest,
)
from novel_forge.workspace.execution_result import ExecutionResult, StepCallback
from novel_forge.workspace.runtime import RuntimeServices

__all__ = [
    "_execute_book_consistency_legacy",
    "_run_book_consistency_auto_repair",
    "_run_book_consistency_verify",
    "run_book_consistency_audit",
]


async def _execute_book_consistency_legacy(
    runtime: RuntimeServices,
    request: BookConsistencyRequest,
    *,
    on_step_progress: StepCallback = None,
) -> ExecutionResult[Any]:
    """Delegate the historical composite entry to the current two workflows.

    The audit remains the returned result for compatibility. A historical
    ``repair_mode="targeted"`` request additionally drains only ``ready``
    tickets from the audit-produced repair queue. Verification, rollback,
    reporting, and checkpoint behavior therefore have one production owner.
    """

    # Imported lazily to avoid a cycle: the stable entry module re-exports this
    # compatibility function for one deprecation cycle.
    from novel_forge.workspace.book_ops.execution_book_entry import (
        execute_book_consistency,
        execute_global_repair_queue,
    )

    audit_execution = await execute_book_consistency(
        runtime,
        request,
        on_step_progress=on_step_progress,
    )
    repair_mode = str(getattr(request, "repair_mode", "off") or "off").strip().lower()
    if repair_mode != "targeted":
        return audit_execution

    if on_step_progress:
        on_step_progress(
            "book_consistency_legacy_repair_delegated",
            {"status": "running", "architecture": "global_repair_queue"},
        )

    await execute_global_repair_queue(
        runtime,
        GlobalRepairQueueRequest(
            project_id=request.project_id,
            max_items=int(getattr(request, "repair_max_chapters", 20) or 20),
            verify_before_apply=bool(getattr(request, "verify_before_repair", True)),
            rollback_on_failure=bool(getattr(request, "rollback_on_failure", True)),
            concurrency=int(getattr(request, "repair_concurrency", 1) or 1),
        ),
        on_step_progress=on_step_progress,
    )
    return audit_execution
