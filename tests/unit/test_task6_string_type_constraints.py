#!/usr/bin/env python3
"""Render test: verify string type constraints are present in rendered templates."""

from __future__ import annotations

from pathlib import Path

from jinja2 import Environment, FileSystemLoader

PROMPTS_DIR = Path(__file__).parent.parent.parent / "novel_forge" / "prompts" / "prompts"

ENRICH_CONSTRAINT = "appearance`、`personality`、`backstory`、`arc`、`voice` 必须是单段字符串；禁止拆成对象、数组或带子字段的结构"
INTRODUCE_CONSTRAINT = "appearance`、`personality`、`backstory`、`arc`、`abilities`、`voice`、`notes` 必须是单段字符串；禁止拆成对象、数组或带子字段的结构"

EVIDENCE_DIR = Path(__file__).parent.parent.parent / ".omo" / "evidence" / "nested-field-type-drift-fix"


def _render_template(filename: str, context: dict | None = None) -> str:
    env = Environment(loader=FileSystemLoader(str(PROMPTS_DIR)), trim_blocks=True, lstrip_blocks=True)
    tpl = env.get_template(f"initialization/{filename}")
    return tpl.render(context or {})


def test_enrich_character() -> None:
    output = _render_template("enrich_character.j2", {
        "character_name": "TestChar",
        "role": "supporting",
        "chapter_number": 1,
        "relationships": {},
        "chapter_text": "Test chapter text.",
    })
    assert ENRICH_CONSTRAINT in output, (
        f"enrich_character.j2 missing constraint string.\n"
        f"Expected substring: {ENRICH_CONSTRAINT}"
    )
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    (EVIDENCE_DIR / "task-6-enrich-constraint.txt").write_text(output, encoding="utf-8")


def test_introduce_character() -> None:
    output = _render_template("introduce_character.j2", {
        "character_name": "TestChar",
        "chapter_number": 1,
        "genre": "mystery",
        "tone": "suspenseful",
        "premise": "Test premise.",
        "world_setting": "",
        "outline_context": "Test outline context.",
        "existing_characters": [],
    })
    assert INTRODUCE_CONSTRAINT in output, (
        f"introduce_character.j2 missing constraint string.\n"
        f"Expected substring: {INTRODUCE_CONSTRAINT}"
    )
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    (EVIDENCE_DIR / "task-6-introduce-constraint.txt").write_text(output, encoding="utf-8")


if __name__ == "__main__":
    test_enrich_character()
    print("PASS: enrich_character.j2 contains string type constraint")
    test_introduce_character()
    print("PASS: introduce_character.j2 contains string type constraint")
    print(f"\nEvidence saved to {EVIDENCE_DIR}")
