from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType


def _load_script() -> ModuleType:
    script_path = Path(__file__).resolve().parents[2] / "scripts" / "benchmark_chapter_pipeline.py"
    spec = importlib.util.spec_from_file_location("benchmark_chapter_pipeline", script_path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _write_summary(path: Path, *, run_id: str, duration_ms: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "run_id": run_id,
        "command": "app-service-run-chapter",
        "status": "success",
        "trace_summary": {
            "performance_metrics": {
                "duration_ms": duration_ms,
                "phase_timings": {"generate": duration_ms / 2, "review": duration_ms / 2},
                "llm_calls": {"succeeded": 2},
                "tokens": {"prompt": 10, "completion": 20, "total": 30},
                "storage_reads": {"load_json": 3},
                "storage_writes": {"save_json": 1},
                "report_refreshes": {"reused": 1},
                "repair_rounds": 1,
            }
        },
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_summarize_run_logs_aggregates_performance_metrics(tmp_path: Path) -> None:
    module = _load_script()
    logs_dir = tmp_path / "logs"
    _write_summary(logs_dir / "run_a" / "summary.json", run_id="run_a", duration_ms=100.0)
    _write_summary(logs_dir / "run_b" / "summary.json", run_id="run_b", duration_ms=200.0)

    summary = module.summarize_run_logs(logs_dir=logs_dir)

    assert summary["schema_version"] == 1
    assert summary["summary_files"] == 2
    assert summary["runs_with_metrics"] == 2
    assert summary["duration_ms"]["median"] == 150.0
    assert summary["llm_calls"]["succeeded"] == 4
    assert summary["tokens"]["total"] == 60
    assert summary["storage_reads"]["load_json"] == 6
    assert summary["report_refreshes"]["reused"] == 2
    assert summary["repair_rounds"] == 2


def test_summarize_run_logs_limit_uses_latest_summaries(tmp_path: Path) -> None:
    module = _load_script()
    logs_dir = tmp_path / "logs"
    _write_summary(logs_dir / "20260101_old" / "summary.json", run_id="old", duration_ms=100.0)
    _write_summary(logs_dir / "20260102_new" / "summary.json", run_id="new", duration_ms=300.0)

    summary = module.summarize_run_logs(logs_dir=logs_dir, limit=1)

    assert summary["summary_files"] == 1
    assert summary["runs_with_metrics"] == 1
    assert summary["runs"][0]["run_id"] == "new"
    assert summary["duration_ms"]["total"] == 300.0
