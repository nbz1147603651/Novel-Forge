#!/usr/bin/env python3
"""Verify Jinja2 prompt template syntax for a prompt pack."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

_repo_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_repo_root))

from novel_forge.prompts.packs import get_prompt_pack  # noqa: E402
from novel_forge.prompts.registry import _TASK_TEMPLATE_MAP  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--locale", "--lang", default="zh", help="Prompt pack locale to verify.")
    args = parser.parse_args(argv)

    pack = get_prompt_pack(args.locale)
    tmpl_dir = pack.templates_dir
    env = Environment(
        loader=FileSystemLoader(str(tmpl_dir)),
        extensions=["jinja2.ext.do"],
    )

    templates = sorted(
        str(path.relative_to(tmpl_dir)).replace("\\", "/") for path in tmpl_dir.rglob("*.j2")
    )

    all_ok = True
    for template_name in templates:
        try:
            env.get_template(template_name)
            print(f"OK  {template_name}")
        except Exception as exc:
            print(f"ERR {template_name} — {exc}")
            all_ok = False

    registered_templates = sorted(set(_TASK_TEMPLATE_MAP.values()))
    for template_name in registered_templates:
        if template_name not in templates:
            print(f"ERR registry — missing template file: {template_name}")
            all_ok = False
            continue
        try:
            env.get_template(template_name)
        except Exception as exc:
            print(f"ERR registry {template_name} — {exc}")
            all_ok = False

    if all_ok:
        print(
            f"\n所有模板语法正常。locale={pack.locale}；"
            f"共检查 {len(templates)} 个模板，{len(registered_templates)} 个注册任务模板文件。"
        )
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
