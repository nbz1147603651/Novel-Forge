"""Tests for gateway.task_circuit_breaker."""

from __future__ import annotations

import time

import pytest

from novel_forge.common.constants import TaskType
from novel_forge.core.exceptions import TaskCircuitOpenError
from novel_forge.gateway.task_circuit_breaker import (
    TaskCircuitState,
    TaskTypeCircuitBreaker,
)


@pytest.fixture()
def breaker() -> TaskTypeCircuitBreaker:
    return TaskTypeCircuitBreaker(
        threshold=3,
        cooldown_seconds=60.0,
    )


@pytest.fixture()
def fast_cooldown_breaker() -> TaskTypeCircuitBreaker:
    return TaskTypeCircuitBreaker(
        threshold=3,
        cooldown_seconds=0.01,
    )


class TestInitialState:
    def test_initial_state_closed(self, breaker: TaskTypeCircuitBreaker) -> None:
        assert breaker.get_state(TaskType.DRAFT_CHAPTER) == TaskCircuitState.CLOSED
        assert breaker.get_failure_count(TaskType.DRAFT_CHAPTER) == 0


class TestClosedToOpen:
    def test_3_failures_open_circuit(self, breaker: TaskTypeCircuitBreaker) -> None:
        t = TaskType.REPAIR_INIT_ARTIFACT_PATCH
        assert breaker.get_state(t) == TaskCircuitState.CLOSED

        breaker.record_failure(t)
        assert breaker.get_state(t) == TaskCircuitState.CLOSED
        assert breaker.get_failure_count(t) == 1

        breaker.record_failure(t)
        assert breaker.get_state(t) == TaskCircuitState.CLOSED
        assert breaker.get_failure_count(t) == 2

        breaker.record_failure(t)
        assert breaker.get_state(t) == TaskCircuitState.OPEN
        assert breaker.get_failure_count(t) == 3

    def test_1_failure_does_not_open(self, breaker: TaskTypeCircuitBreaker) -> None:
        t = TaskType.DRAFT_CHAPTER
        breaker.record_failure(t)
        assert breaker.get_state(t) == TaskCircuitState.CLOSED

    def test_2_failures_does_not_open(self, breaker: TaskTypeCircuitBreaker) -> None:
        t = TaskType.DRAFT_CHAPTER
        breaker.record_failure(t)
        breaker.record_failure(t)
        assert breaker.get_state(t) == TaskCircuitState.CLOSED

    def test_closed_stays_closed_on_success(self, breaker: TaskTypeCircuitBreaker) -> None:
        t = TaskType.DRAFT_CHAPTER
        breaker.record_failure(t)
        breaker.record_failure(t)
        assert breaker.get_failure_count(t) == 2
        breaker.record_success(t)
        assert breaker.get_state(t) == TaskCircuitState.CLOSED
        assert breaker.get_failure_count(t) == 0


