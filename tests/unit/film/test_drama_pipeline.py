"""短剧全流程：端到端最小闭环（3 集样例）+ 制作包抽查门禁。"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from novel_forge.common.constants import TaskType
from novel_forge.film.drama.pipeline import DramaPipeline
from novel_forge.film.drama.schemas import (
    DramaSeriesPlan,
    EpisodeOutline,
    validate_episode_screenplay,
)
from novel_forge.film.drama.store import DramaProjectState
from novel_forge.persistence.models import ProjectLayout


def _layout(tmp_path: Path) -> ProjectLayout:
    layout = ProjectLayout(tmp_path / "project")
    layout.ensure_dirs()
    return layout


def _pipeline(tmp_path: Path, router: object = None) -> DramaPipeline:
    return DramaPipeline(project_id="demo", layout=_layout(tmp_path), router=router)


_PLAN = {
    "title": "重生之都市修仙",
    "total_episodes": 3,
    "episode_duration_s": 120,
    "three_acts": ["建置", "对抗", "高潮"],
    "paywall_beats": [
        {
            "episode_number": 1,
            "position": "end",
            "strategy": "cliffhanger_reversal",
            "description": "结尾悬念",
        },
        {
            "episode_number": 3,
            "position": "end",
            "strategy": "identity_reveal",
            "description": "身份揭晓",
        },
    ],
    "thrill_matrix": [
        {"episode_number": 1, "kind": "打脸", "intensity": 5, "description": ""}
    ],
    "waveform_stages": [
        {"stage": "开局钩子", "episode_start": 1, "episode_end": 1, "intensity_target": 4},
        {"stage": "中段反转", "episode_start": 2, "episode_end": 2, "intensity_target": 6},
        {"stage": "高潮对决", "episode_start": 3, "episode_end": 3, "intensity_target": 9},
    ],
    "antagonist_system": [
        {"name": "表层对手", "tier": "surface", "character_ref": "赵天霸"},
        {"name": "幕后主使", "tier": "boss", "character_ref": "黑袍人"},
    ],
}


def _outline_payload() -> dict:
    episodes = []
    markers = {
        1: "cliffhanger_reversal@end",
        2: "",
        3: "identity_reveal@end",
    }
    for number in (1, 2, 3):
        episodes.append(
            {
                "episode_number": number,
                "title": f"第{number}集",
                "summary": f"第{number}集概要",
                "waveform_stage": "开局钩子",
                "paywall_marker": markers[number],
                "scenes": [
                    {
                        "scene_number": i,
                        "summary": f"场次{i}",
                        "location": "宴会厅",
                        "emotional_beat": "压抑",
                    }
                    for i in range(1, 5)
                ],
            }
        )
    return {"episodes": episodes}


def _screenplay_payload(episode_number: int) -> dict:
    scenes = []
    shot_split = [4, 4, 4]
    line_split = [7, 7, 7]
    for index in range(3):
        scenes.append(
            {
                "scene_number": index + 1,
                "heading": f"宴会厅·{'夜' if index else '日'}",
                "action": "灯光骤暗。",
                "shot_count": shot_split[index],
                "lines": [
                    {"speaker": "赵天霸", "line": f"第{line}句对白。"}
                    for line in range(1, line_split[index] + 1)
                ],
            }
        )
    return {"episode_number": episode_number, "scenes": scenes}


class FakeRouter:
    def __init__(self, *, break_screenplay: bool = False) -> None:
        self.calls: list[TaskType] = []
        self.break_screenplay = break_screenplay

    async def route(self, request) -> SimpleNamespace:
        self.calls.append(request.task_type)
        if request.task_type is TaskType.DRAMA_SERIES_PLAN:
            return SimpleNamespace(content=json.dumps(_PLAN, ensure_ascii=False))
        if request.task_type is TaskType.EPISODE_OUTLINE:
            return SimpleNamespace(content=json.dumps(_outline_payload(), ensure_ascii=False))
        if request.task_type is TaskType.EPISODE_SCREENPLAY:
            if self.break_screenplay:
                return SimpleNamespace(content=json.dumps({"episode_number": 1, "scenes": []}))
            prompt_text = request.messages[0]["content"]
            for number in (1, 2, 3):
                if f'"episode_number": {number}' in prompt_text:
                    return SimpleNamespace(
                        content=json.dumps(_screenplay_payload(number), ensure_ascii=False)
                    )
            return SimpleNamespace(content=json.dumps(_screenplay_payload(1)))
        raise AssertionError(f"unexpected task type: {request.task_type}")


async def _closed_loop(tmp_path: Path, router: object) -> DramaProjectState:
    pipeline = _pipeline(tmp_path, router)
    state = await pipeline.plan_series(
        title="重生之都市修仙",
        total_episodes=3,
        character_roster=("林晚", "赵天霸"),
    )
    state = await pipeline.expand_outlines(start=1, end=3)
    for episode_number in (1, 2, 3):
        state = await pipeline.write_episode_screenplay(episode_number=episode_number)
    return state


async def test_end_to_end_closed_loop_with_router(tmp_path: Path) -> None:
    router = FakeRouter()
    pipeline = _pipeline(tmp_path, router)
    state = await _closed_loop(tmp_path, router)

    assert TaskType.DRAMA_SERIES_PLAN in router.calls
    assert len(state.outlines) == 3 and len(state.screenplays) == 3
    for screenplay in state.screenplays:
        passed, issues = validate_episode_screenplay(screenplay)
        assert passed, issues

    markers = [m for m in state.timeline_markers if m.name.startswith("paywall:")]
    assert len(markers) == 2
    first = next(m for m in markers if ":ep1:" in m.name)
    assert first.time_s == pytest.approx(0.9 * 120)
    last = next(m for m in markers if ":ep3:" in m.name)
    assert last.time_s == pytest.approx(2 * 120 + 0.9 * 120)

    checkpoints = [node for node in state.run_plan if node.human_checkpoint]
    assert len(checkpoints) == 2

    assert pipeline.audit_production_package(state) == []
    manifest = pipeline.export_production_package(state)
    assert manifest["gate_passed"] is True
    assert manifest["screenplay_episodes"] == [1, 2, 3]
    markdown = [path for path in manifest["artifacts"] if path.endswith(".md")]
    assert len(markdown) == 3
    assert Path(manifest["artifacts"][0]).exists()


async def test_closed_loop_offline_fallback(tmp_path: Path) -> None:
    pipeline = _pipeline(tmp_path, router=None)
    state = await pipeline.plan_series(title="离线短剧", total_episodes=3)
    assert state.series_plan is not None
    assert len(state.series_plan.waveform_stages) == 4
    covered = {
        number
        for stage in state.series_plan.waveform_stages
        for number in range(stage.episode_start, stage.episode_end + 1)
    }
    assert covered == {1, 2, 3}

    state = await pipeline.expand_outlines(start=1, end=3)
    for episode_number in (1, 2, 3):
        state = await pipeline.write_episode_screenplay(episode_number=episode_number)
    assert pipeline.audit_production_package(state) == []
    manifest = pipeline.export_production_package(state)
    assert manifest["gate_passed"] is True


async def test_invalid_screenplay_falls_back_to_passing_draft(tmp_path: Path) -> None:
    router = FakeRouter(break_screenplay=True)
    pipeline = _pipeline(tmp_path, router)
    state = await pipeline.plan_series(title="兜底", total_episodes=3)
    state = await pipeline.expand_outlines(start=1, end=1)
    state = await pipeline.write_episode_screenplay(episode_number=1)
    screenplay = state.screenplay_for(1)
    assert screenplay is not None
    passed, issues = validate_episode_screenplay(screenplay)
    assert passed, issues


async def test_audit_flags_paywall_marker_mismatch(tmp_path: Path) -> None:
    pipeline = _pipeline(tmp_path)
    plan = DramaSeriesPlan.model_validate(_PLAN)
    outlines = [
        EpisodeOutline.model_validate(item) for item in _outline_payload()["episodes"]
    ]
    outlines[2] = outlines[2].model_copy(update={"paywall_marker": "crisis_unresolved@end"})
    state = DramaProjectState(
        project_id="demo", series_plan=plan, outlines=outlines
    )
    issues = pipeline.audit_production_package(state)
    assert "paywall_marker_mismatch:3" in issues


async def test_audit_requires_series_plan(tmp_path: Path) -> None:
    pipeline = _pipeline(tmp_path)
    issues = pipeline.audit_production_package(DramaProjectState(project_id="demo"))
    assert issues == ["missing_series_plan"]


async def test_outline_requires_plan_first(tmp_path: Path) -> None:
    pipeline = _pipeline(tmp_path)
    with pytest.raises(ValueError, match="Series plan"):
        await pipeline.expand_outlines(start=1, end=2)
