"""ContinuityRules — local rule checks for chapter-to-chapter continuity."""

from __future__ import annotations

from dataclasses import dataclass

from novel_forge.core.schemas.chapter import ChapterOutcome
from novel_forge.core.schemas.continuity import ChapterBridge, ContinuityReport
from novel_forge.core.schemas.story_state import ChapterExitState


@dataclass
class ContinuityValidationResult:
    """Result of continuity-specific validation checks."""

    violations: list[str]
    warnings: list[str]


class ContinuityRules:
    """Validate continuity handoff beyond base canon consistency."""

    def validate(
        self,
        *,
        previous_exit_state: ChapterExitState | None,
        bridge: ChapterBridge | None,
        continuity_report: ContinuityReport | None,
        outcome: ChapterOutcome,
        repair_exhausted: bool = False,
    ) -> ContinuityValidationResult:
        violations: list[str] = []
        warnings: list[str] = []

        if bridge is None:
            warnings.append("Missing chapter bridge artifact.")
        if outcome.chapter_exit_state is None:
            violations.append("Missing chapter_exit_state in extracted outcome.")

        if previous_exit_state is not None and bridge is not None:
            if bridge.from_chapter != previous_exit_state.chapter_number:
                violations.append(
                    "Bridge from_chapter does not match previous chapter exit state."
                )
            if not bridge.opening_pov:
                warnings.append("Bridge is missing opening_pov.")
            if (
                previous_exit_state.location
                and bridge.opening_location
                and previous_exit_state.location != bridge.opening_location
                and not bridge.action_handoff
            ):
                warnings.append(
                    "Opening location changes without an explicit action handoff."
                )
            if (
                previous_exit_state.pov
                and bridge.opening_pov
                and previous_exit_state.pov != bridge.opening_pov
                and not bridge.transition_mode
            ):
                warnings.append("POV changed without a declared transition mode.")

        if continuity_report is not None:
            unresolved_high = [
                issue.summary
                for issue in continuity_report.issues
                if issue.severity.lower() in {"high", "critical"}
            ]
            if unresolved_high:
                msg = (
                    "High-severity continuity issues remain after repair: "
                    + "；".join(unresolved_high[:3])
                )
                if repair_exhausted:
                    # 修复轮次已用尽：降级为 warning，不再阻断归档。
                    # 避免修复→失败→重规划→修复→失败的无限循环。
                    warnings.append(f"[降级] {msg}")
                else:
                    violations.append(msg)

        if outcome.chapter_exit_state is not None:
            for rel_delta in outcome.relationship_deltas:
                rel = rel_delta.relationship
                if abs(rel.trust - 0.5) > 0.45 and not rel.last_shift_event:
                    warnings.append(
                        f"Relationship '{rel_delta.pair_id}' changed sharply without last_shift_event."
                    )
            for char_delta in outcome.character_state_deltas:
                if not char_delta.change_summary:
                    warnings.append(f"Character state delta '{char_delta.name}' lacks change_summary.")

        return ContinuityValidationResult(violations=violations, warnings=warnings)
