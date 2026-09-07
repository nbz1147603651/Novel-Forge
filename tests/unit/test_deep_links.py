"""Unit tests for deep-link generation, parsing, and rendering integration."""

from __future__ import annotations

import os
import re

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from novel_forge.desktop.pages.standalone.renderer_html import (  # noqa: E402
    chapter_deep_link,
    parse_deep_link,
)


class TestChapterDeepLink:
    """Tests for chapter_deep_link() HTML generation."""

    def test_default_label(self) -> None:
        html = chapter_deep_link("my_project", 5)
        assert 'class="deep-link"' in html
        assert "nf://chapter/my_project/5" in html
        assert "第 5 章" in html

    def test_custom_label(self) -> None:
        html = chapter_deep_link("proj", 3, "跳转到第三章")
        assert "跳转到第三章" in html
        assert "nf://chapter/proj/3" in html

    def test_html_escapes_project_id(self) -> None:
        html = chapter_deep_link('a"b', 1)
        assert "&quot;" in html  # project_id is escaped


class TestParseDeepLink:
    """Tests for parse_deep_link() URL parsing."""

    def test_valid_chapter_link(self) -> None:
        result = parse_deep_link("nf://chapter/my_project/5")
        assert result == {
            "type": "chapter",
            "project_id": "my_project",
            "chapter_number": 5,
        }

    def test_non_nf_scheme(self) -> None:
        assert parse_deep_link("http://example.com") is None

    def test_unknown_type(self) -> None:
        assert parse_deep_link("nf://unknown/a/b") is None

    def test_non_numeric_chapter(self) -> None:
        assert parse_deep_link("nf://chapter/proj/abc") is None

    def test_empty_string(self) -> None:
        assert parse_deep_link("") is None

    def test_partial_path(self) -> None:
        assert parse_deep_link("nf://chapter/proj") is None


class TestRoundTrip:
    """Ensure generate → parse round-trips correctly."""

    @pytest.mark.parametrize(
        "project_id,chapter",
        [
            ("浮京一梦3", 1),
            ("my_project", 52),
            ("test-proj", 100),
        ],
    )
    def test_roundtrip(self, project_id: str, chapter: int) -> None:
        html = chapter_deep_link(project_id, chapter)
        href = re.search(r'href="([^"]+)"', html)
        assert href is not None
        parsed = parse_deep_link(href.group(1))
        assert parsed is not None
        assert parsed["project_id"] == project_id
        assert parsed["chapter_number"] == chapter


class TestRepairReportDeepLinks:
    """Test that render_book_consistency_repair_report embeds deep links."""

    @pytest.fixture(autouse=True)
    def _ensure_qapp(self) -> None:
        from PySide6.QtWidgets import QApplication
        if QApplication.instance() is None:
            QApplication([])

    def test_repair_report_contains_deep_links(self) -> None:
        from novel_forge.desktop.pages.document_renderer_story_artifacts import (
            render_book_consistency_repair_report,
        )

        data = {
            "report_type": "book_consistency_repair_report",
            "project_id": "test_proj",
            "summary": "测试修复报告",
            "analysis": {"issue_count": 3, "consistency_score": 7.5},
            "task_flow": {
                "targeted_chapters": 2,
                "processed_chapters": 2,
                "applied_chapters": 1,
                "failed_chapters": 0,
                "repair_concurrency": 2,
            },
            "matching": {
                "matched_by_ref": 2,
                "matched_by_fuzzy": 1,
                "issue_pool_size": 5,
            },
            "chapters": [
                {
                    "chapter_number": 3,
                    "status": "applied",
                    "continuity_issue_indices": [0, 1],
                    "causal_issue_indices": [],
                    "matched_issue_count": 2,
                    "match_mode": "ref+fuzzy",
                },
                {
                    "chapter_number": 7,
                    "status": "skipped",
                    "reason": "无匹配问题",
                    "continuity_issue_indices": [],
                    "causal_issue_indices": [],
                    "matched_issue_count": 0,
                    "match_mode": "none",
                },
            ],
        }

        browser = render_book_consistency_repair_report(data)
        html = browser.toHtml()

        # Chapter 3 should be a deep link
        assert "nf://chapter/test_proj/3" in html
        # Chapter 7 should also be a deep link
        assert "nf://chapter/test_proj/7" in html
        # Browser should be marked with deep links
        assert browser.property("has_deep_links") is True

    def test_no_deep_links_without_project_id(self) -> None:
        from novel_forge.desktop.pages.document_renderer_story_artifacts import (
            render_book_consistency_repair_report,
        )

        data = {
            "report_type": "book_consistency_repair_report",
            "summary": "无 project_id",
            "analysis": {},
            "task_flow": {},
            "matching": {},
            "chapters": [{"chapter_number": 1, "status": "applied"}],
        }

        browser = render_book_consistency_repair_report(data)
        html = browser.toHtml()
        assert "nf://chapter/" not in html
        assert browser.property("has_deep_links") is not True

    def test_book_task_flow_contains_compact_progress_overview(self) -> None:
        from novel_forge.desktop.pages.document_renderer_story_artifacts import (
            render_book_consistency_repair_report,
        )

        data = {
            "report_type": "book_consistency_repair_report",
            "project_id": "test_proj",
            "summary": "测试修复报告",
            "analysis": {
                "issue_count": 3,
                "consistency_score": 7.5,
                "chapters_audited": [1, 2, 3],
                "analysis_mode": "full_text",
            },
            "task_flow": {
                "targeted_chapters": 2,
                "processed_chapters": 2,
                "applied_chapters": 1,
                "failed_chapters": 0,
                "repair_mode": "targeted",
            },
            "matching": {"issue_pool_size": 5},
            "chapters": [],
        }

        browser = render_book_consistency_repair_report(data)
        html = browser.toHtml()

        assert "当前阶段" in html
        assert "阶段进度" in html
        assert "1/9" in html
        assert "报告归档" in html
