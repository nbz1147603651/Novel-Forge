"""Tests for task output adapters."""

from __future__ import annotations

import pytest

from novel_forge.common.constants import TaskType
from novel_forge.core.format_contracts import validate_json_output_contract
from novel_forge.core.parsing.response_schemas import validate_response_schema
from novel_forge.pipeline.long.services.task_output_adapters import (
    TaskOutputAdapterResult,
    apply_task_output_adapter,
)
from novel_forge.pipeline.long.services.task_semantic_contracts import (
    TaskSemanticContractError,
    validate_task_semantic_contract,
)


def test_enrich_character_adapter_flattens_nested_appearance() -> None:
    data = {
        "name": "张三",
        "appearance": {"hair": "black", "eyes": "brown"},
        "personality": "introverted",
        "backstory": "orphaned young",
        "arc": "growth arc",
    }
    result = apply_task_output_adapter(data, TaskType.ENRICH_CHARACTER)
    assert result.changed is True
    assert result.adapter == "enrich_character_text_shape_normalizer"
    assert isinstance(data["appearance"], str)
    assert "hair: black" in data["appearance"]
    assert "eyes: brown" in data["appearance"]


@pytest.mark.parametrize(
    ("task_type", "payload_key"),
    [
        (TaskType.PLAN_CHAPTER, None),
        (TaskType.PLAN_CHAPTER_SCENES, "scene_plan"),
    ],
)
def test_plan_adapter_removes_input_guidance_and_inherited_requirement_fields(
    task_type: TaskType,
    payload_key: str | None,
) -> None:
    plan = {
        "guidance_requirements": [
            {
                "requirement_id": "chapter_contract.required_progressions:0",
                "text": "必须以行动推进。",
            }
        ],
        "scene_intents": [],
        "world_rule_applications": [],
        "required_literals": [
            {
                "contract_id": "password",
                "literal": "0707",
                "scene_id": "scene_02",
                "reason": "密码不可改写",
                "placement_hint": "输入门禁时",
                "requirement": {
                    "source": "chapter_contract.required_literals:0",
                    "scope": "scene_02",
                    "satisfaction": "literal",
                    "status": "pending",
                    "evidence": "",
                    "source_text_hash": "",
                    "requirement_id": "chapter_contract.required_literals:0",
                    "text": "0707",
                },
            }
        ],
        "cross_scene_intent": {
            "cross_scene_references": [
                {
                    "from_scene": "scene_01",
                    "to_scene": "scene_02",
                    "ref_type": "callback",
                    "description": "先取得密码，再打开门禁。",
                    "requirement": {
                        "source": "chapter_contract.required_progressions:0",
                        "scope": "chapter:1",
                        "satisfaction": "narrative",
                        "status": "pending",
                        "evidence": "",
                        "source_text_hash": "",
                        "requirement_id": "chapter_contract.required_progressions:0",
                        "text": "先取得密码。",
                    },
                }
            ],
            "pacing_curve": [],
        },
    }
    data = {payload_key: plan} if payload_key else plan

    result = apply_task_output_adapter(data, task_type)

    assert result.changed is True
    assert result.adapter == "plan_requirement_envelope_normalizer"
    assert "guidance_requirements" not in plan
    for requirement in (
        plan["required_literals"][0]["requirement"],
        plan["cross_scene_intent"]["cross_scene_references"][0]["requirement"],
    ):
        assert "requirement_id" not in requirement
        assert "text" not in requirement
        assert requirement["source"]
        assert requirement["satisfaction"] in {"narrative", "literal"}
    assert result.metadata == {
        "task": task_type.value,
        "input_guidance_removed": True,
        "duplicate_requirement_fields_removed": 4,
        "changed_paths": [
            "guidance_requirements",
            "cross_scene_intent.cross_scene_references[0].requirement.requirement_id",
            "cross_scene_intent.cross_scene_references[0].requirement.text",
            "required_literals[0].requirement.requirement_id",
            "required_literals[0].requirement.text",
        ],
    }


