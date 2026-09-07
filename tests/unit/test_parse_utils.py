"""Tests for robust JSON parsing utilities."""

from __future__ import annotations

import ast
import json
from importlib import import_module
from pathlib import Path

import pytest

from novel_forge.core.parsing.parse_utils import safe_parse_json
from novel_forge.core.parsing.text_utils import extract_text_content


def _collect_imported_names(module_name: str) -> set[str]:
    repo_root = Path(__file__).resolve().parents[2]
    imported_names: set[str] = set()

    for folder_name in ("novel_forge", "tests"):
        folder = repo_root / folder_name
        for path in folder.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module == module_name:
                    imported_names.update(
                        alias.name
                        for alias in node.names
                        if alias.name != "*"
                    )

    return imported_names


@pytest.mark.parametrize(
    "module_name",
    [
        "novel_forge.core.parsing.parse_utils",
        "novel_forge.core.parsing.text_utils",
    ],
)
def test_public_utility_exports_cover_repo_imports(module_name: str) -> None:
    module = import_module(module_name)
    missing = sorted(
        name for name in _collect_imported_names(module_name)
        if not hasattr(module, name)
    )
    assert not missing


def test_extract_text_content_lives_in_text_utils() -> None:
    """extract_text_content is in text_utils, not parse_utils.
    This test pins the canonical import path so an accidental move breaks loudly.
    """
    from novel_forge.core.parsing import text_utils as _tu
    assert hasattr(_tu, "extract_text_content")
    from novel_forge.core.parsing import parse_utils as _pu
    assert not hasattr(_pu, "extract_text_content"), (
        "extract_text_content should not be exported from parse_utils; "
        "it is a text utility, not a JSON parsing utility"
    )
    assert _tu.extract_text_content('{"content": "hello"}') == "hello"
    assert _tu.extract_text_content("hello") == "hello"


# ── Comprehensive extract_text_content tests ──────────────────────────────


class TestExtractTextContent:
    """Test the enhanced JSON text extraction logic."""

    LONG_PROSE = "这是一段足够长的正文内容，" * 20  # ~200+ chars

    def test_plain_text_passthrough(self) -> None:
        assert extract_text_content("普通正文") == "普通正文"

    def test_content_field(self) -> None:
        raw = json.dumps({"content": self.LONG_PROSE}, ensure_ascii=False)
        assert extract_text_content(raw) == self.LONG_PROSE

    def test_full_chapter_field(self) -> None:
        raw = json.dumps({"full_chapter": self.LONG_PROSE}, ensure_ascii=False)
        assert extract_text_content(raw) == self.LONG_PROSE

    def test_text_field(self) -> None:
        raw = json.dumps({"text": self.LONG_PROSE}, ensure_ascii=False)
        assert extract_text_content(raw) == self.LONG_PROSE

    def test_revised_text_field(self) -> None:
        raw = json.dumps({"revised_text": self.LONG_PROSE}, ensure_ascii=False)
        assert extract_text_content(raw) == self.LONG_PROSE

    def test_nested_structure(self) -> None:
        """E.g. {"chapter_6": {"full_chapter": "..."}}"""
        raw = json.dumps(
            {"chapter_6": {"full_chapter": self.LONG_PROSE}},
            ensure_ascii=False,
        )
        assert extract_text_content(raw) == self.LONG_PROSE

    def test_nested_content_field(self) -> None:
        raw = json.dumps(
            {"result": {"content": self.LONG_PROSE}},
            ensure_ascii=False,
        )
        assert extract_text_content(raw) == self.LONG_PROSE

    def test_multiple_fields_picks_content(self) -> None:
        """When both 'content' and other fields exist, prefer 'content'."""
        raw = json.dumps(
            {"title": "第六章", "content": self.LONG_PROSE},
            ensure_ascii=False,
        )
        assert extract_text_content(raw) == self.LONG_PROSE

    def test_longest_string_fallback(self) -> None:
        """Unknown field names — fall back to longest string."""
        raw = json.dumps(
            {"summary": "短摘要", "unknown_field": self.LONG_PROSE},
            ensure_ascii=False,
        )
        assert extract_text_content(raw) == self.LONG_PROSE

    def test_markdown_fenced_json(self) -> None:
        raw = '```json\n{"content": "' + self.LONG_PROSE + '"}\n```'
        assert extract_text_content(raw) == self.LONG_PROSE

    def test_short_json_passthrough(self) -> None:
        """Very short JSON strings are not considered prose."""
        raw = '{"status": "ok"}'
        result = extract_text_content(raw)
        # Should pass through since no field has >= 50 chars
        assert result == raw

    def test_short_chinese_prose_normalizes_ascii_quotes(self) -> None:
        raw = '她抬眼道: "你终于来了。"'
        assert extract_text_content(raw) == '她抬眼道: “你终于来了。”'

    def test_short_json_with_chinese_text_still_passthrough(self) -> None:
        raw = '{"status": "成功"}'
        assert extract_text_content(raw) == raw


