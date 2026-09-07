from __future__ import annotations

from pathlib import Path
from typing import Sequence

import pytest

from novel_forge.core.schemas.audit import AuditIssueV2, ResolvedRepairTarget
from novel_forge.core.schemas.repair import (
    RepairArtifactSnapshot,
    RepairCase,
    RepairPatchRecord,
    RepairPublishReceipt,
    RepairValidatorResult,
    RepairVerificationBundle,
)
from novel_forge.persistence.repair_case_store import (
    RepairCaseConflictError,
    RepairCaseStore,
    repair_content_hash,
)
from novel_forge.pipeline.repair_orchestration.orchestrator import RepairOrchestrator
from novel_forge.pipeline.repair_orchestration.plugins import (
    LegacyRepairHandlerAdapter,
    RepairAuthorityContext,
    RepairCandidateDraft,
    RepairCandidateMaterial,
    RepairPluginRegistry,
    RepairPublicationError,
    RepairPublisherRegistry,
    UnsafeRepairPluginError,
)


def _issue() -> AuditIssueV2:
    locator = {
        "target_format": "json_artifact",
        "role": "repair",
        "artifact": "plan",
        "json_pointer": "/duration",
        "field_path": "duration",
        "stable_node_id": "plan:duration",
        "container_hash": repair_content_hash({"duration": "三日"}),
        "comparator_id": "story_duration_v1",
        "expected_raw": "五日",
        "actual_raw": "三日",
        "expected_normalized": 5,
        "actual_normalized": 3,
    }
    return AuditIssueV2.model_validate(
        {
            "issue_id": "duration-1",
            "dimension": "alignment",
            "issue_type": "duration_conflict",
            "severity": "high",
            "blocking": True,
            "summary": "停职期限冲突",
            "description": "计划应与大纲的五日期限一致。",
            "evidence": [{"quote": "三日", "source": "plan", "locator": locator}],
            "repair_targets": [locator],
            "reference_targets": [],
            "repair_intent": {
                "operation": "json_patch",
                "target_policy": "exact_field",
                "rationale": "仅修复期限字段。",
                "preserve": ["结局", "人物命运", "世界规则"],
            },
        }
    )


class _CandidatePlugin:
    name = "duration-candidate"
    content_types = ("init_plan",)
    candidate_safe = True

    def __init__(self, *, fail_first: bool = False) -> None:
        self.issue = _issue()
        self.fail_first = fail_first
        self.seen_source_hashes: list[str] = []

    async def audit(self, snapshot: RepairArtifactSnapshot) -> list[AuditIssueV2]:
        del snapshot
        return [self.issue]

    def locate(
        self,
        issue: AuditIssueV2,
        snapshot: RepairArtifactSnapshot,
    ) -> list[ResolvedRepairTarget]:
        return [
            ResolvedRepairTarget(
                target_id="target-duration",
                target_format="json_artifact",
                surface="plan",
                locator=issue.repair_targets[0],
                path="/duration",
                current_value=snapshot.payload["duration"],
                current_hash=repair_content_hash(snapshot.payload["duration"]),
                issue_ids=[issue.issue_id],
                allowed_operation="json_patch",
            )
        ]

    async def propose(
        self,
        issue: AuditIssueV2,
        targets: Sequence[ResolvedRepairTarget],
        snapshot: RepairArtifactSnapshot,
    ) -> RepairCandidateDraft:
        del issue
        self.seen_source_hashes.append(snapshot.source_hash)
        return RepairCandidateDraft(
            payload={"duration": "五日"},
            origin="deterministic",
            patches=(
                RepairPatchRecord(
                    target_id=targets[0].target_id,
                    expected_hash=targets[0].current_hash,
                    replacement_hash=repair_content_hash("五日"),
                    field_path="duration",
                ),
            ),
            change_ratio=0.1,
            protected_items=("结局", "人物命运", "世界规则"),
            metadata={"attempt": snapshot.metadata.get("repair_attempt", 1)},
        )

    async def verify(
        self,
        candidate: RepairCandidateMaterial,
        issues: Sequence[AuditIssueV2],
    ) -> RepairVerificationBundle:
        attempt = int(candidate.candidate.metadata.get("attempt", 1))
        passed = candidate.payload == {"duration": "五日"} and not (
            self.fail_first and attempt == 1
        )
        return RepairVerificationBundle(
            case_id=candidate.candidate.case_id,
            candidate_version=candidate.candidate.version,
            candidate_hash=candidate.candidate.candidate_hash,
            passed=passed,
            resolved_issue_ids=[issues[0].issue_id] if passed else [],
            residual_issue_ids=[] if passed else [issues[0].issue_id],
            validators=[
                RepairValidatorResult(
                    validator_id="duration_comparator",
                    passed=passed,
                    evidence={"candidate": candidate.payload},
                )
            ],
        )


