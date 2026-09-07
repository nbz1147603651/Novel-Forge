"""Regression tests for CharacterBible fallback in init-long."""

from __future__ import annotations

from novel_forge.core.config import Settings
from novel_forge.gateway.adapters.mock import MockAdapter
from novel_forge.gateway.router import ModelRouter
from novel_forge.persistence.filesystem import FileSystemStorage
from novel_forge.pipeline.chapter_runner import ChapterRunner
from novel_forge.prompts.builder import PromptBuilder


def test_init_long_character_bible_fallback_on_empty_list(tmp_path) -> None:
    adapter = MockAdapter()
    router = ModelRouter(adapters={"mock": adapter}, default_provider="mock")
    runner = ChapterRunner(
        router,
        PromptBuilder(),
        FileSystemStorage(tmp_path),
        settings=Settings(_env_file=None),
    )

    # Directly exercise the coercion helper with the exact failure shape.
    bible = runner._coerce_character_bible({"characters": []})
    assert len(bible.characters) == 1
    assert bible.characters[0].name
