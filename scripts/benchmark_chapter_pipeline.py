"""Summarize chapter pipeline performance metrics from run logs.

Reads ``logs/<run_id>/summary.json`` files produced by app-service jobs and
aggregates the internal ``trace_summary.performance_metrics`` block.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path
from typing import Any

BASE = Path(__file__).resolve().parent.parent


def load_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Failed to parse JSON from {path}: {exc}") from exc
    return payload if isinstance(payload, dict) else {}


def _logs_dir(project_root: Path | None, logs_dir: Path | None) -> Path:
    if logs_dir is not None:
        return logs_dir.expanduser().resolve()
    if project_root is None:
        raise ValueError("Either --project-root or --logs-dir is required.")
    return project_root.expanduser().resolve() / "logs"


def iter_summary_paths(logs_dir: Path) -> list[Path]:
    if not logs_dir.exists():
        return []
    return sorted(path for path in logs_dir.glob("*/summary.json") if path.is_file())


def _metrics_from_summary(summary: dict[str, Any]) -> dict[str, Any]:
    trace = summary.get("trace_summary")
    if isinstance(trace, dict):
        metrics = trace.get("performance_metrics")
        if isinstance(metrics, dict):
            return metrics
    result = summary.get("result")
    if isinstance(result, dict):
        trace = result.get("trace_summary")
        if isinstance(trace, dict):
            metrics = trace.get("performance_metrics")
            if isinstance(metrics, dict):
                return metrics
    return {}


def _sum_counter(target: dict[str, int | float], source: Any) -> None:
    if not isinstance(source, dict):
        return
    for key, value in source.items():
        try:
            target[str(key)] = target.get(str(key), 0) + float(value or 0)
        except (TypeError, ValueError):
            continue


def _median(values: list[float]) -> float:
    return round(statistics.median(values), 2) if values else 0.0


def summarize_run_logs(
    *,
    project_root: Path | None = None,
    logs_dir: Path | None = None,
    limit: int = 0,
) -> dict[str, Any]:
    resolved_logs_dir = _logs_dir(project_root, logs_dir)
    summary_paths = iter_summary_paths(resolved_logs_dir)
    if limit > 0:
        summary_paths = summary_paths[-limit:]

    durations: list[float] = []
    phase_timings: dict[str, float] = {}
    llm_calls: dict[str, float] = {}
    tokens: dict[str, float] = {}
    storage_reads: dict[str, float] = {}
    storage_writes: dict[str, float] = {}
    report_refreshes: dict[str, float] = {}
    repair_rounds = 0
    runs: list[dict[str, Any]] = []

    for summary_path in summary_paths:
        summary = load_json(summary_path)
        metrics = _metrics_from_summary(summary)
        if not metrics:
            continue
        duration = float(metrics.get("duration_ms") or summary.get("duration_ms") or 0.0)
        durations.append(duration)
        _sum_counter(phase_timings, metrics.get("phase_timings"))
        _sum_counter(llm_calls, metrics.get("llm_calls"))
        _sum_counter(tokens, metrics.get("tokens"))
        _sum_counter(storage_reads, metrics.get("storage_reads"))
        _sum_counter(storage_writes, metrics.get("storage_writes"))
        _sum_counter(report_refreshes, metrics.get("report_refreshes"))
        repair_rounds += int(float(metrics.get("repair_rounds") or 0))
        runs.append(
            {
                "run_id": str(summary.get("run_id") or summary_path.parent.name),
                "command": str(summary.get("command") or ""),
                "status": str(summary.get("status") or ""),
                "duration_ms": round(duration, 2),
                "summary_path": str(summary_path),
            }
        )

    return {
        "schema_version": 1,
        "logs_dir": str(resolved_logs_dir),
        "summary_files": len(summary_paths),
        "runs_with_metrics": len(runs),
        "duration_ms": {
            "total": round(sum(durations), 2),
            "median": _median(durations),
            "max": round(max(durations), 2) if durations else 0.0,
        },
        "phase_timings": {key: round(value, 2) for key, value in sorted(phase_timings.items())},
        "llm_calls": {key: int(value) for key, value in sorted(llm_calls.items())},
        "tokens": {key: int(value) for key, value in sorted(tokens.items())},
        "storage_reads": {key: int(value) for key, value in sorted(storage_reads.items())},
        "storage_writes": {key: int(value) for key, value in sorted(storage_writes.items())},
        "report_refreshes": {key: int(value) for key, value in sorted(report_refreshes.items())},
        "repair_rounds": repair_rounds,
        "runs": runs,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Summarize chapter pipeline performance logs.")
    parser.add_argument("--project-root", type=Path, help="Project root containing logs/.")
    parser.add_argument("--logs-dir", type=Path, help="Direct logs directory override.")
    parser.add_argument("--limit", type=int, default=0, help="Use only the latest N summaries.")
    parser.add_argument("--output", type=Path, help="Optional JSON output path.")
    args = parser.parse_args(argv)

    try:
        summary = summarize_run_logs(
            project_root=args.project_root,
            logs_dir=args.logs_dir,
            limit=max(0, int(args.limit or 0)),
        )
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    payload = json.dumps(summary, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload + "\n", encoding="utf-8")
    print(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
