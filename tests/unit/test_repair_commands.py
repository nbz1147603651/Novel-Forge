from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from novel_forge.api.deps import get_storage
from novel_forge.api.routes.authoring import router
from novel_forge.app_service.authoring_commands import AuthoringCommands
from novel_forge.app_service.repair_commands import RepairCommands
from novel_forge.app_service.repair_publishers import AuthoringProposalChapterPublisher
from novel_forge.core.authoring import AuthoringPolicy, AuthoringProposalDecision
from novel_forge.core.schemas.repair import (
    RepairApprovalRequest,
    RepairCandidateEditRequest,
    RepairCaseDecisionRequest,
    RepairManualAnnotationRequest,
    RepairPublishRequest,
    RepairRecoveryRequest,
)
from novel_forge.persistence.authoring_proposals import ProposalStore
from novel_forge.persistence.authoring_store import (
    AuthoringDeniedError,
    AuthoringStore,
    story_input_version,
)
from novel_forge.persistence.filesystem import FileSystemStorage
from novel_forge.persistence.models import ProjectLayout
from novel_forge.workspace.authoring_proposals import decide_proposal


def _chapter_storage(tmp_path: Path) -> tuple[FileSystemStorage, Path, str]:
    storage = FileSystemStorage(tmp_path)
    root = storage.ensure_project_dir("book")
    layout = ProjectLayout(root)
    source = "雨线落在旧窗上。沈昭停住脚步，听见钟声。"
    path = layout.chapter_review_draft_path(2)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")
    return storage, path, source


def _annotation(source: str, *, artifact_id: str, source_hash: str):
    selected = "沈昭停住脚步"
    start = source.index(selected)
    return RepairManualAnnotationRequest(
        chapter_number=2,
        artifact_id=artifact_id,
        source_hash=source_hash,
        char_start=start,
        char_end=start + len(selected),
        selected_text=selected,
        summary="动作需要更明确",
        description="保留人物决定，只改善动作表达。",
        severity="medium",
    )


def test_manual_annotation_and_edit_are_isolated_and_cas_guarded(tmp_path: Path) -> None:
    storage, chapter_path, original = _chapter_storage(tmp_path)
    commands = RepairCommands(storage)
    source = commands.source("book", 2)

    detail = commands.annotate(
        "book",
        _annotation(original, artifact_id=source.artifact_id, source_hash=source.source_hash),
    )

    assert detail.case.status == "located"
    assert detail.case.authority == "automatic_working_candidate"
    assert detail.case.targets[0].locator.comparator_id == "exact_utf8_span_v1"
    assert detail.case.targets[0].locator.expected_raw == "沈昭停住脚步"
    assert detail.source_payload == original
    assert chapter_path.read_text(encoding="utf-8") == original

    edited = commands.save_edit(
        "book",
        detail.case.case_id,
        RepairCandidateEditRequest(
            case_version=detail.case.version,
            candidate_version=0,
            replacement_text="沈昭在门槛前骤然停步",
        ),
    )

    assert edited.case.status == "needs_verification"
    assert edited.case.verification is None
    assert edited.case.latest_candidate is not None
    assert "沈昭在门槛前骤然停步" in edited.candidate_payload
    assert edited.capabilities.verify is True
    assert edited.capabilities.publish is False
    assert chapter_path.read_text(encoding="utf-8") == original

    with pytest.raises(ValueError, match="changed before candidate edit"):
        commands.save_edit(
            "book",
            detail.case.case_id,
            RepairCandidateEditRequest(
                case_version=detail.case.version,
                candidate_version=0,
                replacement_text="陈旧编辑",
            ),
        )


