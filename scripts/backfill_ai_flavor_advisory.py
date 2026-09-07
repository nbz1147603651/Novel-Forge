#!/usr/bin/env python3
"""Backfill EvalReport.ai_flavor_advisory from persisted humanize reports.

This is a safe companion to ``calibrate_ai_flavor_distribution.py``. It reads
``reports/chapter_NNN_eval.json`` and ``reports/chapter_NNN_humanize.json`` and
adds the deterministic M5 advisory block to eval reports. Dry-run is the default;
use ``--in-place`` to write.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from novel_forge.eval.evaluator import DraftEvaluator  # noqa: E402
from scripts.calibrate_ai_flavor_distribution import _iter_eval_files  # noqa: E402

_CHAPTER_EVAL_RE = re.compile(r"^chapter_(\d{3})_eval\.json$")


def _discover_project_dirs(root: Path, *, project: str | None = None) -> list[Path]:
    if (root / "reports").is_dir():
        project_dirs = [root]
    else:
        project_dirs = sorted(d for d in root.iterdir() if d.is_dir() and (d / "reports").is_dir())
    if project:
        project_dirs = [d for d in project_dirs if d.name == project]
    return project_dirs


def _chapter_number(path: Path) -> int | None:
    match = _CHAPTER_EVAL_RE.match(path.name)
    if not match:
        return None
    return int(match.group(1))


def _load_json(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    tmp_path = path.with_name(f"{path.name}.tmp")
    tmp_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(tmp_path, path)


def backfill_project(
    project_dir: Path,
    *,
    in_place: bool = False,
    overwrite: bool = False,
) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    counts = {
        "reports_seen": 0,
        "would_update": 0,
        "updated": 0,
        "skipped_existing": 0,
        "skipped_missing_humanize": 0,
        "errors": 0,
    }

    for eval_file in _iter_eval_files(project_dir):
        chapter = _chapter_number(eval_file)
        if chapter is None:
            continue
        counts["reports_seen"] += 1
        humanize_file = project_dir / "reports" / f"chapter_{chapter:03d}_humanize.json"
        item: dict[str, Any] = {
            "project": project_dir.name,
            "chapter": chapter,
            "eval_file": str(eval_file),
            "humanize_file": str(humanize_file),
        }

        eval_payload = _load_json(eval_file)
        if eval_payload is None:
            counts["errors"] += 1
            item["action"] = "error_invalid_eval_json"
            results.append(item)
            continue
        if eval_payload.get("ai_flavor_advisory") and not overwrite:
            counts["skipped_existing"] += 1
            item["action"] = "skipped_existing"
            results.append(item)
            continue

        humanize_payload = _load_json(humanize_file)
        if humanize_payload is None:
            counts["skipped_missing_humanize"] += 1
            item["action"] = "skipped_missing_humanize"
            results.append(item)
            continue

        advisory = DraftEvaluator._build_ai_flavor_advisory(humanize_payload)
        item.update({
            "hit_count": advisory.get("hit_count", 0),
            "deterministic_score": advisory.get("deterministic_score", 10.0),
        })
        if in_place:
            eval_payload["ai_flavor_advisory"] = advisory
            _write_json_atomic(eval_file, eval_payload)
            counts["updated"] += 1
            item["action"] = "updated"
        else:
            counts["would_update"] += 1
            item["action"] = "would_update"
        results.append(item)

    return {
        "project": project_dir.name,
        **counts,
        "results": results,
    }


def _format_summary(summaries: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for summary in summaries:
        lines.append(f"\n=== {summary['project']} ===")
        lines.append(
            "  Reports: {reports_seen} | would_update: {would_update} | "
            "updated: {updated} | existing: {skipped_existing} | "
            "missing_humanize: {skipped_missing_humanize} | errors: {errors}".format(
                **summary
            )
        )
    lines.append("\nDry-run by default. Re-run with --in-place to write advisory blocks.")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Backfill EvalReport.ai_flavor_advisory from chapter humanize reports."
    )
    parser.add_argument(
        "--project-root",
        type=Path,
        default=_REPO_ROOT / "data",
        help="Project directory or root containing project subdirectories.",
    )
    parser.add_argument("--project", type=str, default=None, help="Restrict to one project name.")
    parser.add_argument("--in-place", action="store_true", help="Write updated eval reports.")
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Replace existing ai_flavor_advisory blocks.",
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON summary.")
    args = parser.parse_args(argv)

    if not args.project_root.is_dir():
        print(f"ERROR: {args.project_root} is not a directory.", file=sys.stderr)
        return 1

    project_dirs = _discover_project_dirs(args.project_root, project=args.project)
    if args.project and not project_dirs:
        print(
            f"ERROR: project {args.project!r} not found under {args.project_root}",
            file=sys.stderr,
        )
        return 1

    summaries = [
        backfill_project(project_dir, in_place=args.in_place, overwrite=args.overwrite)
        for project_dir in project_dirs
    ]

    if args.json:
        print(json.dumps(summaries, ensure_ascii=False, indent=2))
    else:
        if not summaries:
            print("No projects with reports/ found.")
        print(_format_summary(summaries))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
