"""PolishOutlineStep — AI-assisted outline refinement after batch generation.

Follows the same suggestion-select-merge pattern as ai_generate.py's
polish_config, but operates on StoryOutline / ChapterOutline data
structures instead of flat creative config dicts.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from novel_forge.core.constants import TaskType
from novel_forge.core.schemas.outline import ChapterOutline, StoryOutline
from novel_forge.persistence.polish_history import PolishHistoryRecorder
from novel_forge.pipeline.steps.base import PipelineStep
from novel_forge.pipeline.steps.step_registry import register_step

_logger = logging.getLogger(__name__)

_CHAPTER_FIELD_ALIASES: dict[str, str] = {
    "chapter_title": "title",
    "chapterTitle": "title",
    "heading": "title",
    "synopsis": "goal",
    "chapter_synopsis": "goal",
    "key_scenes": "main_plot_points",
    "scene_list": "main_plot_points",
    "character_arc_beat": "notes",
}


def _normalize_polish_suggestions(value: Any) -> list[str]:
    """Normalize suggestion list from model/UI inputs. Max 8, deduplicated."""
    if value is None:
        return []
    raw_items: list[str] = []
    if isinstance(value, str):
        text = value
        for separator in ("；", ";", "。", "，", ",", "、"):
            text = text.replace(separator, "\n")
        raw_items.extend(line.strip() for line in text.splitlines())
    elif isinstance(value, list):
        for item in value:
            if isinstance(item, str):
                raw_items.append(item)
            elif isinstance(item, dict):
                picked = item.get("text") or item.get("label") or item.get("direction") or ""
                raw_items.append(str(picked))
            elif item is not None:
                raw_items.append(str(item))
    elif isinstance(value, dict):
        for item in value.values():
            if item is not None:
                raw_items.append(str(item))
    else:
        raw_items.append(str(value))

    normalized: list[str] = []
    for item in raw_items:
        clean = " ".join(str(item).split()).strip("；;，,。 ")
        if not clean:
            continue
        if clean not in normalized:
            normalized.append(clean)
        if len(normalized) >= 8:
            break
    return normalized


def _normalize_focus_fields(value: Any) -> list[str]:
    """Normalize focus-field selector values for polish config."""
    if value is None:
        return []
    raw_items: list[str] = []
    if isinstance(value, str):
        raw_items = [
            part.strip() for part in value.replace("；", ",").replace("，", ",").split(",")
        ]
    elif isinstance(value, list):
        raw_items = [str(item).strip() for item in value]
    elif isinstance(value, dict):
        for key, enabled in value.items():
            if enabled:
                raw_items.append(str(key).strip())
    else:
        raw_items = [str(value).strip()]

    normalized: list[str] = []
    for item in raw_items:
        if not item:
            continue
        field_name = _CHAPTER_FIELD_ALIASES.get(item, item)
        if field_name not in normalized:
            normalized.append(field_name)
    return normalized


def _parse_chapter_range(value: str | None, *, total_chapters: int) -> list[int] | None:
    """Parse '3-7', '1,5,10', '第3至7章', '7' or None → chapter numbers."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return None

    chapters: list[int] = []
    normalized = str(value).strip()
    normalized = re.sub(r"[第章节\s]", "", normalized)
    normalized = (
        normalized.replace("，", ",")
        .replace("、", ",")
        .replace("；", ",")
        .replace(";", ",")
        .replace("至", "-")
        .replace("到", "-")
        .replace("~", "-")
        .replace("－", "-")
        .replace("—", "-")
        .replace("–", "-")
    )
    parts = [part for part in normalized.split(",") if part.strip()]
    for part in parts:
        part = part.strip()
        if "-" in part:
            start_str, end_str = [s for s in part.split("-", 1)]
            start, end = int(start_str.strip()), int(end_str.strip())
            if start < 1 or end > total_chapters or start > end:
                raise ValueError(f"章节范围 {value} 超出有效范围 (1-{total_chapters})")
            chapters.extend(range(start, end + 1))
        else:
            ch = int(part)
            if ch < 1 or ch > total_chapters:
                raise ValueError(f"章节号 {ch} 超出有效范围 (1-{total_chapters})")
            chapters.append(ch)

    return sorted(set(chapters))


