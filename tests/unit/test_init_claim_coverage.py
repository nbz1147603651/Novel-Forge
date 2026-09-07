from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from novel_forge.persistence.filesystem import FileSystemStorage
from novel_forge.persistence.models import ProjectLayout
from novel_forge.pipeline.long.services.init.init_claim_coverage import (
    CLAIM_CONTRACT_COVERAGE_REPORT,
    _local_cognitive_backfill,
    build_claim_contract_coverage_report,
    run_init_claim_contract_coverage_audit,
)
from novel_forge.pipeline.long.services.init.init_coherence_v2 import CLAIM_LEDGER_JSON


def _ledger(*claims: dict[str, Any]) -> dict[str, Any]:
    claims_by_id = {claim["claim_id"]: {"status": "active", **claim} for claim in claims}
    return {
        "claims_by_id": claims_by_id,
        "active_claim_ids": [claim["claim_id"] for claim in claims],
    }


def _claim(**overrides: Any) -> dict[str, Any]:
    return {
        "claim_id": "claim_event_1",
        "artifact": "outline",
        "source_path": "/chapters/2",
        "claim_type": "event",
        "claim_text": "第二章公开关键证据。",
        "evidence": "第二章目标写明公开关键证据。",
        "chapter_numbers": [2],
        "confidence": 0.9,
        "cognitive_subjects": [],
        "cognitive_object": "",
        "cognitive_level": "unaware",
        "action_level": "none",
        "reader_awareness": "unknown",
        "character_knowledge_coverage": {},
        "cognitive_chapter": None,
        "public_reveal_chapter": None,
        "foreshadow_chapters": [],
        **overrides,
    }


def _contracts(*items: dict[str, Any]) -> dict[str, Any]:
    return {"chapter_contracts": list(items)}


def test_progressive_coverage_defers_future_claims_without_discarding_them() -> None:
    ledger = _ledger(_claim(chapter_numbers=[49], priority="P0"))
    report = build_claim_contract_coverage_report(
        ledger, _contracts({"chapter_number": 1}), hard_through_chapter=5
    )
    assert not report["blocked"]
    assert report["deferred_claims"] == 1
    assert report["issues"] == []
    assert report["items"][0]["deferred_chapters"] == [49]
    assert ledger["claims_by_id"]["claim_event_1"]["chapter_numbers"] == [49]
    expanded = build_claim_contract_coverage_report(
        ledger, _contracts({"chapter_number": 1}), hard_through_chapter=50
    )
    assert expanded["blocked"]
    assert expanded["uncovered_claims"] == 1


def test_progressive_coverage_keeps_current_gaps_and_bounds_repair_scope() -> None:
    report = build_claim_contract_coverage_report(
        _ledger(_claim(chapter_numbers=[3, 49], cognitive_chapter=3, priority="P0")),
        _contracts({"chapter_number": 1}),
        hard_through_chapter=5,
    )
    assert report["blocked"]
    assert report["items"][0]["chapter_scope"] == [3]
    assert report["items"][0]["deferred_chapters"] == [49]
    assert report["issues"][0]["repair_scope"][0]["chapters"] == [3]


def test_progressive_coverage_does_not_turn_macro_arc_endpoints_into_single_chapter_duties() -> (
    None
):
    report = build_claim_contract_coverage_report(
        _ledger(_claim(chapter_numbers=[3, 49], chapter_range={"start": 3, "end": 49})),
        _contracts({"chapter_number": 3}),
        hard_through_chapter=5,
    )
    assert report["items"][0]["coverage_status"] == "deferred"
    assert report["issues"] == []


def test_full_coverage_does_not_infer_frontier_from_missing_contracts() -> None:
    report = build_claim_contract_coverage_report(
        _ledger(_claim(chapter_numbers=[49], priority="P0")),
        _contracts({"chapter_number": 1}),
    )
    assert report["blocked"]
    assert report["uncovered_claims"] == 1


