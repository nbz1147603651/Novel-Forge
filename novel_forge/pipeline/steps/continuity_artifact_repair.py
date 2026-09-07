"""Deterministic artifact repair for continuity-owned structured files."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from novel_forge.core.schemas.continuity import CausalLink, ChapterBridge
from novel_forge.core.utils.field_extractor import field as extract_field


@dataclass(frozen=True)
class ArtifactOperation:
    """One deterministic artifact update."""

    surface: str
    path: str
    old_value: Any
    new_value: Any
    reason: str


@dataclass(frozen=True)
class BridgeArtifactRepairResult:
    """Result of repairing bridge metadata."""

    revised_bridge: ChapterBridge
    applied: bool = False
    operations: tuple[ArtifactOperation, ...] = field(default_factory=tuple)
    skipped_reason: str = ""


class BridgeArtifactRepairer:
    """Repair bridge contract drift using structured state, not story text."""

    _BRIDGE_SURFACE = "bridge_artifact"
    _BRIDGE_FIELD_HINTS = frozenset(
        {
            "from_chapter",
            "to_chapter",
            "opening_pov",
            "opening_location",
            "opening_time",
            "transition_mode",
            "action_handoff",
            "emotional_carryover",
            "causal_link",
            "bridge",
            "桥接",
            "视角",
            "pov",
            "地点",
            "时间",
            "动作接力",
            "情绪承接",
            "因果接力",
        }
    )

    @classmethod
    def _issue_surface(cls, issue: Any) -> str:
        return str(extract_field(issue, "repair_surface", "") or "").strip().lower()

    @classmethod
    def _issue_blob(cls, issues: list[Any]) -> str:
        parts: list[str] = []
        for issue in issues:
            parts.extend(
                str(extract_field(issue, key, "") or "")
                for key in ("issue_type", "summary", "evidence", "evidence_quote")
            )
            actions = extract_field(issue, "fix_actions", [])
            if isinstance(actions, list):
                parts.extend(str(item or "") for item in actions)
        return " ".join(parts).lower()

    @classmethod
    def _mentions(cls, blob: str, *markers: str) -> bool:
        return any(marker.lower() in blob for marker in markers)

    @classmethod
    def _clean(cls, value: Any) -> str:
        return str(value or "").strip()

    @classmethod
    def _owns_bridge_artifact(cls, issue: Any) -> bool:
        if cls._issue_surface(issue) == cls._BRIDGE_SURFACE:
            return True
        issue_type = str(extract_field(issue, "issue_type", "") or "").strip().lower()
        if issue_type not in {"bridge_contract_not_followed", "pov_jump"}:
            return False
        blob = cls._issue_blob([issue])
        return any(hint in blob for hint in cls._BRIDGE_FIELD_HINTS)

    @classmethod
    def repair(
        cls,
        *,
        bridge: ChapterBridge,
        chapter_state_packet: Any,
        chapter_outline: Any | None,
        issues: list[Any],
    ) -> BridgeArtifactRepairResult:
        """Return an updated bridge when open issues belong to bridge metadata."""

        bridge_issues = [
            issue
            for issue in issues
            if cls._owns_bridge_artifact(issue)
            and str(extract_field(issue, "status", "open") or "open").strip().lower() == "open"
        ]
        if not bridge_issues:
            return BridgeArtifactRepairResult(
                revised_bridge=bridge,
                applied=False,
                skipped_reason="no_bridge_artifact_issues",
            )

        previous_exit = extract_field(chapter_state_packet, "previous_exit_state", None)
        updates: dict[str, Any] = {}
        operations: list[ArtifactOperation] = []
        issue_blob = cls._issue_blob(bridge_issues)

        def set_field(path: str, value: Any, reason: str) -> None:
            old = extract_field(bridge, path, None)
            if value is None or value == "" or old == value:
                return
            updates[path] = value
            operations.append(
                ArtifactOperation(
                    surface=cls._BRIDGE_SURFACE,
                    path=path,
                    old_value=old,
                    new_value=value,
                    reason=reason,
                )
            )

        previous_chapter = extract_field(previous_exit, "chapter_number", 0)
        if (
            previous_chapter
            and bridge.to_chapter
            and previous_chapter < bridge.to_chapter
            and bridge.from_chapter != previous_chapter
        ):
            set_field(
                "from_chapter",
                previous_chapter,
                "bridge.from_chapter must match previous_exit_state.chapter_number",
            )

        outline_pov = str(extract_field(chapter_outline, "pov_character", "") or "").strip()
        if outline_pov and bridge.opening_pov != outline_pov:
            set_field(
                "opening_pov",
                outline_pov,
                "bridge.opening_pov follows the current chapter outline POV",
            )

        outline_location = cls._clean(extract_field(chapter_outline, "setting", ""))
        if outline_location and (
            not cls._clean(bridge.opening_location)
            or cls._mentions(issue_blob, "opening_location", "地点", "location", "场景")
        ):
            set_field(
                "opening_location",
                outline_location,
                "bridge.opening_location follows the current chapter outline setting",
            )

        previous_time = cls._clean(extract_field(previous_exit, "time_marker", ""))
        if previous_time and (
            not cls._clean(bridge.opening_time)
            or cls._mentions(issue_blob, "opening_time", "时间", "time")
        ):
            set_field(
                "opening_time",
                previous_time,
                "bridge.opening_time falls back to the previous exit time marker",
            )

        previous_emotion = cls._clean(extract_field(previous_exit, "emotional_state", ""))
        if previous_emotion and (
            not cls._clean(bridge.emotional_carryover)
            or cls._mentions(issue_blob, "emotional_carryover", "情绪承接")
        ):
            set_field(
                "emotional_carryover",
                previous_emotion,
                "bridge.emotional_carryover follows previous_exit_state.emotional_state",
            )

        active_goals = extract_field(previous_exit, "active_goals", []) or []
        first_goal = cls._clean(active_goals[0]) if isinstance(active_goals, list) and active_goals else ""
        action_handoff_candidate = (
            cls._clean(extract_field(extract_field(bridge, "causal_link", None), "causal_mechanism", ""))
            or cls._clean(bridge.bridge_summary)
            or first_goal
        )
        if action_handoff_candidate and (
            not cls._clean(bridge.action_handoff)
            or cls._mentions(issue_blob, "action_handoff", "动作接力")
        ):
            set_field(
                "action_handoff",
                action_handoff_candidate,
                "bridge.action_handoff is reconstructed from causal/summary/exit action context",
            )

        open_questions = extract_field(previous_exit, "open_questions", []) or []
        unresolved_question = (
            cls._clean(open_questions[0])
            if isinstance(open_questions, list) and open_questions
            else ""
        )
        if bridge.causal_link is None and (
            cls._mentions(issue_blob, "causal_link", "因果接力")
            or cls._clean(bridge.bridge_summary)
            or unresolved_question
        ):
            causal_link = CausalLink(
                previous_event=cls._clean(bridge.bridge_summary) or first_goal,
                causal_mechanism=action_handoff_candidate,
                unresolved_question=unresolved_question,
            )
            if any(
                (
                    causal_link.previous_event,
                    causal_link.causal_mechanism,
                    causal_link.unresolved_question,
                )
            ):
                set_field(
                    "causal_link",
                    causal_link,
                    "bridge.causal_link is reconstructed from bridge summary and previous exit state",
                )

        opening_pov = str(updates.get("opening_pov") or bridge.opening_pov or "").strip()
        previous_pov = str(extract_field(previous_exit, "pov", "") or "").strip()
        transition_mode = str(bridge.transition_mode or "").strip().lower()
        if previous_pov and opening_pov and previous_pov != opening_pov:
            if transition_mode != "pov_switch":
                set_field(
                    "transition_mode",
                    "pov_switch",
                    "different previous/opening POV requires an explicit POV-switch transition",
                )
        elif not transition_mode and previous_chapter:
            set_field(
                "transition_mode",
                "action_handoff",
                "missing transition_mode defaults to action_handoff for non-initial chapters",
            )

        if not operations:
            return BridgeArtifactRepairResult(
                revised_bridge=bridge,
                applied=False,
                skipped_reason="bridge_artifact_already_consistent",
            )

        return BridgeArtifactRepairResult(
            revised_bridge=ChapterBridge.model_validate(
                bridge.model_copy(update=updates).model_dump(mode="json")
            ),
            applied=True,
            operations=tuple(operations),
        )
