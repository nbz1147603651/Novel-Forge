"""Validate the narrative blueprint element library."""

from __future__ import annotations

import argparse
import json
import sys

from novel_forge.pipeline.steps.blueprint_element_select.validation import (
    ElementLibraryIssue,
    validate_element_library,
)


def _format_issue(issue: ElementLibraryIssue) -> str:
    return f"[{issue.severity.upper()}] {issue.code}: {issue.message}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate built-in/external narrative blueprint elements and presets."
    )
    parser.add_argument("--json", action="store_true", help="Print a JSON validation report.")
    parser.add_argument(
        "--strict-warnings",
        action="store_true",
        help="Exit non-zero when warnings are present.",
    )
    args = parser.parse_args(argv)

    report = validate_element_library()
    if args.json:
        print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    else:
        print(
            "Blueprint element library "
            f"v{report.version}: {report.total_elements} elements "
            f"({report.builtin_count} builtin, {report.external_count} external), "
            f"{report.required_count} required, {report.extension_count} extension."
        )
        if report.errors:
            print("\nErrors:")
            for issue in report.errors:
                print(f"- {_format_issue(issue)}")
        if report.warnings:
            print("\nWarnings:")
            for issue in report.warnings:
                print(f"- {_format_issue(issue)}")
        if report.ok and (not report.warnings or not args.strict_warnings):
            print("\nOK")

    if report.errors:
        return 1
    if args.strict_warnings and report.warnings:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