def test_coverage_entry_point_uses_outline_frontier_and_retains_future_ledger(
    tmp_path: Any,
) -> None:
    ctx = SimpleNamespace(
        layout=ProjectLayout(tmp_path / "project"),
        storage=FileSystemStorage(tmp_path),
        settings=SimpleNamespace(init_claim_coverage_enabled=True),
    )
    ledger = _ledger(
        _claim(claim_id="current", chapter_numbers=[3], priority="P0"),
        _claim(claim_id="future", chapter_numbers=[49], priority="P0"),
    )
    ledger_path = ctx.layout.memory_dir / CLAIM_LEDGER_JSON
    ctx.storage.save_json(ledger_path, ledger)
    artifacts = {"outline": {"total_chapters": 60, "hard_through_chapter": 5}}
    report = run_init_claim_contract_coverage_audit(
        ctx,
        artifacts=artifacts,
        chapter_contracts=_contracts({"chapter_number": 1}),
    )
    assert report["blocked"]
    assert report["hard_through_chapter"] == 5
    assert report["deferred_claims"] == 1
    assert report["uncovered_claims"] == 1
    assert report["issues"][0]["repair_scope"][0]["chapters"] == [3]
    assert ctx.storage.load_json(ledger_path) == ledger
    artifacts["outline"]["hard_through_chapter"] = 50
    expanded = run_init_claim_contract_coverage_audit(
        ctx,
        artifacts=artifacts,
        chapter_contracts=_contracts({"chapter_number": 1}),
    )
    assert expanded["uncovered_claims"] == 2


def test_progressive_backfill_keeps_future_reveal_anchor_and_does_not_invent_future_rows() -> None:
    claim = _claim(
        chapter_numbers=[1, 49],
        cognitive_chapter=1,
        public_reveal_chapter=49,
        cognitive_subjects=["主角"],
        cognitive_object="真相",
        cognitive_level="confirmed",
        action_level="internal",
        priority="P0",
    )
    ledger = _ledger(claim)
    contracts = _contracts({"chapter_number": 1, "cognitive_constraints": []})
    report = build_claim_contract_coverage_report(ledger, contracts, hard_through_chapter=5)
    assert report["blocked"]
    repaired = _local_cognitive_backfill(contracts, report)
    assert contracts["chapter_contracts"][0]["cognitive_constraints"] == []
    assert len(repaired["chapter_contracts"]) == 1
    constraint = repaired["chapter_contracts"][0]["cognitive_constraints"][0]
    assert constraint["cognitive_chapter"] == 1
    assert constraint["public_reveal_chapter"] == 49
    assert constraint["action_level"] == "internal"
    assert not build_claim_contract_coverage_report(ledger, repaired, hard_through_chapter=5)[
        "blocked"
    ]


def test_claim_contract_coverage_maps_event_to_required_events() -> None:
    report = build_claim_contract_coverage_report(
        _ledger(_claim()),
        _contracts({"chapter_number": 2, "required_events": ["公开关键证据"]}),
    )

    assert report["verdict"] == "accept"
    assert report["covered_claims"] == 1
    assert report["items"][0]["coverage_status"] == "covered"
    assert report["items"][0]["matched_contract_refs"][0]["field"] == "required_events"


def test_claim_contract_coverage_does_not_treat_subject_ids_as_cognitive_subjects() -> None:
    report = build_claim_contract_coverage_report(
        _ledger(_claim(subject_ids=["玄昱"])),
        _contracts({"chapter_number": 2, "required_events": ["公开关键证据"]}),
    )

    item = report["items"][0]
    assert report["verdict"] == "accept"
    assert item["coverage_status"] == "covered"
    assert item.get("requires_cognitive_coverage") is None
    assert item["matched_contract_refs"][0]["field"] == "required_events"


