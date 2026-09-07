#!/usr/bin/env python3
"""Generate the prompt template index from registry, contracts, and metadata."""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from pathlib import Path

_repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_repo_root))

from novel_forge.core.format_contracts import OutputKind  # noqa: E402
from novel_forge.prompts.metadata import (  # noqa: E402
    PromptTemplateRecord,
    collect_prompt_templates,
)
from novel_forge.prompts.packs import get_prompt_pack  # noqa: E402

_CATEGORY_NAMES = {
    "beats": "节拍",
    "canon": "典据",
    "checking": "检查",
    "compression": "压缩",
    "initialization": "初始化",
    "planning": "规划",
    "summary": "总结",
    "writing": "写作",
}


def _task_text(record: PromptTemplateRecord) -> str:
    if not record.task_names:
        return "-"
    return "<br>".join(f"`{name}`" for name in record.task_names)


def _output_text(record: PromptTemplateRecord) -> str:
    if not record.output_kinds:
        return "-"
    return "/".join(kind.value.upper() for kind in record.output_kinds)


def _keys_text(record: PromptTemplateRecord) -> str:
    if not record.required_top_level_keys:
        return "-"
    keys = "、".join(f"`{key}`" for key in record.required_top_level_keys)
    if record.enforce_required_keys:
        return f"{keys}（强制）"
    return keys


def _purpose_text(record: PromptTemplateRecord) -> str:
    if not record.purpose:
        return "-"
    return record.purpose.replace("|", "\\|")


def render_prompt_index(records: tuple[PromptTemplateRecord, ...] | None = None) -> str:
    """Render the generated prompt INDEX.md content."""
    catalog = records if records is not None else collect_prompt_templates()
    registered = tuple(record for record in catalog if record.registered)
    partials = tuple(record for record in catalog if record.partial)
    standalone = tuple(record for record in catalog if not record.registered and not record.partial)

    by_category: dict[str, list[PromptTemplateRecord]] = defaultdict(list)
    for record in registered:
        by_category[record.category].append(record)

    json_count = sum(OutputKind.JSON in record.output_kinds for record in registered)
    text_count = sum(OutputKind.TEXT in record.output_kinds for record in registered)

    lines = [
        "# 提示词模板索引",
        "",
        "> 本文件由 `scripts/generate_prompt_index.py` 根据 `registry.py`、"
        "`format_contracts.py` 与模板备注自动生成。请不要手工维护模板清单。",
        "",
        "## 模板概览",
        "",
        f"- 注册任务模板：{len(registered)}",
        f"- 共享宏 / 局部模板：{len(partials)}",
        f"- 未注册独立模板：{len(standalone)}",
        f"- JSON 输出任务模板：{json_count}",
        f"- TEXT 输出任务模板：{text_count}",
        "",
        "| 类别 | 中文名 | 文件数 |",
        "|------|--------|--------|",
    ]

    for category in sorted(by_category):
        name = _CATEGORY_NAMES.get(category, category)
        lines.append(f"| `{category}/` | {name} | {len(by_category[category])} |")

    lines.extend(
        [
            "",
            "## 注册任务模板",
            "",
        ]
    )

    for category in sorted(by_category):
        name = _CATEGORY_NAMES.get(category, category)
        lines.extend(
            [
                f"### {category}/ - {name}",
                "",
                "| 模板文件 | TaskType | 输出 | 顶层字段 | 用途 |",
                "|---------|----------|------|----------|------|",
            ]
        )
        for record in sorted(by_category[category], key=lambda item: item.template):
            filename = Path(record.template).name
            lines.append(
                f"| [{filename}]({record.template}) | {_task_text(record)} | "
                f"{_output_text(record)} | {_keys_text(record)} | {_purpose_text(record)} |"
            )
        lines.append("")

    if partials:
        lines.extend(
            [
                "## 共享宏与局部模板",
                "",
                "| 模板文件 | 类别 | 用途 |",
                "|---------|------|------|",
            ]
        )
        for record in sorted(partials, key=lambda item: item.template):
            lines.append(
                f"| [{record.template}]({record.template}) | `{record.category}` | "
                f"{_purpose_text(record)} |"
            )
        lines.append("")

    if standalone:
        lines.extend(
            [
                "## 未注册独立模板",
                "",
                "| 模板文件 | 用途 |",
                "|---------|------|",
            ]
        )
        for record in sorted(standalone, key=lambda item: item.template):
            lines.append(f"| [{record.template}]({record.template}) | {_purpose_text(record)} |")
        lines.append("")

    lines.extend(
        [
            "## 维护命令",
            "",
            "```bash",
            "python scripts/lint_prompt_layers.py",
            "python scripts/audit_prompt_format_layers.py --all",
            "python scripts/audit_prompt_packs.py",
            "python scripts/scaffold_prompt.py <task_type> <category> --output-kind json",
            "python scripts/generate_prompt_index.py --check",
            "python scripts/verify_templates.py",
            "python scripts/verify_format_contracts.py",
            "```",
            "",
        ]
    )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Fail if INDEX.md is not already up to date.",
    )
    parser.add_argument("--locale", "--lang", default="zh", help="Prompt pack locale to index.")
    args = parser.parse_args(argv)

    pack = get_prompt_pack(args.locale)
    records = collect_prompt_templates(prompts_dir=pack.templates_dir)
    index_path = pack.templates_dir / "INDEX.md"
    rendered = render_prompt_index(records)
    if args.check:
        current = index_path.read_text(encoding="utf-8") if index_path.exists() else ""
        if current.rstrip() != rendered.rstrip():
            print(f"{index_path} is out of date. Run scripts/generate_prompt_index.py.")
            return 1
        print(f"{index_path} is up to date.")
        return 0

    index_path.write_text(rendered, encoding="utf-8")
    print(f"Wrote {index_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
