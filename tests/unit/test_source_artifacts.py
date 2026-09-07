from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from novel_forge.core.schemas.artifacts import CanonicalEntityRef, StageArtifact
from novel_forge.narrative_state.schemas import ChapterContract
from novel_forge.persistence.filesystem import FileSystemStorage
from novel_forge.persistence.models import ProjectLayout
from novel_forge.pipeline.long.services.attention_budget import apply_stage_context_manifest
from novel_forge.pipeline.long.services.context.source_artifacts import (
    SOURCE_ARTIFACT_TYPES,
    _collect_name_value,
    _looks_like_name,
    _relevant_names,
    _source_payload_issues,
    attach_chapter_instruction_to_source_slice,
    attach_chapter_research_to_source_slice,
    build_init_entity_catalog,
    hash_payload,
    load_chapter_source_slice,
    load_init_readiness_artifact,
    load_stage_artifact,
    normalize_cognitive_subjects,
    persist_init_source_artifacts,
    persist_stage_artifact,
    project_stage_source_cards,
    source_artifact_hashes,
)
from novel_forge.pipeline.long.services.contract_field_semantics import (
    ChapterContractFieldSemantic,
    chapter_contract_field_semantic,
)


def _world_rule_book() -> dict[str, object]:
    """A complete initialization ledger used by source-artifact tests."""

    return {
        "version": "1",
        "rules": [
            {
                "rule_id": "wr_time",
                "content": "时辰刻度只允许一至四刻。",
                "category": "time_space",
                "severity": "hard",
                "always_on": True,
                "forbidden_behavior": ["使用小时分钟等现代计时"],
                "cost_or_consequence": ["跨时段行动必须交代时辰变化"],
            },
            {
                "rule_id": "wr_address",
                "content": "称谓必须与官阶和亲疏关系一致。",
                "category": "social_language",
                "severity": "hard",
                "always_on": True,
                "forbidden_behavior": ["以下犯上直呼尊长姓名"],
                "cost_or_consequence": ["失礼会招致质询或惩戒"],
            },
            {
                "rule_id": "wr_knowledge",
                "content": "角色只能依据亲见、转述或账册内容行动。",
                "category": "information",
                "severity": "hard",
                "always_on": True,
                "forbidden_behavior": ["凭空得知密室外事实"],
                "cost_or_consequence": ["未知信息需通过调查获得"],
            },
            {
                "rule_id": "wr_gate",
                "content": "夜禁后城门闭锁，出入需有凭验。",
                "category": "time_space",
                "severity": "hard",
                "applicability_tags": ["夜禁", "城门"],
                "forbidden_behavior": ["夜禁后无凭验出城"],
                "cost_or_consequence": ["违者留档受审"],
            },
            {
                "rule_id": "wr_accounts",
                "content": "账房文书需留有经手痕迹。",
                "category": "resource_material",
                "severity": "soft",
                "applicability_tags": ["账房", "账册"],
                "allowed_behavior": ["通过笔迹和封签核验"],
            },
            {
                "rule_id": "wr_jade",
                "content": "魂玉的转手必须有见证或封存记录。",
                "category": "resource_material",
                "severity": "hard",
                "applicability_tags": ["魂玉", "封存"],
                "forbidden_behavior": ["无记录改变魂玉持有人"],
                "cost_or_consequence": ["记录缺失会成为追查线索"],
            },
            {
                "rule_id": "wr_rumor",
                "content": "传闻只能作为怀疑，不得直接等同事实。",
                "category": "information",
                "severity": "soft",
                "applicability_tags": ["传闻", "证词"],
            },
            {
                "rule_id": "wr_office",
                "content": "官署文书须按层级传递，不可越级调阅。",
                "category": "social_language",
                "severity": "soft",
                "applicability_tags": ["官署", "文书"],
            },
            {
                "rule_id": "wr_travel",
                "content": "城内跨坊移动需要可见的时间与路径。",
                "category": "time_space",
                "severity": "soft",
                "applicability_tags": ["坊", "巷口"],
            },
            {
                "rule_id": "wr_evidence",
                "content": "旧案结论必须由至少两类独立证据支撑。",
                "category": "general",
                "severity": "soft",
                "applicability_tags": ["旧案", "证据"],
            },
        ],
    }


def _source_payloads(character_name: str = "玄昱") -> dict[str, object]:
    return {
        "spec": {
            "title": "魂玉",
            "genre": "古代悬疑",
            "theme": "身份与真相",
            "tone": "克制",
            "length_target": 30000,
            "language": "zh",
        },
        "story_bible": {
            "title": "魂玉",
            "premise": "玄昱追查魂玉旧案。",
            "era": "架空古代",
            "rules": ["时辰刻度只允许一至四刻。"],
            "world_rule_book": _world_rule_book(),
            "themes": ["身份"],
            "banned_intent_rules": ["不得提前确认核心谜底。"],
        },
        "character_bible": {
            "characters": [
                {
                    "name": "玄昱",
                    "character_id": "char_xuanyu",
                    "role": "protagonist",
                    "voice": "克制、寡言",
                    "dialogue_style": "短句",
                }
            ]
        },
        "character_system": {
            "roster": [
                {
                    "character_id": "char_xuanyu",
                    "name": "玄昱",
                    "role": "protagonist",
                }
            ],
            "relationship_edges": [],
            "identity_links": [],
        },
        "entity_graph": {
            "entities": [
                {
                    "entity_id": "char_xuanyu",
                    "name": "玄昱",
                    "entity_type": "character",
                    "aliases": [],
                },
                {
                    "entity_id": "loc_zhangfang",
                    "name": "账房",
                    "entity_type": "location",
                    "aliases": [],
                },
            ],
            "entity_links": [],
        },
        "style_profile": {"narrative_voice": "第三人称限知", "forbidden_phrases": []},
        "creative_packet": {
            "thematic_promises": ["身份真相逐步逼近"],
            "signature_motifs": ["魂玉冷光"],
        },
        "blueprint": {"synopsis": "玄昱追查旧案。"},
        "outline": {
            "total_chapters": 1,
            "chapters": [
                {
                    "chapter_number": 1,
                    "title": "账房风声",
                    "pov_character": "玄昱",
                    "involved_characters": ["玄昱"],
                }
            ],
        },
        "narrative_contract": {
            "world_rules": ["时辰刻度只允许一至四刻。"],
            "promise_plan": [],
            "continuity_protocol": {},
        },
        "chapter_contracts": {
            "chapter_contracts": [
                {
                    "chapter_number": 1,
                    "involved_characters": [character_name],
                    "required_events": ["进入账房"],
                    "future_leak_risks": ["不得确认书童不存在。"],
                }
            ],
            "coverage": {},
        },
        "readiness_report": {
            "allowed": True,
            "summary": "初始化准入通过，可以进入章节生成。",
        },
    }


@pytest.mark.parametrize(
    ("path", "semantic"),
    [
        (("knowledge_ops", "subject"), ChapterContractFieldSemantic.FACT_TEXT),
        (("knowledge_ops", "target"), ChapterContractFieldSemantic.FACT_TEXT),
        (("knowledge_ops", "fact"), ChapterContractFieldSemantic.FACT_TEXT),
        (("knowledge_ops", "character"), ChapterContractFieldSemantic.ENTITY_REF),
        (("knowledge_ops", "entity_id"), ChapterContractFieldSemantic.ENTITY_REF),
        (("item_ops", "subject"), ChapterContractFieldSemantic.ENTITY_REF),
        (("item_ops", "entity_id"), ChapterContractFieldSemantic.ENTITY_REF),
        (("relationship_ops", "target"), ChapterContractFieldSemantic.ENTITY_REF),
        (("relationship_ops", "source"), ChapterContractFieldSemantic.STRUCTURAL),
        (
            ("cognitive_constraints", "cognitive_subjects"),
            ChapterContractFieldSemantic.ENTITY_REF,
        ),
        (
            ("cognitive_constraints", "character_knowledge_coverage"),
            ChapterContractFieldSemantic.ENTITY_REF_MAP,
        ),
        (("cognitive_constraints", "claim_id"), ChapterContractFieldSemantic.STRUCTURAL),
        (("emotional_plan", "subject_entity_id"), ChapterContractFieldSemantic.ENTITY_REF),
        (
            ("new_group_candidates", "group_name"),
            ChapterContractFieldSemantic.NEW_ENTITY_CANDIDATE,
        ),
        (("required_events",), ChapterContractFieldSemantic.FREE_TEXT),
        (("subject",), ChapterContractFieldSemantic.UNKNOWN),
    ],
)
def test_chapter_contract_field_semantics_are_explicit(
    path: tuple[str, ...],
    semantic: ChapterContractFieldSemantic,
) -> None:
    assert chapter_contract_field_semantic(path) == semantic


def _persist(storage: FileSystemStorage, layout: ProjectLayout, **overrides: object):
    payloads = {**_source_payloads(), **overrides}
    return persist_init_source_artifacts(
        storage=storage,
        layout=layout,
        project_id="魂玉",
        spec=payloads["spec"],
        story_bible=payloads["story_bible"],
        character_bible=payloads["character_bible"],
        character_system=payloads["character_system"],
        entity_graph=payloads["entity_graph"],
        style_profile=payloads["style_profile"],
        creative_packet=payloads["creative_packet"],
        blueprint=payloads["blueprint"],
        outline=payloads["outline"],
        narrative_contract=payloads["narrative_contract"],
        chapter_contracts=payloads["chapter_contracts"],
        readiness_report=payloads["readiness_report"],
    )


def test_persist_source_artifacts_and_build_chapter_slice(tmp_path) -> None:
    storage = FileSystemStorage(tmp_path)
    layout = ProjectLayout(storage.ensure_project_dir("魂玉"))

    readiness = _persist(storage, layout)
    assert readiness.quality_status == "pass"

    slice_artifact = load_chapter_source_slice(
        storage,
        layout,
        project_id="魂玉",
        chapter_number=1,
    )

    assert slice_artifact.quality_status == "pass"
    assert slice_artifact.payload["chapter_contract"]["chapter_number"] == 1
    assert slice_artifact.payload["forbidden_reveal_boundaries"]

    draft_cards = project_stage_source_cards(slice_artifact, stage="draft")
    runtime = slice_artifact.payload["runtime"]
    assert "runtime" not in draft_cards
    assert "archive_refs" not in draft_cards
    assert draft_cards["chapter_contract"] == runtime["chapter_contract"]
    assert draft_cards["relevant_entities"] == runtime["entities"]
    assert draft_cards["story_foundation"] == runtime["world"]
    assert draft_cards["style_voice"]["style_profile"] == runtime["style"]
    assert "story_foundation" in draft_cards
    assert "style_voice" in draft_cards
    assert "creative_direction" not in draft_cards
    assert draft_cards["user_intent"]["schema"] == "user_intent_card_v1"
    assert draft_cards["literary_contract"]["schema"] == "literary_contract_v1"
    assert draft_cards["literary_contract"]["character"]["pov_character"] == "玄昱"

    repair_cards = project_stage_source_cards(slice_artifact, stage="repair")
    assert "runtime" not in repair_cards
    assert "chapter_contract" in repair_cards
    assert "style_voice" not in repair_cards
    assert repair_cards["literary_contract"]["plot"]["required_events"] == ["进入账房"]

    instructed = attach_chapter_instruction_to_source_slice(
        slice_artifact,
        "本章保持玄昱单一视角。",
        chapter_number=1,
    )
    instructed_cards = project_stage_source_cards(instructed, stage="draft")
    assert instructed_cards["user_intent"]["chapter_instruction"]["value"] == (
        "本章保持玄昱单一视角。"
    )
    assert "chapter_instruction" not in slice_artifact.payload["runtime"]["user_intent"]

    research_pack = {
        "pack_id": "chapter-research:test",
        "purpose": "chapter_generation",
        "query": "test",
        "evidence_cards": [
            {
                "card_id": "fact",
                "kind": "external_fact",
                "source_ref": "source:fact",
                "excerpt": "事实卡",
                "authority": "supporting",
            },
            {
                "card_id": "inspiration",
                "kind": "external_inspiration",
                "source_ref": "source:inspiration",
                "excerpt": "抽象机制卡",
                "authority": "supporting",
            },
        ],
    }
    researched = attach_chapter_research_to_source_slice(
        slice_artifact,
        evidence_pack=research_pack,
        uncertainty=("来源有限",),
    )
    draft_cards = project_stage_source_cards(researched, stage="draft")
    repair_cards = project_stage_source_cards(researched, stage="repair")
    wave_cards = project_stage_source_cards(researched, stage="wave")
    assert [card["kind"] for card in draft_cards["research_evidence_pack"]["evidence_cards"]] == [
        "external_fact",
        "external_inspiration",
    ]
    assert [card["kind"] for card in repair_cards["research_evidence_pack"]["evidence_cards"]] == [
        "external_fact"
    ]
    assert wave_cards["research_evidence_pack"] == {}


