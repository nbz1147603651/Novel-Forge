"""Durable workspace execution for AI-assisted chapter-outline refinement."""

from __future__ import annotations

import hashlib
from typing import Any, cast

from novel_forge.core.infra.resource_locks import ResourceLockType, ResourceName
from novel_forge.core.schemas.outline import StoryOutline
from novel_forge.obs.tracer import PipelineTrace
from novel_forge.persistence.authoring_store import AuthoringStore
from novel_forge.persistence.models import ProjectLayout
from novel_forge.persistence.planning_revision import PlanningRevision
from novel_forge.persistence.polish_history import PolishHistoryRecorder
from novel_forge.persistence.project_staleness import (
    RevisionScope,
    UpstreamArtifactKind,
    record_upstream_artifact_revision,
)
from novel_forge.pipeline.steps.polish_outline_step import (
    PolishOutlineInput,
    PolishOutlineStep,
    _parse_chapter_range,
)
from novel_forge.workspace.authoring_control import planning_authority
from novel_forge.workspace.contracts import PolishOutlineRequest, SyncChapterContractsRequest
from novel_forge.workspace.execution_result import ExecutionResult, StepCallback
from novel_forge.workspace.helpers.execution_runners import (
    _project_lock,
    execute_sync_chapter_contracts,
)
from novel_forge.workspace.runtime import RuntimeServices


def _outline_hash(payload: StoryOutline) -> str:
    return hashlib.sha256(payload.model_dump_json(exclude_none=True).encode("utf-8")).hexdigest()


async def execute_polish_outline(
    runtime: RuntimeServices,
    request: PolishOutlineRequest,
    *,
    on_step_progress: StepCallback = None,
) -> ExecutionResult[dict[str, Any]]:
    layout = ProjectLayout(runtime.storage.existing_project_dir(request.project_id))
    if not layout.outline_path.exists():
        raise ValueError("项目尚未生成章节大纲，无法执行大纲润色。")
    outline = StoryOutline.model_validate(runtime.storage.load_json(layout.outline_path))
    chapters = _parse_chapter_range(request.chapter_range, total_chapters=outline.total_chapters)
    chapters = chapters or [c.chapter_number for c in outline.chapters]
    if not chapters:
        raise ValueError("项目尚未生成章节大纲，无法执行大纲润色。")
    with planning_authority(runtime, request.project_id, chapters):
        return await _execute_polish_outline(runtime, request, chapters, on_step_progress)


