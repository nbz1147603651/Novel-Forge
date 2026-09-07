"""Bounded future-plan exploration; publication is supplied by the workspace."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from novel_forge.common.constants import TaskType
from novel_forge.core.schemas.future_planning import FutureOutlineDecision
from novel_forge.core.schemas.outline import StoryOutline
from novel_forge.pipeline.steps.base import PipelineStep
from novel_forge.pipeline.steps.polish_outline_step import PolishOutlineInput, PolishOutlineStep


def record_future_replan_trigger(storage: Any, layout: Any, settings: Any, chapter: int) -> None:
    """Record an optional exploration trigger without changing replan behavior."""
    try:
        path = layout.plans_dir / "planning_policy.json"
        policy = storage.load_json(path) if path.exists() else {}
        mode = policy.get("mode", getattr(settings, "future_planning_mode", "fixed"))
        if mode in {"proposal", "adaptive"}:
            storage.save_json(
                layout.reports_dir / "future_planning_trigger.json",
                {"chapter_number": chapter, "reason": "consistency_replan"},
            )
    except Exception:
        logging.getLogger(__name__).exception("future_replan_trigger_failed")


def planning_boundary(
    outline: StoryOutline, chapter_number: int, blueprint: dict[str, Any] | None = None
) -> bool:
    source = blueprint or {}
    return (
        any(v.end_chapter == chapter_number for v in outline.volumes)
        or any(
            int(p.get("chapter_number", 0)) == chapter_number
            for p in source.get("key_turning_points", [])
            if isinstance(p, dict)
        )
        or any(
            int(p.get("chapter_end", 0)) == chapter_number
            for p in source.get("narrative_phases", [])
            if isinstance(p, dict)
        )
    )


def exploration_has_budget(settings: Any, router: Any) -> bool:
    tracker = getattr(router, "spending_tracker", None)
    reserve = float(getattr(settings, "future_planning_budget_reserve_usd", 1.0))
    for period in ("daily", "monthly"):
        limit = float(getattr(settings, f"budget_{period}_usd", 0.0) or 0.0)
        if limit <= 0:
            continue
        if tracker is None:
            return False
        spent = float(getattr(tracker, f"{period}_spent", 0.0))
        if spent + reserve > limit:
            return False
    return True


def protected_chapters(root: Path, policy: dict[str, Any]) -> set[int]:
    result = {int(n) for n in policy.get("locked_chapters", [])}
    for path in (root / "chapters").glob("chapter_*.md"):
        number = path.stem.removeprefix("chapter_")
        if number.isdigit():
            result.add(int(number))
    return result


def validate_candidate_boundaries(
    original: StoryOutline,
    candidate: StoryOutline,
    *,
    protected: set[int],
    allowed: set[int] | None = None,
) -> list[int]:
    before = {c.chapter_number: c.model_dump(exclude={"created_at"}) for c in original.chapters}
    after = {c.chapter_number: c.model_dump(exclude={"created_at"}) for c in candidate.chapters}
    if original.total_chapters != candidate.total_chapters or before.keys() != after.keys():
        raise ValueError("requires_confirmation: chapter numbering/count changed")
    # Global structure may carry locked payoffs; chapter exploration cannot edit it.
    exclude = {"chapters", "created_at"}
    if original.model_dump(exclude=exclude) != candidate.model_dump(exclude=exclude):
        raise ValueError("requires_confirmation: global outline structure changed")
    changed = sorted(n for n in before if before[n] != after[n])
    if protected.intersection(changed):
        raise ValueError("requires_confirmation: published or human-locked chapter changed")
    if allowed is not None and not set(changed).issubset(allowed):
        raise ValueError("Candidate changed chapters outside the exploration window")
    return changed


def validate_protected_contracts(
    before: dict[str, Any], after: dict[str, Any], protected: set[int]
) -> None:
    def items(payload: dict[str, Any]) -> dict[int, Any]:
        return {
            int(item.get("chapter_number", 0)): item
            for item in payload.get("chapter_contracts", [])
            if isinstance(item, dict)
        }

    old, new = items(before), items(after)
    if any(old.get(n) != new.get(n) for n in protected):
        raise ValueError("Contract synchronization changed a protected chapter")


async def explore_future_candidate(
    *,
    router: Any,
    builder: Any,
    settings: Any,
    outline: StoryOutline,
    chapter_number: int,
    accepted_context: dict[str, Any],
    protected: set[int],
    on_step: Any = None,
) -> StoryOutline | None:
    if not exploration_has_budget(settings, router):
        return None
    width = int(getattr(settings, "future_planning_window", 4))
    selected = [
        c.chapter_number
        for c in outline.chapters
        if chapter_number < c.chapter_number <= chapter_number + width
        and c.chapter_number not in protected
    ]
    if not selected:
        return None
    step = PolishOutlineStep(router, builder, settings=settings, on_step=on_step)
    result = await step.run(
        PolishOutlineInput(
            story_outline=outline,
            record_history=False,  # The isolated revision owns this exploration's history.
            chapter_range=selected,
            user_hint=(
                "提出一个后续剧情候选，保留原方案供独立比较。只调整所选未成稿章节；"
                "不得改变章号、章数、人工锁定点或用户明确要求。基于动机、因果、未兑现承诺"
                "探索新的行动路径；不增加意象呼应配额。事实边界与承诺如下：\n"
                + json.dumps(accepted_context, ensure_ascii=False)
            ),
        )
    )
    if result.adjusted_outline is None or not result.changed_chapters:
        return None
    # The workspace retains even an out-of-bounds candidate as a confirmation
    # proposal, but never publishes it without boundary validation.
    return result.adjusted_outline


class FutureOutlineReviewStep(PipelineStep[dict[str, Any], FutureOutlineDecision]):
    @property
    def step_name(self) -> str:
        return "review_future_outline"

    async def _execute(self, input_data: dict[str, Any]) -> FutureOutlineDecision:
        result = await self._call_with_retry(
            TaskType.REVIEW_FUTURE_OUTLINE, input_data, max_tokens=4096, temperature=0.2
        )
        return FutureOutlineDecision.model_validate(result)