def _chapter_range_to_text(value: str | tuple[int, int] | list[int] | None) -> str:
    """Normalize UI/service range formats to the parser's text form."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, tuple):
        return f"{value[0]}-{value[1]}"
    return ",".join(str(ch) for ch in value)


def _filter_chapters_by_range(
    outline: StoryOutline,
    chapter_range: tuple[int, int] | None,
) -> list[ChapterOutline]:
    """Filter chapters by (start, end) range. None = return all."""
    if chapter_range is None:
        return list(outline.chapters)

    start, end = chapter_range
    if start < 1 or end > (outline.total_chapters or len(outline.chapters)):
        raise ValueError(
            f"章节范围 ({start}-{end}) 超出大纲总章节数 "
            f"({outline.total_chapters or len(outline.chapters)})"
        )
    if start > end:
        raise ValueError(f"章节范围起始 ({start}) 不能大于结束 ({end})")

    return [ch for ch in outline.chapters if start <= ch.chapter_number <= end]


def _merge_adjusted_chapters(
    original: StoryOutline,
    adjusted_data: list[dict[str, Any]],
) -> StoryOutline:
    """Merge adjusted chapter data back into the original StoryOutline.

    Only updates chapters present in adjusted_data. Unmentioned chapters
    are preserved exactly. Revalidates every updated chapter to avoid leaking
    non-schema attributes from model output.
    """
    if not adjusted_data:
        return original

    adjustments: dict[int, dict[str, Any]] = {}
    valid_fields = set(ChapterOutline.model_fields)
    for item in adjusted_data:
        if not isinstance(item, dict):
            continue
        ch_num = item.get("chapter_number")
        if ch_num is None:
            continue
        clean_update: dict[str, Any] = {}
        for key, value in item.items():
            if key == "chapter_number":
                continue
            canonical_key = _CHAPTER_FIELD_ALIASES.get(str(key), str(key))
            if canonical_key in valid_fields:
                clean_update[canonical_key] = value
        adjustments[int(ch_num)] = clean_update

    new_chapters: list[ChapterOutline] = []
    for ch in original.chapters:
        if ch.chapter_number in adjustments:
            update_fields = adjustments[ch.chapter_number]
            filtered = {k: v for k, v in update_fields.items() if v is not None}
            if filtered:
                payload = ch.model_dump(mode="json")
                payload.update(filtered)
                ch = ChapterOutline.model_validate(payload)
        new_chapters.append(ch)

    if not new_chapters:
        return original

    return original.model_copy(update={"chapters": new_chapters})


def _changed_chapters_between(original: StoryOutline, adjusted: StoryOutline) -> list[int]:
    """Return chapter numbers whose serialized content changed after merging."""
    before = {ch.chapter_number: ch.model_dump(mode="json") for ch in original.chapters}
    changed: list[int] = []
    for ch in adjusted.chapters:
        payload = ch.model_dump(mode="json")
        if before.get(ch.chapter_number) != payload:
            changed.append(ch.chapter_number)
    return sorted(changed)


def _fallback_polish_suggestions(
    user_hint: str,
    focus_fields: list[str],
    chapters: list[ChapterOutline],
) -> list[str]:
    """Provide actionable UI suggestions when the model returns none in analysis mode."""
    field_labels = {
        "title": "章节标题",
        "goal": "章节目标",
        "beats_summary": "节奏梗概",
        "main_plot_points": "主线推进",
        "subplot_points": "支线推进",
        "expected_hook": "章尾钩子",
        "expected_payoffs": "伏笔兑现",
        "notes": "章节备注",
    }
    labels = [field_labels.get(field, field) for field in focus_fields[:3]]
    focus_text = "、".join(labels) if labels else "目标、节奏与主线"
    scope = f"{len(chapters)}章" if chapters else "选中章节"
    hint = user_hint.strip()
    suggestions = [
        f"围绕{focus_text}逐章强化{scope}的递进关系",
        "保留原有因果与人物动机，只做局部增补和校准",
        "让每章结尾与下一章开场形成更清晰的追读钩子",
    ]
    if "标题" in hint or "title" in " ".join(focus_fields).lower():
        suggestions.insert(0, "重拟章节标题，使其更贴合本章冲突与悬念")
    if hint:
        suggestions.append(f"优先落实用户方向：{hint[:26]}")

    normalized: list[str] = []
    for item in suggestions:
        if item not in normalized:
            normalized.append(item)
        if len(normalized) >= 6:
            break
    return normalized


@dataclass
class PolishOutlineInput:
    """Input for the outline polish step."""

    story_outline: StoryOutline | None
    user_hint: str = ""
    selected_suggestions: list[str] = field(default_factory=list)
    focus_fields: list[str] = field(default_factory=list)
    chapter_range: str | tuple[int, int] | list[int] | None = None
    analysis_only: bool = False
    polish_mode: str = ""
    record_history: bool = True


@dataclass
class PolishOutlineResult:
    """Result of the outline polish step."""

    adjusted_outline: StoryOutline | None
    polish_suggestions: list[str] = field(default_factory=list)
    changed_chapters: list[int] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@register_step("polish_outline")
class PolishOutlineStep(PipelineStep[PolishOutlineInput, PolishOutlineResult]):
    """Refine an existing StoryOutline based on user direction + suggestions."""

    @property
    def step_name(self) -> str:
        return "polish_outline"

    async def _execute(self, input_data: PolishOutlineInput) -> PolishOutlineResult:
        """Execute the outline polish step."""
        outline = input_data.story_outline
        if outline is None:
            return PolishOutlineResult(
                adjusted_outline=None,
                polish_suggestions=input_data.selected_suggestions,
                warnings=["StoryOutline is None"],
            )

        total = outline.total_chapters or len(outline.chapters)

        selected = _normalize_polish_suggestions(input_data.selected_suggestions)
        focus = _normalize_focus_fields(input_data.focus_fields)
        range_text = _chapter_range_to_text(input_data.chapter_range)

        chapter_list: list[int] | None = None
        if range_text:
            try:
                chapter_list = _parse_chapter_range(range_text, total_chapters=total)
            except ValueError as exc:
                return PolishOutlineResult(
                    adjusted_outline=outline,
                    warnings=[str(exc)],
                )

        filtered_chapters = outline.chapters
        if chapter_list is not None:
            filtered_chapters = [ch for ch in outline.chapters if ch.chapter_number in chapter_list]

        ctx = {
            "current_outline_json": json.dumps(
                {
                    "total_chapters": total,
                    "chapters": [
                        ch.model_dump(mode="json", exclude_none=True) for ch in filtered_chapters
                    ],
                },
                ensure_ascii=False,
                indent=2,
            ),
            "user_hint": input_data.user_hint,
            "selected_suggestions": selected,
            "focus_fields": focus,
            "chapter_range": range_text,
            "analysis_only": input_data.analysis_only,
            "polish_mode": input_data.polish_mode,
        }

        try:
            response = await self._call_with_retry(
                TaskType.POLISH_OUTLINE,
                ctx,
                max_tokens=self._dynamic_max_tokens(
                    TaskType.POLISH_OUTLINE,
                    max(2500, len(filtered_chapters) * 450),
                    prompt_overhead=3500,
                    min_tokens=4096,
                ),
                temperature=self.settings.temp_polish_outline
                if hasattr(self.settings, "temp_polish_outline")
                else 0.7,
            )
        except Exception as exc:
            _logger.warning("polish_outline_call_failed | error=%s", exc)
            suggestions = (
                _fallback_polish_suggestions(input_data.user_hint, focus, list(filtered_chapters))
                if input_data.analysis_only
                else selected
            )
            return PolishOutlineResult(
                adjusted_outline=outline,
                polish_suggestions=suggestions,
                warnings=[f"LLM call failed: {exc}"],
            )

        adjusted_data: list[dict[str, Any]] = []
        suggestions: list[str] = []

        if isinstance(response, dict):
            adjusted_data = response.get("adjusted_chapters", []) or []
            suggestions = _normalize_polish_suggestions(response.get("polish_suggestions", []))
        elif isinstance(response, str):
            try:
                parsed = json.loads(response)
                adjusted_data = parsed.get("adjusted_chapters", []) or []
                suggestions = _normalize_polish_suggestions(parsed.get("polish_suggestions", []))
            except json.JSONDecodeError:
                adjusted_data = []

        if input_data.analysis_only:
            if not suggestions:
                suggestions = _fallback_polish_suggestions(
                    input_data.user_hint,
                    focus,
                    list(filtered_chapters),
                )
            result = PolishOutlineResult(
                adjusted_outline=outline,
                polish_suggestions=suggestions,
            )
            self._persist_history(input_data, outline, result)
            return result

        result = PolishOutlineResult(
            adjusted_outline=outline,
            polish_suggestions=suggestions if suggestions else selected,
        )

        if adjusted_data:
            merged = _merge_adjusted_chapters(outline, adjusted_data)
            changed = _changed_chapters_between(outline, merged)
            result = PolishOutlineResult(
                adjusted_outline=merged,
                polish_suggestions=suggestions if suggestions else selected,
                changed_chapters=changed,
            )

        self._persist_history(input_data, outline, result)
        return result

    def _persist_history(
        self,
        input_data: PolishOutlineInput,
        original_outline: StoryOutline,
        result: PolishOutlineResult,
    ) -> None:
        if not input_data.record_history:
            return
        try:
            from novel_forge.core.config import get_settings

            settings = get_settings()
            project_dir = settings.storage_root / (getattr(self, "_project_id", "") or "default")
            recorder = PolishHistoryRecorder(project_dir)
            recorder.record_outline_polish(
                user_hint=input_data.user_hint,
                selected_suggestions=input_data.selected_suggestions,
                focus_fields=input_data.focus_fields,
                chapter_range=_chapter_range_to_text(input_data.chapter_range),
                before_outline=original_outline.model_dump(mode="json"),
                after_outline=(
                    result.adjusted_outline.model_dump(mode="json")
                    if result.adjusted_outline
                    else {}
                ),
                changed_chapters=result.changed_chapters,
                ai_suggestions=result.polish_suggestions,
                result_type="analyze" if input_data.analysis_only else "execute",
            )
        except Exception as exc:
            _logger.warning("polish_outline_history_persist_failed | error=%s", exc)
