"""Unit tests for scripts/calibrate_ai_flavor_distribution.py.

Validates the offline calibration tool that aggregates M5 ai_flavor_advisory
data across chapters of a project, to inform future rubric v4 promotion.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.calibrate_ai_flavor_distribution import (
    _format_project_summary,
    _iter_eval_files,
    _load_advisories,
    _summarize_project,
    main,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _write_eval(
    project_dir: Path,
    chapter: int,
    *,
    advisory: dict | None = None,
) -> Path:
    """Write a minimal eval.json with optional advisory block."""
    reports_dir = project_dir / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    path = reports_dir / f"chapter_{chapter:03d}_eval.json"
    payload = {
        "scores": [{"dimension": "style", "score": 8.0}],
        "overall_score": 8.0,
        "passed": True,
    }
    if advisory is not None:
        payload["ai_flavor_advisory"] = advisory
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _make_advisory(
    *,
    hit_count: int,
    deterministic_score: float,
    by_pattern: dict[str, int] | None = None,
    by_severity: dict[str, int] | None = None,
    critical_count: int = 0,
) -> dict:
    return {
        "hit_count": hit_count,
        "critical_count": critical_count,
        "deterministic_score": deterministic_score,
        "by_pattern_id": by_pattern or {},
        "by_severity": by_severity or {"critical": 0, "high": 0, "medium": 0, "low": 0},
        "evidence": [],
        "rubric_version": "2026-06-30.evaluate_draft.v3",
    }


# ---------------------------------------------------------------------------
# _iter_eval_files / _load_advisories
# ---------------------------------------------------------------------------


class TestIterEvalFiles:
    def test_finds_chapter_eval_files(self, tmp_path: Path) -> None:
        project = tmp_path / "p"
        _write_eval(project, 1)
        _write_eval(project, 2)
        # Non-matching files are skipped.
        (project / "reports" / "chapter_001_retrieval_eval.json").write_text("{}")
        (project / "reports" / "summary.json").write_text("{}")
        files = _iter_eval_files(project)
        names = [f.name for f in files]
        assert "chapter_001_eval.json" in names
        assert "chapter_002_eval.json" in names
        assert "chapter_001_retrieval_eval.json" not in names

    def test_missing_reports_dir_returns_empty(self, tmp_path: Path) -> None:
        project = tmp_path / "p"
        project.mkdir()
        assert _iter_eval_files(project) == []


class TestLoadAdvisories:
    def test_only_advisory_present_reports_counted(self, tmp_path: Path) -> None:
        project = tmp_path / "p"
        _write_eval(project, 1)  # no advisory
        _write_eval(project, 2, advisory=_make_advisory(hit_count=2, deterministic_score=6.0))
        _write_eval(project, 3, advisory=_make_advisory(hit_count=1, deterministic_score=8.0))
        advisories = _load_advisories(project)
        assert len(advisories) == 2
        scores = sorted(a["deterministic_score"] for a in advisories)
        assert scores == [6.0, 8.0]


# ---------------------------------------------------------------------------
# _summarize_project
# ---------------------------------------------------------------------------


class TestSummarizeProject:
    def test_no_reports_returns_note(self, tmp_path: Path) -> None:
        summary = _summarize_project(tmp_path / "p")
        assert summary["report_count"] == 0
        assert "note" in summary

    def test_no_advisories_returns_note(self, tmp_path: Path) -> None:
        project = tmp_path / "p"
        _write_eval(project, 1)  # no advisory
        summary = _summarize_project(project)
        assert summary["report_count"] == 1
        assert summary["advisory_count"] == 0
        assert "note" in summary

    def test_aggregates_basic_stats(self, tmp_path: Path) -> None:
        project = tmp_path / "p"
        _write_eval(project, 1, advisory=_make_advisory(hit_count=1, deterministic_score=9.0))
        _write_eval(project, 2, advisory=_make_advisory(hit_count=5, deterministic_score=5.0))
        _write_eval(project, 3, advisory=_make_advisory(hit_count=8, deterministic_score=2.0))

        summary = _summarize_project(project)
        assert summary["report_count"] == 3
        assert summary["advisory_count"] == 3
        hc = summary["hit_count"]
        assert hc["min"] == 1
        assert hc["max"] == 8
        assert hc["mean"] == pytest.approx(4.67, abs=0.01)
        # Histogram: 0=0, 1-2=1 (ch1), 3-5=1 (ch2), 6+=1 (ch3)
        assert hc["histogram"]["0"] == 0
        assert hc["histogram"]["1-2"] == 1
        assert hc["histogram"]["3-5"] == 1
        assert hc["histogram"]["6+"] == 1

    def test_aggregates_pattern_frequency(self, tmp_path: Path) -> None:
        project = tmp_path / "p"
        _write_eval(project, 1, advisory=_make_advisory(
            hit_count=3, deterministic_score=4.0,
            by_pattern={"weak_verb_stacking": 2, "tautology_marker": 1},
        ))
        _write_eval(project, 2, advisory=_make_advisory(
            hit_count=2, deterministic_score=6.0,
            by_pattern={"weak_verb_stacking": 2},
        ))
        summary = _summarize_project(project)
        assert summary["top_patterns"]["weak_verb_stacking"] == 4
        assert summary["top_patterns"]["tautology_marker"] == 1

    def test_aggregates_severity_counts(self, tmp_path: Path) -> None:
        project = tmp_path / "p"
        _write_eval(project, 1, advisory=_make_advisory(
            hit_count=3, deterministic_score=4.0,
            by_severity={"critical": 1, "high": 1, "medium": 1, "low": 0},
        ))
        _write_eval(project, 2, advisory=_make_advisory(
            hit_count=2, deterministic_score=6.0,
            by_severity={"critical": 0, "high": 2, "medium": 0, "low": 0},
        ))
        summary = _summarize_project(project)
        assert summary["by_severity"]["critical"] == 1
        assert summary["by_severity"]["high"] == 3

    def test_counts_critical_chapters(self, tmp_path: Path) -> None:
        project = tmp_path / "p"
        _write_eval(project, 1, advisory=_make_advisory(
            hit_count=1, deterministic_score=2.0, critical_count=1,
        ))
        _write_eval(project, 2, advisory=_make_advisory(
            hit_count=1, deterministic_score=8.0, critical_count=0,
        ))
        summary = _summarize_project(project)
        assert summary["critical_chapter_count"] == 1


# ---------------------------------------------------------------------------
# _format_project_summary
# ---------------------------------------------------------------------------


class TestFormatProjectSummary:
    def test_format_includes_key_sections(self) -> None:
        summary = {
            "project": "test_project",
            "report_count": 5,
            "advisory_count": 3,
            "hit_count": {"min": 0, "max": 4, "mean": 1.5, "median": 1.0,
                          "histogram": {"0": 2, "1-2": 1, "3-5": 0, "6+": 0}},
            "deterministic_score": {"mean": 8.0, "median": 9.0,
                                    "p10": 6.0, "p25": 7.5, "p50": 9.0,
                                    "p75": 9.5, "p90": 9.8},
            "top_patterns": {"weak_verb_stacking": 3},
            "by_severity": {"critical": 0, "high": 2, "medium": 1, "low": 0},
            "critical_chapter_count": 0,
        }
        out = _format_project_summary(summary)
        assert "test_project" in out
        assert "Reports scanned" in out
        assert "Hit count:" in out
        assert "Deterministic score:" in out
        assert "Top patterns:" in out
        assert "By severity:" in out

    def test_format_note_path(self) -> None:
        summary = {"project": "x", "report_count": 0, "advisory_count": 0,
                    "note": "no eval reports with ai_flavor_advisory"}
        out = _format_project_summary(summary)
        assert "no eval reports with ai_flavor_advisory" in out


# ---------------------------------------------------------------------------
# main() — end-to-end CLI
# ---------------------------------------------------------------------------


class TestMainCLI:
    def test_no_projects_returns_zero(self, tmp_path: Path, capsys) -> None:
        # tmp_path has no reports/ subdir
        rc = main(["--project-root", str(tmp_path)])
        assert rc == 0
        out = capsys.readouterr().out
        assert "No projects with reports/ found." in out
        assert "Reminder" in out

    def test_project_root_itself_is_project(self, tmp_path: Path, capsys) -> None:
        # tmp_path/reports/ layout (single project directly under root)
        _write_eval(tmp_path, 1, advisory=_make_advisory(
            hit_count=2, deterministic_score=8.0,
            by_pattern={"weak_verb_stacking": 1, "tautology_marker": 1},
        ))
        rc = main(["--project-root", str(tmp_path)])
        assert rc == 0
        out = capsys.readouterr().out
        assert tmp_path.name in out
        assert "weak_verb_stacking" in out

    def test_subdirectory_projects_layout(self, tmp_path: Path, capsys) -> None:
        # tmp_path/<project>/reports/ layout (multiple projects)
        for name in ("proj_a", "proj_b"):
            _write_eval(tmp_path / name, 1, advisory=_make_advisory(
                hit_count=1, deterministic_score=9.0,
            ))
        rc = main(["--project-root", str(tmp_path)])
        assert rc == 0
        out = capsys.readouterr().out
        assert "proj_a" in out
        assert "proj_b" in out

    def test_project_filter(self, tmp_path: Path, capsys) -> None:
        for name in ("keep", "skip"):
            _write_eval(tmp_path / name, 1, advisory=_make_advisory(
                hit_count=1, deterministic_score=9.0,
            ))
        rc = main(["--project-root", str(tmp_path), "--project", "keep"])
        assert rc == 0
        out = capsys.readouterr().out
        assert "keep" in out
        assert "skip" not in out

    def test_missing_project_filter_returns_error(self, tmp_path: Path, capsys) -> None:
        rc = main(["--project-root", str(tmp_path), "--project", "nope"])
        assert rc == 1
        err = capsys.readouterr().err
        assert "not found" in err

    def test_json_output(self, tmp_path: Path, capsys) -> None:
        _write_eval(tmp_path, 1, advisory=_make_advisory(
            hit_count=2, deterministic_score=7.0,
        ))
        rc = main(["--project-root", str(tmp_path), "--json"])
        assert rc == 0
        out = capsys.readouterr().out
        # Should be valid JSON
        data = json.loads(out)
        assert isinstance(data, list)
        assert len(data) == 1
        assert data[0]["advisory_count"] == 1

    def test_nonexistent_project_root_returns_error(self, tmp_path: Path, capsys) -> None:
        rc = main(["--project-root", str(tmp_path / "nope")])
        assert rc == 1
        err = capsys.readouterr().err
        assert "not a directory" in err