def test_annotation_rejects_changed_source_and_decision_stays_available(tmp_path: Path) -> None:
    storage, chapter_path, original = _chapter_storage(tmp_path)
    commands = RepairCommands(storage)
    source = commands.source("book", 2)
    request = _annotation(
        original,
        artifact_id=source.artifact_id,
        source_hash=source.source_hash,
    )
    chapter_path.write_text(original + "后来又响了一声。", encoding="utf-8")
    with pytest.raises(ValueError, match="来源已变化"):
        commands.annotate("book", request)

    fresh = commands.source("book", 2)
    detail = commands.annotate(
        "book",
        _annotation(
            fresh.content,
            artifact_id=fresh.artifact_id,
            source_hash=fresh.source_hash,
        ),
    )
    rejected = commands.decide(
        "book",
        detail.case.case_id,
        RepairCaseDecisionRequest(
            case_version=detail.case.version,
            decision="reject",
            reason="作者决定保留原表达",
        ),
    )
    assert rejected.case.status == "rejected"
    assert chapter_path.read_text(encoding="utf-8") == fresh.content


def test_shadow_compare_is_read_only_and_version_bound(tmp_path: Path) -> None:
    storage, chapter_path, original = _chapter_storage(tmp_path)
    commands = RepairCommands(storage)
    source = commands.source("book", 2)
    detail = commands.annotate(
        "book",
        _annotation(original, artifact_id=source.artifact_id, source_hash=source.source_hash),
    )

    comparison = commands.shadow_compare(
        "book",
        detail.case.case_id,
        case_version=detail.case.version,
        candidate_version=0,
    )

    assert comparison["shadow_only"] is True
    assert comparison["state_unchanged"] is True
    assert comparison["candidate_verification"] is None
    unchanged = commands.detail("book", detail.case.case_id)
    assert unchanged.case.version == detail.case.version
    assert unchanged.case.status == "located"
    assert chapter_path.read_text(encoding="utf-8") == original


def test_repair_http_contract_exposes_evidence_and_fails_closed_on_publish(
    tmp_path: Path,
) -> None:
    storage, chapter_path, original = _chapter_storage(tmp_path)
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_storage] = lambda: storage

    with TestClient(app) as client:
        source_response = client.get("/authoring/book/repairs/source?chapter=2")
        assert source_response.status_code == 200
        source = source_response.json()
        request = _annotation(
            original,
            artifact_id=source["artifact_id"],
            source_hash=source["source_hash"],
        )
        annotation_response = client.post(
            "/authoring/book/repairs/annotations",
            json=request.model_dump(mode="json"),
        )
        assert annotation_response.status_code == 200
        detail = annotation_response.json()
        case_id = detail["case"]["case_id"]

        cases = client.get("/authoring/book/repairs?chapter=2&source=author_annotation")
        assert cases.status_code == 200
        assert [item["case_id"] for item in cases.json()] == [case_id]
        assert client.get(f"/authoring/book/repairs/{case_id}").status_code == 200

        publish = client.post(
            f"/authoring/book/repairs/{case_id}/publish",
            json={
                "case_version": detail["case"]["version"],
                "candidate_version": 1,
                "authority_version": 0,
            },
        )
        assert publish.status_code == 409
        assert "修复案例或候选版本已变化" in publish.text

    assert chapter_path.read_text(encoding="utf-8") == original


def _official_chapter_storage(tmp_path: Path) -> tuple[FileSystemStorage, ProjectLayout, str]:
    storage = FileSystemStorage(tmp_path)
    root = storage.ensure_project_dir("official-book")
    layout = ProjectLayout(root)
    original = "雨线落在旧窗上。沈昭停住脚步，听见钟声。"
    layout.chapter_path(1).parent.mkdir(parents=True, exist_ok=True)
    layout.chapter_path(1).write_text(original, encoding="utf-8")
    authority = AuthoringStore(root)
    policy = authority.set_policy(
        AuthoringPolicy(mode="coauthor", end_chapter=10), expected_version=0
    )
    authority.start(expected_version=policy.version, input_version=story_input_version(root))
    return storage, layout, original


async def _verified_official_case(storage: FileSystemStorage, original: str):
    commands = RepairCommands(storage)
    source = commands.source("official-book", 1)
    annotated = commands.annotate(
        "official-book",
        _annotation(
            original, artifact_id=source.artifact_id, source_hash=source.source_hash
        ).model_copy(update={"chapter_number": 1}),
    )
    edited = commands.save_edit(
        "official-book",
        annotated.case.case_id,
        RepairCandidateEditRequest(
            case_version=annotated.case.version,
            candidate_version=0,
            replacement_text="沈昭在门槛前骤然停步",
        ),
    )
    verified = await commands.verify(
        "official-book",
        edited.case.case_id,
        case_version=edited.case.version,
        candidate_version=1,
    )
    return commands, verified


