"""Additional chapter-level tools and utilities."""

from __future__ import annotations

from typing import Annotated, Any, cast

from fastapi import APIRouter, Depends

from novel_forge.api.deps import get_runtime_services
from novel_forge.api.run_logging import execute_api_with_run_logger
from novel_forge.workspace.contracts import (
    BookConsistencyRequest,
    BookEditorialAuditRequest,
    ExportBookRequest,
    ExtendOutlineRequest,
    PolishChapterRequest,
    ReextractRelationshipsRequest,
    SyncChapterContractsRequest,
)
from novel_forge.workspace.execution import (
    execute_book_consistency,
    execute_book_editorial_audit,
    execute_export_book,
    execute_extend_outline,
    execute_polish_chapter,
    execute_reextract_relationships,
    execute_sync_chapter_contracts,
)
from novel_forge.workspace.runtime import RuntimeServices

router = APIRouter()
RuntimeDep = Annotated[RuntimeServices, Depends(get_runtime_services)]


def _result_dict(execution: Any) -> dict[str, Any]:
    return cast(dict[str, Any], execution.result)


@router.post("/polish", response_model=dict)
async def polish_chapter(
    req: PolishChapterRequest,
    runtime: RuntimeDep,
) -> dict[str, Any]:
    """Polish a completed chapter for literary quality."""
    execution = await execute_api_with_run_logger(
        runtime,
        project_id=req.project_id,
        command="api:chapter-tools.polish",
        metadata=req.model_dump(mode="json"),
        chapter_number=req.chapter_number,
        execute=lambda on_step: execute_polish_chapter(
            runtime,
            req,
            on_step_progress=on_step,
        ),
    )
    return _result_dict(execution)


@router.post("/extend-outline", response_model=dict)
async def extend_outline(
    req: ExtendOutlineRequest,
    runtime: RuntimeDep,
) -> dict[str, Any]:
    """Append chapters to the end of an existing long-form outline."""
    execution = await execute_api_with_run_logger(
        runtime,
        project_id=req.project_id,
        command="api:chapter-tools.extend-outline",
        metadata=req.model_dump(mode="json"),
        execute=lambda on_step: execute_extend_outline(
            runtime,
            req,
            on_step_progress=on_step,
        ),
    )
    return _result_dict(execution)


@router.post("/book-consistency", response_model=dict)
async def book_consistency(
    req: BookConsistencyRequest,
    runtime: RuntimeDep,
) -> dict[str, Any]:
    """Run a whole-book consistency audit across completed chapters."""
    execution = await execute_api_with_run_logger(
        runtime,
        project_id=req.project_id,
        command="api:chapter-tools.book-consistency",
        metadata=req.model_dump(mode="json"),
        execute=lambda on_step: execute_book_consistency(
            runtime,
            req,
            on_step_progress=on_step,
        ),
    )
    return _result_dict(execution)


@router.post("/book-editorial-audit", response_model=dict)
async def book_editorial_audit(
    req: BookEditorialAuditRequest,
    runtime: RuntimeDep,
) -> dict[str, Any]:
    """Run a whole-book publication-level editorial audit."""
    execution = await execute_api_with_run_logger(
        runtime,
        project_id=req.project_id,
        command="api:chapter-tools.book-editorial-audit",
        metadata=req.model_dump(mode="json"),
        execute=lambda on_step: execute_book_editorial_audit(
            runtime,
            req,
            on_step_progress=on_step,
        ),
    )
    return _result_dict(execution)


@router.post("/export", response_model=dict)
async def export_book(
    req: ExportBookRequest,
    runtime: RuntimeDep,
) -> dict[str, Any]:
    """Export completed chapters to the requested format."""
    execution = await execute_api_with_run_logger(
        runtime,
        project_id=req.project_id,
        command="api:chapter-tools.export",
        metadata=req.model_dump(mode="json"),
        execute=lambda on_step: execute_export_book(
            runtime,
            req,
            on_step_progress=on_step,
        ),
    )
    return _result_dict(execution)


@router.post("/reextract-relationships", response_model=dict)
async def reextract_relationships(
    req: ReextractRelationshipsRequest,
    runtime: RuntimeDep,
) -> dict[str, Any]:
    """Re-extract relationship deltas from existing chapter text."""
    execution = await execute_api_with_run_logger(
        runtime,
        project_id=req.project_id,
        command="api:chapter-tools.reextract-relationships",
        metadata=req.model_dump(mode="json"),
        chapter_number=req.chapter_number if req.chapter_number > 0 else None,
        execute=lambda on_step: execute_reextract_relationships(
            runtime,
            req,
            on_step_progress=on_step,
        ),
    )
    return _result_dict(execution)


@router.post("/sync-chapter-contracts", response_model=dict)
async def sync_chapter_contracts(
    req: SyncChapterContractsRequest,
    runtime: RuntimeDep,
) -> dict[str, Any]:
    """Re-sync chapter contracts after an outline edit (local regeneration).

    Refreshes ``chapter_contracts.json`` and ``plot_milestone_index.json``
    for the affected + cascade chapter set, marks downstream artifacts
    stale, and never modifies chapter prose.
    """
    execution = await execute_api_with_run_logger(
        runtime,
        project_id=req.project_id,
        command="api:chapter-tools.sync-chapter-contracts",
        metadata=req.model_dump(mode="json"),
        execute=lambda on_step: execute_sync_chapter_contracts(
            runtime,
            req,
            on_step_progress=on_step,
        ),
    )
    return _result_dict(execution)