def test_plan_adapter_keeps_unknown_requirement_fields_for_closed_schema_rejection() -> None:
    data = {
        "scene_intents": [],
        "cross_scene_intent": {
            "cross_scene_references": [
                {
                    "from_scene": "scene_01",
                    "to_scene": "scene_02",
                    "ref_type": "callback",
                    "description": "承接。",
                    "requirement": {"unexpected_authority": "model"},
                }
            ],
            "pacing_curve": [],
        },
    }

    result = apply_task_output_adapter(data, TaskType.PLAN_CHAPTER)

    assert result.changed is False
    with pytest.raises(ValueError, match="unexpected_authority"):
        validate_response_schema(data, TaskType.PLAN_CHAPTER)


def test_enrich_character_adapter_flattens_nested_personality() -> None:
    data = {
        "appearance": "tall",
        "personality": {"trait1": "brave", "trait2": "loyal"},
        "backstory": "war veteran",
        "arc": "redemption",
    }
    result = apply_task_output_adapter(data, TaskType.ENRICH_CHARACTER)
    assert result.changed is True
    assert isinstance(data["personality"], str)
    assert "trait1: brave" in data["personality"]
    assert "trait2: loyal" in data["personality"]


def test_enrich_character_adapter_flattens_relationships_nested_values() -> None:
    data = {
        "appearance": "tall",
        "personality": "brave",
        "backstory": "orphan",
        "arc": "hero journey",
        "relationships": {"李四": {"status": "friend", "trust": "high"}},
    }
    result = apply_task_output_adapter(data, TaskType.ENRICH_CHARACTER)
    assert result.changed is True
    assert isinstance(data["relationships"]["李四"], str)


def test_enrich_character_adapter_returns_unchanged_for_already_flat() -> None:
    data = {
        "appearance": "tall and dark",
        "personality": "brave and loyal",
        "backstory": "orphaned young",
        "arc": "from zero to hero",
    }
    result = apply_task_output_adapter(data, TaskType.ENRICH_CHARACTER)
    assert result.changed is False


def test_enrich_character_adapter_result_is_task_output_adapter_result() -> None:
    data = {
        "appearance": {"hair": "black"},
        "personality": "brave",
        "backstory": "orphan",
        "arc": "growth",
    }
    result = apply_task_output_adapter(data, TaskType.ENRICH_CHARACTER)
    assert isinstance(result, TaskOutputAdapterResult)
    assert result.changed is True
    assert "appearance" in result.changed_keys
    assert result.metadata == {"task": TaskType.ENRICH_CHARACTER.value}


def test_enrich_character_adapter_handles_empty_nested_values() -> None:
    data = {
        "appearance": {},
        "personality": [],
        "backstory": None,
        "arc": "growth",
    }
    result = apply_task_output_adapter(data, TaskType.ENRICH_CHARACTER)
    assert result.changed is True
    assert data["appearance"] == ""
    assert data["personality"] == ""
    assert data["backstory"] == ""


def test_enrich_character_adapter_flattens_list_values() -> None:
    data = {
        "appearance": ["tall", "dark-haired", "scarred"],
        "personality": "brave",
        "backstory": "orphan",
        "arc": "growth",
    }
    result = apply_task_output_adapter(data, TaskType.ENRICH_CHARACTER)
    assert result.changed is True
    assert data["appearance"] == "tall；dark-haired；scarred"


def test_candidate_state_delta_adapter_normalizes_description_and_quote_aliases() -> None:
    data = {
        "candidates": [
            {
                "delta_type": "event",
                "description": "沈念卿与陆云峥完成初遇。",
                "quote": "目光精准地落在她身上",
            }
        ]
    }

    result = apply_task_output_adapter(data, TaskType.EXTRACT_CANDIDATE_STATE_DELTAS)

    assert result.changed is True
    assert result.adapter == "candidate_state_delta_alias_normalizer"
    assert data["candidates"][0]["summary"] == "沈念卿与陆云峥完成初遇。"
    assert data["candidates"][0]["evidence"] == [{"quote": "目光精准地落在她身上"}]


