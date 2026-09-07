"""Tests for post-generation knowledge boundary verification (Task 7).

TDD RED tests — written before implementation.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from novel_forge.core.review.audit_profiles import get_audit_profile
from novel_forge.core.review.audit_taxonomy import classify_review_issue
from novel_forge.core.review.review_contracts import compile_repair_tickets_from_findings
from novel_forge.pipeline.long.services.knowledge_boundary_audit import (
    findings_from_adjudicated_issues,
    findings_from_boundary_issues,
    run_knowledge_boundary_audit,
)
from novel_forge.pipeline.long.services.post_gen_verification import (
    BoundaryIssue,
    Severity,
    build_knowledge_audit_card,
    verify_knowledge_boundaries,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_knowledge_card(character_cards: list[dict] | None = None) -> dict:
    """Build a minimal knowledge_card dict matching the pipeline format."""
    return {"character_cards": character_cards or []}


def _make_card(
    entity_id: str,
    character: str,
    known_facts: list[str] | None = None,
    suspicions: list[str] | None = None,
    misbeliefs: list[str] | None = None,
    secrets_kept: list[str] | None = None,
) -> dict:
    """Build a single character card."""
    return {
        "entity_id": entity_id,
        "character": character,
        "known_facts": known_facts or [],
        "suspicions": suspicions or [],
        "misbeliefs": misbeliefs or [],
        "secrets_kept": secrets_kept or [],
    }


def test_hidden_knowledge_prompt_keys_only_appear_in_audit_prompt() -> None:
    prompts_dir = Path(__file__).resolve().parents[2] / "novel_forge" / "prompts" / "prompts"
    offenders = []
    for template in prompts_dir.rglob("*.j2"):
        text = template.read_text(encoding="utf-8")
        if "hidden_candidates" in text or "audit_candidates" in text:
            offenders.append(template.relative_to(prompts_dir).as_posix())

    assert offenders == ["checking/knowledge_boundary_audit.j2"]


def _make_temporal_card(
    entity_id: str,
    character: str,
    source_chapter: int,
    known_facts: list[str] | None = None,
) -> dict:
    """Build a card with source_chapter for temporal testing."""
    card = _make_card(entity_id, character, known_facts=known_facts)
    card["source_chapter"] = source_chapter
    return card


# ---------------------------------------------------------------------------
# Tests: basic boundary verification
# ---------------------------------------------------------------------------


class TestVerifyKnowledgeBoundaries:
    """Test the main verify_knowledge_boundaries function."""


class TestNoLeaksDetected:
    """Verify no issues when draft is clean."""

    def test_empty_draft_no_issues(self) -> None:
        """Empty draft should produce no issues."""
        card = _make_card("char_a", "Alice", known_facts=["Alice knows magic"])
        knowledge_card = _make_knowledge_card([card])
        issues = verify_knowledge_boundaries(
            draft_text="",
            knowledge_card=knowledge_card,
            pov_character="Alice",
            current_chapter=5,
        )
        assert issues == []

    def test_pov_character_knowledge_appears_freely(self) -> None:
        """POV character's own knowledge should appear without issues."""
        card = _make_card("char_a", "Alice", known_facts=["Alice knows the secret code"])
        knowledge_card = _make_knowledge_card([card])
        issues = verify_knowledge_boundaries(
            draft_text="Alice knows the secret code and used it to open the door.",
            knowledge_card=knowledge_card,
            pov_character="Alice",
            current_chapter=5,
        )
        assert issues == []

    def test_public_knowledge_no_issues(self) -> None:
        """Public knowledge entries should not trigger issues even if they appear."""
        card = _make_card(
            "char_b", "Bob",
            known_facts=["The town was founded in 1850"],
        )
        knowledge_card = _make_knowledge_card([card])
        # Bob's knowledge that is NOT the POV character, but we treat it as
        # appearing in the draft — this is a leak
        issues = verify_knowledge_boundaries(
            draft_text="The town was founded in 1850.",
            knowledge_card=knowledge_card,
            pov_character="Alice",
            current_chapter=5,
        )
        # This is PRIVATE by default so should be flagged
        assert len(issues) > 0


