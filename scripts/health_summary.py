#!/usr/bin/env python3
"""Cross-run health summary for novel-forge projects.

Reads run logs from ``data/<project>/logs/`` and aggregates key metrics:
- Quality gate pass/fail rates (chapter-level)
- Repair round counts by dimension
- Model fallback rates and reasons
- Token consumption by task_type
- Chapter duration statistics
- Checkpoint recovery events

Usage:
    python scripts/health_summary.py --project-root data/入梦破局
    python scripts/health_summary.py --project-root data/入梦破局 --format json
    python scripts/health_summary.py --project-root data/入梦破局 --last 5
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class RunMetrics:
    """Aggregated metrics for a single run."""

    run_id: str = ""
    started_at: str = ""
    finished_at: str = ""
    status: str = "unknown"  # success / failed / cancelled
    chapter_count: int = 0
    chapters_passed: int = 0
    chapters_failed: int = 0
    repair_rounds: dict[str, list[int]] = field(default_factory=lambda: defaultdict(list))
    model_calls: int = 0
    model_fallbacks: int = 0
    fallback_reasons: Counter = field(default_factory=Counter)
    token_usage: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    total_cost_usd: float = 0.0
    checkpoint_recoveries: int = 0
    errors: list[str] = field(default_factory=list)
    skipped_lines: int = 0


def _parse_jsonl(path: Path) -> tuple[list[dict[str, Any]], int]:
    """Parse a JSONL file, returning (records, skipped_count)."""
    records: list[dict[str, Any]] = []
    skipped = 0
    if not path.exists():
        return records, skipped
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            skipped += 1
    return records, skipped


def _parse_model_call(path: Path) -> dict[str, Any] | None:
    """Parse a single model call JSON file."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _extract_run_metrics(run_dir: Path) -> RunMetrics:
    """Extract metrics from a single run directory."""
    metrics = RunMetrics(run_id=run_dir.name)

    # Parse events.jsonl
    events, skipped = _parse_jsonl(run_dir / "events.jsonl")
    metrics.skipped_lines += skipped

    for event in events:
        ts = event.get("ts", "")
        event_type = event.get("event", "")
        data = event.get("data", {})

        if event_type == "run_started":
            metrics.started_at = ts
        elif event_type == "run_finished":
            metrics.finished_at = ts
            metrics.status = "success"
        elif event_type == "run_failed":
            metrics.finished_at = ts
            metrics.status = "failed"
        elif event_type == "step":
            step_name = data.get("step", "")
            step_data = data.get("data", {})

            # Track chapter events
            if step_name == "chapter_started":
                metrics.chapter_count += 1
            elif step_name == "quality_gate_result":
                if step_data.get("passed"):
                    metrics.chapters_passed += 1
                else:
                    metrics.chapters_failed += 1
            elif step_name == "checkpoint_recovered":
                metrics.checkpoint_recoveries += 1

            # Track repair rounds by dimension
            if step_name.startswith("repair_") and "round" in step_data:
                dimension = step_name.replace("repair_", "").replace("_loop", "")
                try:
                    metrics.repair_rounds[dimension].append(int(step_data["round"]))
                except (TypeError, ValueError):
                    pass

    # Parse errors.jsonl
    errors, skipped = _parse_jsonl(run_dir / "errors.jsonl")
    metrics.skipped_lines += skipped
    for err in errors:
        data = err.get("data", {})
        if isinstance(data, dict):
            msg = str(data.get("error", ""))[:200]
        else:
            msg = str(data)[:200]
        if msg:
            metrics.errors.append(msg)

    # Parse model_calls/*.json
    model_calls_dir = run_dir / "model_calls"
    if model_calls_dir.exists():
        for call_file in sorted(model_calls_dir.glob("*.json")):
            call = _parse_model_call(call_file)
            if call is None:
                metrics.skipped_lines += 1
                continue
            metrics.model_calls += 1

            # Track fallbacks
            route = call.get("route", "")
            if "fallback" in route.lower():
                metrics.model_fallbacks += 1
                metrics.fallback_reasons[route] += 1

            # Track token usage by task
            task = call.get("task", "unknown")
            # Tokens can be in top-level fields or nested usage dict
            total_tokens = int(call.get("total_tokens", 0) or 0)
            if total_tokens == 0:
                prompt = int(call.get("prompt_tokens", 0) or 0)
                completion = int(call.get("completion_tokens", 0) or 0)
                total_tokens = prompt + completion
            if total_tokens == 0 and isinstance(call.get("usage"), dict):
                usage = call["usage"]
                total_tokens = int(usage.get("input_tokens", 0) or 0) + int(usage.get("output_tokens", 0) or 0)
            metrics.token_usage[task] += total_tokens

            # Track cost
            cost = call.get("cost_usd")
            if cost is not None:
                try:
                    metrics.total_cost_usd += float(cost)
                except (TypeError, ValueError):
                    pass

    return metrics


def _collect_runs(project_root: Path, last_n: int | None = None) -> list[RunMetrics]:
    """Collect metrics from all runs in the project."""
    logs_dir = project_root / "logs"
    if not logs_dir.exists():
        return []

    run_dirs = sorted(logs_dir.iterdir(), reverse=True)
    if last_n:
        run_dirs = run_dirs[:last_n]

    return [_extract_run_metrics(d) for d in run_dirs if d.is_dir()]