def test_derived_world_entity_id_from_contract_is_registered(tmp_path) -> None:
    storage = FileSystemStorage(tmp_path)
    layout = ProjectLayout(storage.ensure_project_dir("魂玉"))
    payloads = _source_payloads(character_name="玄昱")
    contract = payloads["chapter_contracts"]["chapter_contracts"][0]
    contract["item_ops"] = [
        {
            "item_id": "item_a084157bbf75",
            "description": "充电线被藏在账房抽屉里。",
        }
    ]

    readiness = _persist(storage, layout, chapter_contracts=payloads["chapter_contracts"])

    assert readiness.quality_status == "pass"
    refs = {ref.entity_id: ref for ref in readiness.canonical_entity_refs}
    assert refs["item_a084157bbf75"].entity_type == "item"
    assert refs["item_a084157bbf75"].canonical_name == "item_a084157bbf75"

    artifact = storage.load_json(layout.source_artifact_path("chapter_contract_index"))
    persisted = artifact["payload"]["by_chapter"]["1"]
    assert "item_a084157bbf75" in persisted["canonical_entity_ids"]

    slice_artifact = load_chapter_source_slice(
        storage,
        layout,
        project_id="魂玉",
        chapter_number=1,
    )
    assert slice_artifact.quality_status == "pass"
    assert any(ref.entity_id == "item_a084157bbf75" for ref in slice_artifact.canonical_entity_refs)


def test_chapter_slice_uses_readiness_registry_for_repaired_entity_refs(tmp_path) -> None:
    storage = FileSystemStorage(tmp_path)
    layout = ProjectLayout(storage.ensure_project_dir("魂玉"))
    payloads = _source_payloads(character_name="玄昱")
    contract = payloads["chapter_contracts"]["chapter_contracts"][0]
    contract["item_ops"] = [
        {
            "item_id": "item_a084157bbf75",
            "description": "苏皖借充电线进入房间。",
        }
    ]

    _persist(storage, layout, chapter_contracts=payloads["chapter_contracts"])

    contract_artifact = storage.load_json(layout.source_artifact_path("chapter_contract_index"))
    contract_artifact["canonical_entity_refs"] = [
        ref
        for ref in contract_artifact.get("canonical_entity_refs", [])
        if ref.get("entity_id") != "item_a084157bbf75"
    ]
    contract_artifact["quality_status"] = "fail"
    contract_artifact["blocking_issues"] = [
        {
            "code": "unresolved_contract_entity",
            "message": "第 1 章契约引用未登记角色/实体：item_a084157bbf75",
            "severity": "critical",
            "source": "chapter_contract_index",
            "path": "by_chapter.1",
        }
    ]
    storage.save_json(layout.source_artifact_path("chapter_contract_index"), contract_artifact)

    readiness_payload = storage.load_json(layout.init_readiness_artifact_path)
    for ref in readiness_payload.get("canonical_entity_refs", []):
        if ref.get("entity_id") == "item_a084157bbf75":
            ref["canonical_name"] = "充电线"
    readiness_payload["source_hashes"] = source_artifact_hashes(storage, layout)
    storage.save_json(layout.init_readiness_artifact_path, readiness_payload)

    readiness = load_init_readiness_artifact(storage, layout)
    healed_contract_artifact = storage.load_json(
        layout.source_artifact_path("chapter_contract_index")
    )
    assert readiness.quality_status == "pass"
    assert healed_contract_artifact["quality_status"] == "pass"
    assert healed_contract_artifact["blocking_issues"] == []

    slice_artifact = load_chapter_source_slice(
        storage,
        layout,
        project_id="魂玉",
        chapter_number=1,
    )

    assert slice_artifact.quality_status == "pass"
    assert any(
        ref.entity_id == "item_a084157bbf75" and ref.canonical_name == "充电线"
        for ref in slice_artifact.canonical_entity_refs
    )


def test_readiness_consistency_blocks_unresolved_child_artifact_issue(tmp_path) -> None:
    storage = FileSystemStorage(tmp_path)
    layout = ProjectLayout(storage.ensure_project_dir("魂玉"))
    payloads = _source_payloads(character_name="玄昱")
    contract = payloads["chapter_contracts"]["chapter_contracts"][0]
    contract["involved_character_ids"] = ["char_a084157bbf75"]

    readiness = _persist(storage, layout, chapter_contracts=payloads["chapter_contracts"])
    readiness_payload = readiness.model_dump(mode="json")
    readiness_payload["quality_status"] = "pass"
    readiness_payload["blocking_issues"] = []
    readiness_payload["source_hashes"] = source_artifact_hashes(storage, layout)
    storage.save_json(layout.init_readiness_artifact_path, readiness_payload)

    with pytest.raises(ValueError, match="char_a084157bbf75"):
        load_init_readiness_artifact(storage, layout)

    normalized = storage.load_json(layout.init_readiness_artifact_path)
    assert normalized["quality_status"] == "fail"
    assert any("char_a084157bbf75" in issue["message"] for issue in normalized["blocking_issues"])


def test_unregistered_derived_character_id_still_blocks_readiness(tmp_path) -> None:
    storage = FileSystemStorage(tmp_path)
    layout = ProjectLayout(storage.ensure_project_dir("魂玉"))
    payloads = _source_payloads(character_name="玄昱")
    contract = payloads["chapter_contracts"]["chapter_contracts"][0]
    contract["involved_character_ids"] = ["char_a084157bbf75"]

    readiness = _persist(storage, layout, chapter_contracts=payloads["chapter_contracts"])

    assert readiness.quality_status == "fail"
    assert any(
        issue.code == "unresolved_contract_entity" and "char_a084157bbf75" in issue.message
        for issue in readiness.blocking_issues
    )


def test_chapter_source_slice_contains_runtime_and_audit_capsules(tmp_path) -> None:
    storage = FileSystemStorage(tmp_path)
    layout = ProjectLayout(storage.ensure_project_dir("魂玉"))
    _persist(storage, layout)

    source_slice = load_chapter_source_slice(
        storage,
        layout,
        project_id="魂玉",
        chapter_number=1,
    )

    runtime = source_slice.payload["runtime"]
    audit = source_slice.payload["audit"]
    assert runtime["schema"] == "runtime_capsule_v1"
    assert runtime["chapter_contract"]["schema"] == "RuntimeChapterContract"
    assert runtime["chapter_contract"]["p0_required_progressions"] == ["进入账房"]
    assert runtime["chapter_contract"]["literary_contract"]["schema"] == "literary_contract_v1"
    assert runtime["chapter_contract"]["literary_contract"]["theme"]["primary_theme"] == "身份"
    assert runtime["chapter_contract"]["literary_contract"]["theme"]["theme_duties"] == []
    assert any(
        item["text"] == "进入账房" and item["satisfaction"] == "narrative"
        for item in runtime["chapter_contract"]["guidance_requirements"]
    )
    assert runtime["world"]["schema"] == "RuntimeWorldCapsule"
    assert runtime["entities"][0]["canonical_name"] == "玄昱"
    assert audit["schema"] == "audit_capsule_v1"
    assert "continuity" in audit["dimension_refs"]
    assert audit["dimension_refs"]["literary_contract"] == [
        "runtime.chapter_contract.literary_contract"
    ]
    assert source_slice.payload["archive_refs"]["chapter_contract_index"]["artifact_id"] == (
        "chapter_contract_index"
    )


def test_runtime_capsule_preserves_complete_chapter_p0_information(tmp_path) -> None:
    storage = FileSystemStorage(tmp_path)
    layout = ProjectLayout(storage.ensure_project_dir("魂玉"))
    payloads = _source_payloads()
    contract = payloads["chapter_contracts"]["chapter_contracts"][0]
    contract["required_events"] = [f"必达事件-{index}" for index in range(18)]
    contract["forbidden_changes"] = [f"禁止变化-{index}" for index in range(21)]
    contract["cognitive_constraints"] = [
        {
            "claim_id": f"claim-{index}",
            "claim_text": f"认知事实-{index}",
            "cognitive_subjects": ["玄昱"],
            "cognitive_object": f"谜面-{index}",
            "cognitive_level": "suspected",
            "action_level": "internal",
            "reader_awareness": "partial",
            "cognitive_chapter": 1,
        }
        for index in range(35)
    ]
    _persist(storage, layout, chapter_contracts=payloads["chapter_contracts"])

    source_slice = load_chapter_source_slice(
        storage,
        layout,
        project_id="魂玉",
        chapter_number=1,
    )
    runtime_contract = source_slice.payload["runtime"]["chapter_contract"]
    audit = source_slice.payload["audit"]

    assert runtime_contract["required_events"] == contract["required_events"]
    assert runtime_contract["forbidden_changes"] == contract["forbidden_changes"]
    assert runtime_contract["cognitive_constraints"] == contract["cognitive_constraints"]
    assert audit["claim_refs"] == contract["cognitive_constraints"]
    assert "hidden_counts" not in runtime_contract


def test_incomplete_chapter_source_slice_requires_clean_rebuild(tmp_path) -> None:
    storage = FileSystemStorage(tmp_path)
    layout = ProjectLayout(storage.ensure_project_dir("魂玉"))
    _persist(storage, layout)
    source_slice = load_chapter_source_slice(
        storage,
        layout,
        project_id="魂玉",
        chapter_number=1,
    )
    legacy_payload = source_slice.model_dump(mode="json")
    legacy_payload["payload"].pop("runtime", None)
    legacy_payload["payload"].pop("audit", None)
    legacy_payload["payload"].pop("archive_refs", None)

    with pytest.raises(ValueError, match="必须重建该章的 source slice"):
        project_stage_source_cards(legacy_payload, stage="draft")


