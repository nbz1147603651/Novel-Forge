"""Repair rate diagnostic: analyzes book consistency audit & repair reports."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

BASE = Path(__file__).parent.parent


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Required file not found: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        raise ValueError(f"Failed to parse JSON from {path}: {e}") from e


def analyze_repair_rate(project: str) -> dict[str, Any]:
    reports_dir = BASE / f"data/{project}/reports"
    repair_report_path = reports_dir / "book_consistency_repair_report.json"
    audit_report_path = reports_dir / "book_consistency_audit_latest.json"

    if not reports_dir.exists():
        raise FileNotFoundError(f"Reports directory not found: {reports_dir}")

    repair_data = load_json(repair_report_path)
    audit_data = load_json(audit_report_path)

    issue_distribution: dict[str, int] = {"continuity": 0, "causal": 0, "forbidden_element": 0}

    issues = audit_data.get("issues", [])
    for issue in issues:
        cat = issue.get("category", "")
        if cat in issue_distribution:
            issue_distribution[cat] += 1

    post_repair = repair_data.get("post_repair_summary", {})
    repair_results = {
        "closed": post_repair.get("issues_closed", 0),
        "remaining": post_repair.get("issues_remaining", 0),
    }

    unclosed_reasons: dict[str, int] = {"repair_failed": 0, "not_attempted": 0, "blocked": 0}

    summary_text = repair_data.get("summary", "")
    if "阻止" in summary_text or "blocked" in summary_text.lower():
        blocked_count = summary_text.count("阻止")
        unclosed_reasons["blocked"] = blocked_count
    unclosed_reasons["not_attempted"] = repair_results["remaining"] - unclosed_reasons["blocked"]

    blocked_chapters = {49: 0, 50: 0, 52: 0, 56: 0}
    blocked_list = repair_data.get("blocked_chapter_numbers", [])
    for ch in blocked_list:
        if ch in blocked_chapters:
            blocked_chapters[ch] += 1

    for issue in issues:
        chapter = issue.get("primary_chapter", 0)
        if chapter in blocked_chapters:
            blocked_chapters[chapter] += 1

    total_checked = repair_results["closed"] + repair_results["remaining"]
    repair_rate = 0.0
    if total_checked > 0:
        repair_rate = round((repair_results["closed"] / total_checked) * 100, 2)

    report = {
        "issue_distribution": issue_distribution,
        "repair_results": repair_results,
        "unclosed_reasons": unclosed_reasons,
        "blocked_chapters": blocked_chapters,
        "repair_rate": repair_rate,
        "metadata": {
            "project": project,
            "repair_report": str(repair_report_path),
            "audit_report": str(audit_report_path),
        },
    }

    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Analyze book repair rate from audit reports")
    parser.add_argument("--project", default="朱批录", help="Project name (default: 朱批录)")
    args = parser.parse_args()

    try:
        report = analyze_repair_rate(args.project)
    except FileNotFoundError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        print(
            "This script requires the audit and repair reports "
            "to be generated first.",
            file=sys.stderr,
        )
        sys.exit(1)
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)

    print(f"=== Repair Rate Analysis for Project: {args.project} ===\n")
    print("Issue Distribution:")
    for cat, count in report["issue_distribution"].items():
        print(f"  {cat}: {count}")
    print("\nRepair Results:")
    print(f"  Closed: {report['repair_results']['closed']}")
    print(f"  Remaining: {report['repair_results']['remaining']}")
    print("\nUnclosed Reasons:")
    for reason, count in report["unclosed_reasons"].items():
        print(f"  {reason}: {count}")
    print("\nBlocked Chapters:")
    for chapter, count in report["blocked_chapters"].items():
        print(f"  Chapter {chapter}: {count}")
    print(f"\nOverall Repair Rate: {report['repair_rate']}%")

    output_path = BASE / f"data/{args.project}/reports/repair_rate_analysis.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[JSON report saved to: {output_path}]")


if __name__ == "__main__":
    main()