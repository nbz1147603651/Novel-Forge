"""Tests for the two-level finished-text backflow (Batch 2, P0-2).

Acceptance criteria pinned here:
- level-1 extraction is faithful: dialogue/action come from the real finished
  chapter text and every scene carries ``source_refs`` lineage (chapter +
  paragraph range);
- the outline projection is merged without losing planning metadata;
- level-2 adaptation validation accepts faithful rewrites and rejects drift
  (empty output, invented speakers, dropped cast names, volume blow-up);
- canon stays read-only: the projector never writes upstream artifacts.
"""

from __future__ import annotations

import json
from pathlib import Path

from novel_forge.film.schemas import (
    ProductionBible,
    ProductionCharacter,
    ProductionLocation,
    Screenplay,
    ScreenplayLine,
    ScreenplayScene,
)
from novel_forge.film.screenplay_projection import (
    ChapterScreenplayProjector,
    validate_screenplay_adaptation,
)
from novel_forge.persistence.filesystem import FileSystemStorage
from novel_forge.persistence.models import ProjectLayout

CHAPTER_TEXT = """# 第 1 章 夜访

夜色压下来，旧仓库的铁门半开着。
林远推开门，手电的光柱扫过成堆的木箱。
苏晚站在门口，没有进来。
林远说：“东西应该就在里面。”
苏晚低声回答：“我总觉得有人盯着我们。”
他把手电递过去，自己摸向墙边的电闸。
灯亮了。
满地的脚印，全都朝着同一个方向。
"""

CHAPTER_PLAN = {
    "scene_intents": [
        {
            "scene_id": "ch1-s1",
            "summary": "夜访旧仓库，寻找失踪的货单",
            "purpose": "建立双人同盟与首个悬念",
            "conflict": "黑暗与未知的监视感",
            "required_characters": ["林远", "苏晚"],
            "location": "旧码头仓库",
            "time_marker": "夜",
            "emotional_beat": "压抑→警觉",
            "target_words": 200,
        },
        {
            "scene_id": "ch1-s2",
            "summary": "灯亮后发现统一方向的脚印",
            "purpose": "抛出本章钩子",
            "conflict": "脚印暗示另有其人先到",
            "required_characters": ["林远"],
            "location": "旧码头仓库",
            "time_marker": "夜",
            "emotional_beat": "惊疑",
            "target_words": 100,
        }
    ],
    "emotional_arc": ["压抑", "警觉", "惊疑"],
    "chapter_type": "hook",
}