def test_cached_chapter_slice_cannot_bypass_world_rule_book_reinitialization(tmp_path) -> None:
    storage = FileSystemStorage(tmp_path)
    layout = ProjectLayout(storage.ensure_project_dir("魂玉"))
    _persist(storage, layout)
    load_chapter_source_slice(storage, layout, project_id="魂玉", chapter_number=1)

    foundation = storage.load_json(layout.source_artifact_path("story_foundation"))
    foundation["payload"].pop("world_rule_book", None)
    storage.save_json(layout.source_artifact_path("story_foundation"), foundation)

    with pytest.raises(ValueError, match="不支持自动迁移"):
        load_chapter_source_slice(storage, layout, project_id="魂玉", chapter_number=1)


def test_draft_attention_budget_observes_pressure_without_truncating() -> None:
    cards = {
        "stage": "draft",
        "source": {"relevant_entities": [{"entity_id": str(i)} for i in range(10)]},
        "chapter": {"chapter_number": 1},
        "contract": {
            "hard_facts": [f"fact-{i}" for i in range(50)],
            "cognitive_constraints": [{"claim_id": str(i)} for i in range(20)],
        },
        "memory": {
            "previous_chapter_events": [{"event_summary": str(i)} for i in range(6)],
            "relevant_history": [{"event_summary": str(i)} for i in range(5)],
            "foreshadow_due": [{"description": str(i)} for i in range(5)],
            "motif_suggestions": [{"motif_name": str(i)} for i in range(5)],
            "summary_context": "x" * 1000,
        },
        "style": {
            "modules": [{"name": str(i), "rules": ["a"]} for i in range(8)],
            "banned_phrases": [str(i) for i in range(20)],
        },
        "plan": {"scene_intents": []},
        "characters": [{"name": str(i)} for i in range(9)],
    }

    budgeted = apply_stage_context_manifest(cards, stage="draft")

    assert len(budgeted["source"]["relevant_entities"]) == 10
    assert len(budgeted["contract"]["hard_facts"]) == 50
    assert len(budgeted["contract"]["cognitive_constraints"]) == 20
    assert len(budgeted["memory"]["previous_chapter_events"]) == 6
    assert len(budgeted["memory"]["relevant_history"]) == 5
    assert len(budgeted["memory"]["motif_suggestions"]) == 5
    assert len(budgeted["style"]["modules"]) == 8
    assert len(budgeted["characters"]) == 9
    overages = budgeted["context_budget"]["advisory_overages"]
    assert overages["contract.hard_facts"] == 10
    assert overages["source.relevant_entities"] == 3


def test_plan_keeps_optional_cards_when_raw_source_projection_is_large() -> None:
    protected = "P0 契约" * 7_000
    budgeted = apply_stage_context_manifest(
        {
            "stage": "plan",
            "source": {"chapter_contract": {"required_events": [protected]}},
            "chapter": {"chapter_number": 1},
            "contract": {"hard_facts": ["必须保留"]},
            "bridge": {"action_handoff": "必须承接"},
            "quality": {"guidance": "P2" * 1_000},
            "style": {"summary": "风格" * 1_000},
            "subplot_weave": {"threads": ["支线" * 1_000]},
            "arc_liveness": {"alerts": ["弧光" * 1_000]},
            "strand": {"alerts": ["线索" * 1_000]},
        },
        stage="plan",
    )

    assert budgeted["source"]["chapter_contract"]["required_events"] == [protected]
    assert budgeted["contract"]["hard_facts"] == ["必须保留"]
    assert budgeted["bridge"]["action_handoff"] == "必须承接"
    assert budgeted["quality"]["guidance"] == "P2" * 1_000
    assert budgeted["subplot_weave"]["threads"] == ["支线" * 1_000]
    assert budgeted["style"]["summary"] == "风格" * 1_000
    assert budgeted["arc_liveness"]["alerts"] == ["弧光" * 1_000]
    assert budgeted["strand"]["alerts"] == ["线索" * 1_000]
    hidden = budgeted["context_budget"]["hidden_counts"]
    assert not any(key.startswith("evicted_optional_card.") for key in hidden)
    assert budgeted["context_budget"]["advisory_overages"]["style.summary"] == 1580


def test_wave_attention_budget_denies_memory_and_knowledge() -> None:
    budgeted = apply_stage_context_manifest(
        {
            "stage": "wave",
            "source": {"relevant_entities": [{"entity_id": str(i)} for i in range(9)]},
            "chapter": {"chapter_number": 1},
            "contract": {"hard_facts": ["must not reach wave"], "cognitive_constraints": []},
            "memory": {"summary_context": "too broad"},
            "knowledge": {"chapter_ops": [{"description": "too broad"}]},
            "plan": {"cross_scene_intent": {}},
        },
        stage="wave",
    )

    assert "memory" not in budgeted
    assert "knowledge" not in budgeted
    assert budgeted["contract"]["hard_facts"] == ["must not reach wave"]
    assert len(budgeted["source"]["relevant_entities"]) == 9
    assert budgeted["context_budget"]["hidden_counts"]["denied_card.memory"] == 1
    assert budgeted["context_budget"]["advisory_overages"]["contract.hard_facts"] == 1


def test_polish_attention_budget_keeps_only_light_cards() -> None:
    budgeted = apply_stage_context_manifest(
        {
            "stage": "polish",
            "source": {"relevant_entities": [{"entity_id": str(i)} for i in range(7)]},
            "chapter": {"chapter_number": 1},
            "contract": {"hard_facts": ["should not reach polish"]},
            "plan": {"scene_intents": [{"scene_id": "s1"}]},
            "knowledge": {"character_cards": [{"character": "林晚"}]},
            "repair": {"known_issue_summaries": [{"summary": "too broad"}]},
            "editorial": {
                "character_voices": [{"character": str(i)} for i in range(6)],
                "symbol_policies": [{"symbol": str(i)} for i in range(6)],
                "scene_resistance_rules": [{"required_resistance": str(i)} for i in range(6)],
                "revelation_ladder": [{"thread": str(i)} for i in range(8)],
                "editorial_element_directives": [{"element_id": str(i)} for i in range(6)],
            },
            "style": {
                "summary": "x" * 400,
                "modules": [{"name": str(i), "rules": ["a"]} for i in range(5)],
                "banned_phrases": [str(i) for i in range(12)],
            },
            "memory": {
                "expression_channel_records": [{"text": str(i)} for i in range(6)],
                "summary_context": "should not be consumed by polish template",
            },
            "quality": {"in_chapter_payoffs": [str(i) for i in range(5)]},
        },
        stage="polish",
    )

    assert "contract" not in budgeted
    assert "plan" not in budgeted
    assert "knowledge" not in budgeted
    assert "repair" not in budgeted
    assert len(budgeted["source"]["relevant_entities"]) == 7
    assert len(budgeted["editorial"]["character_voices"]) == 6
    assert len(budgeted["style"]["modules"]) == 5
    assert len(budgeted["style"]["banned_phrases"]) == 12
    assert len(budgeted["memory"]["expression_channel_records"]) == 6
    assert len(budgeted["quality"]["in_chapter_payoffs"]) == 5
    assert budgeted["context_budget"]["advisory_overages"]["style.modules"] == 2
    assert "denied_card.plan" in budgeted["context_budget"]["hidden_counts"]


def test_stage_source_projection_keeps_raw_init_authoring_artifacts_out(tmp_path) -> None:
    storage = FileSystemStorage(tmp_path)
    layout = ProjectLayout(storage.ensure_project_dir("魂玉"))
    _persist(storage, layout)
    source_slice = load_chapter_source_slice(
        storage,
        layout,
        project_id="魂玉",
        chapter_number=1,
    )

    assert {"project_spec", "character_system", "entity_graph", "blueprint"} <= set(
        SOURCE_ARTIFACT_TYPES
    )

    for stage in ("bridge", "plan", "draft", "wave", "repair"):
        cards = project_stage_source_cards(source_slice, stage=stage)
        for raw_key in ("project_spec", "character_system", "entity_graph", "blueprint"):
            assert raw_key not in cards
        assert "chapter_contract" in cards

    draft_cards = project_stage_source_cards(source_slice, stage="draft")
    assert "relevant_entities" in draft_cards
    assert "story_foundation" in draft_cards
    assert "style_voice" in draft_cards


def test_unregistered_character_blocks_init_readiness(tmp_path) -> None:
    storage = FileSystemStorage(tmp_path)
    layout = ProjectLayout(storage.ensure_project_dir("魂玉"))
    payloads = _source_payloads(character_name="玄奘")

    readiness = _persist(storage, layout, chapter_contracts=payloads["chapter_contracts"])

    assert readiness.quality_status == "fail"
    assert any(issue.code == "unresolved_contract_entity" for issue in readiness.blocking_issues)


def test_source_artifact_failure_projects_into_init_readiness_stage(tmp_path) -> None:
    from novel_forge.pipeline.long.services.init.init_coherence import build_init_readiness_report
    from novel_forge.pipeline.long.services.init.init_service import (
        _source_artifact_readiness_stage_report,
        _source_artifact_repair_plan,
    )

    storage = FileSystemStorage(tmp_path)
    layout = ProjectLayout(storage.ensure_project_dir("魂玉"))
    payloads = _source_payloads(character_name="玄奘")

    readiness = _persist(storage, layout, chapter_contracts=payloads["chapter_contracts"])
    plan = _source_artifact_repair_plan(readiness)
    source_report = _source_artifact_readiness_stage_report(
        readiness,
        repair_plan=plan,
        attempts=1,
        stop_reason="no_repairable_source_artifact_issue",
    )
    init_readiness = build_init_readiness_report(
        reports={"source_artifacts": source_report},
        repairs=[],
        min_severity="high",
        required=True,
    )

    assert source_report["blocked"] is True
    assert source_report["remaining_issue_codes"] == ["unresolved_contract_entity"]
    assert init_readiness["allowed"] is False
    assert init_readiness["stages"]["source_artifacts"]["blocked"] is True
    assert init_readiness["remaining_issues"][0]["stage"] == "source_artifacts"


def test_character_short_alias_resolves_contract_references(tmp_path) -> None:
    storage = FileSystemStorage(tmp_path)
    layout = ProjectLayout(storage.ensure_project_dir("魂玉"))
    payloads = _source_payloads(character_name="清漪")
    character_bible = {
        "characters": [
            {
                "name": "沈清漪",
                "character_id": "char_qingyi",
                "role": "deuteragonist",
                "voice": "克制、敏锐",
                "dialogue_style": "短句",
            }
        ]
    }
    entity_graph = {
        "entities": [
            {
                "entity_id": "char_qingyi",
                "name": "沈清漪",
                "entity_type": "character",
                "aliases": [],
            }
        ],
        "entity_links": [],
    }

    readiness = _persist(
        storage,
        layout,
        character_bible=character_bible,
        character_system={
            "roster": [{"character_id": "char_qingyi", "name": "沈清漪"}],
            "relationship_edges": [],
            "identity_links": [],
        },
        entity_graph=entity_graph,
        chapter_contracts=payloads["chapter_contracts"],
    )

    assert readiness.quality_status == "pass"
    qingyi = next(ref for ref in readiness.canonical_entity_refs if ref.canonical_name == "沈清漪")
    assert "清漪" in qingyi.aliases


