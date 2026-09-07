from __future__ import annotations

from types import SimpleNamespace

from novel_forge.core.constants import TaskType
from novel_forge.core.schemas.bible import StoryBible
from novel_forge.persistence.models import ProjectLayout
from novel_forge.pipeline.long.services.init.init_split_artifacts import (
    _merge_character_arc_item,
    _merge_story_fragment,
    refine_coherence_profile_split,
)


def test_merge_story_fragment_preserves_out_of_scope_fields_as_notes() -> None:
    payload = {"title": "旧题名", "premise": "核心前提", "tone": "克制"}

    _merge_story_fragment(
        payload,
        {"era": "现代", "theme": ["信任"], "title": "错误覆盖"},
        fragment_name="world",
        allowed_keys={"era", "geography", "culture", "magic_or_tech", "rules"},
    )

    assert payload["era"] == "现代"
    assert payload["title"] == "旧题名"
    assert "world.theme" in payload["notes"]
    assert "world.title" in payload["notes"]


def test_merge_story_fragment_does_not_overwrite_existing_allowed_value() -> None:
    payload = {"notes": "既有备注"}

    _merge_story_fragment(
        payload,
        {"notes": "候选备注"},
        fragment_name="themes",
        allowed_keys={"themes", "banned_intent_rules", "notes"},
    )

    assert payload["notes"] == "既有备注\n分片字段冲突（themes.notes）：保留既有值，候选值=候选备注"


def test_split_story_bible_payload_coerces_structured_notes_and_time_rules() -> None:
    payload: dict[str, object] = {}

    _merge_story_fragment(
        payload,
        {
            "title": "与君共赴2",
            "premise": "现代商战与民国旧约互相映照。",
            "tone": "温暖治愈。",
        },
        fragment_name="core",
        allowed_keys={"title", "premise", "tone"},
    )
    _merge_story_fragment(
        payload,
        {
            "era": "2025年上海与民国十二年上海。",
            "rules": [
                {
                    "rule_id": "wr001",
                    "content": "商业围剿必须通过市场机制实施。",
                }
            ],
        },
        fragment_name="world",
        allowed_keys={"era", "geography", "culture", "magic_or_tech", "rules"},
    )
    _merge_story_fragment(
        payload,
        {
            "time_convention": [
                {
                    "rule_id": "tc001",
                    "content": "现代线统一使用公历公元纪年。",
                },
                {
                    "rule_id": "tc002",
                    "content": "民国线采用民国纪年与公历双轨制。",
                },
            ],
            "address_rules": [
                {
                    "rule_id": "ar001",
                    "content": "商务场合称姓氏加职务。",
                }
            ],
        },
        fragment_name="continuity",
        allowed_keys={
            "time_convention",
            "social_hierarchy",
            "address_rules",
            "self_reference_rules",
            "etiquette_rules",
            "institution_terms",
            "material_culture",
            "anachronism_blacklist",
            "dialogue_register_rules",
        },
    )
    _merge_story_fragment(
        payload,
        {
            "themes": [{"title": "轮回与宿命的补偿"}],
            "notes": [
                "核心道具符号：怀表与金镯贯穿全剧。",
                {"content": "外滩钟楼作为跨时代地标反复出现。"},
            ],
        },
        fragment_name="themes",
        allowed_keys={"themes", "banned_intent_rules", "notes"},
    )

    bible = StoryBible.model_validate(payload)

    assert bible.time_convention == "现代线统一使用公历公元纪年。；民国线采用民国纪年与公历双轨制。"
    assert bible.notes == "核心道具符号：怀表与金镯贯穿全剧。；外滩钟楼作为跨时代地标反复出现。"
    assert bible.rules == ["商业围剿必须通过市场机制实施。"]
    assert bible.themes == ["轮回与宿命的补偿"]
    assert bible.address_rules == ["商务场合称姓氏加职务。"]


def test_merge_character_arc_item_keeps_structured_arc_extras() -> None:
    profiles = {"沈知微": {"name": "沈知微"}}

    _merge_character_arc_item(
        profiles,
        {
            "name": "沈知微",
            "arc_summary": "从防备到信任",
            "milestones": [{"chapter": 12, "event": "第一次动摇"}],
            "subplot_binds": ["医馆存亡线"],
        },
    )

    profile = profiles["沈知微"]
    assert profile["arc"] == "从防备到信任"
    assert profile["arc_milestones"] == [{"chapter": 12, "event": "第一次动摇"}]
    assert profile["arc_subplot_binds"] == ["医馆存亡线"]


async def test_refine_coherence_profile_split_coerces_object_marker_lists(tmp_path) -> None:
    responses = {
        TaskType.INIT_COHERENCE_ONTOLOGY: {
            "genre_tags": ["现代言情"],
            "narrative_modes": [{"name": "双线推进"}],
            "project_ontology": {
                "domains": [{"domain": "中医"}],
                "state_axes": [{"axis": "trust"}],
                "terminology": {"银杏叶": "信物"},
            },
        },
        TaskType.INIT_COHERENCE_PAYOFF_RULES: {
            "payoff_types": [
                {"type": "information", "trigger": "日记曝光"},
                {"type": "relationship", "trigger": "信任重建"},
            ],
            "irreversible_event_markers": [{"marker": "公开", "trigger": "主动坦白"}],
            "temporal_markers": [{"marker": "回忆", "usage": "物证链"}],
            "summary": "按蓝图精炼。",
        },
        TaskType.INIT_COHERENCE_CONFLICT_RULES: {
            "conflict_lens": [{"label": "信任状态不可回滚"}],
        },
        TaskType.INIT_COHERENCE_EXTRACTION_GUIDE: {
            "extraction_guidance": [{"value": "抽取状态变化"}],
        },
    }

    async def call_with_retry(task_type, *_args, **_kwargs):
        return responses[task_type]

    ctx = SimpleNamespace(
        call_with_retry=call_with_retry,
        layout=ProjectLayout(tmp_path / "project"),
        router=SimpleNamespace(),
        settings=SimpleNamespace(temp_refine_init_coherence_profile=0.1),
    )

    profile = await refine_coherence_profile_split(
        ctx,
        current_profile={"summary": "旧画像"},
        spec={"title": "杏林心契"},
        story_bible={},
        character_bible={},
        creative_director_packet={},
        blueprint={"synopsis": "银杏叶牵出旧约。"},
    )

    ontology = profile["project_ontology"]
    assert ontology["domains"] == ["中医"]
    assert ontology["state_axes"] == ["trust"]
    assert ontology["payoff_types"] == ["information", "relationship"]
    assert ontology["irreversible_event_markers"] == ["公开"]
    assert ontology["temporal_markers"] == ["回忆"]
    assert profile["narrative_modes"] == ["双线推进"]
    assert profile["conflict_lens"] == ["信任状态不可回滚"]
    assert profile["extraction_guidance"] == ["抽取状态变化"]
    assert profile["refined_from_blueprint"] is True
