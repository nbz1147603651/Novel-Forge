"""Shared plot guard functionality to break circular dependency between CLI and workspace."""

from __future__ import annotations

import hashlib
import json
import logging
import shutil
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Union

from novel_forge.core.constants import TaskType
from novel_forge.core.parsing.parse_utils import safe_parse_json

if TYPE_CHECKING:
    from novel_forge.story_kernel.schemas import CreativeReport
from novel_forge.core.schemas.chapter import PlotGuardDecision
from novel_forge.core.schemas.outline import ChapterOutline, StoryOutline
from novel_forge.gateway.router import ModelRouter
from novel_forge.persistence.filesystem import FileSystemStorage
from novel_forge.persistence.models import ProjectLayout
from novel_forge.pipeline.long.services.plot_milestones import select_milestone_window
from novel_forge.pipeline.token_budget import calculate_route_aware_max_tokens
from novel_forge.prompts.builder import PromptBuilder

_logger = logging.getLogger(__name__)


def _source_text_hash(text: str) -> str:
    return hashlib.sha256(str(text or "").encode("utf-8")).hexdigest()


def _append_chapter_audit_log(
    *,
    layout: ProjectLayout,
    chapter_number: int,
    result: Any,
    decision: PlotGuardDecision,
    report: CreativeReport,
) -> None:
    """Append chapter audit record to chapter_audit_log.jsonl."""
    audit_log_path = layout.root / "states" / "chapter_audit_log.jsonl"
    try:
        audit_log_path.parent.mkdir(parents=True, exist_ok=True)

        alignment_score = getattr(
            getattr(result, "alignment_report", None), "alignment_score", None
        )
        continuity_score = getattr(
            getattr(result, "continuity_report", None), "continuity_score", None
        )
        causal_score = getattr(getattr(result, "causal_report", None), "causal_score", None)
        eval_score = getattr(getattr(result, "eval_report", None), "overall_score", None)

        deviation_levels = (
            [str(d.deviation_level) for d in report.plot_deviations]
            if report.plot_deviations
            else []
        )
        new_characters = (
            [str(ch.name) for ch in report.new_characters] if report.new_characters else []
        )
        new_locations = [str(loc) for loc in report.new_locations] if report.new_locations else []
        new_key_items = [str(item) for item in report.new_key_items] if report.new_key_items else []

        record = {
            "chapter_number": chapter_number,
            "timestamp": datetime.now().isoformat(),
            "alignment_score": float(alignment_score) if alignment_score is not None else 0.0,
            "continuity_score": float(continuity_score) if continuity_score is not None else 0.0,
            "causal_score": float(causal_score) if causal_score is not None else None,
            "eval_score": float(eval_score) if eval_score is not None else None,
            "deviation_count": len(report.plot_deviations) if report.plot_deviations else 0,
            "deviation_levels": deviation_levels,
            "new_characters": new_characters,
            "new_locations": new_locations,
            "new_key_items": new_key_items,
            "risk_level": decision.risk_level,
            "guard_decision": decision.decision,
        }

        with open(audit_log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as exc:
        _logger.debug("Failed to append chapter audit log: %s", exc)


def _select_plot_guard_outline_window(
    outline: StoryOutline,
    *,
    chapter_number: int,
    max_context_chapters: int,
) -> list[dict[str, Any]]:
    """Return a bounded outline window centered on current→future chapters."""
    start = max(1, chapter_number)
    selected = [ch.model_dump(mode="json") for ch in outline.chapters if ch.chapter_number >= start]
    return selected[: max(1, max_context_chapters)]


def _select_plot_guard_milestone_window(
    layout: ProjectLayout,
    storage: FileSystemStorage,
    *,
    chapter_number: int,
    max_context_chapters: int,
) -> dict[str, Any]:
    """Return a local milestone window if init produced the compact index."""

    if not storage.exists(layout.plot_milestone_index_path):
        return {}
    try:
        index_payload = storage.load_json(layout.plot_milestone_index_path)
        future = max(1, min(4, int(max_context_chapters or 4)))
        return select_milestone_window(
            index_payload,
            chapter_number=chapter_number,
            previous=2,
            future=future,
        ).model_dump(mode="json")
    except Exception as exc:
        _logger.debug("Failed to load plot guard milestone window: %s", exc)
        return {}


def _outline_window_from_milestone_window(window: dict[str, Any]) -> list[dict[str, Any]]:
    """Project a milestone window into the legacy outline_window prompt slot."""

    rows: list[dict[str, Any]] = []
    for group in ("previous_context", "current", "future_guardrails"):
        for milestone in list(window.get(group, []) or []):
            if not isinstance(milestone, dict):
                continue
            rows.append(
                {
                    "chapter_number": milestone.get("chapter_number"),
                    "title": milestone.get("title", ""),
                    "goal": milestone.get("summary", ""),
                    "main_plot_points": [
                        str(milestone.get("summary", "") or "").strip()
                    ],
                    "milestone_status": milestone.get("status", ""),
                    "milestone_group": group,
                }
            )
    return rows


def _normalize_plot_guard_decision(raw: Any) -> PlotGuardDecision:
    """Normalize raw LLM output to a robust PlotGuardDecision."""
    allowed_decisions = {
        "continue",
        "continue_with_constraints",
        "adjust_outline_fast",
        "adjust_outline_smart",
        "pause_for_human",
        "rollback_and_regen",
    }
    allowed_risk = {"low", "medium", "high"}
    allowed_outline_actions = {"none", "fast", "smart"}
    allowed_entity_types = {"character", "location", "item"}
    allowed_entity_actions = {"keep", "defer", "merge", "drop"}

    data = raw if isinstance(raw, dict) else {}
    decision = str(data.get("decision", "continue_with_constraints")).strip().lower()
    if decision not in allowed_decisions:
        decision = "continue_with_constraints"

    risk_level = str(data.get("risk_level", "medium")).strip().lower()
    if risk_level not in allowed_risk:
        risk_level = "medium"

    # 降级单章自动调整大纲权限：仅由宏观审计模块触发
    reasoning_brief = str(data.get("reasoning_brief", "")).strip()
    if decision in {"adjust_outline_fast", "adjust_outline_smart"}:
        original_intent = f"原始意图：{decision}"
        if risk_level == "high":
            decision = "rollback_and_regen"
        else:
            decision = "continue_with_constraints"
        if reasoning_brief:
            reasoning_brief = f"{reasoning_brief} [{original_intent}]"
        else:
            reasoning_brief = original_intent

    outline_action = str(data.get("outline_action", "none")).strip().lower()
    if outline_action not in allowed_outline_actions:
        outline_action = "none"

    entity_actions: list[dict[str, str]] = []
    raw_entity_actions = data.get("entity_actions", [])
    if isinstance(raw_entity_actions, list):
        for item in raw_entity_actions:
            if not isinstance(item, dict):
                continue
            entity_type = str(item.get("entity_type", "character")).strip().lower()
            if entity_type not in allowed_entity_types:
                entity_type = "character"
            action = str(item.get("action", "keep")).strip().lower()
            if action not in allowed_entity_actions:
                action = "keep"
            entity_actions.append(
                {
                    "entity_type": entity_type,
                    "name": str(item.get("name", "")).strip(),
                    "action": action,
                    "reason": str(item.get("reason", "")).strip(),
                    "merge_target": str(item.get("merge_target", "")).strip(),
                }
            )

    constraints: list[str] = []
    raw_constraints = data.get("next_chapter_constraints", [])
    if isinstance(raw_constraints, list):
        for item in raw_constraints:
            text = str(item).strip()
            if text:
                constraints.append(text)
            if len(constraints) >= 5:
                break

    # 处理定向修复指令
    targeted_repairs: list[dict[str, str]] = []
    raw_repairs = data.get("targeted_repairs", [])
    if isinstance(raw_repairs, list):
        for item in raw_repairs:
            if not isinstance(item, dict):
                continue
            repair_type = str(item.get("repair_type", "")).strip().lower()
            if repair_type not in {
                "causal",
                "continuity",
                "character_consistency",
                "plot_hole",
                "pacing",
            }:
                repair_type = ""
            severity = str(item.get("severity", "medium")).strip().lower()
            if severity not in {"low", "medium", "high", "critical"}:
                severity = "medium"
            targeted_repairs.append(
                {
                    "repair_type": repair_type,
                    "severity": severity,
                    "description": str(item.get("description", "")).strip(),
                    "suggested_action": str(item.get("suggested_action", "")).strip(),
                    "location_hint": str(item.get("location_hint", "")).strip(),
                }
            )

    payload = {
        "decision": decision,
        "risk_level": risk_level,
        "outline_action": outline_action,
        "entity_actions": entity_actions,
        "next_chapter_constraints": constraints,
        "reasoning_brief": reasoning_brief,
        "targeted_repairs": targeted_repairs,
    }
    return PlotGuardDecision.model_validate(payload)


def _normalize_next_chapter_constraints(value: Any) -> list[str]:
    """Return the bounded, prompt-safe subset of an AI-judge handoff."""

    if not isinstance(value, list):
        return []

    constraints: list[str] = []
    seen: set[str] = set()
    for item in value:
        text = " ".join(str(item or "").split())[:300]
        if not text or text in seen:
            continue
        constraints.append(text)
        seen.add(text)
        if len(constraints) >= 5:
            break
    return constraints


def _next_chapter_handoff_payload(
    *,
    chapter_number: int,
    decision: PlotGuardDecision,
    accepted: bool,
    recorded_by: str,
) -> dict[str, Any]:
    """Build the durable, one-chapter guardrail handoff record.

    A judge report is produced before a Desktop user accepts the chapter, so
    ``decision.next_chapter_constraints`` alone is never a safe runtime input.
    This payload makes acceptance explicit and gives the consumer an exact
    target chapter.  The full report remains an audit artifact.
    """

    return {
        "status": "accepted" if accepted else "not_accepted",
        "target_chapter": chapter_number + 1,
        "constraints": (
            _normalize_next_chapter_constraints(decision.next_chapter_constraints)
            if accepted
            else []
        ),
        "recorded_by": recorded_by,
        "recorded_at": datetime.now().isoformat(),
    }


def record_plot_guard_handoff(
    *,
    storage: FileSystemStorage,
    layout: ProjectLayout,
    chapter_number: int,
    decision: PlotGuardDecision,
    accepted: bool,
    recorded_by: str,
) -> None:
    """Record whether a guard decision may constrain the next chapter.

    This is intentionally called only after the current chapter's finalization
    decision.  It prevents a saved-but-rejected guard report from leaking into
    the next chapter's Plan/Draft context.
    """

    path = layout.guard_report_path(chapter_number)
    payload: dict[str, Any] = {}
    try:
        loaded = storage.load_json(path) if storage.exists(path) else {}
        if isinstance(loaded, dict):
            payload = dict(loaded)
    except Exception as exc:
        _logger.warning(
            "plot_guard_handoff_load_failed | chapter=%d | error=%s",
            chapter_number,
            exc,
        )

    payload.setdefault("chapter_number", chapter_number)
    payload.setdefault("decision", decision.model_dump(mode="json"))
    payload["next_chapter_handoff"] = _next_chapter_handoff_payload(
        chapter_number=chapter_number,
        decision=decision,
        accepted=accepted,
        recorded_by=recorded_by,
    )
    storage.save_json(path, payload)


def _strip_legacy_guard_constraint_block(value: str) -> str:
    """Remove old text handoff blocks before writing the current decision."""

    kept: list[str] = []
    in_guard_block = False
    for line in str(value or "").splitlines():
        stripped = line.strip()
        if stripped.startswith("AI护栏约束"):
            in_guard_block = True
            continue
        if in_guard_block:
            if not stripped or stripped.startswith("- "):
                continue
            in_guard_block = False
        kept.append(line)
    return "\n".join(kept).strip()


def _apply_plot_guard_to_creative_report(
    report_data: dict[str, Any],
    decision: PlotGuardDecision,
    *,
    apply_entity_actions: bool = True,
) -> dict[str, int]:
    """Apply accepted AI-judge effects to the creative report payload.

    The legacy textual block is retained only for old projects.  New runtime
    consumers read the explicit ``next_chapter_handoff`` in guard_report.json.
    """
    stats = {
        "dropped_new_characters": 0,
        "dropped_new_locations": 0,
        "dropped_new_items": 0,
        "constraints_appended": 0,
    }

    action_map: dict[tuple[str, str], str] = {}
    for item in decision.entity_actions:
        key = (item.entity_type.lower(), item.name.strip().lower())
        if not key[1]:
            continue
        action_map[key] = item.action.lower()

    def _get_action(entity_type: str, name: str) -> str:
        return action_map.get((entity_type, name.strip().lower()), "keep")

    raw_chars = report_data.get("new_characters", [])
    if apply_entity_actions and isinstance(raw_chars, list):
        kept_chars: list[Any] = []
        for char in raw_chars:
            if not isinstance(char, dict):
                continue
            name = str(char.get("name", "")).strip()
            action = _get_action("character", name)
            if action in {"drop", "defer", "merge"}:
                stats["dropped_new_characters"] += 1
                continue
            kept_chars.append(char)
        report_data["new_characters"] = kept_chars

    raw_locations = report_data.get("new_locations", [])
    if apply_entity_actions and isinstance(raw_locations, list):
        kept_locations: list[str] = []
        for loc in raw_locations:
            name = str(loc).strip()
            action = _get_action("location", name)
            if action in {"drop", "defer", "merge"}:
                stats["dropped_new_locations"] += 1
                continue
            if name:
                kept_locations.append(name)
        report_data["new_locations"] = kept_locations

    raw_items = report_data.get("new_key_items", [])
    if apply_entity_actions and isinstance(raw_items, list):
        kept_items: list[str] = []
        for item in raw_items:
            name = str(item).strip()
            action = _get_action("item", name)
            if action in {"drop", "defer", "merge"}:
                stats["dropped_new_items"] += 1
                continue
            if name:
                kept_items.append(name)
        report_data["new_key_items"] = kept_items

    constraints = _normalize_next_chapter_constraints(decision.next_chapter_constraints)
    existing = _strip_legacy_guard_constraint_block(
        str(report_data.get("suggestions_for_next_chapter", ""))
    )
    if constraints:
        lines = "\n".join(f"- {item}" for item in constraints)
        guard_block = f"AI护栏约束：\n{lines}"
        report_data["suggestions_for_next_chapter"] = (
            f"{existing}\n\n{guard_block}".strip() if existing else guard_block
        )
        stats["constraints_appended"] = len(constraints)
    else:
        report_data["suggestions_for_next_chapter"] = existing

    return stats


async def _run_plot_guard_judge(
    *,
    chapter_number: int,
    report: CreativeReport,
    result: Any,
    layout: ProjectLayout,
    storage: FileSystemStorage,
    router: ModelRouter,
    builder: PromptBuilder,
    settings: Any,
) -> PlotGuardDecision:
    """Invoke AI judge to decide auto-run guardrail actions."""

    milestone_window = _select_plot_guard_milestone_window(
        layout,
        storage,
        chapter_number=chapter_number,
        max_context_chapters=settings.long_ai_judge_max_context_chapters,
    )
    outline_window = _outline_window_from_milestone_window(milestone_window)
    if not outline_window:
        outline_data = storage.load_json(layout.outline_path)
        outline = StoryOutline.model_validate(outline_data)
        outline_window = _select_plot_guard_outline_window(
            outline,
            chapter_number=chapter_number,
            max_context_chapters=settings.long_ai_judge_max_context_chapters,
        )

    canon_stats = {
        "character_count": 0,
        "foreshadowing_count": 0,
        "world_facts_count": 0,
    }
    canon_current_path = layout.canon_dir / "canon_current.json"
    if storage.exists(canon_current_path):
        try:
            canon_data = storage.load_json(canon_current_path)
            canon_stats["character_count"] = len(canon_data.get("characters", {}) or {})
            canon_stats["foreshadowing_count"] = len(canon_data.get("foreshadowing", []) or [])
            canon_stats["world_facts_count"] = len(canon_data.get("world_facts", {}) or {})
        except Exception as exc:
            _logger.debug("Failed to load canon stats: %s", exc)

    request = builder.build(
        TaskType.PLOT_GUARD_JUDGE,
        {
            "plot_guard_mode": settings.long_plot_guard_mode,
            "chapter_number": chapter_number,
            "next_chapter_number": chapter_number + 1,
            "alignment_report": result.alignment_report.model_dump(mode="json")
            if getattr(result, "alignment_report", None)
            else None,
            "continuity_report": result.continuity_report.model_dump(mode="json")
            if getattr(result, "continuity_report", None)
            else None,
            "causal_report": result.causal_report.model_dump(mode="json")
            if getattr(result, "causal_report", None)
            else None,
            "eval_report": result.eval_report.model_dump(mode="json")
            if getattr(result, "eval_report", None)
            else None,
            "plot_deviations": [item.model_dump(mode="json") for item in report.plot_deviations],
            "new_characters": [item.model_dump(mode="json") for item in report.new_characters],
            "new_locations": report.new_locations,
            "new_key_items": report.new_key_items,
            "outline_window": outline_window,
            "milestone_window": milestone_window,
            "canon_stats": canon_stats,
        },
        max_tokens=calculate_route_aware_max_tokens(
            router,
            TaskType.PLOT_GUARD_JUDGE,
            max(900, len(outline_window) * 220),
            prompt_overhead=2200,
            min_tokens=min(1024, int(settings.long_ai_judge_max_tokens or 1536)),
            max_cap=int(settings.long_ai_judge_max_tokens or 1536),
        ),
        temperature=settings.temp_plot_guard_judge,
    )
    response = await router.route(request)
    try:
        parsed = safe_parse_json(response.content)
    except Exception as exc:
        preview = " ".join(str(response.content or "").split())[:500]
        _logger.warning(
            "plot_guard_judge_parse_failed | chapter=%d | error=%s | preview=%s",
            chapter_number,
            exc,
            preview,
        )
        return _normalize_plot_guard_decision(
            {
                "decision": "continue_with_constraints",
                "risk_level": "medium",
                "outline_action": "none",
                "reasoning_brief": (
                    "AI Judge 返回的 JSON 无法解析，已使用保守默认决策继续，"
                    "避免护栏判定解析失败中断章节流程。"
                ),
            }
        )
    return _normalize_plot_guard_decision(parsed)


def _load_chapter_summary_for_adjustment(
    layout: ProjectLayout,
    storage: FileSystemStorage,
    *,
    chapter_number: int,
    report_data: dict[str, Any],
) -> str:
    """Best-effort chapter summary for outline adjustment prompts."""
    canon_current_path = layout.canon_dir / "canon_current.json"
    if storage.exists(canon_current_path):
        try:
            canon_data = storage.load_json(canon_current_path)
            chapter_summaries = canon_data.get("chapter_summaries", {})
            if isinstance(chapter_summaries, dict):
                summary = chapter_summaries.get(str(chapter_number))
                if summary is None:
                    summary = chapter_summaries.get(chapter_number)
                if isinstance(summary, str) and summary.strip():
                    return summary.strip()
        except Exception as exc:
            _logger.debug("Failed to load chapter summary from canon: %s", exc)

    report_summary = report_data.get("chapter_summary", "")
    if isinstance(report_summary, str) and report_summary.strip():
        return report_summary.strip()

    chapter_path = layout.chapter_path(chapter_number)
    if storage.exists(chapter_path):
        try:
            chapter_text = storage.load_text(chapter_path).strip()
            if chapter_text:
                excerpt = chapter_text[:1200]
                return excerpt + ("…" if len(chapter_text) > 1200 else "")
        except Exception as exc:
            _logger.debug("Failed to load chapter text for summary: %s", exc)
    return ""


def _select_adjust_outline_context_chapters(
    outline: StoryOutline,
    *,
    completed_chapter: int,
    max_context_chapters: int,
) -> list[dict[str, Any]]:
    """Select a bounded slice of outline chapters to reduce prompt size."""
    start = max(1, completed_chapter)
    selected = [ch.model_dump() for ch in outline.chapters if ch.chapter_number >= start]
    return selected[: max(1, max_context_chapters)]


async def _apply_outline_adjustment(
    *,
    layout: ProjectLayout,
    storage: FileSystemStorage,
    router: ModelRouter,
    builder: PromptBuilder,
    settings: Any,
    report: CreativeReport,
    report_data: dict[str, Any],
    completed_chapter: int,
    smart: bool,
) -> bool:
    """Apply outline adjustment based on creative report deviations."""
    from novel_forge.core.constants import TaskType

    outline_data = storage.load_json(layout.outline_path)
    outline = StoryOutline.model_validate(outline_data)
    updated = False

    if smart:
        backup_path = (
            layout.root / f"outline_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        )
        shutil.copy(layout.outline_path, backup_path)
        print(f"   原大纲已备份到 {backup_path.name}\n")
        print("   机器人正在调用 LLM 智能调整大纲...")

        summary = _load_chapter_summary_for_adjustment(
            layout,
            storage,
            chapter_number=completed_chapter,
            report_data=report_data,
        )
        original_chapters = _select_adjust_outline_context_chapters(
            outline,
            completed_chapter=completed_chapter,
            max_context_chapters=settings.long_adjust_outline_max_chapters_context,
        )
        request = builder.build(
            TaskType.ADJUST_OUTLINE,
            {
                "total_chapters": outline.total_chapters,
                "synopsis": outline.synopsis,
                "original_chapters": original_chapters,
                "completed_chapter": completed_chapter,
                "actual_plot_summary": summary,
                "plot_deviations": [dev.model_dump() for dev in report.plot_deviations],
                "new_characters": [ch.model_dump() for ch in report.new_characters],
                "new_locations": report.new_locations,
                "new_key_items": report.new_key_items,
                "start_adjust_from": completed_chapter + 1,
            },
            max_tokens=calculate_route_aware_max_tokens(
                router,
                TaskType.ADJUST_OUTLINE,
                max(2400, len(original_chapters) * 350),
                prompt_overhead=3000,
                min_tokens=2048,
            ),
            temperature=settings.temp_adjust_outline,
        )
        response = await router.route(request)
        adjusted_data = safe_parse_json(response.content)
        for adjusted_ch in adjusted_data.get("adjusted_chapters", []):
            ch_num = adjusted_ch.get("chapter_number")
            if not isinstance(ch_num, int):
                continue
            for i, orig_ch in enumerate(outline.chapters):
                if orig_ch.chapter_number == ch_num:
                    outline.chapters[i] = ChapterOutline.model_validate(adjusted_ch)
                    updated = True
                    break
        print("   ✅ LLM 调整完成")
        if "adjustment_summary" in adjusted_data:
            print(f"   {adjusted_data['adjustment_summary']}\n")
    else:
        if completed_chapter < outline.total_chapters:
            next_idx = completed_chapter
            if next_idx < len(outline.chapters):
                suggestion = report.suggestions_for_next_chapter.strip()
                if suggestion:
                    backup_path = (
                        layout.root
                        / f"outline_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
                    )
                    shutil.copy(layout.outline_path, backup_path)
                    print(f"   原大纲已备份到 {backup_path.name}\n")
                    outline.chapters[next_idx].goal = suggestion
                    outline.chapters[
                        next_idx
                    ].notes = f"⚠️ 自动调整于 {datetime.now().strftime('%Y-%m-%d')}（因第{completed_chapter}章偏离）"
                    updated = True
                    print(f"   ✅ 已更新第{completed_chapter + 1}章目标\n")
                else:
                    print("   ⚠️ 当前报告没有下一章建议，快速模式未改动大纲\n")

    if updated:
        storage.save_json(layout.outline_path, outline.model_dump(mode="json"))
    return updated


class PlotGuardRule(Enum):
    """Rules that the plot guard enforces."""

    PLOT_CONTINUITY = "plot_continuity"
    CHARACTER_CONSISTENCY = "character_consistency"
    WORLD_BUILDING = "world_building"
    CANON_ADHERENCE = "canon_adherence"


@dataclass
class PlotGuardReport:
    """Report from plot guard analysis."""

    approved: bool
    violations: List[Dict[str, Union[str, int]]]
    suggestions: List[str]
    decision: PlotGuardDecision


@dataclass(frozen=True)
class _PromptChoice:
    """A user/default choice captured for guardrail auditability."""

    value: str
    defaulted: bool = False


_AI_JUDGE_APPLY_MODE_ALIASES = {
    "assist": "assist",
    "confirm": "assist",
    "manual": "assist",
    "review": "assist",
    "trust": "trust",
    "auto": "trust",
    "accept": "trust",
    "apply": "trust",
}


def _normalize_ai_judge_apply_mode(mode: str) -> str:
    normalized = _AI_JUDGE_APPLY_MODE_ALIASES.get(str(mode or "").strip().lower())
    if normalized:
        return normalized
    _logger.warning("unknown_ai_judge_apply_mode | mode=%s | fallback=assist", mode)
    return "assist"


class PlotGuardService:
    """Service for validating story elements against established plot rules."""

    def __init__(self) -> None:
        self.rules: list[Any] = []

    def validate_plot_element(
        self,
        element: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> PlotGuardReport:
        """Validate a plot element against established rules."""
        return PlotGuardReport(
            approved=True,
            violations=[],
            suggestions=[],
            decision=PlotGuardDecision(decision="continue"),
        )


def _rollback_and_cleanup_chapter(
    *,
    layout: ProjectLayout,
    chapter_number: int,
) -> None:
    """Rollback canon and remove all generated artifacts from this chapter onward."""
    from novel_forge.persistence.project_staleness import regenerate_from_chapter

    storage = FileSystemStorage(layout.root.parent)
    regenerate_from_chapter(storage, layout, from_chapter=chapter_number)


class PlotGuardHandler:
    """Handles Plot Guard (AI Judge) logic for chapter generation."""

    def __init__(
        self,
        *,
        layout: ProjectLayout,
        storage: FileSystemStorage,
        router: ModelRouter,
        builder: PromptBuilder,
        settings: Any,
        ai_judge_apply_mode: str,
        auto_mode: bool,
    ):
        self.layout = layout
        self.storage = storage
        self.router = router
        self.builder = builder
        self.settings = settings
        self.ai_judge_apply_mode = _normalize_ai_judge_apply_mode(ai_judge_apply_mode)
        self.auto_mode = auto_mode

    @staticmethod
    def _ask_choice(
        prompt: Any,
        message: str,
        *,
        choices: list[str],
        default: str,
        console: Any,
    ) -> _PromptChoice:
        """Ask for a prompt choice, falling back to the default in non-interactive runs."""
        try:
            return _PromptChoice(str(prompt.ask(message, choices=choices, default=default)))
        except EOFError:
            console.print(f"[dim]未检测到交互输入，使用默认选项 {default}。[/dim]")
            return _PromptChoice(default, defaulted=True)

    async def handle_major_deviation(
        self,
        *,
        chapter_number: int,
        result: Any,
    ) -> bool:
        """Handle major deviation after chapter generation.

        Returns True to continue generation, False to stop.
        """
        if self.settings.long_plot_guard_mode == "free":
            return True
        if not self.auto_mode and self.settings.long_plot_guard_mode != "ai_judge":
            return True

        report = getattr(result, "creative_report", None)
        if report is None:
            return True

        major_deviations = [
            d for d in report.plot_deviations if str(d.deviation_level).lower() == "major"
        ]

        if self.settings.long_plot_guard_mode == "ai_judge":
            return await self._handle_ai_judge_mode(
                chapter_number=chapter_number,
                report=report,
                result=result,
                major_deviations=major_deviations,
            )

        return await self._handle_manual_mode(
            chapter_number=chapter_number,
            report=report,
            major_deviations=major_deviations,
        )

    async def _handle_ai_judge_mode(
        self,
        *,
        chapter_number: int,
        report: CreativeReport,
        result: Any,
        major_deviations: list[Any],
    ) -> bool:
        """Handle AI judge mode for plot guard."""
        from rich.console import Console

        console = Console()

        should_judge = bool(
            report.plot_deviations
            or report.new_characters
            or report.new_locations
            or report.new_key_items
        )
        if not should_judge:
            return True

        report_data_raw = (
            self.storage.load_json(self.layout.creative_report_path(chapter_number))
            if self.storage.exists(self.layout.creative_report_path(chapter_number))
            else report.model_dump(mode="json")
        )
        report_data = report_data_raw if isinstance(report_data_raw, dict) else {}

        decision = await _run_plot_guard_judge(
            chapter_number=chapter_number,
            report=report,
            result=result,
            layout=self.layout,
            storage=self.storage,
            router=self.router,
            builder=self.builder,
            settings=self.settings,
        )

        console.print(
            f"\n[bold cyan]🤖 AI Judge 决策[/bold cyan] "
            f"[dim](risk={decision.risk_level}, decision={decision.decision})[/dim]"
        )
        if decision.reasoning_brief:
            console.print(f"[dim]{decision.reasoning_brief}[/dim]")

        user_action, should_continue = await self._apply_decision(
            chapter_number=chapter_number,
            report=report,
            decision=decision,
            report_data=report_data,
        )

        self._save_guard_report(
            chapter_number=chapter_number,
            major_deviations=major_deviations,
            decision=decision,
            user_action=user_action,
            source_text=str(getattr(result, "text", "") or ""),
        )

        _append_chapter_audit_log(
            layout=self.layout,
            chapter_number=chapter_number,
            result=result,
            decision=decision,
            report=report,
        )

        return should_continue

    async def _apply_decision(
        self,
        *,
        chapter_number: int,
        report: CreativeReport,
        decision: PlotGuardDecision,
        report_data: dict[str, Any],
    ) -> tuple[str, bool]:
        """Apply AI judge decision based on user mode."""
        from rich.console import Console
        from rich.prompt import Prompt

        console = Console()

        if self.ai_judge_apply_mode == "assist":
            return await self._apply_assist_mode(
                chapter_number=chapter_number,
                report=report,
                decision=decision,
                report_data=report_data,
                console=console,
                prompt=Prompt,
            )
        return await self._apply_trust_mode(
            chapter_number=chapter_number,
            report=report,
            decision=decision,
            report_data=report_data,
        )

    async def _apply_assist_mode(
        self,
        *,
        chapter_number: int,
        report: CreativeReport,
        decision: PlotGuardDecision,
        report_data: dict[str, Any],
        console: Any,
        prompt: Any,
    ) -> tuple[str, bool]:
        """Apply AI judge decision in assist mode (ask user)."""
        console.print("  [1] 应用 AI 判断（推荐）")
        console.print("  [2] 转为人工判断")
        if self.auto_mode:
            console.print("  [3] 忽略 AI 结果，继续自动生成")
        else:
            console.print("  [3] 忽略 AI 结果（本章仅记录）")

        choice_result = self._ask_choice(
            prompt,
            "\n你的选择",
            choices=["1", "2", "3"],
            default="1",
            console=console,
        )
        choice = choice_result.value

        if choice == "1":
            should_continue = await self._apply_ai_judge_decision(
                chapter_number=chapter_number,
                report=report,
                decision=decision,
                report_data=report_data,
            )
            action = "apply_ai_assist_default" if choice_result.defaulted else "apply_ai_assist"
            return action, should_continue

        if choice == "2":
            should_continue = await self._handle_manual_plot_guard(
                chapter_number=chapter_number,
                report=report,
                major_deviations=[
                    d for d in report.plot_deviations if str(d.deviation_level).lower() == "major"
                ],
                strict_mode=False,
            )
            return "fallback_manual", should_continue

        return "skip_ai_result", True

    async def _apply_trust_mode(
        self,
        *,
        chapter_number: int,
        report: CreativeReport,
        decision: PlotGuardDecision,
        report_data: dict[str, Any],
    ) -> tuple[str, bool]:
        """Apply AI judge decision in trust mode (auto-apply)."""
        should_continue = await self._apply_ai_judge_decision(
            chapter_number=chapter_number,
            report=report,
            decision=decision,
            report_data=report_data,
        )
        return "apply_ai_trust", should_continue

    async def _apply_ai_judge_decision(
        self,
        *,
        chapter_number: int,
        report: CreativeReport,
        decision: PlotGuardDecision,
        report_data: dict[str, Any],
    ) -> bool:
        """Apply AI judge decision to creative report and outline."""
        from rich.console import Console

        console = Console()

        adjust_mode = decision.outline_action
        if decision.decision == "adjust_outline_fast":
            adjust_mode = "fast"
        elif decision.decision == "adjust_outline_smart":
            adjust_mode = "smart"

        apply_stats = _apply_plot_guard_to_creative_report(
            report_data,
            decision,
            apply_entity_actions=bool(self.settings.long_ai_judge_apply_entity_actions),
        )
        if any(apply_stats.values()):
            self.storage.save_json(
                self.layout.creative_report_path(chapter_number),
                report_data,
            )
            console.print(
                "[dim]AI Judge 已更新创作报告："
                f"drop角色={apply_stats['dropped_new_characters']} "
                f"drop地点={apply_stats['dropped_new_locations']} "
                f"drop物品={apply_stats['dropped_new_items']} "
                f"约束={apply_stats['constraints_appended']}[/dim]"
            )

        if adjust_mode in {"fast", "smart"}:
            await _apply_outline_adjustment(
                layout=self.layout,
                storage=self.storage,
                router=self.router,
                builder=self.builder,
                settings=self.settings,
                report=report,
                report_data=report_data,
                completed_chapter=chapter_number,
                smart=(adjust_mode == "smart"),
            )

        # 处理定向修复指令
        if decision.targeted_repairs:
            await self._apply_targeted_repairs(
                chapter_number=chapter_number,
                decision=decision,
                report_data=report_data,
                console=console,
            )

        if decision.decision in {"pause_for_human", "rollback_and_regen"}:
            if self.auto_mode:
                console.print(
                    "[yellow]⏸️ AI Judge 要求暂停自动续写，请先人工确认后再继续。[/yellow]"
                )
                return False
            console.print(
                "[yellow]⏸️ AI Judge 标记该章需人工复核（当前为单章模式，未自动续写）。[/yellow]"
            )

        return True

    async def _apply_targeted_repairs(
        self,
        *,
        chapter_number: int,
        decision: PlotGuardDecision,
        report_data: dict[str, Any],
        console: Any,
    ) -> None:
        """应用定向修复指令。

        将 AI Judge 的 targeted_repairs 指令保存到修复队列，
        供后续修复循环使用。
        """

        # 持久化修复指令到文件
        repair_directives_path = (
            self.layout.root / "states" / f"targeted_repairs_ch{chapter_number}.json"
        )
        repair_directives = [
            {
                "repair_type": r.repair_type,
                "severity": r.severity,
                "description": r.description,
                "suggested_action": r.suggested_action,
                "location_hint": r.location_hint,
            }
            for r in decision.targeted_repairs
        ]
        self.storage.save_json(
            repair_directives_path,
            {
                "chapter_number": chapter_number,
                "directives": repair_directives,
                "decision_rationale": decision.reasoning_brief,
            },
        )

        console.print(
            f"[dim]AI Judge 给出 {len(repair_directives)} 条定向修复指令："
            + "；".join(f"{r['repair_type']}({r['severity']})" for r in repair_directives)
            + "[/dim]"
        )

    async def _handle_manual_mode(
        self,
        *,
        chapter_number: int,
        report: CreativeReport,
        major_deviations: list[Any],
    ) -> bool:
        """Handle manual plot guard mode."""
        return await self._handle_manual_plot_guard(
            chapter_number=chapter_number,
            report=report,
            major_deviations=major_deviations,
            strict_mode=(self.settings.long_plot_guard_mode == "strict"),
        )

    async def _handle_manual_plot_guard(
        self,
        *,
        chapter_number: int,
        report: CreativeReport,
        major_deviations: list[Any],
        strict_mode: bool,
    ) -> bool:
        """Handle manual plot guard with user interaction."""
        from rich.console import Console

        console = Console()

        if not major_deviations:
            console.print("[dim]当前无 MAJOR 偏离，未触发人工调纲流程。[/dim]")
            return True

        console.print(
            f"\n[bold red]⚠️ 检测到第 {chapter_number} 章存在 {len(major_deviations)} 条 MAJOR 偏离。[/bold red]"
        )
        for idx, dev in enumerate(major_deviations, 1):
            console.print(f"[red]{idx}. 原计划：[/red]{dev.outline_plan}")
            console.print(f"[red]   实际：[/red]{dev.actual_plot}\n")

        report_path = self.layout.creative_report_path(chapter_number)
        report_data = (
            self.storage.load_json(report_path) if self.storage.exists(report_path) else {}
        )

        if strict_mode:
            return await self._handle_strict_mode(
                chapter_number=chapter_number,
                report=report,
                report_data=report_data,
                console=console,
            )

        return await self._handle_normal_mode(
            chapter_number=chapter_number,
            report=report,
            report_data=report_data,
            console=console,
        )

    async def _handle_strict_mode(
        self,
        *,
        chapter_number: int,
        report: CreativeReport,
        report_data: dict[str, Any],
        console: Any,
    ) -> bool:
        """Handle strict mode (must resolve deviations)."""
        from rich.prompt import Prompt

        console.print("严格模式已启用：必须先处理偏离，再决定是否继续。")
        console.print("  [1] 智能调整后续大纲（推荐）")
        console.print("  [2] 快速调整下一章目标")
        if self.auto_mode:
            console.print("  [3] 停止自动生成，稍后手动处理")
        else:
            console.print("  [3] 本章不调整（结束本次运行）")

        choice = self._ask_choice(
            Prompt,
            "\n你的选择",
            choices=["1", "2", "3"],
            default="1",
            console=console,
        ).value

        if choice in ("1", "2"):
            await _apply_outline_adjustment(
                layout=self.layout,
                storage=self.storage,
                router=self.router,
                builder=self.builder,
                settings=self.settings,
                report=report,
                report_data=report_data,
                completed_chapter=chapter_number,
                smart=(choice == "1"),
            )
            return True

        return not self.auto_mode

    async def _handle_normal_mode(
        self,
        *,
        chapter_number: int,
        report: CreativeReport,
        report_data: dict[str, Any],
        console: Any,
    ) -> bool:
        """Handle normal mode (user has more options)."""
        from rich.prompt import Prompt

        console.print("  [1] 智能调整后续大纲（推荐）")
        console.print("  [2] 快速调整下一章目标")
        if self.auto_mode:
            console.print("  [3] 保持大纲不变，继续自动生成")
            console.print("  [4] 停止自动生成（处理后再继续）")
            choices = ["1", "2", "3", "4"]
        else:
            console.print("  [3] 保持大纲不变（本章仅记录）")
            choices = ["1", "2", "3"]

        choice = self._ask_choice(
            Prompt,
            "\n你的选择",
            choices=choices,
            default="1",
            console=console,
        ).value

        if choice in ("1", "2"):
            await _apply_outline_adjustment(
                layout=self.layout,
                storage=self.storage,
                router=self.router,
                builder=self.builder,
                settings=self.settings,
                report=report,
                report_data=report_data,
                completed_chapter=chapter_number,
                smart=(choice == "1"),
            )
            return True

        if choice == "3":
            return True

        return False

    def _save_guard_report(
        self,
        *,
        chapter_number: int,
        major_deviations: list[Any],
        decision: PlotGuardDecision,
        user_action: str,
        source_text: str = "",
    ) -> None:
        """Save guard report to storage."""
        source_hash = _source_text_hash(source_text) if source_text else ""
        accepted_actions = {
            "apply_ai_assist",
            "apply_ai_assist_default",
            "apply_ai_trust",
        }
        accepted = (
            user_action in accepted_actions
            and decision.decision not in {"pause_for_human", "rollback_and_regen"}
        )
        self.storage.save_json(
            self.layout.guard_report_path(chapter_number),
            {
                "chapter_number": chapter_number,
                "major_deviation_count": len(major_deviations),
                "source_text_hash": source_hash,
                "decision": decision.model_dump(mode="json"),
                "apply_mode": self.ai_judge_apply_mode,
                "user_action": user_action,
                "next_chapter_handoff": _next_chapter_handoff_payload(
                    chapter_number=chapter_number,
                    decision=decision,
                    accepted=accepted,
                    recorded_by="plot_guard_handler",
                ),
            },
        )
