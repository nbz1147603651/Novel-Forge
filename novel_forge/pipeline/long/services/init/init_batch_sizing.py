"""Model-aware batch sizing for long initialization artifacts."""

from __future__ import annotations

from typing import Any

from novel_forge.common.constants import TaskType
from novel_forge.pipeline.token_budget import route_output_limit

_OUTLINE_TASKS = frozenset({TaskType.PLAN_OUTLINE_BATCH, TaskType.PLAN_OUTLINE_CONTINUE})
_OUTLINE_LONG_PROJECT_THRESHOLD = 48
_OUTLINE_TARGET_CHARS_PER_CHAPTER = 2400


_TASK_BATCH_SIZE_SETTINGS: dict[TaskType, str] = {
    TaskType.PLAN_OUTLINE_BATCH: "outline_batch_size",
    TaskType.PLAN_OUTLINE_CONTINUE: "outline_batch_size",
    TaskType.PLAN_CHAPTER_CONTRACTS: "chapter_contract_batch_size",
}


def _positive_int_setting(settings: Any, name: str) -> int:
    if settings is None:
        return 0
    try:
        raw = getattr(settings, name)
    except Exception:
        return 0
    if isinstance(raw, bool):
        return 0
    if isinstance(raw, int):
        return max(0, raw)
    if isinstance(raw, float) and raw.is_integer():
        return max(0, int(raw))
    if isinstance(raw, str):
        raw = raw.strip()
        if not raw:
            return 0
        try:
            return max(0, int(raw))
        except ValueError:
            return 0
    return 0


def configured_init_batch_size(
    ctx: Any,
    *,
    task_type: TaskType,
    total_chapters: int,
    safety_cap: int | None = None,
) -> int:
    """Return a user-configured init batch size, or 0 when auto sizing should apply."""
    setting_name = _TASK_BATCH_SIZE_SETTINGS.get(task_type)
    if not setting_name:
        return 0
    configured = _positive_int_setting(getattr(ctx, "settings", None), setting_name)
    if configured <= 0:
        return 0
    total = max(1, int(total_chapters or 1))
    if safety_cap is not None:
        configured = min(configured, max(1, int(safety_cap)))
    return max(1, min(total, configured))


def _outline_batch_size_from_limit(output_limit: int, *, total_chapters: int) -> int:
    """Return a conservative chapter count for verbose outline JSON.

    Chapter outlines are much denser than chapter-contract records: each item
    carries plot points, beat arrays, hooks, payoffs, locations, notes, and
    character lists. Long JSON arrays are also fragile near the tail, so high
    output-capacity models should increase token headroom before they increase
    chapter count.
    """
    total = max(1, int(total_chapters or 1))
    limit = max(1, int(output_limit or 1))
    if limit >= 32768:
        size = 4
    elif limit >= 16384:
        size = 3
    else:
        size = 2
    if total > _OUTLINE_LONG_PROJECT_THRESHOLD:
        size = min(size, 3)
    return max(1, min(total, size))


def _contract_batch_size_from_limit(output_limit: int, *, total_chapters: int) -> int:
    total = max(1, int(total_chapters or 1))
    limit = max(1, int(output_limit or 1))
    if limit >= 65536 and total <= 32:
        size = 8
    elif limit >= 32768:
        size = 6
    else:
        size = 4
    return max(1, min(8, total, size))


def outline_batch_target_output_chars(chapter_count: int) -> int:
    """Return the expected character budget for one outline batch."""
    count = max(1, int(chapter_count or 1))
    return max(3600, count * _OUTLINE_TARGET_CHARS_PER_CHAPTER)


def effective_init_batch_size(
    ctx: Any,
    *,
    task_type: TaskType,
    total_chapters: int,
) -> int:
    """Return the v3 effective serial batch size for init generation.

    The pipeline keeps outline and chapter-contract batches serial for resume
    and conversation-history quality, but sizes each batch from routed model
    capacity instead of a fixed default.
    """
    total = max(1, int(total_chapters or 1))
    output_limit = route_output_limit(
        getattr(ctx, "router", None),
        task_type,
        fallback=8192,
    )

    if task_type in _OUTLINE_TASKS:
        auto_size = _outline_batch_size_from_limit(output_limit, total_chapters=total)
        configured = configured_init_batch_size(
            ctx,
            task_type=task_type,
            total_chapters=total,
            safety_cap=auto_size,
        )
        return configured or auto_size

    configured = configured_init_batch_size(
        ctx,
        task_type=task_type,
        total_chapters=total,
    )
    if configured > 0:
        return configured

    if task_type == TaskType.PLAN_CHAPTER_CONTRACTS:
        size = _contract_batch_size_from_limit(output_limit, total_chapters=total)
    else:
        size = 4
    return max(1, min(8, total, size))


def effective_outline_batch_size(ctx: Any, *, total_chapters: int) -> int:
    """Return the safe outline batch size shared by batch and continuation calls."""
    total = max(1, int(total_chapters or 1))
    batch_size = effective_init_batch_size(
        ctx,
        task_type=TaskType.PLAN_OUTLINE_BATCH,
        total_chapters=total,
    )
    continue_size = effective_init_batch_size(
        ctx,
        task_type=TaskType.PLAN_OUTLINE_CONTINUE,
        total_chapters=total,
    )
    return max(1, min(total, batch_size, continue_size))


def record_effective_init_batch_size(
    ctx: Any,
    *,
    artifact: str,
    task_type: TaskType,
    batch_size: int,
    total_chapters: int,
) -> None:
    """Record effective batch sizing in init efficiency metrics."""
    metrics = getattr(ctx, "_init_efficiency_metrics", None)
    if not isinstance(metrics, dict):
        metrics = {}
        try:
            ctx._init_efficiency_metrics = metrics
        except Exception:
            return
    sizes = metrics.setdefault("effective_batch_sizes", {})
    if isinstance(sizes, dict):
        sizes[str(artifact)] = int(batch_size)
    details = metrics.setdefault("effective_batch_size_details", {})
    if isinstance(details, dict):
        details[str(artifact)] = {
            "task": task_type.value,
            "batch_size": int(batch_size),
            "total_chapters": int(total_chapters or 0),
        }
