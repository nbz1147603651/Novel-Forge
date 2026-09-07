"""Tests for mtime-triggered卷帙 page rebuild (Task 5).

Verifies that ``ProjectsPage._project_mtimes_changed`` correctly detects
modifications to key project files (spec.json, story_bible.json, etc.)
and new files appearing in the plans/reports directories.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

from PySide6.QtWidgets import QApplication

from novel_forge.desktop.pages.standalone.projects_page import ProjectsPage


def _make_app() -> QApplication:
    return QApplication.instance() or QApplication([])


def test_project_mtimes_changed_initially_false(tmp_path: Path) -> None:
    _make_app()
    # Create a spec.json
    (tmp_path / "spec.json").write_text("{}", encoding="utf-8")
    page = ProjectsPage()
    # First scan: sets baseline, returns False
    assert page._project_mtimes_changed(tmp_path) is False
    # Second scan: no change
    assert page._project_mtimes_changed(tmp_path) is False


def test_project_mtimes_changed_detects_modification(tmp_path: Path) -> None:
    _make_app()
    spec = tmp_path / "spec.json"
    spec.write_text("{}", encoding="utf-8")
    page = ProjectsPage()
    # Baseline
    page._project_mtimes_changed(tmp_path)
    # Modify mtime to future
    new_mtime = time.time() + 100
    os.utime(spec, (new_mtime, new_mtime))
    assert page._project_mtimes_changed(tmp_path) is True


def test_project_mtimes_changed_detects_new_file(tmp_path: Path) -> None:
    _make_app()
    # Start with no plans dir
    page = ProjectsPage()
    page._project_mtimes_changed(tmp_path)  # baseline
    # Add a plans dir with a new file
    plans = tmp_path / "plans"
    plans.mkdir()
    (plans / "blueprint.json").write_text("{}", encoding="utf-8")
    assert page._project_mtimes_changed(tmp_path) is True


def test_project_mtimes_changed_detects_nested_report_revision(tmp_path: Path) -> None:
    _make_app()
    revisions = tmp_path / "reports" / "revisions"
    revisions.mkdir(parents=True)
    report = revisions / "chapter_1_polish_chapter.json"
    report.write_text("{}", encoding="utf-8")
    page = ProjectsPage()
    page._project_mtimes_changed(tmp_path)

    report.write_text('{"changed": true}', encoding="utf-8")
    new_mtime = time.time() + 100
    os.utime(report, (new_mtime, new_mtime))

    assert page._project_mtimes_changed(tmp_path) is True