def test_contract_entity_ids_resolve_as_canonical_refs(tmp_path) -> None:
    storage = FileSystemStorage(tmp_path)
    layout = ProjectLayout(storage.ensure_project_dir("魂玉"))
    payloads = _source_payloads(character_name="玄昱")
    contract = payloads["chapter_contracts"]["chapter_contracts"][0]
    contract["pov_character_id"] = "char_xuanyu"
    contract["required_character_ids"] = ["char_xuanyu"]
    contract["involved_character_ids"] = ["char_xuanyu"]
    contract["cast_plan"] = {
        "pov_entity_id": "char_xuanyu",
        "required_character_ids": ["char_xuanyu"],
        "support_character_ids": [],
        "mention_only_entity_ids": [],
        "forbidden_active_character_ids": [],
    }

    readiness = _persist(storage, layout, chapter_contracts=payloads["chapter_contracts"])

    assert readiness.quality_status == "pass"
    source_slice = load_chapter_source_slice(
        storage,
        layout,
        project_id="魂玉",
        chapter_number=1,
    )
    relevant_ids = {ref["entity_id"] for ref in source_slice.payload["relevant_entities"]}
    assert "char_xuanyu" in relevant_ids


def test_emotional_plan_subject_entity_id_is_audited(tmp_path) -> None:
    storage = FileSystemStorage(tmp_path)
    layout = ProjectLayout(storage.ensure_project_dir("魂玉"))
    payloads = _source_payloads(character_name="玄昱")
    contract = payloads["chapter_contracts"]["chapter_contracts"][0]
    contract["emotional_plan"] = {
        "subject_entity_id": "char_unknown",
        "entry_state": "隐忍",
        "pressure_source": "账房旧账逼近真相",
        "relationship_choice": "玄昱选择暂时保护证人",
        "turning_emotion": "警惕转为承担",
        "exit_aftertaste": "余疑未散",
        "expression_channels": ["action"],
    }

    readiness = _persist(storage, layout, chapter_contracts=payloads["chapter_contracts"])

    assert readiness.quality_status == "fail"
    assert any(issue.code == "unresolved_contract_entity" for issue in readiness.blocking_issues)


def test_source_artifact_unresolved_entity_report_scopes_contract_field(tmp_path) -> None:
    from novel_forge.pipeline.long.services.init.init_service import (
        _source_artifact_contract_repair_report,
    )

    storage = FileSystemStorage(tmp_path)
    layout = ProjectLayout(storage.ensure_project_dir("魂玉"))
    payloads = _source_payloads(character_name="玄昱")
    contract = payloads["chapter_contracts"]["chapter_contracts"][0]
    contract["item_ops"] = [
        {
            "entity_id": "item_001",
            "description": "item_001 被玄昱反复摩挲。",
        }
    ]

    readiness = _persist(storage, layout, chapter_contracts=payloads["chapter_contracts"])
    entity_catalog = build_init_entity_catalog(
        payloads["entity_graph"],
        payloads["character_bible"],
    )
    report = _source_artifact_contract_repair_report(
        readiness,
        chapter_contracts=payloads["chapter_contracts"],
        entity_catalog=entity_catalog,
    )

    assert readiness.quality_status == "fail"
    assert report is not None
    assert report["verdict"] == "needs_repair"
    assert report["issues"][0]["unresolved_entity"] == "item_001"
    assert report["issues"][0]["repair_scope"][0]["chapters"] == [1]
    assert "item_ops" in report["issues"][0]["repair_scope"][0]["fields"]
    assert "entity_catalog" in report["change_intent"]


def test_entity_catalog_derives_collision_checked_parenthetical_alias() -> None:
    canonical_name = "幕后势力（海城神经数据科技有限公司）"
    catalog = build_init_entity_catalog(
        {
            "entities": [
                {
                    "entity_id": "org_hidden",
                    "name": canonical_name,
                    "entity_type": "organization",
                    "aliases": [],
                }
            ],
            "entity_links": [],
        },
        {"characters": []},
    )

    alias = catalog["alias_to_entity"]["幕后势力"]
    assert alias["entity_id"] == "org_hidden"
    assert alias["canonical_name"] == canonical_name


def test_parenthetical_alias_does_not_shadow_another_canonical_entity() -> None:
    catalog = build_init_entity_catalog(
        {
            "entities": [
                {
                    "entity_id": "org_qualified",
                    "name": "幕后势力（海城神经数据科技有限公司）",
                    "entity_type": "organization",
                    "aliases": [],
                },
                {
                    "entity_id": "org_plain",
                    "name": "幕后势力",
                    "entity_type": "organization",
                    "aliases": [],
                },
            ],
            "entity_links": [],
        },
        {"characters": []},
    )

    assert catalog["alias_to_entity"]["幕后势力"]["entity_id"] == "org_plain"


def test_source_artifact_entity_alias_repair_defers_near_miss_names_to_llm(tmp_path) -> None:
    from novel_forge.pipeline.long.services.init.init_service import (
        _apply_source_artifact_entity_alias_repair,
        _source_artifact_contract_repair_report,
    )

    storage = FileSystemStorage(tmp_path)
    layout = ProjectLayout(storage.ensure_project_dir("魂玉"))
    payloads = _source_payloads(character_name="玄寅")
    character_bible = {
        "characters": [
            {
                "name": "玄昱",
                "character_id": "char_xuanyu",
                "role": "protagonist",
            },
            {
                "name": "沈清漪",
                "character_id": "char_qingyi",
                "role": "deuteragonist",
            },
        ]
    }
    entity_graph = {
        "entities": [
            {
                "entity_id": "char_xuanyu",
                "name": "玄昱",
                "entity_type": "character",
                "aliases": [],
            },
            {
                "entity_id": "char_qingyi",
                "name": "沈清漪",
                "entity_type": "character",
                "aliases": [],
            },
        ],
        "entity_links": [],
    }
    contract = payloads["chapter_contracts"]["chapter_contracts"][0]
    contract["involved_characters"] = ["玄寅", "沈清浅"]
    contract["cognitive_constraints"] = [
        {
            "cognitive_subjects": ["玄寅", "沈清浅"],
            "character_knowledge_coverage": {"玄寅": "partial", "沈清浅": "full"},
            "claim_text": "玄寅把账册递给沈清浅。",
            "evidence": "玄寅和沈清浅同在账房。",
        }
    ]

    readiness = _persist(
        storage,
        layout,
        character_bible=character_bible,
        character_system={
            "roster": [
                {"character_id": "char_xuanyu", "name": "玄昱"},
                {"character_id": "char_qingyi", "name": "沈清漪"},
            ],
            "relationship_edges": [],
            "identity_links": [],
        },
        entity_graph=entity_graph,
        chapter_contracts=payloads["chapter_contracts"],
    )
    entity_catalog = build_init_entity_catalog(entity_graph, character_bible)
    report = _source_artifact_contract_repair_report(
        readiness,
        chapter_contracts=payloads["chapter_contracts"],
        entity_catalog=entity_catalog,
    )

    assert readiness.quality_status == "fail"
    assert report is not None

    result = _apply_source_artifact_entity_alias_repair(
        payloads["chapter_contracts"],
        report=report,
        entity_catalog=entity_catalog,
        round_index=1,
    )

    assert result.changed is False
    assert result.repair_record["patch_count"] == 0
    assert len(result.repair_record["skipped_patches"]) == 2
    unresolved_contract = result.payload["chapter_contracts"][0]
    assert unresolved_contract["involved_characters"] == ["玄寅", "沈清浅"]


def test_source_artifact_entity_repair_clears_unknown_placeholder_subject() -> None:
    from novel_forge.pipeline.long.services.init.init_service import (
        _apply_source_artifact_entity_alias_repair,
    )

    chapter_contracts = {
        "chapter_contracts": [
            {
                "chapter_number": 18,
                "item_ops": [
                    {
                        "description": "内侍发现青衫袖角拂过的茶渍。",
                        "subject": "未知",
                    }
                ],
            }
        ]
    }
    report = {
        "issues": [
            {
                "id": "source_artifacts_unresolved_entity_18_0",
                "type": "unresolved_contract_entity",
                "unresolved_entity": "未知",
                "repair_scope": [
                    {"artifact": "chapter_contracts", "chapters": [18], "fields": ["item_ops"]}
                ],
            }
        ]
    }

    result = _apply_source_artifact_entity_alias_repair(
        chapter_contracts,
        report=report,
        entity_catalog={"allowed_entities": [], "alias_to_entity": {}},
        round_index=1,
    )

    assert result.changed is True
    repaired_item = result.payload["chapter_contracts"][0]["item_ops"][0]
    assert repaired_item["subject"] == ""
    assert repaired_item["description"] == "内侍发现青衫袖角拂过的茶渍。"
    assert result.repair_record["patches"][0]["operation"] == "clear_placeholder_entity_reference"


def test_source_artifact_entity_repair_honors_reconciled_collective_removal() -> None:
    from novel_forge.pipeline.long.services.init.init_service import (
        _apply_source_artifact_entity_alias_repair,
    )

    chapter_contracts = {
        "chapter_contracts": [
            {
                "chapter_number": 49,
                "entry_state_requirements": ["团队面临是否进入深层梦境的抉择"],
                "knowledge_ops": [
                    {
                        "character": "团队",
                        "knower": "陈半仙",
                        "fact": "洗字方案的技术细节与代价",
                    }
                ],
            }
        ]
    }
    report = {
        "issues": [
            {
                "id": "source_artifacts_unresolved_entity_49_0",
                "type": "unresolved_contract_entity",
                "unresolved_entity": "团队",
                "repair_scope": [
                    {
                        "artifact": "chapter_contracts",
                        "chapters": [49],
                        "fields": ["entry_state_requirements", "knowledge_ops"],
                    }
                ],
            }
        ],
        "change_intent": json.dumps(
            {
                "entity_reconciliation": {
                    "accepted": [
                        {
                            "unresolved_name": "团队",
                            "resolution": "remove",
                            "confidence": 0.85,
                        }
                    ]
                }
            },
            ensure_ascii=False,
        ),
    }

    result = _apply_source_artifact_entity_alias_repair(
        chapter_contracts,
        report=report,
        entity_catalog={"allowed_entities": [], "alias_to_entity": {}},
        round_index=1,
    )

    contract = result.payload["chapter_contracts"][0]
    assert result.changed is True
    assert contract["knowledge_ops"][0]["character"] == ""
    assert contract["entry_state_requirements"] == ["团队面临是否进入深层梦境的抉择"]
    assert result.repair_record["patches"][0]["operation"] == ("clear_reconciled_entity_reference")


def test_source_artifact_repair_plan_only_allows_unresolved_entities(tmp_path) -> None:
    from novel_forge.pipeline.long.services.init.init_service import _source_artifact_repair_plan

    storage = FileSystemStorage(tmp_path)
    layout = ProjectLayout(storage.ensure_project_dir("魂玉"))
    payloads = _source_payloads(character_name="玄奘")

    readiness = _persist(storage, layout, chapter_contracts=payloads["chapter_contracts"])
    plan = _source_artifact_repair_plan(readiness)

    assert [issue["code"] for issue in plan.repairable_issues] == ["unresolved_contract_entity"]
    assert plan.non_repairable_issues == []

    blocked = SimpleNamespace(
        blocking_issues=[
            {"code": "missing_entity_registry", "message": "缺少实体表"},
            {"code": "missing_chapter_contracts", "message": "缺少章节契约"},
        ]
    )
    blocked_plan = _source_artifact_repair_plan(blocked)

    assert blocked_plan.repairable_issues == []
    assert [issue["code"] for issue in blocked_plan.non_repairable_issues] == [
        "missing_entity_registry",
        "missing_chapter_contracts",
    ]


