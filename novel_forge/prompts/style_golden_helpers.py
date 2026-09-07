"""Template-level helpers for rendering StyleGoldenRetriever output.

These are registered as Jinja2 globals via ``PromptRegistry._globals`` so any
template can call them as a plain function rather than via a cross-file macro
(which jinja2 does NOT propagate context to).

Added in M3.5 — see docs/ai_flavor_quality.md.
"""

from __future__ import annotations

from typing import Any

_DEFAULT_TITLE = "### 项目级高分段落参考（请学习笔触，禁止逐字复用）"


def render_style_golden_examples(
    style_golden_examples: list[dict[str, Any]] | None = None,
    *,
    title: str = _DEFAULT_TITLE,
) -> str:
    """Render the project-level high-scoring passages as a markdown block.

    Called as a Jinja2 global from prompt templates. Returns an empty string
    when there are no examples so the call site can remain unconditional::

        {{ render_style_golden_examples(style_golden_examples) }}

    Empty list / None / missing items short-circuit to "" — the section is
    not rendered at all.
    """
    if not style_golden_examples:
        return ""

    lines = [title, ""]
    for passage in style_golden_examples:
        if not isinstance(passage, dict):
            continue
        chapter_number = passage.get("chapter_number", "?")
        eval_score = passage.get("eval_score", 0.0)
        text = str(passage.get("text", "") or "").strip()
        if not text:
            continue
        try:
            score_str = f"{float(eval_score):.1f}"
        except (TypeError, ValueError):
            score_str = str(eval_score)
        lines.append(f"- **第 {chapter_number} 章 · 评分 {score_str}**：")
        # Quote-block each line for readability
        for text_line in text.splitlines() or [text]:
            lines.append(f"  > {text_line}")
        lines.append("")

    # Drop the trailing blank line so consecutive sections don't double-space.
    while lines and lines[-1] == "":
        lines.pop()
    return "\n".join(lines)


__all__ = ["render_style_golden_examples"]
