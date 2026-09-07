"""Internal components for the shared repair loop runner."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Generic, Protocol, TypeVar

from novel_forge.pipeline.long.decisions import filter_must_fix_issues
from novel_forge.pipeline.repair_orchestration.audit_events import text_hash

ReportT = TypeVar("ReportT")


class RepairLoopOwner(Protocol[ReportT]):
    """Narrow owner protocol required by repair loop components."""

    _config: Any
    _on_step: Callable[[str, Any], None]

    def filter_issues_for_round(self, issues: list[Any], current_round: int, ctx: Any) -> list[Any]:
        ...

    def _promote_issues_below_hard_floor(
        self,
        issues: list[Any],
        must_fix_issues: list[Any],
        score: float,
    ) -> list[Any]:
        ...

    def _focus_issues_for_round(self, issues: list[Any], ctx: Any) -> list[Any]:
        ...

    def issue_signature(self, issue: Any) -> str:
        ...

    def _emit_issues_audit_event(
        self,
        event_type: str,
        issues: list[Any],
        ctx: Any,
        *,
        status: str,
        repair_action: str = "",
        bucket: str = "",
        source_text_hash: str = "",
        target_text_hash: str = "",
        failure_kind: str = "",
        fallback_action: str = "",
        extra: dict[str, Any] | None = None,
    ) -> None:
        ...

    def _emit_loop_audit_summary(
        self,
        ctx: Any,
        result: Any,
        *,
        status: str,
        failure_kind: str = "",
        fallback_action: str = "",
    ) -> None:
        ...


@dataclass(frozen=True)
class SelectedRepairIssues:
    """Issue selection result for one repair round."""

    filtered_issues: list[Any]
    must_fix_issues: list[Any]
    source_text_hash: str


class RepairIssueSelector(Generic[ReportT]):
    """Select and track the issues attempted in a repair round."""

    def __init__(self, owner: RepairLoopOwner[ReportT]) -> None:
        self._owner = owner

    def select_for_round(self, ctx: Any, round_num: int) -> SelectedRepairIssues:
        filtered = self._owner.filter_issues_for_round(ctx.issues, round_num, ctx)
        must_fix = filter_must_fix_issues(filtered, self._owner._config.must_fix_severity)
        must_fix = self._owner._promote_issues_below_hard_floor(
            filtered,
            must_fix,
            ctx.score,
        )
        must_fix = self._owner._focus_issues_for_round(must_fix, ctx)
        return SelectedRepairIssues(
            filtered_issues=list(filtered),
            must_fix_issues=list(must_fix),
            source_text_hash=text_hash(ctx.pre_round_text),
        )

    def record_attempts(self, ctx: Any) -> dict[str, int]:
        attempts: dict[str, int] = {}
        for issue in ctx.must_fix_issues:
            signature = self._owner.issue_signature(issue)
            ctx.issue_attempts[signature] = ctx.issue_attempts.get(signature, 0) + 1
            attempts[signature] = ctx.issue_attempts[signature]
        return attempts


class RepairAuditEmitter(Generic[ReportT]):
    """Canonical audit-event facade for repair loop internals."""

    def __init__(self, owner: RepairLoopOwner[ReportT]) -> None:
        self._owner = owner

    def selected(self, ctx: Any, issues: list[Any], *, source_text_hash: str) -> None:
        self._owner._emit_issues_audit_event(
            "selected",
            issues,
            ctx,
            status="selected",
            repair_action="focus_round",
            bucket="repair_audit_selected_issue_ids",
            source_text_hash=source_text_hash,
            extra={"max_rounds": self._owner._config.max_rounds},
        )

    def attempted(
        self,
        ctx: Any,
        issues: list[Any],
        *,
        source_text_hash: str,
        attempts: dict[str, int],
    ) -> None:
        self._owner._emit_issues_audit_event(
            "attempted",
            issues,
            ctx,
            status="attempted",
            repair_action="repair_call",
            bucket="repair_audit_attempted_issue_ids",
            source_text_hash=source_text_hash,
            extra={"attempts": attempts},
        )

    def applied(
        self,
        ctx: Any,
        issues: list[Any],
        *,
        source_text_hash: str,
        target_text_hash: str,
    ) -> None:
        self._owner._emit_issues_audit_event(
            "applied",
            issues,
            ctx,
            status="attempted",
            repair_action="repair_applied",
            source_text_hash=source_text_hash,
            target_text_hash=target_text_hash,
        )

    def failed(
        self,
        ctx: Any,
        issues: list[Any],
        *,
        repair_action: str,
        source_text_hash: str,
        target_text_hash: str = "",
        failure_kind: str = "",
        fallback_action: str = "",
    ) -> None:
        self._owner._emit_issues_audit_event(
            "failed",
            issues,
            ctx,
            status="failed",
            repair_action=repair_action,
            source_text_hash=source_text_hash,
            target_text_hash=target_text_hash,
            failure_kind=failure_kind,
            fallback_action=fallback_action,
        )

    def finalized(
        self,
        ctx: Any,
        issues: list[Any],
        *,
        status: str,
        repair_action: str,
        source_text_hash: str,
        target_text_hash: str = "",
        failure_kind: str = "",
        fallback_action: str = "",
        bucket: str = "",
        extra: dict[str, Any] | None = None,
    ) -> None:
        self._owner._emit_issues_audit_event(
            "finalized",
            issues,
            ctx,
            status=status,
            repair_action=repair_action,
            bucket=bucket,
            source_text_hash=source_text_hash,
            target_text_hash=target_text_hash,
            failure_kind=failure_kind,
            fallback_action=fallback_action,
            extra=extra,
        )

    def loop_summary(
        self,
        ctx: Any,
        result: Any,
        *,
        status: str,
        failure_kind: str = "",
        fallback_action: str = "",
    ) -> None:
        self._owner._emit_loop_audit_summary(
            ctx,
            result,
            status=status,
            failure_kind=failure_kind,
            fallback_action=fallback_action,
        )


class RepairRollbackGuard(Generic[ReportT]):
    """Rollback actions shared by repair safety branches."""

    def __init__(
        self,
        owner: RepairLoopOwner[ReportT],
        audit: RepairAuditEmitter[ReportT],
    ) -> None:
        self._owner = owner
        self._audit = audit

    def rollback_to_pre_round(
        self,
        ctx: Any,
        *,
        event_name: str,
        event_payload: dict[str, Any],
        issues: list[Any],
        source_text_hash: str,
        target_text_hash: str,
        repair_action: str,
        failure_kind: str,
        fallback_action: str = "rollback_to_pre_repair_text",
    ) -> None:
        self._owner._on_step(event_name, event_payload)
        self._audit.finalized(
            ctx,
            issues,
            status="rolled_back",
            repair_action=repair_action,
            source_text_hash=source_text_hash,
            target_text_hash=target_text_hash,
            failure_kind=failure_kind,
            fallback_action=fallback_action,
        )
        ctx.current_text = ctx.pre_round_text


class RepairLoopExecutor(Generic[ReportT]):
    """Component bundle used by the legacy repair loop facade."""

    def __init__(self, owner: RepairLoopOwner[ReportT]) -> None:
        self.issue_selector = RepairIssueSelector[ReportT](owner)
        self.audit = RepairAuditEmitter[ReportT](owner)
        self.rollback_guard = RepairRollbackGuard[ReportT](owner, self.audit)


__all__ = [
    "RepairAuditEmitter",
    "RepairIssueSelector",
    "RepairLoopExecutor",
    "RepairLoopOwner",
    "RepairRollbackGuard",
    "SelectedRepairIssues",
]
