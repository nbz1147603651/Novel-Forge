from __future__ import annotations

from novel_forge.core.schemas.continuity import ChapterPlan, SceneIntent
from novel_forge.pipeline.long.stages.draft import _ensure_scene_word_budgets


def test_ensure_scene_word_budgets_returns_copy_without_mutating_input() -> None:
    plan = ChapterPlan(
        scene_intents=[
            SceneIntent(scene_id="s1", summary="第一场"),
            SceneIntent(scene_id="s2", summary="第二场"),
            SceneIntent(scene_id="s3", summary="第三场"),
        ]
    )

    budgeted = _ensure_scene_word_budgets(plan, 900)

    assert budgeted is not plan
    assert [scene.target_words for scene in budgeted.scene_intents] == [300, 300, 300]
    assert [scene.target_words for scene in plan.scene_intents] == [0, 0, 0]
    assert budgeted.scene_intents[0] is not plan.scene_intents[0]


def test_ensure_scene_word_budgets_rescales_copy_without_mutating_input() -> None:
    plan = ChapterPlan(
        scene_intents=[
            SceneIntent(scene_id="s1", summary="第一场", target_words=1000),
            SceneIntent(scene_id="s2", summary="第二场", target_words=1000),
        ]
    )

    budgeted = _ensure_scene_word_budgets(plan, 1000)

    assert budgeted is not plan
    assert [scene.target_words for scene in budgeted.scene_intents] == [500, 500]
    assert [scene.target_words for scene in plan.scene_intents] == [1000, 1000]
    assert budgeted.scene_intents[0] is not plan.scene_intents[0]
