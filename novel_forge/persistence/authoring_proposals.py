"""Durable, non-canon proposal evidence with candidate/input/version CAS."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

from novel_forge.core.authoring import AuthoringProposalView
from novel_forge.persistence.authoring_store import (
    AuthoringDeniedError,
    AuthoringStore,
    story_input_version,
)
from novel_forge.persistence.filesystem import atomic_write_json


class ProposalStore:
    def __init__(self, root: Path) -> None:
        self.authority = AuthoringStore(root)
        self.root = root
        self.directory = self.authority.directory / "proposals"

    def path(self, proposal_id: str) -> Path:
        if len(proposal_id) != 32 or any(c not in "0123456789abcdef" for c in proposal_id):
            raise ValueError("无效提案编号")
        return self.directory / f"{proposal_id}.json"

    def read(self, proposal_id: str) -> dict[str, Any]:
        return cast(dict[str, Any], json.loads(self.path(proposal_id).read_text(encoding="utf-8")))

    def write(self, data: dict[str, Any]) -> None:
        view = AuthoringProposalView.model_validate(data["view"])
        atomic_write_json(self.path(view.id), data)

    def views(self) -> list[AuthoringProposalView]:
        result = []
        for path in sorted(
            self.directory.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True
        ):
            data = self.read(path.stem)
            view = AuthoringProposalView.model_validate(data["view"])
            if view.status == "approved" and data["command"]["kind"] in {
                "publish_planning",
                "revise_foundation",
            }:
                from novel_forge.persistence.planning_revision import planning_publication_receipt

                publication = planning_publication_receipt(
                    self.root, data["command"]["revision_id"]
                )
                if publication and publication["candidate_version"] == view.candidate_version:
                    view.application_result = {
                        **view.application_result,
                        "recoverable_receipt": True,
                    }
            if view.status == "approved" and data["command"]["kind"] in {
                "revise_chapter",
                "restore_chapter",
            }:
                from novel_forge.core.utils.text_hash import source_text_hash
                from novel_forge.persistence.final_revision_journal import revision_for_proposal
                from novel_forge.persistence.models import ProjectLayout

                layout = ProjectLayout(self.root)
                receipt = revision_for_proposal(layout, view.chapter_number, view.id)
                prose = layout.chapter_path(view.chapter_number)
                if receipt and (
                    receipt.get("transaction_status") == "applied"
                    or (
                        receipt.get("final_revision_status")
                        and prose.is_file()
                        and source_text_hash(prose.read_text(encoding="utf-8"))
                        == receipt["current_hash"]
                    )
                ):
                    view.application_result = {
                        **view.application_result,
                        "recoverable_receipt": True,
                    }
            if view.status in {"pending", "approved", "deferred"}:
                policy = self.authority.policy()
                if (
                    policy is None
                    or policy.version != view.policy_version
                    or story_input_version(self.root) != view.input_version
                ):
                    view = view.model_copy(update={"status": "stale"})
            result.append(view)
        return result

    def assert_current(self, view: AuthoringProposalView) -> None:
        policy = self.authority.policy()
        if (
            policy is None
            or policy.version != view.policy_version
            or view.input_version != story_input_version(self.root)
        ):
            raise AuthoringDeniedError("提案或批准已过期；请基于最新作者输入重新提案")
        if policy.stopped:
            raise AuthoringDeniedError("会话已停止；恢复时需重新核验提案")

    def revoke(self, data: dict[str, Any]) -> None:
        approval_id = data.get("approval_id", "")
        if approval_id:
            # Keep evidence; exact-match approval checks reject this tombstone.
            path = self.authority.directory / "approvals" / f"{approval_id}.json"
            old = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
            atomic_write_json(path, {**old, "revoked": True})
