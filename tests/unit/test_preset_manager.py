from __future__ import annotations

import json

import pytest

import novel_forge.desktop.preset_manager as preset_manager


@pytest.fixture()
def preset_dir(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(preset_manager, "_PRESETS_DIR", tmp_path / ".presets")
    return tmp_path / ".presets"


def test_save_and_load_short_preset_round_trip(preset_dir) -> None:
    payload = {
        "theme": "旧怀表里的第二人生",
        "genre": "literary",
        "tone": "warm",
        "length_target": 2800,
        "max_edit_rounds": 3,
        "extra_field": "ignored",
    }

    saved = preset_manager.save_preset("short", "demo", payload)
    loaded = preset_manager.load_preset("short", "demo")
    raw = json.loads(saved.read_text(encoding="utf-8"))

    assert saved.is_file()
    assert "extra_field" not in raw
    assert loaded["theme"] == "旧怀表里的第二人生"
    assert loaded["length_target"] == 2800
    assert loaded["segment_trigger_words"] == 5500
    assert loaded["language"] == "zh"
    assert set(loaded) == set(preset_manager.SHORT_TEMPLATE)


def test_export_long_template_contains_only_long_fields(tmp_path) -> None:
    path = tmp_path / "long_template.json"

    preset_manager.export_preset_json(path, "long")

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload == preset_manager.LONG_TEMPLATE
    assert "theme" not in payload
    assert payload["research_enabled"] is False
    assert payload["research_provider"] == "auto"
    assert payload["research_query_hint"] == ""


def test_save_and_load_long_preset_keeps_research_configuration(preset_dir) -> None:
    preset_manager.save_preset(
        "long",
        "档案谜案",
        {
            "premise": "记者调查被篡改的旧城档案",
            "research_enabled": True,
            "research_provider": "Brave",
            "research_query_hint": "城市更新档案制度\n历史建筑保护",
        },
    )

    loaded = preset_manager.load_preset("long", "档案谜案")

    assert loaded["research_enabled"] is True
    assert loaded["research_provider"] == "brave"
    assert loaded["research_query_hint"] == "城市更新档案制度\n历史建筑保护"


def test_import_from_cli_json_returns_sanitized_mode_payloads(tmp_path) -> None:
    source = tmp_path / "novel_forge.cli.json"
    source.write_text(
        json.dumps(
            {
                "run_short": {
                    "theme": "短篇主题",
                    "genre": "romance",
                    "length": 3600,
                    "noise": "drop",
                },
                "init_long": {
                    "premise": "长篇前提",
                    "genre": "mystery",
                    "total_chapters": 30,
                    "research_enabled": True,
                    "research_provider": "searxng",
                    "research_query_hint": "唐代市舶司制度",
                    "noise": "drop",
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    result = preset_manager.import_from_cli_json(source)

    assert set(result["short"]) == set(preset_manager.SHORT_TEMPLATE)
    assert set(result["long"]) == set(preset_manager.LONG_TEMPLATE)
    assert result["short"]["theme"] == "短篇主题"
    assert result["long"]["premise"] == "长篇前提"
    assert result["long"]["research_enabled"] is True
    assert result["long"]["research_provider"] == "searxng"
    assert result["long"]["research_query_hint"] == "唐代市舶司制度"


def test_exported_plain_preset_can_be_imported_again(tmp_path) -> None:
    path = tmp_path / "long_preset.json"
    preset_manager.export_preset_json(
        path,
        "long",
        {
            "premise": "近未来神经接口伦理争议",
            "research_enabled": True,
            "research_provider": "tavily",
            "research_query_hint": "脑机接口伦理 临床风险",
        },
    )

    result = preset_manager.import_from_cli_json(path, preferred_mode="long")

    assert set(result) == {"long"}
    assert result["long"]["premise"] == "近未来神经接口伦理争议"
    assert result["long"]["research_enabled"] is True
    assert result["long"]["research_provider"] == "tavily"
    assert result["long"]["research_query_hint"] == "脑机接口伦理 临床风险"


def test_save_polish_history_keeps_creative_note_metadata(preset_dir) -> None:
    preset_manager.save_polish_history(
        "long",
        "demo",
        operation="polish_applied",
        data={"premise": "新前提", "extra_field": "ignored"},
        hint="强化关系张力",
        metadata={
            "accepted_fields": ["premise"],
            "rejected_fields": ["world_hint"],
            "creative_note": {
                "core_pitch": "关系误判推动资产谜局",
                "next_moves": ["强化前三章钩子"],
            },
        },
    )

    entries = preset_manager.load_polish_history("long", "demo")

    assert len(entries) == 1
    entry = entries[0]
    assert entry["metadata"]["accepted_fields"] == ["premise"]
    assert entry["metadata"]["creative_note"]["core_pitch"] == "关系误判推动资产谜局"
    assert "extra_field" not in entry["data"]