def test_safe_parse_json_from_fenced_block_with_prefix() -> None:
    text = (
        "先给你分析：\n"
        "```json\n"
        "{\"title\":\"示例\",\"items\":[1,2,3]}\n"
        "```\n"
        "以上为结果。"
    )
    parsed = safe_parse_json(text)
    assert parsed["title"] == "示例"
    assert parsed["items"] == [1, 2, 3]


def test_safe_parse_json_from_plain_text_wrapper() -> None:
    text = (
        "思考过程略。\n"
        "最终输出如下：\n"
        "{\"ok\":true,\"chapter\":5,\"notes\":\"done\"}\n"
        "请继续。"
    )
    parsed = safe_parse_json(text)
    assert parsed["ok"] is True
    assert parsed["chapter"] == 5


def test_safe_parse_json_from_array_payload() -> None:
    text = "结果: [\"a\", \"b\", {\"c\": 1}]"
    parsed = safe_parse_json(text)
    assert parsed[0] == "a"
    assert parsed[2]["c"] == 1


def test_safe_parse_json_repairs_deep_truncation_for_long_payload() -> None:
    payload = {
        "story_bible": {
            "title": "乱世回滚",
            "premise": "主角不断回档并修改必死剧本",
            "era": "架空乱世",
            "geography": "都城与边镇并行",
            "culture": "官署、坊市与清议交织",
            "magic_or_tech": "回档规则存在明确代价",
            "rules": ["失败会回档", "记忆保留但细节微调"],
            "tone": "紧张中带轻喜剧",
            "themes": ["求生", "制度修复"],
        },
        "character_bible": {"characters": []},
    }
    for index in range(20):
        payload["character_bible"]["characters"].append(
            {
                "name": f"角色{index}",
                "role": "supporting",
                "age": "20",
                "appearance": "外貌" * 40,
                "personality": "性格" * 60,
                "backstory": "背景" * 80,
                "arc": "弧光" * 80,
                "relationships": {"主角": "盟友", "反派": "敌对"},
            }
        )

    text = json.dumps(payload, ensure_ascii=False)
    truncated = text[:-800]

    parsed = safe_parse_json(truncated)

    assert parsed["story_bible"]["title"] == "乱世回滚"
    assert len(parsed["character_bible"]["characters"]) == 18


def test_safe_parse_json_does_not_fallback_to_nested_object_for_truncated_root() -> None:
    payload = {
        "story_bible": {
            "title": "乱世回滚",
            "premise": "主角不断回档并修改必死剧本",
            "era": "架空乱世",
            "geography": "都城与边镇并行",
            "culture": "官署、坊市与清议交织",
            "magic_or_tech": "回档规则存在明确代价",
            "rules": ["失败会回档", "记忆保留但细节微调"],
            "tone": "紧张中带轻喜剧",
            "themes": ["求生", "制度修复"],
        },
        "character_bible": {
            "characters": [
                {
                    "name": "角色0",
                    "role": "supporting",
                    "age": "20",
                    "appearance": "外貌" * 80,
                    "personality": "性格" * 120,
                    "backstory": "背景" * 160,
                    "arc": "弧光" * 6000,
                    "relationships": {"主角": "盟友", "反派": "敌对"},
                }
            ]
        },
    }

    text = json.dumps(payload, ensure_ascii=False)
    arc_fragment = payload["character_bible"]["characters"][0]["arc"]
    cut_at = text.index(arc_fragment) + 9000
    truncated = text[:cut_at]

    with pytest.raises(json.JSONDecodeError):
        safe_parse_json(truncated)