class _FilePublisher:
    name = "derived-file"
    content_types = ("init_plan",)

    def __init__(self, path: Path, *, fail_after_commit: bool = False) -> None:
        self.path = path
        self.fail_after_commit = fail_after_commit
        self.writes = 0
        self.committed: set[str] = set()

    async def publish(
        self,
        case,  # type: ignore[no-untyped-def]
        candidate: RepairCandidateMaterial,
        authority: RepairAuthorityContext,
    ) -> RepairPublishReceipt:
        del authority
        assert case.receipt is not None
        receipt = case.receipt
        if receipt.receipt_id not in self.committed:
            self.path.write_text(str(candidate.payload["duration"]), encoding="utf-8")
            self.writes += 1
            self.committed.add(receipt.receipt_id)
        if self.fail_after_commit:
            raise RuntimeError("response_lost_after_commit")
        return receipt.model_copy(
            update={"transaction_status": "committed", "committed": True}
        )

    async def recover(
        self,
        receipt: RepairPublishReceipt,
        authority: RepairAuthorityContext,
    ) -> RepairPublishReceipt:
        del authority
        if receipt.receipt_id not in self.committed:
            raise RuntimeError("no committed transaction to reconcile")
        return receipt.model_copy(
            update={
                "transaction_status": "reconciled",
                "committed": True,
                "recovered": True,
            }
        )


def _orchestrator(
    tmp_path: Path,
    plugin: _CandidatePlugin,
    publisher: _FilePublisher | None = None,
) -> RepairOrchestrator:
    plugins = RepairPluginRegistry()
    plugins.register(plugin)
    publishers = RepairPublisherRegistry()
    if publisher is not None:
        publishers.register(publisher)
    return RepairOrchestrator(
        plugin_registry=plugins,
        publisher_registry=publishers,
        case_store=RepairCaseStore(tmp_path),
    )


def _snapshot() -> RepairArtifactSnapshot:
    payload = {"duration": "三日"}
    return RepairArtifactSnapshot(
        project_id="project-1",
        content_type="init_plan",
        artifact_id="plan",
        source_version="plan-v1",
        source_hash=repair_content_hash(payload),
        payload=payload,
    )


@pytest.mark.asyncio
async def test_candidate_attempts_reuse_immutable_baseline_and_never_write_official_file(
    tmp_path,
) -> None:
    official = tmp_path / "official-plan.txt"
    official.write_text("三日", encoding="utf-8")
    plugin = _CandidatePlugin(fail_first=True)
    orchestrator = _orchestrator(tmp_path, plugin)
    snapshot = _snapshot()

    case = await orchestrator.run_case(
        snapshot,
        issue=plugin.issue,
        authority="automatic_derived",
        source="init_schema",
        max_attempts=2,
    )

    assert case.status == "verified"
    assert len(case.candidates) == 2
    assert plugin.seen_source_hashes == [snapshot.source_hash, snapshot.source_hash]
    assert official.read_text(encoding="utf-8") == "三日"