def _seed_project(tmp_path: Path, *, project_id: str) -> ProjectLayout:
    storage = FileSystemStorage(tmp_path)
    layout = ProjectLayout(storage.project_dir(project_id))
    layout.root.mkdir(parents=True, exist_ok=True)
    layout.chapters_dir.mkdir(parents=True, exist_ok=True)
    layout.chapter_path(1).write_text(CHAPTER_TEXT, encoding="utf-8")
    layout.characters_path.write_text(
        json.dumps(
            {
                "characters": [
                    {"character_id": "char-lin", "name": "林远"},
                    {"character_id": "char-su", "name": "苏晚"},
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    plan_path = layout.chapter_plan_path(1)
    plan_path.parent.mkdir(parents=True, exist_ok=True)
    plan_path.write_text(json.dumps(CHAPTER_PLAN, ensure_ascii=False), encoding="utf-8")
    return layout


def _bible() -> ProductionBible:
    return ProductionBible(
        project_id="book",
        title="夜访测试",
        characters=[
            ProductionCharacter(character_id="char-lin", name="林远"),
            ProductionCharacter(character_id="char-su", name="苏晚"),
        ],
        locations=[
            ProductionLocation(location_id="loc-warehouse", name="旧码头仓库"),
        ],
    )


def test_level1_extraction_is_faithful_with_lineage(tmp_path) -> None:
    layout = _seed_project(tmp_path, project_id="book")
    projector = ChapterScreenplayProjector(layout, "book")

    screenplay = projector.build(bible=_bible())

    assert screenplay.scenes, "finished chapter must project into scenes"
    # Two scene intents → two scenes, split deterministically.
    assert len(screenplay.scenes) == 2
    all_lines = [line for scene in screenplay.scenes for line in scene.lines]
    dialogue = [line for line in all_lines if line.kind == "dialogue"]
    # The two quoted lines survive verbatim (extraction fidelity).
    texts = {line.text for line in dialogue}
    assert "东西应该就在里面。" in texts
    assert "我总觉得有人盯着我们。" in texts
    # Speaker attribution uses the registered cast.
    speakers = {line.speaker for line in dialogue}
    assert speakers == {"林远", "苏晚"}

    # Lineage: every scene anchors back to chapter + paragraph range.
    for scene in screenplay.scenes:
        assert scene.source_chapter == 1
        assert scene.source_refs, "scene must carry source_refs lineage"
        ref = scene.source_refs[0]
        assert ref.chapter_number == 1
        assert ref.paragraph_start >= 1
        assert ref.paragraph_end >= ref.paragraph_start
    first_ref = screenplay.scenes[0].source_refs[0]
    second_ref = screenplay.scenes[1].source_refs[0]
    assert first_ref.scene_intent_id == "ch1-s1"
    assert second_ref.scene_intent_id == "ch1-s2"
    # Ranges are contiguous and cover the whole chapter.
    assert first_ref.paragraph_start == 1
    assert second_ref.paragraph_end == len(
        [line for line in CHAPTER_TEXT.splitlines() if line.strip() and not line.startswith("#")]
    )
    assert second_ref.paragraph_start == first_ref.paragraph_end + 1

    # Intent metadata flows into scene fields.
    assert screenplay.scenes[0].location_id == "loc-warehouse"
    assert screenplay.scenes[0].time_of_day == "夜"
    assert screenplay.scenes[0].conflict == "黑暗与未知的监视感"

    # Canon is read-only: projection creates nothing upstream.
    assert sorted(layout.chapters_dir.iterdir()) == [layout.chapter_path(1)]


def test_merge_keeps_outline_scenes_for_unwritten_chapters(tmp_path) -> None:
    layout = _seed_project(tmp_path, project_id="book")
    projector = ChapterScreenplayProjector(layout, "book")
    chapter_projection = projector.build(bible=_bible())

    outline_scene_ch1 = ScreenplayScene(
        scene_id="sc-001",
        sequence_number=1,
        heading="大纲场次（第1章）",
        objective="大纲目标",
        conflict="大纲冲突",
        source_chapter=1,
    )
    outline_scene_ch2 = ScreenplayScene(
        scene_id="sc-002",
        sequence_number=2,
        heading="大纲场次（第2章，尚未成稿）",
        objective="保留我",
        source_chapter=2,
    )
    outline = Screenplay(
        title="夜访测试",
        scenes=[outline_scene_ch1, outline_scene_ch2],
    )

    merged = ChapterScreenplayProjector.merge_with_outline(chapter_projection, outline)

    # Chapter 1 scenes come from the finished text (with lineage)…
    ch1_scenes = [scene for scene in merged.scenes if scene.source_chapter == 1]
    assert ch1_scenes and all(scene.source_refs for scene in ch1_scenes)
    assert all(scene.heading != "大纲场次（第1章）" for scene in ch1_scenes)
    # …while the unwritten chapter 2 keeps its outline scene.
    ch2_scenes = [scene for scene in merged.scenes if scene.source_chapter == 2]
    assert len(ch2_scenes) == 1 and ch2_scenes[0].objective == "保留我"
    # Sequences are renumbered deterministically.
    assert [scene.sequence_number for scene in merged.scenes] == list(
        range(1, len(merged.scenes) + 1)
    )


def _scene(lines: list[ScreenplayLine], **overrides: object) -> ScreenplayScene:
    payload = {
        "scene_id": "sc-001",
        "sequence_number": 1,
        "heading": "场次",
        "lines": lines,
    }
    payload.update(overrides)
    return ScreenplayScene(**payload)  # type: ignore[arg-type]


def test_adaptation_validation_accepts_faithful_rewrite() -> None:
    deterministic = _scene(
        [
            ScreenplayLine(kind="action", text="他心里明白这是最后一次见面。"),
            ScreenplayLine(kind="dialogue", speaker="林远", text="别送了。"),
        ]
    )
    adapted = _scene(
        [
            ScreenplayLine(kind="action", text="他伸手触碰门把，又停在半空。最后一次见面，他心里明白。"),
            ScreenplayLine(kind="dialogue", speaker="林远", text="别送了。"),
        ]
    )
    passed, reason = validate_screenplay_adaptation(
        deterministic, adapted, cast_names=("林远", "苏晚")
    )
    assert passed, reason


def test_adaptation_validation_rejects_drift() -> None:
    deterministic = _scene(
        [
            ScreenplayLine(kind="dialogue", speaker="林远", text="东西应该就在里面。"),
            ScreenplayLine(kind="dialogue", speaker="苏晚", text="我总觉得有人盯着我们。"),
        ]
    )

    empty = _scene([])
    passed, reason = validate_screenplay_adaptation(deterministic, empty)
    assert not passed and reason == "empty_adaptation"

    invented = _scene(
        [
            ScreenplayLine(kind="dialogue", speaker="林远", text="东西应该就在里面。"),
            ScreenplayLine(kind="dialogue", speaker="苏晚", text="我总觉得有人盯着我们。"),
            ScreenplayLine(kind="dialogue", speaker="神秘人", text="你们逃不掉的。"),
        ]
    )
    passed, reason = validate_screenplay_adaptation(
        deterministic, invented, cast_names=("林远", "苏晚")
    )
    assert not passed and reason.startswith("invented_speakers")

    missing_speaker = _scene(
        [ScreenplayLine(kind="dialogue", speaker="林远", text="东西应该就在里面。")]
    )
    passed, reason = validate_screenplay_adaptation(deterministic, missing_speaker)
    assert not passed and reason.startswith("missing_speakers")

    blow_up = _scene(
        [
            ScreenplayLine(
                kind="action",
                text="（大量新增情节）" * 40,
            ),
            ScreenplayLine(kind="dialogue", speaker="林远", text="东西应该就在里面。"),
            ScreenplayLine(kind="dialogue", speaker="苏晚", text="我总觉得有人盯着我们。"),
        ]
    )
    passed, reason = validate_screenplay_adaptation(deterministic, blow_up)
    assert not passed and reason.startswith("length_ratio_too_high")


def test_adapt_screenplay_prompt_renders_and_contract_registered() -> None:
    from novel_forge.common.constants import TaskType
    from novel_forge.core.format_contracts import get_task_format_contract
    from novel_forge.prompts.registry import PromptRegistry

    contract = get_task_format_contract(TaskType.ADAPT_SCREENPLAY)
    assert contract is not None
    assert "scenes" in contract.required_top_level_keys

    rendered = PromptRegistry().render(
        TaskType.ADAPT_SCREENPLAY,
        title="夜访测试",
        language="zh",
        direction={"chapter_arcs": []},
        cast_names=["林远", "苏晚"],
        scenes=[{"heading": "场次", "lines": []}],
    )
    assert "第一级投影场次" in rendered
    assert "林远" in rendered
