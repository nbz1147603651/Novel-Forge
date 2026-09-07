"""Tests for versioned audit reports."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from novel_forge.workspace.book_ops.execution_book_entry import (
    _compare_audit_reports,
    _save_versioned_audit_report,
    _update_audit_index,
    _update_audit_symlink,
)


class MockLayout:
    """Mock ProjectLayout for testing."""

    def __init__(self, reports_dir: Path) -> None:
        self._reports_dir = reports_dir

    @property
    def reports_dir(self) -> Path:
        return self._reports_dir


@pytest.fixture
def mock_layout(tmp_path: Path) -> MockLayout:
    reports_dir = tmp_path / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    return MockLayout(reports_dir)


class TestVersionedAuditReports:
    """Test versioned audit report functionality."""

    def test_versioned_reports(self, mock_layout: MockLayout) -> None:
        """Run 3 audits, verify 3 files, index has 3 entries, symlink points to latest."""
        timestamps = []
        report_payload: dict[str, Any] = {
            "issues": [{"id": "issue1", "severity": "medium"}],
            "summary": "Test audit summary",
        }

        for i in range(3):
            ts = _save_versioned_audit_report(
                mock_layout, report_payload, timestamp=f"20260506_{130000 + i:06d}"
            )
            timestamps.append(ts)

            report_path = mock_layout.reports_dir / f"book_consistency_audit_{ts}.json"
            mock_layout.reports_dir.mkdir(parents=True, exist_ok=True)
            report_path.write_text(json.dumps(report_payload), encoding="utf-8")
            assert report_path.exists(), f"Report file {report_path} not created"

        audit_files = list(mock_layout.reports_dir.glob("book_consistency_audit_*.json"))
        assert len(audit_files) == 3, f"Expected 3 audit files, got {len(audit_files)}"

        for i, ts in enumerate(timestamps):
            _update_audit_index(
                mock_layout,
                ts,
                chapter_count=10,
                issue_count=i + 1,
                analysis_mode="summary",
            )

        index_path = mock_layout.reports_dir / "book_consistency_audit_index.json"
        assert index_path.exists(), "Index file not created"

        index_data = json.loads(index_path.read_text(encoding="utf-8"))
        assert len(index_data["audits"]) == 3, (
            f"Expected 3 index entries, got {len(index_data['audits'])}"
        )

        latest_ts = timestamps[-1]
        _update_audit_symlink(mock_layout, latest_ts)

        symlink_path = mock_layout.reports_dir / "book_consistency_audit_latest.json"
        assert symlink_path.exists(), "Symlink not created"
        assert symlink_path.is_symlink(), "Latest is not a symlink"

        link_target = os.readlink(symlink_path)
        assert link_target == f"book_consistency_audit_{latest_ts}.json"

    def test_save_versioned_audit_report_generates_timestamps(
        self, mock_layout: MockLayout
    ) -> None:
        """Verify each call generates a timestamp."""
        report_payload: dict[str, Any] = {"issues": [], "summary": "test"}

        ts1 = _save_versioned_audit_report(mock_layout, report_payload)

        assert len(ts1) == 15, f"Timestamp {ts1} should be 15 characters"
        assert ts1[8] == "_", "Timestamp should have underscore after YYYYMMDD"

    def test_save_versioned_audit_report_writes_payload_and_avoids_collision(
        self, mock_layout: MockLayout
    ) -> None:
        """The helper owns the write and advances timestamps on same-second collisions."""
        ts1 = _save_versioned_audit_report(
            mock_layout,
            {"issues": [{"id": "first"}]},
            timestamp="20260506_132841",
        )
        ts2 = _save_versioned_audit_report(
            mock_layout,
            {"issues": [{"id": "second"}]},
            timestamp="20260506_132841",
        )

        assert ts1 == "20260506_132841"
        assert ts2 == "20260506_132842"
        first_path = mock_layout.reports_dir / f"book_consistency_audit_{ts1}.json"
        second_path = mock_layout.reports_dir / f"book_consistency_audit_{ts2}.json"
        assert json.loads(first_path.read_text(encoding="utf-8"))["issues"][0]["id"] == "first"
        assert json.loads(second_path.read_text(encoding="utf-8"))["issues"][0]["id"] == "second"

    def test_compare_audit_reports_returns_issue_delta(self) -> None:
        previous = {
            "consistency_score": 7.0,
            "issues": [
                {
                    "issue_id": "resolved",
                    "category": "timeline",
                    "severity": "critical",
                    "primary_chapter": 1,
                    "description": "old issue",
                },
                {
                    "issue_id": "kept",
                    "category": "naming",
                    "severity": "warning",
                    "primary_chapter": 2,
                    "description": "kept issue",
                },
            ],
        }
        current = {
            "consistency_score": 8.5,
            "issues": [
                {
                    "issue_id": "kept",
                    "category": "naming",
                    "severity": "warning",
                    "primary_chapter": 2,
                    "description": "kept issue",
                },
                {
                    "issue_id": "new",
                    "category": "worldbuilding",
                    "severity": "info",
                    "primary_chapter": 3,
                    "description": "new issue",
                },
            ],
        }

        delta = _compare_audit_reports(previous, current)

        assert delta["previous_issue_count"] == 2
        assert delta["current_issue_count"] == 2
        assert delta["new_issue_count"] == 1
        assert delta["resolved_issue_count"] == 1
        assert delta["persisting_issue_count"] == 1
        assert delta["consistency_score_delta"] == 1.5
        assert delta["severity_delta"] == {"critical": -1, "warning": 0, "info": 1}
        assert delta["new_issues"][0]["issue_id"] == "new"
        assert delta["resolved_issues"][0]["issue_id"] == "resolved"

    def test_save_versioned_audit_report_attaches_delta_from_latest(
        self, mock_layout: MockLayout
    ) -> None:
        first_ts = _save_versioned_audit_report(
            mock_layout,
            {
                "consistency_score": 6.0,
                "issues": [{"issue_id": "old", "severity": "critical"}],
            },
            timestamp="20260506_132841",
        )
        _update_audit_index(
            mock_layout,
            first_ts,
            chapter_count=3,
            issue_count=1,
            analysis_mode="summary",
        )
        _update_audit_symlink(mock_layout, first_ts)

        second_payload: dict[str, Any] = {
            "consistency_score": 7.0,
            "issues": [{"issue_id": "new", "severity": "warning"}],
        }
        second_ts = _save_versioned_audit_report(
            mock_layout,
            second_payload,
            timestamp="20260506_132842",
        )

        second_path = mock_layout.reports_dir / f"book_consistency_audit_{second_ts}.json"
        saved = json.loads(second_path.read_text(encoding="utf-8"))
        assert saved["delta_from_previous"]["new_issue_count"] == 1
        assert saved["delta_from_previous"]["resolved_issue_count"] == 1

    def test_update_audit_index_creates_new_index(self, mock_layout: MockLayout) -> None:
        """Test creating a new index file."""
        _update_audit_index(
            mock_layout,
            "20260506_132841",
            chapter_count=5,
            issue_count=3,
            analysis_mode="summary",
        )

        index_path = mock_layout.reports_dir / "book_consistency_audit_index.json"
        assert index_path.exists()

        data = json.loads(index_path.read_text(encoding="utf-8"))
        assert len(data["audits"]) == 1
        assert data["audits"][0]["timestamp"] == "20260506_132841"
        assert data["audits"][0]["chapter_count"] == 5
        assert data["audits"][0]["issue_count"] == 3
        assert data["audits"][0]["analysis_mode"] == "summary"

    def test_update_audit_index_appends_to_existing(self, mock_layout: MockLayout) -> None:
        """Test appending to an existing index file."""
        index_path = mock_layout.reports_dir / "book_consistency_audit_index.json"
        index_path.write_text(
            json.dumps({"audits": [{"timestamp": "20260506_100000", "path": "old.json"}]}),
            encoding="utf-8",
        )

        _update_audit_index(
            mock_layout,
            "20260506_132841",
            chapter_count=10,
            issue_count=7,
            analysis_mode="full",
        )

        data = json.loads(index_path.read_text(encoding="utf-8"))
        assert len(data["audits"]) == 2
        assert data["audits"][0]["timestamp"] == "20260506_100000"
        assert data["audits"][1]["timestamp"] == "20260506_132841"

    def test_update_audit_index_replaces_duplicate_timestamp(self, mock_layout: MockLayout) -> None:
        """Repeated index updates for one report should refresh, not duplicate."""
        _update_audit_index(
            mock_layout,
            "20260506_132841",
            chapter_count=10,
            issue_count=7,
            analysis_mode="summary",
        )
        _update_audit_index(
            mock_layout,
            "20260506_132841",
            chapter_count=12,
            issue_count=9,
            analysis_mode="full_text",
        )

        index_path = mock_layout.reports_dir / "book_consistency_audit_index.json"
        data = json.loads(index_path.read_text(encoding="utf-8"))
        assert len(data["audits"]) == 1
        assert data["audits"][0]["chapter_count"] == 12
        assert data["audits"][0]["issue_count"] == 9
        assert data["audits"][0]["analysis_mode"] == "full_text"

    def test_update_audit_symlink_updates_existing(self, mock_layout: MockLayout) -> None:
        """Test that symlink is updated when called multiple times."""
        symlink_path = mock_layout.reports_dir / "book_consistency_audit_latest.json"

        _update_audit_symlink(mock_layout, "20260506_100000")
        assert os.readlink(symlink_path) == "book_consistency_audit_20260506_100000.json"

        _update_audit_symlink(mock_layout, "20260506_120000")
        assert os.readlink(symlink_path) == "book_consistency_audit_20260506_120000.json"

    def test_timestamp_format(self, mock_layout: MockLayout) -> None:
        """Verify timestamp follows YYYYMMDD_HHMMSS format."""
        report_payload: dict[str, Any] = {"issues": [], "summary": "test"}
        ts = _save_versioned_audit_report(mock_layout, report_payload)

        assert len(ts) == 15, f"Timestamp {ts} should be 15 characters"
        assert ts[8] == "_", "Timestamp should have underscore after YYYYMMDD"
        date_part = ts[:8]
        assert date_part.isdigit(), f"Date part {date_part} should be digits"
        time_part = ts[9:]
        assert time_part.isdigit(), f"Time part {time_part} should be digits"
