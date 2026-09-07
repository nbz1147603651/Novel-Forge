"""Workspace-backed Repair Orchestration v2 handlers."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from novel_forge.persistence.models import ProjectLayout
from novel_forge.pipeline.repair_orchestration import (
    RepairArtifactChange,
    RepairDomain,
    RepairExecutionResult,
    RepairMission,
    RepairPlanCandidate,
    RepairStrategy,
    RepairSurface,
    RepairTarget,
    RepairVerificationResult,
)
from novel_forge.pipeline.repair_orchestration.domains._shared import (
    restore_text_path,
    snapshot_text_path,
)
from novel_forge.pipeline.repair_orchestration.text_utils import text_change_ratio
from novel_forge.workspace.contracts import RepairCausalRequest, RepairContinuityRequest
from novel_forge.workspace.execution_result import StepCallback
from novel_forge.workspace.runtime import RuntimeServices


def _coerce_int_list(value: Any) -> list[int]:
    if value is None:
        return []
    if isinstance(value, int):
        return [value]
    if not isinstance(value, list):
        return []
    result: list[int] = []
    for item in value:
        try:
            result.append(int(item))
        except (TypeError, ValueError):
            continue
    return result


def _coerce_str_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _coerce_dict_list(value: Any) -> list[dict[str, Any]]:
    if value is None:
        return []
    if isinstance(value, dict):
        return [dict(value)]
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value if isinstance(item, dict)]


def _strategy_from_payload(target: RepairTarget, default: RepairStrategy) -> RepairStrategy:
    raw = target.payload.get("strategy") or target.payload.get("repair_strategy")
    if raw:
        try:
            strategy = RepairStrategy(str(raw))
            if not target.allowed_strategies or strategy in target.allowed_strategies:
                return strategy
        except ValueError:
            pass
    if target.allowed_strategies:
        return target.allowed_strategies[0]
    return default


def _chapter_number(mission: RepairMission, target: RepairTarget) -> int:
    raw = target.chapter_number
    if raw is None:
        raw = mission.source_context.get("chapter_number")
    try:
        number = int(str(raw))
    except (TypeError, ValueError) as exc:
        raise ValueError("chapter_number is required for workspace repair handlers") from exc
    if number < 1:
        raise ValueError("chapter_number must be >= 1 for workspace repair handlers")
    return number


def _target_has_repair_inputs(target: RepairTarget) -> bool:
    for key in (
        "issue_indices",
        "issue_index",
        "issue_signatures",
        "issue_signature",
        "synthetic_issues",
        "synthetic_issue",
    ):
        if target.payload.get(key):
            return True
    return False


def _should_skip_empty_target(target: RepairTarget) -> bool:
    return bool(target.payload.get("skip_empty_target", False)) and not _target_has_repair_inputs(
        target
    )


def _path_snapshot(path: Path) -> dict[str, Any]:
    return snapshot_text_path(path)


def _restore_path(snapshot: dict[str, Any]) -> None:
    restore_text_path(snapshot)


class _WorkspaceChapterRepairHandler:
    """Base class for legacy workspace chapter repair adapters."""

    name = "workspace_chapter_repair"
    domain: RepairDomain
    default_strategy = RepairStrategy.LLM_PATCH

    def __init__(
        self,
        runtime: RuntimeServices,
        *,
        on_step_progress: StepCallback = None,
        on_audit_update: Any = None,
    ) -> None:
        self._runtime = runtime
        self._on_step_progress = on_step_progress
        self._on_audit_update = on_audit_update

    def supports(self, target: RepairTarget) -> bool:
        return target.domain == self.domain and target.surface == RepairSurface.CHAPTER_TEXT

    def _layout(self, mission: RepairMission) -> ProjectLayout:
        return ProjectLayout(self._runtime.storage.existing_project_dir(mission.project_id))

    def _chapter_text_path(self, layout: ProjectLayout, chapter_number: int) -> Path:
        chapter_path = layout.chapter_path(chapter_number)
        if chapter_path.exists():
            return chapter_path
        return layout.chapter_review_draft_path(chapter_number)

    async def plan(
        self,
        mission: RepairMission,
        target: RepairTarget,
    ) -> RepairPlanCandidate:
        strategy = _strategy_from_payload(target, self.default_strategy)
        return RepairPlanCandidate(
            target_id=target.id,
            strategy=strategy,
            summary=target.summary or f"{target.domain.value} repair",
            rationale="legacy_workspace_repair_adapter",
            preview=str(target.payload.get("preview") or ""),
            estimated_change_ratio=float(target.payload.get("estimated_change_ratio") or 0.05),
            crosses_artifact_boundary=bool(target.payload.get("crosses_artifact_boundary", False)),
            verification_required=True,
        )

    async def snapshot(
        self,
        mission: RepairMission,
        target: RepairTarget,
        plan: RepairPlanCandidate,
    ) -> dict[str, Any]:
        del plan
        chapter_number = _chapter_number(mission, target)
        layout = self._layout(mission)
        paths = [
            self._chapter_text_path(layout, chapter_number),
            layout.continuity_report_path(chapter_number),
            layout.chapter_causal_report_path(chapter_number),
            layout.repair_plan_path(chapter_number),
        ]
        return {"paths": [_path_snapshot(path) for path in paths]}

    async def rollback(
        self,
        mission: RepairMission,
        target: RepairTarget,
        snapshot_payload: dict[str, Any],
    ) -> None:
        del mission, target
        for item in snapshot_payload.get("paths") or []:
            if isinstance(item, dict):
                _restore_path(item)

    async def commit(
        self,
        mission: RepairMission,
        target: RepairTarget,
        plan: RepairPlanCandidate,
        result: RepairExecutionResult,
    ) -> list[RepairArtifactChange]:
        del mission, target, plan
        return [
            RepairArtifactChange(artifact=artifact, metadata={"handler": self.name})
            for artifact in result.changed_artifacts
        ]

    def _text_change_ratio(self, before: str, after: str) -> float:
        return text_change_ratio(before, after)

    async def verify(
        self,
        mission: RepairMission,
        target: RepairTarget,
        plan: RepairPlanCandidate,
        result: RepairExecutionResult,
    ) -> RepairVerificationResult:
        del mission, target, plan
        if result.payload.get("skip_empty_target"):
            return RepairVerificationResult(
                verified=True,
                confidence=1.0,
                reason="empty_repair_target_verified_noop",
            )
        if not result.applied:
            return RepairVerificationResult(
                verified=False,
                confidence=0.0,
                reason=result.failure_reason or "legacy_repair_not_applied",
            )
        if result.failure_reason:
            return RepairVerificationResult(
                verified=False,
                confidence=0.2,
                reason=result.failure_reason,
            )
        return RepairVerificationResult(verified=True, confidence=0.8, reason="legacy_repair_applied")


class WorkspaceContinuityRepairHandler(_WorkspaceChapterRepairHandler):
    """V2 adapter for the existing continuity workspace repair entrypoint."""

    name = "workspace_continuity_repair"
    domain = RepairDomain.CONTINUITY

    async def execute(
        self,
        mission: RepairMission,
        target: RepairTarget,
        plan: RepairPlanCandidate,
    ) -> RepairExecutionResult:
        del plan
        from novel_forge.workspace.repair_ops.execution_repair_continuity import (
            _execute_chapter_continuity_repair_impl,
        )

        chapter_number = _chapter_number(mission, target)
        layout = self._layout(mission)
        text_path = self._chapter_text_path(layout, chapter_number)
        before_text = text_path.read_text(encoding="utf-8") if text_path.exists() else ""
        if _should_skip_empty_target(target):
            return RepairExecutionResult(
                applied=False,
                payload={"skip_empty_target": True},
                changed_artifacts=[],
                change_ratio=0.0,
            )
        request = RepairContinuityRequest(
            project_id=mission.project_id,
            chapter_number=chapter_number,
            issue_indices=_coerce_int_list(
                target.payload.get("issue_indices", target.payload.get("issue_index"))
            ),
            issue_signatures=_coerce_str_list(
                target.payload.get("issue_signatures", target.payload.get("issue_signature"))
            ),
            synthetic_issues=_coerce_dict_list(
                target.payload.get("synthetic_issues", target.payload.get("synthetic_issue"))
            ),
        )
        execution = await _execute_chapter_continuity_repair_impl(
            self._runtime,
            request,
            on_step_progress=self._on_step_progress,
            on_audit_update=self._on_audit_update,
        )
        legacy_result = execution.result
        after_text = text_path.read_text(encoding="utf-8") if text_path.exists() else ""
        applied = bool(getattr(legacy_result, "applied", False))
        change_override = target.payload.get("actual_change_ratio")
        change_ratio = (
            float(change_override)
            if change_override is not None
            else self._text_change_ratio(before_text, after_text)
        )
        return RepairExecutionResult(
            applied=applied,
            payload={"legacy_result_type": type(legacy_result).__name__},
            changed_artifacts=[str(text_path), str(layout.continuity_report_path(chapter_number))],
            change_ratio=change_ratio,
            warnings=list(getattr(legacy_result, "warnings", []) or []),
            failure_reason=str(getattr(legacy_result, "failure_reason", "") or ""),
        )


class WorkspaceCausalRepairHandler(_WorkspaceChapterRepairHandler):
    """V2 adapter for the existing causal workspace repair entrypoint."""

    name = "workspace_causal_repair"
    domain = RepairDomain.CAUSAL

    async def execute(
        self,
        mission: RepairMission,
        target: RepairTarget,
        plan: RepairPlanCandidate,
    ) -> RepairExecutionResult:
        del plan
        from novel_forge.workspace.repair_ops.execution_repair_causal import (
            _execute_chapter_causal_repair_impl,
        )

        chapter_number = _chapter_number(mission, target)
        layout = self._layout(mission)
        text_path = self._chapter_text_path(layout, chapter_number)
        before_text = text_path.read_text(encoding="utf-8") if text_path.exists() else ""
        if _should_skip_empty_target(target):
            return RepairExecutionResult(
                applied=False,
                payload={"skip_empty_target": True},
                changed_artifacts=[],
                change_ratio=0.0,
            )
        request = RepairCausalRequest(
            project_id=mission.project_id,
            chapter_number=chapter_number,
            issue_indices=_coerce_int_list(
                target.payload.get("issue_indices", target.payload.get("issue_index"))
            ),
            issue_signatures=_coerce_str_list(
                target.payload.get("issue_signatures", target.payload.get("issue_signature"))
            ),
            synthetic_issues=_coerce_dict_list(
                target.payload.get("synthetic_issues", target.payload.get("synthetic_issue"))
            ),
            allow_exhausted_retry=bool(target.payload.get("allow_exhausted_retry", False)),
        )
        execution = await _execute_chapter_causal_repair_impl(
            self._runtime,
            request,
            on_step_progress=self._on_step_progress,
            on_audit_update=self._on_audit_update,
        )
        legacy_result = execution.result
        after_text = text_path.read_text(encoding="utf-8") if text_path.exists() else ""
        applied = bool(getattr(legacy_result, "applied", False))
        change_override = target.payload.get("actual_change_ratio")
        change_ratio = (
            float(change_override)
            if change_override is not None
            else self._text_change_ratio(before_text, after_text)
        )
        return RepairExecutionResult(
            applied=applied,
            payload={"legacy_result_type": type(legacy_result).__name__},
            changed_artifacts=[str(text_path), str(layout.chapter_causal_report_path(chapter_number))],
            change_ratio=change_ratio,
            warnings=list(getattr(legacy_result, "warnings", []) or []),
            failure_reason=str(getattr(legacy_result, "failure_reason", "") or ""),
        )
