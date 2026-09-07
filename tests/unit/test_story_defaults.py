"""Regression tests for neutral story defaults across entry points."""

from __future__ import annotations

import json
from pathlib import Path

from novel_forge.cli.runtime_config import default_config_template
from novel_forge.core.domain.story_defaults import DEFAULT_GENRE, DEFAULT_TONE
from novel_forge.desktop.preset_manager import LONG_TEMPLATE, SHORT_TEMPLATE
from novel_forge.desktop.workflow_requests import build_init_long_request, build_short_request
from novel_forge.workspace.contracts import InitLongRequest, RunShortRequest

_REPO_ROOT = Path(__file__).resolve().parents[2]


def test_workspace_contracts_default_to_neutral_genre_and_tone() -> None:
    short = RunShortRequest(theme="一封迟到的来信")
    long = InitLongRequest(premise="一座城市开始遗忘自己的名字")

    assert short.genre == DEFAULT_GENRE
    assert short.tone == DEFAULT_TONE
    assert long.genre == DEFAULT_GENRE
    assert long.tone == DEFAULT_TONE


def test_cli_config_template_default_story_fields_are_neutral() -> None:
    template = default_config_template()

    assert template["run_short"]["genre"] == DEFAULT_GENRE
    assert template["run_short"]["tone"] == DEFAULT_TONE
    assert template["init_long"]["genre"] == DEFAULT_GENRE
    assert template["init_long"]["tone"] == DEFAULT_TONE


def test_tracked_cli_config_default_story_fields_are_neutral() -> None:
    config = json.loads((_REPO_ROOT / "novel_forge.cli.json").read_text(encoding="utf-8"))

    assert config["run_short"]["genre"] == DEFAULT_GENRE
    assert config["run_short"]["tone"] == DEFAULT_TONE
    assert config["run_short"]["project_id"] == ""
    assert config["init_long"]["genre"] == DEFAULT_GENRE
    assert config["init_long"]["tone"] == DEFAULT_TONE
    assert config["init_long"]["project_id"] == ""
    assert config["run_chapter"]["project_id"] == ""
    assert config["run_chapter"]["auto"] is False


def test_desktop_presets_default_to_neutral_genre_and_tone() -> None:
    assert SHORT_TEMPLATE["genre"] == DEFAULT_GENRE
    assert SHORT_TEMPLATE["tone"] == DEFAULT_TONE
    assert SHORT_TEMPLATE["writing_mode"] == "auto"
    assert LONG_TEMPLATE["genre"] == DEFAULT_GENRE
    assert LONG_TEMPLATE["tone"] == DEFAULT_TONE


def test_desktop_request_builders_use_neutral_fallbacks() -> None:
    short = build_short_request(
        project_id="",
        theme="一封迟到的来信",
        genre="",
        tone="",
        length_target=3000,
        max_edit_rounds=2,
        title="",
        language="zh",
        characters_hint="",
        world_hint="",
        conflict_hint="",
        pov_hint="",
        opening_style="",
        ending_style="",
        extra_instructions="",
    )
    long = build_init_long_request(
        project_id="",
        premise="一座城市开始遗忘自己的名字",
        genre="",
        tone="",
        total_chapters=20,
        words_per_chapter=3000,
        volume_mode="auto",
        chapters_per_volume=0,
        title="",
        language="zh",
        characters_hint="",
        world_hint="",
        conflict_hint="",
        pov_hint="",
        opening_style="",
        ending_style="",
        extra_instructions="",
    )

    assert short.genre == DEFAULT_GENRE
    assert short.tone == DEFAULT_TONE
    assert long.genre == DEFAULT_GENRE
    assert long.tone == DEFAULT_TONE
    assert not hasattr(long, "init_blueprint_mode")
