#!/usr/bin/env python3
"""Create a maintainable prompt template skeleton for a new task."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_repo_root))

from novel_forge.common.constants import TaskType  # noqa: E402
from novel_forge.core.format_contracts import OutputKind, get_task_format_contract  # noqa: E402
from novel_forge.prompts.registry import _PROMPTS_DIR  # noqa: E402


def _resolve_task_type(task_type_text: str) -> TaskType | None:
    clean = task_type_text.strip()
    if not clean:
        return None
    if clean in TaskType.__members__:
        return TaskType[clean]
    try:
        return TaskType(clean)
    except ValueError:
        return None


def _safe_relpath(category: str, filename: str) -> Path:
    category_path = Path(category)
    filename_path = Path(filename)
    if category_path.is_absolute() or filename_path.is_absolute():
        raise ValueError("category and filename must be relative paths")
    if ".." in category_path.parts or ".." in filename_path.parts:
        raise ValueError("category and filename may not contain '..'")
    if filename_path.suffix != ".j2":
        filename_path = filename_path.with_suffix(".j2")
    return category_path / filename_path


def render_prompt_skeleton(
    *,
    task_type: str,
    category: str,
    filename: str,
    output_kind: OutputKind,
    required_keys: tuple[str, ...],
    purpose: str,
) -> str:
    """Render a new prompt template skeleton."""
    service_step = task_type.upper()
    output_summary = "TEXT：纯文本结果" if output_kind == OutputKind.TEXT else "JSON：结构化结果"
    fields = "、".join(f"`{key}`" for key in required_keys) if required_keys else "TODO"

    return f"""{{#
================================================================================
【备注】
================================================================================
提示词：{filename if filename.endswith(".j2") else filename + ".j2"}
服务步骤：{service_step}
作用简述：{purpose}
上游输入：
  - TODO：列出调用方注入的上下文变量、来源步骤和是否必需
下游输出：
  - {output_summary}
  - 下游消费者：TODO
消费链优先级：
  P0 = TODO：硬约束，例如 schema、不可变事实、字数、POV、禁用元素
  P1 = TODO：核心执行依据，例如 plan、bridge、contract、report
  P2 = TODO：风格建议或可选上下文
简要例子：
  输入：TODO
  输出：TODO
================================================================================
#}}

{{#- ---------------------------------------------------------------------------
    指导层：任务定义 + 上下文消费
    --------------------------------------------------------------------------- -#}}
## 任务
TODO：写清楚模型扮演的角色、要完成的任务，以及不能做什么。

## 输入
{{{{ input_payload | default("") }}}}

## 约束
1. TODO：写 P0 约束。
2. TODO：写 P1 执行规则。
3. TODO：写 P2 取舍规则。

## 业务字段说明
- 输出契约由 `TaskFormatContract` 和 `PromptBuilder` 统一注入，模板不得写 JSON 协议、Markdown 边界或格式宏。
- 需要在业务层解释的字段：{fields}
"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("task_type", help="TaskType value or enum name, e.g. plan_chapter")
    parser.add_argument("category", help="Prompt category directory, e.g. planning")
    parser.add_argument("--filename", help="Template filename; defaults to <task_type>.j2")
    parser.add_argument("--purpose", default="TODO：补充该提示词的业务用途")
    parser.add_argument("--output-kind", choices=[kind.value for kind in OutputKind])
    parser.add_argument("--required-key", action="append", default=[])
    parser.add_argument("--dry-run", action="store_true", help="Print skeleton instead of writing")
    parser.add_argument("--force", action="store_true", help="Overwrite existing template")
    args = parser.parse_args(argv)

    known_task = _resolve_task_type(args.task_type)
    contract = get_task_format_contract(known_task) if known_task else None
    output_kind = OutputKind(args.output_kind) if args.output_kind else None
    if output_kind is None and contract is not None:
        output_kind = contract.output_kind
    if output_kind is None:
        parser.error("--output-kind is required when task_type is not in TaskType/format_contracts")

    required_keys = tuple(args.required_key)
    if not required_keys and contract is not None:
        required_keys = contract.required_top_level_keys

    filename = args.filename or f"{args.task_type.lower()}.j2"
    rel_path = _safe_relpath(args.category, filename)
    target = _PROMPTS_DIR / rel_path
    skeleton = render_prompt_skeleton(
        task_type=args.task_type,
        category=args.category,
        filename=Path(filename).name,
        output_kind=output_kind,
        required_keys=required_keys,
        purpose=args.purpose,
    )

    if args.dry_run:
        print(skeleton)
        return 0

    if target.exists() and not args.force:
        parser.error(f"{target} already exists; pass --force to overwrite")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(skeleton, encoding="utf-8")
    print(f"Wrote {target}")
    print("Next steps:")
    print("  1. Add/confirm TaskType in novel_forge/common/constants.py")
    print("  2. Register TaskType -> template in novel_forge/prompts/registry.py")
    print("  3. Add/confirm TaskFormatContract in novel_forge/core/format_contracts.py")
    print("  4. Run scripts/lint_prompt_layers.py and scripts/generate_prompt_index.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
