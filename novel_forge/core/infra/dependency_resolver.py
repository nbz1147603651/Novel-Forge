"""Dependency resolver for workspace operations.

Defines file and state dependencies for init_long, prepare_chapter,
run_chapter, and repair operations.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

    from novel_forge.persistence.models import ProjectLayout


class Operation(str, Enum):
    """Workspace operations that have dependencies."""

    INIT_LONG = "init_long"
    PREPARE_CHAPTER = "prepare_chapter"
    RUN_CHAPTER = "run_chapter"
    REPAIR = "repair"


class DependencyKind(str, Enum):
    """Kinds of dependencies an operation may have."""

    FILE = "file"  # A specific file must exist
    DIRECTORY = "directory"  # A directory must exist
    CHAPTER_FILE = "chapter_file"  # A chapter-specific file must exist
    PROJECT_STATE = "project_state"  # Project-level state file(s) must exist
    CANON_STATE = "canon_state"  # Canon state must be initialized
    PREVIOUS_CHAPTER = "previous_chapter"  # Previous chapter must be complete


@dataclass(frozen=True)
class Dependency:
    """A single dependency requirement for an operation.

    Attributes:
        kind: The type of dependency.
        path_hint: A relative path or path template. For CHAPTER_FILE dependencies,
            this may contain '{chapter_num}' which will be substituted.
        description: Human-readable description of why this dependency is needed.
    """

    kind: DependencyKind
    path_hint: str
    description: str

    def resolve_path(self, layout: ProjectLayout, chapter_num: int | None = None) -> Path:
        """Resolve the dependency path for a given layout and optional chapter number.

        Args:
            layout: The project layout to resolve paths against.
            chapter_num: Chapter number for chapter-specific dependencies.

        Returns:
            The resolved absolute Path.
        """
        path_str = self.path_hint

        if self.kind == DependencyKind.CHAPTER_FILE:
            if "{chapter_num}" not in path_str:
                raise ValueError(
                    f"CHAPTER_FILE dependency '{self.path_hint}' must contain '{{chapter_num}}'"
                )
            if chapter_num is None:
                raise ValueError(
                    f"CHAPTER_FILE dependency '{self.path_hint}' requires chapter_num"
                )
            return layout.root / path_str.format(chapter_num=chapter_num)
        elif self.kind == DependencyKind.PREVIOUS_CHAPTER:
            if chapter_num is None:
                raise ValueError(
                    f"PREVIOUS_CHAPTER dependency '{self.path_hint}' requires chapter_num"
                )
            return layout.root / path_str.format(chapter_num=chapter_num)
        elif self.kind == DependencyKind.FILE or self.kind == DependencyKind.DIRECTORY:
            return layout.root / path_str
        elif self.kind == DependencyKind.PROJECT_STATE:
            return layout.root / path_str
        elif self.kind == DependencyKind.CANON_STATE:
            return layout.canon_dir / path_str
        else:
            return layout.root / path_str


# ── Operation Dependency Mappings ──────────────────────────────────────────────

# Dependencies for init_long: no pre-existing project files required.
# This operation creates the project from scratch.
INIT_LONG_DEPENDENCIES: list[Dependency] = []

# Dependencies for prepare_chapter: needs project foundation files.
PREPARE_CHAPTER_DEPENDENCIES: list[Dependency] = [
    Dependency(
        kind=DependencyKind.PROJECT_STATE,
        path_hint="story_bible.json",
        description="Story bible with world-building and plot foundation",
    ),
    Dependency(
        kind=DependencyKind.PROJECT_STATE,
        path_hint="character_bible.json",
        description="Character bible with character definitions",
    ),
    Dependency(
        kind=DependencyKind.PROJECT_STATE,
        path_hint="outline.json",
        description="Story outline with chapter structure",
    ),
    Dependency(
        kind=DependencyKind.FILE,
        path_hint="style_profile.json",
        description="Writing style profile (optional but recommended)",
    ),
]

# Dependencies for run_chapter: needs chapter preparation outputs.
RUN_CHAPTER_DEPENDENCIES: list[Dependency] = [
    Dependency(
        kind=DependencyKind.CHAPTER_FILE,
        path_hint="plans/chapter_{chapter_num}_bridge.json",
        description="Chapter bridge contract from prepare_chapter",
    ),
    Dependency(
        kind=DependencyKind.CHAPTER_FILE,
        path_hint="plans/chapter_{chapter_num}_plan.json",
        description="Chapter plan from prepare_chapter",
    ),
    Dependency(
        kind=DependencyKind.CANON_STATE,
        path_hint="canon_current.json",
        description="Canon state with character states and relationships",
    ),
]

# Dependencies for repair: needs chapter to be at least drafted.
REPAIR_DEPENDENCIES: list[Dependency] = [
    Dependency(
        kind=DependencyKind.CHAPTER_FILE,
        path_hint="chapters/chapter_{chapter_num}.md",
        description="Chapter text file to repair",
    ),
    Dependency(
        kind=DependencyKind.CANON_STATE,
        path_hint="canon_current.json",
        description="Canon state for consistency validation",
    ),
]


OPERATION_DEPENDENCIES: dict[Operation, list[Dependency]] = {
    Operation.INIT_LONG: INIT_LONG_DEPENDENCIES,
    Operation.PREPARE_CHAPTER: PREPARE_CHAPTER_DEPENDENCIES,
    Operation.RUN_CHAPTER: RUN_CHAPTER_DEPENDENCIES,
    Operation.REPAIR: REPAIR_DEPENDENCIES,
}


# ── DependencyResolver ─────────────────────────────────────────────────────────


@dataclass
class DependencyResolver:
    """Resolves and validates dependencies for workspace operations.

    Example:
        >>> from novel_forge.persistence.models import ProjectLayout
        >>> from pathlib import Path
        >>> layout = ProjectLayout(Path("/data/my_project"))
        >>> resolver = DependencyResolver()
        >>> missing = resolver.check_missing(layout, Operation.PREPARE_CHAPTER, chapter_num=1)
        >>> print(missing)  # List of missing Dependency objects
        []
    """

    def resolve(
        self,
        layout: ProjectLayout,
        operation: Operation,
        *,
        chapter_num: int | None = None,
    ) -> list[Dependency]:
        """Return the list of dependencies for the given operation.

        Args:
            layout: The project layout to resolve against.
            operation: The operation to get dependencies for.
            chapter_num: Chapter number if the operation is chapter-scoped.

        Returns:
            List of Dependency objects for the operation.
        """
        return list(OPERATION_DEPENDENCIES.get(operation, []))

    def check_missing(
        self,
        layout: ProjectLayout,
        operation: Operation,
        *,
        chapter_num: int | None = None,
    ) -> list[Dependency]:
        """Return dependencies that are not satisfied (files do not exist).

        Args:
            layout: The project layout to check against.
            operation: The operation to check dependencies for.
            chapter_num: Chapter number if the operation is chapter-scoped.

        Returns:
            List of missing Dependency objects.
        """
        missing: list[Dependency] = []
        for dep in self.resolve(layout, operation, chapter_num=chapter_num):
            path = dep.resolve_path(layout, chapter_num)
            if not path.exists():
                missing.append(dep)
        return missing

    def is_ready(
        self,
        layout: ProjectLayout,
        operation: Operation,
        *,
        chapter_num: int | None = None,
    ) -> bool:
        """Return True if all dependencies for the operation are satisfied.

        Args:
            layout: The project layout to check against.
            operation: The operation to check readiness for.
            chapter_num: Chapter number if the operation is chapter-scoped.

        Returns:
            True if no dependencies are missing, False otherwise.
        """
        return len(self.check_missing(layout, operation, chapter_num=chapter_num)) == 0

    # ── Convenience Check Functions ───────────────────────────────────────────

    def check_init_long(self, layout: ProjectLayout) -> list[Dependency]:
        """Check dependencies for init_long operation.

        init_long has no pre-existing dependencies since it creates a new project.
        Returns an empty list.

        Args:
            layout: The project layout to check against.

        Returns:
            Empty list (init_long has no prerequisites).
        """
        return self.check_missing(layout, Operation.INIT_LONG)

    def check_prepare_chapter(
        self, layout: ProjectLayout, chapter_num: int
    ) -> list[Dependency]:
        """Check dependencies for prepare_chapter operation.

        Args:
            layout: The project layout to check against.
            chapter_num: The chapter number to prepare.

        Returns:
            List of missing dependencies if any.
        """
        return self.check_missing(layout, Operation.PREPARE_CHAPTER, chapter_num=chapter_num)

    def check_run_chapter(
        self, layout: ProjectLayout, chapter_num: int
    ) -> list[Dependency]:
        """Check dependencies for run_chapter operation.

        Args:
            layout: The project layout to check against.
            chapter_num: The chapter number to run.

        Returns:
            List of missing dependencies if any.
        """
        return self.check_missing(layout, Operation.RUN_CHAPTER, chapter_num=chapter_num)

    def check_repair(self, layout: ProjectLayout, chapter_num: int) -> list[Dependency]:
        """Check dependencies for repair operation.

        Args:
            layout: The project layout to check against.
            chapter_num: The chapter number to repair.

        Returns:
            List of missing dependencies if any.
        """
        return self.check_missing(layout, Operation.REPAIR, chapter_num=chapter_num)


# ── Module-Level Convenience Functions ─────────────────────────────────────────


def check_init_long(layout: ProjectLayout) -> list[Dependency]:
    """Check dependencies for init_long operation.

    init_long has no pre-existing dependencies since it creates a new project.
    Returns an empty list.

    Args:
        layout: The project layout to check against.

    Returns:
        Empty list (init_long has no prerequisites).
    """
    return DependencyResolver().check_missing(layout, Operation.INIT_LONG)


def check_prepare_chapter(layout: ProjectLayout, chapter_num: int) -> list[Dependency]:
    """Check dependencies for prepare_chapter operation.

    Args:
        layout: The project layout to check against.
        chapter_num: The chapter number to prepare.

    Returns:
        List of missing dependencies if any.
    """
    return DependencyResolver().check_missing(layout, Operation.PREPARE_CHAPTER, chapter_num=chapter_num)


def check_run_chapter(layout: ProjectLayout, chapter_num: int) -> list[Dependency]:
    """Check dependencies for run_chapter operation.

    Args:
        layout: The project layout to check against.
        chapter_num: The chapter number to run.

    Returns:
        List of missing dependencies if any.
    """
    return DependencyResolver().check_missing(layout, Operation.RUN_CHAPTER, chapter_num=chapter_num)


def check_repair(layout: ProjectLayout, chapter_num: int) -> list[Dependency]:
    """Check dependencies for repair operation.

    Args:
        layout: The project layout to check against.
        chapter_num: The chapter number to repair.

    Returns:
        List of missing dependencies if any.
    """
    return DependencyResolver().check_missing(layout, Operation.REPAIR, chapter_num=chapter_num)
