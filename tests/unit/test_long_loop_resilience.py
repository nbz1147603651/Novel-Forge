"""Regression tests for long chapter loop resilience helpers."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from novel_forge.pipeline.long.loop import (
    _apply_replan_notes_to_bundle,
    _format_replan_notes,
    _memory_pending_snapshot,
)


async def test_preflight_releases_kernel_store_on_load_failure(tmp_storage, monkeypatch) -> None:
    from novel_forge.pipeline.long import preflight

    tmp_storage.project_dir("failed-preflight")
    store = SimpleNamespace(close=AsyncMock())
    monkeypatch.setattr(preflight, "StoryKernelStore", lambda _path: store)
    monkeypatch.setattr(
        preflight, "_prepare_long_project", AsyncMock(side_effect=ValueError("invalid artifacts"))
    )
    with pytest.raises(ValueError, match="invalid artifacts"):
        await preflight.prepare_long_project(
            storage=tmp_storage, project_id="failed-preflight", chapter_number=1
        )
    store.close.assert_awaited_once()


@pytest.mark.parametrize("failed", [False, True])
async def test_long_run_releases_kernel_store_after_pipeline(failed, monkeypatch) -> None:
    from novel_forge.pipeline.long import loop

    ctx = SimpleNamespace(_storage=object())
    runner = SimpleNamespace(create_execution_context=lambda: ctx)
    bundle = SimpleNamespace(canon_store=SimpleNamespace(close=AsyncMock()))
    monkeypatch.setattr(loop, "_check_and_compensate_memory_gap", AsyncMock())
    monkeypatch.setattr(loop, "_detect_kernel_persist_pending_marker", lambda *_args: None)
    monkeypatch.setattr(loop, "_init_readiness_exists", lambda *_args: True)
    monkeypatch.setattr(loop, "prepare_long_project", AsyncMock(return_value=bundle))
    execute = AsyncMock(side_effect=RuntimeError("generation failed") if failed else None)
    monkeypatch.setattr(loop, "_run_prepared_long_chapter", execute)
    if failed:
        with pytest.raises(RuntimeError, match="generation failed"):
            await loop.run_long_chapter(runner, "project", 1)
    else:
        assert await loop.run_long_chapter(runner, "project", 1) is execute.return_value
    bundle.canon_store.close.assert_awaited_once()


def test_replan_notes_are_deduped_and_bounded() -> None:
    notes = _format_replan_notes("", ["重复问题", "旧问题"])
    notes = _format_replan_notes(notes, ["重复问题", "新问题", *[f"问题{i}" for i in range(20)]])

    assert notes.count("重复问题") == 1
    assert "新问题" in notes
    assert len([line for line in notes.splitlines() if line.startswith("- ")]) <= 8
    assert len(notes) <= 1900


def test_apply_replan_notes_replaces_previous_auto_block() -> None:
    bundle = SimpleNamespace(
        chapter_outline=SimpleNamespace(
            notes="人工备注\n\n【自动修复提示】上一轮正文在一致性校验未通过，请在本次方案里明确落实：\n- 旧问题",
            goal="推进主线\n补充约束：【自动修复提示】上一轮正文在一致性校验未通过，请在本次方案里明确落实：\n- 旧问题",
        )
    )
    new_notes = _format_replan_notes("", ["opening_causal_gap：开场缺少因果接续"])

    _apply_replan_notes_to_bundle(bundle, new_notes)

    assert "人工备注" in bundle.chapter_outline.notes
    assert "旧问题" not in bundle.chapter_outline.notes
    assert "旧问题" not in bundle.chapter_outline.goal
    assert bundle.chapter_outline.notes.count("【自动修复提示】") == 1
    assert bundle.chapter_outline.goal.count("【自动修复提示】") == 1
    assert "因果链约束" in bundle.chapter_outline.goal


def test_memory_pending_snapshot_keeps_head_and_tail_when_truncated() -> None:
    text = "开头" + ("中" * 50) + "结尾"

    snapshot = _memory_pending_snapshot(text, max_chars=20)

    assert snapshot.startswith("开头")
    assert snapshot.endswith("结尾")
    assert "中段省略" in snapshot
