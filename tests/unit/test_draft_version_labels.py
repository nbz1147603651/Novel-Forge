from __future__ import annotations

from pathlib import Path

from novel_forge.core.utils.version_diff import list_draft_versions
from novel_forge.desktop.document_presenter import draft_display_name


def test_desktop_draft_display_name_uses_reserved_stage_labels() -> None:
    assert draft_display_name("v0_draft") == "DRAFT 原稿"
    assert draft_display_name("v1_wave") == "初稿成章"
    assert draft_display_name("v2_edited") == "第2轮润色"
    assert draft_display_name("v95_edited") == "归档前字数重整"
    assert draft_display_name("v96_edited") == "追读力修复"
    assert draft_display_name("v97_edited") == "因果修复"
    assert draft_display_name("v98_edited") == "代词修复"
    assert draft_display_name("v99_edited") == "对齐修复"
    assert draft_display_name("v_final_review") == "评审定稿"


def test_version_diff_labels_reserved_stage_versions(tmp_path: Path) -> None:
    chapter_dir = tmp_path / "chapter_002"
    chapter_dir.mkdir()
    for stem in ("v0_draft", "v1_wave", "v1_edited", "v95_edited", "v96_edited", "v97_edited"):
        (chapter_dir / f"{stem}.md").write_text("正文内容。", encoding="utf-8")

    versions = list_draft_versions(tmp_path, 2)
    labels_by_version = {version.version: version.label for version in versions}

    assert labels_by_version[0] == "DRAFT 原稿"
    assert labels_by_version[1] == "初稿成章"
    assert labels_by_version[95] == "归档前字数重整"
    assert labels_by_version[96] == "追读力修复"
    assert labels_by_version[97] == "因果修复"
