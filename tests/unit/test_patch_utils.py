from __future__ import annotations

from types import SimpleNamespace

from novel_forge.core.utils.patch_utils import (
    apply_patches,
    build_issue_windows,
    resolve_location_hint,
    split_paragraphs,
)


def test_resolve_location_hint_supports_paragraph_range() -> None:
    paragraphs = ["p1", "p2", "p3", "p4", "p5", "p6"]
    indices = resolve_location_hint(paragraphs, "第3-4段")
    assert indices == [2, 3]


def test_build_issue_windows_tracks_editable_span_inside_context_window() -> None:
    paragraphs = ["p1", "p2", "p3", "p4", "p5", "p6"]
    issue = SimpleNamespace(location="第3-4段", evidence="")
    windows = build_issue_windows(paragraphs, [issue], context_size=2)
    assert len(windows) == 1
    window = windows[0]
    assert window["para_start"] == 0
    assert window["para_end"] == 5
    assert window["editable_para_start"] == 2
    assert window["editable_para_end"] == 3


def test_apply_patches_restricts_change_to_editable_subwindow() -> None:
    text = "\n\n".join(
        [
            "第一段：上下文A",
            "第二段：上下文B",
            "第三段：目标句。",
            "第四段：目标句。",
            "第五段：上下文C",
            "第六段：上下文D",
        ]
    )
    patches = [
        {
            "original": "目标句。",
            "replacement": "修复句。",
            "para_start": 0,
            "para_end": 5,
            "editable_para_start": 2,
            "editable_para_end": 3,
        }
    ]

    revised, applied = apply_patches(text, patches)
    revised_paragraphs = split_paragraphs(revised)

    assert applied == 1
    assert revised_paragraphs[0] == "第一段：上下文A"
    assert revised_paragraphs[1] == "第二段：上下文B"
    assert revised_paragraphs[4] == "第五段：上下文C"
    assert revised_paragraphs[5] == "第六段：上下文D"
    assert "修复句。" in revised_paragraphs[2] or "修复句。" in revised_paragraphs[3]
