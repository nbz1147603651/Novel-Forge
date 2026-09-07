"""Generic Repair Orchestration v2 runner."""

from __future__ import annotations

import hashlib
from typing import Any, Callable, Sequence
from uuid import uuid4

from novel_forge.core.schemas.audit import AuditIssueV2, ResolvedRepairTarget
from novel_forge.core.schemas.repair import (
    RepairArtifactSnapshot,
    RepairAuthority,
    RepairCandidate,
    RepairCase,
    RepairPatchRecord,
    RepairPublishReceipt,
)
from novel_forge.persistence.repair_case_store import (
    RepairCaseConflictError,
    RepairCaseStore,
    repair_content_hash,
)
from novel_forge.pipeline.repair_orchestration.audit_events import (
    build_repair_audit_summary,
    emit_repair_audit_event,
    emit_repair_audit_summary,
    repair_target_audit_payload,
    text_hash,
)
from novel_forge.pipeline.repair_orchestration.models import (
    RepairAttempt,
    RepairAttemptStatus,
    RepairControlMode,
    RepairMission,
    RepairOutcome,
    RepairPlanCandidate,
    RepairTarget,
)
from novel_forge.pipeline.repair_orchestration.plugins import (
    RepairAuthorityContext,
    RepairCandidateMaterial,
    RepairPlugin,
    RepairPluginRegistry,
    RepairPublicationError,
    RepairPublisherRegistry,
)
from novel_forge.pipeline.repair_orchestration.policy import RepairPolicyEngine
from novel_forge.pipeline.repair_orchestration.registry import RepairHandlerRegistry
from novel_forge.pipeline.repair_orchestration.snapshot import RepairSnapshotStore

StepCallback = Callable[[str, Any], None] | None
StopCheck = Callable[[], str | None] | None


