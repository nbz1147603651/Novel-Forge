from __future__ import annotations

import pytest

from novel_forge.core.config import Settings
from novel_forge.memory.expression_channel import ExpressionChannelMemory
from novel_forge.memory.integration import MemoryContext
from novel_forge.narrative_state.schemas import ExpressionObservation
from novel_forge.persistence.models import ProjectLayout
from novel_forge.pipeline.steps.expression_observation_step import (
    ExpressionObservationInput,
    ExpressionObservationStep,
)
from tests.unit._fake_zvec import install_fake_zvec


def _profile(channel_id: str = "timepiece_body_signal") -> dict[str, object]:
    return {
        "channel_id": channel_id,
        "channel": "somatic_reaction",
        "label": "怀表触发后的身体惊动",
        "surface_forms": ["心口一紧", "指尖发凉"],
        "trigger_contexts": ["时间裂缝", "记忆缺口"],
        "risk_reason": "容易把每次时间异常都写成同一种身体反应。",
        "replacement_axes": ["物件操作", "对白停顿"],
        "allowed_when": "首次强感应或主线揭示节点可少量保留。",
        "cooldown_chapters": 3,
        "actor_scope": "global",
        "confidence": 0.8,
    }


def _editorial_contract_payload() -> dict[str, object]:
    return {
        "project_title": "表达记忆测试",
        "character_voices": [
            {
                "character": "她",
                "sentence_profile": "短句，少解释。",
                "explanation_bias": "用动作替代解释。",
                "emotion_syntax": "情绪升高时停顿变多。",
                "signature_moves": ["按住怀表"],
                "taboo_patterns": ["泛泛内心独白"],
                "sample_lines": ["先别说。"],
            }
        ],
        "climax_markers": [
            {
                "chapter_number": 3,
                "climax_type": "main",
                "description": "怀表线索确认。",
                "expected_aftermath_chapters": 1,
            }
        ],
        "denouement_budget": {
            "expected_chapters": 1,
            "max_confirmation_scenes": 1,
            "required_new_functions": ["余波后果"],
            "forbidden_repeats": ["重复解释怀表含义"],
        },
        "theme_policies": ["主题通过选择和后果呈现。"],
        "symbol_policies": [
            {
                "symbol": "怀表",
                "narrative_function": "时间裂缝与承诺",
                "explanation_policy": "explain_once",
                "escalation_rule": "每次出现改变语境。",
                "max_explicit_explanations": 1,
            }
        ],
        "scene_resistance_rules": [
            {
                "scene_type": "试探",
                "required_resistance": "必须有物件或空间阻力。",
                "examples": ["表盖卡住"],
            }
        ],
        "expression_channel_budget": {"somatic_reaction": 1, "action_tag": 2},
        "expression_channel_profiles": [_profile()],
        "body_signal_budget_per_high_emotion_scene": 1,
        "forbidden_confirmation_phrases": ["原来如此"],
        "revision_priorities": ["先压解释密度"],
        "revelation_ladder": [
            {
                "thread": "怀表线",
                "stage": "物证",
                "stage_order": 1,
                "target_chapter": 2,
                "trigger": "怀表发烫",
                "allowed_disclosure": "只确认时间异常。",
                "required_action_consequence": "角色主动按住表盖。",
            }
        ],
        "editorial_element_directives": [
            {
                "element_id": "expression_memory",
                "element_name": "表达记忆",
                "directive_type": "表达冷却",
                "target_window": "近章",
                "linked_characters": [],
                "requirement": "避免反复用心口反应承载警觉。",
                "success_criteria": "能改用动作或对白停顿。",
            }
        ],
        "time_bridge_policies": ["跨场景转场必须明确。"],
        "title_policy": {
            "max_reuse": 1,
            "allowed_repeated_titles": [],
            "naming_strategy": "不重复标题。",
        },
    }


class _FakeEmbeddingSource:
    async def _generate_embeddings_batch(self, texts: list[str]) -> list[list[float]]:
        return [self._embed(text) for text in texts]

    async def _generate_embedding(self, text: str) -> list[float]:
        return self._embed(text)

    def _embed(self, text: str) -> list[float]:
        bucket = sum(ord(ch) for ch in text) % 7 + 1
        return [float(bucket), 1.0, 0.5, 0.25]


@pytest.mark.asyncio
async def test_expression_observation_step_verifies_mock_quote(router, builder) -> None:
    text = "怀表在掌心发烫，她心口一紧，立刻按住表盖。"
    step = ExpressionObservationStep(router, builder, settings=Settings(_env_file=None))

    result = await step.run(
        ExpressionObservationInput(
            chapter_number=2,
            chapter_text=text,
            expression_channel_profiles=[_profile()],
            scene_intents=[{"summary": "怀表触发时间裂缝", "emotional_beat": "警觉"}],
            pov_character="她",
        )
    )

    assert len(result.observations) == 1
    observation = result.observations[0]
    assert observation.quote == "心口一紧"
    assert observation.channel_id == "timepiece_body_signal"
    assert observation.source_start == text.index("心口一紧")
    assert observation.source_text_hash == result.source_text_hash


