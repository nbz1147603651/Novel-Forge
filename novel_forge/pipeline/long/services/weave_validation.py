"""支线交织关系验证服务。

核心原则：环环相扣、最终收敛、服务于主线。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from novel_forge.core.schemas.outline import NarrativeBlueprint

STRONG_FEEDBACK_LINK_TYPES = {"feed_main", "reveal_key"}
FEEDBACK_LINK_TYPES = STRONG_FEEDBACK_LINK_TYPES | {"create_tension", "theme_echo"}
TRIGGER_LINK_TYPES = {"trigger_start", "trigger_turn"}
VALID_RESOLUTION_TYPES = {"resolve", "reveal", "ascend", "merge"}
VALID_RESOLUTION_TARGETS = {"main_turning_point", "theme_echo", "character_fate"}

# Density thresholds
MIN_LINKS_PRIMARY = 3  # primary 支线最少 weave_links
MIN_LINKS_NORMAL = 2   # normal 支线最少 weave_links
MIN_TRIGGERS = 1       # 每条支线至少 1 个主线触发
MIN_FEEDBACK = 1       # 每条支线至少 1 个反哺主线


class WeaveValidationResult:
    """交织关系验证结果。"""

    def __init__(self) -> None:
        self.warnings: list[str] = []
        self.suggestions: list[str] = []
        self.loop_check_passed: bool = False
        self.feedback_check_passed: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "warnings": list(self.warnings),
            "suggestions": list(self.suggestions),
            "loop_check_passed": self.loop_check_passed,
            "feedback_check_passed": self.feedback_check_passed,
            "is_valid": not self.warnings and not self.suggestions,
        }


@dataclass(frozen=True)
class SubplotExecutionRow:
    """Planned execution state for one subplot lane."""

    name: str
    priority: str
    status: str
    planned_chapters: list[int] = field(default_factory=list)
    next_planned_chapter: int = 0
    resolution_chapter: int = 0
    resolution_target: str = ""
    resolution_type: str = ""
    trigger_links: list[dict[str, Any]] = field(default_factory=list)
    feedback_links: list[dict[str, Any]] = field(default_factory=list)
    cross_subplot_links: list[dict[str, Any]] = field(default_factory=list)
    closed_loop: bool = False
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "priority": self.priority,
            "status": self.status,
            "planned_chapters": list(self.planned_chapters),
            "next_planned_chapter": self.next_planned_chapter,
            "resolution_chapter": self.resolution_chapter,
            "resolution_target": self.resolution_target,
            "resolution_type": self.resolution_type,
            "trigger_links": list(self.trigger_links),
            "feedback_links": list(self.feedback_links),
            "cross_subplot_links": list(self.cross_subplot_links),
            "closed_loop": self.closed_loop,
            "notes": list(self.notes),
        }


@dataclass(frozen=True)
class SubplotExecutionMatrix:
    """A compact overview of subplot activation, feedback and closure plans."""

    current_chapter: int
    rows: list[SubplotExecutionRow] = field(default_factory=list)

    @property
    def closed_loop_ratio(self) -> float:
        if not self.rows:
            return 1.0
        closed = sum(1 for row in self.rows if row.closed_loop)
        return round(closed / len(self.rows), 3)

    def to_dict(self) -> dict[str, Any]:
        return {
            "current_chapter": self.current_chapter,
            "closed_loop_ratio": self.closed_loop_ratio,
            "rows": [row.to_dict() for row in self.rows],
        }


def build_subplot_execution_matrix(
    blueprint: NarrativeBlueprint,
    *,
    current_chapter: int = 0,
) -> SubplotExecutionMatrix:
    """Build a planned subplot execution matrix from a narrative blueprint."""

    rows: list[SubplotExecutionRow] = []
    for subplot in blueprint.subplot_plan or []:
        planned_chapters = sorted(set(subplot.involved_chapters or []))
        future_chapters = [ch for ch in planned_chapters if ch >= current_chapter]
        next_planned = future_chapters[0] if current_chapter > 0 and future_chapters else 0
        trigger_links: list[dict[str, Any]] = []
        feedback_links: list[dict[str, Any]] = []
        cross_subplot_links: list[dict[str, Any]] = []
        notes: list[str] = []

        for link in subplot.weave_links or []:
            payload = {
                "source_type": link.source_type,
                "source_ref": link.source_ref,
                "target_subplot": link.target_subplot,
                "trigger_chapter": link.trigger_chapter,
                "link_type": link.link_type,
                "description": link.description,
            }
            if link.source_type == "main_plot" and link.link_type in TRIGGER_LINK_TYPES:
                trigger_links.append(payload)
            if link.link_type in STRONG_FEEDBACK_LINK_TYPES:
                feedback_links.append(payload)
            if link.source_type == "subplot" or (
                link.target_subplot and link.target_subplot not in {"主线", subplot.name}
            ):
                cross_subplot_links.append(payload)

        closed_loop = bool(trigger_links and feedback_links and subplot.resolution_chapter > 0)
        if not trigger_links:
            notes.append("缺少主线触发")
        if not feedback_links:
            notes.append("缺少反哺主线")
        if subplot.resolution_chapter <= 0:
            notes.append("缺少收束章节")

        status = _subplot_status(
            planned_chapters=planned_chapters,
            current_chapter=current_chapter,
            resolution_chapter=subplot.resolution_chapter,
            closed_loop=closed_loop,
        )
        rows.append(
            SubplotExecutionRow(
                name=subplot.name,
                priority=subplot.priority,
                status=status,
                planned_chapters=planned_chapters,
                next_planned_chapter=next_planned,
                resolution_chapter=subplot.resolution_chapter,
                resolution_target=subplot.resolution_target,
                resolution_type=subplot.resolution_type,
                trigger_links=trigger_links,
                feedback_links=feedback_links,
                cross_subplot_links=cross_subplot_links,
                closed_loop=closed_loop,
                notes=notes,
            )
        )
    return SubplotExecutionMatrix(current_chapter=current_chapter, rows=rows)


def _subplot_status(
    *,
    planned_chapters: list[int],
    current_chapter: int,
    resolution_chapter: int,
    closed_loop: bool,
) -> str:
    if current_chapter <= 0:
        return "planned_closed_loop" if closed_loop else "planned_open_loop"
    if not planned_chapters:
        return "unplanned"
    first = planned_chapters[0]
    last = planned_chapters[-1]
    if current_chapter < first:
        return "not_started"
    if current_chapter in planned_chapters:
        return "active"
    if resolution_chapter and current_chapter >= resolution_chapter:
        return "resolved" if closed_loop else "needs_closure"
    if current_chapter <= last:
        return "dormant"
    return "overdue"


def validate_subplot_weave(blueprint: NarrativeBlueprint) -> WeaveValidationResult:
    """验证支线交织关系的合理性。

    检查项：
    1. weave_links 中的 target_subplot 是否存在于 subplot_plan（或为"主线"）
    2. weave_links 中的 trigger_chapter 是否在有效范围内
    3. 是否存在支线间循环依赖（A→B→A）
    4. 支线是否有明确的收束章节、目标和类型
    5. priority=primary 的支线是否交织关系过少
    6. 每条支线是否有"主线触发启动"的关系（至少一个 trigger_start）
    7. 每条支线是否有"回馈主线"的关系（至少一个 feed_main/reveal_key）
    8. 支线是否形成完整闭环（触发 + 反哺）
    """
    result = WeaveValidationResult()
    subplot_names = {sp.name for sp in blueprint.subplot_plan or []}
    total_chapters = _infer_total_chapters(blueprint)

    all_loops_closed = True
    all_have_feedback = True

    for subplot in blueprint.subplot_plan or []:
        has_trigger_start = False
        has_feedback = False

        for link in subplot.weave_links or []:
            if link.target_subplot and link.target_subplot not in subplot_names:
                if link.target_subplot != "主线":
                    result.warnings.append(
                        f"支线「{subplot.name}」的交织关系指向不存在的支线「{link.target_subplot}」"
                    )
            if link.trigger_chapter > total_chapters:
                result.warnings.append(
                    f"支线「{subplot.name}」的交织触发章节{link.trigger_chapter}超出总章节数{total_chapters}"
                )

            if link.link_type in TRIGGER_LINK_TYPES and link.source_type == "main_plot":
                has_trigger_start = True
            if link.link_type in STRONG_FEEDBACK_LINK_TYPES:
                has_feedback = True

        if not has_trigger_start:
            result.suggestions.append(
                f"支线「{subplot.name}」缺少「主线触发启动」的交织关系，"
                f"建议添加 source_type='main_plot' + link_type='trigger_start'"
            )
            all_loops_closed = False

        if not has_feedback:
            result.suggestions.append(
                f"支线「{subplot.name}」缺少「回馈主线」的交织关系，"
                f"建议添加 link_type='feed_main'/'reveal_key'"
            )
            all_have_feedback = False

        if subplot.resolution_chapter == 0 and subplot.involved_chapters:
            result.suggestions.append(
                f"支线「{subplot.name}」未规划收束章节，建议在 involved_chapters 的最后一章或之后明确收束"
            )

        if subplot.resolution_chapter > 0:
            if not subplot.resolution_target:
                result.suggestions.append(
                    f"支线「{subplot.name}」有收束章节但缺少收束目标（resolution_target），"
                    f"建议填写 'main_turning_point:N' / 'theme_echo' / 'character_fate:角色名'"
                )
            elif not _is_valid_resolution_target(subplot.resolution_target):
                result.warnings.append(
                    f"支线「{subplot.name}」的收束目标「{subplot.resolution_target}」格式无效，"
                    f"应为 'main_turning_point:N' / 'theme_echo' / 'character_fate:角色名'"
                )

            if not subplot.resolution_type:
                result.suggestions.append(
                    f"支线「{subplot.name}」有收束章节但缺少收束类型（resolution_type），"
                    f"建议填写 'resolve'/'reveal'/'ascend'/'merge'"
                )
            elif subplot.resolution_type not in VALID_RESOLUTION_TYPES:
                result.warnings.append(
                    f"支线「{subplot.name}」的收束类型「{subplot.resolution_type}」无效，"
                    f"应为 'resolve'/'reveal'/'ascend'/'merge'"
                )

    for subplot in blueprint.subplot_plan or []:
        _check_subplot_density(subplot, result)

    _check_collision_alignment(blueprint, result)
    _check_circular_dependencies(blueprint, result)

    result.loop_check_passed = all_loops_closed
    result.feedback_check_passed = all_have_feedback

    return result


def _infer_total_chapters(blueprint: NarrativeBlueprint) -> int:
    """从蓝图中推断总章节数。"""
    max_ch = 0
    for phase in blueprint.narrative_phases or []:
        max_ch = max(max_ch, phase.chapter_end)
    for tp in blueprint.key_turning_points or []:
        max_ch = max(max_ch, tp.chapter_number)
    for sp in blueprint.subplot_plan or []:
        if sp.involved_chapters:
            max_ch = max(max_ch, max(sp.involved_chapters))
    return max_ch or 24


def _is_valid_resolution_target(target: str) -> bool:
    """检查收束目标格式是否有效。"""
    if target in {"theme_echo"}:
        return True
    if target.startswith("main_turning_point:"):
        return True
    if target.startswith("character_fate:"):
        return True
    return False


def _check_circular_dependencies(
    blueprint: NarrativeBlueprint, result: WeaveValidationResult
) -> None:
    """检查支线间的循环依赖。"""
    links_by_pair: dict[tuple[str, str], list[str]] = {}
    for subplot in blueprint.subplot_plan or []:
        for link in subplot.weave_links or []:
            if link.source_type == "subplot" and link.target_subplot:
                pair = (link.source_ref, link.target_subplot)
                links_by_pair.setdefault(pair, []).append(link.link_type)

    for (src, tgt), _ in links_by_pair.items():
        reverse_pair = (tgt, src)
        if reverse_pair in links_by_pair:
            result.warnings.append(
                f"检测到循环交织：「{src}」\u2194「{tgt}」，请确认是否为有意设计"
            )


def _check_subplot_density(
    subplot: Any,
    result: WeaveValidationResult,
) -> None:
    priority = getattr(subplot, "priority", "normal") or "normal"
    min_links = MIN_LINKS_PRIMARY if priority == "primary" else MIN_LINKS_NORMAL
    weave_links = getattr(subplot, "weave_links", []) or []
    link_count = len(weave_links)

    if link_count < min_links:
        name = getattr(subplot, "name", "未知")
        result.warnings.append(
            f"支线「{name}」({priority}) 仅有 {link_count} 个交织关系，建议至少 {min_links} 个以保证剧情密度"
        )

    trigger_count = sum(
        1 for link in weave_links
        if getattr(link, "link_type", "") in TRIGGER_LINK_TYPES
        and getattr(link, "source_type", "") == "main_plot"
    )
    feedback_count = sum(
        1 for link in weave_links
        if getattr(link, "link_type", "") in STRONG_FEEDBACK_LINK_TYPES
    )

    name = getattr(subplot, "name", "未知")
    if trigger_count < MIN_TRIGGERS:
        result.warnings.append(
            f"支线「{name}」缺少主线触发 (trigger_start/turn)，建议至少 {MIN_TRIGGERS} 个"
        )
    if feedback_count < MIN_FEEDBACK:
        result.suggestions.append(
            f"支线「{name}」缺少强反哺 (feed_main/reveal_key)，建议至少 {MIN_FEEDBACK} 个以形成闭环"
        )


def _check_collision_alignment(
    blueprint: NarrativeBlueprint,
    result: WeaveValidationResult,
) -> None:
    involved_map: dict[str, set[int]] = {}
    for sp in blueprint.subplot_plan or []:
        name = getattr(sp, "name", "")
        if name:
            involved_map[name] = set(getattr(sp, "involved_chapters", []) or [])

    for collision in getattr(blueprint, "subplot_collisions", []) or []:
        ch = getattr(collision, "collision_chapter", 0)
        if ch <= 0:
            continue
        for sp_name in getattr(collision, "involved_subplots", []) or []:
            chapters = involved_map.get(sp_name)
            if chapters is None:
                result.warnings.append(
                    f"碰撞点引用不存在的支线「{sp_name}」"
                )
            elif ch not in chapters:
                result.warnings.append(
                    f"碰撞点章节 {ch} 不在支线「{sp_name}」的 involved_chapters 范围内"
                )
