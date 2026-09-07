from __future__ import annotations

from pathlib import Path
from typing import Any

from novel_forge.memory.canon_history_queries import (
    get_character_history,
    get_relationship_evolution,
)
from novel_forge.memory.integration import MemoryContext


def _memory_context_with_storage(storage: _CanonStorage | None) -> MemoryContext:
    ctx = MemoryContext()
    ctx._project_id = "demo"
    ctx._storage = storage
    return ctx


class _CanonStorage:
    def __init__(self, payload: dict[str, Any] | None) -> None:
        self.payload = payload
        self.paths: list[Path] = []

    def project_path(self, project_id: str) -> Path:
        return Path("/tmp") / project_id

    def exists(self, path: Path) -> bool:
        self.paths.append(path)
        return self.payload is not None

    def load_json(self, _path: Path) -> dict[str, Any]:
        assert self.payload is not None
        return self.payload


def test_relationship_evolution_reads_legacy_canon_exit_state_shape() -> None:
    storage = _CanonStorage(
        {
            "chapter_exit_states": {
                "2": {
                    "relationships": {
                        "林远-周岚": {
                            "characters": ["林远", "周岚"],
                            "public_status": "互相试探",
                            "trust": 0.4,
                            "tension": 0.7,
                            "last_shift_event": "周岚隐瞒线索",
                        },
                        "周岚-守夜人": {
                            "characters": ["周岚", "守夜人"],
                            "public_status": "旧识",
                        },
                    }
                },
                "4": {
                    "relationships": {
                        "林远-守夜人": {
                            "characters": ["守夜人", "林远"],
                            "public_status": "托付",
                        }
                    }
                },
            }
        }
    )
    ctx = _memory_context_with_storage(storage)

    result = ctx.get_relationship_evolution("林远", current_chapter=5, lookback=4)

    assert storage.paths == [Path("/tmp/demo/canon/canon_current.json")]
    expected = [
        {
            "chapter": 2,
            "partner": "周岚",
            "public_status": "互相试探",
            "trust": 0.4,
            "tension": 0.7,
            "shift_event": "周岚隐瞒线索",
        },
        {
            "chapter": 4,
            "partner": "守夜人",
            "public_status": "托付",
            "trust": 0.5,
            "tension": 0.5,
            "shift_event": "",
        },
    ]
    assert result == expected
    assert (
        get_relationship_evolution(
            storage=storage,
            project_id="demo",
            character_name="林远",
            current_chapter=5,
            lookback=4,
        )
        == expected
    )


def test_character_history_reads_legacy_canon_exit_state_shape() -> None:
    storage = _CanonStorage(
        {
            "chapter_exit_states": {
                "2": {
                    "character_end_states": {
                        "林远": {
                            "physical": {
                                "location": "钟楼",
                                "inventory": ["怀表", "纸灰"],
                            },
                            "emotional": {"primary_emotion": "警觉"},
                            "alive": True,
                        }
                    }
                },
                "3": {
                    "character_end_states": {
                        "周岚": {
                            "physical": {"location": "渡口"},
                            "emotional": {"primary_emotion": "犹疑"},
                        }
                    }
                },
            }
        }
    )
    ctx = _memory_context_with_storage(storage)

    result = ctx.get_character_history("林远", current_chapter=5, lookback=5)

    expected = [
        {
            "chapter": 2,
            "location": "钟楼",
            "emotional_state": "警觉",
            "inventory": ["怀表", "纸灰"],
            "alive": True,
        }
    ]
    assert result == expected
    assert (
        get_character_history(
            storage=storage,
            project_id="demo",
            character_name="林远",
            current_chapter=5,
            lookback=5,
        )
        == expected
    )


def test_canon_history_queries_return_empty_without_storage_or_file() -> None:
    no_storage = _memory_context_with_storage(None)
    missing_file = _memory_context_with_storage(_CanonStorage(None))

    assert no_storage.get_relationship_evolution("林远", current_chapter=5) == []
    assert no_storage.get_character_history("林远", current_chapter=5) == []
    assert missing_file.get_relationship_evolution("林远", current_chapter=5) == []
    assert missing_file.get_character_history("林远", current_chapter=5) == []