@pytest.mark.asyncio
async def test_expression_channel_memory_uses_zvec_channel_filter(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    install_fake_zvec(monkeypatch)
    memory = ExpressionChannelMemory(
        project_id="demo",
        state_path=tmp_path / "expression_channel_memory.json",
        vector_store_path=tmp_path / "zvec_expression_vectors",
        embedding_source=_FakeEmbeddingSource(),
        vector_store_backend="zvec",
    )
    heart = ExpressionObservation(
        chapter_number=1,
        scene_index=1,
        quote="心口一紧",
        source_text_hash="hash1",
        source_start=8,
        source_end=12,
        channel_id="timepiece_body_signal",
        channel="somatic_reaction",
        profile_label="怀表触发后的身体惊动",
        trigger_context="时间裂缝",
        semantic_role="身体惊动",
        confidence=0.85,
    )
    grip = ExpressionObservation(
        chapter_number=1,
        scene_index=2,
        quote="攥紧袖口",
        source_text_hash="hash1",
        source_start=20,
        source_end=24,
        channel_id="grip_action",
        channel="action_tag",
        profile_label="克制情绪的攥握动作",
        trigger_context="压住怒意",
        semantic_role="动作标签",
        confidence=0.86,
    )

    indexed = await memory.replace_chapter_observations(
        chapter_number=1,
        source_text_hash="hash1",
        observations=[heart, grip],
    )
    records = await memory.search_expression_channel_memory(
        current_chapter=3,
        profiles=[_profile("timepiece_body_signal")],
        chapter_context="怀表再次触发时间裂缝",
        cooldown_chapters=3,
    )

    assert indexed["vectors"] == 2
    assert len(records) == 1
    assert records[0]["channel_id"] == "timepiece_body_signal"
    assert records[0]["recent_semantic_hits"][0]["quote"] == "心口一紧"
    assert all(hit["quote"] != "攥紧袖口" for hit in records[0]["recent_semantic_hits"])
    vector_store = memory._vector_store
    assert vector_store is not None
    doc_fields = next(iter(vector_store._collection.docs.values())).fields
    assert "channel_hash" in doc_fields
    assert "actor_hash" in doc_fields


@pytest.mark.asyncio
async def test_expression_channel_memory_reset_all_recreates_vector_collection(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    install_fake_zvec(monkeypatch)
    memory = ExpressionChannelMemory(
        project_id="demo",
        state_path=tmp_path / "expression_channel_memory.json",
        vector_store_path=tmp_path / "zvec_expression_vectors",
        embedding_source=_FakeEmbeddingSource(),
        vector_store_backend="zvec",
    )
    old_observation = ExpressionObservation(
        chapter_number=99,
        scene_index=1,
        quote="心口一紧",
        source_text_hash="old",
        source_start=0,
        source_end=4,
        channel_id="timepiece_body_signal",
        channel="somatic_reaction",
        profile_label="旧观察",
        trigger_context="旧章节",
        semantic_role="身体惊动",
        confidence=0.8,
    )
    new_observation = old_observation.model_copy(
        update={
            "chapter_number": 1,
            "source_text_hash": "new",
            "profile_label": "新观察",
        }
    )

    await memory.replace_chapter_observations(
        chapter_number=99,
        source_text_hash="old",
        observations=[old_observation],
    )
    memory.reset_all(reset_vectors=True)
    await memory.replace_chapter_observations(
        chapter_number=1,
        source_text_hash="new",
        observations=[new_observation],
    )

    assert memory.observation_count == 1
    assert 99 not in memory._chapter_observations
    assert 1 in memory._chapter_observations
    assert len(list(tmp_path.glob("zvec_expression_vectors.corrupt-*rebuild*"))) == 1


@pytest.mark.asyncio
async def test_memory_context_rebuild_expression_memory_from_archived_chapters(
    router,
    builder,
    tmp_storage,
) -> None:
    project_id = "expression_rebuild_demo"
    layout = ProjectLayout(tmp_storage.ensure_project_dir(project_id))
    layout.plans_dir.mkdir(parents=True, exist_ok=True)
    layout.chapters_dir.mkdir(parents=True, exist_ok=True)
    tmp_storage.save_json(
        layout.editorial_contract_path,
        _editorial_contract_payload(),
    )
    layout.chapter_path(1).write_text(
        "怀表在掌心发烫，她心口一紧，立刻按住表盖。",
        encoding="utf-8",
    )
    tmp_storage.save_json(
        layout.chapter_plan_path(1),
        {
            "pov_character": "她",
            "scene_intents": [{"summary": "怀表触发时间裂缝"}],
        },
    )

    memory_context = MemoryContext.create_from_settings(
        router=router,
        builder=builder,
        settings=Settings(_env_file=None),
        project_id=project_id,
        storage=tmp_storage,
    )

    result = await memory_context.rebuild_expression_channel_memory()

    assert result["rebuilt_chapters"] == 1
    assert result["observations"] == 1
    assert result["vectors"] == 1
    assert result["status"]["expression_observation_count"] == 1
