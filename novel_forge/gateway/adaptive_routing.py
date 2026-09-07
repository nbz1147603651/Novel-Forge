"""Adaptive routing audit — shadow-mode decision logging.

Phase 5 of the Pipeline Optimization Plan.  This module provides a
lightweight audit hook that records what an adaptive routing strategy
*would* recommend vs what the user's explicit routing actually selected.

When ``long_adaptive_routing_mode`` is:
- ``off``    : no-op (zero cost)
- ``shadow`` : log the recommendation alongside the actual route
- ``enforce``: reserved for future use — currently behaves like shadow

Priority rules (documented here as the authoritative reference):

1. User explicit ``TASK_ROUTING`` — always wins, never overridden.
2. ``TASK_FALLBACK_ROUTING`` — user-configured candidate pool.
3. Adaptive strategy — may only choose *within* the user candidate pool.
4. Model capability floor — structured-output / context window must fit.
5. Fallback / circuit breaker — original semantics preserved.

Default: adaptive routing is OFF.  When enabled, it cannot introduce
new providers/models outside the user-authorized candidate set.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

_logger = logging.getLogger(__name__)


class RoutingDecisionSource(str, Enum):
    """Where the final routing decision came from."""

    EXPLICIT = "explicit"           # User TASK_ROUTING
    FALLBACK = "fallback"           # User TASK_FALLBACK_ROUTING
    ADAPTIVE = "adaptive"           # Adaptive strategy (within user candidates)
    DEFAULT = "default"             # System default
    CAPABILITY_FILTERED = "capability_filtered"  # Filtered by capability floor


@dataclass(frozen=True)
class AdaptiveRoutingAudit:
    """Record of one routing decision for adaptive-routing shadow analysis."""

    task_type: str
    severity: str                       # "critical" | "high" | "medium" | "low"
    budget_pressure: float              # 0.0 = no pressure, 1.0 = max
    user_explicit_route: str            # e.g. "openai:gpt-4o"
    adaptive_recommended: str           # what the adaptive strategy would pick
    adaptive_applied: bool              # whether the recommendation was used
    reason: str                         # why the decision was made
    candidate_pool: tuple[str, ...] = field(default_factory=tuple)
    capability_floor_met: bool = True


def audit_routing_decision(
    *,
    task_type: str,
    resolved_provider: str,
    resolved_model_id: str,
    settings: Any,
    severity: str = "",
    budget_pressure: float = 0.0,
    candidate_pool: tuple[str, ...] = (),
    spending_tracker: Any | None = None,
) -> AdaptiveRoutingAudit | None:
    """Evaluate and log an adaptive routing recommendation.

    Returns the audit record (or None if mode is off).  Does NOT change
    the actual routing — this is purely observational in shadow mode.
    """
    mode = str(
        getattr(settings, "long_adaptive_routing_mode", "off") or "off"
    )
    if mode == "off":
        return None

    actual_route = f"{resolved_provider}:{resolved_model_id}"

    # Determine what the adaptive strategy would recommend.
    # For now (shadow mode), the recommendation is a simple heuristic:
    # - High budget pressure + low/medium severity → prefer cheaper candidate
    # - Otherwise → keep the resolved route
    adaptive_recommended = actual_route
    reason = "no_adaptive_change_needed"

    if budget_pressure > 0.7 and severity in ("low", "medium", ""):
        # Find cheapest candidate in the pool.
        if candidate_pool:
            cheapest = _estimate_cheapest_candidate(candidate_pool)
            if cheapest and cheapest != actual_route:
                adaptive_recommended = cheapest
                reason = f"budget_pressure_{budget_pressure:.1f}_prefer_cheaper"

    applied = adaptive_recommended == actual_route

    audit = AdaptiveRoutingAudit(
        task_type=task_type,
        severity=severity,
        budget_pressure=budget_pressure,
        user_explicit_route=actual_route,
        adaptive_recommended=adaptive_recommended,
        adaptive_applied=applied,
        reason=reason,
        candidate_pool=candidate_pool,
    )

    _logger.info(
        "adaptive_routing_audit | task=%s | actual=%s | recommended=%s | "
        "applied=%s | reason=%s | pressure=%.2f",
        task_type,
        actual_route,
        adaptive_recommended,
        applied,
        reason,
        budget_pressure,
    )

    return audit


def _estimate_cheapest_candidate(
    candidate_pool: tuple[str, ...],
) -> str | None:
    """Heuristic: pick the candidate with the lowest known price tier.

    This is a placeholder — the real implementation would consult
    ``gateway.pricing`` for per-token costs.  For shadow mode we just
    pick the last candidate (typically the fallback = cheaper model).
    """
    if not candidate_pool:
        return None
    # Simple heuristic: last candidate is typically the cheaper fallback.
    return candidate_pool[-1] if candidate_pool else None
