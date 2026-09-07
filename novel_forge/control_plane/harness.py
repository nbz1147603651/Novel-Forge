"""StageHarness - permission enforcement for pipeline stages.

Validates that each stage operation respects its StageDefinition:
- Input artifact types are allowed
- Output proposal types are allowed
- Commit operations target allowed resource kinds
- Text modifications respect the editable_text_window
- Required validations pass before commit

Phase 5: advisory mode (logs warnings, does not block).
Phase 6: enforce mode (raises on violation).

The harness sits between PipelineStep.run() and _execute(), or wraps
persist_stage_artifact / commit operations.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from novel_forge.control_plane.registry import get_stage_definition
from novel_forge.control_plane.stage_definitions import StageDefinition

_log = logging.getLogger("novel_forge.control_plane.harness")

# Global enforcement mode: "advisory" (Phase 5) or "enforce" (Phase 6)
_enforcement_mode: str = "advisory"


def set_enforcement_mode(mode: str) -> None:
    """Set the harness enforcement mode.

    "advisory": log warnings on violations (Phase 5 default).
    "enforce": raise HarnessViolationError on violations (Phase 6).
    """
    global _enforcement_mode
    if mode not in ("advisory", "enforce"):
        raise ValueError(f"Invalid enforcement mode: {mode}")
    _enforcement_mode = mode


def get_enforcement_mode() -> str:
    """Return the current enforcement mode."""
    return _enforcement_mode


class HarnessViolationError(Exception):
    """Raised when a stage violates its StageDefinition (enforce mode only)."""

    def __init__(self, stage_name: str, violation: str, detail: str = "") -> None:
        self.stage_name = stage_name
        self.violation = violation
        self.detail = detail
        super().__init__(
            f"Stage '{stage_name}' violation: {violation}" + (f" - {detail}" if detail else "")
        )


@dataclass
class HarnessCheckResult:
    """Result of a harness permission check."""

    allowed: bool
    violation: str = ""
    detail: str = ""
    stage_name: str = ""

    @property
    def passed(self) -> bool:
        return self.allowed


class StageHarness:
    """Permission enforcement for a single pipeline stage.

    Usage:
        harness = StageHarness("repair")
        harness.check_input_artifact("wave")  # OK
        harness.check_input_artifact("project_spec")  # Warning/Error
        harness.check_commit("canon")  # Error: repair can't commit canon
    """

    def __init__(self, stage_name: str) -> None:
        self._stage_name = stage_name
        self._definition = get_stage_definition(stage_name)
        if self._definition is None:
            _log.warning("No StageDefinition found for stage '%s'", stage_name)

    @property
    def definition(self) -> StageDefinition | None:
        return self._definition

    @property
    def stage_name(self) -> str:
        return self._stage_name

    @property
    def has_definition(self) -> bool:
        return self._definition is not None

    def _check(self, allowed: bool, violation: str, detail: str = "") -> HarnessCheckResult:
        """Create a check result and handle enforcement."""
        result = HarnessCheckResult(
            allowed=allowed,
            violation=violation if not allowed else "",
            detail=detail,
            stage_name=self._stage_name,
        )
        if not allowed:
            if _enforcement_mode == "enforce":
                raise HarnessViolationError(self._stage_name, violation, detail)
            else:
                _log.warning(
                    "Harness violation (advisory): stage '%s' - %s%s",
                    self._stage_name,
                    violation,
                    f" - {detail}" if detail else "",
                )
        return result

    # ------------------------------------------------------------------
    # Input checks
    # ------------------------------------------------------------------

    def check_input_artifact(self, artifact_type: str) -> HarnessCheckResult:
        """Check if the stage is allowed to read the given artifact type."""
        if self._definition is None:
            return HarnessCheckResult(allowed=True, stage_name=self._stage_name)
        return self._check(
            self._definition.can_read_artifact(artifact_type),
            "unauthorized_input_artifact",
            f"stage '{self._stage_name}' cannot read artifact '{artifact_type}'",
        )

    def check_read_project_spec(self) -> HarnessCheckResult:
        """Check if the stage is allowed to read the full project_spec."""
        if self._definition is None:
            return HarnessCheckResult(allowed=True, stage_name=self._stage_name)
        return self._check(
            self._definition.can_read_full_project_spec,
            "unauthorized_project_spec_read",
            f"stage '{self._stage_name}' cannot read full project_spec",
        )

    # ------------------------------------------------------------------
    # Output checks
    # ------------------------------------------------------------------

    def check_output_proposal(self, proposal_type: str) -> HarnessCheckResult:
        """Check if the stage is allowed to generate the given proposal type."""
        if self._definition is None:
            return HarnessCheckResult(allowed=True, stage_name=self._stage_name)
        return self._check(
            self._definition.can_generate_proposal(proposal_type),
            "unauthorized_output_proposal",
            f"stage '{self._stage_name}' cannot generate proposal '{proposal_type}'",
        )

    # ------------------------------------------------------------------
    # Commit checks
    # ------------------------------------------------------------------

    def check_commit(self, resource_kind: str) -> HarnessCheckResult:
        """Check if the stage is allowed to commit the given resource kind."""
        if self._definition is None:
            return HarnessCheckResult(allowed=True, stage_name=self._stage_name)
        return self._check(
            self._definition.can_commit_resource(resource_kind),
            "unauthorized_commit",
            f"stage '{self._stage_name}' cannot commit resource '{resource_kind}'",
        )

    def check_canon_commit(
        self, *, hard_gates_passed: bool, watermark_match: bool
    ) -> HarnessCheckResult:
        """Check if the stage can commit to Canon (Finalize-specific).

        Canon writes require ALL hard gates to pass AND the canon watermark
        to match the expected state. This is never skippable in degraded mode.
        """
        if self._definition is None:
            return HarnessCheckResult(allowed=True, stage_name=self._stage_name)

        if not self._definition.can_commit_resource("canon"):
            return self._check(
                False,
                "unauthorized_canon_commit",
                f"stage '{self._stage_name}' cannot commit canon",
            )

        if not hard_gates_passed:
            return self._check(
                False, "canon_hard_gate_failed", "cannot commit canon: hard gates not passed"
            )

        if not watermark_match:
            return self._check(
                False, "canon_watermark_mismatch", "cannot commit canon: watermark mismatch"
            )

        return HarnessCheckResult(allowed=True, stage_name=self._stage_name)

    # ------------------------------------------------------------------
    # Text modification checks
    # ------------------------------------------------------------------

    def check_text_modification(self, modification_type: str) -> HarnessCheckResult:
        """Check if the stage is allowed to modify text in the given way.

        modification_type: "full" (full rewrite) or "patch" (bounded patch)
        """
        if self._definition is None:
            return HarnessCheckResult(allowed=True, stage_name=self._stage_name)

        allowed_window = self._definition.editable_text_window
        if allowed_window == "none":
            return self._check(
                False,
                "unauthorized_text_modification",
                f"stage '{self._stage_name}' cannot modify text (read-only)",
            )
        if modification_type == "full" and allowed_window == "patch":
            return self._check(
                False,
                "unauthorized_full_rewrite",
                f"stage '{self._stage_name}' can only do bounded patches, not full rewrites",
            )
        if modification_type == "patch" and allowed_window in ("patch", "full"):
            return HarnessCheckResult(allowed=True, stage_name=self._stage_name)
        if modification_type == "full" and allowed_window == "full":
            return HarnessCheckResult(allowed=True, stage_name=self._stage_name)

        return self._check(
            False,
            "unauthorized_text_modification",
            f"stage '{self._stage_name}' cannot do '{modification_type}' modification",
        )

    # ------------------------------------------------------------------
    # Validation checks
    # ------------------------------------------------------------------

    def check_required_validations(self, passed_validations: set[str]) -> HarnessCheckResult:
        """Check if all required validations have passed."""
        if self._definition is None:
            return HarnessCheckResult(allowed=True, stage_name=self._stage_name)

        missing = self._definition.required_validations - passed_validations
        if missing:
            return self._check(
                False,
                "missing_required_validations",
                f"missing validations: {', '.join(sorted(missing))}",
            )

        return HarnessCheckResult(allowed=True, stage_name=self._stage_name)
