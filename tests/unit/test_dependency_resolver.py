"""Tests for novel_forge.core.infra.dependency_resolver."""

from __future__ import annotations

from pathlib import Path

import pytest

from novel_forge.core.infra.dependency_resolver import (
    OPERATION_DEPENDENCIES,
    Dependency,
    DependencyKind,
    DependencyResolver,
    Operation,
    check_init_long,
    check_prepare_chapter,
    check_repair,
    check_run_chapter,
)
from novel_forge.persistence.models import ProjectLayout


class TestDependencyKind:
    def test_file_kind(self) -> None:
        assert DependencyKind.FILE.value == "file"

    def test_directory_kind(self) -> None:
        assert DependencyKind.DIRECTORY.value == "directory"

    def test_chapter_file_kind(self) -> None:
        assert DependencyKind.CHAPTER_FILE.value == "chapter_file"

    def test_project_state_kind(self) -> None:
        assert DependencyKind.PROJECT_STATE.value == "project_state"

    def test_canon_state_kind(self) -> None:
        assert DependencyKind.CANON_STATE.value == "canon_state"

    def test_previous_chapter_kind(self) -> None:
        assert DependencyKind.PREVIOUS_CHAPTER.value == "previous_chapter"


class TestDependency:
    def test_create_dependency(self) -> None:
        dep = Dependency(
            kind=DependencyKind.FILE,
            path_hint="story_bible.json",
            description="Story bible file",
        )
        assert dep.kind == DependencyKind.FILE
        assert dep.path_hint == "story_bible.json"
        assert dep.description == "Story bible file"

    def test_resolve_path_file(self, tmp_path: Path) -> None:
        layout = ProjectLayout(tmp_path)
        dep = Dependency(
            kind=DependencyKind.FILE,
            path_hint="story_bible.json",
            description="Story bible file",
        )
        resolved = dep.resolve_path(layout)
        assert resolved == tmp_path / "story_bible.json"

    def test_resolve_path_chapter_file(self, tmp_path: Path) -> None:
        layout = ProjectLayout(tmp_path)
        dep = Dependency(
            kind=DependencyKind.CHAPTER_FILE,
            path_hint="plans/chapter_{chapter_num}_plan.json",
            description="Chapter plan",
        )
        resolved = dep.resolve_path(layout, chapter_num=5)
        assert resolved == tmp_path / "plans/chapter_5_plan.json"

    def test_resolve_path_chapter_file_missing_chapter_num_raises(self, tmp_path: Path) -> None:
        layout = ProjectLayout(tmp_path)
        dep = Dependency(
            kind=DependencyKind.CHAPTER_FILE,
            path_hint="plans/chapter_{chapter_num}_plan.json",
            description="Chapter plan",
        )
        with pytest.raises(ValueError, match="requires chapter_num"):
            dep.resolve_path(layout)

    def test_resolve_path_canon_state(self, tmp_path: Path) -> None:
        layout = ProjectLayout(tmp_path)
        dep = Dependency(
            kind=DependencyKind.CANON_STATE,
            path_hint="canon_current.json",
            description="Canon state",
        )
        resolved = dep.resolve_path(layout)
        assert resolved == tmp_path / "canon" / "canon_current.json"


class TestOperation:
    def test_operation_values(self) -> None:
        assert Operation.INIT_LONG.value == "init_long"
        assert Operation.PREPARE_CHAPTER.value == "prepare_chapter"
        assert Operation.RUN_CHAPTER.value == "run_chapter"
        assert Operation.REPAIR.value == "repair"


class TestOperationDependencies:
    def test_init_long_has_no_dependencies(self) -> None:
        assert OPERATION_DEPENDENCIES[Operation.INIT_LONG] == []

    def test_prepare_chapter_has_expected_dependencies(self) -> None:
        deps = OPERATION_DEPENDENCIES[Operation.PREPARE_CHAPTER]
        assert len(deps) == 4
        path_hints = {d.path_hint for d in deps}
        assert "story_bible.json" in path_hints
        assert "character_bible.json" in path_hints
        assert "outline.json" in path_hints
        assert "style_profile.json" in path_hints

    def test_run_chapter_has_expected_dependencies(self) -> None:
        deps = OPERATION_DEPENDENCIES[Operation.RUN_CHAPTER]
        assert len(deps) == 3
        path_hints = {d.path_hint for d in deps}
        assert "plans/chapter_{chapter_num}_bridge.json" in path_hints
        assert "plans/chapter_{chapter_num}_plan.json" in path_hints
        assert "canon_current.json" in path_hints

    def test_repair_has_expected_dependencies(self) -> None:
        deps = OPERATION_DEPENDENCIES[Operation.REPAIR]
        assert len(deps) == 2
        path_hints = {d.path_hint for d in deps}
        assert "chapters/chapter_{chapter_num}.md" in path_hints
        assert "canon_current.json" in path_hints


