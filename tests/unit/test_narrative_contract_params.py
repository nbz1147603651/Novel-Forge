"""Tests for _narrative_contract.j2 rendering with StoryBible parameters."""

from __future__ import annotations

from jinja2 import Environment, FileSystemLoader

from novel_forge.core.schemas.bible import StoryBible


def _make_env() -> Environment:
    return Environment(
        loader=FileSystemLoader("novel_forge/prompts/prompts"),
    )


def _render(contract, story_bible, time_convention, mode):
    env = _make_env()
    sb_dict = story_bible.model_dump(mode="json") if story_bible else {}
    env.globals["story_bible"] = sb_dict
    if time_convention:
        env.globals["time_convention"] = time_convention
    else:
        env.globals.pop("time_convention", None)
    tpl = env.from_string(
        '{% from "_base/_narrative_contract.j2" import render_narrative_contract %}'
        '{{ render_narrative_contract(_nc, _mode) }}'
    )
    return tpl.render(
        _nc=contract,
        _mode=mode,
    )


class TestNarrativeContractParams:
    def test_location_transition_window_sentences(self) -> None:
        bible = StoryBible(premise="test", location_transition_window_sentences=5)
        contract = {"continuity_protocol": {"pov_visibility_rule": "限知视角"}}
        result = _render(contract, bible, "现代", "plan")
        assert "前 5 句必须出现位移动作链" in result

    def test_bridge_echo_window_chars(self) -> None:
        bible = StoryBible(premise="test", bridge_echo_window_chars=1200)
        contract = {"continuity_protocol": {"pov_visibility_rule": "限知视角"}}
        result = _render(contract, bible, "现代", "bridge")
        assert "开场前 1200 字须回应" in result

    def test_max_key_revelations_per_chapter(self) -> None:
        bible = StoryBible(premise="test", max_key_revelations_per_chapter=3)
        contract = {"continuity_protocol": {"pov_visibility_rule": "限知视角"}}
        result = _render(contract, bible, "现代", "plan")
        assert "单章重大揭示不超过 3 条" in result

    def test_min_unresolved_threads_to_keep(self) -> None:
        bible = StoryBible(premise="test", min_unresolved_threads_to_keep=2)
        contract = {"continuity_protocol": {"pov_visibility_rule": "限知视角"}}
        result = _render(contract, bible, "现代", "plan")
        assert "至少保留 2 条未决线索" in result

    def test_min_unresolved_threads_zero(self) -> None:
        bible = StoryBible(premise="test", min_unresolved_threads_to_keep=0)
        contract = {"continuity_protocol": {"pov_visibility_rule": "限知视角"}}
        result = _render(contract, bible, "现代", "plan")
        assert "若为 0 则不强制" in result

    def test_default_values_without_story_bible(self) -> None:
        contract = {"continuity_protocol": {"pov_visibility_rule": "限知视角"}}
        result = _render(contract, None, "现代", "plan")
        assert "前 3 句必须出现位移动作链" in result
        assert "开场前 900 字须回应" in result
        assert "单章重大揭示不超过 2 条" in result

    def test_time_convention_rendered(self) -> None:
        contract = {"continuity_protocol": {"pov_visibility_rule": "限知视角"}}
        result = _render(contract, None, "古代中国：时辰/刻", "plan")
        assert "古代中国：时辰/刻" in result

    def test_world_context_rendered(self) -> None:
        contract = {
            "world_context": {
                "social_hierarchy": "皇权、内廷、外朝、士族门第层级分明",
                "address_rules": ["臣下面圣称陛下，不直呼帝王名讳"],
                "anachronism_blacklist": ["手机", "打卡"],
                "dialogue_register_rules": ["朝堂对白庄重克制，私谈可略白话"],
            },
            "continuity_protocol": {"pov_visibility_rule": "限知视角"},
        }
        result = _render(contract, None, "古代中国：时辰/刻", "plan")
        assert "时代语境/礼制约束" in result
        assert "称谓规则：臣下面圣称陛下，不直呼帝王名讳" in result
        assert "时代错位禁用：手机；打卡" in result
        assert "对白语体：朝堂对白庄重克制，私谈可略白话" in result

    def test_no_contract(self) -> None:
        result = _render(None, None, "现代", "plan")
        assert "全局叙事契约" not in result
