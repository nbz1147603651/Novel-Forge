"""Tests for the project state machine."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from novel_forge.core.exceptions import StateError
from novel_forge.core.project_state import (
    ALLOWED_OPERATIONS,
    ProjectOperation,
    ProjectState,
    ProjectStateMachine,
    ProjectStateRecord,
)


class TestProjectStateEnum:
    def test_all_states_have_string_values(self):
        for state in ProjectState:
            assert isinstance(state.value, str)

    def test_state_count(self):
        assert len(ProjectState) == 8


class TestProjectOperationEnum:
    def test_all_operations_have_string_values(self):
        for op in ProjectOperation:
            assert isinstance(op.value, str)

    def test_operation_count(self):
        assert len(ProjectOperation) == 9


class TestProjectStateRecord:
    def test_to_dict(self):
        record = ProjectStateRecord(
            state=ProjectState.WRITING,
            project_id="test-project",
            transition_count=5,
            last_operation=ProjectOperation.PAUSE,
        )
        data = record.to_dict()
        assert data["state"] == "writing"
        assert data["project_id"] == "test-project"
        assert data["transition_count"] == 5
        assert data["last_operation"] == "pause"

    @pytest.mark.parametrize(
        "data,expected_state,expected_count,expected_op",
        [
            (
                {
                    "state": "completed",
                    "project_id": "my-project",
                    "updated_at": "2026-01-15T10:30:00+00:00",
                    "transition_count": 10,
                    "last_operation": "archive",
                },
                ProjectState.COMPLETED,
                10,
                ProjectOperation.ARCHIVE,
            ),
            (
                {
                    "state": "created",
                    "project_id": "new-project",
                    "updated_at": "2026-01-01T00:00:00+00:00",
                },
                ProjectState.CREATED,
                0,
                None,
            ),
        ],
    )
    def test_from_dict(self, data, expected_state, expected_count, expected_op):
        record = ProjectStateRecord.from_dict(data)
        assert record.state == expected_state
        assert record.project_id == data["project_id"]
        assert record.transition_count == expected_count
        assert record.last_operation == expected_op


class TestAllowedOperations:
    @pytest.mark.parametrize(
        "state,expected_ops",
        [
            (ProjectState.CREATED, {ProjectOperation.INIT, ProjectOperation.ARCHIVE}),
            (ProjectState.ARCHIVED, set()),
            (ProjectState.WRITING, {ProjectOperation.COMPLETE, ProjectOperation.PAUSE, ProjectOperation.ARCHIVE}),
        ],
    )
    def test_allowed_operations(self, state, expected_ops):
        ops = ALLOWED_OPERATIONS[state]
        assert set(ops) == expected_ops


class TestProjectStateMachine:
    def test_initial_state_is_created(self):
        machine = ProjectStateMachine(project_id="test")
        assert machine.state == ProjectState.CREATED

    def test_initial_state_respects_parameter(self):
        machine = ProjectStateMachine(
            project_id="test",
            initial_state=ProjectState.INITIALIZING,
        )
        assert machine.state == ProjectState.INITIALIZING

    @pytest.mark.parametrize(
        "initial_state,operation,can_execute",
        [
            (ProjectState.CREATED, ProjectOperation.INIT, True),
            (ProjectState.CREATED, ProjectOperation.COMPLETE, False),
            (ProjectState.CREATED, ProjectOperation.START_WRITING, False),
        ],
    )
    def test_can_execute(self, initial_state, operation, can_execute):
        machine = ProjectStateMachine(project_id="test", initial_state=initial_state)
        assert machine.can_execute(operation) is can_execute

    @pytest.mark.parametrize(
        "initial_state,target_state,can_transition",
        [
            (ProjectState.CREATED, ProjectState.INITIALIZING, True),
            (ProjectState.CREATED, ProjectState.WRITING, False),
        ],
    )
    def test_can_transition(self, initial_state, target_state, can_transition):
        machine = ProjectStateMachine(project_id="test", initial_state=initial_state)
        assert machine.can_transition(target_state) is can_transition

    def test_get_allowed_operations(self):
        machine = ProjectStateMachine(project_id="test")
        ops = machine.get_allowed_operations()
        assert ProjectOperation.INIT in ops
        assert ProjectOperation.ARCHIVE in ops

    def test_get_allowed_transitions(self):
        machine = ProjectStateMachine(project_id="test")
        transitions = machine.get_allowed_transitions()
        assert ProjectState.INITIALIZING in transitions
        assert ProjectState.ARCHIVED in transitions

    def test_transition_updates_state(self):
        machine = ProjectStateMachine(project_id="test")
        machine.transition(ProjectState.INITIALIZING, ProjectOperation.INIT)
        assert machine.state == ProjectState.INITIALIZING

    def test_transition_increments_count(self):
        machine = ProjectStateMachine(project_id="test")
        assert machine.record.transition_count == 0
        machine.transition(ProjectState.INITIALIZING, ProjectOperation.INIT)
        assert machine.record.transition_count == 1
        machine.transition(ProjectState.OUTLINE_READY, ProjectOperation.COMPLETE_INIT)
        assert machine.record.transition_count == 2

    def test_transition_records_operation(self):
        machine = ProjectStateMachine(project_id="test")
        machine.transition(ProjectState.INITIALIZING, ProjectOperation.INIT)
        assert machine.record.last_operation == ProjectOperation.INIT

    def test_transition_raises_on_invalid(self):
        machine = ProjectStateMachine(project_id="test")
        with pytest.raises(StateError) as exc_info:
            machine.transition(ProjectState.WRITING, ProjectOperation.START_WRITING)
        assert "project" in exc_info.value.state_name
        assert "writing" in exc_info.value.actual

    @pytest.mark.parametrize(
        "initial_state,operation,expected_state",
        [
            (ProjectState.CREATED, ProjectOperation.INIT, ProjectState.INITIALIZING),
            (ProjectState.INITIALIZING, ProjectOperation.COMPLETE_INIT, ProjectState.OUTLINE_READY),
            (ProjectState.INITIALIZING, ProjectOperation.FAIL_INIT, ProjectState.INIT_FAILED),
        ],
    )
    def test_execute_valid_operations(self, initial_state, operation, expected_state):
        machine = ProjectStateMachine(project_id="test", initial_state=initial_state)
        machine.execute(operation)
        assert machine.state == expected_state

    def test_execute_raises_on_invalid_operation(self):
        machine = ProjectStateMachine(project_id="test")
        with pytest.raises(StateError):
            machine.execute(ProjectOperation.COMPLETE)

    @pytest.mark.parametrize(
        "initial_state,is_terminal",
        [
            (ProjectState.ARCHIVED, True),
            (ProjectState.CREATED, False),
        ],
    )
    def test_is_terminal(self, initial_state, is_terminal):
        machine = ProjectStateMachine(project_id="test", initial_state=initial_state)
        assert machine.is_terminal() is is_terminal

    def test_repr(self):
        machine = ProjectStateMachine(project_id="my-project")
        r = repr(machine)
        assert "my-project" in r
        assert "created" in r


class TestProjectStateMachinePersistence:
    def test_persistence_to_disk(self, tmp_path: Path):
        machine = ProjectStateMachine(
            project_id="test-project",
            project_dir=tmp_path / "projects" / "test-project",
        )
        machine.execute(ProjectOperation.INIT)
        machine.execute(ProjectOperation.COMPLETE_INIT)

        state_file = tmp_path / "projects" / "test-project" / ".state.json"
        assert state_file.exists()

        data = json.loads(state_file.read_text(encoding="utf-8"))
        assert data["state"] == "outline_ready"
        assert data["project_id"] == "test-project"
        assert data["transition_count"] == 2

    def test_load_from_disk(self, tmp_path: Path):
        project_dir = tmp_path / "projects" / "test-project"
        project_dir.mkdir(parents=True)

        state_file = project_dir / ".state.json"
        state_file.write_text(
            json.dumps(
                {
                    "state": "writing",
                    "project_id": "test-project",
                    "updated_at": "2026-01-15T10:30:00+00:00",
                    "transition_count": 3,
                    "last_operation": "start_writing",
                }
            ),
            encoding="utf-8",
        )

        machine = ProjectStateMachine.load_from_disk("test-project", project_dir)
        assert machine.state == ProjectState.WRITING
        assert machine.record.transition_count == 3
        assert machine.record.last_operation == ProjectOperation.START_WRITING

    def test_load_from_disk_raises_on_missing_file(self, tmp_path: Path):
        with pytest.raises(FileNotFoundError):
            ProjectStateMachine.load_from_disk("nonexistent", tmp_path)


class TestStateTransitionMatrix:
    """Verify all valid transitions according to the spec."""

    @pytest.mark.parametrize(
        "state,expected_ops",
        [
            (ProjectState.CREATED, {ProjectOperation.INIT, ProjectOperation.ARCHIVE}),
            (ProjectState.INITIALIZING, {ProjectOperation.COMPLETE_INIT, ProjectOperation.FAIL_INIT, ProjectOperation.ARCHIVE}),
            (ProjectState.OUTLINE_READY, {ProjectOperation.START_WRITING, ProjectOperation.PAUSE, ProjectOperation.ARCHIVE}),
            (ProjectState.WRITING, {ProjectOperation.COMPLETE, ProjectOperation.PAUSE, ProjectOperation.ARCHIVE}),
            (ProjectState.COMPLETED, {ProjectOperation.ARCHIVE, ProjectOperation.RESUME}),
            (ProjectState.PAUSED, {ProjectOperation.RESUME, ProjectOperation.ARCHIVE}),
            (ProjectState.INIT_FAILED, {ProjectOperation.RETRY, ProjectOperation.ARCHIVE}),
            (ProjectState.ARCHIVED, set()),
        ],
    )
    def test_state_transitions(self, state, expected_ops):
        allowed = ALLOWED_OPERATIONS[state]
        assert set(allowed) == expected_ops


class TestFullProjectLifecycle:
    """Test a complete project lifecycle through all states."""

    def test_happy_path(self, tmp_path: Path):
        machine = ProjectStateMachine(
            project_id="novel-1",
            project_dir=tmp_path / "novel-1",
        )

        machine.execute(ProjectOperation.INIT)
        assert machine.state == ProjectState.INITIALIZING

        machine.execute(ProjectOperation.COMPLETE_INIT)
        assert machine.state == ProjectState.OUTLINE_READY

        machine.execute(ProjectOperation.START_WRITING)
        assert machine.state == ProjectState.WRITING

        machine.execute(ProjectOperation.COMPLETE)
        assert machine.state == ProjectState.COMPLETED

        machine.execute(ProjectOperation.ARCHIVE)
        assert machine.state == ProjectState.ARCHIVED
        assert machine.is_terminal() is True

    def test_pause_and_resume(self, tmp_path: Path):
        machine = ProjectStateMachine(
            project_id="novel-2",
            project_dir=tmp_path / "novel-2",
        )

        machine.execute(ProjectOperation.INIT)
        machine.execute(ProjectOperation.COMPLETE_INIT)
        machine.execute(ProjectOperation.START_WRITING)

        machine.execute(ProjectOperation.PAUSE)
        assert machine.state == ProjectState.PAUSED

        machine.execute(ProjectOperation.RESUME)
        assert machine.state == ProjectState.WRITING

    def test_init_failure_and_retry(self, tmp_path: Path):
        machine = ProjectStateMachine(
            project_id="novel-3",
            project_dir=tmp_path / "novel-3",
        )

        machine.execute(ProjectOperation.INIT)

        machine.execute(ProjectOperation.FAIL_INIT)
        assert machine.state == ProjectState.INIT_FAILED

        machine.execute(ProjectOperation.RETRY)
        assert machine.state == ProjectState.INITIALIZING

    def test_resume_from_completed(self, tmp_path: Path):
        machine = ProjectStateMachine(
            project_id="novel-4",
            project_dir=tmp_path / "novel-4",
        )

        machine.execute(ProjectOperation.INIT)
        machine.execute(ProjectOperation.COMPLETE_INIT)
        machine.execute(ProjectOperation.START_WRITING)
        machine.execute(ProjectOperation.COMPLETE)

        machine.execute(ProjectOperation.RESUME)
        assert machine.state == ProjectState.WRITING
