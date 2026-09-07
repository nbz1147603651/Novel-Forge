"""Tests for scripts/backfill_ai_flavor_advisory.py."""

from __future__ import annotations

import json
from pathlib import Path

from scripts.backfill_ai_flavor_advisory import (
    _discover_project_dirs,
    backfill_project,
    main,
)


def _write_eval(
    project_dir: Path,
    chapter: int,
    *,
    advisory: dict | None = None,
) -> Path:
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
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def _write_humanize(project_dir: Path, chapter: int, *, hits: list[dict] | None = None) -> Path:
    reports_dir = project_dir / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    path = reports_dir / f"chapter_{chapter:03d}_humanize.json"
    payload = {
        "chapter_number": chapter,
        "pattern_hits": hits if hits is not None else [
            {
                "pattern_id": "weak_verb_stacking",
                "severity": "high",
                "confidence": 1.0,
                "evidence_quote": "觉得似乎意识到",
            }
        ],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


class TestBackfillProject:
    def test_dry_run_does_not_modify_eval_report(self, tmp_path: Path) -> None:
        project = tmp_path / "p"
        eval_path = _write_eval(project, 1)
        _write_humanize(project, 1)

        summary = backfill_project(project)
        payload = json.loads(eval_path.read_text(encoding="utf-8"))

        assert summary["would_update"] == 1
        assert summary["updated"] == 0
        assert "ai_flavor_advisory" not in payload

    def test_in_place_writes_advisory_without_changing_scores(self, tmp_path: Path) -> None:
        project = tmp_path / "p"
        eval_path = _write_eval(project, 1)
        _write_humanize(project, 1)

        summary = backfill_project(project, in_place=True)
        payload = json.loads(eval_path.read_text(encoding="utf-8"))

        assert summary["updated"] == 1
        assert payload["overall_score"] == 8.0
        assert payload["passed"] is True
        assert payload["ai_flavor_advisory"]["hit_count"] == 1
        assert payload["ai_flavor_advisory"]["deterministic_score"] == 8.0

    def test_existing_advisory_is_skipped_by_default(self, tmp_path: Path) -> None:
        project = tmp_path / "p"
        eval_path = _write_eval(project, 1, advisory={"hit_count": 99})
        _write_humanize(project, 1)

        summary = backfill_project(project, in_place=True)
        payload = json.loads(eval_path.read_text(encoding="utf-8"))

        assert summary["skipped_existing"] == 1
        assert payload["ai_flavor_advisory"]["hit_count"] == 99

    def test_overwrite_replaces_existing_advisory(self, tmp_path: Path) -> None:
        project = tmp_path / "p"
        eval_path = _write_eval(project, 1, advisory={"hit_count": 99})
        _write_humanize(project, 1, hits=[])

        summary = backfill_project(project, in_place=True, overwrite=True)
        payload = json.loads(eval_path.read_text(encoding="utf-8"))

        assert summary["updated"] == 1
        assert payload["ai_flavor_advisory"]["hit_count"] == 0
        assert payload["ai_flavor_advisory"]["deterministic_score"] == 10.0

    def test_missing_humanize_report_is_counted(self, tmp_path: Path) -> None:
        project = tmp_path / "p"
        _write_eval(project, 1)

        summary = backfill_project(project, in_place=True)

        assert summary["skipped_missing_humanize"] == 1
        assert summary["updated"] == 0


class TestBackfillCLI:
    def test_project_root_can_be_single_project(self, tmp_path: Path, capsys) -> None:
        _write_eval(tmp_path, 1)
        _write_humanize(tmp_path, 1)

        rc = main(["--project-root", str(tmp_path)])

        assert rc == 0
        out = capsys.readouterr().out
        assert "would_update: 1" in out

    def test_json_output(self, tmp_path: Path, capsys) -> None:
        _write_eval(tmp_path, 1)
        _write_humanize(tmp_path, 1)

        rc = main(["--project-root", str(tmp_path), "--json"])

        assert rc == 0
        data = json.loads(capsys.readouterr().out)
        assert data[0]["would_update"] == 1

    def test_project_filter(self, tmp_path: Path, capsys) -> None:
        for name in ("keep", "skip"):
            _write_eval(tmp_path / name, 1)
            _write_humanize(tmp_path / name, 1)

        rc = main(["--project-root", str(tmp_path), "--project", "keep"])

        assert rc == 0
        out = capsys.readouterr().out
        assert "keep" in out
        assert "skip" not in out

    def test_missing_project_filter_returns_error(self, tmp_path: Path, capsys) -> None:
        rc = main(["--project-root", str(tmp_path), "--project", "missing"])

        assert rc == 1
        assert "not found" in capsys.readouterr().err

    def test_discover_project_dirs_ignores_dirs_without_reports(self, tmp_path: Path) -> None:
        (tmp_path / "without_reports").mkdir()
        _write_eval(tmp_path / "with_reports", 1)

        projects = _discover_project_dirs(tmp_path)

        assert [p.name for p in projects] == ["with_reports"]