def test_claim_contract_coverage_matches_cognitive_constraints_by_claim_id() -> None:
    claim = _claim(
        claim_type="knowledge",
        cognitive_subjects=["玄昱"],
        cognitive_object="沈清漪即青阳会盟少年",
        cognitive_level="confirmed",
        action_level="internal",
        reader_awareness="full",
        character_knowledge_coverage={"玄昱": "full", "沈清漪": "unknown"},
        cognitive_chapter=1,
        public_reveal_chapter=50,
        chapter_numbers=[1],
        claim_text="玄昱确认沈清漪即青阳会盟少年。",
    )
    report = build_claim_contract_coverage_report(
        _ledger(claim),
        _contracts(
            {
                "chapter_number": 1,
                "cognitive_constraints": [
                    {
                        "claim_id": "claim_event_1",
                        "claim_text": "玄昱确认沈清漪即青阳会盟少年。",
                        "cognitive_subjects": ["玄昱"],
                        "cognitive_object": "沈清漪即青阳会盟少年",
                        "cognitive_level": "confirmed",
                        "action_level": "internal",
                        "reader_awareness": "full",
                        "character_knowledge_coverage": {"玄昱": "full", "沈清漪": "unknown"},
                        "cognitive_chapter": 1,
                        "public_reveal_chapter": 50,
                        "foreshadow_chapters": [1, 3, 7],
                    }
                ],
            }
        ),
    )

    assert report["verdict"] == "accept"
    assert report["items"][0]["coverage_status"] == "covered"
    assert report["items"][0]["matched_contract_refs"][0]["field"] == "cognitive_constraints"


def test_claim_contract_coverage_allows_canonical_name_drift_for_exact_claim_anchor() -> None:
    claim = _claim(
        claim_type="knowledge",
        cognitive_subjects=["玄昱"],
        cognitive_object="旧怀表停止倒转",
        cognitive_level="confirmed",
        action_level="internal",
        reader_awareness="full",
        cognitive_chapter=1,
        chapter_numbers=[1],
        claim_text="玄昱确认旧怀表停止倒转。",
    )
    report = build_claim_contract_coverage_report(
        _ledger(claim),
        _contracts(
            {
                "chapter_number": 1,
                "cognitive_constraints": [
                    {
                        "claim_id": "claim_event_1",
                        "claim_text": "玄昱确认怀表停止倒转。",
                        "cognitive_subjects": ["玄昱"],
                        "cognitive_object": "怀表停止倒转",
                        "cognitive_level": "confirmed",
                        "action_level": "internal",
                        "reader_awareness": "full",
                        "cognitive_chapter": 1,
                    }
                ],
            }
        ),
    )

    assert report["verdict"] == "accept"
    assert report["items"][0]["coverage_status"] == "covered"


def test_claim_contract_coverage_matches_cognitive_constraints_by_structure_without_claim_id() -> (
    None
):
    claim = _claim(
        claim_type="knowledge",
        cognitive_subjects=["玄昱"],
        cognitive_object="沈清漪即青阳会盟少年",
        cognitive_level="confirmed",
        action_level="internal",
        reader_awareness="full",
        character_knowledge_coverage={"玄昱": "full", "沈清漪": "unknown"},
        cognitive_chapter=1,
        public_reveal_chapter=50,
        chapter_numbers=[1],
        claim_text="玄昱确认沈清漪即青阳会盟少年。",
    )
    report = build_claim_contract_coverage_report(
        _ledger(claim),
        _contracts(
            {
                "chapter_number": 1,
                "cognitive_constraints": [
                    {
                        "claim_text": "玄昱确认沈清漪即青阳会盟少年。",
                        "cognitive_subjects": ["玄昱"],
                        "cognitive_object": "沈清漪即青阳会盟少年",
                        "cognitive_level": "confirmed",
                        "action_level": "internal",
                        "reader_awareness": "full",
                        "character_knowledge_coverage": {"玄昱": "full", "沈清漪": "unknown"},
                        "cognitive_chapter": 1,
                        "public_reveal_chapter": 50,
                        "foreshadow_chapters": [1, 3, 7],
                    }
                ],
            }
        ),
    )

    item = report["items"][0]
    assert report["verdict"] == "accept"
    assert item["coverage_status"] == "covered"
    assert item["matched_contract_refs"][0]["match"] == "structure"