def test_source_artifact_failure_message_includes_diagnostics(tmp_path) -> None:
    from novel_forge.pipeline.long.services.init.init_service import (
        _format_source_artifact_failure,
        _source_artifact_repair_plan,
    )

    storage = FileSystemStorage(tmp_path)
    layout = ProjectLayout(storage.ensure_project_dir("魂玉"))
    payloads = _source_payloads(character_name="玄奘")
    readiness = _persist(storage, layout, chapter_contracts=payloads["chapter_contracts"])

    message = _format_source_artifact_failure(
        SimpleNamespace(layout=layout),
        readiness_artifact=readiness,
        repair_plan=_source_artifact_repair_plan(readiness),
        attempts=1,
        stop_reason="no_blocking_issue_reduction",
    )

    assert "可修复问题 1 个" in message
    assert "不可自动修复问题 0 个" in message
    assert "已尝试 1 轮" in message
    assert "no_blocking_issue_reduction" in message
    assert "unresolved_contract_entity" in message
    assert str(layout.logs_dir) in message


def test_new_character_candidates_are_registered_as_source_refs(tmp_path) -> None:
    storage = FileSystemStorage(tmp_path)
    layout = ProjectLayout(storage.ensure_project_dir("魂玉"))
    payloads = _source_payloads(character_name="玄昱")
    contract = payloads["chapter_contracts"]["chapter_contracts"][0]
    contract["involved_characters"] = ["玄昱", "药铺伙计", "姜维清"]
    contract["new_character_candidates"] = [
        {
            "character_name": "药铺伙计",
            "importance": "supporting",
            "should_consider_profile": True,
            "role_summary": "蜀国影卫外围潜伏人员，以药铺伙计身份掩护。",
        },
        {
            "name": "姜维清",
            "importance": "supporting",
            "should_consider_profile": True,
            "brief": "鬼蛹之女，以医女身份进入东宫。",
        },
    ]

    readiness = _persist(storage, layout, chapter_contracts=payloads["chapter_contracts"])

    assert readiness.quality_status == "pass"
    names = {ref.canonical_name for ref in readiness.canonical_entity_refs}
    assert {"药铺伙计", "姜维清"} <= names
    source_slice = load_chapter_source_slice(
        storage,
        layout,
        project_id="魂玉",
        chapter_number=1,
    )
    relevant_names = {ref["canonical_name"] for ref in source_slice.payload["relevant_entities"]}
    assert {"玄昱", "药铺伙计", "姜维清"} <= relevant_names


def test_new_group_candidates_are_registered_as_source_refs(tmp_path) -> None:
    storage = FileSystemStorage(tmp_path)
    layout = ProjectLayout(storage.ensure_project_dir("魂玉"))
    payloads = _source_payloads(character_name="玄昱")
    contract = payloads["chapter_contracts"]["chapter_contracts"][0]
    contract["knowledge_ops"] = [
        {
            "subject": "朝堂众臣",
            "operation": "rumor_spread",
            "content": "朝堂众臣只形成传闻压力，不提前确认魂玉真相。",
        }
    ]
    contract["new_group_candidates"] = [
        {
            "group_name": "朝堂众臣",
            "entity_type": "organization",
            "aliases": ["群臣", "臣僚"],
            "brief": "本章新增的集体性政治压力来源。",
        }
    ]

    readiness = _persist(storage, layout, chapter_contracts=payloads["chapter_contracts"])

    assert readiness.quality_status == "pass"
    refs = {ref.canonical_name: ref for ref in readiness.canonical_entity_refs}
    assert refs["朝堂众臣"].entity_type == "organization"
    assert {"群臣", "臣僚"} <= set(refs["朝堂众臣"].aliases)
    source_slice = load_chapter_source_slice(
        storage,
        layout,
        project_id="魂玉",
        chapter_number=1,
    )
    relevant_names = {ref["canonical_name"] for ref in source_slice.payload["relevant_entities"]}
    assert {"玄昱", "朝堂众臣"} <= relevant_names


def test_world_entity_refs_require_model_authored_candidates_or_reconciliation(tmp_path) -> None:
    storage = FileSystemStorage(tmp_path)
    layout = ProjectLayout(storage.ensure_project_dir("魂玉"))
    payloads = _source_payloads(character_name="玄昱")
    contract = payloads["chapter_contracts"]["chapter_contracts"][0]
    contract["item_ops"] = [
        {
            "subject": "边境据点",
            "operation": "establish_pressure",
            "description": "边境据点只作为本章外部压力来源。",
        }
    ]
    contract["knowledge_ops"] = [
        {
            "entity_id": "蜀国",
            "operation": "rumor_pressure",
            "fact": "蜀国传来的战报不能提前确认魂玉真相。",
        }
    ]

    readiness = _persist(storage, layout, chapter_contracts=payloads["chapter_contracts"])

    assert readiness.quality_status == "fail"
    refs = {ref.canonical_name: ref for ref in readiness.canonical_entity_refs}
    assert "边境据点" not in refs
    assert "蜀国" not in refs
    unresolved = {
        issue.message.rsplit("：", 1)[-1]
        for issue in readiness.blocking_issues
        if issue.code == "unresolved_contract_entity"
    }
    assert {"边境据点", "蜀国"} <= unresolved


def test_chapter_contract_schema_preserves_new_entity_candidate_fields() -> None:
    contract = ChapterContract.model_validate(
        {
            "chapter_number": 1,
            "new_group_candidates": [{"group_name": "朝堂众臣", "entity_type": "organization"}],
            "new_location_candidates": [{"location_name": "旧账房暗室"}],
            "new_item_candidates": [{"item_name": "残缺魂玉"}],
            "new_concept_candidates": [{"concept_name": "魂玉共鸣"}],
        }
    )

    dumped = contract.model_dump(mode="json")

    assert dumped["new_group_candidates"][0]["group_name"] == "朝堂众臣"
    assert dumped["new_location_candidates"][0]["location_name"] == "旧账房暗室"
    assert dumped["new_item_candidates"][0]["item_name"] == "残缺魂玉"
    assert dumped["new_concept_candidates"][0]["concept_name"] == "魂玉共鸣"


def test_chapter_slice_cleans_outline_role_labels_and_ignores_outline_extras(tmp_path) -> None:
    storage = FileSystemStorage(tmp_path)
    layout = ProjectLayout(storage.ensure_project_dir("魂玉"))
    payloads = _source_payloads(character_name="玄昱")
    outline = payloads["outline"]
    outline["chapters"][0]["pov_character"] = "玄昱（主）"
    outline["chapters"][0]["involved_characters"] = [
        "玄昱（主）、账房先生（次）",
        "临场内侍",
    ]

    readiness = _persist(storage, layout, outline=outline)

    assert readiness.quality_status == "pass"
    source_slice = load_chapter_source_slice(
        storage,
        layout,
        project_id="魂玉",
        chapter_number=1,
    )
    relevant_names = {ref["canonical_name"] for ref in source_slice.payload["relevant_entities"]}
    assert "玄昱" in relevant_names
    assert "临场内侍" not in relevant_names


def test_source_artifact_hash_staleness_blocks_readiness(tmp_path) -> None:
    storage = FileSystemStorage(tmp_path)
    layout = ProjectLayout(storage.ensure_project_dir("魂玉"))
    _persist(storage, layout)
    story_path = layout.source_artifact_path("story_foundation")
    story_artifact = storage.load_json(story_path)
    story_artifact["payload"]["rules"].append("被篡改的规则")
    storage.save_json(story_path, story_artifact)

    with pytest.raises(ValueError, match="hash 已失效"):
        load_init_readiness_artifact(storage, layout)


def test_empty_chapter_contracts_block_unconfirmed_outline_fallback(tmp_path) -> None:
    storage = FileSystemStorage(tmp_path)
    layout = ProjectLayout(storage.ensure_project_dir("魂玉"))

    readiness = _persist(storage, layout, chapter_contracts={})

    assert readiness.quality_status == "fail"
    assert any(
        issue.code == "unconfirmed_outline_fallback_contract" for issue in readiness.blocking_issues
    )
    artifact = storage.load_json(layout.source_artifact_path("chapter_contract_index"))
    contract = artifact["payload"]["by_chapter"]["1"]
    assert contract["source"] == "outline_fallback"
    assert contract["chapter_number"] == 1
    assert artifact["payload"]["coverage"]["outline_fallback_generated"] is True


def test_outline_fallback_contract_preserves_cast_and_emotional_plan(tmp_path) -> None:
    storage = FileSystemStorage(tmp_path)
    layout = ProjectLayout(storage.ensure_project_dir("魂玉"))
    payloads = _source_payloads(character_name="玄昱")
    outline = payloads["outline"]
    outline["chapters"][0].update(
        {
            "pov_character_id": "char_xuanyu",
            "pov_character_name": "玄昱",
            "involved_character_ids": ["char_xuanyu"],
            "required_character_ids": ["char_xuanyu"],
            "support_character_ids": [],
            "involved_character_names": ["玄昱"],
            "cast_plan": {
                "pov_entity_id": "char_xuanyu",
                "required_character_ids": ["char_xuanyu"],
                "support_character_ids": [],
                "mention_only_entity_ids": [],
                "forbidden_active_character_ids": [],
            },
            "emotional_plan": {
                "subject_entity_id": "char_xuanyu",
                "entry_state": "压住焦躁",
                "pressure_source": "账房旧账逼近真相",
                "relationship_choice": "玄昱选择暂时保护证人",
                "turning_emotion": "警惕转为承担",
                "exit_aftertaste": "余疑未散",
                "expression_channels": ["action", "dialogue"],
            },
            "scene_design_goals": ["用行动表达保护，而不是解释保护"],
        }
    )

    readiness = _persist(
        storage,
        layout,
        outline=outline,
        chapter_contracts={"coverage": {"local_fallback_accepted": True}},
    )

    assert readiness.quality_status == "pass"
    source_slice = load_chapter_source_slice(
        storage,
        layout,
        project_id="魂玉",
        chapter_number=1,
    )
    contract = source_slice.payload["chapter_contract"]
    assert contract["source"] == "outline_fallback"
    assert contract["pov_character_id"] == "char_xuanyu"
    assert contract["cast_plan"]["pov_entity_id"] == "char_xuanyu"
    assert contract["emotional_plan"]["subject_entity_id"] == "char_xuanyu"
    assert contract["scene_design_goals"] == ["用行动表达保护，而不是解释保护"]
    assert "char_xuanyu" in contract["canonical_entity_ids"]


