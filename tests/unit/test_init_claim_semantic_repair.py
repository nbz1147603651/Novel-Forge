"""Tests for evidence-bounded init Claim semantic patches."""

from __future__ import annotations

from copy import deepcopy

import pytest

from novel_forge.core.format_contracts import FormatSchemaIssue
from novel_forge.pipeline.long.services.claim_field_policy import (
    CLAIM_FIELD_OWNERSHIP,
    LOCAL_STRUCTURAL_CLAIM_FIELDS,
    ClaimFieldOwner,
)
from novel_forge.pipeline.long.services.init.init_claim_semantic_repair import (
    apply_init_claim_semantic_patch,
    build_init_claim_semantic_repair_prompt,
    collect_init_claim_repair_targets,
)


def _claims_payload() -> dict[str, object]:
    return {
        "claims": [
            {
                "claim_id": "c40_001",
                "claim_text": "沈崖与林小满建立神经锚点共享。",
                "evidence": "第40章 cast_plan 显示两人在入梦前完成校准。",
                "cognitive_level": "confirmed",
                "character_knowledge_coverage": {
                    "林小满": "confirmed",
                    "沈崖": "confirmed",
                },
                "metadata": {},
            },
            {
                "claim_id": "c41_001",
                "claim_text": "第二条 Claim 不应被局部修复任务注入。",
                "evidence": "第41章独立证据。",
                "character_knowledge_coverage": {"程雾": "partial"},
                "metadata": {"source": "outline"},
            },
        ],
        "summary": "ok",
    }


def _coverage_issues() -> tuple[FormatSchemaIssue, ...]:
    return (
        FormatSchemaIssue(
            path="$.claims[0].character_knowledge_coverage.林小满",
            issue_type="enum_mismatch",
            expected="one of ['unknown', 'partial', 'full']",
            actual="'confirmed'",
        ),
        FormatSchemaIssue(
            path="$.claims[0].character_knowledge_coverage.沈崖",
            issue_type="enum_mismatch",
            expected="one of ['unknown', 'partial', 'full']",
            actual="'confirmed'",
        ),
    )


def _valid_patch() -> dict[str, object]:
    return {
        "repairs": [
            {
                "claim_index": 0,
                "claim_id": "c40_001",
                "field": "character_knowledge_coverage",
                "key": "林小满",
                "value": "full",
                "evidence": "入梦前两人共同完成校准。",
            },
            {
                "claim_index": 0,
                "claim_id": "c40_001",
                "field": "character_knowledge_coverage",
                "key": "沈崖",
                "value": "full",
                "evidence": "入梦前两人共同完成校准。",
            },
        ]
    }


def test_claim_field_ownership_separates_transport_from_narrative_semantics() -> None:
    assert LOCAL_STRUCTURAL_CLAIM_FIELDS == {"metadata"}
    assert CLAIM_FIELD_OWNERSHIP["metadata"] == ClaimFieldOwner.LOCAL_STRUCTURAL
    assert (
        CLAIM_FIELD_OWNERSHIP["character_knowledge_coverage"]
        == ClaimFieldOwner.LLM_SEMANTIC
    )


def test_collects_exact_repairable_claim_paths() -> None:
    payload = _claims_payload()
    issues = _coverage_issues() + (
        FormatSchemaIssue(
            path="$.claims[1].claim_text",
            issue_type="min_length",
            expected="non-empty",
            actual="''",
        ),
    )

    targets = collect_init_claim_repair_targets(payload, issues)

    assert [target.path for target in targets] == [
        "$.claims[0].character_knowledge_coverage.林小满",
        "$.claims[0].character_knowledge_coverage.沈崖",
    ]


def test_prompt_contains_only_affected_claims_and_enum_boundary() -> None:
    payload = _claims_payload()
    targets = collect_init_claim_repair_targets(payload, _coverage_issues())

    prompt = build_init_claim_semantic_repair_prompt(payload, targets)

    assert "c40_001" in prompt
    assert "c41_001" not in prompt
    assert "unknown" in prompt and "partial" in prompt and "full" in prompt
    assert "confirmed" in prompt
    assert "不得新增剧情事实" in prompt
    assert '"keys":["林小满","沈崖"]' in prompt
    # One occurrence in the grouped target manifest and one in evidence.
    assert prompt.count('"claim_id":"c40_001"') == 2


def test_exact_patch_changes_only_approved_paths() -> None:
    payload = _claims_payload()
    original = deepcopy(payload)
    targets = collect_init_claim_repair_targets(payload, _coverage_issues())

    repaired = apply_init_claim_semantic_patch(payload, targets, _valid_patch())

    assert payload == original
    repaired_claims = repaired["claims"]
    assert isinstance(repaired_claims, list)
    assert repaired_claims[0]["character_knowledge_coverage"] == {
        "林小满": "full",
        "沈崖": "full",
    }
    assert repaired_claims[1] == original["claims"][1]


def test_patch_rejects_cross_field_enum_value_before_full_validation() -> None:
    payload = _claims_payload()
    targets = collect_init_claim_repair_targets(payload, _coverage_issues())
    patch = _valid_patch()
    repairs = patch["repairs"]
    assert isinstance(repairs, list)
    repairs[0]["value"] = "confirmed"

    with pytest.raises(ValueError, match="unknown, partial, or full"):
        apply_init_claim_semantic_patch(payload, targets, patch)


@pytest.mark.parametrize("mutation", ["missing", "extra", "wrong_claim", "extra_key"])
def test_patch_rejects_inexact_target_sets(mutation: str) -> None:
    payload = _claims_payload()
    targets = collect_init_claim_repair_targets(payload, _coverage_issues())
    patch = _valid_patch()
    repairs = patch["repairs"]
    assert isinstance(repairs, list)
    if mutation == "missing":
        repairs.pop()
    elif mutation == "extra":
        repairs.append(
            {
                "claim_index": 1,
                "claim_id": "c41_001",
                "field": "character_knowledge_coverage",
                "key": "程雾",
                "value": "full",
                "evidence": "未授权路径。",
            }
        )
    elif mutation == "wrong_claim":
        repairs[0]["claim_id"] = "c41_001"
    else:
        repairs[0]["comment"] = "schema leak"

    with pytest.raises(ValueError):
        apply_init_claim_semantic_patch(payload, targets, patch)