def test_safe_parse_json_repairs_mixed_quote_damage_and_tail_truncation() -> None:
    broken = """
{
  "scene_intents": [
    {
      "scene_id": "scene_01",
      "summary": "主角在终端前复核日志。",
      "conflict": 主角掌握的“真实”与旁人坚持的“记忆”发生冲突。",
      "character_motivations": [
        {
          "character": "主角",
          "motivation": "需要排除"个人造假"的可能性。",
          "stake": "若判断错误将错失唯一线索。"
        }
      ]
    },
    {
      "scene_id": "scene_02",
      "summary": "继续比对外部档案。",
      "conflict": "系统记录与原始备份矛盾。",
      "character_motivations": [
        {
          "character": "同伴",
          "motivation": "推进调查",
          "stake"
"""

    parsed = safe_parse_json(broken)
    assert isinstance(parsed, dict)
    scenes = parsed.get("scene_intents", [])
    assert isinstance(scenes, list)
    assert scenes
    assert scenes[0]["scene_id"] == "scene_01"


def test_safe_parse_json_repairs_minified_newline_kv_and_bare_values() -> None:
    broken = (
        '{"character_bible":{"characters":[{"name":"凌霜","relationships":{"萧策":"初遇观测。\\n青冥":"监督者。'
        '\\n萧玥":"亲情窗口。"}}],"notes":{"萧策":  “危险的扰动源”、“导致失衡的关键变量”。",'
        ' "虚空之海规则": 他与规则关系最紧密。}}}'
    )

    parsed = safe_parse_json(broken)
    assert isinstance(parsed, dict)

    bible = parsed["character_bible"]
    relationships = bible["characters"][0]["relationships"]

    assert relationships["青冥"] == "监督者。"
    assert relationships["萧玥"] == "亲情窗口。"
    assert bible["notes"]["萧策"] == "“危险的扰动源”、“导致失衡的关键变量”。"
    assert bible["notes"]["虚空之海规则"] == "他与规则关系最紧密。"


def test_safe_parse_json_repairs_missing_array_closer_then_tail_truncation() -> None:
    payload = {
        "chapters": [
            {
                "chapter_number": 31,
                "title": "银楔",
                "goal": "建立新同盟秩序。",
                "beats_summary": [
                    "盟誓与正名。",
                    "日常协同磨合。",
                    "朝堂动态反转。",
                ],
                "main_plot_points": [
                    "公开确认凌霜参赞军务身份。",
                    "锁定云岭隘口为下一阶段目标。",
                ],
                "subplot_points": ["朝堂线阶段性收束。"],
                "pov_character": "萧策",
                "setting": "雁回关",
                "expected_word_count": 4200,
                "notes": "为终局大战蓄力。",
            },
            {
                "chapter_number": 32,
                "title": "潮信",
                "goal": "揭示终局代价。",
                "beats_summary": ["异常预兆", "青冥预警"],
                "main_plot_points": ["确认天缺位置", "准备织补方案"],
                "subplot_points": [],
                "pov_character": "凌霜",
                "setting": "雁回关",
                "expected_word_count": 4100,
                "notes": "战前最后准备。",
            },
        ]
    }
    text = json.dumps(payload, ensure_ascii=False)

    # Simulate the exact class of failure seen in production:
    # missing closing ] before next object key.
    broken = text.replace('], "main_plot_points"', ', "main_plot_points"', 1)
    # Simulate additional token-cut tail truncation in the second chapter.
    broken = broken[:-120]

    parsed = safe_parse_json(broken)
    chapter = parsed["chapters"][0]

    assert chapter["chapter_number"] == 31
    assert chapter["beats_summary"][0] == "盟誓与正名。"
    assert chapter["main_plot_points"][0].startswith("公开确认")


