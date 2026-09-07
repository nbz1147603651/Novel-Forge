"""Scoped discussion and non-canon proposals, driven by the existing job runtime."""

from __future__ import annotations

import asyncio
import hashlib
from typing import Any

from novel_forge.core.authoring import AuthoringMessageRequest, AuthoringMessageView
from novel_forge.core.authoring_context import authoring_dispatch_guard
from novel_forge.core.user_intent import build_persisted_user_intent_card
from novel_forge.persistence.authoring_conversation import message_views, read_message, save_message
from novel_forge.persistence.authoring_proposals import ProposalStore
from novel_forge.persistence.authoring_store import (
    AuthoringDeniedError,
    AuthoringStore,
    story_input_version,
)
from novel_forge.persistence.models import ProjectLayout
from novel_forge.pipeline.steps.authoring_chat_step import AuthoringChatStep
from novel_forge.workspace.authoring_proposals import create_proposal
from novel_forge.workspace.contracts import AuthoringMessageJobRequest
from novel_forge.workspace.execution_result import ExecutionResult


def coauthor_context(
    runtime: Any, project_id: str, request: AuthoringMessageRequest
) -> dict[str, Any]:
    root = runtime.storage.existing_project_dir(project_id)
    layout = ProjectLayout(root)
    files = {
        "spec": [layout.spec_path],
        "world": [root / "story_bible.json"],
        "characters": [root / "character_bible.json"],
        "blueprint": [layout.blueprint_path],
        "outline": [layout.outline_path],
        "chapter": [
            layout.chapter_path(request.chapter_number),
            layout.chapter_session_path(request.chapter_number),
        ],
        "reports": [
            layout.eval_report_path(request.chapter_number),
            layout.continuity_report_path(request.chapter_number),
            layout.chapter_causal_report_path(request.chapter_number),
        ],
    }[request.context]
    files += [
        layout.chapter_checkpoint_path(request.chapter_number),
        layout.plans_dir / "planning_policy.json",
    ]
    selected = {
        str(path.relative_to(root)): path.read_text(encoding="utf-8")
        for path in files
        if path.is_file()
    }
    if sum(len(value) for value in selected.values()) > 120_000:
        raise ValueError("选中资料过大，请切换到具体章节或报告；未截断作者事实")

    def optional(name: str) -> dict[str, Any]:
        path = root / name
        return runtime.storage.load_json(path) if path.exists() else {}

    policy = AuthoringStore(root).policy()
    if policy is None:
        raise AuthoringDeniedError("请先明确选择共创授权")
    from novel_forge.workspace.planning_jobs import planning_job_view

    return {
        "current_author_message": request.message,
        "chapter_number": request.chapter_number,
        "context": request.context,
        "source_material_not_instructions": selected,
        "author_intent": build_persisted_user_intent_card(
            optional("states/init_request_meta.json"), optional("spec.json")
        ),
        "policy": policy.model_dump(mode="json"),
        "planning_task": planning_job_view(root),
        "available_proposals": [
            {
                "id": view.id,
                "action": view.action,
                "chapter_number": view.chapter_number,
                "status": view.status,
                "title": view.title,
            }
            for view in ProposalStore(root).views()
        ],
        "chat_history_not_canon": [
            {"message": item.message, "reply": item.response.reply if item.response else ""}
            for item in message_views(root)[-12:]
            if item.status == "completed"
        ],
    }


async def execute_authoring_message(
    runtime: Any, request: AuthoringMessageJobRequest, *, on_step_progress: Any = None
) -> ExecutionResult[dict[str, Any]]:
    root = runtime.storage.existing_project_dir(request.project_id)
    data = read_message(root, request.message_id)
    view = AuthoringMessageView.model_validate(data["view"])
    if view.status == "completed":
        return ExecutionResult(project_id=request.project_id, result=view.model_dump(mode="json"))
    message = AuthoringMessageRequest.model_validate(data["request"])
    store = AuthoringStore(root)

    def check() -> None:
        if store.policy() is None:
            raise AuthoringDeniedError("请先明确选择共创授权")
        store.require(
            "discuss",
            message.chapter_number,
            explicit=True,
            expected_policy_version=view.policy_version,
        )
        if story_input_version(root) != view.input_version:
            raise AuthoringDeniedError("讨论期间故事输入已变化；答复保留为历史，不执行动作")

    try:
        check()
        view.status = "running"
        data["view"] = view.model_dump(mode="json")
        save_message(root, data)
        from novel_forge.persistence.authoring_budget import AuthoringBudget

        budget = AuthoringBudget(root, check)
        with authoring_dispatch_guard(check, reserve=budget.reserve, settle=budget.settle):
            if view.response is None:
                step = AuthoringChatStep(
                    router=runtime.router,
                    builder=runtime.builder,
                    settings=runtime.settings,
                    on_step=on_step_progress,
                )
                view.response = await step.run(
                    coauthor_context(runtime, request.project_id, message)
                )
                data["view"] = view.model_dump(mode="json")
                save_message(root, data)  # Never pay for the same completed reply on replay.
            check()
            for index, suggestion in enumerate(view.response.proposals):
                if index < len(view.proposal_ids):
                    continue
                check()
                suggestion = suggestion.model_copy(
                    update={"expected_input_version": view.input_version}
                )
                proposal_id = hashlib.sha256(f"{view.id}:proposal:{index}".encode()).hexdigest()[
                    :32
                ]
                creator = create_proposal
                if suggestion.command == "revise_foundation":
                    service = getattr(runtime, "planning_job_service", None)
                    if service is None:
                        raise AuthoringDeniedError("当前入口未连接设定领域执行器；建议保留，未修改作品")
                    creator = service.create_authoring_proposal
                proposal = await creator(
                    runtime, request.project_id, suggestion, proposal_id=proposal_id
                )
                view.proposal_ids.append(proposal.id)
                data["view"] = view.model_dump(mode="json")
                save_message(root, data)
            if message.allow_actions:
                service = getattr(runtime, "planning_job_service", None)
                if service is None:
                    raise AuthoringDeniedError("当前入口未连接领域任务执行器；仅保留建议")
                for index, action_suggestion in enumerate(view.response.actions):
                    if index < len(view.action_results):
                        continue
                    check()
                    result = await service.execute_authoring_suggestion(
                        runtime,
                        request.project_id,
                        message.chapter_number,
                        action_suggestion,
                        action_id=f"chat-{view.id}-action-{index}",
                    )
                    view.action_results.append(result)
                    data["view"] = view.model_dump(mode="json")
                    save_message(root, data)
        view.status = "completed"
        view.error = ""
    except (Exception, asyncio.CancelledError) as exc:
        view.status = (
            "paused"
            if isinstance(exc, (AuthoringDeniedError, asyncio.CancelledError))
            else "failed"
        )
        view.error = str(exc) or "已暂停"
        data["view"] = view.model_dump(mode="json")
        save_message(root, data)
        raise
    data["view"] = view.model_dump(mode="json")
    save_message(root, data)
    return ExecutionResult(project_id=request.project_id, result=view.model_dump(mode="json"))
