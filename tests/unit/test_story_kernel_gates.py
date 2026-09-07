"""Structural gate tests for StoryKernel.

Local gates are deliberately format/structure guards. Semantic contradictions
are judged by the LLM-backed init, continuity, causal, and adjudication steps.
"""

from __future__ import annotations

from novel_forge.story_kernel.gates import GateResult, InitTruthGate, PreArchiveTruthGate
from novel_forge.story_kernel.schemas import (
    BusinessDependency,
    Entity,
    KnowledgeLedger,
    ObjectLedger,
    PromiseLedger,
    Relationship,
    StoryKernel,
    TimelineAnchor,
    WorldRule,
)


def _valid_kernel() -> StoryKernel:
    return StoryKernel(
        project_id="test-project",
        title="测试小说",
        premise="一个关于勇气与成长的故事，主角在旅途中发现真正的力量。",
        current_chapter=2,
        world_rules=[
            WorldRule(rule_id="wr-1", content="魔法需要代价", severity="hard"),
            WorldRule(rule_id="wr-2", content="时间线按章节推进", severity="hard"),
            WorldRule(rule_id="wr-3", content="角色行动需要动机", severity="hard"),
        ],
        entities=[
            Entity(
                entity_id="char-protagonist",
                name="沈念卿",
                entity_type="character",
                attributes={"role": "protagonist", "gender": "女"},
            ),
            Entity(
                entity_id="char-elder",
                name="沈鹤卿",
                entity_type="character",
                attributes={"role": "supporting", "gender": "男"},
            ),
            Entity(
                entity_id="item-watch",
                name="怀表",
                entity_type="item",
            ),
        ],
        relationships=[
            Relationship(
                relationship_id="rel-1",
                source_entity_id="char-protagonist",
                target_entity_id="char-elder",
                relation_type="family",
                label="长辈",
            ),
        ],
        timeline=[
            TimelineAnchor(anchor_id="ta-1", chapter=1, event="故事开始"),
        ],
        object_ledger=[
            ObjectLedger(
                entry_id="obj-1",
                item_name="怀表",
                item_entity_id="item-watch",
                owner_entity_id="char-protagonist",
                introduced_chapter=1,
            )
        ],
        knowledge_ledger=[
            KnowledgeLedger(
                entry_id="kn-1",
                entity_id="char-protagonist",
                fact="沈鹤卿知道旧事",
                source_chapter=1,
            )
        ],
        promise_ledger=[
            PromiseLedger(
                entry_id="pr-1",
                description="怀表来历",
                planted_chapter=1,
                status="planted",
            )
        ],
    )


class TestGateResult:
    def test_gate_result_fields(self) -> None:
        result = GateResult(passed=True, violations=[], warnings=[])
        assert result.passed is True
        assert result.violations == []
        assert result.warnings == []


