"""Whitelisted domain proposals; model output never becomes a file write path."""

from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

from novel_forge.core.authoring import (
    AuthoringProposalDecision,
    AuthoringProposalRequest,
    AuthoringProposalView,
)
from novel_forge.persistence.authoring_proposals import ProposalStore
from novel_forge.persistence.authoring_store import (
    AuthoringDeniedError,
    content_version,
    story_input_version,
)
from novel_forge.persistence.models import ProjectLayout
from novel_forge.persistence.planning_revision import PlanningRevision
from novel_forge.workspace.authoring_control import checkpoint_action, checkpoint_command_version
from novel_forge.workspace.helpers.execution_runners import _project_lock


def _candidate_version(root: Any, data: dict[str, Any]) -> str:
    command = data["command"]
    chapter = data["view"]["chapter_number"]
    if command["kind"] == "checkpoint":
        return checkpoint_command_version(root, chapter, command["option_id"], command["notes"])
    if command["kind"] in {"publish_planning", "revise_foundation"}:
        revision = PlanningRevision.load(root, data["view"]["project_id"], command["revision_id"])
        return content_version(revision.changes())
    return content_version(
        {"text": data["view"]["candidate"], "chapter": chapter, "scope": "forward_only"}
    )


async def create_proposal(
    runtime: Any, project_id: str, request: AuthoringProposalRequest, *, proposal_id: str = ""
) -> AuthoringProposalView:
    async with _project_lock(runtime, project_id):
        return create_proposal_under_lock(runtime, project_id, request, proposal_id=proposal_id)


