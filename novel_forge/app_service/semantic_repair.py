"""Bridge verified semantic RepairCases to existing authoring proposals.

The bridge prepares ``PlanningRevision`` candidates and reconciles their
existing publication receipts.  It never publishes a RepairCase directly.
"""

from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace
from typing import Any

from novel_forge.core.authoring import AuthoringProposalRequest, AuthoringProposalView
from novel_forge.core.schemas.outline import NarrativeBlueprint, StoryOutline
from novel_forge.core.schemas.repair import RepairPublishReceipt
from novel_forge.narrative_state.schemas import ChapterContract
from novel_forge.persistence.authoring_proposals import ProposalStore
from novel_forge.persistence.authoring_store import (
    AuthoringDeniedError,
    AuthoringStore,
    story_input_version,
)
from novel_forge.persistence.filesystem import FileSystemStorage, atomic_write_json
from novel_forge.persistence.models import ProjectLayout
from novel_forge.persistence.planning_revision import (
    PlanningRevision,
    planning_publication_receipt,
)
from novel_forge.persistence.repair_case_store import (
    RepairCaseConflictError,
    RepairCaseStore,
    repair_content_hash,
)
from novel_forge.pipeline.long.services.init.init_semantic_repair_cases import (
    SEMANTIC_REPAIR_CONTENT_TYPE,
)
from novel_forge.workspace.authoring_proposals import create_proposal
from novel_forge.workspace.helpers.execution_runners import _project_lock


