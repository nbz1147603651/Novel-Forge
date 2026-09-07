from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import ValidationError

from novel_forge.core.constants import TaskType
from novel_forge.core.schemas.audit import AuditIssueV2
from novel_forge.core.schemas.init_coherence import (
    CoherenceClaim,
    CoherenceClaimBatch,
    ConflictCandidate,
)
from novel_forge.core.schemas.outline import NarrativeBlueprint
from novel_forge.persistence.filesystem import FileSystemStorage
from novel_forge.persistence.models import ProjectLayout
from novel_forge.pipeline.long.services.context.source_artifacts import build_init_entity_catalog
from novel_forge.pipeline.long.services.init.init_coherence import (
    InitCoherenceError,
    RepairScope,
    RepairTarget,
    apply_scoped_init_patch,
    apply_targeted_init_patch,
    build_init_readiness_report,
    collect_repair_scopes,
    has_repair_scope,
    resolve_init_repair_targets,
)
from novel_forge.pipeline.long.services.init.init_coherence_v2 import (
    CLAIM_BATCH_CACHE_SCHEMA_VERSION,
    CLAIM_LEDGER_JSON,
    InitArtifactChunk,
    _adjudicate_candidates,
    _build_artifact_chunks,
    _claim_batch_cache_paths,
    _claims_for_retrieval,
    _extract_claims,
    _extract_stage_claims,
    _load_cached_claim_batch,
    _load_reusable_stage_claims,
    _merge_focus_chunk_reports,
    _normalize_candidate_adjudication_response,
    _normalize_claim_payload,
    _persist_claims,
    _retrieve_exact_candidates,
    _save_cached_claim_batch,
    _split_focus_chapters_for_recheck,
    build_outline_stream_claim_chunks,
    claim_cache_stats,
    finalize_streamed_init_coherence_claims,
    prefetch_init_coherence_claim_chunks,
    retrieve_init_conflict_candidates,
)
from novel_forge.pipeline.long.services.init.init_entity_references import entity_catalog_revision
from novel_forge.pipeline.long.services.init.init_repair_targets import (
    _build_init_target_repair_guidance,
)
from novel_forge.pipeline.long.services.init.init_service import (
    _apply_source_artifact_entity_alias_repair,
    _build_scoped_init_repair_payload,
    _init_coherence_allows_llm_followup_repair,
    _init_coherence_focus_chapters_after_repair,
    _init_coherence_max_repair_rounds,
    _init_coherence_repair_stop_decision,
    _local_story_fallbacks_enabled,
    _repair_init_artifact_payload,
    _try_local_blueprint_coherence_fallback,
)


def _claim_payload(**overrides: Any) -> dict[str, Any]:
    return {
        "claim_id": "claim-1",
        "artifact": "chapter_contracts",
        "source_path": "chapter_contracts[0]",
        "claim_text": "第31章公开关键证据。",
        "evidence": "第31章契约",
        "cognitive_subjects": ["主角A"],
        "cognitive_object": "关键证据",
        "cognitive_level": "confirmed",
        "action_level": "internal",
        "reader_awareness": "partial",
        "character_knowledge_coverage": {"主角A": "partial"},
        "cognitive_chapter": 31,
        "public_reveal_chapter": None,
        "foreshadow_chapters": [1, 2],
        **overrides,
    }


def _raw_claim(**overrides: Any) -> dict[str, Any]:
    payload = {
        "cognitive_subjects": [],
        "cognitive_object": "",
        "cognitive_level": "unaware",
        "action_level": "none",
        "reader_awareness": "unknown",
        "character_knowledge_coverage": {},
        "cognitive_chapter": None,
        "public_reveal_chapter": None,
        "foreshadow_chapters": [],
    }
    payload.update(overrides)
    return payload


def _claim_response(response: dict[str, object]) -> dict[str, object]:
    response = dict(response)
    response.setdefault("coverage_status", "complete")
    response.setdefault("unprocessed_source_refs", [])
    claims = response.get("claims")
    if isinstance(claims, list):
        response["claims"] = [
            _raw_claim(**claim) if isinstance(claim, dict) else claim for claim in claims
        ]
    return response


def _coherence_claim_model_validate(payload: dict[str, Any]) -> CoherenceClaim:
    return CoherenceClaim.model_validate(_claim_payload(**payload))


def test_coherence_claim_accepts_list_chapter_range() -> None:
    claim = _coherence_claim_model_validate(
        {
            "chapter_numbers": [31],
            "chapter_range": [31],
        }
    )

    assert claim.chapter_range is not None
    assert claim.chapter_range.start == 31
    assert claim.chapter_range.end == 31


def test_coherence_claim_accepts_multi_item_chapter_range() -> None:
    claim = _coherence_claim_model_validate(
        {
            "chapter_numbers": [31, 34],
            "chapter_range": [34, 31],
        }
    )

    assert claim.chapter_range is not None
    assert claim.chapter_range.start == 31
    assert claim.chapter_range.end == 34


def test_outline_patch_accepts_authorized_chapter_field() -> None:
    payload = {
        "chapters": [
            {"chapter_number": 3, "goal": "完成婚礼", "beats_summary": ["婚礼完成"]},
            {"chapter_number": 5, "goal": "再次求婚", "beats_summary": ["重复求婚"]},
        ]
    }
    repaired, applied = apply_scoped_init_patch(
        artifact="outline",
        payload=payload,
        patch_payload={
            "patches": [
                {
                    "op": "replace",
                    "path": "/chapters/1/goal",
                    "value": "婚后誓约兑现",
                    "issue_ids": ["issue_1"],
                    "rationale": "修复已婚后再次求婚的状态顺序冲突。",
                }
            ],
            "summary": "done",
        },
        scopes=[
            RepairScope(
                artifact="outline",
                chapters={5},
                fields={"goal"},
                operation="field_replace",
                issue_ids={"issue_1"},
            )
        ],
        max_ops=40,
    )

    assert repaired["chapters"][1]["goal"] == "婚后誓约兑现"
    assert applied == [
        {
            "op": "replace",
            "path": "/chapters/1/goal",
            "issue_ids": ["issue_1"],
            "rationale": "修复已婚后再次求婚的状态顺序冲突。",
        }
    ]


def test_outline_patch_accepts_expected_old_value_precondition() -> None:
    payload = {
        "chapters": [
            {"chapter_number": 5, "goal": "再次求婚", "beats_summary": ["重复求婚"]},
        ]
    }

    repaired, applied = apply_scoped_init_patch(
        artifact="outline",
        payload=payload,
        patch_payload={
            "patches": [
                {
                    "op": "replace",
                    "path": "/chapters/0/goal",
                    "expected_old_value": "再次求婚",
                    "value": "婚后誓约兑现",
                    "issue_ids": ["issue_1"],
                    "rationale": "以旧值前置条件保护局部 patch。",
                }
            ],
            "summary": "done",
        },
        scopes=[
            RepairScope(
                artifact="outline",
                chapters={5},
                fields={"goal"},
                operation="field_replace",
                issue_ids={"issue_1"},
            )
        ],
        max_ops=40,
    )

    assert repaired["chapters"][0]["goal"] == "婚后誓约兑现"
    assert applied[0]["precondition"]["expected_old_value_checked"] is True
    assert applied[0]["precondition"]["actual_old_hash"]


def test_resolve_init_repair_targets_uses_chapter_number_not_array_index() -> None:
    payload = {
        "chapters": [
            {"chapter_number": number, "title": f"第{number}章", "beats_summary": ["普通推进"]}
            for number in range(1, 66)
        ]
    }
    payload["chapters"].append(
        {
            "chapter_number": 66,
            "title": "暗潮重涌",
            "beats_summary": [
                "沈清漪核对宇文氏抄家清单",
                "商漪送来急信",
                "玄昱派暗卫营救",
                "玄昱独处时，幼态人格说「以后我不用再出来帮你扛了」",
            ],
        }
    )
    report = {
        "issues": [
            {
                "issue_id": "issue_001",
                "severity": "high",
                "type": "irreversible_event_conflict",
                "evidence": ["66章：幼态人格称「以后我不用再出来帮你挡了」"],
                "repair_scope": [
                    {
                        "artifact": "outline",
                        "chapters": [66],
                        "fields": ["beats_summary"],
                    }
                ],
            }
        ]
    }
    scopes = collect_repair_scopes(report, default_artifact="outline")

    targets = resolve_init_repair_targets("outline", payload, report, scopes)

    assert [target.target_id for target in targets] == ["outline:66:beats_summary:3"]
    assert targets[0].path == "/chapters/65/beats_summary/3"
    assert targets[0].match_reason == "evidence"


def test_resolve_init_repair_targets_returns_empty_when_location_is_ambiguous() -> None:
    payload = {
        "chapters": [
            {
                "chapter_number": 5,
                "goal": "调查线推进",
                "beats_summary": ["甲线推进", "乙线推进"],
            }
        ]
    }
    report = {
        "issues": [
            {
                "issue_id": "issue_001",
                "severity": "high",
                "type": "other",
                "evidence": ["证据文本不在当前字段中"],
                "repair_scope": [
                    {
                        "artifact": "outline",
                        "chapters": [5],
                        "fields": ["goal", "beats_summary"],
                    }
                ],
            }
        ]
    }
    scopes = collect_repair_scopes(report, default_artifact="outline")

    assert resolve_init_repair_targets("outline", payload, report, scopes) == []


def test_audit_issue_v2_requires_high_issue_repair_targets() -> None:
    with pytest.raises(ValidationError, match="high/critical issues require"):
        AuditIssueV2.model_validate(
            {
                "issue_id": "issue_missing_target",
                "issue_type": "temporal_conflict",
                "severity": "high",
                "blocking": True,
                "summary": "缺少可修复目标。",
                "description": "高危问题必须说明哪一侧可改，否则应转人工。",
                "evidence": [{"quote": "证据", "source": "unit"}],
                "repair_targets": [],
                "reference_targets": [],
                "repair_intent": {
                    "operation": "replace",
                    "target_policy": "single_target",
                    "rationale": "测试 schema 约束。",
                },
            }
        )


def test_resolve_init_repair_targets_prefers_v2_repair_role_over_reference_match() -> None:
    payload = {
        "key_turning_points": [
            {
                "chapter_number": 48,
                "event": "周婶具象动作被错写成第43章已经完成。",
                "description": "第48章仍把周婶具象动作当作未完成转折。",
            }
        ],
        "character_arcs": [
            {
                "character": "周婶",
                "chapter_number": 43,
                "arc": "周婶具象动作在第43章已经完成，后续只应保留影响。",
            }
        ],
    }
    report = {
        "schema_version": "audit_v2",
        "dimension": "init_coherence",
        "verdict": "needs_repair",
        "score": 5.0,
        "summary": "周婶动作章节错位。",
        "metadata": {},
        "source_refs": [],
        "repair_scope": [],
        "preserve": [],
        "change_intent": "修正 key_turning_points 的章节动作状态。",
        "blocked": False,
        "issues": [
            {
                "issue_id": "issue_zhou_shen_48_43",
                "id": "issue_zhou_shen_48_43",
                "dimension": "init_coherence",
                "issue_type": "chapter_state_misalignment",
                "severity": "high",
                "blocking": True,
                "summary": "周婶具象动作第48/43章错位。",
                "description": "key_turning_points 把第43章已完成动作错放到第48章继续推进。",
                "evidence": [
                    {
                        "quote": "周婶具象动作",
                        "source": "key_turning_points vs character_arcs",
                    }
                ],
                "repair_targets": [
                    {
                        "target_format": "json_artifact",
                        "role": "repair",
                        "artifact": "blueprint",
                        "field": "key_turning_points",
                        "chapter_number": 48,
                        "quote": "周婶具象动作",
                        "confidence": 0.9,
                    }
                ],
                "reference_targets": [
                    {
                        "target_format": "json_artifact",
                        "role": "reference",
                        "artifact": "blueprint",
                        "field": "character_arcs",
                        "chapter_number": 43,
                        "quote": "周婶具象动作",
                        "confidence": 0.9,
                    }
                ],
                "repair_intent": {
                    "operation": "replace",
                    "target_policy": "single_target",
                    "rationale": "修 key_turning_points，保留 character_arcs 作为正确参照。",
                    "preserve": ["character_arcs"],
                    "allowed_strategies": ["json_patch", "llm_patch"],
                },
                "postconditions": [],
            }
        ],
    }

    scopes = collect_repair_scopes(report, default_artifact="blueprint")
    targets = resolve_init_repair_targets("blueprint", payload, report, scopes)

    assert len(targets) == 1
    assert targets[0].field == "key_turning_points"
    assert targets[0].path == "/key_turning_points/0/description"
    assert targets[0].current_value == "第48章仍把周婶具象动作当作未完成转折。"
    assert "character_arcs" not in targets[0].path


def test_resolve_init_repair_targets_does_not_fallback_guess_for_v2_manual_target() -> None:
    payload = {"chapters": [{"chapter_number": 5, "goal": "再次求婚"}]}
    report = {
        "schema_version": "audit_v2",
        "dimension": "init_coherence",
        "verdict": "needs_repair",
        "score": 5.0,
        "summary": "需要人工裁决。",
        "metadata": {},
        "source_refs": [],
        "repair_scope": [{"artifact": "outline", "chapters": [5], "fields": ["goal"]}],
        "preserve": [],
        "change_intent": "旧兼容字段不应覆盖 v2 manual_only。",
        "blocked": True,
        "issues": [
            {
                "issue_id": "issue_manual",
                "issue_type": "ambiguous_target",
                "severity": "high",
                "blocking": True,
                "summary": "定位歧义。",
                "description": "审查阶段已判定不能安全自动定位。",
                "evidence": [{"quote": "再次求婚", "source": "outline"}],
                "repair_targets": [
                    {
                        "target_format": "manual_only",
                        "role": "repair",
                        "manual_review_reason": "同一证据跨多个候选，需人工裁决。",
                    }
                ],
                "reference_targets": [],
                "repair_intent": {
                    "operation": "manual_review",
                    "target_policy": "manual_only",
                    "rationale": "定位不安全。",
                },
                "postconditions": [],
            }
        ],
    }

    scopes = collect_repair_scopes(report, default_artifact="outline")

    assert resolve_init_repair_targets("outline", payload, report, scopes) == []


def test_resolve_init_repair_targets_finds_nested_contract_entity_leaves() -> None:
    payload = {
        "chapter_contracts": [
            {
                "chapter_number": 18,
                "knowledge_ops": [
                    {
                        "cognitive_subject": "朝堂众臣",
                        "description": "宇文铎递交铁矿供应缓表",
                    }
                ],
            },
            {
                "chapter_number": 56,
                "cast_plan": {
                    "required_character_ids": [
                        "玄昱",
                        "沈清漪",
                        "char_2dbb565b7d9",
                        "商漪",
                    ]
                },
            },
        ]
    }
    report = {
        "issues": [
            {
                "id": "source_artifacts_unresolved_entity_18_0",
                "severity": "critical",
                "type": "unresolved_contract_entity",
                "evidence": "朝堂众臣",
                "repair_scope": [
                    {"artifact": "chapter_contracts", "chapters": [18], "fields": ["knowledge_ops"]}
                ],
            },
            {
                "id": "source_artifacts_unresolved_entity_56_1",
                "severity": "critical",
                "type": "unresolved_contract_entity",
                "evidence": "char_2dbb565b7d9",
                "repair_scope": [
                    {"artifact": "chapter_contracts", "chapters": [56], "fields": ["cast_plan"]}
                ],
            },
        ]
    }
    scopes = collect_repair_scopes(report, default_artifact="chapter_contracts")

    targets = resolve_init_repair_targets("chapter_contracts", payload, report, scopes)

    assert [(target.target_id, target.path) for target in targets] == [
        (
            "chapter_contracts:18:knowledge_ops:0:cognitive_subject",
            "/chapter_contracts/0/knowledge_ops/0/cognitive_subject",
        ),
        (
            "chapter_contracts:56:cast_plan:required_character_ids:2",
            "/chapter_contracts/1/cast_plan/required_character_ids/2",
        ),
    ]


def test_resolve_init_repair_targets_prioritizes_short_exact_entity_leaf() -> None:
    payload = {
        "chapter_contracts": [
            {
                "chapter_number": 49,
                "entry_state_requirements": ["团队面临是否进入深层梦境的抉择"],
                "knowledge_ops": [
                    {"character": "老周", "knower": "沈岸"},
                    {"character": "团队", "knower": "陈半仙"},
                ],
                "cognitive_constraints": [
                    {"claim_text": "江野在终局前真正融入团队", "cognitive_subjects": ["江野"]}
                ],
            }
        ]
    }
    report = {
        "issues": [
            {
                "id": "source_artifacts_unresolved_entity_49_0",
                "type": "unresolved_contract_entity",
                "severity": "critical",
                "evidence": "团队",
                "repair_scope": [
                    {
                        "artifact": "chapter_contracts",
                        "chapters": [49],
                        "fields": [
                            "cognitive_constraints",
                            "entry_state_requirements",
                            "knowledge_ops",
                        ],
                    }
                ],
            }
        ],
        "change_intent": "修复未登记引用，保留已登记实体与章节事件。",
    }
    scopes = collect_repair_scopes(report, default_artifact="chapter_contracts")

    targets = resolve_init_repair_targets("chapter_contracts", payload, report, scopes)

    assert targets[0].target_id == "chapter_contracts:49:knowledge_ops:1:character"
    assert targets[0].current_value == "团队"
    assert all(target.current_value != "老周" for target in targets)


def test_apply_targeted_init_patch_uses_server_side_old_hash() -> None:
    payload = {"chapters": [{"chapter_number": 5, "goal": "再次求婚"}]}
    report = {
        "issues": [
            {
                "issue_id": "issue_1",
                "severity": "high",
                "evidence": ["5章：再次求婚"],
                "repair_scope": [{"artifact": "outline", "chapters": [5], "fields": ["goal"]}],
            }
        ]
    }
    scopes = collect_repair_scopes(report, default_artifact="outline")
    targets = resolve_init_repair_targets("outline", payload, report, scopes)

    repaired, applied = apply_targeted_init_patch(
        artifact="outline",
        payload=payload,
        patch_payload={
            "patches": [
                {
                    "target_id": targets[0].target_id,
                    "value": "婚后誓约兑现",
                    "issue_ids": ["issue_1"],
                    "rationale": "改掉重复求婚。",
                }
            ],
            "summary": "done",
        },
        target_map={target.target_id: target for target in targets},
        scopes=scopes,
        max_ops=40,
    )

    assert repaired["chapters"][0]["goal"] == "婚后誓约兑现"
    assert applied[0]["target_id"] == "outline:5:goal"
    assert applied[0]["precondition"]["expected_old_value_checked"] is False
    assert applied[0]["precondition"]["expected_old_hash"] == targets[0].current_hash