def test_claim_contract_coverage_rejects_rewritten_cognitive_action_level() -> None:
    claim = _claim(
        claim_type="knowledge",
        cognitive_subjects=["玄昱"],
        cognitive_object="沈清漪即青阳会盟少年",
        cognitive_level="confirmed",
        action_level="internal",
        reader_awareness="full",
        character_knowledge_coverage={"玄昱": "full", "沈清漪": "unknown"},
        cognitive_chapter=1,
        public_reveal_chapter=50,
        chapter_numbers=[1],
        claim_text="玄昱确认沈清漪即青阳会盟少年，但只在心里知道。",
    )
    report = build_claim_contract_coverage_report(
        _ledger(claim),
        _contracts(
            {
                "chapter_number": 1,
                "cognitive_constraints": [
                    {
                        "claim_id": "claim_event_1",
                        "claim_text": "玄昱公开说破沈清漪即青阳会盟少年。",
                        "cognitive_subjects": ["玄昱"],
                        "cognitive_object": "沈清漪即青阳会盟少年",
                        "cognitive_level": "acknowledged",
                        "action_level": "revealed",
                        "reader_awareness": "full",
                        "character_knowledge_coverage": {"玄昱": "full", "沈清漪": "full"},
                        "cognitive_chapter": 1,
                        "public_reveal_chapter": 1,
                        "foreshadow_chapters": [],
                    }
                ],
            }
        ),
    )

    item = report["items"][0]
    assert report["verdict"] == "warn"
    assert item["coverage_status"] == "uncovered"
    assert "cognitive_constraints 与 Claim" in item["reason"]
    assert item["suggested_contract_fields"][-1] == "cognitive_constraints"


def test_claim_contract_coverage_blocks_uncovered_p0() -> None:
    report = build_claim_contract_coverage_report(
        _ledger(_claim(priority="P0")),
        _contracts({"chapter_number": 2, "required_events": ["另一件事"]}),
        block_p0=True,
    )

    assert report["verdict"] == "needs_repair"
    assert report["blocked"] is True
    assert report["uncovered_p0_p1"] == 1
    assert report["issues"][0]["severity"] == "high"


def test_claim_contract_coverage_warns_uncovered_p1_by_default() -> None:
    report = build_claim_contract_coverage_report(
        _ledger(
            _claim(
                claim_id="claim_promise_1",
                claim_type="promise",
                claim_text="第五章兑现前世承诺。",
                evidence="大纲目标要求兑现前世承诺。",
                chapter_numbers=[5],
            )
        ),
        _contracts({"chapter_number": 5, "completion_criteria": ["完成关系缓和"]}),
    )

    assert report["verdict"] == "warn"
    assert report["blocked"] is False
    assert report["issues"][0]["severity"] == "medium"


def test_claim_contract_coverage_ignores_p2_other_as_not_applicable() -> None:
    report = build_claim_contract_coverage_report(
        _ledger(_claim(claim_type="other", claim_text="整体氛围偏悬疑。")),
        _contracts({"chapter_number": 2, "required_events": []}),
    )

    assert report["verdict"] == "accept"
    assert report["uncovered_claims"] == 0
    assert report["items"][0]["coverage_status"] == "not_applicable"


def test_claim_contract_coverage_defers_macro_claim_without_chapter_scope() -> None:
    report = build_claim_contract_coverage_report(
        _ledger(_claim(chapter_numbers=[], claim_text="主线围绕身份误认展开。")),
        _contracts({"chapter_number": 2, "required_events": []}),
    )

    assert report["verdict"] == "accept"
    assert report["items"][0]["coverage_status"] == "deferred"


def test_claim_chapter_scope_deferral_when_range_exceeds_threshold_despite_chapter_numbers() -> (
    None
):
    """chapter_range spanning > _CHAPTER_RANGE_DEFER_THRESHOLD should defer even when chapter_numbers is present.

    _claim_chapter_scope currently returns chapter_numbers immediately without
    checking chapter_range span. A macro claim covering chapters 91-120 (span 30 > 12)
    should be deferred, not treated as per-chapter.
    """
    report = build_claim_contract_coverage_report(
        _ledger(
            _claim(
                claim_id="test_claim_defer_bypass",
                claim_type="event",
                chapter_numbers=[91, 120],
                chapter_range={"start": 91, "end": 120},
                claim_text="主线跨越第91至120章的宏大叙事弧。",
            ),
        ),
        _contracts({"chapter_number": 91, "required_events": []}),
    )

    assert report["items"][0]["coverage_status"] == "deferred"
    assert "宏观" in report["items"][0]["reason"] or "延后" in report["items"][0]["reason"]