def test_stage_artifacts_chain_previous_hashes(tmp_path) -> None:
    storage = FileSystemStorage(tmp_path)
    layout = ProjectLayout(storage.ensure_project_dir("魂玉"))
    _persist(storage, layout)
    source_slice = load_chapter_source_slice(
        storage,
        layout,
        project_id="魂玉",
        chapter_number=1,
    )

    bridge = persist_stage_artifact(
        storage=storage,
        layout=layout,
        project_id="魂玉",
        chapter_number=1,
        artifact_type="bridge",
        payload={"entry_state": "玄昱抵达账房"},
        chapter_source_slice=source_slice,
    )
    plan = persist_stage_artifact(
        storage=storage,
        layout=layout,
        project_id="魂玉",
        chapter_number=1,
        artifact_type="plan",
        payload={"scene_count": 2},
        chapter_source_slice=source_slice,
        previous_artifact=bridge,
    )
    draft = persist_stage_artifact(
        storage=storage,
        layout=layout,
        project_id="魂玉",
        chapter_number=1,
        artifact_type="scene_draft",
        payload={
            "text_path": "drafts/chapter_001/v0_draft.md",
            "text_hash": "abc123",
            "text_chars": 1200,
        },
        chapter_source_slice=source_slice,
        previous_artifact=plan,
    )

    assert plan.previous_artifact_id == bridge.artifact_id
    assert bridge.artifact_id in plan.source_hashes
    assert draft.previous_artifact_id == plan.artifact_id
    assert "text" not in draft.payload
    assert len(bridge.input_signature) == 64
    assert len(plan.input_signature) == 64
    assert plan.parent_artifact_versions == {bridge.artifact_id: 1}
    assert plan.output_hash == hash_payload(plan.payload)
    assert plan.output_version == 1
    assert plan.reuse_policy == "signature_cache"
    assert draft.reuse_policy == "same_run_resume"
    assert (
        load_stage_artifact(
            storage,
            layout,
            chapter_number=1,
            artifact_type="scene_draft",
        )
        == draft
    )

    unchanged_plan = persist_stage_artifact(
        storage=storage,
        layout=layout,
        project_id="魂玉",
        chapter_number=1,
        artifact_type="plan",
        payload={"scene_count": 2},
        chapter_source_slice=source_slice,
        previous_artifact=bridge,
    )
    changed_plan = persist_stage_artifact(
        storage=storage,
        layout=layout,
        project_id="魂玉",
        chapter_number=1,
        artifact_type="plan",
        payload={"scene_count": 3},
        chapter_source_slice=source_slice,
        previous_artifact=bridge,
    )
    assert unchanged_plan.output_version == 1
    assert changed_plan.output_version == 2


def test_stage_artifact_rejects_wrong_chapter_and_corrupted_parent(tmp_path) -> None:
    storage = FileSystemStorage(tmp_path)
    layout = ProjectLayout(storage.ensure_project_dir("handoff_guard"))
    bridge = persist_stage_artifact(
        storage=storage,
        layout=layout,
        project_id="handoff_guard",
        chapter_number=1,
        artifact_type="bridge",
        payload={"entry_state": "门外"},
    )

    wrong_chapter = bridge.model_copy(
        update={"scope": bridge.scope.model_copy(update={"ids": ["2"]})}
    )
    with pytest.raises(ValueError, match="chapter mismatch"):
        persist_stage_artifact(
            storage=storage,
            layout=layout,
            project_id="handoff_guard",
            chapter_number=1,
            artifact_type="plan",
            payload={"scene_count": 1},
            previous_artifact=wrong_chapter,
        )

    corrupted = bridge.model_copy(update={"output_hash": "0" * 64})
    with pytest.raises(ValueError, match="output hash mismatch"):
        persist_stage_artifact(
            storage=storage,
            layout=layout,
            project_id="handoff_guard",
            chapter_number=1,
            artifact_type="plan",
            payload={"scene_count": 1},
            previous_artifact=corrupted,
        )


def test_stage_artifact_helpers_tolerate_minimal_test_layout() -> None:
    storage = SimpleNamespace()
    layout = SimpleNamespace()

    assert load_stage_artifact(storage, layout, chapter_number=1, artifact_type="plan") is None
    artifact = persist_stage_artifact(
        storage=storage,
        layout=layout,
        project_id="",
        chapter_number=1,
        artifact_type="plan",
        payload={"scene_count": 1},
    )

    assert artifact.project_id == "unknown"
    assert artifact.payload["scene_count"] == 1


def test_unsigned_stage_artifact_loads_as_legacy_unknown() -> None:
    artifact = StageArtifact.model_validate(
        {
            "artifact_type": "plan",
            "project_id": "legacy-project",
            "scope": {"kind": "chapter", "ids": ["1"]},
            "payload": {"scene_count": 1},
        }
    )

    assert artifact.workflow_version == "legacy_unknown"
    assert artifact.artifact_schema_version == 1
    assert artifact.input_signature == "legacy_unknown"
    assert artifact.output_hash == "legacy_unknown"
    assert artifact.output_version == 0
    assert artifact.execution_quality_status == "legacy_unknown"
    assert artifact.derivation_status == "legacy_unknown"


class TestNormalizeCognitiveSubjectsStaticCatalog:
    """Tests for normalize_cognitive_subjects — the shared normalization layer."""

    @staticmethod
    def _catalog() -> dict[str, Any]:
        return {
            "allowed_entities": [
                {
                    "entity_id": "char_qingyi",
                    "canonical_name": "沈清漪",
                    "entity_type": "character",
                    "aliases": ["清漪"],
                },
                {
                    "entity_id": "char_xuanyu",
                    "canonical_name": "玄昱",
                    "entity_type": "character",
                    "aliases": [],
                },
                {
                    "entity_id": "char_yuwen",
                    "canonical_name": "宇文铎",
                    "entity_type": "character",
                    "aliases": ["宇文"],
                },
                {
                    "entity_id": "char_gongsun",
                    "canonical_name": "公孙烈",
                    "entity_type": "character",
                    "aliases": [],
                },
            ],
            "alias_to_entity": {
                "沈清漪": {
                    "entity_id": "char_qingyi",
                    "canonical_name": "沈清漪",
                    "entity_type": "character",
                },
                "清漪": {
                    "entity_id": "char_qingyi",
                    "canonical_name": "沈清漪",
                    "entity_type": "character",
                },
                "玄昱": {
                    "entity_id": "char_xuanyu",
                    "canonical_name": "玄昱",
                    "entity_type": "character",
                },
                "宇文铎": {
                    "entity_id": "char_yuwen",
                    "canonical_name": "宇文铎",
                    "entity_type": "character",
                },
                "宇文": {
                    "entity_id": "char_yuwen",
                    "canonical_name": "宇文铎",
                    "entity_type": "character",
                },
                "公孙烈": {
                    "entity_id": "char_gongsun",
                    "canonical_name": "公孙烈",
                    "entity_type": "character",
                },
            },
            "allowed_entity_ids": ["char_qingyi", "char_xuanyu", "char_yuwen", "char_gongsun"],
            "allowed_entity_names": ["沈清漪", "玄昱", "宇文铎", "公孙烈"],
            "policy": "",
        }

    def test_canonical_name_preserved(self) -> None:
        result = normalize_cognitive_subjects(["沈清漪", "玄昱"], self._catalog())
        assert result == ["沈清漪", "玄昱"]

    def test_alias_resolved_to_canonical(self) -> None:
        result = normalize_cognitive_subjects(["清漪"], self._catalog())
        assert result == ["沈清漪"]

    def test_corrupted_token_dropped(self) -> None:
        result = normalize_cognitive_subjects(["文 on轨", "皇 on烈", "on潘"], self._catalog())
        assert result == []

    def test_mixed_valid_and_corrupted(self) -> None:
        result = normalize_cognitive_subjects(["清漪", "文 on轨", "公孙烈"], self._catalog())
        assert result == ["沈清漪", "公孙烈"]

    def test_deduplication_after_resolution(self) -> None:
        result = normalize_cognitive_subjects(["沈清漪", "清漪"], self._catalog())
        assert result == ["沈清漪"]

    def test_empty_catalog_passes_through(self) -> None:
        result = normalize_cognitive_subjects(["沈清漪"], {})
        assert result == ["沈清漪"]

    def test_empty_subjects(self) -> None:
        result = normalize_cognitive_subjects([], self._catalog())
        assert result == []

    def test_no_catalog(self) -> None:
        result = normalize_cognitive_subjects(["沈清漪"], {"allowed_entities": []})
        assert result == ["沈清漪"]


