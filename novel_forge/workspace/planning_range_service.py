"""Shared post-generation service for manual and automatic planning ranges."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from novel_forge.core.schemas.outline import StoryOutline
from novel_forge.persistence.models import ProjectLayout
from novel_forge.workspace.execution_result import StepCallback


@dataclass(frozen=True)
class PlanningRangeFinalization:
    chapter_numbers: tuple[int, ...]
    contracts_result: dict[str, Any]
    source_slices_refreshed: int
    warnings: tuple[str, ...]


async def sync_validate_refresh_planning_range(
    runtime: Any,
    *,
    project_id: str,
    chapter_numbers: list[int] | tuple[int, ...],
    source_slice_chapter_numbers: list[int] | tuple[int, ...] | None = None,
    cascade_downstream: bool,
    require_completed: bool = True,
    sync_executor: Any = None,
    slice_refresher: Any = None,
    on_step_progress: StepCallback = None,
) -> PlanningRangeFinalization:
    """Synchronize contracts, verify range coverage, then refresh source slices."""

    from novel_forge.workspace.contracts import SyncChapterContractsRequest
    if sync_executor is None:
        from novel_forge.workspace.helpers.execution_runners import (
            execute_sync_chapter_contracts,
        )

        sync_executor = execute_sync_chapter_contracts
    if slice_refresher is None:
        from novel_forge.workspace.execution_extend_outline import (
            _refresh_new_source_slices,
        )

        slice_refresher = _refresh_new_source_slices

    numbers = tuple(sorted({int(number) for number in chapter_numbers if int(number) > 0}))
    if not numbers:
        raise ValueError("planning range must include at least one chapter")
    layout = ProjectLayout(runtime.storage.existing_project_dir(project_id))
    outline = StoryOutline.model_validate(runtime.storage.load_json(layout.outline_path))
    available = {int(chapter.chapter_number) for chapter in outline.chapters}
    missing = sorted(set(numbers) - available)
    if missing:
        raise RuntimeError(f"planning range outline coverage incomplete: {missing}")
    hard = int(outline.hard_through_chapter or outline.total_chapters)
    if max(numbers) > hard:
        raise RuntimeError(
            f"planning range exceeds hard waterline: max={max(numbers)}, hard={hard}"
        )

    sync_execution = await sync_executor(
        runtime,
        SyncChapterContractsRequest(
            project_id=project_id,
            affected_chapter_numbers=list(numbers),
            cascade_downstream=cascade_downstream,
            rebuild_milestones=True,
            mark_stale=True,
            prose_untouched=True,
        ),
        on_step_progress=on_step_progress,
    )
    contracts_result = dict(sync_execution.result or {})
    if contracts_result.get("status") != "completed":
        if require_completed:
            raise RuntimeError("planning range chapter contract synchronization did not complete")
        return PlanningRangeFinalization(
            chapter_numbers=numbers,
            contracts_result=contracts_result,
            source_slices_refreshed=0,
            warnings=(
                "chapter contract sync did not complete; retry sync-chapter-contracts",
            ),
        )

    refresh_numbers = (
        list(numbers)
        if source_slice_chapter_numbers is None
        else sorted(
            {
                int(number)
                for number in source_slice_chapter_numbers
                if int(number) > 0
            }
        )
    )
    refreshed, warnings = slice_refresher(
        runtime.storage,
        layout,
        project_id=project_id,
        chapter_numbers=refresh_numbers,
    )
    return PlanningRangeFinalization(
        chapter_numbers=numbers,
        contracts_result=contracts_result,
        source_slices_refreshed=refreshed,
        warnings=tuple(warnings),
    )


__all__ = (
    "PlanningRangeFinalization",
    "sync_validate_refresh_planning_range",
)
