"""Smoke test: verify all registered templates load for every stable locale.

This catches Jinja2 syntax errors, missing imports, and broken macro
definitions that ``audit_prompt_packs.py`` may not cover (e.g. runtime
filter resolution, cross-template imports).
"""

from __future__ import annotations

import re

import pytest

from novel_forge.common.constants import TaskType
from novel_forge.prompts.packs import (
    DEFAULT_PROMPT_LOCALE,
    PromptPack,
    discover_prompt_packs,
)
from novel_forge.prompts.registry import _TASK_TEMPLATE_MAP, PromptRegistry


def _stable_packs() -> dict[str, PromptPack]:
    """Return only stable prompt packs."""
    return {k: v for k, v in discover_prompt_packs().items() if v.is_stable}


@pytest.mark.parametrize("locale", sorted(_stable_packs().keys()))
def test_all_registered_templates_load(locale: str) -> None:
    """Every registered TaskType template must load without Jinja2 errors."""
    registry = PromptRegistry(locale=locale)
    failures: list[str] = []

    for task_type, template_path in _TASK_TEMPLATE_MAP.items():
        try:
            registry.get_template(task_type, prompt_locale=locale)
        except Exception as exc:
            failures.append(f"{task_type.name} ({template_path}): {exc}")

    if failures:
        msg = f"Failed to load templates for locale={locale}:\n" + "\n".join(failures)
        pytest.fail(msg)


@pytest.mark.parametrize("locale", sorted(_stable_packs().keys()))
def test_all_registered_templates_have_source(locale: str) -> None:
    """Every registered TaskType must have a template file on disk."""
    pack = _stable_packs()[locale]
    missing: list[str] = []

    for task_type, template_path in _TASK_TEMPLATE_MAP.items():
        full_path = pack.templates_dir / template_path
        if not full_path.exists():
            missing.append(f"{task_type.name}: {template_path}")

    if missing:
        msg = f"Missing template files for locale={locale}:\n" + "\n".join(missing)
        pytest.fail(msg)


def test_default_locale_is_stable() -> None:
    """The default locale pack must be stable."""
    packs = _stable_packs()
    assert DEFAULT_PROMPT_LOCALE in packs, f"Default locale {DEFAULT_PROMPT_LOCALE!r} is not stable"


@pytest.mark.parametrize(
    "task_type",
    (
        TaskType.SUMMARIZE_CHAPTER,
        TaskType.SUMMARIZE_SCENE,
        TaskType.SUMMARIZE_VOLUME,
        TaskType.SUMMARIZE_ARC,
    ),
)
def test_english_hierarchical_summary_templates_render(task_type: TaskType) -> None:
    """Stable English summary prompts must resolve their shared map-reduce macro."""
    rendered = PromptRegistry(locale="en").render(
        task_type,
        prompt_locale="en",
        summary_pass="evidence_chunk",
        source_scope="chunk 1/2",
        text="A source event occurs.",
        chapter_text="A source event occurs.",
        scene_text="A source event occurs.",
        target_words=100,
        chapter_number=1,
        chapter_title="Opening",
        outline={},
        scene_index=1,
        scene_title="Arrival",
        volume_title="Volume One",
        volume_theme="Discovery",
        arc_name="Discovery Arc",
    )

    assert "Hierarchical Summary Pass" in rendered
    assert "chunk 1/2" in rendered


@pytest.mark.parametrize(
    ("task_type", "stage_cards", "expected_fragments"),
    [
        (
            TaskType.TTS_EMOTION_LABEL,
            {
                "segments": [
                    {
                        "segment_index": 0,
                        "segment_type": "dialogue",
                        "character_name": "Ari",
                        "emotion": "neutral",
                        "emotion_intensity": 0.5,
                        "sub_emotion": None,
                        "scene_context": "dockside",
                        "text": "We should leave now.",
                        "position_ratio": 0.5,
                    }
                ]
            },
            ("Allowed emotion enum", "determined", "playful", "Source text"),
        ),
        (
            TaskType.TTS_SOUND_DESIGN,
            {
                "chapter_number": 3,
                "scene_intents": [
                    {
                        "scene_id": "scene_01",
                        "location": "harbor",
                        "time_marker": "dawn",
                        "emotional_beat": "tense",
                        "sensory_notes": "fog horn",
                    }
                ],
            },
            ("Create a sound cue sheet", "Scene plan", "location=harbor"),
        ),
    ],
)
def test_english_tts_templates_render_localized_guidance(
    task_type: TaskType,
    stage_cards: dict[str, object],
    expected_fragments: tuple[str, ...],
) -> None:
    """The stable English TTS prompts remain English and preserve key guidance."""
    rendered = PromptRegistry(locale="en").render(
        task_type,
        prompt_locale="en",
        stage_cards=stage_cards,
    )

    for fragment in expected_fragments:
        assert fragment in rendered
    assert not re.search(r"[\u4e00-\u9fff]", rendered)
