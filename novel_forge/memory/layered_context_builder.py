"""Layered memory context builder for prompt injection."""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from novel_forge.memory.base import MemoryBudgetConfig
from novel_forge.obs.logger import get_logger

_log = get_logger("memory.context")


def build_layered_memory_context(
    *,
    current_chapter: int,
    budget: MemoryBudgetConfig | None = None,
    project_identity: str = "",
    query: str = "",
    settings: Any,
    summary_service: Any = None,
    episodic_memory: Any = None,
    volume_finder: Callable[[int], tuple[Any, Any] | tuple[None, None]] | None = None,
    relevant_history_search: Callable[..., list[dict[str, Any]]] | None = None,
    logger: Any = _log,
) -> dict[str, str]:
    """Assemble complete selected L0-L3 memory without prefix truncation.

    Layer budgets are pressure signals only. Selection belongs to the
    authoritative summary/retrieval boundary; once content is selected here,
    the complete text is preserved for downstream coverage.
    """
    budget = budget or MemoryBudgetConfig()
    result: dict[str, str] = {
        "L0_identity": "",
        "L1_core_memory": "",
        "L2_on_demand": "",
        "L3_deep_search": "",
    }

    # L0: Project identity
    l0_budget = budget.l0_identity
    if l0_budget.enabled and project_identity:
        result["L0_identity"] = project_identity

    # L1: Core memory (volume-aware + recent chapter summaries)
    l1_budget = budget.l1_core_memory
    l1_parts: list[str] = []
    if l1_budget.enabled and summary_service:
        current_vol, prev_vol = (
            volume_finder(current_chapter) if volume_finder is not None else (None, None)
        )

        if current_vol is not None:
            vol_start = getattr(current_vol, "start_chapter", 1)
            vol_end = getattr(current_vol, "end_chapter", 1)
            vol_span = max(1, vol_end - vol_start + 1)
            progress = (current_chapter - vol_start) / vol_span

            vol_title = getattr(
                current_vol,
                "title",
                f"卷{getattr(current_vol, 'volume_number', '?')}",
            )
            vol_num = getattr(current_vol, "volume_number", "?")
            arc_goal = getattr(current_vol, "arc_goal", "")
            main_conflicts = getattr(current_vol, "main_conflicts", [])
            milestones = getattr(current_vol, "milestone_targets", [])
            climax_hint = getattr(current_vol, "climax_hint", "")

            if progress >= 0.15 and arc_goal:
                vol_parts: list[str] = [
                    f"## 当前卷：{vol_title}（卷{vol_num}，第{vol_start}-{vol_end}章）",
                    f"卷目标：{arc_goal}",
                ]
                if main_conflicts:
                    vol_parts.append(f"核心冲突：{'；'.join(main_conflicts)}")
                if milestones:
                    ml_lines = ["**叙事里程碑**："]
                    for i, ms in enumerate(milestones):
                        expected_ch = vol_start + int(vol_span * (i + 1) / (len(milestones) + 1))
                        marker = "✓" if current_chapter > expected_ch else "○"
                        ml_lines.append(f"  {marker} {ms}（约第{expected_ch}章）")
                    vol_parts.append("\n".join(ml_lines))
                l1_parts.append("\n".join(vol_parts))

            if progress >= 0.85 and climax_hint:
                l1_parts.append(f"⚠️ 卷末高潮提示：{climax_hint}")

            if prev_vol is not None and progress < 0.35:
                prev_vol_num = getattr(prev_vol, "volume_number", 0)
                prev_title = getattr(prev_vol, "title", f"卷{prev_vol_num}")

                resolution = getattr(prev_vol, "resolution_hint", "")
                if resolution:
                    l1_parts.insert(
                        0,
                        f"## 上卷收束（卷{prev_vol_num}「{prev_title}」）\n{resolution}",
                    )

                prev_summary = summary_service.get_summary_for_context(
                    current_chapter=current_chapter,
                    granularity="volume",
                    lookback_volumes=1,
                    current_volume_number=prev_vol_num,
                )
                if prev_summary:
                    if resolution:
                        l1_parts.insert(
                            1,
                            f"## 上卷详要（卷{prev_vol_num}「{prev_title}」）\n{prev_summary}",
                        )
                    else:
                        l1_parts.insert(
                            0,
                            f"## 上卷摘要（卷{prev_vol_num}「{prev_title}」）\n{prev_summary}",
                        )

        else:
            threshold = settings.memory_volume_summary_inject_threshold
            if current_chapter > threshold:
                vol_summary = summary_service.get_summary_for_context(
                    current_chapter=current_chapter,
                    granularity="volume",
                    lookback_volumes=1,
                )
                if vol_summary:
                    l1_parts.append(f"## 卷级宏观\n{vol_summary}")

        recent = summary_service.get_summary_for_context(
            current_chapter=current_chapter,
            granularity="chapter",
        )
        if recent:
            l1_parts.append(recent)
    l1_text = "\n\n".join(l1_parts)
    # Each chapter/volume summary is already model-authored to a configured
    # target size. Keep the complete selected summary window here: a second
    # character-prefix cut can remove the causal payoff from the end.
    result["L1_core_memory"] = l1_text

    # L2: On-demand (recent episodic events)
    l2_budget = budget.l2_on_demand
    l2_text = ""
    l2_events: list[Any] = []
    if l2_budget.enabled and episodic_memory:
        try:
            l2_events = list(
                episodic_memory.search_by_temporal(
                    start_chapter=max(1, current_chapter - 5),
                    end_chapter=current_chapter - 1,
                )
            )
            l2_events.sort(
                key=lambda evt: (evt.chapter_number, evt.scene_index),
                reverse=True,
            )
            if l2_events:
                lines = ["## 近期事件"]
                for evt in l2_events:
                    chars = "、".join(evt.characters_involved) if evt.characters_involved else ""
                    char_part = f"（{chars}）" if chars else ""
                    lines.append(f"- 第{evt.chapter_number}章{char_part}: {evt.event_summary}")
                l2_text = "\n".join(lines)
        except Exception as exc:
            logger.warning("L2 on-demand retrieval failed: %s", exc)
    result["L2_on_demand"] = l2_text

    # Dedup: L1 vs L2 — prefer L2 (precise semantic events)
    if l2_text and l1_text:
        l2_chapters = {
            int(getattr(evt, "chapter_number", 0) or 0)
            for evt in l2_events
            if int(getattr(evt, "chapter_number", 0) or 0) > 0
        }
        if l2_chapters:
            original_l1 = l1_text
            l1_parts_deduped = []
            for part in l1_parts:
                part_chapters = {int(m.group(1)) for m in re.finditer(r"第\s*(\d+)\s*章", part)}
                if part_chapters & l2_chapters:
                    continue
                l1_parts_deduped.append(part)
            l1_text = "\n\n".join(l1_parts_deduped)
            if l1_text != original_l1:
                logger.info(
                    "Memory dedup applied | chapter=%d | L1 parts: %d→%d",
                    current_chapter,
                    len(l1_parts),
                    len(l1_parts_deduped),
                )
            result["L1_core_memory"] = l1_text

    # L3: Deep search (semantic query)
    l3_budget = budget.l3_deep_search
    l3_text = ""
    if l3_budget.enabled and query and episodic_memory and relevant_history_search is not None:
        try:
            search_results = relevant_history_search(
                query=query,
                current_chapter=current_chapter,
                lookback=10,
                top_k=5,
                min_relevance=0.3,
            )
            if search_results:
                lines = ["## 相关历史"]
                for item in search_results:
                    chars = "、".join(item.get("characters_involved", []))
                    char_part = f"（{chars}）" if chars else ""
                    lines.append(
                        f"- 第{item['chapter_number']}章{char_part} "
                        f"[相关度{item['relevance_score']:.2f}]: "
                        f"{item['event_summary']}"
                    )
                l3_text = "\n".join(lines)
        except Exception as exc:
            logger.warning("L3 deep search failed: %s", exc)
    result["L3_deep_search"] = l3_text

    return result


__all__ = ("build_layered_memory_context",)
