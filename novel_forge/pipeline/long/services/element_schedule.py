"""Cross-chapter rhythm scheduling for narrative elements.

This tracker is intentionally soft: it nudges planning toward due elements and
records schedule violations, but it never blocks generation or archive.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import Field

from novel_forge.core.schemas.base import VersionedSchema
from novel_forge.obs.logger import get_logger

_log = get_logger("pipeline.long.services.element_schedule")


class ElementCurveType(str, Enum):
    PROGRESSIVE = "progressive"
    PULSED = "pulsed"
    CAPPED = "capped"


class ElementCurve(VersionedSchema):
    element_id: str
    curve_type: ElementCurveType = ElementCurveType.PROGRESSIVE
    cadence_chapters: int = Field(default=3, ge=1)
    max_consecutive: int = Field(default=3, ge=1)
    cooldown_chapters: int = Field(default=1, ge=0)
    last_seen_chapter: int = Field(default=0, ge=0)
    seen_chapters: list[int] = Field(default_factory=list)
    consecutive_count: int = Field(default=0, ge=0)
    next_due_chapter: int = Field(default=0, ge=0)
    notes: str = ""


class ElementScheduleViolation(VersionedSchema):
    chapter: int = Field(ge=1)
    element_id: str
    violation_type: str
    message: str
    remediation_hint: str = ""


class ElementScheduleTracker(VersionedSchema):
    schema_version: str = "1.0"
    element_curves: dict[str, ElementCurve] = Field(default_factory=dict)
    last_seen_chapter: dict[str, int] = Field(default_factory=dict)
    mandated_next_chapters: dict[str, list[int]] = Field(default_factory=dict)
    schedule_violations: list[ElementScheduleViolation] = Field(default_factory=list)

    def ensure_curve(self, element_id: str, card: dict[str, Any] | None = None) -> ElementCurve:
        element_id = str(element_id or "").strip()
        curve = self.element_curves.get(element_id)
        inferred = infer_curve_for_element(element_id, card or {})
        if curve is None:
            curve = inferred
            self.element_curves[element_id] = curve
            return curve
        if curve.element_id != element_id:
            curve = curve.model_copy(update={"element_id": element_id})
            self.element_curves[element_id] = curve
        return curve

    def due_items(self, chapter_number: int, *, max_items: int = 3) -> list[ElementCurve]:
        due: list[ElementCurve] = []
        for curve in self.element_curves.values():
            if _is_curve_due(curve, chapter_number):
                due.append(curve)
        due.sort(
            key=lambda curve: (
                curve.next_due_chapter or 10_000,
                -max(0, chapter_number - (curve.last_seen_chapter or 0)),
                curve.element_id,
            )
        )
        return due[: max(0, max_items)]

    def record_chapter(
        self,
        chapter_number: int,
        *,
        element_ids: list[str],
        cards_by_id: dict[str, dict[str, Any]] | None = None,
    ) -> list[ElementScheduleViolation]:
        cards_by_id = cards_by_id or {}
        clean_ids = _unique_ids(element_ids)
        for element_id in clean_ids:
            self.ensure_curve(element_id, cards_by_id.get(element_id))

        current_set = set(clean_ids)
        for curve in list(self.element_curves.values()):
            if curve.element_id in current_set:
                seen = sorted({*curve.seen_chapters, int(chapter_number)})
                prev_seen = curve.last_seen_chapter
                curve.seen_chapters = seen
                curve.last_seen_chapter = int(chapter_number)
                self.last_seen_chapter[curve.element_id] = int(chapter_number)
                if prev_seen == int(chapter_number) - 1:
                    curve.consecutive_count = max(1, curve.consecutive_count + 1)
                else:
                    curve.consecutive_count = 1
                curve.next_due_chapter = int(chapter_number) + curve.cadence_chapters
            elif curve.last_seen_chapter > 0 and curve.next_due_chapter <= int(chapter_number):
                curve.next_due_chapter = curve.last_seen_chapter + curve.cadence_chapters

        violations = self._violations_for_chapter(int(chapter_number), current_set)
        if violations:
            existing = {
                (item.chapter, item.element_id, item.violation_type)
                for item in self.schedule_violations
            }
            for item in violations:
                key = (item.chapter, item.element_id, item.violation_type)
                if key not in existing:
                    self.schedule_violations.append(item)
                    existing.add(key)
            self.schedule_violations = self.schedule_violations[-80:]
        return violations

    def _violations_for_chapter(
        self, chapter_number: int, current_set: set[str]
    ) -> list[ElementScheduleViolation]:
        violations: list[ElementScheduleViolation] = []
        mandated_ids = {
            element_id
            for element_id, chapters in self.mandated_next_chapters.items()
            if chapter_number in set(chapters or [])
        }
        for element_id in sorted(mandated_ids - current_set):
            violations.append(
                ElementScheduleViolation(
                    chapter=chapter_number,
                    element_id=element_id,
                    violation_type="mandated_missing",
                    message=f"{element_id} 已到节奏计划要求章，但本章未进入 element_focus。",
                    remediation_hint="下一章优先纳入 element_focus，并落实为具体动作、对白或信息推进。",
                )
            )
        for curve in self.element_curves.values():
            if (
                curve.curve_type == ElementCurveType.CAPPED
                and curve.consecutive_count > curve.max_consecutive
            ):
                violations.append(
                    ElementScheduleViolation(
                        chapter=chapter_number,
                        element_id=curve.element_id,
                        violation_type="capped_overrun",
                        message=(
                            f"{curve.element_id} 已连续 {curve.consecutive_count} 章出现，"
                            f"超过上限 {curve.max_consecutive} 章。"
                        ),
                        remediation_hint=f"后续至少冷却 {curve.cooldown_chapters} 章，转向其他要素。",
                    )
                )
        return violations


_PROGRESSIVE_HINTS = (
    "mystery_reveal_order",
    "mystery_clue_ledger",
    "progression_power_curve",
    "progression_bottleneck_break",
    "scifi_rule_reveal",
    "foreshadowing_thread",
    "editorial_revelation_ladder",
    "relationship_trust_arc",
    "relationship_power_shift",
    "theme_question_progression",
)
_PULSED_HINTS = (
    "romance_sweet_bitter_ratio",
    "healing_daily_ritual",
    "literary_silence_subtext",
    "pacing_tension_release",
)
_CAPPED_HINTS = (
    "comedy_setup_payoff",
    "horror_dread_rhythm",
    "thriller_countdown",
    "pacing_breath_window",
)


def _schedule_path(layout: Any) -> Any:
    path = getattr(layout, "element_schedule_path", None)
    if path is not None:
        return path
    return layout.plans_dir / "element_schedule.json"


def _unique_ids(value: Any, *, max_items: int = 64) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for raw in list(value or []):
        item = str(raw or "").strip()
        if not item or item in seen:
            continue
        seen.add(item)
        result.append(item)
        if len(result) >= max_items:
            break
    return result


def infer_curve_for_element(element_id: str, card: dict[str, Any] | None = None) -> ElementCurve:
    card = card or {}
    category = str(card.get("category", "") or "")
    if element_id in _PULSED_HINTS or "言情" in category or "文学" in category:
        return ElementCurve(
            element_id=element_id,
            curve_type=ElementCurveType.PULSED,
            cadence_chapters=3,
            max_consecutive=2,
            cooldown_chapters=1,
            notes="脉冲型：保持周期性出现，避免连续同质情绪。",
        )
    if (
        element_id in _CAPPED_HINTS
        or "喜剧" in category
        or "恐怖" in category
        or "惊悚" in category
    ):
        return ElementCurve(
            element_id=element_id,
            curve_type=ElementCurveType.CAPPED,
            cadence_chapters=4,
            max_consecutive=2,
            cooldown_chapters=2,
            notes="封顶型：控制连续出现次数，避免疲劳。",
        )
    if element_id in _PROGRESSIVE_HINTS or "悬疑" in category or "升级" in category:
        return ElementCurve(
            element_id=element_id,
            curve_type=ElementCurveType.PROGRESSIVE,
            cadence_chapters=3,
            max_consecutive=3,
            cooldown_chapters=1,
            notes="递进型：若长期未出现，需要进入下一章推进。",
        )
    return ElementCurve(
        element_id=element_id,
        curve_type=ElementCurveType.PROGRESSIVE,
        cadence_chapters=4,
        max_consecutive=3,
        cooldown_chapters=1,
        notes="默认递进型：周期性轻触即可。",
    )


def _is_curve_due(curve: ElementCurve, chapter_number: int) -> bool:
    chapter_number = int(chapter_number)
    if curve.curve_type == ElementCurveType.CAPPED:
        return False
    if curve.last_seen_chapter <= 0:
        return chapter_number >= curve.cadence_chapters
    gap = chapter_number - curve.last_seen_chapter
    return gap >= curve.cadence_chapters


def load_element_schedule(
    storage: Any,
    layout: Any,
    *,
    element_ids: list[str] | None = None,
    cards_by_id: dict[str, dict[str, Any]] | None = None,
) -> ElementScheduleTracker:
    path = _schedule_path(layout)
    payload: dict[str, Any] = {}
    try:
        if storage.exists(path):
            raw = storage.load_json(path)
            payload = raw if isinstance(raw, dict) else {}
    except Exception as exc:
        _log.warning("element_schedule_load_failed | error=%s", exc)
        payload = {}

    try:
        tracker = (
            ElementScheduleTracker.model_validate(payload) if payload else ElementScheduleTracker()
        )
    except Exception as exc:
        _log.warning("element_schedule_validate_failed | error=%s", exc)
        tracker = ElementScheduleTracker()

    cards_by_id = cards_by_id or {}
    for element_id in _unique_ids(element_ids or []):
        tracker.ensure_curve(element_id, cards_by_id.get(element_id))
    return tracker


def save_element_schedule(storage: Any, layout: Any, tracker: ElementScheduleTracker) -> None:
    storage.save_json(_schedule_path(layout), tracker.model_dump(mode="json"))


def build_element_schedule_hint(
    storage: Any,
    layout: Any,
    *,
    chapter_number: int,
    extension_ids: list[str],
    cards_by_id: dict[str, dict[str, Any]] | None = None,
    max_items: int = 3,
) -> dict[str, Any] | None:
    tracker = load_element_schedule(
        storage,
        layout,
        element_ids=extension_ids,
        cards_by_id=cards_by_id,
    )
    due = tracker.due_items(chapter_number, max_items=max_items)
    if due:
        for curve in due:
            chapters = set(tracker.mandated_next_chapters.get(curve.element_id, []))
            chapters.add(int(chapter_number))
            tracker.mandated_next_chapters[curve.element_id] = sorted(chapters)
        save_element_schedule(storage, layout, tracker)
    recent_violations = [
        item.model_dump(mode="json")
        for item in tracker.schedule_violations
        if int(item.chapter) < int(chapter_number)
    ][-max_items:]
    if not due and not recent_violations:
        return None

    due_items = [
        {
            "element_id": curve.element_id,
            "curve_type": curve.curve_type.value,
            "last_seen_chapter": curve.last_seen_chapter,
            "cadence_chapters": curve.cadence_chapters,
            "reason": _due_reason(curve, chapter_number),
            "remediation_hint": _remediation_for_curve(curve),
        }
        for curve in due
    ]
    mandated = [item["element_id"] for item in due_items]
    return {
        "summary": _summary_for_due(due_items, recent_violations),
        "mandated_focus_ids": mandated,
        "recommended_focus_ids": mandated,
        "schedule_due": due_items,
        "schedule_violations": recent_violations,
    }


def update_element_schedule_from_progress(
    storage: Any,
    layout: Any,
    *,
    chapter_number: int,
    progress_entry: dict[str, Any] | None,
    element_cards_by_id: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    if not isinstance(progress_entry, dict):
        return None
    scheduled_ids = _unique_ids(progress_entry.get("scheduled_element_ids", []), max_items=8)
    result_items = [
        item for item in list(progress_entry.get("results", []) or []) if isinstance(item, dict)
    ]
    hit_or_weak_ids = [
        str(item.get("element_id", "") or "").strip()
        for item in result_items
        if str(item.get("status", "") or "").strip().lower() in {"hit", "weak"}
    ]
    applied_ids = _unique_ids(hit_or_weak_ids if result_items else scheduled_ids, max_items=8)
    tracker = load_element_schedule(
        storage,
        layout,
        element_ids=list((element_cards_by_id or {}).keys()),
        cards_by_id=element_cards_by_id,
    )
    violations = tracker.record_chapter(
        chapter_number,
        element_ids=applied_ids,
        cards_by_id=element_cards_by_id,
    )
    if not applied_ids and not violations:
        return None
    save_element_schedule(storage, layout, tracker)
    return {
        "chapter": int(chapter_number),
        "recorded_element_ids": applied_ids,
        "violations": [item.model_dump(mode="json") for item in violations],
    }


def merge_schedule_hint_into_progress_hint(
    progress_hint: dict[str, Any] | None,
    schedule_hint: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if not isinstance(schedule_hint, dict):
        return progress_hint
    merged: dict[str, Any] = dict(progress_hint or {})
    summary_parts = [
        str(merged.get("summary", "") or "").strip(),
        str(schedule_hint.get("summary", "") or "").strip(),
    ]
    merged["summary"] = "；".join(part for part in summary_parts if part)
    for key in ("mandated_focus_ids", "recommended_focus_ids"):
        merged[key] = _unique_ids(
            list(schedule_hint.get(key, []) or []) + list(merged.get(key, []) or []),
            max_items=5,
        )
    for key in ("schedule_due", "schedule_violations"):
        merged[key] = list(schedule_hint.get(key, []) or []) + list(merged.get(key, []) or [])
    return merged


def _due_reason(curve: ElementCurve, chapter_number: int) -> str:
    if curve.last_seen_chapter <= 0:
        return f"该要素尚未在前文落实，第 {chapter_number} 章已到首次推进窗口。"
    gap = int(chapter_number) - int(curve.last_seen_chapter)
    return f"该要素已间隔 {gap} 章未推进，超过节奏间隔 {curve.cadence_chapters} 章。"


def _remediation_for_curve(curve: ElementCurve) -> str:
    if curve.curve_type == ElementCurveType.PULSED:
        return "安排一次轻量但可见的情绪/关系/氛围脉冲，避免同质重复。"
    if curve.curve_type == ElementCurveType.CAPPED:
        return "降低该要素密度，转向其他叙事机制，待冷却后再回收。"
    return "安排一次具体推进：线索、能力、关系或信息必须产生新后果。"


def _summary_for_due(due_items: list[dict[str, Any]], violations: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    if due_items:
        parts.append(
            "要素节奏到期："
            + "、".join(
                str(item.get("element_id", "")) for item in due_items if item.get("element_id")
            )
        )
    if violations:
        parts.append(f"存在 {len(violations)} 条历史节奏偏差，需在本章或下章修正")
    return "；".join(parts)


__all__ = [
    "ElementCurve",
    "ElementCurveType",
    "ElementScheduleTracker",
    "ElementScheduleViolation",
    "build_element_schedule_hint",
    "infer_curve_for_element",
    "load_element_schedule",
    "merge_schedule_hint_into_progress_hint",
    "save_element_schedule",
    "update_element_schedule_from_progress",
]