def test_safe_parse_json_repairs_missing_object_closer_before_array_element() -> None:
    """Regression: LLM omits closing ``}`` for nested object before next array element.

    Real-world pattern from character_bible where ``relationships`` object
    is not closed before the next character in the array::

        "relationships": {"A": "x", "B": "y"}, {"name": "C"}

    The pattern is: value-ending ``"`` followed by ``, {`` where a ``}``
    should appear to close the containing nested object before the comma
    that separates array elements.
    """
    broken = (
        '{"character_bible": {"characters": ['
        '{"name": "崔令仪", "role": "protagonist", '
        '"relationships": {"沈照夜": "亦敌亦友", "太平公主": "权力博弈", '
        '"崔氏族人": "枷锁", '
        '{"name": "沈照夜", "role": "deuteragonist", '
        '"backstory": "暗卫出身"}'
        ']}}'
    )
    parsed = safe_parse_json(broken)
    chars = parsed["character_bible"]["characters"]
    assert len(chars) == 2
    assert chars[0]["name"] == "崔令仪"
    assert chars[0]["relationships"]["沈照夜"] == "亦敌亦友"
    assert chars[1]["name"] == "沈照夜"


def test_safe_parse_json_missing_object_closer_deep_nesting() -> None:
    """Missing closer with 2+ levels of nesting and truncation."""
    broken = (
        '{"items": [{"data": {"k1": "v1", "k2": "v2"}, '
        '{"data": {"k3": "v3"}}]}'
    )
    parsed = safe_parse_json(broken)
    assert len(parsed["items"]) == 2
    assert parsed["items"][0]["data"]["k1"] == "v1"
    assert parsed["items"][1]["data"]["k3"] == "v3"


def test_safe_parse_json_repairs_extra_object_closer_before_array_element() -> None:
    """Regression: character arrays sometimes get ``}}, {"name": ...}``.

    The second ``}`` is redundant: it appears while the parser is still inside
    the characters array, right before the next object element.
    """
    broken = (
        '{"character_bible":{"characters":['
        '{"name":"崔令仪","notes":"她观察一个人时会计算步伐节奏。"}},'
        '{"name":"沈照夜","notes":"袖中藏半枚李唐旧印。"}'
        ']}}'
    )

    parsed = safe_parse_json(broken)
    chars = parsed["character_bible"]["characters"]
    assert [item["name"] for item in chars] == ["崔令仪", "沈照夜"]


def test_safe_parse_json_repairs_inline_relationship_key_separators() -> None:
    """Regression: LLM folds several relationship entries into one value.

    Seen in init_character_bible output:
    ``"沈照夜": "说明；太平公主": "说明；崔令昭": "说明"``
    should become separate relationship keys.
    """
    broken = (
        '{"character_bible":{"characters":[{"name":"崔令仪",'
        '"relationships":{"沈照夜":"既依赖又防备；太平公主":"引路人也是考验；'
        '崔令昭":"唯一的软肋；张说":"政敌与镜像；上官婉儿":"亦师亦敌；"},'
        '"notes":"完成"}]}}'
    )

    parsed = safe_parse_json(broken)
    relationships = parsed["character_bible"]["characters"][0]["relationships"]
    assert relationships["沈照夜"] == "既依赖又防备；"
    assert relationships["太平公主"] == "引路人也是考验；"
    assert relationships["崔令昭"] == "唯一的软肋；"
    assert relationships["张说"] == "政敌与镜像；"
    assert relationships["上官婉儿"] == "亦师亦敌；"


def test_safe_parse_json_repairs_python_style_inline_relationship_separator() -> None:
    """Regression: init_character_bible may mix Python-style separators into JSON.

    Seen in production:
    ``"陈默": "表兄弟', '沈知微": "工作对接关系"``
    should become two relationship entries without dropping the character.
    """
    broken = (
        '{"character_bible":{"characters":[{"name":"李铮","role":"minor",'
        '"relationships":{"陈默":"表兄弟\', \'沈知微":"工作对接关系",'
        '"苏晴":"社区网格化管理认识"},'
        '"notes":"功能角色"}]}}'
    )

    parsed = safe_parse_json(broken)
    relationships = parsed["character_bible"]["characters"][0]["relationships"]

    assert relationships["陈默"] == "表兄弟"
    assert relationships["沈知微"] == "工作对接关系"
    assert relationships["苏晴"] == "社区网格化管理认识"


def test_safe_parse_json_prefers_inline_repair_over_truncating_last_character() -> None:
    """A recoverable trailing character must not be dropped by prefix truncation."""
    broken = (
        '{"character_bible":{"characters":['
        '{"name":"银杏叶书签","role":"minor","relationships":{}},'
        '{"name":"李铮","role":"minor",'
        '"relationships":{"陈默":"表兄弟\', \'沈知微":"工作对接关系",'
        '"苏晴":"社区网格化管理认识"},'
        '"notes":"功能角色"}'
        ']}}'
    )

    parsed = safe_parse_json(broken)
    chars = parsed["character_bible"]["characters"]
    relationships = chars[1]["relationships"]

    assert [item["name"] for item in chars] == ["银杏叶书签", "李铮"]
    assert relationships["沈知微"] == "工作对接关系"