async def test_official_chapter_repair_requires_existing_approval_then_publishes(
    tmp_path: Path,
) -> None:
    storage, layout, original = _official_chapter_storage(tmp_path)
    commands, verified = await _verified_official_case(storage, original)
    assert verified.case.status == "awaiting_approval"
    assert verified.case.proposal_id == ""
    assert verified.capabilities.request_approval is True
    assert layout.chapter_path(1).read_text(encoding="utf-8") == original

    proposed = await commands.request_approval(
        "official-book",
        verified.case.case_id,
        RepairApprovalRequest(
            case_version=verified.case.version,
            candidate_version=1,
        ),
    )
    proposal = ProposalStore(layout.root).views()[0]
    assert proposed.case.proposal_id == proposal.id
    assert proposal.status == "pending"
    assert proposal.application_result["approval_flow"] == "repair_workbench"
    assert layout.chapter_path(1).read_text(encoding="utf-8") == original

    with pytest.raises(AuthoringDeniedError, match="修复工作台管理"):
        await AuthoringCommands(storage).apply("official-book", proposal.id)

    approved = decide_proposal(
        layout.root,
        proposal.id,
        AuthoringProposalDecision(
            decision="accept",
            candidate_version=proposal.candidate_version,
            input_version=proposal.input_version,
            policy_version=proposal.policy_version,
        ),
    )
    assert approved.status == "approved"
    ready = commands.detail("official-book", proposed.case.case_id)
    assert ready.capabilities.publish is True
    assert layout.chapter_path(1).read_text(encoding="utf-8") == original

    published = await commands.publish(
        "official-book",
        ready.case.case_id,
        RepairPublishRequest(
            case_version=ready.case.version,
            candidate_version=1,
            authority_version=approved.policy_version,
        ),
    )
    assert published.case.status == "published"
    assert published.case.receipt is not None
    assert published.case.receipt.transaction_status == "committed"
    assert published.case.receipt.proposal_id == proposal.id
    assert layout.chapter_path(1).read_text(encoding="utf-8") == published.candidate_payload
    assert list((layout.states_dir / "final_revision_versions").rglob("*_before.md"))


async def test_editing_a_linked_candidate_revokes_old_approval_and_verification(
    tmp_path: Path,
) -> None:
    storage, layout, original = _official_chapter_storage(tmp_path)
    commands, verified = await _verified_official_case(storage, original)
    proposed = await commands.request_approval(
        "official-book",
        verified.case.case_id,
        RepairApprovalRequest(case_version=verified.case.version, candidate_version=1),
    )
    proposal = ProposalStore(layout.root).views()[0]
    decide_proposal(
        layout.root,
        proposal.id,
        AuthoringProposalDecision(
            decision="accept",
            candidate_version=proposal.candidate_version,
            input_version=proposal.input_version,
            policy_version=proposal.policy_version,
        ),
    )

    edited = commands.save_edit(
        "official-book",
        proposed.case.case_id,
        RepairCandidateEditRequest(
            case_version=proposed.case.version,
            candidate_version=1,
            replacement_text="沈昭在窗前忽然停步",
        ),
    )
    old_proposal = ProposalStore(layout.root).read(proposal.id)
    assert old_proposal["view"]["status"] == "stale"
    assert old_proposal["view"]["application_result"]["status"] == "superseded"
    assert edited.case.status == "needs_verification"
    assert edited.case.verification is None
    assert edited.case.proposal_id == ""
    assert edited.case.latest_candidate is not None
    assert edited.case.latest_candidate.version == 2
    assert layout.chapter_path(1).read_text(encoding="utf-8") == original


