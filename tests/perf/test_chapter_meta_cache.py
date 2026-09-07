"""P0-6: Defer per-chapter eval/continuity JSON reads via sidecar cache.

``_collect_chapters`` previously did 3 JSON loads per chapter:
1. ``chapter_meta_path`` (chapter_meta.json)
2. ``eval_report_path`` (eval_report.json)
3. ``continuity_report_path`` (continuity_report.json)

After the optimization, a sidecar cache file ``_chapter_meta_cache.json``
in the project directory stores the extracted scores. Subsequent calls
read the sidecar instead of re-loading the individual report files.

The sidecar is invalidated (deleted) when a chapter file is written
(chapter completion event), so it's always fresh on the next read.

This test verifies:
1. ``_collect_chapters`` does ≤ 1 JSON read per chapter (the sidecar).
2. The sidecar cache is populated after the first call.
3. Deleting the sidecar forces a rebuild (invalidation works).
4. ChapterSummary fields are still populated correctly.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from novel_forge.persistence.filesystem import FileSystemStorage
from novel_forge.persistence.models import ProjectLayout
from novel_forge.workspace.projects import ProjectInspector


@pytest.fixture
def project_dir(tmp_path: Path) -> Path:
    """Create a minimal project directory structure with chapters and reports."""
    proj = tmp_path / "test_novel"
    proj.mkdir()

    chapters_dir = proj / "chapters"
    chapters_dir.mkdir()

    reports_dir = proj / "reports"
    reports_dir.mkdir()

    for ch_num in range(1, 4):
        chapter_file = chapters_dir / f"chapter_{ch_num}.md"
        chapter_file.write_text(
            f"# 第{ch_num}章\n\n这是第{ch_num}章的内容。" * 100,
            encoding="utf-8",
        )

        meta_file = chapters_dir / f"chapter_{ch_num:03d}_meta.json"
        meta_file.write_text(
            json.dumps({
                "chapter_number": ch_num,
                "title": f"第{ch_num}章 测试",
                "word_count": 3000,
                "edit_rounds": 1,
                "tokens_used": 5000,
                "cost_usd": 0.01,
                "schema_version": 1,
            }),
            encoding="utf-8",
        )

        eval_file = reports_dir / f"chapter_{ch_num:03d}_eval.json"
        eval_file.write_text(
            json.dumps({
                "overall_score": 7.0 + ch_num * 0.5,
                "dimensions": {},
                "summary": "Good",
                "schema_version": 1,
            }),
            encoding="utf-8",
        )

        cont_file = reports_dir / f"chapter_{ch_num:03d}_continuity.json"
        cont_file.write_text(
            json.dumps({
                "continuity_score": 8.0 - ch_num * 0.2,
                "issues": [],
                "schema_version": 1,
            }),
            encoding="utf-8",
        )

    return proj


class TestChapterMetaCache:
    """Tests for sidecar chapter meta cache in _collect_chapters."""

    def test_collect_chapters_populates_sidecar(
        self,
        project_dir: Path,
    ) -> None:
        storage = FileSystemStorage(project_dir.parent)
        inspector = ProjectInspector(storage)
        layout = ProjectLayout(project_dir)

        chapters = inspector._collect_chapters(layout, outline=None)

        assert len(chapters) == 3

        sidecar_path = project_dir / "_chapter_meta_cache.json"
        assert sidecar_path.exists(), (
            "Sidecar cache _chapter_meta_cache.json should be created after "
            "first _collect_chapters call"
        )

        sidecar_data = json.loads(sidecar_path.read_text(encoding="utf-8"))
        assert "1" in sidecar_data or 1 in sidecar_data

    def test_collect_chapters_reads_at_most_one_json_per_chapter(
        self,
        project_dir: Path,
    ) -> None:
        storage = FileSystemStorage(project_dir.parent)
        inspector = ProjectInspector(storage)
        layout = ProjectLayout(project_dir)

        inspector._collect_chapters(layout, outline=None)

        original_load_json = storage.load_json
        json_load_paths: list[Path] = []

        def tracking_load_json(path: Path) -> dict[str, Any]:
            json_load_paths.append(path)
            return original_load_json(path)

        with patch.object(storage, "load_json", side_effect=tracking_load_json):
            chapters = inspector._collect_chapters(layout, outline=None)

        sidecar_name = "_chapter_meta_cache.json"
        per_chapter_loads = [
            p for p in json_load_paths
            if sidecar_name not in str(p)
        ]

        num_chapters = len(chapters)
        assert len(per_chapter_loads) <= num_chapters, (
            f"Expected ≤ {num_chapters} per-chapter JSON loads (1 per chapter), "
            f"got {len(per_chapter_loads)}. Sidecar cache not being used."
        )

    def test_sidecar_invalidation_forces_rebuild(
        self,
        project_dir: Path,
    ) -> None:
        storage = FileSystemStorage(project_dir.parent)
        inspector = ProjectInspector(storage)
        layout = ProjectLayout(project_dir)

        chapters1 = inspector._collect_chapters(layout, outline=None)
        sidecar_path = project_dir / "_chapter_meta_cache.json"
        assert sidecar_path.exists()

        sidecar_path.unlink()
        assert not sidecar_path.exists()

        chapters2 = inspector._collect_chapters(layout, outline=None)
        assert sidecar_path.exists()

        assert len(chapters1) == len(chapters2)
        for c1, c2 in zip(chapters1, chapters2, strict=True):
            assert c1.chapter_number == c2.chapter_number
            assert c1.overall_score == c2.overall_score
            assert c1.continuity_score == c2.continuity_score

    def test_chapter_summary_fields_correct(
        self,
        project_dir: Path,
    ) -> None:
        storage = FileSystemStorage(project_dir.parent)
        inspector = ProjectInspector(storage)
        layout = ProjectLayout(project_dir)

        chapters = inspector._collect_chapters(layout, outline=None)

        assert len(chapters) == 3

        ch1 = next(c for c in chapters if c.chapter_number == 1)
        assert ch1.overall_score == pytest.approx(7.5, abs=0.01)
        assert ch1.continuity_score == pytest.approx(7.8, abs=0.01)
        assert ch1.word_count > 0
        assert ch1.title != ""

        ch2 = next(c for c in chapters if c.chapter_number == 2)
        assert ch2.overall_score == pytest.approx(8.0, abs=0.01)
        assert ch2.continuity_score == pytest.approx(7.6, abs=0.01)

        ch3 = next(c for c in chapters if c.chapter_number == 3)
        assert ch3.overall_score == pytest.approx(8.5, abs=0.01)
        assert ch3.continuity_score == pytest.approx(7.4, abs=0.01)
