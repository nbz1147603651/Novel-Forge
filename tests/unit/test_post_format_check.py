"""Unit tests for scripts/post_format_check.py."""

from __future__ import annotations

import json
from pathlib import Path

from novel_forge.pipeline.steps.chapter_quality_prescreen import ChapterQualityPrescreen
from scripts import post_format_check


def _write_project(root: Path, name: str, chapters: dict[int, str]) -> Path:
    project = root / name
    chapters_dir = project / "chapters"
    chapters_dir.mkdir(parents=True)
    for chapter_number, text in chapters.items():
        (chapters_dir / f"chapter_{chapter_number:03d}.md").write_text(
            text,
            encoding="utf-8",
        )
    return project


def test_scan_project_returns_numeric_chapter_order(tmp_path: Path) -> None:
    project = _write_project(
        tmp_path,
        "demo",
        {
            10: "第十章。" * 10,
            2: "第二章。。" * 10,
        },
    )
    prescreen = ChapterQualityPrescreen(min_chars=1, max_chars=1000)

    reports, files = post_format_check.scan_project(project, prescreen=prescreen)

    assert [path.name for path in files] == ["chapter_002.md", "chapter_010.md"]
    assert [report.chapter_number for report in reports] == [2, 10]
    assert "double_punctuation" in {hit.kind for hit in reports[0].hits}


def test_main_returns_2_for_missing_project_root(tmp_path: Path, capsys) -> None:
    missing = tmp_path / "missing"

    exit_code = post_format_check.main(["--project-root", str(missing)])

    captured = capsys.readouterr()
    assert exit_code == 2
    assert "Project root not found" in captured.err


def test_json_output_accepts_paths_outside_repo(tmp_path: Path, capsys) -> None:
    project = _write_project(tmp_path, "external", {1: "第一章。" * 10})

    exit_code = post_format_check.main(
        [
            "--project-root",
            str(project),
            "--min-chars",
            "1",
            "--max-chars",
            "1000",
            "--json",
        ]
    )

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert exit_code == 0
    assert payload["projects_scanned"] == 1
    assert payload["reports"][0]["chapter_file"].endswith("chapter_001.md")


def test_multi_project_dry_run_apply_fixes_does_not_require_yes(
    tmp_path: Path,
    capsys,
) -> None:
    _write_project(tmp_path, "one", {1: "第一章。。" * 10})
    _write_project(tmp_path, "two", {1: "第二章。。" * 10})

    exit_code = post_format_check.main(
        [
            "--project-root",
            str(tmp_path),
            "--min-chars",
            "1",
            "--max-chars",
            "1000",
            "--apply-fixes",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "[DRY-RUN]" in captured.err


def test_multi_project_in_place_requires_yes(tmp_path: Path, capsys) -> None:
    _write_project(tmp_path, "one", {1: "第一章。。" * 10})
    _write_project(tmp_path, "two", {1: "第二章。。" * 10})

    exit_code = post_format_check.main(
        [
            "--project-root",
            str(tmp_path),
            "--min-chars",
            "1",
            "--max-chars",
            "1000",
            "--apply-fixes",
            "--in-place",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 2
    assert "requires --yes" in captured.err


def test_single_project_in_place_applies_static_fixes(tmp_path: Path, capsys) -> None:
    project = _write_project(tmp_path, "demo", {1: "第一章。。她她她站住。" * 10})
    chapter = project / "chapters" / "chapter_001.md"

    exit_code = post_format_check.main(
        [
            "--project-root",
            str(project),
            "--min-chars",
            "1",
            "--max-chars",
            "1000",
            "--apply-fixes",
            "--in-place",
        ]
    )

    captured = capsys.readouterr()
    fixed = chapter.read_text(encoding="utf-8")
    assert exit_code == 0
    assert "[FIXED]" in captured.err
    assert "。。" not in fixed
    assert "她她她" not in fixed


def test_quote_balance_scan_is_opt_in(tmp_path: Path, capsys) -> None:
    project = _write_project(tmp_path, "demo", {1: "她说：“风来了。" * 10})

    default_exit = post_format_check.main(
        [
            "--project-root",
            str(project),
            "--min-chars",
            "1",
            "--max-chars",
            "1000",
            "--json",
        ]
    )
    default_payload = json.loads(capsys.readouterr().out)

    checked_exit = post_format_check.main(
        [
            "--project-root",
            str(project),
            "--min-chars",
            "1",
            "--max-chars",
            "1000",
            "--json",
            "--check-quote-balance",
        ]
    )
    checked_payload = json.loads(capsys.readouterr().out)

    default_kinds = {
        hit["kind"]
        for hit in default_payload["reports"][0]["report"]["hits"]
    }
    checked_kinds = {
        hit["kind"]
        for hit in checked_payload["reports"][0]["report"]["hits"]
    }
    assert default_exit == 0
    assert checked_exit == 0
    assert "unbalanced_quote" not in default_kinds
    assert "unbalanced_quote" in checked_kinds
