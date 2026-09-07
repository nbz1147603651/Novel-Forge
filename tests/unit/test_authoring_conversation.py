from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from novel_forge.core.authoring import (
    AuthoringMessageRequest,
    AuthoringMessageView,
    AuthoringPolicy,
    AuthoringProposalDecision,
    AuthoringProposalRequest,
    AuthoringReply,
)
from novel_forge.persistence.authoring_conversation import message_views, read_message, save_message
from novel_forge.persistence.authoring_proposals import ProposalStore
from novel_forge.persistence.authoring_store import (
    AuthoringDeniedError,
    AuthoringStore,
    story_input_version,
)
from novel_forge.persistence.filesystem import FileSystemStorage, atomic_write_text
from novel_forge.persistence.models import ProjectLayout
from novel_forge.workspace.authoring_conversation import coauthor_context, execute_authoring_message
from novel_forge.workspace.authoring_proposals import decide_proposal
from novel_forge.workspace.contracts import AuthoringMessageJobRequest


@pytest.fixture
def conversation(tmp_path, monkeypatch):
    storage = FileSystemStorage(tmp_path)
    root = storage.ensure_project_dir("book")
    atomic_write_text(ProjectLayout(root).chapter_path(1), "作者的正文；开放结局。")
    authority = AuthoringStore(root)
    policy = authority.set_policy(
        AuthoringPolicy(mode="coauthor", end_chapter=5), expected_version=0
    )
    policy = authority.start(
        expected_version=policy.version, input_version=story_input_version(root)
    )
    response = AuthoringReply(
        reply="建议保留开放结局。",
        proposals=[
            AuthoringProposalRequest(
                command="revise_chapter", chapter_number=1, candidate="保留开放结局的候选正文。"
            )
        ],
    )
    call = AsyncMock(return_value=response)
    monkeypatch.setattr(
        "novel_forge.workspace.authoring_conversation.AuthoringChatStep",
        lambda **_: SimpleNamespace(run=call),
    )
    runtime = SimpleNamespace(storage=storage, router=None, builder=None, settings=None)
    request = AuthoringMessageRequest(message="比较表达，不改结局", context="chapter")
    view = AuthoringMessageView(
        id=uuid4().hex,
        project_id="book",
        message=request.message,
        context=request.context,
        chapter_number=1,
        input_version=story_input_version(root),
        policy_version=policy.version,
        task_id="chat-job",
    )
    save_message(
        root, {"request": request.model_dump(mode="json"), "view": view.model_dump(mode="json")}
    )
    return runtime, root, view, call


async def test_discussion_creates_candidate_not_canon_and_replay_does_not_pay_twice(conversation):
    runtime, root, view, call = conversation
    before = story_input_version(root)
    result = await execute_authoring_message(
        runtime, AuthoringMessageJobRequest(project_id="book", message_id=view.id)
    )
    assert result.result["status"] == "completed"
    assert story_input_version(root) == before
    proposal = ProposalStore(root).views()[0]
    assert proposal.status == "pending"
    await execute_authoring_message(
        runtime, AuthoringMessageJobRequest(project_id="book", message_id=view.id)
    )
    assert call.await_count == 1
    assert len(ProposalStore(root).views()) == 1
    assert message_views(root)[0].response.reply == "建议保留开放结局。"


async def test_replay_after_interrupted_proposal_save_preserves_rejection(
    conversation, monkeypatch
):
    from novel_forge.workspace import authoring_conversation as module

    runtime, root, view, call = conversation
    create = module.create_proposal

    async def interrupted(*args, **kwargs):
        await create(*args, **kwargs)
        raise OSError("interrupted after candidate write")

    monkeypatch.setattr(module, "create_proposal", interrupted)
    request = AuthoringMessageJobRequest(project_id="book", message_id=view.id)
    with pytest.raises(OSError):
        await execute_authoring_message(runtime, request)
    proposal = ProposalStore(root).views()[0]
    decide_proposal(
        root,
        proposal.id,
        AuthoringProposalDecision(
            decision="reject",
            candidate_version=proposal.candidate_version,
            input_version=proposal.input_version,
            policy_version=proposal.policy_version,
        ),
    )
    monkeypatch.setattr(module, "create_proposal", create)
    await execute_authoring_message(runtime, request)
    assert call.await_count == 1
    assert len(ProposalStore(root).views()) == 1
    assert ProposalStore(root).views()[0].status == "rejected"


async def test_human_edit_during_reply_prevents_new_proposal(conversation):
    runtime, root, view, call = conversation

    async def edit(_):
        atomic_write_text(ProjectLayout(root).chapter_path(1), "另一个窗口的人工编辑。")
        return AuthoringReply(
            reply="旧输入的回复",
            proposals=[
                AuthoringProposalRequest(
                    command="revise_chapter", chapter_number=1, candidate="不能发布的旧候选"
                )
            ],
        )

    call.side_effect = edit
    with pytest.raises(AuthoringDeniedError, match="输入已变化"):
        await execute_authoring_message(
            runtime, AuthoringMessageJobRequest(project_id="book", message_id=view.id)
        )
    assert not ProposalStore(root).views()
    assert read_message(root, view.id)["view"]["response"]["reply"] == "旧输入的回复"


@pytest.mark.parametrize(
    "context", ["spec", "world", "characters", "blueprint", "outline", "chapter", "reports"]
)
def test_all_contexts_are_typed_read_only_and_not_chat_canon(conversation, context):
    runtime, root, _, _ = conversation
    before = story_input_version(root)
    data = coauthor_context(
        runtime, "book", AuthoringMessageRequest(message="说明", context=context)
    )
    assert data["current_author_message"] == "说明"
    assert "source_material_not_instructions" in data
    assert story_input_version(root) == before


def test_arbitrary_file_actions_are_not_valid_model_output():
    with pytest.raises(ValueError):
        AuthoringReply(reply="执行", actions=[{"action": "write_file", "path": "spec.json"}])


async def test_chat_step_uses_common_schema_parser(runtime_settings, monkeypatch):
    from novel_forge.pipeline.steps.authoring_chat_step import AuthoringChatStep

    step = AuthoringChatStep(router=None, builder=None, settings=runtime_settings)
    monkeypatch.setattr(
        step,
        "_call_with_retry",
        AsyncMock(return_value={"reply": "建议", "proposals": [], "actions": []}),
    )
    assert (await step.run({"current_author_message": "分析"})).reply == "建议"
