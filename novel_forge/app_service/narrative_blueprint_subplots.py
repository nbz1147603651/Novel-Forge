"""Source-shaped subplot mutation helpers shared by Engine write commands.

The React reader must retain the same durable semantics as the PySide6
``SubplotManagerPanel``: edits replace only ``subplot_plan``, preserve character
arcs, write atomically at the route boundary, and invalidate downstream chapter
planning through the normal artifact-staleness mechanism.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

_PRIORITIES = {"primary", "normal", "background"}
_RESOLUTION_TYPES = {"", "resolve", "reveal", "ascend", "merge"}


def blueprint_total_chapters(blueprint: dict[str, Any]) -> int:
    """Return the best declared chapter upper bound without inventing one."""

    values: list[int] = []
    for value in (blueprint.get("total_chapters"), blueprint.get("chapter_count")):
        if isinstance(value, int) and value > 0:
            values.append(value)
    for phase in blueprint.get("narrative_phases", []):
        if not isinstance(phase, dict):
            continue
        value = phase.get("chapter_end")
        if isinstance(value, int) and value > 0:
            values.append(value)
    return max(values, default=0)


def is_event_driven_arc(arc: dict[str, Any], total_chapters: int = 0) -> bool:
    """Match the PySide6 conversion rule for arcs with external event momentum."""

    milestones = arc.get("milestones", [])
    if not isinstance(milestones, list) or not milestones:
        return False

    ranges: list[tuple[int, int]] = []
    for milestone in milestones:
        if not isinstance(milestone, dict):
            continue
        start = _positive_int(milestone.get("chapter_start"))
        end = _positive_int(milestone.get("chapter_end"))
        if start > 0 and end > 0:
            ranges.append((start, end))
    if not ranges:
        return False

    span = max(end for _, end in ranges) - min(start for start, _ in ranges) + 1
    span_threshold = max(10, int(total_chapters * 0.2)) if total_chapters > 0 else 10
    if span < span_threshold:
        return False

    event_keywords = (
        "发现", "揭露", "战斗", "对抗", "阴谋", "计划", "行动", "事件", "冲突", "危机", "背叛",
        "联盟", "争夺", "逃亡", "调查", "追踪", "伏击", "突袭", "谈判", "交易", "密谋",
        "discover", "battle", "fight", "conspiracy", "plot", "action", "conflict", "crisis",
        "betrayal", "alliance", "investigation",
    )
    internal_keywords = (
        "内心", "成长", "转变", "觉悟", "领悟", "释怀", "放下", "挣扎", "迷茫", "坚定", "信念",
        "情感", "心理", "认知", "inner", "growth", "realize", "accept", "understand", "feel",
        "emotion", "psychological", "belief", "change", "mature",
    )
    descriptions = [
        str(milestone.get("description") or "").lower()
        for milestone in milestones
        if isinstance(milestone, dict)
    ]
    event_score = sum(keyword in description for description in descriptions for keyword in event_keywords)
    internal_score = sum(
        keyword in description for description in descriptions for keyword in internal_keywords
    )
    return event_score >= internal_score or internal_score <= 1


def arc_to_subplot(arc: dict[str, Any]) -> dict[str, Any]:
    """Convert one selected character arc while retaining its original artifact."""

    character = str(arc.get("character") or "未知角色").strip() or "未知角色"
    milestones = [item for item in arc.get("milestones", []) if isinstance(item, dict)]
    ranges = [
        (_positive_int(item.get("chapter_start")), _positive_int(item.get("chapter_end")))
        for item in milestones
    ]
    valid_ranges = [(start, end) for start, end in ranges if start > 0 and end > 0]
    involved = (
        list(range(min(start for start, _ in valid_ranges), max(end for _, end in valid_ranges) + 1))
        if valid_ranges
        else []
    )
    events = [
        {
            "chapter_number": chapter,
            "event": str(milestone.get("description") or "").strip(),
            "weave_notes": "",
            "depends_on": [],
        }
        for milestone in milestones
        if (chapter := _positive_int(milestone.get("chapter_start"))) > 0
    ]
    return {
        "name": f"{character}线",
        "description": str(arc.get("arc_summary") or "").strip(),
        "involved_chapters": involved,
        "chapter_events": events,
        "weave_links": [],
        "priority": "normal",
        "resolution_chapter": 0,
        "resolution_target": "",
        "resolution_type": "",
    }


def normalize_subplot_payloads(
    subplots: Iterable[dict[str, Any]], *, total_chapters: int = 0
) -> list[dict[str, Any]]:
    """Validate editor/model data and return the canonical artifact shape.

    This does not synthesize story events.  A manual range therefore remains a
    planning range until the author or the AI explicitly supplies node events.
    """

    normalized: list[dict[str, Any]] = []
    names: set[str] = set()
    for index, raw in enumerate(subplots, start=1):
        if not isinstance(raw, dict):
            raise ValueError(f"第 {index} 条支线不是对象")
        name = str(raw.get("name") or "").strip()
        if not name:
            raise ValueError(f"第 {index} 条支线缺少名称")
        if name in names:
            raise ValueError(f"支线名称重复：{name}")
        names.add(name)

        involved = _normalize_chapters(raw.get("involved_chapters"), total_chapters)
        events = _normalize_events(raw.get("chapter_events"), total_chapters)
        event_chapters = {event["chapter_number"] for event in events}
        involved = sorted(set(involved) | event_chapters)
        if not involved and events:
            involved = sorted(event_chapters)

        resolution_chapter = _positive_int(raw.get("resolution_chapter"))
        if total_chapters and resolution_chapter > total_chapters:
            raise ValueError(f"支线「{name}」的收束章节超出全书范围")
        if involved and resolution_chapter and resolution_chapter < involved[0]:
            raise ValueError(f"支线「{name}」的收束章节早于其开始章节")

        priority = str(raw.get("priority") or "normal")
        if priority not in _PRIORITIES:
            raise ValueError(f"支线「{name}」的优先级无效")
        resolution_type = str(raw.get("resolution_type") or "")
        if resolution_type not in _RESOLUTION_TYPES:
            raise ValueError(f"支线「{name}」的收束类型无效")

        normalized.append(
            {
                "name": name,
                "description": str(raw.get("description") or "").strip(),
                "involved_chapters": involved,
                "chapter_events": events,
                "weave_links": _normalize_weave_links(raw.get("weave_links"), total_chapters),
                "priority": priority,
                "resolution_chapter": resolution_chapter,
                "resolution_target": str(raw.get("resolution_target") or "").strip(),
                "resolution_type": resolution_type,
            }
        )
    return normalized


def _positive_int(value: Any) -> int:
    return value if isinstance(value, int) and value > 0 else 0


def _normalize_chapters(value: Any, total_chapters: int) -> list[int]:
    values = value if isinstance(value, list) else []
    chapters = sorted({_positive_int(item) for item in values} - {0})
    if total_chapters and any(chapter > total_chapters for chapter in chapters):
        raise ValueError("支线章节超出全书范围")
    return chapters


def _normalize_events(value: Any, total_chapters: int) -> list[dict[str, Any]]:
    values = value if isinstance(value, list) else []
    events: list[dict[str, Any]] = []
    seen: set[int] = set()
    for item in values:
        if not isinstance(item, dict):
            continue
        chapter = _positive_int(item.get("chapter_number"))
        if not chapter or chapter in seen:
            continue
        if total_chapters and chapter > total_chapters:
            raise ValueError("支线节点章节超出全书范围")
        seen.add(chapter)
        depends_on = item.get("depends_on")
        events.append(
            {
                "chapter_number": chapter,
                "event": str(item.get("event") or "").strip(),
                "weave_notes": str(item.get("weave_notes") or "").strip(),
                "depends_on": [str(dep).strip() for dep in depends_on if str(dep).strip()]
                if isinstance(depends_on, list)
                else [],
            }
        )
    return sorted(events, key=lambda item: item["chapter_number"])


def _normalize_weave_links(value: Any, total_chapters: int) -> list[dict[str, Any]]:
    values = value if isinstance(value, list) else []
    links: list[dict[str, Any]] = []
    for item in values:
        if not isinstance(item, dict):
            continue
        chapter = _positive_int(item.get("trigger_chapter"))
        if total_chapters and chapter > total_chapters:
            raise ValueError("支线交织章节超出全书范围")
        links.append(
            {
                "source_type": str(item.get("source_type") or "").strip(),
                "source_ref": str(item.get("source_ref") or "").strip(),
                "target_subplot": str(item.get("target_subplot") or "主线").strip() or "主线",
                "trigger_chapter": chapter,
                "link_type": str(item.get("link_type") or "").strip(),
                "description": str(item.get("description") or "").strip(),
            }
        )
    return links