class TestHalfOpenTransitions:
    def test_half_open_after_cooldown(self, fast_cooldown_breaker: TaskTypeCircuitBreaker) -> None:
        t = TaskType.REPAIR_INIT_ARTIFACT_PATCH
        # 3 failures → OPEN
        fast_cooldown_breaker.record_failure(t)
        fast_cooldown_breaker.record_failure(t)
        fast_cooldown_breaker.record_failure(t)
        assert fast_cooldown_breaker.get_state(t) == TaskCircuitState.OPEN

        # Wait for cooldown → HALF_OPEN
        time.sleep(0.02)
        assert fast_cooldown_breaker.get_state(t) == TaskCircuitState.HALF_OPEN

    def test_half_open_success_closes(self, fast_cooldown_breaker: TaskTypeCircuitBreaker) -> None:
        t = TaskType.REPAIR_INIT_ARTIFACT_PATCH
        fast_cooldown_breaker.record_failure(t)
        fast_cooldown_breaker.record_failure(t)
        fast_cooldown_breaker.record_failure(t)
        assert fast_cooldown_breaker.get_state(t) == TaskCircuitState.OPEN

        time.sleep(0.02)
        assert fast_cooldown_breaker.get_state(t) == TaskCircuitState.HALF_OPEN

        # Success → CLOSED
        fast_cooldown_breaker.record_success(t)
        assert fast_cooldown_breaker.get_state(t) == TaskCircuitState.CLOSED
        assert fast_cooldown_breaker.get_failure_count(t) == 0

    def test_half_open_failure_reopens(self, fast_cooldown_breaker: TaskTypeCircuitBreaker) -> None:
        t = TaskType.REPAIR_INIT_ARTIFACT_PATCH
        fast_cooldown_breaker.record_failure(t)
        fast_cooldown_breaker.record_failure(t)
        fast_cooldown_breaker.record_failure(t)
        assert fast_cooldown_breaker.get_state(t) == TaskCircuitState.OPEN

        time.sleep(0.02)
        assert fast_cooldown_breaker.get_state(t) == TaskCircuitState.HALF_OPEN

        # Failure in HALF_OPEN → back to OPEN
        fast_cooldown_breaker.record_failure(t)
        assert fast_cooldown_breaker.get_state(t) == TaskCircuitState.OPEN
        assert fast_cooldown_breaker.get_failure_count(t) == 3


class TestCheckRaisesWhenOpen:
    def test_check_raises_when_open(self, breaker: TaskTypeCircuitBreaker) -> None:
        t = TaskType.REPAIR_INIT_ARTIFACT_PATCH
        breaker.record_failure(t)
        breaker.record_failure(t)
        breaker.record_failure(t)
        assert breaker.get_state(t) == TaskCircuitState.OPEN

        with pytest.raises(TaskCircuitOpenError) as exc_info:
            breaker.check(t)
        assert exc_info.value.task_type == t
        assert exc_info.value.retry_after_s > 0

    def test_check_passes_when_closed(self, breaker: TaskTypeCircuitBreaker) -> None:
        t = TaskType.DRAFT_CHAPTER
        breaker.check(t)  # should not raise

    def test_check_passes_when_half_open(self, fast_cooldown_breaker: TaskTypeCircuitBreaker) -> None:
        t = TaskType.REPAIR_INIT_ARTIFACT_PATCH
        fast_cooldown_breaker.record_failure(t)
        fast_cooldown_breaker.record_failure(t)
        fast_cooldown_breaker.record_failure(t)
        time.sleep(0.02)
        assert fast_cooldown_breaker.get_state(t) == TaskCircuitState.HALF_OPEN
        fast_cooldown_breaker.check(t)  # should not raise


class TestIndependentPerTaskType:
    def test_independent_per_task_type(self, breaker: TaskTypeCircuitBreaker) -> None:
        task_a = TaskType.REPAIR_INIT_ARTIFACT_PATCH
        task_b = TaskType.DRAFT_CHAPTER

        # Fail task_a 3 times → OPEN
        breaker.record_failure(task_a)
        breaker.record_failure(task_a)
        breaker.record_failure(task_a)
        assert breaker.get_state(task_a) == TaskCircuitState.OPEN

        # Task_b should still be CLOSED
        assert breaker.get_state(task_b) == TaskCircuitState.CLOSED
        assert breaker.get_failure_count(task_b) == 0

        # check() should raise for task_a but not task_b
        with pytest.raises(TaskCircuitOpenError):
            breaker.check(task_a)
        breaker.check(task_b)  # should not raise

        # Record success on task_b should work normally
        breaker.record_failure(task_b)
        breaker.record_success(task_b)
        assert breaker.get_state(task_b) == TaskCircuitState.CLOSED
        assert breaker.get_failure_count(task_b) == 0

    def test_failure_on_one_does_not_affect_another(self, breaker: TaskTypeCircuitBreaker) -> None:
        task_a = TaskType.REPAIR_INIT_ARTIFACT_PATCH
        task_b = TaskType.EDIT_CHAPTER

        breaker.record_failure(task_a)
        breaker.record_failure(task_a)
        assert breaker.get_failure_count(task_a) == 2
        assert breaker.get_failure_count(task_b) == 0


