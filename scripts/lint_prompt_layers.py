#!/usr/bin/env python3
"""Lint prompt template maintainability layers and output boundaries."""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

_repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_repo_root))

from novel_forge.prompts.metadata import (  # noqa: E402
    PromptLintIssue,
    collect_prompt_templates,
    lint_prompt_catalog,
)
from novel_forge.prompts.packs import get_prompt_pack  # noqa: E402


def _print_summary(issue_list: tuple[PromptLintIssue, ...], *, locale: str) -> None:
    pack = get_prompt_pack(locale)
    records = collect_prompt_templates(prompts_dir=pack.templates_dir)
    registered = [record for record in records if record.registered]
    by_category = Counter(record.category for record in registered)

    print(f"Prompt layer lint ({pack.locale})")
    print("=" * 60)
    print(f"Templates: {len(records)} total, {len(registered)} registered task templates")
    print("Registered by category:")
    for category, count in sorted(by_category.items()):
        print(f"  - {category}: {count}")
    print()

    if not issue_list:
        print("RESULT: PASS — all registered prompt templates have maintainable layers")
        return

    print("Issues:")
    for issue in issue_list:
        print(f"  [{issue.severity.upper()}] {issue.template} :: {issue.code} — {issue.message}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Only print issues, not the category summary.",
    )
    parser.add_argument("--locale", "--lang", default="zh", help="Prompt pack locale to lint.")
    args = parser.parse_args(argv)

    pack = get_prompt_pack(args.locale)
    records = collect_prompt_templates(prompts_dir=pack.templates_dir)
    issues = lint_prompt_catalog(records)
    if args.quiet:
        for issue in issues:
            print(f"{issue.template}\t{issue.code}\t{issue.message}")
    else:
        _print_summary(issues, locale=pack.locale)

    return 1 if any(issue.severity == "error" for issue in issues) else 0


if __name__ == "__main__":
    raise SystemExit(main())
