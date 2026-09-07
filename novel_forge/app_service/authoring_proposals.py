"""Apply approved proposals only through existing versioned domain services."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from novel_forge.app_service.contracts import JobCommand, JobRecord, JobState
from novel_forge.core.authoring import AuthoringProposalView
from novel_forge.persistence.authoring_proposals import ProposalStore
from novel_forge.persistence.authoring_store import AuthoringDeniedError, content_version
from novel_forge.persistence.filesystem import atomic_write_json
from novel_forge.persistence.planning_revision import (
    PlanningRevision,
    planning_publication_receipt,
)
from novel_forge.workspace.authoring_proposals import _candidate_version
from novel_forge.workspace.contracts import ManualRevisionRequest
from novel_forge.workspace.execution_manual_revision import (
    execute_manual_revision,
    recover_committed_revision,
)
from novel_forge.workspace.helpers.execution_runners import _project_lock
from novel_forge.workspace.planning_jobs import (
    planning_job_path,
    planning_job_view,
    reconcile_planning_job,
)


def reconcile_checkpoint_job(root: Path, record: JobRecord) -> None:
    """Use an exact durable task result; a next checkpoint is not prose acceptance."""
    if str(getattr(record.kind, "value", record.kind)) not in {
        "resolve_chapter_checkpoint",
        "resolve_chapter_checkpoint_finalize",
    } or record.status not in {JobState.SUCCEEDED, JobState.FAILED, JobState.PAUSED}:
        return
    if record.pending_decision and record.status == JobState.PAUSED:
        return  # A persisted live human wait has not completed the proposal's task.
    store = ProposalStore(root)
    with store.authority.lock():
        for path in store.directory.glob("*.json"):
            try:
                data = store.read(path.stem)
                AuthoringProposalView.model_validate(data["view"])
            except (OSError, ValueError, KeyError, TypeError):
                logging.getLogger(__name__).warning("Invalid proposal evidence: %s", path)
                continue
            view = data["view"]
            application = view["application_result"]
            if (
                data["command"]["kind"] != "checkpoint"
                or view["status"] != "approved"
                or view["project_id"] != record.project_id
                or application.get("task_id") != record.job_id
            ):
                continue
            raw_checkpoint = record.result.get("checkpoint")
            checkpoint = raw_checkpoint if isinstance(raw_checkpoint, dict) else {}
            next_checkpoint = (
                record.result.get("status") == "needs_decision"
                and checkpoint.get("checkpoint_id")
                and checkpoint["checkpoint_id"] != data["command"]["checkpoint_id"]
            )
            if next_checkpoint or (
                record.status == JobState.SUCCEEDED and record.result.get("status") == "completed"
            ):
                view["status"] = "applied"
                view["application_result"] = {
                    **application,
                    "status": "needs_decision" if next_checkpoint else "completed",
                    "checkpoint_id": checkpoint.get("checkpoint_id", ""),
                    "message": "任务已到新检查点；仍须对新方案或最终正文单独确认"
                    if next_checkpoint
                    else "已完成此批准对应的任务",
                }
            else:
                store.revoke(data)
                view["status"] = "pending"
                view["application_result"] = {
                    "status": "interrupted",
                    "attempt": application.get("attempt", 1),
                    "previous_task_id": record.job_id,
                    "error": record.error or "任务未完成此操作；请核对当前版本后重新批准或拒绝",
                }
            store.write(data)


def recover_checkpoint_proposals(service: Any) -> None:
    """Restart restores evidence, never approvals or automatic replacement tasks."""
    if service.storage_root is None or not service.storage_root.is_dir():
        return
    for root in service.storage_root.iterdir():
        if not root.is_dir():
            continue
        store = ProposalStore(root)
        for path in store.directory.glob("*.json"):
            try:
                _recover_checkpoint_proposal(service, store, path.stem)
            except (OSError, ValueError, KeyError, TypeError):
                logging.getLogger(__name__).warning("Proposal recovery needs inspection: %s", path)


def _recover_checkpoint_proposal(service: Any, store: ProposalStore, proposal_id: str) -> None:
    data = store.read(proposal_id)
    view = data["view"]
    task_id = view["application_result"].get("task_id")
    if data["command"]["kind"] != "checkpoint" or view["status"] != "approved" or not task_id:
        return
    record = service.get(task_id)
    if record is None:
        record = JobRecord(
            job_id=task_id,
            kind="resolve_chapter_checkpoint",
            project_id=view["project_id"],
            label="中断的提案任务",
            status=JobState.FAILED,
            error="重启未找到此任务的完成证据；未自动重试，请核对版本并重新批准",
        )
    reconcile_checkpoint_job(store.root, record)


async def apply_proposal(
    runtime: Any, service: Any, project_id: str, proposal_id: str
) -> AuthoringProposalView:
    try:
        view = await _apply_proposal(runtime, service, project_id, proposal_id)
        if view.status == "applied":
            from novel_forge.app_service.semantic_repair import (
                reconcile_semantic_repair_receipt,
            )

            reconcile_semantic_repair_receipt(
                runtime.storage.existing_project_dir(project_id), proposal_id
            )
            proposal_store = ProposalStore(runtime.storage.existing_project_dir(project_id))
            proposal_data = proposal_store.read(proposal_id)
            if (
                service is not None
                and proposal_data.get("command", {}).get("kind")
                in {"publish_planning", "revise_foundation"}
            ):
                try:
                    semantic_job = service.maybe_refresh_semantic_consistency(project_id)
                except Exception as exc:
                    with proposal_store.authority.lock():
                        proposal_data = proposal_store.read(proposal_id)
                        persisted = AuthoringProposalView.model_validate(proposal_data["view"])
                        persisted.application_result = {
                            **persisted.application_result,
                            "semantic_refresh_error": str(exc),
                        }
                        proposal_data["view"] = persisted.model_dump(mode="json")
                        proposal_store.write(proposal_data)
                        view = persisted
                else:
                    if semantic_job is not None:
                        with proposal_store.authority.lock():
                            proposal_data = proposal_store.read(proposal_id)
                            persisted = AuthoringProposalView.model_validate(proposal_data["view"])
                            persisted.application_result = {
                                **persisted.application_result,
                                "semantic_job_id": semantic_job.job_id,
                            }
                            proposal_data["view"] = persisted.model_dump(mode="json")
                            proposal_store.write(proposal_data)
                            view = persisted
        return view
    except Exception as exc:
        store = ProposalStore(runtime.storage.existing_project_dir(project_id))
        with store.authority.lock():
            data = store.read(proposal_id)
            if data["view"]["application_result"].get("status") == "applying":
                data["view"]["application_result"].update(status="failed", error=str(exc))
                store.write(data)
        task_id = data["view"]["application_result"].get("task_id")
        if data["command"]["kind"] == "checkpoint" and task_id:
            get = getattr(service, "get", None)
            record = get(task_id) if callable(get) else None
            if record is None:
                record = JobRecord(
                    job_id=task_id,
                    kind="resolve_chapter_checkpoint",
                    project_id=project_id,
                    label="未派发的提案",
                    status=JobState.FAILED,
                    error=str(exc),
                )
            reconcile_checkpoint_job(store.root, record)
        raise


async def _apply_proposal(
    runtime: Any, service: Any, project_id: str, proposal_id: str
) -> AuthoringProposalView:
    root = runtime.storage.existing_project_dir(project_id)
    store = ProposalStore(root)
    existing = store.read(proposal_id)
    task_id = existing["view"]["application_result"].get("task_id")
    get = getattr(service, "get", None)
    if existing["command"]["kind"] == "checkpoint" and task_id and callable(get):
        record = get(task_id)
        if record is not None:
            reconcile_checkpoint_job(root, record)
            existing = store.read(proposal_id)
    if (
        existing["command"]["kind"] in {"publish_planning", "revise_foundation"}
        and existing["view"]["status"] == "approved"
    ):
        receipt = planning_publication_receipt(root, existing["command"]["revision_id"])
        if receipt and receipt["candidate_version"] == existing["view"]["candidate_version"]:
            async with _project_lock(runtime, project_id):
                state = reconcile_planning_job(root)
                with store.authority.lock():
                    if existing["command"]["kind"] == "revise_foundation":
                        from novel_forge.app_service.authoring_foundation import (
                            complete_foundation_commit,
                        )

                        complete_foundation_commit(
                            runtime,
                            project_id,
                            PlanningRevision.load(
                                root, project_id, existing["command"]["revision_id"]
                            ),
                        )
                    existing = store.read(proposal_id)
                    existing["view"].update(
                        status="applied", application_result={**receipt, "status": "published"}
                    )
                    store.write(existing)
            if (
                service is not None
                and state.get("job_id")
                and state.get("request_id") == existing["command"].get("planning_request_id")
            ):
                service.planning_candidate_published(state)
            return AuthoringProposalView.model_validate(existing["view"])
    if (
        existing["command"]["kind"] in {"revise_chapter", "restore_chapter"}
        and existing["view"]["status"] == "approved"
    ):
        receipt = await recover_committed_revision(
            runtime, project_id, existing["view"]["chapter_number"], proposal_id
        )
        if receipt is not None:
            with store.authority.lock():
                existing = store.read(proposal_id)
                existing["view"].update(status="applied", application_result=receipt)
                store.write(existing)
                return AuthoringProposalView.model_validate(existing["view"])
    with store.authority.lock():
        data = store.read(proposal_id)
        view = AuthoringProposalView.model_validate(data["view"])
        if view.project_id != project_id:
            raise ValueError("提案不属于此作品")
        if view.status == "applied" or view.application_result.get("task_id"):
            return view
        store.assert_current(view)
        if view.status != "approved" or _candidate_version(root, data) != view.candidate_version:
            raise AuthoringDeniedError("提案尚未批准或候选版本已过期")
        approval_id = data.get("approval_id", "")
        store.authority.require(
            view.action,
            view.chapter_number,
            approval_id=approval_id,
            candidate_version=view.candidate_version,
            major_change=view.action in {"revise", "publish_planning", "extend"},
        )
        from novel_forge.pipeline.finalization_manifest import require_authoring_chapter_predecessor

        require_authoring_chapter_predecessor(root, view.chapter_number, view.action)
        attempt = int(view.application_result.get("attempt", 0)) + 1
        application: dict[str, Any] = {"status": "applying"}
        if data["command"]["kind"] == "checkpoint":
            application.update(attempt=attempt, task_id=f"proposal-{proposal_id}-{attempt}")
        data["view"]["application_result"] = application
        store.write(data)
    command = data["command"]
    if command["kind"] == "checkpoint":
        record = service.resolve_book_autorun_checkpoint(
            project_id=project_id,
            checkpoint_id=command["checkpoint_id"],
            option_id=command["option_id"],
            notes=command["notes"],
            authoring_approval_id=approval_id,
            job_id=application["task_id"],
        )
        if record is None:
            record = service.submit(
                JobCommand(
                    job_id=application["task_id"],
                    kind="resolve_chapter_checkpoint",
                    project_id=project_id,
                    payload={
                        "project_id": project_id,
                        "chapter_number": view.chapter_number,
                        "checkpoint_id": command["checkpoint_id"],
                        "option_id": command["option_id"],
                        "notes": command["notes"],
                        "authoring_approval_id": approval_id,
                    },
                )
            )
        if record.job_id != application["task_id"]:
            raise AuthoringDeniedError("作品存在其他任务；此提案未派发，请暂停或等候后重新核对")
        view.application_result = {**application, "status": "submitted"}
    elif command["kind"] in {"revise_chapter", "restore_chapter"}:
        result = await execute_manual_revision(
            runtime,
            ManualRevisionRequest(
                project_id=project_id,
                chapter_number=view.chapter_number,
                text=view.candidate,
                scope="forward_only",
                expected_input_version=view.input_version,
                authoring_approval_id=approval_id,
                reason=f"authoring_proposal:{proposal_id}",
            ),
        )
        view.application_result = result.result
        view.status = "applied"
    elif command["kind"] == "revise_foundation":
        from novel_forge.app_service.authoring_foundation import complete_foundation_commit

        async with _project_lock(runtime, project_id):
            store.assert_current(view)
            revision = PlanningRevision.load(root, project_id, command["revision_id"])
            if (
                revision.purpose != "foundation"
                or content_version(revision.changes()) != command["validated_version"]
            ):
                raise AuthoringDeniedError("设定候选验证后发生变化")
            changed = revision.publish(
                authoring_approval_id=approval_id, authoring_chapter=view.chapter_number
            )
            with store.authority.lock():
                complete_foundation_commit(runtime, project_id, revision)
            view.status = "applied"
            view.application_result = {
                "status": "published",
                "changed_artifacts": changed,
                "message": "设定专项修订已应用；旧正文保留，受影响契约与报告须重新验证",
            }
    elif command["kind"] == "publish_planning":
        async with _project_lock(runtime, project_id):
            store.assert_current(view)
            revision = PlanningRevision.load(root, project_id, command["revision_id"])
            if content_version(revision.changes()) != command["validated_version"]:
                raise AuthoringDeniedError("规划候选在验证后发生变化")
            changed = revision.publish(
                authoring_approval_id=approval_id, authoring_chapter=view.chapter_number
            )
            state = planning_job_view(root)
            if state.get("request_id") == command["planning_request_id"]:
                state.update(status="published", error="")
                state["result"]["published"] = True
                atomic_write_json(planning_job_path(root), state)
            view.status = "applied"
            view.application_result = {"status": "published", "changed_artifacts": changed}
        if service is not None and state.get("job_id"):
            service.planning_candidate_published(state)
    else:
        raise ValueError("不支持的领域命令")
    with store.authority.lock():
        latest = store.read(proposal_id)
        if latest["view"]["status"] == "approved":
            latest["view"] = view.model_dump(mode="json")
            store.write(latest)
        else:
            view = AuthoringProposalView.model_validate(latest["view"])
    if command["kind"] == "checkpoint":
        reconcile_checkpoint_job(root, record)
        view = AuthoringProposalView.model_validate(store.read(proposal_id)["view"])
    return view
