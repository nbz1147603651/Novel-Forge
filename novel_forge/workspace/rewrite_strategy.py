"""Rewrite strategy helpers for chapter-studio regeneration."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from novel_forge.core.domain.guardrails import detect_prompt_leaks, scrub_prompt_artifacts
from novel_forge.persistence.filesystem import FileSystemStorage
from novel_forge.persistence.models import ProjectLayout
from novel_forge.workspace.contracts import RewriteStrategy

_CHAPTER_FILE_RE = re.compile(r"chapter_(\d+)\.md$")

REWRITE_STRATEGY_LABELS: dict[str, str] = {
    "auto": "自动判断",
    "sequential": "普通重写",
    "compatible": "兼容后文",
    "reconstruct": "重构后续",
    "surgical": "外科修补",
}


@dataclass(frozen=True)
class RewriteStrategyPlan:
    """Effective rewrite strategy plus optional prompt-side author constraints."""

    requested_strategy: str
    effective_strategy: str
    downstream_chapters: tuple[int, ...] = ()
    prompt_note: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def has_downstream(self) -> bool:
        return bool(self.downstream_chapters)


def build_rewrite_strategy_plan(
    *,
    storage: FileSystemStorage,
    layout: ProjectLayout,
    chapter_number: int,
    force: bool,
    requested_strategy: RewriteStrategy | str = "auto",
) -> RewriteStrategyPlan:
    """Resolve a rewrite strategy and build any author-layer prompt note.

    The causal chapter context remains past-only.  This helper only builds an
    explicit author-facing compatibility layer for early-chapter rewrites.
    """
    requested = _normalize_strategy(requested_strategy)
    downstream = tuple(_downstream_chapter_numbers(layout, chapter_number))
    effective = _effective_strategy(requested, force=force, has_downstream=bool(downstream))

    prompt_note = ""
    context_payload: dict[str, Any] = {
        "chapter_number": chapter_number,
        "requested_strategy": requested,
        "effective_strategy": effective,
        "strategy_label": REWRITE_STRATEGY_LABELS.get(effective, effective),
        "force": force,
        "downstream_count": len(downstream),
        "downstream_chapters": list(downstream[:20]),
        "author_layer_only": effective in {"compatible", "surgical"},
    }

    if requested == "compatible" and not downstream:
        context_payload["downgraded_reason"] = "no_downstream_chapters"
    if not force and requested == "auto":
        context_payload["downgraded_reason"] = "not_force_regeneration"

    if effective == "compatible":
        prompt_note = _build_compatibility_note(
            storage=storage,
            layout=layout,
            chapter_number=chapter_number,
            downstream=downstream,
            surgical=False,
        )
    elif effective == "surgical":
        prompt_note = _build_surgical_note()
        if downstream:
            prompt_note = (
                prompt_note
                + "\n\n"
                + _build_compatibility_note(
                    storage=storage,
                    layout=layout,
                    chapter_number=chapter_number,
                    downstream=downstream,
                    surgical=True,
                )
            ).strip()
    elif effective == "reconstruct":
        prompt_note = (
            "【重写策略：重构式重写】\n"
            "本次以当前章以前的因果上下文、当前大纲与用户备注为准；"
            "不读取已写后文兼容约束。本章之后的旧正文可能需要重新生成或修补。"
        )

    if prompt_note:
        context_payload["prompt_note"] = prompt_note
    if downstream:
        context_payload.update(
            _build_downstream_payload(
                storage=storage,
                layout=layout,
                chapter_number=chapter_number,
                downstream=downstream,
            )
        )

    return RewriteStrategyPlan(
        requested_strategy=requested,
        effective_strategy=effective,
        downstream_chapters=downstream,
        prompt_note=prompt_note,
        metadata=context_payload,
    )


def notes_with_rewrite_strategy(existing_notes: str, plan: RewriteStrategyPlan) -> str:
    """Append the strategy prompt note to user notes without duplicating it."""
    base = str(existing_notes or "").strip()
    note = str(plan.prompt_note or "").strip()
    if not note:
        return base
    if note in base:
        return base
    return f"{base}\n\n{note}".strip() if base else note


def rewrite_context_path(layout: ProjectLayout, chapter_number: int) -> Path:
    return layout.states_dir / f"chapter_{chapter_number:03d}_rewrite_context.json"


def _normalize_strategy(value: Any) -> str:
    raw = str(value or "auto").strip().lower()
    return raw if raw in REWRITE_STRATEGY_LABELS else "auto"


def _effective_strategy(requested: str, *, force: bool, has_downstream: bool) -> str:
    if not force and requested == "auto":
        return "sequential"
    if requested == "auto":
        return "compatible" if has_downstream else "sequential"
    if requested == "compatible" and not has_downstream:
        return "sequential"
    return requested


def _downstream_chapter_numbers(layout: ProjectLayout, chapter_number: int) -> list[int]:
    numbers: list[int] = []
    for path in layout.chapters_dir.glob("chapter_*.md"):
        match = _CHAPTER_FILE_RE.match(path.name)
        if not match:
            continue
        try:
            number = int(match.group(1))
        except ValueError:
            continue
        if number > chapter_number:
            numbers.append(number)
    return sorted(numbers)


def _build_compatibility_note(
    *,
    storage: FileSystemStorage,
    layout: ProjectLayout,
    chapter_number: int,
    downstream: tuple[int, ...],
    surgical: bool,
) -> str:
    payload = _build_downstream_payload(
        storage=storage,
        layout=layout,
        chapter_number=chapter_number,
        downstream=downstream,
    )
    lines: list[str] = [
        "【作者层后文兼容约束】",
        "用途：仅用于让早期重写后的正文尽量接住已写后文；这些内容不是本章角色已知信息。",
        "硬规则：本章因果上下文仍只承认前文；不得把后文章节事件直接写成本章事实或提前揭露。",
        "优先级：若本章章节契约/大纲禁止某人物、真相或事件出场，优先遵守本章契约；用替代交接方式保持后文可接。",
    ]
    if surgical:
        lines.append("外科修补：只围绕指定问题做最小必要调整，尽量保持本章结构、节奏与章末接口。")
    count = int(payload.get("downstream_count", 0) or 0)
    lines.append(f"后文状态：第 {chapter_number + 1} 章以后已有 {count} 章正文。")

    opening = str(payload.get("next_chapter_opening_excerpt", "") or "").strip()
    next_chapter = payload.get("next_chapter")
    if opening and next_chapter:
        lines.append(f"硬连续性锚点：第 {next_chapter} 章开头已经写成：{opening}")

    summaries = payload.get("downstream_summaries", [])
    if isinstance(summaries, list) and summaries:
        lines.append("后文依赖摘要（作者层，只可用于兼容，不可照搬为本章事实）：")
        for item in summaries[:6]:
            if isinstance(item, dict):
                ch = item.get("chapter_number", "?")
                summary = str(item.get("summary", "") or "").strip()
                if summary:
                    lines.append(f"- 第 {ch} 章：{summary}")

    lines.extend(
        [
            "写法要求：可以埋物件、情绪、动机或环境伏笔；不能提前解释未来真相。",
            "生成后需要保持下一章开头能自然衔接，但不要复刻旧版本章中的违规内容。",
        ]
    )
    return "\n".join(lines)


def _build_surgical_note() -> str:
    return (
        "【重写策略：外科修补】\n"
        "只围绕用户备注或已选问题做最小必要改动；尽量保留本章原有结构、场景顺序、"
        "人物关系、情绪递进与章末接口。不要扩大重写范围，不要新增无关设定。"
    )


def _build_downstream_payload(
    *,
    storage: FileSystemStorage,
    layout: ProjectLayout,
    chapter_number: int,
    downstream: tuple[int, ...],
) -> dict[str, Any]:
    next_chapter = downstream[0] if downstream else 0
    opening = _chapter_opening_excerpt(storage, layout, next_chapter) if next_chapter else ""
    summaries = [
        item
        for ch in downstream[:6]
        if (item := _downstream_summary(storage, layout, ch))
    ]
    return {
        "downstream_count": len(downstream),
        "downstream_chapters": list(downstream[:20]),
        "next_chapter": next_chapter,
        "next_chapter_opening_excerpt": opening,
        "downstream_summaries": summaries,
        "compatibility_scope": {
            "start_chapter": chapter_number + 1,
            "sampled_chapters": list(downstream[:6]),
        },
    }


def _chapter_opening_excerpt(
    storage: FileSystemStorage,
    layout: ProjectLayout,
    chapter_number: int,
    *,
    limit: int = 700,
) -> str:
    if chapter_number <= 0:
        return ""
    try:
        text = storage.load_text(layout.chapter_path(chapter_number))
    except Exception:
        return ""
    text = re.sub(r"^\s*#.*(?:\n|$)", "", str(text or ""), count=1).strip()
    text = _sanitize_compatibility_text(text)
    return _excerpt(text, limit=limit)


def _downstream_summary(
    storage: FileSystemStorage,
    layout: ProjectLayout,
    chapter_number: int,
) -> dict[str, Any] | None:
    summary = _creative_report_summary(storage, layout, chapter_number)
    if not summary:
        summary = _chapter_opening_excerpt(storage, layout, chapter_number, limit=240)
    if not summary:
        return None
    return {
        "chapter_number": chapter_number,
        "summary": summary,
    }


def _creative_report_summary(
    storage: FileSystemStorage,
    layout: ProjectLayout,
    chapter_number: int,
) -> str:
    try:
        payload = storage.load_json(layout.creative_report_path(chapter_number))
    except Exception:
        return ""
    if not isinstance(payload, dict):
        return ""
    for key in (
        "summary",
        "chapter_summary",
        "overall_assessment",
        "main_plot_advancement_notes",
        "creative_summary",
    ):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return _excerpt(_sanitize_compatibility_text(value), limit=280)
    structured = payload.get("structured_summary")
    if isinstance(structured, dict):
        parts = [
            str(structured.get(key, "") or "").strip()
            for key in ("chapter_summary", "main_event", "ending_state")
            if str(structured.get(key, "") or "").strip()
        ]
        if parts:
            return _excerpt(_sanitize_compatibility_text("；".join(parts)), limit=280)
    return ""


def _sanitize_compatibility_text(text: str) -> str:
    cleaned, _removed = scrub_prompt_artifacts(str(text or ""))
    if detect_prompt_leaks(cleaned, max_hits=1):
        return ""
    return cleaned


def _excerpt(text: str, *, limit: int) -> str:
    cleaned = re.sub(r"\n{3,}", "\n\n", str(text or "").strip())
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[:limit].rstrip() + "……"
