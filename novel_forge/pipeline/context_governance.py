"""Shared prompt-context partitioning and evidence-budget primitives.

The helpers in this module deliberately separate two overflow policies:

* authoritative inputs use complete partitioning and may never be truncated;
* ranked historical evidence is selected once under an explicit token budget.

Task-specific services remain responsible for constructing semantic projections,
while this module enforces the mechanical coverage and budget invariants.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from typing import Generic, TypeVar

ItemT = TypeVar("ItemT")


class CompletePartitionError(ValueError):
    """Raised when one authoritative item cannot fit without data loss."""


class EvidenceBudgetExceeded(ValueError):
    """Raised when mandatory evidence alone exceeds the shared pack budget."""


@dataclass(frozen=True)
class CompletePartition(Generic[ItemT]):
    """One lossless partition of an ordered authoritative input sequence."""

    items: tuple[ItemT, ...]
    estimated_chars: int


@dataclass(frozen=True)
class EvidenceSelection(Generic[ItemT]):
    """A single-boundary selection result for ranked historical evidence."""

    selected: tuple[ItemT, ...]
    omitted_count: int
    used_tokens: int
    token_budget: int


def json_char_size(value: object) -> int:
    """Return deterministic serialized character size for a prompt payload."""

    return len(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str))


def estimate_json_tokens(value: object) -> int:
    """Conservative tokenizer-independent estimate used at evidence boundaries."""

    chars = json_char_size(value)
    return max(1, (chars + 2) // 3)


def partition_complete(
    items: Sequence[ItemT],
    *,
    build_payload: Callable[[list[ItemT]], object],
    measure_chars: Callable[[object], int] = json_char_size,
    char_budget: int,
    max_items: int,
    item_label: str = "item",
) -> list[CompletePartition[ItemT]]:
    """Partition every ordered item exactly once under a rendered-size budget.

    A single oversized item fails explicitly.  Silently shortening it would turn
    a completeness task into an unreliable sample, which is never acceptable for
    authoritative outlines, contracts, or audit text.
    """

    if char_budget <= 0:
        raise ValueError("char_budget must be positive")
    if max_items <= 0:
        raise ValueError("max_items must be positive")
    if not items:
        return [CompletePartition(items=(), estimated_chars=measure_chars(build_payload([])))]

    partitions: list[CompletePartition[ItemT]] = []
    current: list[ItemT] = []
    for item in items:
        candidate = [*current, item]
        candidate_chars = measure_chars(build_payload(candidate))
        if current and (len(candidate) > max_items or candidate_chars > char_budget):
            current_chars = measure_chars(build_payload(current))
            partitions.append(
                CompletePartition(items=tuple(current), estimated_chars=current_chars)
            )
            current = [item]
            candidate_chars = measure_chars(build_payload(current))
            candidate = current

        if candidate_chars > char_budget:
            raise CompletePartitionError(
                f"single {item_label} exceeds complete prompt budget: "
                f"estimated_chars={candidate_chars}, budget={char_budget}"
            )
        current = candidate

    if current:
        partitions.append(
            CompletePartition(
                items=tuple(current),
                estimated_chars=measure_chars(build_payload(current)),
            )
        )
    return partitions


def select_ranked_evidence(
    ranked_items: Iterable[ItemT],
    *,
    token_budget: int,
    estimate_tokens: Callable[[ItemT], int],
    mandatory_items: Iterable[ItemT] = (),
    identity: Callable[[ItemT], str],
) -> EvidenceSelection[ItemT]:
    """Apply one shared token budget after deduplicating mandatory and ranked cards."""

    clean_budget = max(0, int(token_budget))
    selected: list[ItemT] = []
    selected_ids: set[str] = set()
    used_tokens = 0

    for item in mandatory_items:
        item_id = identity(item)
        if not item_id or item_id in selected_ids:
            continue
        cost = max(1, int(estimate_tokens(item)))
        if used_tokens + cost > clean_budget:
            raise EvidenceBudgetExceeded(
                "mandatory evidence exceeds shared token budget: "
                f"required>{clean_budget}, selected={len(selected)}"
            )
        selected.append(item)
        selected_ids.add(item_id)
        used_tokens += cost

    omitted = 0
    for item in ranked_items:
        item_id = identity(item)
        if not item_id or item_id in selected_ids:
            continue
        cost = max(1, int(estimate_tokens(item)))
        if used_tokens + cost > clean_budget:
            omitted += 1
            continue
        selected.append(item)
        selected_ids.add(item_id)
        used_tokens += cost

    return EvidenceSelection(
        selected=tuple(selected),
        omitted_count=omitted,
        used_tokens=used_tokens,
        token_budget=clean_budget,
    )


__all__ = (
    "CompletePartition",
    "CompletePartitionError",
    "EvidenceBudgetExceeded",
    "EvidenceSelection",
    "estimate_json_tokens",
    "json_char_size",
    "partition_complete",
    "select_ranked_evidence",
)