async def _execute_polish_outline(
    runtime: RuntimeServices,
    request: PolishOutlineRequest,
    allowed_chapters: list[int],
    on_step_progress: StepCallback,
) -> ExecutionResult[dict[str, Any]]:
    """Apply a bounded outline polish and refresh affected chapter contracts.

    Contract regeneration and staleness updates run in an isolated candidate.
    Live artifacts are published together under the project lock only on success.
    """

    project_id = request.project_id.strip()
    if not project_id:
        raise ValueError("project_id is required")

    changed_chapters: list[int] = []
    suggestions: list[str] = []
    warnings: list[str] = []
    previous_hash = ""
    revision: PlanningRevision | None = None

    async with _project_lock(
        runtime,
        project_id,
        ResourceName.CANON,
        lock_type=ResourceLockType.EXCLUSIVE,
    ):
        storage = runtime.storage
        layout = ProjectLayout(storage.existing_project_dir(project_id))
        if not storage.exists(layout.outline_path):
            raise ValueError("项目尚未生成章节大纲，无法执行大纲润色。")

        outline = StoryOutline.model_validate(storage.load_json(layout.outline_path))
        previous_hash = _outline_hash(outline)
        prepared_revision = (
            None if request.analysis_only else PlanningRevision(layout.root, project_id)
        )
        if on_step_progress is not None:
            on_step_progress(
                "outline_polish_start",
                {
                    "project_id": project_id,
                    "chapter_range": request.chapter_range,
                    "analysis_only": request.analysis_only,
                },
            )

        step = PolishOutlineStep(
            runtime.router,
            runtime.builder,
            settings=runtime.settings,
            trace=PipelineTrace(),
            on_step=on_step_progress,
        )
        step._project_id = project_id  # noqa: SLF001 - history is project-scoped by the step.
        result = await step.run(
            PolishOutlineInput(
                story_outline=outline,
                user_hint=request.user_hint,
                selected_suggestions=list(request.selected_suggestions),
                focus_fields=list(request.focus_fields),
                chapter_range=request.chapter_range or None,
                analysis_only=request.analysis_only,
                record_history=False,
            )
        )
        suggestions = list(result.polish_suggestions)
        warnings = list(result.warnings)
        changed_chapters = sorted({int(number) for number in result.changed_chapters})
        # History is append-only discussion evidence, not formal planning. Use
        # this runtime's root (never the process-global settings storage root).
        try:
            PolishHistoryRecorder(layout.root).record_outline_polish(
                user_hint=request.user_hint,
                selected_suggestions=list(request.selected_suggestions),
                focus_fields=list(request.focus_fields),
                chapter_range=request.chapter_range,
                before_outline=outline.model_dump(mode="json"),
                after_outline=result.adjusted_outline.model_dump(mode="json")
                if result.adjusted_outline is not None
                else {},
                changed_chapters=changed_chapters,
                ai_suggestions=suggestions,
                result_type="analyze" if request.analysis_only else "candidate",
            )
        except OSError as exc:
            warnings.append(f"润色历史记录失败；候选版本证据仍保留：{exc}")
        if set(changed_chapters) - set(allowed_chapters):
            raise ValueError("候选改变了本次选择范围之外的章节，未发布")

        if not request.analysis_only and changed_chapters:
            if result.adjusted_outline is None:
                raise RuntimeError("大纲润色未返回可保存的大纲。")
            if result.adjusted_outline.total_chapters != outline.total_chapters:
                raise ValueError("润色不得改变全书目标；请使用延长全书专项命令")
            revision = prepared_revision
            assert revision is not None
            candidate_layout = ProjectLayout(revision.project)
            revision.storage.save_json(
                candidate_layout.outline_path,
                result.adjusted_outline.model_dump(mode="json", exclude_none=True),
            )

    contracts: dict[str, Any] | None = None
    if revision is not None:
        contracts = await publish_outline_revision(
            runtime,
            project_id=project_id,
            revision=revision,
            changed_chapters=changed_chapters,
            previous_hash=previous_hash,
            sync_contracts=request.sync_contracts,
            on_step_progress=on_step_progress,
        )
        if on_step_progress is not None and not (contracts or {}).get("proposal_id"):
            on_step_progress("outline_polish_persisted", {"changed_chapters": changed_chapters})

    return ExecutionResult(
        project_id=project_id,
        result={
            "project_id": project_id,
            "changed_chapters": changed_chapters,
            "polish_suggestions": suggestions,
            "warnings": warnings,
            "analysis_only": request.analysis_only,
            "contracts": contracts,
            "status": "candidate" if (contracts or {}).get("proposal_id") else "completed",
            "message": "规划修改候选待作者批准；正式规划未改变"
            if (contracts or {}).get("proposal_id")
            else "大纲操作完成",
        },
    )


class _CandidateRuntime:
    def __init__(self, runtime: Any, storage: Any) -> None:
        self._runtime = runtime
        self.storage = storage

    def __getattr__(self, name: str) -> Any:
        return getattr(self._runtime, name)


