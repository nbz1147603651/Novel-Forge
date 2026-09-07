"""Tests for ``CoherenceClaim._coerce_payoff_null_to_empty`` validator.

Covers the ``payoff_id`` / ``payoff_kind`` ``mode="before"`` field validator
added in T12 to handle LLM outputs that emit ``null`` for optional payoff
metadata instead of omitting the field entirely.
"""

from __future__ import annotations

from typing import Any

from novel_forge.core.schemas.init_coherence import CoherenceClaim

# ── Helpers ────────────────────────────────────────────────────

_COHERENCE_CLAIM_REQUIRED: dict[str, Any] = {
    "claim_id": "c1",
    "artifact": "story_bible",
    "source_path": "/dev/chaos",
    "cognitive_subjects": ["玄昱"],
    "cognitive_object": "沈清漪身份",
    "cognitive_level": "confirmed",
    "action_level": "internal",
    "reader_awareness": "partial",
    "character_knowledge_coverage": {"玄昱": "partial"},
    "cognitive_chapter": None,
    "public_reveal_chapter": None,
    "foreshadow_chapters": [],
    "claim_text": "something happened",
    "evidence": "sourced",
}


def _make_claim(**overrides: Any) -> dict[str, Any]:
    """Return a dict with required CoherenceClaim fields + overrides."""
    data = dict(_COHERENCE_CLAIM_REQUIRED)
    data.update(overrides)
    return data


# ── Tests ──────────────────────────────────────────────────────


class TestPayoffNullToEmpty:
    """Verify ``payoff_id`` / ``payoff_kind`` coerce ``null`` to ``""``."""

    def test_both_null_coerced_to_empty(self) -> None:
        """``payoff_id: null, payoff_kind: null`` → both become ``""``."""
        claim = CoherenceClaim(
            **_make_claim(
                payoff_id=None,
                payoff_kind=None,
            )
        )
        assert claim.payoff_id == ""
        assert claim.payoff_kind == ""

    def test_valid_values_preserved(self) -> None:
        """``payoff_id: "S1", payoff_kind: "三段式伏笔回收"`` → kept as-is."""
        claim = CoherenceClaim(
            **_make_claim(
                payoff_id="S1",
                payoff_kind="三段式伏笔回收",
            )
        )
        assert claim.payoff_id == "S1"
        assert claim.payoff_kind == "三段式伏笔回收"

    def test_empty_string_preserved(self) -> None:
        """``payoff_id: ""`` → stays ``""``."""
        claim = CoherenceClaim(
            **_make_claim(
                payoff_id="",
                payoff_kind="",
            )
        )
        assert claim.payoff_id == ""
        assert claim.payoff_kind == ""

    def test_missing_fields_default_to_empty(self) -> None:
        """Omitting ``payoff_id`` / ``payoff_kind`` → Pydantic default ``""``."""
        data = dict(_COHERENCE_CLAIM_REQUIRED)
        # Explicitly remove the keys to simulate omission
        data.pop("payoff_id", None)
        data.pop("payoff_kind", None)
        claim = CoherenceClaim(**data)
        assert claim.payoff_id == ""
        assert claim.payoff_kind == ""

    def test_whitespace_stripped(self) -> None:
        """``payoff_id: "  P1  "`` → ``"P1"`` (strip)."""
        claim = CoherenceClaim(
            **_make_claim(
                payoff_id="  P1  ",
                payoff_kind="  三段式伏笔回收  ",
            )
        )
        assert claim.payoff_id == "P1"
        assert claim.payoff_kind == "三段式伏笔回收"

    def test_null_payoff_id_only(self) -> None:
        """Only ``payoff_id: null`` → ``""``, ``payoff_kind`` keeps its value."""
        claim = CoherenceClaim(
            **_make_claim(
                payoff_id=None,
                payoff_kind="callback",
            )
        )
        assert claim.payoff_id == ""
        assert claim.payoff_kind == "callback"

    def test_null_payoff_kind_only(self) -> None:
        """Only ``payoff_kind: null`` → ``""``, ``payoff_id`` keeps its value."""
        claim = CoherenceClaim(
            **_make_claim(
                payoff_id="S1",
                payoff_kind=None,
            )
        )
        assert claim.payoff_id == "S1"
        assert claim.payoff_kind == ""

    def test_non_string_coerced_and_stripped(self) -> None:
        """Non-string values (int, float) are coerced to str and stripped."""
        claim = CoherenceClaim(
            **_make_claim(
                payoff_id=42,
                payoff_kind=3.14,
            )
        )
        assert claim.payoff_id == "42"
        assert claim.payoff_kind == "3.14"


class TestPayoffIntegration:
    """Integration-style tests simulating LLM output with null payoff fields."""

    def test_llm_null_payoff_no_validation_error(self) -> None:
        """Simulate LLM output with ``payoff_id: null`` — must not raise.

        This reproduces the exact error from T12: 3 out of 6 claims had
        ``payoff_id: null, payoff_kind: null`` in the LLM JSON response.
        The validator should coerce these to ``""`` without raising
        ``ValidationError``.
        """
        # Simulated LLM JSON output (as would come from json.loads)
        llm_claim_payload = {
            "claim_id": "claim_003",
            "artifact": "character_bible",
            "source_path": "character_bible/characters/玄昱",
            "claim_text": "玄昱知道沈清漪的真实身份",
            "evidence": "角色设定第3条",
            "cognitive_subjects": ["玄昱"],
            "cognitive_object": "沈清漪身份",
            "cognitive_level": "confirmed",
            "action_level": "internal",
            "reader_awareness": "partial",
            "character_knowledge_coverage": {"玄昱": "full"},
            "cognitive_chapter": None,
            "public_reveal_chapter": None,
            "foreshadow_chapters": [1, 2],
            "payoff_id": None,
            "payoff_kind": None,
            "confidence": 0.8,
            "metadata": None,
        }

        # Must not raise ValidationError
        claim = CoherenceClaim.model_validate(llm_claim_payload)
        assert claim.payoff_id == ""
        assert claim.payoff_kind == ""

    def test_mixed_claims_batch_no_error(self) -> None:
        """A batch of claims with mixed null/non-null payoff fields all validate.

        Simulates the real-world scenario where some claims have payoff
        metadata and others don't.
        """
        claims_payload = [
            # Claim with valid payoff
            {
                **_COHERENCE_CLAIM_REQUIRED,
                "claim_id": "c_with_payoff",
                "payoff_id": "S1",
                "payoff_kind": "三段式伏笔回收",
            },
            # Claim with null payoff (the problematic case)
            {
                **_COHERENCE_CLAIM_REQUIRED,
                "claim_id": "c_null_payoff",
                "payoff_id": None,
                "payoff_kind": None,
            },
            # Claim with empty payoff
            {
                **_COHERENCE_CLAIM_REQUIRED,
                "claim_id": "c_empty_payoff",
                "payoff_id": "",
                "payoff_kind": "",
            },
            # Claim with whitespace payoff
            {
                **_COHERENCE_CLAIM_REQUIRED,
                "claim_id": "c_ws_payoff",
                "payoff_id": "  P1  ",
                "payoff_kind": "  callback  ",
            },
        ]

        claims = [CoherenceClaim.model_validate(p) for p in claims_payload]

        assert claims[0].payoff_id == "S1"
        assert claims[0].payoff_kind == "三段式伏笔回收"
        assert claims[1].payoff_id == ""
        assert claims[1].payoff_kind == ""
        assert claims[2].payoff_id == ""
        assert claims[2].payoff_kind == ""
        assert claims[3].payoff_id == "P1"
        assert claims[3].payoff_kind == "callback"
