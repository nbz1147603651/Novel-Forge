"""StoryKernelConsistencyRules — rule engine to validate ChapterOutcome against StoryKernel.

Four-layer validation:
  Layer 1 (low-risk):  timeline_monotonicity, chapter_sequence, invalid_character_names
  Layer 2 (medium-risk): dead_characters, suspicious_deaths  (iterate kernel.entities)
  Layer 3 (high-risk):  foreshadowing_lifecycle  (PromiseLedger.status transitions)
  Layer 4 (new):        plot_thread_consistency  (kernel.plot_threads)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from novel_forge.core.domain.guardrails import is_system_artifact_name
from novel_forge.core.schemas.chapter import ChapterOutcome
from novel_forge.story_kernel.schemas import StoryKernel


@dataclass
class ValidationResult:
    """Outcome of a consistency validation pass."""

    violations: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Valid promise status transitions
# ---------------------------------------------------------------------------

_VALID_PROMISE_TRANSITIONS: dict[str, set[str]] = {
    "planted": {"hinted", "paid", "broken"},
    "hinted": {"hinted", "paid", "broken"},
    "partially_paid": {"partially_paid", "paid", "broken"},
    "paid": set(),       # terminal — no further transitions
    "broken": set(),     # terminal — no further transitions
}


class StoryKernelConsistencyRules:
    """Validates that a ChapterOutcome does not violate established StoryKernel canon."""

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def validate(
        self, kernel: StoryKernel, outcome: ChapterOutcome
    ) -> ValidationResult:
        """Run all four rule layers and return merged violations + warnings."""
        violations: list[str] = []
        warnings: list[str] = []

        # Layer 1 — low-risk structural checks
        violations.extend(self._check_timeline_monotonicity(kernel, outcome))
        violations.extend(self._check_chapter_sequence(kernel, outcome))
        violations.extend(self._check_invalid_character_names(outcome))

        # Layer 2 — medium-risk entity lifecycle checks
        violations.extend(self._check_dead_characters(kernel, outcome))
        warnings.extend(self._check_suspicious_deaths(kernel, outcome))

        # Layer 3 — high-risk foreshadowing lifecycle
        violations.extend(self._check_foreshadowing_lifecycle(kernel, outcome))

        # Layer 4 — plot thread consistency
        violations.extend(self._check_plot_thread_consistency(kernel, outcome))

        return ValidationResult(violations=violations, warnings=warnings)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _find_entity_by_name(entities: list[Any], name: str) -> Any | None:
        """Linear scan entity list by name."""
        for e in entities:
            if e.name == name:
                return e
        return None

    # ------------------------------------------------------------------
    # Layer 1 — low-risk structural checks
    # ------------------------------------------------------------------

    @staticmethod
    def _check_timeline_monotonicity(
        kernel: StoryKernel, outcome: ChapterOutcome
    ) -> list[str]:
        """New events must not reference a chapter *before* the outcome's source chapter."""
        violations: list[str] = []
        for event in outcome.new_events:
            if event.chapter < outcome.source_chapter:
                violations.append(
                    f"Event '{event.event}' has chapter {event.chapter}, "
                    f"but outcome is for chapter {outcome.source_chapter}."
                )
        return violations

    @staticmethod
    def _check_chapter_sequence(
        kernel: StoryKernel, outcome: ChapterOutcome
    ) -> list[str]:
        """Outcome source_chapter must equal kernel.current_chapter + 1."""
        violations: list[str] = []
        expected = kernel.current_chapter + 1
        if outcome.source_chapter != expected:
            violations.append(
                f"Expected chapter {expected}, got {outcome.source_chapter}."
            )
        return violations

    @staticmethod
    def _check_invalid_character_names(outcome: ChapterOutcome) -> list[str]:
        """Reject character names that look like leaked schema keys."""
        violations: list[str] = []
        for name in outcome.character_updates:
            if is_system_artifact_name(name):
                violations.append(
                    f"Invalid character name '{name}' looks like a leaked schema key."
                )
        return violations

    # ------------------------------------------------------------------
    # Layer 2 — medium-risk entity lifecycle checks
    # ------------------------------------------------------------------

    @staticmethod
    def _check_dead_characters(
        kernel: StoryKernel, outcome: ChapterOutcome
    ) -> list[str]:
        """Dead/destroyed characters must not be resurrected in character_updates."""
        violations: list[str] = []

        for entity in kernel.entities:
            if entity.status in ("destroyed", "retired"):
                if entity.name in outcome.character_updates:
                    updated = outcome.character_updates[entity.name]
                    if updated.alive:
                        violations.append(
                            f"Dead character '{entity.name}' cannot be resurrected "
                            f"(chapter {outcome.source_chapter}). If the character appears "
                            f"(ghost, memory, evidence), they should remain alive=False."
                        )
        return violations

    @staticmethod
    def _check_suspicious_deaths(
        kernel: StoryKernel, outcome: ChapterOutcome
    ) -> list[str]:
        """Warn when a previously-alive character is being marked dead, or a new character
        is introduced as dead."""
        warnings: list[str] = []

        entity_by_name = {e.name: e for e in kernel.entities}

        for name, updated in outcome.character_updates.items():
            if name in entity_by_name:
                existing = entity_by_name[name]
                if existing.status not in ("destroyed", "retired") and not updated.alive:
                    warnings.append(
                        f"WARNING: Character '{name}' is being marked as dead (alive=False) "
                        f"in chapter {outcome.source_chapter}, but was previously alive. "
                        f"Verify this is intentional."
                    )
            else:
                if not updated.alive:
                    warnings.append(
                        f"WARNING: New character '{name}' is being introduced as dead "
                        f"(alive=False) in chapter {outcome.source_chapter}. "
                        f"Verify this is correct."
                    )

        return warnings

    # ------------------------------------------------------------------
    # Layer 3 — high-risk foreshadowing lifecycle
    # ------------------------------------------------------------------

    @staticmethod
    def _check_foreshadowing_lifecycle(
        kernel: StoryKernel, outcome: ChapterOutcome
    ) -> list[str]:
        """Foreshadowing status must follow valid transitions."""
        violations: list[str] = []
        existing = {p.entry_id: p for p in kernel.promise_ledger}

        for fs_update in outcome.foreshadowing_updates:
            if fs_update.entry_id in existing:
                old_status = str(existing[fs_update.entry_id].status)
                new_status = str(fs_update.status)
                if old_status == new_status:
                    continue
                allowed = _VALID_PROMISE_TRANSITIONS.get(old_status, set())
                if new_status not in allowed:
                    violations.append(
                        f"Foreshadowing '{fs_update.entry_id}': invalid transition "
                        f"{old_status} -> {new_status}."
                    )
        return violations

    # ------------------------------------------------------------------
    # Layer 4 — plot thread consistency
    # ------------------------------------------------------------------

    @staticmethod
    def _check_plot_thread_consistency(
        kernel: StoryKernel, outcome: ChapterOutcome
    ) -> list[str]:
        """Validate plot thread deltas against kernel.plot_threads.

        Checks:
        - Thread IDs in deltas must reference existing kernel threads (or be new)
        - Status must not go backwards (e.g. resolved → active)
        - last_touched_chapter must not decrease
        """
        violations: list[str] = []
        existing_threads = {t.thread_id: t for t in kernel.plot_threads}

        for delta in outcome.plot_thread_deltas:
            tid = delta.thread_id
            new_thread = delta.thread

            if tid in existing_threads:
                old_thread = existing_threads[tid]

                # Status regression: terminal states should not reopen
                terminal_statuses = {"resolved", "abandoned", "completed"}
                if old_thread.status in terminal_statuses and new_thread.status == "active":
                    violations.append(
                        f"Plot thread '{tid}' ({new_thread.title}): "
                        f"status regression {old_thread.status} -> active."
                    )

                # Chapter must not go backwards
                if new_thread.last_touched_chapter < old_thread.last_touched_chapter:
                    violations.append(
                        f"Plot thread '{tid}' ({new_thread.title}): "
                        f"last_touched_chapter decreased from "
                        f"{old_thread.last_touched_chapter} to "
                        f"{new_thread.last_touched_chapter}."
                    )

        return violations
