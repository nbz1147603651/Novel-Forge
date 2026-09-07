"""Tests for CapacityScheduler - priority-based admission control."""

from __future__ import annotations

import pytest

from novel_forge.control_plane.capacity import (
    CapacityScheduler,
    get_global_capacity_scheduler,
)
from novel_forge.control_plane.enums import AssuranceLevel, Priority
from novel_forge.control_plane.health import RoutingHealthRegistry


@pytest.fixture
def registry() -> RoutingHealthRegistry:
    return RoutingHealthRegistry()


@pytest.fixture
def scheduler(registry: RoutingHealthRegistry) -> CapacityScheduler:
    return CapacityScheduler(registry)


# ---------------------------------------------------------------------------
# can_admit
# ---------------------------------------------------------------------------


def test_can_admit_delegates_to_registry(
    registry: RoutingHealthRegistry, scheduler: CapacityScheduler
) -> None:
    registry.record_success("openai", "DRAFT_CHAPTER")
    decision = scheduler.can_admit(Priority.P1, "DRAFT_CHAPTER")
    assert decision.admitted


# ---------------------------------------------------------------------------
# should_degrade_commit
# ---------------------------------------------------------------------------


def test_should_degrade_commit_allowed() -> None:
    scheduler = CapacityScheduler(RoutingHealthRegistry())
    assert scheduler.should_degrade_commit("resolve_chapter_checkpoint_finalize")
    assert scheduler.should_degrade_commit("export_book")
    assert scheduler.should_degrade_commit("rebuild_memory_vectors")


def test_should_degrade_commit_not_allowed() -> None:
    scheduler = CapacityScheduler(RoutingHealthRegistry())
    assert not scheduler.should_degrade_commit("run_chapter")
    assert not scheduler.should_degrade_commit("init_long")
    assert not scheduler.should_degrade_commit("repair_continuity")
    assert not scheduler.should_degrade_commit("polish_chapter")
    assert not scheduler.should_degrade_commit("book_consistency")


# ---------------------------------------------------------------------------
# admit_or_degrade
# ---------------------------------------------------------------------------


def test_admit_or_degrade_p0_local_commit_in_degraded_mode(
    registry: RoutingHealthRegistry, scheduler: CapacityScheduler
) -> None:
    """P0 local-commit-allowed jobs should always proceed, even when degraded."""
    registry.mark_all_unavailable("all providers down")

    decision = scheduler.admit_or_degrade(
        "resolve_chapter_checkpoint_finalize",
        Priority.P0,
        "FINALIZE",
    )
    assert decision.admitted
    assert decision.assurance == AssuranceLevel.DEGRADED


def test_admit_or_degrade_p0_non_local_commit_in_degraded_mode(
    registry: RoutingHealthRegistry, scheduler: CapacityScheduler
) -> None:
    """P0 jobs that are NOT local-commit-allowed should follow normal rules."""
    registry.mark_all_unavailable("all providers down")

    # resolve_chapter_checkpoint (without _finalize) is P0 but not in the
    # degraded-commit-allowed set
    decision = scheduler.admit_or_degrade(
        "resolve_chapter_checkpoint",
        Priority.P0,
    )
    # P0 is still admitted in degraded mode, but without the local-commit override
    assert decision.admitted
    assert decision.assurance == AssuranceLevel.DEGRADED


def test_admit_or_degrade_p1_normal_mode(
    registry: RoutingHealthRegistry, scheduler: CapacityScheduler
) -> None:
    """P1 with healthy providers -> admitted normally."""
    registry.record_success("openai", "DRAFT_CHAPTER")

    decision = scheduler.admit_or_degrade(
        "run_chapter",
        Priority.P1,
        "DRAFT_CHAPTER",
    )
    assert decision.admitted
    assert decision.assurance == AssuranceLevel.NORMAL


def test_admit_or_degrade_p3_rejected_no_providers(
    registry: RoutingHealthRegistry, scheduler: CapacityScheduler
) -> None:
    """P3 with no providers -> rejected."""
    decision = scheduler.admit_or_degrade(
        "polish_chapter",
        Priority.P3,
        "POLISH_CHAPTER",
    )
    assert not decision.admitted


def test_admit_or_degrade_p1_degraded_when_no_providers(
    registry: RoutingHealthRegistry, scheduler: CapacityScheduler
) -> None:
    """P1 with no providers -> admitted with degraded assurance."""
    decision = scheduler.admit_or_degrade(
        "run_chapter",
        Priority.P1,
        "DRAFT_CHAPTER",
    )
    assert decision.admitted
    assert decision.assurance == AssuranceLevel.DEGRADED


def test_admit_or_degrade_does_not_allow_creative_in_degraded(
    registry: RoutingHealthRegistry, scheduler: CapacityScheduler
) -> None:
    """Creative generation (run_chapter) is NEVER allowed to commit in degraded mode."""
    registry.mark_all_unavailable("all providers down")

    # run_chapter is P1, not in degraded-commit-allowed set
    decision = scheduler.admit_or_degrade("run_chapter", Priority.P1, "DRAFT_CHAPTER")
    # P1 is admitted (will enter retry_wait), but assurance is degraded
    assert decision.admitted
    assert decision.assurance == AssuranceLevel.DEGRADED
    # And it's NOT in the degraded-commit-allowed set
    assert not scheduler.should_degrade_commit("run_chapter")


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------


def test_global_singleton() -> None:
    s1 = get_global_capacity_scheduler()
    s2 = get_global_capacity_scheduler()
    assert s1 is s2
