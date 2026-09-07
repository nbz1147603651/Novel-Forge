"""Planning jobs use the existing durable queue and restart recovery."""

from __future__ import annotations

import logging
from typing import Any, cast

from novel_forge.app_service.contracts import JobCommand, JobKind, JobRecord, JobState


def submit_planning_job(service: Any, state: dict[str, Any]) -> JobRecord:
    existing = service.get(state["job_id"])
    if existing is not None:
        return cast(JobRecord, existing)
    return cast(
        JobRecord,
        service.submit(
            JobCommand(
                job_id=state["job_id"],
                kind=JobKind.PLANNING_HORIZON,
                project_id=state["project_id"],
                mock=state.get("mock", False),
                label=f"细化第 {state['from_chapter']}–{state['target_chapter']} 章",
                payload={
                    **{
                        name: state[name]
                        for name in ("project_id", "request_id", "chapter_number", "target_chapter")
                    },
                    "attempt": state.get("attempt", 1),
                    "explicit": state.get("explicit", False),
                },
                metadata={"planning_request_id": state["request_id"]},
            )
        ),
    )


def recover_planning_jobs(service: Any) -> None:
    from novel_forge.persistence.authoring_store import AuthoringStore
    from novel_forge.workspace.planning_jobs import reconcile_planning_job

    if service.storage_root is None or not service.storage_root.is_dir():
        return
    for root in service.storage_root.iterdir():
        if not root.is_dir() or root.name.startswith("."):
            continue
        try:
            state = reconcile_planning_job(root)
            policy = AuthoringStore(root).policy()
        except (OSError, ValueError, TypeError):
            logging.getLogger(__name__).warning(
                "Planning recovery needs manual inspection: %s", root.name
            )
            continue
        if state.get("status") == "published":
            service.planning_candidate_published(state)
            continue
        if state.get("status") not in {"queued", "running"}:
            continue
        if policy is not None and policy.stopped:
            continue
        # Failed/rejected candidates never restart themselves and spend again.
        existing = service.get(state.get("job_id", ""))
        if existing is not None and existing.status in {JobState.FAILED, JobState.PAUSED}:
            continue
        submit_planning_job(service, state)