def test_safe_parse_json_repairs_array_closed_by_object_brace() -> None:
    """Regression: LLM closes an array with } instead of ].

    Seen in EXTRACT_CANON output where ``known_facts`` array ends without
    ``]`` and is instead closed by the parent object brace ``}}``.

        "known_facts":["item1","item2","item3"}}}},"next_key":...

    Should recover all array elements and preserve sibling fields.
    """
    broken = (
        '{"canon_delta":{"character_updates":{"人物A":{"known_facts":["事实1","事实2","事实3"'
        '}}}},"creative_report":{"highlights":["亮点1"]}}}'
    )
    parsed = safe_parse_json(broken)
    assert "canon_delta" in parsed
    facts = parsed["canon_delta"]["character_updates"]["人物A"]["known_facts"]
    assert facts == ["事实1", "事实2", "事实3"]
    assert "creative_report" in parsed


def test_safe_parse_json_repairs_extra_bracket_inside_object() -> None:
    """Regression: LLM emits stray ] inside an object between fields.

    Seen in EXTRACT_CANON character_state_deltas where physical_state closes
    with ``}]`` instead of just ``}``, leaving emotional_state outside the
    delta object.

        "physical_state":{"loc":"X","notable":"Y"}],"emotional_state":{...}

    Should recover emotional_state as a sibling field inside the same object.
    """
    broken = (
        '{"character_state_deltas":[{"character_id":"沈念卿",'
        '"physical_state":{"location":"古董店","notable":"神情恍惚"}'
        '],"emotional_state":{"primary_emotion":"震惊"},"delta_summary":"触发前世记忆"}]}'
    )
    parsed = safe_parse_json(broken)
    delta = parsed["character_state_deltas"][0]
    assert delta["character_id"] == "沈念卿"
    assert delta["physical_state"]["location"] == "古董店"
    assert delta["emotional_state"]["primary_emotion"] == "震惊"


def test_safe_parse_json_repairs_split_root_object_siblings() -> None:
    """Regression: EXTRACT_CANON may close root then continue sibling sections."""
    broken = (
        '{"canon_delta":{"chapter_number":81},'
        '"creative_report":{"summary":"本章完成伏笔回收"},'
        '"chapter_exit_state":{"location":"宫城","tension_level":7}},'
        '{"character_state_deltas":[{"character_id":"张说","delta_summary":"完成表态"}],'
        '"relationship_deltas":[],"plot_thread_deltas":[],'
        '"structured_summary":{"chapter_number":81,"summary":"众人确认下一步行动"}}'
    )

    parsed = safe_parse_json(broken)

    assert parsed["canon_delta"]["chapter_number"] == 81
    assert parsed["chapter_exit_state"]["location"] == "宫城"
    assert parsed["character_state_deltas"][0]["character_id"] == "张说"
    assert parsed["structured_summary"]["chapter_number"] == 81


def test_safe_parse_json_inserts_missing_object_closer_before_array_closer() -> None:
    """Regression: 魂玉 plan_outline_batch attempt 1 LLM closed an inner
    object with ``]`` instead of ``}`` followed by ``]``, so
    ``[..., {"description": "..."]`` lost the ``}``. The closer-v2 fix
    must INSERT the missing ``}`` (not DELETE ``]``) and recover the
    intended structure."""
    broken = (
        '{"chapters": ['
        '{"chapter_number": 19, "scene_payoffs": ['
        '{"payoff_type": "relationship", "description": "情感障碍出现微小松动"'
        '],'
        '"title": "袖底蛊痕"}'
        ']}'
    )
    parsed = safe_parse_json(broken)
    assert parsed["chapters"][0]["chapter_number"] == 19
    assert parsed["chapters"][0]["title"] == "袖底蛊痕"
    payoffs = parsed["chapters"][0]["scene_payoffs"]
    assert payoffs[0]["payoff_type"] == "relationship"
    assert payoffs[0]["description"] == "情感障碍出现微小松动"