class TestSourcePayloadIssuesWithEntityCatalog:
    """Tests that _source_payload_issues uses entity_catalog to resolve names."""

    def test_cognitive_subjects_corrupted_token_not_flagged(self, tmp_path) -> None:
        storage = FileSystemStorage(tmp_path)
        layout = ProjectLayout(storage.ensure_project_dir("魂玉"))
        payloads = _source_payloads(character_name="玄昱")
        contract = payloads["chapter_contracts"]["chapter_contracts"][0]
        contract["cognitive_constraints"] = [
            {
                "claim_id": "clm_001",
                "cognitive_subjects": ["清漪", "文 on轨"],
                "claim_text": "测试 claim",
                "cognitive_level": "unaware",
                "action_level": "none",
            }
        ]
        entity_graph = {
            "entities": [
                {
                    "entity_id": "char_xuanyu",
                    "name": "玄昱",
                    "entity_type": "character",
                    "aliases": [],
                },
                {
                    "entity_id": "char_qingyi",
                    "name": "沈清漪",
                    "entity_type": "character",
                    "aliases": ["清漪"],
                },
            ],
            "entity_links": [],
        }
        readiness = _persist(
            storage,
            layout,
            entity_graph=entity_graph,
            character_bible={
                "characters": [
                    {"name": "玄昱", "character_id": "char_xuanyu", "role": "protagonist"},
                    {"name": "沈清漪", "character_id": "char_qingyi", "role": "supporting"},
                ]
            },
            character_system={
                "roster": [
                    {"character_id": "char_xuanyu", "name": "玄昱", "role": "protagonist"},
                    {"character_id": "char_qingyi", "name": "沈清漪", "role": "supporting"},
                ],
                "relationship_edges": [],
                "identity_links": [],
            },
            chapter_contracts=payloads["chapter_contracts"],
        )
        corrupted_flags = [
            issue
            for issue in readiness.blocking_issues
            if issue.code == "unresolved_contract_entity" and "on轨" in issue.message
        ]
        assert not corrupted_flags

    def test_existing_corrupted_cognitive_subjects_cleaned_on_persist(self, tmp_path) -> None:
        """When chapter_contracts already contains corrupted cognitive_subjects,
        persist_init_source_artifacts should normalize them before validation,
        so that resuming a failed init does not re-trigger the same false flags."""
        storage = FileSystemStorage(tmp_path)
        layout = ProjectLayout(storage.ensure_project_dir("魂玉"))
        payloads = _source_payloads(character_name="玄昱")
        contract = payloads["chapter_contracts"]["chapter_contracts"][0]
        contract["cognitive_constraints"] = [
            {
                "claim_id": "clm_001",
                "cognitive_subjects": ["清漪", "文 on轨", "皇 on烈"],
                "claim_text": "测试 claim",
                "cognitive_level": "unaware",
                "action_level": "none",
            }
        ]
        entity_graph = {
            "entities": [
                {
                    "entity_id": "char_xuanyu",
                    "name": "玄昱",
                    "entity_type": "character",
                    "aliases": [],
                },
                {
                    "entity_id": "char_qingyi",
                    "name": "沈清漪",
                    "entity_type": "character",
                    "aliases": ["清漪"],
                },
            ],
            "entity_links": [],
        }
        readiness = _persist(
            storage,
            layout,
            entity_graph=entity_graph,
            character_bible={
                "characters": [
                    {"name": "玄昱", "character_id": "char_xuanyu", "role": "protagonist"},
                    {"name": "沈清漪", "character_id": "char_qingyi", "role": "supporting"},
                ]
            },
            character_system={
                "roster": [
                    {"character_id": "char_xuanyu", "name": "玄昱", "role": "protagonist"},
                    {"character_id": "char_qingyi", "name": "沈清漪", "role": "supporting"},
                ],
                "relationship_edges": [],
                "identity_links": [],
            },
            chapter_contracts=payloads["chapter_contracts"],
        )
        corrupted_flags = [
            issue
            for issue in readiness.blocking_issues
            if issue.code == "unresolved_contract_entity"
            and any(token in issue.message for token in ("on轨", "on烈"))
        ]
        assert not corrupted_flags

    def test_entity_reference_cleanup_preserves_unknowns_for_llm_reconciliation(
        self, tmp_path
    ) -> None:
        storage = FileSystemStorage(tmp_path)
        layout = ProjectLayout(storage.ensure_project_dir("魂玉"))
        payloads = _source_payloads(character_name="玄昱")
        contract = payloads["chapter_contracts"]["chapter_contracts"][0]
        contract["knowledge_ops"] = [
            {
                "type": "reveal",
                "subject": "沈清漪漪",
                "description": "清漪确认账册风险",
            },
            {
                "type": "hint",
                "subject": "文 on轨",
                "description": "污染 token 应被清空而不是进入准入 gate",
            },
        ]
        contract["cognitive_constraints"] = [
            {
                "claim_id": "clm_001",
                "cognitive_subjects": ["清漪", "文 on轨"],
                "claim_text": "测试 claim",
                "cognitive_level": "confirmed",
                "action_level": "internal",
                "character_knowledge_coverage": {
                    "沈清漪漪": "full",
                    "文 on轨": "unknown",
                },
            }
        ]
        contract["emotional_plan"] = {
            "subject_entity_id": "char_qingyi",
            "entry_state": "沈清漪漪带着上一节点的压力进入本章。",
        }
        contract["scene_design_goals"] = ["沈清漪漪必须用可见行动处理压力。"]
        entity_graph = {
            "entities": [
                {
                    "entity_id": "char_xuanyu",
                    "name": "玄昱",
                    "entity_type": "character",
                    "aliases": [],
                },
                {
                    "entity_id": "char_qingyi",
                    "name": "沈清漪",
                    "entity_type": "character",
                    "aliases": ["清漪"],
                },
            ],
            "entity_links": [],
        }

        readiness = _persist(
            storage,
            layout,
            entity_graph=entity_graph,
            character_bible={
                "characters": [
                    {"name": "玄昱", "character_id": "char_xuanyu", "role": "protagonist"},
                    {"name": "沈清漪", "character_id": "char_qingyi", "role": "supporting"},
                ]
            },
            character_system={
                "roster": [
                    {"character_id": "char_xuanyu", "name": "玄昱", "role": "protagonist"},
                    {"character_id": "char_qingyi", "name": "沈清漪", "role": "supporting"},
                ],
                "relationship_edges": [],
                "identity_links": [],
            },
            chapter_contracts=payloads["chapter_contracts"],
        )

        assert readiness.quality_status == "fail"
        artifact = storage.load_json(layout.source_artifact_path("chapter_contract_index"))
        persisted = artifact["payload"]["by_chapter"]["1"]
        assert persisted["knowledge_ops"][0]["subject"] == "沈清漪漪"
        assert persisted["knowledge_ops"][1]["subject"] == "文 on轨"
        constraint = persisted["cognitive_constraints"][0]
        assert constraint["cognitive_subjects"] == ["沈清漪", "文 on轨"]
        assert constraint["character_knowledge_coverage"] == {
            "沈清漪漪": "full",
            "文 on轨": "unknown",
        }
        assert "沈清漪漪" in persisted["emotional_plan"]["entry_state"]
        assert "沈清漪漪" in persisted["scene_design_goals"][0]

    def test_unknown_placeholder_subject_cleaned_on_persist(self, tmp_path) -> None:
        storage = FileSystemStorage(tmp_path)
        layout = ProjectLayout(storage.ensure_project_dir("魂玉"))
        payloads = _source_payloads(character_name="玄昱")
        contract = payloads["chapter_contracts"]["chapter_contracts"][0]
        contract["item_ops"] = [
            {
                "description": "内侍发现青衫袖角拂过的茶渍。",
                "subject": "未知",
            }
        ]

        readiness = _persist(storage, layout, chapter_contracts=payloads["chapter_contracts"])

        assert readiness.quality_status == "pass"
        artifact = storage.load_json(layout.source_artifact_path("chapter_contract_index"))
        item_op = artifact["payload"]["by_chapter"]["1"]["item_ops"][0]
        assert item_op["subject"] == ""

    def test_knowledge_ops_subject_is_fact_not_entity_reference(self, tmp_path) -> None:
        storage = FileSystemStorage(tmp_path)
        layout = ProjectLayout(storage.ensure_project_dir("魂玉"))
        payloads = _source_payloads(character_name="玄昱")
        contract = payloads["chapter_contracts"]["chapter_contracts"][0]
        contract["knowledge_ops"] = [
            {
                "description": "清漪得知商漪被胁迫传递情报的完整过程。",
                "character": "玄昱",
                "subject": "商漪背叛真相",
            }
        ]

        readiness = _persist(storage, layout, chapter_contracts=payloads["chapter_contracts"])

        assert readiness.quality_status == "pass"
        artifact = storage.load_json(layout.source_artifact_path("chapter_contract_index"))
        knowledge_op = artifact["payload"]["by_chapter"]["1"]["knowledge_ops"][0]
        assert knowledge_op["subject"] == "商漪背叛真相"

    def test_reported_knowledge_subject_phrases_do_not_block_entity_gate(self, tmp_path) -> None:
        storage = FileSystemStorage(tmp_path)
        layout = ProjectLayout(storage.ensure_project_dir("魂玉"))
        payloads = _source_payloads(character_name="玄昱")
        contract = payloads["chapter_contracts"]["chapter_contracts"][0]
        facts = [
            "商漪背叛真相",
            "蜀国影卫内部分裂",
            "清漪身份的正名确认",
            "鬼蛹背叛的完整后果",
            "蜀国影卫体系内部裂隙",
            "战后朝局态势",
            "彼此十年身份的最终确认",
            "母妃旧案真相",
            "父子重构",
        ]
        contract["knowledge_ops"] = [
            {
                "description": f"玄昱获知{fact}。",
                "character": "玄昱",
                "subject": fact,
            }
            for fact in facts
        ]

        readiness = _persist(storage, layout, chapter_contracts=payloads["chapter_contracts"])

        assert readiness.quality_status == "pass"
        artifact = storage.load_json(layout.source_artifact_path("chapter_contract_index"))
        persisted = artifact["payload"]["by_chapter"]["1"]["knowledge_ops"]
        assert [item["subject"] for item in persisted] == facts

    def test_knowledge_ops_fact_fields_are_not_entity_audited(self, tmp_path) -> None:
        storage = FileSystemStorage(tmp_path)
        layout = ProjectLayout(storage.ensure_project_dir("魂玉"))
        payloads = _source_payloads(character_name="玄昱")
        contract = payloads["chapter_contracts"]["chapter_contracts"][0]
        contract["knowledge_ops"] = [
            {
                "description": "玄昱获知战后朝局态势。",
                "character": "玄昱",
                "subject": "商漪背叛真相",
                "target": "蜀国影卫内部分裂",
                "fact": "清漪身份的正名确认",
                "knowledge_name": "鬼蛹背叛的完整后果",
                "value": "蜀国影卫体系内部裂隙",
                "object": "战后朝局态势",
            }
        ]

        readiness = _persist(storage, layout, chapter_contracts=payloads["chapter_contracts"])

        assert readiness.quality_status == "pass"
        artifact = storage.load_json(layout.source_artifact_path("chapter_contract_index"))
        knowledge_op = artifact["payload"]["by_chapter"]["1"]["knowledge_ops"][0]
        assert knowledge_op["subject"] == "商漪背叛真相"
        assert knowledge_op["target"] == "蜀国影卫内部分裂"
        assert knowledge_op["fact"] == "清漪身份的正名确认"
        assert knowledge_op["knowledge_name"] == "鬼蛹背叛的完整后果"
        assert knowledge_op["value"] == "蜀国影卫体系内部裂隙"
        assert knowledge_op["object"] == "战后朝局态势"

    def test_knowledge_ops_character_is_still_entity_audited(self, tmp_path) -> None:
        storage = FileSystemStorage(tmp_path)
        layout = ProjectLayout(storage.ensure_project_dir("魂玉"))
        payloads = _source_payloads(character_name="玄昱")
        contract = payloads["chapter_contracts"]["chapter_contracts"][0]
        contract["knowledge_ops"] = [
            {
                "description": "玄奘得知账房真相。",
                "character": "玄奘",
                "subject": "账房真相",
            }
        ]

        readiness = _persist(storage, layout, chapter_contracts=payloads["chapter_contracts"])

        assert readiness.quality_status == "fail"
        assert any(
            issue.code == "unresolved_contract_entity" and "玄奘" in issue.message
            for issue in readiness.blocking_issues
        )


class TestNormalizeCognitiveSubjectsAliasBuilder:
    """Tests for normalize_cognitive_subjects — the shared normalization layer."""

    @staticmethod
    def _catalog(names_aliases: dict[str, list[str]]) -> dict[str, Any]:
        allowed: list[dict[str, Any]] = []
        alias_to_entity: dict[str, dict[str, str]] = {}
        for canonical, aliases in names_aliases.items():
            entity_id = f"char_{hash(canonical) % 10000:04x}"
            item = {
                "entity_id": entity_id,
                "canonical_name": canonical,
                "entity_type": "character",
                "aliases": aliases,
            }
            allowed.append(item)
            for token in [entity_id, canonical, *aliases]:
                alias_to_entity[token] = {
                    "entity_id": entity_id,
                    "canonical_name": canonical,
                    "entity_type": "character",
                }
        return {"allowed_entities": allowed, "alias_to_entity": alias_to_entity}

    def test_canonical_name_preserved(self) -> None:
        catalog = self._catalog({"沈清漪": ["清漪"], "玄昱": []})
        result = normalize_cognitive_subjects(["沈清漪", "玄昱"], catalog)
        assert result == ["沈清漪", "玄昱"]

    def test_alias_normalized_to_canonical(self) -> None:
        catalog = self._catalog({"沈清漪": ["清漪"]})
        result = normalize_cognitive_subjects(["清漪"], catalog)
        assert result == ["沈清漪"]

    def test_corrupted_token_dropped(self) -> None:
        catalog = self._catalog({"宇文铎": [], "公孙烈": []})
        result = normalize_cognitive_subjects(["文 on轨", "皇 on烈", "商 on潘"], catalog)
        assert result == []

    def test_mixed_valid_and_corrupted(self) -> None:
        catalog = self._catalog({"沈清漪": ["清漪"], "玄昱": []})
        result = normalize_cognitive_subjects(["清漪", "文 on轨", "玄昱"], catalog)
        assert result == ["沈清漪", "玄昱"]

    def test_duplicate_canonical_deduped(self) -> None:
        catalog = self._catalog({"沈清漪": ["清漪"]})
        result = normalize_cognitive_subjects(["沈清漪", "清漪"], catalog)
        assert result == ["沈清漪"]

    def test_empty_catalog_passes_through(self) -> None:
        result = normalize_cognitive_subjects(["沈清漪"], {})
        assert result == ["沈清漪"]

    def test_none_catalog_passes_through(self) -> None:
        catalog = self._catalog({"沈清漪": []})
        result = normalize_cognitive_subjects(["沈清漪"], catalog)
        assert result == ["沈清漪"]

    def test_empty_subjects(self) -> None:
        catalog = self._catalog({"沈清漪": []})
        result = normalize_cognitive_subjects([], catalog)
        assert result == []


