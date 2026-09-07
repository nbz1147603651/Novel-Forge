"""Durable planning intent bridge to the existing JobService, not a scheduler."""

from __future__ import annotations

import asyncio
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from novel_forge.core.authoring_context import authoring_dispatch_guard
from novel_forge.core.schemas.outline import StoryOutline
from novel_forge.core.utils.text_hash import source_text_hash
from novel_forge.persistence.authoring_store import (
    AuthoringDeniedError,
    AuthoringStore,
    content_version,
)
from novel_forge.persistence.filesystem import atomic_write_json
from novel_forge.persistence.models import ProjectLayout
from novel_forge.persistence.planning_revision import planning_publication_receipt
from novel_forge.pipeline.artifact_manifest import ArtifactManifest
from novel_forge.pipeline.finalization_manifest import (
    record_finalization_pending,
    record_finalization_success,
)
from novel_forge.workspace.contracts import AdvancePlanningHorizonRequest
from novel_forge.workspace.execution_result import ExecutionResult
from novel_forge.workspace.planning_horizon import next_planning_horizon_target


def planning_job_path(root: Path) -> Path:
    return root / ".authoring" / "planning_horizon.json"


def planning_job_view(root: Path) -> dict[str, Any]:
    path = planning_job_path(root)
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def record_planning_candidate(root: Path, request_id: str, result: dict[str, Any]) -> None:
    """Persist the validated candidate before any publication can begin."""
    with AuthoringStore(root).lock():
        state = planning_job_view(root)
        if state.get("request_id") != request_id:
            raise AuthoringDeniedError("规划任务已替换；旧候选不覆盖新请求")
        state.update(status="candidate", result=result, error="")
        atomic_write_json(planning_job_path(root), state)


def reconcile_planning_job(root: Path) -> dict[str, Any]:
    """Receipt-only recovery is safe after pause and never authorizes new work."""
    with AuthoringStore(root).lock():
        state = planning_job_view(root)
        result = state.get("result", {})
        revision_id = result.get("revision_id")
        receipt = planning_publication_receipt(root, revision_id) if revision_id else None
        if receipt and receipt["candidate_version"] == result.get("candidate_version"):
            if state.get("status") != "published":
                state.update(status="published", error="")
                result["published"] = True
                atomic_write_json(planning_job_path(root), state)
        return state


class PlanningTaskPending(AuthoringDeniedError):
    def __init__(self, project_id: str, chapter: int, state: dict[str, Any]) -> None:
        super().__init__(state.get("error") or "章节规划待完成或批准；未生成正文")
        self.payload = {
            "project_id": project_id,
            "status": "needs_decision",
            "checkpoint": {
                "checkpoint_id": f"planning-{state['request_id']}",
                "checkpoint_type": "planning_wait",
                "chapter_number": chapter,
                "waiting_task_id": state["job_id"],
                "planning_status": state["status"],
                "summary": str(self),
                "options": [],
            },
        }


async def request_planning_horizon(
    runtime: Any,
    *,
    project_id: str,
    current_chapter: int,
    target_chapter: int | None = None,
    retry: bool = False,
    on_step_progress: Any = None,
    job_service: Any = None,
    explicit: bool = False,
) -> dict[str, Any] | None:
    from novel_forge.core.infra.resource_locks import ResourceName
    from novel_forge.workspace.helpers.execution_runners import _project_lock

    async with _project_lock(runtime, project_id, ResourceName.CANON):
        layout = ProjectLayout(runtime.storage.existing_project_dir(project_id))
        if not layout.outline_path.exists():
            return None
        outline = StoryOutline.model_validate(runtime.storage.load_json(layout.outline_path))
        hard = int(outline.hard_through_chapter or outline.total_chapters)
        target = target_chapter or next_planning_horizon_target(
            total_chapters=outline.total_chapters,
            hard_through_chapter=hard,
            current_chapter=current_chapter,
        )
        if target is None or target <= hard:
            return None
        if target > min(outline.total_chapters, hard + 5):
            raise ValueError("自动细化单批最多五章；补齐全书请使用明确的补齐命令")
        AuthoringStore(layout.root).require("plan_candidates", target, explicit=explicit)
        previous = reconcile_planning_job(layout.root)
        if previous.get("target_chapter") == target and previous.get("status") in {
            "queued",
            "running",
            "candidate",
            "failed",
            "paused",
        }:
            if previous["status"] not in {"failed", "paused"} or not retry:
                state = previous
            else:
                state = {
                    **previous,
                    "status": "queued",
                    "error": "",
                    "attempt": int(previous.get("attempt", 1)) + 1,
                }
                state["job_id"] = f"horizon-{state['request_id'][:20]}-{state['attempt']}"
                state["explicit"] = explicit
        else:
            request_id = content_version(
                {"outline": outline.model_dump(mode="json"), "target": target}
            )
            text_path = layout.chapter_path(current_chapter)
            state = {
                "request_id": request_id,
                "project_id": project_id,
                "chapter_number": current_chapter,
                "from_chapter": hard + 1,
                "target_chapter": target,
                "text_hash": source_text_hash(runtime.storage.load_text(text_path))
                if text_path.exists()
                else "",
                "status": "queued",
                "job_id": f"horizon-{request_id[:20]}-1",
                "attempt": 1,
                "mock": bool(getattr(runtime, "_mock_mode", False)),
                "error": "",
                "explicit": explicit,
            }
        atomic_write_json(planning_job_path(layout.root), state)
    if state["status"] not in {"queued", "running"}:
        if state["status"] == "failed":
            raise RuntimeError(state.get("error") or "规划待重试")
        return state
    service = job_service or getattr(runtime, "planning_job_service", None)
    if service is None:
        # CLI/PySide standalone callers synchronously execute the SAME persisted
        # request. Workspace never creates a second scheduler or imports app_service.
        await execute_planning_horizon_job(
            runtime,
            AdvancePlanningHorizonRequest(
                **{
                    key: state[key]
                    for key in (
                        "project_id",
                        "request_id",
                        "chapter_number",
                        "target_chapter",
                        "attempt",
                        "explicit",
                    )
                }
            ),
            on_step_progress=on_step_progress,
        )
        return planning_job_view(layout.root)
    record = service.submit_planning_horizon(state)
    if on_step_progress is not None:
        on_step_progress("planning_horizon_queued", {**state, "task_id": record.job_id})
    return state


