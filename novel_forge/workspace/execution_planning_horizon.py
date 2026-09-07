"""Publish progressive planning only after outline, contracts and slices agree."""

from __future__ import annotations

from dataclasses import asdict, replace
from typing import Any

from novel_forge.core.authoring import authoring_permission
from novel_forge.core.infra.resource_locks import ResourceName
from novel_forge.core.schemas.outline import StoryOutline
from novel_forge.persistence.authoring_store import (
    AuthoringDeniedError,
    AuthoringStore,
    content_version,
)
from novel_forge.persistence.models import ProjectLayout
from novel_forge.persistence.planning_revision import PlanningRevision
from novel_forge.pipeline.long.services.blueprint.outline_helpers import is_outline_complete
from novel_forge.pipeline.long.services.future_planning import (
    protected_chapters,
    validate_protected_contracts,
)
from novel_forge.workspace.execution_outline_polish import (
    _CandidateRuntime,
    _outline_hash,
    prepare_outline_revision,
)
from novel_forge.workspace.helpers.execution_runners import _project_lock
from novel_forge.workspace.planning_horizon import (
    PlanningHorizonAdvance,
    advance_planning_horizon_locked,
    next_planning_horizon_target,
)


async def ensure_chapter_planning(
    runtime: Any, *, project_id: str, chapter_number: int, on_step_progress: Any = None
) -> None:
    """Retry a failed future expansion before a chapter reaches the old hard stop."""
    storage = getattr(runtime, "storage", None)
    if storage is None or not callable(getattr(storage, "existing_project_dir", None)):
        return
    async with _project_lock(runtime, project_id, ResourceName.CANON):
        layout = ProjectLayout(storage.existing_project_dir(project_id))
        if not storage.exists(layout.outline_path):
            return
        outline_data = storage.load_json(layout.outline_path)
        # Legacy outlines are fully committed; their normal preflight owns
        # validation. Only explicit progressive watermarks require this repair.
        if not isinstance(outline_data, dict) or not outline_data.get("hard_through_chapter"):
            return
        outline = StoryOutline.model_validate(outline_data)
        hard = int(outline.hard_through_chapter or outline.total_chapters)
        if chapter_number <= hard or chapter_number > outline.total_chapters:
            return
        target = min(outline.total_chapters, max(chapter_number, hard + 5))
    from novel_forge.workspace.planning_jobs import PlanningTaskPending, request_planning_horizon

    state = await request_planning_horizon(
        runtime,
        project_id=project_id,
        current_chapter=max(0, chapter_number - 1),
        target_chapter=target,
        on_step_progress=on_step_progress,
    )
    if state and state["status"] != "published":
        # Release the chapter worker's queue slot instead of waiting inside it.
        raise PlanningTaskPending(project_id, chapter_number, state)


