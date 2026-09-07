from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from novel_forge.app_service.authoring_proposals import apply_proposal
from novel_forge.app_service.repair_commands import RepairCommands
from novel_forge.core.authoring import (
    AuthoringPolicy,
    AuthoringProposalDecision,
)
from novel_forge.core.schemas.init_coherence import CoherenceClaim, ConflictCandidate
from novel_forge.core.schemas.outline import StoryOutline
from novel_forge.core.schemas.repair import (
    RepairApprovalRequest,
    RepairCaseDecisionRequest,
)
from novel_forge.narrative_state.schemas import ChapterContract
from novel_forge.persistence.authoring_proposals import ProposalStore
from novel_forge.persistence.authoring_store import AuthoringStore, story_input_version
from novel_forge.persistence.filesystem import FileSystemStorage, atomic_write_json
from novel_forge.persistence.models import ProjectLayout
from novel_forge.persistence.repair_case_store import (
    RepairCaseConflictError,
    RepairCaseStore,
)
from novel_forge.pipeline.long.services.init.init_repair_targets import (
    _repair_init_artifact_payload,
)
from novel_forge.pipeline.long.services.init.init_semantic_repair_cases import (
    reconcile_semantic_repair_cases,
    record_semantic_candidate_verification,
)
from novel_forge.workspace.authoring_proposals import decide_proposal


def _claim(claim_id: str, artifact: str, path: str, text: str) -> CoherenceClaim:
    return CoherenceClaim.model_validate(
        {
            "claim_id": claim_id,
            "artifact": artifact,
            "source_path": path,
            "source_field": path.rsplit("/", 1)[-1],
            "chapter_numbers": [1],
            "subject_ids": ["人物A"],
            "axis": "institutional_state",
            "claim_type": "state",
            "claim_text": text,
            "temporality": "actual",
            "evidence": text,
            "cognitive_subjects": ["人物A"],
            "cognitive_object": text,
            "cognitive_level": "confirmed",
            "action_level": "internal",
            "reader_awareness": "full",
            "character_knowledge_coverage": {"人物A": "full"},
            "cognitive_chapter": 1,
            "public_reveal_chapter": 1,
            "foreshadow_chapters": [],
            "confidence": 0.99,
        }
    )


def _outline() -> dict[str, Any]:
    return StoryOutline.model_validate(
        {
            "total_chapters": 1,
            "chapters": [
                {
                    "chapter_number": 1,
                    "title": "开章",
                    "goal": "人物A必须遵守既定处分",
                }
            ],
        }
    ).model_dump(mode="json")


def _contract(required_event: str) -> dict[str, Any]:
    contract = ChapterContract.model_validate(
        {
            "chapter_number": 1,
            "title": "开章",
            "required_events": [required_event],
        }
    ).model_dump(mode="json")
    return {"chapter_contracts": [contract]}


def _semantic_inputs(
    required_event: str = "人物A在处分期内不得履职",
) -> tuple[
    dict[str, dict[str, Any]], list[CoherenceClaim], list[ConflictCandidate], dict[str, Any]
]:
    artifacts = {"outline": _outline(), "chapter_contracts": _contract(required_event)}
    claims = [
        _claim(
            "outline-rule",
            "outline",
            "/chapters/0/goal",
            "人物A必须遵守既定处分",
        ),
        _claim(
            "contract-rule",
            "chapter_contracts",
            "/chapter_contracts/0/required_events/0",
            required_event,
        ),
    ]
    candidates = [
        ConflictCandidate(
            candidate_id="pair-1",
            candidate_type="same_or_overlapping_scope",
            reason="模型必须裁决两条来源事实能否同时成立",
            claim_ids=[claim.claim_id for claim in claims],
            claims=claims,
            severity_hint="high",
        )
    ]
    report = {
        "report_id": "semantic-report-1",
        "compiler_fingerprint": "compiler-v1",
        "verdict": "needs_repair",
        "blocked": True,
        "summary": "模型判定来源含义冲突",
        "issues": [
            {
                "issue_id": "semantic-issue-1",
                "issue_type": "semantic_conflict",
                "severity": "high",
                "candidate_ids": ["pair-1"],
                "summary": "两条制度状态无法同时成立",
                "description": "根据项目原文，模型判定需要修订下游章节契约。",
                "metadata": {"major_source_meaning": True},
            }
        ],
        "claim_coverage": {"complete": True},
        "pair_coverage": {"complete": True},
    }
    return artifacts, claims, candidates, report