class TestInitTruthGateStructure:
    def test_valid_kernel_passes(self) -> None:
        result = InitTruthGate.validate(_valid_kernel())
        assert result.passed is True
        assert result.violations == []

    def test_required_title_and_premise_are_structural(self) -> None:
        kernel = _valid_kernel().model_copy(update={"title": "", "premise": ""})
        result = InitTruthGate.validate(kernel)
        assert result.passed is False
        assert any("title" in issue for issue in result.violations)
        assert any("premise" in issue for issue in result.violations)

    def test_missing_protagonist_fails(self) -> None:
        kernel = _valid_kernel()
        entities = [
            entity.model_copy(update={"attributes": {"role": "supporting"}})
            for entity in kernel.entities
        ]
        result = InitTruthGate.validate(kernel.model_copy(update={"entities": entities}))
        assert result.passed is False
        assert any("protagonist" in issue for issue in result.violations)

    def test_relationship_endpoint_must_exist(self) -> None:
        kernel = _valid_kernel()
        relationships = [
            kernel.relationships[0].model_copy(update={"target_entity_id": "missing"})
        ]
        result = InitTruthGate.validate(kernel.model_copy(update={"relationships": relationships}))
        assert result.passed is False
        assert any("target_entity_id" in issue for issue in result.violations)

    def test_business_dependency_cycle_fails(self) -> None:
        kernel = _valid_kernel().model_copy(
            update={
                "business_dependencies": [
                    BusinessDependency(
                        dependency_id="bd-1",
                        source_id="a",
                        target_id="b",
                        dependency_type="requires",
                    ),
                    BusinessDependency(
                        dependency_id="bd-2",
                        source_id="b",
                        target_id="a",
                        dependency_type="requires",
                    ),
                ]
            }
        )
        result = InitTruthGate.validate(kernel)
        assert result.passed is False
        assert any("循环依赖" in issue for issue in result.violations)

    def test_semantic_relationship_labels_are_not_locally_judged(self) -> None:
        kernel = _valid_kernel()
        relationships = [
            Relationship(
                relationship_id="rel-kinship",
                source_entity_id="char-protagonist",
                target_entity_id="char-elder",
                relation_type="family",
                label="外祖母",
            )
        ]
        result = InitTruthGate.validate(kernel.model_copy(update={"relationships": relationships}))
        assert result.passed is True

    def test_world_rule_keyword_conflicts_are_not_locally_judged(self) -> None:
        kernel = _valid_kernel().model_copy(
            update={
                "world_rules": [
                    WorldRule(rule_id="wr-1", content="不涉及轮回或超自然。", severity="hard"),
                    WorldRule(rule_id="wr-2", content="时间不可逆转。", severity="hard"),
                    WorldRule(rule_id="wr-3", content="人物行动必须现实。", severity="hard"),
                ],
                "entities": [
                    entity.model_copy(update={"notes": "前世曾经叫顾锦绣。"})
                    if entity.entity_id == "char-protagonist"
                    else entity
                    for entity in _valid_kernel().entities
                ],
            }
        )
        result = InitTruthGate.validate(kernel)
        assert result.passed is True


class TestPreArchiveTruthGateStructure:
    def test_valid_kernel_passes_even_with_unregistered_prose_name(self) -> None:
        result = PreArchiveTruthGate.validate(
            "赵六说他拿出了天机镜。",
            _valid_kernel(),
            chapter_number=2,
        )
        assert result.passed is True

    def test_future_knowledge_fails(self) -> None:
        kernel = _valid_kernel()
        knowledge = [
            KnowledgeLedger(
                entry_id="kn-future",
                entity_id="char-protagonist",
                fact="未来才知道的秘密",
                source_chapter=5,
            )
        ]
        result = PreArchiveTruthGate.validate(
            "",
            kernel.model_copy(update={"knowledge_ledger": knowledge}),
            chapter_number=2,
        )
        assert result.passed is False
        assert any("未来知识" in issue for issue in result.violations)

    def test_paid_promise_requires_payoff_chapter(self) -> None:
        kernel = _valid_kernel()
        promises = [
            PromiseLedger(
                entry_id="pr-paid",
                description="怀表来历",
                planted_chapter=1,
                status="paid",
                payoff_chapter=0,
            )
        ]
        result = PreArchiveTruthGate.validate(
            "",
            kernel.model_copy(update={"promise_ledger": promises}),
            chapter_number=2,
        )
        assert result.passed is False
        assert any("payoff_chapter" in issue for issue in result.violations)

    def test_future_timeline_entry_fails(self) -> None:
        kernel = _valid_kernel()
        timeline = [
            TimelineAnchor(anchor_id="ta-1", chapter=1, event="开始"),
            TimelineAnchor(anchor_id="ta-future", chapter=5, event="未来事件"),
        ]
        result = PreArchiveTruthGate.validate(
            "",
            kernel.model_copy(update={"timeline": timeline}),
            chapter_number=2,
        )
        assert result.passed is False
        assert any("未来章节" in issue for issue in result.violations)

    def test_object_references_must_exist_when_ids_are_present(self) -> None:
        kernel = _valid_kernel()
        object_ledger = [
            ObjectLedger(
                entry_id="obj-bad",
                item_name="怀表",
                item_entity_id="missing-item",
                owner_entity_id="char-protagonist",
            )
        ]
        result = PreArchiveTruthGate.validate(
            "",
            kernel.model_copy(update={"object_ledger": object_ledger}),
            chapter_number=2,
        )
        assert result.passed is False
        assert any("item_entity_id" in issue for issue in result.violations)