async def execute_planning_horizon_job(
    runtime: Any,
    request: AdvancePlanningHorizonRequest,
    *,
    on_step_progress: Any = None,
) -> ExecutionResult[dict[str, Any]]:
    from novel_forge.workspace.execution_planning_horizon import advance_planning_horizon

    layout = ProjectLayout(runtime.storage.existing_project_dir(request.project_id))
    store = AuthoringStore(layout.root)
    reconcile_planning_job(layout.root)
    with store.lock():
        state = planning_job_view(layout.root)
        if (
            state.get("request_id") != request.request_id
            or state.get("target_chapter") != request.target_chapter
            or state.get("attempt", 1) != request.attempt
        ):
            raise AuthoringDeniedError("规划请求已过期，请刷新水位")
        if state["status"] in {"published", "candidate"}:
            return ExecutionResult(project_id=request.project_id, result=state)
        state["status"] = "running"
        atomic_write_json(planning_job_path(layout.root), state)
    policy = store.policy()

    def check_dispatch() -> None:
        if store.policy() != policy:
            raise AuthoringDeniedError("授权已变更；规划任务已停止派发")
        if policy is not None:
            store.require(
                "plan_candidates",
                request.target_chapter,
                explicit=request.explicit,
                expected_policy_version=policy.version,
            )

    def save_state() -> None:
        with store.lock():
            current = planning_job_view(layout.root)
            if current.get("job_id") != state["job_id"]:
                raise AuthoringDeniedError("规划任务已被替代；保留旧候选但不覆盖新任务")
            atomic_write_json(planning_job_path(layout.root), state)

    try:
        from novel_forge.persistence.authoring_budget import AuthoringBudget

        budget = AuthoringBudget(layout.root, check_dispatch)
        with authoring_dispatch_guard(
            check_dispatch,
            reserve=budget.reserve if policy else None,
            settle=budget.settle if policy else None,
        ):
            check_dispatch()
            result = await advance_planning_horizon(
                runtime,
                project_id=request.project_id,
                target_chapter=request.target_chapter,
                explicit=request.explicit,
                planning_request_id=request.request_id,
                on_step_progress=on_step_progress,
            )
        state.update(
            {
                "status": "published" if result is None or result.published else "candidate",
                "result": asdict(result) if result else {},
                "error": "",
            }
        )
    except (Exception, asyncio.CancelledError) as exc:
        durable = reconcile_planning_job(layout.root)
        if durable.get("job_id") == state["job_id"] and durable.get("result"):
            # A validated/committed candidate survives loss of the return value.
            state.update(durable)
        state.update(
            {
                "status": state["status"]
                if state["status"] in {"candidate", "published"}
                else (
                    "paused"
                    if isinstance(exc, (AuthoringDeniedError, asyncio.CancelledError))
                    else "failed"
                ),
                "error": str(exc) or "规划已暂停",
            }
        )
        if state["status"] == "published":
            state.update(recovered_error=state["error"], error="")
        save_state()
        if state.get("text_hash") and state["status"] != "published":
            record_finalization_pending(
                ArtifactManifest(runtime.storage, layout),
                chapter_number=request.chapter_number,
                phase="planning_horizon",
                text_hash=state["text_hash"],
                error=exc,
                metadata={"job_id": state["job_id"]},
            )
        if state["status"] != "published":
            raise
    save_state()
    if state.get("text_hash") and state["status"] == "published":
        record_finalization_success(
            ArtifactManifest(runtime.storage, layout),
            chapter_number=request.chapter_number,
            phase="planning_horizon",
            text_hash=state["text_hash"],
            metadata={"job_id": state["job_id"]},
        )
    return ExecutionResult(project_id=request.project_id, result=state)