class TestPrivateKnowledgeLeak:
    """Verify PRIVATE knowledge from non-POV entities is detected."""

    def test_private_fact_from_other_character_detected(self) -> None:
        """PRIVATE fact from non-POV character appearing in draft is a leak."""
        card = _make_card(
            "char_b", "Bob",
            known_facts=["the treasure is hidden beneath the old oak tree"],
        )
        knowledge_card = _make_knowledge_card([card])
        issues = verify_knowledge_boundaries(
            draft_text="Alice discovered that the treasure is hidden beneath the old oak tree.",
            knowledge_card=knowledge_card,
            pov_character="Alice",
            current_chapter=5,
        )
        assert len(issues) >= 1
        issue = issues[0]
        assert isinstance(issue, BoundaryIssue)
        assert issue.severity in (Severity.LOW, Severity.MEDIUM, Severity.HIGH)
        assert "char_b" in issue.entry_id or "Bob" in issue.description

    def test_short_phrases_not_flagged(self) -> None:
        """Phrases shorter than 5 characters should not trigger detection."""
        card = _make_card(
            "char_b", "Bob",
            known_facts=["yes"],
        )
        knowledge_card = _make_knowledge_card([card])
        issues = verify_knowledge_boundaries(
            draft_text="Alice said yes to the proposal.",
            knowledge_card=knowledge_card,
            pov_character="Alice",
            current_chapter=5,
        )
        assert issues == []

    def test_multiple_leaks_detected(self) -> None:
        """Multiple private facts from different characters all detected."""
        card_b = _make_card(
            "char_b", "Bob",
            known_facts=["the password is alpha bravo charlie"],
        )
        card_c = _make_card(
            "char_c", "Charlie",
            known_facts=["the meeting point is the abandoned warehouse"],
        )
        knowledge_card = _make_knowledge_card([card_b, card_c])
        issues = verify_knowledge_boundaries(
            draft_text=(
                "Alice typed the password is alpha bravo charlie into the terminal. "
                "Then she headed to the meeting point is the abandoned warehouse."
            ),
            knowledge_card=knowledge_card,
            pov_character="Alice",
            current_chapter=5,
        )
        assert len(issues) >= 2


class TestTemporalLeak:
    """Verify knowledge from future chapters is detected."""

    def test_future_knowledge_detected(self) -> None:
        """Fact from a future chapter appearing in current draft is a leak."""
        card = _make_temporal_card(
            "char_b", "Bob",
            source_chapter=10,
            known_facts=["the bomb explodes at midnight"],
        )
        knowledge_card = _make_knowledge_card([card])
        issues = verify_knowledge_boundaries(
            draft_text="Alice somehow knew the bomb explodes at midnight.",
            knowledge_card=knowledge_card,
            pov_character="Alice",
            current_chapter=5,
        )
        assert len(issues) >= 1
        issue = issues[0]
        assert issue.severity in (Severity.LOW, Severity.MEDIUM, Severity.HIGH)

    def test_current_chapter_knowledge_not_flagged(self) -> None:
        """Fact from current chapter should not be flagged as temporal leak."""
        card = _make_temporal_card(
            "char_b", "Bob",
            source_chapter=5,
            known_facts=["the library closes at sunset"],
        )
        knowledge_card = _make_knowledge_card([card])
        issues = verify_knowledge_boundaries(
            draft_text="Bob mentioned the library closes at sunset.",
            knowledge_card=knowledge_card,
            pov_character="Alice",
            current_chapter=5,
        )
        # source_chapter == current_chapter: not a temporal leak
        # But may still be flagged as privacy leak (PRIVATE, non-POV)
        temporal_issues = [i for i in issues if "temporal" in i.description.lower() or "future" in i.description.lower()]
        assert len(temporal_issues) == 0

    def test_source_chapter_zero_not_temporal_leak(self) -> None:
        """source_chapter=0 means 'always visible' — not a temporal leak."""
        card = _make_temporal_card(
            "char_b", "Bob",
            source_chapter=0,
            known_facts=["water boils at one hundred degrees"],
        )
        knowledge_card = _make_knowledge_card([card])
        issues = verify_knowledge_boundaries(
            draft_text="Everyone knows water boils at one hundred degrees.",
            knowledge_card=knowledge_card,
            pov_character="Alice",
            current_chapter=5,
        )
        temporal_issues = [i for i in issues if "temporal" in i.description.lower() or "future" in i.description.lower()]
        assert len(temporal_issues) == 0


class TestIssueStructure:
    """Verify Issue dataclass fields and severity levels."""

    def test_issue_has_required_fields(self) -> None:
        """BoundaryIssue must have chapter, description, severity, entry_id."""
        card = _make_card(
            "char_b", "Bob",
            known_facts=["the secret location is mount everest base camp"],
        )
        knowledge_card = _make_knowledge_card([card])
        issues = verify_knowledge_boundaries(
            draft_text="She found the secret location is mount everest base camp.",
            knowledge_card=knowledge_card,
            pov_character="Alice",
            current_chapter=3,
        )
        assert len(issues) >= 1
        issue = issues[0]
        assert hasattr(issue, "chapter")
        assert hasattr(issue, "description")
        assert hasattr(issue, "severity")
        assert hasattr(issue, "entry_id")
        assert isinstance(issue.chapter, int)
        assert isinstance(issue.description, str)
        assert isinstance(issue.severity, Severity)
        assert isinstance(issue.entry_id, str)

    def test_severity_levels_exist(self) -> None:
        """Severity enum must have LOW, MEDIUM, HIGH."""
        assert hasattr(Severity, "LOW")
        assert hasattr(Severity, "MEDIUM")
        assert hasattr(Severity, "HIGH")


