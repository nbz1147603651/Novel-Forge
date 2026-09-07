"""Tests for cleanup_concept_pollution in narrative_state/store.py."""

from __future__ import annotations

from types import SimpleNamespace

from novel_forge.narrative_state.schemas import EntityRecord, EntityRegistry
from novel_forge.narrative_state.store import NarrativeStateStore, cleanup_concept_pollution


def _make_registry(entities: list[EntityRecord]) -> EntityRegistry:
    return EntityRegistry(entities=entities)


def _concept(
    name: str,
    entity_id: str = "test_id",
    *,
    source: str = "",
) -> EntityRecord:
    return EntityRecord(
        entity_id=entity_id,
        name=name,
        entity_type="concept",
        source=source,
    )


def _character(name: str, entity_id: str = "char_id") -> EntityRecord:
    return EntityRecord(
        entity_id=entity_id,
        name=name,
        entity_type="character",
    )


def _location(name: str, entity_id: str = "loc_id") -> EntityRecord:
    return EntityRecord(
        entity_id=entity_id,
        name=name,
        entity_type="location",
    )


class TestCleanupLongConcepts:
    """Concepts with name > 50 chars are removed."""

    def test_concept_name_exactly_51_chars(self) -> None:
        name = "A" * 51
        registry = _make_registry([_concept(name)])

        removed = cleanup_concept_pollution(registry)

        assert removed == 1
        assert len(registry.entities) == 0

    def test_concept_name_100_chars(self) -> None:
        name = "这是一段非常长的提示词片段" * 10  # well over 50
        registry = _make_registry([_concept(name)])

        removed = cleanup_concept_pollution(registry)

        assert removed == 1
        assert len(registry.entities) == 0

    def test_concept_name_exactly_50_chars_preserved(self) -> None:
        name = "A" * 50
        registry = _make_registry([_concept(name)])

        removed = cleanup_concept_pollution(registry)

        assert removed == 0
        assert len(registry.entities) == 1


class TestCleanupDerivedInitConcepts:
    """Concepts mechanically extracted from init metadata are removed."""

    def test_deterministic_init_registry_concept_removed(self) -> None:
        registry = _make_registry(
            [
                _concept("宿命与救赎", source="deterministic_init_registry"),
            ]
        )

        removed = cleanup_concept_pollution(registry)

        assert removed == 1
        assert registry.entities == []


class TestCleanupSentenceConcepts:
    """Concepts ending with 。 and > 20 chars are removed."""

    def test_concept_ending_with_period_over_20_chars(self) -> None:
        name = "这是一个以句号结尾的指令片段。"  # 13 chars, need > 20
        name = "这是一个以句号结尾的较长指令片段句子。"  # 17 Chinese chars ≈ 34 bytes but 17 chars
        # Let me count: 这是一个以句号结尾的较长指令片段句子 = 16 chars + 。= 17
        # Need > 20 chars
        name = "这是一段非常长的以句号结尾的提示指令片段句子内容。"  # 22 chars
        registry = _make_registry([_concept(name)])

        removed = cleanup_concept_pollution(registry)

        assert removed == 1
        assert len(registry.entities) == 0

    def test_concept_ending_with_period_exactly_20_chars_preserved(self) -> None:
        # 20 chars ending with 。
        name = "A" * 19 + "。"
        assert len(name) == 20
        registry = _make_registry([_concept(name)])

        removed = cleanup_concept_pollution(registry)

        assert removed == 0
        assert len(registry.entities) == 1

    def test_concept_ending_with_period_21_chars_removed(self) -> None:
        name = "A" * 20 + "。"
        assert len(name) == 21
        registry = _make_registry([_concept(name)])

        removed = cleanup_concept_pollution(registry)

        assert removed == 1
        assert len(registry.entities) == 0

    def test_short_concept_ending_with_period_preserved(self) -> None:
        name = "短句。"
        registry = _make_registry([_concept(name)])

        removed = cleanup_concept_pollution(registry)

        assert removed == 0


class TestCleanupInstructionPatterns:
    """Concepts matching instruction patterns and > 12 chars are removed."""

    def test_instruction_pattern_must(self) -> None:
        name = "必须遵守这个世界规则"  # 10 chars, need > 12
        name = "必须严格遵守这个世界的规则约束"  # 14 chars
        registry = _make_registry([_concept(name)])

        removed = cleanup_concept_pollution(registry)

        assert removed == 1

    def test_instruction_pattern_forbidden(self) -> None:
        name = "禁止使用现代的词汇表达方式"  # 13 chars, > 12
        registry = _make_registry([_concept(name)])

        removed = cleanup_concept_pollution(registry)

        assert removed == 1

    def test_instruction_pattern_short_preserved(self) -> None:
        """Short names with instruction patterns are preserved (≤ 12 chars)."""
        name = "必须修改"  # 4 chars
        registry = _make_registry([_concept(name)])

        removed = cleanup_concept_pollution(registry)

        assert removed == 0
        assert len(registry.entities) == 1

    def test_instruction_pattern_exactly_13_chars(self) -> None:
        name = "需要控制情绪变化方向"  # 10 chars... let me count: 需要控制情绪变化方向 = 10
        # Need > 12 chars
        name = "需要严格控制角色情绪的变化方向"  # 14 chars
        registry = _make_registry([_concept(name)])

        removed = cleanup_concept_pollution(registry)

        assert removed == 1