def test_claim_contract_coverage_skips_stale_hashes() -> None:
    report = build_claim_contract_coverage_report(
        _ledger(_claim(artifact_hash="old")),
        _contracts({"chapter_number": 2, "required_events": ["公开关键证据"]}),
        artifact_hashes={"outline": "new"},
    )

    assert report["verdict"] == "accept"
    assert report["total_claims"] == 0
    assert report["stale_claims"] == 1


def test_run_claim_contract_coverage_missing_ledger_blocks_by_default(tmp_path: Any) -> None:
    project_dir = tmp_path / "project"
    layout = ProjectLayout(project_dir)
    ctx = SimpleNamespace(
        layout=layout,
        storage=FileSystemStorage(tmp_path),
        settings=SimpleNamespace(init_claim_coverage_enabled=True),
        events=[],
    )
    ctx.on_step = lambda step, payload: ctx.events.append((step, payload))

    report = run_init_claim_contract_coverage_audit(
        ctx,
        artifacts={},
        chapter_contracts={"chapter_contracts": []},
    )

    assert report["degraded"] is True
    assert report["degraded_reason"] == "missing_claim_ledger"
    assert report["verdict"] == "needs_repair"
    assert report["blocked"] is True
    assert report["issues"]
    assert (layout.reports_dir / CLAIM_CONTRACT_COVERAGE_REPORT).exists()
    assert ctx.events[-1][0] == "init_claim_contract_coverage"


def test_run_claim_contract_coverage_missing_ledger_can_degrade_without_blocking(
    tmp_path: Any,
) -> None:
    project_dir = tmp_path / "project"
    layout = ProjectLayout(project_dir)
    ctx = SimpleNamespace(
        layout=layout,
        storage=FileSystemStorage(tmp_path),
        settings=SimpleNamespace(
            init_claim_coverage_enabled=True,
            init_claim_coverage_block_degraded=False,
        ),
        events=[],
    )
    ctx.on_step = lambda step, payload: ctx.events.append((step, payload))

    report = run_init_claim_contract_coverage_audit(
        ctx,
        artifacts={},
        chapter_contracts={"chapter_contracts": []},
    )

    assert report["degraded"] is True
    assert report["degraded_reason"] == "missing_claim_ledger"
    assert report["verdict"] == "accept"
    assert report["blocked"] is False
    assert report["issues"] == []


def test_run_claim_contract_coverage_blocks_when_ledger_hashes_are_stale(
    tmp_path: Any,
) -> None:
    project_dir = tmp_path / "project"
    layout = ProjectLayout(project_dir)
    storage = FileSystemStorage(tmp_path)
    ctx = SimpleNamespace(
        layout=layout,
        storage=storage,
        settings=SimpleNamespace(init_claim_coverage_enabled=True),
        events=[],
    )
    ctx.on_step = lambda step, payload: ctx.events.append((step, payload))
    storage.save_json(
        layout.memory_dir / "init_coherence_claim_ledger.json",
        _ledger(_claim(artifact_hash="old-hash")),
    )

    report = run_init_claim_contract_coverage_audit(
        ctx,
        artifacts={"outline": {"chapters": [{"chapter_number": 2, "title": "新"}]}},
        chapter_contracts=_contracts({"chapter_number": 2, "required_events": ["公开关键证据"]}),
    )

    assert report["degraded"] is True
    assert report["degraded_reason"] == "claim_ledger_artifact_hash_mismatch"
    assert report["verdict"] == "needs_repair"
    assert report["blocked"] is True
    assert report["stale_claims"] == 1


def test_claim_contract_coverage_accepts_chapter_contracts_claim_in_required_events() -> None:
    """Claims extracted from the contract itself must be self-covered.

    The contract_coherence stage re-extracts claims from the contract's
    type-specific fields (e.g., required_events). If those claims have
    cognitive fields, the audit must not punish the placement just because
    the text is in required_events instead of cognitive_constraints.
    """
    claim = _claim(
        claim_id="chapter_contracts_1_8_c1",
        artifact="chapter_contracts",
        claim_type="state",
        claim_text="玄策在合卺酒夜凭执念与笔迹确认沈清漪即十年前青阳会盟白月光少年却按兵不动",
        evidence="契约 required_events 复述该约束。",
        chapter_numbers=[1],
        cognitive_subjects=["玄策"],
        cognitive_object="沈清漪即青阳会盟白月光少年",
        cognitive_level="confirmed",
        action_level="internal",
        reader_awareness="full",
        character_knowledge_coverage={"玄策": "full", "沈清漪": "unknown"},
    )
    report = build_claim_contract_coverage_report(
        _ledger(claim),
        _contracts(
            {
                "chapter_number": 1,
                "required_events": ["玄策凭执念与笔迹识破新妻即青阳会盟白月光却按兵不动"],
                "cognitive_constraints": [],
            }
        ),
    )
    assert report["verdict"] == "accept"
    assert report["items"][0]["coverage_status"] == "covered"
    assert "chapter_contracts 自身抽取" in report["items"][0]["reason"]