def create_proposal_under_lock(
    runtime: Any, project_id: str, request: AuthoringProposalRequest, *, proposal_id: str = ""
) -> AuthoringProposalView:
    """Shared by synchronous domain editors; caller holds the project lock."""
    root = runtime.storage.existing_project_dir(project_id)
    store = ProposalStore(root)
    store.authority.require_enabled()
    if proposal_id:
        try:
            existing = store.read(proposal_id)
        except FileNotFoundError:
            pass
        else:
            # Internal stable message/action key, not accepted from model output.
            # Replay must not resurrect a rejected or edited proposal.
            return AuthoringProposalView.model_validate(existing["view"])
    policy = store.authority.policy()
    if policy is None:
        raise AuthoringDeniedError("请先明确选择协作策略；不会自动升级旧项目权限")
    if not policy.start_chapter <= request.chapter_number <= policy.end_chapter:
        raise AuthoringDeniedError("提案超出已选择章段")
    layout = ProjectLayout(root)
    if request.expected_input_version and request.expected_input_version != story_input_version(
        root
    ):
        raise AuthoringDeniedError("生成提案期间故事输入已改变；未将旧建议绑定到新作品")
    chapter_path = layout.chapter_path(request.chapter_number)
    original = chapter_path.read_text(encoding="utf-8") if chapter_path.exists() else ""
    candidate = request.candidate
    if request.command == "restore_chapter":
        from novel_forge.persistence.final_revision_journal import revision_text

        candidate = revision_text(
            layout, request.chapter_number, request.revision_id, request.revision_side
        )
    action = "revise"
    command: dict[str, Any] = {"kind": request.command}
    affected = [request.chapter_number]
    risks = ["正式修改前保存旧版本；已有报告和下游状态可能过期"]
    if request.command == "checkpoint":
        checkpoint = runtime.storage.load_json(
            layout.chapter_checkpoint_path(request.chapter_number)
        )
        if request.option_id not in {item["option_id"] for item in checkpoint.get("options", [])}:
            raise ValueError("选项不属于当前检查点")
        session = runtime.storage.load_json(layout.chapter_session_path(request.chapter_number))
        action = checkpoint_action(request.option_id)
        if action not in {"generate", "archive"}:
            raise ValueError("该选项涉及额外重大变更，请使用专项修订提案")
        command.update(
            checkpoint_id=checkpoint["checkpoint_id"],
            option_id=request.option_id,
            notes=request.notes,
        )
        candidate = str(
            session.get("pending_result", {}).get("current_text")
            or json.dumps(session.get("plan", checkpoint), ensure_ascii=False, indent=2)
        )
        if action == "archive":
            risks = ["仅验收此版正文；终结检查若改变正文，须再次验收。事实与一致性门禁仍生效"]
        else:
            risks = ["批准方案仅授权生成与有限修复，不授权正文归档或下一章正史"]
    elif request.command == "publish_planning":
        from novel_forge.workspace.planning_jobs import planning_job_view

        planning = planning_job_view(root)
        revision = PlanningRevision.load(root, project_id, request.revision_id)
        proof = revision.validation()
        matching_horizon = (
            planning.get("status") == "candidate"
            and planning.get("result", {}).get("revision_id") == request.revision_id
        )
        if not matching_horizon and proof is None:
            raise ValueError("只能批准已经完成严格同步的规划候选")
        proof = proof or {
            "action": "publish_planning",
            "candidate_version": planning["result"].get("candidate_version"),
        }
        command["revision_id"] = request.revision_id
        action = proof["action"]
        changes = revision.changes()
        if content_version(changes) != proof.get("candidate_version"):
            raise ValueError("规划候选验证版本不匹配；请重新准备候选")
        command["validated_version"] = proof["candidate_version"]
        command["planning_request_id"] = planning["request_id"] if matching_horizon else ""
        candidate = json.dumps(changes, ensure_ascii=False, indent=2)
        original = json.dumps(
            {name: revision.before.get(name) for name in changes}, ensure_ascii=False, indent=2
        )
        outline = runtime.storage.load_json(layout.outline_path)
        affected = proof.get("affected_chapters") or list(
            range(request.chapter_number, int(outline["total_chapters"]) + 1)
        )
        risks = ["规划发布采用输入版本检查；批准只接受当前候选，不解除其他锁定或开放后续写入"]
        if action == "extend":
            risks.append("专项变更全书目标及旧终章定位；已归档正文保留，相关规划/报告可能过期")
            view_title = "延长全书专项批准"
        else:
            view_title = request.title
    elif request.command == "revise_foundation":
        revision = PlanningRevision.load(root, project_id, request.revision_id)
        proof = revision.validation()
        if (
            revision.purpose != "foundation"
            or not proof
            or proof.get("foundation_artifact") != request.foundation_artifact
        ):
            raise ValueError("设定候选尚未通过领域校验与来源同步")
        command.update(
            revision_id=revision.revision_id,
            validated_version=proof["candidate_version"],
            foundation_artifact=request.foundation_artifact,
        )
        changes = revision.changes()
        original = json.dumps(
            {name: revision.before.get(name) for name in changes}, ensure_ascii=False, indent=2
        )
        candidate = json.dumps(changes, ensure_ascii=False, indent=2)
        affected = proof["affected_chapters"]
        risks = [
            "仅向后更新人物声音/画像，不要求改写既有正文；后续契约按新设定准备"
            if proof.get("revision_scope") == "forward_only"
            else "全书设定专项修改；不重写已归档正文，已有契约、报告与后续状态须重验",
            "仅批准所展示的领域差异；原始作者要求与其他锁定继续保留，冲突不会被自动消除",
            "人物或世界规则投影未完成时暂停后续生成，可恢复本次已提交结果",
        ]
    elif not original or not candidate.strip():
        raise ValueError("正文修订必须提供已有终稿和非空候选")
    if request.command in {"revise_chapter", "restore_chapter"}:
        command.update(revision_id=request.revision_id, revision_side=request.revision_side)
        affected = sorted(
            {
                request.chapter_number,
                *(
                    int(p.stem.split("_")[-1])
                    for p in layout.chapters_dir.glob("chapter_*.md")
                    if p.stem.split("_")[-1].isdigit()
                    and int(p.stem.split("_")[-1]) >= request.chapter_number
                ),
            }
        )
    view = AuthoringProposalView(
        id=proposal_id or uuid4().hex,
        project_id=project_id,
        action=action,
        chapter_number=request.chapter_number,
        policy_version=policy.version,
        input_version=story_input_version(root),
        candidate_version="",
        title=view_title if request.command == "publish_planning" else request.title,
        original=original,
        candidate=candidate,
        evidence=request.evidence,
        affected_chapters=affected,
        risks=risks,
    )
    data = {"view": view.model_dump(mode="json"), "command": command}
    if request.command in {"revise_chapter", "restore_chapter"}:
        view.editable = True
        view.lock_conflicts = ["涉及已归档正文；批准仅限此候选的专项修订，不解除其他作者锁定"]
        view.cost_hint = "应用此正文不调用模型；后续重验如需模型，仍受既有预算约束"
    elif request.command == "publish_planning":
        from novel_forge.pipeline.long.services.future_planning import protected_chapters

        policy_path = layout.plans_dir / "planning_policy.json"
        planning_policy = runtime.storage.load_json(policy_path) if policy_path.is_file() else {}
        locked = protected_chapters(root, planning_policy).intersection(affected)
        if locked:
            view.lock_conflicts = [
                "涉及已归档或人工锁定章的规划："
                + "、".join(map(str, sorted(locked)))
                + "；专项批准只适用于此候选，正文不改，其他锁定继续有效"
            ]
        view.cost_hint = "候选已生成；发布本身不调用模型，后续重新验证仍受原预算约束"
    elif request.command == "revise_foundation":
        view.lock_conflicts = [
            "涉及全书设定及可能的结局、人物命运、世界规则；请核对全部差异与原作者锁定"
        ]
        view.cost_hint = "应用此候选不调用模型；后续重新验证仍受既有预算约束"
    view.candidate_version = _candidate_version(root, data)
    data["view"] = view.model_dump(mode="json")
    with store.authority.lock():
        store.write(data)
    return view