def test_candidate_state_delta_adapter_does_not_invent_missing_delta_semantics() -> None:
    data = {"candidates": [{"delta_type": "event", "summary": "", "evidence": []}]}

    apply_task_output_adapter(data, TaskType.EXTRACT_CANDIDATE_STATE_DELTAS)

    assert "proposed_delta" not in data["candidates"][0]
    with pytest.raises(TaskSemanticContractError) as exc_info:
        validate_task_semantic_contract(data, TaskType.EXTRACT_CANDIDATE_STATE_DELTAS)
    assert {issue.path for issue in exc_info.value.issues} == {
        "$.candidates[0].summary",
        "$.candidates[0].proposed_delta",
        "$.candidates[0].evidence",
    }


def test_guard_constraint_adapter_does_not_turn_missing_status_into_unknown() -> None:
    data = {"status": "", "confidence": "0.8", "evidence": "", "notes": ""}

    apply_task_output_adapter(data, TaskType.GUARD_CONSTRAINT_CHECK)

    assert data["status"] == ""
    assert data["confidence"] == 0.8
    with pytest.raises(TaskSemanticContractError) as exc_info:
        validate_task_semantic_contract(data, TaskType.GUARD_CONSTRAINT_CHECK)
    assert {issue.path for issue in exc_info.value.issues} == {"$.status", "$.evidence"}


def test_macro_guard_adapter_does_not_default_missing_dimensions_to_healthy() -> None:
    data = {
        "dimensions": {"outline_alignment": "0.7"},
        "summary": "模型只返回了不完整的宏观检查。",
    }

    apply_task_output_adapter(data, TaskType.MACRO_GUARD_AUDIT)

    assert data["dimensions"] == {"outline_alignment": 0.7}
    assert data["reasoning"] == "模型只返回了不完整的宏观检查。"
    assert "recommended_action" not in data
    assert "drift_score" not in data
    assert "confidence" not in data
    with pytest.raises(TaskSemanticContractError) as exc_info:
        validate_task_semantic_contract(data, TaskType.MACRO_GUARD_AUDIT)
    paths = {issue.path for issue in exc_info.value.issues}
    assert "$.recommended_action" in paths
    assert "$.dimensions.character_arc_consistency" in paths
    assert "$.confidence" in paths


def test_book_consistency_adapter_does_not_invent_repair_semantics() -> None:
    data = {
        "issues": [
            {
                "issue_id": "timeline_1",
                "issue_type": "timeline",
                "chapters_involved": [1, "2"],
                "primary_chapter": "2",
                "location": "第2章",
                "evidence": "第二章将事件写在第一章之前。",
                "description": "时间顺序冲突。",
                "confidence": "0.9",
                "evidence_pairs": [],
                "verification_questions": [],
                "handoff_notes": "",
                "linked_issue_refs": [],
                "paragraph_index": 1,
                "paragraph_span": [1, 2],
            }
        ]
    }

    apply_task_output_adapter(data, TaskType.BOOK_CONSISTENCY_TIMELINE)

    issue = data["issues"][0]
    assert issue["category"] == "timeline"
    assert issue["chapters_involved"] == [1, 2]
    assert issue["primary_chapter"] == 2
    assert issue["confidence"] == 0.9
    for key in ("severity", "suggestion", "fix_mode", "fix_action"):
        assert key not in issue
    with pytest.raises(TaskSemanticContractError) as exc_info:
        validate_task_semantic_contract(data, TaskType.BOOK_CONSISTENCY_TIMELINE)
    paths = {problem.path for problem in exc_info.value.issues}
    assert "$.issues[0].severity" in paths
    assert "$.issues[0].suggestion" in paths
    assert "$.issues[0].fix_mode" in paths
    assert "$.issues[0].fix_action" in paths


