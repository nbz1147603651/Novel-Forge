"""Workspace memory maintenance execution helpers."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from novel_forge.persistence.foundation_guard import require_versioned_maintenance_write
from novel_forge.workspace.contracts import RebuildMemoryVectorsRequest
from novel_forge.workspace.execution_result import ExecutionResult, StepCallback
from novel_forge.workspace.runtime import RuntimeServices


def require_memory_maintenance_authority(
    runtime: RuntimeServices, project_id: str, operation: str
) -> None:
    """Legacy whole-memory rewrites are not a versioned authoring command."""
    project_path = getattr(getattr(runtime, "storage", None), "project_path", None)
    root = project_path(project_id) if callable(project_path) else None
    if isinstance(root, Path):
        require_versioned_maintenance_write(root, operation)


async def execute_rebuild_memory_vectors(
    runtime: RuntimeServices,
    request: RebuildMemoryVectorsRequest,
    *,
    on_step_progress: StepCallback = None,
) -> ExecutionResult[dict[str, Any]]:
    """Rebuild vector-backed memory collections for a project."""
    project_id = request.project_id.strip()
    if not project_id:
        raise ValueError("project_id is required")
    require_memory_maintenance_authority(runtime, project_id, "重建全书记忆与表达画像")

    def emit(step: str, payload: dict[str, Any]) -> None:
        if callable(on_step_progress):
            on_step_progress(step, payload)

    emit(
        "memory_vector_rebuild_start",
        {
            "project_id": project_id,
            "include_expression": request.include_expression,
            "from_chapter": request.from_chapter,
            "to_chapter": request.to_chapter,
        },
    )

    memory_context = await runtime.get_memory_context(project_id, storage=runtime.storage)
    if memory_context is None:
        raise RuntimeError(f"Memory context unavailable for {project_id!r}")

    emit("memory_vector_rebuild_episodic_start", {"project_id": project_id})
    episodic_result = await memory_context.rebuild_vector_collection()
    emit(
        "memory_vector_rebuild_episodic_done",
        {
            "project_id": project_id,
            "rebuilt_vectors": int(episodic_result.get("rebuilt_vectors", 0) or 0),
            "backend": str(episodic_result.get("backend", "") or ""),
            "index_type": str(episodic_result.get("index_type", "") or ""),
            "saved": bool(episodic_result.get("saved", False)),
        },
    )

    expression_result: dict[str, Any] | None = None
    expression_error = ""
    if request.include_expression:
        emit(
            "memory_vector_rebuild_expression_start",
            {
                "project_id": project_id,
                "from_chapter": request.from_chapter,
                "to_chapter": request.to_chapter,
            },
        )
        try:
            expression_result = await memory_context.rebuild_expression_channel_memory(
                from_chapter=request.from_chapter,
                to_chapter=request.to_chapter,
            )
            emit(
                "memory_vector_rebuild_expression_done",
                {
                    "project_id": project_id,
                    "rebuilt_chapters": int(expression_result.get("rebuilt_chapters", 0) or 0),
                    "observations": int(expression_result.get("observations", 0) or 0),
                    "vectors": int(expression_result.get("vectors", 0) or 0),
                    "skipped": str(expression_result.get("skipped", "") or ""),
                },
            )
        except RuntimeError as exc:
            expression_error = str(exc)
            expression_result = {
                "rebuilt_chapters": 0,
                "observations": 0,
                "vectors": 0,
                "skipped": "unavailable",
                "error": expression_error,
            }
            emit(
                "memory_vector_rebuild_expression_skipped",
                {"project_id": project_id, "reason": expression_error},
            )

    result = {
        "project_id": project_id,
        "episodic": episodic_result,
        "expression": expression_result,
        "warnings": [expression_error] if expression_error else [],
    }
    emit(
        "memory_vector_rebuild_done",
        {
            "project_id": project_id,
            "rebuilt_vectors": int(episodic_result.get("rebuilt_vectors", 0) or 0),
            "expression_vectors": int((expression_result or {}).get("vectors", 0) or 0),
            "expression_skipped": str((expression_result or {}).get("skipped", "") or ""),
        },
    )
    return ExecutionResult(project_id=project_id, result=result)