def test_safe_parse_json_recovers_nested_unclosed_objects() -> None:
    """Regression: missing ``}`` deep inside nested ``array > object > array
    > object`` shapes must still recover. Previously the closer-v2 fix
    only handled the shallow case and left nested array stack frames
    un-popped after INSERT."""
    raw = (
        '{"book": {"chapters": ['
        '{"scenes": [{"payoffs": [{"type": "x", "desc": "y"], "name": "A"}],'
        '"title": "T"}]}}'
    )
    parsed = safe_parse_json(raw)
    payoff = parsed["book"]["chapters"][0]["scenes"][0]["payoffs"][0]
    assert payoff == {"type": "x", "desc": "y"}
    assert parsed["book"]["chapters"][0]["scenes"][0]["name"] == "A"
    assert parsed["book"]["chapters"][0]["title"] == "T"


def test_safe_parse_json_handles_two_unclosed_siblings_in_array() -> None:
    """Regression: when an array of objects has multiple siblings each
    missing ``}``, every one of them must be repaired, not just the first.
    Mirrors the production 魂玉 [2] shape where the LLM emitted three
    chapter entries each with an unclosed inner ``scene_payoffs``."""
    raw = (
        '{"chapters": ['
        '{"scene_payoffs": [{"payoff_type": "a", "description": "p1"], "title": "T1"},'
        '{"scene_payoffs": [{"payoff_type": "b", "description": "p2"], "title": "T2"}'
        ']}'
    )
    parsed = safe_parse_json(raw)
    assert len(parsed["chapters"]) == 2
    assert parsed["chapters"][0]["title"] == "T1"
    assert parsed["chapters"][1]["title"] == "T2"
    assert parsed["chapters"][0]["scene_payoffs"][0]["description"] == "p1"
    assert parsed["chapters"][1]["scene_payoffs"][0]["description"] == "p2"


def test_safe_parse_json_repairs_premature_root_close_with_fullwidth_comma() -> None:
    """Regression: Chinese model output may close root before later fields."""
    broken = (
        '{"canon_delta":{"chapter_number":4},'
        '"structured_summary":"本章完成前世锚点确认"}，'
        '"must_carry_forward":["怀表发烫","外滩钟楼老照片"],'
        '"bridge_hints":["下章前往外滩钟楼"]}，'
        '"chapter_exit_state":{"location":"外滩钟楼"}'
        '}'
    )

    parsed = safe_parse_json(broken)

    assert parsed["canon_delta"]["chapter_number"] == 4
    assert parsed["must_carry_forward"] == ["怀表发烫", "外滩钟楼老照片"]
    assert parsed["bridge_hints"] == ["下章前往外滩钟楼"]
    assert parsed["chapter_exit_state"]["location"] == "外滩钟楼"


def test_safe_parse_json_repairs_missing_commas_between_array_strings() -> None:
    """Regression: long init contracts may emit adjacent array strings."""
    broken = (
        '{"chapter_contracts":[{"chapter_number":22,'
        '"entry_state_requirements":['
        '"陆云峥在守护与尊重之间痛苦撕裂"\n'
        '"沈念卿暂时与陆云峥保持距离"\n'
        '"两人隔着玻璃门对视"],'
        '"required_events":["周芷若启动恶意收购要约"],'
        '"allowed_changes":[]}]}'
    )

    parsed = safe_parse_json(broken)
    requirements = parsed["chapter_contracts"][0]["entry_state_requirements"]

    assert requirements == [
        "陆云峥在守护与尊重之间痛苦撕裂",
        "沈念卿暂时与陆云峥保持距离",
        "两人隔着玻璃门对视",
    ]


def test_safe_parse_json_repairs_missing_commas_between_array_objects() -> None:
    """Regression: long init contracts may emit adjacent array objects."""
    broken = (
        '{"cross_subplot_links":['
        '{"source_type":"subplot","source_ref":"主线","trigger_chapter":1}'
        '{"source_type":"subplot","source_ref":"林绾绾线","trigger_chapter":3}'
        "],"
        '"ending_strategy":"收束所有支线"}'
    )

    parsed = safe_parse_json(broken)
    links = parsed["cross_subplot_links"]

    assert [link["source_ref"] for link in links] == ["主线", "林绾绾线"]
    assert parsed["ending_strategy"] == "收束所有支线"


