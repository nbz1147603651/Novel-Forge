"""Regression tests for split character-bible artifact merging."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from novel_forge.core.constants import TaskType
from novel_forge.core.domain.character_boundary import canonicalize_relationship_items
from novel_forge.core.schemas.bible import CharacterBible, StoryBible
from novel_forge.narrative_state.schemas import EntityRecord, EntityRegistry
from novel_forge.pipeline.long.services.init.init_split_artifacts import (
    _build_relationship_candidate_evidence,
    _dedupe_character_roster_names,
    _merge_character_arc_item,
    _profile_roster_batches,
    _profiles_by_roster,
    _project_relationship_matrix_into_profiles,
    build_character_bible_split,
)
from novel_forge.pipeline.long.services.init.init_v2 import (
    build_character_system,
    build_entity_graph,
)


def test_character_arc_merge_does_not_create_unknown_profiles() -> None:
    profiles = {
        "陈砚": {"name": "陈砚", "role": "supporting"},
        "顾锦绣（民国）": {"name": "顾锦绣（民国）", "role": "minor"},
    }

    _merge_character_arc_item(profiles, {"name": "陈溯", "arc": "误写出来的角色弧光"})
    _merge_character_arc_item(profiles, {"name": "顾锦绪（民国）", "arc": "误写出来的角色弧光"})

    assert set(profiles) == {"陈砚", "顾锦绣（民国）"}


def test_character_arc_merge_updates_existing_profile_only() -> None:
    profiles = {"陈砚": {"name": "陈砚", "role": "supporting"}}

    _merge_character_arc_item(profiles, {"name": "陈砚", "arc": "从隐匿身份到主动选择。"})

    assert profiles["陈砚"]["arc"] == "从隐匿身份到主动选择。"


def test_profile_batch_name_drift_is_folded_back_to_roster_names() -> None:
    roster = [
        {
            "name": "林绾绾",
            "role": "supporting",
            "age": 27,
            "gender": "female",
            "status": "active",
            "time_layer": "modern",
        },
        {
            "name": "程砚秋",
            "role": "supporting",
            "age": 33,
            "gender": "male",
            "status": "active",
            "time_layer": "modern",
        },
    ]
    profiles, aliases, unmatched = _profiles_by_roster(
        [
            {
                "name": "林苒苒",
                "role": "supporting",
                "age": 27,
                "gender": "female",
                "status": "active",
                "time_layer": "modern",
                "social_status": "时尚杂志主编，沈念卿闺蜜。",
                "backstory": "林苒苒曾请程肃秋协助舆论反击。",
            },
            {
                "name": "程肃秋",
                "role": "supporting",
                "age": 33,
                "gender": "male",
                "status": "active",
                "time_layer": "modern",
                "social_status": "智云科技合伙人。",
            },
        ],
        roster,
    )

    assert set(profiles) == {"林绾绾", "程砚秋"}
    assert profiles["林绾绾"]["name"] == "林绾绾"
    assert profiles["程砚秋"]["name"] == "程砚秋"
    assert aliases == {"林苒苒": "林绾绾", "程肃秋": "程砚秋"}
    assert unmatched == []
    assert profiles["林绾绾"]["backstory"] == "林绾绾曾请程砚秋协助舆论反击。"
    assert "notes" not in profiles["林绾绾"]


def test_duplicate_roster_names_are_qualified_before_profile_generation() -> None:
    roster = [
        {
            "name": "陆云峥",
            "role": "deuteragonist",
            "age": 31,
            "gender": "男",
            "status": "active",
            "time_layer": "modern",
            "function": "现代线男主。",
        },
        {
            "name": "陆云峥",
            "role": "minor",
            "age": 26,
            "gender": "男",
            "status": "retired",
            "time_layer": "past",
            "function": "过去线人物。",
        },
    ]

    normalized, changes = _dedupe_character_roster_names(roster)

    assert [item["name"] for item in normalized] == ["陆云峥（现代第二主角）", "陆云峥（过去退场小角色）"]
    assert [item["canonical_name"] for item in changes] == [
        "陆云峥（现代第二主角）",
        "陆云峥（过去退场小角色）",
    ]
    assert "原始同名角色" in normalized[0]["function"]


def test_ambiguous_profile_alias_is_not_used_for_relationship_normalization() -> None:
    roster = [
        {
            "name": "陆云峥（现代）",
            "role": "deuteragonist",
            "age": 31,
            "gender": "男",
            "status": "active",
            "time_layer": "modern",
        },
        {
            "name": "陆云峥（过去）",
            "role": "minor",
            "age": 26,
            "gender": "男",
            "status": "retired",
            "time_layer": "past",
        },
    ]

    profiles, aliases, unmatched = _profiles_by_roster(
        [
            {
                "name": "陆云峥",
                "role": "deuteragonist",
                "age": 31,
                "gender": "男",
                "status": "active",
                "time_layer": "modern",
                "backstory": "现代线创业者。",
            },
            {
                "name": "陆云峥",
                "role": "minor",
                "age": 26,
                "gender": "男",
                "status": "retired",
                "time_layer": "past",
                "backstory": "过去线实业家。",
            },
        ],
        roster,
    )

    assert set(profiles) == {"陆云峥（现代）", "陆云峥（过去）"}
    assert aliases == {}
    assert unmatched == []


def test_profile_relationships_are_cleared_when_prompt_is_violated() -> None:
    roster = [
        {
            "name": "沈念卿",
            "role": "protagonist",
            "age": 29,
            "gender": "female",
            "status": "active",
            "time_layer": "modern",
        }
    ]

    profiles, _, _ = _profiles_by_roster(
        [
            {
                "name": "沈念卿",
                "role": "protagonist",
                "age": 29,
                "gender": "female",
                "status": "active",
                "time_layer": "modern",
                "relationships": {"陆云峥": "违规提前写入的关系"},
            }
        ],
        roster,
    )

    assert "relationships" not in profiles["沈念卿"]


def test_unmatched_off_roster_profiles_are_reported() -> None:
    roster = [
        {
            "name": "林绾绾",
            "role": "supporting",
            "age": 27,
            "gender": "female",
            "status": "active",
            "time_layer": "modern",
        }
    ]

    profiles, aliases, unmatched = _profiles_by_roster(
        [
            {
                "name": "陌生角色",
                "role": "antagonist",
                "age": 41,
                "gender": "male",
                "status": "active",
                "time_layer": "modern",
            }
        ],
        roster,
    )

    assert profiles == {}
    assert aliases == {}
    assert unmatched == [
        {
            "name": "陌生角色",
            "role": "antagonist",
            "age": 41,
            "gender": "male",
            "status": "active",
            "time_layer": "modern",
        }
    ]


def test_profile_roster_batches_keep_small_rosters_single_call() -> None:
    roster = [{"name": f"角色{i}"} for i in range(10)]

    assert _profile_roster_batches(roster) == [roster]


def test_profile_roster_batches_split_large_rosters_for_bounded_parallelism() -> None:
    roster = [{"name": f"角色{i}"} for i in range(12)]

    batches = _profile_roster_batches(roster)

    assert [len(batch) for batch in batches] == [5, 5, 2]
    assert [item["name"] for item in batches[0]] == ["角色0", "角色1", "角色2", "角色3", "角色4"]


def test_profile_roster_batches_respect_configured_threshold_and_batch_size() -> None:
    roster = [{"name": f"角色{i}"} for i in range(10)]

    batches = _profile_roster_batches(roster, parallel_min_roster=8, batch_size=4)

    assert [len(batch) for batch in batches] == [4, 4, 2]


def test_relationship_matrix_aliases_are_canonicalized_before_projection() -> None:
    matrix = [
        {
            "character_a": "林苒苒",
            "character_b": "陈砚",
            "relation_type": "romantic_tension",
            "description": "独立爱情线。",
        },
        {
            "character_a": "程肃秋",
            "character_b": "林素素",
            "relation_type": "relationship",
            "description": "前世线索与今生合作。",
        },
    ]

    result = canonicalize_relationship_items(
        matrix,
        aliases={"林苒苒": "林绾绾", "程肃秋": "程砚秋"},
        roster=[{"name": "林绾绾"}, {"name": "程砚秋"}, {"name": "陈砚"}, {"name": "林素素"}],
    )
    canonicalized = result.accepted

    assert canonicalized[0]["character_a"] == "林绾绾"
    assert canonicalized[1]["character_a"] == "程砚秋"


def test_relationship_matrix_name_drift_is_rejected_for_llm_retry() -> None:
    matrix = [
        {
            "character_a": "林纾纾",
            "character_b": "陈砚",
            "relation_type": "romantic_tension",
            "description": "身份对立的恋人。第3章邂逅神秘摄影师陈砚后心跳漏拍，媒体资源也会帮助沈念卿。",
        }
    ]

    result = canonicalize_relationship_items(
        matrix,
        aliases={},
        roster=[
            {"name": "林绾绾", "role": "supporting"},
            {"name": "陈砚", "role": "supporting"},
            {"name": "林素素", "role": "supporting"},
        ],
    )
    canonicalized = result.accepted
    rejected = result.rejected

    assert canonicalized == []
    assert rejected[0]["invalid_character_names"] == ["林纾纾"]


def test_relationship_matrix_does_not_guess_from_roster_function_context() -> None:
    matrix = [
        {
            "character_a": "林沸沸",
            "character_b": "陈邃",
            "relation_type": "romantic_tension",
            "description": (
                "恋人关系，伴随身份危机。陈邃试图揭露祖父罪行，"
                "林沸沸在闺蜜与恋人之间抉择，最终选择支持正义。"
            ),
        },
        {
            "character_a": "林素素",
            "character_b": "陈伯庸",
            "relation_type": "antagonism",
            "description": "林素素掌握陈伯庸阴谋的关键证据。",
        },
    ]

    result = canonicalize_relationship_items(
        matrix,
        aliases={"林纾纾": "林绾绾", "陈烨": "陈砚"},
        roster=[
            {
                "name": "林绾绾",
                "role": "supporting",
                "function": "沈念卿闺蜜，邂逅陈砚后在爱情与正义之间抉择。",
            },
            {
                "name": "陈砚",
                "role": "supporting",
                "function": "陈伯庸之孙，与林绾绾在弄堂雨中拥吻。",
            },
            {
                "name": "林素素",
                "role": "supporting",
                "function": "周芷若助理，与程砚秋在技术合作中建立信任。",
            },
            {
                "name": "陈伯庸",
                "role": "antagonist",
                "function": "幕后黑手。",
            },
        ],
    )
    canonicalized = result.accepted
    rejected = result.rejected

    assert canonicalized == [matrix[1]]
    assert rejected[0]["invalid_character_names"] == ["林沸沸", "陈邃"]


def test_relationship_matrix_rewrites_alias_text_and_drops_unresolved_names() -> None:
    matrix = [
        {
            "character_a": "林纤纤",
            "character_b": "陈屿",
            "relation_type": "romantic_tension",
            "description": "林纤纤与陈屿在身份对立中相爱。",
        },
        {
            "character_a": "沈念卿",
            "character_b": "陌生角色",
            "relation_type": "relationship",
            "description": "陌生角色只是不该进入下游的漂移名。",
        },
    ]

    result = canonicalize_relationship_items(
        matrix,
        aliases={"林纤纤": "林绾绾", "陈屿": "陈砚"},
        roster=[
            {"name": "沈念卿", "role": "protagonist"},
            {"name": "林绾绾", "role": "supporting"},
            {"name": "陈砚", "role": "supporting"},
        ],
    )
    canonicalized = result.accepted

    assert canonicalized == [
        {
            "character_a": "林绾绾",
            "character_b": "陈砚",
            "relation_type": "romantic_tension",
            "description": "林绾绾与陈砚在身份对立中相爱。",
        }
    ]


def test_relationship_matrix_context_match_is_not_a_local_identity_decision() -> None:
    matrix = [
        {
            "character_a": "沈念卿",
            "character_b": "林蓁蓁",
            "relation_type": "family",
            "description": "林蓁蓁是《都市丽人》杂志主编，发现陈砚真实身份后帮助沈念卿反击舆论。",
        }
    ]

    result = canonicalize_relationship_items(
        matrix,
        aliases={},
        roster=[
            {"name": "沈念卿", "role": "protagonist"},
            {"name": "林绾绾", "role": "supporting"},
            {"name": "陈砚", "role": "supporting"},
        ],
    )
    canonicalized = result.accepted
    rejected = result.rejected

    assert canonicalized == []
    assert rejected[0]["invalid_character_names"] == ["林蓁蓁"]


def test_relationship_candidate_evidence_scans_profile_fields() -> None:
    evidence = _build_relationship_candidate_evidence(
        [
            {
                "name": "林晚",
                "backstory": "林晚曾被陈默保护，也因此欠下选择代价。",
                "arc": "从依赖沈知微到主动面对陈默。",
            }
        ],
        [{"name": "林晚"}, {"name": "陈默"}, {"name": "沈知微"}],
    )

    assert {
        (item["source"], item["target"], item["field"])
        for item in evidence
    } == {
        ("林晚", "陈默", "backstory"),
        ("林晚", "沈知微", "arc"),
    }


def test_final_relationship_matrix_is_the_only_profile_projection_source() -> None:
    profiles = {
        "沈知微": {"name": "沈知微", "relationships": {"林晚": "种子关系不应残留"}},
        "林晚": {"name": "林晚"},
        "陈默": {"name": "陈默"},
    }

    _project_relationship_matrix_into_profiles(
        profiles,
        [
            {
                "character_a": "林晚",
                "character_b": "陈默",
                "description": "旧识与保护关系，推动林晚的选择代价。",
            }
        ],
    )

    assert profiles["沈知微"]["relationships"] == {}
    assert profiles["林晚"]["relationships"] == {"陈默": "旧识与保护关系，推动林晚的选择代价。"}
    assert profiles["陈默"]["relationships"] == {"林晚": "旧识与保护关系，推动林晚的选择代价。"}


class _JsonStorage:
    def save_json(self, path, payload) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


async def test_split_character_bible_generates_final_matrix_after_arcs(tmp_path) -> None:
    calls: list[tuple[TaskType, dict]] = []

    async def fake_call_with_retry(task_type, context, **_kwargs):
        calls.append((task_type, dict(context)))
        if task_type == TaskType.INIT_CHARACTER_ROSTER:
            return {
                "character_roster": [
                    {"name": "沈知微", "role": "protagonist", "status": "active"},
                    {"name": "林晚", "role": "supporting", "status": "active"},
                    {"name": "陈默", "role": "supporting", "status": "active"},
                ]
            }
        if task_type == TaskType.INIT_CHARACTER_PROFILE_BATCH:
            return {
                "character_profiles": [
                    {"name": "沈知微", "role": "protagonist", "backstory": "沈知微调查真相。"},
                    {"name": "林晚", "role": "supporting", "backstory": "林晚曾被陈默保护。"},
                    {"name": "陈默", "role": "supporting", "backstory": "陈默与林晚有旧债。"},
                ]
            }
        if task_type == TaskType.INIT_CHARACTER_RELATIONSHIP_MATRIX:
            phase = context.get("relationship_generation_phase")
            if phase == "seed":
                return {
                    "relationship_matrix": [
                        {
                            "character_a": "沈知微",
                            "character_b": "林晚",
                            "relation_type": "alliance",
                            "description": "种子关系，仅供弧线参考。",
                            "confidence": 0.8,
                        }
                    ]
                }
            if context.get("relationship_generation_attempt") == 2:
                return {
                    "relationship_matrix": [
                        {
                            "character_a": "林晚",
                            "character_b": "陈默",
                            "relation_type": "relationship",
                            "description": "旧识与保护关系，推动林晚的选择代价。",
                            "confidence": 0.9,
                        }
                    ]
                }
            if phase == "repair":
                return {
                    "relationship_matrix": [
                        {
                            "character_a": "沈知微",
                            "character_b": "林晚",
                            "relation_type": "alliance",
                            "description": "共同调查真相，沈知微负责决策，林晚提供旧案证据。",
                            "confidence": 0.9,
                        }
                    ]
                }
            return {"relationship_matrix": []}
        if task_type == TaskType.INIT_CHARACTER_ARC_PLAN:
            return {
                "character_arcs": [
                    {"name": "林晚", "arc": "从回避陈默到承认旧债。"},
                    {"name": "陈默", "arc": "从保护林晚到承担后果。"},
                ]
            }
        raise AssertionError(task_type)

    ctx = SimpleNamespace(
        settings=SimpleNamespace(init_fragment_max_parallel=1, temp_init_character_bible=0.1),
        layout=SimpleNamespace(root=tmp_path, states_dir=tmp_path / "states"),
        storage=_JsonStorage(),
        router=None,
        call_with_retry=fake_call_with_retry,
        coerce_character_bible=lambda payload: CharacterBible.model_validate(payload),
    )

    bible = await build_character_bible_split(
        ctx,
        base_ctx={"premise": "三人旧事牵引主线。"},
        story_bible=StoryBible(premise="三人旧事牵引主线。"),
        language="zh",
    )

    relationship_calls = [
        context for task, context in calls if task == TaskType.INIT_CHARACTER_RELATIONSHIP_MATRIX
    ]
    arc_context = next(context for task, context in calls if task == TaskType.INIT_CHARACTER_ARC_PLAN)
    final_context = next(
        context
        for context in relationship_calls
        if context.get("relationship_generation_phase") == "final"
    )
    retry_context = next(
        context
        for context in relationship_calls
        if context.get("relationship_generation_attempt") == 2
    )

    assert [context.get("relationship_generation_phase") for context in relationship_calls[:2]] == [
        "seed",
        "final",
    ]
    assert "relationship_seed_matrix" in arc_context
    assert final_context["character_arcs"]
    assert final_context["relationship_seed_matrix"]
    assert final_context["relationship_candidate_evidence"]
    assert retry_context["relationship_retry_reasons"]
    by_name = {profile.name: profile for profile in bible.characters}
    assert by_name["沈知微"].relationships == {
        "林晚": "共同调查真相，沈知微负责决策，林晚提供旧案证据。"
    }
    assert by_name["林晚"].relationships == {
        "沈知微": "共同调查真相，沈知微负责决策，林晚提供旧案证据。",
        "陈默": "旧识与保护关系，推动林晚的选择代价。",
    }


async def test_rejected_relationship_repairs_add_edges_without_replacing_valid_matrix(
    tmp_path,
) -> None:
    calls: list[tuple[TaskType, dict]] = []

    async def fake_call_with_retry(task_type, context, **_kwargs):
        calls.append((task_type, dict(context)))
        if task_type == TaskType.INIT_CHARACTER_ROSTER:
            return {
                "character_roster": [
                    {"name": "沈知微", "role": "protagonist", "status": "active"},
                    {"name": "林晚", "role": "supporting", "status": "active"},
                    {"name": "陈默", "role": "supporting", "status": "active"},
                ]
            }
        if task_type == TaskType.INIT_CHARACTER_PROFILE_BATCH:
            return {
                "character_profiles": [
                    {"name": "沈知微", "role": "protagonist", "backstory": "沈知微调查真相。"},
                    {"name": "林晚", "role": "supporting", "backstory": "林晚协助沈知微。"},
                    {"name": "陈默", "role": "supporting", "backstory": "陈默保护林晚。"},
                ]
            }
        if task_type == TaskType.INIT_CHARACTER_ARC_PLAN:
            return {"character_arcs": []}
        if task_type == TaskType.INIT_CHARACTER_RELATIONSHIP_MATRIX:
            phase = context.get("relationship_generation_phase")
            attempt = context.get("relationship_generation_attempt")
            if phase == "seed":
                return {"relationship_matrix": []}
            if attempt == 2:
                return {
                    "relationship_matrix": [
                        {
                            "character_a": "沈知微",
                            "character_b": "林晚",
                            "relation_type": "rivalry",
                            "description": "重试不得覆盖的回归文案。",
                            "confidence": 0.9,
                        },
                        {
                            "character_a": "沈知微",
                            "character_b": "陈默",
                            "relation_type": "alliance",
                            "description": "陈默补充旧案证据，与沈知微形成临时同盟。",
                            "confidence": 0.88,
                        },
                    ]
                }
            return {
                "relationship_matrix": [
                    {
                        "character_a": "沈知微",
                        "character_b": "林晚",
                        "relation_type": "alliance",
                        "description": "已验证的共同调查关系。",
                        "confidence": 0.95,
                    },
                    {
                        "character_a": "林晚",
                        "character_b": "陈默",
                        "relation_type": "relationship",
                        "description": "已验证的旧识与保护关系。",
                        "confidence": 0.9,
                    },
                    {
                        "character_a": "陈莫",
                        "character_b": "沈知微",
                        "relation_type": "alliance",
                        "description": "错名端点导致的遗失关系。",
                        "confidence": 0.8,
                    },
                ]
            }
        raise AssertionError(task_type)

    ctx = SimpleNamespace(
        settings=SimpleNamespace(init_fragment_max_parallel=1, temp_init_character_bible=0.1),
        layout=SimpleNamespace(root=tmp_path, states_dir=tmp_path / "states"),
        storage=_JsonStorage(),
        router=None,
        call_with_retry=fake_call_with_retry,
        coerce_character_bible=lambda payload: CharacterBible.model_validate(payload),
    )

    bible = await build_character_bible_split(
        ctx,
        base_ctx={"premise": "三人在旧案中形成彼此牵制的关系。"},
        story_bible=StoryBible(premise="三人在旧案中形成彼此牵制的关系。"),
        language="zh",
    )

    repair_calls = [
        context
        for task, context in calls
        if task == TaskType.INIT_CHARACTER_RELATIONSHIP_MATRIX
        and context.get("relationship_generation_attempt") == 2
    ]
    assert len(repair_calls) == 1
    assert any(
        reason.get("code") == "invalid_character_reference"
        for reason in repair_calls[0]["relationship_retry_reasons"]
    )
    by_name = {profile.name: profile for profile in bible.characters}
    assert by_name["沈知微"].relationships == {
        "林晚": "已验证的共同调查关系。",
        "陈默": "陈默补充旧案证据，与沈知微形成临时同盟。",
    }


async def test_drifted_names_are_quarantined_before_repair_and_downstream_projection(
    tmp_path,
) -> None:
    calls: list[tuple[TaskType, dict]] = []
    drifted_names = {"顾珺", "顾琰", "顾珩", "顾衍"}

    async def fake_call_with_retry(task_type, context, **_kwargs):
        calls.append((task_type, dict(context)))
        if task_type == TaskType.INIT_CHARACTER_ROSTER:
            return {
                "character_roster": [
                    {
                        "name": "顾琛",
                        "role": "protagonist",
                        "age": 30,
                        "gender": "male",
                        "status": "active",
                        "time_layer": "modern",
                    },
                    {
                        "name": "林晚",
                        "role": "supporting",
                        "age": 28,
                        "gender": "female",
                        "status": "active",
                        "time_layer": "modern",
                    },
                ]
            }
        if task_type == TaskType.INIT_CHARACTER_PROFILE_BATCH:
            return {
                "character_profiles": [
                    {
                        "name": "顾珺",
                        "role": "protagonist",
                        "age": 30,
                        "gender": "male",
                        "status": "active",
                        "time_layer": "modern",
                        "backstory": "顾珺曾与林晚共同调查旧案。",
                    },
                    {
                        "name": "林晚",
                        "role": "supporting",
                        "age": 28,
                        "gender": "female",
                        "status": "active",
                        "time_layer": "modern",
                        "backstory": "林晚信任顾琛，但隐瞒关键证据。",
                    },
                ]
            }
        if task_type == TaskType.INIT_CHARACTER_ARC_PLAN:
            return {"character_arcs": [{"name": "顾琰", "arc": "从怀疑走向信任。"}]}
        if task_type == TaskType.INIT_CHARACTER_RELATIONSHIP_MATRIX:
            serialized_context = json.dumps(context, ensure_ascii=False)
            assert all(name not in serialized_context for name in drifted_names)
            phase = context.get("relationship_generation_phase")
            attempt = context.get("relationship_generation_attempt")
            if phase == "seed":
                return {
                    "relationship_matrix": [
                        {
                            "character_a": "顾珩",
                            "character_b": "林晚",
                            "relation_type": "alliance",
                            "description": "顾珩与林晚共同调查旧案。",
                            "confidence": 0.8,
                        }
                    ]
                }
            if attempt == 2:
                return {
                    "relationship_matrix": [
                        {
                            "character_a": "顾琛",
                            "character_b": "林晚",
                            "relation_type": "alliance",
                            "description": "两人共同调查旧案，信任与隐瞒持续拉扯。",
                            "confidence": 0.92,
                        }
                    ]
                }
            return {
                "relationship_matrix": [
                    {
                        "character_a": "顾衍",
                        "character_b": "林晚",
                        "relation_type": "alliance",
                        "description": "顾衍与林晚共同调查旧案。",
                        "confidence": 0.8,
                    }
                ]
            }
        raise AssertionError(task_type)

    ctx = SimpleNamespace(
        settings=SimpleNamespace(init_fragment_max_parallel=1, temp_init_character_bible=0.1),
        layout=SimpleNamespace(root=tmp_path, states_dir=tmp_path / "states"),
        storage=_JsonStorage(),
        router=None,
        call_with_retry=fake_call_with_retry,
        coerce_character_bible=lambda payload: CharacterBible.model_validate(payload),
    )

    bible = await build_character_bible_split(
        ctx,
        base_ctx={"premise": "顾琛与林晚追查旧案。"},
        story_bible=StoryBible(premise="顾琛与林晚追查旧案。"),
        language="zh",
    )

    matrix_payload = json.loads(
        (tmp_path / "states" / "init_v2" / "character_relationship_matrix.json").read_text(
            encoding="utf-8"
        )
    )
    matrix = matrix_payload["relationship_matrix"]
    system = build_character_system(bible, relationship_matrix=matrix)
    stale_registry = EntityRegistry(
        entities=[
            EntityRecord(
                entity_id="char_stale",
                name="顾珩",
                entity_type="character",
                source="stale_checkpoint",
            )
        ]
    )
    registry, graph = build_entity_graph(
        registry=stale_registry,
        character_system=system,
        original_character_bible=bible,
    )

    serialized_outputs = json.dumps(
        {
            "bible": bible.model_dump(mode="json"),
            "matrix": matrix,
            "system": system.model_dump(mode="json"),
            "registry": registry.model_dump(mode="json"),
            "graph": graph.model_dump(mode="json"),
        },
        ensure_ascii=False,
    )
    assert all(name not in serialized_outputs for name in drifted_names)
    assert {item.name for item in system.roster} == {"顾琛", "林晚"}
    assert {(edge.source_name, edge.target_name) for edge in system.relationship_edges} == {
        ("顾琛", "林晚")
    }


async def test_repair_format_failure_emits_semantic_terminal_event(tmp_path) -> None:
    events: list[tuple[str, dict]] = []

    async def fake_call_with_retry(task_type, context, **_kwargs):
        if task_type == TaskType.INIT_CHARACTER_ROSTER:
            return {
                "character_roster": [
                    {"name": "顾琛", "role": "protagonist", "status": "active"},
                    {"name": "林晚", "role": "supporting", "status": "active"},
                ]
            }
        if task_type == TaskType.INIT_CHARACTER_PROFILE_BATCH:
            return {
                "character_profiles": [
                    {"name": "顾琛", "role": "protagonist", "backstory": "顾琛信任林晚。"},
                    {"name": "林晚", "role": "supporting", "backstory": "林晚隐瞒证据。"},
                ]
            }
        if task_type == TaskType.INIT_CHARACTER_ARC_PLAN:
            return {"character_arcs": []}
        if task_type == TaskType.INIT_CHARACTER_RELATIONSHIP_MATRIX:
            if context.get("relationship_generation_attempt") == 2:
                return {
                    "relationship_matrix": [
                        {
                            "character_a": "顾珩",
                            "character_b": "林晚",
                            "relation_type": "alliance",
                            "description": "错名关系。",
                            "confidence": 0.8,
                        }
                    ]
                }
            return {"relationship_matrix": []}
        raise AssertionError(task_type)

    ctx = SimpleNamespace(
        settings=SimpleNamespace(init_fragment_max_parallel=1, temp_init_character_bible=0.1),
        layout=SimpleNamespace(root=tmp_path, states_dir=tmp_path / "states"),
        storage=_JsonStorage(),
        router=None,
        call_with_retry=fake_call_with_retry,
        coerce_character_bible=lambda payload: CharacterBible.model_validate(payload),
        on_step=lambda step, payload: events.append((step, payload)),
    )

    with pytest.raises(
        RuntimeError,
        match="Character relationship repair failed before downstream projection",
    ):
        await build_character_bible_split(
            ctx,
            base_ctx={"premise": "顾琛与林晚追查旧案。"},
            story_bible=StoryBible(premise="顾琛与林晚追查旧案。"),
            language="zh",
        )

    assert events[-1][0] == "character_relationship_repair_failed"
    failure = json.loads(
        (
            tmp_path
            / "states"
            / "init_v2"
            / "character_relationship_integrity_failure.json"
        ).read_text(encoding="utf-8")
    )
    assert failure["attempt"] == 2
    assert failure["fallback"] == "abort_before_downstream_projection"