async def request_semantic_repair_approval(
    storage: FileSystemStorage,
    project_id: str,
    case_id: str,
    *,
    expected_case_version: int,
    expected_candidate_version: int,
) -> AuthoringProposalView:
    """Prepare and bind an existing PlanningRevision proposal to one case."""

    root = storage.existing_project_dir(project_id)
    repair_store = RepairCaseStore(root)
    case = repair_store.load_case(case_id)
    if case is None or case.content_type != SEMANTIC_REPAIR_CONTENT_TYPE:
        raise FileNotFoundError(f"semantic repair case not found: {case_id}")
    candidate = case.latest_candidate
    if (
        case.version != expected_case_version
        or candidate is None
        or candidate.version != expected_candidate_version
        or case.verification is None
        or not case.verification.passed
        or case.authority != "proposal_required"
    ):
        raise RepairCaseConflictError("语义修复案例或已验证候选已变化")
    policy = AuthoringStore(root).policy()
    current_input = story_input_version(root)
    if (
        policy is None
        or policy.version != case.policy_version
        or current_input != case.input_version
        or policy.stopped
    ):
        repair_store.append_event(
            case.case_id,
            "case_stale",
            {"reason": "story input or authoring policy changed before proposal"},
            expected_version=case.version,
        )
        raise AuthoringDeniedError("故事输入或授权已变化；旧候选不能提交")
    candidate_payload = repair_store.get_blob(candidate.blob_hash)
    if not isinstance(candidate_payload, dict) or repair_content_hash(candidate_payload) != (
        candidate.candidate_hash
    ):
        raise RepairCaseConflictError("语义修复候选内容与哈希不一致")
    layout = ProjectLayout(root)
    artifact = str(case.metadata.get("repair_artifact") or case.artifact_id)
    target_path = _artifact_path(layout, artifact)
    current_payload = storage.load_json(target_path)
    if repair_content_hash(current_payload) != str(case.metadata.get("repair_artifact_hash") or ""):
        repair_store.append_event(
            case.case_id,
            "case_stale",
            {"reason": "semantic repair target changed before proposal"},
            expected_version=case.version,
        )
        raise RepairCaseConflictError("待修订来源已变化，请重新编译语义一致性")

    proposal_id = hashlib.sha256(
        f"semantic:{case.case_id}:{candidate.version}:{candidate.candidate_hash}".encode()
    ).hexdigest()[:32]
    runtime = SimpleNamespace(storage=storage)
    if artifact == "blueprint":
        _validate_exact_model_payload(NarrativeBlueprint, candidate_payload, artifact)
        from novel_forge.app_service.authoring_foundation import create_authoring_proposal

        proposal = await create_authoring_proposal(
            runtime,
            project_id,
            AuthoringProposalRequest(
                command="revise_foundation",
                chapter_number=policy.start_chapter,
                title=f"语义一致性：{case.title}",
                candidate=json.dumps(candidate_payload, ensure_ascii=False),
                expected_input_version=case.input_version,
                foundation_artifact="blueprint",
                evidence=_proposal_evidence(case.case_id, candidate.candidate_hash),
            ),
            proposal_id=proposal_id,
        )
    else:
        _validate_planning_payload(artifact, candidate_payload)
        async with _project_lock(runtime, project_id):
            if story_input_version(root) != case.input_version or repair_content_hash(
                storage.load_json(target_path)
            ) != str(case.metadata.get("repair_artifact_hash") or ""):
                latest = repair_store.load_case(case.case_id)
                if latest is not None:
                    repair_store.append_event(
                        case.case_id,
                        "case_stale",
                        {"reason": "semantic repair target changed during proposal preparation"},
                        expected_version=latest.version,
                    )
                raise RepairCaseConflictError("待修订来源已变化，请重新编译语义一致性")
            revision = PlanningRevision(root, project_id, purpose="planning")
            atomic_write_json(
                _artifact_path(ProjectLayout(revision.project), artifact),
                candidate_payload,
            )
            affected = case.chapter_numbers or _all_outline_chapters(layout, storage)
            revision.mark_validated(affected)
        proposal = await create_proposal(
            runtime,
            project_id,
            AuthoringProposalRequest(
                command="publish_planning",
                chapter_number=policy.start_chapter,
                title=f"语义一致性：{case.title}",
                revision_id=revision.revision_id,
                expected_input_version=case.input_version,
                evidence=_proposal_evidence(case.case_id, candidate.candidate_hash),
            ),
            proposal_id=proposal_id,
        )
    proposal_store = ProposalStore(root)
    with proposal_store.authority.lock():
        data = proposal_store.read(proposal.id)
        view = AuthoringProposalView.model_validate(data["view"])
        if (
            view.status != "pending"
            or view.input_version != case.input_version
            or view.policy_version != case.policy_version
        ):
            proposal_store.revoke(data)
            view.status = "stale"
            view.application_result = {
                **view.application_result,
                "status": "authority_changed",
                "message": "来源或授权在提案准备期间改变；旧提案已撤销。",
            }
            data["view"] = view.model_dump(mode="json")
            proposal_store.write(data)
            raise RepairCaseConflictError("语义修复提案未绑定当前权限版本")
        data["repair_case_id"] = case.case_id
        data["semantic_repair"] = True
        view.application_result = {
            **view.application_result,
            "approval_flow": "semantic_repair",
            "repair_case_id": case.case_id,
            "message": "批准只授权既有 PlanningRevision 发布；RepairCase 仅核对回执。",
        }
        data["view"] = view.model_dump(mode="json")
        proposal_store.write(data)
    try:
        repair_store.append_event(
            case.case_id,
            "approval_requested",
            {"proposal_id": proposal.id},
            expected_version=case.version,
            actor="author",
        )
    except Exception:
        with proposal_store.authority.lock():
            data = proposal_store.read(proposal.id)
            proposal_store.revoke(data)
            view = AuthoringProposalView.model_validate(data["view"])
            view.status = "stale"
            view.application_result = {
                **view.application_result,
                "status": "orphaned",
                "message": "RepairCase 在提案绑定前变化；旧提案已撤销。",
            }
            data["view"] = view.model_dump(mode="json")
            proposal_store.write(data)
        raise
    return proposal