def decide_proposal(
    root: Any, proposal_id: str, request: AuthoringProposalDecision
) -> AuthoringProposalView:
    store = ProposalStore(root)
    with store.authority.lock():
        data = store.read(proposal_id)
        view = AuthoringProposalView.model_validate(data["view"])
        if data["command"]["kind"] in {"publish_planning", "revise_foundation"}:
            from novel_forge.persistence.planning_revision import planning_publication_receipt

            if planning_publication_receipt(root, data["command"]["revision_id"]):
                raise AuthoringDeniedError("此版本已正式提交，请核验已提交结果；撤回须创建新修订")
        if (view.candidate_version, view.input_version, view.policy_version) != (
            request.candidate_version,
            request.input_version,
            request.policy_version,
        ):
            raise AuthoringDeniedError("显示版本与提案不一致，请刷新后再决定")
        if data.get("repair_case_id") and request.decision == "edit":
            raise AuthoringDeniedError("修复提案必须回到精确字符区间编辑；保存后须重新复验和提案")
        if (
            view.status in {"applied", "rejected"}
            or view.application_result.get("task_id")
            or view.application_result.get("status") == "applying"
        ):
            raise AuthoringDeniedError("提案已经处理；运行中的任务请使用暂停入口")
        if request.decision in {"reject", "defer"}:
            store.revoke(data)
            view.status = "rejected" if request.decision == "reject" else "deferred"
        else:
            store.assert_current(view)
            if _candidate_version(root, data) != view.candidate_version:
                raise AuthoringDeniedError("候选内容已变化，旧批准不可复用")
            if request.decision == "edit":
                if (
                    data["command"]["kind"] not in {"revise_chapter", "restore_chapter"}
                    or not request.edited_candidate
                    or not request.edited_candidate.strip()
                ):
                    raise ValueError("此提案不能直接编辑正文；请通过相应领域编辑器修改后重新提案")
                store.revoke(data)
                view.candidate = request.edited_candidate
                data["view"] = view.model_dump(mode="json")
                view.candidate_version = _candidate_version(root, data)
                view.status = "pending"
            else:
                data["approval_id"] = store.authority.approve_locked(
                    action=view.action,
                    chapter=view.chapter_number,
                    candidate_version=view.candidate_version,
                    input_version=view.input_version,
                    policy_version=view.policy_version,
                )
                view.status = "approved"
        data["view"] = view.model_dump(mode="json")
        store.write(data)
    return view
