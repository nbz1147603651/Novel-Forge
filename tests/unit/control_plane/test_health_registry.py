"""Tests for RoutingHealthRegistry - shared health and capacity admission."""

from __future__ import annotations

import pytest

from novel_forge.control_plane.enums import AssuranceLevel, Priority
from novel_forge.control_plane.health import (
    ProviderHealthEntry,
    RoutingHealthRegistry,
    get_global_health_registry,
    make_router_health_observer,
)


@pytest.fixture
def registry() -> RoutingHealthRegistry:
    return RoutingHealthRegistry()


# ---------------------------------------------------------------------------
# ProviderHealthEntry properties
# ---------------------------------------------------------------------------


def test_entry_is_open() -> None:
    entry = ProviderHealthEntry(provider="openai", state="open")
    assert entry.is_open
    assert not entry.is_available

    entry.state = "closed"
    assert not entry.is_open
    assert entry.is_available

    entry.state = "half_open"
    assert not entry.is_open
    assert entry.is_available


# ---------------------------------------------------------------------------
# Record failure / success
# ---------------------------------------------------------------------------


def test_record_failure(registry: RoutingHealthRegistry) -> None:
    registry.record_failure("openai", "DRAFT_CHAPTER", failure_kind="timeout")
    entry = registry.get_or_create("openai", "DRAFT_CHAPTER")
    assert entry.failure_count == 1
    assert entry.last_failure_kind == "timeout"
    assert entry.last_failure_at != ""


def test_record_success(registry: RoutingHealthRegistry) -> None:
    registry.record_success("openai", "DRAFT_CHAPTER")
    entry = registry.get_or_create("openai", "DRAFT_CHAPTER")
    assert entry.last_success_at != ""
    assert entry.state == "closed"


def test_breaker_opens_after_threshold(registry: RoutingHealthRegistry) -> None:
    """Breaker should open after _BREAKER_OPEN_THRESHOLD failures."""
    threshold = registry._BREAKER_OPEN_THRESHOLD
    for _ in range(threshold):
        registry.record_failure("deepseek", "DRAFT_CHAPTER")

    entry = registry.get_or_create("deepseek", "DRAFT_CHAPTER")
    assert entry.state == "open"
    assert entry.is_open


def test_breaker_closes_on_success(registry: RoutingHealthRegistry) -> None:
    """Breaker should close when a success is recorded."""
    # Open the breaker
    threshold = registry._BREAKER_OPEN_THRESHOLD
    for _ in range(threshold):
        registry.record_failure("openai", "DRAFT_CHAPTER")
    entry = registry.get_or_create("openai", "DRAFT_CHAPTER")
    assert entry.state == "open"

    # Record success -> breaker closes
    registry.record_success("openai", "DRAFT_CHAPTER")
    entry = registry.get_or_create("openai", "DRAFT_CHAPTER")
    assert entry.state == "closed"
    assert entry.failure_count == 0


def test_open_breaker_becomes_half_open_after_recovery_window(
    registry: RoutingHealthRegistry,
) -> None:
    """Expired recovery windows must allow a real half-open probe."""
    for _ in range(registry._BREAKER_OPEN_THRESHOLD):
        registry.record_failure("openai", "DRAFT_CHAPTER")
    entry = registry.get_or_create("openai", "DRAFT_CHAPTER")
    assert entry.state == "open"

    from datetime import datetime, timedelta, timezone

    entry.opened_until = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    assert registry.get_or_create("openai", "DRAFT_CHAPTER").state == "half_open"


def test_router_health_observer_mirrors_completed_route_outcomes(
    registry: RoutingHealthRegistry,
) -> None:
    observer = make_router_health_observer(registry)
    registry.register_provider("openai")

    observer("api_call_done", {"provider": "openai", "task": "DRAFT_CHAPTER"})
    assert registry.get_or_create("openai", "DRAFT_CHAPTER").last_success_at

    observer("api_call_timeout", {"provider": "openai", "task": "DRAFT_CHAPTER"})
    entry = registry.get_or_create("openai", "DRAFT_CHAPTER")
    assert entry.failure_count == 1
    assert entry.last_failure_kind == "api_call_timeout"


# ---------------------------------------------------------------------------
# Count healthy providers
# ---------------------------------------------------------------------------


def test_count_healthy_providers(registry: RoutingHealthRegistry) -> None:
    registry.record_success("openai", "DRAFT_CHAPTER")
    registry.record_success("deepseek", "DRAFT_CHAPTER")

    # Both are healthy (closed)
    assert registry.count_healthy_providers("DRAFT_CHAPTER") == 2
    assert registry.has_any_healthy_provider("DRAFT_CHAPTER")


def test_count_healthy_after_breaker_open(registry: RoutingHealthRegistry) -> None:
    registry.record_success("openai", "DRAFT_CHAPTER")
    registry.record_success("deepseek", "DRAFT_CHAPTER")

    # Open deepseek's breaker
    threshold = registry._BREAKER_OPEN_THRESHOLD
    for _ in range(threshold):
        registry.record_failure("deepseek", "DRAFT_CHAPTER")

    assert registry.count_healthy_providers("DRAFT_CHAPTER") == 1
    assert registry.has_any_healthy_provider("DRAFT_CHAPTER")


