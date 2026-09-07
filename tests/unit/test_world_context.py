from __future__ import annotations

from types import SimpleNamespace

from novel_forge.core.domain.world_context import (
    coerce_rule_list,
    dump_story_bible_for_prompt,
    extract_world_context,
    format_address_rules_for_prompt,
    prune_empty_world_context_fields,
    render_world_context_rules,
)


def test_coerce_rule_list_trims_drops_empty_and_caps_items_and_chars() -> None:
    value = ["  第一条规则  ", "", None, "第二条规则很长"]

    assert coerce_rule_list(value, max_items=2, max_chars=4) == ["第一条规", "第二条规"]
    assert coerce_rule_list("  单条规则  ") == ["单条规则"]
    assert coerce_rule_list({"not": "a list"}) == []


def test_extract_world_context_accepts_dict_and_story_bible_like_objects() -> None:
    source = SimpleNamespace(
        social_hierarchy=" 皇权、内廷、外朝 ",
        address_rules=[" 臣下面圣称陛下 ", ""],
        self_reference_rules=[],
        etiquette_rules=["不得越阶落座"],
        institution_terms=["司礼监"],
        material_culture=[],
        anachronism_blacklist=["手机", "打卡"],
        dialogue_register_rules=["臣子对白避免现代口语"],
    )

    context = extract_world_context(source)

    assert context == {
        "social_hierarchy": "皇权、内廷、外朝",
        "address_rules": ["臣下面圣称陛下"],
        "etiquette_rules": ["不得越阶落座"],
        "institution_terms": ["司礼监"],
        "anachronism_blacklist": ["手机", "打卡"],
        "dialogue_register_rules": ["臣子对白避免现代口语"],
    }
    assert extract_world_context({"address_rules": ["称主公"], "material_culture": ["竹简"]}) == {
        "address_rules": ["称主公"],
        "material_culture": ["竹简"],
    }


def test_render_world_context_rules_uses_stable_labels_and_order() -> None:
    rendered = render_world_context_rules(
        {
            "social_hierarchy": "士族门第层级分明",
            "dialogue_register_rules": ["长辈对白偏文雅"],
            "address_rules": ["晚辈称先生"],
            "anachronism_blacklist": ["手机"],
        }
    )

    assert rendered.splitlines() == [
        "- 社会等级：士族门第层级分明",
        "- 称谓规则：晚辈称先生",
        "- 时代错位禁用：手机",
        "- 对白语体：长辈对白偏文雅",
    ]


def test_format_address_rules_for_prompt_returns_only_dialogue_boundary_rules() -> None:
    formatted = format_address_rules_for_prompt(
        {
            "social_hierarchy": "不应出现在此格式化结果",
            "address_rules": ["臣下面圣称陛下"],
            "self_reference_rules": ["皇帝自称朕"],
            "etiquette_rules": ["不得直视帝王"],
            "institution_terms": ["司礼监"],
            "dialogue_register_rules": ["禁现代网络语"],
        }
    )

    assert formatted == (
        "称谓规则：臣下面圣称陛下；自称规则：皇帝自称朕；"
        "对白语体：禁现代网络语；礼制/行为边界：不得直视帝王"
    )
    assert "司礼监" not in formatted


def test_prompt_dump_prunes_empty_optional_world_context_fields_without_mutating_input() -> None:
    payload = {
        "title": "长安夜雨",
        "social_hierarchy": "",
        "address_rules": [],
        "anachronism_blacklist": ["手机"],
        "rules": ["夜禁后一律闭坊"],
    }

    dumped = dump_story_bible_for_prompt(payload, mode="json")

    assert dumped == {
        "title": "长安夜雨",
        "anachronism_blacklist": ["手机"],
        "rules": ["夜禁后一律闭坊"],
    }
    assert "address_rules" in payload


def test_prompt_dump_supports_model_dump_objects() -> None:
    class FakeBible:
        def model_dump(self, *, mode: str) -> dict[str, object]:
            assert mode == "json"
            return {
                "title": "雾都",
                "dialogue_register_rules": ["下城区对白短促"],
                "material_culture": [],
            }

    assert dump_story_bible_for_prompt(FakeBible(), mode="json") == {
        "title": "雾都",
        "dialogue_register_rules": ["下城区对白短促"],
    }


def test_prune_empty_world_context_fields_keeps_non_world_empty_fields() -> None:
    data = {
        "title": "",
        "address_rules": [],
        "time_convention": "",
        "rules": [],
    }

    assert prune_empty_world_context_fields(data) == {"title": "", "rules": []}