@pytest.mark.asyncio
async def test_located_case_prepares_then_verifies_as_two_separate_durable_steps(
    tmp_path: Path,
) -> None:
    plugin = _CandidatePlugin()
    store = RepairCaseStore(tmp_path)
    snapshot = _snapshot()
    source_blob = store.put_blob(snapshot.payload, kind="repair_source")
    case = store.create_case(
        RepairCase(
            case_id="located-case",
            project_id=snapshot.project_id,
            content_type=snapshot.content_type,
            source="manual_annotation",
            artifact_id=snapshot.artifact_id,
            source_version=snapshot.source_version,
            source_hash=snapshot.source_hash,
            authority="automatic_derived",
            issues=[plugin.issue],
            metadata={"source_blob_hash": source_blob},
        )
    )
    targets = plugin.locate(plugin.issue, snapshot)
    case = store.append_event(
        case.case_id,
        "targets_resolved",
        {"targets": [item.model_dump(mode="json") for item in targets]},
        expected_version=case.version,
    )
    orchestrator = _orchestrator(tmp_path, plugin)

    prepared = await orchestrator.prepare_case(
        case.case_id,
        expected_case_version=case.version,
    )

    assert prepared.status == "candidate_ready"
    assert prepared.verification is None
    assert prepared.latest_candidate is not None
    requested = store.append_event(
        prepared.case_id,
        "verification_requested",
        {},
        expected_version=prepared.version,
    )
    verified = await orchestrator.verify_case(
        requested.case_id,
        expected_case_version=requested.version,
        expected_candidate_version=1,
    )
    assert verified.status == "verified"
    assert verified.verification is not None and verified.verification.passed


@pytest.mark.asyncio
async def test_human_edit_invalidates_verification_before_exactly_once_publication(
    tmp_path,
) -> None:
    official = tmp_path / "official-plan.txt"
    official.write_text("三日", encoding="utf-8")
    plugin = _CandidatePlugin()
    publisher = _FilePublisher(official)
    orchestrator = _orchestrator(tmp_path, plugin, publisher)
    snapshot = _snapshot()
    case = await orchestrator.run_case(
        snapshot,
        issue=plugin.issue,
        authority="automatic_derived",
        source="init_schema",
        max_attempts=1,
    )
    assert case.verification is not None
    assert case.latest_candidate is not None
    edited = orchestrator.save_edited_candidate(
        case.case_id,
        payload={"duration": "五日"},
        patches=[
            RepairPatchRecord(
                target_id="target-duration",
                expected_hash=repair_content_hash("三日"),
                replacement_hash=repair_content_hash("五日"),
                field_path="duration",
            )
        ],
        change_ratio=0.1,
        expected_case_version=case.version,
        expected_candidate_version=case.latest_candidate.version,
    )
    assert edited.status == "needs_verification"
    assert edited.verification is None
    assert edited.latest_candidate is not None
    authority = RepairAuthorityContext(
        authority="automatic_derived",
        target=str(official),
        current_hash=snapshot.source_hash,
        source_version=snapshot.source_version,
        publish_allowed=True,
    )
    with pytest.raises(RepairPublicationError, match="latest verified"):
        await orchestrator.publish_case(
            edited.case_id,
            expected_case_version=edited.version,
            expected_candidate_version=edited.latest_candidate.version,
            authority=authority,
        )
    verified = await orchestrator.verify_case(
        edited.case_id,
        expected_case_version=edited.version,
        expected_candidate_version=edited.latest_candidate.version,
    )
    assert verified.latest_candidate is not None
    published = await orchestrator.publish_case(
        verified.case_id,
        expected_case_version=verified.version,
        expected_candidate_version=verified.latest_candidate.version,
        authority=authority,
    )
    recovered = await orchestrator.recover_case(
        published.case_id,
        expected_case_version=published.version,
        authority=authority,
    )

    assert official.read_text(encoding="utf-8") == "五日"
    assert publisher.writes == 1
    assert recovered.receipt is not None and recovered.receipt.recovered