def test_apply_targeted_init_patch_rejects_stale_hash_in_lenient_mode() -> None:
    payload = {"chapters": [{"chapter_number": 5, "goal": "再次求婚"}]}
    report = {
        "issues": [
            {
                "issue_id": "issue_1",
                "severity": "high",
                "evidence": ["5章：再次求婚"],
                "repair_scope": [{"artifact": "outline", "chapters": [5], "fields": ["goal"]}],
            }
        ]
    }
    scopes = collect_repair_scopes(report, default_artifact="outline")
    targets = resolve_init_repair_targets("outline", payload, report, scopes)
    changed_payload = {"chapters": [{"chapter_number": 5, "goal": "已经改过"}]}
    skipped: list[dict[str, object]] = []

    repaired, applied = apply_targeted_init_patch(
        artifact="outline",
        payload=changed_payload,
        patch_payload={
            "patches": [{"target_id": targets[0].target_id, "value": "婚后誓约兑现"}],
            "summary": "stale",
        },
        target_map={target.target_id: target for target in targets},
        scopes=scopes,
        max_ops=40,
        strict=False,
        skipped=skipped,
    )

    assert repaired == changed_payload
    assert applied == []
    assert "哈希不匹配" in str(skipped[0]["reason"])


def test_apply_targeted_init_patch_leniently_defers_overflow_ops() -> None:
    payload = {
        "chapters": [
            {"chapter_number": chapter, "goal": f"重复目标 {chapter}"} for chapter in range(1, 48)
        ]
    }
    scope = RepairScope(
        artifact="outline",
        chapters=set(range(1, 48)),
        fields={"goal"},
        operation="field_replace",
        issue_ids={"issue_many"},
    )
    targets = [
        RepairTarget(
            artifact="outline",
            target_id=f"outline:{chapter}:goal",
            path=f"/chapters/{chapter - 1}/goal",
            chapter_number=chapter,
            field="goal",
            current_value=f"重复目标 {chapter}",
            current_hash=hashlib.sha256(
                json.dumps(
                    f"重复目标 {chapter}",
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                    default=str,
                ).encode("utf-8")
            ).hexdigest(),
            issue_ids=("issue_many",),
            match_reason="evidence",
        )
        for chapter in range(1, 48)
    ]
    skipped: list[dict[str, object]] = []

    repaired, applied = apply_targeted_init_patch(
        artifact="outline",
        payload=payload,
        patch_payload={
            "patches": [
                {"target_id": target.target_id, "value": f"修复目标 {index}"}
                for index, target in enumerate(targets, 1)
            ],
            "summary": "overflow",
        },
        target_map={target.target_id: target for target in targets},
        scopes=[scope],
        max_ops=40,
        strict=False,
        skipped=skipped,
    )

    assert len(applied) == 40
    assert len(skipped) == 7
    assert repaired["chapters"][0]["goal"] == "修复目标 1"
    assert repaired["chapters"][39]["goal"] == "修复目标 40"
    assert repaired["chapters"][40]["goal"] == "重复目标 41"
    assert "超过本轮上限 40" in str(skipped[0]["reason"])


def test_scoped_patch_rejects_path_that_does_not_contain_issue_evidence() -> None:
    payload = {
        "chapter_contracts": [
            {
                "chapter_number": 18,
                "knowledge_ops": [
                    {
                        "cognitive_subject": "朝堂众臣",
                        "description": "宇文铎递交铁矿供应缓表",
                    }
                ],
            }
        ]
    }
    skipped: list[dict[str, object]] = []

    repaired, applied = apply_scoped_init_patch(
        artifact="chapter_contracts",
        payload=payload,
        patch_payload={
            "patches": [
                {
                    "op": "replace",
                    "path": "/chapter_contracts/0/knowledge_ops/0/description",
                    "value": "宇文铎递交铁矿供应缓表，朝堂记录其后果",
                    "issue_ids": ["issue_1"],
                }
            ],
            "summary": "wrong leaf",
        },
        scopes=[
            RepairScope(
                artifact="chapter_contracts",
                chapters={18},
                fields={"knowledge_ops"},
                operation="field_replace",
                issue_ids={"issue_1"},
            )
        ],
        max_ops=40,
        strict=False,
        skipped=skipped,
        issue_evidence_by_id={"issue_1": ["朝堂众臣"]},
    )

    assert repaired == payload
    assert applied == []
    assert "未包含 issue evidence" in str(skipped[0]["reason"])


def test_scoped_patch_rejects_list_replacement_that_changes_unrelated_entities() -> None:
    payload = {
        "chapter_contracts": [
            {
                "chapter_number": 56,
                "cast_plan": {
                    "required_character_ids": [
                        "玄昱",
                        "沈清漪",
                        "char_2dbb565b7d9",
                        "商漪",
                    ]
                },
            }
        ]
    }
    skipped: list[dict[str, object]] = []

    repaired, applied = apply_scoped_init_patch(
        artifact="chapter_contracts",
        payload=payload,
        patch_payload={
            "patches": [
                {
                    "op": "replace",
                    "path": "/chapter_contracts/0/cast_plan/required_character_ids",
                    "value": ["玄孋", "沈清苒", "公孙烈", "商苒"],
                    "issue_ids": ["issue_1"],
                }
            ],
            "summary": "overbroad list",
        },
        scopes=[
            RepairScope(
                artifact="chapter_contracts",
                chapters={56},
                fields={"cast_plan"},
                operation="field_replace",
                issue_ids={"issue_1"},
            )
        ],
        max_ops=40,
        strict=False,
        skipped=skipped,
        issue_evidence_by_id={"issue_1": ["char_2dbb565b7d9"]},
    )

    assert repaired == payload
    assert applied == []
    assert "未命中 issue evidence" in str(skipped[0]["reason"])


def test_blueprint_patch_accepts_chapter_scoped_phase_path() -> None:
    payload = {
        "narrative_phases": [
            {
                "phase_name": "引入期",
                "chapter_start": 1,
                "chapter_end": 13,
                "description": "鹿鸣初入小镇，风声成为观察入口。",
            },
            {
                "phase_name": "转折期",
                "chapter_start": 14,
                "chapter_end": 26,
                "description": "草图交付被写成不可逆完成，后续缺少承接空间。",
            },
        ]
    }

    repaired, applied = apply_scoped_init_patch(
        artifact="blueprint",
        payload=payload,
        patch_payload={
            "patches": [
                {
                    "op": "replace",
                    "path": "/narrative_phases/1/description",
                    "value": "草图交付只形成阶段性转折，后续仍保留承接与复盘空间。",
                    "issue_ids": ["issue_phase"],
                }
            ],
            "summary": "phase scoped",
        },
        scopes=[
            RepairScope(
                artifact="blueprint",
                chapters={14},
                fields={"narrative_phases"},
                operation="field_replace",
                issue_ids={"issue_phase"},
            )
        ],
        max_ops=40,
        strict=False,
    )

    assert repaired["narrative_phases"][1]["description"].startswith("草图交付只形成")
    assert applied[0]["path"] == "/narrative_phases/1/description"


def test_blueprint_patch_rejects_phase_outside_scope_chapters() -> None:
    payload = {
        "narrative_phases": [
            {
                "phase_name": "引入期",
                "chapter_start": 1,
                "chapter_end": 13,
                "description": "鹿鸣初入小镇，风声成为观察入口。",
            },
            {
                "phase_name": "转折期",
                "chapter_start": 14,
                "chapter_end": 26,
                "description": "草图交付被写成不可逆完成，后续缺少承接空间。",
            },
        ]
    }
    skipped: list[dict[str, object]] = []

    repaired, applied = apply_scoped_init_patch(
        artifact="blueprint",
        payload=payload,
        patch_payload={
            "patches": [
                {
                    "op": "replace",
                    "path": "/narrative_phases/0/description",
                    "value": "误改第 1-13 章阶段。",
                    "issue_ids": ["issue_phase"],
                }
            ],
            "summary": "wrong phase",
        },
        scopes=[
            RepairScope(
                artifact="blueprint",
                chapters={14},
                fields={"narrative_phases"},
                operation="field_replace",
                issue_ids={"issue_phase"},
            )
        ],
        max_ops=40,
        strict=False,
        skipped=skipped,
    )

    assert repaired == payload
    assert applied == []
    assert "蓝图章节范围" in str(skipped[0]["reason"])


def test_resolve_blueprint_targets_filters_by_scope_chapters() -> None:
    payload = {
        "narrative_phases": [
            {
                "phase_name": "引入期",
                "chapter_start": 1,
                "chapter_end": 13,
                "description": "最后一次听风被误放在前期。",
            },
            {
                "phase_name": "转折期",
                "chapter_start": 14,
                "chapter_end": 26,
                "description": "第14章最后一次听风被写成不可逆完成。",
            },
        ]
    }
    scopes = [
        RepairScope(
            artifact="blueprint",
            chapters={14},
            fields={"narrative_phases"},
            operation="field_replace",
            issue_ids={"issue_phase"},
        )
    ]

    targets = resolve_init_repair_targets(
        "blueprint",
        payload,
        {
            "issues": [
                {
                    "id": "issue_phase",
                    "description": "第14章最后一次听风不应是不可逆完成。",
                    "repair_scope": [
                        {
                            "artifact": "blueprint",
                            "chapters": [14],
                            "fields": ["narrative_phases"],
                        }
                    ],
                }
            ]
        },
        scopes,
    )

    assert [target.path for target in targets] == ["/narrative_phases/1/description"]


def test_source_artifact_entity_alias_repair_defers_collective_and_near_id() -> None:
    chapter_contracts = {
        "chapter_contracts": [
            {
                "chapter_number": 18,
                "knowledge_ops": [{"cognitive_subject": "朝堂众臣"}],
            },
            {
                "chapter_number": 56,
                "cast_plan": {
                    "required_character_ids": [
                        "玄昱",
                        "沈清漪",
                        "char_2dbb565b7d9",
                        "商漪",
                    ]
                },
            },
        ]
    }
    entity_catalog = build_init_entity_catalog(
        {
            "entities": [
                {"entity_id": "conc_court", "name": "朝堂", "entity_type": "concept"},
                {"entity_id": "char_xuanyu", "name": "玄昱", "entity_type": "character"},
                {"entity_id": "char_qingyi", "name": "沈清漪", "entity_type": "character"},
                {"entity_id": "char_2bdbb565b7d9", "name": "公孙烈", "entity_type": "character"},
                {"entity_id": "char_shangyi", "name": "商漪", "entity_type": "character"},
            ]
        },
        {"characters": []},
    )
    report = {
        "issues": [
            {
                "id": "source_artifacts_unresolved_entity_18_0",
                "type": "unresolved_contract_entity",
                "unresolved_entity": "朝堂众臣",
                "repair_scope": [
                    {"artifact": "chapter_contracts", "chapters": [18], "fields": ["knowledge_ops"]}
                ],
            },
            {
                "id": "source_artifacts_unresolved_entity_56_1",
                "type": "unresolved_contract_entity",
                "unresolved_entity": "char_2dbb565b7d9",
                "repair_scope": [
                    {"artifact": "chapter_contracts", "chapters": [56], "fields": ["cast_plan"]}
                ],
            },
        ]
    }

    result = _apply_source_artifact_entity_alias_repair(
        chapter_contracts,
        report=report,
        entity_catalog=entity_catalog,
        round_index=1,
    )

    assert result.changed is False
    assert result.payload is chapter_contracts
    assert result.repair_record["patch_count"] == 0
    assert result.repair_record["skipped_patch_count"] == 2


def test_scoped_patch_skips_noop_replace_in_lenient_mode() -> None:
    skipped: list[dict[str, object]] = []

    repaired, applied = apply_scoped_init_patch(
        artifact="outline",
        payload={"chapters": [{"chapter_number": 5, "goal": "保持不变"}]},
        patch_payload={
            "patches": [
                {
                    "op": "replace",
                    "path": "/chapters/0/goal",
                    "value": "保持不变",
                    "issue_ids": ["issue_1"],
                }
            ],
            "summary": "noop",
        },
        scopes=[
            RepairScope(
                artifact="outline",
                chapters={5},
                fields={"goal"},
                operation="field_replace",
                issue_ids={"issue_1"},
            )
        ],
        max_ops=40,
        strict=False,
        skipped=skipped,
    )

    assert repaired["chapters"][0]["goal"] == "保持不变"
    assert applied == []
    assert "未改变原值" in str(skipped[0]["reason"])


def test_outline_patch_rejects_expected_old_value_mismatch() -> None:
    payload = {"chapters": [{"chapter_number": 5, "goal": "再次求婚"}]}

    with pytest.raises(InitCoherenceError, match="前置旧值不匹配"):
        apply_scoped_init_patch(
            artifact="outline",
            payload=payload,
            patch_payload={
                "patches": [
                    {
                        "op": "replace",
                        "path": "/chapters/0/goal",
                        "expected_old_value": "已经结婚",
                        "value": "婚后誓约兑现",
                    }
                ],
                "summary": "bad",
            },
            scopes=[
                RepairScope(
                    artifact="outline",
                    chapters={5},
                    fields={"goal"},
                    operation="field_replace",
                    issue_ids={"issue_1"},
                )
            ],
            max_ops=40,
        )


@pytest.mark.parametrize(
    ("patch", "message"),
    [
        ({"op": "remove", "path": "/chapters/1/goal"}, "只允许 replace"),
        ({"op": "replace", "path": "/chapters/0/goal", "value": "越界章节"}, "repair_scope"),
        ({"op": "replace", "path": "/chapters/1/title", "value": "未授权字段"}, "repair_scope"),
        ({"op": "replace", "path": "/chapters/1/chapter_number", "value": 6}, "chapter_number"),
        ({"op": "replace", "path": "/chapters/1", "value": {}}, "整章"),
    ],
)
def test_outline_patch_rejects_unsafe_paths(patch: dict[str, object], message: str) -> None:
    payload = {
        "chapters": [
            {"chapter_number": 3, "goal": "完成婚礼"},
            {"chapter_number": 5, "goal": "再次求婚", "title": "旧标题"},
        ]
    }

    with pytest.raises(InitCoherenceError, match=message):
        apply_scoped_init_patch(
            artifact="outline",
            payload=payload,
            patch_payload={"patches": [patch], "summary": "bad"},
            scopes=[
                RepairScope(
                    artifact="outline",
                    chapters={5},
                    fields={"goal"},
                    operation="field_replace",
                    issue_ids={"issue_1"},
                )
            ],
            max_ops=40,
        )


def test_blueprint_patch_requires_field_whitelist() -> None:
    payload = {"synopsis": "旧", "narrative_phases": []}

    with pytest.raises(InitCoherenceError, match="字段白名单"):
        apply_scoped_init_patch(
            artifact="blueprint",
            payload=payload,
            patch_payload={
                "patches": [{"op": "replace", "path": "/synopsis", "value": "新"}],
                "summary": "bad",
            },
            scopes=[
                RepairScope(
                    artifact="blueprint",
                    chapters=set(),
                    fields={"narrative_phases"},
                    operation="field_replace",
                    issue_ids={"issue_1"},
                )
            ],
            max_ops=40,
        )


def test_blueprint_patch_rejects_top_level_composite_replacement() -> None:
    payload = {
        "narrative_phases": [
            {
                "phase_name": "开局",
                "chapter_start": 1,
                "chapter_end": 5,
                "description": "旧阶段",
            }
        ]
    }

    with pytest.raises(InitCoherenceError, match="顶层复合字段"):
        apply_scoped_init_patch(
            artifact="blueprint",
            payload=payload,
            patch_payload={
                "patches": [
                    {
                        "op": "replace",
                        "path": "/narrative_phases",
                        "value": [
                            {
                                "phase_name": "新阶段",
                                "chapter_start": 1,
                                "chapter_end": 5,
                                "description": "整体替换",
                            }
                        ],
                    }
                ],
                "summary": "bad",
            },
            scopes=[
                RepairScope(
                    artifact="blueprint",
                    chapters={1, 2, 3, 4, 5},
                    fields={"narrative_phases"},
                    operation="field_replace",
                    issue_ids={"issue_1"},
                )
            ],
            max_ops=40,
        )


def test_blueprint_patch_skips_top_level_long_text_shrink_in_lenient_mode() -> None:
    old_synopsis = "旧主线推进与人物关系变化。" * 12
    skipped: list[dict[str, object]] = []

    repaired, applied = apply_scoped_init_patch(
        artifact="blueprint",
        payload={"synopsis": old_synopsis},
        patch_payload={
            "patches": [
                {
                    "op": "replace",
                    "path": "/synopsis",
                    "expected_old_value": old_synopsis,
                    "value": "短句修复。",
                    "issue_ids": ["issue_1"],
                }
            ],
            "summary": "bad",
        },
        scopes=[
            RepairScope(
                artifact="blueprint",
                chapters=set(),
                fields={"synopsis"},
                operation="field_replace",
                issue_ids={"issue_1"},
            )
        ],
        max_ops=40,
        strict=False,
        skipped=skipped,
    )

    assert repaired["synopsis"] == old_synopsis
    assert applied == []
    assert "短文本覆盖顶层长文本字段" in str(skipped[0]["reason"])


def test_blueprint_patch_lenient_mode_normalizes_window_indexes_and_skips_bad_ops() -> None:
    payload = {
        "key_turning_points": [
            {"state_after": "旧0"},
            {"state_after": "旧1"},
            {"state_after": "旧2"},
            {"state_after": "旧3"},
            {"state_after": "旧4"},
            {"state_after": "旧5"},
            {"state_after": "旧6"},
            {"state_after": "旧7"},
        ],
        "narrative_phases": [{"key_events": ["a", "b", "c", "d"]}],
        "synopsis": "旧概要",
    }
    skipped: list[dict[str, object]] = []

    repaired, applied = apply_scoped_init_patch(
        artifact="blueprint",
        payload=payload,
        patch_payload={
            "patches": [
                {
                    "op": "replace",
                    "path": "/key_turning_points/0:8[5]/state_after",
                    "value": "新5",
                    "issue_ids": ["issue_1"],
                },
                {
                    "op": "edit",
                    "path": "/narrative_phases/0/key_events/3",
                    "value": "新事件",
                    "issue_ids": ["issue_2"],
                },
                {
                    "op": "edit",
                    "path": "/key_events",
                    "issue_ids": ["issue_3"],
                },
            ],
            "summary": "done",
        },
        scopes=[
            RepairScope(
                artifact="blueprint",
                chapters={35},
                fields={"key_turning_points/0:8[5]", "narrative_phases/0/key_events/3"},
                operation="field_replace",
                issue_ids={"issue_1", "issue_2"},
            )
        ],
        max_ops=40,
        strict=False,
        skipped=skipped,
    )

    assert repaired["key_turning_points"][5]["state_after"] == "新5"
    assert repaired["narrative_phases"][0]["key_events"][3] == "新事件"
    assert [item["path"] for item in applied] == [
        "/key_turning_points/5/state_after",
        "/narrative_phases/0/key_events/3",
    ]
    assert len(skipped) == 1
    assert skipped[0]["path"] == "/key_events"


def test_claim_normalization_forces_chunk_artifact_provenance() -> None:
    chunk = InitArtifactChunk(
        artifact="blueprint",
        chunk_id="blueprint_volumes_1_1",
        source_path="/volumes/0:1",
        source_field="volumes",
        chapter_numbers=[41, 42],
        payload={"field": "volumes", "items": []},
        extraction_mode="shard",
        evidence_refs=["blueprint:/volumes/0"],
    )

    claim = _normalize_claim_payload(
        {
            "claim_id": "bad_artifact_001",
            "artifact": "outline",
            "source_path": "/volumes/0/milestone_targets",
            "source_field": "milestone_targets",
            "claim_text": "第42章完成卷内反转。",
            "evidence": "第42章完成卷内反转。",
        },
        chunk=chunk,
        claim_index=1,
    )

    assert claim["artifact"] == "blueprint"
    assert claim["source_path"] == "/volumes/0/milestone_targets"
    assert claim["metadata"]["raw_artifact"] == "outline"
    assert claim["metadata"]["artifact_corrected_from_chunk"] is True


