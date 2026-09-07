"""Batch 6 unit tests: visual continuity, vision QC, P2 reference capabilities."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from novel_forge.film.reference_capabilities import (
    ASSET_TYPE_EXPRESSION_SHEET,
    ASSET_TYPE_POSE_SHEET,
    ASSET_TYPE_PROP_SHEET,
    ASSET_TYPE_SCENE_VARIANTS,
    ASSET_TYPE_STORYBOARD_CONTACT_SHEET,
    LOCALIZATION_GUIDANCE,
    build_storyboard_contact_sheet,
    build_variant_assets,
    render_hollywood_screenplay,
)
from novel_forge.film.schemas import (
    FilmShot,
    FilmStudioState,
    FilmStyleLock,
    FilmTimeline,
    ProductionBible,
    ProductionCharacter,
    ProductionLocation,
    QcCheck,
    QcCheckStatus,
    QcReport,
    ScreenIdentityLock,
    Screenplay,
    ScreenplayLine,
    ScreenplayScene,
    ShotLanguage,
)
from novel_forge.film.style_library import STYLE_LIBRARY, match_style, style_decision_choices
from novel_forge.film.vision_qc import (
    MAX_RETRIES,
    VISION_DIMENSIONS,
    FilmVisionQcEngine,
    VisionQcReport,
    deterministic_scores,
    gate_passed,
    overall_from_dimensions,
)
from novel_forge.film.visual_continuity import (
    build_visual_state_timeline,
    enrich_production_bible,
    extract_visual_motifs,
    load_accepted_ledger_entries,
)
from novel_forge.persistence.models import ProjectLayout


def _bible() -> ProductionBible:
    return ProductionBible(
        project_id="demo",
        title="演示项目",
        style=FilmStyleLock(visual_thesis="冷峻都市影像", texture_medium="cinematic live action"),
        characters=[
            ProductionCharacter(
                character_id="char-a",
                name="林一",
                shot_language_seed="平视、固定、85mm",
                screen_identity=ScreenIdentityLock(
                    facial_anchors=["方下巴", "单眼皮"],
                    silhouette="高瘦轮廓",
                    signature_props=["旧怀表"],
                    visual_state_timeline=["第3章 · 左臂受伤缠绷带"],
                ),
            )
        ],
        locations=[
            ProductionLocation(
                location_id="loc-a",
                name="旧电台",
                spatial_layout="狭长控制室",
                key_light="窗侧逆光",
                color_mood="冷青灰",
                weather_states=["雨夜"],
            )
        ],
    )


def _shot(bible: ProductionBible, *, prompt: str = "") -> FilmShot:
    thesis = bible.style.visual_thesis
    return FilmShot(
        shot_id="sc01-sh-01",
        scene_id="sc01",
        shot_number=1,
        title="建立镜头",
        action="林一推门进入控制室",
        language=ShotLanguage(composition="主体居中，轴线清晰"),
        prompt=prompt or f"{thesis}；旧电台；建立镜头；cinematic live action",
        character_ids=["char-a"],
        location_id="loc-a",
    )


def _state(bible: ProductionBible, shot: FilmShot) -> FilmStudioState:
    return FilmStudioState(
        project_id="demo",
        project_title="演示项目",
        production_bible=bible,
        screenplay=Screenplay(title="演示剧本"),
        timeline=FilmTimeline(name="主时间线"),
        shots=[shot],
    )


# ── B6-1: 资产卡与镜头种子 ────────────────────────────────────────────────


def test_variant_assets_cover_g4_types() -> None:
    assets = build_variant_assets(_bible())
    types = {asset.asset_type for asset in assets}
    assert {
        ASSET_TYPE_EXPRESSION_SHEET,
        ASSET_TYPE_POSE_SHEET,
        ASSET_TYPE_PROP_SHEET,
        ASSET_TYPE_SCENE_VARIANTS,
    } <= types
    prop_sheet = next(item for item in assets if item.asset_type == ASSET_TYPE_PROP_SHEET)
    assert "旧怀表" in prop_sheet.prompt
    # 面板数量强约束 + 镜头绕行描述法 must be templated into every sheet
    for asset in assets:
        assert "面板" in asset.prompt
        assert "镜头绕行" in asset.prompt


def test_contact_sheet_grid_prompt() -> None:
    bible = _bible()
    shots = [_shot(bible)]
    sheet = build_storyboard_contact_sheet(shots, bible)
    assert sheet is not None
    assert sheet.asset_type == ASSET_TYPE_STORYBOARD_CONTACT_SHEET
    assert "1格" in sheet.prompt
    assert build_storyboard_contact_sheet([], bible) is None


# ── B6-2: 视觉状态时间线与视觉母题 ────────────────────────────────────────


def _ledger_entry(
    *, chapter: int, summary: str, delta_type: str = "character_state", verdict: str = "accept"
) -> dict[str, Any]:
    return {
        "entry_id": f"e-{chapter}-{summary[:4]}",
        "chapter_number": chapter,
        "candidate_id": "c1",
        "delta_type": delta_type,
        "summary": summary,
        "decision": {"verdict": verdict},
        "evidence_status": "active",
    }


def test_ledger_filters_and_timeline_attribution(tmp_path: Path) -> None:
    layout = ProjectLayout(tmp_path / "demo")
    layout.ensure_dirs()
    ledger = layout.narrative_state_dir / "state_ledger.jsonl"
    ledger.parent.mkdir(parents=True, exist_ok=True)
    entries = [
        _ledger_entry(chapter=2, summary="林一换上深绿风衣"),
        _ledger_entry(chapter=5, summary="林一左臂受伤", delta_type="character_state"),
        _ledger_entry(chapter=6, summary="被拒绝的候选", verdict="reject"),
        _ledger_entry(chapter=7, summary="知识类变化", delta_type="knowledge"),
    ]
    ledger.write_text("\n".join(json.dumps(item, ensure_ascii=False) for item in entries), encoding="utf-8")
    accepted = load_accepted_ledger_entries(layout.narrative_state_dir)
    assert [item["chapter_number"] for item in accepted] == [2, 5]
    timeline = build_visual_state_timeline(accepted, ["林一", "路人甲"])
    assert timeline["林一"] == ["第2章 · 林一换上深绿风衣", "第5章 · 林一左臂受伤"]
    assert "路人甲" not in timeline


def test_motif_extraction_orders_by_recurrence() -> None:
    motif_state = {
        "motifs": {
            "m1": {"name": "雨", "occurrence_count": 9, "thematic_meaning": "洗刷真相", "retired": False},
            "m2": {"name": "怀表", "occurrence_count": 3, "thematic_meaning": "", "retired": False},
            "m3": {"name": "孤灯", "occurrence_count": 1, "retired": False},
            "m4": {"name": "旧磁带", "occurrence_count": 5, "retired": True},
        }
    }
    assert extract_visual_motifs(motif_state) == ["雨（洗刷真相）", "怀表"]


def test_enrich_production_bible_injects_timeline_and_motifs(tmp_path: Path) -> None:
    layout = ProjectLayout(tmp_path / "demo")
    layout.ensure_dirs()
    ledger = layout.narrative_state_dir / "state_ledger.jsonl"
    ledger.write_text(
        json.dumps(_ledger_entry(chapter=4, summary="林一剪去长发"), ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    motif_path = layout.memory_dir / "motif_state.json"
    motif_path.write_text(
        json.dumps(
            {"motifs": {"m1": {"name": "雨", "occurrence_count": 4, "retired": False}}},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    bible = enrich_production_bible(_bible(), layout)
    character = bible.characters[0]
    assert "第4章 · 林一剪去长发" in character.screen_identity.visual_state_timeline
    assert "第3章 · 左臂受伤缠绷带" in character.screen_identity.visual_state_timeline
    assert bible.style.visual_motifs == ["雨"]


# ── B6-3: 视觉质检两级门禁 ────────────────────────────────────────────────


def test_deterministic_scores_and_gate() -> None:
    bible = _bible()
    shot = _shot(bible)
    scores = deterministic_scores(shot, bible)
    assert {item.dimension for item in scores} == {dim for dim, _n, _d in VISION_DIMENSIONS}
    assert all(0.0 <= item.score <= 10.0 for item in scores)
    overall = overall_from_dimensions(scores)
    assert overall <= min(item.score for item in scores) + 2.0
    report = VisionQcReport(target_id="s1", dimensions=scores, overall_score=overall)
    report.passed = gate_passed(report)
    # prompt carries thesis + medium + key light + identity anchors → deliverable
    assert report.passed


async def test_evaluate_blocks_on_signal_failure() -> None:
    engine = FilmVisionQcEngine()
    state = _state(_bible(), _shot(_bible()))
    failed_signal = QcReport(
        target_id="sc01-sh-01",
        target_type="shot",
        checks=[QcCheck(name="materialized", status=QcCheckStatus.FAIL)],
    )
    report = await engine.evaluate(state, "sc01-sh-01", signal_report=failed_signal)
    assert not report.passed
    assert report.overall_score == 0.0


async def test_evaluate_retries_capped_and_router_merge() -> None:
    low_scores = [
        {"id": dim, "score": 4.0, "note": "待改进"} for dim, _n, _d in VISION_DIMENSIONS
    ]

    class StubRouter:
        def __init__(self) -> None:
            self.calls = 0

        async def route(self, _request: Any) -> Any:
            self.calls += 1

            class _Response:
                content = json.dumps(
                    {"dimensions": low_scores, "overall_score": 4.0, "summary": "低分"},
                    ensure_ascii=False,
                )

            return _Response()

    router = StubRouter()
    engine = FilmVisionQcEngine(router=router)
    state = _state(_bible(), _shot(_bible()))
    report = await engine.evaluate(state, "sc01-sh-01")
    assert not report.passed
    assert report.retries_used == MAX_RETRIES
    assert router.calls == MAX_RETRIES + 1


async def test_evaluate_rejects_unknown_dimension_ids() -> None:
    class StubRouter:
        async def route(self, _request: Any) -> Any:
            class _Response:
                content = json.dumps(
                    {"dimensions": [{"id": "unknown_dim", "score": 9.0, "note": ""}]},
                    ensure_ascii=False,
                )

            return _Response()

    engine = FilmVisionQcEngine(router=StubRouter())
    state = _state(_bible(), _shot(_bible()))
    report = await engine.score_shot(state, "sc01-sh-01")
    # invalid LLM output falls back to deterministic scoring
    assert {item.dimension for item in report.dimensions} == {
        dim for dim, _n, _d in VISION_DIMENSIONS
    }


# ── B6-4: 风格库与好莱坞导出 ────────────────────────────────────────────────


def test_style_library_shape_and_matching() -> None:
    assert len(STYLE_LIBRARY) == 36
    assert len({entry.category for entry in STYLE_LIBRARY}) == 12
    assert match_style("武侠", "飘逸江湖").style_id == "action-wuxia"
    assert match_style("赛博朋克", "未来都市").category == "科幻未来"
    assert match_style("", "") is None
    choices = style_decision_choices("悬疑", "惊悚")
    assert 2 <= len(choices) <= 4
    assert all("（" in choice for choice in choices)


def test_hollywood_screenplay_rendering() -> None:
    bible = _bible()
    screenplay = Screenplay(
        title="长夜电台",
        synopsis="失声主持人面对真相",
        scenes=[
            ScreenplayScene(
                scene_id="sc01",
                sequence_number=1,
                heading="内景 旧电台控制室 夜",
                objective="林一重启深夜节目",
                lines=[
                    ScreenplayLine(kind="dialogue", speaker="林一", text="今晚我们聊聊真相。"),
                    ScreenplayLine(kind="action", text="红灯亮起，磁带开始转动。"),
                ],
            )
        ],
    )
    shots = [_shot(bible)]
    text = render_hollywood_screenplay(screenplay, shots)
    assert "INT." in text
    assert "NIGHT" in text
    assert "内景" not in text
    assert "SHOT 01" in text
    assert "林一" in text
    assert "LOGLINE: 失声主持人面对真相" in text
    assert "en" in LOCALIZATION_GUIDANCE and "zh" in LOCALIZATION_GUIDANCE


def test_character_shot_seed_mapping() -> None:
    # B6-1: ProductionCharacter carries shot_language_seed for downstream shots
    character = _bible().characters[0]
    assert character.shot_language_seed == "平视、固定、85mm"
    location = _bible().locations[0]
    assert location.key_light == "窗侧逆光"
    assert location.color_mood == "冷青灰"
