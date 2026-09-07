from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from novel_forge.pipeline.long.services.init.init_upstream_health import (
    assert_upstream_health_allows_progress,
    record_character_bible_health,
    record_character_system_health,
    record_story_bible_health,
    research_context_fingerprint,
    story_bible_research_context_matches,
)


class _Storage:
    def exists(self, path: Path) -> bool:
        return path.exists()

    def load_json(self, path: Path) -> dict[str, object]:
        return json.loads(path.read_text(encoding="utf-8"))

    def save_json(self, path: Path, payload: dict[str, object]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


class _Model:
    def __init__(self, **values: object) -> None:
        self.__dict__.update(values)

    def model_dump(self, mode: str = "json") -> dict[str, object]:
        del mode
        return {key: _dump(value) for key, value in self.__dict__.items()}


def _dump(value: object) -> object:
    if isinstance(value, _Model):
        return value.model_dump(mode="json")
    if isinstance(value, list):
        return [_dump(item) for item in value]
    return value


def _ctx(tmp_path: Path) -> SimpleNamespace:
    events: list[tuple[str, dict[str, object]]] = []
    return SimpleNamespace(
        layout=SimpleNamespace(reports_dir=tmp_path / "reports"),
        storage=_Storage(),
        on_step=lambda step, payload: events.append((step, payload)),
        events=events,
    )


def test_story_bible_health_records_research_context_fingerprint(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)
    research_context = {"summary": "旧城档案制度资料"}

    record_story_bible_health(
        ctx,
        story_bible=_Model(
            premise="记者调查旧城档案",
            rules=["档案调阅需要登记"],
            time_convention="近未来",
        ),
        research_context=research_context,
    )

    assert story_bible_research_context_matches(
        ctx,
        research_context_fingerprint(research_context),
    )
    assert not story_bible_research_context_matches(
        ctx,
        research_context_fingerprint({"summary": "另一个资料包"}),
    )


def test_story_bible_health_blocks_missing_core_premise(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)

    record_story_bible_health(
        ctx,
        story_bible=_Model(premise="", rules=[], time_convention=""),
        research_context={},
    )

    with pytest.raises(ValueError):
        assert_upstream_health_allows_progress(ctx, artifact="story_bible")


def test_character_bible_health_blocks_duplicate_or_missing_ids(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)

    record_character_bible_health(
        ctx,
        character_bible=_Model(
            characters=[
                _Model(name="沈念卿", character_id="char_a"),
                _Model(name="陆云峥", character_id="char_a"),
                _Model(name="档案员", character_id=""),
            ]
        ),
    )

    with pytest.raises(ValueError):
        assert_upstream_health_allows_progress(ctx, artifact="character_bible")


def test_character_bible_health_blocks_missing_source_voice(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)

    record_character_bible_health(
        ctx,
        character_bible=_Model(
            characters=[
                _Model(
                    name="沈念卿",
                    character_id="char_a",
                    personality="克制",
                    backstory="调查旧案",
                    arc="从逃避到直面",
                    voice="",
                )
            ]
        ),
    )

    with pytest.raises(ValueError, match="character_bible"):
        assert_upstream_health_allows_progress(ctx, artifact="character_bible")


def test_character_system_health_blocks_isolated_active_character(tmp_path: Path) -> None:
    ctx = _ctx(tmp_path)

    record_character_system_health(
        ctx,
        character_system=_Model(
            relationship_edges=[],
            identity_links=[],
            profiles=[{"name": "阿荞", "voice": "低声慢语。"}],
            audit=[_Model(code="isolated_active_character", character_name="阿荞")],
        ),
    )

    with pytest.raises(ValueError, match="character_system"):
        assert_upstream_health_allows_progress(ctx, artifact="character_system")