async def advance_planning_horizon(
    runtime: Any,
    *,
    project_id: str,
    current_chapter: int = 0,
    target_chapter: int | None = None,
    on_step_progress: Any = None,
    explicit: bool = False,
    planning_request_id: str = "",
) -> PlanningHorizonAdvance | None:
    """Prepare an isolated range; failure leaves every live planning artifact intact."""
    async with _project_lock(runtime, project_id, ResourceName.CANON):
        layout = ProjectLayout(runtime.storage.existing_project_dir(project_id))
        if not runtime.storage.exists(layout.outline_path):
            return None
        original = StoryOutline.model_validate(runtime.storage.load_json(layout.outline_path))
        hard = int(original.hard_through_chapter or original.total_chapters)
        target = (
            target_chapter
            if target_chapter is not None
            else next_planning_horizon_target(
                total_chapters=original.total_chapters,
                hard_through_chapter=hard,
                current_chapter=current_chapter,
            )
        )
        if target is None:
            return None
        if target == hard:
            if not is_outline_complete(
                original.model_copy(update={"planned_through_chapter": target})
            ):
                raise ValueError("大纲实际内容与规划边界不一致，请先继续初始化恢复缺失章节。")
            return None
        if not hard < target <= original.total_chapters:
            raise ValueError("规划补齐只能在当前全书目标章数内进行。")
        author_policy = AuthoringStore(layout.root).policy()
        if author_policy is not None:
            for chapter in range(hard + 1, target + 1):
                allowed = authoring_permission(
                    author_policy, "plan_candidates", chapter, explicit=explicit
                )
                if not allowed.allowed:
                    raise AuthoringDeniedError(allowed.reason)
        policy_path = layout.plans_dir / "planning_policy.json"
        policy = runtime.storage.load_json(policy_path) if policy_path.exists() else {}
        protected = protected_chapters(layout.root, policy) | set(range(1, hard + 1))
        if protected.intersection(range(hard + 1, target + 1)):
            raise ValueError("待补齐范围含已成稿或人工锁定章节，请先核对规划边界。")
        old_contracts = runtime.storage.load_json(layout.plans_dir / "chapter_contracts.json")
        revision = PlanningRevision(layout.root, project_id)

    if on_step_progress is not None:
        on_step_progress("planning_horizon_start", {"from_chapter": hard + 1, "to_chapter": target})
    candidate_runtime = _CandidateRuntime(runtime, revision.storage)

    # The existing generator writes checkpoints; keep all of them inside the
    # candidate too. Never announce advancement before contract validation.
    def candidate_progress(step: str, payload: Any) -> None:
        if step != "planning_horizon_advanced" and on_step_progress is not None:
            on_step_progress(step, payload)

    advance = await advance_planning_horizon_locked(
        candidate_runtime,
        project_id=project_id,
        current_chapter=current_chapter,
        target_chapter=target,
        on_step_progress=candidate_progress,
    )
    if advance is None:
        raise RuntimeError("规划补齐未生成目标范围。")
    candidate_layout = ProjectLayout(revision.project)
    candidate = StoryOutline.model_validate(
        revision.storage.load_json(candidate_layout.outline_path)
    )
    before = {chapter.chapter_number: chapter for chapter in original.chapters}
    after = {chapter.chapter_number: chapter for chapter in candidate.chapters}
    if candidate.total_chapters != original.total_chapters or any(
        before.get(number) != after.get(number) for number in protected
    ):
        raise RuntimeError("规划补齐改变了全书目标或已确认章节，未发布候选。")
    await prepare_outline_revision(
        runtime,
        project_id=project_id,
        revision=revision,
        changed_chapters=list(advance.affected_chapters),
        previous_hash=_outline_hash(original),
        on_step_progress=on_step_progress,
    )
    validate_protected_contracts(
        old_contracts,
        revision.storage.load_json(candidate_layout.plans_dir / "chapter_contracts.json"),
        protected,
    )
    from novel_forge.workspace.execution_extend_outline import _refresh_new_source_slices

    refreshed, warnings = _refresh_new_source_slices(
        revision.storage,
        candidate_layout,
        project_id=project_id,
        chapter_numbers=list(advance.affected_chapters),
    )
    if warnings or refreshed != len(advance.affected_chapters):
        raise RuntimeError(f"规划来源投影未完整刷新：{warnings}")
    advance = replace(
        advance,
        revision_id=revision.revision_id,
        published=False,
        candidate_version=content_version(revision.changes()),
    )
    revision.mark_validated(list(advance.affected_chapters))
    if planning_request_id:
        from novel_forge.workspace.planning_jobs import record_planning_candidate

        record_planning_candidate(layout.root, planning_request_id, asdict(advance))
    async with _project_lock(runtime, project_id, ResourceName.CANON):
        current_policy = AuthoringStore(layout.root).policy()
        if author_policy != current_policy:
            raise AuthoringDeniedError("规划期间授权已变化；候选保留，未发布")
        if (
            current_policy is not None
            and not authoring_permission(
                current_policy,
                "publish_planning",
                target,
                major_change=target == original.total_chapters,
            ).allowed
        ):
            return advance
        revision.publish(automatic_horizon=True, authoring_chapter=target)
    if on_step_progress is not None:
        on_step_progress(
            "planning_horizon_advanced",
            {
                "previous_hard_through": hard,
                "hard_through_chapter": advance.hard_through_chapter,
                "planned_through_chapter": advance.planned_through_chapter,
                "generated_chapters": list(advance.generated_chapters),
            },
        )
    return replace(advance, published=True)
