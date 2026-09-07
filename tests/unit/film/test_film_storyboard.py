"""影视分镜模块单测：节拍拆解 / 每场镜头硬预算 / 空镜 padding / 密集节拍合并 /
身份锁继承 / 审计门禁 / LLM 只建议节奏（FILM_SHOT_LAYOUT，失败回退确定性）/
STORYBOARD 阶段闭环。

定位注记：film 分镜与 comic 层共用同一套分镜头级拆解能力 —— ComicPanel
镜像 FilmShot、ComicPage 镜像 Track，可行域分配算法与身份锁投影共享
（film/storyboard.py），因此布局硬标准与身份锁继承是本文件的核心断言。
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from novel_forge.common.constants import TaskType
from novel_forge.film.pipeline import FilmProductionPipeline
from novel_forge.film.schemas import (
    FilmShot,
    FilmStage,
    FilmStageStatus,
    FilmStudioState,
    FilmTimeline,
    ProductionBible,
    ProductionCharacter,
    ProductionLocation,
    ProductionMode,
    ScreenIdentityLock,
    Screenplay,
    ScreenplayLine,
    ScreenplayScene,
)
from novel_forge.film.storyboard import (
    SHOTS_PER_SCENE_RANGE,
    allocate_beat_budgets,
    audit_storyboard,
    build_shot_beats,
    plan_storyboard_shots,
)
from novel_forge.persistence.models import ProjectLayout

# ── fixtures ────────────────────────────────────────────────────────────


def _layout(tmp_path: Path) -> ProjectLayout:
    layout = ProjectLayout(tmp_path / "project")
    layout.ensure_dirs()
    return layout


def _bible() -> ProductionBible:
    return ProductionBible(
        project_id="demo",
        title="演示项目",
        characters=[
            ProductionCharacter(
                character_id="ch-lin",
                name="林一",
                screen_identity=ScreenIdentityLock(
                    facial_anchors=["左眉疤", "单眼皮"],
                    silhouette="瘦高身形",
                    costume_palette=["黑", "灰"],
                    signature_props=["旧怀表"],
                    visual_state_timeline=["干净衬衫", "袖口染血"],
                ),
            )
        ],
        locations=[
            ProductionLocation(
                location_id="loc-hall",
                name="宴会厅",
                spatial_layout="长桌居中",
                key_light="顶光",
                color_mood="冷金",
            )
        ],
    )


def _scene(
    scene_id: str,
    lines: list[ScreenplayLine],
    *,
    characters: list[str] | None = None,
    visual_hook: str = "",
) -> ScreenplayScene:
    return ScreenplayScene(
        scene_id=scene_id,
        sequence_number=1,
        heading="宴会厅·夜",
        location_id="loc-hall",
        characters=characters if characters is not None else ["林一"],
        objective="逼问真相",
        visual_hook=visual_hook,
        lines=lines,
    )


def _dialogue_lines(count: int, *, speaker: str = "林一") -> list[ScreenplayLine]:
    return [
        ScreenplayLine(kind="dialogue", speaker=speaker, text=f"第{i}句对白。")
        for i in range(1, count + 1)
    ]


def _state(screenplay: Screenplay, bible: ProductionBible | None = None) -> FilmStudioState:
    return FilmStudioState(
        project_id="demo",
        project_title="演示项目",
        production_bible=bible or _bible(),
        screenplay=screenplay,
        timeline=FilmTimeline(name="主时间线"),
    )


class StubRouter:
    def __init__(self, content: str = "") -> None:
        self.content = content
        self.calls: list[TaskType] = []

    async def route(self, request) -> SimpleNamespace:
        self.calls.append(request.task_type)
        return SimpleNamespace(content=self.content)


# ── 节拍拆解（FilmShot 镜像 ComicPanel）─────────────────────────────────


def test_build_shot_beats_maps_lines() -> None:
    scene = _scene(
        "sc-01",
        [
            ScreenplayLine(kind="dialogue", speaker="林一", text="你来了。"),
            ScreenplayLine(kind="narration", text="夜色渐深。"),
            ScreenplayLine(kind="action", text="他握紧了怀表。"),
        ],
    )
    beats = build_shot_beats(scene)
    # 建立镜头 + 3 条线
    assert len(beats) == 4
    assert beats[0].kind == "establishing"
    assert beats[0].beat.startswith("建立镜头")
    assert beats[1].kind == "dialogue" and beats[1].speaker == "林一"
    assert beats[1].line_text == "你来了。"
    assert beats[2].kind == "narration"
    assert beats[3].kind == "action" and beats[3].action == "他握紧了怀表。"


# ── 布局硬标准：每场镜头预算 + 空镜 padding + 密集合并 ──────────────────


def test_plan_storyboard_respects_shot_budget() -> None:
    low, high = SHOTS_PER_SCENE_RANGE
    screenplay = Screenplay(
        title="演示剧本",
        scenes=[
            _scene("sc-01", _dialogue_lines(4), visual_hook="吊灯摇晃"),
            _scene("sc-02", _dialogue_lines(2), visual_hook="窗外暴雨"),
        ],
    )
    shots = plan_storyboard_shots(screenplay, _bible())
    assert shots
    per_scene: dict[str, int] = {}
    for shot in shots:
        per_scene[shot.scene_id] = per_scene.get(shot.scene_id, 0) + 1
    assert set(per_scene) == {"sc-01", "sc-02"}
    for count in per_scene.values():
        assert low <= count <= high
    assert audit_storyboard(shots, screenplay) == []


def test_plan_storyboard_pads_with_insert_shots() -> None:
    # 仅 2 beats（建立镜头 + 1 句对白）→ 必须 padding 到下限 3 镜
    screenplay = Screenplay(
        title="演示剧本",
        scenes=[_scene("sc-01", _dialogue_lines(1), visual_hook="吊灯摇晃")],
    )
    shots = plan_storyboard_shots(screenplay, _bible())
    assert len(shots) == SHOTS_PER_SCENE_RANGE[0]
    inserts = [shot for shot in shots if shot.title == "插入/反应镜头"]
    assert inserts, "预算不足时应插入空镜/反应镜头"
    for shot in inserts:
        assert shot.dialogue == "", "空镜镜头不得引入新对白"
        assert "吊灯摇晃" in shot.prompt
    assert audit_storyboard(shots, screenplay) == []


def test_plan_storyboard_merges_dense_beats_into_budget() -> None:
    # 12 句对白 + 建立镜头 = 13 beats → 镜头数仍不得超过上限 8，节拍全部装填
    screenplay = Screenplay(title="演示剧本", scenes=[_scene("sc-01", _dialogue_lines(12))])
    shots = plan_storyboard_shots(screenplay, _bible())
    low, high = SHOTS_PER_SCENE_RANGE
    assert low <= len(shots) <= high
    # 所有对白文本都被装填（允许合并进同一镜头）
    merged = "；".join(shot.dialogue for shot in shots if shot.dialogue)
    for i in range(1, 13):
        assert f"第{i}句对白。" in merged
    assert audit_storyboard(shots, screenplay) == []


def test_shared_allocator_stays_feasible() -> None:
    counts = allocate_beat_budgets(13, 5, low=1, high=3)
    assert sum(counts) == 13
    assert len(counts) == 5
    assert all(1 <= count <= 3 for count in counts)
    # 与 comic 页/格分配同一入口：预算内可行域分配
    counts = allocate_beat_budgets(18, 3, low=5, high=7, pacing_targets={1: 99})
    assert sum(counts) == 18
    assert all(5 <= count <= 7 for count in counts)


# ── 身份锁继承（共享 ProductionBible）───────────────────────────────────


def test_identity_lock_inherited_into_shot_prompt() -> None:
    screenplay = Screenplay(title="演示剧本", scenes=[_scene("sc-01", _dialogue_lines(4))])
    shots = plan_storyboard_shots(screenplay, _bible())
    establishing = shots[0]
    assert "面部锚点：左眉疤、单眼皮" in establishing.prompt
    assert "标志道具：旧怀表" in establishing.prompt
    assert "当前状态：袖口染血" in establishing.prompt  # timeline 最新状态
    assert establishing.character_ids == ["ch-lin"]
    # 场景上下文也进入 prompt
    assert "长桌居中" in establishing.prompt


# ── 审计门禁（确定性交付闸口）────────────────────────────────────────────


def test_audit_storyboard_reports_machine_readable_codes() -> None:
    screenplay = Screenplay(title="演示剧本", scenes=[_scene("sc-01", _dialogue_lines(1))])
    shot = FilmShot(shot_id="dup", scene_id="sc-01", shot_number=1, prompt="")
    issues = audit_storyboard([shot, shot], screenplay)
    joined = "\n".join(issues)
    assert "duplicate_shot_id:dup" in joined
    assert "empty_shot_prompt:dup" in joined
    assert "shot_count_out_of_range:sc-01:2" in joined
    orphan = FilmShot(shot_id="x", scene_id="sc-99", shot_number=1, prompt="p")
    assert "orphan_shot:x" in "\n".join(audit_storyboard([orphan], screenplay))


# ── pipeline 闭环：LLM 只建议节奏，确定性兜底 ────────────────────────────


def _pipeline(tmp_path: Path, router: StubRouter | None = None) -> FilmProductionPipeline:
    return FilmProductionPipeline(project_id="demo", layout=_layout(tmp_path), router=router)


async def test_refine_storyboard_uses_llm_pacing_and_clamps(tmp_path: Path) -> None:
    screenplay = Screenplay(title="回流剧本", scenes=[_scene("sc-01", _dialogue_lines(6))])
    router = StubRouter(
        json.dumps({"scenes": [{"scene_id": "sc-01", "shot_count": 99, "rhythm_note": "快切"}]})
    )
    pipeline = _pipeline(tmp_path, router)
    shots = await pipeline._refine_storyboard(_state(screenplay), use_ai=True)
    assert router.calls == [TaskType.FILM_SHOT_LAYOUT]
    # 99 被钳位到上限 8；rhythm_note 落在该场首镜
    scene_shots = [shot for shot in shots if shot.scene_id == "sc-01"]
    assert len(scene_shots) == SHOTS_PER_SCENE_RANGE[1]
    assert scene_shots[0].rhythm_note == "LLM 节奏：快切"
    assert audit_storyboard(shots, screenplay) == []


async def test_refine_storyboard_falls_back_on_invalid_llm_output(
    tmp_path: Path,
) -> None:
    screenplay = Screenplay(title="回流剧本", scenes=[_scene("sc-01", _dialogue_lines(6))])
    pipeline = _pipeline(tmp_path, StubRouter("这不是 JSON"))
    shots = await pipeline._refine_storyboard(_state(screenplay), use_ai=True)
    assert shots
    assert audit_storyboard(shots, screenplay) == []


async def test_refine_storyboard_ignores_unknown_scene_ids(tmp_path: Path) -> None:
    screenplay = Screenplay(title="回流剧本", scenes=[_scene("sc-01", _dialogue_lines(2))])
    router = StubRouter(
        json.dumps(
            {
                "scenes": [
                    {"scene_id": "sc-999", "shot_count": 8},
                    {"scene_id": "sc-01", "shot_count": 4},
                ]
            }
        )
    )
    pipeline = _pipeline(tmp_path, router)
    shots = await pipeline._refine_storyboard(_state(screenplay), use_ai=True)
    assert len([shot for shot in shots if shot.scene_id == "sc-01"]) == 4


async def test_refine_storyboard_preserves_locked_shots(tmp_path: Path) -> None:
    screenplay = Screenplay(title="回流剧本", scenes=[_scene("sc-01", _dialogue_lines(1))])
    state = _state(screenplay)
    pipeline = _pipeline(tmp_path)
    baseline = plan_storyboard_shots(screenplay, _bible())
    locked_shot = baseline[1].model_copy(update={"locked": True, "title": "人工锁定镜头"})
    state = state.model_copy(update={"shots": [baseline[0], locked_shot, *baseline[2:]]})
    shots = await pipeline._refine_storyboard(state, use_ai=False)
    kept = next(shot for shot in shots if shot.shot_id == locked_shot.shot_id)
    assert kept.title == "人工锁定镜头" and kept.locked


# ── STORYBOARD 阶段端到端 ───────────────────────────────────────────────


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _seed_project(tmp_path: Path) -> FilmProductionPipeline:
    layout = ProjectLayout(tmp_path / "长夜电台")
    layout.ensure_dirs()
    _write_json(
        layout.spec_path,
        {"title": "长夜电台", "theme": "深夜来电", "genre": "悬疑", "language": "zh"},
    )
    _write_json(
        layout.characters_path,
        {
            "characters": [
                {
                    "character_id": "char-shen",
                    "name": "沈鹿溪",
                    "role": "protagonist",
                    "appearance": "短黑发，深绿旧风衣",
                }
            ]
        },
    )
    _write_json(
        layout.bible_path,
        {"locations": [{"id": "loc-radio", "name": "旧电台直播间", "layout": "控制台朝东"}]},
    )
    _write_json(
        layout.outline_path,
        {
            "chapters": [
                {
                    "chapter_number": 1,
                    "scenes": [
                        {
                            "title": "停播后的第一通电话",
                            "location": "旧电台直播间",
                            "objective": "确认来电者身份",
                            "visual_hook": "ON AIR 红灯亮起",
                            "summary": "她接起一通没有号码的电话。",
                        }
                    ],
                }
            ]
        },
    )
    return FilmProductionPipeline(project_id="长夜电台", layout=layout)


async def test_storyboard_stage_replans_shots_within_budget(tmp_path: Path) -> None:
    pipeline = _seed_project(tmp_path)
    baseline = pipeline.get_or_bootstrap(mode=ProductionMode.AUTONOMOUS)
    assert len(baseline.shots) == 4  # 投影基线：每场 4 个模式镜头

    state = await pipeline.advance(
        mode=ProductionMode.AUTONOMOUS,
        use_ai=False,
        run_until=FilmStage.DELIVERY,
    )

    assert state.current_stage == FilmStage.SHOT_PRODUCTION
    storyboard = next(item for item in state.stages if item.stage == FilmStage.STORYBOARD)
    assert storyboard.status == FilmStageStatus.COMPLETED
    # 分镜重规划：节拍驱动（1 场景 2 节拍 → 空镜补齐到下限 3）
    low, high = SHOTS_PER_SCENE_RANGE
    assert low <= len(state.shots) <= high
    assert audit_storyboard(state.shots, state.screenplay) == []
    assert all(shot.prompt.strip() for shot in state.shots)