def test_claim_contract_coverage_rejects_upstream_cognitive_claim_without_constraint() -> None:
    """Upstream cognitive claims must reach cognitive_constraints.

    Textual placement in required_events proves the event exists, but it
    does not preserve who knows what, when it is public, or whether the
    action remains internal. Those anchors are the whole point of the
    cognitive constraint projection.
    """
    claim = _claim(
        claim_id="blueprint_volumes_1_3_c001",
        artifact="blueprint",
        claim_type="state",
        claim_text="玄策在合卺酒夜识破沈清漪身份却按兵不动",
        evidence="卷一开篇设定。",
        chapter_numbers=[1],
        cognitive_subjects=["玄策"],
        cognitive_object="沈清漪即青阳会盟白月光少年",
        cognitive_level="confirmed",
        action_level="internal",
        reader_awareness="full",
        character_knowledge_coverage={"玄策": "full", "沈清漪": "unknown"},
    )
    report = build_claim_contract_coverage_report(
        _ledger(claim),
        _contracts(
            {
                "chapter_number": 1,
                "required_events": ["玄策在合卺酒夜识破沈清漪身份却按兵不动"],
                "cognitive_constraints": [],
            }
        ),
    )
    item = report["items"][0]
    assert item["coverage_status"] == "uncovered"
    assert item["requires_cognitive_coverage"] is True
    assert "cognitive_constraints" in item["suggested_contract_fields"]
    assert "认知语义未完成约束" in item["reason"]


def test_cognitive_coverage_gaps_no_misleading_message_for_non_cognitive_claim() -> None:
    claim = _claim(
        claim_id="test_claim_non_cognitive",
        claim_type="event",
        chapter_numbers=[1],
        cognitive_subjects=[],
        cognitive_object="",
        cognitive_level="unaware",
        action_level="none",
        reader_awareness="unknown",
        character_knowledge_coverage={},
        cognitive_chapter=None,
        public_reveal_chapter=None,
        foreshadow_chapters=[],
    )
    report = build_claim_contract_coverage_report(
        _ledger(claim),
        _contracts(
            {"chapter_number": 1, "required_events": ["发生了一件事"], "cognitive_constraints": []}
        ),
    )

    item = report["items"][0]
    assert "认知语义未完成约束" not in item["reason"], (
        f"Non-cognitive claim should not trigger cognitive gap message, got: {item['reason']}"
    )


def test_cognitive_match_rejects_action_level_conflict() -> None:
    """Cognitive text match must not bypass action_level conflicts.

    A claim with ``action_level=internal`` ("only in his mind knows")
    cannot be considered covered by a constraint marked
    ``action_level=revealed`` ("publicly reveals the secret") just
    because the two sentences share many characters. The audit must
    still flag it as uncovered so the operator can see the conflict.
    """
    claim = _claim(
        claim_id="outline_chapters_0_goal_c1",
        artifact="outline",
        claim_type="state",
        claim_text="玄昱在合卺酒夜凭执念与笔迹确认沈清漪即十年前青阳会盟白月光少年却按兵不动",
        evidence="大纲目标要求按兵不动。",
        chapter_numbers=[1],
        cognitive_subjects=["玄昱"],
        cognitive_object="沈清漪即青阳会盟白月光少年",
        cognitive_level="confirmed",
        action_level="internal",
        reader_awareness="full",
        character_knowledge_coverage={"玄昱": "full", "沈清漪": "unknown"},
        cognitive_chapter=1,
        public_reveal_chapter=50,
        foreshadow_chapters=[1, 3],
    )
    report = build_claim_contract_coverage_report(
        _ledger(claim),
        _contracts(
            {
                "chapter_number": 1,
                "cognitive_constraints": [
                    {
                        "claim_id": "claim_event_1",
                        "claim_text": "玄昱在合卺酒夜凭执念与笔迹确认沈清漪即十年前青阳会盟白月光少年却按兵不动",
                        "cognitive_subjects": ["玄昱"],
                        "cognitive_object": "沈清漪即青阳会盟白月光少年",
                        "cognitive_level": "confirmed",
                        "action_level": "revealed",  # conflicts with claim's "internal"
                        "reader_awareness": "full",
                        "character_knowledge_coverage": {"玄昱": "full", "沈清漪": "full"},
                        "cognitive_chapter": 1,
                        "public_reveal_chapter": 1,
                        "foreshadow_chapters": [],
                    }
                ],
            }
        ),
    )
    item = report["items"][0]
    assert item["coverage_status"] == "uncovered"
    assert report["verdict"] in {"warn", "needs_repair"}


