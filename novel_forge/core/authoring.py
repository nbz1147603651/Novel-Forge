"""Author authority, separate from model recommendations and client preferences.

These are application contracts, not model output schemas. Unconfigured legacy
projects retain their old controls; adopting a policy never starts a task.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

AuthoringMode = Literal["manual", "coauthor", "authorized_auto"]
SemanticConsistencyStatus = Literal[
    "uncompiled",
    "queued",
    "running",
    "clean",
    "conflict",
    "review_required",
    "stale",
    "failed",
]
AuthoringAction = Literal[
    "discuss",
    "prepare",
    "generate",
    "repair",
    "archive",
    "plan_candidates",
    "publish_planning",
    "revise",
    "extend",
    "resume",
    "pause",
]
AUTHORING_ACTIONS = (
    "discuss",
    "prepare",
    "generate",
    "repair",
    "archive",
    "plan_candidates",
    "publish_planning",
    "revise",
    "extend",
    "resume",
    "pause",
)


class AuthoringModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class AuthoringPolicy(AuthoringModel):
    version: int = Field(default=1, ge=1)
    mode: AuthoringMode = "manual"
    start_chapter: int = Field(default=1, ge=1)
    end_chapter: int = Field(default=1, ge=1)
    # A tighter bound only; None means the existing gateway budget, never unlimited.
    budget_usd: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    stopped: bool = True
    stop_reason: str = "尚未启动"

    @model_validator(mode="after")
    def ordered_scope(self) -> AuthoringPolicy:
        if self.end_chapter < self.start_chapter:
            raise ValueError("结束章不能早于开始章")
        return self


class AuthoringPermission(AuthoringModel):
    action: str
    allowed: bool
    requires_approval: bool = False
    reason: str = ""


class SemanticConsistencyView(AuthoringModel):
    """Read-only projection derived from the existing claim ledger and jobs."""

    status: SemanticConsistencyStatus = "uncompiled"
    source_version: str = ""
    source_fingerprint: str = ""
    ledger_hash: str = ""
    report_id: str = ""
    issue_ids: list[str] = Field(default_factory=list)
    issue_count: int = Field(default=0, ge=0)
    job_id: str = ""
    affected_chapters: list[int] = Field(default_factory=list)
    reason: str = ""


class AuthoringSessionView(AuthoringModel):
    project_id: str
    configured: bool = False
    disabled: bool = False
    policy: AuthoringPolicy = Field(default_factory=AuthoringPolicy)
    input_version: str = ""
    allowed_actions: list[AuthoringPermission] = Field(default_factory=list)
    current_task_id: str = ""
    waiting_reason: str = ""
    stop_state: Literal["idle", "running", "stopping", "stopped"] = "idle"
    proposal_ids: list[str] = Field(default_factory=list)
    checkpoint: dict[str, Any] = Field(default_factory=dict)
    planning_task: dict[str, Any] = Field(default_factory=dict)
    revision_history: list[dict[str, Any]] = Field(default_factory=list)
    current_text_hash: str = ""
    budget: dict[str, Any] = Field(default_factory=dict)
    semantic_consistency: SemanticConsistencyView = Field(
        default_factory=SemanticConsistencyView
    )


class AuthoringProposalView(AuthoringModel):
    id: str
    project_id: str
    action: AuthoringAction
    chapter_number: int = Field(ge=1)
    policy_version: int = Field(ge=1)
    input_version: str
    candidate_version: str
    title: str
    original: str = ""
    candidate: str = ""
    evidence: list[str] = Field(default_factory=list)
    affected_chapters: list[int] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    lock_conflicts: list[str] = Field(default_factory=list)
    cost_hint: str = "沿用现有模型预算；接受后可能产生模型费用"
    status: Literal["pending", "approved", "rejected", "deferred", "stale", "applied"] = "pending"
    application_result: dict[str, Any] = Field(default_factory=dict)
    editable: bool = False


class AuthoringProposalRequest(AuthoringModel):
    command: Literal[
        "checkpoint", "revise_chapter", "restore_chapter", "publish_planning", "revise_foundation"
    ]
    chapter_number: int = Field(ge=1)
    title: str = Field(default="共创提案", max_length=200)
    candidate: str = ""
    option_id: str = ""
    notes: str = ""
    revision_id: str = ""
    revision_side: Literal["before", "after"] = "before"
    evidence: list[str] = Field(default_factory=list)
    expected_input_version: str = ""
    foundation_artifact: Literal["spec", "world", "characters", "blueprint"] = "spec"


class AuthoringProposalDecision(AuthoringModel):
    decision: Literal["accept", "reject", "defer", "edit"]
    candidate_version: str
    input_version: str
    policy_version: int = Field(ge=1)
    edited_candidate: str | None = None


class AuthoringMessageRequest(AuthoringModel):
    message: str = Field(min_length=1, max_length=8000)
    chapter_number: int = Field(default=1, ge=1)
    context: Literal[
        "spec", "world", "characters", "blueprint", "outline", "chapter", "reports"
    ] = "chapter"
    allow_actions: bool = False


class AuthoringActionSuggestion(AuthoringModel):
    action: Literal["prepare", "apply_approved_proposal"]
    proposal_id: str = ""
    reason: str = ""


class AuthoringReply(AuthoringModel):
    reply: str = Field(min_length=1)
    proposals: list[AuthoringProposalRequest] = Field(default_factory=list, max_length=3)
    actions: list[AuthoringActionSuggestion] = Field(default_factory=list, max_length=3)


class AuthoringMessageView(AuthoringModel):
    id: str
    project_id: str
    message: str
    context: str
    chapter_number: int
    input_version: str
    policy_version: int
    task_id: str
    created_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    status: Literal["queued", "running", "completed", "failed", "paused"] = "queued"
    response: AuthoringReply | None = None
    proposal_ids: list[str] = Field(default_factory=list)
    action_results: list[dict[str, Any]] = Field(default_factory=list)
    error: str = ""


def authoring_permission(
    policy: AuthoringPolicy,
    action: str,
    chapter_number: int,
    *,
    explicit: bool = False,
    approved: bool = False,
    major_change: bool = False,
    spent_usd: float = 0,
) -> AuthoringPermission:
    """One fail-closed predicate for UI, dispatch and commit boundaries."""

    def denied(reason: str, *, approval: bool = False) -> AuthoringPermission:
        return AuthoringPermission(
            action=action, allowed=False, requires_approval=approval, reason=reason
        )

    if action not in AUTHORING_ACTIONS:
        return denied("不支持的领域动作")
    if action == "pause":
        return AuthoringPermission(action=action, allowed=True)
    if action == "discuss":
        if policy.budget_usd is not None and spent_usd >= policy.budget_usd:
            return denied("本次授权预算已用尽")
        return AuthoringPermission(
            action=action, allowed=explicit, reason="" if explicit else "请先发送消息"
        )
    if not policy.start_chapter <= chapter_number <= policy.end_chapter:
        return denied("超出已批准章段")
    if action == "resume":
        return AuthoringPermission(
            action=action, allowed=explicit, reason="" if explicit else "恢复需作者主动启动"
        )
    if policy.stopped:
        return denied(policy.stop_reason or "会话已停止")
    if (
        policy.budget_usd is not None
        and spent_usd >= policy.budget_usd
        and action not in {"archive", "revise", "publish_planning", "extend"}
    ):
        return denied("本次授权预算已用尽")
    if (major_change or action in {"revise", "extend"}) and not approved:
        return denied("重大或正式内容变更需要专项批准", approval=True)
    if policy.mode == "manual" and not (explicit or approved):
        return denied("手动模式等待作者启动", approval=True)
    if (
        policy.mode == "coauthor"
        and action in {"generate", "archive", "publish_planning"}
        and not approved
    ):
        return denied(
            "请确认本章方案" if action == "generate" else "请验收候选版本后继续", approval=True
        )
    return AuthoringPermission(action=action, allowed=True)
