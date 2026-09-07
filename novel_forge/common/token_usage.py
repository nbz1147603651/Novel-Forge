"""Canonical token-usage normalization and aggregation helpers.

The gateway, desktop jobs, task observation, and analytics views all receive
slightly different usage payloads.  This module keeps their accounting rules
in one place so UI code never invents prices or double-counts repeated call
snapshots.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping


def _non_negative_int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _non_negative_float(value: Any) -> float:
    try:
        return max(0.0, float(value or 0.0))
    except (TypeError, ValueError):
        return 0.0


@dataclass(frozen=True)
class TokenUsage:
    """Normalized usage with an explicit provenance label."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cost_usd: float = 0.0
    call_count: int = 0
    source: str = "unknown"

    @property
    def unattributed_tokens(self) -> int:
        """Tokens reported only as a total by providers without a split."""

        return max(0, self.total_tokens - self.prompt_tokens - self.completion_tokens)

    @property
    def split_coverage(self) -> float:
        if self.total_tokens <= 0:
            return 1.0
        attributed = min(self.total_tokens, self.prompt_tokens + self.completion_tokens)
        return attributed / self.total_tokens

    def plus(self, other: TokenUsage, *, source: str | None = None) -> TokenUsage:
        return TokenUsage(
            prompt_tokens=self.prompt_tokens + other.prompt_tokens,
            completion_tokens=self.completion_tokens + other.completion_tokens,
            total_tokens=self.total_tokens + other.total_tokens,
            cost_usd=self.cost_usd + other.cost_usd,
            call_count=self.call_count + other.call_count,
            source=source or self.source or other.source,
        )


def token_usage_from_mapping(
    payload: Mapping[str, Any] | None,
    *,
    source: str = "model_call",
) -> TokenUsage:
    """Normalize one provider/model-call payload without estimating cost."""

    data = payload or {}
    prompt = _non_negative_int(data.get("prompt_tokens"))
    completion = _non_negative_int(data.get("completion_tokens"))
    total = _non_negative_int(data.get("total_tokens"))
    if total <= 0:
        total = prompt + completion
    return TokenUsage(
        prompt_tokens=prompt,
        completion_tokens=completion,
        total_tokens=total,
        cost_usd=_non_negative_float(data.get("cost_usd")),
        call_count=1 if total > 0 or prompt > 0 or completion > 0 else 0,
        source=source,
    )


def aggregate_model_call_payloads(
    payloads: Iterable[Mapping[str, Any]],
    *,
    source: str = "model_calls",
) -> TokenUsage:
    """Aggregate terminal call payloads, de-duplicating stable ``call_id`` values.

    A call may be emitted first as running and later as success.  Some event
    bridges can also replay the terminal snapshot.  Only the latest successful
    payload for each call id contributes to the result.
    """

    terminal: dict[str, Mapping[str, Any]] = {}
    anonymous_index = 0
    for payload in payloads:
        if str(payload.get("status") or "").strip().lower() != "success":
            continue
        call_id = str(payload.get("call_id") or "").strip()
        if not call_id:
            call_id = f"anonymous:{anonymous_index}"
            anonymous_index += 1
        terminal[call_id] = payload

    result = TokenUsage(source=source)
    for payload in terminal.values():
        result = result.plus(token_usage_from_mapping(payload, source=source), source=source)
    return result


def apply_live_usage_payload(
    current_tokens: int,
    current_cost_usd: float,
    payload: Mapping[str, Any],
    *,
    is_model_call: bool,
) -> tuple[int, float]:
    """Apply a live step payload to legacy cumulative counters.

    ``tokens_so_far`` is a trace-level cumulative total and therefore acts as
    a floor.  Cost changes only when an explicit provider cost is available;
    token counts are never converted with a fabricated flat rate.
    """

    tokens = max(0, int(current_tokens or 0))
    cost = max(0.0, float(current_cost_usd or 0.0))
    if is_model_call and str(payload.get("status") or "").strip().lower() == "success":
        usage = token_usage_from_mapping(payload)
        tokens += usage.total_tokens
        cost += usage.cost_usd

    if "tokens_so_far" in payload:
        tokens = max(tokens, _non_negative_int(payload.get("tokens_so_far")))
    for key in ("cost_so_far", "cumulative_cost_usd"):
        if key in payload:
            cost = max(cost, _non_negative_float(payload.get(key)))
    return tokens, cost


def format_token_count(value: int, *, compact: bool = False) -> str:
    """Format tokens consistently across task panels, pets, and analytics."""

    count = max(0, int(value or 0))
    if not compact or count < 10_000:
        return f"{count:,}"
    if count < 1_000_000:
        compact_value = f"{count / 1_000:.1f}".rstrip("0").rstrip(".")
        return f"{compact_value}K"
    compact_value = f"{count / 1_000_000:.1f}".rstrip("0").rstrip(".")
    return f"{compact_value}M"