def _format_table(runs: list[RunMetrics]) -> str:
    """Format metrics as a terminal table."""
    if not runs:
        return "No runs found."

    lines: list[str] = []
    lines.append("=" * 80)
    lines.append("HEALTH SUMMARY")
    lines.append("=" * 80)

    # Overall stats
    total_runs = len(runs)
    successful = sum(1 for r in runs if r.status == "success")
    failed = sum(1 for r in runs if r.status == "failed")

    lines.append(f"\nRuns: {total_runs} total, {successful} success, {failed} failed")

    # Chapter stats
    total_chapters = sum(r.chapter_count for r in runs)
    total_passed = sum(r.chapters_passed for r in runs)
    total_failed_ch = sum(r.chapters_failed for r in runs)
    pass_rate = (total_passed / total_chapters * 100) if total_chapters > 0 else 0

    lines.append(f"\nChapters: {total_chapters} total")
    lines.append(
        f"  Quality gate pass rate: {pass_rate:.1f}% "
        f"({total_passed} passed, {total_failed_ch} failed)"
    )

    # Repair stats
    all_repair_rounds: dict[str, list[int]] = defaultdict(list)
    for r in runs:
        for dim, rounds in r.repair_rounds.items():
            all_repair_rounds[dim].extend(rounds)

    if all_repair_rounds:
        lines.append("\nRepair rounds by dimension:")
        for dim in sorted(all_repair_rounds.keys()):
            rounds = all_repair_rounds[dim]
            avg = sum(rounds) / len(rounds) if rounds else 0
            lines.append(f"  {dim}: avg={avg:.1f}, max={max(rounds) if rounds else 0}, count={len(rounds)}")

    # Model stats
    total_calls = sum(r.model_calls for r in runs)
    total_fallbacks = sum(r.model_fallbacks for r in runs)
    fallback_rate = (total_fallbacks / total_calls * 100) if total_calls > 0 else 0

    lines.append(f"\nModel calls: {total_calls} total")
    lines.append(f"  Fallback rate: {fallback_rate:.1f}% ({total_fallbacks}/{total_calls})")

    # Token stats
    total_tokens = sum(sum(r.token_usage.values()) for r in runs)
    total_cost = sum(r.total_cost_usd for r in runs)
    lines.append(f"\nToken usage: {total_tokens:,} total")
    lines.append(f"Estimated cost: ${total_cost:.4f}")

    # Checkpoint recoveries
    total_recoveries = sum(r.checkpoint_recoveries for r in runs)
    if total_recoveries > 0:
        lines.append(f"\nCheckpoint recoveries: {total_recoveries}")

    # Recent runs table
    lines.append("\n" + "-" * 80)
    lines.append("RECENT RUNS")
    lines.append("-" * 80)
    lines.append(f"{'Run ID':<50} {'Status':<10} {'Ch':<5} {'Calls':<8} {'Cost':<10}")
    lines.append("-" * 80)

    for r in runs[:10]:  # Show last 10 runs
        run_id_short = r.run_id[:47] + "..." if len(r.run_id) > 50 else r.run_id
        lines.append(
            f"{run_id_short:<50} {r.status:<10} {r.chapter_count:<5} "
            f"{r.model_calls:<8} ${r.total_cost_usd:<9.4f}"
        )

    # Top fallback reasons
    all_fallback_reasons: Counter = Counter()
    for r in runs:
        all_fallback_reasons.update(r.fallback_reasons)
    if all_fallback_reasons:
        lines.append("\nTop fallback reasons:")
        for reason, count in all_fallback_reasons.most_common(5):
            lines.append(f"  {reason}: {count}")

    return "\n".join(lines)


def _format_json(runs: list[RunMetrics]) -> str:
    """Format metrics as JSON."""
    data = {
        "total_runs": len(runs),
        "runs": [
            {
                "run_id": r.run_id,
                "status": r.status,
                "chapter_count": r.chapter_count,
                "chapters_passed": r.chapters_passed,
                "chapters_failed": r.chapters_failed,
                "model_calls": r.model_calls,
                "model_fallbacks": r.model_fallbacks,
                "total_cost_usd": round(r.total_cost_usd, 6),
                "checkpoint_recoveries": r.checkpoint_recoveries,
                "token_usage": dict(r.token_usage),
                "repair_rounds": {k: len(v) for k, v in r.repair_rounds.items()},
                "error_count": len(r.errors),
            }
            for r in runs
        ],
    }
    return json.dumps(data, indent=2, ensure_ascii=False)


def main() -> int:
    parser = argparse.ArgumentParser(description="Cross-run health summary")
    parser.add_argument(
        "--project-root",
        type=Path,
        required=True,
        help="Path to project root (e.g., data/入梦破局)",
    )
    parser.add_argument(
        "--format",
        choices=["table", "json"],
        default="table",
        help="Output format (default: table)",
    )
    parser.add_argument(
        "--last",
        type=int,
        default=None,
        help="Only analyze the last N runs",
    )
    args = parser.parse_args()

    if not args.project_root.exists():
        print(f"Error: Project root not found: {args.project_root}", file=sys.stderr)
        return 1

    runs = _collect_runs(args.project_root, args.last)
    if not runs:
        print(f"No runs found in {args.project_root / 'logs'}", file=sys.stderr)
        return 1

    if args.format == "json":
        print(_format_json(runs))
    else:
        print(_format_table(runs))

    return 0


if __name__ == "__main__":
    sys.exit(main())