class TestEdgeCases:
    """Edge cases and boundary conditions."""

    def test_empty_knowledge_card_no_issues(self) -> None:
        """Empty knowledge_card should produce no issues."""
        issues = verify_knowledge_boundaries(
            draft_text="Some text here.",
            knowledge_card={},
            pov_character="Alice",
            current_chapter=5,
        )
        assert issues == []

    def test_missing_character_cards_key(self) -> None:
        """knowledge_card without character_cards key should not crash."""
        issues = verify_knowledge_boundaries(
            draft_text="Some text here.",
            knowledge_card={"other_key": []},
            pov_character="Alice",
            current_chapter=5,
        )
        assert issues == []

    def test_empty_fact_not_flagged(self) -> None:
        """Empty fact strings should not trigger false positives."""
        card = _make_card("char_b", "Bob", known_facts=[""])
        knowledge_card = _make_knowledge_card([card])
        issues = verify_knowledge_boundaries(
            draft_text="Bob walked into the room.",
            knowledge_card=knowledge_card,
            pov_character="Alice",
            current_chapter=5,
        )
        assert issues == []

    def test_pov_character_empty_string(self) -> None:
        """Empty POV character means all entries checked."""
        card = _make_card("char_b", "Bob", known_facts=["the hidden passage behind the bookshelf"])
        knowledge_card = _make_knowledge_card([card])
        issues = verify_knowledge_boundaries(
            draft_text="She found the hidden passage behind the bookshelf.",
            knowledge_card=knowledge_card,
            pov_character="",
            current_chapter=5,
        )
        # With empty POV, all PRIVATE entries from non-POV should still be checked
        assert len(issues) >= 1

    def test_no_fact_in_draft_produces_no_issue(self) -> None:
        """When fact phrase does NOT appear in draft, no issue produced."""
        card = _make_card(
            "char_b", "Bob",
            known_facts=["the dragon sleeps inside the volcano"],
        )
        knowledge_card = _make_knowledge_card([card])
        issues = verify_knowledge_boundaries(
            draft_text="Alice walked through the forest peacefully.",
            knowledge_card=knowledge_card,
            pov_character="Alice",
            current_chapter=5,
        )
        assert issues == []

    def test_suspicions_and_secrets_also_checked(self) -> None:
        """Suspicions and secrets_kept should also be boundary-checked."""
        card = _make_card(
            "char_b", "Bob",
            known_facts=[],
            suspicions=["the mayor is secretly a werewolf"],
            secrets_kept=["the ancient amulet controls time"],
        )
        knowledge_card = _make_knowledge_card([card])
        issues = verify_knowledge_boundaries(
            draft_text=(
                "Alice learned that the mayor is secretly a werewolf. "
                "She also discovered the ancient amulet controls time."
            ),
            knowledge_card=knowledge_card,
            pov_character="Alice",
            current_chapter=5,
        )
        assert len(issues) >= 2


