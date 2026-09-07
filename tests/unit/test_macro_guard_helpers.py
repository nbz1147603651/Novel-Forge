from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from novel_forge.core.exceptions import ConsistencyViolationError
from novel_forge.core.schemas.chapter import MacroGuardReport
from novel_forge.core.utils.macro_guard_helpers import (
    load_macro_guard_adjustment_state,
    record_macro_guard_adjustment,
    should_trigger_macro_guard,
)
from novel_forge.persistence.filesystem import FileSystemStorage
from novel_forge.persistence.models import ProjectLayout
from novel_forge.pipeline.long.chapter_flow_finalize import _persist_macro_guard_result
from novel_forge.pipeline.long.stages.planning import generate_bridge_and_plan


def _make_layout(tmp_path: Path) -> tuple[FileSystemStorage, ProjectLayout]:
    root = tmp_path / "proj"
    storage = FileSystemStorage(root)
    layout = ProjectLayout(root)
    return storage, layout


def test_record_macro_guard_adjustment_persists_state_and_cooldown(tmp_path: Path) -> None:
    storage, layout = _make_layout(tmp_path)
    settings = SimpleNamespace(long_macro_guard_cooldown_chapters=5)

    state1 = record_macro_guard_adjustment(storage, layout, 12, settings)
    state2 = record_macro_guard_adjustment(storage, layout, 20, settings)
    loaded = load_macro_guard_adjustment_state(storage, layout)
    cooldown = storage.load_json(layout.states_dir / "macro_guard_cooldown.json")

    assert state1["applied_adjustments"] == 1
    assert state2["applied_adjustments"] == 2
    assert loaded == {"applied_adjustments": 2, "last_adjustment_chapter": 20}
    assert cooldown["last_adjustment_chapter"] == 20
    assert cooldown["cooldown_chapters"] == 5


def test_should_trigger_macro_guard_respects_cooldown_marker(tmp_path: Path) -> None:
    storage, layout = _make_layout(tmp_path)
    settings = SimpleNamespace(
        long_macro_guard_enabled=True,
        long_macro_guard_interval=1,
        long_macro_guard_cooldown_chapters=5,
    )
    storage.save_json(
        layout.states_dir / "macro_guard_cooldown.json",
        {"last_adjustment_chapter": 20, "cooldown_chapters": 5},
    )

    assert should_trigger_macro_guard(22, layout, storage, settings) is False
    assert should_trigger_macro_guard(26, layout, storage, settings) is True


def test_persist_macro_guard_critical_writes_replan_marker(tmp_path: Path) -> None:
    storage, layout = _make_layout(tmp_path)
    report = MacroGuardReport(
        drift_score=0.91,
        recommended_action="critical_rollback",
        reasoning="主线轨迹严重偏移",
    )

    _persist_macro_guard_result(storage, layout, 4, report)

    marker = storage.load_json(layout.states_dir / "macro_guard_replan_required_ch5.json")
    assert marker["action"] == "critical_rollback"
    assert marker["source_chapter"] == 4
    assert marker["target_chapter"] == 5
    assert marker["requires_replan"] is True


@pytest.mark.asyncio
async def test_macro_guard_critical_marker_blocks_planning(tmp_path: Path) -> None:
    storage, layout = _make_layout(tmp_path)
    storage.save_json(
        layout.states_dir / "macro_guard_replan_required_ch5.json",
        {
            "action": "critical_rollback",
            "source_chapter": 4,
            "target_chapter": 5,
            "drift_score": 0.9,
            "requires_replan": True,
        },
    )
    events: list[tuple[str, object]] = []
    runner = SimpleNamespace(
        _storage=storage,
        _settings=SimpleNamespace(long_macro_guard_critical_blocks_archive=True),
        _on_step=lambda step, payload=None: events.append((step, payload)),
    )
    bundle = SimpleNamespace(layout=layout, chapter_outline=SimpleNamespace())

    with pytest.raises(ConsistencyViolationError, match="critical_rollback"):
        await generate_bridge_and_plan(
            runner,
            bundle,
            packet=SimpleNamespace(),
            chapter_number=5,
            trace=SimpleNamespace(),
        )

    assert any(step == "macro_guard_replan_required" for step, _payload in events)


@pytest.mark.asyncio
async def test_macro_guard_critical_marker_can_be_downgraded(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    storage, layout = _make_layout(tmp_path)
    storage.save_json(
        layout.states_dir / "macro_guard_replan_required_ch5.json",
        {"action": "critical_rollback", "source_chapter": 4, "target_chapter": 5},
    )
    events: list[tuple[str, object]] = []
    runner = SimpleNamespace(
        _storage=storage,
        _settings=SimpleNamespace(
            long_macro_guard_critical_blocks_archive=False,
            long_macro_guard_auto_apply_hint=True,
        ),
        _on_step=lambda step, payload=None: events.append((step, payload)),
    )
    bundle = SimpleNamespace(layout=layout, chapter_outline=SimpleNamespace())

    async def _stop_after_marker(*_args, **_kwargs):
        raise RuntimeError("continued_after_marker")

    monkeypatch.setattr(
        "novel_forge.pipeline.long.stages.planning._collect_generation_memory_hints",
        _stop_after_marker,
    )

    with pytest.raises(RuntimeError, match="continued_after_marker"):
        await generate_bridge_and_plan(
            runner,
            bundle,
            packet=SimpleNamespace(guard_constraints=[]),
            chapter_number=5,
            trace=SimpleNamespace(),
        )

    assert any(step == "macro_guard_replan_marker_downgraded" for step, _payload in events)
