"""Unit tests for the deterministic init-coherence auto-repair layer."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from novel_forge.pipeline.long.services.init.init_coherence import build_init_readiness_report
from novel_forge.pipeline.long.services.init.init_coherence_auto_repair import (
    DETERMINISTIC_ISSUE_TYPES,
    AutoRepairFix,
    AutoRepairResult,
    auto_repair_coherence_issues,
    build_downgraded_report,
    register_default_handlers,
    summarize_unresolved_issues,
    verify_auto_repair_candidate,
)
from novel_forge.pipeline.long.services.init.init_service import (
    _format_init_coherence_error,
    _init_coherence_claim_ledger,
    _init_coherence_repair_round_start,
    _run_init_coherence_auto_repair,
)

# Ensure the built-in handlers are registered even if the module was
# imported in isolation (defensive — the module already registers them
# at import-time, but this keeps the tests resilient).
register_default_handlers()


# ─────────────────────────────────────────────────────────────────────
# chapter_number_mismatch
# ─────────────────────────────────────────────────────────────────────


def _build_claim_ledger(claims: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "active_claim_ids": [str(claim.get("claim_id") or "") for claim in claims],
        "claims_by_id": {str(claim.get("claim_id") or ""): claim for claim in claims},
    }


def _build_chapter_contracts(per_chapter: dict[int, dict[str, Any]]) -> dict[str, Any]:
    return {
        "chapter_contracts": [
            {"chapter_number": number, **payload} for number, payload in sorted(per_chapter.items())
        ]
    }


def test_auto_repair_moves_constraint_to_canonical_chapter() -> None:
    """A cognitive_constraint in the wrong chapter is migrated via the ledger."""
    payload = _build_chapter_contracts(
        {
            93: {
                "title": "九十三章",
                "cognitive_constraints": [],
            },
            95: {
                "title": "九十五章",
                "cognitive_constraints": [
                    {
                        "claim_id": "claim_qingci",
                        "text": "沈清辞试穿皇后祭礼服",
                    }
                ],
            },
        }
    )
    claim_ledger = _build_claim_ledger(
        [
            {
                "claim_id": "claim_qingci",
                "chapter_numbers": [93],
                "claim_text": "沈清辞试穿皇后祭礼服",
            }
        ]
    )
    report = {
        "verdict": "needs_repair",
        "issues": [
            {
                "id": "cand_0033_issue_002",
                "type": "chapter_number_mismatch",
                "severity": "high",
                "candidate_ids": ["cand_0033"],
                "evidence": "事件应在 93 章，但被标在 95 章",
                "description": "章节归属错误",
                "repair_scope": [
                    {
                        "artifact": "chapter_contracts",
                        "chapters": [95],
                        "fields": ["cognitive_constraints"],
                        "operation": "field_replace",
                    }
                ],
            }
        ],
        "summary": "test",
    }

    result = auto_repair_coherence_issues(
        payload=payload,
        report=report,
        artifact="chapter_contracts",
        claim_ledger=claim_ledger,
    )

    assert isinstance(result, AutoRepairResult)
    assert result.has_fixes
    assert result.fixes[0].issue_type == "chapter_number_mismatch"
    assert "cand_0033_issue_002" in result.resolved_issue_ids
    assert payload["chapter_contracts"][1]["cognitive_constraints"][0]["claim_id"] == (
        "claim_qingci"
    )
    assert verify_auto_repair_candidate(
        result,
        report=report,
        artifact="chapter_contracts",
        claim_ledger=claim_ledger,
    )
    assert result.verification is not None
    assert result.verification.passed is True

    by_chapter = {item["chapter_number"]: item for item in result.payload["chapter_contracts"]}
    assert by_chapter[95]["cognitive_constraints"] == []
    assert len(by_chapter[93]["cognitive_constraints"]) == 1
    assert by_chapter[93]["cognitive_constraints"][0]["claim_id"] == "claim_qingci"


def test_auto_repair_multi_chapter_claim_moves_to_one_canonical_chapter() -> None:
    payload = _build_chapter_contracts(
        {
            5: {"cognitive_constraints": []},
            6: {"cognitive_constraints": []},
            7: {"cognitive_constraints": [{"claim_id": "claim_range", "claim_text": "跨章推进"}]},
        }
    )
    claim_ledger = _build_claim_ledger([{"claim_id": "claim_range", "chapter_numbers": [5, 6]}])
    report = {
        "verdict": "needs_repair",
        "issues": [
            {
                "id": "range_issue",
                "type": "chapter_number_mismatch",
                "severity": "high",
                "repair_scope": [
                    {
                        "artifact": "chapter_contracts",
                        "chapters": [7],
                        "fields": ["cognitive_constraints"],
                    }
                ],
            }
        ],
    }

    result = auto_repair_coherence_issues(
        payload=payload,
        report=report,
        artifact="chapter_contracts",
        claim_ledger=claim_ledger,
    )

    assert result.has_fixes
    by_chapter = {item["chapter_number"]: item for item in result.payload["chapter_contracts"]}
    assert len(by_chapter[5]["cognitive_constraints"]) == 1
    assert by_chapter[6]["cognitive_constraints"] == []
    assert by_chapter[7]["cognitive_constraints"] == []
    moved = by_chapter[5]["cognitive_constraints"][0]
    assert moved["claim_id"] == "claim_range"
    assert moved["auto_repair_multi_chapter_claim"] is True


def test_auto_repair_drops_placeholder_when_no_ledger() -> None:
    """Without a claim ledger, a placeholder entry in the wrong chapter is dropped."""
    payload = _build_chapter_contracts(
        {
            95: {
                "title": "九十五章",
                "cognitive_constraints": [
                    {
                        "claim_id": "orphan_claim",
                        "claim_text": "",
                        "cognitive_subjects": [],
                    }
                ],
            }
        }
    )
    report = {
        "verdict": "needs_repair",
        "issues": [
            {
                "id": "orphan_1",
                "type": "chapter_number_mismatch",
                "severity": "high",
                "candidate_ids": ["orphan_claim"],
                "description": "占位条目在错误章节",
                "repair_scope": [
                    {
                        "artifact": "chapter_contracts",
                        "chapters": [95],
                        "fields": ["cognitive_constraints"],
                    }
                ],
            }
        ],
    }

    result = auto_repair_coherence_issues(
        payload=payload,
        report=report,
        artifact="chapter_contracts",
        claim_ledger=None,
    )

    assert result.has_fixes
    assert result.payload["chapter_contracts"][0]["cognitive_constraints"] == []


# ─────────────────────────────────────────────────────────────────────
# state_axis_timeline_conflict / state_axis_first_occurrence_conflict
# ─────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "issue_type, marker",
    [
        ("state_axis_timeline_conflict", "[validation]"),
        ("state_axis_first_occurrence_conflict", "[continuation]"),
    ],
)
def test_auto_repair_demotes_later_state_axis_claims(issue_type: str, marker: str) -> None:
    payload = _build_chapter_contracts(
        {
            95: {
                "title": "九十五章",
                "cognitive_constraints": [{"claim_id": "c1", "text": "玄曜人格整合完成"}],
            },
            106: {
                "title": "一零六章",
                "cognitive_constraints": [
                    {"claim_id": "c2", "text": "玄曜首次完全感知不到幼态人格"}
                ],
            },
            107: {
                "title": "一零七章",
                "cognitive_constraints": [
                    {"claim_id": "c3", "text": "重摆青阳棋局时玄曜人格完全整合"}
                ],
            },
        }
    )
    report = {
        "verdict": "needs_repair",
        "issues": [
            {
                "id": "cand_0025_issue_001",
                "type": issue_type,
                "severity": "high",
                "candidate_ids": ["cand_0025", "cand_0026", "cand_0027"],
                "description": "多个不可逆事件完成节点重复",
                "repair_scope": [
                    {
                        "artifact": "chapter_contracts",
                        "chapters": [95, 106, 107],
                        "fields": ["cognitive_constraints"],
                    }
                ],
            }
        ],
    }

    result = auto_repair_coherence_issues(
        payload=payload,
        report=report,
        artifact="chapter_contracts",
    )

    assert result.has_fixes
    assert "cand_0025_issue_001" in result.resolved_issue_ids
    by_chapter = {item["chapter_number"]: item for item in result.payload["chapter_contracts"]}
    # Earliest chapter is unchanged.
    earliest = by_chapter[95]["cognitive_constraints"][0]
    assert earliest["text"] == "玄曜人格整合完成"
    assert "demoted_by" not in earliest
    # Later chapters are demoted.
    for chapter in (106, 107):
        entry = by_chapter[chapter]["cognitive_constraints"][0]
        expected_prefix = "【后续验证】" if marker == "[validation]" else "【后续延续】"
        assert entry["text"].startswith(expected_prefix), entry
        assert entry["demoted_by"] == marker
        assert entry["demoted_in_favor_of_chapter"] == 95


def test_auto_repair_does_not_demote_empty_state_axis_entries() -> None:
    payload = _build_chapter_contracts(
        {
            1: {"cognitive_constraints": [{"claim_id": "c1", "claim_text": "首次确认"}]},
            2: {"cognitive_constraints": [{"claim_id": "c2"}]},
        }
    )
    report = {
        "verdict": "needs_repair",
        "issues": [
            {
                "id": "empty_later",
                "type": "state_axis_timeline_conflict",
                "severity": "high",
                "repair_scope": [
                    {
                        "artifact": "chapter_contracts",
                        "chapters": [1, 2],
                        "fields": ["cognitive_constraints"],
                    }
                ],
            }
        ],
    }

    result = auto_repair_coherence_issues(
        payload=payload,
        report=report,
        artifact="chapter_contracts",
    )

    assert not result.has_fixes
    entry = payload["chapter_contracts"][1]["cognitive_constraints"][0]
    assert "text" not in entry
    assert "demoted_by" not in entry


# ─────────────────────────────────────────────────────────────────────
# foreshadow_timeline_reversed
# ─────────────────────────────────────────────────────────────────────


def test_auto_repair_clamps_foreshadow_before_reveal() -> None:
    payload = _build_chapter_contracts(
        {
            78: {
                "title": "七十八章",
                "knowledge_ops": [
                    {
                        "claim_id": "k78",
                        "text": "沈氏母族平反",
                        "foreshadow_chapter": 79,
                    }
                ],
                "promise_ops": [
                    {
                        "claim_id": "p78",
                        "text": "玄曜人格整合揭示",
                        "foreshadow_chapter": 80,
                    }
                ],
            }
        }
    )
    report = {
        "verdict": "needs_repair",
        "issues": [
            {
                "id": "cand_0035_issue_004",
                "type": "foreshadow_timeline_reversed",
                "severity": "medium",
                "candidate_ids": ["cand_0035", "cand_0036"],
                "description": "伏笔晚于揭示",
                "repair_scope": [
                    {
                        "artifact": "chapter_contracts",
                        "chapters": [78],
                        "fields": ["knowledge_ops", "promise_ops"],
                    }
                ],
            }
        ],
    }

    result = auto_repair_coherence_issues(
        payload=payload,
        report=report,
        artifact="chapter_contracts",
    )

    assert result.has_fixes
    assert "cand_0035_issue_004" in result.resolved_issue_ids
    item = result.payload["chapter_contracts"][0]
    assert item["knowledge_ops"][0]["foreshadow_chapter"] == 77
    # The promise_ops entry had foreshadow_chapter 80, which is > 78; it
    # gets clamped to 77 (= max(1, 78 - 1)). The fact that it is also
    # "already too late" — chapter 1 — is acceptable: the system can no
    # longer plant foreshadow at chapter 1, but at least the timeline is
    # now valid.
    assert item["promise_ops"][0]["foreshadow_chapter"] == 77


def test_auto_repair_clamps_foreshadow_when_chapter_is_one() -> None:
    payload = _build_chapter_contracts(
        {
            1: {
                "title": "第一章",
                "knowledge_ops": [
                    {
                        "claim_id": "k1",
                        "text": "X 揭示",
                        "foreshadow_chapter": 5,
                    }
                ],
            }
        }
    )
    report = {
        "verdict": "needs_repair",
        "issues": [
            {
                "id": "edge_case",
                "type": "foreshadow_timeline_reversed",
                "severity": "medium",
                "candidate_ids": ["cand_edge"],
                "repair_scope": [
                    {
                        "artifact": "chapter_contracts",
                        "chapters": [1],
                        "fields": ["knowledge_ops"],
                    }
                ],
            }
        ],
    }
    result = auto_repair_coherence_issues(
        payload=payload,
        report=report,
        artifact="chapter_contracts",
    )
    assert result.has_fixes
    assert result.payload["chapter_contracts"][0]["knowledge_ops"][0]["foreshadow_chapter"] == 1


def test_auto_repair_clamps_plural_foreshadow_chapters() -> None:
    payload = _build_chapter_contracts(
        {
            78: {
                "cognitive_constraints": [
                    {
                        "claim_id": "c78",
                        "claim_text": "沈氏母族平反",
                        "foreshadow_chapters": [70, 79],
                    }
                ]
            }
        }
    )
    report = {
        "verdict": "needs_repair",
        "issues": [
            {
                "id": "plural",
                "type": "foreshadow_timeline_reversed",
                "severity": "medium",
                "repair_scope": [
                    {
                        "artifact": "chapter_contracts",
                        "chapters": [78],
                        "fields": ["cognitive_constraints"],
                    }
                ],
            }
        ],
    }

    result = auto_repair_coherence_issues(
        payload=payload,
        report=report,
        artifact="chapter_contracts",
    )

    assert result.has_fixes
    entry = result.payload["chapter_contracts"][0]["cognitive_constraints"][0]
    assert entry["foreshadow_chapters"] == [70, 77]
    assert entry["foreshadow_chapters_clamped_by_issue_id"] == "plural"


def test_auto_repair_renames_duplicate_payoff_ids() -> None:
    payload = _build_chapter_contracts(
        {
            41: {"promise_ops": [{"payoff_id": "S007", "description": "旧识确认"}]},
            114: {"promise_ops": [{"payoff_id": "S007", "description": "赌约兑现"}]},
            120: {
                "promise_ops": [
                    {"payoff_id": "S007", "description": "人格整合"},
                    {"payoff_id": "S007", "description": "和平约定"},
                ]
            },
        }
    )
    report = {
        "verdict": "needs_repair",
        "issues": [
            {
                "id": "payoff_dup",
                "type": "payoff_id_duplication",
                "severity": "high",
                "repair_scope": [
                    {
                        "artifact": "chapter_contracts",
                        "chapters": [41, 114, 120],
                        "fields": ["promise_ops"],
                    }
                ],
            }
        ],
    }

    result = auto_repair_coherence_issues(
        payload=payload,
        report=report,
        artifact="chapter_contracts",
    )

    assert result.has_fixes
    ids = [
        item["promise_ops"][0]["payoff_id"]
        for item in result.payload["chapter_contracts"][:2]
    ]
    ids.extend(
        entry["payoff_id"] for entry in result.payload["chapter_contracts"][2]["promise_ops"]
    )
    assert len(ids) == len(set(ids))
    assert ids[0] == "S007"
    assert all(value.startswith("S007_ch") for value in ids[1:])


def test_auto_repair_splits_character_identity_conflict_payoff() -> None:
    payload = _build_chapter_contracts(
        {
            83: {
                "promise_ops": [
                    {
                        "payoff_id": "bp_ph4_6_005",
                        "cognitive_subjects": ["玄玱", "清欢"],
                        "description": "清欢确认玄玱身份",
                    },
                    {
                        "payoff_id": "bp_ph4_6_005",
                        "cognitive_subjects": ["清欢", "玄溟"],
                        "description": "清欢确认玄溟身份",
                    },
                ]
            }
        }
    )
    report = {
        "verdict": "needs_repair",
        "issues": [
            {
                "id": "identity_conflict",
                "type": "character_identity_conflict",
                "severity": "high",
                "evidence": {
                    "claim_1_subjects": ["玄玱", "清欢"],
                    "claim_2_subjects": ["清欢", "玄溟"],
                    "payoff_id": "bp_ph4_6_005",
                },
                "repair_scope": [
                    {
                        "artifact": "chapter_contracts",
                        "chapters": [83],
                        "fields": ["promise_ops"],
                    }
                ],
            }
        ],
    }

    result = auto_repair_coherence_issues(
        payload=payload,
        report=report,
        artifact="chapter_contracts",
    )

    assert result.has_fixes
    assert result.fixes[0].issue_type == "character_identity_conflict"
    entries = result.payload["chapter_contracts"][0]["promise_ops"]
    assert entries[0]["payoff_id"] == "bp_ph4_6_005"
    assert entries[1]["payoff_id"].startswith("bp_ph4_6_005_ch083")
    assert entries[1]["auto_repair_identity_subjects"] == ["清欢", "玄溟"]


# ─────────────────────────────────────────────────────────────────────
# Downgraded report & summary
# ─────────────────────────────────────────────────────────────────────


def test_build_downgraded_report_accepts_when_no_remaining_high() -> None:
    original = {
        "verdict": "needs_repair",
        "issues": [
            {
                "id": "a",
                "type": "chapter_number_mismatch",
                "severity": "high",
            },
            {
                "id": "b",
                "type": "state_axis_timeline_conflict",
                "severity": "low",
            },
        ],
        "blocked": True,
        "summary": "候选裁判完成：59 个候选，4 个问题，最终判定 needs_repair。",
    }

    downgraded = build_downgraded_report(original, resolved_issue_ids=["a", "b"])

    assert downgraded["verdict"] == "accept"
    assert downgraded["blocked"] is False
    assert downgraded["issues"] == []
    assert "auto_repair_resolved_issue_ids" in downgraded


def test_build_downgraded_report_keeps_ambiguous_when_high_remains() -> None:
    original = {
        "verdict": "needs_repair",
        "issues": [
            {"id": "a", "type": "chapter_number_mismatch", "severity": "high"},
            {"id": "b", "type": "other_conflict", "severity": "high"},
        ],
        "blocked": True,
        "summary": "x",
    }
    downgraded = build_downgraded_report(original, resolved_issue_ids=["a"])
    assert downgraded["verdict"] == "ambiguous"
    assert downgraded["blocked"] is True
    assert len(downgraded["issues"]) == 1
    assert downgraded["issues"][0]["id"] == "b"


def test_build_downgraded_report_honors_min_severity() -> None:
    original = {
        "verdict": "needs_repair",
        "issues": [
            {"id": "a", "type": "chapter_number_mismatch", "severity": "high"},
            {"id": "b", "type": "foreshadow_timeline_reversed", "severity": "medium"},
        ],
        "blocked": True,
        "summary": "x",
    }

    downgraded = build_downgraded_report(
        original,
        resolved_issue_ids=["a"],
        min_severity="medium",
    )

    assert downgraded["verdict"] == "ambiguous"
    assert downgraded["blocked"] is True


def test_summarize_unresolved_issues_lists_blocking_problems() -> None:
    report = {
        "issues": [
            {
                "id": "cand_0025_issue_001",
                "type": "state_axis_timeline_conflict",
                "severity": "high",
                "description": "玄曜人格整合节点重复",
                "evidence": "95/106/107 章均出现完成声明",
                "repair_scope": [{"artifact": "chapter_contracts", "chapters": [95, 106, 107]}],
            },
            {
                "id": "cand_0033_issue_002",
                "type": "chapter_number_mismatch",
                "severity": "high",
                "description": "沈清辞试穿礼服在 95 章",
                "evidence": "outline 明确事件在 93 章",
                "repair_scope": [{"artifact": "chapter_contracts", "chapters": [95]}],
            },
        ]
    }
    summary = summarize_unresolved_issues(report)
    assert "state_axis_timeline_conflict" in summary
    assert "cand_0025_issue_001" in summary
    assert "玄曜人格整合节点重复" in summary


# ─────────────────────────────────────────────────────────────────────
# Defensive: non-dict payload, unrelated issue types, missing scope
# ─────────────────────────────────────────────────────────────────────


def test_auto_repair_handles_non_dict_payload() -> None:
    result = auto_repair_coherence_issues(
        payload=None,  # type: ignore[arg-type]
        report={"issues": []},
        artifact="chapter_contracts",
    )
    assert result.payload == {}
    assert result.skipped_reason == "payload is not a dict"
    assert not result.has_fixes


def test_auto_repair_skips_issues_with_unrelated_scope() -> None:
    payload = _build_chapter_contracts(
        {
            10: {
                "title": "十章",
                "cognitive_constraints": [{"text": "kept"}],
            }
        }
    )
    report = {
        "verdict": "needs_repair",
        "issues": [
            {
                "id": "wrong_artifact",
                "type": "chapter_number_mismatch",
                "severity": "high",
                "repair_scope": [{"artifact": "outline", "chapters": [10]}],
            }
        ],
    }
    result = auto_repair_coherence_issues(
        payload=payload,
        report=report,
        artifact="chapter_contracts",
    )
    assert not result.has_fixes
    assert result.unresolved_issue_ids == ["wrong_artifact"]
    # Original payload must be untouched.
    assert payload["chapter_contracts"][0]["cognitive_constraints"][0]["text"] == "kept"


def test_auto_repair_ignores_unsupported_issue_types() -> None:
    payload = _build_chapter_contracts({1: {"title": "一"}})
    report = {
        "verdict": "needs_repair",
        "issues": [
            {
                "id": "weird",
                "type": "novel_unknown_issue",
                "severity": "high",
            }
        ],
    }
    result = auto_repair_coherence_issues(
        payload=payload,
        report=report,
        artifact="chapter_contracts",
    )
    assert not result.has_fixes
    assert result.unresolved_issue_ids == []  # Not even recorded as unresolved.


def test_deterministic_issue_types_contains_expected_keys() -> None:
    # Pin the public contract so a future refactor that drops a type
    # has to update the test deliberately.
    assert DETERMINISTIC_ISSUE_TYPES == frozenset(
        {
            "chapter_number_mismatch",
            "chapter_range_mismatch",
            "payoff_id_duplication",
            "state_axis_first_occurrence_conflict",
            "state_axis_timeline_conflict",
            "foreshadow_timeline_reversed",
            "character_identity_conflict",
        }
    )


def test_summary_only_no_effect_does_not_count_as_repair_round() -> None:
    settings = SimpleNamespace(init_coherence_block_min_severity="high")
    report = {
        "issues": [
            {
                "id": "oversized_issue",
                "type": "character_identity_conflict",
                "severity": "high",
            }
        ]
    }
    repairs = [
        {
            "artifact": "chapter_contracts",
            "round": 3,
            "status": "no_effect",
            "repair_type": "summary_only",
            "source_issue_ids": ["oversized_issue"],
        }
    ]

    assert (
        _init_coherence_repair_round_start(
            settings,
            repairs,
            artifact="chapter_contracts",
            report=report,
        )
        == 0
    )


def test_auto_repair_result_dataclass_fields() -> None:
    fix = AutoRepairFix(
        issue_id="x",
        issue_type="chapter_number_mismatch",
        chapters_affected=[1],
        fields_affected=["cognitive_constraints"],
        description="d",
    )
    blob = fix.to_dict()
    assert blob["issue_id"] == "x"
    assert blob["chapters_affected"] == [1]


# ─────────────────────────────────────────────────────────────────────
# Orchestrator helpers (_run_init_coherence_auto_repair, _format_init_coherence_error)
# ─────────────────────────────────────────────────────────────────────


class _StubSettings:
    def __init__(self) -> None:
        self.init_coherence_deterministic_repair = True
        self.init_coherence_auto_repair_deterministic = True


class _StubLayout:
    memory_dir = __import__("pathlib").Path("/tmp") / "auto_repair_test"
    reports_dir = __import__("pathlib").Path("/tmp") / "auto_repair_test_reports"


class _StubStorage:
    def __init__(self, ledger: dict[str, Any] | None) -> None:
        self._ledger = ledger
        self.saved: list[tuple[Any, Any]] = []

    def load_json(self, path: Any) -> Any:
        return self._ledger

    def save_json(self, path: Any, payload: Any) -> None:
        self.saved.append((path, payload))


class _StubCtx:
    def __init__(self, ledger: dict[str, Any] | None) -> None:
        self.settings = _StubSettings()
        self.layout = _StubLayout()
        self.storage = _StubStorage(ledger)
        self._emitted: list[tuple[str, dict[str, Any]]] = []

    def on_step(self, step: str, data: dict[str, Any]) -> None:
        self._emitted.append((step, data))


def test_run_init_coherence_auto_repair_resolves_issues() -> None:
    payload = _build_chapter_contracts(
        {
            95: {
                "title": "九十五章",
                "cognitive_constraints": [{"claim_id": "claim_a", "text": "玄曜人格整合完成"}],
            },
            106: {
                "title": "一零六章",
                "cognitive_constraints": [
                    {"claim_id": "claim_b", "text": "玄曜首次完全感知不到幼态人格"}
                ],
            },
        }
    )
    report = {
        "verdict": "needs_repair",
        "issues": [
            {
                "id": "cand_0025_issue_001",
                "type": "state_axis_timeline_conflict",
                "severity": "high",
                "candidate_ids": ["cand_0025"],
                "repair_scope": [
                    {
                        "artifact": "chapter_contracts",
                        "chapters": [95, 106],
                        "fields": ["cognitive_constraints"],
                    }
                ],
            }
        ],
        "summary": "原始 summary",
    }
    ctx = _StubCtx(ledger=None)
    downgraded = _run_init_coherence_auto_repair(
        ctx,
        artifact="chapter_contracts",
        report=report,
        payload=payload,
    )
    assert downgraded["verdict"] == "accept"
    assert downgraded["blocked"] is False
    assert downgraded["issues"] == []
    assert "auto_repair_applied" in downgraded
    step_names = [name for name, _ in ctx._emitted]
    assert "coherence_auto_repair_applied" in step_names


def test_run_init_coherence_auto_repair_records_repair_ledger() -> None:
    payload = _build_chapter_contracts(
        {
            1: {
                "title": "一",
                "cognitive_constraints": [{"claim_id": "c1", "claim_text": "首次确认"}],
            },
            2: {
                "title": "二",
                "cognitive_constraints": [{"claim_id": "c2", "claim_text": "再次确认"}],
            },
        }
    )
    report = {
        "verdict": "needs_repair",
        "issues": [
            {
                "id": "issue_a",
                "type": "state_axis_timeline_conflict",
                "severity": "high",
                "repair_scope": [
                    {
                        "artifact": "chapter_contracts",
                        "chapters": [1, 2],
                        "fields": ["cognitive_constraints"],
                    }
                ],
            }
        ],
        "summary": "x",
    }
    ctx = _StubCtx(ledger=None)
    repairs: list[dict[str, Any]] = []

    downgraded = _run_init_coherence_auto_repair(
        ctx,
        artifact="chapter_contracts",
        report=report,
        payload=payload,
        repairs=repairs,
    )

    assert downgraded["verdict"] == "accept"
    assert len(repairs) == 1
    repair = repairs[0]
    assert repair["repair_type"] == "deterministic_coherence"
    assert repair["source_issue_ids"] == ["issue_a"]
    assert repair["patches"][0]["issue_ids"] == ["issue_a"]
    assert repair["candidate"]["candidate_hash"]
    assert repair["verification"]["passed"] is True
    assert repair["audit_issues"][0]["issue_id"] == "issue_a"
    assert ctx.storage.saved


def test_run_init_coherence_auto_repair_rejects_unverified_candidate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = _build_chapter_contracts(
        {
            1: {
                "title": "一",
                "cognitive_constraints": [{"claim_id": "c1", "claim_text": "首次确认"}],
            },
            2: {
                "title": "二",
                "cognitive_constraints": [{"claim_id": "c2", "claim_text": "再次确认"}],
            },
        }
    )
    original = _build_chapter_contracts(
        {
            1: {
                "title": "一",
                "cognitive_constraints": [{"claim_id": "c1", "claim_text": "首次确认"}],
            },
            2: {
                "title": "二",
                "cognitive_constraints": [{"claim_id": "c2", "claim_text": "再次确认"}],
            },
        }
    )
    report = {
        "verdict": "needs_repair",
        "issues": [
            {
                "id": "issue_rejected",
                "type": "state_axis_timeline_conflict",
                "severity": "high",
                "repair_scope": [
                    {
                        "artifact": "chapter_contracts",
                        "chapters": [1, 2],
                        "fields": ["cognitive_constraints"],
                    }
                ],
            }
        ],
    }
    monkeypatch.setattr(
        "novel_forge.pipeline.long.services.init.init_source_resume.verify_auto_repair_candidate",
        lambda *args, **kwargs: False,
    )
    ctx = _StubCtx(ledger=None)

    returned = _run_init_coherence_auto_repair(
        ctx,
        artifact="chapter_contracts",
        report=report,
        payload=payload,
    )

    assert returned is report
    assert payload == original
    assert not ctx.storage.saved
    assert [name for name, _ in ctx._emitted] == ["coherence_auto_repair_rejected"]


def test_readiness_report_surfaces_auto_repair_summary() -> None:
    report = {
        "verdict": "accept",
        "issues": [],
        "summary": "ok",
        "auto_repair_applied": {
            "fix_count": 2,
            "resolved_issue_ids": ["a", "b"],
            "unresolved_issue_ids": [],
        },
    }

    readiness = build_init_readiness_report(
        reports={"contract_coherence": report},
        repairs=[],
        min_severity="high",
        required=True,
    )

    auto_repair = readiness["stages"]["contract_coherence"]["auto_repair"]
    assert auto_repair["fix_count"] == 2
    assert auto_repair["resolved_issue_ids"] == ["a", "b"]


def test_run_init_coherence_auto_repair_respects_disable_flag() -> None:
    payload = _build_chapter_contracts({1: {"title": "一"}})
    report = {
        "verdict": "needs_repair",
        "issues": [
            {
                "id": "x",
                "type": "chapter_number_mismatch",
                "severity": "high",
                "repair_scope": [{"artifact": "chapter_contracts", "chapters": [1]}],
            }
        ],
    }
    ctx = _StubCtx(ledger=None)
    ctx.settings.init_coherence_deterministic_repair = False
    returned = _run_init_coherence_auto_repair(
        ctx,
        artifact="chapter_contracts",
        report=report,
        payload=payload,
    )
    assert returned is report
    assert "auto_repair_applied" not in returned


def test_init_coherence_claim_ledger_ignores_non_dict_payload() -> None:
    ctx = _StubCtx(ledger=[1, 2, 3])  # type: ignore[arg-type]

    assert _init_coherence_claim_ledger(ctx) == {}


def test_format_init_coherence_error_includes_issues_and_recovery_hints() -> None:
    report = {
        "verdict": "needs_repair",
        "summary": "候选裁判完成：59 个候选，4 个问题，最终判定 needs_repair。",
        "issues": [
            {
                "id": "cand_0025_issue_001",
                "type": "state_axis_timeline_conflict",
                "severity": "high",
                "description": "玄曜人格整合节点重复",
                "evidence": "95/106/107 章均出现完成声明",
                "repair_scope": [{"artifact": "chapter_contracts", "chapters": [95, 106, 107]}],
            }
        ],
    }
    message = _format_init_coherence_error(
        stage="contract_coherence",
        report=report,
        repairs=[{"artifact": "chapter_contracts"}],
    )
    assert "章节契约一致性裁判未通过" in message
    assert "候选裁判完成" in message
    assert "state_axis_timeline_conflict" in message
    assert "玄曜人格整合节点重复" in message
    assert "修复建议" in message
    assert "data/<project>/plans/chapter_contracts.json" in message
    assert "TASK_FALLBACK_ROUTING" in message


def test_merge_adjudication_batch_reports_worst_verdict_wins() -> None:
    """Bisected batch reports merge with worst-verdict precedence."""
    from novel_forge.pipeline.long.services.init.init_coherence_v2 import (
        _merge_adjudication_batch_reports,
    )

    left = {
        "verdict": "accept",
        "issues": [],
        "source_refs": [],
        "repair_scope": [],
        "preserve": [],
        "change_intent": "",
        "blocked": False,
        "summary": "left ok",
        "candidate_ids": ["cand_1", "cand_2"],
    }
    right = {
        "verdict": "needs_repair",
        "issues": [{"severity": "high", "description": "状态顺序冲突"}],
        "source_refs": [{"source": "outline_1"}],
        "repair_scope": [{"artifact": "outline", "chapters": [3]}],
        "preserve": [],
        "change_intent": "修复第3章",
        "blocked": True,
        "summary": "right broken",
        "candidate_ids": ["cand_3", "cand_4"],
    }

    merged = _merge_adjudication_batch_reports(left, right)

    assert merged["verdict"] == "needs_repair"
    assert merged["blocked"] is True
    assert len(merged["issues"]) == 1
    assert merged["source_refs"] == [{"source": "outline_1"}]
    assert merged["repair_scope"] == [{"artifact": "outline", "chapters": [3]}]
    assert merged["candidate_ids"] == ["cand_1", "cand_2", "cand_3", "cand_4"]
    assert merged["schema_version"] == "audit_v2"


def test_merge_adjudication_batch_reports_concatenates_issues_and_truncates() -> None:
    """Issues / source refs from both halves concatenate, bounded by 24 items."""
    from novel_forge.pipeline.long.services.init.init_coherence_v2 import (
        _merge_adjudication_batch_reports,
    )

    left = {
        "verdict": "defer",
        "issues": [{"severity": "low", "description": f"issue_{index}"} for index in range(20)],
        "source_refs": [{"source": f"src_{index}"} for index in range(20)],
        "repair_scope": [],
        "preserve": [],
        "change_intent": "",
        "blocked": False,
        "summary": "",
        "candidate_ids": list(range(20)),
    }
    right = {
        "verdict": "ambiguous",
        "issues": [{"severity": "low", "description": f"issue_{index}"} for index in range(20)],
        "source_refs": [{"source": f"src_{index}"} for index in range(20)],
        "repair_scope": [],
        "preserve": [],
        "change_intent": "",
        "blocked": False,
        "summary": "",
        "candidate_ids": list(range(20, 40)),
    }

    merged = _merge_adjudication_batch_reports(left, right)

    assert merged["verdict"] == "ambiguous"
    assert len(merged["issues"]) == 40
    assert len(merged["source_refs"]) == 24
    assert len(merged["repair_scope"]) == 0
