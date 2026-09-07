"""Tests for chapter-scoped relationship selection and profile mapping."""

from __future__ import annotations

from novel_forge.story_kernel.retriever import StoryKernelRetriever
from novel_forge.story_kernel.schemas import Entity, Relationship, StoryKernel


def _entity(name: str, **kwargs) -> Entity:
    return Entity(
        entity_id=f"char_{name}",
        name=name,
        entity_type="character",
        **kwargs,
    )


def _rel(
    rel_id: str,
    src: str,
    tgt: str,
    *,
    chapter: int,
    label: str = "",
    trust: float = 0.5,
    tension: float = 0.5,
    dependency: float = 0.0,
    shift: str = "",
) -> Relationship:
    return Relationship(
        relationship_id=rel_id,
        source_entity_id=f"char_{src}",
        target_entity_id=f"char_{tgt}",
        label=label,
        trust=trust,
        tension=tension,
        dependency=dependency,
        shift_summary=shift,
        last_shift_chapter=chapter,
    )


def test_retriever_prioritizes_involved_relationships() -> None:
    state = StoryKernel(
        project_id="p",
        current_chapter=8,
        entities=[
            _entity("主角"), _entity("甲"), _entity("乙"),
            _entity("丙"), _entity("丁"), _entity("戊"),
        ],
        relationships=[
            _rel("r1", "主角", "甲", chapter=7, label="互信"),
            _rel("r2", "甲", "乙", chapter=6, label="合作"),
            _rel(
                "r3",
                "乙",
                "丙",
                chapter=4,
                label="敌对",
                tension=0.96,
                dependency=0.35,
                shift="乙在上章公开警告丙",
            ),
            _rel("r4", "丁", "戊", chapter=7, label="同行"),
        ],
    )
    retriever = StoryKernelRetriever(max_characters=3)
    ctx = retriever.get_context(
        state,
        for_chapter=8,
        involved_characters=["主角", "甲", "乙"],
        pov_character="主角",
    )
    pair_ids = {rel.pair_id for rel in ctx.active_relationships}

    # pair_id is sourced from relationship_id in the retriever
    assert "r1" in pair_ids
    assert "r2" in pair_ids
    assert "r3" in pair_ids
    rel3 = next(rel for rel in ctx.active_relationships if rel.pair_id == "r3")
    assert rel3.dependency == 0.35
    assert rel3.last_shift_event == "乙在上章公开警告丙"


def test_relationship_fallbacks_include_pressure_bands() -> None:
    from types import SimpleNamespace

    from novel_forge.core.schemas.story_state import RelationshipState
    from novel_forge.pipeline.steps.planning.hints import (
        _fallback_relationship_dynamics,
        _fallback_relationship_evolution,
    )

    rel = RelationshipState(
        pair_id="甲_乙",
        characters=["甲", "乙"],
        public_status="互相牵制",
        trust=0.82,
        tension=0.91,
        dependency=0.22,
        last_shift_event="甲在上一章被迫借助乙脱身",
    )

    dynamics = _fallback_relationship_dynamics(["甲"], active_relationships=[rel])
    assert "信任高" in dynamics
    assert "张力高" in dynamics
    assert "依赖低" in dynamics
    assert "甲在上一章被迫借助乙脱身" in dynamics

    evolution = _fallback_relationship_evolution(SimpleNamespace(active_relationships=[rel]))
    assert evolution
    assert "信任高" in evolution[0]
    assert "张力高" in evolution[0]