def test_init_knowledge_boundaries_adapter_strips_nested_role_leakage() -> None:
    data = {
        "characters": [
            {
                "character_id": "char_1",
                "name": "玄昱",
                "knowledge_boundaries": {
                    "known_facts": ["已知 A"],
                    "suspected": ["怀疑 A"],
                    "misbeliefs": ["误解 A"],
                    "secrets_kept": ["秘密 A"],
                    "sensory_access_rules": ["限制 A"],
                    "character_id": "char_1",
                    "name": "玄昱",
                    "knowledge_boundaries": {
                        "known_facts": ["已知 B"],
                        "suspected": ["怀疑 B"],
                        "misbeliefs": ["误解 B"],
                        "secrets_kept": ["秘密 B"],
                        "sensory_access_rules": ["限制 B"],
                    },
                },
            },
            {
                "character_id": "char_2",
                "name": "沈清漪",
                "knowledge_boundaries": {
                    "known_facts": ["已知 C"],
                    "suspected": ["怀疑 C"],
                    "misbeliefs": ["误解 C"],
                    "secrets_kept": ["秘密 C"],
                    "sensory_access_rules": ["限制 C"],
                },
            },
        ]
    }

    result = apply_task_output_adapter(data, TaskType.INIT_KNOWLEDGE_BOUNDARIES)

    assert result.changed is True
    assert result.adapter == "init_knowledge_boundaries_shape_normalizer"
    first_boundary = data["characters"][0]["knowledge_boundaries"]
    assert set(first_boundary) == {
        "known_facts",
        "suspected",
        "misbeliefs",
        "secrets_kept",
        "sensory_access_rules",
    }
    assert first_boundary["known_facts"] == ["已知 A"]
    validate_response_schema(data, TaskType.INIT_KNOWLEDGE_BOUNDARIES)
    validate_json_output_contract(TaskType.INIT_KNOWLEDGE_BOUNDARIES, data)


def test_init_knowledge_boundaries_adapter_promotes_wrapped_boundary() -> None:
    data = {
        "characters": [
            {
                "character_id": "char_1",
                "name": "玄昱",
                "knowledge_boundaries": {
                    "character_id": "char_1",
                    "name": "玄昱",
                    "knowledge_boundaries": {
                        "known_facts": ["已知 B"],
                        "suspected": ["怀疑 B"],
                        "misbeliefs": ["误解 B"],
                        "secrets_kept": ["秘密 B"],
                        "sensory_access_rules": ["限制 B"],
                    },
                },
            }
        ]
    }

    result = apply_task_output_adapter(data, TaskType.INIT_KNOWLEDGE_BOUNDARIES)

    assert result.changed is True
    assert result.metadata is not None
    assert result.metadata["nested_fields_promoted"] == 5
    boundary = data["characters"][0]["knowledge_boundaries"]
    assert boundary["known_facts"] == ["已知 B"]
    validate_json_output_contract(TaskType.INIT_KNOWLEDGE_BOUNDARIES, data)


def test_init_coherence_ontology_adapter_moves_semantic_top_level_drift() -> None:
    data = {
        "genre_tags": ["生活治愈"],
        "narrative_modes": [{"name": "群像交织"}],
        "project_ontology": {
            "domains": [{"domain": "乡村振兴"}],
            "state_axes": [{"axis": "trust"}],
            "terminology": {"风声": {"definition": "声景锚点"}},
        },
        "locations": {"有风小筑": {"function": "主场景"}},
        "narrative_constraints": {"伏笔管理": ["前三章不揭示完整过去"]},
        "schema_version": "3.0",
    }

    result = apply_task_output_adapter(data, TaskType.INIT_COHERENCE_ONTOLOGY)

    assert result.changed is True
    assert result.adapter == "init_coherence_ontology_shape_normalizer"
    assert set(data) == {"genre_tags", "narrative_modes", "project_ontology"}
    assert data["narrative_modes"] == ["群像交织"]
    assert data["project_ontology"]["domains"] == ["乡村振兴"]
    assert data["project_ontology"]["state_axes"] == ["trust"]
    assert data["project_ontology"]["locations"]["有风小筑"]["function"] == "主场景"
    assert "narrative_constraints" in data["project_ontology"]
    assert result.metadata is not None
    assert result.metadata["moved_semantic_keys"] == ["locations", "narrative_constraints"]
    assert result.metadata["dropped_keys"] == ["schema_version"]
    validate_response_schema(data, TaskType.INIT_COHERENCE_ONTOLOGY)
    validate_json_output_contract(TaskType.INIT_COHERENCE_ONTOLOGY, data)


