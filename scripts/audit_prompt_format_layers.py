#!/usr/bin/env python3
"""Audit whether prompt format layers match each registered task contract."""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

_repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_repo_root))

from novel_forge.prompts.metadata import collect_prompt_templates, lint_prompt_catalog  # noqa: E402
from novel_forge.prompts.packs import get_prompt_pack  # noqa: E402


def _keys_text(keys: tuple[str, ...], *, strict: bool) -> str:
    if not keys:
        return "-"
    suffix = " !" if strict else ""
    return ", ".join(keys) + suffix


def _sources_text(sources: tuple[str, ...]) -> str:
    return ", ".join(sources) if sources else "-"


def _render_markdown(*, show_all: bool, locale: str) -> str:
    pack = get_prompt_pack(locale)
    catalog = collect_prompt_templates(prompts_dir=pack.templates_dir)
    records = tuple(record for record in catalog if record.registered)
    issues = lint_prompt_catalog(catalog)
    by_profile = Counter(record.format_profile for record in records)

    lines = [
        "# Prompt Format Layer Audit",
        "",
        f"Prompt pack: `{pack.locale}` (`{pack.status}`)",
        "",
        "This report is generated from `PromptTemplateRecord` metadata, "
        "`TaskFormatContract`, and registered template files.",
        "",
        "## Summary",
        "",
    ]
    for profile, count in sorted(by_profile.items()):
        lines.append(f"- `{profile}`: {count}")
    lines.append(f"- lint issues: {len(issues)}")
    lines.append("")

    if issues:
        lines.extend(["## Issues", ""])
        for issue in issues:
            lines.append(f"- `{issue.template}` `{issue.code}`: {issue.message}")
        lines.append("")

    if show_all:
        lines.extend(
            [
                "## Per-Task Matrix",
                "",
                "| TaskType | Template | Profile | Keys | Boundary | Missing Keys |",
                "|----------|----------|---------|------|----------|--------------|",
            ]
        )
        for record in sorted(records, key=lambda item: (item.category, item.template)):
            for task_name in record.task_names:
                missing = ", ".join(record.missing_required_keys) or "-"
                lines.append(
                    f"| `{task_name}` | `{record.template}` | `{record.format_profile}` | "
                    f"{_keys_text(record.required_top_level_keys, strict=record.enforce_required_keys)} | "
                    f"{_sources_text(record.format_boundary_sources)} | {missing} |"
                )
        lines.append("")

    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--all",
        action="store_true",
        help="Include the full per-task matrix.",
    )
    parser.add_argument("--locale", "--lang", default="zh", help="Prompt pack locale to audit.")
    args = parser.parse_args(argv)

    pack = get_prompt_pack(args.locale)
    catalog = collect_prompt_templates(prompts_dir=pack.templates_dir)
    print(_render_markdown(show_all=args.all, locale=pack.locale))
    return 1 if lint_prompt_catalog(catalog) else 0


if __name__ == "__main__":
    raise SystemExit(main())