class TestNonConceptPreserved:
    """Non-concept entities are never removed, even if they match rules."""

    def test_character_with_long_name_preserved(self) -> None:
        name = "A" * 100
        registry = _make_registry([_character(name)])

        removed = cleanup_concept_pollution(registry)

        assert removed == 0
        assert len(registry.entities) == 1

    def test_location_with_instruction_pattern_preserved(self) -> None:
        name = "必须遵守这个世界的所有规则和约定"
        registry = _make_registry([_location(name)])

        removed = cleanup_concept_pollution(registry)

        assert removed == 0
        assert len(registry.entities) == 1

    def test_character_ending_with_period_preserved(self) -> None:
        name = "A" * 25 + "。"
        registry = _make_registry([_character(name)])

        removed = cleanup_concept_pollution(registry)

        assert removed == 0


class TestMixedRegistry:
    """Cleanup correctly handles mixed entity types."""

    def test_mixed_types_selective_removal(self) -> None:
        entities = [
            _character("林远", "c1"),
            _concept("正常概念", "cp1"),
            _concept("A" * 60, "cp2"),  # should be removed
            _location("废弃图书馆", "l1"),
            _concept(
                "这是一个以句号结尾的非常长的指令片段句子内容展示。", "cp3"
            ),  # > 20 chars + 。
            _character("苏晴", "c2"),
        ]
        registry = _make_registry(entities)

        removed = cleanup_concept_pollution(registry)

        assert removed == 2
        remaining_ids = {e.entity_id for e in registry.entities}
        assert "c1" in remaining_ids
        assert "cp1" in remaining_ids
        assert "cp2" not in remaining_ids
        assert "l1" in remaining_ids
        assert "cp3" not in remaining_ids
        assert "c2" in remaining_ids

    def test_empty_registry(self) -> None:
        registry = _make_registry([])

        removed = cleanup_concept_pollution(registry)

        assert removed == 0
        assert len(registry.entities) == 0

    def test_all_clean_concepts(self) -> None:
        """When all concepts are clean, nothing is removed."""
        entities = [
            _concept("短句", "cp1"),
            _concept("正常概念名", "cp2"),
            _concept("ABC", "cp3"),
        ]
        registry = _make_registry(entities)

        removed = cleanup_concept_pollution(registry)

        assert removed == 0
        assert len(registry.entities) == 3

    def test_whitespace_trimmed_before_checks(self) -> None:
        """Name is stripped before length checks."""
        # 51 chars including leading/trailing spaces, but stripped name is shorter
        name = "  " + "A" * 48 + "  "
        registry = _make_registry([_concept(name)])

        removed = cleanup_concept_pollution(registry)

        # stripped name is 48 chars, which is ≤ 50, so preserved
        assert removed == 0


def test_store_load_and_save_apply_concept_cleanup(tmp_path) -> None:
    store = NarrativeStateStore(tmp_path)
    registry = EntityRegistry(
        entities=[
            _concept("宿命", "concept_fate", source="主题分析"),
            _concept("business_market_feedback", "bad", source="deterministic_init_registry"),
        ]
    )

    store.save_entity_registry(registry)
    loaded = store.load_entity_registry()

    assert [entity.name for entity in loaded.entities] == ["宿命"]


def test_save_report_artifacts_does_not_create_projection_when_absent(tmp_path) -> None:
    store = NarrativeStateStore(tmp_path)
    prompt_payload = store.projection_for_prompt()
    assert prompt_payload["last_chapter"] == 0
    assert not store.projection_path.exists()

    report = SimpleNamespace(
        chapter_number=1,
        source_text_hash="hash",
        candidates=[],
        omitted_candidates=[],
        decisions=[],
        ledger_entries=[],
        contract_coverage=None,
        final_adjudication=SimpleNamespace(
            verdict="accept",
            severity="low",
            confidence=1.0,
            should_block_archive=False,
            accepted_candidate_ids=[],
            pending_candidate_ids=[],
            pending_items=[],
            repair_candidate_ids=[],
            repair_issues=[],
        ),
    )

    store.save_report_artifacts(
        report=report,
        report_paths=[store.adjudication_report_path(1)],
    )

    assert not store.projection_path.exists()
    assert store.memory_index_path.exists()