def test_init_claims_adapter_preserves_invalid_character_knowledge_coverage_for_retry() -> None:
    """Models sometimes paste cognitive_level tokens into the narrower
    character_knowledge_coverage slot. The adapter must preserve the bad values so
    the semantic contract can expose their exact paths to the LLM repair mission.
    """
    data = {
        "claims": [
            {
                "claim_id": "c001",
                "claim_text": "ok",
                "evidence": "e1",
                "character_knowledge_coverage": {"玄策": "full", "沈清漪": "partial"},
            },
            {
                "claim_id": "c002",
                "claim_text": "drift",
                "evidence": "e2",
                "character_knowledge_coverage": {
                    "商溟": "confirmed",
                    "沈清漪": "acknowledged",
                    "玄策": "unknown",
                    "百里隐": "unaware",
                },
            },
            {
                "claim_id": "c003",
                "claim_text": "lowercase",
                "evidence": "e3",
                "character_knowledge_coverage": {"A": "Full", "B": "PARTIAL"},
            },
        ]
    }
    result = apply_task_output_adapter(data, TaskType.EXTRACT_BLUEPRINT_HOLISTIC_CLAIMS)
    assert result.changed is True
    assert result.adapter == "init_claims_shape_normalizer"
    assert result.metadata is not None
    assert result.metadata.get("enum_normalized_fields", 0) >= 1

    assert data["claims"][0]["character_knowledge_coverage"] == {
        "玄策": "full",
        "沈清漪": "partial",
    }
    # Invalid tokens remain visible; only valid mixed-case tokens are normalized.
    assert data["claims"][1]["character_knowledge_coverage"] == {
        "商溟": "confirmed",
        "沈清漪": "acknowledged",
        "玄策": "unknown",
        "百里隐": "unaware",
    }
    # mixed-case valid values are lowercased
    assert data["claims"][2]["character_knowledge_coverage"] == {
        "A": "full",
        "B": "partial",
    }
    with pytest.raises(TaskSemanticContractError) as exc_info:
        validate_task_semantic_contract(data, TaskType.EXTRACT_BLUEPRINT_HOLISTIC_CLAIMS)
    paths = {issue.path for issue in exc_info.value.issues}
    assert "$.claims[1].character_knowledge_coverage.商溟" in paths
    assert "$.claims[1].character_knowledge_coverage.沈清漪" in paths


def test_init_claims_adapter_preserves_invalid_enums_for_retry() -> None:
    data = {
        "claims": [
            {
                "claim_id": "c001",
                "claim_text": "drift",
                "evidence": "e1",
                "cognitive_level": "definitely_wrong",
                "action_level": "spilled",
                "reader_awareness": "kinda_knows",
                "temporality": "fictional",
                "metadata": {},
            },
            {
                "claim_id": "c002",
                "claim_text": "ok",
                "evidence": "e2",
                "cognitive_level": "partial",
                "action_level": "internal",
                "reader_awareness": "partial",
                "temporality": "foreshadow",
                "metadata": {},
            },
        ]
    }
    result = apply_task_output_adapter(data, TaskType.EXTRACT_INIT_COHERENCE_CLAIMS)
    assert result.changed is False
    assert data["claims"][0]["cognitive_level"] == "definitely_wrong"
    assert data["claims"][0]["action_level"] == "spilled"
    assert data["claims"][0]["reader_awareness"] == "kinda_knows"
    assert data["claims"][0]["temporality"] == "fictional"
    # valid values pass through untouched
    assert data["claims"][1]["cognitive_level"] == "partial"
    assert data["claims"][1]["action_level"] == "internal"
    assert data["claims"][1]["reader_awareness"] == "partial"
    assert data["claims"][1]["temporality"] == "foreshadow"
    with pytest.raises(TaskSemanticContractError) as exc_info:
        validate_task_semantic_contract(data, TaskType.EXTRACT_INIT_COHERENCE_CLAIMS)
    paths = {issue.path for issue in exc_info.value.issues}
    assert "$.claims[0].cognitive_level" in paths
    assert "$.claims[0].action_level" in paths


