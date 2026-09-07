#!/usr/bin/env python3
"""post_format_check.py — offline scanner for chapter format/length issues.

Walks all projects under ``./data`` (or path supplied via --project-root),
runs ``ChapterQualityPrescreen`` over every ``chapters/chapter_*.md`` file,
and prints a per-chapter report of any formatting / length collapse.

This is the offline equivalent of the runtime gate proposed in plan §2.1.2.
Use it to:
  1. Audit an existing project (e.g. 山风与归人2) for the kinds of production
     collapses that previously slipped past eval and into publication.
  2. Sanity-check the gate before promoting it to the runtime chapter flow.
  3. Wire into a CI pipeline (exit 1 on any critical hit).

Examples:

    python scripts/post_format_check.py                          # scan ./data
    python scripts/post_format_check.py --project-root data/山风与归人2
    python scripts/post_format_check.py --apply-fixes --in-place # auto-fix 双句号/重复字
    python scripts/post_format_check.py --json > report.json     # machine-readable
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Iterable

# Ensure project root is importable when run as a script.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from novel_forge.pipeline.steps.chapter_quality_prescreen import (  # noqa: E402
    ChapterQualityPrescreen,
    PrescreenReport,
)


def _iter_chapter_files(project_dir: Path) -> Iterable[Path]:
    """Yield chapter_*.md files in numeric order."""
    chapters_dir = project_dir / "chapters"
    if not chapters_dir.is_dir():
        return []
    files = sorted(
        chapters_dir.glob("chapter_*.md"),
        key=lambda p: int(p.stem.split("_")[1]) if p.stem.split("_")[1].isdigit() else 0,
    )
    return files


def _display_path(path: Path) -> str:
    """Return a stable display path for JSON reports."""
    try:
        return str(path.relative_to(_REPO_ROOT))
    except ValueError:
        return str(path)


def _format_report_human(report: PrescreenReport, project_name: str) -> str:
    """Format a single chapter report for human reading."""
    lines = []
    status = "PASS" if report.passed else "FAIL"
    lines.append(
        f"  [{status}] {project_name} Ch{report.chapter_number:03d}  "
        f"({report.char_count} chars, {len(report.hits)} hits)"
    )
    for hit in report.hits:
        quote_repr = repr(hit.quote[:30]) if hit.quote else "(no quote)"
        lines.append(
            f"         [{hit.severity:>8}] {hit.kind:<22} {quote_repr}"
            f"  → {hit.fix_suggestion}"
        )
    return "\n".join(lines)


def scan_project(
    project_dir: Path,
    *,
    prescreen: ChapterQualityPrescreen,
) -> tuple[list[PrescreenReport], list[Path]]:
    """Run prescreen over every chapter in a project. Returns (reports, files)."""
    reports: list[PrescreenReport] = []
    files = list(_iter_chapter_files(project_dir))
    for chapter_file in files:
        text = chapter_file.read_text(encoding="utf-8")
        chapter_number = (
            int(chapter_file.stem.split("_")[1])
            if chapter_file.stem.split("_")[1].isdigit()
            else 0
        )
        report = prescreen.prescreen(text, chapter_number=chapter_number)
        reports.append(report)
    return reports, files


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Scan chapter files for format/length collapses.",
    )
    parser.add_argument(
        "--project-root",
        type=Path,
        default=_REPO_ROOT / "data",
        help="Root directory containing project subdirectories (default: ./data).",
    )
    parser.add_argument(
        "--min-chars",
        type=int,
        default=ChapterQualityPrescreen.DEFAULT_MIN_CHARS,
        help="Minimum chapter length (default: 4000).",
    )
    parser.add_argument(
        "--max-chars",
        type=int,
        default=ChapterQualityPrescreen.DEFAULT_MAX_CHARS,
        help="Maximum chapter length (default: 12000).",
    )
    parser.add_argument(
        "--check-quote-balance",
        action="store_true",
        help="Also report unmatched Chinese quote marks. Disabled by default because "
             "some prose styles use opening quotes as dialogue markers.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON instead of human text.",
    )
    parser.add_argument(
        "--apply-fixes",
        action="store_true",
        help="Auto-apply deterministic fixes (collapse 双句号 / 重复字) and rewrite in place.",
    )
    parser.add_argument(
        "--in-place",
        action="store_true",
        help="When --apply-fixes is set, write changes back to the original file. "
             "Without this flag, --apply-fixes only prints which files would change.",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Allow --apply-fixes --in-place to write more than one project. "
             "Single-project writes and multi-project dry-runs do not require this flag.",
    )
    args = parser.parse_args(argv)

    if not args.project_root.exists():
        print(f"Project root not found: {args.project_root}", file=sys.stderr)
        return 2

    prescreen = ChapterQualityPrescreen(
        min_chars=args.min_chars,
        max_chars=args.max_chars,
        check_quote_balance=args.check_quote_balance,
    )

    project_dirs = sorted(
        d for d in args.project_root.iterdir()
        if d.is_dir() and (d / "chapters").is_dir()
    )

    # If --project-root points AT a single project (i.e. it has chapters/ itself),
    # treat that directory as the single project.
    if (args.project_root / "chapters").is_dir():
        project_dirs = [args.project_root]

    if not project_dirs:
        print(f"No projects found under {args.project_root}", file=sys.stderr)
        return 2

    # Safety gate: writing fixes to more than one project requires explicit --yes.
    if args.apply_fixes and args.in_place and len(project_dirs) > 1 and not args.yes:
        print(
            f"ERROR: --apply-fixes --in-place on multiple projects requires --yes.\n"
            f"  Found {len(project_dirs)} projects under {args.project_root}:\n"
            + "\n".join(f"    - {d.name}" for d in project_dirs)
            + "\n\nRe-run with --yes to apply fixes to all listed projects.",
            file=sys.stderr,
        )
        return 2

    all_reports: list[dict[str, object]] = []
    critical_count = 0
    high_count = 0

    for project_dir in project_dirs:
        project_name = project_dir.name
        reports, files = scan_project(project_dir, prescreen=prescreen)

        if args.apply_fixes:
            for chapter_file, report in zip(files, reports, strict=True):
                text = chapter_file.read_text(encoding="utf-8")
                fixed = ChapterQualityPrescreen.apply_format_fixes(text)
                if fixed != text:
                    if args.in_place:
                        chapter_file.write_text(fixed, encoding="utf-8")
                        print(
                            f"  [FIXED] {project_name} Ch{report.chapter_number:03d}",
                            file=sys.stderr,
                        )
                    else:
                        print(
                            f"  [DRY-RUN] {project_name} Ch{report.chapter_number:03d} "
                            f"would be fixed (use --in-place to write)",
                            file=sys.stderr,
                        )

        for report in reports:
            critical_count += sum(1 for h in report.hits if h.severity == "critical")
            high_count += sum(1 for h in report.hits if h.severity == "high")

        if args.json:
            for report, chapter_file in zip(reports, files, strict=True):
                all_reports.append({
                    "project": project_name,
                    "chapter_file": _display_path(chapter_file),
                    "report": asdict(report),
                })
        else:
            failed = [r for r in reports if not r.passed]
            warned = [r for r in reports if r.passed and r.hits]
            print(f"\n=== {project_name} ===")
            print(
                f"  Chapters scanned: {len(reports)}  |  "
                f"failed: {len(failed)}  |  warnings: {len(warned)}"
            )
            for report in failed + warned:
                print(_format_report_human(report, project_name))

    if args.json:
        summary = {
            "projects_scanned": len(project_dirs),
            "critical_hits": critical_count,
            "high_hits": high_count,
            "reports": all_reports,
        }
        print(json.dumps(summary, ensure_ascii=False, indent=2))

    # Exit 1 if any critical hits — useful for CI gating.
    if critical_count > 0:
        print(
            f"\nERROR: {critical_count} critical hit(s) detected.",
            file=sys.stderr,
        )
        return 1
    if high_count > 0:
        print(
            f"\nWARNING: {high_count} high-severity hit(s) detected.",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