def reconcile_semantic_repair_receipt(
    project_root: Any,
    proposal_id: str,
) -> None:
    """Mirror a committed PlanningRevision receipt into linked repair evidence."""

    root = project_root
    proposal_store = ProposalStore(root)
    data = proposal_store.read(proposal_id)
    case_id = str(data.get("repair_case_id") or "")
    if not case_id or not bool(data.get("semantic_repair")):
        return
    view = AuthoringProposalView.model_validate(data["view"])
    if view.status != "applied":
        return
    command = data.get("command") or {}
    revision_id = str(command.get("revision_id") or "")
    receipt = planning_publication_receipt(root, revision_id)
    if receipt is None or receipt.get("candidate_version") != view.candidate_version:
        return
    repair_store = RepairCaseStore(root)
    case = repair_store.load_case(case_id)
    if case is None or case.latest_candidate is None:
        return
    if case.receipt is not None and case.receipt.committed:
        return
    candidate = case.latest_candidate
    layout = ProjectLayout(root)
    artifact = str(case.metadata.get("repair_artifact") or case.artifact_id)
    current_payload = json.loads(_artifact_path(layout, artifact).read_text(encoding="utf-8"))
    if not isinstance(current_payload, dict):
        raise RepairCaseConflictError("已发布规划内容不是预期对象")
    if repair_content_hash(current_payload) != candidate.candidate_hash:
        raise RepairCaseConflictError("已发布规划内容与语义修复候选不一致")
    receipt_id = hashlib.sha256(
        f"semantic-receipt:{case.case_id}:{candidate.version}:{proposal_id}".encode()
    ).hexdigest()[:32]
    prepared = RepairPublishReceipt(
        receipt_id=receipt_id,
        case_id=case.case_id,
        candidate_version=candidate.version,
        authority=case.authority,
        target=case.artifact_id,
        before_hash=candidate.base_hash,
        after_hash=candidate.candidate_hash,
        input_version=case.input_version,
        policy_version=case.policy_version,
        approval_id=str(data.get("approval_id") or ""),
        proposal_id=proposal_id,
        transaction_status="prepared",
        metadata={"planning_revision_id": revision_id, "external_publisher": True},
    )
    case = repair_store.append_event(
        case.case_id,
        "publication_prepared",
        {"receipt": prepared.model_dump(mode="json")},
        expected_version=case.version,
    )
    committed = prepared.model_copy(
        update={
            "transaction_status": "committed",
            "committed": True,
            "message": "Existing PlanningRevision publication receipt reconciled.",
        }
    )
    repair_store.append_event(
        case.case_id,
        "publication_committed",
        {"receipt": committed.model_dump(mode="json")},
        expected_version=case.version,
    )


def _artifact_path(layout: ProjectLayout, artifact: str) -> Any:
    if artifact == "blueprint":
        return layout.blueprint_path
    if artifact == "outline":
        return layout.outline_path
    if artifact == "chapter_contracts":
        return layout.plans_dir / "chapter_contracts.json"
    raise ValueError(f"unsupported semantic planning artifact: {artifact}")


def _validate_planning_payload(artifact: str, payload: dict[str, Any]) -> None:
    if artifact == "outline":
        _validate_exact_model_payload(StoryOutline, payload, artifact)
        return
    if artifact == "chapter_contracts":
        values = payload.get("chapter_contracts")
        if not isinstance(values, list) or not values:
            raise ValueError("章节契约候选必须包含 chapter_contracts")
        for item in values:
            ChapterContract.model_validate(item)
        return
    raise ValueError(f"unsupported semantic planning artifact: {artifact}")


def _all_outline_chapters(layout: ProjectLayout, storage: FileSystemStorage) -> list[int]:
    outline = storage.load_json(layout.outline_path)
    chapters: list[int] = []
    for item in outline.get("chapters", []) or []:
        if not isinstance(item, dict):
            continue
        try:
            chapter = int(item.get("chapter_number") or 0)
        except (TypeError, ValueError):
            continue
        if chapter > 0:
            chapters.append(chapter)
    return sorted(set(chapters))


def _validate_exact_model_payload(model: Any, payload: dict[str, Any], artifact: str) -> None:
    normalized = model.model_validate(payload).model_dump(mode="json")
    if normalized != payload:
        raise ValueError(f"{artifact} 候选必须是已复验的完整标准化对象")


def _proposal_evidence(case_id: str, candidate_hash: str) -> list[str]:
    return [
        f"repair_case:{case_id}",
        f"candidate_sha256:{candidate_hash}",
        "同一未改基线的模型候选已重新完成 claims 抽取与语义裁决",
    ]


__all__ = [
    "reconcile_semantic_repair_receipt",
    "request_semantic_repair_approval",
]
