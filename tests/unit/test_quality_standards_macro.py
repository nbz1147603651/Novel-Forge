"""Tests for _quality_standards.j2 phrase_blacklist macro rendering."""

from __future__ import annotations

import pytest
from jinja2 import DictLoader, Environment


@pytest.fixture
def env() -> Environment:
    loader = DictLoader({})
    return Environment(loader=loader)


def _load_quality_standards(env: Environment) -> str:
    template_path = "novel_forge/prompts/prompts/_base/_quality_standards.j2"
    with open(template_path, encoding="utf-8") as f:
        return f.read()


class TestPhraseBlacklistMacro:
    def test_default_blacklist(self, env: Environment) -> None:
        source = _load_quality_standards(env)
        env.loader = DictLoader({"test.j2": source + "\n{{ phrase_blacklist() }}"})  # type: ignore
        tpl = env.get_template("test.j2")
        result = tpl.render()
        assert "深吸一口气" in result
        assert "目光坚定" in result
        assert "声音低沉" in result

    def test_custom_phrases(self, env: Environment) -> None:
        source = _load_quality_standards(env)
        env.loader = DictLoader({"test.j2": source + "\n{{ phrase_blacklist(banned_phrases=['自定义短语']) }}"})  # type: ignore
        tpl = env.get_template("test.j2")
        result = tpl.render()
        assert "自定义短语" in result
        assert "深吸一口气" not in result

    def test_empty_phrases_renders_defaults(self, env: Environment) -> None:
        source = _load_quality_standards(env)
        env.loader = DictLoader({"test.j2": source + "\n{{ phrase_blacklist(banned_phrases=[]) }}"})  # type: ignore
        tpl = env.get_template("test.j2")
        result = tpl.render()
        assert "模板化短语黑名单" in result
        assert "深吸一口气" in result

    def test_none_uses_default(self, env: Environment) -> None:
        source = _load_quality_standards(env)
        env.loader = DictLoader({"test.j2": source + "\n{{ phrase_blacklist(banned_phrases=None) }}"})  # type: ignore
        tpl = env.get_template("test.j2")
        result = tpl.render()
        assert "深吸一口气" in result

    def test_custom_title(self, env: Environment) -> None:
        source = _load_quality_standards(env)
        env.loader = DictLoader({"test.j2": source + "\n{{ phrase_blacklist(title='自定义标题') }}"})  # type: ignore
        tpl = env.get_template("test.j2")
        result = tpl.render()
        assert "自定义标题" in result

    def test_empty_title(self, env: Environment) -> None:
        source = _load_quality_standards(env)
        env.loader = DictLoader({"test.j2": source + "\n{{ phrase_blacklist(title='') }}"})  # type: ignore
        tpl = env.get_template("test.j2")
        result = tpl.render()
        assert "模板化短语黑名单" not in result


class TestParagraphUniquenessMacro:
    def test_includes_phrase_blacklist(self, env: Environment) -> None:
        source = _load_quality_standards(env)
        env.loader = DictLoader({"test.j2": source + "\n{{ paragraph_uniqueness() }}"})  # type: ignore
        tpl = env.get_template("test.j2")
        result = tpl.render()
        assert "禁止连续两段" in result
        assert "深吸一口气" in result

    def test_custom_banned_phrases(self, env: Environment) -> None:
        source = _load_quality_standards(env)
        env.loader = DictLoader({"test.j2": source + "\n{{ paragraph_uniqueness(banned_phrases=['自定义']) }}"})  # type: ignore
        tpl = env.get_template("test.j2")
        result = tpl.render()
        assert "自定义" in result
