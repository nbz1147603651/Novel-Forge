"""Book consistency audit and reevaluation execution helpers.

Thin backward-compatible facade.  Implementation lives in the
execution_book_* sub-modules.
"""

from __future__ import annotations

from novel_forge.workspace.book_ops.execution_book_common import (
    _compact_issue_pool_entry,
    _load_issue_panel_pool_for_chapters,
    _resolve_book_audit_analysis_mode,
)
from novel_forge.workspace.book_ops.execution_book_continue import _continue_repair_from_audit
from novel_forge.workspace.book_ops.execution_book_entry import execute_book_consistency
from novel_forge.workspace.book_ops.execution_book_reevaluate import execute_reevaluate_chapter
from novel_forge.workspace.book_ops.execution_book_repair import (
    _build_book_consistency_repair_report_payload,
    _run_book_consistency_auto_repair,
)
from novel_forge.workspace.book_ops.execution_book_verify import _run_book_consistency_verify
from novel_forge.workspace.execution_result import ExecutionResult, StepCallback

__all__ = [
    "ExecutionResult",
    "StepCallback",
    "_compact_issue_pool_entry",
    "_load_issue_panel_pool_for_chapters",
    "_resolve_book_audit_analysis_mode",
    "_continue_repair_from_audit",
    "execute_book_consistency",
    "_build_book_consistency_repair_report_payload",
    "_run_book_consistency_auto_repair",
    "execute_reevaluate_chapter",
    "_run_book_consistency_verify",
]