async def test_repair_receipt_recovery_reconciles_without_replaying_candidate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage, layout, original = _official_chapter_storage(tmp_path)
    commands, verified = await _verified_official_case(storage, original)
    proposed = await commands.request_approval(
        "official-book",
        verified.case.case_id,
        RepairApprovalRequest(case_version=verified.case.version, candidate_version=1),
    )
    proposal = ProposalStore(layout.root).views()[0]
    approved = decide_proposal(
        layout.root,
        proposal.id,
        AuthoringProposalDecision(
            decision="accept",
            candidate_version=proposal.candidate_version,
            input_version=proposal.input_version,
            policy_version=proposal.policy_version,
        ),
    )
    original_publish = AuthoringProposalChapterPublisher.publish

    async def lose_response(self, case, candidate, authority):
        await original_publish(self, case, candidate, authority)
        raise OSError("lost response after chapter commit")

    monkeypatch.setattr(AuthoringProposalChapterPublisher, "publish", lose_response)
    with pytest.raises(OSError, match="lost response"):
        await commands.publish(
            "official-book",
            proposed.case.case_id,
            RepairPublishRequest(
                case_version=proposed.case.version,
                candidate_version=1,
                authority_version=approved.policy_version,
            ),
        )
    interrupted = commands.detail("official-book", proposed.case.case_id)
    assert interrupted.case.receipt is not None
    assert interrupted.case.receipt.transaction_status == "prepared"
    committed_text = layout.chapter_path(1).read_text(encoding="utf-8")
    revision_count = len(list((layout.states_dir / "final_revision_versions").rglob("*_before.md")))

    monkeypatch.setattr(AuthoringProposalChapterPublisher, "publish", original_publish)
    recovered = await commands.recover(
        "official-book",
        interrupted.case.case_id,
        RepairRecoveryRequest(
            case_version=interrupted.case.version,
            receipt_id=interrupted.case.receipt.receipt_id,
        ),
    )
    assert recovered.case.status == "published"
    assert recovered.case.receipt is not None and recovered.case.receipt.recovered is True
    assert layout.chapter_path(1).read_text(encoding="utf-8") == committed_text
    assert (
        len(list((layout.states_dir / "final_revision_versions").rglob("*_before.md")))
        == revision_count
    )