def test_local_cognitive_backfill_adds_constraint_to_first_repair_chapter() -> None:
    """_local_cognitive_backfill injects a CognitiveConstraint into the first
    existing chapter when an old coverage report only has repair_scope endpoints.
    """
    from novel_forge.pipeline.long.services.init.init_claim_coverage import (
        _local_cognitive_backfill,
    )

    chapter_contracts: dict[str, Any] = {
        "chapter_contracts": [
            {"chapter_number": 91, "title": "ch91", "cognitive_constraints": []},
        ],
    }

    coverage_report: dict[str, Any] = {
        "issues": [
            {
                "id": "claim_contract_coverage_blueprint_volumes_4_4_bp_v4_arc_goal",
                "claim_id": "blueprint_volumes_4_4_bp_v4_arc_goal",
                "claim_type": "event",
                "priority": "P0",
                "description": (
                    "P0 Claim 未被最终章节契约覆盖:第四卷以彻底粉碎蜀国阴谋、"
                    "清算麋鹿旧案、完成府兵制改革与魏吴新盟缔结、残玉合一象征天下归一为主弧目标。"
                    "认知契约缺口：未写入章节契约 cognitive_constraints。"
                ),
                "repair_scope": [
                    {
                        "artifact": "chapter_contracts",
                        "chapters": [91, 120],
                        "fields": [
                            "required_events",
                            "entry_state_requirements",
                            "exit_state_targets",
                            "hard_facts",
                            "forbidden_changes",
                            "completion_criteria",
                            "cognitive_constraints",
                            "knowledge_ops",
                        ],
                    }
                ],
            }
        ],
    }

    result = _local_cognitive_backfill(chapter_contracts, coverage_report)

    ch91 = next(c for c in result["chapter_contracts"] if c["chapter_number"] == 91)
    assert len(ch91["cognitive_constraints"]) == 1
    assert ch91["cognitive_constraints"][0]["claim_id"] == "blueprint_volumes_4_4_bp_v4_arc_goal"


def test_local_cognitive_backfill_hydrates_each_explicit_claim_scope_chapter() -> None:
    from novel_forge.pipeline.long.services.init.init_claim_coverage import (
        _local_cognitive_backfill,
    )

    claim = _claim(
        claim_id="claim_multi_chapter_secret",
        artifact="blueprint",
        claim_type="knowledge",
        priority="P0",
        claim_text="第二至三章玄昱持续确认密信来源。",
        chapter_numbers=[2, 3],
        cognitive_subjects=["玄昱"],
        cognitive_object="密信来源",
        cognitive_level="confirmed",
        action_level="internal",
        reader_awareness="partial",
        character_knowledge_coverage={"玄昱": "full"},
    )
    chapter_contracts = _contracts(
        {"chapter_number": 2, "title": "ch2", "cognitive_constraints": []},
        {"chapter_number": 3, "title": "ch3", "cognitive_constraints": []},
    )
    report = build_claim_contract_coverage_report(
        _ledger(claim),
        chapter_contracts,
        block_p0=True,
    )

    repaired = _local_cognitive_backfill(chapter_contracts, report)

    by_chapter = {
        contract["chapter_number"]: contract for contract in repaired["chapter_contracts"]
    }
    assert by_chapter[2]["cognitive_constraints"][0]["claim_id"] == "claim_multi_chapter_secret"
    assert by_chapter[3]["cognitive_constraints"][0]["claim_id"] == "claim_multi_chapter_secret"