def _ctx(tmp_path: Path, project_id: str = "book") -> tuple[SimpleNamespace, ProjectLayout]:
    storage = FileSystemStorage(tmp_path)
    layout = ProjectLayout(storage.ensure_project_dir(project_id))
    layout.ensure_dirs()
    return SimpleNamespace(storage=storage, layout=layout), layout


def test_semantic_decision_reuses_only_exact_source_and_claims(tmp_path: Path) -> None:
    ctx, layout = _ctx(tmp_path)
    artifacts, claims, candidates, report = _semantic_inputs()
    first = reconcile_semantic_repair_cases(
        ctx,
        stage="chapter_contracts_coherence",
        repair_artifact="chapter_contracts",
        artifacts=artifacts,
        claims=claims,
        candidates=candidates,
        report=report,
    )
    case = RepairCaseStore(layout.root).load_case(first["repair_case_ids"][0])
    assert case is not None
    assert case.status == "located"
    assert case.authority == "automatic_derived"
    assert case.metadata["major_source_meaning"] is True
    assert case.metadata["claim_ids"] == ["contract-rule", "outline-rule"]

    commands = RepairCommands(ctx.storage)
    with pytest.raises(RepairCaseConflictError, match="claims"):
        commands.decide(
            "book",
            case.case_id,
            RepairCaseDecisionRequest(
                case_version=case.version,
                decision="accept_compatible",
                source_hash=case.source_hash,
                claim_ids=["outline-rule"],
            ),
        )
    resolved = commands.decide(
        "book",
        case.case_id,
        RepairCaseDecisionRequest(
            case_version=case.version,
            decision="accept_compatible",
            reason="作者确认两处分别指权限与日程，可同时成立。",
            source_hash=case.source_hash,
            claim_ids=["contract-rule", "outline-rule"],
        ),
    )
    assert resolved.case.status == "resolved"

    reused = reconcile_semantic_repair_cases(
        ctx,
        stage="chapter_contracts_coherence",
        repair_artifact="chapter_contracts",
        artifacts=artifacts,
        claims=claims,
        candidates=candidates,
        report=report,
    )
    assert reused["verdict"] == "accept"
    assert reused["issues"] == []

    changed_artifacts, changed_claims, changed_candidates, changed_report = _semantic_inputs(
        "人物A经过特批可在处分期内履职"
    )
    changed = reconcile_semantic_repair_cases(
        ctx,
        stage="chapter_contracts_coherence",
        repair_artifact="chapter_contracts",
        artifacts=changed_artifacts,
        claims=changed_claims,
        candidates=changed_candidates,
        report=changed_report,
    )
    assert changed["issues"]
    assert changed["repair_case_ids"] != first["repair_case_ids"]
    old = RepairCaseStore(layout.root).load_case(case.case_id)
    assert old is not None and old.status == "stale"


