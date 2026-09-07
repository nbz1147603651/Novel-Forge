from __future__ import annotations

from types import SimpleNamespace

from novel_forge.core.constants import TaskType
from novel_forge.pipeline.long.services.init.init_batch_sizing import (
    effective_init_batch_size,
    effective_outline_batch_size,
    outline_batch_target_output_chars,
)


class _Router:
    def __init__(self, output_limit: int | dict[TaskType, int]) -> None:
        self.output_limit = output_limit

    def output_limit_for_task(self, task_type, **_kwargs) -> int:
        if isinstance(self.output_limit, dict):
            return self.output_limit.get(task_type, 8192)
        return self.output_limit


def test_effective_init_batch_size_keeps_low_context_outline_at_two() -> None:
    ctx = SimpleNamespace(router=_Router(8192))

    assert (
        effective_init_batch_size(
            ctx,
            task_type=TaskType.PLAN_OUTLINE_BATCH,
            total_chapters=24,
        )
        == 2
    )


def test_effective_init_batch_size_uses_four_for_high_context_outline() -> None:
    ctx = SimpleNamespace(router=_Router(32768))

    assert (
        effective_init_batch_size(
            ctx,
            task_type=TaskType.PLAN_OUTLINE_BATCH,
            total_chapters=24,
        )
        == 4
    )


def test_effective_init_batch_size_uses_six_for_high_context_models() -> None:
    ctx = SimpleNamespace(router=_Router(32768))

    assert (
        effective_init_batch_size(
            ctx,
            task_type=TaskType.PLAN_CHAPTER_CONTRACTS,
            total_chapters=24,
        )
        == 6
    )


def test_effective_init_batch_size_caps_long_outline_batches() -> None:
    ctx = SimpleNamespace(router=_Router(65536))

    assert (
        effective_init_batch_size(
            ctx,
            task_type=TaskType.PLAN_OUTLINE_BATCH,
            total_chapters=70,
        )
        == 3
    )


def test_effective_init_batch_size_caps_long_outline_continue_batches() -> None:
    ctx = SimpleNamespace(router=_Router(65536))

    assert (
        effective_init_batch_size(
            ctx,
            task_type=TaskType.PLAN_OUTLINE_CONTINUE,
            total_chapters=70,
        )
        == 3
    )


def test_effective_init_batch_size_uses_eight_for_ultra_context_short_projects() -> None:
    ctx = SimpleNamespace(router=_Router(65536))

    assert (
        effective_init_batch_size(
            ctx,
            task_type=TaskType.PLAN_CHAPTER_CONTRACTS,
            total_chapters=24,
        )
        == 8
    )


def test_effective_init_batch_size_clamps_outline_override_to_safety_cap() -> None:
    ctx = SimpleNamespace(router=_Router(65536), settings=SimpleNamespace(outline_batch_size=7))

    assert (
        effective_init_batch_size(
            ctx,
            task_type=TaskType.PLAN_OUTLINE_BATCH,
            total_chapters=70,
        )
        == 3
    )


def test_effective_init_batch_size_allows_smaller_outline_override() -> None:
    ctx = SimpleNamespace(router=_Router(65536), settings=SimpleNamespace(outline_batch_size=2))

    assert (
        effective_init_batch_size(
            ctx,
            task_type=TaskType.PLAN_OUTLINE_BATCH,
            total_chapters=70,
        )
        == 2
    )


def test_effective_outline_batch_size_uses_most_constrained_outline_route() -> None:
    ctx = SimpleNamespace(
        router=_Router(
            {
                TaskType.PLAN_OUTLINE_BATCH: 65536,
                TaskType.PLAN_OUTLINE_CONTINUE: 8192,
            }
        )
    )

    assert effective_outline_batch_size(ctx, total_chapters=70) == 2


def test_outline_batch_target_output_chars_tracks_chapter_count() -> None:
    assert outline_batch_target_output_chars(1) == 3600
    assert outline_batch_target_output_chars(3) == 7200


def test_effective_init_batch_size_uses_contract_override_when_configured() -> None:
    ctx = SimpleNamespace(
        router=_Router(65536),
        settings=SimpleNamespace(chapter_contract_batch_size=5),
    )

    assert (
        effective_init_batch_size(
            ctx,
            task_type=TaskType.PLAN_CHAPTER_CONTRACTS,
            total_chapters=24,
        )
        == 5
    )


def test_effective_init_batch_size_clamps_override_to_total_chapters() -> None:
    ctx = SimpleNamespace(
        router=_Router(8192),
        settings=SimpleNamespace(chapter_contract_batch_size=12),
    )

    assert (
        effective_init_batch_size(
            ctx,
            task_type=TaskType.PLAN_CHAPTER_CONTRACTS,
            total_chapters=3,
        )
        == 3
    )