def test_init_claims_adapter_normalizes_aliases_but_does_not_backfill_claim_fields() -> None:
    data = {
        "Claims": [
            {
                "claim_id": "c001",
                "artifact": "chapter_contracts",
                "source_path": "/chapter_contracts/0",
                "claim_text": "玄昱在账房发现旧案线索。",
                "evidence": "玄昱翻到账房旧册。",
            }
        ],
        "Summary": "ok",
    }

    result = apply_task_output_adapter(data, TaskType.EXTRACT_INIT_COHERENCE_CLAIMS)

    assert result.changed is True
    assert "Claims" not in data
    assert "Summary" not in data
    assert data["summary"] == "ok"
    claim = data["claims"][0]
    for key in (
        "cognitive_subjects",
        "cognitive_object",
        "cognitive_level",
        "action_level",
        "reader_awareness",
        "public_reveal_chapter",
        "foreshadow_chapters",
    ):
        assert key not in claim
    with pytest.raises(TaskSemanticContractError):
        validate_task_semantic_contract(data, TaskType.EXTRACT_INIT_COHERENCE_CLAIMS)


def test_init_claims_adapter_noop_when_enums_already_valid() -> None:
    """When every field the adapter inspects is already valid, the adapter
    is still allowed to report changed=True only if it had to coerce a
    missing/None scalar (metadata, confidence, claim_type, irreversible).
    This test pins the no-op path by providing all those fields up front.
    """
    data = {
        "summary": "",
        "claims": [
            {
                "claim_id": "c001",
                "claim_text": "ok",
                "evidence": "e1",
                "character_knowledge_coverage": {"A": "full"},
                "cognitive_level": "confirmed",
                "action_level": "revealed",
                "reader_awareness": "partial",
                "temporality": "foreshadow",
                "claim_type": "event",
                "confidence": 0.7,
                "irreversible": False,
                "metadata": {"src": "blueprint"},
                "foreshadow_chapters": [],
            }
        ],
    }
    result = apply_task_output_adapter(data, TaskType.EXTRACT_BLUEPRINT_HOLISTIC_CLAIMS)
    # Nothing changed: shape was already correct, no enum drift, no scalar drift
    assert result.changed is False
    assert data["claims"][0]["character_knowledge_coverage"] == {"A": "full"}


def test_init_claims_adapter_does_not_replace_invalid_scalar_semantics() -> None:
    data = {
        "claims": [
            {
                "claim_id": "c1",
                "claim_text": "x",
                "evidence": "e",
                "metadata": None,
                "confidence": None,
                "claim_type": None,
                "irreversible": None,
            },
            {
                "claim_id": "c2",
                "claim_text": "x",
                "evidence": "e",
                "metadata": "not a dict",
                "confidence": "abc",
                "claim_type": "NotInEnum",
                "irreversible": "yes",
            },
        ]
    }
    result = apply_task_output_adapter(data, TaskType.EXTRACT_INIT_COHERENCE_CLAIMS)
    assert result.changed is True
    assert data["claims"][0]["metadata"] == {}
    assert data["claims"][0]["confidence"] is None
    assert data["claims"][0]["claim_type"] is None
    assert data["claims"][0]["irreversible"] is None
    assert data["claims"][1]["metadata"] == {}
    assert data["claims"][1]["confidence"] == "abc"
    assert data["claims"][1]["claim_type"] == "NotInEnum"
    assert data["claims"][1]["irreversible"] == "yes"
    with pytest.raises(TaskSemanticContractError):
        validate_task_semantic_contract(data, TaskType.EXTRACT_INIT_COHERENCE_CLAIMS)