def test_count_healthy_no_providers(registry: RoutingHealthRegistry) -> None:
    assert registry.count_healthy_providers("DRAFT_CHAPTER") == 0
    assert not registry.has_any_healthy_provider("DRAFT_CHAPTER")


# ---------------------------------------------------------------------------
# can_admit - normal mode
# ---------------------------------------------------------------------------


def test_can_admit_p0_always_admitted(registry: RoutingHealthRegistry) -> None:
    """P0 is always admitted, even with no providers."""
    decision = registry.can_admit(Priority.P0, "DRAFT_CHAPTER")
    assert decision.admitted
    assert decision.assurance == AssuranceLevel.NORMAL


def test_can_admit_p1_with_healthy_provider(registry: RoutingHealthRegistry) -> None:
    registry.record_success("openai", "DRAFT_CHAPTER")
    decision = registry.can_admit(Priority.P1, "DRAFT_CHAPTER")
    assert decision.admitted


def test_can_admit_p1_no_providers_degraded(registry: RoutingHealthRegistry) -> None:
    """P1 with no providers -> admitted with degraded assurance."""
    decision = registry.can_admit(Priority.P1, "DRAFT_CHAPTER")
    assert decision.admitted
    assert decision.assurance == AssuranceLevel.DEGRADED


def test_can_admit_p3_with_healthy_providers(registry: RoutingHealthRegistry) -> None:
    registry.record_success("openai", "POLISH_CHAPTER")
    registry.record_success("deepseek", "POLISH_CHAPTER")
    decision = registry.can_admit(Priority.P3, "POLISH_CHAPTER")
    assert decision.admitted


def test_can_admit_p3_rejected_no_providers(registry: RoutingHealthRegistry) -> None:
    """P3 with no providers -> rejected."""
    decision = registry.can_admit(Priority.P3, "POLISH_CHAPTER")
    assert not decision.admitted
    assert decision.assurance == AssuranceLevel.DEGRADED


def test_can_admit_p2_rejected_no_providers(registry: RoutingHealthRegistry) -> None:
    """P2 with no providers -> rejected."""
    decision = registry.can_admit(Priority.P2, "PLAN_CHAPTER")
    assert not decision.admitted


def test_can_admit_p3_rejected_provider_breaker_open(registry: RoutingHealthRegistry) -> None:
    """P3 with specific provider breaker open -> rejected."""
    threshold = registry._BREAKER_OPEN_THRESHOLD
    for _ in range(threshold):
        registry.record_failure("openai", "POLISH_CHAPTER")

    decision = registry.can_admit(Priority.P3, "POLISH_CHAPTER", provider="openai")
    assert not decision.admitted


# ---------------------------------------------------------------------------
# can_admit - global degraded mode
# ---------------------------------------------------------------------------


def test_global_degraded_p0_admitted(registry: RoutingHealthRegistry) -> None:
    registry.mark_all_unavailable("all providers down")
    decision = registry.can_admit(Priority.P0, "DRAFT_CHAPTER")
    assert decision.admitted
    assert decision.assurance == AssuranceLevel.DEGRADED


def test_global_degraded_p1_admitted_degraded(registry: RoutingHealthRegistry) -> None:
    registry.mark_all_unavailable("all providers down")
    decision = registry.can_admit(Priority.P1, "DRAFT_CHAPTER")
    assert decision.admitted
    assert decision.assurance == AssuranceLevel.DEGRADED


def test_global_degraded_p2_rejected(registry: RoutingHealthRegistry) -> None:
    registry.mark_all_unavailable("all providers down")
    decision = registry.can_admit(Priority.P2, "PLAN_CHAPTER")
    assert not decision.admitted


def test_global_degraded_p3_rejected(registry: RoutingHealthRegistry) -> None:
    registry.mark_all_unavailable("all providers down")
    decision = registry.can_admit(Priority.P3, "POLISH_CHAPTER")
    assert not decision.admitted


def test_clear_global_degraded(registry: RoutingHealthRegistry) -> None:
    registry.mark_all_unavailable("test")
    assert registry.is_globally_degraded
    registry.clear_global_degraded()
    assert not registry.is_globally_degraded


# ---------------------------------------------------------------------------
# Snapshot
# ---------------------------------------------------------------------------


def test_snapshot(registry: RoutingHealthRegistry) -> None:
    registry.record_success("openai", "DRAFT_CHAPTER")
    registry.record_failure("deepseek", "WAVE_CHAPTER")

    snap = registry.snapshot()
    assert ("openai", "DRAFT_CHAPTER") in snap
    assert ("deepseek", "WAVE_CHAPTER") in snap
    assert snap[("openai", "DRAFT_CHAPTER")].state == "closed"


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------


def test_global_singleton() -> None:
    r1 = get_global_health_registry()
    r2 = get_global_health_registry()
    assert r1 is r2
