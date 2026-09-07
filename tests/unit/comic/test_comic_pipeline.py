"""漫画管线单测：分格预算 / 空镜 padding / 身份继承 / 气泡映射 / 审计门禁 /
pipeline 闭环（LLM 仅建议节奏，失败回退确定性规划）/ DeliveryManifest 四媒体。

定位注记：comic 层是分镜头级拆解能力，ComicPanel 镜像 FilmShot，为影视
模块分镜铺路——因此布局硬标准与身份锁继承是本文件的核心断言。
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from novel_forge.comic.layout import build_panel_beats, plan_comic_pages
from novel_forge.comic.pipeline import ComicPipeline
from novel_forge.comic.schemas import (
    PAGE_ASPECT,
    PAGE_PANEL_RANGE,
    WEBTOON_ASPECT,
    WEBTOON_PANEL_RANGE,
    ComicFormat,
    ComicPage,
    ComicPanel,
    ComicProjectState,
    SpeechBubble,
    audit_comic_layout,
)
from novel_forge.common.constants import TaskType
from novel_forge.film.schemas import (
    DeliveryManifest,
    DeliveryMediaType,
    FilmStudioState,
    FilmTimeline,
    ProductionBible,
    ProductionCharacter,
    ProductionLocation,
    ScreenIdentityLock,
    Screenplay,
    ScreenplayLine,
    ScreenplayScene,
)
from novel_forge.film.store import FilmProjectStore
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


def _film_state(screenplay: Screenplay, bible: ProductionBible) -> FilmStudioState:
    return FilmStudioState(
        project_id="demo",
        project_title="演示项目",
        production_bible=bible,
        screenplay=screenplay,
        timeline=FilmTimeline(name="主时间线"),
    )


def _seed_film_sources(
    tmp_path: Path, screenplay: Screenplay, bible: ProductionBible | None = None
) -> ProjectLayout:
    layout = _layout(tmp_path)
    FilmProjectStore(layout).save(_film_state(screenplay, bible or _bible()))
    return layout


class StubRouter:
    def __init__(self, content: str = "") -> None:
        self.content = content
        self.calls: list[TaskType] = []

    async def route(self, request) -> SimpleNamespace:
        self.calls.append(request.task_type)
        return SimpleNamespace(content=self.content)


# ── 气泡映射（Clip 内气泡 = ScreenplayLine 映射）──────────────────────────


def test_build_panel_beats_maps_dialogue_thought_narration() -> None:
    scene = _scene(
        "sc-01",
        [
            ScreenplayLine(kind="dialogue", speaker="林一", text="你来了。"),
            ScreenplayLine(
                kind="dialogue", speaker="林一", text="不能暴露。", performance_note="心想"
            ),
            ScreenplayLine(kind="narration", text="夜色渐深。"),
            ScreenplayLine(kind="action", text="他握紧了怀表。"),
        ],
    )
    beats = build_panel_beats(scene)
    # 建立镜头 + 4 条线
    assert len(beats) == 5
    assert beats[0].beat.startswith("建立镜头")

    kinds = {kind: [] for kind in ("speech", "thought", "narration")}
    for beat in beats:
        for bubble in beat.bubbles:
            kinds[bubble.kind].append(bubble)
    assert len(kinds["speech"]) == 1
    assert kinds["speech"][0].source_line_ref == "sc-01:L0"
    assert len(kinds["thought"]) == 1
    assert len(kinds["narration"]) == 1
    assert kinds["narration"][0].speaker == ""
    # action 行没有气泡，只有动作
    assert beats[4].action == "他握紧了怀表。"
    assert beats[4].bubbles == []


# ── 布局硬标准：预算 + 空镜 padding ───────────────────────────────────────


@pytest.mark.parametrize(
    ("fmt", "expected_range", "expected_aspect"),
    [
        (ComicFormat.PAGE, PAGE_PANEL_RANGE, PAGE_ASPECT),
        (ComicFormat.WEBTOON, WEBTOON_PANEL_RANGE, WEBTOON_ASPECT),
    ],
)
def test_plan_pages_respects_panel_budget(
    fmt: ComicFormat, expected_range: tuple[int, int], expected_aspect: str
) -> None:
    low, high = expected_range
    # 14 beats → 多页；所有页必须落在预算区间内
    screenplay = Screenplay(
        title="演示剧本",
        scenes=[
            _scene("sc-01", _dialogue_lines(6), visual_hook="吊灯摇晃"),
            _scene("sc-02", _dialogue_lines(7), visual_hook="窗外暴雨"),
        ],
    )
    pages = plan_comic_pages(screenplay, _bible(), fmt)
    assert pages
    for page in pages:
        assert low <= page.panel_count <= high
        for panel in page.panels:
            assert panel.aspect == expected_aspect
            assert f"画幅{expected_aspect}" in panel.prompt


def test_plan_pages_pads_with_silent_inserts() -> None:
    # 仅 2 beats（建立镜头 + 1 句对白）→ 页漫必须 padding 到 ≥5 格
    screenplay = Screenplay(
        title="演示剧本", scenes=[_scene("sc-01", _dialogue_lines(1), visual_hook="吊灯摇晃")]
    )
    pages = plan_comic_pages(screenplay, _bible(), ComicFormat.PAGE)
    assert len(pages) == 1
    assert pages[0].panel_count >= PAGE_PANEL_RANGE[0]
    padded = [p for p in pages[0].panels if p.beat.startswith("空镜/反应格")]
    assert padded, "预算不足时应插入空镜/反应格"
    for panel in padded:
        assert panel.bubbles == [], "空镜格不得引入新对白"


def test_plan_pages_empty_screenplay_returns_empty() -> None:
    screenplay = Screenplay(title="空剧本")
    assert plan_comic_pages(screenplay, _bible(), ComicFormat.PAGE) == []


def test_tail_rebalance_keeps_hard_floor() -> None:
    # 9 beats / page(5-7)：尾部均衡重分配后每页都不得低于硬下限
    screenplay = Screenplay(
        title="演示剧本", scenes=[_scene("sc-01", _dialogue_lines(8))]
    )
    pages = plan_comic_pages(screenplay, _bible(), ComicFormat.PAGE)
    assert pages
    for page in pages:
        assert PAGE_PANEL_RANGE[0] <= page.panel_count <= PAGE_PANEL_RANGE[1]


# ── 身份锁继承 ────────────────────────────────────────────────────────────


def test_identity_lock_inherited_into_panel_prompt() -> None:
    screenplay = Screenplay(
        title="演示剧本", scenes=[_scene("sc-01", _dialogue_lines(4))]
    )
    pages = plan_comic_pages(screenplay, _bible(), ComicFormat.WEBTOON)
    establishing = pages[0].panels[0]
    assert "面部锚点：左眉疤、单眼皮" in establishing.prompt
    assert "标志道具：旧怀表" in establishing.prompt
    assert "当前状态：袖口染血" in establishing.prompt  # timeline 最新状态
    assert establishing.character_ids == ["ch-lin"]
    # 场景上下文也进入 prompt
    assert "长桌居中" in establishing.prompt


# ── 审计门禁（确定性交付闸口）────────────────────────────────────────────


def _panel(
    panel_id: str, *, prompt: str = "p", bubbles: list[SpeechBubble] | None = None
) -> ComicPanel:
    return ComicPanel(
        panel_id=panel_id,
        page_number=1,
        panel_number=1,
        prompt=prompt,
        bubbles=bubbles or [],
    )


def test_audit_passes_for_valid_layout() -> None:
    panels = [
        ComicPanel(panel_id=f"p001-{i:02d}", page_number=1, panel_number=i, prompt="p")
        for i in range(1, 6)
    ]
    state = ComicProjectState(
        project_id="demo", pages=[ComicPage(page_number=1, panels=panels)]
    )
    assert audit_comic_layout(state) == []


def test_audit_reports_machine_readable_codes() -> None:
    broken = ComicPanel(
        panel_id="dup-1",
        page_number=1,
        panel_number=1,
        prompt="",
        bubbles=[SpeechBubble(text=" ")],
    )
    state = ComicProjectState(
        project_id="demo",
        pages=[
            ComicPage(page_number=1, panels=[broken, broken, _panel("dup-1")]),
        ],
    )
    issues = audit_comic_layout(state)
    joined = "\n".join(issues)
    assert "panel_count_out_of_range:1:3" in joined  # 3 格 < 页漫下限 5
    assert "duplicate_panel_id:dup-1" in joined
    assert "empty_panel_prompt:" in joined
    assert "empty_bubble_text:" in joined


# ── pipeline 闭环 ────────────────────────────────────────────────────────


async def test_plan_pages_requires_screenplay_projection(tmp_path: Path) -> None:
    pipeline = ComicPipeline(project_id="demo", layout=_layout(tmp_path))
    with pytest.raises(RuntimeError, match="剧本"):
        await pipeline.plan_pages(fmt=ComicFormat.PAGE)


async def test_plan_pages_closed_loop_with_deterministic_layout(tmp_path: Path) -> None:
    screenplay = Screenplay(
        title="回流剧本", version=3, scenes=[_scene("sc-01", _dialogue_lines(6))]
    )
    layout = _seed_film_sources(tmp_path, screenplay)
    pipeline = ComicPipeline(project_id="demo", layout=layout)
    state = await pipeline.plan_pages(fmt=ComicFormat.PAGE)
    assert state.source_revision == "screenplay_v3"
    assert state.total_panels > 0
    assert pipeline.audit(state) == []
    # 状态已持久化
    reloaded = pipeline.store.load()
    assert reloaded is not None
    assert reloaded.total_panels == state.total_panels


async def test_plan_pages_uses_llm_pacing_hint_and_clamps(tmp_path: Path) -> None:
    screenplay = Screenplay(
        title="回流剧本", scenes=[_scene("sc-01", _dialogue_lines(6))]
    )
    layout = _seed_film_sources(tmp_path, screenplay)
    router = StubRouter(json.dumps({"pages": [{"page_number": 1, "panel_count": 99}]}))
    pipeline = ComicPipeline(project_id="demo", layout=layout, router=router)
    state = await pipeline.plan_pages(fmt=ComicFormat.PAGE)
    assert router.calls == [TaskType.COMIC_PANEL_LAYOUT]
    # 99 被钳位到 high=7 → rhythm_note 记录目标
    assert state.pages[0].rhythm_note == "LLM 节奏：目标 7 格"
    assert pipeline.audit(state) == []


async def test_plan_pages_falls_back_on_invalid_llm_output(tmp_path: Path) -> None:
    screenplay = Screenplay(
        title="回流剧本", scenes=[_scene("sc-01", _dialogue_lines(6))]
    )
    layout = _seed_film_sources(tmp_path, screenplay)
    pipeline = ComicPipeline(
        project_id="demo", layout=layout, router=StubRouter("这不是 JSON")
    )
    state = await pipeline.plan_pages(fmt=ComicFormat.PAGE)
    assert state.total_panels > 0
    assert pipeline.audit(state) == []


# ── 导出包 + DeliveryManifest 四媒体 ─────────────────────────────────────


async def test_export_package_registers_comic_on_manifest(tmp_path: Path) -> None:
    screenplay = Screenplay(
        title="回流剧本", scenes=[_scene("sc-01", _dialogue_lines(6))]
    )
    layout = _seed_film_sources(tmp_path, screenplay)
    pipeline = ComicPipeline(project_id="demo", layout=layout)
    state = await pipeline.plan_pages(fmt=ComicFormat.PAGE)
    result = pipeline.export_package(state)
    for key in ("pages_path", "preview_path", "manifest_path"):
        assert Path(result[key]).exists(), key
    assert result["audit_issues"] == []
    manifest = DeliveryManifest.model_validate_json(
        Path(result["manifest_path"]).read_text(encoding="utf-8")
    )
    assert len(manifest.artifacts) == 1
    artifact = manifest.artifacts[0]
    assert artifact.media_type is DeliveryMediaType.COMIC
    assert artifact.item_count == state.total_panels
    assert "page comic" in artifact.note
    preview = Path(result["preview_path"]).read_text(encoding="utf-8")
    assert "漫画预览" in preview and "气泡" in preview


def test_delivery_manifest_registers_all_four_media() -> None:
    base = DeliveryManifest(project_id="p", title="t")
    manifest = base
    for media, path in (
        (DeliveryMediaType.NOVEL, "exports/book.md"),
        (DeliveryMediaType.AUDIOBOOK, "audiobook/package.json"),
        (DeliveryMediaType.COMIC, "comic/comic_pages.json"),
        (DeliveryMediaType.FILM, "film/master.mp4"),
    ):
        manifest = manifest.register_artifact(media, path, item_count=1)
    # register_artifact 返回副本，原对象不变；链式注册后四媒体齐全且顺序稳定
    assert base.artifacts == []
    assert [a.media_type for a in manifest.artifacts] == [
        DeliveryMediaType.NOVEL,
        DeliveryMediaType.AUDIOBOOK,
        DeliveryMediaType.COMIC,
        DeliveryMediaType.FILM,
    ]