def test_safe_parse_json_repairs_quoted_object_openers_in_array() -> None:
    """Regression: model may emit },\"{\"key\"... for the next array object."""
    broken = (
        '{"cross_subplot_links":['
        '{"source_type":"subplot","source_ref":"主线","trigger_chapter":1},'
        '"{"source_type":"subplot","source_ref":"林绾绾线","trigger_chapter":3}'
        "],"
        '"ending_strategy":"收束所有支线"}'
    )

    parsed = safe_parse_json(broken)
    links = parsed["cross_subplot_links"]

    assert [link["source_ref"] for link in links] == ["主线", "林绾绾线"]
    assert parsed["ending_strategy"] == "收束所有支线"


def test_safe_parse_json_repairs_object_key_only_members() -> None:
    """Regression: extract_canon may emit a flag-like object member without a colon."""
    broken = (
        '{"canon_delta":{"item_updates":{"暗卫密报":{'
        '"内容":"三行字，极短",'
        '"处置":"放入暗档木匣最底层",'
        '"未向沈清漪透露"}}},'
        '"creative_report":{},'
        '"chapter_exit_state":{},'
        '"character_state_deltas":[],'
        '"relationship_deltas":[],'
        '"plot_thread_deltas":[],'
        '"structured_summary":"ok"}'
    )

    parsed = safe_parse_json(broken)
    secret_report = parsed["canon_delta"]["item_updates"]["暗卫密报"]

    assert secret_report["内容"] == "三行字，极短"
    assert secret_report["未向沈清漪透露"] is True


def test_safe_parse_json_repairs_flattened_weave_link_after_empty_string() -> None:
    """Regression: subplot links may use an empty string before a flattened object."""
    broken = (
        '{"subplot_plan":[{"name":"方明哲学术施压线","weave_links":['
        '{"source_type":"main_plot","source_ref":"合作方案","target_subplot":"方明哲线",'
        '"trigger_chapter":9,"link_type":"trigger_start","description":"触发支线"},'
        '"","source_type":"subplot","source_ref":"方明哲线","target_subplot":"主线",'
        '"trigger_chapter":40,"link_type":"feed_main","description":"反哺主线"}]}]}'
    )

    parsed = safe_parse_json(broken)
    links = parsed["subplot_plan"][0]["weave_links"]

    assert len(links) == 2
    assert all(isinstance(link, dict) for link in links)
    assert [link["source_type"] for link in links] == ["main_plot", "subplot"]
    assert links[1]["description"] == "反哺主线"


def test_safe_parse_json_repairs_extra_quote_after_composite_value() -> None:
    """Regression: plan_outline may insert a stray quote after a link array."""
    broken = (
        '{"subplot_plan":[{"name":"林绾绾与陈砚","weave_links":['
        '{"source_type":"subplot","source_ref":"身份危机","trigger_chapter":30}'
        ']"},{"name":"程砚秋与林素素","weave_links":[]}]}'
    )

    parsed = safe_parse_json(broken)
    subplots = parsed["subplot_plan"]

    assert subplots[0]["weave_links"][0]["source_ref"] == "身份危机"
    assert subplots[1]["name"] == "程砚秋与林素素"


def test_safe_parse_json_repairs_truncated_unicode_escape() -> None:
    """Regression: truncated claims may end inside a ``\\uXXXX`` escape."""
    broken = r'{"claims":[{"claim_text":"沈念卿离开咖啡馆\u53'

    parsed = safe_parse_json(broken)

    assert parsed["claims"][0]["claim_text"].endswith("u53")


def test_safe_parse_json_repairs_flattened_arc_milestones() -> None:
    """Regression: character arc milestones may lose object openers."""
    broken = (
        '{"character_arcs":[{"character":"沈念卿","arc_summary":"从设防到信任",'
        '"milestones":[{"chapter_end":15,"chapter_start":1,"description":"初遇与逃离"},'
        '"chapter_end":40,"chapter_start":16,"description":"信任开始累积"},'
        '"chapter_end":70,"chapter_start":41,"description":"主动选择并肩"}]}]}'
    )

    parsed = safe_parse_json(broken)
    milestones = parsed["character_arcs"][0]["milestones"]

    assert [(item["chapter_start"], item["chapter_end"]) for item in milestones] == [
        (1, 15),
        (16, 40),
        (41, 70),
    ]