def test_claim_normalization_isolates_source_path_to_current_chunk() -> None:
    chunk = InitArtifactChunk(
        artifact="blueprint",
        chunk_id="blueprint_volumes_1_3",
        source_path="/volumes/0:3",
        source_field="volumes",
        chapter_numbers=[1, 20],
        payload={"field": "volumes", "items": []},
    )

    claim = _normalize_claim_payload(
        {
            "claim_id": "claim_1",
            "artifact": "outline",
            "source_path": "/synopsis",
            "source_field": "goal",
            "claim_text": "第一卷完成关系推进。",
            "evidence": "关系推进",
        },
        chunk=chunk,
        claim_index=1,
    )

    assert claim["claim_id"] == "blueprint_volumes_1_3_claim_1"
    assert claim["artifact"] == "blueprint"
    assert claim["source_path"] == "/volumes/0:3"
    assert claim["source_field"] == "volumes"
    assert claim["metadata"]["raw_source_path"] == "/synopsis"
    assert claim["metadata"]["source_path_corrected_from_chunk"] is True
    assert claim["metadata"]["raw_source_field"] == "goal"
    assert claim["metadata"]["source_field_corrected_from_chunk"] is True


def test_claim_normalization_preserves_raw_entity_fields_for_adjudication() -> None:
    chunk = InitArtifactChunk(
        artifact="chapter_contracts",
        chunk_id="chapter_contracts_1_1",
        source_path="/chapter_contracts/0",
        source_field="chapter_contracts",
        chapter_numbers=[1],
        payload={"field": "chapter_contracts", "items": []},
    )
    entity_catalog = {
        "alias_to_entity": {
            "清漪": {
                "entity_id": "char_qingyi",
                "canonical_name": "沈清漪",
                "entity_type": "character",
            },
            "沈清漪": {
                "entity_id": "char_qingyi",
                "canonical_name": "沈清漪",
                "entity_type": "character",
            },
        },
        "allowed_entities": [
            {
                "entity_id": "char_qingyi",
                "canonical_name": "沈清漪",
                "entity_type": "character",
                "aliases": ["清漪"],
            }
        ],
    }

    claim = _normalize_claim_payload(
        {
            "claim_id": "claim_1",
            "artifact": "chapter_contracts",
            "source_path": "/chapter_contracts/0",
            "claim_text": "清漪确认账册。",
            "evidence": "清漪确认账册。",
            "cognitive_subjects": ["清漪", "文 on轨", "沈清漪漪"],
            "character_knowledge_coverage": {
                "清漪": "full",
                "文 on轨": "unknown",
                "沈清漪漪": "partial",
            },
        },
        chunk=chunk,
        claim_index=1,
        entity_catalog=entity_catalog,
    )

    assert claim["cognitive_subjects"] == ["清漪", "文 on轨", "沈清漪漪"]
    assert claim["character_knowledge_coverage"] == {
        "清漪": "full",
        "文 on轨": "unknown",
        "沈清漪漪": "partial",
    }


def test_local_blueprint_fallback_softens_duplicate_irreversible_turning_point(
    tmp_path,
) -> None:
    storage = FileSystemStorage(tmp_path)
    layout = ProjectLayout(tmp_path / "project")
    events: list[tuple[str, dict[str, object]]] = []
    ctx = SimpleNamespace(
        settings=SimpleNamespace(init_coherence_block_min_severity="high"),
        storage=storage,
        layout=layout,
        on_step=lambda step, data: events.append((step, data)),
    )
    blueprint = NarrativeBlueprint.model_validate(
        {
            "key_turning_points": [
                {
                    "chapter_number": 80,
                    "description": "隐疾、科研瓶颈、信任壁垒全解，关系达知己共生。",
                }
            ],
            "volumes": [{"volume_number": 1, "start_chapter": 1, "end_chapter": 81}],
        }
    )
    report = {
        "verdict": "needs_repair",
        "issues": [
            {
                "id": "issue_1",
                "severity": "high",
                "description": "不可逆事件重复标记冲突，疑似首次完成重复计数。",
                "resolution": "调整为总结性复述。",
                "repair_scope": [
                    {
                        "artifact": "blueprint",
                        "chapters": [80],
                        "fields": ["key_turning_points"],
                    }
                ],
            }
        ],
    }
    repairs: list[dict[str, object]] = []

    repaired = _try_local_blueprint_coherence_fallback(
        ctx,
        blueprint=blueprint,
        report=report,
        round_index=2,
        repairs=repairs,
    )

    assert repaired is not None
    description = repaired.key_turning_points[0].description
    assert "不标记为新的首次揭示" in description
    assert repairs[0]["local_fallback"] is True
    assert events[0][0] == "repair_init_artifact_patch"


def test_init_readiness_blocks_remaining_high_issue() -> None:
    readiness = build_init_readiness_report(
        reports={
            "outline_inheritance": {
                "verdict": "needs_repair",
                "issues": [{"id": "issue_1", "severity": "high", "description": "重复求婚"}],
                "summary": "仍有 high 问题。",
                "claims_count": 12,
                "extracted_claims_count": 3,
                "active_claims_count": 12,
                "claim_ledger_path": "memory/init_coherence_claim_ledger.json",
            }
        },
        repairs=[],
        min_severity="high",
        required=True,
    )

    assert readiness["allowed"] is False
    assert readiness["stages"]["outline_inheritance"]["blocking_issue_count"] == 1
    assert readiness["active_claims_count"] == 12
    assert readiness["extracted_claims_count"] == 3
    assert readiness["claim_ledger_path"] == "memory/init_coherence_claim_ledger.json"
    assert readiness["recovery_actions"][0]["kind"] == "ai_repair"
    assert readiness["recovery_actions"][1]["kind"] == "manual_repair"
    assert readiness["recovery_actions"][1]["artifacts"] == ["outline"]


def test_init_readiness_summarizes_repair_effectiveness_from_issue_ids() -> None:
    readiness = build_init_readiness_report(
        reports={
            "outline_inheritance": {
                "verdict": "needs_repair",
                "issues": [{"id": "issue_b", "severity": "high", "description": "仍未修复"}],
                "summary": "仍有 high 问题。",
            }
        },
        repairs=[
            {
                "artifact": "outline",
                "round": 1,
                "source_issue_ids": ["issue_a", "issue_b"],
                "patches": [
                    {"path": "/chapters/0/goal", "issue_ids": ["issue_a"]},
                    {
                        "path": "/chapters/1/goal",
                        "issue_ids": ["issue_b"],
                        "precondition": {"expected_old_value_checked": True},
                    },
                ],
                "skipped_patch_count": 1,
            }
        ],
        min_severity="high",
        required=True,
    )

    effectiveness = readiness["repair_effectiveness"]
    assert effectiveness["repair_round_count"] == 1
    assert effectiveness["patch_count"] == 2
    assert effectiveness["skipped_patch_count"] == 1
    assert effectiveness["patch_precondition_count"] == 1
    assert effectiveness["resolved_issue_ids"] == ["issue_a"]
    assert effectiveness["remaining_touched_issue_ids"] == ["issue_b"]


def test_init_readiness_recovery_actions_include_blueprint_ai_options() -> None:
    readiness = build_init_readiness_report(
        reports={
            "blueprint_coherence": {
                "verdict": "needs_repair",
                "issues": [
                    {
                        "id": "issue_chapter",
                        "issue_type": "chapter_number_contradiction",
                        "severity": "high",
                        "description": "同一事件出现在第 6、9、14 章。",
                        "repair_scope": [
                            {
                                "artifact": "blueprint",
                                "chapters": [6, 9, 14],
                                "fields": ["character_arcs", "narrative_phases"],
                            }
                        ],
                    }
                ],
                "summary": "仍有 high 问题。",
            }
        },
        repairs=[],
        min_severity="high",
        required=True,
    )

    actions = readiness["recovery_actions"]
    assert actions[0]["artifacts"] == ["blueprint"]
    assert actions[0]["artifact_paths"] == ["plans/narrative_blueprint.json"]
    assert actions[0]["options"][0]["directions"][0]["id"].endswith(":unify_event_chapter")
    assert actions[1]["kind"] == "manual_repair"
    assert actions[2]["kind"] == "verify"


def test_init_readiness_does_not_mark_no_effect_source_ids_resolved() -> None:
    readiness = build_init_readiness_report(
        reports={},
        repairs=[
            {
                "artifact": "chapter_contracts",
                "round": 1,
                "status": "no_effect",
                "source_issue_ids": ["source_artifacts_unresolved_entity_1_0"],
                "patch_count": 0,
                "patches": [],
                "skipped_patches": [],
            }
        ],
        min_severity="high",
        required=True,
    )

    effectiveness = readiness["repair_effectiveness"]
    assert effectiveness["source_issue_ids"] == ["source_artifacts_unresolved_entity_1_0"]
    assert effectiveness["applied_issue_ids"] == []
    assert effectiveness["resolved_issue_ids"] == []


def test_init_coherence_max_repair_rounds_fallback_matches_settings_default() -> None:
    assert _init_coherence_max_repair_rounds(SimpleNamespace()) == 2
    assert (
        _init_coherence_max_repair_rounds(SimpleNamespace(init_coherence_max_repair_rounds=99)) == 3
    )
    assert (
        _init_coherence_max_repair_rounds(SimpleNamespace(init_coherence_max_repair_rounds=-1)) == 0
    )


def test_init_coherence_allows_one_llm_followup_for_new_issue_after_repair() -> None:
    settings = SimpleNamespace(init_coherence_block_min_severity="high")
    report = {
        "verdict": "needs_repair",
        "issues": [{"id": "issue_new", "severity": "high"}],
    }
    repairs = [
        {
            "artifact": "blueprint",
            "source_issue_ids": ["issue_old"],
            "patches": [{"issue_ids": ["issue_old"]}],
        }
    ]

    assert _init_coherence_allows_llm_followup_repair(
        settings,
        artifact="blueprint",
        report=report,
        repairs=repairs,
        repair_round=1,
        followup_used=False,
    )
    assert not _init_coherence_allows_llm_followup_repair(
        settings,
        artifact="blueprint",
        report=report,
        repairs=repairs,
        repair_round=1,
        followup_used=True,
    )


def test_init_coherence_repair_stop_decision_stops_same_blocking_issue() -> None:
    settings = SimpleNamespace(
        init_coherence_block_min_severity="high",
        init_coherence_stop_on_no_progress=True,
        init_coherence_max_stagnant_repair_rounds=1,
    )
    report = {
        "verdict": "needs_repair",
        "issues": [
            {
                "id": "issue_still_blocked",
                "severity": "high",
                "description": "第5章关系状态仍与第3章冲突。",
                "repair_scope": [{"artifact": "outline", "chapters": [5], "fields": ["goal"]}],
            }
        ],
    }

    stop, reason, issue_keys, stagnant_rounds = _init_coherence_repair_stop_decision(
        settings,
        report,
        previous_issue_keys=None,
        stagnant_rounds=0,
    )
    assert not stop
    assert reason == ""

    stop, reason, next_issue_keys, stagnant_rounds = _init_coherence_repair_stop_decision(
        settings,
        report,
        previous_issue_keys=issue_keys,
        stagnant_rounds=stagnant_rounds,
    )
    assert stop
    assert reason == "no_blocking_issue_reduction"
    assert next_issue_keys == issue_keys
    assert stagnant_rounds == 1


def test_init_coherence_repair_stop_decision_allows_new_followup_issue() -> None:
    settings = SimpleNamespace(
        init_coherence_block_min_severity="high",
        init_coherence_stop_on_no_progress=True,
        init_coherence_max_stagnant_repair_rounds=1,
    )
    old_report = {
        "verdict": "needs_repair",
        "issues": [{"id": "issue_old", "severity": "high", "description": "旧问题"}],
    }
    new_report = {
        "verdict": "needs_repair",
        "issues": [{"id": "issue_new", "severity": "high", "description": "新问题"}],
    }

    _, _, old_keys, stagnant_rounds = _init_coherence_repair_stop_decision(
        settings,
        old_report,
        previous_issue_keys=None,
        stagnant_rounds=0,
    )
    stop, reason, _, stagnant_rounds = _init_coherence_repair_stop_decision(
        settings,
        new_report,
        previous_issue_keys=old_keys,
        stagnant_rounds=stagnant_rounds,
    )

    assert not stop
    assert reason == ""
    assert stagnant_rounds == 0


def test_init_coherence_followup_does_not_retry_same_issue() -> None:
    settings = SimpleNamespace(init_coherence_block_min_severity="high")
    report = {
        "verdict": "needs_repair",
        "issues": [{"id": "issue_old", "severity": "high"}],
    }
    repairs = [{"artifact": "blueprint", "source_issue_ids": ["issue_old"]}]

    assert not _init_coherence_allows_llm_followup_repair(
        settings,
        artifact="blueprint",
        report=report,
        repairs=repairs,
        repair_round=1,
        followup_used=False,
    )


def test_init_target_repair_guidance_includes_previous_patch_history() -> None:
    target = RepairTarget(
        artifact="chapter_contracts",
        target_id="chapter_contracts.2.required_events.0",
        path="/chapter_contracts/1/required_events/0",
        chapter_number=2,
        field="required_events",
        current_value="玄昱阶段性确认密信来源。",
        current_hash="hash-new",
        issue_ids=("issue_a",),
        match_reason="evidence",
    )
    guidance = _build_init_target_repair_guidance(
        SimpleNamespace(init_coherence_max_repair_rounds=3),
        artifact="chapter_contracts",
        round_index=2,
        repairs=[
            {
                "artifact": "chapter_contracts",
                "source_issue_ids": ["issue_a"],
                "attempted_patches": [
                    {
                        "target_id": "chapter_contracts.2.required_events.0",
                        "issue_ids": ["issue_a"],
                        "value_preview": "玄昱确认密信来源。",
                        "rationale": "上一轮改写仍过强。",
                    }
                ],
                "patches": [
                    {
                        "target_id": "chapter_contracts.2.required_events.0",
                        "field": "required_events",
                        "chapter_number": 2,
                        "issue_ids": ["issue_a"],
                    }
                ],
                "skipped_patches": [
                    {
                        "target_id": "unknown",
                        "reason": "未知 repair target_id：unknown",
                    }
                ],
            }
        ],
        source_issue_ids=["issue_a"],
        targets=[target],
    )

    assert guidance is not None
    assert guidance["previous_attempted_patches"]
    assert guidance["previous_applied_patches"]
    assert guidance["previous_skipped_patches"]
    assert any("上一轮模型尝试" in item for item in guidance["diagnosis"])
    assert any("当前真实目标旧值" in item for item in guidance["diagnosis"])


async def test_repair_init_artifact_records_failed_attempt(tmp_path) -> None:
    storage = FileSystemStorage(tmp_path)
    layout = ProjectLayout(tmp_path / "project")
    layout.ensure_dirs()
    events: list[tuple[str, dict[str, object]]] = []

    async def failing_call_with_retry(*_args, **_kwargs) -> dict[str, object]:
        raise RuntimeError("repair model unavailable")

    ctx = SimpleNamespace(
        settings=SimpleNamespace(
            init_coherence_block_min_severity="high",
            init_coherence_patch_max_ops=40,
            temp_repair_init_artifact_patch=0.15,
        ),
        storage=storage,
        layout=layout,
        router=None,
        call_with_retry=failing_call_with_retry,
        on_step=lambda step, data: events.append((step, data)),
    )
    report = {
        "verdict": "needs_repair",
        "issues": [
            {
                "id": "issue_1",
                "severity": "high",
                "description": "第5章关系状态与前文不一致。",
                "repair_scope": [
                    {
                        "artifact": "outline",
                        "chapters": [5],
                        "fields": ["goal"],
                        "issue_ids": ["issue_1"],
                    }
                ],
            }
        ],
    }
    repairs: list[dict[str, object]] = []

    with pytest.raises(RuntimeError, match="repair model unavailable"):
        await _repair_init_artifact_payload(
            ctx,
            artifact="outline",
            payload={"chapters": [{"chapter_number": 5, "goal": "错误状态"}]},
            report=report,
            round_index=1,
            repairs=repairs,
        )

    assert repairs[0]["status"] == "failed"
    assert repairs[0]["source_issue_ids"]
    assert repairs[0]["error_type"] == "RuntimeError"
    assert any(step == "repair_init_artifact_patch_failed" for step, _ in events)
    persisted = storage.load_json(layout.reports_dir / "init_artifact_repair.json")
    assert persisted["repairs"][0]["error"] == "repair model unavailable"


async def test_repair_init_artifact_batches_large_target_sets(tmp_path) -> None:
    storage = FileSystemStorage(tmp_path)
    layout = ProjectLayout(tmp_path / "project")
    layout.ensure_dirs()
    seen_target_counts: list[int] = []

    async def repair_call_with_retry(_task_type, context, **_kwargs) -> dict[str, object]:
        repair_targets = list(context.get("repair_targets") or [])
        seen_target_counts.append(len(repair_targets))
        return {
            "patches": [
                {
                    "target_id": target["target_id"],
                    "value": "修复后的目标",
                    "issue_ids": ["issue_many"],
                    "rationale": "只修复本批次定位目标。",
                }
                for target in repair_targets
            ],
            "summary": "batched",
        }

    ctx = SimpleNamespace(
        settings=SimpleNamespace(
            init_coherence_block_min_severity="high",
            init_coherence_patch_max_ops=40,
            init_coherence_target_patch_batch_size=12,
            temp_repair_init_artifact_patch=0.15,
        ),
        storage=storage,
        layout=layout,
        router=None,
        call_with_retry=repair_call_with_retry,
        on_step=lambda _step, _data: None,
    )
    payload = {
        "chapters": [
            {"chapter_number": chapter, "goal": f"重复目标 {chapter}"} for chapter in range(1, 48)
        ]
    }
    report = {
        "verdict": "needs_repair",
        "summary": "多个章节目标重复。",
        "issues": [
            {
                "id": "issue_many",
                "severity": "high",
                "description": "多个章节使用重复目标。",
                "evidence": ["重复目标"],
                "repair_scope": [
                    {
                        "artifact": "outline",
                        "chapters": list(range(1, 48)),
                        "fields": ["goal"],
                        "issue_ids": ["issue_many"],
                    }
                ],
            }
        ],
    }

    repaired = await _repair_init_artifact_payload(
        ctx,
        artifact="outline",
        payload=payload,
        report=report,
        round_index=1,
        repairs=[],
        split_issues=False,
    )

    assert seen_target_counts == [12]
    assert repaired["chapters"][0]["goal"] == "修复后的目标"
    assert repaired["chapters"][11]["goal"] == "修复后的目标"
    assert repaired["chapters"][12]["goal"] == "重复目标 13"
    saved = storage.load_json(layout.reports_dir / "init_artifact_repair.json")
    record = saved["repairs"][0]
    assert record["target_count"] == 47
    assert record["located_count"] == 12
    assert record["deferred_target_count"] == 35


