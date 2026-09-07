"""Unit tests for entity reconciliation (RECONCILE_ENTITIES) and related helpers."""
# ruff: noqa: I001
from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

# Import init_service first to trigger cross-module namespace linking
# that makes private helpers (_positive_int, _init_coherence_list) available
# in init_source_artifact_repair's namespace.
import novel_forge.pipeline.long.services.init.init_service  # noqa: F401

from novel_forge.common.constants import TaskType
from novel_forge.core.format_contracts import validate_json_output_contract
from novel_forge.core.parsing.response_schemas import validate_response_schema
from novel_forge.core.schemas.artifacts import CanonicalEntityRef
from novel_forge.pipeline.long.services.init.init_source_artifact_repair import (
    SourceArtifactRepairPlan,
    _build_reconciliation_chapter_context,
    _extract_unresolved_names_from_plan,
    _init_source_artifact_repair_rounds,
    _resolve_source_artifact_entity_alias,
    reconcile_unresolved_entities,
)
from novel_forge.pipeline.long.services.context.source_artifacts import (
    _infer_world_entity_type,
    build_init_entity_catalog,
)


# ── helpers ──────────────────────────────────────────────────────────────

def _repair_plan_with(names: list[str]) -> SourceArtifactRepairPlan:
    """Build a minimal repair plan with unresolved entity names."""
    issues = [
        {
            "code": "unresolved_contract_entity",
            "path": f"by_chapter.{i + 1}",
            "message": f"第 {i + 1} 章契约引用未登记角色/实体：{name}",
            "unresolved_entity": name,
        }
        for i, name in enumerate(names)
    ]
    return SourceArtifactRepairPlan(
        repairable_issues=issues,
        non_repairable_issues=[],
    )


