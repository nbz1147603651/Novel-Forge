from __future__ import annotations

from novel_forge.core.schemas.outline import (
    ChapterOutline,
    StoryOutline,
    normalize_outline_beat_text,
)
from novel_forge.pipeline.long.services.blueprint.outline_helpers import (
    normalize_chapter_outline_beats,
    normalize_outline_chapters,
)


def test_normalize_outline_beat_text_strips_display_labels() -> None:
    assert (
        normalize_outline_beat_text("【节拍1：前世线索】程砚秋发现女工照片")
        == "程砚秋发现女工照片"
    )
    assert normalize_outline_beat_text("【记忆冲击】沈念卿收到匿名包裹") == "沈念卿收到匿名包裹"
    assert normalize_outline_beat_text("节拍2：陆云峥选择克制守护") == "陆云峥选择克制守护"
    assert normalize_outline_beat_text("沈念卿深夜惊醒") == "沈念卿深夜惊醒"


def test_normalize_chapter_outline_beats_preserves_plot_text_only() -> None:
    chapter = ChapterOutline(
        chapter_number=1,
        title="钟楼",
        goal="推进记忆线",
        beats_summary=[
            "【节拍1：梦境】沈念卿梦见外滩钟楼",
            "【线索】怀表停止在十三下",
            "节拍3：陆云峥看清梦中人的轮廓",
        ],
        main_plot_points=["梦境相连"],
        expected_word_count=3000,
    )

    normalized = normalize_chapter_outline_beats(chapter)

    assert normalized.beats_summary == [
        "沈念卿梦见外滩钟楼",
        "怀表停止在十三下",
        "陆云峥看清梦中人的轮廓",
    ]


def test_normalize_outline_chapters_strips_beat_labels_in_final_outline() -> None:
    outline = StoryOutline(
        total_chapters=1,
        chapters=[
            ChapterOutline(
                chapter_number=1,
                title="旧照",
                goal="揭示线索",
                beats_summary=["【节拍1：旧照】林绾绾收到修复照片"],
                main_plot_points=["照片出现"],
                expected_word_count=500,
            )
        ],
    )

    normalized = normalize_outline_chapters(outline, total_chapters=1, words_per_chapter=4500)

    assert normalized.chapters[0].beats_summary == ["林绾绾收到修复照片"]