class TestDependencyResolver:
    @pytest.fixture
    def layout(self, tmp_path: Path) -> ProjectLayout:
        return ProjectLayout(tmp_path)

    @pytest.fixture
    def resolver(self) -> DependencyResolver:
        return DependencyResolver()

    def test_resolve_init_long(self, resolver: DependencyResolver, layout: ProjectLayout) -> None:
        deps = resolver.resolve(layout, Operation.INIT_LONG)
        assert deps == []

    def test_resolve_prepare_chapter(self, resolver: DependencyResolver, layout: ProjectLayout) -> None:
        deps = resolver.resolve(layout, Operation.PREPARE_CHAPTER)
        assert len(deps) == 4

    def test_resolve_run_chapter(self, resolver: DependencyResolver, layout: ProjectLayout) -> None:
        deps = resolver.resolve(layout, Operation.RUN_CHAPTER, chapter_num=1)
        assert len(deps) == 3

    def test_resolve_repair(self, resolver: DependencyResolver, layout: ProjectLayout) -> None:
        deps = resolver.resolve(layout, Operation.REPAIR, chapter_num=1)
        assert len(deps) == 2

    def test_check_missing_none_missing(self, resolver: DependencyResolver, layout: ProjectLayout) -> None:
        layout.root.mkdir(parents=True, exist_ok=True)
        layout.bible_path.touch()
        layout.characters_path.touch()
        layout.outline_path.touch()
        layout.style_profile_path.touch()
        missing = resolver.check_missing(layout, Operation.PREPARE_CHAPTER)
        assert missing == []

    def test_check_missing_some_missing(self, resolver: DependencyResolver, layout: ProjectLayout) -> None:
        layout.root.mkdir(parents=True, exist_ok=True)
        layout.bible_path.touch()
        layout.characters_path.touch()
        missing = resolver.check_missing(layout, Operation.PREPARE_CHAPTER)
        assert len(missing) == 2
        path_hints = {d.path_hint for d in missing}
        assert "outline.json" in path_hints
        assert "style_profile.json" in path_hints

    def test_is_ready_true(self, resolver: DependencyResolver, layout: ProjectLayout) -> None:
        layout.root.mkdir(parents=True, exist_ok=True)
        layout.bible_path.touch()
        layout.characters_path.touch()
        layout.outline_path.touch()
        layout.style_profile_path.touch()
        assert resolver.is_ready(layout, Operation.PREPARE_CHAPTER) is True

    def test_is_ready_false(self, resolver: DependencyResolver, layout: ProjectLayout) -> None:
        layout.root.mkdir(parents=True, exist_ok=True)
        layout.bible_path.touch()
        assert resolver.is_ready(layout, Operation.PREPARE_CHAPTER) is False

    def test_run_chapter_missing_chapter_raises(self, resolver: DependencyResolver, layout: ProjectLayout) -> None:
        layout.root.mkdir(parents=True, exist_ok=True)
        layout.canon_dir.mkdir(parents=True, exist_ok=True)
        (layout.canon_dir / "canon_current.json").touch()
        missing = resolver.check_missing(layout, Operation.RUN_CHAPTER, chapter_num=1)
        assert len(missing) == 2
        descriptions = {d.description for d in missing}
        assert "Chapter bridge contract from prepare_chapter" in descriptions
        assert "Chapter plan from prepare_chapter" in descriptions


class TestConvenienceFunctions:
    @pytest.fixture
    def layout(self, tmp_path: Path) -> ProjectLayout:
        return ProjectLayout(tmp_path)

    def test_check_init_long_empty(self, layout: ProjectLayout) -> None:
        result = check_init_long(layout)
        assert result == []

    def test_check_prepare_chapter_all_missing(self, layout: ProjectLayout) -> None:
        result = check_prepare_chapter(layout, 1)
        assert len(result) == 4

    def test_check_prepare_chapter_all_present(self, layout: ProjectLayout) -> None:
        layout.root.mkdir(parents=True, exist_ok=True)
        layout.bible_path.touch()
        layout.characters_path.touch()
        layout.outline_path.touch()
        layout.style_profile_path.touch()
        result = check_prepare_chapter(layout, 1)
        assert result == []

    def test_check_run_chapter(self, layout: ProjectLayout) -> None:
        result = check_run_chapter(layout, 1)
        assert len(result) == 3

    def test_check_repair(self, layout: ProjectLayout) -> None:
        result = check_repair(layout, 1)
        assert len(result) == 2
