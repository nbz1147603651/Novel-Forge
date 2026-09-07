"""ShortBlueprintStep — generates a lightweight narrative blueprint from spec."""

from __future__ import annotations

import re
from typing import Any

from novel_forge.core.constants import TaskType
from novel_forge.core.schemas.blueprint_elements import BlueprintElementSelection
from novel_forge.core.schemas.short_blueprint import (
    AnchorCharacter,
    ShortBlueprint,
    ShortCharacterArc,
    ShortNarrativePhase,
    ShortTurningPoint,
    StoryAnchor,
)
from novel_forge.core.schemas.spec import StorySpec
from novel_forge.obs.logger import get_logger
from novel_forge.pipeline.steps.base import PipelineStep
from novel_forge.pipeline.steps.step_registry import register_step

_log = get_logger("pipeline.short_blueprint")


@register_step("short_blueprint")
class ShortBlueprintStep(PipelineStep[StorySpec, ShortBlueprint]):
    """Spec → LLM → ShortBlueprint."""

    def __init__(
        self,
        router: Any,
        builder: Any,
        *,
        settings: Any,
        trace: Any = None,
        element_selection: BlueprintElementSelection | None = None,
        extra_context: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(router, builder, settings=settings, trace=trace)
        self._element_selection = element_selection
        self._extra_context = extra_context or {}

    @property
    def step_name(self) -> str:
        return "short_blueprint"

    async def _execute(self, input_data: StorySpec) -> ShortBlueprint:
        context: dict[str, Any] = {"spec": input_data, **self._extra_context}
        if self._element_selection is not None:
            context["blueprint_element_selection"] = self._element_selection.model_dump(mode="json")

        max_tok = self._dynamic_max_tokens(
            TaskType.SHORT_BLUEPRINT,
            max(1800, int(input_data.length_target or 3000) // 2),
            prompt_overhead=2000,
            min_tokens=2048,
        )
        temp = self.settings.temp_plan_outline

        # Stage 1: try normal LLM call + parse
        try:
            data = await self._call_with_retry(
                TaskType.SHORT_BLUEPRINT,
                context,
                max_tokens=max_tok,
                temperature=temp,
            )
            blueprint = _parse_blueprint(data, spec=input_data)
        except Exception as exc:
            _log.warning("short_blueprint_stage1_failed | err=%s", exc)

            # Stage 2: one LLM retry with error context
            try:
                retry_ctx = dict(context)
                retry_ctx["error_hint"] = (
                    "上次输出格式异常，请确保输出完整的 JSON 结构，"
                    "包含 synopsis、narrative_phases、turning_points、character_arcs、"
                    "emotional_arc、ending_strategy 等字段。"
                )
                data = await self._call_with_retry(
                    TaskType.SHORT_BLUEPRINT,
                    retry_ctx,
                    max_tokens=max_tok,
                    temperature=temp,
                    max_retries=1,
                )
                blueprint = _parse_blueprint(data, spec=input_data)
            except Exception as exc2:
                # Stage 3: structured fallback — no LLM call
                _log.warning(
                    "short_blueprint_stage2_failed | using structured fallback | err=%s",
                    exc2,
                )
                blueprint = _build_fallback_blueprint(input_data)

        if self._element_selection is not None:
            blueprint = blueprint.model_copy(update={"element_selection": self._element_selection})
        return blueprint


_SPLIT_RE = re.compile(r"[、,，/／|｜；;\n]+")
_PHASE_NAMES = ("引入", "发展", "对抗", "收束")
_PHASE_TENSIONS = ("低", "渐升", "高", "回落")
_PHASE_EMOTIONS = ("铺垫", "不安", "爆发", "余震")


def _clean_text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _split_text_items(value: Any) -> list[str]:
    if isinstance(value, list):
        raw_items = value
    else:
        raw_items = _SPLIT_RE.split(_clean_text(value))
    items: list[str] = []
    seen: set[str] = set()
    for raw in raw_items:
        text = _clean_text(raw)
        if not text or text in seen:
            continue
        seen.add(text)
        items.append(text)
    return items


def _safe_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _build_anchor_characters(
    raw_anchor: Any,
    *,
    phases: list[ShortNarrativePhase],
    arcs: list[ShortCharacterArc],
) -> list[AnchorCharacter]:
    characters: list[AnchorCharacter] = []
    seen: set[str] = set()
    if isinstance(raw_anchor, dict):
        for item in raw_anchor.get("core_characters", []):
            if not isinstance(item, dict):
                continue
            name = _clean_text(item.get("name"))
            if not name or name in seen:
                continue
            seen.add(name)
            characters.append(
                AnchorCharacter(
                    name=name,
                    role=_clean_text(item.get("role")) or "主要角色",
                )
            )
    for arc in arcs:
        if arc.character and arc.character not in seen:
            seen.add(arc.character)
            characters.append(AnchorCharacter(name=arc.character, role="主要角色"))
    for phase in phases:
        for name in phase.characters_present:
            if name and name not in seen:
                seen.add(name)
                characters.append(AnchorCharacter(name=name, role="主要角色"))
    return characters[:4]


def _normalize_turning_points(
    raw_points: Any,
    *,
    phases: list[ShortNarrativePhase],
    spec: StorySpec,
    synopsis: str,
) -> list[ShortTurningPoint]:
    turning_points: list[ShortTurningPoint] = []
    seen_positions: set[int] = set()
    for raw in raw_points if isinstance(raw_points, list) else []:
        if not isinstance(raw, dict):
            continue
        pos = max(0, min(100, _safe_int(raw.get("position_percent"), 50)))
        desc = _clean_text(raw.get("description"))
        impact = _clean_text(raw.get("impact"))
        if pos in seen_positions or not desc:
            continue
        seen_positions.add(pos)
        turning_points.append(
            ShortTurningPoint(
                position_percent=pos,
                description=desc,
                impact=impact or "改变角色选择与故事走向",
            )
        )
    turning_points.sort(key=lambda item: item.position_percent)
    if turning_points:
        return turning_points[:3]

    for phase in phases:
        if phase.tension_level in {"高", "高潮"}:
            midpoint = max(10, min(90, (phase.position_start + phase.position_end) // 2))
            desc = phase.key_event or spec.conflict_hint or synopsis or "核心冲突进入不可回避的阶段"
            turning_points.append(
                ShortTurningPoint(
                    position_percent=midpoint,
                    description=_clean_text(desc),
                    impact="迫使角色做出关键选择",
                )
            )
            break
    if not turning_points:
        turning_points.append(
            ShortTurningPoint(
                position_percent=60,
                description=_clean_text(spec.conflict_hint or synopsis or "核心矛盾被推到台前"),
                impact="让故事从铺垫转入正面冲突",
            )
        )
    return turning_points[:3]


def _normalize_phases(
    raw_phases: Any,
    *,
    spec: StorySpec,
    synopsis: str,
) -> list[ShortNarrativePhase]:
    parsed: list[ShortNarrativePhase] = []
    for raw in raw_phases if isinstance(raw_phases, list) else []:
        if not isinstance(raw, dict):
            continue
        parsed.append(
            ShortNarrativePhase(
                phase_name=_clean_text(raw.get("phase_name")),
                position_start=max(0, min(100, _safe_int(raw.get("position_start"), 0))),
                position_end=max(0, min(100, _safe_int(raw.get("position_end"), 100))),
                description=_clean_text(raw.get("description")),
                tension_level=_clean_text(raw.get("tension_level")),
                emotional_focus=_clean_text(raw.get("emotional_focus")),
                time_setting=_clean_text(raw.get("time_setting")),
                location=_clean_text(raw.get("location")),
                characters_present=_split_text_items(raw.get("characters_present", [])),
                key_event=_clean_text(raw.get("key_event")),
            )
        )

    if not parsed:
        parsed = [
            ShortNarrativePhase(),
            ShortNarrativePhase(),
            ShortNarrativePhase(),
            ShortNarrativePhase(),
        ]

    parsed = parsed[:5]
    while len(parsed) < 3:
        parsed.append(ShortNarrativePhase())

    count = len(parsed)
    default_width = max(15, 100 // count)
    current_start = 0
    normalized: list[ShortNarrativePhase] = []
    for index, phase in enumerate(parsed):
        phase_name = phase.phase_name or _PHASE_NAMES[min(index, len(_PHASE_NAMES) - 1)]
        start = current_start
        if index == count - 1:
            end = 100
        else:
            proposed_end = (
                phase.position_end if phase.position_end > start else start + default_width
            )
            remaining = count - index - 1
            max_end = 100 - remaining * 10
            end = min(max(proposed_end, start + 10), max_end)
        current_start = end
        normalized.append(
            ShortNarrativePhase(
                phase_name=phase_name,
                position_start=start,
                position_end=end,
                description=phase.description
                or f"{phase_name}阶段围绕“{_clean_text(spec.conflict_hint or synopsis or spec.theme)}”推进。",
                tension_level=phase.tension_level
                or _PHASE_TENSIONS[min(index, len(_PHASE_TENSIONS) - 1)],
                emotional_focus=phase.emotional_focus
                or _PHASE_EMOTIONS[min(index, len(_PHASE_EMOTIONS) - 1)],
                time_setting=phase.time_setting,
                location=phase.location,
                characters_present=phase.characters_present,
                key_event=phase.key_event or phase.description,
            )
        )
    normalized[-1].position_end = 100
    return normalized


def _build_anchor(
    raw_anchor: Any,
    *,
    phases: list[ShortNarrativePhase],
    arcs: list[ShortCharacterArc],
    turning_points: list[ShortTurningPoint],
    spec: StorySpec,
    synopsis: str,
) -> StoryAnchor:
    anchor_dict = raw_anchor if isinstance(raw_anchor, dict) else {}
    characters = _build_anchor_characters(anchor_dict, phases=phases, arcs=arcs)
    locations = _split_text_items(anchor_dict.get("primary_locations", []))
    if not locations:
        for phase in phases:
            if phase.location and phase.location not in locations:
                locations.append(phase.location)
    time_frame = _clean_text(anchor_dict.get("time_frame"))
    if not time_frame:
        times = [
            _clean_text(phase.time_setting) for phase in phases if _clean_text(phase.time_setting)
        ]
        if times:
            time_frame = times[0] if len(times) == 1 else f"{times[0]}至{times[-1]}"
    central_event = _clean_text(anchor_dict.get("central_event"))
    if not central_event:
        central_event = _clean_text(
            spec.conflict_hint
            or (turning_points[0].description if turning_points else "")
            or synopsis
            or spec.theme
        )
    return StoryAnchor(
        time_frame=time_frame,
        primary_locations=locations[:4],
        core_characters=characters,
        central_event=central_event,
    )


def _backfill_phases(
    phases: list[ShortNarrativePhase],
    *,
    anchor: StoryAnchor,
    turning_points: list[ShortTurningPoint],
    synopsis: str,
) -> list[ShortNarrativePhase]:
    fallback_characters = [item.name for item in anchor.core_characters]
    fallback_location = anchor.primary_locations[0] if anchor.primary_locations else ""
    normalized: list[ShortNarrativePhase] = []
    for index, phase in enumerate(phases):
        related_turning_point = next(
            (
                item
                for item in turning_points
                if phase.position_start <= item.position_percent <= phase.position_end
            ),
            None,
        )
        normalized.append(
            phase.model_copy(
                update={
                    "location": phase.location or fallback_location,
                    "time_setting": phase.time_setting or anchor.time_frame,
                    "characters_present": phase.characters_present or fallback_characters[:3],
                    "key_event": phase.key_event
                    or (related_turning_point.description if related_turning_point else "")
                    or anchor.central_event
                    or synopsis,
                    "description": phase.description
                    or (related_turning_point.description if related_turning_point else synopsis),
                    "emotional_focus": phase.emotional_focus
                    or _PHASE_EMOTIONS[min(index, len(_PHASE_EMOTIONS) - 1)],
                }
            )
        )
    return normalized


def _normalize_character_arcs(
    raw_arcs: Any,
    *,
    anchor: StoryAnchor,
    turning_points: list[ShortTurningPoint],
) -> list[ShortCharacterArc]:
    arcs: list[ShortCharacterArc] = []
    seen: set[str] = set()
    for raw in raw_arcs if isinstance(raw_arcs, list) else []:
        if not isinstance(raw, dict):
            continue
        name = _clean_text(raw.get("character"))
        if not name or name in seen:
            continue
        seen.add(name)
        arcs.append(
            ShortCharacterArc(
                character=name,
                arc_summary=_clean_text(raw.get("arc_summary"))
                or f"围绕“{anchor.central_event}”发生一次清晰的状态变化。",
                key_moment=_clean_text(raw.get("key_moment"))
                or (
                    turning_points[0].description if turning_points else "在核心冲突升级处发生转变"
                ),
            )
        )
    if arcs:
        return arcs[:4]

    default_key_moment = (
        turning_points[0].description if turning_points else "在核心冲突最紧绷时做出选择"
    )
    return [
        ShortCharacterArc(
            character=character.name,
            arc_summary=f"围绕“{anchor.central_event}”从原有立场被迫走向新的选择。",
            key_moment=default_key_moment,
        )
        for character in anchor.core_characters[:3]
    ]


def _build_fallback_blueprint(spec: StorySpec) -> ShortBlueprint:
    synopsis = _clean_text(spec.theme) or f"一段{_clean_text(spec.genre)}风格的故事"

    conflict = _clean_text(spec.conflict_hint) or synopsis
    hint_chars = [_clean_text(h) for h in _SPLIT_RE.split(spec.characters_hint) if _clean_text(h)]
    default_char = hint_chars[0] if hint_chars else "主角"

    phases = [
        ShortNarrativePhase(
            phase_name="引入",
            position_start=0,
            position_end=25,
            description=f"引入主要人物与背景设定，围绕\u201c{conflict}\u201d铺设悬念。",
            tension_level="低",
            emotional_focus="铺垫",
        ),
        ShortNarrativePhase(
            phase_name="发展",
            position_start=25,
            position_end=50,
            description=f"矛盾逐步显现，角色被迫面对\u201c{conflict}\u201d。",
            tension_level="渐升",
            emotional_focus="不安",
        ),
        ShortNarrativePhase(
            phase_name="对抗",
            position_start=50,
            position_end=75,
            description=f"核心冲突达到顶点，围绕\u201c{conflict}\u201d展开正面交锋。",
            tension_level="高",
            emotional_focus="爆发",
        ),
        ShortNarrativePhase(
            phase_name="收束",
            position_start=75,
            position_end=100,
            description="冲突走向结局，完成故事收束。",
            tension_level="回落",
            emotional_focus="余震",
        ),
    ]

    tone_hint = _clean_text(spec.tone) or "转折"
    turning_points = [
        ShortTurningPoint(
            position_percent=55,
            description=conflict,
            impact=f"以{tone_hint}的方式将故事推入不可逆的高潮",
        ),
    ]

    anchor = StoryAnchor(
        time_frame=_clean_text(spec.world_hint) or "故事发生的主要时段",
        core_characters=[AnchorCharacter(name=default_char, role="主角")],
        central_event=conflict,
    )

    arcs = [
        ShortCharacterArc(
            character=default_char,
            arc_summary=f"围绕\u201c{conflict}\u201d经历信念动摇，最终做出关键选择。",
            key_moment=conflict,
        ),
    ]

    emotional_arc = " \u2192 ".join(p.emotional_focus for p in phases)
    ending_strategy = _clean_text(
        spec.ending_style or f"围绕\u201c{conflict}\u201d完成收束，留下明确情绪落点"
    )

    return ShortBlueprint(
        synopsis=synopsis,
        anchor_elements=anchor,
        narrative_phases=phases,
        turning_points=turning_points,
        character_arcs=arcs,
        emotional_arc=emotional_arc,
        ending_strategy=ending_strategy,
    )


def _parse_blueprint(data: dict[str, Any], *, spec: StorySpec) -> ShortBlueprint:
    """Safely parse LLM JSON into a normalized ShortBlueprint."""
    synopsis = _clean_text(data.get("synopsis")) or _clean_text(spec.theme)
    phases = _normalize_phases(data.get("narrative_phases", []), spec=spec, synopsis=synopsis)
    raw_arcs = data.get("character_arcs", [])
    arcs_seed = [
        ShortCharacterArc(
            character=_clean_text(item.get("character")),
            arc_summary=_clean_text(item.get("arc_summary")),
            key_moment=_clean_text(item.get("key_moment")),
        )
        for item in raw_arcs
        if isinstance(item, dict) and _clean_text(item.get("character"))
    ]
    turning_points = _normalize_turning_points(
        data.get("turning_points", []),
        phases=phases,
        spec=spec,
        synopsis=synopsis,
    )
    anchor = _build_anchor(
        data.get("anchor_elements", {}),
        phases=phases,
        arcs=arcs_seed,
        turning_points=turning_points,
        spec=spec,
        synopsis=synopsis,
    )
    phases = _backfill_phases(
        phases, anchor=anchor, turning_points=turning_points, synopsis=synopsis
    )
    arcs = _normalize_character_arcs(raw_arcs, anchor=anchor, turning_points=turning_points)
    emotional_arc = _clean_text(data.get("emotional_arc")) or " → ".join(
        phase.emotional_focus for phase in phases if phase.emotional_focus
    )
    ending_strategy = _clean_text(data.get("ending_strategy")) or _clean_text(
        spec.ending_style or f"围绕“{anchor.central_event}”完成收束，并留下明确情绪落点"
    )

    return ShortBlueprint(
        synopsis=synopsis,
        anchor_elements=anchor,
        narrative_phases=phases,
        turning_points=turning_points,
        character_arcs=arcs,
        emotional_arc=emotional_arc,
        ending_strategy=ending_strategy,
    )
