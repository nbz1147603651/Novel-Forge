"""Qt-free authoring projections consumed by both desktops and HTTP."""

from __future__ import annotations

import json
from pathlib import Path

from novel_forge.core.authoring import (
    AUTHORING_ACTIONS,
    AuthoringPolicy,
    AuthoringSessionView,
    authoring_permission,
)
from novel_forge.core.utils.text_hash import source_text_hash
from novel_forge.persistence.authoring_store import AuthoringStore, story_input_version
from novel_forge.persistence.final_revision_journal import revision_history
from novel_forge.persistence.models import ProjectLayout
from novel_forge.persistence.semantic_consistency import semantic_consistency_view_for_root


def authoring_session(root: Path, project_id: str, chapter: int = 1) -> AuthoringSessionView:
    from novel_forge.persistence.authoring_proposals import ProposalStore
    from novel_forge.workspace.authoring_control import checkpoint_action
    from novel_forge.workspace.planning_jobs import planning_job_view

    store = AuthoringStore(root)
    policy = store.policy()
    effective = policy or AuthoringPolicy()
    from novel_forge.persistence.authoring_budget import AuthoringBudget

    budget = AuthoringBudget(root).totals()
    checkpoint_path = ProjectLayout(root).chapter_checkpoint_path(chapter)
    prose_path = ProjectLayout(root).chapter_path(chapter)
    checkpoint = (
        json.loads(checkpoint_path.read_text(encoding="utf-8")) if checkpoint_path.exists() else {}
    )
    checkpoint["options"] = [
        option
        for option in checkpoint.get("options", [])
        if checkpoint_action(str(option.get("option_id", ""))) in {"generate", "archive"}
    ]
    view = AuthoringSessionView(
        project_id=project_id,
        configured=policy is not None,
        disabled=store.disabled(),
        policy=effective,
        input_version=story_input_version(root),
        allowed_actions=[
            authoring_permission(
                effective,
                action,
                chapter,
                explicit=True,
                spent_usd=budget["spent_usd"] + budget["reserved_usd"],
            )
            for action in AUTHORING_ACTIONS
        ],
        waiting_reason=effective.stop_reason
        if effective.stopped
        else str(checkpoint.get("summary", "")),
        stop_state="stopped" if effective.stopped else "idle",
        checkpoint=checkpoint,
        planning_task=planning_job_view(root),
        proposal_ids=[view.id for view in ProposalStore(root).views()],
        budget=budget,
        semantic_consistency=semantic_consistency_view_for_root(root),
        current_text_hash=source_text_hash(prose_path.read_text(encoding="utf-8"))
        if prose_path.is_file()
        else "",
        revision_history=[
            {
                key: item.get(key, "")
                for key in (
                    "timestamp",
                    "previous_hash",
                    "current_hash",
                    "reason",
                    "transaction_status",
                )
            }
            for item in revision_history(ProjectLayout(root), chapter)[:20]
        ],
    )
    from novel_forge.persistence.foundation_guard import assert_foundation_sync_complete

    try:
        store.require_enabled()
        assert_foundation_sync_complete(root)
    except ValueError as exc:
        view.waiting_reason = str(exc)
        for permission in view.allowed_actions:
            if permission.action != "pause" and (view.disabled or permission.action != "discuss"):
                permission.allowed = False
                permission.reason = str(exc)
    if not view.disabled and not effective.stopped:
        from novel_forge.pipeline.finalization_manifest import authoring_chapter_wait_reason

        if waiting := authoring_chapter_wait_reason(root, chapter):
            view.waiting_reason = waiting
            for permission in view.allowed_actions:
                if permission.action in {"prepare", "generate", "archive"}:
                    permission.allowed = False
                    permission.reason = waiting
    return view
