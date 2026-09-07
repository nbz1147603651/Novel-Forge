from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from novel_forge.pipeline.repair_orchestration.audit_events import text_hash
from novel_forge.pipeline.repair_orchestration.loop_components import RepairLoopExecutor


class _Owner:
    def __init__(self) -> None:
        self._config = SimpleNamespace(must_fix_severity="high", max_rounds=3)
        self.events: list[tuple[str, dict[str, Any]]] = []
        self.audit_events: list[dict[str, Any]] = []

    def filter_issues_for_round(self, issues: list[Any], _round_num: int, _ctx: Any) -> list[Any]:
        return list(issues)

    def _promote_issues_below_hard_floor(
        self,
        _issues: list[Any],
        must_fix_issues: list[Any],
        _score: float,
    ) -> list[Any]:
        return list(must_fix_issues)

    def _focus_issues_for_round(self, issues: list[Any], _ctx: Any) -> list[Any]:
        return list(issues[:1])

    def issue_signature(self, issue: Any) -> str:
        if isinstance(issue, dict):
            return str(issue.get("issue_id") or issue.get("summary") or "")
        return str(getattr(issue, "issue_id", "") or getattr(issue, "summary", "") or "")

    def _emit_issues_audit_event(
        self,
        event_type: str,
        issues: list[Any],
        _ctx: Any,
        **payload: Any,
    ) -> None:
        self.audit_events.append(
            {"event_type": event_type, "issue_count": len(issues), **payload}
        )

    def _on_step(self, event_name: str, payload: dict[str, Any]) -> None:
        self.events.append((event_name, payload))


def test_repair_loop_components_select_and_track_attempts() -> None:
    owner = _Owner()
    components = RepairLoopExecutor[Any](owner)
    ctx = SimpleNamespace(
        issues=[
            SimpleNamespace(issue_id="a", severity="critical", summary="first"),
            SimpleNamespace(issue_id="b", severity="medium", summary="second"),
        ],
        must_fix_issues=[],
        score=7.0,
        pre_round_text="before",
        issue_attempts={},
        extra={},
    )

    selected = components.issue_selector.select_for_round(ctx, 0)
    ctx.must_fix_issues = selected.must_fix_issues
    attempts = components.issue_selector.record_attempts(ctx)
    components.audit.selected(ctx, selected.must_fix_issues, source_text_hash=selected.source_text_hash)
    components.audit.attempted(
        ctx,
        selected.must_fix_issues,
        source_text_hash=selected.source_text_hash,
        attempts=attempts,
    )

    assert [issue.issue_id for issue in selected.must_fix_issues] == ["a"]
    assert attempts == {"a": 1}
    assert owner.audit_events[0]["event_type"] == "selected"
    assert owner.audit_events[1]["event_type"] == "attempted"


def test_repair_rollback_guard_restores_pre_round_text_and_emits_audit() -> None:
    owner = _Owner()
    components = RepairLoopExecutor[Any](owner)
    ctx = SimpleNamespace(
        current_text="after",
        pre_round_text="before",
        must_fix_issues=[{"issue_id": "a", "severity": "critical"}],
    )

    components.rollback_guard.rollback_to_pre_round(
        ctx,
        event_name="change_budget_exceeded",
        event_payload={"chapter": 1},
        issues=list(ctx.must_fix_issues),
        source_text_hash=text_hash("before"),
        target_text_hash=text_hash("after"),
        repair_action="change_budget_exceeded",
        failure_kind="change_budget",
    )

    assert ctx.current_text == "before"
    assert owner.events == [("change_budget_exceeded", {"chapter": 1})]
    assert owner.audit_events[0]["event_type"] == "finalized"
    assert owner.audit_events[0]["status"] == "rolled_back"