class TestAuditCardMode:
    """Verify the local-only audit card catches hidden knowledge leaks."""

    def test_audit_card_keeps_hidden_candidates(self) -> None:
        kernel_context = {
            "entities": [
                {"entity_id": "char_alice", "name": "Alice", "entity_type": "character"},
                {"entity_id": "char_bob", "name": "Bob", "entity_type": "character"},
            ],
            "knowledge_ledger": [
                {
                    "entry_id": "k_public",
                    "entity_id": "char_bob",
                    "fact": "镇子建于一八五零年",
                    "knowledge_type": "known",
                    "source_chapter": 1,
                    "visibility": "public",
                },
                {
                    "entry_id": "k_private",
                    "entity_id": "char_bob",
                    "fact": "钥匙藏在旧橡树下",
                    "knowledge_type": "known",
                    "source_chapter": 1,
                    "visibility": "private",
                },
                {
                    "entry_id": "k_future",
                    "entity_id": "char_bob",
                    "fact": "炸弹会在午夜爆炸",
                    "knowledge_type": "known",
                    "source_chapter": 10,
                    "visibility": "public",
                },
            ],
        }

        card = build_knowledge_audit_card(
            kernel_context=kernel_context,
            chapter_contract={},
            current_chapter=5,
            pov_character="Alice",
        )

        hidden_ids = {item["entry_id"] for item in card["hidden_candidates"]}
        assert hidden_ids == {"k_private", "k_future"}
        hidden_by_id = {item["entry_id"]: item for item in card["hidden_candidates"]}
        assert hidden_by_id["k_private"]["entity_name"] == "Bob"
        assert "character" not in hidden_by_id["k_private"]
        assert "private_non_pov" in hidden_by_id["k_private"]["boundary_reasons"]
        assert "reasons" not in hidden_by_id["k_private"]
        assert hidden_by_id["k_private"]["knowledge_type"] == "known"
        assert hidden_by_id["k_private"]["confidence"] == 1.0

    def test_audit_card_allows_current_chapter_knowledge_op(self) -> None:
        kernel_context = {
            "entities": [{"entity_id": "char_bob", "name": "Bob", "entity_type": "character"}],
            "knowledge_ledger": [
                {
                    "entry_id": "k_private",
                    "entity_id": "char_bob",
                    "fact": "钥匙藏在旧橡树下",
                    "knowledge_type": "known",
                    "source_chapter": 1,
                    "visibility": "private",
                },
            ],
        }

        card = build_knowledge_audit_card(
            kernel_context=kernel_context,
            chapter_contract={
                "knowledge_ops": [
                    {"character": "Bob", "operation": "revealed", "fact": "钥匙藏在旧橡树下"}
                ]
            },
            current_chapter=5,
            pov_character="Alice",
        )

        assert card["hidden_candidates"] == []

    def test_hidden_candidate_exact_leak_is_high_confidence(self) -> None:
        audit_card = {
            "hidden_candidates": [
                {
                    "entry_id": "k_future",
                    "entity_id": "char_bob",
                    "entity_name": "Bob",
                    "fact": "炸弹会在午夜爆炸",
                    "boundary_reasons": ["future_source_chapter"],
                }
            ]
        }

        issues = verify_knowledge_boundaries(
            draft_text="Alice suddenly knew that 炸弹会在午夜爆炸.",
            knowledge_card=audit_card,
            pov_character="Alice",
            current_chapter=5,
        )

        assert len(issues) == 1
        assert issues[0].severity == Severity.HIGH
        assert issues[0].confidence >= 0.85
        assert issues[0].evidence_quote == "炸弹会在午夜爆炸"

    def test_high_confidence_issue_becomes_blocking_finding(self) -> None:
        issue = BoundaryIssue(
            chapter=5,
            description="future leak",
            severity=Severity.HIGH,
            entry_id="k_future",
            evidence_quote="炸弹会在午夜爆炸",
            confidence=0.94,
            reason="future_source_chapter",
        )

        findings = findings_from_boundary_issues(
            [issue],
            chapter_number=5,
            current_text="Alice知道炸弹会在午夜爆炸。",
            block_high_confidence=True,
            source_module="post_gen_verification",
            candidate_by_id={
                "k_future": {
                    "entry_id": "k_future",
                    "fact": "炸弹会在午夜爆炸",
                    "fact_fingerprint": "fp1",
                }
            },
        )

        assert len(findings) == 1
        assert findings[0].blocks_finalize is True
        assert findings[0].issue_type == "knowledge_leak"
        assert findings[0].metadata["fact_fingerprint"] == "fp1"

    def test_prescreen_issue_without_exact_hidden_match_does_not_block_fallback(self) -> None:
        issue = BoundaryIssue(
            chapter=5,
            description="future leak",
            severity=Severity.HIGH,
            entry_id="k_future",
            evidence_quote="午夜爆炸",
            confidence=0.94,
            reason="future_source_chapter",
        )

        findings = findings_from_boundary_issues(
            [issue],
            chapter_number=5,
            current_text="Alice知道炸弹会在午夜爆炸。",
            block_high_confidence=True,
            source_module="post_gen_verification",
            candidate_by_id={
                "k_future": {
                    "entry_id": "k_future",
                    "fact": "炸弹会在午夜爆炸",
                    "fact_fingerprint": "fp1",
                }
            },
        )

        assert len(findings) == 1
        assert findings[0].blocks_finalize is False

    def test_prescreen_finding_ids_do_not_depend_on_issue_order(self) -> None:
        issue_a = BoundaryIssue(
            chapter=5,
            description="future leak",
            severity=Severity.HIGH,
            entry_id="k_future",
            evidence_quote="炸弹会在午夜爆炸",
            confidence=0.94,
            reason="future_source_chapter",
        )
        issue_b = BoundaryIssue(
            chapter=5,
            description="private knowledge",
            severity=Severity.HIGH,
            entry_id="k_private",
            evidence_quote="门后藏着账册",
            confidence=0.91,
            reason="private_fact",
        )

        kwargs = {
            "chapter_number": 5,
            "current_text": "Alice知道炸弹会在午夜爆炸，也知道门后藏着账册。",
            "block_high_confidence": True,
            "source_module": "post_gen_verification",
            "candidate_by_id": {
                "k_future": {
                    "entry_id": "k_future",
                    "fact": "炸弹会在午夜爆炸",
                    "fact_fingerprint": "fp1",
                },
                "k_private": {
                    "entry_id": "k_private",
                    "fact": "门后藏着账册",
                    "fact_fingerprint": "fp2",
                },
            },
        }

        first = findings_from_boundary_issues([issue_a, issue_b], **kwargs)
        second = findings_from_boundary_issues([issue_b, issue_a], **kwargs)

        first_ids = {finding.metadata["entry_id"]: finding.finding_id for finding in first}
        second_ids = {finding.metadata["entry_id"]: finding.finding_id for finding in second}
        assert first_ids == second_ids
        assert all(
            finding_id.startswith("post_gen_verification_ch005-knowledge_boundary-")
            for finding_id in first_ids.values()
        )

    def test_llm_leak_adjudication_becomes_blocking_finding(self) -> None:
        findings = findings_from_adjudicated_issues(
            [
                {
                    "decision": "leak",
                    "issue_type": "knowledge_leak",
                    "severity": "high",
                    "confidence": 0.86,
                    "entry_id": "k_private",
                    "evidence_quote": "她知道门后藏着账册",
                    "paragraph_start": 2,
                    "paragraph_end": 2,
                    "reason": "非 POV 私密知识被直接确认。",
                    "repair_goal": "改成角色只观察到异常，不确认隐藏事实。",
                }
            ],
            chapter_number=4,
            current_text="她知道门后藏着账册。",
            candidate_by_id={
                "k_private": {
                    "entry_id": "k_private",
                    "fact": "门后藏着账册",
                    "fact_fingerprint": "fp2",
                }
            },
        )

        assert len(findings) == 1
        assert findings[0].issue_type == "knowledge_leak"
        assert findings[0].blocks_finalize is True
        assert findings[0].metadata["decision"] == "leak"
        assert findings[0].metadata["fact_fingerprint"] == "fp2"

    def test_llm_adjudication_redacts_hidden_fact_from_finding_text(self) -> None:
        findings = findings_from_adjudicated_issues(
            [
                {
                    "decision": "leak",
                    "issue_type": "knowledge_leak",
                    "severity": "high",
                    "confidence": 0.9,
                    "entry_id": "k_private",
                    "evidence_quote": "她知道门后藏着账册",
                    "reason": "LLM reason accidentally repeats 门后藏着账册",
                    "repair_goal": "删除 门后藏着账册 的确认。",
                }
            ],
            chapter_number=4,
            current_text="她知道门后藏着账册。",
            candidate_by_id={
                "k_private": {
                    "entry_id": "k_private",
                    "fact": "门后藏着账册",
                    "fact_fingerprint": "fp2",
                }
            },
        )

        assert "门后藏着账册" not in findings[0].summary
        assert "门后藏着账册" not in findings[0].repair_goal
        assert "[hidden_fact]" in findings[0].summary

    def test_llm_ambiguous_adjudication_reports_without_blocking(self) -> None:
        findings = findings_from_adjudicated_issues(
            [
                {
                    "decision": "ambiguous",
                    "issue_type": "knowledge_boundary_ambiguous",
                    "severity": "high",
                    "confidence": 0.9,
                    "entry_id": "k_private",
                    "evidence_quote": "她似乎猜到了",
                    "reason": "可能是猜测，也可能是越界确认。",
                    "repair_goal": "人工复核。",
                }
            ],
            chapter_number=4,
            current_text="她似乎猜到了。",
            candidate_by_id={"k_private": {"entry_id": "k_private", "fact_fingerprint": "fp2"}},
        )

        assert len(findings) == 1
        assert findings[0].issue_type == "knowledge_boundary_ambiguous"
        assert findings[0].severity == "medium"
        assert findings[0].blocks_finalize is False

    def test_adjudicated_finding_ids_do_not_depend_on_issue_order(self) -> None:
        issue_a = {
            "decision": "leak",
            "issue_type": "knowledge_leak",
            "severity": "high",
            "confidence": 0.86,
            "entry_id": "k_private",
            "evidence_quote": "她知道门后藏着账册",
            "paragraph_start": 2,
            "paragraph_end": 2,
            "reason": "非 POV 私密知识被直接确认。",
            "repair_goal": "改成角色只观察到异常，不确认隐藏事实。",
        }
        issue_b = {
            "decision": "premature_reveal",
            "issue_type": "premature_reveal",
            "severity": "high",
            "confidence": 0.88,
            "entry_id": "k_future",
            "evidence_quote": "他确认了未来的背叛",
            "paragraph_start": 4,
            "paragraph_end": 4,
            "reason": "未来揭示被提前确认。",
            "repair_goal": "改成不完整线索，不确认未来事实。",
        }

        kwargs = {
            "chapter_number": 4,
            "current_text": "她知道门后藏着账册。他确认了未来的背叛。",
            "candidate_by_id": {
                "k_private": {"entry_id": "k_private", "fact_fingerprint": "fp2"},
                "k_future": {"entry_id": "k_future", "fact_fingerprint": "fp3"},
            },
        }

        first = findings_from_adjudicated_issues([issue_a, issue_b], **kwargs)
        second = findings_from_adjudicated_issues([issue_b, issue_a], **kwargs)

        first_ids = {finding.metadata["entry_id"]: finding.finding_id for finding in first}
        second_ids = {finding.metadata["entry_id"]: finding.finding_id for finding in second}
        assert first_ids == second_ids
        assert all(
            finding_id.startswith("knowledge_boundary_audit_ch004-knowledge_boundary-")
            for finding_id in first_ids.values()
        )

    def test_adjudicated_missing_entry_id_fallback_is_order_independent(self) -> None:
        issue_without_entry = {
            "decision": "ambiguous",
            "issue_type": "knowledge_boundary_ambiguous",
            "severity": "medium",
            "confidence": 0.72,
            "evidence_quote": "她似乎已经知道账册位置",
            "reason": "缺少 entry_id 的兼容输入仍应有稳定兜底身份。",
            "repair_goal": "人工复核。",
        }
        issue_with_entry = {
            "decision": "leak",
            "issue_type": "knowledge_leak",
            "severity": "high",
            "confidence": 0.86,
            "entry_id": "k_private",
            "evidence_quote": "她知道门后藏着账册",
            "reason": "非 POV 私密知识被直接确认。",
            "repair_goal": "改成角色只观察到异常，不确认隐藏事实。",
        }

        kwargs = {
            "chapter_number": 4,
            "current_text": "她似乎已经知道账册位置。她知道门后藏着账册。",
        }

        first = findings_from_adjudicated_issues(
            [issue_without_entry, issue_with_entry],
            **kwargs,
        )
        second = findings_from_adjudicated_issues(
            [issue_with_entry, issue_without_entry],
            **kwargs,
        )

        first_unknown = [finding for finding in first if finding.metadata["entry_id"].startswith("unknown_")]
        second_unknown = [
            finding for finding in second if finding.metadata["entry_id"].startswith("unknown_")
        ]
        assert len(first_unknown) == len(second_unknown) == 1
        assert first_unknown[0].finding_id == second_unknown[0].finding_id

    def test_legitimate_reveal_adjudication_is_not_a_finding(self) -> None:
        findings = findings_from_adjudicated_issues(
            [
                {
                    "decision": "legitimate_reveal",
                    "issue_type": "none",
                    "severity": "info",
                    "confidence": 0.9,
                    "entry_id": "k_allowed",
                    "evidence_quote": "她从信中读到真相",
                    "reason": "当前章已有获知证据。",
                    "repair_goal": "",
                }
            ],
            chapter_number=4,
            current_text="她从信中读到真相。",
        )

        assert findings == []

    def test_knowledge_boundary_dimension_routes_to_existing_repair_ticket_flow(self) -> None:
        classification = classify_review_issue(
            issue_type="premature_reveal",
            source_module="knowledge_boundary_audit",
            dimension="knowledge_boundary",
        )

        assert classification.primary_dimension == "knowledge_boundary"
        assert get_audit_profile("knowledge_boundary").repair_lane == "text_repair"

        findings = findings_from_adjudicated_issues(
            [
                {
                    "decision": "premature_reveal",
                    "issue_type": "premature_reveal",
                    "severity": "high",
                    "confidence": 0.85,
                    "entry_id": "k_future",
                    "evidence_quote": "他确认了未来的背叛",
                    "reason": "未来揭示被提前确认。",
                    "repair_goal": "改成不完整线索，不确认未来事实。",
                }
            ],
            chapter_number=6,
            current_text="他确认了未来的背叛。",
            candidate_by_id={"k_future": {"entry_id": "k_future", "fact_fingerprint": "fp3"}},
        )
        tickets = compile_repair_tickets_from_findings(findings)

        assert len(tickets) == 1
        assert tickets[0].dimension == "knowledge_boundary"
        assert tickets[0].metadata["fact_fingerprint"] == "fp3"
        assert "隐藏" not in tickets[0].repair_goal


