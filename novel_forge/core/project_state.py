"""Project state machine for Novel Forge projects.

This module provides:
- ProjectState: Enum defining all valid project lifecycle states
- ProjectStateRecord: Dataclass for persisting project state to disk
- ProjectStateMachine: State machine with transition validation
- ALLOWED_OPERATIONS: Matrix of operations permitted in each state
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

from novel_forge.core.exceptions import StateError

_logger = logging.getLogger(__name__)


class ProjectState(str, Enum):
    """Valid states in the project lifecycle."""

    CREATED = "created"
    INITIALIZING = "initializing"
    OUTLINE_READY = "outline_ready"
    WRITING = "writing"
    COMPLETED = "completed"
    PAUSED = "paused"
    ARCHIVED = "archived"
    INIT_FAILED = "init_failed"


# Operations that can be performed on a project
class ProjectOperation(str, Enum):
    """Valid operations that can trigger state transitions."""

    INIT = "init"
    COMPLETE_INIT = "complete_init"
    FAIL_INIT = "fail_init"
    START_WRITING = "start_writing"
    COMPLETE = "complete"
    PAUSE = "pause"
    RESUME = "resume"
    ARCHIVE = "archive"
    RETRY = "retry"


# State transition matrix: from_state -> set of valid target states
_ALLOWED_TRANSITIONS: dict[ProjectState, set[ProjectState]] = {
    ProjectState.CREATED: {ProjectState.INITIALIZING, ProjectState.ARCHIVED},
    ProjectState.INITIALIZING: {ProjectState.OUTLINE_READY, ProjectState.INIT_FAILED},
    ProjectState.OUTLINE_READY: {ProjectState.WRITING, ProjectState.PAUSED, ProjectState.ARCHIVED},
    ProjectState.WRITING: {ProjectState.COMPLETED, ProjectState.PAUSED, ProjectState.ARCHIVED},
    ProjectState.COMPLETED: {ProjectState.ARCHIVED, ProjectState.WRITING},
    ProjectState.PAUSED: {ProjectState.WRITING, ProjectState.ARCHIVED},
    ProjectState.ARCHIVED: set(),  # Terminal state - no transitions out
    ProjectState.INIT_FAILED: {ProjectState.INITIALIZING, ProjectState.ARCHIVED},
}

# Operation validity matrix: state -> set of allowed operations
ALLOWED_OPERATIONS: dict[ProjectState, set[ProjectOperation]] = {
    ProjectState.CREATED: {ProjectOperation.INIT, ProjectOperation.ARCHIVE},
    ProjectState.INITIALIZING: {
        ProjectOperation.COMPLETE_INIT,
        ProjectOperation.FAIL_INIT,
        ProjectOperation.ARCHIVE,
    },
    ProjectState.OUTLINE_READY: {
        ProjectOperation.START_WRITING,
        ProjectOperation.PAUSE,
        ProjectOperation.ARCHIVE,
    },
    ProjectState.WRITING: {
        ProjectOperation.COMPLETE,
        ProjectOperation.PAUSE,
        ProjectOperation.ARCHIVE,
    },
    ProjectState.COMPLETED: {ProjectOperation.ARCHIVE, ProjectOperation.RESUME},
    ProjectState.PAUSED: {ProjectOperation.RESUME, ProjectOperation.ARCHIVE},
    ProjectState.ARCHIVED: set(),  # No operations allowed on archived projects
    ProjectState.INIT_FAILED: {ProjectOperation.RETRY, ProjectOperation.ARCHIVE},
}


@dataclass(frozen=True)
class ProjectStateRecord:
    """Immutable record of project state for persistence."""

    state: ProjectState
    project_id: str
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    transition_count: int = 0
    last_operation: ProjectOperation | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dictionary for JSON persistence."""
        return {
            "state": self.state.value,
            "project_id": self.project_id,
            "updated_at": self.updated_at.isoformat(),
            "transition_count": self.transition_count,
            "last_operation": self.last_operation.value if self.last_operation else None,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ProjectStateRecord:
        """Deserialize from dictionary."""
        return cls(
            state=ProjectState(data["state"]),
            project_id=data["project_id"],
            updated_at=datetime.fromisoformat(data["updated_at"]),
            transition_count=data.get("transition_count", 0),
            last_operation=(
                ProjectOperation(data["last_operation"]) if data.get("last_operation") else None
            ),
        )


class ProjectStateMachine:
    """State machine for managing project lifecycle transitions.

    Provides:
    - State transition validation
    - Operation permission checking
    - Persistence to .state.json file
    """

    STATE_FILE_NAME = ".state.json"

    def __init__(
        self,
        project_id: str,
        project_dir: Path | None = None,
        initial_state: ProjectState = ProjectState.CREATED,
    ) -> None:
        """Initialize the state machine.

        Args:
            project_id: Unique identifier for the project
            project_dir: Directory for state file persistence (optional)
            initial_state: Starting state (defaults to CREATED)
        """
        self._project_id = project_id
        self._project_dir = project_dir
        self._record = ProjectStateRecord(
            state=initial_state,
            project_id=project_id,
        )

    @property
    def state(self) -> ProjectState:
        """Current project state."""
        return self._record.state

    @property
    def project_id(self) -> str:
        """Project identifier."""
        return self._project_id

    @property
    def record(self) -> ProjectStateRecord:
        """Current state record."""
        return self._record

    def can_transition(self, target_state: ProjectState) -> bool:
        """Check if transition to target state is valid."""
        allowed = _ALLOWED_TRANSITIONS.get(self._record.state, set())
        return target_state in allowed

    def can_execute(self, operation: ProjectOperation) -> bool:
        """Check if operation is allowed in current state."""
        allowed = ALLOWED_OPERATIONS.get(self._record.state, set())
        return operation in allowed

    def get_allowed_operations(self) -> set[ProjectOperation]:
        """Return set of operations allowed in current state."""
        return ALLOWED_OPERATIONS.get(self._record.state, set()).copy()

    def get_allowed_transitions(self) -> set[ProjectState]:
        """Return set of states reachable from current state."""
        return _ALLOWED_TRANSITIONS.get(self._record.state, set()).copy()

    def transition(
        self,
        target_state: ProjectState,
        operation: ProjectOperation | None = None,
    ) -> ProjectStateRecord:
        """Transition to a new state.

        Args:
            target_state: The state to transition to
            operation: The operation triggering this transition

        Returns:
            Updated ProjectStateRecord

        Raises:
            StateError: If transition is not allowed
        """
        if not self.can_transition(target_state):
            raise StateError(
                state_name="project",
                expected=", ".join(sorted(s.value for s in self.get_allowed_transitions())),
                actual=target_state.value,
            )

        self._record = ProjectStateRecord(
            state=target_state,
            project_id=self._project_id,
            updated_at=datetime.now(timezone.utc),
            transition_count=self._record.transition_count + 1,
            last_operation=operation,
        )

        if self._project_dir is not None:
            self._persist()

        _logger.info(
            "Project %s transitioned from %s to %s (op: %s)",
            self._project_id,
            target_state.value,
            self._record.state.value,
            operation.value if operation else None,
        )

        return self._record

    def execute(self, operation: ProjectOperation) -> ProjectStateRecord:
        """Execute an operation and transition accordingly.

        Args:
            operation: The operation to execute

        Returns:
            Updated ProjectStateRecord

        Raises:
            StateError: If operation is not allowed
        """
        if not self.can_execute(operation):
            allowed = self.get_allowed_operations()
            raise StateError(
                state_name="project",
                expected=", ".join(sorted(o.value for o in allowed))
                if allowed
                else "no operations",
                actual=operation.value,
            )

        # Determine target state based on operation
        target_state = self._resolve_target_state(operation)
        return self.transition(target_state, operation)

    def _resolve_target_state(self, operation: ProjectOperation) -> ProjectState:
        """Resolve operation to target state."""
        mapping: dict[ProjectOperation, ProjectState] = {
            ProjectOperation.INIT: ProjectState.INITIALIZING,
            ProjectOperation.COMPLETE_INIT: ProjectState.OUTLINE_READY,
            ProjectOperation.FAIL_INIT: ProjectState.INIT_FAILED,
            ProjectOperation.START_WRITING: ProjectState.WRITING,
            ProjectOperation.COMPLETE: ProjectState.COMPLETED,
            ProjectOperation.PAUSE: ProjectState.PAUSED,
            ProjectOperation.RESUME: ProjectState.WRITING,
            ProjectOperation.ARCHIVE: ProjectState.ARCHIVED,
            ProjectOperation.RETRY: ProjectState.INITIALIZING,
        }
        return mapping[operation]

    def _persist(self) -> None:
        """Persist state to .state.json file."""
        if self._project_dir is None:
            return

        state_file = self._project_dir / self.STATE_FILE_NAME
        try:
            state_file.parent.mkdir(parents=True, exist_ok=True)
            # Atomic write using temp file
            temp_file = state_file.with_suffix(".tmp")
            temp_file.write_text(
                json.dumps(self._record.to_dict(), indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            temp_file.replace(state_file)
        except OSError as e:
            _logger.warning("Failed to persist project state: %s", e)

    @staticmethod
    def load_from_disk(project_id: str, project_dir: Path) -> ProjectStateMachine:
        """Load state machine from disk.

        Args:
            project_id: Unique identifier for the project
            project_dir: Directory containing the state file

        Returns:
            Loaded ProjectStateMachine instance

        Raises:
            FileNotFoundError: If state file does not exist
        """
        state_file = project_dir / ProjectStateMachine.STATE_FILE_NAME
        if not state_file.exists():
            raise FileNotFoundError(f"State file not found: {state_file}")

        data = json.loads(state_file.read_text(encoding="utf-8"))
        record = ProjectStateRecord.from_dict(data)

        machine = ProjectStateMachine.__new__(ProjectStateMachine)
        machine._project_id = project_id
        machine._project_dir = project_dir
        machine._record = record
        return machine

    def is_terminal(self) -> bool:
        """Check if current state is a terminal state."""
        return len(self.get_allowed_transitions()) == 0

    def __repr__(self) -> str:
        return (
            f"ProjectStateMachine(project_id={self._project_id!r}, "
            f"state={self._record.state.value!r})"
        )


__all__ = [
    "ProjectState",
    "ProjectOperation",
    "ProjectStateRecord",
    "ProjectStateMachine",
    "ALLOWED_OPERATIONS",
]
