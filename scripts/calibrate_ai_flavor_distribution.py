#!/usr/bin/env python3
"""calibrate_ai_flavor_distribution.py — aggregate ai_flavor advisory across
chapters of a project to inform whether to promote the M5 advisory into a
rubric-v4 hard gate.

Walks all ``data/<project>/reports/chapter_*_eval.json`` files, extracts each
report's ``ai_flavor_advisory`` block (filled in by DraftEvaluator's M5 stamp),
and prints / writes a per-project distribution summary:

- Hit count histogram (0 / 1-2 / 3-5 / 6+)
- deterministic_score distribution (mean / median / percentiles)
- by_pattern_id top-K frequency
- by_severity distribution

This is purely a calibration tool. It does NOT modify any rubric or hard gate.
Its purpose is to let you eyeball whether ai_flavor concentration is bimodal
(bad chapters cluster near 0, good chapters cluster near 10) or uniformly
distributed (in which case promoting it to a hard gate would penalize
the entire corpus).

Usage::

    # Default: scan all projects under ./data
    python scripts/calibrate_ai_flavor_distribution.py

    # Single project
    python scripts/calibrate_ai_flavor_distribution.py --project-root data/山风与归人2

    # Emit JSON for downstream tooling
    python scripts/calibrate_ai_flavor_distribution.py --json > calibration.json

Exit code: 0 always (calibration is advisory; even if you find a bad
distribution, the script never fails).

Added in M5 — see docs/ai_flavor_quality.md.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path
from statistics import mean, median
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# Matches exactly chapter_NNN_eval.json (3-digit chapter number).
# Avoids picking up variant filenames like chapter_001_retrieval_eval.json.
_EVAL_FILE_RE = re.compile(r"^chapter_\d{3}_eval\.json$")


def _iter_eval_files(project_dir: Path):
    reports_dir = project_dir / "reports"
    if not reports_dir.is_dir():
        return []
    return sorted(
        f for f in reports_dir.glob("chapter_*.json")
        if _EVAL_FILE_RE.match(f.name)
    )


def _load_advisories(project_dir: Path) -> list[dict[str, Any]]:
    """Return a list of ai_flavor_advisory blocks across all eval reports."""
    out: list[dict[str, Any]] = []
    for f in _iter_eval_files(project_dir):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        advisory = data.get("ai_flavor_advisory")
        if not advisory:
            # Old report without advisory block — skip silently.
            continue
        out.append(advisory)
    return out


def _summarize_project(project_dir: Path) -> dict[str, Any]:
    eval_files = _iter_eval_files(project_dir)
    advisories = _load_advisories(project_dir)

    if not advisories:
        return {
            "project": project_dir.name,
            "report_count": len(eval_files),
            "advisory_count": 0,
            "note": "no eval reports with ai_flavor_advisory",
        }

    scores = [float(a.get("deterministic_score", 10.0)) for a in advisories]
    hit_counts = [int(a.get("hit_count", 0)) for a in advisories]
    pattern_counter: Counter[str] = Counter()
    severity_counter: Counter[str] = Counter()
    for a in advisories:
        pattern_counter.update(a.get("by_pattern_id", {}) or {})
        severity_counter.update(a.get("by_severity", {}) or {})

    # Hit count histogram (bins chosen to be readable at a glance)
    bins = {"0": 0, "1-2": 0, "3-5": 0, "6+": 0}
    for n in hit_counts:
        if n == 0:
            bins["0"] += 1
        elif n <= 2:
            bins["1-2"] += 1
        elif n <= 5:
            bins["3-5"] += 1
        else:
            bins["6+"] += 1

    # Score distribution: percentiles (10/25/50/75/90)
    sorted_scores = sorted(scores)
    n = len(sorted_scores)

    def _pct(p: float) -> float:
        if n == 0:
            return 0.0
        idx = max(0, min(n - 1, int(p * n) - 1))
        return sorted_scores[idx]

    return {
        "project": project_dir.name,
        "report_count": len(eval_files),
        "advisory_count": len(advisories),
        "hit_count": {
            "min": min(hit_counts) if hit_counts else 0,
            "max": max(hit_counts) if hit_counts else 0,
            "mean": round(mean(hit_counts), 2) if hit_counts else 0.0,
            "median": median(hit_counts) if hit_counts else 0.0,
            "histogram": bins,
        },
        "deterministic_score": {
            "mean": round(mean(scores), 2) if scores else 0.0,
            "median": median(scores) if scores else 0.0,
            "p10": round(_pct(0.10), 2),
            "p25": round(_pct(0.25), 2),
            "p50": round(_pct(0.50), 2),
            "p75": round(_pct(0.75), 2),
            "p90": round(_pct(0.90), 2),
        },
        "top_patterns": dict(pattern_counter.most_common(10)),
        "by_severity": dict(severity_counter),
        "critical_chapter_count": sum(
            1 for a in advisories if int(a.get("critical_count", 0)) > 0
        ),
    }


def _format_project_summary(summary: dict[str, Any]) -> str:
    lines = [f"\n=== {summary['project']} ==="]
    lines.append(
        f"  Reports scanned: {summary['report_count']}  "
        f"(with advisory: {summary.get('advisory_count', 0)})"
    )
    if summary.get("note"):
        lines.append(f"  Note: {summary['note']}")
        return "\n".join(lines)
    hc = summary["hit_count"]
    lines.append(
        f"  Hit count: min={hc['min']}, max={hc['max']}, "
        f"mean={hc['mean']}, median={hc['median']}"
    )
    lines.append(f"  Hit histogram: {hc['histogram']}")
    sc = summary["deterministic_score"]
    lines.append(
        f"  Deterministic score: mean={sc['mean']}, median={sc['median']}, "
        f"p10={sc['p10']} p25={sc['p25']} p50={sc['p50']} "
        f"p75={sc['p75']} p90={sc['p90']}"
    )
    lines.append(f"  Top patterns: {summary['top_patterns']}")
    lines.append(f"  By severity: {summary['by_severity']}")
    lines.append(
        f"  Chapters with critical hits: {summary['critical_chapter_count']}"
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Aggregate M5 ai_flavor_advisory distributions across projects."
    )
    parser.add_argument(
        "--project-root",
        type=Path,
        default=_REPO_ROOT / "data",
        help="Root directory containing project subdirectories (default: ./data).",
    )
    parser.add_argument(
        "--project",
        type=str,
        default=None,
        help="Restrict to a single project name under --project-root.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit JSON instead of human-readable text.",
    )
    args = parser.parse_args(argv)

    if not args.project_root.is_dir():
        print(f"ERROR: {args.project_root} is not a directory.", file=sys.stderr)
        return 1

    # Discover projects. Two layouts are supported:
    #   1. project-root itself contains reports/ (e.g. data/<project>/reports)
    #      — caller passed the project path directly
    #   2. project-root has project subdirs each with their own reports/
    #      (e.g. data/ contains 山风与归人2/, 山风与归人/, ...)
    project_dirs: list[Path] = []
    if (args.project_root / "reports").is_dir():
        project_dirs = [args.project_root]
    else:
        project_dirs = sorted(
            d for d in args.project_root.iterdir()
            if d.is_dir() and (d / "reports").is_dir()
        )
    if args.project:
        project_dirs = [d for d in project_dirs if d.name == args.project]
        if not project_dirs:
            print(
                f"ERROR: project {args.project!r} not found under {args.project_root}",
                file=sys.stderr,
            )
            return 1

    summaries = [_summarize_project(d) for d in project_dirs]

    if args.json:
        print(json.dumps(summaries, ensure_ascii=False, indent=2))
    else:
        if not summaries:
            print("No projects with reports/ found.")
        for s in summaries:
            print(_format_project_summary(s))
        print(
            "\nReminder: this is calibration only. The advisory block "
            "DOES NOT modify scores, overall_score, or passed. Promote to "
            "hard gate only after distribution review."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
