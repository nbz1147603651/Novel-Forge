"""Transport-neutral author controls for HTTP and the legacy desktop.

Local approval/application uses storage only. Models can only run via the
existing JobService; opening this control never constructs a model gateway.
"""

from __future__ import annotations

import hashlib
from types import SimpleNamespace
from typing import Any, cast

from novel_forge.app_service.authoring import authoring_session
from novel_forge.app_service.contracts import JobCommand, JobKind, JobRecord, JobState
from novel_forge.core.authoring import (
    AuthoringPolicy,
    AuthoringProposalDecision,
    AuthoringProposalRequest,
    AuthoringProposalView,
    AuthoringSessionView,
)
from novel_forge.persistence.authoring_proposals import ProposalStore
from novel_forge.persistence.authoring_store import (
    AuthoringDeniedError,
    AuthoringStore,
    story_input_version,
)
from novel_forge.persistence.filesystem import FileSystemStorage


def require_authoring_rollout() -> None:
    from novel_forge.app_service.engine_views import engine_capabilities

    if not engine_capabilities().features.get("authoring_coauthor", False):
        raise AuthoringDeniedError("完整共创能力尚未开放；仅可查看、暂停、拒绝建议或恢复已提交结果")


class AuthoringCommands:
    def __init__(self, storage: FileSystemStorage, service: Any = None) -> None:
        self.storage = storage
        self.service = service

    def view(self, project_id: str, chapter: int = 1) -> AuthoringSessionView:
        root = self.storage.existing_project_dir(project_id)
        view = authoring_session(root, project_id, chapter)
        service = self.service
        if service is not None and service.storage_root is not None:
            if service.storage_root.resolve() != root.parent.resolve():
                raise AuthoringDeniedError("任务服务与作品存储不一致")
            state, task_id = service.authoring_activity(project_id)
            if state in {"running", "stopping"}:
                view.stop_state = state
                view.current_task_id = task_id
                if state == "stopping":
                    view.waiting_reason = "正在安全停止；已发出请求可结束计算，但不得发布候选"
            semantic_prefix = self.semantic_job_id(
                project_id,
                view.input_version,
                view.policy.version,
            )
            list_jobs = getattr(service, "list", None)
            semantic_job = (
                next(
                    (
                        item
                        for item in list_jobs(project_id)
                        if item.job_id == semantic_prefix
                        or item.job_id.startswith(f"{semantic_prefix}-attempt-")
                    ),
                    None,
                )
                if callable(list_jobs)
                else service.get(semantic_prefix)
            )
            if semantic_job is not None:
                if semantic_job.status == JobState.QUEUED:
                    view.semantic_consistency.status = "queued"
                    view.semantic_consistency.reason = "语义一致性刷新已排队"
                elif semantic_job.status == JobState.RUNNING:
                    view.semantic_consistency.status = "running"
                    view.semantic_consistency.reason = "正在编译来源 Claim 并进行模型裁决"
                elif semantic_job.status == JobState.FAILED:
                    view.semantic_consistency.status = "failed"
                    view.semantic_consistency.reason = (
                        semantic_job.error
                        or str(semantic_job.error_summary.get("summary") or "语义刷新失败")
                    )
                view.semantic_consistency.job_id = semantic_job.job_id
        return view

    @staticmethod
    def semantic_job_id(project_id: str, input_version: str, policy_version: int) -> str:
        identity = hashlib.sha256(
            f"semantic:{project_id}:{input_version}:{policy_version}".encode("utf-8")
        ).hexdigest()[:24]
        return f"semantic-{identity}"

    def refresh_semantic_consistency(
        self,
        project_id: str,
        *,
        expected_story_version: str,
        expected_policy_version: int,
    ) -> JobRecord:
        """Submit one exact, path-free refresh through the existing JobService."""

        require_authoring_rollout()
        if self.service is None:
            raise AuthoringDeniedError("语义刷新需要可恢复的后台任务服务")
        root = self.storage.existing_project_dir(project_id)
        store = AuthoringStore(root)
        policy = store.policy()
        if policy is None or policy.version != expected_policy_version:
            raise AuthoringDeniedError("授权版本已变化；旧刷新请求未提交")
        if policy.stopped:
            raise AuthoringDeniedError("共创已停止；启动授权后才能产生语义刷新费用")
        if story_input_version(root) != expected_story_version:
            raise AuthoringDeniedError("故事来源已变化；请刷新页面后重新提交")
        job_id = self.semantic_job_id(
            project_id,
            expected_story_version,
            expected_policy_version,
        )
        matching_jobs: list[JobRecord] = [
            item
            for item in self.service.list(project_id)
            if item.job_id == job_id or item.job_id.startswith(f"{job_id}-attempt-")
        ]
        active = next(
            (
                item
                for item in matching_jobs
                if item.status in {JobState.QUEUED, JobState.RUNNING}
            ),
            None,
        )
        if active is not None:
            return active
        if matching_jobs:
            job_id = f"{job_id}-attempt-{len(matching_jobs) + 1}"
        return cast(
            JobRecord,
            self.service.submit(
                JobCommand(
                    job_id=job_id,
                    kind=JobKind.SEMANTIC_CONSISTENCY,
                    project_id=project_id,
                    payload={
                        "project_id": project_id,
                        "expected_story_version": expected_story_version,
                        "expected_policy_version": expected_policy_version,
                    },
                )
            ),
        )

    def _idle(self, project_id: str) -> None:
        if self.view(project_id).stop_state in {"running", "stopping"}:
            raise AuthoringDeniedError("请先暂停并等待安全停止，再修改或启用授权")

    def set_policy(self, project_id: str, policy: AuthoringPolicy, expected_version: int) -> None:
        require_authoring_rollout()
        self._idle(project_id)
        AuthoringStore(self.storage.existing_project_dir(project_id)).set_policy(
            policy, expected_version=expected_version
        )

    def start(self, project_id: str, expected_version: int, input_version: str) -> None:
        require_authoring_rollout()
        self._idle(project_id)
        policy = AuthoringStore(self.storage.existing_project_dir(project_id)).start(
            expected_version=expected_version, input_version=input_version
        )
        maybe_refresh = getattr(self.service, "maybe_refresh_semantic_consistency", None)
        if policy.mode != "manual" and callable(maybe_refresh):
            maybe_refresh(project_id)

    def decide(
        self, project_id: str, proposal_id: str, decision: AuthoringProposalDecision
    ) -> AuthoringProposalView:
        from novel_forge.workspace.authoring_proposals import decide_proposal

        if decision.decision not in {"reject", "defer"}:
            require_authoring_rollout()
        return decide_proposal(self.storage.existing_project_dir(project_id), proposal_id, decision)

    async def propose(
        self, project_id: str, request: AuthoringProposalRequest
    ) -> AuthoringProposalView:
        from novel_forge.app_service.authoring_foundation import create_authoring_proposal

        require_authoring_rollout()
        return await create_authoring_proposal(
            SimpleNamespace(storage=self.storage), project_id, request
        )

    async def apply(self, project_id: str, proposal_id: str) -> AuthoringProposalView:
        from novel_forge.app_service.authoring_proposals import apply_proposal

        root = self.storage.existing_project_dir(project_id)
        proposal_data = ProposalStore(root).read(proposal_id)
        if proposal_data.get("repair_case_id"):
            raise AuthoringDeniedError("此提案由修复工作台管理；批准后请返回工作台生成发布回执")
        view = next((item for item in ProposalStore(root).views() if item.id == proposal_id), None)
        if view is None or not (
            view.status == "applied"
            or view.application_result.get("recoverable_receipt")
            or view.application_result.get("task_id")
        ):
            require_authoring_rollout()
        return await apply_proposal(
            SimpleNamespace(storage=self.storage), self.service, project_id, proposal_id
        )