def test_init_claims_adapter_converts_explicit_foreshadow_value_but_not_missing() -> None:
    data = {
        "claims": [
            {
                "claim_id": "cc41_48_c041_01",
                "artifact": "chapter_contracts",
                "source_path": "/chapter_contracts/40:48/current/41",
                "claim_type": "knowledge",
                "claim_text": "姜维清品茶时无意提及蜀国蚕月计划与鬼蟊。",
                "evidence": "姜维清品茶时无意提及蜀国蚕月计划与鬼蟊",
                "cognitive_subjects": ["沈清漪"],
                "cognitive_object": "姜维清与蜀国蚕月计划及鬼蟊有关联",
                "cognitive_level": "suspicion",
                "action_level": "internal",
                "reader_awareness": "partial",
                "character_knowledge_coverage": {"沈清漪": "partial"},
                "cognitive_chapter": 41,
                "public_reveal_chapter": None,
                "metadata": {},
                "confidence": 1.0,
            },
            {
                "claim_id": "cc41_48_c042_01",
                "artifact": "chapter_contracts",
                "source_path": "/chapter_contracts/40:48/current/42",
                "claim_type": "event",
                "claim_text": "沈清漪追查鬼蟊线索。",
                "evidence": "沈清漪追查鬼蟊线索",
                "cognitive_subjects": [],
                "cognitive_object": "",
                "cognitive_level": "unaware",
                "action_level": "none",
                "reader_awareness": "unknown",
                "character_knowledge_coverage": {},
                "cognitive_chapter": None,
                "public_reveal_chapter": None,
                "foreshadow_chapters": "40",
                "metadata": {},
                "confidence": 0.8,
            },
        ]
    }

    result = apply_task_output_adapter(data, TaskType.EXTRACT_INIT_COHERENCE_CLAIMS)

    assert result.changed is True
    assert "foreshadow_chapters" not in data["claims"][0]
    assert data["claims"][1]["foreshadow_chapters"] == [40]
    with pytest.raises(TaskSemanticContractError) as exc_info:
        validate_task_semantic_contract(data, TaskType.EXTRACT_INIT_COHERENCE_CLAIMS)
    assert "$.claims[0].foreshadow_chapters" in {issue.path for issue in exc_info.value.issues}


def test_init_claims_adapter_preserves_non_dict_awareness_for_retry() -> None:
    for bad in (None, 12345, "string", [1, 2, 3], True):
        data = {
            "claims": [
                {
                    "claim_id": "c1",
                    "claim_text": "x",
                    "evidence": "e",
                    "character_knowledge_coverage": bad,
                    "metadata": {},
                }
            ]
        }
        result = apply_task_output_adapter(data, TaskType.EXTRACT_INIT_COHERENCE_CLAIMS)
        assert result.changed is False, f"failed for {bad!r}"
        assert data["claims"][0]["character_knowledge_coverage"] == bad
        with pytest.raises(TaskSemanticContractError):
            validate_task_semantic_contract(data, TaskType.EXTRACT_INIT_COHERENCE_CLAIMS)


def test_init_claims_adapter_preserves_out_of_range_confidence_for_retry() -> None:
    data = {
        "claims": [
            {
                "claim_id": "c1",
                "claim_text": "x",
                "evidence": "e",
                "confidence": 1.5,
                "metadata": {},
            },
            {
                "claim_id": "c2",
                "claim_text": "x",
                "evidence": "e",
                "confidence": -0.2,
                "metadata": {},
            },
        ]
    }
    result = apply_task_output_adapter(data, TaskType.EXTRACT_INIT_COHERENCE_CLAIMS)
    assert result.changed is False
    assert data["claims"][0]["confidence"] == 1.5
    assert data["claims"][1]["confidence"] == -0.2
    with pytest.raises(TaskSemanticContractError):
        validate_task_semantic_contract(data, TaskType.EXTRACT_INIT_COHERENCE_CLAIMS)
