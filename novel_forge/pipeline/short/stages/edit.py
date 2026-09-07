"""Edit stage: multi-round edit loop with completeness checks and repair."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from novel_forge.core.constants import TaskType
from novel_forge.core.domain.guardrails import detect_prompt_leaks
from novel_forge.core.schemas.eval_schema import EvalReport
from novel_forge.core.utils.text_validation import count_chapter_words
from novel_forge.obs.logger import get_logger
from novel_forge.pipeline.short._shared import (
    clean_text,
    excerpt_text,
    project_short_blueprint_prompt_card,
    project_short_execution_prompt_card,
    short_research_prompt_context,
    unique_texts,
)
from novel_forge.pipeline.steps.edit_step import EditInput, EditStep
from novel_forge.pipeline.steps.evaluate_step import EvaluateStep

if TYPE_CHECKING:
    from novel_forge.core.schemas.beats import StoryBeats
    from novel_forge.core.schemas.short_blueprint import ShortBlueprint
    from novel_forge.core.schemas.spec import StorySpec
    from novel_forge.persistence.models import ProjectLayout
    from novel_forge.pipeline.short_runner import ShortStoryRunner

_log = get_logger("pipeline.short.stages.edit")

_INCOMPLETE_ENDING_RE = re.compile(r"(待续|未完|to be continued|下回分解)", re.IGNORECASE)
_INCOMPLETE_END_PUNCT = ("，", ",", "：", ":", "；", ";", "、", "（", "(")
_COMPLETENESS_HINT_RE = re.compile(
    r"(结尾|收尾|收束|没头没尾|无头无尾|戛然而止|未完成|不完整|开篇)"
)
_QUALITY_HINT_RE = re.compile(
    r"(主题|母题|收束|余韵|情绪落点|情感落点|呼应|风格|文风|文学性|氛围|开篇|结尾|冲突|吸引力|节奏)",
    re.IGNORECASE,
)
_PRIMARY_QUALITY_DIMENSIONS = frozenset({"continuity", "style", "engagement"})
_SECONDARY_QUALITY_DIMENSIONS = frozenset({"pacing", "character"})
_QUALITY_DIMENSION_LABELS = {
    "continuity": "连贯性",
    "style": "风格",
    "engagement": "吸引力",
    "pacing": "节奏",
    "character": "人物",
}


def _is_en_language(language: str) -> bool:
    """Return True if the language code indicates English output."""
    key = str(language or "zh").strip().lower().replace("_", "-")
    return key in {"en", "en-us", "en-gb", "english"} or key.startswith("en")


def _completeness_strings(language: str) -> dict[str, str]:
    """Return language-specific completeness check messages."""
    if _is_en_language(language):
        return {
            "empty_text": "The text is empty; cannot form a complete short story.",
            "too_short": "The text is only about {count} words, clearly insufficient for a complete short story.",
            "incomplete_punct": "The text ends with an incomplete punctuation mark, as if truncated.",
            "incomplete_ending": 'The final paragraph contains a "to be continued" style break, which does not meet short story completion requirements.',
            "short_final_para": "The final paragraph is too short; insufficient emotional and event resolution.",
            "no_final_para": "Missing a valid final paragraph; cannot complete the ending resolution.",
            "no_clear_pause": "The ending lacks a clear pause; the resolution strategy cannot properly land.",
            "short_resolution": "The resolution beat landing space is too short; risks feeling incomplete.",
        }
    return {
        "empty_text": "正文为空，无法形成完整短篇。",
        "too_short": "正文长度仅约 {count} 字，明显不足以完成一个完整短篇。",
        "incomplete_punct": "正文结尾停在未完成的连接符后，像是被截断。",
        "incomplete_ending": '正文尾段出现"待续/未完"式停断，不符合短篇完整收束要求。',
        "short_final_para": "最后一段过短，情绪和事件落点不足。",
        "no_final_para": "缺少有效尾段，无法完成结尾落点。",
        "no_clear_pause": "结尾缺少清晰停顿，收束策略无法真正落地。",
        "short_resolution": "resolution beat 的落地空间过短，容易出现没头没尾。",
    }


def check_short_story_completeness(
    runner: ShortStoryRunner,
    text: str,
    spec: StorySpec,
    execution_plan: dict[str, Any],
) -> dict[str, Any]:
    source = str(text or "").strip()
    issues: list[str] = []
    char_count = count_chapter_words(source)
    target_words = max(500, spec.length_target or 3000)
    paragraphs = [p.strip() for p in source.split("\n\n") if p.strip()]
    tail = source[-400:]
    last_paragraph = paragraphs[-1] if paragraphs else ""
    completion_contract = execution_plan.get("completion_contract", {}) if execution_plan else {}
    strings = _completeness_strings(getattr(spec, "language", "zh"))

    if not source:
        issues.append(strings["empty_text"])
    if char_count and char_count < int(target_words * 0.45):
        issues.append(strings["too_short"].format(count=char_count))
    if source.endswith(_INCOMPLETE_END_PUNCT):
        issues.append(strings["incomplete_punct"])
    if _INCOMPLETE_ENDING_RE.search(last_paragraph or tail):
        issues.append(strings["incomplete_ending"])
    if last_paragraph and len(last_paragraph) < 18 and char_count >= 800:
        issues.append(strings["short_final_para"])
    if completion_contract:
        ending_strategy = clean_text(completion_contract.get("ending_strategy"))
        resolution_target = clean_text(completion_contract.get("resolution_target"))
        if not last_paragraph:
            issues.append(strings["no_final_para"])
        if ending_strategy and not any(
            token in tail for token in ("。", "！", "？", "…", ".", "!", "?")
        ):
            issues.append(strings["no_clear_pause"])
        if resolution_target and len(tail) < 80:
            issues.append(strings["short_resolution"])

    return {
        "passed": not issues,
        "issues": issues,
        "char_count": char_count,
        "tail_excerpt": tail[-120:],
    }


def eval_flags_incomplete_story(eval_report: EvalReport) -> bool:
    haystacks = [eval_report.summary]
    haystacks.extend(score.comment for score in eval_report.scores)
    for suggestion in eval_report.repair_suggestions:
        haystacks.append(suggestion.issue)
        haystacks.append(suggestion.suggestion)
    return any(_COMPLETENESS_HINT_RE.search(text or "") for text in haystacks)


def collect_short_quality_repair_issues(
    eval_report: EvalReport,
    *,
    overall_score_floor: float = 7.4,
    primary_dimension_floor: float = 7.0,
    secondary_dimension_floor: float = 6.6,
    max_issues: int = 4,
) -> list[str]:
    raw_issues: list[str] = []
    summary = clean_text(eval_report.summary)
    summary_has_quality_hint = bool(_QUALITY_HINT_RE.search(summary))

    if summary and (
        eval_report.overall_score < overall_score_floor
        or (summary_has_quality_hint and eval_report.overall_score < overall_score_floor + 0.4)
    ):
        raw_issues.append(f"评估总结指出：{summary}")

    for score in eval_report.scores:
        dimension = clean_text(score.dimension).lower()
        comment = clean_text(score.comment)
        label = _QUALITY_DIMENSION_LABELS.get(dimension, dimension or "质量")
        if dimension in _PRIMARY_QUALITY_DIMENSIONS and score.score < primary_dimension_floor:
            raw_issues.append(
                f"{label}维度仅 {score.score:.1f} 分：{comment or '需要针对性修补。'}"
            )
        elif (
            dimension in _SECONDARY_QUALITY_DIMENSIONS
            and score.score < secondary_dimension_floor
            and _QUALITY_HINT_RE.search(comment)
        ):
            raw_issues.append(f"{label}维度存在拖弱项：{comment or '需要定向修补。'}")

    for suggestion in eval_report.repair_suggestions:
        dimension = clean_text(suggestion.dimension).lower()
        location = clean_text(suggestion.location)
        issue = clean_text(suggestion.issue)
        fix = clean_text(suggestion.suggestion)
        haystack = " ".join(part for part in (issue, fix, location) if part)
        is_relevant = (
            dimension in _PRIMARY_QUALITY_DIMENSIONS
            or (
                dimension in _SECONDARY_QUALITY_DIMENSIONS
                and clean_text(suggestion.priority).lower() == "high"
            )
            or bool(_QUALITY_HINT_RE.search(haystack))
        )
        if not is_relevant:
            continue

        prefix = f"{location}：" if location else ""
        if issue and fix and fix not in issue:
            raw_issues.append(f"{prefix}{issue}；{fix}")
        elif issue:
            raw_issues.append(f"{prefix}{issue}")
        elif fix:
            raw_issues.append(f"{prefix}{fix}")

    normalized = [
        excerpt_text(item, limit=120) for item in unique_texts(raw_issues) if clean_text(item)
    ]
    return normalized[:max_issues]


def _build_eval_feedback(eval_report: EvalReport) -> dict[str, Any]:
    low_dimensions = [
        {
            "dimension": score.dimension,
            "score": score.score,
            "comment": score.comment,
        }
        for score in eval_report.scores
        if score.score < 7.0
    ]
    repair_suggestions = [
        {
            "issue": suggestion.issue,
            "location": suggestion.location,
            "suggestion": suggestion.suggestion,
            "priority": suggestion.priority,
            "dimension": suggestion.dimension,
        }
        for suggestion in eval_report.repair_suggestions[:6]
    ]
    return {
        "scores": [s.model_dump(mode="json") for s in eval_report.scores],
        "overall_score": eval_report.overall_score,
        "summary": eval_report.summary,
        "low_dimensions": low_dimensions,
        "repair_suggestions": repair_suggestions,
    }


def _build_short_repair_context(
    runner: ShortStoryRunner,
    layout: ProjectLayout,
    spec: StorySpec,
    beats: StoryBeats,
    blueprint: ShortBlueprint | None,
    execution_plan: dict[str, Any],
    issues: list[str],
    *,
    issue_context_key: str,
    repair_warning: str,
    eval_report: EvalReport | None,
    current_text: str,
) -> dict[str, Any]:
    style_profile = runner._load_style_profile(layout)
    edit_ctx: dict[str, Any] = {
        "user_intent": runner._user_intent,
        **short_research_prompt_context(runner, include_inspiration=False),
        "beats": beats,
        "tone": spec.tone,
        "genre": spec.genre,
        "theme": spec.theme,
        "opening_style": getattr(spec, "opening_style", ""),
        "ending_style": getattr(spec, "ending_style", ""),
        "target_word_count": spec.length_target,
        "execution_plan": project_short_execution_prompt_card(execution_plan, stage="edit"),
        "style_profile": style_profile,
        "chapter_goal": clean_text(
            getattr(blueprint, "synopsis", "") or spec.conflict_hint or spec.theme
        ),
        issue_context_key: issues,
    }
    if issue_context_key == "completeness_issues":
        edit_ctx["completeness_warning"] = repair_warning
    else:
        edit_ctx["repair_warning"] = repair_warning
    if eval_report is not None:
        edit_ctx["eval_feedback"] = _build_eval_feedback(eval_report)
    prompt_leaks = detect_prompt_leaks(current_text, max_hits=6)
    if prompt_leaks:
        edit_ctx["prompt_leak_hits"] = prompt_leaks
    if blueprint is not None:
        edit_ctx["blueprint"] = project_short_blueprint_prompt_card(blueprint, stage="edit")
    return edit_ctx


async def run_completion_repair(
    runner: ShortStoryRunner,
    layout: ProjectLayout,
    spec: StorySpec,
    beats: StoryBeats,
    blueprint: ShortBlueprint | None,
    execution_plan: dict[str, Any],
    current_text: str,
    issues: list[str],
    *,
    pass_label: str,
    iteration: int,
    eval_report: EvalReport | None = None,
    persist_draft: bool = True,
) -> str:
    edit_step = EditStep(
        runner._router,
        runner._builder,
        settings=runner._settings,
        trace=runner._trace,
        on_step=getattr(runner, "on_step", None),
    )
    edit_ctx = _build_short_repair_context(
        runner,
        layout,
        spec,
        beats,
        blueprint,
        execution_plan,
        issues,
        issue_context_key="completeness_issues",
        repair_warning=(
            "这是一篇短篇小说，必须形成完整的 beginning-middle-ending。"
            "请修到核心冲突得到阶段性回答、最后一段有明确情绪或事件落点，"
            "禁止停在待续式悬空位置。"
        ),
        eval_report=eval_report,
        current_text=current_text,
    )
    edit_result = await edit_step.run(
        EditInput(
            task_type=TaskType.EDIT,
            draft_text=current_text,
            context=edit_ctx,
            max_rounds=runner._max_edit + 1,
            iteration=iteration,
        )
    )
    if persist_draft:
        runner._storage.save_text(layout.short_draft_path(iteration), edit_result.revised_text)
    runner._on_step(
        pass_label,
        {
            "issues": issues,
            "iteration": iteration,
        },
    )
    return edit_result.revised_text


async def run_quality_repair(
    runner: ShortStoryRunner,
    layout: ProjectLayout,
    spec: StorySpec,
    beats: StoryBeats,
    blueprint: ShortBlueprint | None,
    execution_plan: dict[str, Any],
    current_text: str,
    issues: list[str],
    *,
    pass_label: str,
    iteration: int,
    eval_report: EvalReport,
    persist_draft: bool = True,
) -> str:
    edit_step = EditStep(
        runner._router,
        runner._builder,
        settings=runner._settings,
        trace=runner._trace,
        on_step=getattr(runner, "on_step", None),
    )
    edit_ctx = _build_short_repair_context(
        runner,
        layout,
        spec,
        beats,
        blueprint,
        execution_plan,
        issues,
        issue_context_key="repair_focus_issues",
        repair_warning=(
            "这是一次短篇定向质量修复。优先修补上方问题，增强主题回应、情绪落点、"
            "风格稳定性或读者吸引力；不要推翻已经有效的段落结构。"
        ),
        eval_report=eval_report,
        current_text=current_text,
    )
    edit_result = await edit_step.run(
        EditInput(
            task_type=TaskType.EDIT,
            draft_text=current_text,
            context=edit_ctx,
            max_rounds=runner._max_edit + 1,
            iteration=iteration,
        )
    )
    if persist_draft:
        runner._storage.save_text(layout.short_draft_path(iteration), edit_result.revised_text)
    runner._on_step(
        pass_label,
        {
            "issues": issues,
            "iteration": iteration,
        },
    )
    return edit_result.revised_text


async def run_adaptive_edit(
    runner: ShortStoryRunner,
    layout: ProjectLayout,
    spec: StorySpec,
    beats: StoryBeats,
    blueprint: ShortBlueprint | None,
    execution_plan: dict[str, Any],
    current_text: str,
    issues: list[str],
    *,
    iteration: int,
    max_rounds: int,
    eval_report: EvalReport,
) -> str:
    """Execute one bounded adaptive revision through the canonical EDIT task."""

    edit_step = EditStep(
        runner._router,
        runner._builder,
        settings=runner._settings,
        trace=runner._trace,
        on_step=getattr(runner, "_on_step", None),
    )
    edit_ctx = _build_short_repair_context(
        runner,
        layout,
        spec,
        beats,
        blueprint,
        execution_plan,
        issues,
        issue_context_key="repair_focus_issues",
        repair_warning=(
            "这是一次合并后的短篇定向修订。按列表优先修复用户硬意图、"
            "完整性和高优先级质量问题；不得借机改写人物、关系、POV、"
            "结局、世界规则或 locked 要素。"
        ),
        eval_report=eval_report,
        current_text=current_text,
    )
    edit_result = await edit_step.run(
        EditInput(
            task_type=TaskType.EDIT,
            draft_text=current_text,
            context=edit_ctx,
            max_rounds=max_rounds,
            iteration=iteration,
        )
    )
    runner._storage.save_text(layout.short_draft_path(iteration), edit_result.revised_text)
    runner._on_step(
        f"short_adaptive_edit_{iteration}",
        {"issues": issues, "iteration": iteration},
    )
    return edit_result.revised_text


def _build_short_eval_context(
    runner: ShortStoryRunner,
    spec: StorySpec,
    beats: StoryBeats,
    blueprint: ShortBlueprint | None,
    execution_plan: dict[str, Any],
    layout: Any | None = None,
) -> dict[str, Any]:
    style_profile: dict[str, Any] | None = None
    if layout is not None:
        style_profile = runner._load_style_profile(layout)

    beats_payload = beats.model_dump(mode="json")
    blueprint_payload = (
        project_short_blueprint_prompt_card(blueprint, stage="evaluate")
        if blueprint is not None
        else None
    )

    return {
        "user_intent": runner._user_intent,
        **short_research_prompt_context(runner, include_inspiration=False),
        "tone": spec.tone,
        "genre": spec.genre,
        "theme": getattr(spec, "theme", ""),
        "opening_style": getattr(spec, "opening_style", ""),
        "ending_style": getattr(spec, "ending_style", ""),
        "target_word_count": spec.length_target,
        "beats": list(beats_payload.get("beats", [])),
        "blueprint": blueprint_payload,
        "execution_plan": project_short_execution_prompt_card(
            execution_plan,
            stage="evaluate",
        ),
        "chapter_goal": clean_text(
            getattr(blueprint, "synopsis", "") or spec.conflict_hint or spec.theme
        ),
        "style_profile": style_profile,
    }


async def run_edit_loop(
    runner: ShortStoryRunner,
    layout: ProjectLayout,
    spec: StorySpec,
    beats: StoryBeats,
    blueprint: ShortBlueprint | None,
    execution_plan: dict[str, Any],
    current_text: str,
) -> str:
    edit_step = EditStep(
        runner._router,
        runner._builder,
        settings=runner._settings,
        trace=runner._trace,
        on_step=getattr(runner, "on_step", None),
    )
    eval_step = EvaluateStep(
        runner._router,
        runner._builder,
        settings=runner._settings,
        trace=runner._trace,
        extra_context=_build_short_eval_context(
            runner, spec, beats, blueprint, execution_plan, layout
        ),
    )

    length_target = spec.length_target or 3000
    last_eval: EvalReport | None = None
    style_profile = runner._load_style_profile(layout)

    for iteration in range(1, runner._max_edit + 1):
        edit_ctx: dict[str, Any] = {
            "user_intent": runner._user_intent,
            **short_research_prompt_context(runner, include_inspiration=False),
            "beats": beats,
            "tone": spec.tone,
            "genre": spec.genre,
            "theme": spec.theme,
            "opening_style": getattr(spec, "opening_style", ""),
            "ending_style": getattr(spec, "ending_style", ""),
            "target_word_count": length_target,
            "execution_plan": project_short_execution_prompt_card(
                execution_plan,
                stage="edit",
            ),
            "style_profile": style_profile,
        }
        if blueprint is not None:
            edit_ctx["blueprint"] = project_short_blueprint_prompt_card(
                blueprint,
                stage="edit",
            )
        if last_eval is not None:
            edit_ctx["eval_feedback"] = _build_eval_feedback(last_eval)
        prompt_leaks = detect_prompt_leaks(current_text, max_hits=6)
        if prompt_leaks:
            edit_ctx["prompt_leak_hits"] = prompt_leaks

        current_word_count = count_chapter_words(current_text)
        ratio = current_word_count / max(length_target, 1)
        if ratio < 0.7 or ratio > 1.5:
            edit_ctx["word_count_warning"] = (
                f"当前字数 {current_word_count}，目标 {length_target}，"
                f"偏离比例 {ratio:.0%}。请在编辑时向目标字数靠拢。"
            )
            _log.warning(
                "word_count_drift | iteration=%d | current=%d | target=%d | ratio=%.2f",
                iteration,
                current_word_count,
                length_target,
                ratio,
            )

        edit_result = await edit_step.run(
            EditInput(
                task_type=TaskType.EDIT,
                draft_text=current_text,
                context=edit_ctx,
                max_rounds=runner._max_edit,
                iteration=iteration,
            )
        )
        current_text = edit_result.revised_text
        runner._storage.save_text(layout.short_draft_path(iteration), current_text)
        runner._on_step(f"edit_{iteration}", edit_result)

        if iteration >= runner._max_edit:
            continue

        mid_eval = await eval_step.run(current_text)
        last_eval = mid_eval
        completeness_check = check_short_story_completeness(
            runner, current_text, spec, execution_plan
        )
        if mid_eval.overall_score >= 8.0 and completeness_check["passed"]:
            runner._on_step("early_stop", {"round": iteration, "score": mid_eval.overall_score})
            break

    return current_text
