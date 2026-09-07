from __future__ import annotations

from types import SimpleNamespace

from novel_forge.core.schemas.continuity import ChapterBridge, ChapterPlan, SceneIntent
from novel_forge.pipeline.long.services.generation.scene_writing import validate_scene_plan_locally
from novel_forge.pipeline.long.services.init_repair import (
    InitRepairContext,
    InitRepairOrchestrator,
)
from novel_forge.pipeline.long.services.scene_plan_repair import ScenePlanRepairPolicy


def _scene(scene_id: str, order: int, deps: list[str] | None = None) -> SceneIntent:
    return SceneIntent(
        scene_id=scene_id,
        summary=f"{scene_id} summary",
        required_outcome=f"scene_{order:02d} outcome",
        exit_state=f"{scene_id} exit",
        draft_order=order,
        dependency_scene_ids=deps or [],
    )


def _ctx() -> InitRepairContext:
    return InitRepairContext(
        service_ctx=SimpleNamespace(),
        outline_ctx={},
        total_chapters=1,
        artifacts={
            "bridge": ChapterBridge(to_chapter=1),
            "chapter_number": 1,
            "target_word_count": 0,
        },
    )


async def test_scene_plan_repair_normalizes_duplicate_scene_ids() -> None:
    plan = ChapterPlan(scene_intents=[_scene("scene_01", 1), _scene("scene_01", 2)])

    outcome = await InitRepairOrchestrator(ScenePlanRepairPolicy()).repair(plan, _ctx())

    assert outcome.report.is_valid
    ids = [scene.scene_id for scene in outcome.payload.scene_intents]
    assert len(ids) == len(set(ids))
    assert ids == ["scene_01", "scene_02"]


async def test_scene_plan_repair_local_fallback_breaks_dependency_cycle() -> None:
    plan = ChapterPlan(
        scene_intents=[
            _scene("scene_01", 1, deps=["scene_02"]),
            _scene("scene_02", 2, deps=["scene_01"]),
        ]
    )

    outcome = await InitRepairOrchestrator(ScenePlanRepairPolicy()).repair(plan, _ctx())
    report = validate_scene_plan_locally(
        plan=outcome.payload,
        bridge=ChapterBridge(to_chapter=1),
        target_word_count=0,
    )

    assert outcome.report.is_valid
    assert report["valid"] is True
    assert not any(issue["code"] == "dependency_cycle" for issue in report["issues"])
