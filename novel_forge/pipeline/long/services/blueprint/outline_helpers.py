"""Outline helpers — normalisation, batching, phase guidance, volume mode."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Any

from novel_forge.core.constants import PipelineConstants
from novel_forge.core.schemas.bible import CharacterBible
from novel_forge.core.schemas.outline import (
    ChapterOutline,
    CharacterArcPlan,
    NarrativeBlueprint,
    NarrativePhase,
    StoryOutline,
    VolumeOutline,
    normalize_outline_beats,
)

if TYPE_CHECKING:
    pass


# ---------------------------------------------------------------------------
# Volume coverage & defaults
# ---------------------------------------------------------------------------


def is_valid_volume_coverage(volumes: list[VolumeOutline], total_chapters: int) -> bool:
    if not volumes:
        return False
    sorted_volumes = sorted(volumes, key=lambda v: v.volume_number)
    current_start = 1
    for vol in sorted_volumes:
        if vol.start_chapter != current_start:
            return False
        if vol.end_chapter < vol.start_chapter:
            return False
        current_start = vol.end_chapter + 1
    return sorted_volumes[-1].end_chapter == total_chapters


def build_default_volumes(
    *,
    total_chapters: int,
    chapters_per_volume: int,
    narrative_phases: list[NarrativePhase] | None = None,
) -> list[VolumeOutline]:
    size = max(1, chapters_per_volume)
    volume_count = max(1, math.ceil(total_chapters / size))
    phases = narrative_phases or []

    volumes: list[VolumeOutline] = []
    for i in range(volume_count):
        start_chapter = i * size + 1
        end_chapter = min((i + 1) * size, total_chapters)

        # Extract phase-derived content for this volume's chapter range
        volume_phases = [
            p for p in phases
            if p.chapter_start <= end_chapter and p.chapter_end >= start_chapter
        ]

        if volume_phases:
            arc_goal_parts = [p.description for p in volume_phases if p.description]
            arc_goal = "；".join(arc_goal_parts[:2]) if arc_goal_parts else f"推进主线第{i + 1}阶段并形成阶段性收束。"
            milestone_targets = [
                e for p in volume_phases
                for e in (p.key_events or [])
            ][:4]
            main_conflicts = [
                f"{p.phase_name}阶段的核心矛盾" for p in volume_phases if p.phase_name
            ][:3]
            climax_hint = volume_phases[-1].description if volume_phases else ""
            resolution_hint = "收束本卷主线，为下一卷预留发力点。"
        else:
            arc_goal = f"推进主线第{i + 1}阶段并形成阶段性收束。"
            milestone_targets = [
                "推进主线核心冲突并完成阶段升级。",
                "在卷末形成明确的新悬念与下一卷入口。",
            ]
            main_conflicts = []
            climax_hint = ""
            resolution_hint = ""

        volumes.append(
            VolumeOutline(
                volume_number=i + 1,
                title=f"第{i + 1}卷",
                start_chapter=start_chapter,
                end_chapter=end_chapter,
                arc_goal=arc_goal,
                milestone_targets=milestone_targets,
                main_conflicts=main_conflicts,
                climax_hint=climax_hint,
                resolution_hint=resolution_hint,
                notes="自动分卷生成（基于叙事阶段），可后续手工优化。" if volume_phases else "自动分卷生成，可后续手工优化。",
            )
        )
    return volumes


def normalize_outline_volumes(
    outline: StoryOutline,
    *,
    total_chapters: int,
    use_volume_mode: bool,
    chapters_per_volume: int,
) -> StoryOutline:
    if not use_volume_mode:
        outline.volume_mode = False
        outline.volumes = []
        return outline

    outline.volume_mode = True
    if not is_valid_volume_coverage(outline.volumes, total_chapters):
        outline.volumes = build_default_volumes(
            total_chapters=total_chapters,
            chapters_per_volume=chapters_per_volume,
            narrative_phases=outline.narrative_phases if hasattr(outline, "narrative_phases") else None,
        )
    return outline


# ---------------------------------------------------------------------------
# Outline completeness & normalisation
# ---------------------------------------------------------------------------


def is_outline_complete(outline: StoryOutline) -> bool:
    """Return True if every chapter has real (non-placeholder) content."""
    existing_numbers = {ch.chapter_number for ch in outline.chapters}
    target = int(outline.planned_through_chapter or outline.total_chapters)
    for ch in outline.chapters:
        if ch.chapter_number > target:
            continue
        if ch.notes == PipelineConstants.PLACEHOLDER_NOTE or not ch.beats_summary:
            return False
    for i in range(1, target + 1):
        if i not in existing_numbers:
            return False
    return True


def normalize_chapter_outline_beats(chapter: ChapterOutline) -> ChapterOutline:
    """Return a chapter with normalized ``beats_summary`` internals."""
    normalized_beats = normalize_outline_beats(chapter.beats_summary)
    if normalized_beats == chapter.beats_summary:
        return chapter
    return chapter.model_copy(update={"beats_summary": normalized_beats})


def normalize_outline_chapters(
    outline: StoryOutline,
    *,
    total_chapters: int,
    words_per_chapter: int,
) -> StoryOutline:
    """Ensure outline chapters are contiguous and match requested chapter count."""
    normalized: list[ChapterOutline] = []
    by_number = {
        chapter.chapter_number: chapter
        for chapter in outline.chapters
        if chapter.chapter_number >= 1
    }
    default_words = max(500, words_per_chapter)

    for chapter_num in range(1, total_chapters + 1):
        existing = by_number.get(chapter_num)
        if existing is not None:
            updates: dict[str, Any] = {}
            if existing.expected_word_count >= 500:
                chapter = existing
            else:
                updates["expected_word_count"] = default_words
                chapter = existing.model_copy(update=updates)
            normalized.append(normalize_chapter_outline_beats(chapter))
            continue

        normalized.append(
            ChapterOutline.model_validate(
                {
                    "chapter_number": chapter_num,
                    "title": PipelineConstants.DEFAULT_CHAPTER_TITLE_TEMPLATE.format(
                        chapter_num=chapter_num
                    ),
                    "goal": PipelineConstants.DEFAULT_CHAPTER_GOAL,
                    "expected_word_count": default_words,
                    "notes": PipelineConstants.PLACEHOLDER_NOTE,
                }
            )
        )

    outline.total_chapters = total_chapters
    outline.chapters = normalized
    return outline


# ---------------------------------------------------------------------------
# Backfill involved_characters from character_bible + textual references
# ---------------------------------------------------------------------------


def backfill_involved_characters(
    outline: StoryOutline,
    character_bible: CharacterBible | None = None,
) -> StoryOutline:
    """Ensure every chapter has a populated ``involved_characters`` list.

    When the LLM-generated outline already provides the field, it is preserved.
    Otherwise, we extract character names by scanning each chapter's textual
    fields (goal, beats_summary, main_plot_points, notes) against the known
    character names from the bible.  The POV character is always included.

    This runs *after* normalize_outline_chapters so it can also fill in
    placeholders.
    """
    known_names: list[str] = []
    if character_bible is not None:
        known_names = [c.name for c in character_bible.characters if c.name]

    for chapter in outline.chapters:
        if chapter.involved_characters:
            # Already populated (e.g. from LLM output) — just ensure POV is present
            if chapter.pov_character and chapter.pov_character not in chapter.involved_characters:
                chapter.involved_characters.insert(0, chapter.pov_character)
            continue

        # Build from textual references
        mentioned: list[str] = []
        if chapter.pov_character:
            mentioned.append(chapter.pov_character)

        # Combine all textual fields for scanning
        text_pool = " ".join(
            filter(
                None,
                [
                    chapter.goal,
                    " ".join(chapter.beats_summary),
                    " ".join(chapter.main_plot_points),
                    " ".join(chapter.subplot_points),
                    chapter.notes,
                ],
            )
        )

        for name in known_names:
            if name and name in text_pool and name not in mentioned:
                mentioned.append(name)

        chapter.involved_characters = mentioned

    return outline


# ---------------------------------------------------------------------------
# Batch building
# ---------------------------------------------------------------------------


def build_outline_batches(remaining: list[int], batch_size: int) -> list[tuple[int, int]]:
    """Build batches from remaining chapter numbers.

    Splits numbers into contiguous runs, then chunks each run by *batch_size*.
    """
    if not remaining:
        return []

    batches: list[tuple[int, int]] = []
    idx = 0
    size = max(1, batch_size)

    while idx < len(remaining):
        run_start = remaining[idx]
        run_end = run_start
        while idx + 1 < len(remaining) and remaining[idx + 1] == run_end + 1:
            idx += 1
            run_end = remaining[idx]

        chunk_start = run_start
        while chunk_start <= run_end:
            chunk_end = min(chunk_start + size - 1, run_end)
            batches.append((chunk_start, chunk_end))
            chunk_start = chunk_end + 1

        idx += 1

    return batches


# ---------------------------------------------------------------------------
# CSV parsing & target allow-list helpers
# ---------------------------------------------------------------------------


def parse_csv_items(value: str) -> set[str]:
    """Parse comma-separated config values into lowercase tokens."""
    if not value.strip():
        return set()
    return {item.strip().lower() for item in value.split(",") if item.strip()}


def is_target_allowed(
    provider: str,
    model_id: str,
    *,
    allowed_providers: set[str],
    allowed_models: set[str],
) -> bool:
    provider_norm = provider.strip().lower()
    model_norm = model_id.strip().lower()
    if allowed_providers and provider_norm not in allowed_providers:
        return False
    if allowed_models and model_norm not in allowed_models:
        return False
    return True


# ---------------------------------------------------------------------------
# Previous-chapters summary for outline continuation
# ---------------------------------------------------------------------------


def summarize_previous_chapters_state(
    accumulated_chapters: list[ChapterOutline],
    batch_start: int,
) -> str:
    """Summarize what was actually written in prior chapters to guide continuation."""
    if not accumulated_chapters:
        return ""

    lines: list[str] = []
    lines.append("## 已生成章节状态摘要\n")

    sorted_chs = sorted(accumulated_chapters, key=lambda c: c.chapter_number, reverse=True)
    recent_chs = sorted(sorted_chs[: min(3, len(sorted_chs))], key=lambda c: c.chapter_number)

    if recent_chs:
        last_ch = recent_chs[-1]
        lines.append(
            f"* 最后一章（第{last_ch.chapter_number}章）「{last_ch.title}」：{last_ch.goal}"
        )
        if last_ch.main_plot_points:
            lines.append(f"  - 主线推进：{' / '.join(last_ch.main_plot_points[:2])}")
        if last_ch.subplot_points:
            lines.append(f"  - 支线推进：{' / '.join(last_ch.subplot_points[:2])}")
        if last_ch.pov_character:
            lines.append(f"  - 视角角色：{last_ch.pov_character}")

        accumulated_pov_chars = sorted(
            set(ch.pov_character for ch in accumulated_chapters if ch.pov_character),
        )
        if accumulated_pov_chars:
            lines.append(f"* 已经登场的主要视角：{', '.join(accumulated_pov_chars)}")

        all_main_points: set[str] = set()
        for ch in accumulated_chapters:
            all_main_points.update(ch.main_plot_points or [])
        if all_main_points:
            sample_points = sorted(list(all_main_points))[:3]
            lines.append(f"* 已推进的主线精髓：{' / '.join(sample_points)[:100]}…")

    lines.append(
        f"\n* 本批衔接要求：第{batch_start}章正是上一批的续程，会承接最后一批的上述情节、人物状态、伏笔悬念。"
    )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Scoped blueprint extraction for first batch
# ---------------------------------------------------------------------------


def extract_scoped_blueprint(
    blueprint: NarrativeBlueprint,
    batch_start: int,
    batch_end: int,
    total_chapters: int,
) -> NarrativeBlueprint:
    """Extract a scoped version of the blueprint relevant to the first batch.

    This reduces token consumption by only including blueprint elements
    that are directly relevant to the first batch chapters.
    """
    from copy import deepcopy

    scoped = deepcopy(blueprint)

    # Keep synopsis (always needed for context)
    # scoped.synopsis = blueprint.synopsis  # Already copied

    # Filter volumes to those that overlap with the batch
    scoped.volumes = [
        vol
        for vol in (blueprint.volumes or [])
        if vol.end_chapter >= batch_start and vol.start_chapter <= batch_end
    ]

    # Filter narrative phases to those that overlap with the batch
    # Plus the first phase AFTER the batch (for continuity)
    scoped.narrative_phases = [
        phase
        for phase in (blueprint.narrative_phases or [])
        if phase.chapter_end >= batch_start and phase.chapter_start <= batch_end
    ]
    # Add next phase if exists (for continuity reference)
    all_phases = sorted((blueprint.narrative_phases or []), key=lambda p: p.chapter_start)
    for phase in all_phases:
        if phase.chapter_start > batch_end:
            scoped.narrative_phases.append(phase)
            break

    # Filter key turning points to those in or near the batch
    # Include points within 2 chapters after the batch
    scoped.key_turning_points = [
        tp
        for tp in (blueprint.key_turning_points or [])
        if tp.chapter_number >= batch_start and tp.chapter_number <= batch_end + 2
    ]

    # Filter character arcs to those with milestones in or near the batch
    # Include arcs that start within 5 chapters after the batch
    filtered_arcs = []
    for arc in blueprint.character_arcs or []:
        relevant_milestones = [
            m
            for m in (arc.milestones or [])
            if m.chapter_end >= batch_start and m.chapter_start <= batch_end + 5
        ]
        if relevant_milestones:
            filtered_arcs.append(
                CharacterArcPlan(
                    character=arc.character,
                    arc_summary=arc.arc_summary,
                    milestones=relevant_milestones,
                )
            )
    scoped.character_arcs = filtered_arcs

    # Filter subplots to those involving chapters in or near the batch
    scoped.subplot_plan = [
        sp
        for sp in (blueprint.subplot_plan or [])
        if any(batch_start - 3 <= ch <= batch_end + 3 for ch in sp.involved_chapters)
    ]

    # Keep ending strategy (always needed for continuity)
    # scoped.ending_strategy = blueprint.ending_strategy  # Already copied

    return scoped


# ---------------------------------------------------------------------------
# Subplot weave hints extraction
# ---------------------------------------------------------------------------


def extract_subplot_weave_hints(
    blueprint: NarrativeBlueprint,
    chapter_number: int,
) -> tuple[list[str], list[str]]:
    """提取指定章节的支线交织提示和依赖警告。

    Returns:
        (weave_hints, dependency_warnings)
    """
    weave_hints: list[str] = []
    dep_warnings: list[str] = []

    subplot_plan = blueprint.subplot_plan or []

    for subplot in subplot_plan:
        # 1. 检查 weave_links 中 trigger_chapter == chapter_number 的关系
        for link in subplot.weave_links or []:
            if link.trigger_chapter == chapter_number:
                weave_hints.append(
                    f"{link.source_type}「{link.source_ref}」将在本章{link.link_type}支线「{link.target_subplot}」：{link.description}"
                )

        # 2. 检查 chapter_events 中本章节点的 weave_notes
        for event in subplot.chapter_events or []:
            if event.chapter_number == chapter_number and event.weave_notes:
                weave_hints.append(
                    f"支线「{subplot.name}」第{chapter_number}章节点交织：{event.weave_notes}"
                )

            # 3. 检查 depends_on 是否满足
            for dep in event.depends_on or []:
                parts = dep.split(":")
                if len(parts) == 2:
                    dep_subplot_name, dep_chapter_str = parts
                    try:
                        dep_chapter = int(dep_chapter_str)
                    except ValueError:
                        continue

                    # 检查依赖的支线节点是否已在之前章节出现
                    dep_subplot = next(
                        (sp for sp in subplot_plan if sp.name == dep_subplot_name),
                        None,
                    )
                    if dep_subplot:
                        dep_event_exists = any(
                            e.chapter_number == dep_chapter
                            for e in dep_subplot.chapter_events or []
                        )
                        if not dep_event_exists:
                            dep_warnings.append(
                                f"支线「{subplot.name}」第{chapter_number}章依赖「{dep_subplot_name}:第{dep_chapter}章」，"
                                f"但该节点在「{dep_subplot_name}」中不存在"
                            )
                        elif dep_chapter >= chapter_number:
                            dep_warnings.append(
                                f"支线「{subplot.name}」第{chapter_number}章依赖「{dep_subplot_name}:第{dep_chapter}章」，"
                                f"但该节点尚未发生（未来章节）"
                            )

    return weave_hints, dep_warnings


def extract_subplot_convergence_check(
    blueprint: NarrativeBlueprint,
    chapter_number: int,
) -> list[str]:
    """检查指定章节是否有需要收敛的支线，生成收束提示。

    核心原则：环环相扣、最终收敛、服务于主线。
    """
    convergence_hints: list[str] = []

    for subplot in blueprint.subplot_plan or []:
        if subplot.resolution_chapter == chapter_number:
            target_desc = subplot.resolution_target or "未指定"
            type_desc = subplot.resolution_type or "未指定"

            convergence_hints.append(
                f"支线「{subplot.name}」在本章收束 → 目标：「{target_desc}」 / 方式：「{type_desc}」"
            )

            if subplot.resolution_target.startswith("main_turning_point:"):
                try:
                    tp_num = int(subplot.resolution_target.split(":")[1])
                    turning_points = sorted(
                        blueprint.key_turning_points or [],
                        key=lambda item: item.chapter_number,
                    )
                    target_turning_point = next(
                        (tp for tp in turning_points if tp.chapter_number == tp_num),
                        None,
                    )
                    if target_turning_point is None and 1 <= tp_num <= len(turning_points):
                        target_turning_point = turning_points[tp_num - 1]
                    if target_turning_point is not None:
                        convergence_hints.append(
                            f"  → 该支线将推动主线转折「{target_turning_point.description}」"
                        )
                except (IndexError, ValueError):
                    pass

            elif subplot.resolution_target == "theme_echo":
                convergence_hints.append("  → 该支线将呼应主题，升华故事内核")

            elif subplot.resolution_target.startswith("character_fate:"):
                char_name = (
                    subplot.resolution_target.split(":")[1]
                    if ":" in subplot.resolution_target
                    else ""
                )
                if char_name:
                    convergence_hints.append(f"  → 该支线将决定角色「{char_name}」的命运走向")

    return convergence_hints


# ---------------------------------------------------------------------------
# Phase guidance extraction from blueprint
# ---------------------------------------------------------------------------


def extract_phase_guidance(
    blueprint: NarrativeBlueprint,
    batch_start: int,
    batch_end: int,
    total_chapters: int,
    accumulated_chapters: list[ChapterOutline] | None = None,
) -> str:
    """Extract structured guidance for a chapter batch from the blueprint."""

    def _phase_role(phase_start: int, phase_end: int) -> str:
        if phase_start >= batch_start and phase_end <= batch_end:
            return "本批完整覆盖该阶段，需完成其阶段任务。"
        if phase_start >= batch_start:
            return "本批进入该阶段，重点完成开局铺垫并建立后续推进惯性。"
        if phase_end <= batch_end:
            return "本批处于该阶段尾段，需完成收口并推动转段。"
        return "本批位于该阶段中段，重点持续加压并推进既定目标。"

    def _milestone_role(ms_start: int, ms_end: int) -> str:
        if ms_start >= batch_start and ms_end <= batch_end:
            return "本批必须落地该里程碑。"
        if ms_start >= batch_start:
            return "本批应启动这条弧光并留下后续增长空间。"
        if ms_end <= batch_end:
            return "本批应让这条弧光在本段落点。"
        return "本批应持续推进这条弧光，不可停滞。"

    lines: list[str] = [
        f"* 本批定位：第{batch_start}–{batch_end}章，共{batch_end - batch_start + 1}章。",
    ]

    # Add chapter reference summaries for continuity
    if accumulated_chapters:
        prev_chapters = [ch for ch in accumulated_chapters if ch.chapter_number < batch_start][
            -5:
        ]  # Last 5 chapters before this batch
        if prev_chapters:
            lines.append("* 前期章节参考：")
            for ch in prev_chapters:
                ref_parts = [f"第{ch.chapter_number}章「{ch.title or '未命名'}」"]
                if ch.main_plot_points:
                    ref_parts.append(f"主线：{ch.main_plot_points[0][:40]}...")
                if ch.beats_summary and len(ch.beats_summary) > 0:
                    ref_parts.append(f"关键节拍：{ch.beats_summary[0][:30]}...")
                lines.append(f"  - {' | '.join(ref_parts)}")
            lines.append("  ⚠️ 本批章节必须紧密承接上述章节的情节走向和人物状态。")

    overlapping_volumes = [
        volume
        for volume in blueprint.volumes
        if volume.end_chapter >= batch_start and volume.start_chapter <= batch_end
    ]
    if overlapping_volumes:
        lines.append("* 当前分卷任务：")
        for volume in overlapping_volumes:
            lines.append(
                f"  - 卷{volume.volume_number}「{volume.title or '未命名分卷'}」"
                f"（第{volume.start_chapter}–{volume.end_chapter}章）：{volume.arc_goal}"
            )
            if volume.main_conflicts:
                lines.append(f"    主冲突：{'；'.join(volume.main_conflicts)}")
            if volume.milestone_targets:
                lines.append(f"    卷里程碑：{'；'.join(volume.milestone_targets)}")

    overlapping_phases = [
        phase
        for phase in blueprint.narrative_phases
        if phase.chapter_end >= batch_start and phase.chapter_start <= batch_end
    ]
    if overlapping_phases:
        lines.append("* 阶段推进要求：")
        for phase in overlapping_phases:
            lines.append(
                f"  - 「{phase.phase_name}」（第{phase.chapter_start}–{phase.chapter_end}章）："
                f"{phase.description} {_phase_role(phase.chapter_start, phase.chapter_end)}"
            )
            if phase.key_events:
                lines.append(f"    本阶段关键事件：{'；'.join(phase.key_events)}")
            if phase.tension_level:
                lines.append(f"    张力目标：{phase.tension_level}")

    in_batch_turning_points = [
        tp for tp in blueprint.key_turning_points if batch_start <= tp.chapter_number <= batch_end
    ]
    nearby_turning_points = [
        tp
        for tp in blueprint.key_turning_points
        if tp not in in_batch_turning_points and abs(tp.chapter_number - batch_end) <= 2
    ]
    if in_batch_turning_points or nearby_turning_points:
        lines.append("* 转折点要求：")
        for tp in in_batch_turning_points:
            lines.append(f"  - 必须在第{tp.chapter_number}章落下关键转折：{tp.description}")
        for tp in nearby_turning_points:
            if tp.chapter_number > batch_end:
                lines.append(
                    f"  - 需要为第{tp.chapter_number}章的下一转折提前预热：{tp.description}"
                )
            else:
                lines.append(f"  - 需承接第{tp.chapter_number}章刚发生的转折后果：{tp.description}")

    active_arc_lines: list[str] = []
    for arc in blueprint.character_arcs:
        for milestone in arc.milestones:
            if milestone.chapter_end >= batch_start and milestone.chapter_start <= batch_end:
                active_arc_lines.append(
                    f"  - 角色「{arc.character}」（第{milestone.chapter_start}–{milestone.chapter_end}章）："
                    f"{milestone.description} {_milestone_role(milestone.chapter_start, milestone.chapter_end)}"
                )
    if active_arc_lines:
        lines.append("* 角色弧光要求：")
        lines.extend(active_arc_lines)

    active_subplot_lines: list[str] = []
    for subplot in blueprint.subplot_plan:
        relevant_chapters = [
            ch for ch in subplot.involved_chapters if batch_start <= ch <= batch_end
        ]
        if not relevant_chapters:
            continue
        chapter_text = "、".join(f"第{ch}章" for ch in relevant_chapters)
        if min(relevant_chapters) == min(subplot.involved_chapters):
            subplot_role = "本批启动该支线。"
        elif max(relevant_chapters) == max(subplot.involved_chapters):
            subplot_role = "本批需要让该支线阶段性收束。"
        else:
            subplot_role = "本批持续推进该支线。"
        active_subplot_lines.append(
            f"  - 支线「{subplot.name}」（{chapter_text}）：{subplot.description} {subplot_role}"
        )
    if active_subplot_lines:
        lines.append("* 支线推进要求：")
        lines.extend(active_subplot_lines)

    # ── 支线交织关系提示 ─────────────────────────────────────
    weave_lines: list[str] = []
    for subplot in blueprint.subplot_plan:
        for link in subplot.weave_links or []:
            if batch_start <= link.trigger_chapter <= batch_end:
                weave_lines.append(
                    f"  - 交织触发：第{link.trigger_chapter}章，{link.source_type}「{link.source_ref}」"
                    f"→ {link.link_type} → 支线「{link.target_subplot}」（{link.description}）"
                )
            elif link.trigger_chapter > batch_end and link.trigger_chapter <= batch_end + 2:
                weave_lines.append(
                    f"  - 交织预热：支线「{subplot.name}」将在第{link.trigger_chapter}章"
                    f"受{link.source_type}「{link.source_ref}」影响（{link.link_type}），"
                    f"本批需为此铺垫"
                )
    if weave_lines:
        lines.append("* 支线交织关系：")
        lines.extend(weave_lines)

    if batch_end >= total_chapters:
        lines.append("* 本批收束要求：")
        lines.append(
            f"  - 本批已到全书结尾，必须对照最终收束策略完成落点：{blueprint.ending_strategy}"
        )
    else:
        lines.append("* 本批收口要求：")
        lines.append("  - 本批末章必须留下明确的情节钩子、人物决策或未解问题，便于下一批自然续接。")

    return "\n".join(lines)


def extract_phase_rhythm_guidance(
    blueprint: NarrativeBlueprint,
    batch_start: int,
    batch_end: int,
    total_chapters: int,
) -> str:
    """Build concise rhythm guidance for PLAN_OUTLINE_CONTINUE batches.

    The prompt only needs compact, chapter-batch-level pacing signals. Keep the
    guidance actionable so outline generation can translate it into chapter hooks
    and conflict density rather than broad literary advice.
    """

    if total_chapters <= 0:
        return ""

    lines: list[str] = []
    overlapping_phases = [
        phase
        for phase in (blueprint.narrative_phases or [])
        if phase.chapter_end >= batch_start and phase.chapter_start <= batch_end
    ]
    for phase in overlapping_phases[:2]:
        phase_name = str(getattr(phase, "phase_name", "") or "当前阶段").strip()
        tension = str(getattr(phase, "tension_level", "") or "").strip()
        if tension in {"低", "回落"}:
            lines.append(
                f"- {phase_name}（张力={tension}）：允许铺垫与余波，但每章仍须保留至少一个外部推进点，避免纯对话空转。"
            )
        elif tension in {"渐升", "中", "中高"}:
            lines.append(
                f"- {phase_name}（张力={tension}）：本批应逐章加压，每2-3章至少安排一次阻力升级、动作事件或关系硬碰撞。"
            )
        elif tension in {"高", "高潮"}:
            lines.append(
                f"- {phase_name}（张力={tension}）：保持高事件密度，关键章节应让冲突当章落地，避免把高潮写成解释或回顾。"
            )
        elif tension:
            lines.append(
                f"- {phase_name}：当前阶段张力为{tension}，请据此分配轻重章，避免连续同质节奏。"
            )

    batch_midpoint = (batch_start + batch_end) / 2
    batch_position = batch_midpoint / max(total_chapters, 1)
    if 0.40 <= batch_position <= 0.70:
        lines.append(
            "- 当前处于书中段，禁止连续生成低冲突章节；至少每2-3章安排一次可见的外部事件、行动升级或局势变化。"
        )
    elif batch_position < 0.25:
        lines.append(
            "- 当前仍在前段，优先建立钩子、人物目标与冲突入口，不要把篇幅耗在重复报时、定位和铺景。"
        )
    elif batch_position > 0.80:
        lines.append(
            "- 当前进入后段，减少横向发散；章节应优先回收关键悬念、压缩支线游离，并确保每章后果清晰。"
        )

    return "\n".join(lines)


def extract_chapter_rhythm_targets(
    blueprint: NarrativeBlueprint,
    chapter_number: int,
) -> dict[str, Any]:
    """Return the blueprint rhythm target for one chapter, if present."""

    for point in list(getattr(blueprint, "chapter_rhythm_curve", []) or []):
        if int(getattr(point, "chapter_number", 0) or 0) != chapter_number:
            continue
        return {
            "chapter_number": int(getattr(point, "chapter_number", 0) or 0),
            "target_pacing": int(getattr(point, "target_pacing", 3) or 3),
            "target_tension": int(getattr(point, "target_tension", 3) or 3),
            "beat_pattern": str(getattr(point, "beat_pattern", "") or ""),
            "description": str(getattr(point, "description", "") or ""),
        }
    return {}


# ---------------------------------------------------------------------------
# Conversation-history helpers
# ---------------------------------------------------------------------------


def history_prior_messages(
    conversation_history: list[dict[str, str]],
    *,
    history_window_rounds: int,
) -> list[dict[str, str]] | None:
    """Return recent conversation messages for multi-turn continuation."""
    if not conversation_history:
        return None
    max_msgs = history_window_rounds * 2
    return conversation_history[-max_msgs:]


def chapter_map_values(
    chapter_map: dict[int, ChapterOutline],
) -> list[ChapterOutline] | None:
    """Return current accumulated chapter list for guidance context."""
    if not chapter_map:
        return None
    return list(chapter_map.values())


# ---------------------------------------------------------------------------
# Scoped weave links extraction
# ---------------------------------------------------------------------------


def extract_scoped_weave_links(
    blueprint: Any,
    batch_start: int,
    batch_end: int,
    *,
    buffer: int = 2,
) -> list[dict[str, Any]]:
    """Extract subplot weave links relevant to the current batch.

    Only returns links whose trigger_chapter falls within
    [batch_start - buffer, batch_end + buffer].
    """
    scoped: list[dict[str, Any]] = []
    low = batch_start - buffer
    high = batch_end + buffer
    for subplot in getattr(blueprint, "subplot_plan", []) or []:
        relevant_links = []
        for link in getattr(subplot, "weave_links", []) or []:
            tc = getattr(link, "trigger_chapter", 0)
            if low <= tc <= high:
                relevant_links.append({
                    "source_type": getattr(link, "source_type", ""),
                    "source_ref": getattr(link, "source_ref", ""),
                    "link_type": getattr(link, "link_type", ""),
                    "trigger_chapter": tc,
                    "target_subplot": getattr(link, "target_subplot", ""),
                    "description": getattr(link, "description", ""),
                })
        if relevant_links:
            scoped.append({"name": getattr(subplot, "name", ""), "weave_links": relevant_links})
    return scoped
