"""VolumeAuditStep — generate volume-end summary/audit and bridge plan."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from novel_forge.core.constants import TaskType
from novel_forge.core.schemas.outline import VolumeOutline
from novel_forge.core.schemas.volume import VolumeAuditReport
from novel_forge.pipeline.steps.base import PipelineStep

if TYPE_CHECKING:
    from novel_forge.pipeline.steps.book_consistency_step import ConsistencyIssue


def aggregate_volume_issues(reports: list[VolumeAuditReport]) -> list["ConsistencyIssue"]:
    from novel_forge.pipeline.steps.book_consistency_step import ConsistencyIssue

    merged: list[ConsistencyIssue] = []
    for report in reports:
        for item in report.consistency_issues:
            if isinstance(item, dict):
                merged.append(VolumeAuditStep._to_consistency_issue(item))
            elif isinstance(item, ConsistencyIssue):
                merged.append(item)
    return merged


@dataclass
class VolumeAuditInput:
    """Input for volume-end audit."""

    volume: VolumeOutline
    story_synopsis: str
    chapter_summaries: list[dict[str, Any]]
    timeline_events: list[dict[str, Any]]
    active_characters: list[dict[str, Any]]
    active_foreshadowing: list[dict[str, Any]]
    world_fact_keys: list[str]
    blueprint_phases: list[dict[str, Any]] = field(default_factory=list)
    blueprint_arc_milestones: list[dict[str, Any]] = field(default_factory=list)
    episodic_context: dict[str, Any] | None = None
    kernel_context: dict[str, Any] | None = None


class VolumeAuditStep(PipelineStep[VolumeAuditInput, VolumeAuditReport]):
    """Run a volume-level audit and bridge planning prompt."""

    _LIST_STR_FIELDS = (
        "carry_over_characters",
        "retire_characters",
        "carry_over_items",
        "retire_items",
        "carry_over_world_fact_keys",
        "retire_world_fact_keys",
        "carry_over_foreshadowing_ids",
        "resolved_foreshadowing_ids",
        "token_optimization_notes",
    )

    @property
    def step_name(self) -> str:
        return "volume_audit"

    @staticmethod
    def _to_str_list(value: Any) -> list[str]:
        if isinstance(value, list):
            return [str(v).strip() for v in value if v is not None and str(v).strip()]
        if isinstance(value, dict):
            return [str(v).strip() for v in value.values() if v is not None and str(v).strip()]
        if isinstance(value, str) and value.strip():
            return [value.strip()]
        return []

    @classmethod
    def _normalize_volume_payload(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        result = dict(data)
        for field_name in cls._LIST_STR_FIELDS:
            if field_name in result:
                result[field_name] = cls._to_str_list(result[field_name])
        if "consistency_issues" in result:
            result["consistency_issues"] = cls._normalize_consistency_issues(
                result["consistency_issues"]
            )
        ms = result.get("milestone_status", [])
        if isinstance(ms, dict):
            result["milestone_status"] = list(ms.values())
        elif not isinstance(ms, list):
            result["milestone_status"] = []
        return result

    @staticmethod
    def _normalize_consistency_issues(items: list[Any]) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for item in items:
            if isinstance(item, dict):
                result.append(item)
            elif isinstance(item, str) and item.strip():
                result.append(
                    {
                        "category": "narrative_drift",
                        "severity": "warning",
                        "chapters_involved": [],
                        "description": str(item),
                    }
                )
        return result

    @staticmethod
    def _to_consistency_issue(issue_dict: dict[str, Any]) -> "ConsistencyIssue":
        from novel_forge.pipeline.steps.book_consistency_step import ConsistencyIssue

        chapters = issue_dict.get("chapters_involved", [])
        if not isinstance(chapters, list):
            chapters = []
        try:
            chapters = [int(c) for c in chapters]
        except (TypeError, ValueError):
            chapters = []

        primary = 0
        try:
            primary = int(issue_dict.get("primary_chapter", 0) or 0)
        except (TypeError, ValueError):
            pass
        if primary <= 0 and chapters:
            primary = chapters[0]

        span = issue_dict.get("paragraph_span", [])
        if not isinstance(span, list):
            span = []

        _raw_p = issue_dict.get("paragraph_index", 0) or 0
        if isinstance(_raw_p, list):
            _raw_p = _raw_p[0] if _raw_p else 0
        try:
            p_idx = int(_raw_p)
        except (TypeError, ValueError):
            p_idx = 0

        try:
            confidence = float(issue_dict.get("confidence", 0.0) or 0.0)
        except (TypeError, ValueError):
            confidence = 0.0

        linked = issue_dict.get("linked_issue_refs", [])
        if not isinstance(linked, list):
            linked = []

        return ConsistencyIssue(
            issue_id=str(issue_dict.get("issue_id", "") or ""),
            category=str(issue_dict.get("category") or issue_dict.get("issue_type") or "unknown"),
            severity=str(issue_dict.get("severity") or "warning"),
            chapters_involved=chapters,
            description=str(issue_dict.get("description") or issue_dict.get("evidence") or ""),
            suggestion=str(issue_dict.get("suggestion", "") or ""),
            issue_type=str(issue_dict.get("issue_type", "") or ""),
            primary_chapter=primary,
            location=str(issue_dict.get("location", "") or ""),
            paragraph_index=p_idx,
            paragraph_span=span,
            evidence=str(issue_dict.get("evidence", "") or ""),
            fix_mode=str(issue_dict.get("fix_mode", "repair_continuity") or "repair_continuity"),
            fix_action=str(issue_dict.get("fix_action", "rewrite") or "rewrite"),
            confidence=confidence,
            verification_status=str(issue_dict.get("verification_status", "") or ""),
            linked_issue_refs=[r for r in linked if isinstance(r, dict)],
        )

    async def _execute(self, input_data: VolumeAuditInput) -> VolumeAuditReport:
        payload: dict[str, Any] = {}
        if input_data.kernel_context is not None:
            payload.update(input_data.kernel_context)
        payload.update({
            "volume": input_data.volume,
            "story_synopsis": input_data.story_synopsis,
            "chapter_summaries": input_data.chapter_summaries,
            "timeline_events": input_data.timeline_events,
            "active_characters": input_data.active_characters,
            "active_foreshadowing": input_data.active_foreshadowing,
            "world_fact_keys": input_data.world_fact_keys,
            "blueprint_phases": input_data.blueprint_phases,
            "blueprint_arc_milestones": input_data.blueprint_arc_milestones,
        })
        if input_data.episodic_context is not None:
            payload["episodic_context"] = input_data.episodic_context
        data = await self._call_with_retry(
            TaskType.VOLUME_AUDIT,
            payload,
            max_tokens=self._dynamic_max_tokens(
                TaskType.VOLUME_AUDIT,
                max(2500, len(input_data.chapter_summaries) * 450),
                prompt_overhead=3000,
                min_tokens=4096,
            ),
            temperature=self.settings.temp_volume_audit,
        )
        return VolumeAuditReport.model_validate(self._normalize_volume_payload(data))