async def test_repair_init_artifact_excludes_nonblocking_issue_scopes(tmp_path) -> None:
    storage = FileSystemStorage(tmp_path)
    layout = ProjectLayout(tmp_path / "project")
    layout.ensure_dirs()
    seen_targets: list[dict[str, Any]] = []

    async def repair_call_with_retry(_task_type, context, **_kwargs) -> dict[str, object]:
        seen_targets.extend(context.get("repair_targets") or [])
        return {"patches": [], "summary": "no safe patch"}

    ctx = SimpleNamespace(
        settings=SimpleNamespace(
            init_coherence_block_min_severity="high",
            init_coherence_patch_max_ops=40,
            init_coherence_target_patch_batch_size=12,
            temp_repair_init_artifact_patch=0.15,
        ),
        storage=storage,
        layout=layout,
        router=None,
        call_with_retry=repair_call_with_retry,
        on_step=lambda _step, _data: None,
    )
    payload = {
        "chapter_contracts": [
            {"chapter_number": 4, "required_events": ["共用证据锚点"]},
            {"chapter_number": 5, "required_events": ["共用证据锚点"]},
        ]
    }
    report = {
        "verdict": "needs_repair",
        "summary": "only high blocks",
        "issues": [
            {
                "id": "blocking_ch5",
                "severity": "high",
                "description": "共用证据锚点",
                "repair_scope": [
                    {
                        "artifact": "chapter_contracts",
                        "chapters": [5],
                        "fields": ["required_events"],
                    }
                ],
            },
            {
                "id": "warning_ch4",
                "severity": "medium",
                "description": "共用证据锚点",
                "repair_scope": [
                    {
                        "artifact": "chapter_contracts",
                        "chapters": [4],
                        "fields": ["required_events"],
                    }
                ],
            },
        ],
    }

    await _repair_init_artifact_payload(
        ctx,
        artifact="chapter_contracts",
        payload=payload,
        report=report,
        round_index=1,
        repairs=[],
        split_issues=False,
    )

    assert seen_targets
    assert {target["chapter_number"] for target in seen_targets} == {5}


def test_scoped_init_repair_payload_preserves_outline_indexes() -> None:
    payload = {
        "title": "测试",
        "chapters": [
            {
                "chapter_number": number,
                "title": f"第{number}章",
                "goal": f"目标{number}",
                "notes": "不应暴露的长备注" * 20,
            }
            for number in range(1, 121)
        ],
    }
    report = {
        "repair_scope": [
            {
                "artifact": "outline",
                "chapters": [60],
                "fields": ["notes"],
            }
        ]
    }
    scopes = collect_repair_scopes(report, default_artifact="outline")

    scoped = _build_scoped_init_repair_payload(payload, artifact="outline", scopes=scopes)

    assert scoped is not None
    assert len(scoped["chapters"]) == 120
    assert scoped["chapters"][59]["chapter_number"] == 60
    assert scoped["chapters"][59]["notes"] == payload["chapters"][59]["notes"]
    assert "notes" not in scoped["chapters"][0]


async def test_oversized_init_repair_uses_scoped_patch_and_records_ledger(tmp_path) -> None:
    storage = FileSystemStorage(tmp_path)
    layout = ProjectLayout(tmp_path / "project")
    layout.ensure_dirs()
    events: list[tuple[str, dict[str, Any]]] = []
    call_contexts: list[dict[str, Any]] = []

    async def call_with_retry(task_type, context, **_kwargs):
        assert task_type == TaskType.REPAIR_INIT_ARTIFACT_PATCH
        call_contexts.append(context)
        assert context["artifact_payload"] == {}
        repair_targets = context["repair_targets"]
        assert len(repair_targets) == 1
        assert repair_targets[0]["target_id"] == "outline:60:notes"
        assert repair_targets[0]["chapter_number"] == 60
        assert repair_targets[0]["current_value"] == payload["chapters"][59]["notes"]
        return {
            "patches": [
                {
                    "target_id": "outline:60:notes",
                    "value": "修复后：第60章只保留人格整合的阶段性进展。",
                    "issue_ids": ["issue_1"],
                    "rationale": "把第60章从完成节点降为阶段进展。",
                }
            ],
            "summary": "scoped patch applied",
        }

    ctx = SimpleNamespace(
        settings=SimpleNamespace(
            init_coherence_block_min_severity="high",
            init_coherence_patch_max_ops=40,
            temp_repair_init_artifact_patch=0.15,
        ),
        storage=storage,
        layout=layout,
        router=None,
        call_with_retry=call_with_retry,
        on_step=lambda step, data: events.append((step, data)),
    )
    payload = {
        "title": "测试",
        "chapters": [
            {
                "chapter_number": number,
                "title": f"第{number}章",
                "goal": f"目标{number}",
                "notes": ("旧备注" * 250) + str(number),
            }
            for number in range(1, 121)
        ],
    }
    report = {
        "verdict": "needs_repair",
        "issues": [
            {
                "id": "issue_1",
                "severity": "high",
                "type": "state_axis_timeline_conflict",
                "description": "第60章误写为人格整合完成。",
                "repair_scope": [
                    {
                        "artifact": "outline",
                        "chapters": [60],
                        "fields": ["notes"],
                        "issue_ids": ["issue_1"],
                    }
                ],
            }
        ],
        "repair_scope": [
            {
                "artifact": "outline",
                "chapters": [60],
                "fields": ["notes"],
                "issue_ids": ["issue_1"],
            }
        ],
        "change_intent": "修正第60章人格整合节点。",
        "summary": "需要修复。",
        "batch_reports": [{"large": "x" * 20000}],
    }
    repairs: list[dict[str, Any]] = []

    repaired = await _repair_init_artifact_payload(
        ctx,
        artifact="outline",
        payload=payload,
        report=report,
        round_index=1,
        repairs=repairs,
    )

    assert call_contexts
    assert "batch_reports" not in call_contexts[0]["coherence_report"]
    assert repaired["chapters"][59]["notes"] == "修复后：第60章只保留人格整合的阶段性进展。"
    assert repaired["chapters"][0]["notes"] == payload["chapters"][0]["notes"]
    assert repairs[0]["status"] == "applied"
    assert repairs[0]["repair_type"] == "target_patch"
    assert repairs[0]["source_issue_ids"] == ["issue_1"]
    assert repairs[0]["target_count"] == 1
    assert repairs[0]["applied_target_count"] == 1
    assert any(step == "repair_init_artifact_targets" for step, _ in events)
    persisted = storage.load_json(layout.reports_dir / "init_artifact_repair.json")
    assert persisted["repairs"][0]["repair_type"] == "target_patch"


async def test_init_repair_target_mode_fixes_irreversible_outline_timeline(tmp_path) -> None:
    storage = FileSystemStorage(tmp_path)
    layout = ProjectLayout(tmp_path / "project")
    layout.ensure_dirs()
    call_contexts: list[dict[str, Any]] = []

    payload = {
        "chapters": [
            {
                "chapter_number": 66,
                "title": "暗潮重涌",
                "beats_summary": ["幼态人格说「以后我不用再出来帮你扛了」"],
            },
            {
                "chapter_number": 78,
                "title": "天牢遗痕",
                "main_plot_points": ["沈氏平反", "幼态人格彻底融入主人格，完成核心人格整合"],
            },
            {
                "chapter_number": 104,
                "title": "太庙疑云",
                "beats_summary": ["幼态人格最后一次出现，提醒玄昱握紧沈清漪的手"],
            },
        ],
    }

    async def call_with_retry(task_type, context, **_kwargs):
        assert task_type == TaskType.REPAIR_INIT_ARTIFACT_PATCH
        call_contexts.append(context)
        targets = context["repair_targets"]
        assert {target["target_id"] for target in targets} == {
            "outline:66:beats_summary:0",
            "outline:78:main_plot_points:1",
            "outline:104:beats_summary:0",
        }
        return {
            "patches": [
                {
                    "target_id": "outline:66:beats_summary:0",
                    "value": "幼态人格说「以后我会少出来打扰，先在意识里休息」",
                    "issue_ids": ["issue_001"],
                    "rationale": "把不可逆离场改为阶段性休息。",
                },
                {
                    "target_id": "outline:78:main_plot_points:1",
                    "value": "幼态人格与主人格达成阶段性和解，整合进程取得关键突破",
                    "issue_ids": ["issue_001"],
                    "rationale": "把完成节点降级为阶段性突破。",
                },
                {
                    "target_id": "outline:104:beats_summary:0",
                    "value": "幼态人格一次关键出现，提醒玄昱握紧沈清漪的手",
                    "issue_ids": ["issue_001"],
                    "rationale": "删除最后一次的不可逆声明。",
                },
            ],
            "summary": "target patch applied",
        }

    ctx = SimpleNamespace(
        settings=SimpleNamespace(
            init_coherence_block_min_severity="high",
            init_coherence_patch_max_ops=40,
            temp_repair_init_artifact_patch=0.15,
        ),
        storage=storage,
        layout=layout,
        router=None,
        call_with_retry=call_with_retry,
        on_step=lambda *_args: None,
    )
    report = {
        "verdict": "needs_repair",
        "issues": [
            {
                "issue_id": "issue_001",
                "id": "cand_0004_issue_1",
                "severity": "high",
                "type": "irreversible_event_conflict",
                "description": "幼态人格整合线中不可逆状态声明重复出现。",
                "evidence": [
                    "66章：幼态人格称「以后我不用再出来帮你挡了」",
                    "78章：声明「幼态人格彻底融入主人格，完成核心人格整合」",
                    "104章：声明「幼态人格最后一次出现」",
                ],
                "repair_scope": [
                    {
                        "artifact": "outline",
                        "chapters": [66, 78, 104],
                        "fields": ["beats_summary", "main_plot_points"],
                        "issue_ids": ["cand_0004_issue_1"],
                    }
                ],
            }
        ],
        "repair_scope": [
            {
                "artifact": "outline",
                "chapters": [66, 78, 104],
                "fields": ["beats_summary", "main_plot_points"],
                "issue_ids": ["cand_0004_issue_1"],
            }
        ],
        "change_intent": "消除幼态人格整合线的不可逆重复声明。",
        "summary": "needs repair",
    }
    repairs: list[dict[str, Any]] = []

    repaired = await _repair_init_artifact_payload(
        ctx,
        artifact="outline",
        payload=payload,
        report=report,
        round_index=1,
        repairs=repairs,
    )

    assert call_contexts[0]["artifact_payload"] == {}
    assert repaired["chapters"][0]["beats_summary"][0].endswith("先在意识里休息」")
    assert "阶段性和解" in repaired["chapters"][1]["main_plot_points"][1]
    assert "最后一次" not in repaired["chapters"][2]["beats_summary"][0]
    assert repairs[0]["repair_type"] == "target_patch"
    assert repairs[0]["patch_count"] == 3
    assert repairs[0]["applied_target_count"] == 3


async def test_init_repair_splits_multiple_issues_into_focused_calls(tmp_path) -> None:
    storage = FileSystemStorage(tmp_path)
    layout = ProjectLayout(tmp_path / "project")
    layout.ensure_dirs()
    events: list[tuple[str, dict[str, Any]]] = []
    call_contexts: list[dict[str, Any]] = []

    payload = {
        "chapters": [
            {"chapter_number": 5, "title": "旧誓", "goal": "重复求婚"},
            {"chapter_number": 9, "title": "旧图", "notes": "草图伏笔晚于揭示"},
        ]
    }

    async def call_with_retry(task_type, context, **_kwargs):
        assert task_type == TaskType.REPAIR_INIT_ARTIFACT_PATCH
        call_contexts.append(context)
        issues = context["coherence_report"]["issues"]
        assert len(issues) == 1
        issue_id = issues[0]["id"]
        targets = context["repair_targets"]
        assert len(targets) == 1
        return {
            "patches": [
                {
                    "target_id": targets[0]["target_id"],
                    "value": f"修复-{issue_id}",
                    "issue_ids": [issue_id],
                    "rationale": f"只处理 {issue_id}。",
                }
            ],
            "summary": f"fixed {issue_id}",
        }

    ctx = SimpleNamespace(
        settings=SimpleNamespace(
            init_coherence_block_min_severity="high",
            init_coherence_patch_max_ops=40,
            init_coherence_point_repair_enabled=True,
            temp_repair_init_artifact_patch=0.15,
        ),
        storage=storage,
        layout=layout,
        router=None,
        call_with_retry=call_with_retry,
        on_step=lambda step, data: events.append((step, data)),
    )
    report = {
        "verdict": "needs_repair",
        "issues": [
            {
                "id": "issue_a",
                "severity": "high",
                "type": "chapter_number_contradiction",
                "description": "第5章重复求婚。",
                "evidence": ["重复求婚"],
                "repair_scope": [
                    {
                        "artifact": "outline",
                        "chapters": [5],
                        "fields": ["goal"],
                        "issue_ids": ["issue_a"],
                    }
                ],
            },
            {
                "id": "issue_b",
                "severity": "high",
                "type": "foreshadow_after_reveal_violation",
                "description": "第9章草图伏笔晚于揭示。",
                "evidence": ["草图伏笔晚于揭示"],
                "repair_scope": [
                    {
                        "artifact": "outline",
                        "chapters": [9],
                        "fields": ["notes"],
                        "issue_ids": ["issue_b"],
                    }
                ],
            },
        ],
        "summary": "两个错误必须分别处理。",
    }
    repairs: list[dict[str, Any]] = []

    repaired = await _repair_init_artifact_payload(
        ctx,
        artifact="outline",
        payload=payload,
        report=report,
        round_index=1,
        repairs=repairs,
    )

    assert len(call_contexts) == 2
    assert [ctx["coherence_report"]["focused_issue_id"] for ctx in call_contexts] == [
        "issue_a",
        "issue_b",
    ]
    assert repaired["chapters"][0]["goal"] == "修复-issue_a"
    assert repaired["chapters"][1]["notes"] == "修复-issue_b"
    assert [repair["source_issue_ids"] for repair in repairs] == [["issue_a"], ["issue_b"]]
    assert [repair["patch_count"] for repair in repairs] == [1, 1]
    assert any(step == "repair_init_artifact_point_batch" for step, _ in events)
    focus_events = [data for step, data in events if step == "repair_init_artifact_issue_focus"]
    assert [event["issue_id"] for event in focus_events] == ["issue_a", "issue_b"]


async def test_streamed_outline_claims_finalize_to_reusable_stage(tmp_path) -> None:
    storage = FileSystemStorage(tmp_path)
    layout = ProjectLayout(tmp_path / "project")
    layout.ensure_dirs()
    events: list[tuple[str, dict[str, object]]] = []

    async def call_with_retry(task_type, context, **_kwargs):
        assert task_type == TaskType.EXTRACT_INIT_COHERENCE_CLAIMS
        claims = []
        for chapter in context["chunk"]["payload"]["current"]:
            number = int(chapter["chapter_number"])
            claims.append(
                _raw_claim(
                    claim_id=f"outline_ch{number:03d}_goal",
                    artifact="outline",
                    source_path=f"/chapters/{number - 1}/goal",
                    source_field="goal",
                    chapter_numbers=[number],
                    subject_ids=["主角A"],
                    subject_text="主角A",
                    axis="plot_progress",
                    claim_type="event",
                    claim_text=str(chapter["goal"]),
                    evidence=str(chapter["goal"]),
                    confidence=0.9,
                )
            )
        return _claim_response({"claims": claims})

    ctx = SimpleNamespace(
        settings=SimpleNamespace(init_coherence_claim_max_parallel=1),
        storage=storage,
        layout=layout,
        router=None,
        call_with_retry=call_with_retry,
        on_step=lambda step, data: events.append((step, data)),
    )
    chapters = [
        {"chapter_number": 1, "title": "一", "goal": "发现线索"},
        {"chapter_number": 2, "title": "二", "goal": "确认线索"},
    ]
    chunks = build_outline_stream_claim_chunks(ctx.settings, chapters=chapters)

    claims = await prefetch_init_coherence_claim_chunks(
        ctx,
        stage="outline_inheritance",
        profile={"summary": "demo"},
        chunks=chunks,
    )
    finalized = finalize_streamed_init_coherence_claims(
        ctx,
        stage="outline_inheritance",
        artifacts={
            "outline": {
                "total_chapters": 2,
                "volume_mode": False,
                "chapters": chapters,
                "synopsis": "demo",
            }
        },
    )

    ledger = storage.load_json(layout.memory_dir / CLAIM_LEDGER_JSON)
    stage = ledger["stages"]["outline_inheritance"]
    assert len(claims) == 2
    assert finalized is True
    assert stage["streamed"] is True
    assert sorted(stage["claim_ids"]) == [
        "outline_stream_1_2_outline_ch001_goal",
        "outline_stream_1_2_outline_ch002_goal",
    ]
    assert all(ledger["claims_by_id"][claim_id]["artifact_hash"] for claim_id in stage["claim_ids"])
    assert any(event[0] == "extract_init_coherence_claims" for event in events)


def test_streamed_outline_claim_chunks_follow_configured_batch_size() -> None:
    settings = SimpleNamespace(init_coherence_claim_batch_size=3)
    chapters = [
        {"chapter_number": number, "title": f"第{number}章", "goal": f"目标{number}"}
        for number in range(1, 9)
    ]

    chunks = build_outline_stream_claim_chunks(settings, chapters=chapters)

    assert [chunk.chapter_numbers for chunk in chunks] == [[1, 2, 3], [4, 5, 6], [7, 8]]
    assert [chunk.chunk_id for chunk in chunks] == [
        "outline_stream_1_3",
        "outline_stream_4_6",
        "outline_stream_7_8",
    ]


def test_outline_claim_chunks_keep_large_config_when_payload_is_small() -> None:
    settings = SimpleNamespace(
        init_coherence_claim_batch_size=8,
        init_coherence_overlap_chapters=3,
    )
    chapters = [
        {"chapter_number": number, "title": f"第{number}章", "goal": f"目标{number}"}
        for number in range(1, 13)
    ]

    chunks = _build_artifact_chunks(settings, {"outline": {"chapters": chapters}})

    assert [chunk.chapter_numbers for chunk in chunks] == [
        [1, 2, 3, 4, 5, 6, 7, 8],
        [9, 10, 11, 12],
    ]
    assert chunks[0].payload["previous_context"] == []
    assert len(chunks[0].payload["next_context"]) == 3
    assert len(chunks[1].payload["previous_context"]) == 3
    assert chunks[1].payload["next_context"] == []


def test_outline_claim_chunks_split_oversized_batches_dynamically() -> None:
    settings = SimpleNamespace(
        init_coherence_claim_batch_size=8,
        init_coherence_overlap_chapters=3,
        init_coherence_claim_payload_char_budget=1_000,
    )
    chapters = [
        {
            "chapter_number": number,
            "title": f"第{number}章",
            "goal": f"目标{number}" + "：复杂铺垫" * 80,
        }
        for number in range(1, 9)
    ]

    chunks = _build_artifact_chunks(settings, {"outline": {"chapters": chapters}})

    covered = [number for chunk in chunks for number in chunk.chapter_numbers]
    assert covered == list(range(1, 9))
    assert len(chunks) > 1
    assert all(len(chunk.payload["current"]) < 8 for chunk in chunks)
    assert any("adaptive_split" in chunk.payload for chunk in chunks)


