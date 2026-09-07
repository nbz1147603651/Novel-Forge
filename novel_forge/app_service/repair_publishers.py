"""Guarded repair publishers backed by existing authoring transactions."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from novel_forge.app_service.authoring_proposals import apply_proposal
from novel_forge.core.authoring import AuthoringProposalView
from novel_forge.core.schemas.repair import RepairCase, RepairPublishReceipt
from novel_forge.persistence.authoring_proposals import ProposalStore
from novel_forge.persistence.models import ProjectLayout
from novel_forge.persistence.repair_case_store import repair_content_hash
from novel_forge.pipeline.repair_orchestration.plugins import (
    RepairAuthorityContext,
    RepairCandidateMaterial,
    RepairPublicationError,
)


class AuthoringProposalChapterPublisher:
    """Apply one approved chapter candidate through the manual-revision journal."""

    name = "authoring_proposal_chapter_publisher_v1"
    content_types: Sequence[str] = ("chapter_text",)

    def __init__(self, runtime: Any, service: Any = None) -> None:
        self.runtime = runtime
        self.service = service

    def _proposal(
        self,
        case: RepairCase,
        candidate: RepairCandidateMaterial,
        authority: RepairAuthorityContext,
    ) -> tuple[ProposalStore, dict[str, Any], AuthoringProposalView]:
        if not authority.proposal_id or not authority.approval_id:
            raise RepairPublicationError("正文发布需要绑定已批准的精确提案")
        root = self.runtime.storage.existing_project_dir(case.project_id)
        store = ProposalStore(root)
        try:
            data = store.read(authority.proposal_id)
        except FileNotFoundError as exc:
            raise RepairPublicationError("修订提案不存在") from exc
        view = AuthoringProposalView.model_validate(data["view"])
        if (
            view.id != case.proposal_id
            or view.id != authority.proposal_id
            or data.get("command", {}).get("kind") != "revise_chapter"
            or view.chapter_number not in case.chapter_numbers
            or view.candidate != candidate.payload
            or data.get("approval_id", "") != authority.approval_id
        ):
            raise RepairPublicationError("提案与当前修复候选或批准不一致")
        if view.status not in {"approved", "applied"}:
            raise RepairPublicationError("修订提案尚未批准")
        return store, data, view

    async def publish(
        self,
        case: RepairCase,
        candidate: RepairCandidateMaterial,
        authority: RepairAuthorityContext,
    ) -> RepairPublishReceipt:
        _store, _data, view = self._proposal(case, candidate, authority)
        applied = await apply_proposal(
            self.runtime,
            self.service,
            case.project_id,
            view.id,
        )
        if applied.status != "applied":
            raise RepairPublicationError("修订提案未返回已应用状态")
        layout = ProjectLayout(self.runtime.storage.existing_project_dir(case.project_id))
        chapter = layout.chapter_path(view.chapter_number)
        if not chapter.is_file() or repair_content_hash(chapter.read_text(encoding="utf-8")) != (
            candidate.candidate.candidate_hash
        ):
            raise RepairPublicationError("正式正文哈希与已批准候选不一致")
        receipt = case.receipt
        if receipt is None or receipt.transaction_status != "prepared":
            raise RepairPublicationError("发布器未收到预写回执")
        return receipt.model_copy(
            update={
                "transaction_status": "committed",
                "committed": True,
                "message": "已通过已批准的正文修订提案原子应用",
                "metadata": {
                    **receipt.metadata,
                    "proposal_candidate_version": view.candidate_version,
                    "manual_revision": applied.application_result,
                },
            }
        )

    async def recover(
        self,
        receipt: RepairPublishReceipt,
        authority: RepairAuthorityContext,
    ) -> RepairPublishReceipt:
        # Recovery is reached through RepairCommands, which binds the exact case
        # and candidate before invoking this publisher.  ``apply_proposal`` first
        # reconciles the existing manual-revision journal and never rewrites a
        # later chapter version.
        if (
            authority.receipt_id != receipt.receipt_id
            or authority.proposal_id != receipt.proposal_id
            or authority.approval_id != receipt.approval_id
            or authority.target != receipt.target
        ):
            raise RepairPublicationError("回执恢复身份与已保存发布意图不一致")
        project_id = str(receipt.metadata.get("project_id") or "")
        chapter_number = int(receipt.metadata.get("chapter_number") or 0)
        if not project_id or chapter_number < 1 or not receipt.proposal_id:
            raise RepairPublicationError("发布回执缺少可核对的作品或章节身份")
        root = self.runtime.storage.existing_project_dir(project_id)
        proposal_data = ProposalStore(root).read(receipt.proposal_id)
        proposal = AuthoringProposalView.model_validate(proposal_data["view"])
        chapter = ProjectLayout(root).chapter_path(chapter_number)
        current_hash = (
            repair_content_hash(chapter.read_text(encoding="utf-8")) if chapter.is_file() else ""
        )
        if (
            proposal_data.get("repair_case_id") != receipt.case_id
            or proposal_data.get("command", {}).get("kind") != "revise_chapter"
            or proposal_data.get("approval_id", "") != receipt.approval_id
            or proposal.chapter_number != chapter_number
            or repair_content_hash(proposal.candidate) != receipt.after_hash
            or proposal.status not in {"approved", "applied"}
            or current_hash not in {receipt.before_hash, receipt.after_hash}
        ):
            raise RepairPublicationError("回执、提案或当前正文不一致；未重放旧内容")
        applied = await apply_proposal(
            self.runtime,
            self.service,
            project_id,
            receipt.proposal_id,
        )
        if (
            applied.status != "applied"
            or not chapter.is_file()
            or repair_content_hash(chapter.read_text(encoding="utf-8")) != receipt.after_hash
        ):
            raise RepairPublicationError("回执核对未确认已批准候选；未重放旧内容")
        return receipt.model_copy(
            update={
                "transaction_status": "reconciled",
                "committed": True,
                "recovered": True,
                "message": "已核对既有正文修订回执，未重放候选内容",
                "metadata": {
                    **receipt.metadata,
                    "manual_revision": applied.application_result,
                },
            }
        )


__all__ = ["AuthoringProposalChapterPublisher"]
