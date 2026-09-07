"""Tests for StoryBible.time_convention field and its rendering in templates."""

from __future__ import annotations

from jinja2 import Environment, FileSystemLoader

from novel_forge.core.schemas.bible import StoryBible


def _render_contract(contract, story_bible, time_convention, mode):
    env = Environment(loader=FileSystemLoader("novel_forge/prompts/prompts"))
    sb_dict = story_bible.model_dump(mode="json") if story_bible else {}
    env.globals["story_bible"] = sb_dict
    if time_convention:
        env.globals["time_convention"] = time_convention
    else:
        env.globals.pop("time_convention", None)
    tpl = env.from_string(
        '{% from "_base/_narrative_contract.j2" import render_narrative_contract %}'
        '{{ render_narrative_contract(narrative_contract, mode) }}'
    )
    return tpl.render(
        narrative_contract=contract,
        mode=mode,
    )


class TestTimeConvention:
    def test_default_empty(self) -> None:
        bible = StoryBible(premise="test")
        assert bible.time_convention == ""

    def test_ancient_chinese(self) -> None:
        bible = StoryBible(premise="test", time_convention="古代中国：时辰/刻")
        assert bible.time_convention == "古代中国：时辰/刻"

    def test_modern(self) -> None:
        bible = StoryBible(premise="test", time_convention="现代：小时/分钟")
        assert bible.time_convention == "现代：小时/分钟"

    def test_fantasy(self) -> None:
        bible = StoryBible(premise="test", time_convention="奇幻：沙漏/月相")
        assert bible.time_convention == "奇幻：沙漏/月相"

    def test_serialization_roundtrip(self) -> None:
        bible = StoryBible(premise="test", time_convention="古代中国：时辰/刻")
        data = bible.model_dump(mode="json")
        restored = StoryBible.model_validate(data)
        assert restored.time_convention == "古代中国：时辰/刻"

    def test_backward_compat_old_json_without_field(self) -> None:
        old_data = {"premise": "old story", "schema_version": "2.0"}
        bible = StoryBible.model_validate(old_data)
        assert bible.time_convention == ""

    def test_rendered_in_narrative_contract(self) -> None:
        bible = StoryBible(premise="test", time_convention="古代中国：时辰/刻")
        contract = {"continuity_protocol": {"pov_visibility_rule": "限知视角"}}
        result = _render_contract(contract, bible, bible.time_convention, "plan")
        assert "古代中国：时辰/刻" in result

    def test_empty_time_convention_uses_default(self) -> None:
        bible = StoryBible(premise="test", time_convention="")
        contract = {"continuity_protocol": {"pov_visibility_rule": "限知视角"}}
        result = _render_contract(contract, bible, bible.time_convention, "plan")
        assert "按项目时代背景自然选择时间单位" in result
