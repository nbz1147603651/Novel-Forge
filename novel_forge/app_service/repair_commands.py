"""Transport-neutral commands for the candidate-first repair workbench."""

from __future__ import annotations

import hashlib
from difflib import SequenceMatcher
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

from novel_forge.app_service.engine_views import engine_capabilities
from novel_forge.app_service.repair_publishers import AuthoringProposalChapterPublisher
from novel_forge.core.authoring import AuthoringProposalRequest, AuthoringProposalView
from novel_forge.core.schemas.audit import AuditIssueV2, ResolvedRepairTarget
from novel_forge.core.schemas.repair import (
    RepairApprovalRequest,
    RepairCandidateEditRequest,
    RepairCase,
    RepairCaseDecisionRequest,
    RepairCaseDetailView,
    RepairManualAnnotationRequest,
    RepairPatchRecord,
    RepairPublishRequest,
    RepairRecoveryRequest,
    RepairSourceView,
    RepairWorkbenchCapabilities,
)
from novel_forge.persistence.authoring_proposals import ProposalStore
from novel_forge.persistence.authoring_store import (
    AuthoringDeniedError,
    AuthoringStore,
    story_input_version,
)
from novel_forge.persistence.filesystem import FileSystemStorage
from novel_forge.persistence.models import ProjectLayout
from novel_forge.persistence.repair_case_store import (
    RepairCaseConflictError,
    RepairCaseStore,
    repair_content_hash,
)
from novel_forge.pipeline.repair_orchestration.domains.manual_chapter import (
    ManualChapterCandidatePlugin,
)
from novel_forge.pipeline.repair_orchestration.orchestrator import RepairOrchestrator
from novel_forge.pipeline.repair_orchestration.plugins import (
    RepairAuthorityContext,
    RepairPluginRegistry,
    RepairPublisherRegistry,
)

_PLUGINS = RepairPluginRegistry()
_PLUGINS.register(ManualChapterCandidatePlugin())
_PUBLISHERS = RepairPublisherRegistry()


def repair_plugin_registry() -> RepairPluginRegistry:
    """Return the process registry populated by domain migration batches."""

    return _PLUGINS


def repair_publisher_registry() -> RepairPublisherRegistry:
    """Return process-scoped publishers that do not require a project runtime."""

    return _PUBLISHERS


