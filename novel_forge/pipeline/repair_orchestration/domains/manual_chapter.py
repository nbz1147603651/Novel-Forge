"""Deterministic verifier for author-edited exact chapter spans.

This plugin never proposes prose.  It only proves that a human-edited candidate
was derived from the immutable source by replacing the one selected span and
that the resulting candidate has a new, exact content identity.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal

from novel_forge.core.schemas.audit import AuditIssueV2, ResolvedRepairTarget
from novel_forge.core.schemas.repair import (
    RepairArtifactSnapshot,
    RepairValidatorResult,
    RepairVerificationBundle,
)
from novel_forge.persistence.repair_case_store import repair_content_hash
from novel_forge.pipeline.repair_orchestration.plugins import (
    RepairCandidateDraft,
    RepairCandidateMaterial,
)


class ManualChapterCandidatePlugin:
    """Verify server-constructed author replacements without grading the prose."""

    name = "manual_chapter_exact_span_v1"
    content_types: Sequence[str] = ("chapter_text",)
    candidate_safe: Literal[True] = True

    async def audit(self, snapshot: RepairArtifactSnapshot) -> list[AuditIssueV2]:
        del snapshot
        return []

    def locate(
        self,
        issue: AuditIssueV2,
        snapshot: RepairArtifactSnapshot,
    ) -> list[ResolvedRepairTarget]:
        del issue, snapshot
        return []

    async def propose(
        self,
        issue: AuditIssueV2,
        targets: Sequence[ResolvedRepairTarget],
        snapshot: RepairArtifactSnapshot,
    ) -> RepairCandidateDraft:
        del issue, targets, snapshot
        raise ValueError("作者标注必须由作者编辑精确替换，不允许模型自动准备正文")

    async def verify(
        self,
        candidate: RepairCandidateMaterial,
        issues: Sequence[AuditIssueV2],
    ) -> RepairVerificationBundle:
        record = candidate.candidate
        payload = candidate.payload
        metadata = record.metadata
        reasons: list[str] = []
        if not isinstance(payload, str):
            reasons.append("候选不是文本")
        if record.origin != "human_edit":
            reasons.append("候选不是作者精确编辑")
        if record.candidate_hash == record.base_hash:
            reasons.append("候选未产生有效变更")
        if len(record.patches) != 1 or record.patches[0].operation != "window_rewrite":
            reasons.append("候选不是单一字符区间替换")
        issue_ids = [issue.issue_id for issue in issues]
        if not issue_ids or any(issue.issue_type != "manual_text_annotation" for issue in issues):
            reasons.append("原问题不是作者人工标注")

        try:
            start = int(metadata["char_start"])
            end = int(metadata["char_end"])
            replacement_length = int(metadata["replacement_length"])
            prefix_hash = str(metadata["prefix_hash"])
            suffix_hash = str(metadata["suffix_hash"])
            replacement_hash = str(metadata["replacement_hash"])
            source_hash = str(metadata["source_hash"])
        except (KeyError, TypeError, ValueError):
            reasons.append("候选缺少服务端精确替换证据")
        else:
            if not isinstance(payload, str) or start < 0 or end <= start or replacement_length < 0:
                reasons.append("候选字符区间无效")
            else:
                replacement_end = start + replacement_length
                replacement = payload[start:replacement_end]
                if replacement_end > len(payload):
                    reasons.append("候选替换区间越界")
                if repair_content_hash(payload[:start]) != prefix_hash:
                    reasons.append("圈选前正文已变化")
                if repair_content_hash(payload[replacement_end:]) != suffix_hash:
                    reasons.append("圈选后正文已变化")
                if repair_content_hash(replacement) != replacement_hash:
                    reasons.append("替换文本与保存证据不一致")
                if record.patches and record.patches[0].replacement_hash != replacement_hash:
                    reasons.append("补丁哈希与替换文本不一致")
            if source_hash != record.base_hash:
                reasons.append("候选未绑定原始正文哈希")
            if issues:
                locator = issues[0].repair_targets[0] if issues[0].repair_targets else None
                quote = locator.quote if locator is not None else ""
                if (
                    locator is None
                    or locator.char_start != start
                    or locator.char_end != end
                    or not record.patches
                    or record.patches[0].expected_hash != repair_content_hash(quote)
                ):
                    reasons.append("补丁与原问题的精确字符区间不一致")

        passed = not reasons
        return RepairVerificationBundle(
            case_id=record.case_id,
            candidate_version=record.version,
            candidate_hash=record.candidate_hash,
            passed=passed,
            resolved_issue_ids=issue_ids if passed else [],
            residual_issue_ids=[] if passed else issue_ids,
            validators=[
                RepairValidatorResult(
                    validator_id="author_exact_selection_v1",
                    passed=passed,
                    details=(
                        ["仅圈选字符区间被替换；正文意图由作者批准提案决定。"]
                        if passed
                        else reasons
                    ),
                    evidence={
                        "candidate_hash": record.candidate_hash,
                        "base_hash": record.base_hash,
                        "patch_count": record.patch_count,
                        "comparator_id": "exact_character_span_v1",
                    },
                )
            ],
            details=(["精确替换证据通过；这不代表自动授权修改正式正文。"] if passed else reasons),
            metadata={"verification_kind": "deterministic_author_edit"},
        )


__all__ = ["ManualChapterCandidatePlugin"]