async def test_extract_claims_passes_conservative_claim_limit(tmp_path) -> None:
    storage = FileSystemStorage(tmp_path)
    layout = ProjectLayout(tmp_path / "project")
    layout.ensure_dirs()
    seen_limits: list[int] = []

    async def call_with_retry(task_type, context, **_kwargs):
        assert task_type == TaskType.EXTRACT_INIT_COHERENCE_CLAIMS
        seen_limits.append(context["claim_limit"])
        return _claim_response({
            "claims": [
                _raw_claim(
                    claim_id="outline_stream_1_1_goal",
                    artifact="outline",
                    source_path="/chapters/0/goal",
                    source_field="goal",
                    chapter_numbers=[1],
                    subject_ids=["主角A"],
                    axis="plot_progress",
                    claim_type="event",
                    claim_text="第1章推进目标。",
                    evidence="第1章推进目标。",
                )
            ]
        })

    ctx = SimpleNamespace(
        settings=SimpleNamespace(
            init_coherence_claim_max_parallel=1,
            temp_extract_init_coherence_claims=0.1,
        ),
        storage=storage,
        layout=layout,
        router=None,
        call_with_retry=call_with_retry,
        on_step=lambda _step, _data: None,
    )
    chunk = InitArtifactChunk(
        artifact="outline",
        chunk_id="outline_stream_1_1",
        source_path="/chapters/0:1",
        source_field="chapters",
        chapter_numbers=[1],
        payload={"current": [{"chapter_number": 1, "goal": "目标1"}]},
        extraction_mode="stream",
        evidence_refs=["/chapters/0"],
    )

    claims = await _extract_claims(
        ctx,
        stage="outline_inheritance",
        profile={"summary": "demo"},
        chunks=[chunk],
    )

    assert len(claims) == 1
    assert seen_limits == [6]


async def test_streamed_outline_claim_prefetch_shares_chunk_parallel_budget(
    tmp_path,
) -> None:
    storage = FileSystemStorage(tmp_path)
    layout = ProjectLayout(tmp_path / "project")
    layout.ensure_dirs()
    counters = {"active": 0, "max_active": 0, "calls": 0}

    async def call_with_retry(task_type, context, **_kwargs):
        assert task_type == TaskType.EXTRACT_INIT_COHERENCE_CLAIMS
        counters["calls"] += 1
        counters["active"] += 1
        counters["max_active"] = max(counters["max_active"], counters["active"])
        try:
            await asyncio.sleep(0.01)
            chunk = context["chunk"]
            chapter_number = int(chunk["chapter_numbers"][0])
            return _claim_response({
                "claims": [
                    _raw_claim(
                        claim_id=f"{chunk['chunk_id']}_goal",
                        artifact="outline",
                        source_path=f"/chapters/{chapter_number - 1}/goal",
                        source_field="goal",
                        chapter_numbers=[chapter_number],
                        subject_ids=["主角A"],
                        axis="plot_progress",
                        claim_type="event",
                        claim_text=f"第{chapter_number}章推进目标。",
                        evidence=f"第{chapter_number}章推进目标。",
                    )
                ]
            })
        finally:
            counters["active"] -= 1

    ctx = SimpleNamespace(
        settings=SimpleNamespace(
            init_coherence_claim_max_parallel=2,
            temp_extract_init_coherence_claims=0.1,
        ),
        storage=storage,
        layout=layout,
        router=None,
        call_with_retry=call_with_retry,
        on_step=lambda _step, _data: None,
    )

    def chunk(number: int) -> InitArtifactChunk:
        return InitArtifactChunk(
            artifact="outline",
            chunk_id=f"outline_stream_{number}_{number}",
            source_path=f"/chapters/{number - 1}:{number}",
            source_field="chapters",
            chapter_numbers=[number],
            payload={"current": [{"chapter_number": number, "goal": f"目标{number}"}]},
            extraction_mode="stream",
            evidence_refs=[f"/chapters/{number - 1}"],
        )

    shared_limiter = asyncio.Semaphore(2)
    results = await asyncio.gather(
        prefetch_init_coherence_claim_chunks(
            ctx,
            stage="outline_inheritance",
            profile={"summary": "demo"},
            chunks=[chunk(1), chunk(2)],
            concurrency_limiter=shared_limiter,
        ),
        prefetch_init_coherence_claim_chunks(
            ctx,
            stage="outline_inheritance",
            profile={"summary": "demo"},
            chunks=[chunk(3), chunk(4)],
            concurrency_limiter=shared_limiter,
        ),
    )

    assert sum(len(batch) for batch in results) == 4
    assert counters["calls"] == 4
    assert counters["max_active"] == 2


async def test_blueprint_holistic_and_sharded_claims_are_merged(tmp_path) -> None:
    storage = FileSystemStorage(tmp_path)
    layout = ProjectLayout(tmp_path / "project")
    layout.ensure_dirs()
    calls: list[TaskType] = []

    async def call_with_retry(task_type, context, **_kwargs):
        calls.append(task_type)
        if task_type == TaskType.EXTRACT_BLUEPRINT_HOLISTIC_CLAIMS:
            return _claim_response({
                "claims": [
                    _raw_claim(
                        claim_id="holistic_dependency",
                        artifact="blueprint",
                        source_path="/key_turning_points",
                        source_field="key_turning_points",
                        chapter_numbers=[2],
                        subject_ids=["主角A"],
                        subject_text="主角A",
                        axis="motivation",
                        claim_type="dependency",
                        claim_text="关键转折依赖前置动机已经成立。",
                        evidence="关键转折依赖前置动机",
                        confidence=0.91,
                    )
                ]
            })
        assert task_type == TaskType.EXTRACT_INIT_COHERENCE_CLAIMS
        return _claim_response({
            "claims": [
                _raw_claim(
                    claim_id="shard_state",
                    artifact="blueprint",
                    source_path=context["chunk"]["source_path"],
                    source_field=context["chunk"]["source_field"],
                    chapter_numbers=[1],
                    subject_ids=["主角A"],
                    subject_text="主角A",
                    axis="identity",
                    claim_type="state",
                    claim_text="主角A身份状态被设定。",
                    evidence="身份状态设定",
                    confidence=0.8,
                )
            ]
        })

    ctx = SimpleNamespace(
        settings=SimpleNamespace(
            init_blueprint_holistic_claims_enabled=True,
            init_blueprint_holistic_claim_max_tokens=4096,
            init_coherence_claim_max_parallel=1,
        ),
        storage=storage,
        layout=layout,
        router=None,
        call_with_retry=call_with_retry,
        on_step=lambda *_args: None,
    )
    artifacts = {
        "blueprint": {
            "synopsis": "demo",
            "key_turning_points": [{"chapter_number": 2, "description": "关键转折依赖前置动机"}],
            "character_arcs": [{"character": "主角A", "arc_summary": "身份状态设定"}],
        }
    }
    chunks = _build_artifact_chunks(ctx.settings, artifacts)

    claims = await _extract_stage_claims(
        ctx,
        stage="blueprint_coherence",
        profile={"summary": "demo"},
        artifacts=artifacts,
        chunks=chunks,
        focus_chapters=None,
    )

    assert TaskType.EXTRACT_BLUEPRINT_HOLISTIC_CLAIMS in calls
    assert TaskType.EXTRACT_INIT_COHERENCE_CLAIMS in calls
    modes = {claim.metadata.get("extraction_mode") for claim in claims}
    assert {"holistic", "shard"}.issubset(modes)


def test_local_story_fallbacks_disabled_by_default() -> None:
    assert _local_story_fallbacks_enabled(SimpleNamespace()) is False
    assert (
        _local_story_fallbacks_enabled(SimpleNamespace(init_disable_local_story_fallbacks=False))
        is True
    )


def test_high_issue_without_scope_is_not_auto_repairable() -> None:
    assert (
        has_repair_scope(
            {
                "verdict": "needs_repair",
                "issues": [{"id": "issue_1", "severity": "high", "description": "无法定位"}],
                "repair_scope": [
                    {
                        "artifact": "outline",
                        "chapters": [5],
                        "fields": ["goal"],
                    }
                ],
            },
            artifact="outline",
            min_severity="high",
        )
        is True
    )
    assert (
        has_repair_scope(
            {
                "verdict": "needs_repair",
                "issues": [{"id": "issue_1", "severity": "high", "description": "无法定位"}],
                "repair_scope": [],
            },
            artifact="outline",
            min_severity="high",
        )
        is False
    )


def test_repair_scope_collection_accepts_chapter_range() -> None:
    report = {
        "verdict": "needs_repair",
        "issues": [
            {
                "id": "issue_1",
                "severity": "high",
                "description": "章节契约窗口存在冲突。",
                "repair_scope": [
                    {
                        "artifact": "chapter_contracts",
                        "chapter_range": {"start": 49, "end": 52},
                        "fields": ["knowledge_ops", "cognitive_constraints"],
                    }
                ],
            }
        ],
        "repair_scope": [],
    }

    scopes = collect_repair_scopes(report, default_artifact="chapter_contracts")

    assert has_repair_scope(report, artifact="chapter_contracts", min_severity="high") is True
    assert scopes == [
        RepairScope(
            artifact="chapter_contracts",
            chapters={49, 50, 51, 52},
            fields={"knowledge_ops", "cognitive_constraints"},
            operation="field_replace",
            issue_ids=set(),
        )
    ]


def test_coherence_claim_requires_source_and_evidence() -> None:
    with pytest.raises(ValidationError):
        CoherenceClaim.model_validate(
            {
                "claim_id": "claim_1",
                "artifact": "outline",
                "claim_type": "state",
                "claim_text": "第5章仍按未婚状态推进。",
                "subject_text": "主角关系",
                "axis": "relationship_status",
            }
        )


def test_coherence_claim_accepts_empty_range_and_infers_from_chapters() -> None:
    claim = _coherence_claim_model_validate(
        {
            "claim_id": "claim_empty_range",
            "artifact": "blueprint",
            "source_path": "/narrative_phases/0",
            "source_field": "narrative_phases",
            "chapter_numbers": [2, 4],
            "chapter_range": {"start": None, "end": None},
            "claim_text": "第2至4章完成初始关系推进。",
            "evidence": "第2至4章",
        }
    )

    assert claim.chapter_range is not None
    assert claim.chapter_range.start == 2
    assert claim.chapter_range.end == 4


def test_coherence_claim_accepts_empty_global_range() -> None:
    claim = _coherence_claim_model_validate(
        {
            "claim_id": "claim_global",
            "artifact": "blueprint",
            "source_path": "/synopsis",
            "source_field": "synopsis",
            "chapter_range": {"start": None, "end": None},
            "claim_text": "全局设定保持科学与古法共生。",
            "evidence": "全局设定",
        }
    )

    assert claim.chapter_range is not None
    assert claim.chapter_range.start == 0
    assert claim.chapter_range.end == 0


def test_coherence_claim_requires_cognitive_fields_for_new_extractions() -> None:
    payload = _claim_payload()
    payload.pop("cognitive_object")

    with pytest.raises(ValidationError):
        CoherenceClaim.model_validate(payload)


