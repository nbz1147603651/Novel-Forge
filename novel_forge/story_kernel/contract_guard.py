"""Runtime enforcement for StoryKernel field contracts.

`contracts.py` declares per-step read/write/immutable permissions over the
StoryKernel field pool.  Those declarations used to be documentation-only;
this module adds the runtime guard that validates actual kernel mutations at
the single write boundary (`StoryKernelStateWriter`).

Enforcement modes (settings `kernel_contract_mode` /
NOVEL_FORGE_KERNEL_CONTRACT_MODE):
    off    — guard disabled, zero overhead
    warn   — violations are logged, the write proceeds (default)
    strict — violations raise KernelContractViolationError, write is aborted

Scope notes:
- Metadata fields (project_id, current_chapter, active_volume, title,
  premise) are orchestration bookkeeping and exempt from enforcement.
- Kernel fields outside VALID_FIELD_NAMES (artifact_refs, project_mode,
  archived_* , ...) are outside the contract surface and ignored.
- When multiple steps participate in one write (e.g. extract +
  state_adjudication), effective writes are the union of the contracts'
  writes and effective immutables are the intersection — a field is only
  treated as immutable when every participating contract forbids changing it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Iterable

from novel_forge.core.exceptions import ConsistencyError
from novel_forge.obs.logger import get_logger
from novel_forge.story_kernel.contracts import ALL_CONTRACTS, VALID_FIELD_NAMES, FieldContract

if TYPE_CHECKING:
    from novel_forge.story_kernel.schemas import StoryKernel

_logger = get_logger("story_kernel.contract_guard")

# Violation kinds
IMMUTABLE_WRITE = "immutable_write"
UNDECLARED_WRITE = "undeclared_write"

# Orchestration bookkeeping — always writable, never contract-checked.
METADATA_FIELDS: frozenset[str] = frozenset(
    {"project_id", "current_chapter", "active_volume", "title", "premise"}
)

# Contracts indexed by their declared step_name (e.g. "extract").
CONTRACTS_BY_STEP: dict[str, FieldContract] = {c.step_name: c for c in ALL_CONTRACTS.values()}


@dataclass(frozen=True)
class KernelContractViolation:
    """One detected contract breach on a single kernel field."""

    step_name: str
    field_name: str
    kind: str  # IMMUTABLE_WRITE | UNDECLARED_WRITE
    message: str


class KernelContractViolationError(ConsistencyError):
    """Raised in strict mode when a kernel write breaches field contracts.

    Subclasses ConsistencyError (not ConsistencyViolationError) so it does NOT
    trigger the automatic chapter replan loop — a contract breach is a
    programming/configuration error, not a narrative consistency problem.
    """

    def __init__(self, violations: list[KernelContractViolation]) -> None:
        self.violations: tuple[KernelContractViolation, ...] = tuple(violations)
        super().__init__(
            "StoryKernel field contract violation(s): " + "; ".join(v.message for v in violations),
            details=[v.message for v in violations],
        )


def normalize_step_names(step_names: str | Iterable[str]) -> list[str]:
    """Normalize a step name or iterable of step names into a clean list."""
    if isinstance(step_names, str):
        step_names = (step_names,)
    seen: set[str] = set()
    ordered: list[str] = []
    for name in step_names:
        key = str(name or "").strip()
        if key and key not in seen:
            seen.add(key)
            ordered.append(key)
    return ordered


def contracts_for_steps(step_names: str | Iterable[str]) -> list[FieldContract]:
    """Resolve declared contracts for the given step names.

    Unknown step names are skipped — a missing contract means we cannot judge
    the write, not that the write is illegal.
    """
    return [
        CONTRACTS_BY_STEP[name]
        for name in normalize_step_names(step_names)
        if name in CONTRACTS_BY_STEP
    ]


def diff_kernel_fields(before: StoryKernel, after: StoryKernel) -> set[str]:
    """Return the set of contract-surface fields whose value changed.

    Only VALID_FIELD_NAMES are compared; other kernel attributes
    (artifact_refs, project_mode, archives, ...) are outside the contract
    surface by design.
    """
    before_dump = before.model_dump(mode="json")
    after_dump = after.model_dump(mode="json")
    return {name for name in VALID_FIELD_NAMES if before_dump.get(name) != after_dump.get(name)}


def check_contract_write(
    step_names: str | Iterable[str],
    *,
    before: StoryKernel,
    after: StoryKernel,
) -> list[KernelContractViolation]:
    """Validate a kernel mutation against the participating steps' contracts.

    Returns an empty list when the write is allowed or when none of the given
    step names has a declared contract.
    """
    contracts = contracts_for_steps(step_names)
    if not contracts:
        return []
    effective_writes: set[str] = set().union(*(c.writes for c in contracts))
    effective_immutable: set[str] = set.intersection(*(set(c.immutable) for c in contracts))
    label = "+".join(c.step_name for c in contracts)
    violations: list[KernelContractViolation] = []
    for field_name in sorted(diff_kernel_fields(before, after)):
        if field_name in METADATA_FIELDS:
            continue
        if field_name in effective_immutable:
            violations.append(
                KernelContractViolation(
                    step_name=label,
                    field_name=field_name,
                    kind=IMMUTABLE_WRITE,
                    message=(f"step(s) '{label}' modified immutable kernel field '{field_name}'"),
                )
            )
        elif field_name not in effective_writes:
            violations.append(
                KernelContractViolation(
                    step_name=label,
                    field_name=field_name,
                    kind=UNDECLARED_WRITE,
                    message=(
                        f"step(s) '{label}' modified kernel field "
                        f"'{field_name}' which is not in its declared writes"
                    ),
                )
            )
    return violations


def enforce_contract_write(
    step_names: str | Iterable[str],
    *,
    before: StoryKernel,
    after: StoryKernel,
    mode: str = "warn",
) -> list[KernelContractViolation]:
    """Check a kernel write and apply the enforcement mode.

    Returns the detected violations.  In "warn" mode violations are logged and
    the caller may proceed; in "strict" mode a KernelContractViolationError is
    raised before any persistence happens.
    """
    mode = str(mode or "warn").strip().lower()
    if mode == "off":
        return []
    violations = check_contract_write(step_names, before=before, after=after)
    if not violations:
        return []
    if mode == "strict":
        raise KernelContractViolationError(violations)
    for violation in violations:
        _logger.warning(
            "kernel_contract_violation | kind=%s | field=%s | %s",
            violation.kind,
            violation.field_name,
            violation.message,
        )
    return violations


__all__ = [
    "CONTRACTS_BY_STEP",
    "IMMUTABLE_WRITE",
    "METADATA_FIELDS",
    "UNDECLARED_WRITE",
    "KernelContractViolation",
    "KernelContractViolationError",
    "check_contract_write",
    "contracts_for_steps",
    "diff_kernel_fields",
    "enforce_contract_write",
    "normalize_step_names",
]