class TestReset:
    def test_reset_single_task(self, breaker: TaskTypeCircuitBreaker) -> None:
        t = TaskType.REPAIR_INIT_ARTIFACT_PATCH
        breaker.record_failure(t)
        breaker.record_failure(t)
        breaker.record_failure(t)
        assert breaker.get_state(t) == TaskCircuitState.OPEN

        breaker.reset(t)
        assert breaker.get_state(t) == TaskCircuitState.CLOSED
        assert breaker.get_failure_count(t) == 0

    def test_reset_all(self, breaker: TaskTypeCircuitBreaker) -> None:
        t1 = TaskType.REPAIR_INIT_ARTIFACT_PATCH
        t2 = TaskType.DRAFT_CHAPTER
        breaker.record_failure(t1)
        breaker.record_failure(t1)
        breaker.record_failure(t1)
        breaker.record_failure(t2)
        assert breaker.get_state(t1) == TaskCircuitState.OPEN

        breaker.reset()
        assert breaker.get_state(t1) == TaskCircuitState.CLOSED
        assert breaker.get_failure_count(t1) == 0
        assert breaker.get_state(t2) == TaskCircuitState.CLOSED
        assert breaker.get_failure_count(t2) == 0


class TestTimeUntilHalfOpen:
    def test_time_until_half_open_when_open(self, breaker: TaskTypeCircuitBreaker) -> None:
        t = TaskType.REPAIR_INIT_ARTIFACT_PATCH
        breaker.record_failure(t)
        breaker.record_failure(t)
        breaker.record_failure(t)
        assert breaker.get_state(t) == TaskCircuitState.OPEN
        remaining = breaker.time_until_half_open(t)
        assert remaining is not None
        assert remaining > 0

    def test_time_until_half_open_none_when_closed(self, breaker: TaskTypeCircuitBreaker) -> None:
        t = TaskType.DRAFT_CHAPTER
        assert breaker.time_until_half_open(t) is None

    def test_time_until_half_open_none_when_half_open(
        self, fast_cooldown_breaker: TaskTypeCircuitBreaker
    ) -> None:
        t = TaskType.REPAIR_INIT_ARTIFACT_PATCH
        fast_cooldown_breaker.record_failure(t)
        fast_cooldown_breaker.record_failure(t)
        fast_cooldown_breaker.record_failure(t)
        time.sleep(0.02)
        assert fast_cooldown_breaker.get_state(t) == TaskCircuitState.HALF_OPEN
        assert fast_cooldown_breaker.time_until_half_open(t) is None


class TestDisabled:
    def test_disabled_breaker_always_passes_check(self) -> None:
        disabled = TaskTypeCircuitBreaker(threshold=3, cooldown_seconds=60.0, enabled=False)
        t = TaskType.REPAIR_INIT_ARTIFACT_PATCH
        disabled.record_failure(t)
        disabled.record_failure(t)
        disabled.record_failure(t)
        disabled.check(t)  # should not raise

    def test_disabled_breaker_does_not_track_failures(self) -> None:
        disabled = TaskTypeCircuitBreaker(threshold=3, cooldown_seconds=60.0, enabled=False)
        t = TaskType.REPAIR_INIT_ARTIFACT_PATCH
        disabled.record_failure(t)
        disabled.record_failure(t)
        disabled.record_failure(t)
        assert disabled.get_failure_count(t) == 0
        assert disabled.get_state(t) == TaskCircuitState.CLOSED


class TestTaskCircuitOpenError:
    def test_error_attributes(self) -> None:
        t = TaskType.REPAIR_INIT_ARTIFACT_PATCH
        err = TaskCircuitOpenError(t, retry_after_s=45.0)
        assert err.task_type == t
        assert err.retry_after_s == 45.0
        assert "repair_init_artifact_patch" in str(err)
        assert "45.0" in str(err)
        assert err.is_transient_error is False
        assert err.error_code == "task_circuit_open"