def test_coherence_claim_accepts_conservative_cognitive_defaults() -> None:
    claim = CoherenceClaim.model_validate(
        _claim_payload(
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
    )

    assert claim.cognitive_subjects == []
    assert claim.cognitive_object == ""
    assert claim.cognitive_level == "unaware"
    assert claim.action_level == "none"
    assert claim.reader_awareness == "unknown"
    assert claim.character_knowledge_coverage == {}


async def test_exact_candidate_retrieval_finds_relationship_state_collision() -> None:
    claims = [
        _coherence_claim_model_validate(
            {
                "claim_id": "outline_ch003_married",
                "artifact": "outline",
                "source_path": "/chapters/2/goal",
                "source_field": "goal",
                "chapter_numbers": [3],
                "subject_ids": ["主角A", "主角B"],
                "subject_text": "主角A与主角B的关系",
                "axis": "relationship_status",
                "claim_type": "state",
                "claim_text": "第3章完成婚礼，关系进入已婚状态。",
                "state_after": "已婚",
                "irreversible": True,
                "temporality": "actual",
                "evidence": "完成婚礼",
                "confidence": 0.92,
            }
        ),
        _coherence_claim_model_validate(
            {
                "claim_id": "outline_ch005_proposal",
                "artifact": "outline",
                "source_path": "/chapters/4/goal",
                "source_field": "goal",
                "chapter_numbers": [5],
                "subject_ids": ["主角A", "主角B"],
                "subject_text": "主角A与主角B的关系",
                "axis": "relationship_status",
                "claim_type": "state",
                "claim_text": "第5章以首次求婚推进关系确认。",
                "state_after": "待求婚",
                "temporality": "actual",
                "evidence": "再次求婚",
                "confidence": 0.9,
            }
        ),
    ]
    ctx = SimpleNamespace(
        settings=SimpleNamespace(
            init_coherence_candidate_max_per_batch=80,
            init_coherence_use_memory=False,
            init_coherence_semantic_top_k=0,
        )
    )

    candidates, degraded_memory, _index = await retrieve_init_conflict_candidates(ctx, claims)

    assert degraded_memory is False
    assert candidates
    assert candidates[0].candidate_type == "same_subject_axis"
    assert set(candidates[0].claim_ids) == {
        "outline_ch003_married",
        "outline_ch005_proposal",
    }


def test_exact_candidate_retrieval_sends_stage_point_progression_to_model() -> None:
    claims = [
        _coherence_claim_model_validate(
            {
                "claim_id": "char_arc_0_ms3",
                "artifact": "blueprint",
                "source_path": "/character_arcs/0/milestones/2",
                "source_field": "milestones",
                "chapter_numbers": [41, 60],
                "chapter_range": {"start": 41, "end": 60},
                "subject_ids": ["陆深", "沈知微"],
                "subject_text": "陆深与沈知微的协作",
                "axis": "knowledge",
                "claim_type": "state",
                "claim_text": "陆深发现隐疾根源并改善消毒流程，从认知穿刺转向价值观重塑。",
                "state_before": "认知穿刺执念",
                "state_after": "接纳执念价值",
                "evidence": "从认知穿刺转向接纳执念价值的价值观重塑。",
            }
        ),
        _coherence_claim_model_validate(
            {
                "claim_id": "blueprint_kp_006",
                "artifact": "blueprint",
                "source_path": "/key_turning_points/0:8",
                "source_field": "key_turning_points",
                "chapter_numbers": [42],
                "chapter_range": {"start": 42, "end": 42},
                "subject_ids": ["陆深", "沈知微"],
                "subject_text": "陆深的隐疾与执念",
                "axis": "knowledge",
                "claim_type": "payoff",
                "claim_text": "陆深发现左胸隐痛根源是自我苛责科研执念。",
                "state_before": "未觉察隐疾根源",
                "state_after": "知晓隐疾源于自我苛责执念",
                "event_type": "discover_self_punitive_origin",
                "payoff_id": "core_suspense_resolution",
                "payoff_kind": "information",
                "irreversible": True,
                "evidence": "核心悬疑揭示，目标转向直面内心。",
            }
        ),
    ]

    candidates = _retrieve_exact_candidates(claims, max_candidates=10)

    assert len(candidates) == 1
    assert candidates[0].candidate_type == "structural_overlap"
    assert set(candidates[0].claim_ids) == {"char_arc_0_ms3", "blueprint_kp_006"}


def test_exact_candidate_retrieval_covers_every_overlapping_pair_once() -> None:
    claims = [
        _coherence_claim_model_validate(
            {
                "claim_id": f"overlap_{number}",
                "artifact": "outline",
                "source_path": f"/chapters/0/field_{number}",
                "source_field": f"field_{number}",
                "chapter_numbers": [1],
                "subject_ids": [f"主体{number}"],
                "axis": f"axis_{number}",
                "claim_text": f"事实 {number}",
                "evidence": f"证据 {number}",
            }
        )
        for number in range(1, 4)
    ]

    candidates = _retrieve_exact_candidates(claims, max_candidates=1)

    structural = [
        candidate for candidate in candidates if candidate.candidate_type == "structural_overlap"
    ]
    assert len(structural) == 3
    assert {
        tuple(sorted(candidate.claim_ids)) for candidate in structural
    } == {
        ("overlap_1", "overlap_2"),
        ("overlap_1", "overlap_3"),
        ("overlap_2", "overlap_3"),
    }


def test_exact_candidate_retrieval_finds_cognitive_regression_cross_type() -> None:
    claims = [
        _coherence_claim_model_validate(
            {
                "claim_id": "claim_confirmed",
                "artifact": "outline",
                "source_path": "/chapters/0/goal",
                "chapter_numbers": [1],
                "subject_ids": ["玄昱"],
                "claim_type": "knowledge",
                "claim_text": "玄昱确认沈清漪即青阳会盟少年。",
                "evidence": "第1章确认",
                "cognitive_subjects": ["玄昱"],
                "cognitive_object": "沈清漪即青阳会盟少年",
                "cognitive_level": "confirmed",
                "action_level": "internal",
                "cognitive_chapter": 1,
            }
        ),
        _coherence_claim_model_validate(
            {
                "claim_id": "claim_suspicion",
                "artifact": "chapter_contracts",
                "source_path": "/chapter_contracts/4",
                "chapter_numbers": [5],
                "subject_ids": ["玄昱"],
                "claim_type": "event",
                "claim_text": "玄昱仅怀疑沈清漪与青阳会盟少年有关。",
                "evidence": "第5章契约",
                "cognitive_subjects": ["玄昱"],
                "cognitive_object": "沈清漪即青阳会盟少年",
                "cognitive_level": "suspicion",
                "action_level": "hinted",
                "cognitive_chapter": 5,
            }
        ),
    ]

    candidates = _retrieve_exact_candidates(claims, max_candidates=10)

    assert len(candidates) == 1
    assert candidates[0].candidate_type == "cognitive_regression"
    assert set(candidates[0].claim_ids) == {
        "claim_confirmed",
        "claim_suspicion",
    }


def test_exact_candidate_retrieval_uses_cognitive_chapter_for_regression() -> None:
    claims = [
        _coherence_claim_model_validate(
            {
                "claim_id": "claim_confirmed_by_cognitive_chapter",
                "artifact": "outline",
                "source_path": "/knowledge/identity/confirmed",
                "chapter_numbers": [],
                "chapter_range": None,
                "subject_ids": ["玄昱"],
                "claim_type": "knowledge",
                "claim_text": "玄昱确认沈清漪即青阳会盟少年。",
                "evidence": "认知章锚定为第1章",
                "cognitive_subjects": ["玄昱"],
                "cognitive_object": "沈清漪即青阳会盟少年",
                "cognitive_level": "confirmed",
                "action_level": "internal",
                "cognitive_chapter": 1,
            }
        ),
        _coherence_claim_model_validate(
            {
                "claim_id": "claim_suspicion_by_cognitive_chapter",
                "artifact": "chapter_contracts",
                "source_path": "/knowledge/identity/suspicion",
                "chapter_numbers": [],
                "chapter_range": None,
                "subject_ids": ["玄昱"],
                "claim_type": "knowledge",
                "claim_text": "玄昱第5章仅怀疑沈清漪身份。",
                "evidence": "认知章锚定为第5章",
                "cognitive_subjects": ["玄昱"],
                "cognitive_object": "沈清漪即青阳会盟少年",
                "cognitive_level": "suspicion",
                "action_level": "hinted",
                "cognitive_chapter": 5,
            }
        ),
    ]

    candidates = _retrieve_exact_candidates(claims, max_candidates=10)

    assert len(candidates) == 1
    assert candidates[0].candidate_type == "structural_overlap"
    assert set(candidates[0].claim_ids) == {
        "claim_confirmed_by_cognitive_chapter",
        "claim_suspicion_by_cognitive_chapter",
    }


def test_exact_candidate_retrieval_finds_premature_public_reveal() -> None:
    claims = [
        _coherence_claim_model_validate(
            {
                "claim_id": "claim_internal_until_50",
                "artifact": "outline",
                "source_path": "/chapters/0/goal",
                "chapter_numbers": [1],
                "subject_ids": ["玄昱"],
                "claim_type": "knowledge",
                "claim_text": "玄昱在洞房夜确认沈清漪即青阳会盟少年，但不说破。",
                "evidence": "第1章内心确认，第50章相认",
                "cognitive_subjects": ["玄昱"],
                "cognitive_object": "沈清漪即青阳会盟少年",
                "cognitive_level": "confirmed",
                "action_level": "internal",
                "reader_awareness": "full",
                "cognitive_chapter": 1,
                "public_reveal_chapter": 50,
            }
        ),
        _coherence_claim_model_validate(
            {
                "claim_id": "claim_revealed_early",
                "artifact": "chapter_contracts",
                "source_path": "/chapter_contracts/4",
                "chapter_numbers": [5],
                "subject_ids": ["玄昱"],
                "claim_type": "knowledge",
                "claim_text": "玄昱第5章公开说破沈清漪即青阳会盟少年。",
                "evidence": "第5章公开说破",
                "cognitive_subjects": ["玄昱"],
                "cognitive_object": "沈清漪即青阳会盟少年",
                "cognitive_level": "acknowledged",
                "action_level": "revealed",
                "cognitive_chapter": 5,
            }
        ),
    ]

    candidates = _retrieve_exact_candidates(claims, max_candidates=10)

    assert len(candidates) == 1
    assert candidates[0].candidate_type == "premature_public_reveal"
    assert set(candidates[0].claim_ids) == {
        "claim_internal_until_50",
        "claim_revealed_early",
    }


def test_exact_candidate_retrieval_finds_foreshadow_after_reveal_single_claim() -> None:
    claim = _coherence_claim_model_validate(
        {
            "claim_id": "claim_bad_anchor",
            "artifact": "outline",
            "source_path": "/chapters/0/goal",
            "chapter_numbers": [1],
            "subject_ids": ["玄昱"],
            "claim_type": "knowledge",
            "claim_text": "第50章公开相认，却把第60章列为伏笔。",
            "evidence": "伏笔章晚于公开章",
            "cognitive_subjects": ["玄昱"],
            "cognitive_object": "沈清漪即青阳会盟少年",
            "cognitive_level": "confirmed",
            "action_level": "internal",
            "public_reveal_chapter": 50,
            "foreshadow_chapters": [60],
        }
    )

    candidates = _retrieve_exact_candidates([claim], max_candidates=10)

    assert [candidate.candidate_type for candidate in candidates] == ["foreshadow_after_reveal"]


class _ParallelCallCtx:
    def __init__(self, *, settings: SimpleNamespace, response: dict[str, object]) -> None:
        self.settings = settings
        self.router = None
        self.response = response
        self.call_count = 0
        self.active_calls = 0
        self.max_active_calls = 0
        self.events: list[tuple[str, dict[str, object]]] = []
        self.contexts: list[dict[str, object]] = []
        self.storage: Any = None
        self.layout: Any = None

    async def call_with_retry(self, *_args: object, **_kwargs: object) -> dict[str, object]:
        self.call_count += 1
        if len(_args) > 1 and isinstance(_args[1], dict):
            self.contexts.append(_args[1])
        self.active_calls += 1
        self.max_active_calls = max(self.max_active_calls, self.active_calls)
        try:
            await asyncio.sleep(0.01)
            return _claim_response(self.response)
        finally:
            self.active_calls -= 1

    def on_step(self, step: str, payload: dict[str, object]) -> None:
        self.events.append((step, payload))


async def test_init_coherence_claim_extraction_runs_limited_parallel_batches() -> None:
    ctx = _ParallelCallCtx(
        settings=SimpleNamespace(
            init_coherence_claim_max_parallel=2,
            temp_extract_init_coherence_claims=0.1,
        ),
        response={
            "claims": [
                {
                    "subject_ids": ["沈知微"],
                    "axis": "event",
                    "claim_type": "event",
                    "claim_text": "沈知微完成一次关键诊断。",
                    "evidence": "关键诊断",
                }
            ]
        },
    )
    chunks = [
        InitArtifactChunk(
            artifact="outline",
            chunk_id=f"outline_{number}",
            source_path=f"/chapters/{number}",
            source_field="chapters",
            chapter_numbers=[number],
            payload={"chapter_number": number},
        )
        for number in range(1, 5)
    ]

    claims = await _extract_claims(
        ctx,
        stage="outline_inheritance",
        profile={"summary": "test"},
        chunks=chunks,
    )

    assert len(claims) == 4
    assert ctx.max_active_calls == 2
    progress_events = [event for event in ctx.events if event[0] == "extract_init_coherence_claims"]
    assert len(progress_events) == 4
    assert progress_events[-1][1]["claims"] == 4
    assert progress_events[-1][1]["max_parallel"] == 2


async def test_init_coherence_claim_extraction_coerces_null_optional_strings() -> None:
    ctx = _ParallelCallCtx(
        settings=SimpleNamespace(
            init_coherence_claim_max_parallel=1,
            temp_extract_init_coherence_claims=0.1,
        ),
        response={
            "claims": [
                {
                    "claim_id": "outline_ch001_nulls",
                    "source_path": "/chapters/1/goal",
                    "claim_text": "主角完成初次诊断。",
                    "evidence": "初次诊断",
                    "state_before": None,
                    "state_after": None,
                    "event_type": None,
                    "subject_ids": ["主角", None],
                }
            ]
        },
    )
    chunks = [
        InitArtifactChunk(
            artifact="outline",
            chunk_id="outline_1",
            source_path="/chapters/1",
            source_field="chapters",
            chapter_numbers=[1],
            payload={"chapter_number": 1},
        )
    ]

    claims = await _extract_claims(
        ctx,
        stage="outline_inheritance",
        profile={"summary": "test"},
        chunks=chunks,
    )

    assert len(claims) == 1
    assert claims[0].state_before == ""
    assert claims[0].state_after == ""
    assert claims[0].event_type == ""
    assert claims[0].subject_ids == ["主角"]


async def test_init_coherence_claim_extraction_splits_until_coverage_complete() -> None:
    calls: list[str] = []

    async def call_with_retry(task_type, context, **_kwargs):
        assert task_type == TaskType.EXTRACT_INIT_COHERENCE_CLAIMS
        chunk_payload = context["chunk"]
        calls.append(chunk_payload["chunk_id"])
        if "__coverage_" not in chunk_payload["chunk_id"]:
            return {
                "claims": [],
                "coverage_status": "partial",
                "unprocessed_source_refs": ["/chapters"],
            }
        chapter = chunk_payload["payload"]["current"][0]
        return _claim_response(
            {
                "claims": [
                    {
                        "claim_id": f"claim_{chapter['chapter_number']}",
                        "claim_text": chapter["goal"],
                        "evidence": chapter["goal"],
                    }
                ]
            }
        )

    ctx = SimpleNamespace(
        settings=SimpleNamespace(
            init_coherence_claim_max_parallel=1,
            init_coherence_claim_split_max_depth=2,
            temp_extract_init_coherence_claims=0.1,
        ),
        router=None,
        call_with_retry=call_with_retry,
        on_step=lambda *_args: None,
    )
    chunk = InitArtifactChunk(
        artifact="outline",
        chunk_id="outline_coverage",
        source_path="/chapters/0:2",
        source_field="chapters",
        chapter_numbers=[1, 2],
        payload={
            "current": [
                {"chapter_number": 1, "goal": "发现线索。"},
                {"chapter_number": 2, "goal": "确认线索。"},
            ]
        },
        extraction_mode="stream",
    )

    claims = await _extract_claims(
        ctx,
        stage="outline_inheritance",
        profile={"summary": "test"},
        chunks=[chunk],
    )

    assert len(claims) == 2
    assert calls == [
        "outline_coverage",
        "outline_coverage__coverage_1_a",
        "outline_coverage__coverage_1_b",
    ]
    coverage = ctx._init_coherence_claim_coverage["outline_inheritance"]
    assert coverage[0]["coverage_status"] == "complete"
    assert coverage[0]["unprocessed_source_refs"] == []
    assert coverage[0]["split_depth"] == 1


async def test_init_coherence_unsplittable_uncertain_coverage_is_preserved() -> None:
    async def call_with_retry(*_args, **_kwargs):
        return {
            "claims": [],
            "coverage_status": "uncertain",
            "unprocessed_source_refs": ["/rules/0"],
        }

    ctx = SimpleNamespace(
        settings=SimpleNamespace(
            init_coherence_claim_max_parallel=1,
            init_coherence_claim_split_max_depth=2,
            temp_extract_init_coherence_claims=0.1,
        ),
        router=None,
        call_with_retry=call_with_retry,
        on_step=lambda *_args: None,
    )
    chunk = InitArtifactChunk(
        artifact="story_bible",
        chunk_id="rules_unsplittable",
        source_path="/rules/0",
        source_field="rules",
        chapter_numbers=[],
        payload={"rule": "原文较短，但模型不能确认覆盖。"},
    )

    claims = await _extract_claims(
        ctx,
        stage="source_artifacts",
        profile={"summary": "test"},
        chunks=[chunk],
    )

    assert claims == []
    coverage = ctx._init_coherence_claim_coverage["source_artifacts"]
    assert coverage[0]["coverage_status"] == "uncertain"
    assert coverage[0]["unprocessed_source_refs"] == ["/rules/0"]


async def test_outline_claim_extraction_falls_back_when_model_returns_empty() -> None:
    ctx = _ParallelCallCtx(
        settings=SimpleNamespace(
            init_coherence_claim_max_parallel=1,
            temp_extract_init_coherence_claims=0.1,
        ),
        response={"claims": []},
    )
    chunks = [
        InitArtifactChunk(
            artifact="outline",
            chunk_id="outline_stream_1_2",
            source_path="/chapters/0:2",
            source_field="chapters",
            chapter_numbers=[1, 2],
            payload={
                "current": [
                    {"chapter_number": 1, "title": "初遇", "goal": "主角发现第一条线索。"},
                    {"chapter_number": 2, "title": "追索", "goal": "主角确认线索来源。"},
                ]
            },
            extraction_mode="stream",
        )
    ]

    claims = await _extract_claims(
        ctx,
        stage="outline_inheritance",
        profile={"summary": "test"},
        chunks=chunks,
    )

    assert [claim.claim_text for claim in claims] == [
        "主角发现第一条线索。",
        "主角确认线索来源。",
    ]
    assert all(claim.metadata["local_fallback"] is True for claim in claims)
    progress_events = [event for event in ctx.events if event[0] == "extract_init_coherence_claims"]
    assert progress_events[-1][1]["fallback"] == "local_outline_claims"
    assert progress_events[-1][1]["fallback_claims"] == 2
    assert progress_events[-1][1]["claims"] == 2


async def test_outline_claim_extraction_filters_repetitive_model_loop() -> None:
    ctx = _ParallelCallCtx(
        settings=SimpleNamespace(
            init_coherence_claim_max_parallel=1,
            temp_extract_init_coherence_claims=0.1,
        ),
        response={
            "claims": [
                {
                    "claim_text": "1937年8月13日下午三点十三分1937年8月13日下午三点十三分",
                    "evidence": "1937年8月13日下午三点十三分",
                }
            ]
        },
    )
    chunks = [
        InitArtifactChunk(
            artifact="outline",
            chunk_id="outline_stream_30_30",
            source_path="/chapters/29:30",
            source_field="chapters",
            chapter_numbers=[30],
            payload={"current": [{"chapter_number": 30, "goal": "两人揭开百年真相。"}]},
            extraction_mode="stream",
        )
    ]

    claims = await _extract_claims(
        ctx,
        stage="outline_inheritance",
        profile={"summary": "test"},
        chunks=chunks,
    )

    assert [claim.claim_text for claim in claims] == ["两人揭开百年真相。"]
    progress_events = [event for event in ctx.events if event[0] == "extract_init_coherence_claims"]
    assert progress_events[-1][1]["filtered_claims"] == 1
    assert progress_events[-1][1]["fallback_claims"] == 1


async def test_outline_claim_extraction_filters_narrative_excerpt() -> None:
    ctx = _ParallelCallCtx(
        settings=SimpleNamespace(
            init_coherence_claim_max_parallel=1,
            temp_extract_init_coherence_claims=0.1,
        ),
        response={
            "claims": [
                {
                    "claim_text": (
                        "2025年12月14日，技术部内依旧灯火通明。程晓秋盯着屏幕："
                        "「这段视频的加密等级很高，我需要一点时间破解。」林素素端着水过来："
                        "「你休息一下吧。」程晓秋摇头：「还不行。」"
                    ),
                    "evidence": "技术部内依旧灯火通明。",
                }
            ]
        },
    )
    chunks = [
        InitArtifactChunk(
            artifact="outline",
            chunk_id="outline_stream_46_48",
            source_path="/chapters/45:48",
            source_field="chapters",
            chapter_numbers=[46],
            payload={"current": [{"chapter_number": 46, "goal": "团队破解关键加密视频。"}]},
            extraction_mode="stream",
        )
    ]

    claims = await _extract_claims(
        ctx,
        stage="outline_inheritance",
        profile={"summary": "test"},
        chunks=chunks,
    )

    assert [claim.claim_text for claim in claims] == ["团队破解关键加密视频。"]
    progress_events = [event for event in ctx.events if event[0] == "extract_init_coherence_claims"]
    assert progress_events[-1][1]["filtered_claims"] == 1
    assert progress_events[-1][1]["fallback_claims"] == 1


async def test_init_coherence_claim_extraction_reuses_chunk_cache(tmp_path: Any) -> None:
    ctx = _ParallelCallCtx(
        settings=SimpleNamespace(
            init_coherence_claim_max_parallel=1,
            temp_extract_init_coherence_claims=0.1,
        ),
        response={
            "claims": [
                {
                    "claim_id": "outline_ch001_cached",
                    "source_path": "/chapters/1/goal",
                    "claim_text": "主角完成初次诊断。",
                    "evidence": "初次诊断",
                }
            ]
        },
    )
    storage = FileSystemStorage(tmp_path)
    ctx.storage = storage
    ctx.layout = ProjectLayout(storage.ensure_project_dir("project"))
    chunks = [
        InitArtifactChunk(
            artifact="outline",
            chunk_id="outline_1",
            source_path="/chapters/1",
            source_field="chapters",
            chapter_numbers=[1],
            payload={"chapter_number": 1},
        )
    ]

    first = await _extract_claims(
        ctx,
        stage="outline_inheritance",
        profile={"summary": "test"},
        chunks=chunks,
    )
    second = await _extract_claims(
        ctx,
        stage="outline_inheritance",
        profile={"summary": "test"},
        chunks=chunks,
    )

    assert len(first) == 1
    assert [claim.claim_id for claim in second] == ["outline_1_outline_ch001_cached"]
    assert ctx.call_count == 1
    assert ctx.events[-1][1]["cached"] is True


async def test_init_coherence_claim_cache_is_artifact_scoped_across_gates(tmp_path: Any) -> None:
    ctx = _ParallelCallCtx(
        settings=SimpleNamespace(
            init_coherence_claim_max_parallel=1,
            temp_extract_init_coherence_claims=0.1,
        ),
        response={
            "claims": [
                {
                    "source_path": "/blueprint/phases/1",
                    "claim_text": "第一阶段完成承诺建立。",
                    "evidence": "承诺建立",
                }
            ]
        },
    )
    storage = FileSystemStorage(tmp_path)
    ctx.storage = storage
    ctx.layout = ProjectLayout(storage.ensure_project_dir("project"))
    chunk = InitArtifactChunk(
        artifact="blueprint",
        chunk_id="blueprint_phase_1",
        source_path="/phases/1",
        source_field="narrative_phases",
        chapter_numbers=[1, 2],
        payload={"phase": "承诺建立"},
    )

    await _extract_claims(
        ctx,
        stage="blueprint_coherence",
        profile={"summary": "test"},
        chunks=[chunk],
    )
    claims = await _extract_claims(
        ctx,
        stage="outline_inheritance",
        profile={"summary": "test"},
        chunks=[chunk],
    )

    assert [claim.claim_id for claim in claims] == ["blueprint_phase_1_claim_001"]
    assert ctx.call_count == 1
    assert claim_cache_stats(ctx) == {"claim_cache_hits": 1, "claim_cache_misses": 1}
    assert ctx.events[-1][1]["stage"] == "outline_inheritance"
    assert ctx.events[-1][1]["cached"] is True
    cache_file = next((ctx.layout.memory_dir / "init_coherence_claim_batches").glob("*.json"))
    payload = storage.load_json(cache_file)
    assert payload["schema_version"] == CLAIM_BATCH_CACHE_SCHEMA_VERSION
    assert payload["artifact"] == "blueprint"
    assert "stage" not in payload


async def test_init_coherence_focused_recheck_reuses_unfocused_chunks(tmp_path: Any) -> None:
    ctx = _ParallelCallCtx(
        settings=SimpleNamespace(
            init_coherence_claim_max_parallel=1,
            temp_extract_init_coherence_claims=0.1,
        ),
        response={
            "claims": [
                {
                    "claim_text": "章节窗口状态变化。",
                    "evidence": "状态变化",
                }
            ]
        },
    )
    storage = FileSystemStorage(tmp_path)
    ctx.storage = storage
    ctx.layout = ProjectLayout(storage.ensure_project_dir("project"))
    original_chunks = [
        InitArtifactChunk(
            artifact="outline",
            chunk_id="outline_1",
            source_path="/chapters/1",
            source_field="chapters",
            chapter_numbers=[1],
            payload={"chapter_number": 1, "goal": "保持不变"},
        ),
        InitArtifactChunk(
            artifact="outline",
            chunk_id="outline_2",
            source_path="/chapters/2",
            source_field="chapters",
            chapter_numbers=[2],
            payload={"chapter_number": 2, "goal": "旧焦点"},
        ),
    ]
    focused_recheck_chunks = [
        original_chunks[0],
        InitArtifactChunk(
            artifact="outline",
            chunk_id="outline_2",
            source_path="/chapters/2",
            source_field="chapters",
            chapter_numbers=[2],
            payload={"chapter_number": 2, "goal": "新焦点"},
        ),
    ]

    await _extract_claims(
        ctx,
        stage="outline_inheritance",
        profile={"summary": "test"},
        chunks=original_chunks,
    )
    await _extract_claims(
        ctx,
        stage="contract_coherence",
        profile={"summary": "test"},
        chunks=focused_recheck_chunks,
    )

    assert ctx.call_count == 3
    assert claim_cache_stats(ctx) == {"claim_cache_hits": 1, "claim_cache_misses": 3}
    progress_events = [event for event in ctx.events if event[0] == "extract_init_coherence_claims"]
    assert progress_events[-2][1]["cached"] is True
    assert progress_events[-1][1]["cached"] is False


def test_init_coherence_claim_cache_coerces_legacy_null_strings(tmp_path: Any) -> None:
    storage = FileSystemStorage(tmp_path)
    layout = ProjectLayout(storage.ensure_project_dir("project"))
    ctx = SimpleNamespace(storage=storage, layout=layout)
    chunk = InitArtifactChunk(
        artifact="outline",
        chunk_id="outline_1",
        source_path="/chapters/1",
        source_field="chapters",
        chapter_numbers=[1],
        payload={"chapter_number": 1},
    )
    claim = _coherence_claim_model_validate(
        {
            "claim_id": "outline_ch001_cached",
            "artifact": "outline",
            "source_path": "/chapters/1",
            "claim_text": "主角完成初次诊断。",
            "evidence": "初次诊断",
        }
    )
    _save_cached_claim_batch(
        ctx,
        stage="outline_inheritance",
        profile={"summary": "test"},
        chunk=chunk,
        batch=CoherenceClaimBatch(
            claims=[claim],
            coverage_status="complete",
            unprocessed_source_refs=[],
        ),
    )
    cache_file = next((layout.memory_dir / "init_coherence_claim_batches").glob("*.json"))
    payload = storage.load_json(cache_file)
    payload["claims"][0]["state_before"] = None
    payload["claims"][0]["state_after"] = None
    payload["claims"][0]["event_type"] = None
    storage.save_json(cache_file, payload)

    batch = _load_cached_claim_batch(
        ctx,
        stage="outline_inheritance",
        profile={"summary": "test"},
        chunk=chunk,
    )

    assert batch is not None
    assert batch.claims[0].state_before == ""
    assert batch.claims[0].state_after == ""
    assert batch.claims[0].event_type == ""


def test_init_coherence_claim_cache_ignores_legacy_stage_scoped_file(tmp_path: Any) -> None:
    storage = FileSystemStorage(tmp_path)
    layout = ProjectLayout(storage.ensure_project_dir("project"))
    ctx = SimpleNamespace(storage=storage, layout=layout)
    profile = {"summary": "test"}
    chunk = InitArtifactChunk(
        artifact="outline",
        chunk_id="outline_1",
        source_path="/chapters/1",
        source_field="chapters",
        chapter_numbers=[1],
        payload={"chapter_number": 1},
    )
    cache_info = _claim_batch_cache_paths(
        ctx,
        stage="outline_inheritance",
        profile=profile,
        chunk=chunk,
    )
    assert cache_info is not None
    primary_path, profile_hash, chunk_hash = cache_info
    storage.save_json(
        primary_path,
        {
            "schema_version": 1,
            "stage": "outline_inheritance",
            "chunk_id": "outline_1",
            "profile_hash": profile_hash,
            "chunk_hash": chunk_hash,
            "claims": [
                {
                    "claim_id": "legacy_claim",
                    "artifact": "outline",
                    "source_path": "/chapters/1",
                    "claim_text": "旧缓存仍可读取。",
                    "evidence": "旧缓存",
                }
            ],
        },
    )

    claims = _load_cached_claim_batch(
        ctx,
        stage="outline_inheritance",
        profile=profile,
        chunk=chunk,
    )

    assert claims is None


async def test_init_coherence_candidate_adjudication_runs_limited_parallel_batches() -> None:
    settings = SimpleNamespace(
        init_coherence_llm_candidate_batch_size=1,
        init_coherence_llm_candidate_max_parallel=2,
        init_coherence_block_min_severity="high",
        temp_adjudicate_init_conflict_candidates=0.1,
    )
    ctx = _ParallelCallCtx(
        settings=settings,
        response={
            "verdict": "accept",
            "issues": [],
            "source_refs": [],
            "repair_scope": [],
            "preserve": [],
            "change_intent": "",
            "blocked": False,
            "summary": "ok",
        },
    )
    claims = [
        _coherence_claim_model_validate(
            {
                "claim_id": f"claim_{number}",
                "artifact": "outline",
                "source_path": f"/chapters/{number}",
                "source_field": "chapters",
                "chapter_numbers": [number],
                "subject_ids": ["沈知微"],
                "axis": "event",
                "claim_type": "event",
                "claim_text": f"第{number}章推进诊断。",
                "evidence": "诊断推进",
            }
        )
        for number in range(1, 4)
    ]
    candidates = [
        ConflictCandidate(
            candidate_id=f"cand_{index}",
            candidate_type="same_subject_axis",
            reason="test",
            claim_ids=[claim.claim_id],
            claims=[claim],
        )
        for index, claim in enumerate(claims, start=1)
    ]

    report = await _adjudicate_candidates(
        ctx,
        stage="outline_inheritance",
        repair_artifact="outline",
        profile={"summary": "test"},
        candidates=candidates,
        claims_count=len(claims),
        degraded_memory=False,
    )

    assert report.candidate_count == 3
    assert report.verdict == "accept"
    assert ctx.max_active_calls == 2
    batch_events = [
        event for event in ctx.events if event[0].startswith("adjudicate_init_conflict_candidates_")
    ]
    assert sorted(event[1]["batch"] for event in batch_events if "batch" in event[1]) == [1, 2, 3]
    assert batch_events[-1][1]["max_parallel"] == 2
    assert all("entity_catalog" not in context["coherence_profile"] for context in ctx.contexts)
    assert all("claims" not in context["candidates"][0] for context in ctx.contexts)
    assert all(len(context["claim_catalog"]) == 1 for context in ctx.contexts)
    assert {next(iter(context["claim_catalog"])) for context in ctx.contexts} == {
        "claim_1",
        "claim_2",
        "claim_3",
    }


async def test_init_coherence_ambiguous_adjudication_requires_human_review() -> None:
    settings = SimpleNamespace(
        init_coherence_llm_candidate_batch_size=8,
        init_coherence_llm_candidate_max_parallel=1,
        init_coherence_block_min_severity="high",
        temp_adjudicate_init_conflict_candidates=0.1,
    )
    ctx = _ParallelCallCtx(
        settings=settings,
        response={
            "verdict": "defer",
            "issues": [],
            "source_refs": [],
            "repair_scope": [],
            "preserve": [],
            "change_intent": "",
            "blocked": False,
            "summary": "证据不足。",
        },
    )
    claim = _coherence_claim_model_validate(
        {
            "claim_id": "claim_uncertain",
            "artifact": "outline",
            "source_path": "/chapters/0/goal",
            "source_field": "goal",
            "chapter_numbers": [1],
            "claim_text": "线索可能只是角色误解。",
            "evidence": "角色误解。",
        }
    )
    candidate = ConflictCandidate(
        candidate_id="cand_uncertain",
        candidate_type="structural_overlap",
        reason="test",
        claim_ids=[claim.claim_id],
        claims=[claim],
    )

    report = await _adjudicate_candidates(
        ctx,
        stage="outline_inheritance",
        repair_artifact="outline",
        profile={"summary": "test"},
        candidates=[candidate],
        claims_count=1,
        degraded_memory=False,
    )

    assert report.verdict == "defer"
    assert report.blocked is True
    assert report.issues[0]["issue_type"] == "semantic_judgement_uncertain"


async def test_init_coherence_candidate_adjudication_reuses_completed_batches(
    tmp_path: Path,
) -> None:
    settings = SimpleNamespace(
        init_coherence_llm_candidate_batch_size=1,
        init_coherence_llm_candidate_max_parallel=2,
        init_coherence_block_min_severity="high",
        temp_adjudicate_init_conflict_candidates=0.1,
    )
    ctx = _ParallelCallCtx(
        settings=settings,
        response={
            "verdict": "accept",
            "issues": [],
            "source_refs": [],
            "repair_scope": [],
            "preserve": [],
            "change_intent": "",
            "blocked": False,
            "summary": "ok",
        },
    )
    ctx.storage = FileSystemStorage(tmp_path)
    ctx.layout = ProjectLayout(ctx.storage.ensure_project_dir("project"))
    claim = _coherence_claim_model_validate(
        {
            "claim_id": "claim_cached",
            "artifact": "outline",
            "source_path": "/chapters/1",
            "source_field": "chapters",
            "chapter_numbers": [1],
            "subject_ids": ["沈知微"],
            "axis": "event",
            "claim_type": "event",
            "claim_text": "第1章推进诊断。",
            "evidence": "诊断推进",
        }
    )
    candidate = ConflictCandidate(
        candidate_id="cand_cached",
        candidate_type="same_subject_axis",
        reason="test",
        claim_ids=[claim.claim_id],
        claims=[claim],
    )
    first = await _adjudicate_candidates(
        ctx,
        stage="outline_inheritance",
        repair_artifact="outline",
        profile={"summary": "test"},
        candidates=[candidate],
        claims_count=1,
        degraded_memory=False,
    )
    resumed_ctx = _ParallelCallCtx(settings=settings, response=ctx.response)
    resumed_ctx.storage = ctx.storage
    resumed_ctx.layout = ctx.layout
    second = await _adjudicate_candidates(
        resumed_ctx,
        stage="outline_inheritance",
        repair_artifact="outline",
        profile={"summary": "test"},
        candidates=[candidate],
        claims_count=1,
        degraded_memory=False,
    )

    assert first.model_dump(exclude={"created_at"}) == second.model_dump(exclude={"created_at"})
    assert ctx.call_count == 1
    assert resumed_ctx.call_count == 0
    batch_events = [
        payload
        for step, payload in [*ctx.events, *resumed_ctx.events]
        if step == "adjudicate_init_conflict_candidates_1_1"
    ]
    assert [payload["cached"] for payload in batch_events] == [False, True]


def test_adjudication_normalization_preserves_model_stage_point_issue() -> None:
    stage_claim = _coherence_claim_model_validate(
        {
            "claim_id": "char_arc_0_ms3",
            "artifact": "blueprint",
            "source_path": "/character_arcs/0/milestones/2",
            "source_field": "milestones",
            "chapter_numbers": [41, 60],
            "chapter_range": {"start": 41, "end": 60},
            "subject_ids": ["陆深", "沈知微"],
            "subject_text": "陆深与沈知微的协作",
            "axis": "knowledge",
            "claim_type": "state",
            "claim_text": "陆深发现隐疾根源并改善消毒流程，从认知穿刺转向价值观重塑。",
            "state_after": "接纳执念价值",
            "evidence": "阶段性深化。",
        }
    )
    point_claim = _coherence_claim_model_validate(
        {
            "claim_id": "blueprint_kp_006",
            "artifact": "blueprint",
            "source_path": "/key_turning_points/0:8",
            "source_field": "key_turning_points",
            "chapter_numbers": [42],
            "chapter_range": {"start": 42, "end": 42},
            "subject_ids": ["陆深", "沈知微"],
            "subject_text": "陆深的隐疾与执念",
            "axis": "knowledge",
            "claim_type": "payoff",
            "claim_text": "陆深发现左胸隐痛根源是自我苛责科研执念。",
            "state_after": "知晓隐疾源于自我苛责执念",
            "payoff_id": "core_suspense_resolution",
            "payoff_kind": "information",
            "irreversible": True,
            "evidence": "核心悬疑揭示。",
        }
    )
    candidate = ConflictCandidate(
        candidate_id="cand_0076",
        candidate_type="same_subject_axis",
        reason="同一主体与状态轴在不同来源中出现。",
        claim_ids=["char_arc_0_ms3", "blueprint_kp_006"],
        claims=[stage_claim, point_claim],
    )

    normalized = _normalize_candidate_adjudication_response(
        {
            "verdict": "needs_repair",
            "blocked": False,
            "issues": [
                {
                    "id": "issue_1",
                    "candidate_ids": ["cand_0076"],
                    "severity": "high",
                    "description": "阶段顺序矛盾。",
                    "repair_scope": [
                        {
                            "artifact": "blueprint",
                            "chapters": [42],
                            "fields": ["chapter_range", "source_path"],
                        }
                    ],
                }
            ],
            "repair_scope": [
                {"artifact": "blueprint", "chapters": [42], "fields": ["chapter_range"]}
            ],
        },
        [candidate],
    )

    assert normalized["verdict"] == "needs_repair"
    assert len(normalized["issues"]) == 1
    assert normalized["issues"][0]["candidate_ids"] == ["cand_0076"]
    assert normalized["repair_scope"]


def test_adjudication_normalization_does_not_semantically_accept_llm_issues() -> None:
    synopsis_claim = _coherence_claim_model_validate(
        {
            "claim_id": "blueprint_synopsis_001",
            "artifact": "blueprint",
            "source_path": "/synopsis",
            "source_field": "synopsis",
            "subject_ids": ["陆子深", "沈知微"],
            "subject_text": "陆子深与沈知微",
            "claim_type": "event",
            "claim_text": "陆子深在奶奶敦促下就诊于中医沈知微。",
            "evidence": "奶奶敦促下就诊于中医沈知微",
        }
    )
    arc_claim = _coherence_claim_model_validate(
        {
            "claim_id": "outline_char_milestone1_001",
            "artifact": "blueprint",
            "source_path": "/character_arcs/0:4",
            "source_field": "character_arcs",
            "chapter_numbers": [1, 20],
            "chapter_range": {"start": 1, "end": 20},
            "subject_ids": ["陆子深", "沈知微"],
            "subject_text": "陆子深与沈知微的初遇",
            "claim_type": "event",
            "claim_text": "陆子深先看西医报告未见异常，在奶奶督促下初遇沈知微。",
            "evidence": "初遇沈知微，三指搭脉后决定调理。",
        }
    )
    candidate = ConflictCandidate(
        candidate_id="sem_0011",
        candidate_type="semantic_similarity",
        reason="两个叙事事实高度相似。",
        claim_ids=["blueprint_synopsis_001", "outline_char_milestone1_001"],
        claims=[synopsis_claim, arc_claim],
    )

    normalized = _normalize_candidate_adjudication_response(
        {
            "verdict": "needs_repair",
            "blocked": False,
            "issues": [
                {
                    "id": "issue_1",
                    "candidate_ids": ["sem_0011"],
                    "severity": "high",
                    "description": "synopsis 与 character_arcs 对初遇就诊的状态顺序冲突。",
                    "repair_scope": [],
                }
            ],
            "repair_scope": [],
        },
        [candidate],
    )

    assert normalized["verdict"] == "needs_repair"
    assert normalized["issues"][0]["id"] == "sem_0011_issue_1"
    assert normalized["issues"][0]["repair_scope"] == [
        {
            "artifact": "blueprint",
            "chapters": [1, 20],
            "fields": ["character_arcs"],
            "operation": "field_replace",
            "issue_ids": ["sem_0011_issue_1"],
        }
    ]
    assert normalized["repair_scope"] == normalized["issues"][0]["repair_scope"]


def test_adjudication_normalization_namespaces_issue_ids_and_infers_scope() -> None:
    volume_claim = _coherence_claim_model_validate(
        {
            "claim_id": "outline_vol1_003",
            "artifact": "blueprint",
            "source_path": "/volumes/0",
            "source_field": "milestone_targets",
            "chapter_numbers": [1, 20],
            "chapter_range": {"start": 1, "end": 20},
            "subject_ids": ["主角A", "主角B"],
            "subject_text": "主角A与主角B的关系",
            "axis": "relationship_status",
            "claim_type": "event",
            "claim_text": "两人在第一卷完成初识。",
            "state_before": "陌生",
            "state_after": "初识",
            "evidence": "第一卷完成初识",
        }
    )
    turning_point_claim = _coherence_claim_model_validate(
        {
            "claim_id": "outline_ch030_001",
            "artifact": "blueprint",
            "source_path": "/key_turning_points/1",
            "source_field": "key_turning_points",
            "chapter_numbers": [30],
            "chapter_range": {"start": 30, "end": 30},
            "subject_ids": ["主角A", "主角B"],
            "subject_text": "主角A与主角B的关系",
            "axis": "relationship_status",
            "claim_type": "relationship",
            "claim_text": "第30章关系从初遇排斥转向试探性靠近。",
            "state_before": "初遇排斥",
            "state_after": "试探性靠近",
            "evidence": "关系从初遇排斥转向试探性靠近",
        }
    )
    candidate = ConflictCandidate(
        candidate_id="cand_0005",
        candidate_type="same_subject_axis",
        reason="同一主体与状态轴在不同来源中出现。",
        claim_ids=["outline_vol1_003", "outline_ch030_001"],
        claims=[volume_claim, turning_point_claim],
    )

    normalized = _normalize_candidate_adjudication_response(
        {
            "verdict": "needs_repair",
            "blocked": False,
            "issues": [
                {
                    "id": "issue_1",
                    "candidate_ids": ["cand_0005"],
                    "severity": "high",
                    "description": "key_turning_points 中 state_before 与第一卷关系状态矛盾。",
                    "resolution": "修正 key_turning_points 的关系起点表述。",
                    "repair_scope": [],
                }
            ],
            "repair_scope": [],
        },
        [candidate],
    )

    assert normalized["issues"][0]["id"] == "cand_0005_issue_1"
    assert normalized["issues"][0]["repair_scope"] == [
        {
            "artifact": "blueprint",
            "chapters": [30],
            "fields": ["key_turning_points"],
            "operation": "field_replace",
            "issue_ids": ["cand_0005_issue_1"],
        }
    ]
    assert normalized["repair_scope"] == normalized["issues"][0]["repair_scope"]


def test_adjudication_normalization_limits_repair_scope_to_gate_artifact() -> None:
    blueprint_claim = _coherence_claim_model_validate(
        {
            "claim_id": "blueprint_phase_claim",
            "artifact": "blueprint",
            "source_path": "/narrative_phases/0/description",
            "source_field": "narrative_phases",
            "chapter_numbers": [5],
            "subject_ids": ["主角A"],
            "axis": "relationship_status",
            "claim_type": "state",
            "claim_text": "蓝图要求第5章关系仍在试探。",
            "state_after": "试探",
            "evidence": "关系仍在试探",
        }
    )
    outline_claim = _coherence_claim_model_validate(
        {
            "claim_id": "outline_goal_claim",
            "artifact": "outline",
            "source_path": "/chapters/4/goal",
            "source_field": "goal",
            "chapter_numbers": [5],
            "subject_ids": ["主角A"],
            "axis": "relationship_status",
            "claim_type": "state",
            "claim_text": "第5章已经完成结盟。",
            "state_after": "结盟",
            "evidence": "完成结盟",
        }
    )
    candidate = ConflictCandidate(
        candidate_id="cand_scope",
        candidate_type="same_subject_axis",
        reason="test",
        claim_ids=["blueprint_phase_claim", "outline_goal_claim"],
        claims=[blueprint_claim, outline_claim],
    )

    normalized = _normalize_candidate_adjudication_response(
        {
            "verdict": "needs_repair",
            "blocked": False,
            "issues": [
                {
                    "id": "issue_1",
                    "candidate_ids": ["cand_scope"],
                    "severity": "high",
                    "description": "outline 误译了蓝图关系阶段。",
                    "repair_scope": [
                        {
                            "artifact": "blueprint",
                            "chapters": [5],
                            "fields": ["narrative_phases"],
                            "operation": "field_replace",
                            "issue_ids": ["issue_1"],
                            "source_path": "/narrative_phases/0/description",
                        }
                    ],
                }
            ],
            "repair_scope": [
                {
                    "artifact": "blueprint",
                    "chapters": [5],
                    "fields": ["narrative_phases"],
                    "issue_ids": ["issue_1"],
                }
            ],
        },
        [candidate],
        repair_artifact="outline",
    )

    assert normalized["issues"][0]["repair_scope"] == [
        {
            "artifact": "outline",
            "chapters": [5],
            "fields": ["goal"],
            "operation": "field_replace",
            "issue_ids": ["cand_scope_issue_1"],
        }
    ]
    assert normalized["repair_scope"] == normalized["issues"][0]["repair_scope"]


def test_adjudication_normalization_infers_outline_scope_from_container_claim() -> None:
    outline_claim = _coherence_claim_model_validate(
        {
            "claim_id": "outline_stream_49_56_claim_001",
            "artifact": "outline",
            "source_path": "/chapters/48:56",
            "source_field": "chapters",
            "chapter_numbers": [52],
            "chapter_range": {"start": 49, "end": 56},
            "subject_ids": ["主角A", "主角B"],
            "subject_text": "主角A与主角B",
            "axis": "relationship_status",
            "claim_type": "relationship",
            "claim_text": "第52章两人完成结盟并互相托付。",
            "state_after": "结盟",
            "evidence": "第52章大纲",
        }
    )
    candidate = ConflictCandidate(
        candidate_id="cand_outline",
        candidate_type="same_subject_axis",
        reason="同一关系轴冲突。",
        claim_ids=[outline_claim.claim_id],
        claims=[outline_claim],
    )

    normalized = _normalize_candidate_adjudication_response(
        {
            "verdict": "needs_repair",
            "blocked": False,
            "issues": [
                {
                    "id": "issue_1",
                    "candidate_ids": ["cand_outline"],
                    "severity": "high",
                    "description": "第52章关系状态与上游阶段目标冲突。",
                    "repair_scope": [],
                }
            ],
            "repair_scope": [],
        },
        [candidate],
        repair_artifact="outline",
    )

    scope = normalized["issues"][0]["repair_scope"][0]
    assert scope["artifact"] == "outline"
    assert scope["chapters"] == [52]
    assert set(scope["fields"]) == {"beats_summary", "goal", "main_plot_points", "notes"}
    assert normalized["repair_scope"] == normalized["issues"][0]["repair_scope"]
    assert has_repair_scope(normalized, artifact="outline", min_severity="high") is True


def test_adjudication_normalization_infers_chapter_contract_scope_from_container_claim() -> None:
    contract_claim = _coherence_claim_model_validate(
        {
            "claim_id": "chapter_contracts_89_96_cc89_96_009",
            "artifact": "chapter_contracts",
            "source_path": "/chapter_contracts/88:96",
            "source_field": "chapter_contracts",
            "chapter_numbers": [95],
            "chapter_range": {"start": 89, "end": 96},
            "subject_ids": ["玄策", "沈清漪"],
            "subject_text": "玄策与沈清漪",
            "axis": "identity_recognition",
            "claim_type": "state",
            "claim_text": "沈清漪将残玉放在玄策面前，玄策确认沈清漪就是青阳会盟的少年，两人相拥而泣。",
            "state_after": "玄策确认沈清漪即青阳会盟少年",
            "payoff_id": "心意互通与青阳相认",
            "irreversible": True,
            "evidence": "第95章章节契约",
            "cognitive_subjects": ["玄策", "沈清漪"],
            "cognitive_object": "沈清漪即青阳会盟少年",
            "cognitive_level": "acknowledged",
            "action_level": "revealed",
            "reader_awareness": "full",
            "character_knowledge_coverage": {"玄策": "full", "沈清漪": "full"},
            "cognitive_chapter": 95,
            "public_reveal_chapter": 115,
            "foreshadow_chapters": [1, 18, 89, 93],
        }
    )
    narrative_claim = _coherence_claim_model_validate(
        {
            "claim_id": "narrative_contract_promise_plan_7_9_c001",
            "artifact": "narrative_contract",
            "source_path": "/promise_plan/7/9",
            "source_field": "promise_plan",
            "chapter_numbers": [35, 62],
            "chapter_range": {"start": 35, "end": 62},
            "subject_ids": ["玄策", "沈清漪"],
            "subject_text": "心意互通与青阳相认",
            "axis": "payoff",
            "claim_type": "promise",
            "claim_text": "承诺「心意互通与青阳相认」设于第35章、兑于第62章。",
            "payoff_id": "心意互通与青阳相认",
            "evidence": "narrative_contract promise_plan",
        }
    )
    candidate = ConflictCandidate(
        candidate_id="cand_0071",
        candidate_type="semantic_similarity",
        reason="同一相认 payoff 在契约与叙事契约中冲突。",
        claim_ids=[narrative_claim.claim_id, contract_claim.claim_id],
        claims=[narrative_claim, contract_claim],
    )

    normalized = _normalize_candidate_adjudication_response(
        {
            "verdict": "needs_repair",
            "blocked": False,
            "issues": [
                {
                    "id": "issue_1",
                    "candidate_ids": ["cand_0071"],
                    "severity": "high",
                    "description": "第95章章节契约相认 payoff 与上游第62章兑现计划冲突。",
                    "repair_scope": [],
                }
            ],
            "repair_scope": [],
        },
        [candidate],
        repair_artifact="chapter_contracts",
    )

    scope = normalized["issues"][0]["repair_scope"][0]
    fields = set(scope["fields"])
    assert normalized["verdict"] == "needs_repair"
    assert scope["artifact"] == "chapter_contracts"
    assert scope["chapters"] == [95]
    assert {"promise_ops", "knowledge_ops", "cognitive_constraints"}.issubset(fields)
    assert {"relationship_ops", "item_ops", "required_progressions"}.issubset(fields)
    assert scope["issue_ids"] == ["cand_0071_issue_1"]
    assert normalized["repair_scope"] == normalized["issues"][0]["repair_scope"]
    assert has_repair_scope(normalized, artifact="chapter_contracts", min_severity="high") is True


def test_adjudication_normalization_accepts_repair_scope_chapter_range() -> None:
    claim = _coherence_claim_model_validate(
        {
            "claim_id": "contract_ch052_claim",
            "artifact": "chapter_contracts",
            "source_path": "/chapter_contracts/48:56",
            "source_field": "chapter_contracts",
            "chapter_numbers": [52],
            "chapter_range": {"start": 49, "end": 56},
            "subject_ids": ["主角A"],
            "axis": "knowledge",
            "claim_type": "knowledge",
            "claim_text": "第52章提前公开核心认知。",
            "evidence": "第52章契约",
            "cognitive_subjects": ["主角A"],
            "cognitive_object": "核心认知",
            "cognitive_level": "confirmed",
            "action_level": "revealed",
            "cognitive_chapter": 52,
        }
    )
    candidate = ConflictCandidate(
        candidate_id="cand_range",
        candidate_type="premature_public_reveal",
        reason="test",
        claim_ids=[claim.claim_id],
        claims=[claim],
    )

    normalized = _normalize_candidate_adjudication_response(
        {
            "verdict": "needs_repair",
            "blocked": False,
            "issues": [
                {
                    "id": "issue_range",
                    "candidate_ids": ["cand_range"],
                    "severity": "high",
                    "description": "第49-56章契约窗口存在提前公开。",
                    "repair_scope": [
                        {
                            "artifact": "chapter_contracts",
                            "chapter_range": {"start": 49, "end": 56},
                            "fields": ["knowledge_ops", "cognitive_constraints"],
                        }
                    ],
                }
            ],
            "repair_scope": [],
        },
        [candidate],
        repair_artifact="chapter_contracts",
    )

    assert normalized["issues"][0]["repair_scope"] == [
        {
            "artifact": "chapter_contracts",
            "chapters": [49, 50, 51, 52, 53, 54, 55, 56],
            "fields": ["knowledge_ops", "cognitive_constraints"],
            "operation": "field_replace",
            "issue_ids": ["cand_range_issue_range"],
        }
    ]
    assert normalized["repair_scope"] == normalized["issues"][0]["repair_scope"]


def test_adjudication_normalization_drops_unanchored_hallucinated_issues() -> None:
    claim = _coherence_claim_model_validate(
        {
            "claim_id": "outline_claim",
            "artifact": "outline",
            "source_path": "/chapters/0/goal",
            "source_field": "goal",
            "chapter_numbers": [1],
            "claim_text": "第1章建立目标。",
            "evidence": "建立目标",
        }
    )
    candidate = ConflictCandidate(
        candidate_id="cand_real",
        candidate_type="same_subject_axis",
        reason="test",
        claim_ids=["outline_claim"],
        claims=[claim],
    )

    normalized = _normalize_candidate_adjudication_response(
        {
            "verdict": "needs_repair",
            "blocked": True,
            "issues": [
                {
                    "id": "issue_1",
                    "candidate_ids": ["cand_fake"],
                    "severity": "critical",
                    "description": "不存在的候选问题。",
                    "repair_scope": [{"artifact": "outline", "chapters": [1], "fields": ["goal"]}],
                }
            ],
            "repair_scope": [],
        },
        [candidate],
        repair_artifact="outline",
    )

    assert normalized["verdict"] == "ambiguous"
    assert normalized["issues"] == []
    assert normalized["repair_scope"] == []
    assert normalized["blocked"] is False


def test_claim_ledger_focus_recheck_keeps_global_active_context(tmp_path) -> None:
    storage = FileSystemStorage(tmp_path)
    layout = ProjectLayout(storage.project_dir("claim_ledger_demo"))
    ctx = SimpleNamespace(storage=storage, layout=layout)
    chapter_three = _coherence_claim_model_validate(
        {
            "claim_id": "outline_ch003_married",
            "artifact": "outline",
            "source_path": "/chapters/2/goal",
            "source_field": "goal",
            "chapter_numbers": [3],
            "subject_ids": ["主角A", "主角B"],
            "subject_text": "主角A与主角B的关系",
            "axis": "relationship_status",
            "claim_type": "state",
            "claim_text": "第3章完成婚礼，关系进入已婚状态。",
            "state_after": "已婚",
            "irreversible": True,
            "temporality": "actual",
            "evidence": "完成婚礼",
            "confidence": 0.92,
        }
    )
    stale_chapter_five = _coherence_claim_model_validate(
        {
            "claim_id": "outline_ch005_proposal",
            "artifact": "outline",
            "source_path": "/chapters/4/goal",
            "source_field": "goal",
            "chapter_numbers": [5],
            "subject_ids": ["主角A", "主角B"],
            "subject_text": "主角A与主角B的关系",
            "axis": "relationship_status",
            "claim_type": "state",
            "claim_text": "第5章以首次求婚推进关系确认。",
            "state_after": "待求婚",
            "temporality": "actual",
            "evidence": "再次求婚",
            "confidence": 0.9,
        }
    )
    fixed_chapter_five = _coherence_claim_model_validate(
        {
            "claim_id": "outline_ch005_vows",
            "artifact": "outline",
            "source_path": "/chapters/4/goal",
            "source_field": "goal",
            "chapter_numbers": [5],
            "subject_ids": ["主角A", "主角B"],
            "subject_text": "主角A与主角B的关系",
            "axis": "relationship_status",
            "claim_type": "state",
            "claim_text": "第5章兑现婚后誓约。",
            "state_after": "已婚承诺推进",
            "temporality": "actual",
            "evidence": "婚后誓约兑现",
            "confidence": 0.91,
        }
    )
    contract_chapter_five = _coherence_claim_model_validate(
        {
            "claim_id": "contract_ch005_alias_drift",
            "artifact": "chapter_contracts",
            "source_path": "/chapter_contracts/4",
            "source_field": "cognitive_constraints",
            "chapter_numbers": [5],
            "subject_ids": ["主角A", "主角B"],
            "subject_text": "主角A与主角B的关系",
            "axis": "relationship_status",
            "claim_type": "state",
            "claim_text": "第5章契约误写为首次确认关系。",
            "state_after": "待求婚",
            "temporality": "actual",
            "evidence": "契约别名漂移",
            "confidence": 0.88,
        }
    )

    adjudication_metadata = {
        "entity_adjudication_status": "adjudicated",
        "entity_reference_revision": entity_catalog_revision(None),
    }
    chapter_three, stale_chapter_five, fixed_chapter_five, contract_chapter_five = (
        claim.model_copy(update={"metadata": adjudication_metadata})
        for claim in (
            chapter_three,
            stale_chapter_five,
            fixed_chapter_five,
            contract_chapter_five,
        )
    )

    _persist_claims(
        ctx,
        [chapter_three, stale_chapter_five],
        stage="outline_inheritance",
        artifacts={"outline": {"chapters": []}},
        focus_chapters=None,
    )
    ledger = _persist_claims(
        ctx,
        [fixed_chapter_five],
        stage="outline_inheritance",
        artifacts={"outline": {"chapters": []}},
        focus_chapters=[5],
    )
    ledger = _persist_claims(
        ctx,
        [contract_chapter_five],
        stage="contract_coherence",
        artifacts={"chapter_contracts": {"chapter_contracts": []}},
        focus_chapters=None,
    )

    retrieval_claims = _claims_for_retrieval(
        [fixed_chapter_five],
        ledger,
        stage="outline_inheritance",
        artifact_keys={"outline"},
        focus_chapters=[5],
    )
    retrieval_ids = {claim.claim_id for claim in retrieval_claims}
    stored_ledger = storage.load_json(layout.memory_dir / CLAIM_LEDGER_JSON)

    assert retrieval_ids == {"outline_ch003_married", "outline_ch005_vows"}
    assert "contract_ch005_alias_drift" not in retrieval_ids
    assert stored_ledger["claims_by_id"]["outline_ch005_proposal"]["status"] == "invalidated"
    assert stored_ledger["stages"]["outline_inheritance"]["active_claim_count"] == 2


def test_claim_ledger_reusable_stage_rejects_claims_overwritten_by_other_stage(tmp_path) -> None:
    storage = FileSystemStorage(tmp_path)
    layout = ProjectLayout(storage.project_dir("claim_ledger_stage_isolation"))
    ctx = SimpleNamespace(storage=storage, layout=layout)
    artifacts = {"outline": {"chapters": [{"chapter_number": 1, "goal": "目标"}]}}
    first = _coherence_claim_model_validate(
        {
            "claim_id": "shared_claim",
            "artifact": "outline",
            "source_path": "/chapters/0/goal",
            "source_field": "goal",
            "chapter_numbers": [1],
            "claim_text": "第一阶段 claim。",
            "evidence": "第一阶段",
        }
    )
    second = _coherence_claim_model_validate(
        {
            "claim_id": "shared_claim",
            "artifact": "outline",
            "source_path": "/chapters/0/goal",
            "source_field": "goal",
            "chapter_numbers": [1],
            "claim_text": "第二阶段 claim。",
            "evidence": "第二阶段",
        }
    )

    _persist_claims(
        ctx,
        [first],
        stage="outline_inheritance",
        artifacts=artifacts,
        focus_chapters=None,
    )
    _persist_claims(
        ctx,
        [second],
        stage="contract_coherence",
        artifacts=artifacts,
        focus_chapters=None,
    )

    reusable = _load_reusable_stage_claims(
        ctx,
        stage="outline_inheritance",
        artifacts=artifacts,
        focus_chapters=None,
    )

    assert reusable is None


def test_generic_init_coherence_chunks_are_capped_below_chapter_batches() -> None:
    settings = SimpleNamespace(init_coherence_claim_batch_size=8)
    chunks = _build_artifact_chunks(
        settings,
        {
            "blueprint": {
                "subplot_plan": [{"name": f"subplot-{i}", "chapter_events": []} for i in range(8)]
            }
        },
    )

    assert [len(chunk.payload["items"]) for chunk in chunks] == [3, 3, 2]


def test_init_coherence_focus_after_repair_prefers_applied_patch_chapters() -> None:
    events: list[tuple[str, dict[str, Any]]] = []
    ctx = SimpleNamespace(
        settings=SimpleNamespace(init_coherence_recheck_affected_window=2),
        on_step=lambda name, payload: events.append((name, payload)),
    )
    broad_report = {
        "issues": [
            {
                "id": "contract_broad_scope",
                "severity": "high",
                "description": "旧 report scope 覆盖过宽。",
                "repair_scope": [
                    {
                        "artifact": "chapter_contracts",
                        "chapters": list(range(1, 130)),
                        "fields": ["arc_state"],
                    }
                ],
            }
        ]
    }
    repairs = [
        {
            "artifact": "chapter_contracts",
            "round": 1,
            "status": "applied",
            "patch_count": 4,
            "patches": [
                {"target_id": "chapter_contracts:81:arc_state"},
                {"chapter_number": 106, "path": "/chapter_contracts/105/scene_contract"},
                {"path": "/chapter_contracts/106/continuity_contract"},
                {"target_id": "chapter_contracts:113:payoff"},
            ],
        }
    ]

    focus = _init_coherence_focus_chapters_after_repair(
        ctx,
        report=broad_report,
        artifact="chapter_contracts",
        repairs=repairs,
    )

    assert focus == [
        79,
        80,
        81,
        82,
        83,
        104,
        105,
        106,
        107,
        108,
        109,
        111,
        112,
        113,
        114,
        115,
    ]
    assert 1 not in focus
    assert events[-1][0] == "init_coherence_focus_chapters"
    assert events[-1][1]["source"] == "applied_repair_patches"


def test_split_focus_chapters_for_recheck_preserves_ordered_chunks() -> None:
    settings = SimpleNamespace(init_coherence_recheck_chunk_chapters=5)

    chunks = _split_focus_chapters_for_recheck(
        settings,
        [1, 2, 3, 4, 5, 6, 10, 11, 12, 13, 14, 15, 16],
    )

    assert chunks == [
        [1, 2, 3, 4, 5],
        [6],
        [10, 11, 12, 13, 14],
        [15, 16],
    ]


def test_merge_focus_chunk_reports_keeps_worst_verdict_and_sums_counts() -> None:
    reports = [
        {
            "verdict": "accept",
            "blocked": False,
            "claims_count": 30,
            "candidate_count": 2,
            "adjudicated_candidate_count": 2,
            "extracted_claims_count": 35,
            "active_claims_count": 30,
            "ledger_active_claims_count": 100,
            "exact_candidate_count": 1,
            "semantic_candidate_count": 1,
            "post_filter_drop_count": 0,
            "fully_pushed_down_ratio": 0.5,
            "artifact_hashes": {"outline": "a"},
        },
        {
            "verdict": "needs_repair",
            "blocked": True,
            "issues": [{"id": "issue-1", "severity": "high", "description": "冲突"}],
            "repair_scope": [{"artifact": "chapter_contracts", "chapters": [10]}],
            "claims_count": 40,
            "candidate_count": 3,
            "adjudicated_candidate_count": 3,
            "extracted_claims_count": 42,
            "active_claims_count": 40,
            "ledger_active_claims_count": 120,
            "exact_candidate_count": 2,
            "semantic_candidate_count": 1,
            "post_filter_drop_count": 1,
            "fully_pushed_down_ratio": 1.0,
        },
    ]

    merged = _merge_focus_chunk_reports(
        stage="contract_coherence",
        repair_artifact="chapter_contracts",
        focus_chunks=[[1, 2, 3], [10, 11]],
        reports=reports,
    )

    assert merged["verdict"] == "needs_repair"
    assert merged["blocked"] is True
    assert merged["claims_count"] == 70
    assert merged["candidate_count"] == 5
    assert merged["ledger_active_claims_count"] == 120
    assert merged["retrieval_scope"] == "focus_chunked"
    assert merged["focus_chapters"] == [1, 2, 3, 10, 11]
    assert merged["focus_chunks"] == [[1, 2, 3], [10, 11]]
    assert merged["issues"] == [{"id": "issue-1", "severity": "high", "description": "冲突"}]
