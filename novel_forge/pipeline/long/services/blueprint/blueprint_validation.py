"""Narrative blueprint validation service."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from novel_forge.core.schemas.outline import NarrativeBlueprint
from novel_forge.pipeline.long.services.blueprint.blueprint_chapter_refs import (
    extract_explicit_chapter_refs,
)

_COMPLEXITY_SUBPLOT_MIN = {"simple": 1, "standard": 2, "complex": 3, "epic": 4}
_VALID_SUBPLOT_PRIORITIES = {"primary", "normal", "background"}
_VALID_SOURCE_TYPES = {"main_plot", "subplot", "turning_point"}
_TRIGGER_LINK_TYPES = {"trigger_start", "trigger_turn"}
_STRONG_FEEDBACK_LINK_TYPES = {"feed_main", "reveal_key"}
_VALID_LINK_TYPES = (
    _TRIGGER_LINK_TYPES
    | _STRONG_FEEDBACK_LINK_TYPES
    | {
        "constrain",
        "enable",
        "conflict",
        "create_tension",
        "theme_echo",
    }
)
_VALID_RESOLUTION_TYPES = {"resolve", "reveal", "ascend", "merge"}
_VALID_SUSPENSE_TYPES = {"mystery", "crisis", "emotion", "choice", "desire"}
_VALID_URGENCY_LEVELS = {"critical", "high", "normal", "low"}


@dataclass
class BlueprintValidationResult:
    warnings: list[str] = field(default_factory=list)
    suggestions: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    structure_check_passed: bool = True
    quantity_check_passed: bool = True

    @property
    def is_valid(self) -> bool:
        return not self.errors

    @property
    def has_warnings(self) -> bool:
        return bool(self.warnings or self.suggestions)


def validate_blueprint(
    blueprint: NarrativeBlueprint,
    *,
    total_chapters: int = 0,
    narrative_complexity: str = "standard",
) -> BlueprintValidationResult:
    result = BlueprintValidationResult()
    effective_total = _effective_total_chapters(blueprint, total_chapters)
    _check_structure(blueprint, result, effective_total)
    _check_quantity(blueprint, result, narrative_complexity)
    _check_volumes(blueprint, result, effective_total)
    _check_turning_points(blueprint, result, effective_total)
    _check_character_arcs(blueprint, result, effective_total)
    _check_subplots(blueprint, result, effective_total)
    _check_suspense_schedule(blueprint, result, effective_total)
    _check_textual_chapter_refs(blueprint, result, effective_total)
    return result


def _effective_total_chapters(blueprint: NarrativeBlueprint, total_chapters: int) -> int:
    if total_chapters > 0:
        return total_chapters
    max_chapter = 0
    for phase in blueprint.narrative_phases or []:
        max_chapter = max(max_chapter, int(getattr(phase, "chapter_end", 0) or 0))
    for volume in blueprint.volumes or []:
        max_chapter = max(max_chapter, int(getattr(volume, "end_chapter", 0) or 0))
    for turning_point in blueprint.key_turning_points or []:
        max_chapter = max(
            max_chapter,
            int(getattr(turning_point, "chapter_number", 0) or 0),
        )
    for subplot in blueprint.subplot_plan or []:
        max_chapter = max(
            max_chapter, *(list(getattr(subplot, "involved_chapters", []) or []) or [0])
        )
        max_chapter = max(max_chapter, int(getattr(subplot, "resolution_chapter", 0) or 0))
    for suspense in blueprint.suspense_schedule or []:
        max_chapter = max(
            max_chapter,
            int(getattr(suspense, "introduce_chapter", 0) or 0),
            int(getattr(suspense, "resolve_chapter", 0) or 0),
        )
    return max_chapter


def _check_structure(
    blueprint: NarrativeBlueprint,
    result: BlueprintValidationResult,
    total_chapters: int,
) -> None:
    if not blueprint.synopsis:
        result.errors.append("synopsis 为空，蓝图缺少故事概述")
        result.structure_check_passed = False

    if not blueprint.narrative_phases:
        result.errors.append("narrative_phases 为空，蓝图缺少叙事阶段")
        result.structure_check_passed = False
        return

    phases = sorted(blueprint.narrative_phases, key=lambda p: p.chapter_start)
    if phases:
        if phases[0].chapter_start != 1:
            result.errors.append(f"叙事阶段从第 {phases[0].chapter_start} 章开始，必须覆盖第 1 章")
            result.structure_check_passed = False

        for phase in phases:
            if phase.chapter_end < phase.chapter_start:
                result.errors.append(
                    f"叙事阶段「{phase.phase_name or '?'}」章节区间倒置："
                    f"{phase.chapter_start}-{phase.chapter_end}"
                )
                result.structure_check_passed = False
            if total_chapters and phase.chapter_end > total_chapters:
                result.errors.append(
                    f"叙事阶段「{phase.phase_name or '?'}」结束章节 {phase.chapter_end} "
                    f"超出总章节数 {total_chapters}"
                )
                result.structure_check_passed = False
            if not phase.description:
                result.suggestions.append(
                    f"叙事阶段「{phase.phase_name or '?'}」缺少阶段叙事目标描述"
                )

        for i in range(len(phases) - 1):
            current_end = phases[i].chapter_end
            next_start = phases[i + 1].chapter_start
            if next_start > current_end + 1:
                result.errors.append(
                    f"叙事阶段存在间隙：第 {current_end} 章到第 {next_start} 章之间无覆盖"
                )
                result.structure_check_passed = False
            elif next_start <= current_end:
                result.errors.append(f"叙事阶段重叠：第 {next_start} 章已被前一阶段覆盖")
                result.structure_check_passed = False

        if total_chapters and phases[-1].chapter_end != total_chapters:
            result.errors.append(
                f"叙事阶段只覆盖到第 {phases[-1].chapter_end} 章，"
                f"必须覆盖到总章节数 {total_chapters}"
            )
            result.structure_check_passed = False

    if not blueprint.key_turning_points:
        result.warnings.append("key_turning_points 为空，追读力系统需要转折点数据")


def _check_quantity(
    blueprint: NarrativeBlueprint,
    result: BlueprintValidationResult,
    narrative_complexity: str,
) -> None:
    subplot_count = len(blueprint.subplot_plan or [])
    suspense_count = len(blueprint.suspense_schedule or [])

    min_subplots = _COMPLEXITY_SUBPLOT_MIN.get(narrative_complexity, 2)

    if subplot_count < min_subplots:
        result.errors.append(
            f"支线数量不足：当前 {subplot_count} 条，"
            f"{narrative_complexity} 复杂度建议至少 {min_subplots} 条"
        )
        result.quantity_check_passed = False

    if suspense_count < 1:
        result.errors.append("悬念时间表为空，追读力系统需要悬念数据")
        result.quantity_check_passed = False


def _check_volumes(
    blueprint: NarrativeBlueprint,
    result: BlueprintValidationResult,
    total_chapters: int,
) -> None:
    if not blueprint.volume_mode:
        return
    volumes = sorted(blueprint.volumes or [], key=lambda item: item.start_chapter)
    if not volumes:
        result.errors.append("volume_mode=true 但 volumes 为空")
        result.structure_check_passed = False
        return
    if volumes[0].start_chapter != 1:
        result.errors.append(f"分卷从第 {volumes[0].start_chapter} 章开始，必须覆盖第 1 章")
        result.structure_check_passed = False
    for volume in volumes:
        if volume.end_chapter < volume.start_chapter:
            result.errors.append(
                f"分卷「{volume.title or volume.volume_number}」章节区间倒置："
                f"{volume.start_chapter}-{volume.end_chapter}"
            )
            result.structure_check_passed = False
        if total_chapters and volume.end_chapter > total_chapters:
            result.errors.append(
                f"分卷「{volume.title or volume.volume_number}」结束章节 {volume.end_chapter} "
                f"超出总章节数 {total_chapters}"
            )
            result.structure_check_passed = False
        if not volume.arc_goal:
            result.suggestions.append(
                f"分卷「{volume.title or volume.volume_number}」缺少 arc_goal"
            )
    for idx in range(len(volumes) - 1):
        current_end = volumes[idx].end_chapter
        next_start = volumes[idx + 1].start_chapter
        if next_start != current_end + 1:
            result.errors.append(f"分卷区间不连续：第 {current_end} 章后接第 {next_start} 章")
            result.structure_check_passed = False
    if total_chapters and volumes[-1].end_chapter != total_chapters:
        result.errors.append(
            f"分卷只覆盖到第 {volumes[-1].end_chapter} 章，必须覆盖到 {total_chapters} 章"
        )
        result.structure_check_passed = False


def _check_turning_points(
    blueprint: NarrativeBlueprint,
    result: BlueprintValidationResult,
    total_chapters: int,
) -> None:
    points = sorted(blueprint.key_turning_points or [], key=lambda item: item.chapter_number)
    if total_chapters >= 12 and len(points) < 3:
        result.suggestions.append("关键转折点少于 3 个，长篇结构容易缺少中段升级")
    seen_chapters: set[int] = set()
    for point in points:
        chapter = int(point.chapter_number or 0)
        if chapter in seen_chapters:
            result.warnings.append(f"多个关键转折绑定到第 {chapter} 章，建议确认是否必要")
        seen_chapters.add(chapter)
        if chapter < 1 or (total_chapters and chapter > total_chapters):
            result.errors.append(f"关键转折章节越界：第 {chapter} 章")
            result.structure_check_passed = False
        if not point.description:
            result.suggestions.append(f"第 {chapter} 章关键转折缺少 description")
        if not point.location:
            result.suggestions.append(f"第 {chapter} 章关键转折缺少 location")
        if not point.characters_involved:
            result.suggestions.append(f"第 {chapter} 章关键转折缺少 characters_involved")
    if total_chapters and points and points[-1].chapter_number < max(1, total_chapters - 3):
        result.suggestions.append("最终关键转折距离终章较远，建议确认结尾收束是否有足够主线锚点")


def _check_character_arcs(
    blueprint: NarrativeBlueprint,
    result: BlueprintValidationResult,
    total_chapters: int,
) -> None:
    """Validate arc milestone ranges and their explicit phase anchoring."""
    phases = sorted(blueprint.narrative_phases or [], key=lambda item: item.chapter_start)

    for arc in blueprint.character_arcs or []:
        character = str(arc.character or "").strip()
        label = character or "<未命名角色>"
        if not character:
            result.suggestions.append("存在未命名角色弧光")
        if not arc.arc_summary:
            result.suggestions.append(f"角色弧光「{label}」缺少 arc_summary")
        if not arc.milestones:
            result.suggestions.append(f"角色弧光「{label}」缺少 milestones")
            continue

        if total_chapters >= 24 and len(arc.milestones) < 2:
            result.errors.append(
                f"角色弧光「{label}」milestones 数量不足：当前 {len(arc.milestones)}，"
                "长篇蓝图至少需要前段/中段/后段中的两个变化锚点"
            )
            result.structure_check_passed = False

        previous_start = 0
        for index, milestone in enumerate(arc.milestones, start=1):
            start = int(milestone.chapter_start or 0)
            end = int(milestone.chapter_end or 0)
            milestone_label = f"角色弧光「{label}」第 {index} 个里程碑"

            if start < 1 or end < 1:
                result.errors.append(f"{milestone_label} 章节号必须 >= 1：{start}-{end}")
                result.structure_check_passed = False
                continue
            if end < start:
                result.errors.append(f"{milestone_label} 章节区间倒置：{start}-{end}")
                result.structure_check_passed = False
                continue
            if total_chapters and end > total_chapters:
                result.errors.append(
                    f"{milestone_label} 结束章节 {end} 超出总章节数 {total_chapters}"
                )
                result.structure_check_passed = False
            if previous_start and start < previous_start:
                result.warnings.append(
                    f"{milestone_label} 起始章节早于前一里程碑，建议确认弧光顺序：{start}"
                )
            previous_start = start
            if not milestone.description:
                result.suggestions.append(f"{milestone_label} 缺少 description")
            if (
                total_chapters >= 24
                and len(arc.milestones) == 1
                and start > max(1, int(total_chapters * 0.4))
            ):
                result.errors.append(
                    f"{milestone_label} 从第 {start} 章才开始，缺少前中段角色变化锚点"
                )
                result.structure_check_passed = False

            overlapping = [
                phase
                for phase in phases
                if phase.chapter_end >= start and phase.chapter_start <= end
            ]
            if phases and not overlapping:
                result.warnings.append(f"{milestone_label} 未落入任何叙事阶段：{start}-{end}")
                continue
            if character and overlapping and all(phase.key_characters for phase in overlapping):
                phase_characters: set[str] = set()
                for phase in overlapping:
                    for name in phase.key_characters:
                        phase_characters.update(_split_character_names(name))
                if character not in phase_characters:
                    phase_names = "、".join(
                        phase.phase_name or f"{phase.chapter_start}-{phase.chapter_end}"
                        for phase in overlapping
                    )
                    result.suggestions.append(
                        f"{milestone_label} 落在阶段「{phase_names}」，但该角色未列入阶段核心人物"
                    )


def _split_character_names(value: Any) -> set[str]:
    """Split compact model output like "A、B" into individual character names."""
    text = str(value or "").strip()
    if not text:
        return set()
    parts = {text}
    for separator in ("、", "，", ",", "；", ";", "/", "|", "\n"):
        text = text.replace(separator, "\n")
    parts.update(item.strip() for item in text.splitlines() if item.strip())
    return parts


def _check_subplots(
    blueprint: NarrativeBlueprint,
    result: BlueprintValidationResult,
    total_chapters: int,
) -> None:
    subplot_names = {subplot.name for subplot in blueprint.subplot_plan or [] if subplot.name}
    if not blueprint.subplot_plan:
        return

    for subplot in blueprint.subplot_plan:
        label = subplot.name or "<未命名支线>"
        priority = str(subplot.priority or "normal").strip()
        strict = priority in {"primary", "normal", ""}
        if not subplot.name:
            result.errors.append("存在未命名支线")
            result.structure_check_passed = False
        if priority not in _VALID_SUBPLOT_PRIORITIES:
            result.errors.append(f"支线「{label}」priority 非法：{priority}")
            result.structure_check_passed = False

        involved = sorted(set(subplot.involved_chapters or []))
        if not involved:
            _add_subplot_issue(result, strict, f"支线「{label}」缺少 involved_chapters")
        for chapter in involved:
            if chapter < 1 or (total_chapters and chapter > total_chapters):
                result.errors.append(f"支线「{label}」involved_chapters 越界：第 {chapter} 章")
                result.structure_check_passed = False

        events = list(subplot.chapter_events or [])
        min_events = min(3, max(1, len(involved)))
        if len(events) < min_events:
            _add_subplot_issue(
                result,
                strict,
                f"支线「{label}」chapter_events 数量不足：当前 {len(events)}，建议至少 {min_events}",
            )
        involved_set = set(involved)
        for event in events:
            chapter = int(event.chapter_number or 0)
            if chapter < 1 or (total_chapters and chapter > total_chapters):
                result.errors.append(f"支线「{label}」节点章节越界：第 {chapter} 章")
                result.structure_check_passed = False
            if involved_set and chapter not in involved_set:
                result.errors.append(f"支线「{label}」节点第 {chapter} 章不在 involved_chapters 中")
                result.structure_check_passed = False
            if not event.event:
                result.suggestions.append(f"支线「{label}」第 {chapter} 章节点缺少事件描述")

        has_main_trigger_start = False
        has_strong_feedback = False
        links = list(subplot.weave_links or [])
        if len(links) < 2:
            _add_subplot_issue(result, strict, f"支线「{label}」weave_links 少于 2 条")
        for link in links:
            source_type = str(link.source_type or "").strip()
            link_type = str(link.link_type or "").strip()
            trigger_chapter = int(link.trigger_chapter or 0)
            if source_type not in _VALID_SOURCE_TYPES:
                result.errors.append(f"支线「{label}」weave link source_type 非法：{source_type}")
                result.structure_check_passed = False
            if link_type not in _VALID_LINK_TYPES:
                result.errors.append(f"支线「{label}」weave link link_type 非法：{link_type}")
                result.structure_check_passed = False
            if trigger_chapter < 0 or (total_chapters and trigger_chapter > total_chapters):
                result.errors.append(
                    f"支线「{label}」weave link 触发章节越界：第 {trigger_chapter} 章"
                )
                result.structure_check_passed = False
            if involved_set and trigger_chapter > 0 and trigger_chapter not in involved_set:
                result.warnings.append(
                    f"支线「{label}」weave link trigger_chapter={trigger_chapter} "
                    f"不在 involved_chapters {sorted(involved_set)} 中，"
                    f"请将 {trigger_chapter} 加入 involved_chapters"
                )
            if (
                link.target_subplot
                and link.target_subplot not in subplot_names
                and link.target_subplot != "主线"
            ):
                result.errors.append(
                    f"支线「{label}」weave link 指向不存在的支线「{link.target_subplot}」"
                )
                result.structure_check_passed = False
            if source_type == "main_plot" and link_type == "trigger_start":
                has_main_trigger_start = True
            if link_type in _STRONG_FEEDBACK_LINK_TYPES:
                has_strong_feedback = True

        if not has_main_trigger_start:
            _add_subplot_issue(
                result,
                strict,
                f"支线「{label}」缺少主线触发启动 link（main_plot + trigger_start）",
            )
        if not has_strong_feedback:
            _add_subplot_issue(
                result,
                strict,
                f"支线「{label}」缺少强反哺 link（feed_main 或 reveal_key）",
            )

        resolution_chapter = int(subplot.resolution_chapter or 0)
        if resolution_chapter <= 0:
            _add_subplot_issue(result, strict, f"支线「{label}」缺少 resolution_chapter")
        else:
            if total_chapters and resolution_chapter > total_chapters:
                result.errors.append(
                    f"支线「{label}」resolution_chapter 越界：第 {resolution_chapter} 章"
                )
                result.structure_check_passed = False
            if involved and resolution_chapter < min(involved):
                result.warnings.append(f"支线「{label}」收束章节早于首次 involved_chapter")
        if resolution_chapter > 0:
            if not subplot.resolution_target:
                _add_subplot_issue(result, strict, f"支线「{label}」缺少 resolution_target")
            elif not _valid_resolution_target(subplot.resolution_target):
                result.errors.append(
                    f"支线「{label}」resolution_target 格式非法：{subplot.resolution_target}"
                )
                result.structure_check_passed = False
            elif strict and subplot.resolution_target == "theme_echo":
                result.errors.append(
                    f"支线「{label}」为 {priority} 优先级，resolution_target 不能只写 theme_echo"
                )
                result.structure_check_passed = False
            if not subplot.resolution_type:
                _add_subplot_issue(result, strict, f"支线「{label}」缺少 resolution_type")
            elif subplot.resolution_type not in _VALID_RESOLUTION_TYPES:
                result.errors.append(
                    f"支线「{label}」resolution_type 非法：{subplot.resolution_type}"
                )
                result.structure_check_passed = False


def _add_subplot_issue(
    result: BlueprintValidationResult,
    strict: bool,
    message: str,
) -> None:
    if strict:
        result.errors.append(message)
        result.structure_check_passed = False
    else:
        result.suggestions.append(message)


def _valid_resolution_target(value: str) -> bool:
    return (
        value == "theme_echo"
        or value.startswith("main_turning_point:")
        or value.startswith("character_fate:")
    )


def _check_suspense_schedule(
    blueprint: NarrativeBlueprint,
    result: BlueprintValidationResult,
    total_chapters: int,
) -> None:
    subplot_names = {subplot.name for subplot in blueprint.subplot_plan or [] if subplot.name}
    seen_ids: set[str] = set()
    for item in blueprint.suspense_schedule or []:
        label = item.suspense_id or "<未命名悬念>"
        if not item.suspense_id:
            result.suggestions.append("存在未命名 suspense_schedule 条目")
        elif item.suspense_id in seen_ids:
            result.errors.append(f"悬念 ID 重复：{item.suspense_id}")
            result.structure_check_passed = False
        seen_ids.add(item.suspense_id)

        if item.suspense_type not in _VALID_SUSPENSE_TYPES:
            result.errors.append(f"悬念「{label}」suspense_type 非法：{item.suspense_type}")
            result.structure_check_passed = False
        if item.urgency_level not in _VALID_URGENCY_LEVELS:
            result.warnings.append(f"悬念「{label}」urgency_level 非法：{item.urgency_level}")
        if item.introduce_chapter < 1 or (
            total_chapters and item.introduce_chapter > total_chapters
        ):
            result.errors.append(
                f"悬念「{label}」introduce_chapter 越界：第 {item.introduce_chapter} 章"
            )
            result.structure_check_passed = False
        if item.resolve_chapter:
            if total_chapters and item.resolve_chapter > total_chapters:
                result.errors.append(
                    f"悬念「{label}」resolve_chapter 越界：第 {item.resolve_chapter} 章"
                )
                result.structure_check_passed = False
            if item.resolve_chapter < item.introduce_chapter:
                result.errors.append(f"悬念「{label}」resolve_chapter 早于 introduce_chapter")
                result.structure_check_passed = False
        if not item.related_subplot:
            result.suggestions.append(f"悬念「{label}」缺少 related_subplot")
        elif item.related_subplot != "主线" and item.related_subplot not in subplot_names:
            result.warnings.append(f"悬念「{label}」关联了不存在的支线「{item.related_subplot}」")

        affinity = item.strand_affinity or {}
        missing_keys = {"quest", "fire", "constellation"} - set(affinity)
        if missing_keys:
            result.warnings.append(
                f"悬念「{label}」strand_affinity 缺少：{', '.join(sorted(missing_keys))}"
            )
        total_affinity = sum(float(value or 0.0) for value in affinity.values())
        if total_affinity <= 0:
            result.warnings.append(f"悬念「{label}」strand_affinity 总和无效")
        elif total_affinity < 0.7 or total_affinity > 1.3:
            result.suggestions.append(
                f"悬念「{label}」strand_affinity 总和为 {total_affinity:.2f}，建议接近 1.0"
            )


def _check_textual_chapter_refs(
    blueprint: NarrativeBlueprint,
    result: BlueprintValidationResult,
    total_chapters: int,
) -> None:
    """Ensure explicit chapter references inside ranged structures match their ranges."""
    _check_global_chapter_refs(
        result,
        "全书概述",
        {
            "synopsis": blueprint.synopsis,
            "ending_strategy": blueprint.ending_strategy,
        },
        total_chapters,
    )

    for volume in blueprint.volumes or []:
        label = f"分卷「{volume.title or volume.volume_number}」"
        _check_bounded_chapter_refs(
            result,
            label,
            {
                "arc_goal": volume.arc_goal,
                "milestone_targets": volume.milestone_targets,
                "main_conflicts": volume.main_conflicts,
                "climax_hint": volume.climax_hint,
                "resolution_hint": volume.resolution_hint,
                "notes": volume.notes,
            },
            start=volume.start_chapter,
            end=volume.end_chapter,
            total_chapters=total_chapters,
        )

    for phase in blueprint.narrative_phases or []:
        label = f"叙事阶段「{phase.phase_name or '?'}」"
        _check_bounded_chapter_refs(
            result,
            label,
            {
                "phase_name": phase.phase_name,
                "description": phase.description,
                "key_events": phase.key_events,
                "tension_level": phase.tension_level,
                "time_context": phase.time_context,
            },
            start=phase.chapter_start,
            end=phase.chapter_end,
            total_chapters=total_chapters,
        )

    for arc in blueprint.character_arcs or []:
        character = arc.character or "<未命名角色>"
        for milestone in arc.milestones or []:
            _check_bounded_chapter_refs(
                result,
                f"角色弧光「{character}」里程碑",
                {"description": milestone.description},
                start=milestone.chapter_start,
                end=milestone.chapter_end,
                total_chapters=total_chapters,
            )


def _check_global_chapter_refs(
    result: BlueprintValidationResult,
    label: str,
    value: Any,
    total_chapters: int,
) -> None:
    if not total_chapters:
        return
    for chapter in _chapter_refs(value):
        if chapter < 1 or chapter > total_chapters:
            _add_structure_error(
                result,
                f"{label}文本引用第 {chapter} 章，但总章节数是 {total_chapters}",
            )


def _check_bounded_chapter_refs(
    result: BlueprintValidationResult,
    label: str,
    value: Any,
    *,
    start: int,
    end: int,
    total_chapters: int,
) -> None:
    refs = _chapter_refs(value)
    if not refs:
        return
    for chapter in refs:
        if total_chapters and (chapter < 1 or chapter > total_chapters):
            _add_structure_error(
                result,
                f"{label}文本引用第 {chapter} 章，但总章节数是 {total_chapters}",
            )
            continue
        if chapter < start or chapter > end:
            _add_structure_error(
                result,
                f"{label}文本引用第 {chapter} 章，但所属章节范围是 {start}-{end}",
            )


def _add_structure_error(result: BlueprintValidationResult, message: str) -> None:
    result.errors.append(message)
    result.structure_check_passed = False


def _chapter_refs(value: Any) -> list[int]:
    return extract_explicit_chapter_refs(value)