class RepairCommands:
    """Path-free read and mutation boundary for repair evidence."""

    def __init__(
        self,
        storage: FileSystemStorage,
        *,
        plugins: RepairPluginRegistry | None = None,
        publishers: RepairPublisherRegistry | None = None,
        service: Any = None,
    ) -> None:
        self.storage = storage
        self.plugins = plugins or repair_plugin_registry()
        if publishers is not None:
            self.publishers = publishers
        else:
            self.publishers = RepairPublisherRegistry()
            self.publishers.register(
                AuthoringProposalChapterPublisher(
                    SimpleNamespace(storage=storage),
                    service,
                )
            )

    def _root(self, project_id: str) -> Path:
        return self.storage.existing_project_dir(project_id)

    def _store(self, project_id: str) -> RepairCaseStore:
        return RepairCaseStore(self._root(project_id))

    def _proposal_view(self, case: RepairCase) -> AuthoringProposalView | None:
        if not case.proposal_id:
            return None
        root = self._root(case.project_id)
        return next(
            (view for view in ProposalStore(root).views() if view.id == case.proposal_id),
            None,
        )

    def source(self, project_id: str, chapter_number: int) -> RepairSourceView:
        """Select the current chapter source without accepting a client path."""

        root = self._root(project_id)
        layout = ProjectLayout(root)
        candidates = [
            ("review", layout.chapter_review_draft_path(chapter_number), "working_candidate"),
            ("final", layout.chapter_path(chapter_number), "official"),
        ]
        draft_dir = layout.chapter_draft_dir(chapter_number)
        if draft_dir.is_dir():
            drafts = sorted(
                (path for path in draft_dir.glob("v*.md") if path.is_file()),
                key=lambda path: (path.stat().st_mtime_ns, path.name),
                reverse=True,
            )
            candidates.extend((f"draft:{path.name}", path, "working_candidate") for path in drafts)
        for artifact_suffix, path, state in candidates:
            if not path.is_file():
                continue
            content = path.read_text(encoding="utf-8")
            source_hash = repair_content_hash(content)
            artifact_id = f"chapter:{chapter_number}:{artifact_suffix}"
            return RepairSourceView(
                project_id=project_id,
                artifact_id=artifact_id,
                chapter_number=chapter_number,
                source_version=f"{artifact_id}:{source_hash[:16]}",
                source_hash=source_hash,
                content=content,
                state=state,
            )
        raise FileNotFoundError(f"第 {chapter_number} 章尚无可标注正文")

    def list_cases(
        self,
        project_id: str,
        *,
        content_type: str = "",
        status: str = "",
        source: str = "",
        chapter_number: int | None = None,
        severity: str = "",
    ) -> list[RepairCase]:
        cases = self._store(project_id).list_cases()
        result: list[RepairCase] = []
        for case in cases:
            if content_type and case.content_type != content_type:
                continue
            if status and case.status != status:
                continue
            if source and case.source != source:
                continue
            if chapter_number is not None and chapter_number not in case.chapter_numbers:
                continue
            if severity and not any(issue.severity == severity for issue in case.issues):
                continue
            result.append(case)
        return result

    def detail(self, project_id: str, case_id: str) -> RepairCaseDetailView:
        store = self._store(project_id)
        case = store.load_case(case_id)
        if case is None or case.project_id != project_id:
            raise FileNotFoundError(f"repair case not found: {case_id}")
        source_payload: Any = None
        source_blob = str(case.metadata.get("source_blob_hash") or "")
        if source_blob:
            source_payload = store.get_blob(source_blob)
        candidate_payload: Any = None
        if case.latest_candidate is not None:
            candidate_payload = store.get_blob(case.latest_candidate.blob_hash)
        plugin_available = self.plugins.resolve(case.content_type) is not None
        publisher_available = self.publishers.resolve(case.content_type) is not None
        proposal = self._proposal_view(case)
        features = engine_capabilities().features
        workbench_open = features.get("repair_workbench_v1", False)
        publish_open = features.get("repair_publish_v1", False)
        editable = (
            workbench_open
            and case.status
            not in {
                "published",
                "resolved",
                "stale",
                "rejected",
                "manual_required",
            }
            and case.receipt is None
            and (proposal is None or proposal.status != "applied")
        )
        prepare_available = (
            workbench_open
            and plugin_available
            and case.source != "author_annotation"
            and case.status == "located"
        )
        verify_available = (
            workbench_open
            and plugin_available
            and case.latest_candidate is not None
            and case.status in {"candidate_ready", "needs_verification"}
        )
        semantic_approval = (
            case.content_type == "semantic_consistency"
            and case.authority == "proposal_required"
            and case.verification is not None
            and case.verification.passed
            and case.status in {"verified", "awaiting_approval"}
            and not case.proposal_id
            and case.receipt is None
        )
        request_approval = publish_open and (
            semantic_approval
            or (
                publisher_available
                and case.content_type == "chapter_text"
                and case.source == "author_annotation"
                and case.authority == "proposal_required"
                and case.verification is not None
                and case.verification.passed
                and case.status in {"verified", "awaiting_approval"}
                and not case.proposal_id
                and case.receipt is None
            )
        )
        publish = (
            publish_open
            and publisher_available
            and case.status == "awaiting_approval"
            and case.verification is not None
            and case.verification.passed
            and proposal is not None
            and proposal.status == "approved"
            and case.receipt is None
        )
        recover = (
            case.receipt is not None
            and case.receipt.transaction_status == "prepared"
            and not case.receipt.recovered
            and publisher_available
        )
        reason = ""
        if not workbench_open:
            reason = "修复工作台能力当前关闭；仅可查看、拒绝、延期和核对回执"
        elif case.content_type == "book_repair_chapter":
            reason = "全书跨章批次尚无可证明原子性的发布器；不允许逐章应用"
        elif recover:
            reason = "已保存发布意图；只核对既有修订回执，不重放旧候选"
        elif case.authority == "automatic_working_candidate":
            reason = "工作候选须继续走终稿验收与归档屏障，不在此处发布正式正文"
        elif case.status == "resolved" and case.content_type == "semantic_consistency":
            reason = "作者已确认这些来源事实可同时成立；仅在精确来源哈希不变时复用"
        elif request_approval:
            reason = "候选已通过精确替换门禁；请创建版本化正文修订提案"
        elif proposal is not None and proposal.status in {"pending", "deferred"}:
            reason = "请在现有共创提案中核对差异并批准；批准不会立即改写正文"
        elif proposal is not None and proposal.status == "approved":
            reason = "提案已精确批准；可通过修复发布器应用并生成回执"
        elif proposal is not None and proposal.status in {"rejected", "stale"}:
            reason = "修订提案已拒绝或过期；不会应用正文"
        elif not plugin_available:
            reason = "当前内容插件尚未迁移；可人工标注和编辑候选，模型准备/复验保持关闭"
        elif not publish_open:
            reason = "候选可验证，但正式发布能力当前关闭"
        elif case.source == "author_annotation" and case.status == "located":
            reason = "请在候选页编辑作者圈选的精确字符区间"
        return RepairCaseDetailView(
            case=case,
            events=store.events(case_id=case_id),
            source_payload=source_payload,
            candidate_payload=candidate_payload,
            capabilities=RepairWorkbenchCapabilities(
                annotate=workbench_open,
                prepare=prepare_available,
                edit=editable,
                verify=verify_available,
                request_approval=request_approval,
                reject_or_defer=(
                    case.status not in {"published", "rejected"} and case.receipt is None
                ),
                publish=publish,
                recover=recover,
                reason=reason,
            ),
        )

    def annotate(
        self,
        project_id: str,
        request: RepairManualAnnotationRequest,
    ) -> RepairCaseDetailView:
        if not engine_capabilities().features.get("repair_workbench_v1", False):
            raise ValueError("修复工作台能力当前关闭")
        source = self.source(project_id, request.chapter_number)
        if source.artifact_id != request.artifact_id or source.source_hash != request.source_hash:
            raise ValueError("标注来源已变化，请刷新正文后重新圈选")
        selected = source.content[request.char_start : request.char_end]
        if selected != request.selected_text:
            raise ValueError("圈选字符区间与当前正文不一致")
        locator = {
            "target_format": "prose_text",
            "role": "repair",
            "surface": source.artifact_id,
            "chapter_number": request.chapter_number,
            "quote": selected,
            "char_start": request.char_start,
            "char_end": request.char_end,
            "text_hash": source.source_hash,
            "stable_node_id": f"chapter:{request.chapter_number}:text",
            "container_hash": source.source_hash,
            "source_version": source.source_version,
            "comparator_id": "exact_utf8_span_v1",
            "expected_raw": selected,
            "actual_raw": selected,
            "expected_normalized": selected,
            "actual_normalized": selected,
        }
        issue_id = uuid4().hex
        issue = AuditIssueV2.model_validate(
            {
                "issue_id": issue_id,
                "dimension": "author_annotation",
                "issue_type": "manual_text_annotation",
                "severity": request.severity,
                "blocking": request.severity in {"high", "critical"},
                "summary": request.summary,
                "description": request.description or request.summary,
                "evidence": [
                    {
                        "quote": selected,
                        "source": source.artifact_id,
                        "locator": locator,
                        "confidence": 1.0,
                    }
                ],
                "repair_targets": [locator],
                "reference_targets": [],
                "repair_intent": {
                    "operation": "window_rewrite",
                    "target_policy": "exact_character_span",
                    "rationale": "作者在当前正文中人工圈选的问题。",
                    "preserve": ["未圈选正文", "作者锁定内容", "结局", "人物命运", "世界规则"],
                },
            }
        )
        target = ResolvedRepairTarget(
            target_id=uuid4().hex,
            target_format="prose_text",
            surface=source.artifact_id,
            locator=issue.repair_targets[0],
            path=f"chapter:{request.chapter_number}",
            window={"char_start": request.char_start, "char_end": request.char_end},
            current_value=selected,
            current_hash=repair_content_hash(selected),
            issue_ids=[issue_id],
            allowed_operation="window_rewrite",
            confidence=1.0,
            reason="author_exact_selection",
        )
        root = self._root(project_id)
        authoring_policy = AuthoringStore(root).policy()
        authority = (
            "automatic_working_candidate"
            if source.state == "working_candidate"
            else "proposal_required"
        )
        store = RepairCaseStore(root)
        source_blob = store.put_blob(source.content, kind="repair_source")
        case = store.create_case(
            RepairCase(
                case_id=uuid4().hex,
                project_id=project_id,
                content_type="chapter_text",
                source="author_annotation",
                artifact_id=source.artifact_id,
                source_version=source.source_version,
                source_hash=source.source_hash,
                authority=authority,
                input_version=story_input_version(root),
                policy_version=(authoring_policy.version if authoring_policy is not None else None),
                title=request.summary,
                chapter_numbers=[request.chapter_number],
                issues=[issue],
                metadata={
                    "source_blob_hash": source_blob,
                    "selection": {"char_start": request.char_start, "char_end": request.char_end},
                },
            ),
            actor="author",
        )
        case = store.append_event(
            case.case_id,
            "targets_resolved",
            {"targets": [target.model_dump(mode="json")]},
            expected_version=case.version,
            actor="author",
        )
        return self.detail(project_id, case.case_id)

    def save_edit(
        self,
        project_id: str,
        case_id: str,
        request: RepairCandidateEditRequest,
    ) -> RepairCaseDetailView:
        detail = self.detail(project_id, case_id)
        if not detail.capabilities.edit:
            raise ValueError(detail.capabilities.reason or "当前案例不可编辑")
        case = detail.case
        latest_version = case.latest_candidate.version if case.latest_candidate is not None else 0
        if case.version != request.case_version or latest_version != request.candidate_version:
            raise RepairCaseConflictError("repair case changed before candidate edit")
        if not isinstance(detail.source_payload, str) or len(case.targets) != 1:
            raise ValueError("当前案例不是可编辑的单一文本靶点")
        target = case.targets[0]
        start = int(target.window.get("char_start", -1))
        end = int(target.window.get("char_end", -1))
        if start < 0 or end <= start or detail.source_payload[start:end] != target.current_value:
            raise ValueError("候选靶点已过期或不精确")
        self._invalidate_linked_proposal(case)
        candidate_payload = (
            detail.source_payload[:start] + request.replacement_text + detail.source_payload[end:]
        )
        ratio = 1.0 - SequenceMatcher(None, detail.source_payload, candidate_payload).ratio()
        patch = RepairPatchRecord(
            target_id=target.target_id,
            expected_hash=target.current_hash,
            replacement_hash=repair_content_hash(request.replacement_text),
            operation="window_rewrite",
            rationale="作者编辑的精确替换候选",
        )
        orchestrator = RepairOrchestrator(case_store=self._store(project_id))
        orchestrator.save_edited_candidate(
            case_id,
            payload=candidate_payload,
            patches=[patch],
            change_ratio=max(0.0, min(1.0, ratio)),
            expected_case_version=request.case_version,
            expected_candidate_version=request.candidate_version,
            protected_items=["未圈选正文", "作者锁定内容", "结局", "人物命运", "世界规则"],
            metadata={
                "verification_kind": "author_exact_selection_v1",
                "source_hash": case.source_hash,
                "char_start": start,
                "char_end": end,
                "replacement_length": len(request.replacement_text),
                "replacement_hash": repair_content_hash(request.replacement_text),
                "prefix_hash": repair_content_hash(detail.source_payload[:start]),
                "suffix_hash": repair_content_hash(detail.source_payload[end:]),
            },
        )
        return self.detail(project_id, case_id)

    def _invalidate_linked_proposal(self, case: RepairCase) -> None:
        """Revoke an older exact approval before accepting a new candidate version."""

        if not case.proposal_id:
            return
        store = ProposalStore(self._root(case.project_id))
        with store.authority.lock():
            data = store.read(case.proposal_id)
            if data.get("repair_case_id") != case.case_id:
                raise RepairCaseConflictError("修复案例与正文提案绑定不一致")
            view = AuthoringProposalView.model_validate(data["view"])
            if view.status == "applied":
                raise RepairCaseConflictError("已应用的正文提案不能再编辑候选")
            store.revoke(data)
            view.status = "stale"
            view.application_result = {
                **view.application_result,
                "status": "superseded",
                "message": "修复候选已编辑；旧批准已撤销，须重新复验和提案",
            }
            data["view"] = view.model_dump(mode="json")
            store.write(data)

    def decide(
        self,
        project_id: str,
        case_id: str,
        request: RepairCaseDecisionRequest,
    ) -> RepairCaseDetailView:
        store = self._store(project_id)
        case = store.load_case(case_id)
        if case is None or case.project_id != project_id:
            raise FileNotFoundError(f"repair case not found: {case_id}")
        if case.receipt is not None:
            raise RepairCaseConflictError("发布意图已保存；只能核对回执，不能改写案例决定")
        if case.version != request.case_version:
            raise RepairCaseConflictError("修复案例版本已变化，请刷新后重试")
        self._invalidate_linked_proposal(case)
        if request.decision in {"accept_compatible", "authoritative_claims"}:
            if case.content_type != "semantic_consistency":
                raise ValueError("只有语义一致性案例可以记录该决定")
            expected_claim_ids = sorted(
                str(item) for item in case.metadata.get("claim_ids", []) if str(item)
            )
            if (
                request.source_hash != case.source_hash
                or sorted(request.claim_ids) != expected_claim_ids
                or not set(request.authoritative_claim_ids).issubset(expected_claim_ids)
            ):
                raise RepairCaseConflictError("来源或 claims 已变化，请刷新后重新决定")
            store.append_event(
                case_id,
                "semantic_decision_recorded",
                {
                    "decision": request.decision,
                    "reason": request.reason,
                    "source_hash": request.source_hash,
                    "claim_ids": request.claim_ids,
                    "authoritative_claim_ids": request.authoritative_claim_ids,
                },
                expected_version=request.case_version,
                actor="author",
            )
        else:
            store.append_event(
                case_id,
                "decision_recorded",
                {"decision": request.decision, "reason": request.reason},
                expected_version=request.case_version,
                actor="author",
            )
        return self.detail(project_id, case_id)

    def _execution_stop_reason(self, case: RepairCase) -> str:
        store = AuthoringStore(self._root(case.project_id))
        try:
            store.require(
                "discuss",
                case.chapter_numbers[0] if case.chapter_numbers else 1,
                explicit=True,
            )
        except AuthoringDeniedError as exc:
            return str(exc)
        return ""

    async def prepare(
        self,
        project_id: str,
        case_id: str,
        *,
        case_version: int,
    ) -> RepairCaseDetailView:
        detail = self.detail(project_id, case_id)
        if detail.case.version != case_version:
            raise ValueError("修复案例版本已变化，请刷新后重试")
        if not detail.capabilities.prepare:
            raise ValueError(detail.capabilities.reason or "当前内容插件不能准备候选")
        orchestrator = RepairOrchestrator(
            plugin_registry=self.plugins,
            case_store=self._store(project_id),
        )
        await orchestrator.prepare_case(
            case_id,
            expected_case_version=case_version,
            stop_check=lambda: self._execution_stop_reason(detail.case),
        )
        return self.detail(project_id, case_id)

    async def verify(
        self,
        project_id: str,
        case_id: str,
        *,
        case_version: int,
        candidate_version: int,
    ) -> RepairCaseDetailView:
        detail = self.detail(project_id, case_id)
        if not detail.capabilities.verify:
            raise ValueError(detail.capabilities.reason or "当前内容插件不能复验")
        if detail.case.version != case_version:
            raise ValueError("修复案例版本已变化，请刷新后重试")
        if detail.case.latest_candidate is None or (
            detail.case.latest_candidate.version != candidate_version
        ):
            raise ValueError("修复候选版本已变化，请刷新后重试")
        if detail.case.status == "candidate_ready":
            case = self._store(project_id).append_event(
                case_id,
                "verification_requested",
                {},
                expected_version=case_version,
            )
            case_version = case.version
        orchestrator = RepairOrchestrator(
            plugin_registry=self.plugins,
            case_store=self._store(project_id),
        )
        await orchestrator.verify_case(
            case_id,
            expected_case_version=case_version,
            expected_candidate_version=candidate_version,
            stop_check=lambda: self._execution_stop_reason(detail.case),
        )
        return self.detail(project_id, case_id)

    def shadow_compare(
        self,
        project_id: str,
        case_id: str,
        *,
        case_version: int,
        candidate_version: int,
    ) -> dict[str, Any]:
        """Return an evaluation-only comparison without changing case state."""

        detail = self.detail(project_id, case_id)
        case = detail.case
        if case.version != case_version:
            raise ValueError("修复案例版本已变化，请刷新后重试")
        latest_version = case.latest_candidate.version if case.latest_candidate else 0
        if candidate_version != latest_version:
            raise ValueError("修复候选版本已变化，请刷新后重试")
        legacy = case.metadata.get("legacy_shadow_verification")
        verification = case.verification
        return {
            "project_id": project_id,
            "case_id": case_id,
            "case_version": case.version,
            "candidate_version": latest_version,
            "shadow_only": True,
            "state_unchanged": True,
            "candidate_verification": (
                verification.model_dump(mode="json") if verification is not None else None
            ),
            "legacy_verification": legacy if isinstance(legacy, dict) else None,
            "comparable": verification is not None and isinstance(legacy, dict),
        }

    def _official_chapter(self, case: RepairCase) -> tuple[int, Path, str]:
        if len(case.chapter_numbers) != 1:
            raise ValueError("单章发布器要求唯一章节身份")
        chapter_number = case.chapter_numbers[0]
        if case.artifact_id != f"chapter:{chapter_number}:final":
            raise ValueError("只有正式章节正文才能创建版本化修订提案")
        path = ProjectLayout(self._root(case.project_id)).chapter_path(chapter_number)
        if not path.is_file():
            raise FileNotFoundError(f"第 {chapter_number} 章正式正文不存在")
        return chapter_number, path, path.read_text(encoding="utf-8")

    async def request_approval(
        self,
        project_id: str,
        case_id: str,
        request: RepairApprovalRequest,
    ) -> RepairCaseDetailView:
        detail = self.detail(project_id, case_id)
        case = detail.case
        candidate = case.latest_candidate
        if (
            case.version != request.case_version
            or candidate is None
            or (candidate.version != request.candidate_version)
        ):
            raise RepairCaseConflictError("修复案例或候选版本已变化，请刷新后重试")
        if not detail.capabilities.request_approval:
            raise ValueError(detail.capabilities.reason or "当前候选不能创建修订提案")
        if case.content_type == "semantic_consistency":
            from novel_forge.app_service.semantic_repair import (
                request_semantic_repair_approval,
            )

            await request_semantic_repair_approval(
                self.storage,
                project_id,
                case_id,
                expected_case_version=request.case_version,
                expected_candidate_version=request.candidate_version,
            )
            return self.detail(project_id, case_id)
        chapter_number, chapter_path, current_text = self._official_chapter(case)
        current_hash = repair_content_hash(current_text)
        current_input = story_input_version(self._root(project_id))
        policy = AuthoringStore(self._root(project_id)).policy()
        if (
            current_hash != case.source_hash
            or current_input != case.input_version
            or policy is None
            or policy.version != case.policy_version
        ):
            self._store(project_id).append_event(
                case.case_id,
                "case_stale",
                {"reason": "正文、故事输入或授权在复验后已变化"},
                expected_version=case.version,
            )
            raise RepairCaseConflictError("正文、故事输入或授权已变化，旧候选不能提交")
        authoring = AuthoringStore(self._root(project_id))
        authoring.require_enabled()
        if policy.stopped:
            raise AuthoringDeniedError("会话已停止；不会创建新的正文修订提案")
        candidate_payload = detail.candidate_payload
        if not isinstance(candidate_payload, str) or repair_content_hash(candidate_payload) != (
            candidate.candidate_hash
        ):
            raise RepairCaseConflictError("修复候选内容与哈希不一致")
        proposal_id = hashlib.sha256(
            f"repair:{case.case_id}:{candidate.version}:{candidate.candidate_hash}".encode()
        ).hexdigest()[:32]
        from novel_forge.workspace.authoring_proposals import create_proposal

        proposal = await create_proposal(
            SimpleNamespace(storage=self.storage),
            project_id,
            AuthoringProposalRequest(
                command="revise_chapter",
                chapter_number=chapter_number,
                title=f"修复工作台：{case.title or '正文精确修订'}",
                candidate=candidate_payload,
                evidence=[
                    f"repair_case:{case.case_id}",
                    f"candidate_sha256:{candidate.candidate_hash}",
                    "作者精确圈选替换已通过确定性门禁",
                ],
                expected_input_version=case.input_version,
            ),
            proposal_id=proposal_id,
        )
        proposal_store = ProposalStore(self._root(project_id))
        with proposal_store.authority.lock():
            data = proposal_store.read(proposal.id)
            bound = AuthoringProposalView.model_validate(data["view"])
            if (
                bound.candidate != candidate_payload
                or bound.input_version != case.input_version
                or bound.policy_version != case.policy_version
                or bound.status != "pending"
            ):
                raise RepairCaseConflictError("修订提案未绑定当前候选或已经处理")
            data["repair_case_id"] = case.case_id
            bound.application_result = {
                **bound.application_result,
                "approval_flow": "repair_workbench",
                "repair_case_id": case.case_id,
                "message": "批准只记录精确授权；必须回到修复工作台应用并生成回执",
            }
            data["view"] = bound.model_dump(mode="json")
            proposal_store.write(data)
        try:
            self._store(project_id).append_event(
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
                bound = AuthoringProposalView.model_validate(data["view"])
                bound.status = "stale"
                bound.application_result = {
                    **bound.application_result,
                    "status": "orphaned",
                    "message": "修复案例在提案绑定前变化；旧提案已撤销",
                }
                data["view"] = bound.model_dump(mode="json")
                proposal_store.write(data)
            raise
        if chapter_path.read_text(encoding="utf-8") != current_text:
            with proposal_store.authority.lock():
                data = proposal_store.read(proposal.id)
                proposal_store.revoke(data)
                bound = AuthoringProposalView.model_validate(data["view"])
                bound.status = "stale"
                bound.application_result = {
                    **bound.application_result,
                    "status": "source_changed",
                    "message": "创建提案期间正文已变化；旧批准已撤销",
                }
                data["view"] = bound.model_dump(mode="json")
                proposal_store.write(data)
            latest_case = self._store(project_id).load_case(case.case_id)
            if latest_case is not None:
                self._store(project_id).append_event(
                    case.case_id,
                    "case_stale",
                    {"reason": "创建提案期间正文已变化"},
                    expected_version=latest_case.version,
                )
            raise RepairCaseConflictError("创建提案期间正文已变化；旧提案已撤销")
        return self.detail(project_id, case_id)

    async def publish(
        self,
        project_id: str,
        case_id: str,
        request: RepairPublishRequest,
    ) -> RepairCaseDetailView:
        if not engine_capabilities().features.get("repair_publish_v1", False):
            raise ValueError("正式修复发布能力当前关闭")
        detail = self.detail(project_id, case_id)
        case = detail.case
        candidate = case.latest_candidate
        if (
            case.version != request.case_version
            or candidate is None
            or (candidate.version != request.candidate_version)
        ):
            raise RepairCaseConflictError("修复案例或候选版本已变化，请刷新后重试")
        if not detail.capabilities.publish or not case.proposal_id:
            raise ValueError(detail.capabilities.reason or "当前修复候选不具备发布资格")
        chapter_number, _chapter_path, current_text = self._official_chapter(case)
        root = self._root(project_id)
        proposal_store = ProposalStore(root)
        proposal_data = proposal_store.read(case.proposal_id)
        proposal = AuthoringProposalView.model_validate(proposal_data["view"])
        approval_id = str(proposal_data.get("approval_id") or "")
        policy = AuthoringStore(root).policy()
        if (
            proposal_data.get("repair_case_id") != case.case_id
            or proposal.status != "approved"
            or not approval_id
            or policy is None
            or request.authority_version != policy.version
            or case.policy_version != policy.version
        ):
            raise AuthoringDeniedError("修订提案、批准或授权版本不一致")
        authority_store = AuthoringStore(root)
        authority_store.require(
            "revise",
            chapter_number,
            approval_id=approval_id,
            candidate_version=proposal.candidate_version,
            major_change=True,
            expected_policy_version=case.policy_version,
        )
        orchestrator = RepairOrchestrator(
            publisher_registry=self.publishers,
            case_store=self._store(project_id),
        )
        await orchestrator.publish_case(
            case_id,
            expected_case_version=case.version,
            expected_candidate_version=candidate.version,
            authority=RepairAuthorityContext(
                authority=case.authority,
                target=case.artifact_id,
                current_hash=repair_content_hash(current_text),
                source_version=case.source_version,
                input_version=story_input_version(root),
                policy_version=policy.version,
                approval_id=approval_id,
                proposal_id=proposal.id,
                publish_allowed=True,
                stopped=policy.stopped,
            ),
        )
        return self.detail(project_id, case_id)

    async def recover(
        self,
        project_id: str,
        case_id: str,
        request: RepairRecoveryRequest,
    ) -> RepairCaseDetailView:
        detail = self.detail(project_id, case_id)
        case = detail.case
        receipt = case.receipt
        if case.version != request.case_version:
            raise RepairCaseConflictError("修复案例版本已变化，请刷新后重试")
        if receipt is None or receipt.receipt_id != request.receipt_id:
            raise ValueError("没有与请求匹配的发布回执")
        root = self._root(project_id)
        policy = AuthoringStore(root).policy()
        chapter_number = case.chapter_numbers[0] if len(case.chapter_numbers) == 1 else 0
        chapter_path = ProjectLayout(root).chapter_path(chapter_number) if chapter_number else None
        current_hash = (
            repair_content_hash(chapter_path.read_text(encoding="utf-8"))
            if chapter_path is not None and chapter_path.is_file()
            else ""
        )
        orchestrator = RepairOrchestrator(
            publisher_registry=self.publishers,
            case_store=self._store(project_id),
        )
        await orchestrator.recover_case(
            case_id,
            expected_case_version=case.version,
            authority=RepairAuthorityContext(
                authority=case.authority,
                target=receipt.target,
                current_hash=current_hash,
                source_version=case.source_version,
                input_version=story_input_version(root),
                policy_version=policy.version if policy is not None else None,
                approval_id=receipt.approval_id,
                proposal_id=receipt.proposal_id,
                receipt_id=receipt.receipt_id,
            ),
        )
        return self.detail(project_id, case_id)


__all__ = [
    "RepairCommands",
    "repair_plugin_registry",
    "repair_publisher_registry",
]
