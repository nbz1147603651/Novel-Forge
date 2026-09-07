"""Tests for CLI runtime config helpers."""

from __future__ import annotations

import json
from inspect import signature
from pathlib import Path

import pytest

from novel_forge.cli.commands.init_long import init_long
from novel_forge.cli.runtime_config import (
    RuntimeConfigError,
    default_config_template,
    load_runtime_config,
    resolve_bool_option,
    resolve_float_option,
    resolve_int_option,
    resolve_json_dict_option,
    resolve_str_option,
)


class TestRuntimeConfigLoading:
    def test_loads_default_filename(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.chdir(tmp_path)
        path = tmp_path / "novel_forge.cli.json"
        path.write_text(json.dumps({"run_short": {"theme": "x"}}), encoding="utf-8")
        cfg, used_path = load_runtime_config(None)
        assert used_path is not None
        assert used_path.resolve() == path.resolve()
        assert cfg["run_short"]["theme"] == "x"

    def test_returns_empty_when_no_config(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("NOVEL_FORGE_CLI_CONFIG", raising=False)
        cfg, used_path = load_runtime_config(None)
        assert cfg == {}
        assert used_path is None

    def test_explicit_missing_path_raises(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.chdir(tmp_path)
        missing_path = tmp_path / "missing.json"
        with pytest.raises(RuntimeConfigError, match="配置文件不存在"):
            load_runtime_config(str(missing_path))

    def test_default_template_has_key_sections(self) -> None:
        template = default_config_template()
        assert "run_short" in template
        assert "init_long" in template
        assert "run_chapter" in template
        assert "sync_bible" in template
        assert "book_audit" in template
        assert template["run_chapter"]["chapter"] == 0
        assert template["run_chapter"]["auto"] is False
        assert template["run_chapter"]["end_chapter"] == 0
        assert template["run_chapter"]["ai_judge_apply_mode"] == "assist"
        assert template["init_long"]["polish_hint"] == ""
        assert template["init_long"]["research_enabled"] is False
        assert template["init_long"]["research_provider"] == "auto"
        assert template["init_long"]["research_query_hint"] == ""
        assert template["init_long"]["blueprint_element_preferences"] == {}
        assert template["sync_bible"]["chapter"] == 0
        assert template["book_audit"]["analysis_mode"] == "auto"

    def test_init_long_command_exposes_outline_polish_and_element_options(self) -> None:
        params = signature(init_long).parameters
        assert "polish_hint" in params
        assert "research_enabled" in params
        assert "research_provider" in params
        assert "research_query_hint" in params
        assert "blueprint_element_preferences" in params


class TestRuntimeConfigResolve:
    def test_cli_value_overrides_config(self) -> None:
        cfg = {"run_short": {"genre": "mystery"}}
        value = resolve_str_option(
            "fantasy", cfg, section="run_short", key="genre", default="scifi"
        )
        assert value == "fantasy"

    def test_falls_back_to_defaults_block(self) -> None:
        cfg = {"defaults": {"mock": True}}
        value = resolve_bool_option(None, cfg, section="run_short", key="mock", default=False)
        assert value is True

    def test_resolve_int_validates_type(self) -> None:
        cfg = {"run_short": {"length": "3000"}}
        with pytest.raises(RuntimeConfigError, match="必须是整数"):
            resolve_int_option(
                None, cfg, section="run_short", key="length", default=1000, minimum=1
            )

    def test_resolve_float_accepts_int_and_float_config(self) -> None:
        cfg = {"book_audit": {"temperature": 0.25}}
        value = resolve_float_option(
            None,
            cfg,
            section="book_audit",
            key="temperature",
            default=None,
            minimum=0.0,
            maximum=2.0,
        )
        assert value == 0.25

    def test_resolve_json_dict_option_accepts_cli_json(self) -> None:
        value = resolve_json_dict_option(
            '{"preset_id":"mystery","manual_override":true}',
            {},
            section="init_long",
            key="blueprint_element_preferences",
            default={},
        )
        assert value == {"preset_id": "mystery", "manual_override": True}

    def test_resolve_json_dict_option_accepts_config_dict(self) -> None:
        cfg = {"init_long": {"blueprint_element_preferences": {"preset_id": "romance"}}}
        value = resolve_json_dict_option(
            None,
            cfg,
            section="init_long",
            key="blueprint_element_preferences",
            default={},
        )
        assert value == {"preset_id": "romance"}

    def test_resolve_json_dict_option_rejects_non_object(self) -> None:
        with pytest.raises(RuntimeConfigError, match="必须是 JSON 对象"):
            resolve_json_dict_option(
                '["mystery"]',
                {},
                section="init_long",
                key="blueprint_element_preferences",
                default={},
            )