def test_local_cognitive_backfill_hydrates_strict_claim_for_reaudit() -> None:
    from novel_forge.pipeline.long.services.init.init_claim_coverage import (
        _local_cognitive_backfill,
    )

    claim = _claim(
        claim_id="narrative_contract_ending_strategy_ending_65_1",
        artifact="narrative_contract",
        source_path="/ending_strategy",
        claim_type="event",
        priority="P0",
        claim_text="第65章商演配合鬼蝉反水救出姜维清，完成双面间谍的自我救赎。",
        evidence="第65章商演配合鬼蝉反水救出姜维清。",
        chapter_numbers=[65],
        cognitive_subjects=["商演", "清涛"],
        cognitive_object="商演双面间谍身份的自我救赎",
        cognitive_level="confirmed",
        action_level="acted",
        reader_awareness="partial",
        character_knowledge_coverage={"商演": "full", "清涛": "full"},
        cognitive_chapter=65,
        foreshadow_chapters=[45],
        metadata={"entity_adjudication_status": "adjudicated"},
    )
    chapter_contracts = _contracts(
        {
            "chapter_number": 65,
            "required_events": ["商演配合鬼蝉反水救出姜维清"],
            "cognitive_constraints": [
                {
                    "claim_id": "narrative_contract_ending_strategy_ending_65_1",
                    "claim_text": "",
                    "cognitive_subjects": [],
                    "cognitive_object": "",
                    "cognitive_level": "unaware",
                    "action_level": "none",
                    "reader_awareness": "unknown",
                }
            ],
        }
    )

    report = build_claim_contract_coverage_report(
        _ledger(claim),
        chapter_contracts,
        block_p0=True,
    )
    assert report["verdict"] == "needs_repair"

    repaired = _local_cognitive_backfill(chapter_contracts, report)
    constraint = repaired["chapter_contracts"][0]["cognitive_constraints"][0]
    assert constraint["cognitive_subjects"] == ["商演", "清涛"]
    assert constraint["cognitive_object"] == "商演双面间谍身份的自我救赎"
    assert constraint["action_level"] == "acted"

    repaired_report = build_claim_contract_coverage_report(
        _ledger(claim),
        repaired,
        block_p0=True,
    )
    assert repaired_report["verdict"] == "accept"
    assert repaired_report["uncovered_p0_p1"] == 0


def test_local_cognitive_backfill_preserves_resolved_subjects_from_partial_claim() -> None:
    """An unrelated unresolved mention must not erase resolved cognitive subjects."""
    from novel_forge.pipeline.long.services.init.init_claim_coverage import (
        _local_cognitive_backfill,
    )

    claim = _claim(
        claim_id="claim_partial_entity_projection",
        artifact="outline",
        claim_type="relationship",
        priority="P0",
        claim_text="江野向沈岸透露哥哥死因与密钥数据有关。",
        evidence="江野向沈岸透露了调查结果。",
        chapter_numbers=[5],
        cognitive_subjects=["沈岸", "江野"],
        cognitive_object="江野哥哥死因与密钥数据的关联",
        cognitive_level="confirmed",
        action_level="revealed",
        reader_awareness="partial",
        character_knowledge_coverage={"沈岸": "partial", "江野": "full"},
        cognitive_chapter=5,
        metadata={"entity_adjudication_status": "partial"},
    )
    chapter_contracts = _contracts(
        {"chapter_number": 5, "title": "ch5", "cognitive_constraints": []}
    )
    report = build_claim_contract_coverage_report(
        _ledger(claim),
        chapter_contracts,
        block_p0=True,
    )

    repaired = _local_cognitive_backfill(chapter_contracts, report)
    constraint = repaired["chapter_contracts"][0]["cognitive_constraints"][0]

    assert constraint["cognitive_subjects"] == ["沈岸", "江野"]
    repaired_report = build_claim_contract_coverage_report(
        _ledger(claim),
        repaired,
        block_p0=True,
    )
    assert repaired_report["verdict"] == "accept"