@pytest.mark.asyncio
async def test_recovery_reconciles_lost_publish_response_without_replaying_content(
    tmp_path,
) -> None:
    official = tmp_path / "official-plan.txt"
    official.write_text("三日", encoding="utf-8")
    plugin = _CandidatePlugin()
    publisher = _FilePublisher(official, fail_after_commit=True)
    orchestrator = _orchestrator(tmp_path, plugin, publisher)
    snapshot = _snapshot()
    case = await orchestrator.run_case(
        snapshot,
        issue=plugin.issue,
        authority="automatic_derived",
        source="init_schema",
        max_attempts=1,
    )
    assert case.latest_candidate is not None
    authority = RepairAuthorityContext(
        authority="automatic_derived",
        target=str(official),
        current_hash=snapshot.source_hash,
        source_version=snapshot.source_version,
        publish_allowed=True,
    )
    with pytest.raises(RuntimeError, match="response_lost"):
        await orchestrator.publish_case(
            case.case_id,
            expected_case_version=case.version,
            expected_candidate_version=case.latest_candidate.version,
            authority=authority,
        )
    prepared = RepairCaseStore(tmp_path).load_case(case.case_id)
    assert prepared is not None and prepared.receipt is not None
    assert prepared.receipt.transaction_status == "prepared"
    recovery_authority = RepairAuthorityContext(
        authority="automatic_derived",
        target=str(official),
        current_hash=repair_content_hash("五日"),
        source_version=snapshot.source_version,
        publish_allowed=False,
        stopped=True,
        disabled_reason="repair capability disabled",
    )
    recovered = await orchestrator.recover_case(
        prepared.case_id,
        expected_case_version=prepared.version,
        authority=recovery_authority,
    )

    assert recovered.receipt is not None and recovered.receipt.recovered
    assert official.read_text(encoding="utf-8") == "五日"
    assert publisher.writes == 1


@pytest.mark.asyncio
async def test_publication_fails_closed_when_input_or_policy_version_is_stale(tmp_path) -> None:
    official = tmp_path / "official-plan.txt"
    official.write_text("三日", encoding="utf-8")
    plugin = _CandidatePlugin()
    publisher = _FilePublisher(official)
    orchestrator = _orchestrator(tmp_path, plugin, publisher)
    snapshot = _snapshot()
    case = await orchestrator.run_case(
        snapshot,
        issue=plugin.issue,
        authority="automatic_derived",
        source="init_schema",
        input_version="input-v1",
        policy_version=2,
        max_attempts=1,
    )
    assert case.latest_candidate is not None
    with pytest.raises(RepairCaseConflictError, match="input version changed"):
        await orchestrator.publish_case(
            case.case_id,
            expected_case_version=case.version,
            expected_candidate_version=case.latest_candidate.version,
            authority=RepairAuthorityContext(
                authority="automatic_derived",
                target=str(official),
                current_hash=snapshot.source_hash,
                source_version=snapshot.source_version,
                input_version="input-v2",
                policy_version=2,
                publish_allowed=True,
            ),
        )

    stale = RepairCaseStore(tmp_path).load_case(case.case_id)
    assert stale is not None and stale.status == "stale"
    assert official.read_text(encoding="utf-8") == "三日"
    assert publisher.writes == 0


@pytest.mark.asyncio
async def test_proposal_authority_and_stop_state_never_publish_without_approval(
    tmp_path,
) -> None:
    official = tmp_path / "official-plan.txt"
    official.write_text("三日", encoding="utf-8")
    plugin = _CandidatePlugin()
    publisher = _FilePublisher(official)
    orchestrator = _orchestrator(tmp_path, plugin, publisher)
    snapshot = _snapshot()
    deferred = await orchestrator.run_case(
        snapshot,
        issue=plugin.issue,
        authority="automatic_derived",
        source="init_schema",
        stop_check=lambda: "authoring_stopped",
    )
    assert deferred.status == "deferred"
    assert deferred.candidates == []

    proposal = await orchestrator.run_case(
        snapshot,
        issue=plugin.issue,
        authority="proposal_required",
        source="init_schema",
        max_attempts=1,
    )
    assert proposal.status == "awaiting_approval"
    assert proposal.latest_candidate is not None
    with pytest.raises(RepairPublicationError, match="exact approval"):
        await orchestrator.publish_case(
            proposal.case_id,
            expected_case_version=proposal.version,
            expected_candidate_version=proposal.latest_candidate.version,
            authority=RepairAuthorityContext(
                authority="proposal_required",
                target=str(official),
                current_hash=snapshot.source_hash,
                source_version=snapshot.source_version,
                publish_allowed=True,
            ),
        )

    assert official.read_text(encoding="utf-8") == "三日"
    assert publisher.writes == 0


def test_candidate_registry_rejects_legacy_execute_first_handler() -> None:
    registry = RepairPluginRegistry()
    with pytest.raises(UnsafeRepairPluginError, match="candidate-only"):
        registry.register(LegacyRepairHandlerAdapter(object()))  # type: ignore[arg-type]