def _entity_catalog(
    entities: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build a minimal entity catalog."""
    ents = entities or [
        {
            "entity_id": "char_xuanyu",
            "canonical_name": "玄昱",
            "entity_type": "character",
            "aliases": ["玄昱大人"],
        },
        {
            "entity_id": "char_shenqing",
            "canonical_name": "沈清浅",
            "entity_type": "character",
            "aliases": [],
        },
    ]
    alias_map: dict[str, dict[str, str]] = {}
    for ent in ents:
        for token in [ent["entity_id"], ent["canonical_name"], *ent.get("aliases", [])]:
            alias_map.setdefault(
                token,
                {
                    "entity_id": ent["entity_id"],
                    "canonical_name": ent["canonical_name"],
                    "entity_type": ent["entity_type"],
                },
            )
    return {
        "allowed_entities": ents,
        "allowed_entity_ids": [e["entity_id"] for e in ents],
        "allowed_entity_names": [e["canonical_name"] for e in ents],
        "alias_to_entity": alias_map,
    }


# ── _extract_unresolved_names_from_plan ──────────────────────────────────


def test_extract_unresolved_names_deduplicates() -> None:
    plan = _repair_plan_with(["沈清浅", "魏帝玄苍之妃", "沈清浅"])
    names = _extract_unresolved_names_from_plan(plan)
    assert names == ["沈清浅", "魏帝玄苍之妃"]


def test_extract_unresolved_names_empty_plan() -> None:
    plan = SourceArtifactRepairPlan(
        repairable_issues=[],
        non_repairable_issues=[],
    )
    assert _extract_unresolved_names_from_plan(plan) == []


# ── _build_reconciliation_chapter_context ─────────────────────────────────


def test_build_chapter_context_with_outline() -> None:
    plan = _repair_plan_with(["魏帝玄苍之妃"])
    chapter_contracts = {
        "chapter_contracts": [
            {
                "chapter_number": 1,
                "summary": "玄昱入宫探查",
                "pov_character": "玄昱",
                "required_characters": ["玄昱", "魏帝"],
                "required_events": ["入宫", "发现线索"],
                "new_character_candidates": [],
                "new_entity_candidates": [],
            }
        ]
    }
    outline = {
        "chapters": [
            {"chapter_number": 1, "summary": "主角入宫开始调查"},
        ]
    }
    ctx = _build_reconciliation_chapter_context(
        chapter_contracts, plan, outline
    )
    assert "第 1 章" in ctx
    assert "玄昱入宫探查" in ctx
    assert "主角入宫开始调查" in ctx
    assert "玄昱" in ctx


def test_build_chapter_context_empty_plan() -> None:
    plan = SourceArtifactRepairPlan(
        repairable_issues=[],
        non_repairable_issues=[],
    )
    result = _build_reconciliation_chapter_context({}, plan)
    assert result == "(无受影响的章节上下文)"


# ── _infer_world_entity_type (fixed) ────────────────────────────────────


def test_infer_world_entity_type_character_path() -> None:
    """Character-typed paths should return 'character' regardless of suffix."""
    result = _infer_world_entity_type("沈清浅", ("characters", "pov_character"))
    assert result == "character"


def test_infer_world_entity_type_unknown_for_cjk() -> None:
    """CJK names without known suffix should return 'unknown' (not empty)."""
    result = _infer_world_entity_type("魏帝玄苍之妃", ("events",))
    assert result == "unknown"


def test_infer_world_entity_type_empty_for_non_cjk() -> None:
    """Non-CJK tokens should return empty string."""
    result = _infer_world_entity_type("London", ("locations",))
    assert result == ""


def test_infer_world_entity_type_does_not_assign_semantics_from_suffix() -> None:
    result = _infer_world_entity_type("天机阁", ("organizations",))
    assert result == "unknown"


# ── build_init_entity_catalog with extra_refs ───────────────────────────


def test_build_entity_catalog_with_extra_refs() -> None:
    """extra_refs should be merged into the catalog."""
    entity_graph = SimpleNamespace(
        entities=[
            SimpleNamespace(
                entity_id="char_a",
                name="角色A",
                entity_type="character",
                aliases=[],
            )
        ]
    )
    character_bible = SimpleNamespace(
        characters=[
            SimpleNamespace(
                name="角色B",
                character_id="char_b",
                role="supporting",
                aliases=[],
            )
        ]
    )
    extra = [
        CanonicalEntityRef(
            entity_id="char_extra",
            canonical_name="额外角色",
            entity_type="character",
            aliases=["Extra"],
        )
    ]
    catalog = build_init_entity_catalog(
        entity_graph,
        character_bible,
        extra_refs=extra,
    )
    names = catalog["allowed_entity_names"]
    assert "额外角色" in names
    assert "Extra" in catalog["alias_to_entity"]


def test_build_entity_catalog_dedupes_extra_refs() -> None:
    """extra_refs should not duplicate an existing canonical entity."""
    entity_graph = {
        "entities": [
            {
                "entity_id": "char_a",
                "name": "角色A",
                "entity_type": "character",
                "aliases": ["旧称"],
            }
        ]
    }
    character_bible = SimpleNamespace(characters=[])
    extra = [
        CanonicalEntityRef(
            entity_id="char_a",
            canonical_name="角色A",
            entity_type="character",
            aliases=["新称"],
        )
    ]
    catalog = build_init_entity_catalog(
        entity_graph,
        character_bible,
        extra_refs=extra,
    )
    matches = [
        item
        for item in catalog["allowed_entities"]
        if item["canonical_name"] == "角色A"
    ]
    assert len(matches) == 1
    assert {"旧称", "新称"}.issubset(set(matches[0]["aliases"]))


# ── Exact, LLM-adjudicated alias application ─────────────────────────────


def test_resolve_entity_alias_does_not_guess_from_edit_distance() -> None:
    catalog = _entity_catalog([
        {
            "entity_id": "loc_tianji",
            "canonical_name": "天机阁",
            "entity_type": "location",
            "aliases": [],
        },
    ])
    result = _resolve_source_artifact_entity_alias("天机搁", catalog)
    assert result is None


def test_resolve_entity_alias_applies_exact_catalog_decision() -> None:
    catalog = _entity_catalog([
        {
            "entity_id": "char_shenqing",
            "canonical_name": "沈清浅",
            "entity_type": "character",
            "aliases": [],
        },
    ])
    catalog["alias_to_entity"]["沉清浅"] = {
        "entity_id": "char_shenqing",
        "canonical_name": "沈清浅",
        "entity_type": "character",
    }
    result = _resolve_source_artifact_entity_alias("沉清浅", catalog)
    assert result is not None
    assert result["canonical_name"] == "沈清浅"
    assert result["match_reason"] == "catalog_alias"


# ── _init_source_artifact_repair_rounds default ──────────────────────────


def test_default_repair_rounds_is_two() -> None:
    """Default should be 2 rounds."""
    settings = SimpleNamespace()
    assert _init_source_artifact_repair_rounds(settings) == 2


def test_repair_rounds_respects_setting() -> None:
    settings = SimpleNamespace(init_source_artifact_repair_rounds=3)
    assert _init_source_artifact_repair_rounds(settings) == 3


def test_repair_rounds_clamped() -> None:
    settings = SimpleNamespace(init_source_artifact_repair_rounds=10)
    assert _init_source_artifact_repair_rounds(settings) == 4


# ── reconcile_unresolved_entities (async, with mock) ─────────────────────


def test_reconcile_entities_payload_passes_schema_contract_before_consumer() -> None:
    payload = {
        "resolutions": [
            {
                "unresolved_name": "玄昱大人",
                "resolution": "alias",
                "canonical_name": "玄昱",
                "entity_type": "character",
                "relationship": "同一角色别称",
                "confidence": 0.92,
                "reasoning": "上下文指向同一角色",
            }
        ]
    }

    validate_response_schema(payload, TaskType.RECONCILE_ENTITIES)
    validate_json_output_contract(TaskType.RECONCILE_ENTITIES, payload)


def test_reconcile_entities_contract_rejects_legacy_bare_list() -> None:
    payload = [
        {
            "unresolved_name": "玄昱大人",
            "resolution": "alias",
            "canonical_name": "玄昱",
            "confidence": 0.92,
            "reasoning": "上下文指向同一角色",
        }
    ]

    with pytest.raises(ValueError, match="valid dictionary"):
        validate_response_schema(payload, TaskType.RECONCILE_ENTITIES)  # type: ignore[arg-type]


async def test_reconcile_no_unresolved_returns_empty() -> None:
    """When repair plan has no issues, should return empty immediately."""
    plan = SourceArtifactRepairPlan(
        repairable_issues=[],
        non_repairable_issues=[],
    )
    ctx = SimpleNamespace(
        call_with_retry=AsyncMock(),
        on_step=lambda *a: None,
        settings=SimpleNamespace(),
        router=SimpleNamespace(),
    )
    refs, record = await reconcile_unresolved_entities(
        ctx,
        chapter_contracts={},
        entity_catalog=_entity_catalog(),
        repair_plan=plan,
    )
    assert refs == []
    assert record["status"] == "no_unresolved"


async def test_reconcile_alias_resolution() -> None:
    """LLM alias resolution should update alias_to_entity."""
    plan = _repair_plan_with(["玄昱大人"])
    catalog = _entity_catalog()

    async def mock_call(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return {
            "resolutions": [
                {
                    "unresolved_name": "玄昱大人",
                    "resolution": "alias",
                    "canonical_name": "玄昱",
                    "entity_type": "character",
                    "confidence": 0.95,
                    "reasoning": "尊称",
                }
            ]
        }

    ctx = SimpleNamespace(
        call_with_retry=mock_call,
        on_step=lambda *a: None,
        settings=SimpleNamespace(temp_reconcile_entities=0.2),
        router=SimpleNamespace(),
    )
    refs, record = await reconcile_unresolved_entities(
        ctx,
        chapter_contracts={"chapter_contracts": []},
        entity_catalog=catalog,
        repair_plan=plan,
    )
    assert record["status"] == "completed"
    assert record["resolution_count"] == 1
    # alias should now be in the catalog's alias_to_entity
    assert "玄昱大人" in catalog["alias_to_entity"]
    assert catalog["alias_to_entity"]["玄昱大人"]["canonical_name"] == "玄昱"


async def test_reconcile_alias_cannot_use_another_mentions_candidate() -> None:
    plan = _repair_plan_with(["玄昱大人"])
    catalog = _entity_catalog()

    async def mock_call(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return {
            "resolutions": [
                {
                    "unresolved_name": "玄昱大人",
                    "resolution": "alias",
                    "canonical_name": "沈清浅",
                    "confidence": 0.96,
                    "reasoning": "错误地使用了其他候选。",
                }
            ]
        }

    ctx = SimpleNamespace(
        call_with_retry=mock_call,
        on_step=lambda *a: None,
        settings=SimpleNamespace(temp_reconcile_entities=0.2),
        router=SimpleNamespace(),
    )

    refs, record = await reconcile_unresolved_entities(
        ctx,
        chapter_contracts={"chapter_contracts": []},
        entity_catalog=catalog,
        repair_plan=plan,
    )

    assert refs == []
    assert record["resolution_count"] == 0
    assert record["rejected"][0]["reason"] == "canonical_not_in_retrieved_candidates"
    assert catalog["alias_to_entity"]["玄昱大人"]["canonical_name"] == "玄昱"


async def test_reconcile_new_entity_creates_ref() -> None:
    """LLM new_entity resolution should create a CanonicalEntityRef."""
    plan = _repair_plan_with(["新角色"])
    catalog = _entity_catalog()

    async def mock_call(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return {
            "resolutions": [
                {
                    "unresolved_name": "新角色",
                    "resolution": "new_entity",
                    "entity_type": "character",
                    "confidence": 0.85,
                    "reasoning": "叙事必需的新角色",
                }
            ]
        }

    ctx = SimpleNamespace(
        call_with_retry=mock_call,
        on_step=lambda *a: None,
        settings=SimpleNamespace(temp_reconcile_entities=0.2),
        router=SimpleNamespace(),
    )
    refs, record = await reconcile_unresolved_entities(
        ctx,
        chapter_contracts={"chapter_contracts": []},
        entity_catalog=catalog,
        repair_plan=plan,
    )
    assert len(refs) == 1
    assert refs[0].canonical_name == "新角色"
    assert refs[0].entity_type == "character"
    assert record["resolution_count"] == 1


async def test_reconcile_low_confidence_rejected() -> None:
    """Resolutions below confidence threshold should be rejected."""
    plan = _repair_plan_with(["不确定角色"])
    catalog = _entity_catalog()

    async def mock_call(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return {
            "resolutions": [
                {
                    "unresolved_name": "不确定角色",
                    "resolution": "alias",
                    "canonical_name": "玄昱",
                    "confidence": 0.3,
                    "reasoning": "不确定",
                }
            ]
        }

    ctx = SimpleNamespace(
        call_with_retry=mock_call,
        on_step=lambda *a: None,
        settings=SimpleNamespace(temp_reconcile_entities=0.2),
        router=SimpleNamespace(),
    )
    refs, record = await reconcile_unresolved_entities(
        ctx,
        chapter_contracts={"chapter_contracts": []},
        entity_catalog=catalog,
        repair_plan=plan,
    )
    assert refs == []
    assert record["resolution_count"] == 0
    assert len(record["rejected"]) == 1


async def test_reconcile_non_numeric_confidence_rejected() -> None:
    """Malformed confidence values should reject that resolution, not raise."""
    plan = _repair_plan_with(["可疑角色"])
    catalog = _entity_catalog()

    async def mock_call(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return {
            "resolutions": [
                {
                    "unresolved_name": "可疑角色",
                    "resolution": "alias",
                    "canonical_name": "玄昱",
                    "confidence": "高",
                    "reasoning": "模型返回了非数字置信度",
                }
            ]
        }

    ctx = SimpleNamespace(
        call_with_retry=mock_call,
        on_step=lambda *a: None,
        settings=SimpleNamespace(temp_reconcile_entities=0.2),
        router=SimpleNamespace(),
    )
    refs, record = await reconcile_unresolved_entities(
        ctx,
        chapter_contracts={"chapter_contracts": []},
        entity_catalog=catalog,
        repair_plan=plan,
    )
    assert refs == []
    assert record["resolution_count"] == 0
    assert record["rejected"][0]["reason"] == "below_threshold"


async def test_reconcile_llm_failure_graceful() -> None:
    """If LLM call fails, should return gracefully without blocking."""
    plan = _repair_plan_with(["某角色"])
    catalog = _entity_catalog()

    async def mock_call(*args: Any, **kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("LLM unavailable")

    ctx = SimpleNamespace(
        call_with_retry=mock_call,
        on_step=lambda *a: None,
        settings=SimpleNamespace(temp_reconcile_entities=0.2),
        router=SimpleNamespace(),
    )
    refs, record = await reconcile_unresolved_entities(
        ctx,
        chapter_contracts={"chapter_contracts": []},
        entity_catalog=catalog,
        repair_plan=plan,
    )
    assert refs == []
    assert record["status"] == "llm_failed"


async def test_reconcile_remove_resolution() -> None:
    """Remove resolution should be accepted but produce no refs."""
    plan = _repair_plan_with(["十年离散共同收束"])
    catalog = _entity_catalog()

    async def mock_call(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return {
            "resolutions": [
                {
                    "unresolved_name": "十年离散共同收束",
                    "resolution": "remove",
                    "confidence": 0.85,
                    "reasoning": "抽象概念，非实体",
                }
            ]
        }

    ctx = SimpleNamespace(
        call_with_retry=mock_call,
        on_step=lambda *a: None,
        settings=SimpleNamespace(temp_reconcile_entities=0.2),
        router=SimpleNamespace(),
    )
    refs, record = await reconcile_unresolved_entities(
        ctx,
        chapter_contracts={"chapter_contracts": []},
        entity_catalog=catalog,
        repair_plan=plan,
    )
    assert refs == []
    assert record["resolution_count"] == 1
    assert record["accepted"][0]["resolution"] == "remove"
