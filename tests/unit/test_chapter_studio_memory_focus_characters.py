"""Tests for extracting structured chapter focus characters."""

from __future__ import annotations

import json
from pathlib import Path

from novel_forge.desktop.pages.chapter_studio.memory import (
    load_chapter_focus_characters,
)
from novel_forge.persistence.models import ProjectLayout


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def test_focus_characters_from_chapter_plan(tmp_path: Path) -> None:
    layout = ProjectLayout(tmp_path / "proj")
    layout.ensure_dirs()

    _write_json(
        layout.chapter_plan_path(5),
        {
            "pov_character": "风伏京",
            "scene_intents": [
                {
                    "required_characters": ["风伏京", "罗浮"],
                    "character_motivations": [
                        {"character": "风伏京", "motivation": "稳住局势"},
                        {"character": "沈砚", "motivation": "试探底牌"},
                    ],
                },
                {
                    "required_characters": ["罗浮"],
                    "character_motivations": [],
                },
            ],
        },
    )

    names = load_chapter_focus_characters(layout.root, 5)

    assert names == ["风伏京", "罗浮", "沈砚"]


def test_focus_characters_fallback_to_state_packet(tmp_path: Path) -> None:
    layout = ProjectLayout(tmp_path / "proj")
    layout.ensure_dirs()

    _write_json(
        layout.chapter_state_packet_path(7),
        {
            "chapter_outline": {
                "pov_character": "风伏京",
            },
            "narrative_context": {
                "current_phase_key_characters": ["罗浮", "萧临"],
            },
        },
    )

    names = load_chapter_focus_characters(layout.root, 7)

    assert names == ["风伏京", "罗浮", "萧临"]


def test_focus_characters_support_generic_nested_character_keys(tmp_path: Path) -> None:
    layout = ProjectLayout(tmp_path / "proj")
    layout.ensure_dirs()

    _write_json(
        layout.chapter_plan_path(3),
        {
            "beats": [
                {
                    "participants": {
                        "characters": [
                            {"name": "顾承"},
                            {"name": "秦岚"},
                        ]
                    }
                }
            ],
            "meta": {
                "characters_involved": ["萧临"],
                # Should be ignored as prose, not a character name.
                "characters_hint": "主角在雨夜对峙，关系持续拉扯。",
            },
        },
    )

    names = load_chapter_focus_characters(layout.root, 3)

    assert names == ["顾承", "秦岚", "萧临"]