def test_candidate_reverification_is_durable_and_deduplicated(tmp_path: Path) -> None:
    ctx, layout = _ctx(tmp_path)
    artifacts, claims, candidates, report = _semantic_inputs()
    projected = reconcile_semantic_repair_cases(
        ctx,
        stage="chapter_contracts_coherence",
        repair_artifact="chapter_contracts",
        artifacts=artifacts,
        claims=claims,
        candidates=candidates,
        report=report,
    )
    candidate_payload = _contract("人物A在处分期内不得履职，除非权威来源明示特批")
    verification = {
        "report_id": "semantic-report-verified",
        "compiler_fingerprint": "compiler-v2",
        "verdict": "accept",
        "blocked": False,
        "summary": "全部 claims 与 pair 已复验",
        "issues": [],
        "claim_coverage": {"complete": True},
        "pair_coverage": {"complete": True},
    }
    cases = record_semantic_candidate_verification(
        layout.root,
        case_ids=projected["repair_case_ids"],
        artifact="chapter_contracts",
        baseline=artifacts["chapter_contracts"],
        candidate_payload=candidate_payload,
        verification_report=verification,
        attempt=1,
    )
    assert len(cases) == 1
    assert cases[0].status == "verified"
    assert cases[0].verification is not None and cases[0].verification.passed
    before_version = cases[0].version

    duplicate = record_semantic_candidate_verification(
        layout.root,
        case_ids=projected["repair_case_ids"],
        artifact="chapter_contracts",
        baseline=artifacts["chapter_contracts"],
        candidate_payload=candidate_payload,
        verification_report=verification,
        attempt=2,
    )
    assert duplicate[0].version == before_version
    assert len(duplicate[0].candidates) == 1


