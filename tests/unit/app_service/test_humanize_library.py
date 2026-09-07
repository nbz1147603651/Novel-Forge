"""Tests for the Engine-owned humanize-library mutation boundary."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from novel_forge.app_service.humanize_library import (
    HumanizeLibraryConflictError,
    HumanizePatternInput,
    load_humanize_library,
    merge_humanize_patterns,
    remove_humanize_pattern,
    save_humanize_pattern,
    set_humanize_pattern_enabled,
)
from novel_forge.memory.humanize_library_store import HumanizeLibrary


@pytest.fixture
def library_factory(tmp_path: Path) -> Callable[[], HumanizeLibrary]:
    database = tmp_path / "humanize-library.db"
    return lambda: HumanizeLibrary.from_path(database)


def test_humanize_pattern_mutations_are_revision_guarded(
    library_factory: Callable[[], HumanizeLibrary],
) -> None:
    initial = load_humanize_library(library_factory=library_factory)
    created = save_humanize_pattern(
        HumanizePatternInput(
            pattern_id="lib_user_a1b2c3d4",
            name="空泛收束",
            category="模板结尾",
            severity="高",
            keywords=("因此", "最终", "命运"),
            notes="读者测试发现的重复收束。",
            example_phrase="最终，一切都归于命运。",
        ),
        expected_revision=initial.revision,
        library_factory=library_factory,
    )

    assert created.entry is not None
    assert created.entry.pattern_id == "lib_user_a1b2c3d4"
    assert created.entry.severity == "high"
    assert created.entry.example_phrases == ["最终，一切都归于命运。"]

    disabled = set_humanize_pattern_enabled(
        "lib_user_a1b2c3d4",
        False,
        expected_revision=created.revision,
        library_factory=library_factory,
    )
    assert disabled.entry is not None and disabled.entry.enabled is False

    with pytest.raises(HumanizeLibraryConflictError):
        remove_humanize_pattern(
            "lib_user_a1b2c3d4",
            expected_revision=created.revision,
            library_factory=library_factory,
        )

    removed = remove_humanize_pattern(
        "lib_user_a1b2c3d4",
        expected_revision=disabled.revision,
        library_factory=library_factory,
    )
    assert removed.entry is None
    assert load_humanize_library(library_factory=library_factory).entries == ()


def test_humanize_pattern_merge_preserves_target_and_removes_source(
    library_factory: Callable[[], HumanizeLibrary],
) -> None:
    initial = load_humanize_library(library_factory=library_factory)
    source = save_humanize_pattern(
        HumanizePatternInput(
            pattern_id="lib_user_11111111",
            name="来源模式",
            category="句式",
            severity="medium",
        ),
        expected_revision=initial.revision,
        library_factory=library_factory,
    )
    save_humanize_pattern(
        HumanizePatternInput(
            pattern_id="lib_user_22222222",
            name="目标模式",
            category="句式",
            severity="medium",
        ),
        expected_revision=source.revision,
        library_factory=library_factory,
    )
    with library_factory() as library:
        library.update("lib_user_11111111", hit_count=2, last_hit_chapter=7)
        library.update("lib_user_22222222", hit_count=3, last_hit_chapter=5)

    before_merge = load_humanize_library(library_factory=library_factory)
    merged = merge_humanize_patterns(
        "lib_user_11111111",
        "lib_user_22222222",
        expected_revision=before_merge.revision,
        library_factory=library_factory,
    )

    assert merged.entry is not None
    assert merged.entry.pattern_id == "lib_user_22222222"
    assert merged.entry.hit_count == 5
    assert merged.entry.last_hit_chapter == 7
    assert [
        entry.pattern_id for entry in load_humanize_library(library_factory=library_factory).entries
    ] == ["lib_user_22222222"]


def test_humanize_imported_pattern_gets_a_valid_imported_id(
    library_factory: Callable[[], HumanizeLibrary],
) -> None:
    initial = load_humanize_library(library_factory=library_factory)

    created = save_humanize_pattern(
        HumanizePatternInput(
            name="外部样例",
            category="模板句式",
            severity="low",
            source="imported",
        ),
        expected_revision=initial.revision,
        library_factory=library_factory,
    )

    assert created.entry is not None
    assert created.entry.source == "imported"
    assert created.entry.pattern_id.startswith("lib_imported_")
