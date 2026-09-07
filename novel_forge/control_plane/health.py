"""RoutingHealthRegistry - shared provider health and capacity admission.

Phase 4: fills in the shared circuit-breaker / rate-limiter / capacity logic
that was a skeleton in Phase 0.

Key design:
- Process-level singleton (threading.Lock-guarded), keyed by ``(provider, task_type)``.
- Stores health state: breaker state (closed/open/half_open), failure count,
  available concurrency, token budget remaining, assurance level.
- ``can_admit(priority, task_type, provider)`` enforces capacity reservation:
  P0/P1 always admitted (unless all providers are down).
  P3 rejected when health capacity is below the P0/P1 channel threshold.
- All providers unavailable -> degraded assurance, not hard failure.
- ``mark_all_unavailable()`` for total provider outage -> degraded mode.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from novel_forge.control_plane.enums import AssuranceLevel, Priority


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class ProviderHealthEntry:
    """Health snapshot for a single (provider, task_type) pair."""

    provider: str
    task_type: str = ""
    state: str = "closed"  # closed / open / half_open
    opened_until: str = ""
    failure_count: int = 0
    last_failure_kind: str = ""
    last_failure_at: str = ""
    last_success_at: str = ""
    available_concurrency: int = -1  # -1 = unknown / unlimited
    token_budget_remaining: int = -1
    assurance: AssuranceLevel = AssuranceLevel.NORMAL

    @property
    def is_open(self) -> bool:
        """True if the circuit breaker is currently open (rejecting requests)."""
        return self.state == "open"

    @property
    def is_available(self) -> bool:
        """True if the provider can accept requests (closed or half_open)."""
        return self.state in ("closed", "half_open")


@dataclass
class AdmitDecision:
    """Result of a capacity admission check."""

    admitted: bool
    reason: str = ""
    assurance: AssuranceLevel = AssuranceLevel.NORMAL
    degraded_reason: str = ""


class RoutingHealthRegistry:
    """Process-level registry of provider/task health.

    Replaces the fragmented per-ModelRouter circuit breaker state with a
    single shared view. All JobService workers, the API singleton router,
    and TTS execute functions share this registry.

    The registry is advisory in Phase 4: ``can_admit`` returns decisions
    but does not block. Callers (JobService.submit, CapacityScheduler)
    are responsible for acting on the decision.
    """

    # Thresholds for capacity reservation
    _MIN_HEALTHY_PROVIDERS_FOR_P3 = 1  # P3 needs at least 1 healthy provider
    _BREAKER_OPEN_THRESHOLD = 5  # Failures before opening breaker
    _BREAKER_RECOVERY_S = 60  # Seconds before half-open probe

    def __init__(self) -> None:
        self._entries: dict[tuple[str, str], ProviderHealthEntry] = {}
        self._lock = threading.Lock()
        self._global_degraded: bool = False
        self._global_degraded_reason: str = ""

    def get_or_create(self, provider: str, task_type: str = "") -> ProviderHealthEntry:
        with self._lock:
            entry = self._get_or_create_locked(provider, task_type)
            self._refresh_entry_locked(entry)
            return entry

    def _get_or_create_locked(self, provider: str, task_type: str = "") -> ProviderHealthEntry:
        """Get or create entry WITHOUT acquiring lock (caller must hold lock)."""
        key = (provider, task_type)
        entry = self._entries.get(key)
        if entry is None:
            entry = ProviderHealthEntry(provider=provider, task_type=task_type)
            self._entries[key] = entry
        return entry

    def _refresh_entry_locked(self, entry: ProviderHealthEntry) -> None:
        """Move an expired open breaker into its single half-open probe state."""

        if entry.state != "open" or not entry.opened_until:
            return
        try:
            opened_until = datetime.fromisoformat(entry.opened_until)
        except ValueError:
            # A malformed timestamp must never keep a provider permanently
            # unavailable. Treat it as ready for a recovery probe.
            entry.state = "half_open"
            return
        if datetime.now(timezone.utc) >= opened_until:
            entry.state = "half_open"

    def register_provider(self, provider: str, task_type: str = "") -> ProviderHealthEntry:
        """Register a configured provider as an initially healthy route.

        Registration is deliberately separate from ``record_success``: no
        model call is fabricated, but capacity admission can see configured
        routes before their first request.  This prevents P2/P3 work from
        being rejected simply because a freshly-created runtime has not made a
        call yet.
        """

        return self.get_or_create(provider, task_type)

    def record_failure(self, provider: str, task_type: str = "", *, failure_kind: str = "") -> None:
        """Record a provider failure and potentially open the breaker."""
        with self._lock:
            entry = self._get_or_create_locked(provider, task_type)
            entry.failure_count += 1
            entry.last_failure_kind = failure_kind
            entry.last_failure_at = _utc_now_iso()
            # Open breaker if threshold reached
            if entry.failure_count >= self._BREAKER_OPEN_THRESHOLD and entry.state != "open":
                entry.state = "open"
                entry.opened_until = (
                    datetime.now(timezone.utc) + timedelta(seconds=self._BREAKER_RECOVERY_S)
                ).isoformat()

    def record_success(self, provider: str, task_type: str = "") -> None:
        """Record a provider success and close the breaker if it was open."""
        with self._lock:
            entry = self._get_or_create_locked(provider, task_type)
            entry.last_success_at = _utc_now_iso()
            # Close breaker on success (half_open -> closed)
            if entry.state in ("open", "half_open"):
                entry.state = "closed"
                entry.failure_count = 0
                entry.opened_until = ""

    def mark_all_unavailable(self, reason: str = "all providers unavailable") -> None:
        """Mark all known providers as unavailable (global outage)."""
        with self._lock:
            for entry in self._entries.values():
                entry.state = "open"
                entry.assurance = AssuranceLevel.DEGRADED
            self._global_degraded = True
            self._global_degraded_reason = reason

    def clear_global_degraded(self) -> None:
        """Clear the global degraded flag (providers recovered)."""
        with self._lock:
            self._global_degraded = False
            self._global_degraded_reason = ""

    @property
    def is_globally_degraded(self) -> bool:
        with self._lock:
            return self._global_degraded

    @property
    def degraded_reason(self) -> str:
        with self._lock:
            return self._global_degraded_reason

    def count_healthy_providers(self, task_type: str = "") -> int:
        """Count providers that are available (closed or half_open)."""
        with self._lock:
            for entry in self._entries.values():
                self._refresh_entry_locked(entry)
            return sum(
                1
                for (prov, tt), entry in self._entries.items()
                if (task_type == "" or tt == task_type or tt == "") and entry.is_available
            )

    def has_any_healthy_provider(self, task_type: str = "") -> bool:
        """True if at least one provider is available for the given task type."""
        return self.count_healthy_providers(task_type) > 0

    def snapshot(self) -> dict[tuple[str, str], ProviderHealthEntry]:
        """Return a shallow copy of all health entries (thread-safe read)."""
        with self._lock:
            for entry in self._entries.values():
                self._refresh_entry_locked(entry)
            return dict(self._entries)

    def can_admit(
        self, priority: Priority | str, task_type: str = "", provider: str = ""
    ) -> AdmitDecision:
        """Check if a task with the given priority can be admitted.

        Rules:
        - P0 (local commits, recovery): always admitted, even in degraded mode.
        - P1 (prose generation, repair, core TTS): admitted if at least one
          provider is healthy. If all providers are down, admitted with
          degraded assurance (will enter retry_wait).
        - P2 (planning, quality checks): admitted if at least one provider healthy.
        - P3 (critic, polish, humanize, macro audit): rejected if fewer than
          _MIN_HEALTHY_PROVIDERS_FOR_P3 healthy providers, or if globally degraded.
        """
        prio = Priority(priority) if isinstance(priority, str) else priority

        # Global degraded check
        if self.is_globally_degraded:
            if prio == Priority.P0:
                return AdmitDecision(
                    admitted=True,
                    assurance=AssuranceLevel.DEGRADED,
                    degraded_reason=self.degraded_reason,
                )
            elif prio == Priority.P1:
                return AdmitDecision(
                    admitted=True,
                    reason="admitted in degraded mode (will enter retry_wait)",
                    assurance=AssuranceLevel.DEGRADED,
                    degraded_reason=self.degraded_reason,
                )
            else:
                return AdmitDecision(
                    admitted=False,
                    reason=f"P{prio.rank} rejected: global degraded ({self.degraded_reason})",
                    assurance=AssuranceLevel.DEGRADED,
                    degraded_reason=self.degraded_reason,
                )

        # Check specific provider if given
        if provider:
            entry = self.get_or_create(provider, task_type)
            if entry.is_open and prio.rank >= Priority.P2.rank:
                return AdmitDecision(
                    admitted=False,
                    reason=f"provider {provider} breaker is open",
                )

        # Count healthy providers
        healthy = self.count_healthy_providers(task_type)

        if healthy == 0:
            # No healthy providers
            if prio == Priority.P0:
                # P0 (local commits, recovery): always admitted normally -
                # these don't need providers
                return AdmitDecision(admitted=True)
            elif prio == Priority.P1:
                # P1: admit with degraded (will enter retry_wait)
                return AdmitDecision(
                    admitted=True,
                    reason="no healthy providers, admitted with degraded assurance",
                    assurance=AssuranceLevel.DEGRADED,
                    degraded_reason="no healthy providers available",
                )
            else:
                return AdmitDecision(
                    admitted=False,
                    reason=f"P{prio.rank} rejected: no healthy providers",
                    assurance=AssuranceLevel.DEGRADED,
                )

        # P3 needs minimum healthy providers
        if prio == Priority.P3 and healthy < self._MIN_HEALTHY_PROVIDERS_FOR_P3:
            return AdmitDecision(
                admitted=False,
                reason=f"P3 rejected: only {healthy} healthy provider(s) "
                f"(need >= {self._MIN_HEALTHY_PROVIDERS_FOR_P3})",
            )

        return AdmitDecision(admitted=True)


def make_router_health_observer(
    registry: RoutingHealthRegistry,
) -> Callable[[str, dict[str, Any]], None]:
    """Create a router observer that keeps the shared registry live.

    The gateway already owns request retries and the authoritative provider
    circuit breaker.  This observer mirrors the *completed* request outcome
    into the control-plane view without taking part in routing or raising into
    a model call.  It is attached only when runtime control is enabled.
    """

    success_events = frozenset({"api_call_done", "api_stream_done"})
    failure_events = frozenset(
        {"api_call_error", "api_stream_error", "api_call_timeout", "api_call_incomplete"}
    )

    def observe(event: str, payload: dict[str, Any]) -> None:
        provider = str(payload.get("provider") or "").strip()
        if not provider:
            return
        task_type = str(payload.get("task") or "").strip()
        if event in success_events:
            registry.record_success(provider, task_type)
        elif event in failure_events:
            registry.record_failure(provider, task_type, failure_kind=event)

    return observe


# Process-level singleton
_global_registry: RoutingHealthRegistry | None = None
_global_registry_lock = threading.Lock()


def get_global_health_registry() -> RoutingHealthRegistry:
    """Return the process-wide RoutingHealthRegistry singleton."""
    global _global_registry
    with _global_registry_lock:
        if _global_registry is None:
            _global_registry = RoutingHealthRegistry()
        return _global_registry