class RepairOrchestrator:
    """Coordinate repair handlers through plan/snapshot/execute/verify/commit."""

    def __init__(
        self,
        *,
        registry: RepairHandlerRegistry | None = None,
        snapshot_store: RepairSnapshotStore | None = None,
        policy_engine: RepairPolicyEngine | None = None,
        plugin_registry: RepairPluginRegistry | None = None,
        publisher_registry: RepairPublisherRegistry | None = None,
        case_store: RepairCaseStore | None = None,
        on_step: StepCallback = None,
    ) -> None:
        self._registry = registry or RepairHandlerRegistry()
        self._snapshot_store = snapshot_store or RepairSnapshotStore()
        self._policy = policy_engine or RepairPolicyEngine()
        self._plugins = plugin_registry
        self._publishers = publisher_registry
        self._case_store = case_store
        self._on_step = on_step

    def _emit(self, event: str, payload: dict[str, Any]) -> None:
        if self._on_step is not None:
            self._on_step(event, payload)

    @staticmethod
    def _remember_issue_id(items: list[str], issue_id: str) -> None:
        if issue_id and issue_id not in items:
            items.append(issue_id)

    @staticmethod
    def _mission_text_hash(mission: RepairMission) -> str:
        if "current_text" not in mission.source_context:
            return ""
        return text_hash(str(mission.source_context.get("current_text") or ""))

    def _emit_target_audit(
        self,
        mission: RepairMission,
        target: RepairTarget,
        *,
        event_type: str,
        round_number: int = 0,
        status: str = "",
        repair_action: str = "",
        plan: RepairPlanCandidate | None = None,
        failure_kind: str = "",
        fallback_action: str = "",
        source_text_hash: str = "",
        target_text_hash: str = "",
        extra: dict[str, Any] | None = None,
    ) -> str:
        try:
            payload = repair_target_audit_payload(
                mission,
                target,
                event_type=event_type,
                round_number=round_number,
                status=status,
                repair_action=repair_action,
                plan=plan,
                failure_kind=failure_kind,
                fallback_action=fallback_action,
                source_text_hash=source_text_hash,
                target_text_hash=target_text_hash,
                extra=extra,
            )
            emit_repair_audit_event(self._on_step, payload)
            return str(payload.get("issue_id") or "")
        except Exception:
            return ""

    @staticmethod
    def _event_base(
        mission: RepairMission,
        target: RepairTarget,
        *,
        plan: RepairPlanCandidate | None = None,
        round_number: int = 0,
        snapshot_id: str = "",
    ) -> dict[str, Any]:
        return {
            "trace_id": mission.trace_id,
            "mode": mission.control_mode.value,
            "target_id": target.id,
            "domain": target.domain.value,
            "surface": target.surface.value,
            "strategy": plan.strategy.value if plan is not None else "",
            "round": round_number,
            "snapshot_id": snapshot_id,
        }

    @staticmethod
    def _append_once(items: list[str], value: str) -> None:
        if value not in items:
            items.append(value)

    @staticmethod
    def _round_target(target: RepairTarget, *, round_number: int, max_rounds: int) -> RepairTarget:
        payload = dict(target.payload)
        payload["repair_round"] = round_number
        payload["max_rounds"] = max_rounds
        return target.model_copy(update={"payload": payload})

    def _candidate_components(
        self,
        content_type: str,
    ) -> tuple[RepairPlugin, RepairCaseStore]:
        if self._plugins is None or self._case_store is None:
            raise RuntimeError("candidate-first repair components are not configured")
        plugin = self._plugins.resolve(content_type)
        if plugin is None:
            raise LookupError(f"no candidate-first repair plugin: {content_type}")
        return plugin, self._case_store

    @staticmethod
    def _check_stop(stop_check: StopCheck) -> str:
        if stop_check is None:
            return ""
        try:
            return str(stop_check() or "")
        except Exception as exc:
            return f"stop_check_failed:{type(exc).__name__}"

    @staticmethod
    def _chapter_numbers(issue: AuditIssueV2) -> list[int]:
        chapters: set[int] = set()
        for locator in [*issue.repair_targets, *issue.reference_targets]:
            if locator.chapter_number is not None and locator.chapter_number > 0:
                chapters.add(locator.chapter_number)
            chapters.update(item for item in locator.chapter_range if item > 0)
            chapters.update(item for item in locator.chapter_set if item > 0)
        return sorted(chapters)

    @staticmethod
    def _validate_candidate_targets(
        candidate: RepairCandidate,
        targets: Sequence[ResolvedRepairTarget],
    ) -> None:
        target_by_id = {target.target_id: target for target in targets}
        for patch in candidate.patches:
            target = target_by_id.get(patch.target_id)
            if target is None:
                raise RepairCaseConflictError(
                    f"candidate patch references an unknown target: {patch.target_id}"
                )
            if target.current_hash and patch.expected_hash != target.current_hash:
                raise RepairCaseConflictError(
                    f"candidate patch base changed for target: {patch.target_id}"
                )

    async def audit_cases(
        self,
        snapshot: RepairArtifactSnapshot,
        *,
        authority: RepairAuthority,
        source: str,
        input_version: str = "",
        policy_version: int | None = None,
        max_attempts: int = 2,
        stop_check: StopCheck = None,
    ) -> list[RepairCase]:
        """Audit once and create one independently recoverable case per issue."""

        plugin, _store = self._candidate_components(snapshot.content_type)
        stopped = self._check_stop(stop_check)
        if stopped:
            raise RuntimeError(stopped)
        issues = await plugin.audit(snapshot)
        cases: list[RepairCase] = []
        for issue in issues:
            cases.append(
                await self.run_case(
                    snapshot,
                    issue=issue,
                    authority=authority,
                    source=source,
                    input_version=input_version,
                    policy_version=policy_version,
                    max_attempts=max_attempts,
                    stop_check=stop_check,
                )
            )
        return cases

    async def run_case(
        self,
        snapshot: RepairArtifactSnapshot,
        *,
        issue: AuditIssueV2 | None = None,
        authority: RepairAuthority,
        source: str,
        case_id: str = "",
        input_version: str = "",
        policy_version: int | None = None,
        max_attempts: int = 2,
        stop_check: StopCheck = None,
    ) -> RepairCase:
        """Run locate/propose/verify against one immutable, non-canon baseline."""

        plugin, store = self._candidate_components(snapshot.content_type)
        if max_attempts < 1 or max_attempts > 5:
            raise ValueError("repair candidate attempts must be between 1 and 5")
        if issue is None:
            issues = await plugin.audit(snapshot)
            if len(issues) != 1:
                raise ValueError("run_case requires one issue; use audit_cases for a report")
            issue = issues[0]
        source_blob_hash = store.put_blob(snapshot.payload, kind="repair_source")
        case = RepairCase(
            case_id=case_id or uuid4().hex,
            project_id=snapshot.project_id,
            content_type=snapshot.content_type,
            source=source,
            artifact_id=snapshot.artifact_id,
            source_version=snapshot.source_version,
            source_hash=snapshot.source_hash,
            authority=authority,
            input_version=input_version,
            policy_version=policy_version,
            title=issue.summary,
            chapter_numbers=self._chapter_numbers(issue),
            issues=[issue],
            metadata={
                "plugin": plugin.name,
                "max_attempts": max_attempts,
                "source_blob_hash": source_blob_hash,
            },
        )
        case = store.create_case(case)
        try:
            targets = plugin.locate(issue, snapshot)
        except Exception as exc:
            return store.append_event(
                case.case_id,
                "case_failed",
                {"reason": f"locator_failed:{type(exc).__name__}"},
                expected_version=case.version,
            )
        case = store.append_event(
            case.case_id,
            "targets_resolved",
            {"targets": [item.model_dump(mode="json") for item in targets]},
            expected_version=case.version,
        )
        if case.status == "manual_required":
            return case
        if authority == "manual_only":
            return store.append_event(
                case.case_id,
                "manual_required",
                {"reason": "repair authority is manual_only"},
                expected_version=case.version,
            )

        for attempt in range(1, max_attempts + 1):
            stopped = self._check_stop(stop_check)
            if stopped:
                return store.append_event(
                    case.case_id,
                    "decision_recorded",
                    {"decision": "defer", "reason": stopped},
                    expected_version=case.version,
                )
            attempt_snapshot = snapshot.model_copy(
                update={"metadata": {**snapshot.metadata, "repair_attempt": attempt}}
            )
            try:
                draft = await plugin.propose(issue, targets, attempt_snapshot)
                if draft.change_ratio < 0.0 or draft.change_ratio > 1.0:
                    raise ValueError("repair candidate change_ratio must be between 0 and 1")
                candidate_hash = repair_content_hash(draft.payload)
                blob_hash = store.put_blob(draft.payload)
                candidate = RepairCandidate(
                    case_id=case.case_id,
                    version=len(case.candidates) + 1,
                    base_hash=snapshot.source_hash,
                    candidate_hash=candidate_hash,
                    blob_hash=blob_hash,
                    origin=draft.origin,
                    patch_count=len(draft.patches),
                    change_ratio=draft.change_ratio,
                    patches=list(draft.patches),
                    protected_items=list(draft.protected_items),
                    metadata={**draft.metadata, "attempt": attempt},
                )
                self._validate_candidate_targets(candidate, targets)
                case = store.append_event(
                    case.case_id,
                    "candidate_built",
                    {"candidate": candidate.model_dump(mode="json")},
                    expected_version=case.version,
                )
            except Exception as exc:
                case = store.append_event(
                    case.case_id,
                    "case_failed",
                    {"reason": f"proposal_failed:{type(exc).__name__}"},
                    expected_version=case.version,
                )
                if attempt == max_attempts:
                    return case
                continue

            stopped = self._check_stop(stop_check)
            if stopped:
                return store.append_event(
                    case.case_id,
                    "decision_recorded",
                    {"decision": "defer", "reason": stopped},
                    expected_version=case.version,
                )
            case = store.append_event(
                case.case_id,
                "verification_requested",
                {},
                expected_version=case.version,
            )
            try:
                material = RepairCandidateMaterial(candidate=candidate, payload=draft.payload)
                verification = await plugin.verify(material, case.issues)
                if (
                    verification.case_id != case.case_id
                    or verification.candidate_version != candidate.version
                    or verification.candidate_hash != candidate.candidate_hash
                ):
                    raise RepairCaseConflictError(
                        "plugin verification is not bound to the latest candidate"
                    )
                if (
                    not {item.issue_id for item in case.issues}.issubset(
                        verification.resolved_issue_ids
                    )
                    and verification.passed
                ):
                    raise RepairCaseConflictError(
                        "plugin verification did not close every original issue"
                    )
                verification_blob = store.put_blob(
                    verification,
                    kind="repair_verification",
                )
                verification = verification.model_copy(
                    update={
                        "metadata": {
                            **verification.metadata,
                            "blob_hash": verification_blob,
                        }
                    }
                )
                case = store.append_event(
                    case.case_id,
                    "verification_completed",
                    {"verification": verification.model_dump(mode="json")},
                    expected_version=case.version,
                )
            except Exception as exc:
                case = store.append_event(
                    case.case_id,
                    "case_failed",
                    {"reason": f"verification_failed:{type(exc).__name__}"},
                    expected_version=case.version,
                )
                if attempt == max_attempts:
                    return case
                continue

            if verification.passed:
                if authority == "proposal_required":
                    case = store.append_event(
                        case.case_id,
                        "approval_requested",
                        {"proposal_id": ""},
                        expected_version=case.version,
                    )
                return case
        return case

    def save_edited_candidate(
        self,
        case_id: str,
        *,
        payload: Any,
        patches: Sequence[Any],
        change_ratio: float,
        expected_case_version: int,
        expected_candidate_version: int,
        protected_items: Sequence[str] = (),
        metadata: dict[str, Any] | None = None,
        actor: str = "author",
    ) -> RepairCase:
        """Persist an edited candidate and invalidate all older verification."""

        if self._case_store is None:
            raise RuntimeError("candidate-first repair store is not configured")
        case = self._case_store.load_case(case_id)
        if case is None:
            raise FileNotFoundError(f"repair case not found: {case_id}")
        if case.version != expected_case_version:
            raise RepairCaseConflictError("repair case changed before candidate edit")
        latest_version = case.latest_candidate.version if case.latest_candidate is not None else 0
        if latest_version != expected_candidate_version:
            raise RepairCaseConflictError("repair candidate changed before candidate edit")
        if case.status in {"published", "resolved", "stale", "rejected", "manual_required"}:
            raise RepairCaseConflictError(f"repair case cannot be edited in status {case.status}")
        patch_records = [RepairPatchRecord.model_validate(item) for item in patches]
        candidate_hash = repair_content_hash(payload)
        blob_hash = self._case_store.put_blob(payload)
        candidate = RepairCandidate(
            case_id=case.case_id,
            version=latest_version + 1,
            base_hash=case.source_hash,
            candidate_hash=candidate_hash,
            blob_hash=blob_hash,
            origin="human_edit",
            patch_count=len(patch_records),
            change_ratio=change_ratio,
            patches=patch_records,
            protected_items=list(protected_items),
            metadata=dict(metadata or {}),
        )
        self._validate_candidate_targets(candidate, case.targets)
        return self._case_store.append_event(
            case.case_id,
            "candidate_edited",
            {"candidate": candidate.model_dump(mode="json")},
            expected_version=case.version,
            actor=actor,
        )

    async def prepare_case(
        self,
        case_id: str,
        *,
        expected_case_version: int,
        stop_check: StopCheck = None,
    ) -> RepairCase:
        """Build one isolated candidate for an already located durable case."""

        if self._case_store is None or self._plugins is None:
            raise RuntimeError("candidate-first repair components are not configured")
        case = self._case_store.load_case(case_id)
        if case is None:
            raise FileNotFoundError(f"repair case not found: {case_id}")
        if case.version != expected_case_version:
            raise RepairCaseConflictError("repair case changed before candidate preparation")
        if case.status != "located" or not case.targets or len(case.issues) != 1:
            raise RepairCaseConflictError("repair case is not one precisely located issue")
        stopped = self._check_stop(stop_check)
        if stopped:
            return self._case_store.append_event(
                case.case_id,
                "decision_recorded",
                {"decision": "defer", "reason": stopped},
                expected_version=case.version,
            )
        plugin = self._plugins.resolve(case.content_type)
        if plugin is None:
            raise LookupError(f"no candidate-first repair plugin: {case.content_type}")
        source_blob_hash = str(case.metadata.get("source_blob_hash") or "")
        if not source_blob_hash:
            raise RepairCaseConflictError("repair case has no immutable source blob")
        source_payload = self._case_store.get_blob(source_blob_hash)
        if repair_content_hash(source_payload) != case.source_hash:
            raise RepairCaseConflictError("repair source blob does not match its hash")
        snapshot = RepairArtifactSnapshot(
            project_id=case.project_id,
            content_type=case.content_type,
            artifact_id=case.artifact_id,
            source_version=case.source_version,
            source_hash=case.source_hash,
            payload=source_payload,
            metadata={"repair_case_id": case.case_id},
        )
        draft = await plugin.propose(case.issues[0], case.targets, snapshot)
        if draft.change_ratio < 0.0 or draft.change_ratio > 1.0:
            raise ValueError("repair candidate change_ratio must be between 0 and 1")
        candidate_hash = repair_content_hash(draft.payload)
        candidate = RepairCandidate(
            case_id=case.case_id,
            version=1,
            base_hash=case.source_hash,
            candidate_hash=candidate_hash,
            blob_hash=self._case_store.put_blob(draft.payload),
            origin=draft.origin,
            patch_count=len(draft.patches),
            change_ratio=draft.change_ratio,
            patches=list(draft.patches),
            protected_items=list(draft.protected_items),
            metadata={**draft.metadata, "attempt": 1},
        )
        self._validate_candidate_targets(candidate, case.targets)
        case = self._case_store.append_event(
            case.case_id,
            "candidate_built",
            {"candidate": candidate.model_dump(mode="json")},
            expected_version=case.version,
        )
        stopped = self._check_stop(stop_check)
        if stopped:
            case = self._case_store.append_event(
                case.case_id,
                "decision_recorded",
                {"decision": "defer", "reason": stopped},
                expected_version=case.version,
            )
        return case

    async def verify_case(
        self,
        case_id: str,
        *,
        expected_case_version: int,
        expected_candidate_version: int,
        stop_check: StopCheck = None,
    ) -> RepairCase:
        """Re-run the original plugin validators for an edited candidate."""

        if self._case_store is None or self._plugins is None:
            raise RuntimeError("candidate-first repair components are not configured")
        case = self._case_store.load_case(case_id)
        if case is None or case.latest_candidate is None:
            raise FileNotFoundError(f"repair candidate not found: {case_id}")
        if case.version != expected_case_version:
            raise RepairCaseConflictError("repair case changed before verification")
        candidate = case.latest_candidate
        if candidate.version != expected_candidate_version:
            raise RepairCaseConflictError("repair candidate changed before verification")
        if case.status != "needs_verification":
            raise RepairCaseConflictError("repair candidate is not awaiting verification")
        stopped = self._check_stop(stop_check)
        if stopped:
            return self._case_store.append_event(
                case.case_id,
                "decision_recorded",
                {"decision": "defer", "reason": stopped},
                expected_version=case.version,
            )
        plugin = self._plugins.resolve(case.content_type)
        if plugin is None:
            raise LookupError(f"no candidate-first repair plugin: {case.content_type}")
        payload = self._case_store.get_blob(candidate.blob_hash)
        if repair_content_hash(payload) != candidate.candidate_hash:
            raise RepairCaseConflictError("repair candidate blob does not match its hash")
        verification = await plugin.verify(
            RepairCandidateMaterial(candidate=candidate, payload=payload),
            case.issues,
        )
        if (
            verification.case_id != case.case_id
            or verification.candidate_version != candidate.version
            or verification.candidate_hash != candidate.candidate_hash
        ):
            raise RepairCaseConflictError("plugin verification is stale")
        if verification.passed and not {item.issue_id for item in case.issues}.issubset(
            verification.resolved_issue_ids
        ):
            raise RepairCaseConflictError("verification did not close every original issue")
        blob_hash = self._case_store.put_blob(
            verification,
            kind="repair_verification",
        )
        verification = verification.model_copy(
            update={"metadata": {**verification.metadata, "blob_hash": blob_hash}}
        )
        case = self._case_store.append_event(
            case.case_id,
            "verification_completed",
            {"verification": verification.model_dump(mode="json")},
            expected_version=case.version,
        )
        stopped = self._check_stop(stop_check)
        if stopped:
            return self._case_store.append_event(
                case.case_id,
                "decision_recorded",
                {"decision": "defer", "reason": stopped},
                expected_version=case.version,
            )
        if verification.passed and case.authority == "proposal_required":
            case = self._case_store.append_event(
                case.case_id,
                "approval_requested",
                {"proposal_id": case.proposal_id},
                expected_version=case.version,
            )
        return case

    async def publish_case(
        self,
        case_id: str,
        *,
        expected_case_version: int,
        expected_candidate_version: int,
        authority: RepairAuthorityContext,
    ) -> RepairCase:
        """Authorize and atomically publish one exactly verified candidate."""

        if self._case_store is None or self._publishers is None:
            raise RuntimeError("repair publication components are not configured")
        case = self._case_store.load_case(case_id)
        if case is None or case.latest_candidate is None:
            raise FileNotFoundError(f"repair case not found: {case_id}")
        if case.version != expected_case_version:
            raise RepairCaseConflictError("repair case changed before publication")
        candidate = case.latest_candidate
        if candidate.version != expected_candidate_version:
            raise RepairCaseConflictError("repair candidate changed before publication")
        verification = case.verification
        if (
            case.status not in {"verified", "awaiting_approval"}
            or verification is None
            or not verification.passed
            or verification.candidate_version != candidate.version
            or verification.candidate_hash != candidate.candidate_hash
        ):
            raise RepairPublicationError("only the latest verified candidate may publish")
        if case.receipt is not None:
            raise RepairPublicationError("publication already prepared; recover its receipt")
        if authority.stopped or authority.disabled_reason:
            raise RepairPublicationError(
                authority.disabled_reason or "repair publication is stopped"
            )
        if not authority.publish_allowed or authority.authority != case.authority:
            raise RepairPublicationError("repair publication authority denied")
        if case.authority in {"manual_only", "automatic_working_candidate"}:
            raise RepairPublicationError("this repair authority cannot publish official content")
        if case.authority == "proposal_required" and not (
            authority.approval_id or authority.proposal_id
        ):
            raise RepairPublicationError("proposal repair requires exact approval evidence")
        stale_reason = ""
        if authority.current_hash != case.source_hash:
            stale_reason = "repair source hash changed"
        elif case.source_version and authority.source_version != case.source_version:
            stale_reason = "repair source version changed"
        elif case.input_version and authority.input_version != case.input_version:
            stale_reason = "repair input version changed"
        elif case.policy_version is not None and authority.policy_version != case.policy_version:
            stale_reason = "repair policy version changed"
        if stale_reason:
            self._case_store.append_event(
                case.case_id,
                "case_stale",
                {"reason": stale_reason},
                expected_version=case.version,
            )
            raise RepairCaseConflictError(stale_reason)
        publisher = self._publishers.resolve(case.content_type)
        if publisher is None:
            raise LookupError(f"no repair publisher: {case.content_type}")
        payload = self._case_store.get_blob(candidate.blob_hash)
        if repair_content_hash(payload) != candidate.candidate_hash:
            raise RepairCaseConflictError("repair candidate blob does not match its hash")
        receipt_id = (
            authority.receipt_id
            or hashlib.sha256(
                f"{case.case_id}:{candidate.version}:{authority.target}".encode("utf-8")
            ).hexdigest()
        )
        prepared = RepairPublishReceipt(
            receipt_id=receipt_id,
            case_id=case.case_id,
            candidate_version=candidate.version,
            authority=case.authority,
            target=authority.target,
            before_hash=case.source_hash,
            after_hash=candidate.candidate_hash,
            input_version=authority.input_version,
            policy_version=authority.policy_version,
            approval_id=authority.approval_id,
            proposal_id=authority.proposal_id,
            transaction_status="prepared",
            metadata={
                "project_id": case.project_id,
                "content_type": case.content_type,
                "artifact_id": case.artifact_id,
                "chapter_number": case.chapter_numbers[0] if len(case.chapter_numbers) == 1 else 0,
            },
        )
        case = self._case_store.append_event(
            case.case_id,
            "publication_prepared",
            {"receipt": prepared.model_dump(mode="json")},
            expected_version=case.version,
        )
        committed = await publisher.publish(
            case,
            RepairCandidateMaterial(candidate=candidate, payload=payload),
            authority,
        )
        self._validate_receipt(prepared, committed, recovered=False)
        return self._case_store.append_event(
            case.case_id,
            "publication_committed",
            {"receipt": committed.model_dump(mode="json")},
            expected_version=case.version,
        )

    async def recover_case(
        self,
        case_id: str,
        *,
        expected_case_version: int,
        authority: RepairAuthorityContext,
    ) -> RepairCase:
        """Reconcile an existing receipt without replaying candidate content."""

        if self._case_store is None or self._publishers is None:
            raise RuntimeError("repair publication components are not configured")
        case = self._case_store.load_case(case_id)
        if case is None or case.receipt is None:
            raise FileNotFoundError(f"repair publication receipt not found: {case_id}")
        if case.version != expected_case_version:
            raise RepairCaseConflictError("repair case changed before recovery")
        if case.receipt.recovered:
            return case
        publisher = self._publishers.resolve(case.content_type)
        if publisher is None:
            raise LookupError(f"no repair publisher: {case.content_type}")
        reconciled = await publisher.recover(case.receipt, authority)
        self._validate_receipt(case.receipt, reconciled, recovered=True)
        return self._case_store.append_event(
            case.case_id,
            "recovery_reconciled",
            {"receipt": reconciled.model_dump(mode="json")},
            expected_version=case.version,
        )

    @staticmethod
    def _validate_receipt(
        expected: RepairPublishReceipt,
        actual: RepairPublishReceipt,
        *,
        recovered: bool,
    ) -> None:
        identity = (
            "receipt_id",
            "case_id",
            "candidate_version",
            "authority",
            "target",
            "before_hash",
            "after_hash",
        )
        if any(getattr(expected, field) != getattr(actual, field) for field in identity):
            raise RepairPublicationError("repair publication receipt identity changed")
        if not actual.committed or actual.transaction_status not in {"committed", "reconciled"}:
            raise RepairPublicationError("repair publisher did not confirm a committed transaction")
        if recovered and (not actual.recovered or actual.transaction_status != "reconciled"):
            raise RepairPublicationError("repair receipt recovery was not reconciled")

    async def run(self, mission: RepairMission) -> RepairOutcome:
        outcome = RepairOutcome(project_id=mission.project_id, trace_id=mission.trace_id)
        change_budget = float(mission.policy.get("change_budget", 0.15) or 0.0)
        max_rounds = max(1, int(mission.max_rounds or 1))
        doom_loop_threshold = max(1, int(mission.policy.get("doom_loop_threshold", 3) or 3))
        retry_verification_failure = bool(
            mission.policy.get(
                "retry_verification_failure",
                mission.control_mode == RepairControlMode.AI_AUTO,
            )
        )
        retry_handler_exception = bool(
            mission.policy.get(
                "retry_handler_exception",
                mission.control_mode == RepairControlMode.AI_AUTO,
            )
        )

        self._emit(
            "repair_v2_mission_start",
            {
                "trace_id": mission.trace_id,
                "project_id": mission.project_id,
                "mode": mission.control_mode.value,
                "targets": len(mission.targets),
                "max_rounds": max_rounds,
            },
        )

        failure_counts: dict[str, int] = {}
        selected_issue_ids: list[str] = []
        attempted_issue_ids: list[str] = []
        verified_issue_ids: list[str] = []
        finalized_issue_ids: list[str] = []
        remaining_issue_ids: list[str] = []
        for target in mission.targets:
            target_source_hash = self._mission_text_hash(mission)
            selected_issue_id = self._emit_target_audit(
                mission,
                target,
                event_type="selected",
                status="selected",
                repair_action="target_selected",
                source_text_hash=target_source_hash,
            )
            self._remember_issue_id(selected_issue_ids, selected_issue_id)
            handler = self._registry.resolve(target)
            if handler is None:
                outcome.targets_remaining.append(target.id)
                outcome.warnings.append(f"no_handler:{target.signature}")
                self._emit(
                    "repair_v2_target_skipped",
                    {
                        "trace_id": mission.trace_id,
                        "mode": mission.control_mode.value,
                        "target_id": target.id,
                        "domain": target.domain.value,
                        "surface": target.surface.value,
                        "strategy": "",
                        "round": 0,
                        "snapshot_id": "",
                        "reason": "no_handler",
                    },
                )
                skipped_issue_id = self._emit_target_audit(
                    mission,
                    target,
                    event_type="finalized",
                    status="skipped",
                    repair_action="no_handler",
                    failure_kind="no_handler",
                    fallback_action="needs_human_review",
                    source_text_hash=target_source_hash,
                )
                self._remember_issue_id(finalized_issue_ids, skipped_issue_id)
                self._remember_issue_id(remaining_issue_ids, skipped_issue_id)
                continue

            target_done = False
            for round_number in range(1, max_rounds + 1):
                round_target = self._round_target(
                    target,
                    round_number=round_number,
                    max_rounds=max_rounds,
                )
                plan = await handler.plan(mission, round_target)
                decision = self._policy.decide(
                    mode=mission.control_mode,
                    plan=plan,
                    change_budget=change_budget,
                    consecutive_failures=failure_counts.get(target.signature, 0),
                    verification_available=bool(plan.verification_required),
                    hard_block=bool(plan.metadata.get("hard_block")),
                    data_damage_risk=bool(plan.metadata.get("data_damage_risk")),
                )
                snapshot_payload = await handler.snapshot(mission, round_target, plan)
                snapshot = self._snapshot_store.create(
                    target_id=target.id,
                    payload=snapshot_payload,
                    metadata={
                        "trace_id": mission.trace_id,
                        "domain": target.domain.value,
                        "surface": target.surface.value,
                        "strategy": plan.strategy.value,
                        "round": round_number,
                    },
                )
                attempt = RepairAttempt(
                    target_id=target.id,
                    round_number=round_number,
                    domain=target.domain,
                    surface=target.surface,
                    strategy=plan.strategy,
                    status=RepairAttemptStatus.PLANNED,
                    snapshot_id=snapshot.snapshot_id,
                    decision=decision.action,
                    decision_reason=decision.reason,
                    change_ratio=plan.estimated_change_ratio,
                    metadata={
                        "handler": handler.name,
                        "risk": decision.risk.value,
                        "preview": plan.preview,
                    },
                )
                outcome.attempts.append(attempt)
                self._emit(
                    "repair_v2_plan",
                    {
                        **self._event_base(
                            mission,
                            target,
                            plan=plan,
                            round_number=round_number,
                            snapshot_id=snapshot.snapshot_id,
                        ),
                        "risk": decision.risk.value,
                        "decision": decision.action,
                    },
                )
                attempt_issue_id = self._emit_target_audit(
                    mission,
                    round_target,
                    event_type="attempted",
                    round_number=round_number,
                    status="planned",
                    repair_action=decision.action,
                    plan=plan,
                    source_text_hash=target_source_hash,
                    extra={"risk": decision.risk.value},
                )
                self._remember_issue_id(attempted_issue_ids, attempt_issue_id)

                if decision.action in {"preview_only", "request_approval", "block"}:
                    attempt.status = (
                        RepairAttemptStatus.PENDING_APPROVAL
                        if decision.requires_human
                        else RepairAttemptStatus.SKIPPED
                    )
                    self._append_once(outcome.targets_remaining, target.id)
                    outcome.needs_human_review = (
                        outcome.needs_human_review or decision.requires_human
                    )
                    pending_issue_id = self._emit_target_audit(
                        mission,
                        round_target,
                        event_type="finalized",
                        round_number=round_number,
                        status=decision.action,
                        repair_action=decision.action,
                        plan=plan,
                        source_text_hash=target_source_hash,
                        fallback_action="needs_human_review"
                        if decision.requires_human
                        else "skipped",
                    )
                    self._remember_issue_id(finalized_issue_ids, pending_issue_id)
                    self._remember_issue_id(remaining_issue_ids, pending_issue_id)
                    target_done = True
                    break

                try:
                    result = await handler.execute(mission, round_target, plan)
                    attempt.status = (
                        RepairAttemptStatus.APPLIED
                        if result.applied
                        else RepairAttemptStatus.SKIPPED
                    )
                    attempt.change_ratio = result.change_ratio
                    attempt.warnings.extend(result.warnings)
                    outcome.warnings.extend(result.warnings)

                    if change_budget > 0 and result.change_ratio > change_budget:
                        await handler.rollback(mission, round_target, snapshot.payload)
                        attempt.status = RepairAttemptStatus.ROLLED_BACK
                        attempt.rollback_reason = "change_budget_exceeded"
                        self._append_once(outcome.targets_remaining, target.id)
                        outcome.needs_human_review = True
                        failure_counts[target.signature] = (
                            failure_counts.get(target.signature, 0) + 1
                        )
                        self._emit(
                            "repair_v2_rollback",
                            {
                                **self._event_base(
                                    mission,
                                    target,
                                    plan=plan,
                                    round_number=round_number,
                                    snapshot_id=snapshot.snapshot_id,
                                ),
                                "reason": "change_budget_exceeded",
                                "change_ratio": result.change_ratio,
                                "threshold": change_budget,
                            },
                        )
                        rolled_back_issue_id = self._emit_target_audit(
                            mission,
                            round_target,
                            event_type="finalized",
                            round_number=round_number,
                            status="rolled_back",
                            repair_action="change_budget_exceeded",
                            plan=plan,
                            source_text_hash=target_source_hash,
                            target_text_hash=self._mission_text_hash(mission),
                            failure_kind="change_budget",
                            fallback_action="rollback",
                            extra={"change_ratio": result.change_ratio, "threshold": change_budget},
                        )
                        self._remember_issue_id(finalized_issue_ids, rolled_back_issue_id)
                        self._remember_issue_id(remaining_issue_ids, rolled_back_issue_id)
                        target_done = True
                        break

                    verification = await handler.verify(mission, round_target, plan, result)
                    attempt.verification = verification
                    if not verification.verified:
                        await handler.rollback(mission, round_target, snapshot.payload)
                        attempt.status = RepairAttemptStatus.ROLLED_BACK
                        attempt.rollback_reason = verification.reason or "verification_failed"
                        failure_counts[target.signature] = (
                            failure_counts.get(target.signature, 0) + 1
                        )
                        self._emit(
                            "repair_v2_rollback",
                            {
                                **self._event_base(
                                    mission,
                                    target,
                                    plan=plan,
                                    round_number=round_number,
                                    snapshot_id=snapshot.snapshot_id,
                                ),
                                "reason": attempt.rollback_reason,
                            },
                        )
                        rolled_back_issue_id = self._emit_target_audit(
                            mission,
                            round_target,
                            event_type="finalized",
                            round_number=round_number,
                            status="rolled_back",
                            repair_action="verification_failed",
                            plan=plan,
                            source_text_hash=target_source_hash,
                            target_text_hash=self._mission_text_hash(mission),
                            failure_kind="verification",
                            fallback_action="rollback",
                            extra={"reason": attempt.rollback_reason},
                        )
                        self._remember_issue_id(finalized_issue_ids, rolled_back_issue_id)
                        should_retry = (
                            retry_verification_failure
                            and round_number < max_rounds
                            and failure_counts[target.signature] < doom_loop_threshold
                        )
                        if should_retry:
                            self._emit(
                                "repair_v2_retry_scheduled",
                                {
                                    **self._event_base(
                                        mission,
                                        target,
                                        plan=plan,
                                        round_number=round_number,
                                        snapshot_id=snapshot.snapshot_id,
                                    ),
                                    "next_round": round_number + 1,
                                    "reason": attempt.rollback_reason,
                                },
                            )
                            continue
                        if failure_counts[target.signature] >= doom_loop_threshold:
                            outcome.warnings.append(f"doom_loop_guard:{target.signature}")
                            self._emit(
                                "repair_v2_doom_loop_guard",
                                {
                                    **self._event_base(
                                        mission,
                                        target,
                                        plan=plan,
                                        round_number=round_number,
                                        snapshot_id=snapshot.snapshot_id,
                                    ),
                                    "failures": failure_counts[target.signature],
                                },
                            )
                        self._append_once(outcome.targets_remaining, target.id)
                        outcome.needs_human_review = True
                        self._remember_issue_id(remaining_issue_ids, rolled_back_issue_id)
                        target_done = True
                        break

                    verified_issue_id = self._emit_target_audit(
                        mission,
                        round_target,
                        event_type="verified",
                        round_number=round_number,
                        status="verified",
                        repair_action="verify",
                        plan=plan,
                        source_text_hash=target_source_hash,
                        target_text_hash=self._mission_text_hash(mission),
                        extra={
                            "confidence": verification.confidence,
                            "reason": verification.reason,
                        },
                    )
                    self._remember_issue_id(verified_issue_ids, verified_issue_id)
                    changes = await handler.commit(mission, round_target, plan, result)
                    attempt.status = RepairAttemptStatus.VERIFIED
                    outcome.artifacts_changed.extend(changes)
                    if bool(result.payload.get("needs_human_review", False)):
                        self._append_once(outcome.targets_remaining, target.id)
                        outcome.needs_human_review = True
                        self._remember_issue_id(remaining_issue_ids, verified_issue_id)
                    else:
                        self._append_once(outcome.targets_resolved, target.id)
                    outcome.applied = outcome.applied or result.applied
                    target_done = True
                    self._emit(
                        "repair_v2_target_committed",
                        {
                            **self._event_base(
                                mission,
                                target,
                                plan=plan,
                                round_number=round_number,
                                snapshot_id=snapshot.snapshot_id,
                            ),
                        },
                    )
                    finalized_issue_id = self._emit_target_audit(
                        mission,
                        round_target,
                        event_type="finalized",
                        round_number=round_number,
                        status="finalized"
                        if not result.payload.get("needs_human_review", False)
                        else "needs_human_review",
                        repair_action="commit",
                        plan=plan,
                        source_text_hash=target_source_hash,
                        target_text_hash=self._mission_text_hash(mission),
                        extra={"artifacts_changed": [change.artifact for change in changes]},
                    )
                    self._remember_issue_id(finalized_issue_ids, finalized_issue_id)
                    break
                except Exception as exc:
                    await handler.rollback(mission, round_target, snapshot.payload)
                    attempt.status = RepairAttemptStatus.FAILED
                    attempt.rollback_reason = f"{type(exc).__name__}: {exc}"
                    failure_counts[target.signature] = failure_counts.get(target.signature, 0) + 1
                    self._emit(
                        "repair_v2_target_failed",
                        {
                            **self._event_base(
                                mission,
                                target,
                                plan=plan,
                                round_number=round_number,
                                snapshot_id=snapshot.snapshot_id,
                            ),
                            "error_type": type(exc).__name__,
                            "error": str(exc),
                        },
                    )
                    failed_issue_id = self._emit_target_audit(
                        mission,
                        round_target,
                        event_type="failed",
                        round_number=round_number,
                        status="failed",
                        repair_action="execute",
                        plan=plan,
                        source_text_hash=target_source_hash,
                        target_text_hash=self._mission_text_hash(mission),
                        failure_kind="internal",
                        fallback_action="rollback",
                        extra={"error_type": type(exc).__name__, "error": str(exc)},
                    )
                    should_retry = (
                        retry_handler_exception
                        and round_number < max_rounds
                        and failure_counts[target.signature] < doom_loop_threshold
                    )
                    if should_retry:
                        self._emit(
                            "repair_v2_retry_scheduled",
                            {
                                **self._event_base(
                                    mission,
                                    target,
                                    plan=plan,
                                    round_number=round_number,
                                    snapshot_id=snapshot.snapshot_id,
                                ),
                                "next_round": round_number + 1,
                                "reason": attempt.rollback_reason,
                            },
                        )
                        continue
                    if failure_counts[target.signature] >= doom_loop_threshold:
                        outcome.warnings.append(f"doom_loop_guard:{target.signature}")
                        self._emit(
                            "repair_v2_doom_loop_guard",
                            {
                                **self._event_base(
                                    mission,
                                    target,
                                    plan=plan,
                                    round_number=round_number,
                                    snapshot_id=snapshot.snapshot_id,
                                ),
                                "failures": failure_counts[target.signature],
                            },
                        )
                    self._append_once(outcome.targets_remaining, target.id)
                    outcome.needs_human_review = True
                    self._remember_issue_id(finalized_issue_ids, failed_issue_id)
                    self._remember_issue_id(remaining_issue_ids, failed_issue_id)
                    target_done = True
                    break

            if not target_done:
                self._append_once(outcome.targets_remaining, target.id)
                outcome.needs_human_review = True
                self._remember_issue_id(remaining_issue_ids, selected_issue_id)

        if outcome.targets_remaining:
            outcome.status = "needs_review" if outcome.needs_human_review else "partial"
        self._emit(
            "repair_v2_mission_complete",
            {
                "trace_id": mission.trace_id,
                "project_id": mission.project_id,
                "status": outcome.status,
                "applied": outcome.applied,
                "resolved": len(outcome.targets_resolved),
                "remaining": len(outcome.targets_remaining),
            },
        )
        emit_repair_audit_summary(
            self._on_step,
            build_repair_audit_summary(
                dimension="repair_v2",
                surface="mission",
                rounds_used=len(outcome.attempts),
                status=outcome.status,
                selected_issue_ids=selected_issue_ids,
                attempted_issue_ids=attempted_issue_ids,
                verified_issue_ids=verified_issue_ids,
                finalized_issue_ids=finalized_issue_ids,
                remaining_issue_ids=remaining_issue_ids,
                failure_kind="remaining_targets" if outcome.targets_remaining else "",
                fallback_action="needs_human_review" if outcome.needs_human_review else "",
                extra={
                    "trace_id": mission.trace_id,
                    "project_id": mission.project_id,
                    "applied": outcome.applied,
                    "resolved_targets": list(outcome.targets_resolved),
                    "remaining_targets": list(outcome.targets_remaining),
                    "warnings": list(outcome.warnings),
                },
            ),
        )
        return outcome