async def test_repair_recovery_never_overwrites_later_author_text(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage, layout, original = _official_chapter_storage(tmp_path)
    commands, verified = await _verified_official_case(storage, original)
    proposed = await commands.request_approval(
        "official-book",
        verified.case.case_id,
        RepairApprovalRequest(case_version=verified.case.version, candidate_version=1),
    )
    proposal = ProposalStore(layout.root).views()[0]
    approved = decide_proposal(
        layout.root,
        proposal.id,
        AuthoringProposalDecision(
            decision="accept",
            candidate_version=proposal.candidate_version,
            input_version=proposal.input_version,
            policy_version=proposal.policy_version,
        ),
    )
    original_publish = AuthoringProposalChapterPublisher.publish

    async def lose_response(self, case, candidate, authority):
        await original_publish(self, case, candidate, authority)
        raise OSError("lost response after chapter commit")

    monkeypatch.setattr(AuthoringProposalChapterPublisher, "publish", lose_response)
    with pytest.raises(OSError, match="lost response"):
        await commands.publish(
            "official-book",
            proposed.case.case_id,
            RepairPublishRequest(
                case_version=proposed.case.version,
                candidate_version=1,
                authority_version=approved.policy_version,
            ),
        )
    interrupted = commands.detail("official-book", proposed.case.case_id)
    assert interrupted.case.receipt is not None
    later_text = "作者在发布响应丢失后亲笔写的新版本。"
    layout.chapter_path(1).write_text(later_text, encoding="utf-8")

    monkeypatch.setattr(AuthoringProposalChapterPublisher, "publish", original_publish)
    with pytest.raises(RuntimeError, match="未重放旧内容"):
        await commands.recover(
            "official-book",
            interrupted.case.case_id,
            RepairRecoveryRequest(
                case_version=interrupted.case.version,
                receipt_id=interrupted.case.receipt.receipt_id,
            ),
        )
    assert layout.chapter_path(1).read_text(encoding="utf-8") == later_text


async def test_repair_recovery_rejects_changed_proposal_before_any_prose_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage, layout, original = _official_chapter_storage(tmp_path)
    commands, verified = await _verified_official_case(storage, original)
    proposed = await commands.request_approval(
        "official-book",
        verified.case.case_id,
        RepairApprovalRequest(case_version=verified.case.version, candidate_version=1),
    )
    proposal = ProposalStore(layout.root).views()[0]
    approved = decide_proposal(
        layout.root,
        proposal.id,
        AuthoringProposalDecision(
            decision="accept",
            candidate_version=proposal.candidate_version,
            input_version=proposal.input_version,
            policy_version=proposal.policy_version,
        ),
    )
    original_publish = AuthoringProposalChapterPublisher.publish

    async def fail_before_commit(self, case, candidate, authority):
        raise OSError("failed before chapter commit")

    monkeypatch.setattr(AuthoringProposalChapterPublisher, "publish", fail_before_commit)
    with pytest.raises(OSError, match="before chapter commit"):
        await commands.publish(
            "official-book",
            proposed.case.case_id,
            RepairPublishRequest(
                case_version=proposed.case.version,
                candidate_version=1,
                authority_version=approved.policy_version,
            ),
        )
    interrupted = commands.detail("official-book", proposed.case.case_id)
    assert interrupted.case.receipt is not None
    proposal_store = ProposalStore(layout.root)
    data = proposal_store.read(proposal.id)
    data["view"]["candidate"] = "被篡改的提案候选"
    proposal_store.write(data)

    monkeypatch.setattr(AuthoringProposalChapterPublisher, "publish", original_publish)
    with pytest.raises(RuntimeError, match="未重放旧内容"):
        await commands.recover(
            "official-book",
            interrupted.case.case_id,
            RepairRecoveryRequest(
                case_version=interrupted.case.version,
                receipt_id=interrupted.case.receipt.receipt_id,
            ),
        )
    assert layout.chapter_path(1).read_text(encoding="utf-8") == original


def test_repair_http_contract_routes_approved_chapter_through_versioned_publisher(
    tmp_path: Path,
) -> None:
    storage, layout, original = _official_chapter_storage(tmp_path)
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_storage] = lambda: storage

    with TestClient(app) as client:
        source = client.get("/authoring/official-book/repairs/source?chapter=1").json()
        annotation = _annotation(
            original,
            artifact_id=source["artifact_id"],
            source_hash=source["source_hash"],
        ).model_copy(update={"chapter_number": 1})
        annotated = client.post(
            "/authoring/official-book/repairs/annotations",
            json=annotation.model_dump(mode="json"),
        ).json()
        case_id = annotated["case"]["case_id"]
        edited_response = client.post(
            f"/authoring/official-book/repairs/{case_id}/candidate",
            json={
                "case_version": annotated["case"]["version"],
                "candidate_version": 0,
                "replacement_text": "沈昭在门槛前骤然停步",
            },
        )
        assert edited_response.status_code == 200
        edited = edited_response.json()
        verified_response = client.post(
            f"/authoring/official-book/repairs/{case_id}/verify",
            json={"case_version": edited["case"]["version"], "candidate_version": 1},
        )
        assert verified_response.status_code == 200
        verified = verified_response.json()
        proposal_response = client.post(
            f"/authoring/official-book/repairs/{case_id}/approval",
            json={"case_version": verified["case"]["version"], "candidate_version": 1},
        )
        assert proposal_response.status_code == 200
        proposed = proposal_response.json()
        proposal = ProposalStore(layout.root).views()[0]
        approved = decide_proposal(
            layout.root,
            proposal.id,
            AuthoringProposalDecision(
                decision="accept",
                candidate_version=proposal.candidate_version,
                input_version=proposal.input_version,
                policy_version=proposal.policy_version,
            ),
        )
        assert layout.chapter_path(1).read_text(encoding="utf-8") == original
        publish_response = client.post(
            f"/authoring/official-book/repairs/{case_id}/publish",
            json={
                "case_version": proposed["case"]["version"],
                "candidate_version": 1,
                "authority_version": approved.policy_version,
            },
        )
        assert publish_response.status_code == 200
        assert publish_response.json()["case"]["status"] == "published"
        assert publish_response.json()["case"]["receipt"]["committed"] is True

    assert layout.chapter_path(1).read_text(encoding="utf-8") != original