async def prepare_outline_revision(
    runtime: Any,
    *,
    project_id: str,
    revision: PlanningRevision,
    changed_chapters: list[int],
    previous_hash: str,
    sync_contracts: bool = True,
    on_step_progress: StepCallback = None,
) -> dict[str, Any] | None:
    contracts = None
    if sync_contracts:
        execution = await execute_sync_chapter_contracts(
            cast(RuntimeServices, _CandidateRuntime(runtime, revision.storage)),
            SyncChapterContractsRequest(
                project_id=project_id,
                affected_chapter_numbers=changed_chapters,
                cascade_downstream=True,
                rebuild_milestones=True,
                mark_stale=True,
                prose_untouched=True,
            ),
            on_step_progress=on_step_progress,
            strict=True,
        )
        contracts = dict(execution.result)
        if contracts.get("status") != "completed":
            raise RuntimeError(f"Candidate contract synchronization failed: {contracts}")
    record_upstream_artifact_revision(
        revision.storage,
        ProjectLayout(revision.project),
        artifact_kind=UpstreamArtifactKind.OUTLINE,
        previous_hash=previous_hash,
        scope=RevisionScope.FORWARD_ONLY,
        from_chapter=min(changed_chapters),
        reason="validated_outline_revision",
    )
    revision.storage.save_json(
        revision.project / "plans" / "active_planning_revision.json",
        {"revision_id": revision.revision_id, "changed_chapters": changed_chapters},
    )
    return contracts


async def publish_outline_revision(
    runtime: Any,
    *,
    project_id: str,
    revision: PlanningRevision,
    changed_chapters: list[int],
    previous_hash: str,
    sync_contracts: bool = True,
    on_step_progress: StepCallback = None,
) -> dict[str, Any] | None:
    authority = AuthoringStore(revision.root)
    if authority.policy() is not None and not sync_contracts:
        raise ValueError("共创规划候选必须完成严格契约同步")
    contracts = await prepare_outline_revision(
        runtime,
        project_id=project_id,
        revision=revision,
        changed_chapters=changed_chapters,
        previous_hash=previous_hash,
        sync_contracts=sync_contracts,
        on_step_progress=on_step_progress,
    )
    if authority.policy() is not None:
        revision.mark_validated(changed_chapters)
        proposal_id = await propose_planning_revision(
            runtime, project_id, revision, min(changed_chapters)
        )
        return {
            **(contracts or {}),
            "status": "candidate",
            "proposal_id": proposal_id,
            "revision_id": revision.revision_id,
        }
    async with _project_lock(runtime, project_id, ResourceName.CANON):
        revision.publish()
    return contracts


async def propose_planning_revision(
    runtime: Any, project_id: str, revision: PlanningRevision, chapter: int
) -> str:
    from novel_forge.core.authoring import AuthoringProposalRequest
    from novel_forge.workspace.authoring_proposals import create_proposal

    view = await create_proposal(
        runtime,
        project_id,
        AuthoringProposalRequest(
            command="publish_planning",
            chapter_number=chapter,
            revision_id=revision.revision_id,
            title="规划修改专项批准",
            evidence=["已完成隔离候选的契约同步与领域校验；正式输入仍会在发布前重新检查"],
        ),
        proposal_id=revision.revision_id,
    )
    return view.id


async def propose_contract_sync(
    runtime: RuntimeServices,
    request: SyncChapterContractsRequest,
    *,
    on_step_progress: StepCallback = None,
) -> ExecutionResult[dict[str, Any]]:
    """The old sync button prepares the same strictly validated planning proposal."""
    root = runtime.storage.existing_project_dir(request.project_id)
    async with _project_lock(runtime, request.project_id):
        outline = StoryOutline.model_validate(
            runtime.storage.load_json(ProjectLayout(root).outline_path)
        )
        chapters = request.affected_chapter_numbers or [c.chapter_number for c in outline.chapters]
        if not chapters:
            raise ValueError("尚无可同步的章节")
        revision = PlanningRevision(root, request.project_id)
    with planning_authority(runtime, request.project_id, chapters):
        result = await publish_outline_revision(
            runtime,
            project_id=request.project_id,
            revision=revision,
            changed_chapters=chapters,
            previous_hash=_outline_hash(outline),
            sync_contracts=True,
            on_step_progress=on_step_progress,
        )
    return ExecutionResult(
        project_id=request.project_id,
        result={
            **(result or {}),
            "status": "candidate",
            "message": "契约同步候选待作者批准；正式大纲与契约保持不变",
        },
    )


__all__ = ["execute_polish_outline"]