@pytest.mark.asyncio
async def test_candidate_loop_restarts_from_same_baseline_and_adopts_only_full_pass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from novel_forge.pipeline.long.services.init import init_coherence_v2, init_repair_targets

    ctx, layout = _ctx(tmp_path)
    ctx.settings = SimpleNamespace(
        init_coherence_block_min_severity="high",
        init_coherence_max_repair_rounds=3,
    )
    artifacts, claims, candidates, report = _semantic_inputs()
    projected = reconcile_semantic_repair_cases(
        ctx,
        stage="chapter_contracts_coherence",
        repair_artifact="chapter_contracts",
        artifacts=artifacts,
        claims=claims,
        candidates=candidates,
        report=report,
    )
    ctx._semantic_gate_inputs = {
        "chapter_contracts_coherence": {
            "profile": {"summary": "test"},
            "artifacts": artifacts,
        }
    }
    baseline = artifacts["chapter_contracts"]
    observed_inputs: list[dict[str, Any]] = []
    first_candidate = _contract("未通过的候选")
    accepted_candidate = _contract("完成全量重新抽取与语义裁决的候选")

    async def fake_build(_ctx: Any, **kwargs: Any) -> dict[str, Any]:
        observed_inputs.append(kwargs["payload"])
        return first_candidate if len(observed_inputs) == 1 else accepted_candidate

    reports = iter(
        [
            {
                "verdict": "needs_repair",
                "blocked": True,
                "summary": "仍有冲突",
                "issues": [{"issue_id": "remaining"}],
                "claim_coverage": {"complete": True},
                "pair_coverage": {"complete": True},
            },
            {
                "verdict": "accept",
                "blocked": False,
                "summary": "全部通过",
                "issues": [],
                "claim_coverage": {"complete": True},
                "pair_coverage": {"complete": True},
            },
        ]
    )

    async def fake_gate(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return next(reports)

    monkeypatch.setattr(init_repair_targets, "_repair_init_artifact_payload_once", fake_build)
    monkeypatch.setattr(init_coherence_v2, "run_init_coherence_v2_gate", fake_gate)
    repairs: list[dict[str, Any]] = []
    result = await _repair_init_artifact_payload(
        ctx,
        artifact="chapter_contracts",
        payload=baseline,
        report={
            **projected,
            "stage": "chapter_contracts_coherence",
            "repair_scope": [
                {
                    "artifact": "chapter_contracts",
                    "chapters": [1],
                    "fields": ["required_events"],
                }
            ],
        },
        round_index=1,
        repairs=repairs,
    )

    assert result == accepted_candidate
    assert observed_inputs == [baseline, baseline]
    assert repairs[-1]["status"] == "verified_candidate_adopted"
    case = RepairCaseStore(layout.root).load_case(projected["repair_case_ids"][0])
    assert case is not None and case.status == "verified"
    assert len(case.candidates) == 2
    assert case.latest_candidate is not None
    assert case.latest_candidate.candidate_hash != case.candidates[0].candidate_hash

    reuse_repairs: list[dict[str, Any]] = []
    reused = await _repair_init_artifact_payload(
        ctx,
        artifact="chapter_contracts",
        payload=baseline,
        report={
            **projected,
            "stage": "chapter_contracts_coherence",
            "repair_scope": [
                {
                    "artifact": "chapter_contracts",
                    "chapters": [1],
                    "fields": ["required_events"],
                }
            ],
        },
        round_index=2,
        repairs=reuse_repairs,
    )
    assert reused == accepted_candidate
    assert observed_inputs == [baseline, baseline]
    assert reuse_repairs[-1]["status"] == "verified_candidate_reused"


@pytest.mark.asyncio
async def test_approved_semantic_candidate_uses_existing_planning_revision_once(
    tmp_path: Path,
) -> None:
    ctx, layout = _ctx(tmp_path, "configured")
    artifacts, claims, candidates, report = _semantic_inputs()
    atomic_write_json(layout.outline_path, artifacts["outline"])
    contract_path = layout.plans_dir / "chapter_contracts.json"
    atomic_write_json(contract_path, artifacts["chapter_contracts"])
    authority = AuthoringStore(layout.root)
    policy = authority.set_policy(
        AuthoringPolicy(mode="coauthor", start_chapter=1, end_chapter=1), expected_version=0
    )
    authority.start(expected_version=policy.version, input_version=story_input_version(layout.root))

    projected = reconcile_semantic_repair_cases(
        ctx,
        stage="chapter_contracts_coherence",
        repair_artifact="chapter_contracts",
        artifacts=artifacts,
        claims=claims,
        candidates=candidates,
        report=report,
    )
    candidate_payload = _contract("人物A必须遵守处分；如有特批必须在权威来源明示")
    verified = record_semantic_candidate_verification(
        layout.root,
        case_ids=projected["repair_case_ids"],
        artifact="chapter_contracts",
        baseline=artifacts["chapter_contracts"],
        candidate_payload=candidate_payload,
        verification_report={
            "report_id": "verified",
            "compiler_fingerprint": "compiler-v2",
            "verdict": "accept",
            "blocked": False,
            "summary": "已重新抽取并完成语义裁决",
            "issues": [],
            "claim_coverage": {"complete": True},
            "pair_coverage": {"complete": True},
        },
        attempt=1,
    )[0]
    assert verified.authority == "proposal_required"

    commands = RepairCommands(ctx.storage)
    approval_detail = await commands.request_approval(
        "configured",
        verified.case_id,
        RepairApprovalRequest(
            case_version=verified.version,
            candidate_version=verified.latest_candidate.version,
        ),
    )
    case = approval_detail.case
    assert case.status == "awaiting_approval"
    assert case.proposal_id
    proposal = ProposalStore(layout.root).read(case.proposal_id)
    assert proposal["semantic_repair"] is True
    view = ProposalStore(layout.root).views()[0]
    approved = decide_proposal(
        layout.root,
        view.id,
        AuthoringProposalDecision(
            decision="accept",
            candidate_version=view.candidate_version,
            input_version=view.input_version,
            policy_version=view.policy_version,
        ),
    )
    assert approved.status == "approved"

    runtime = SimpleNamespace(storage=ctx.storage)
    applied = await apply_proposal(runtime, None, "configured", view.id)
    assert applied.status == "applied"
    assert ctx.storage.load_json(contract_path) == candidate_payload
    published = RepairCaseStore(layout.root).load_case(case.case_id)
    assert published is not None
    assert published.status == "published"
    assert published.receipt is not None and published.receipt.committed
    event_count = len(RepairCaseStore(layout.root).events(case_id=case.case_id))

    replay = await apply_proposal(runtime, None, "configured", view.id)
    assert replay.status == "applied"
    assert len(RepairCaseStore(layout.root).events(case_id=case.case_id)) == event_count
    assert ctx.storage.load_json(contract_path) == candidate_payload