def test_safe_parse_json_repairs_premature_chapter_close_before_expected_payoffs() -> None:
    """Regression: outline batches may close chapters before the final chapter field."""
    broken = (
        '{"chapters":[{"chapter_number":20,"title":"来不及说出口的三个字",'
        '"goal":"推进关系临界点","beats_summary":["她强撑没有后退"],'
        '"main_plot_points":["陆云峥首次展示怀表"],'
        '"expected_hook":{"hook_type":"emotion","hook_strength":"strong",'
        '"hook_description":"沈鹤卿说这次可别再错过了"}]},'
        '"expected_payoffs":[{"payoff_type":"relationship","description":"她选择靠近"}]}]}'
    )

    parsed = safe_parse_json(broken)
    chapter = parsed["chapters"][0]

    assert len(parsed["chapters"]) == 1
    assert chapter["chapter_number"] == 20
    assert chapter["expected_payoffs"][0]["description"] == "她选择靠近"


def test_safe_parse_json_repairs_missing_key_open_quote_in_minified_object() -> None:
    """Regression: long outline subplot events may drop a key's opening quote."""
    broken = (
        '{"subplot_plan":[{"name":"青衿窃语","chapter_events":['
        '{"chapter_number":15,'
        '"event":"林晚向沈知微汇报脑电数据样本时，无意提及\'老师最近常查中医古籍\'",'
        'weave_notes":"支线核心事件反哺主线沈知微生疑",'
        '"depends_on":["青衿窃语:10"]}]}]}'
    )

    parsed = safe_parse_json(broken)
    event = parsed["subplot_plan"][0]["chapter_events"][0]

    assert event["weave_notes"] == "支线核心事件反哺主线沈知微生疑"
    assert event["depends_on"] == ["青衿窃语:10"]


def test_safe_parse_json_repairs_extra_key_open_quote_without_losing_tail() -> None:
    """Regression: editorial contracts may emit {""element_id": ...} after one item."""
    broken = (
        '{"editorial_element_directives":['
        '{"element_id":"romance_emotional_barriers","element_name":"情感障碍阶梯"},'
        '{""element_id":"romance_relationship_contract","element_name":"关系契约"}'
        '],"time_bridge_policies":["跨月转场必须给出时间桥"],'
        '"title_policy":{"max_reuse":2}}'
    )

    parsed = safe_parse_json(broken)

    assert len(parsed["editorial_element_directives"]) == 2
    assert parsed["editorial_element_directives"][1]["element_id"] == (
        "romance_relationship_contract"
    )
    assert parsed["time_bridge_policies"] == ["跨月转场必须给出时间桥"]


def test_safe_parse_json_repairs_extra_closing_paren_after_array_value() -> None:
    """Regression: plot guard judge may write ]), before the next root key."""
    broken = (
        '{"decision":"continue_with_constraints","risk_level":"low",'
        '"outline_action":"none","entity_actions":[],'
        '"next_chapter_constraints":["继续铺垫账号数据","不得提前兑现风速线索"]),'
        '"reasoning_brief":"章节质量稳定，可继续生成","targeted_repairs":[]}'
    )

    parsed = safe_parse_json(broken)

    assert parsed["decision"] == "continue_with_constraints"
    assert parsed["next_chapter_constraints"] == ["继续铺垫账号数据", "不得提前兑现风速线索"]
    assert parsed["reasoning_brief"] == "章节质量稳定，可继续生成"


def test_safe_parse_json_repairs_json5_style_world_rules_keys() -> None:
    """Regression: init_story_world_rules may return JSON5-like bare keys."""
    broken = (
        '{world_rules:{era:"当代中国东部沿海二线城市",'
        'geography:"大学城理工楼负一层与老街区知微堂由银杏道相连",'
        'culture:"中西医并行构成信任结构",'
        'constraints:["医保目录包含针灸推拿","实验室数据必须可复核"]}}'
    )

    parsed = safe_parse_json(broken)

    assert parsed["world_rules"]["era"] == "当代中国东部沿海二线城市"
    assert parsed["world_rules"]["geography"].startswith("大学城")
    assert parsed["world_rules"]["constraints"] == [
        "医保目录包含针灸推拿",
        "实验室数据必须可复核",
    ]