class TestNormalizeCognitiveSubjectsFixtureCatalog:
    """Tests for the shared normalize_cognitive_subjects function."""

    @pytest.fixture()
    def entity_catalog(self) -> dict[str, Any]:
        return {
            "allowed_entities": [
                {
                    "entity_id": "char_qingyi",
                    "canonical_name": "沈清漪",
                    "entity_type": "character",
                    "aliases": ["清漪"],
                },
                {
                    "entity_id": "char_xuanyu",
                    "canonical_name": "玄昱",
                    "entity_type": "character",
                    "aliases": [],
                },
                {
                    "entity_id": "char_yuwenduo",
                    "canonical_name": "宇文铎",
                    "entity_type": "character",
                    "aliases": ["文铎"],
                },
                {
                    "entity_id": "char_gongsunlie",
                    "canonical_name": "公孙烈",
                    "entity_type": "character",
                    "aliases": ["烈"],
                },
            ],
            "alias_to_entity": {
                "char_qingyi": {
                    "entity_id": "char_qingyi",
                    "canonical_name": "沈清漪",
                    "entity_type": "character",
                },
                "沈清漪": {
                    "entity_id": "char_qingyi",
                    "canonical_name": "沈清漪",
                    "entity_type": "character",
                },
                "清漪": {
                    "entity_id": "char_qingyi",
                    "canonical_name": "沈清漪",
                    "entity_type": "character",
                },
                "char_xuanyu": {
                    "entity_id": "char_xuanyu",
                    "canonical_name": "玄昱",
                    "entity_type": "character",
                },
                "玄昱": {
                    "entity_id": "char_xuanyu",
                    "canonical_name": "玄昱",
                    "entity_type": "character",
                },
                "char_yuwenduo": {
                    "entity_id": "char_yuwenduo",
                    "canonical_name": "宇文铎",
                    "entity_type": "character",
                },
                "宇文铎": {
                    "entity_id": "char_yuwenduo",
                    "canonical_name": "宇文铎",
                    "entity_type": "character",
                },
                "文铎": {
                    "entity_id": "char_yuwenduo",
                    "canonical_name": "宇文铎",
                    "entity_type": "character",
                },
                "char_gongsunlie": {
                    "entity_id": "char_gongsunlie",
                    "canonical_name": "公孙烈",
                    "entity_type": "character",
                },
                "公孙烈": {
                    "entity_id": "char_gongsunlie",
                    "canonical_name": "公孙烈",
                    "entity_type": "character",
                },
                "烈": {
                    "entity_id": "char_gongsunlie",
                    "canonical_name": "公孙烈",
                    "entity_type": "character",
                },
            },
            "allowed_entity_ids": [
                "char_qingyi",
                "char_xuanyu",
                "char_yuwenduo",
                "char_gongsunlie",
            ],
            "allowed_entity_names": ["沈清漪", "玄昱", "宇文铎", "公孙烈"],
            "policy": "",
        }

    def test_canonical_name_preserved(self, entity_catalog: dict[str, Any]) -> None:
        from novel_forge.pipeline.long.services.context.source_artifacts import (
            normalize_cognitive_subjects,
        )

        result = normalize_cognitive_subjects(["沈清漪", "玄昱"], entity_catalog)
        assert result == ["沈清漪", "玄昱"]

    def test_alias_normalized_to_canonical(self, entity_catalog: dict[str, Any]) -> None:
        from novel_forge.pipeline.long.services.context.source_artifacts import (
            normalize_cognitive_subjects,
        )

        result = normalize_cognitive_subjects(["清漪", "文铎"], entity_catalog)
        assert result == ["沈清漪", "宇文铎"]

    def test_corrupted_token_dropped(self, entity_catalog: dict[str, Any]) -> None:
        from novel_forge.pipeline.long.services.context.source_artifacts import (
            normalize_cognitive_subjects,
        )

        result = normalize_cognitive_subjects(["文 on轨", "皇 on烈", "on潘"], entity_catalog)
        assert result == []

    def test_mixed_valid_and_corrupted(self, entity_catalog: dict[str, Any]) -> None:
        from novel_forge.pipeline.long.services.context.source_artifacts import (
            normalize_cognitive_subjects,
        )

        result = normalize_cognitive_subjects(["沈清漪", "文 on轨", "公孙烈"], entity_catalog)
        assert result == ["沈清漪", "公孙烈"]

    def test_empty_catalog_passes_through(self) -> None:
        from novel_forge.pipeline.long.services.context.source_artifacts import (
            normalize_cognitive_subjects,
        )

        result = normalize_cognitive_subjects(["沈清漪"], {})
        assert result == ["沈清漪"]

    def test_no_catalog_passes_through(self) -> None:
        from novel_forge.pipeline.long.services.context.source_artifacts import (
            normalize_cognitive_subjects,
        )

        result = normalize_cognitive_subjects(["沈清漪"], None)
        assert result == ["沈清漪"]

    def test_duplicate_canonical_deduped(self, entity_catalog: dict[str, Any]) -> None:
        from novel_forge.pipeline.long.services.context.source_artifacts import (
            normalize_cognitive_subjects,
        )

        result = normalize_cognitive_subjects(["沈清漪", "清漪"], entity_catalog)
        assert result == ["沈清漪"]


class TestNormalizeCognitiveSubjectsMinimalCatalog:
    """Tests for normalize_cognitive_subjects: the shared normalization layer
    that resolves LLM-generated cognitive_subjects against entity_catalog."""

    @staticmethod
    def _catalog(names: list[tuple[str, str, list[str]]]) -> dict:
        """Build a minimal entity_catalog from (entity_id, canonical_name, aliases)."""
        allowed = []
        alias_to_entity: dict[str, dict[str, str]] = {}
        for eid, canonical, aliases in names:
            allowed.append({"entity_id": eid, "canonical_name": canonical, "aliases": aliases})
            for token in [eid, canonical, *aliases]:
                alias_to_entity.setdefault(
                    token,
                    {"entity_id": eid, "canonical_name": canonical, "entity_type": "character"},
                )
        return {
            "allowed_entities": allowed,
            "allowed_entity_ids": [a["entity_id"] for a in allowed],
            "allowed_entity_names": [a["canonical_name"] for a in allowed],
            "alias_to_entity": alias_to_entity,
            "policy": "",
        }

    def test_canonical_name_preserved(self) -> None:
        catalog = self._catalog([("char_001", "沈清漪", ["清漪"])])
        result = normalize_cognitive_subjects(["沈清漪"], catalog)
        assert result == ["沈清漪"]

    def test_alias_resolved_to_canonical(self) -> None:
        catalog = self._catalog([("char_001", "沈清漪", ["清漪"])])
        result = normalize_cognitive_subjects(["清漪"], catalog)
        assert result == ["沈清漪"]

    def test_corrupted_token_dropped(self) -> None:
        catalog = self._catalog(
            [
                ("char_001", "沈清漪", ["清漪"]),
                ("char_002", "宇文铎", ["文铎"]),
            ]
        )
        result = normalize_cognitive_subjects(["文 on轨", "皇 on烈", "沈清漪"], catalog)
        assert result == ["沈清漪"]

    def test_unknown_name_dropped(self) -> None:
        catalog = self._catalog([("char_001", "沈清漪", ["清漪"])])
        result = normalize_cognitive_subjects(["未知角色"], catalog)
        assert result == []

    def test_empty_catalog_passes_through(self) -> None:
        result = normalize_cognitive_subjects(["沈清漪"], {})
        assert result == ["沈清漪"]

    def test_no_catalog_passes_through(self) -> None:
        result = normalize_cognitive_subjects(["沈清漪"], {"alias_to_entity": {}})
        assert result == ["沈清漪"]

    def test_duplicate_canonical_deduped(self) -> None:
        catalog = self._catalog([("char_001", "沈清漪", ["清漪", "漪"])])
        result = normalize_cognitive_subjects(["沈清漪", "清漪", "漪"], catalog)
        assert result == ["沈清漪"]


def test_relationship_ops_source_is_not_collected_as_entity_name() -> None:
    """relationship_ops[*].source is provenance, not an entity reference."""

    contract = {
        "chapter_number": 17,
        "relationship_ops": [
            {
                "entity_id": "char_abc",
                "character": "玄昱",
                "target_id": "char_def",
                "target": "沈清漪",
                "type": "trust_deepening",
                "source": "plan_chapter_contracts",
            }
        ],
    }

    names = _relevant_names({}, contract)

    assert "plan_chapter_contracts" not in names
    assert "玄昱" in names
    assert "沈清漪" in names

    issues = _source_payload_issues(
        artifact_type="chapter_contract_index",
        payload={"by_chapter": {"17": contract}, "coverage": {}},
        canonical_refs=[
            CanonicalEntityRef(
                entity_id="char_abc",
                canonical_name="玄昱",
                entity_type="character",
                aliases=[],
            ),
            CanonicalEntityRef(
                entity_id="char_def",
                canonical_name="沈清漪",
                entity_type="character",
                aliases=[],
            ),
        ],
    )

    assert not any(issue.code == "unresolved_contract_entity" for issue in issues)


# ---------------------------------------------------------------------------
# Regression: LLM concatenation artifacts must be filtered
# ---------------------------------------------------------------------------


class TestLooksLikeNameFilterCorruptedEntities:
    """Regression tests for entity name filter catching LLM corruption artifacts."""

    def test_entity_id_with_underscore_is_allowed(self) -> None:
        # Entity IDs with underscores are valid names (e.g. from entity_id paths)
        assert _looks_like_name("char_abc")
        assert _looks_like_name("item_a084157bbf75")
        assert _looks_like_name("concept_net")

    def test_camelcase_concatenation_is_filtered(self) -> None:
        assert not _looks_like_name("netViolence")
        assert not _looks_like_name("conceptNet")

    def test_pure_ascii_camelcase_is_filtered(self) -> None:
        # camelCase boundaries in pure ASCII tokens are filtered
        assert not _looks_like_name("netViolence")
        assert not _looks_like_name("conceptNet")
        # But plain ASCII words without corruption markers are allowed
        assert _looks_like_name("Violence")
        assert _looks_like_name("concept")

    def test_short_ascii_tokens_are_preserved(self) -> None:
        # 3 chars or fewer ASCII tokens are allowed (could be abbreviations)
        assert _looks_like_name("AI")
        assert _looks_like_name("Dr")

    def test_chinese_names_are_preserved(self) -> None:
        assert _looks_like_name("沈听澜")
        assert _looks_like_name("宇文铎")

    def test_concatenated_entity_id_is_filtered(self) -> None:
        # The original bug: concept_netViolence was not filtered
        assert not _looks_like_name("concept_netViolence")


class TestCollectNameValueSplitConcatenatedTokens:
    """Regression tests for name collection splitting concatenated tokens."""

    def test_underscore_entity_ids_are_not_split(self) -> None:
        # Underscore entity IDs like "char_abc" must NOT be split
        names: list[str] = []
        _collect_name_value("char_abc", names)
        assert "char_abc" in names

    def test_camelcase_concatenated_tokens_are_split(self) -> None:
        names: list[str] = []
        _collect_name_value("netViolence", names)
        assert "net" in names
        assert "Violence" in names

    def test_mixed_underscore_and_camelcase_are_split(self) -> None:
        # "concept_netViolence" → "concept_net" + "Violence" via camelCase split
        names: list[str] = []
        _collect_name_value("concept_netViolence", names)
        assert "concept_net" in names
        assert "Violence" in names

    def test_chinese_names_are_not_split(self) -> None:
        names: list[str] = []
        _collect_name_value("沈听澜", names)
        assert names == ["沈听澜"]
