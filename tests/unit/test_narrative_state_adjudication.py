"""Tests for LLM-adjudicated narrative state plumbing."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from novel_forge.common.constants import TaskType
from novel_forge.gateway.types import ModelResponse
from novel_forge.narrative_state.evidence import EvidenceIndex
from novel_forge.narrative_state.schemas import (
    AdjudicationDecision,
    CandidateStateDelta,
    ContractCoverageReport,
    EvidenceSpan,
    FinalStateAdjudication,
)
from novel_forge.narrative_state.store import NarrativeStateStore
from novel_forge.obs.tracer import PipelineTrace
from novel_forge.pipeline.long.stages.finalize_report import _contract_slot_targets
from novel_forge.pipeline.steps.state_adjudication_step import (
    FinalStateAdjudicationInput,
    FinalStateAdjudicationStep,
    RepairAdjudicatedIssueInput,
    StateDeltaAdjudicationInput,
    StateDeltaAdjudicationStep,
    _candidate_extraction_limit,
    _coerce_adjudicated_repair_response,
    _compact_final_state_update,
    _contract_target_required_for_archive,
    _coverage_status,
    _ensure_contract_evidence_candidates,
    _filter_final_text_repairs,
    _filter_found_quotes,
    _final_allows_state_write,
    _final_merge_context_chars,
    _fit_final_merge_context_budget,
    _mechanical_final_merge_payload,
    _normalize_final_state_updates,
    _normalize_target_coverage_matrix,
    _reconcile_final_candidate_buckets,
    _repair_context_windows,
    _rescue_omitted_contract_candidates,
    _scope_candidate,
    _scope_current_state,
    _scope_entity_registry,
    _scope_final_candidates,
    _scope_final_current_state,
    _scope_final_decisions,
    _scope_memory_repair_hints,
    _scope_repair_adjudication_payload,
    _select_candidates_for_adjudication,
    build_contract_coverage_report,
    compile_contract_targets,
    normalize_candidate_state_deltas,
    persist_adjudicated_state_ledger,
    run_narrative_state_adjudication,
)


def test_contract_coverage_status_acceptance_overrides_rejected_alternate() -> None:
    assert (
        _coverage_status(
            evidence_found=True,
            accepted=["cand_accepted"],
            pending=[],
            repair=[],
            rejected=["cand_rejected"],
            final_status="not_covered",
        )
        == "accepted_covered"
    )
    assert (
        _coverage_status(
            evidence_found=True,
            accepted=["cand_accepted"],
            pending=[],
            repair=[],
            rejected=["cand_rejected"],
            final_status="needs_repair",
        )
        == "repair_requested"
    )


def test_evidence_index_locates_quote_without_verdict() -> None:
    index = EvidenceIndex(3, "第一段。\n\n沈念卿把怀表放回桌上。")

    span = index.locate_quote("怀表放回桌上")

    assert span.found is True
    assert span.chapter_number == 3
    assert span.paragraph_index == 1
    assert not hasattr(span, "verdict")


def test_evidence_index_can_prefer_last_quote_occurrence() -> None:
    index = EvidenceIndex(1, "“他们来了。”\n\n后来，他又低声说：“他们来了。”")

    span = index.locate_quote("他们来了", prefer_last=True)

    assert span.found is True
    assert span.paragraph_index == 1
    assert span.start_offset == index.text.rfind("他们来了")


def test_evidence_index_matches_chinese_quote_variants() -> None:
    index = EvidenceIndex(3, "她在便签上看见“沈”这个字。")

    span = index.locate_quote("「沈」")

    assert span.found is True
    assert span.context == "她在便签上看见“沈”这个字。"


def test_candidate_normalization_only_adds_mechanical_fields() -> None:
    candidates = normalize_candidate_state_deltas(
        [
            {
                "delta_type": "item",
                "summary": "怀表可能回到桌上。",
                "evidence": [{"quote": "怀表放回桌上"}],
            }
        ],
        chapter_number=3,
        chapter_text="沈念卿把怀表放回桌上。",
    )

    assert len(candidates) == 1
    assert candidates[0].candidate_id.startswith("cand_")
    assert candidates[0].evidence[0].found is True
    assert not hasattr(candidates[0], "verdict")


def test_candidate_normalization_maps_llm_delta_type_aliases() -> None:
    candidates = normalize_candidate_state_deltas(
        [
            {
                "delta_type": "emotional_breakthrough",
                "summary": "沈念卿可能完成情绪突破。",
                "evidence": [{"quote": "她终于敢把那句话说出口"}],
            },
            {
                "delta_type": "unexpected_micro_shift",
                "summary": "模型自造的细分类应降级为其他。",
                "evidence": [{"quote": "灯火忽然暗了一瞬"}],
            },
        ],
        chapter_number=3,
        chapter_text="她终于敢把那句话说出口。\n\n灯火忽然暗了一瞬。",
    )

    assert candidates[0].delta_type == "character_state"
    assert candidates[0].proposed_delta["subtype"] == "emotional_breakthrough"
    assert candidates[0].proposed_delta["raw_delta_type"] == "emotional_breakthrough"
    assert candidates[1].delta_type == "other"
    assert candidates[1].proposed_delta["subtype"] == "unexpected_micro_shift"


def test_candidate_normalization_coerces_loose_candidate_fields() -> None:
    candidates = normalize_candidate_state_deltas(
        [
            {
                "delta_type": "revelation",
                "summary": "林远得知裂缝真相。",
                "entity_ids": "lin_yuan, time_rift",
                "proposed_delta": "得知裂缝不是自然现象",
                "evidence": "裂缝不是自然现象",
            }
        ],
        chapter_number=4,
        chapter_text="林远终于确认，裂缝不是自然现象。",
    )

    assert candidates[0].delta_type == "knowledge"
    assert candidates[0].entity_ids == ["lin_yuan", "time_rift"]
    assert candidates[0].proposed_delta["value"] == "得知裂缝不是自然现象"
    assert candidates[0].proposed_delta["subtype"] == "revelation"
    assert candidates[0].evidence[0].found is True


def test_candidate_normalization_accepts_description_and_quote_aliases() -> None:
    candidates = normalize_candidate_state_deltas(
        [
            {
                "delta_type": "event",
                "description": "沈念卿与陆云峥完成初遇。",
                "quote": "目光精准地落在她身上",
            }
        ],
        chapter_number=1,
        chapter_text="他越过人群，目光精准地落在她身上。",
    )

    assert len(candidates) == 1
    assert candidates[0].summary == "沈念卿与陆云峥完成初遇。"
    assert candidates[0].evidence[0].quote == "目光精准地落在她身上"
    assert candidates[0].evidence[0].found is True


def test_candidate_normalization_drops_empty_placeholder_candidates() -> None:
    candidates = normalize_candidate_state_deltas(
        [{"delta_type": "event", "summary": "", "proposed_delta": {}, "evidence": []}],
        chapter_number=1,
        chapter_text="沈念卿走进会场。",
    )

    assert candidates == []


def test_candidate_normalization_drops_candidates_without_exact_chapter_quote() -> None:
    candidates = normalize_candidate_state_deltas(
        [
            {
                "delta_type": "event",
                "summary": "模型补写了正文没有的变化。",
                "evidence": [{"quote": "这句话不在正文里"}],
            }
        ],
        chapter_number=1,
        chapter_text="沈念卿走进会场。",
    )

    assert candidates == []


def test_candidate_normalization_backfills_empty_proposed_delta_as_format_payload() -> None:
    candidates = normalize_candidate_state_deltas(
        [
            {
                "delta_type": "knowledge",
                "summary": "沈念卿发现便签字迹与旧手稿相似。",
                "entity_ids": ["shen_nianqing"],
                "proposed_delta": {},
                "evidence": [{"quote": "字迹与旧手稿相似"}],
            }
        ],
        chapter_number=3,
        chapter_text="沈念卿停住手，确认便签上的字迹与旧手稿相似。",
    )

    assert len(candidates) == 1
    proposed = candidates[0].proposed_delta
    assert proposed["source"] == "llm_candidate_normalized"
    assert proposed["semantic_source"] == "llm_candidate"
    assert proposed["format_source"] == "local_format_fallback"
    assert proposed["delta_type"] == "knowledge"
    assert proposed["summary"] == "沈念卿发现便签字迹与旧手稿相似。"
    assert proposed["entity_ids"] == ["shen_nianqing"]
    assert proposed["evidence_quotes"] == ["字迹与旧手稿相似"]


def test_contract_evidence_anchor_injects_exit_state_candidate_when_llm_misses_quote() -> None:
    chapter_text = "\n\n".join(
        [
            "沈鹤卿打开暗格，里面躺着锦盒、信件和老照片。",
            "远在老城厢古董店的摇椅旁，沈鹤卿扶着货架，颤抖的手指指向暗格的方向，嘴里反复念着同一句话：“来了……他们来了……”",
        ]
    )
    candidates = [
        CandidateStateDelta(
            candidate_id="event_006",
            chapter_number=1,
            delta_type="event",
            summary="沈鹤卿打开暗格发现锦盒、信件和老照片",
            evidence=[
                EvidenceSpan(
                    quote="里面躺着锦盒、信件和老照片",
                    chapter_number=1,
                    found=True,
                )
            ],
        )
    ]

    completed = _ensure_contract_evidence_candidates(
        candidates,
        chapter_number=1,
        chapter_text=chapter_text,
        chapter_contract={
            "exit_state_targets": ["沈鹤卿在古董店颤巍巍说「他们来了」"]
        },
    )

    anchor = completed[0]
    assert anchor.candidate_id == "contract_exit_state_targets_001"
    assert anchor.summary == "契约目标已在正文出现：沈鹤卿在古董店颤巍巍说「他们来了」"
    assert anchor.proposed_delta["source"] == "chapter_contract"
    assert anchor.proposed_delta["scope"] == "contract_progression"
    assert anchor.proposed_delta["state_path"] == "contract.exit_state_targets.1.01"
    assert anchor.proposed_delta["contract_field"] == "exit_state_targets"
    assert anchor.evidence[0].quote == "他们来了"
    assert anchor.evidence[0].found is True
    assert anchor.evidence[0].paragraph_index == 1
    assert completed[1:] == candidates


def test_contract_evidence_anchor_does_not_duplicate_existing_quote_candidate() -> None:
    chapter_text = "沈鹤卿颤巍巍说：“他们来了。”"
    candidates = normalize_candidate_state_deltas(
        [
            {
                "candidate_id": "event_existing",
                "delta_type": "event",
                "summary": "沈鹤卿说出关键台词。",
                "evidence": [{"quote": "他们来了"}],
            }
        ],
        chapter_number=1,
        chapter_text=chapter_text,
    )

    completed = _ensure_contract_evidence_candidates(
        candidates,
        chapter_number=1,
        chapter_text=chapter_text,
        chapter_contract={
            "exit_state_targets": ["沈鹤卿在古董店颤巍巍说「他们来了」"]
        },
    )

    assert completed == candidates


def test_current_state_scoping_exposes_fixed_update_slots_and_roster() -> None:
    scoped = _scope_current_state(
        {
            "chapter_number": 10,
            "character_roster": [
                {
                    "character_id": "char_shen",
                    "name": "沈念卿",
                    "role": "protagonist",
                    "gender": "女",
                    "social_status": "记者",
                    "appearance": "不应进入状态抽取",
                }
            ],
            "state_update_slots": {
                "chapter_presence": {
                    "state_path": "chapter.10.present_characters",
                    "allowed_character_names": ["沈念卿"],
                    "write_fields": ["present_characters", "summary"],
                },
                "main_plot": [
                    {
                        "state_path": "plot.main.chapter_10.01",
                        "target": "沈念卿收到关键密信",
                        "write_fields": ["summary", "value", "next_impact"],
                    }
                ],
            },
        }
    )

    assert scoped["character_roster"] == [
        {
            "character_id": "char_shen",
            "name": "沈念卿",
            "role": "protagonist",
            "gender": "女",
            "social_status": "记者",
        }
    ]
    assert scoped["state_update_slots"]["chapter_presence"]["state_path"] == (
        "chapter.10.present_characters"
    )
    assert scoped["state_update_slots"]["main_plot"][0]["state_path"] == (
        "plot.main.chapter_10.01"
    )


def test_current_state_scoping_filters_non_character_roster_noise() -> None:
    scoped = _scope_current_state(
        {
            "chapter_number": 3,
            "character_roster": [
                {"name": "builtin", "status": "active"},
                {"name": "checklist", "status": "active"},
                {
                    "character_id": "char_qingyi",
                    "name": "沈清漪",
                    "role": "protagonist",
                    "gender": "女",
                    "status": "active",
                },
            ],
        }
    )

    assert [item["name"] for item in scoped["character_roster"]] == ["沈清漪"]


def test_current_state_scoping_preserves_complete_scoped_inputs_without_history_duplicates() -> None:
    long_exit = "上一章出口状态" * 80
    scoped = _scope_current_state(
        {
            "chapter_number": 88,
            "character_roster": [
                {"character_id": f"char_{index}", "name": f"角色{index}", "role": "support"}
                for index in range(55)
            ],
            "state_update_slots": {
                "main_plot": [
                    {"state_path": f"plot.main.88.{index}", "target": f"目标{index}"}
                    for index in range(25)
                ]
            },
            "previous_exit_state": {
                "must_carry_forward": [f"承接{index}" for index in range(14)],
                "notes": long_exit,
            },
            "active_relationships": [{"pair_id": f"pair_{index}"} for index in range(20)],
            "active_plot_threads": [{"thread_id": f"thread_{index}"} for index in range(18)],
            "canon_snapshot": {"duplicated": True},
            "accepted_state_ledger_tail": [{"candidate_id": "accepted_duplicate"}],
            "authoritative_projection": {
                "last_chapter": 87,
                "accepted_updates": [{"candidate_id": "accepted_duplicate"}],
                "recent_summaries": ["重复历史"],
                "pending_items": [{"candidate_id": f"pending_{index}"} for index in range(9)],
            },
        }
    )

    assert len(scoped["character_roster"]) == 55
    assert len(scoped["state_update_slots"]["main_plot"]) == 25
    assert scoped["previous_exit_state"]["notes"] == long_exit
    assert len(scoped["previous_exit_state"]["must_carry_forward"]) == 14
    assert len(scoped["active_relationships"]) == 20
    assert len(scoped["active_plot_threads"]) == 18
    assert len(scoped["authoritative_projection"]["pending_items"]) == 9
    assert "canon_snapshot" not in scoped
    assert "accepted_state_ledger_tail" not in scoped
    assert "accepted_updates" not in scoped["authoritative_projection"]


def test_entity_registry_routes_referenced_entity_without_first_n_or_alias_truncation() -> None:
    aliases = [f"别名{index}" for index in range(12)]
    entities = [
        {
            "entity_id": f"char_{index}",
            "name": f"角色{index}",
            "entity_type": "character",
            "aliases": [],
        }
        for index in range(55)
    ]
    entities.append(
        {
            "entity_id": "char_target",
            "name": "目标角色",
            "entity_type": "character",
            "aliases": aliases,
        }
    )

    scoped = _scope_entity_registry(
        {"entities": entities},
        chapter_text=f"门后传来{aliases[-1]}的声音。",
        current_state={},
        chapter_contract={},
    )

    assert scoped == {
        "entities": [
            {
                "entity_id": "char_target",
                "name": "目标角色",
                "entity_type": "character",
                "aliases": aliases,
            }
        ]
    }


def test_final_state_updates_are_limited_to_fixed_slots() -> None:
    candidate = CandidateStateDelta(
        candidate_id="c_main",
        chapter_number=10,
        delta_type="event",
        summary="密信交到沈念卿手中。",
        proposed_delta={
            "source": "chapter_text",
            "scope": "main_plot",
            "state_path": "plot.main.chapter_10.01",
            "summary": "密信交到沈念卿手中。",
            "value": "密信已交付",
            "context": "不应保留",
        },
        evidence=[EvidenceSpan(quote="密信交到她手中", chapter_number=10, found=True)],
    )

    updates = _normalize_final_state_updates(
        [
            {
                "candidate_id": "c_bad",
                "scope": "main_plot",
                "state_path": "plot.main.freeform",
                "summary": "自造路径不应写入。",
            },
            {
                "candidate_id": "c_main",
                "scope": "main_plot",
                "state_path": "plot.main.chapter_10.01",
                "summary": "密信交到沈念卿手中。",
                "value": "密信已交付",
                "context": "不应保留",
            },
        ],
        candidates=[candidate],
        current_state={
            "state_update_slots": {
                "main_plot": [{"state_path": "plot.main.chapter_10.01"}]
            }
        },
        accepted_candidate_ids=["c_main"],
    )

    assert updates == [
        {
            "candidate_id": "c_main",
            "scope": "main_plot",
            "state_path": "plot.main.chapter_10.01",
            "value": "密信已交付",
            "summary": "密信交到沈念卿手中。",
            "source": "chapter_text",
        }
    ]


def test_final_state_updates_fall_back_to_candidate_slot_when_final_omits_path() -> None:
    candidate = CandidateStateDelta(
        candidate_id="c_main",
        chapter_number=10,
        delta_type="event",
        summary="密信交到沈念卿手中。",
        proposed_delta={
            "source": "chapter_text",
            "scope": "main_plot",
            "state_path": "plot.main.chapter_10.01",
            "summary": "密信交到沈念卿手中。",
            "value": "密信已交付",
        },
        evidence=[EvidenceSpan(quote="密信交到她手中", chapter_number=10, found=True)],
    )

    updates = _normalize_final_state_updates(
        [{"candidate_id": "c_main", "summary": "缺少 state_path 的最终裁判输出"}],
        candidates=[candidate],
        current_state={
            "state_update_slots": {
                "main_plot": [{"state_path": "plot.main.chapter_10.01"}]
            }
        },
        accepted_candidate_ids=["c_main"],
    )

    assert updates == [
        {
            "candidate_id": "c_main",
            "scope": "main_plot",
            "state_path": "plot.main.chapter_10.01",
            "value": "密信已交付",
            "summary": "密信交到沈念卿手中。",
            "source": "chapter_text",
        }
    ]


def test_final_state_updates_keep_knowledge_op_write_fields() -> None:
    candidate = CandidateStateDelta(
        candidate_id="c_knowledge",
        chapter_number=4,
        delta_type="knowledge",
        summary="林远怀疑裂缝不是自然现象。",
        entity_ids=["char_lin_yuan"],
        proposed_delta={
            "source": "chapter_contract",
            "scope": "contract_progression",
            "state_path": "contract.knowledge_ops.4.01",
            "summary": "林远怀疑裂缝不是自然现象。",
            "value": "裂缝不是自然现象",
            "entity_ids": ["char_lin_yuan"],
            "knowledge_type": "suspected",
        },
        evidence=[EvidenceSpan(quote="裂缝不是自然现象", chapter_number=4, found=True)],
    )

    updates = _normalize_final_state_updates(
        [{"candidate_id": "c_knowledge", "summary": "最终裁判省略了知识字段"}],
        candidates=[candidate],
        current_state={
            "state_update_slots": {
                "contract_progression": [
                    {"state_path": "contract.knowledge_ops.4.01"}
                ]
            }
        },
        accepted_candidate_ids=["c_knowledge"],
    )

    assert updates == [
        {
            "candidate_id": "c_knowledge",
            "scope": "contract_progression",
            "state_path": "contract.knowledge_ops.4.01",
            "value": "裂缝不是自然现象",
            "summary": "林远怀疑裂缝不是自然现象。",
            "entity_ids": ["char_lin_yuan"],
            "knowledge_type": "suspected",
            "source": "chapter_contract",
        }
    ]


def test_final_state_updates_merge_candidate_cognition_and_keep_all_accepted() -> None:
    candidates = [
        CandidateStateDelta(
            candidate_id=f"c_cognition_{index}",
            chapter_number=10,
            delta_type="cognitive",
            summary=f"角色完成认知变化 {index}",
            proposed_delta={
                "candidate_id": f"c_cognition_{index}",
                "source": "chapter_contract",
                "scope": "contract_progression",
                "state_path": f"contract.cognitive_constraints.10.{index:02d}",
                "summary": f"角色完成认知变化 {index}",
                "value": f"线索 {index}",
                "cognitive_subjects": ["林小满", "沈崖"],
                "cognitive_object": f"线索 {index}",
                "cognitive_level": "confirmed",
                "action_level": "can_prepare",
                "character_knowledge_coverage": {
                    "林小满": "full",
                    "沈崖": "partial",
                },
            },
            evidence=[EvidenceSpan(quote=f"线索 {index}", chapter_number=10, found=True)],
        )
        for index in range(1, 9)
    ]
    paths = [candidate.proposed_delta["state_path"] for candidate in candidates]

    updates = _normalize_final_state_updates(
        [
            {
                "candidate_id": candidate.candidate_id,
                "state_path": candidate.proposed_delta["state_path"],
                "value": f"裁决确认 {index}",
            }
            for index, candidate in enumerate(candidates, start=1)
        ],
        candidates=candidates,
        current_state={
            "state_update_slots": {
                "contract_progression": [{"state_path": path} for path in paths]
            }
        },
        accepted_candidate_ids=[candidate.candidate_id for candidate in candidates],
    )

    assert len(updates) == 8
    assert updates[-1]["value"] == "裁决确认 8"
    assert updates[-1]["cognitive_subjects"] == ["林小满", "沈崖"]
    assert updates[-1]["character_knowledge_coverage"] == {
        "林小满": "full",
        "沈崖": "partial",
    }


def test_cognitive_contract_target_is_writable_without_becoming_archive_required() -> None:
    slots = _contract_slot_targets(
        {
            "chapter_number": 10,
            "cognitive_constraints": [
                {
                    "claim_id": "claim_ladder_10",
                    "claim_text": "林小满已经确认铃声来源。",
                    "cognitive_subjects": ["林小满", "沈崖"],
                    "cognitive_object": "铃声来源",
                    "cognitive_level": "confirmed",
                    "action_level": "hinted",
                    "reader_awareness": "partial",
                    "character_knowledge_coverage": {
                        "林小满": "full",
                        "沈崖": "unknown",
                    },
                }
            ],
        },
        chapter_number=10,
    )

    assert len(slots) == 1
    assert slots[0]["required_for_archive"] is False
    assert slots[0]["state_path"] == "contract.cognitive_constraints.10.01"
    assert "character_knowledge_coverage" in slots[0]["write_fields"]


def test_contract_evidence_anchor_uses_schema_metadata_not_fixed_field_list() -> None:
    completed = _ensure_contract_evidence_candidates(
        [],
        chapter_number=2,
        chapter_text="林远终于决定进入裂缝，完成这一章的可观察标准。",
        chapter_contract={
            "completion_criteria": ["林远终于决定进入裂缝"],
            "allowed_changes": ["完成这一章的可观察标准"],
        },
    )

    assert [candidate.candidate_id for candidate in completed] == [
        "contract_completion_criteria_001"
    ]
    assert completed[0].proposed_delta["contract_field"] == "completion_criteria"
    assert completed[0].evidence[0].quote == "林远终于决定进入裂缝"


def test_contract_coverage_report_marks_accepted_contract_anchor() -> None:
    candidates = _ensure_contract_evidence_candidates(
        [],
        chapter_number=1,
        chapter_text="沈鹤卿低声说：“他们来了。”",
        chapter_contract={
            "chapter_number": 1,
            "exit_state_targets": ["沈鹤卿说出「他们来了」"],
        },
    )
    report = build_contract_coverage_report(
        chapter_number=1,
        chapter_contract={
            "chapter_number": 1,
            "exit_state_targets": ["沈鹤卿说出「他们来了」"],
        },
        candidates=candidates,
        decisions=[
            AdjudicationDecision(
                candidate_id="contract_exit_state_targets_001",
                verdict="accept",
                confidence=0.9,
            )
        ],
    )

    assert report.total_required_targets == 1
    assert report.covered_count == 1
    assert report.all_required_covered is True
    assert report.items[0].status == "accepted_covered"
    assert report.items[0].accepted_candidate_ids == ["contract_exit_state_targets_001"]
    assert report.uncovered_targets == []


def test_contract_evidence_anchor_injects_knowledge_op_candidate() -> None:
    candidates = _ensure_contract_evidence_candidates(
        [],
        chapter_number=4,
        chapter_text="林远终于确认，裂缝不是自然现象。",
        chapter_contract={
            "chapter_number": 4,
            "knowledge_ops": [
                {
                    "type": "suspect",
                    "character": "林远",
                    "description": "裂缝不是自然现象",
                    "evidence": "裂缝不是自然现象",
                }
            ],
        },
        entity_registry={
            "entities": [
                {
                    "entity_id": "char_lin_yuan",
                    "name": "林远",
                    "entity_type": "character",
                }
            ]
        },
    )

    anchor = candidates[0]
    assert anchor.candidate_id == "contract_knowledge_ops_001"
    assert anchor.delta_type == "knowledge"
    assert anchor.entity_ids == ["char_lin_yuan"]
    assert anchor.summary == "契约知识变化已在正文出现：林远怀疑：裂缝不是自然现象"
    assert anchor.proposed_delta["contract_field"] == "knowledge_ops"
    assert anchor.proposed_delta["state_path"] == "contract.knowledge_ops.4.01"
    assert anchor.proposed_delta["knowledge_type"] == "suspected"
    assert anchor.proposed_delta["value"] == "裂缝不是自然现象"
    assert anchor.evidence[0].quote == "裂缝不是自然现象"
    assert anchor.evidence[0].found is True


def test_contract_coverage_report_marks_accepted_knowledge_op() -> None:
    contract = {
        "chapter_number": 4,
        "knowledge_ops": [
            {
                "type": "known",
                "character": "林远",
                "description": "裂缝不是自然现象",
                "evidence": "裂缝不是自然现象",
            }
        ],
    }
    candidates = _ensure_contract_evidence_candidates(
        [],
        chapter_number=4,
        chapter_text="林远终于确认，裂缝不是自然现象。",
        chapter_contract=contract,
    )

    report = build_contract_coverage_report(
        chapter_number=4,
        chapter_contract=contract,
        candidates=candidates,
        decisions=[
            AdjudicationDecision(
                candidate_id="contract_knowledge_ops_001",
                verdict="accept",
                confidence=0.9,
            )
        ],
    )

    assert report.total_required_targets == 1
    assert report.covered_count == 1
    assert report.items[0].field_name == "knowledge_ops"
    assert report.items[0].status == "accepted_covered"
    assert report.uncovered_targets == []


def test_contract_coverage_report_keeps_unaccepted_targets_visible() -> None:
    candidates = _ensure_contract_evidence_candidates(
        [],
        chapter_number=1,
        chapter_text="沈鹤卿低声说：“他们来了。”",
        chapter_contract={
            "chapter_number": 1,
            "exit_state_targets": ["沈鹤卿说出「他们来了」"],
        },
    )
    report = build_contract_coverage_report(
        chapter_number=1,
        chapter_contract={
            "chapter_number": 1,
            "exit_state_targets": ["沈鹤卿说出「他们来了」"],
        },
        candidates=candidates,
        decisions=[
            AdjudicationDecision(
                candidate_id="contract_exit_state_targets_001",
                verdict="ambiguous",
                confidence=0.5,
            )
        ],
    )

    assert report.covered_count == 0
    assert report.evidence_found_count == 1
    assert report.items[0].status == "pending"
    assert report.unaccepted_targets[0]["candidate_ids"] == [
        "contract_exit_state_targets_001"
    ]


def test_llm_selected_contract_target_derives_local_state_path() -> None:
    contract = {
        "chapter_number": 3,
        "exit_state_targets": [
            "清漪与玄昱达成明牌合作关系，各有攻防",
            "玄昱当众握住清漪手向姜维清介绍正妻",
        ],
    }
    targets = compile_contract_targets(contract, chapter_number=3)
    candidates = normalize_candidate_state_deltas(
        [
            {
                "candidate_id": "cp3_003",
                "delta_type": "event",
                "summary": "玄昱当众握住清漪手向姜维清介绍正妻。",
                "covered_target_ids": ["exit_state_targets.3.02"],
                "proposed_delta": {
                    "scope": "main_plot",
                    "state_path": "plot.main.chapter_3.03",
                    "summary": "玄昱公开介绍正妻",
                },
                "evidence": [
                    {
                        "quote": "本殿想向长公主介绍一下。这位，是我的正妻，沈氏清漪。"
                    }
                ],
            }
        ],
        chapter_number=3,
        chapter_text="本殿想向长公主介绍一下。这位，是我的正妻，沈氏清漪。",
        contract_targets=targets,
    )

    candidate = candidates[0]
    assert candidate.covered_target_ids == ["exit_state_targets.3.02"]
    assert candidate.proposed_delta["scope"] == "contract_progression"
    assert candidate.proposed_delta["state_path"] == "contract.exit_state_targets.3.02"

    report = build_contract_coverage_report(
        chapter_number=3,
        chapter_contract=contract,
        candidates=candidates,
        decisions=[
            AdjudicationDecision(
                candidate_id="cp3_003",
                verdict="accept",
                covered_target_ids=["exit_state_targets.3.02"],
                coverage_status="covered",
                issue_kind="mapping",
                repair_kind="mapping",
            )
        ],
    )

    covered = [item for item in report.items if item.status == "accepted_covered"]
    assert [item.target_id for item in covered] == ["exit_state_targets.3.02"]


def test_cognitive_constraint_target_preserves_cognitive_payload(tmp_path: Path) -> None:
    contract = {
        "chapter_number": 2,
        "cognitive_constraints": [
            {
                "constraint_id": "constraint_ladder_15",
                "claim_id": "claim_ladder_15",
                "claim_text": "小书童无人见过",
                "cognitive_object": "小书童身份线",
                "cognitive_subjects": ["沈清漪"],
                "cognitive_level": "suspicion",
                "action_level": "hinted",
                "character_knowledge_coverage": {"沈清漪": "partial", "玄昱": "unknown"},
                "cognitive_chapter": 15,
                "public_reveal_chapter": 45,
            }
        ],
    }

    targets = compile_contract_targets(contract, chapter_number=2)

    assert len(targets) == 1
    target = targets[0]
    assert target.target_id == "cognitive_constraints.2.01"
    assert target.field_name == "cognitive_constraints"
    assert target.target == (
        "claim=claim_ladder_15；小书童无人见过；对象=小书童身份线；"
        "认知主体=沈清漪；允许认知=suspicion；允许行动=hinted；"
        "角色知情=沈清漪=partial, 玄昱=unknown；"
        "认知锚点=第15章；公开锚点=第45章"
    )
    assert target.required_for_archive is False
    assert target.state_path == "contract.cognitive_constraints.2.01"
    assert target.delta_type == "cognitive"

    candidates = _ensure_contract_evidence_candidates(
        [],
        chapter_number=2,
        chapter_text="沈清漪终于确认，小书童无人见过。",
        chapter_contract=contract,
    )

    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.delta_type == "cognitive"
    assert candidate.covered_target_ids == ["cognitive_constraints.2.01"]
    assert candidate.cognitive_subjects == ["沈清漪"]
    assert candidate.cognitive_object == "小书童身份线"
    assert candidate.cognitive_level == "suspicion"
    assert candidate.action_level == "hinted"
    assert candidate.character_knowledge_coverage == {"沈清漪": "partial", "玄昱": "unknown"}
    assert candidate.proposed_delta["state_path"] == "contract.cognitive_constraints.2.01"
    assert candidate.proposed_delta["cognitive_level"] == "suspicion"
    assert candidate.proposed_delta["character_knowledge_coverage"] == {
        "沈清漪": "partial",
        "玄昱": "unknown",
    }

    store = NarrativeStateStore(tmp_path)
    final = FinalStateAdjudication(
        chapter_number=2,
        verdict="accept",
        accepted_candidate_ids=[candidate.candidate_id],
        state_updates=[candidate.proposed_delta],
    )
    entries = store.build_ledger_entries(
        chapter_number=2,
        candidates=[candidate],
        decisions=[AdjudicationDecision(candidate_id=candidate.candidate_id, verdict="accept")],
        final_adjudication=final,
    )
    store.append_entries(entries)

    projection = store.load_projection()
    cognitive_updates = projection.by_delta_type["cognitive"]
    assert cognitive_updates[0]["state_update"]["cognitive_level"] == "suspicion"
    assert cognitive_updates[0]["state_update"]["cognitive_object"] == "小书童身份线"
    assert cognitive_updates[0]["state_update"]["character_knowledge_coverage"] == {
        "沈清漪": "partial",
        "玄昱": "unknown",
    }


def test_final_state_update_keeps_complete_cognitive_role_coverage() -> None:
    coverage = {f"角色{index}": "partial" for index in range(10)}
    subjects = list(coverage)
    update = _compact_final_state_update(
        {
            "state_path": "contract.cognitive_constraints.8.01",
            "cognitive_subjects": subjects,
            "character_knowledge_coverage": coverage,
        },
        allowed_paths={"contract.cognitive_constraints.8.01"},
    )

    assert update["cognitive_subjects"] == subjects
    assert update["character_knowledge_coverage"] == coverage


def test_archive_required_targets_are_schema_metadata_driven() -> None:
    contract = {
        "chapter_number": 1,
        "entry_state_requirements": ["前置状态"],
        "required_events": ["必达事件"],
        "knowledge_ops": [{"fact": "角色获知事实", "knowledge_type": "known"}],
        "cognitive_constraints": [{"claim": "只可怀疑", "cognitive_level": "suspicion"}],
        "exit_state_targets": ["出口状态"],
        "required_progressions": ["必须推进"],
        "completion_criteria": ["完成标准"],
    }

    targets = compile_contract_targets(contract, chapter_number=1)
    required_by_field = {target.field_name: target.required_for_archive for target in targets}

    assert required_by_field["entry_state_requirements"] is False
    assert required_by_field["cognitive_constraints"] is False
    assert required_by_field["required_events"] is True
    assert required_by_field["knowledge_ops"] is True
    assert required_by_field["exit_state_targets"] is True
    assert required_by_field["required_progressions"] is True
    assert required_by_field["completion_criteria"] is True
    assert _contract_target_required_for_archive("cognitive_constraints") is False
    assert _contract_target_required_for_archive("required_events") is True


def test_candidate_extraction_limit_scales_with_archive_required_targets() -> None:
    contract = {
        "chapter_number": 1,
        "required_events": [f"事件{i}" for i in range(1, 5)],
        "exit_state_targets": [f"出口{i}" for i in range(1, 5)],
        "required_progressions": [f"推进{i}" for i in range(1, 5)],
    }
    targets = compile_contract_targets(contract, chapter_number=1)

    limit = _candidate_extraction_limit(
        SimpleNamespace(narrative_state_candidate_max_count=6),
        targets,
    )

    assert limit == 16


def test_candidate_selection_keeps_all_required_candidates_over_limit() -> None:
    contract = {
        "chapter_number": 1,
        "required_events": [f"硬目标{i}" for i in range(1, 9)],
    }
    targets = compile_contract_targets(contract, chapter_number=1)
    candidates = [
        CandidateStateDelta(
            candidate_id=f"hard_{index:02d}",
            chapter_number=1,
            delta_type="event",
            summary=f"硬目标{index}",
            covered_target_ids=[f"required_events.1.{index:02d}"],
            proposed_delta={
                "source": "chapter_text",
                "delta_type": "event",
                "covered_target_ids": [f"required_events.1.{index:02d}"],
            },
            evidence=[EvidenceSpan(quote=f"硬目标{index}", found=True)],
        )
        for index in range(1, 9)
    ]

    selected, omitted = _select_candidates_for_adjudication(
        candidates,
        max_candidates=6,
        contract_targets=targets,
    )

    assert [candidate.candidate_id for candidate in selected] == [
        f"hard_{index:02d}" for index in range(1, 9)
    ]
    assert omitted == []


def test_candidate_selection_keeps_required_candidate_over_non_required_anchor() -> None:
    contract = {
        "chapter_number": 1,
        "required_events": ["残玉试探", "小书童伏笔", "蜀国影卫传信"],
        "knowledge_ops": ["玄昱怀疑青阳旧约", "清漪记下小书童异常"],
        "exit_state_targets": ["小书童异常待验证", "残玉各留底牌", "影卫完成渗透"],
        "required_progressions": [
            "三线启幕",
            "青阳旧约交锋",
            "小书童虚影初现",
            "影卫短笛传信启动",
            "宇文铎门阀博弈",
        ],
        "completion_criteria": [
            "合卺酒试探完成",
            "清漪记下异常",
            "影卫传信闭环",
            "宇文铎意图铺垫",
        ],
        "cognitive_constraints": [
            {
                "constraint_id": "claim_ladder_15",
                "claim": "小书童无人见过",
                "object": "小书童身份线",
                "cognitive_subjects": ["沈清漪"],
                "cognitive_level": "suspicion",
                "action_level": "hinted",
            }
        ],
    }
    targets = compile_contract_targets(contract, chapter_number=1)

    def candidate(
        candidate_id: str,
        target_ids: list[str],
        *,
        source: str = "chapter_text",
        extraction_notes: str = "",
    ) -> CandidateStateDelta:
        return CandidateStateDelta(
            candidate_id=candidate_id,
            chapter_number=1,
            delta_type="event",
            summary=candidate_id,
            covered_target_ids=target_ids,
            proposed_delta={
                "source": source,
                "delta_type": "event",
                "covered_target_ids": target_ids,
            },
            evidence=[EvidenceSpan(quote=candidate_id, found=True)],
            extraction_notes=extraction_notes,
        )

    candidates = [
        candidate(
            "contract_cognitive_constraints_001",
            ["cognitive_constraints.1.01"],
            source="chapter_contract",
            extraction_notes="mechanical_contract_evidence_anchor",
        ),
        candidate("c1_chapter_presence", ["exit_state_targets.1.01", "exit_state_targets.1.03"]),
        candidate(
            "c2_jade_pendant_exchange",
            [
                "required_events.1.01",
                "exit_state_targets.1.02",
                "required_progressions.1.02",
                "completion_criteria.1.01",
                "knowledge_ops.1.01",
            ],
        ),
        candidate(
            "c3_scholar_figure_hinted",
            [
                "required_events.1.02",
                "exit_state_targets.1.01",
                "required_progressions.1.03",
                "completion_criteria.1.02",
                "knowledge_ops.1.02",
            ],
        ),
        candidate(
            "c4_shu_yingwei_infiltration",
            [
                "required_events.1.03",
                "exit_state_targets.1.03",
                "required_progressions.1.04",
                "completion_criteria.1.03",
            ],
        ),
        candidate(
            "c5_yuwenduo_calculating",
            ["required_progressions.1.05", "completion_criteria.1.04"],
        ),
        candidate(
            "c6_qingyang_old_pact_awareness",
            [
                "knowledge_ops.1.01",
                "knowledge_ops.1.02",
                "required_progressions.1.01",
                "required_progressions.1.02",
                "completion_criteria.1.01",
            ],
        ),
    ]

    selected, omitted = _select_candidates_for_adjudication(
        candidates,
        max_candidates=6,
        contract_targets=targets,
    )

    selected_ids = [candidate.candidate_id for candidate in selected]
    omitted_ids = [candidate.candidate_id for candidate in omitted]
    assert "c4_shu_yingwei_infiltration" in selected_ids
    assert "contract_cognitive_constraints_001" in omitted_ids


def test_hunyu_chapter_one_shadow_guard_candidate_reaches_coverage() -> None:
    contract = {
        "chapter_number": 1,
        "required_events": ["残玉试探", "小书童伏笔", "蜀国影卫传信闭环"],
        "required_progressions": [
            "三线启幕",
            "青阳旧约交锋",
            "小书童虚影初现",
            "影卫短笛传信启动",
        ],
        "completion_criteria": [
            "合卺酒试探完成",
            "清漪记下异常",
            "影卫传信闭环",
        ],
        "cognitive_constraints": [
            {
                "claim": "小书童身份线只可怀疑",
                "cognitive_level": "suspicion",
            }
        ],
    }
    targets = compile_contract_targets(contract, chapter_number=1)

    def candidate(
        candidate_id: str,
        target_ids: list[str],
        *,
        source: str = "chapter_text",
        extraction_notes: str = "",
        quote: str | None = None,
    ) -> CandidateStateDelta:
        return CandidateStateDelta(
            candidate_id=candidate_id,
            chapter_number=1,
            delta_type="event",
            summary=candidate_id,
            covered_target_ids=target_ids,
            proposed_delta={
                "source": source,
                "delta_type": "event",
                "covered_target_ids": target_ids,
            },
            evidence=[EvidenceSpan(quote=quote or candidate_id, found=True)],
            extraction_notes=extraction_notes,
        )

    candidates = [
        candidate(
            "contract_cognitive_constraints_001",
            ["cognitive_constraints.1.01"],
            source="chapter_contract",
            extraction_notes="mechanical_contract_evidence_anchor",
        ),
        candidate("c1_chapter_presence", ["required_events.1.01"]),
        candidate("c2_jade_pendant_exchange", ["required_progressions.1.02"]),
        candidate("c3_scholar_figure_hinted", ["required_events.1.02"]),
        candidate("c5_yuwenduo_calculating", ["completion_criteria.1.01"]),
        candidate("c6_qingyang_old_pact_awareness", ["required_progressions.1.03"]),
        candidate(
            "c4_shu_yingwei_infiltration",
            [
                "required_events.1.03",
                "required_progressions.1.04",
                "completion_criteria.1.03",
            ],
            quote="蜀国影卫换作侍女，以短笛完成传信闭环。",
        ),
    ]

    selected, omitted = _select_candidates_for_adjudication(
        candidates,
        max_candidates=6,
        contract_targets=targets,
    )
    selected_ids = {candidate.candidate_id for candidate in selected}
    omitted_ids = {candidate.candidate_id for candidate in omitted}

    assert "c4_shu_yingwei_infiltration" in selected_ids
    assert "contract_cognitive_constraints_001" in omitted_ids

    decisions = [
        AdjudicationDecision(
            candidate_id=candidate.candidate_id,
            verdict="accept",
            covered_target_ids=list(candidate.covered_target_ids),
            coverage_status="covered",
            issue_kind="none",
            repair_kind="none",
        )
        for candidate in selected
    ]
    coverage = build_contract_coverage_report(
        chapter_number=1,
        chapter_contract=contract,
        candidates=selected,
        decisions=decisions,
    )
    shadow_guard_targets = {
        "required_events.1.03",
        "required_progressions.1.04",
        "completion_criteria.1.03",
    }
    uncovered_ids = {str(item.get("target_id") or "") for item in coverage.uncovered_targets}

    assert shadow_guard_targets.isdisjoint(uncovered_ids)


async def test_pre_block_rescue_adjudicates_omitted_required_candidate() -> None:
    contract = {"chapter_number": 1, "required_events": ["蜀国影卫传信闭环"]}
    targets = compile_contract_targets(contract, chapter_number=1)
    omitted = [
        CandidateStateDelta(
            candidate_id="c4_shu_yingwei_infiltration",
            chapter_number=1,
            delta_type="event",
            summary="蜀国影卫传信闭环",
            covered_target_ids=["required_events.1.01"],
            proposed_delta={
                "source": "chapter_text",
                "delta_type": "event",
                "covered_target_ids": ["required_events.1.01"],
            },
            evidence=[EvidenceSpan(quote="蜀国影卫传信闭环", found=True)],
        )
    ]
    final = FinalStateAdjudication(
        chapter_number=1,
        verdict="needs_repair",
        repair_candidate_ids=["ghost"],
        should_block_archive=True,
        summary="误报无候选支持",
    )
    coverage = build_contract_coverage_report(
        chapter_number=1,
        chapter_contract=contract,
        candidates=[],
        decisions=[],
    )
    events: list[tuple[str, dict]] = []

    class FakeAdjudicationStep:
        async def run(self, input_data: StateDeltaAdjudicationInput) -> AdjudicationDecision:
            return AdjudicationDecision(
                candidate_id=input_data.candidate.candidate_id,
                verdict="accept",
                covered_target_ids=list(input_data.candidate.covered_target_ids),
                coverage_status="covered",
                issue_kind="none",
                repair_kind="none",
            )

    class FakeFinalStep:
        async def run(self, input_data: FinalStateAdjudicationInput) -> FinalStateAdjudication:
            return FinalStateAdjudication(
                chapter_number=input_data.chapter_number,
                verdict="accept",
                accepted_candidate_ids=["c4_shu_yingwei_infiltration"],
                should_block_archive=False,
                summary="rescue accepted",
            )

    candidates, omitted_after, decisions, rescued_coverage, rescued_final = (
        await _rescue_omitted_contract_candidates(
            FakeAdjudicationStep(),
            FakeFinalStep(),
            candidates=[],
            omitted_candidates=omitted,
            decisions=[],
            final_adjudication=final,
            contract_coverage=coverage,
            contract_targets=targets,
            chapter_contract=contract,
            current_state={},
            chapter_number=1,
            chapter_text="蜀国影卫传信闭环",
            on_step=lambda step, payload: events.append((step, payload)),
        )
    )

    assert [candidate.candidate_id for candidate in candidates] == ["c4_shu_yingwei_infiltration"]
    assert omitted_after == []
    assert [decision.candidate_id for decision in decisions] == ["c4_shu_yingwei_infiltration"]
    assert rescued_coverage.all_required_covered is True
    assert rescued_final.should_block_archive is False
    assert any(step == "state_adjudication_rescue" for step, _payload in events)


async def test_pre_block_rescue_keeps_block_when_no_omitted_evidence_exists() -> None:
    contract = {"chapter_number": 1, "required_events": ["蜀国影卫传信闭环"]}
    targets = compile_contract_targets(contract, chapter_number=1)
    final = FinalStateAdjudication(
        chapter_number=1,
        verdict="needs_repair",
        repair_candidate_ids=["ghost"],
        should_block_archive=True,
        summary="真实无证据",
    )
    coverage = build_contract_coverage_report(
        chapter_number=1,
        chapter_contract=contract,
        candidates=[],
        decisions=[],
    )

    result = await _rescue_omitted_contract_candidates(
        object(),
        object(),
        candidates=[],
        omitted_candidates=[],
        decisions=[],
        final_adjudication=final,
        contract_coverage=coverage,
        contract_targets=targets,
        chapter_contract=contract,
        current_state={},
        chapter_number=1,
        chapter_text="",
        on_step=lambda _step, _payload: None,
    )

    assert result[4].should_block_archive is True


def test_text_repair_kind_is_the_only_final_repair_branch() -> None:
    decisions = [
        AdjudicationDecision(
            candidate_id="cp3_003",
            verdict="accept",
            covered_target_ids=["exit_state_targets.3.02"],
            issue_kind="mapping",
            repair_kind="mapping",
        ),
        AdjudicationDecision(
            candidate_id="cp3_004",
            verdict="needs_repair",
            covered_target_ids=["exit_state_targets.3.04"],
            coverage_status="overreached",
            issue_kind="contract_overreach",
            repair_kind="text",
        ),
    ]

    payload = _filter_final_text_repairs(
        {
            "verdict": "needs_repair",
            "accepted_candidate_ids": ["cp3_003"],
            "repair_candidate_ids": ["cp3_003", "cp3_004"],
            "repair_issues": [
                {"candidate_id": "cp3_003", "repair_kind": "mapping"},
                {"candidate_id": "cp3_004", "repair_kind": "text"},
            ],
            "should_block_archive": True,
        },
        decisions=decisions,
    )

    assert payload["repair_candidate_ids"] == ["cp3_004"]
    assert payload["repair_issues"] == [{"candidate_id": "cp3_004", "repair_kind": "text"}]
    assert payload["should_block_archive"] is True


def test_mapping_only_repair_does_not_block_or_trigger_text_repair() -> None:
    payload = _filter_final_text_repairs(
        {
            "verdict": "needs_repair",
            "accepted_candidate_ids": ["cp3_003"],
            "repair_candidate_ids": ["cp3_003"],
            "repair_issues": [{"candidate_id": "cp3_003", "repair_kind": "mapping"}],
            "should_block_archive": True,
        },
        decisions=[
            AdjudicationDecision(
                candidate_id="cp3_003",
                verdict="accept",
                issue_kind="mapping",
                repair_kind="mapping",
            )
        ],
    )

    assert payload["repair_candidate_ids"] == []
    assert payload["repair_issues"] == []
    assert payload["verdict"] == "accept"
    assert payload["should_block_archive"] is False


def test_target_coverage_matrix_discards_invalid_ids_and_statuses() -> None:
    contract = {"chapter_number": 3, "exit_state_targets": ["目标一"]}
    targets = compile_contract_targets(contract, chapter_number=3)
    matrix = _normalize_target_coverage_matrix(
        [
            {
                "target_id": "exit_state_targets.3.01",
                "coverage_status": "covered",
                "candidate_ids": ["c1", "ghost"],
            },
            {"target_id": "exit_state_targets.3.99", "coverage_status": "covered"},
            {"target_id": "exit_state_targets.3.01", "coverage_status": "nonsense"},
        ],
        contract_targets=targets,
        decisions=[AdjudicationDecision(candidate_id="c1", verdict="accept")],
    )

    assert matrix == [
        {
            "target_id": "exit_state_targets.3.01",
            "coverage_status": "covered",
            "candidate_ids": ["c1"],
            "repair_kind": "none",
        }
    ]


def test_adjudication_quote_filter_keeps_only_text_backed_quotes() -> None:
    assert _filter_found_quotes(
        ["不是怀疑，不是猜测。是确认。", "不存在的句子"],
        evidence_text="她想：不是怀疑，不是猜测。是确认。",
    ) == ["不是怀疑，不是猜测。是确认。"]


def test_scoped_candidate_hides_raw_delta_type_from_adjudication_prompt() -> None:
    candidate = CandidateStateDelta(
        candidate_id="c1",
        chapter_number=4,
        delta_type="character_state",
        summary="沈念卿完成情绪突破。",
        proposed_delta={
            "subtype": "emotional_breakthrough",
            "raw_delta_type": "emotional_breakthrough",
        },
    )

    scoped = _scope_candidate(candidate)

    assert scoped["proposed_delta"]["subtype"] == "emotional_breakthrough"
    assert "raw_delta_type" not in scoped["proposed_delta"]


async def test_final_state_adjudication_uses_dynamic_output_budget(
    monkeypatch,
    runtime_settings,
) -> None:
    captured: dict[str, object] = {}

    def fake_dynamic_max_tokens(
        self,
        task_type,
        target_output_chars,
        *,
        prompt_overhead=2000,
        safety_margin=0.85,
        min_tokens=4096,
        max_cap=None,
    ) -> int:
        captured["dynamic_args"] = {
            "task_type": task_type,
            "target_output_chars": target_output_chars,
            "prompt_overhead": prompt_overhead,
            "safety_margin": safety_margin,
            "min_tokens": min_tokens,
            "max_cap": max_cap,
        }
        return 9000

    class RecordingBuilder:
        def build(
            self,
            task_type,
            context,
            *,
            max_tokens,
            temperature,
            top_p=None,
            prior_messages=None,
            thinking=False,
            multi_turn=False,
        ):
            captured["builder_max_tokens"] = max_tokens
            captured["context"] = context
            return SimpleNamespace(
                task_type=task_type,
                context=context,
                max_tokens=max_tokens,
                temperature=temperature,
                top_p=top_p,
                model_id="mock-model",
                messages=[
                    {"role": "system", "content": "system"},
                    {"role": "user", "content": json.dumps(context, ensure_ascii=False)},
                ],
                thinking=thinking,
                multi_turn=multi_turn,
            )

    class RecordingRouter:
        def output_limit_for_task(self, task_type, *, provider=None, model_id=None):
            return 12000

        async def route(self, request, *, provider=None):
            captured["router_max_tokens"] = request.max_tokens
            return ModelResponse(
                content=json.dumps(
                    {
                        "verdict": "accept",
                        "accepted_candidate_ids": ["c1"],
                        "pending_candidate_ids": [],
                        "repair_candidate_ids": [],
                        "should_block_archive": False,
                        "summary": "ok",
                    }
                ),
                model_id="mock-model",
                prompt_tokens=10,
                completion_tokens=20,
                total_tokens=30,
                latency_ms=1.0,
                cost_usd=0.0,
            )

    monkeypatch.setattr(
        FinalStateAdjudicationStep,
        "_dynamic_max_tokens",
        fake_dynamic_max_tokens,
    )

    candidate = CandidateStateDelta(
        candidate_id="c1",
        chapter_number=1,
        delta_type="event",
        summary="林远继续追查时间裂缝。",
    )
    decision = AdjudicationDecision(candidate_id="c1", verdict="accept")
    step = FinalStateAdjudicationStep(
        RecordingRouter(),
        RecordingBuilder(),
        settings=runtime_settings,
        trace=PipelineTrace(),
    )

    result = await step.run(
        FinalStateAdjudicationInput(
            chapter_number=1,
            candidates=[candidate],
            decisions=[decision],
            chapter_contract={"chapter_number": 1, "required_events": ["追查时间裂缝"]},
            current_state={},
        )
    )

    assert result.verdict == "accept"
    assert captured["dynamic_args"] == {
        "task_type": TaskType.ADJUDICATE_FINAL_STATE,
        "target_output_chars": 4200,
        "prompt_overhead": 8000,
        "safety_margin": 0.85,
        "min_tokens": 4096,
        "max_cap": 8192,
    }
    assert captured["builder_max_tokens"] == 9000
    assert captured["router_max_tokens"] == 9000
    final_context = captured["context"]
    assert isinstance(final_context, dict)
    assert json.loads(final_context["chapter_contract"]) == {"chapter_number": 1}


def test_final_state_prompt_scope_omits_evidence_and_historical_state() -> None:
    candidate = CandidateStateDelta(
        candidate_id="c1",
        chapter_number=2,
        delta_type="event",
        summary="主线推进。",
        proposed_delta={"state_path": "plot.main.2", "value": "advanced"},
        evidence=[EvidenceSpan(quote="很长的正文证据", found=True)],
    )
    decision = AdjudicationDecision(
        candidate_id="c1",
        verdict="accept",
        rationale="逐项裁判已经完成，不应在最终合并阶段重复展开。",
        evidence_quotes=["很长的正文证据"],
    )

    scoped_candidates = _scope_final_candidates([candidate])
    scoped_decisions = _scope_final_decisions([decision])
    scoped_state = _scope_final_current_state(
        {
            "chapter_number": 2,
            "state_update_slots": {"main_plot": [{"state_path": "plot.main.2"}]},
            "canon_snapshot": {"huge_history": "x" * 10000},
            "accepted_state_ledger_tail": [{"summary": "old"}],
        }
    )

    assert "evidence" not in scoped_candidates[0]
    assert "rationale" not in scoped_decisions[0]
    assert "evidence_quotes" not in scoped_decisions[0]
    assert "canon_snapshot" not in scoped_state
    assert "accepted_state_ledger_tail" not in scoped_state


async def test_final_state_adjudication_skips_model_when_item_decisions_converge(
    runtime_settings,
) -> None:
    class NeverCalledRouter:
        async def route(self, *_args, **_kwargs):
            raise AssertionError("converged final merge must not call an LLM")

    step = FinalStateAdjudicationStep(
        NeverCalledRouter(),
        SimpleNamespace(),
        settings=runtime_settings,
        trace=PipelineTrace(),
    )
    result = await step.run(
        FinalStateAdjudicationInput(
            chapter_number=1,
            candidates=[
                CandidateStateDelta(candidate_id="accepted", chapter_number=1, delta_type="event"),
                CandidateStateDelta(candidate_id="mapping", chapter_number=1, delta_type="event"),
            ],
            decisions=[
                AdjudicationDecision(candidate_id="accepted", verdict="accept"),
                AdjudicationDecision(
                    candidate_id="mapping",
                    verdict="needs_repair",
                    repair_kind="mapping",
                ),
            ],
            chapter_contract={"chapter_number": 1},
            current_state={},
            contract_coverage=ContractCoverageReport(
                chapter_number=1,
                all_required_covered=True,
            ),
        )
    )

    assert result.verdict == "accept"
    assert result.accepted_candidate_ids == ["accepted", "mapping"]
    assert result.should_block_archive is False


def test_final_merge_budget_compacts_prose_without_losing_ids_or_verdicts() -> None:
    context = {
        "chapter_number": 1,
        "candidates": json.dumps(
            [
                {
                    "candidate_id": f"candidate_{index}",
                    "delta_type": "event",
                    "summary": "候选说明" * 120,
                    "covered_target_ids": [f"target_{index}"],
                    "proposed_delta": {"state_path": f"plot.main.{index}", "value": "推进"},
                }
                for index in range(24)
            ],
            ensure_ascii=False,
        ),
        "decisions": json.dumps(
            [
                {
                    "candidate_id": f"candidate_{index}",
                    "verdict": "ambiguous",
                    "severity": "medium",
                    "repair_instruction": "修复说明" * 100,
                    "covered_target_ids": [f"target_{index}"],
                }
                for index in range(24)
            ],
            ensure_ascii=False,
        ),
        "contract_targets": json.dumps(
            [
                {
                    "target_id": f"target_{index}",
                    "target": "长契约目标说明" * 100,
                    "required_for_archive": True,
                    "state_path": f"plot.main.{index}",
                    "delta_type": "event",
                }
                for index in range(24)
            ],
            ensure_ascii=False,
        ),
        "current_state": json.dumps(
            {
                "chapter_number": 1,
                "character_roster": [
                    {"character_id": f"char_{index}", "name": "角色" * 40}
                    for index in range(12)
                ],
                "state_update_slots": {},
            },
            ensure_ascii=False,
        ),
        "contract_coverage_report": json.dumps(
            {
                "items": [
                    {
                        "target_id": f"target_{index}",
                        "status": "uncovered",
                        "candidate_ids": [f"candidate_{index}"],
                    }
                    for index in range(24)
                ]
            },
            ensure_ascii=False,
        ),
        "pre_block_recheck": False,
    }

    original_chars = _final_merge_context_chars(context)
    compacted, changed = _fit_final_merge_context_budget(context, max_chars=8000)

    assert changed is True
    assert _final_merge_context_chars(compacted) < original_chars
    assert json.loads(compacted["contract_targets"])[0]["target_id"] == "target_0"
    decision = json.loads(compacted["decisions"])[0]
    assert decision["candidate_id"] == "candidate_0"
    assert decision["verdict"] == "ambiguous"


def test_final_merge_budget_never_allows_incomplete_contract_archive() -> None:
    """Coverage gap without repair decisions is a soft warning, not a hard block."""
    payload = _mechanical_final_merge_payload(
        chapter_number=1,
        decisions=[AdjudicationDecision(candidate_id="c1", verdict="accept")],
        context_budget_exceeded=True,
        coverage_incomplete=True,
    )

    # Coverage gap alone (no repair items) should NOT block archive.
    # It produces verdict=accept with severity=medium as a downstream warning.
    assert payload["verdict"] == "accept"
    assert payload["should_block_archive"] is False
    assert payload["severity"] == "medium"
    assert payload["accepted_candidate_ids"] == ["c1"]


def test_final_candidate_buckets_cannot_promote_rejected_or_omit_accepted() -> None:
    reconciled = _reconcile_final_candidate_buckets(
        {
            "verdict": "accept",
            "accepted_candidate_ids": ["rejected", "ghost"],
            "pending_candidate_ids": [],
            "repair_candidate_ids": [],
            "should_block_archive": False,
            "summary": "",
        },
        decisions=[
            AdjudicationDecision(candidate_id="accepted", verdict="accept"),
            AdjudicationDecision(candidate_id="rejected", verdict="reject"),
            AdjudicationDecision(candidate_id="pending", verdict="defer"),
            AdjudicationDecision(
                candidate_id="repair",
                verdict="needs_repair",
                repair_kind="text",
            ),
        ],
    )

    assert reconciled["accepted_candidate_ids"] == ["accepted"]
    assert reconciled["rejected_candidate_ids"] == ["rejected"]
    assert reconciled["pending_candidate_ids"] == ["pending"]
    assert reconciled["repair_candidate_ids"] == ["repair"]
    assert reconciled["verdict"] == "needs_repair"
    assert reconciled["should_block_archive"] is True


def test_final_state_output_estimate_tracks_input_scale() -> None:
    small_candidates = [
        CandidateStateDelta(
            candidate_id="c1",
            chapter_number=1,
            delta_type="event",
            summary="林远追查裂缝。",
        )
    ]
    small_decisions = [AdjudicationDecision(candidate_id="c1", verdict="accept")]
    small_targets = compile_contract_targets(
        {"chapter_number": 1, "required_events": ["追查时间裂缝"]},
        chapter_number=1,
    )
    small_estimate = FinalStateAdjudicationStep._estimate_final_output_chars(
        candidates=small_candidates,
        decisions=small_decisions,
        contract_targets=small_targets,
        contract_coverage=None,
    )

    big_candidates = [
        CandidateStateDelta(
            candidate_id=f"c{i}",
            chapter_number=1,
            delta_type="event",
            summary=f"第 {i} 个需要归档的契约推进。",
        )
        for i in range(12)
    ]
    big_decisions = [
        AdjudicationDecision(
            candidate_id=f"c{i}",
            verdict="needs_repair" if i % 5 == 0 else "accept",
        )
        for i in range(12)
    ]
    big_contract = {
        "chapter_number": 1,
        "required_events": [f"必需事件 {i}" for i in range(10)],
        "exit_state_targets": [f"出口状态 {i}" for i in range(10)],
        "completion_criteria": [f"完成标准 {i}" for i in range(8)],
    }
    big_targets = compile_contract_targets(big_contract, chapter_number=1)
    big_coverage = build_contract_coverage_report(
        chapter_number=1,
        chapter_contract=big_contract,
        candidates=big_candidates,
        decisions=big_decisions,
    )
    big_estimate = FinalStateAdjudicationStep._estimate_final_output_chars(
        candidates=big_candidates,
        decisions=big_decisions,
        contract_targets=big_targets,
        contract_coverage=big_coverage,
    )

    assert small_estimate >= 4200
    assert big_estimate > small_estimate


def test_store_writes_only_final_llm_accepted_candidates(tmp_path: Path) -> None:
    store = NarrativeStateStore(tmp_path)
    candidate = CandidateStateDelta(
        candidate_id="c1",
        chapter_number=1,
        delta_type="event",
        summary="候选事件",
    )
    decision = AdjudicationDecision(candidate_id="c1", verdict="accept", confidence=0.9)
    final = FinalStateAdjudication(
        chapter_number=1,
        verdict="accept",
        accepted_candidate_ids=["c1"],
        rejected_candidate_ids=[],
        pending_candidate_ids=[],
        repair_candidate_ids=[],
        state_updates=[{"candidate_id": "c1", "state_path": "plot.main", "value": "advanced"}],
    )

    entries = store.build_ledger_entries(
        chapter_number=1,
        candidates=[candidate],
        decisions=[decision],
        final_adjudication=final,
    )
    store.append_entries(entries)

    saved = store.load_ledger_entries()
    assert len(saved) == 1
    assert saved[0].candidate_id == "c1"
    assert saved[0].decision.verdict == "accept"
    projection = store.load_projection()
    assert projection.facts_by_path["plot.main"] == "advanced"
    assert "event" in projection.by_delta_type
    assert store.memory_index_path.exists()


def test_store_persists_llm_pending_items(tmp_path: Path) -> None:
    store = NarrativeStateStore(tmp_path)
    candidate = CandidateStateDelta(
        candidate_id="c2",
        chapter_number=2,
        delta_type="alias",
        summary="绾绾身份待定。",
    )
    decision = AdjudicationDecision(
        candidate_id="c2",
        verdict="defer",
        pending_reason="正文证据不足。",
    )
    final = FinalStateAdjudication(
        chapter_number=2,
        verdict="defer",
        accepted_candidate_ids=[],
        pending_candidate_ids=["c2"],
        repair_candidate_ids=[],
    )

    pending = store.append_pending_items(
        chapter_number=2,
        candidates=[candidate],
        decisions=[decision],
        final_adjudication=final,
    )

    assert pending[0]["candidate_id"] == "c2"
    prompt_payload = store.projection_for_prompt(max_entries=5, max_pending=5)
    assert prompt_payload["pending_items"][0]["summary"] == "绾绾身份待定。"


def test_materialized_state_preserves_complete_nested_fixed_value(tmp_path: Path) -> None:
    store = NarrativeStateStore(tmp_path)
    long_fact = "固定状态" * 120
    candidate = CandidateStateDelta(
        candidate_id="c_complete_state",
        chapter_number=1,
        delta_type="character_state",
        summary="更新角色当前状态。",
    )
    final = FinalStateAdjudication(
        chapter_number=1,
        verdict="accept",
        accepted_candidate_ids=["c_complete_state"],
        state_updates=[
            {
                "candidate_id": "c_complete_state",
                "state_path": "characters.linwan.current_state",
                "value": {"description": long_fact, "markers": list(range(20))},
            }
        ],
    )
    entries = store.build_ledger_entries(
        chapter_number=1,
        candidates=[candidate],
        decisions=[
            AdjudicationDecision(
                candidate_id="c_complete_state",
                verdict="accept",
                confidence=0.9,
            )
        ],
        final_adjudication=final,
    )

    store.append_entries(entries)

    value = store.load_projection().facts_by_path["characters.linwan.current_state"]
    assert value == {"description": long_fact, "markers": list(range(20))}
    assert "facts_by_path" not in store.projection_for_prompt()


def test_projection_for_prompt_can_exclude_future_chapters(tmp_path: Path) -> None:
    store = NarrativeStateStore(tmp_path)
    entries = []
    for chapter_number in (1, 2, 3):
        candidate = CandidateStateDelta(
            candidate_id=f"c{chapter_number}",
            chapter_number=chapter_number,
            delta_type="event",
            summary=f"第{chapter_number}章状态。",
        )
        decision = AdjudicationDecision(
            candidate_id=f"c{chapter_number}",
            verdict="accept",
            confidence=0.9,
        )
        final = FinalStateAdjudication(
            chapter_number=chapter_number,
            verdict="accept",
            accepted_candidate_ids=[f"c{chapter_number}"],
            rejected_candidate_ids=[],
            pending_candidate_ids=[],
            repair_candidate_ids=[],
            state_updates=[
                {
                    "candidate_id": f"c{chapter_number}",
                    "state_path": f"plot.chapter_{chapter_number}",
                    "value": f"kept_{chapter_number}",
                }
            ],
        )
        entries.extend(
            store.build_ledger_entries(
                chapter_number=chapter_number,
                candidates=[candidate],
                decisions=[decision],
                final_adjudication=final,
            )
        )
    store.append_entries(entries)
    deferred = CandidateStateDelta(
        candidate_id="c_pending_3",
        chapter_number=3,
        delta_type="knowledge",
        summary="第三章待定信息。",
    )
    store.append_pending_items(
        chapter_number=3,
        candidates=[deferred],
        decisions=[AdjudicationDecision(candidate_id="c_pending_3", verdict="defer")],
        final_adjudication=FinalStateAdjudication(
            chapter_number=3,
            verdict="defer",
            pending_candidate_ids=["c_pending_3"],
        ),
    )

    prompt_payload = store.projection_for_prompt(
        max_entries=10,
        max_pending=10,
        max_chapter=1,
    )

    assert prompt_payload["last_chapter"] == 1
    assert prompt_payload["recent_summaries"] == ["第1章状态。"]
    assert [item["candidate_id"] for item in prompt_payload["accepted_updates"]] == ["c1"]
    assert "facts_by_path" not in prompt_payload
    assert prompt_payload["pending_items"] == []


def test_state_projection_keeps_complete_materialized_facts_and_bounded_history_views(
    tmp_path: Path,
) -> None:
    store = NarrativeStateStore(tmp_path)
    entries = []
    for chapter_number in range(1, 261):
        candidate_id = f"c{chapter_number}"
        candidate = CandidateStateDelta(
            candidate_id=candidate_id,
            chapter_number=chapter_number,
            delta_type="event",
            summary=f"第{chapter_number}章状态。",
            evidence=[
                EvidenceSpan(
                    quote="很长的证据" * 80,
                    paragraph_index=0,
                )
            ],
        )
        decision = AdjudicationDecision(
            candidate_id=candidate_id,
            verdict="accept",
            confidence=0.9,
        )
        final = FinalStateAdjudication(
            chapter_number=chapter_number,
            verdict="accept",
            accepted_candidate_ids=[candidate_id],
            state_updates=[
                {
                    "candidate_id": candidate_id,
                    "state_path": f"plot.chapter_{chapter_number}",
                    "value": f"kept_{chapter_number}",
                    "context": "不应进入热投影" * 80,
                }
            ],
        )
        entries.extend(
            store.build_ledger_entries(
                chapter_number=chapter_number,
                candidates=[candidate],
                decisions=[decision],
                final_adjudication=final,
            )
        )

    store.append_entries(entries)

    projection = store.load_projection()
    assert projection.entries == []
    assert len(projection.accepted_updates) < len(entries)
    assert len(projection.facts_by_path) == len(entries)
    assert len(projection.by_delta_type["event"]) < len(entries)
    assert "context" not in projection.accepted_updates[-1]["state_update"]
    assert len(projection.accepted_updates[-1]["evidence_quotes"][0]) <= 163

    prompt_payload = store.projection_for_prompt(
        max_entries=5,
        max_pending=0,
        max_chapter=1,
    )
    assert [item["candidate_id"] for item in prompt_payload["accepted_updates"]] == ["c1"]
    assert "facts_by_path" not in prompt_payload


def test_state_ledger_keeps_only_core_update_and_quote_evidence(tmp_path: Path) -> None:
    store = NarrativeStateStore(tmp_path)
    candidate = CandidateStateDelta(
        candidate_id="c_presence",
        chapter_number=1,
        delta_type="character_state",
        summary="沈念卿与陆云峥本章出场。",
        evidence=[
            EvidenceSpan(
                quote="沈念卿抬头，看见陆云峥站在门口。",
                chapter_number=1,
                paragraph_index=0,
                found=True,
                context="这一整段上下文不应写入状态账本。",
            )
        ],
    )
    final = FinalStateAdjudication(
        chapter_number=1,
        verdict="accept",
        accepted_candidate_ids=["c_presence"],
        state_updates=[
            {
                "candidate_id": "c_presence",
                "scope": "chapter_presence",
                "state_path": "chapter.1.present_characters",
                "present_characters": ["沈念卿", "陆云峥"],
                "value": ["沈念卿", "陆云峥"],
                "context": "长上下文不应写入" * 40,
            }
        ],
    )

    entries = store.build_ledger_entries(
        chapter_number=1,
        candidates=[candidate],
        decisions=[AdjudicationDecision(candidate_id="c_presence", verdict="accept")],
        final_adjudication=final,
    )

    assert entries[0].state_update["scope"] == "chapter_presence"
    assert entries[0].state_update["present_characters"] == ["沈念卿", "陆云峥"]
    assert "context" not in entries[0].state_update
    assert entries[0].evidence[0].quote == "沈念卿抬头，看见陆云峥站在门口。"
    assert entries[0].evidence[0].context == ""


def test_state_ledger_skips_accepted_candidate_without_slot_path(tmp_path: Path) -> None:
    store = NarrativeStateStore(tmp_path)
    candidate = CandidateStateDelta(
        candidate_id="c_freeform",
        chapter_number=1,
        delta_type="event",
        summary="自由状态不应落盘。",
        evidence=[EvidenceSpan(quote="自由状态", chapter_number=1, found=True)],
    )
    final = FinalStateAdjudication(
        chapter_number=1,
        verdict="accept",
        accepted_candidate_ids=["c_freeform"],
        state_updates=[{"candidate_id": "c_freeform", "summary": "没有槽位路径"}],
    )

    entries = store.build_ledger_entries(
        chapter_number=1,
        candidates=[candidate],
        decisions=[AdjudicationDecision(candidate_id="c_freeform", verdict="accept")],
        final_adjudication=final,
    )

    assert entries == []


def test_store_preserves_omitted_candidates_as_unadjudicated_pending(tmp_path: Path) -> None:
    store = NarrativeStateStore(tmp_path)
    omitted = CandidateStateDelta(
        candidate_id="c_omitted",
        chapter_number=2,
        delta_type="knowledge",
        summary="未进入本轮裁判的知识变化。",
    )
    final = FinalStateAdjudication(
        chapter_number=2,
        verdict="accept",
        accepted_candidate_ids=[],
        pending_candidate_ids=[],
        repair_candidate_ids=[],
    )

    pending = store.append_pending_items(
        chapter_number=2,
        candidates=[],
        decisions=[],
        final_adjudication=final,
        omitted_candidates=[omitted],
    )

    assert pending[0]["candidate_id"] == "c_omitted"
    assert pending[0]["flow_status"] == "not_adjudicated"
    assert store.load_projection().pending_items[0]["flow_reason"] == "candidate_cap_omitted"


def test_state_write_waits_when_llm_requests_repair() -> None:
    final = FinalStateAdjudication(
        chapter_number=1,
        verdict="needs_repair",
        accepted_candidate_ids=["c1"],
        pending_candidate_ids=[],
        repair_candidate_ids=["c1"],
        should_block_archive=False,
    )

    assert _final_allows_state_write(final) is False


def test_final_adjudication_normalizes_mixed_with_accepted_bucket_to_accept() -> None:
    final = FinalStateAdjudication.model_validate(
        {
            "chapter_number": 1,
            "verdict": "mixed",
            "accepted_candidate_ids": ["c1"],
            "rejected_candidate_ids": ["c2"],
            "pending_candidate_ids": [],
            "repair_candidate_ids": [],
            "state_updates": [
                {"candidate_id": "c1", "state_path": "plot.main", "value": "advanced"}
            ],
            "should_block_archive": False,
        }
    )

    assert final.verdict == "accept"
    assert _final_allows_state_write(final) is True


def test_final_adjudication_normalizes_mixed_with_repair_bucket_to_needs_repair() -> None:
    final = FinalStateAdjudication.model_validate(
        {
            "chapter_number": 1,
            "verdict": "mixed",
            "accepted_candidate_ids": ["c1"],
            "repair_candidate_ids": ["c3"],
            "state_updates": [
                {"candidate_id": "c1", "state_path": "plot.main", "value": "advanced"}
            ],
            "should_block_archive": False,
        }
    )

    assert final.verdict == "needs_repair"
    assert _final_allows_state_write(final) is False


def test_candidate_adjudication_unknown_mixed_verdict_falls_back_to_ambiguous() -> None:
    decision = AdjudicationDecision.model_validate(
        {"candidate_id": "c1", "verdict": "mixed", "severity": "medium", "rationale": "折中"}
    )

    assert decision.verdict == "ambiguous"


def test_candidate_adjudication_normalizes_status_like_severity() -> None:
    decision = AdjudicationDecision.model_validate(
        {
            "candidate_id": "c1",
            "verdict": "reject",
            "severity": "error",
            "rationale": "候选缺少证据。",
        }
    )

    assert decision.severity == "medium"


def test_final_adjudication_normalizes_status_like_severity() -> None:
    final = FinalStateAdjudication.model_validate(
        {
            "chapter_number": 1,
            "verdict": "needs_repair",
            "severity": "blocker",
            "accepted_candidate_ids": [],
            "pending_candidate_ids": [],
            "repair_candidate_ids": ["c1"],
            "should_block_archive": True,
            "summary": "需要修复。",
        }
    )

    assert final.severity == "critical"


async def test_state_delta_step_normalizes_severity_before_model_validation() -> None:
    class FakeStateDeltaAdjudicationStep(StateDeltaAdjudicationStep):
        async def _call_with_retry(self, *_args, **_kwargs) -> dict:
            return {
                "candidate_id": "c1",
                "verdict": "reject",
                "severity": "error",
                "rationale": "空候选不能写入。",
            }

    step = FakeStateDeltaAdjudicationStep(
        None,
        None,
        settings=SimpleNamespace(temp_adjudicate_state_delta=0.1),
        trace=PipelineTrace(),
    )
    decision = await step.run(
        StateDeltaAdjudicationInput(
            chapter_number=1,
            candidate=CandidateStateDelta(
                candidate_id="c1",
                chapter_number=1,
                delta_type="event",
                summary="候选事件。",
            ),
            chapter_contract={},
            current_state={},
            evidence_window="",
        )
    )

    assert decision.verdict == "reject"
    assert decision.severity == "medium"


async def test_state_delta_step_binds_returned_verdict_to_requested_candidate() -> None:
    class FakeStateDeltaAdjudicationStep(StateDeltaAdjudicationStep):
        async def _call_with_retry(self, *_args, **_kwargs) -> dict:
            return {
                "candidate_id": "wrong_id",
                "verdict": "accept",
                "severity": "low",
                "rationale": "模型返回了错误 id。",
            }

    step = FakeStateDeltaAdjudicationStep(
        None,
        None,
        settings=SimpleNamespace(temp_adjudicate_state_delta=0.1),
        trace=PipelineTrace(),
    )
    decision = await step.run(
        StateDeltaAdjudicationInput(
            chapter_number=1,
            candidate=CandidateStateDelta(
                candidate_id="expected_id",
                chapter_number=1,
                delta_type="event",
                summary="候选事件。",
            ),
            chapter_contract={},
            current_state={},
            evidence_window="证据窗口",
        )
    )

    assert decision.candidate_id == "expected_id"
    assert decision.verdict == "accept"


def test_repair_adjudication_payload_only_includes_repair_targets() -> None:
    final = FinalStateAdjudication(
        chapter_number=3,
        verdict="needs_repair",
        repair_candidate_ids=["c_repair"],
        repair_issues=[
            {
                "candidate_id": "c_repair",
                "severity": "high",
                "instruction": "补清怀表归属。",
                "debug_notes": "不应依赖的大段诊断",
            }
        ],
        should_block_archive=True,
        summary="怀表状态冲突，需要正文修复。",
    )
    decisions = [
        AdjudicationDecision(
            candidate_id="c_ok",
            verdict="accept",
            rationale="已成立，但不应进入修复提示词。",
        ),
        AdjudicationDecision(
            candidate_id="c_repair",
            verdict="needs_repair",
            severity="high",
            rationale="证据与当前状态冲突。",
            repair_instruction="补清怀表归属。",
        ),
    ]
    candidates = [
        CandidateStateDelta(
            candidate_id="c_ok",
            chapter_number=3,
            delta_type="event",
            summary="普通事件。",
            extraction_notes="不应进入修复提示词。",
        ),
        CandidateStateDelta(
            candidate_id="c_repair",
            chapter_number=3,
            delta_type="item",
            summary="怀表归属变化不清。",
        ),
    ]

    payload = _scope_repair_adjudication_payload(final, decisions, candidates)

    assert payload["final_adjudication"]["repair_candidate_ids"] == ["c_repair"]
    assert [item["candidate_id"] for item in payload["repair_decisions"]] == ["c_repair"]
    assert [item["candidate_id"] for item in payload["repair_candidates"]] == ["c_repair"]
    assert "不应进入修复提示词" not in json.dumps(payload, ensure_ascii=False)


def test_repair_adjudication_payload_excludes_unselected_repair_instructions() -> None:
    final = FinalStateAdjudication(
        chapter_number=3,
        verdict="needs_repair",
        repair_candidate_ids=["c_repair"],
        should_block_archive=True,
    )
    decisions = [
        AdjudicationDecision(
            candidate_id="c_repair",
            verdict="needs_repair",
            repair_instruction="只修复这个候选。",
        ),
        AdjudicationDecision(
            candidate_id="c_rejected",
            verdict="reject",
            repair_instruction="这条拒绝候选不应污染修复提示词。",
        ),
    ]
    candidates = [
        CandidateStateDelta(candidate_id="c_repair", chapter_number=3, delta_type="event"),
        CandidateStateDelta(candidate_id="c_rejected", chapter_number=3, delta_type="event"),
    ]

    payload = _scope_repair_adjudication_payload(final, decisions, candidates)
    serialized = json.dumps(payload, ensure_ascii=False)

    assert [item["candidate_id"] for item in payload["repair_decisions"]] == ["c_repair"]
    assert [item["candidate_id"] for item in payload["repair_candidates"]] == ["c_repair"]
    assert "拒绝候选不应污染" not in serialized


def test_memory_repair_hints_are_scoped_as_non_authoritative() -> None:
    payload = _scope_memory_repair_hints(
        {
            "relevant_history": ["第1章：沈鹤卿曾经守着古董店暗格。"],
            "previous_chapter_events": ["第0章：不应存在的测试事件"] * 20,
            "authoritative_narrative_state": {"facts_by_path": {"secret": "不应从记忆注入"}},
        }
    )
    serialized = json.dumps(payload, ensure_ascii=False)

    assert payload["source_type"] == "memory_hint_non_authoritative"
    assert "不得把记忆摘要当作正文证据" in serialized
    assert "沈鹤卿曾经守着古董店暗格" in serialized
    assert "不应从记忆注入" not in serialized
    assert len(payload["previous_chapter_events"]) == 8


def _repair_input_for_text(
    chapter_text: str,
    *,
    evidence_quote: str = "",
) -> RepairAdjudicatedIssueInput:
    return RepairAdjudicatedIssueInput(
        chapter_number=3,
        chapter_text=chapter_text,
        final_adjudication=FinalStateAdjudication(
            chapter_number=3,
            verdict="accept",
            repair_candidate_ids=["c_repair"],
            should_block_archive=False,
        ),
        decisions=[
            AdjudicationDecision(
                candidate_id="c_repair",
                verdict="needs_repair",
                repair_instruction="补足可验证证据。",
                evidence_quotes=[evidence_quote] if evidence_quote else [],
            )
        ],
        candidates=[
            CandidateStateDelta(
                candidate_id="c_repair",
                chapter_number=3,
                delta_type="character_state",
                summary="林绾绾情绪变化。",
                evidence=[
                    EvidenceSpan(
                        quote=evidence_quote,
                        chapter_number=3,
                        found=bool(evidence_quote),
                    )
                ]
                if evidence_quote
                else [],
            )
        ],
        chapter_contract={},
        current_state={},
    )


def test_repair_adjudicated_issue_keeps_original_when_model_returns_existing_fragment() -> None:
    fragment = "宾客席另一侧，林绾绾已经哭花了眼妆。顾墨白默默递过去一包纸巾。"
    original = "\n\n".join(
        [
            "# 第3章",
            "前情铺垫" * 260,
            fragment,
            "后续仪式继续推进，所有宾客都在温柔的光里鼓掌。" * 220,
        ]
    )

    repaired, action = _coerce_adjudicated_repair_response(
        _repair_input_for_text(original),
        fragment,
    )

    assert repaired == original
    assert action == "partial_already_present"


def test_repair_adjudicated_issue_merges_partial_response_by_evidence_quote() -> None:
    quote = "林绾绾低头找纸巾，顾墨白站在旁边。"
    replacement = "林绾绾已经哭花了眼妆，顾墨白默默递过去一包纸巾。"
    original = "\n\n".join(
        [
            "# 第3章",
            "前情铺垫" * 260,
            quote,
            "后续仪式继续推进，所有宾客都在温柔的光里鼓掌。" * 220,
        ]
    )

    repaired, action = _coerce_adjudicated_repair_response(
        _repair_input_for_text(original, evidence_quote=quote),
        replacement,
    )

    assert action == "evidence_quote"
    assert replacement in repaired


def test_repair_adjudicated_issue_rejects_oversized_inline_quote_replacement() -> None:
    quote = "她想起那盘未完的棋，却不敢确认。"
    original = "\n\n".join(
        [
            "# 第3章",
            "前情铺垫" * 260,
            f"更鼓声落下时，{quote}她把白子压在指腹下。",
            "后续仪式继续推进，所有宾客都在温柔的光里鼓掌。" * 220,
        ]
    )
    replacement = "\n\n".join(
        [
            "她醒了。天已经亮了，窗外透进来微弱的光。",
            "船头坐着另一个人，低着头剥莲子，笛声断断续续。",
        ]
    )

    repaired, action = _coerce_adjudicated_repair_response(
        _repair_input_for_text(original, evidence_quote=quote),
        replacement,
    )

    assert repaired == original
    assert action == "partial_unmerged:multi_paragraph_replacement_for_inline_quote"


def test_repair_adjudicated_issue_context_uses_evidence_window() -> None:
    quote = "林绾绾低头找纸巾，顾墨白站在旁边。"
    original = "\n\n".join(
        [
            "前置铺垫" * 200,
            "无关旁支" * 200,
            quote,
            "需要保留的后续反应。",
            "远端结尾" * 200,
        ]
    )

    window = _repair_context_windows(
        _repair_input_for_text(original, evidence_quote=quote),
        radius=1,
        max_chars=1200,
    )

    assert quote in window
    assert "【窗口：原文段落 2-4】" in window
    assert "前置铺垫" not in window
    assert "远端结尾" not in window


async def test_run_narrative_state_adjudication_with_mock_adapter(
    tmp_path: Path,
    router,
    builder,
    runtime_settings,
) -> None:
    events: list[str] = []
    report_path = tmp_path / "reports" / "chapter_001_state_adjudication.json"

    report = await run_narrative_state_adjudication(
        router=router,
        builder=builder,
        settings=runtime_settings,
        trace=PipelineTrace(),
        project_root=tmp_path,
        chapter_number=1,
        chapter_text="林远继续追查时间裂缝。",
        chapter_contract={"chapter_number": 1, "required_events": ["追查时间裂缝"]},
        current_state={},
        known_characters=["林远"],
        on_step=lambda step, payload: events.append(step),
        report_path=report_path,
    )

    assert report.final_adjudication.verdict == "accept"
    assert report_path.exists()
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    assert set(payload["final_adjudication"]["accepted_candidate_ids"]) == {
        "mock_candidate_1",
        "contract_required_events_001",
    }
    assert "candidate_state_deltas" in events
    assert "contract_coverage_report" in events
    assert "final_state_adjudication" in events
    store = NarrativeStateStore(tmp_path)
    index_payload = json.loads(store.report_index_path.read_text(encoding="utf-8"))
    evidence_payload = json.loads(store.evidence_snapshot_path(1).read_text(encoding="utf-8"))
    assert index_payload["reports"][0]["candidate_count"] == 2
    assert index_payload["reports"][0]["contract_total_required_targets"] == 1
    assert index_payload["reports"][0]["contract_covered_count"] == 1
    assert "contract_coverage" in evidence_payload
    assert {item["candidate_id"] for item in evidence_payload["evidence"]} == {
        "contract_required_events_001",
        "mock_candidate_1",
    }


async def test_deferred_ledger_write_waits_for_archive_success(
    tmp_path: Path,
    router,
    builder,
    runtime_settings,
) -> None:
    report = await run_narrative_state_adjudication(
        router=router,
        builder=builder,
        settings=runtime_settings,
        trace=PipelineTrace(),
        project_root=tmp_path,
        chapter_number=1,
        chapter_text="林远继续追查时间裂缝。",
        chapter_contract={"chapter_number": 1, "required_events": ["追查时间裂缝"]},
        current_state={},
        known_characters=["林远"],
        on_step=lambda _step, _payload: None,
        report_path=tmp_path / "reports" / "chapter_001_state_adjudication.json",
        persist_ledger=False,
    )

    store = NarrativeStateStore(tmp_path)
    assert store.load_ledger_entries() == []

    entries = await persist_adjudicated_state_ledger(project_root=tmp_path, report=report)

    assert len(entries) == 2
    assert {entry.candidate_id for entry in entries} == {
        "mock_candidate_1",
        "contract_required_events_001",
    }
    assert len(store.load_ledger_entries()) == 2