class _AuditReportStorage:
    def __init__(self) -> None:
        self.saved: dict[Path, dict] = {}

    def save_json(self, path: Path, payload: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.saved[path] = payload


def _audit_runner(events: list[tuple[str, dict]]) -> SimpleNamespace:
    return SimpleNamespace(
        _router=object(),
        _builder=object(),
        _settings=SimpleNamespace(temp_knowledge_boundary_audit=0.1),
        _on_step=lambda step, payload: events.append((step, payload)),
    )


async def test_knowledge_boundary_audit_persists_pass_report_without_hidden_candidates(
    monkeypatch,
    tmp_path: Path,
) -> None:
    async def _composer(_runner, _bundle):
        return SimpleNamespace(compose_draft_input=lambda _chapter: {"knowledge_ledger": []})

    monkeypatch.setattr(
        "novel_forge.pipeline.long.services.knowledge_boundary_audit.load_story_kernel_composer",
        _composer,
    )
    events: list[tuple[str, dict]] = []
    storage = _AuditReportStorage()
    report_path = tmp_path / "reports" / "chapter_002_knowledge_boundary_verification.json"
    bundle = SimpleNamespace(
        layout=SimpleNamespace(knowledge_boundary_report_path=lambda _chapter: report_path),
        chapter_outline=SimpleNamespace(pov_character="林溪"),
    )

    findings = await run_knowledge_boundary_audit(
        runner=_audit_runner(events),
        storage=storage,
        bundle=bundle,
        packet=SimpleNamespace(chapter_contract={}),
        current_text="她只看见窗外落雨。",
        chapter_number=2,
        stage="review_finalize",
        block_high_confidence=True,
    )

    assert findings == []
    payload = storage.saved[report_path]
    assert payload["verdict"] == "pass"
    assert payload["audit_skipped_reason"] == "no_hidden_candidates"
    assert payload["hidden_candidate_count"] == 0
    assert payload["findings"] == []
    assert payload["repair_tickets"] == []
    assert events[0][0] == "knowledge_boundary_verification"


async def test_knowledge_boundary_audit_skips_llm_but_persists_pass_when_prescreen_clean(
    monkeypatch,
    tmp_path: Path,
) -> None:
    hidden_fact = "门后藏着账册"

    async def _composer(_runner, _bundle):
        return SimpleNamespace(
            compose_draft_input=lambda _chapter: {
                "knowledge_ledger": [
                    {
                        "entry_id": "k_future",
                        "entity_id": "char_b",
                        "fact": hidden_fact,
                        "source_chapter": 9,
                        "visibility": "private",
                    }
                ]
            }
        )

    class _NoLlmStep:
        def __init__(self, *_args, **_kwargs) -> None:
            raise AssertionError("clean prescreen should not call the LLM audit step")

    monkeypatch.setattr(
        "novel_forge.pipeline.long.services.knowledge_boundary_audit.load_story_kernel_composer",
        _composer,
    )
    monkeypatch.setattr(
        "novel_forge.pipeline.long.services.knowledge_boundary_audit.KnowledgeBoundaryAuditStep",
        _NoLlmStep,
    )
    storage = _AuditReportStorage()
    report_path = tmp_path / "reports" / "chapter_002_knowledge_boundary_verification.json"
    bundle = SimpleNamespace(
        layout=SimpleNamespace(knowledge_boundary_report_path=lambda _chapter: report_path),
        chapter_outline=SimpleNamespace(pov_character="林溪"),
    )

    findings = await run_knowledge_boundary_audit(
        runner=_audit_runner([]),
        storage=storage,
        bundle=bundle,
        packet=SimpleNamespace(chapter_contract={}),
        current_text="她只看见窗外落雨。",
        chapter_number=2,
        stage="review_finalize",
        block_high_confidence=True,
    )

    payload = storage.saved[report_path]
    assert findings == []
    assert payload["verdict"] == "pass"
    assert payload["audit_skipped_reason"] == "no_prescreen_hits"
    assert payload["hidden_candidate_count"] == 1
    assert payload["prescreen_hit_count"] == 0
    assert hidden_fact not in str(payload)


async def test_knowledge_boundary_audit_report_contains_standard_repair_tickets(
    monkeypatch,
    tmp_path: Path,
) -> None:
    hidden_fact = "门后藏着账册"

    async def _composer(_runner, _bundle):
        return SimpleNamespace(
            compose_draft_input=lambda _chapter: {
                "knowledge_ledger": [
                    {
                        "entry_id": "k_future",
                        "entity_id": "char_b",
                        "fact": hidden_fact,
                        "source_chapter": 9,
                        "visibility": "private",
                    }
                ]
            }
        )

    class _FakeAuditStep:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        async def run(self, input_data):
            assert input_data.audit_candidates[0]["fact"] == hidden_fact
            assert input_data.audit_candidates[0]["entry_id"] == "k_future"
            assert "candidate_id" not in input_data.audit_candidates[0]
            return SimpleNamespace(
                verdict="issues_found",
                issues=[
                    {
                        "decision": "leak",
                        "issue_type": "knowledge_leak",
                        "severity": "high",
                        "confidence": 0.9,
                        "entry_id": "k_future",
                        "evidence_quote": hidden_fact,
                        "reason": "正文确认了当前章不可见的事实。",
                        "repair_goal": "移除该确认，改为可见线索或怀疑。",
                    }
                ],
            )

    monkeypatch.setattr(
        "novel_forge.pipeline.long.services.knowledge_boundary_audit.load_story_kernel_composer",
        _composer,
    )
    monkeypatch.setattr(
        "novel_forge.pipeline.long.services.knowledge_boundary_audit.KnowledgeBoundaryAuditStep",
        _FakeAuditStep,
    )
    storage = _AuditReportStorage()
    report_path = tmp_path / "reports" / "chapter_002_knowledge_boundary_verification.json"
    bundle = SimpleNamespace(
        layout=SimpleNamespace(knowledge_boundary_report_path=lambda _chapter: report_path),
        chapter_outline=SimpleNamespace(pov_character="林溪"),
    )

    findings = await run_knowledge_boundary_audit(
        runner=_audit_runner([]),
        storage=storage,
        bundle=bundle,
        packet=SimpleNamespace(chapter_contract={}),
        current_text=f"她突然意识到，{hidden_fact}。",
        chapter_number=2,
        stage="review_finalize",
        block_high_confidence=True,
    )

    payload = storage.saved[report_path]
    assert len(findings) == 1
    assert payload["verdict"] == "issues_found"
    assert payload["repair_tickets"][0]["dimension"] == "knowledge_boundary"
    assert payload["repair_tickets"][0]["metadata"]["fact_fingerprint"]


async def test_knowledge_boundary_audit_llm_failure_uses_local_fallback(
    monkeypatch,
    tmp_path: Path,
) -> None:
    hidden_fact = "账册藏在后院暗格里"

    async def _composer(_runner, _bundle):
        return SimpleNamespace(
            compose_draft_input=lambda _chapter: {
                "knowledge_ledger": [
                    {
                        "entry_id": "k_private",
                        "entity_id": "char_b",
                        "entity_name": "周岚",
                        "fact": hidden_fact,
                        "source_chapter": 9,
                        "visibility": "private",
                    }
                ]
            }
        )

    class _FailingAuditStep:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        async def run(self, _input_data):
            raise RuntimeError("provider unavailable")

    monkeypatch.setattr(
        "novel_forge.pipeline.long.services.knowledge_boundary_audit.load_story_kernel_composer",
        _composer,
    )
    monkeypatch.setattr(
        "novel_forge.pipeline.long.services.knowledge_boundary_audit.KnowledgeBoundaryAuditStep",
        _FailingAuditStep,
    )
    storage = _AuditReportStorage()
    report_path = tmp_path / "reports" / "chapter_002_knowledge_boundary_verification.json"
    bundle = SimpleNamespace(
        layout=SimpleNamespace(knowledge_boundary_report_path=lambda _chapter: report_path),
        chapter_outline=SimpleNamespace(pov_character="林溪"),
    )

    findings = await run_knowledge_boundary_audit(
        runner=_audit_runner([]),
        storage=storage,
        bundle=bundle,
        packet=SimpleNamespace(chapter_contract={}),
        current_text=f"林溪突然确认，{hidden_fact}。",
        chapter_number=2,
        stage="archive_pre_persist",
        block_high_confidence=True,
    )

    payload = storage.saved[report_path]
    assert payload["fallback_used"] is True
    assert payload["verdict"] == "issues_found"
    assert len(findings) == 1
    assert findings[0].source_module == "post_gen_verification_local_fallback"
    assert findings[0].metadata["local_fallback"] is True
    assert findings[0].blocks_finalize is True
