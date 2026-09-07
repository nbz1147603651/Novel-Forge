"""追读力时间线窗口管理器 — 跨章节悬念追踪与追读力窗口管理。

Manages a sliding window of chapters to track suspense timelines,
hook alternation patterns, tension curves, and strand distribution.
Provides planning hints and execution constraints for upcoming chapters.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from novel_forge.core.schemas.reading_power import HookType, ReadingPowerReport
from novel_forge.core.schemas.reading_power_timeline_window_report import (
    NextChapterConstraints as PydanticNextChapterConstraints,
)
from novel_forge.core.schemas.reading_power_timeline_window_report import (
    ReadingPowerTimelineWindowReport as PydanticTimelineWindowReport,
)
from novel_forge.core.schemas.reading_power_timeline_window_report import (
    TimelineDeviationAlert,
)
from novel_forge.core.schemas.reading_power_window_config import ReadingPowerWindowConfig
from novel_forge.core.schemas.suspense_timeline_entry import SuspenseTimelineEntry
from novel_forge.obs.logger import get_logger

_logger = get_logger("pipeline.services.reading_power_window")

_HOOK_STRAND_AFFINITY: dict[str, dict[str, float]] = {
    "crisis": {"quest": 0.8, "fire": 0.1, "constellation": 0.1},
    "mystery": {"quest": 0.3, "fire": 0.1, "constellation": 0.6},
    "emotion": {"quest": 0.1, "fire": 0.8, "constellation": 0.1},
    "choice": {"quest": 0.4, "fire": 0.3, "constellation": 0.3},
    "desire": {"quest": 0.3, "fire": 0.4, "constellation": 0.3},
    "none": {"quest": 0.33, "fire": 0.33, "constellation": 0.34},
}

_HOOK_TYPE_ALIASES: dict[str, str] = {
    "suspense": "mystery",
    "revelation": "mystery",
    "悬念": "mystery",
    "谜团": "mystery",
    "揭示": "mystery",
    "危机": "crisis",
    "情绪": "emotion",
    "选择": "choice",
    "期待": "desire",
}


@dataclass
class NextChapterConstraints:
    recommended_hook_type: str = ""
    force_resolve_suspense_ids: list[str] = field(default_factory=list)
    suspense_to_resolve: list[dict[str, Any]] = field(default_factory=list)
    strand_recommendation: str = ""
    tension_target: float = 5.0
    turning_point_warning: str = ""
    alternation_warning: str = ""
    execution_score: float = 0.0
    suspense_chain_score: float = 0.0
    alerts: list[str] = field(default_factory=list)


@dataclass
class ReadingPowerTimelineWindowReport:
    current_chapter: int = 0
    window_start: int = 0
    window_end: int = 0
    phase_name: str = ""
    phase_tension: float = 5.0

    inherited_suspense: list[dict[str, Any]] = field(default_factory=list)
    suspense_by_urgency: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    force_resolve_suspense: list[dict[str, Any]] = field(default_factory=list)

    hook_type_history: list[str] = field(default_factory=list)
    hook_type_sequence: list[str] = field(default_factory=list)
    recommended_hook_type: str = ""
    alternation_score: float = 0.0
    consecutive_same_hook: int = 0

    tension_history: list[float] = field(default_factory=list)
    current_tension: float = 5.0
    tension_deviation: float = 0.0

    strand_distribution: dict[str, str] = field(default_factory=dict)
    strand_history: list[dict[str, Any]] = field(default_factory=list)

    main_plot_progress: dict[str, Any] = field(default_factory=dict)
    subplot_progress: dict[str, Any] = field(default_factory=dict)

    turning_point_approaching: bool = False
    turning_point_distance: int = 0
    turning_point_type: str = ""

    timeline_execution_score: float = 0.0
    suspense_chain_score: float = 0.0
    composite_score: float = 0.0

    alerts: list[str] = field(default_factory=list)

    element_progress_summary: str = ""


class ReadingPowerTimelineWindowManager:
    def __init__(
        self,
        blueprint: Any,
        storage: Any,
        layout: Any,
        config: ReadingPowerWindowConfig,
    ) -> None:
        self._blueprint = blueprint
        self._storage = storage
        self._layout = layout
        self._config = config

        self._suspense_timeline: list[SuspenseTimelineEntry] = []
        self._hook_type_history: list[dict[str, Any]] = []
        self._reading_power_history: list[dict[str, Any]] = []
        self._strand_history: list[dict[str, Any]] = []
        self._main_plot_history: list[dict[str, Any]] = []
        self._subplot_history: list[dict[str, Any]] = []

        self._init_suspense_timeline_from_blueprint()
        self._derive_suspense_from_outlines()
        self._init_strand_tracking()
        self._load_timeline_state()

        self._last_window_report: ReadingPowerTimelineWindowReport | None = None
        self._last_pydantic_report: PydanticTimelineWindowReport | None = None
        self._last_next_chapter_constraints_pydantic: PydanticNextChapterConstraints | None = None

    # ── Initialisation ───────────────────────────────────────────────────

    @staticmethod
    def _extract_item_field(item: Any, field_name: str, default: Any = None) -> Any:
        """Extract a field from an item that may be a dict or Pydantic model."""
        if isinstance(item, dict):
            return item.get(field_name, default)
        if hasattr(item, field_name):
            val = getattr(item, field_name, default)
            return val if val is not None else default
        if hasattr(item, "model_dump"):
            return item.model_dump(mode="json").get(field_name, default)
        return default

    @classmethod
    def _get_field(cls, item: Any, field_name: str, default: Any = None) -> Any:
        return cls._extract_item_field(item, field_name, default)

    @staticmethod
    def _normalise_hook_type(raw: Any) -> str:
        value = str(raw or "").strip().lower()
        value = _HOOK_TYPE_ALIASES.get(value, value)
        return value if value in {h.value for h in HookType} else "mystery"

    @staticmethod
    def _normalise_hook_strength(raw: Any) -> str:
        value = str(raw or "medium").strip().lower()
        return value if value in {"strong", "medium", "weak"} else "medium"

    def _load_outline_chapters(self) -> list[Any]:
        """Load chapter outlines from blueprint object or project outline file."""
        chapters = self._get_field(self._blueprint, "chapters", None)
        if chapters:
            return list(chapters)

        outline = self._get_field(self._blueprint, "outline", None)
        if outline is not None:
            chapters = self._get_field(outline, "chapters", None)
            if chapters:
                return list(chapters)

        outline_path = getattr(self._layout, "outline_path", None)
        if outline_path is not None:
            try:
                storage_exists = getattr(self._storage, "exists", None)
                exists = (
                    storage_exists(outline_path)
                    if callable(storage_exists)
                    else outline_path.exists()
                )
                if exists:
                    raw = self._storage.load_json(outline_path)
                    chapters = raw.get("chapters", []) if isinstance(raw, dict) else []
                    if isinstance(chapters, list):
                        return chapters
            except Exception as exc:
                _logger.debug("outline load for reading power failed: %s", exc)

        return []

    def _init_suspense_timeline_from_blueprint(self) -> None:
        try:
            schedule = self._get_field(self._blueprint, "suspense_schedule", None)
            if schedule is None:
                _logger.info("suspense_schedule not found on blueprint; timeline starts empty")
                return
            if not isinstance(schedule, list):
                _logger.warning("suspense_schedule is not a list; skipping initialisation")
                return

            for item in schedule:
                try:
                    # SuspenseScheduleItem uses introduce_chapter/resolve_chapter;
                    # legacy dicts may use setup_chapter/planned_resolution_chapter.
                    setup_chapter = self._extract_item_field(
                        item, "setup_chapter", None
                    ) or self._extract_item_field(item, "introduce_chapter", 1)
                    resolve_chapter = self._extract_item_field(
                        item, "planned_resolution_chapter", None
                    ) or self._extract_item_field(item, "resolve_chapter", None)

                    suspense_id = str(
                        self._extract_item_field(
                            item, "suspense_id", f"s_{len(self._suspense_timeline)}"
                        )
                    )
                    suspense_type_raw = self._extract_item_field(item, "suspense_type", "mystery")
                    suspense_type = HookType(self._normalise_hook_type(suspense_type_raw))

                    entry = SuspenseTimelineEntry(
                        suspense_id=suspense_id,
                        suspense_type=suspense_type,
                        suspense_description=str(
                            self._extract_item_field(item, "description", "")
                            or self._extract_item_field(item, "suspense_description", "")
                        ),
                        setup_chapter=int(setup_chapter),
                        setup_strength=str(
                            self._extract_item_field(item, "setup_strength", "medium")
                        ),
                        setup_phase=str(self._extract_item_field(item, "setup_phase", "middle")),
                        setup_tension_expected=float(
                            self._extract_item_field(item, "setup_tension_expected", 5.0)
                        ),
                        planned_resolution_chapter=int(resolve_chapter)
                        if resolve_chapter
                        else None,
                        resolution_window_start=self._extract_item_field(
                            item, "resolution_window_start"
                        ),
                        resolution_window_end=self._extract_item_field(
                            item, "resolution_window_end"
                        ),
                        related_main_plot_point=self._extract_item_field(
                            item, "related_main_plot_point"
                        ),
                        related_subplot_id=self._extract_item_field(item, "related_subplot_id"),
                        strand_affinity=self._extract_item_field(item, "strand_affinity", {}) or {},
                        related_element_ids=list(
                            self._extract_item_field(item, "related_element_ids", []) or []
                        ),
                    )
                    self._suspense_timeline.append(entry)
                except Exception as exc:
                    _logger.warning("failed to parse suspense_schedule item: %s", exc)

            self._suspense_timeline.sort(key=lambda e: e.setup_chapter)
            _logger.info(
                "suspense_timeline initialised with %d entries from blueprint",
                len(self._suspense_timeline),
            )
        except Exception as exc:
            _logger.warning("_init_suspense_timeline_from_blueprint failed: %s", exc)

    def _derive_suspense_from_outlines(self) -> None:
        if self._suspense_timeline:
            return
        try:
            chapters = self._load_outline_chapters()
            if not chapters:
                return

            existing_ids: set[str] = set()
            new_entries = 0

            for ch_outline in chapters:
                if ch_outline is None:
                    continue
                chapter_num = int(self._get_field(ch_outline, "chapter_number", 0) or 0)
                if chapter_num < 1:
                    continue

                expected_hook = self._get_field(ch_outline, "expected_hook", None)
                if expected_hook is None:
                    continue

                hook_type_str = self._normalise_hook_type(
                    self._get_field(expected_hook, "hook_type", "")
                )
                hook_strength = self._normalise_hook_strength(
                    self._get_field(expected_hook, "hook_strength", "medium")
                )
                hook_description = str(self._get_field(expected_hook, "hook_description", "") or "")

                if not hook_type_str or hook_type_str == "none":
                    continue

                suspense_id = f"s_ch{chapter_num}_{hook_type_str}"
                if suspense_id in existing_ids:
                    continue

                expected_payoffs = self._get_field(ch_outline, "expected_payoffs", []) or []
                related_element_ids = []
                for payoff in expected_payoffs:
                    pt = self._get_field(payoff, "payoff_type", None)
                    if pt:
                        related_element_ids.append(pt)

                affinity = self._infer_strand_affinity(hook_type_str)
                resolve_ch = chapter_num + 3

                entry = SuspenseTimelineEntry(
                    suspense_id=suspense_id,
                    suspense_type=HookType(hook_type_str),
                    suspense_description=hook_description
                    or f"第{chapter_num}章{hook_type_str}型钩子",
                    setup_chapter=chapter_num,
                    setup_strength=hook_strength,
                    setup_phase="closing",
                    setup_tension_expected=self._infer_tension_from_hook(
                        hook_type_str, hook_strength, {"micro_payoffs": []}
                    ),
                    planned_resolution_chapter=resolve_ch,
                    resolution_window_start=chapter_num + 1,
                    resolution_window_end=chapter_num + 5,
                    strand_affinity=affinity,
                    related_element_ids=related_element_ids,
                )
                self._suspense_timeline.append(entry)
                existing_ids.add(suspense_id)
                new_entries += 1

            if new_entries > 0:
                self._suspense_timeline.sort(key=lambda e: e.setup_chapter)
                _logger.info("derived %d suspense entries from chapter outlines", new_entries)
        except Exception as exc:
            _logger.warning("_derive_suspense_from_outlines failed: %s", exc)

    def _init_strand_tracking(self) -> None:
        try:
            subplot_plan = self._get_field(self._blueprint, "subplot_plan", None)
            if subplot_plan is None:
                return
            if not isinstance(subplot_plan, list):
                return

            for sp in subplot_plan:
                name = self._get_field(sp, "name", "") or ""
                if not name:
                    continue
                involved = list(self._get_field(sp, "involved_chapters", []) or [])
                self._subplot_history.append(
                    {
                        "subplot_name": name,
                        "involved_chapters": involved,
                        "priority": self._get_field(sp, "priority", "normal"),
                        "resolution_chapter": self._get_field(sp, "resolution_chapter", 0),
                    }
                )

            _logger.info("strand_tracking initialised with %d subplots", len(self._subplot_history))
        except Exception as exc:
            _logger.warning("_init_strand_tracking failed: %s", exc)

    def _drop_chapter_observations(self, chapter: int) -> None:
        """Remove prior derived observations for a chapter before re-evaluating it."""
        for attr_name in (
            "_hook_type_history",
            "_reading_power_history",
            "_strand_history",
            "_main_plot_history",
            "_subplot_history",
        ):
            history = getattr(self, attr_name, [])
            if isinstance(history, list):
                setattr(
                    self,
                    attr_name,
                    [item for item in history if item.get("chapter") != chapter],
                )

        auto_prefix = f"s_{chapter}_"
        self._suspense_timeline = [
            entry
            for entry in self._suspense_timeline
            if not (
                entry.setup_chapter == chapter
                and str(entry.suspense_id).startswith(auto_prefix)
                and not entry.resolution_executed
            )
        ]

    def _get_phase_for_chapter(self, chapter: int) -> str:
        try:
            phases = self._get_field(self._blueprint, "narrative_phases", []) or []
            for phase in phases:
                start = int(self._get_field(phase, "chapter_start", 1) or 1)
                end = int(self._get_field(phase, "chapter_end", 9999) or 9999)
                if start <= chapter <= end:
                    return str(self._get_field(phase, "phase_name", "unknown"))
        except Exception as exc:
            _logger.warning("_get_phase_for_chapter failed for chapter %d: %s", chapter, exc)
        return "unknown"

    def _get_tension_for_chapter(self, chapter: int) -> float:
        try:
            phases = self._get_field(self._blueprint, "narrative_phases", []) or []
            for phase in phases:
                start = int(self._get_field(phase, "chapter_start", 1) or 1)
                end = int(self._get_field(phase, "chapter_end", 9999) or 9999)
                if start <= chapter <= end:
                    tension_str = self._get_field(phase, "tension_level", "")
                    return self._parse_tension_level(tension_str, chapter, start, end)
        except Exception as exc:
            _logger.warning("_get_tension_for_chapter failed for chapter %d: %s", chapter, exc)
        return 5.0

    @staticmethod
    def _parse_tension_level(
        tension_str: str,
        chapter: int,
        phase_start: int,
        phase_end: int,
    ) -> float:
        tension_lower = (tension_str or "").lower()
        tension_map: dict[str, float] = {
            "very_low": 1.0,
            "low": 3.0,
            "medium": 5.0,
            "high": 7.0,
            "very_high": 9.0,
            "climax": 10.0,
            "rising": 6.0,
            "falling": 4.0,
            "plateau": 5.0,
        }
        for key, value in tension_map.items():
            if key in tension_lower:
                return value

        if phase_end > phase_start:
            progress = (chapter - phase_start) / (phase_end - phase_start)
            return round(2.0 + progress * 6.0, 1)
        return 5.0

    @staticmethod
    def _infer_strand_affinity(hook_type: str) -> dict[str, float]:
        return _HOOK_STRAND_AFFINITY.get(hook_type, _HOOK_STRAND_AFFINITY["none"])

    @staticmethod
    def _has_text_overlap(source: str, target: str, *, min_ngram: int = 4) -> bool:
        source_norm = "".join(str(source or "").lower().split())
        target_norm = "".join(str(target or "").lower().split())
        if not source_norm or not target_norm:
            return False
        if source_norm in target_norm or target_norm in source_norm:
            return True
        if len(source_norm) < min_ngram:
            return source_norm in target_norm
        return any(
            source_norm[i : i + min_ngram] in target_norm
            for i in range(0, max(1, len(source_norm) - min_ngram + 1))
        )

    def _is_turning_point_chapter(self, chapter: int) -> bool:
        turning_points = self._get_field(self._blueprint, "key_turning_points", []) or []
        for item in turning_points:
            if int(self._get_field(item, "chapter_number", 0) or 0) == chapter:
                return True
        return False

    def _synthesise_expected_hook(
        self,
        current_chapter: int,
        chapter_outline: Any,
        recommended_hook_type: str,
    ) -> dict[str, Any] | None:
        if chapter_outline is None:
            return None

        goal = str(self._get_field(chapter_outline, "goal", "") or "")
        notes = str(self._get_field(chapter_outline, "notes", "") or "")
        main_points = [
            str(item).strip()
            for item in list(self._get_field(chapter_outline, "main_plot_points", []) or [])
            if str(item).strip()
        ]
        subplot_points = [
            str(item).strip()
            for item in list(self._get_field(chapter_outline, "subplot_points", []) or [])
            if str(item).strip()
        ]
        subplot_focus = str(self._get_field(chapter_outline, "subplot_focus", "") or "")
        source_text = "；".join(
            [goal, notes, *main_points[:2], *subplot_points[:1], subplot_focus]
        ).strip("；")
        if not source_text:
            return None

        lowered = source_text.lower()
        hook_type = recommended_hook_type or "mystery"
        if any(k in source_text for k in ("危机", "刺杀", "追杀", "暴露", "崩", "死", "险")):
            hook_type = "crisis"
        elif any(k in source_text for k in ("选择", "抉择", "两难", "是否", "该不该")):
            hook_type = "choice"
        elif any(k in lowered for k in ("mystery", "secret", "truth")) or any(
            k in source_text for k in ("谜", "真相", "线索", "身份", "为何", "秘密", "旧案")
        ):
            hook_type = "mystery"
        elif any(
            k in source_text for k in ("心动", "心疼", "决裂", "信任", "背叛", "情感", "关系")
        ):
            hook_type = "emotion"
        elif any(k in source_text for k in ("期待", "将获", "即将", "机会", "晋升")):
            hook_type = "desire"

        strength = "strong" if self._is_turning_point_chapter(current_chapter) else "medium"
        phase_tension = self._get_tension_for_chapter(current_chapter)
        if phase_tension >= 7.0:
            strength = "strong"
        elif phase_tension <= 3.5:
            strength = "medium"

        hook_desc = main_points[-1] if main_points else goal or source_text
        return {
            "hook_type": self._normalise_hook_type(hook_type),
            "hook_strength": strength,
            "hook_description": f"{hook_desc}，章末留下可驱动下一章的具体追问或行动压力",
            "source": "auto_synthesised_from_outline_blueprint",
        }

    def _synthesise_expected_payoffs(
        self,
        chapter_outline: Any,
        *,
        min_payoffs: int = 2,
    ) -> list[dict[str, Any]]:
        if chapter_outline is None:
            return []
        candidates: list[tuple[str, str]] = []
        for point in list(self._get_field(chapter_outline, "main_plot_points", []) or []):
            text = str(point or "").strip()
            if not text:
                continue
            payoff_type = "information"
            if any(k in text for k in ("关系", "信任", "情感", "对立", "和解", "决裂")):
                payoff_type = "relationship"
            elif any(k in text for k in ("能力", "算学", "破局", "展示", "胜过")):
                payoff_type = "ability"
            elif any(k in text for k in ("线索", "伏笔", "旧案", "笔迹", "证据")):
                payoff_type = "clue"
            candidates.append((payoff_type, text))
        for point in list(self._get_field(chapter_outline, "subplot_points", []) or []):
            text = str(point or "").strip()
            if text:
                candidates.append(("relationship", text))

        payoffs: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        for payoff_type, desc in candidates:
            key = (payoff_type, desc)
            if key in seen:
                continue
            seen.add(key)
            payoffs.append(
                {
                    "payoff_type": payoff_type,
                    "description": desc,
                    "strength": "medium",
                    "source": "auto_synthesised_from_outline",
                }
            )
            if len(payoffs) >= max(1, min_payoffs):
                break
        return payoffs

    # ── Persistence ──────────────────────────────────────────────────────

    def _load_timeline_state(self) -> None:
        try:
            path = self._layout.plans_dir / "reading_power_timeline_state.json"
            if not self._storage.exists(path):
                return
            data = self._storage.load_json(path)
            if not isinstance(data, dict):
                return

            tl_entries = data.get("suspense_timeline", [])
            if isinstance(tl_entries, list):
                for item in tl_entries:
                    if isinstance(item, dict):
                        try:
                            entry = SuspenseTimelineEntry(**item)
                            existing_ids = {e.suspense_id for e in self._suspense_timeline}
                            if entry.suspense_id not in existing_ids:
                                self._suspense_timeline.append(entry)
                        except Exception as exc:
                            _logger.warning("failed to restore suspense entry: %s", exc)

            for key, target in [
                ("hook_type_history", self._hook_type_history),
                ("reading_power_history", self._reading_power_history),
                ("strand_history", self._strand_history),
                ("main_plot_history", self._main_plot_history),
                ("subplot_history", self._subplot_history),
            ]:
                stored = data.get(key)
                if isinstance(stored, list):
                    target.extend(stored)

            self._suspense_timeline.sort(key=lambda e: e.setup_chapter)
            _logger.info("timeline state loaded from disk")
        except Exception as exc:
            _logger.warning("_load_timeline_state failed: %s", exc)

    def save_timeline_state(self) -> None:
        try:
            path = self._layout.plans_dir / "reading_power_timeline_state.json"
            data: dict[str, Any] = {
                "schema_version": "1.0",
                "suspense_timeline": [e.model_dump(mode="json") for e in self._suspense_timeline],
                "hook_type_history": self._hook_type_history,
                "reading_power_history": self._reading_power_history,
                "strand_history": self._strand_history,
                "main_plot_history": self._main_plot_history,
                "subplot_history": self._subplot_history,
            }
            self._storage.save_json(path, data)
            _logger.info("timeline state saved to disk")
        except Exception as exc:
            _logger.warning("save_timeline_state failed: %s", exc)

    # ── Core public methods ──────────────────────────────────────────────

    def build_reading_power_hint(
        self,
        current_chapter: int,
        chapter_outline: Any,
        strand_hint: str = "",
        element_progress_hint: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Build a reading-power hint for the planning phase.

        Returns a dict suitable for injection into planning prompt context.
        """
        try:
            inherited = self._collect_inherited_suspense(current_chapter)
            by_urgency = self._group_by_urgency(inherited, current_chapter)
            force_resolve = self._get_force_resolve_list(current_chapter)

            hook_sequence = [
                h.get("hook_type", "none")
                for h in self._hook_type_history[-self._config.window_size :]
            ]
            recommended = self._recommend_hook_type(hook_sequence)

            element_summary = ""
            if element_progress_hint:
                rec_ids = element_progress_hint.get("recommended_focus_ids", [])
                if rec_ids:
                    element_summary = f"建议优先关注叙事要素: {', '.join(rec_ids)}"

            strand_dist: dict[str, str] = {}
            if self._strand_history:
                recent = self._strand_history[-self._config.window_size :]
                counts: dict[str, float] = {"quest": 0.0, "fire": 0.0, "constellation": 0.0}
                for entry in recent:
                    dom = entry.get("dominant", "quest")
                    counts[dom] = counts.get(dom, 0.0) + 1.0
                    for sec in entry.get("secondary", []):
                        counts[sec] = counts.get(sec, 0.0) + 0.5
                total = sum(counts.values()) or 1.0
                strand_dist = {k: f"{v / total * 100:.0f}%" for k, v in counts.items()}

            # ── Load previous chapter's suggestions for closed-loop feedback ──
            prev_suggestions: list[str] = []
            prev_overall_score: float | None = None
            if current_chapter > 1:
                try:
                    prev_rp_path = self._layout.reading_power_report_path(current_chapter - 1)
                    storage_exists = getattr(self._storage, "exists", None)
                    prev_exists = (
                        storage_exists(prev_rp_path)
                        if callable(storage_exists)
                        else prev_rp_path.exists()
                    )
                    prev_rp_data = self._storage.load_json(prev_rp_path) if prev_exists else None
                    if prev_rp_data and not self._is_fallback_rp_report(prev_rp_data):
                        prev_suggestions = prev_rp_data.get("suggestions", [])
                        prev_overall_score = prev_rp_data.get("overall_score")
                except Exception:
                    pass

            # ── Extract chapter outline expectations (窗口中心：执行验证依据) ──
            # 章节大纲规划的钩子和微兑现预期 → 窗口中心约束
            outline_expected_hook: dict[str, Any] | None = None
            outline_expected_payoffs: list[dict[str, Any]] = []
            outline_main_plot_points: list[str] = []
            outline_subplot_points: list[str] = []
            if chapter_outline is not None:
                try:
                    # 提取大纲钩子预期
                    hook_plan = self._get_field(chapter_outline, "expected_hook", None)
                    if hook_plan is not None:
                        if hasattr(hook_plan, "model_dump"):
                            outline_expected_hook = hook_plan.model_dump(mode="json")
                        elif isinstance(hook_plan, dict):
                            outline_expected_hook = hook_plan
                        else:
                            outline_expected_hook = {
                                "hook_type": getattr(hook_plan, "hook_type", ""),
                                "hook_strength": getattr(hook_plan, "hook_strength", ""),
                                "hook_description": getattr(hook_plan, "hook_description", ""),
                            }

                    # 提取大纲微兑现预期
                    payoff_plans = list(
                        self._get_field(chapter_outline, "expected_payoffs", []) or []
                    )
                    for pp in payoff_plans:
                        if hasattr(pp, "model_dump"):
                            outline_expected_payoffs.append(pp.model_dump(mode="json"))
                        elif isinstance(pp, dict):
                            outline_expected_payoffs.append(pp)
                        else:
                            outline_expected_payoffs.append(
                                {
                                    "payoff_type": getattr(pp, "payoff_type", ""),
                                    "description": getattr(pp, "description", ""),
                                }
                            )

                    # 提取主线/支线推进点（用于执行验证）
                    outline_main_plot_points = list(
                        self._get_field(chapter_outline, "main_plot_points", []) or []
                    )
                    outline_subplot_points = list(
                        self._get_field(chapter_outline, "subplot_points", []) or []
                    )
                    outline_subplot_focus = str(
                        self._get_field(chapter_outline, "subplot_focus", "") or ""
                    )
                except Exception as outline_exc:
                    _logger.warning(
                        "extract outline expectations failed | chapter=%d | error=%s",
                        current_chapter,
                        outline_exc,
                    )

            # ── 大纲钩子优先，历史推荐作为备选（窗口右边界约束） ──
            # 如果大纲规划了钩子类型，则使用大纲预期；否则使用历史推算
            final_hook_recommendation = recommended
            outline_hook_override = False
            if outline_expected_hook and outline_expected_hook.get("hook_type"):
                outline_expected_hook["hook_type"] = self._normalise_hook_type(
                    outline_expected_hook.get("hook_type")
                )
                outline_expected_hook["hook_strength"] = self._normalise_hook_strength(
                    outline_expected_hook.get("hook_strength")
                )
                final_hook_recommendation = outline_expected_hook.get("hook_type", "")
                outline_hook_override = True
            else:
                outline_expected_hook = self._synthesise_expected_hook(
                    current_chapter,
                    chapter_outline,
                    recommended,
                )
                if outline_expected_hook:
                    final_hook_recommendation = outline_expected_hook.get("hook_type", recommended)

            if not outline_expected_payoffs:
                outline_expected_payoffs = self._synthesise_expected_payoffs(
                    chapter_outline,
                    min_payoffs=2,
                )

            # ── 跨章节追读力趋势 ──
            reading_power_trend = self.compute_reading_power_trend()

            return {
                # ── 窗口左边界：继承悬念（来自大纲悬念时间表） ──
                "inherited_suspense": inherited,
                "suspense_by_urgency": by_urgency,
                "force_resolve_suspense": force_resolve,
                "forced_pending_questions": [
                    s.get("description", "") for s in force_resolve if s.get("description")
                ],
                # ── 窗口中心：章节大纲规划（执行验证依据） ──
                "outline_expected_hook": outline_expected_hook,
                "outline_expected_payoffs": outline_expected_payoffs,
                "outline_main_plot_points": outline_main_plot_points,
                "outline_subplot_points": outline_subplot_points,
                "outline_subplot_focus": outline_subplot_focus,
                # ── 窗口右边界：下一章约束（历史推算 + 大纲优先） ──
                "hook_type_sequence": hook_sequence,
                "recommended_hook_type": final_hook_recommendation,
                "hook_type_constraint": final_hook_recommendation,
                "outline_hook_override": outline_hook_override,
                # ── 其他约束 ──
                "element_progress_summary": element_summary,
                "strand_distribution": strand_dist,
                "prev_chapter_suggestions": prev_suggestions,
                "prev_chapter_overall_score": prev_overall_score,
                "tension_target": self._compute_tension_target(current_chapter),
                "strand_recommendation": self._build_strand_recommendation(),
                "payoff_guidance": self._build_payoff_guidance(),
                "turning_point_warning": self._build_turning_point_warning(current_chapter),
                # ── 跨章节趋势 ──
                "reading_power_trend": reading_power_trend.get("trend", "unknown")
                if reading_power_trend
                else "unknown",
                "reading_power_trend_mean": reading_power_trend.get("mean", 0.0)
                if reading_power_trend
                else 0.0,
                "reading_power_trend_direction": reading_power_trend.get("trend", "stable")
                if reading_power_trend
                else "stable",
                # ── 窗口元信息 ──
                "window_size": self._config.window_size,
                "current_chapter": current_chapter,
            }
        except Exception as exc:
            _logger.warning(
                "build_reading_power_hint failed for chapter %d: %s", current_chapter, exc
            )
            return {
                "inherited_suspense": [],
                "suspense_by_urgency": {},
                "force_resolve_suspense": [],
                "forced_pending_questions": [],
                "outline_expected_hook": None,
                "outline_expected_payoffs": [],
                "outline_main_plot_points": [],
                "outline_subplot_points": [],
                "hook_type_sequence": [],
                "recommended_hook_type": "",
                "hook_type_constraint": "",
                "outline_hook_override": False,
                "element_progress_summary": "",
                "strand_distribution": {},
                "prev_chapter_suggestions": [],
                "prev_chapter_overall_score": None,
                "tension_target": 5.0,
                "strand_recommendation": "",
                "payoff_guidance": "",
                "turning_point_warning": "",
                "reading_power_trend": "unknown",
                "reading_power_trend_mean": 0.0,
                "reading_power_trend_direction": "stable",
                "window_size": 5,
                "current_chapter": current_chapter,
            }

    def build_eval_suspense_entries(self, current_chapter: int) -> list[dict[str, Any]]:
        """Return active suspense entries for chapter-level reading-power evaluation."""
        entries: list[dict[str, Any]] = []
        for entry in self._suspense_timeline:
            if entry.resolution_executed:
                continue
            if entry.setup_chapter > current_chapter:
                continue
            entry.update_window_position(current_chapter, self._config.suspense_delay_threshold)
            entries.append(
                {
                    "suspense_id": entry.suspense_id,
                    "suspense_type": entry.suspense_type.value
                    if hasattr(entry.suspense_type, "value")
                    else str(entry.suspense_type),
                    "suspense_description": entry.suspense_description,
                    "setup_chapter": entry.setup_chapter,
                    "planned_resolution_chapter": entry.planned_resolution_chapter,
                    "resolution_window_start": entry.resolution_window_start,
                    "resolution_window_end": entry.resolution_window_end,
                    "urgency_level": entry.urgency_level,
                }
            )
        return entries[-20:]

    def update_window(
        self,
        current_chapter: int,
        chapter_outline: Any,
        reading_power_report: ReadingPowerReport | dict[str, Any],
        strand_hint: str = "",
        element_progress_hint: dict[str, Any] | None = None,
    ) -> ReadingPowerTimelineWindowReport:
        """Update the window and generate a full report."""
        report = ReadingPowerTimelineWindowReport(current_chapter=current_chapter)
        try:
            rp_data = self._normalise_rp_report(reading_power_report)

            window_size = self._config.window_size
            left_offset = self._config.window_left_offset
            right_offset = self._config.window_right_offset
            report.window_start = max(1, current_chapter + left_offset - window_size + 1)
            report.window_end = current_chapter + right_offset

            report.phase_name = self._get_phase_for_chapter(current_chapter)
            report.phase_tension = self._get_tension_for_chapter(current_chapter)

            inherited = self._collect_inherited_suspense(current_chapter)
            report.inherited_suspense = inherited
            report.suspense_by_urgency = self._group_by_urgency(inherited, current_chapter)
            report.force_resolve_suspense = self._get_force_resolve_list(current_chapter)

            if self._is_fallback_rp_report(rp_data):
                hook_sequence = [
                    h.get("hook_type", "none")
                    for h in self._hook_type_history[-(window_size + 1) :]
                ]
                report.hook_type_sequence = hook_sequence
                report.hook_type_history = [
                    h.get("hook_type", "none") for h in self._hook_type_history
                ]
                report.recommended_hook_type = self._recommend_hook_type(hook_sequence)
                report.alternation_score = self._compute_alternation_score(hook_sequence)
                report.consecutive_same_hook = self._count_consecutive_same(hook_sequence)
                report.current_tension = report.phase_tension
                report.tension_deviation = 0.0
                report.tension_history = [
                    h.get("tension", 5.0)
                    for h in self._reading_power_history[-window_size:]
                    if not h.get("is_fallback", False)
                ]
                report.strand_history = self._strand_history[-window_size:]
                report.suspense_chain_score = self._compute_suspense_chain_score(current_chapter)
                report.alerts = ["追读力评估未完成：本章未纳入钩子、张力、趋势和悬念执行统计"]
                self._last_pydantic_report = None
                self._last_window_report = report
                return report

            self._drop_chapter_observations(current_chapter)

            hook_type = rp_data.get("hook_type", "none")
            hook_strength = rp_data.get("hook_strength", "weak")
            self._hook_type_history.append(
                {
                    "chapter": current_chapter,
                    "hook_type": hook_type,
                    "hook_strength": hook_strength,
                }
            )
            hook_sequence = [
                h.get("hook_type", "none") for h in self._hook_type_history[-(window_size + 1) :]
            ]
            report.hook_type_sequence = hook_sequence
            report.hook_type_history = [h.get("hook_type", "none") for h in self._hook_type_history]
            report.recommended_hook_type = self._recommend_hook_type(hook_sequence)
            report.alternation_score = self._compute_alternation_score(hook_sequence)
            report.consecutive_same_hook = self._count_consecutive_same(hook_sequence)

            current_tension = self._infer_tension_from_hook(hook_type, hook_strength, rp_data)
            report.current_tension = current_tension
            expected_tension = report.phase_tension
            report.tension_deviation = abs(current_tension - expected_tension)
            self._reading_power_history.append(
                {
                    "chapter": current_chapter,
                    "tension": current_tension,
                    "overall_score": rp_data.get("overall_score", 0.0),
                    "score": rp_data.get("overall_score", 0.0),
                    "payoff_count": len(rp_data.get("micro_payoffs", []) or []),
                    "is_fallback": False,
                }
            )
            report.tension_history = [
                h.get("tension", 5.0) for h in self._reading_power_history[-window_size:]
            ]

            affinity = self._infer_strand_affinity(hook_type)
            self._strand_history.append(
                {
                    "chapter": current_chapter,
                    "dominant": max(affinity, key=lambda key: affinity[key]),
                    "secondary": [
                        k for k in sorted(affinity, key=lambda key: affinity[key], reverse=True)[1:]
                    ],
                    "affinity": affinity,
                }
            )
            recent_strands = self._strand_history[-window_size:]
            counts: dict[str, float] = {"quest": 0.0, "fire": 0.0, "constellation": 0.0}
            for entry in recent_strands:
                dom = entry.get("dominant", "quest")
                counts[dom] = counts.get(dom, 0.0) + 1.0
                for sec in entry.get("secondary", []):
                    counts[sec] = counts.get(sec, 0.0) + 0.5
            total = sum(counts.values()) or 1.0
            report.strand_distribution = {k: f"{v / total * 100:.0f}%" for k, v in counts.items()}
            report.strand_history = recent_strands

            report.main_plot_progress = self._build_plot_progression_report(
                current_chapter,
                chapter_outline,
                is_main=True,
            )
            report.subplot_progress = self._build_plot_progression_report(
                current_chapter,
                chapter_outline,
                is_main=False,
            )

            report.turning_point_approaching = self._check_turning_point_approaching(
                current_chapter
            )
            report.turning_point_distance = self._get_turning_point_distance(current_chapter)
            report.turning_point_type = self._get_turning_point_type(current_chapter)

            if element_progress_hint:
                rec_ids = element_progress_hint.get("recommended_focus_ids", [])
                if rec_ids:
                    report.element_progress_summary = f"建议优先关注叙事要素: {', '.join(rec_ids)}"

            report.timeline_execution_score = self._compute_timeline_execution_score(
                current_chapter,
                rp_data,
            )
            report.suspense_chain_score = self._compute_suspense_chain_score(current_chapter)
            report.composite_score = self._compute_composite_score(report)

            report.alerts = self._generate_timeline_alerts(report, current_chapter)

            self._mark_suspense_from_report(current_chapter, rp_data)

            self.save_timeline_state()

            try:
                unresolved_suspenses = [
                    e
                    for e in self._suspense_timeline
                    if e.setup_executed
                    and not e.resolution_executed
                    and e.setup_chapter < current_chapter
                ]
                resolved_in_window = [
                    e
                    for e in self._suspense_timeline
                    if e.resolution_executed
                    and e.planned_resolution_chapter is not None
                    and report.window_start <= e.planned_resolution_chapter <= report.window_end
                ]
                misses_in_window = [
                    e
                    for e in self._suspense_timeline
                    if not e.resolution_executed
                    and e.setup_executed
                    and e.resolution_window_end is not None
                    and e.resolution_window_end < current_chapter
                ]
                deviation_alerts = [
                    TimelineDeviationAlert(
                        alert_type="hook_monotony",
                        severity="warning"
                        if report.consecutive_same_hook >= self._config.max_consecutive_same_hook
                        else "info",
                        planned_value="alternating hook types",
                        actual_value=report.hook_type_sequence[-1]
                        if report.hook_type_sequence
                        else "none",
                        deviation_summary=f"Consecutive same hook type: {report.consecutive_same_hook}",
                        correction_suggestion="Switch to a different hook type",
                    )
                    if report.consecutive_same_hook >= self._config.max_consecutive_same_hook
                    else None
                ]
                if report.tension_deviation > self._config.tension_deviation_tolerance:
                    deviation_alerts.append(
                        TimelineDeviationAlert(
                            alert_type="tension_mismatch",
                            severity="warning",
                            planned_value=report.phase_tension,
                            actual_value=report.current_tension,
                            deviation_summary=f"Tension deviation {report.tension_deviation:.1f} exceeds tolerance",
                            correction_suggestion="Adjust pacing to match phase tension",
                        )
                    )
                deviation_alerts = [a for a in deviation_alerts if a is not None]

                strand_balance = 5.0
                if report.strand_distribution:
                    values = [float(v.rstrip("%")) for v in report.strand_distribution.values()]
                    if values:
                        avg = sum(values) / len(values)
                        variance = sum((v - avg) ** 2 for v in values) / len(values)
                        strand_balance = round(max(0.0, min(10.0, 10.0 - variance * 0.5)), 1)

                pydantic_report = PydanticTimelineWindowReport(
                    window_start_chapter=report.window_start,
                    window_center_chapter=current_chapter,
                    window_end_chapter=report.window_end,
                    inherited_suspenses=unresolved_suspenses,
                    chapter_execution_verified=True,
                    suspense_resolutions_in_window=resolved_in_window,
                    suspense_misses_in_window=misses_in_window,
                    next_chapter_constraints=None,
                    deviation_alerts=deviation_alerts,
                    plot_progression=None,
                    element_progress_summary={"summary": report.element_progress_summary}
                    if report.element_progress_summary
                    else {},
                    strand_balance_score=strand_balance,
                    timeline_execution_score=report.timeline_execution_score,
                    suspense_chain_score=report.suspense_chain_score,
                    composite_score=report.composite_score,
                )
                pydantic_report.compute_composite_score()
                self._last_pydantic_report = pydantic_report
            except Exception as pyd_exc:
                _logger.warning("Pydantic report construction failed: %s", pyd_exc)
                self._last_pydantic_report = None

            _logger.info(
                "window updated for chapter %d | phase=%s | tension=%.1f | score=%.1f",
                current_chapter,
                report.phase_name,
                report.current_tension,
                report.composite_score,
            )
        except Exception as exc:
            _logger.warning("update_window failed for chapter %d: %s", current_chapter, exc)
            report.alerts.append(f"窗口更新异常: {exc}")

        self._last_window_report = report
        return report

    # ── Constraint builders for hint injection ───────────────────────────

    def _compute_tension_target(self, current_chapter: int) -> float:
        """Compute target tension for next chapter based on window analysis."""
        if not self._config.enable_tension_recovery or not self._reading_power_history:
            return 5.0
        recent_tensions = [
            h.get("tension", 5.0) for h in self._reading_power_history[-self._config.window_size :]
        ]
        if not recent_tensions:
            return 5.0
        avg = sum(recent_tensions) / len(recent_tensions)
        # If tension has been low, target higher; if high, target lower (recovery)
        target = float(avg + (5.0 - avg) * self._config.tension_recovery_factor)
        return float(round(max(2.0, min(9.0, target)), 1))

    def _build_strand_recommendation(self) -> str:
        """Build strand distribution recommendation."""
        if not self._strand_history:
            return ""
        recent = self._strand_history[-self._config.window_size :]
        counts: dict[str, float] = {"quest": 0.0, "fire": 0.0, "constellation": 0.0}
        for entry in recent:
            dom = entry.get("dominant", "quest")
            counts[dom] = counts.get(dom, 0.0) + 1.0
            for sec in entry.get("secondary", []):
                counts[sec] = counts.get(sec, 0.0) + 0.5
        total = sum(counts.values()) or 1.0
        ratios = {k: v / total for k, v in counts.items()}
        weakest = min(ratios, key=lambda key: ratios[key])
        labels = {"quest": "主线推进", "fire": "情绪爆发", "constellation": "伏笔编织"}
        rec = f"{labels.get(weakest, weakest)}线偏弱（{ratios[weakest] * 100:.0f}%），建议适当加强"
        if self._subplot_history:
            recent_subplots = [h for h in self._subplot_history[-3:] if h.get("subplot_focus")]
            if recent_subplots:
                focuses = [h["subplot_focus"] for h in recent_subplots]
                rec += f"；近期支线聚焦：{' → '.join(focuses)}"
        return rec

    def _build_payoff_guidance(self) -> str:
        """Build micro-payoff guidance based on recent deficit."""
        if not self._reading_power_history:
            return ""
        recent = self._reading_power_history[-self._config.window_size :]
        payoff_counts = [h.get("payoff_count", 0) for h in recent]
        if not payoff_counts:
            return ""
        avg_payoffs = sum(payoff_counts) / len(payoff_counts)
        if avg_payoffs < 1.5:
            return "近几章微兑现密度偏低，建议增加信息/关系/能力类兑现"
        return ""

    def _build_turning_point_warning(self, current_chapter: int) -> str:
        """Warn if approaching a narrative turning point."""
        if not self._blueprint:
            return ""
        phases = self._get_field(self._blueprint, "narrative_phases", []) or []
        for phase in phases:
            start = int(
                self._get_field(
                    phase,
                    "start_chapter",
                    self._get_field(phase, "chapter_start", 0),
                )
                or 0
            )
            end = int(
                self._get_field(
                    phase,
                    "end_chapter",
                    self._get_field(phase, "chapter_end", 0),
                )
                or 0
            )
            phase_type = str(
                self._get_field(
                    phase,
                    "phase_type",
                    self._get_field(phase, "phase_name", ""),
                )
                or ""
            )
            if start and end and current_chapter >= start - 2 and current_chapter < start:
                return f"即将进入{phase_type}阶段（第{start}章），注意调整节奏和情绪基调"
        return ""

    # ── Internal helpers ─────────────────────────────────────────────────

    def get_next_chapter_constraints(
        self,
        current_chapter: int | None = None,
        window_report: ReadingPowerTimelineWindowReport | None = None,
    ) -> NextChapterConstraints:
        """Get execution constraints for the next chapter."""
        try:
            if window_report is None:
                window_report = self._last_window_report
            if window_report is None:
                return NextChapterConstraints(alerts=["暂无追读力窗口报告，无法生成下一章约束"])
            if current_chapter is None:
                current_chapter = window_report.current_chapter
            next_chapter = current_chapter + 1
            constraints = self._build_next_chapter_constraints(
                next_chapter,
                window_report,
            )

            try:
                pydantic_constraints = PydanticNextChapterConstraints(
                    suspense_ids_to_resolve=constraints.force_resolve_suspense_ids,
                    expected_hook_type=constraints.recommended_hook_type or None,
                    tension_adjustment_target=constraints.tension_target,
                    subplots_to_activate=[],
                    element_focus_recommendation=[],
                    strand_distribution_hint={},
                )
                self._last_next_chapter_constraints_pydantic = pydantic_constraints
            except Exception as pyd_exc:
                _logger.warning("Pydantic NextChapterConstraints construction failed: %s", pyd_exc)
                self._last_next_chapter_constraints_pydantic = None

            _logger.info(
                "next chapter constraints for ch.%d | hook=%s | alerts=%d",
                next_chapter,
                constraints.recommended_hook_type,
                len(constraints.alerts),
            )
            return constraints
        except Exception as exc:
            _logger.warning("get_next_chapter_constraints failed: %s", exc)
            return NextChapterConstraints(
                alerts=[f"约束生成异常: {exc}"],
            )

    # ── Suspense event marking ──────────────────────────────────────────────

    def mark_suspense_events(
        self,
        chapter_number: int,
        plan: Any,
        bridge: Any,
    ) -> None:
        """Mark suspense timeline entries based on plan and bridge information.

        Extracts suspense setup information from plan/bridge objects and updates
        the suspense timeline accordingly. Handles None or missing attributes gracefully.
        """
        try:
            plan_hook_type: str | None = None
            plan_hook_desc: str = ""

            if plan is not None:
                expected_hook = self._get_field(plan, "expected_hook", None)
                if expected_hook is not None:
                    plan_hook_type = self._normalise_hook_type(
                        self._get_field(expected_hook, "hook_type", "")
                    )
                    plan_hook_desc = str(
                        self._get_field(expected_hook, "hook_description", "") or ""
                    )

            pending_questions: list[Any] = []

            if bridge is not None:
                pending_questions = list(self._get_field(bridge, "pending_questions", []) or [])

            # Mark existing suspense entries as setup_executed if plan/bridge reference them
            for entry in self._suspense_timeline:
                if entry.setup_executed:
                    continue
                if entry.setup_chapter == chapter_number:
                    entry.setup_executed = True
                    _logger.info(
                        "suspense marked as setup: %s at chapter %d",
                        entry.suspense_id,
                        chapter_number,
                    )

            # Create new suspense entries from plan hook if not already tracked
            if plan_hook_type and plan_hook_type != "none":
                existing_ids = {
                    e.suspense_id
                    for e in self._suspense_timeline
                    if e.setup_chapter == chapter_number
                    and (
                        e.suspense_type.value
                        if hasattr(e.suspense_type, "value")
                        else str(e.suspense_type)
                    )
                    == plan_hook_type
                }
                if not existing_ids:
                    hook_type_enum = HookType(self._normalise_hook_type(plan_hook_type))
                    new_entry = SuspenseTimelineEntry(
                        suspense_id=f"s_plan_{chapter_number}_{plan_hook_type}",
                        suspense_type=hook_type_enum,
                        suspense_description=plan_hook_desc or f"Planned {plan_hook_type} hook",
                        setup_chapter=chapter_number,
                        setup_strength="medium",
                        setup_phase="closing",
                        setup_tension_expected=self._get_tension_for_chapter(chapter_number),
                        planned_resolution_chapter=chapter_number + 3,
                        resolution_window_start=chapter_number + 1,
                        resolution_window_end=chapter_number + 5,
                    )
                    self._suspense_timeline.append(new_entry)
                    _logger.info(
                        "plan-based suspense added: %s at chapter %d",
                        new_entry.suspense_id,
                        chapter_number,
                    )

            # Create suspense entries from bridge pending questions
            for i, question in enumerate(pending_questions):
                q_desc = (
                    str(question)
                    if not isinstance(question, dict)
                    else question.get("question", str(question))
                )
                q_id = (
                    question.get("suspense_id", f"bridge_q_{chapter_number}_{i}")
                    if isinstance(question, dict)
                    else f"bridge_q_{chapter_number}_{i}"
                )
                existing = {e.suspense_id for e in self._suspense_timeline}
                if q_id not in existing:
                    bridge_entry = SuspenseTimelineEntry(
                        suspense_id=q_id,
                        suspense_type=HookType.MYSTERY,
                        suspense_description=q_desc,
                        setup_chapter=chapter_number,
                        setup_strength="weak",
                        setup_phase="middle",
                        setup_tension_expected=5.0,
                        planned_resolution_chapter=chapter_number + 2,
                        resolution_window_start=chapter_number + 1,
                        resolution_window_end=chapter_number + 4,
                    )
                    self._suspense_timeline.append(bridge_entry)
                    _logger.info(
                        "bridge-based suspense added: %s at chapter %d",
                        bridge_entry.suspense_id,
                        chapter_number,
                    )

            self._suspense_timeline.sort(key=lambda e: e.setup_chapter)

        except Exception as exc:
            _logger.warning(
                "mark_suspense_events failed for chapter %d: %s",
                chapter_number,
                exc,
            )

    # ── Internal helpers ─────────────────────────────────────────────────

    def _collect_inherited_suspense(self, current_chapter: int) -> list[dict[str, Any]]:
        inherited: list[dict[str, Any]] = []
        for entry in self._suspense_timeline:
            if not entry.setup_executed or entry.resolution_executed:
                continue
            if entry.setup_chapter >= current_chapter:
                continue
            entry.update_window_position(
                current_chapter,
                self._config.suspense_delay_threshold,
            )
            inherited.append(
                {
                    "suspense_id": entry.suspense_id,
                    "suspense_type": entry.suspense_type.value
                    if hasattr(entry.suspense_type, "value")
                    else str(entry.suspense_type),
                    "description": entry.suspense_description,
                    "setup_chapter": entry.setup_chapter,
                    "urgency_level": entry.urgency_level,
                    "window_position": entry.current_window_position,
                }
            )
        return inherited

    def _group_by_urgency(
        self,
        inherited: list[dict[str, Any]],
        current_chapter: int,
    ) -> dict[str, list[dict[str, Any]]]:
        groups: dict[str, list[dict[str, Any]]] = {
            "critical": [],
            "high": [],
            "medium": [],
            "low": [],
        }
        for item in inherited:
            level = item.get("urgency_level", "low")
            if level in groups:
                groups[level].append(item)
            else:
                groups["low"].append(item)
        return groups

    def _get_force_resolve_list(self, current_chapter: int) -> list[dict[str, Any]]:
        if not self._config.enable_force_resolve:
            return []
        force_list: list[dict[str, Any]] = []
        threshold = self._config.force_resolve_threshold
        for entry in self._suspense_timeline:
            if entry.resolution_executed or not entry.setup_executed:
                continue
            chapters_since_setup = current_chapter - entry.setup_chapter
            if chapters_since_setup >= threshold:
                force_list.append(
                    {
                        "suspense_id": entry.suspense_id,
                        "description": entry.suspense_description,
                        "chapters_pending": chapters_since_setup,
                        "urgency": "critical",
                    }
                )
        return force_list

    def _recommend_hook_type(self, hook_sequence: list[str]) -> str:
        if not hook_sequence:
            return "mystery"

        consecutive = self._count_consecutive_same(hook_sequence)
        if consecutive >= self._config.hook_alternation_threshold:
            last_type = hook_sequence[-1] if hook_sequence else "none"
            alternatives = [h for h in HookType if h.value != last_type and h.value != "none"]
            if alternatives:
                return alternatives[0].value

        recent_set = set(hook_sequence[-self._config.window_size :])
        for ht in HookType:
            if ht.value not in recent_set and ht.value != "none":
                return ht.value

        return "mystery"

    def _compute_alternation_score(self, hook_sequence: list[str]) -> float:
        if len(hook_sequence) < 2:
            return 10.0

        same_count = 0
        for i in range(1, len(hook_sequence)):
            if hook_sequence[i] == hook_sequence[i - 1]:
                same_count += 1

        alternation_ratio = 1.0 - (same_count / (len(hook_sequence) - 1))
        return round(alternation_ratio * 10.0, 1)

    def _count_consecutive_same(self, hook_sequence: list[str]) -> int:
        if not hook_sequence:
            return 0
        last = hook_sequence[-1]
        count = 1
        for h in reversed(hook_sequence[:-1]):
            if h == last:
                count += 1
            else:
                break
        return count

    def _check_hook_match(self, expected_hook: str, actual_hook: str) -> bool:
        if not expected_hook or expected_hook == "none":
            return True
        return expected_hook.lower() == actual_hook.lower()

    def _analyze_hook_deviation(self, expected_hook: str, actual_hook: str) -> dict[str, Any]:
        matched = self._check_hook_match(expected_hook, actual_hook)
        return {
            "matched": matched,
            "expected": expected_hook,
            "actual": actual_hook,
            "deviation_severity": "none" if matched else "warning",
        }

    def _infer_tension_from_hook(
        self,
        hook_type: str,
        hook_strength: str,
        rp_data: dict[str, Any],
    ) -> float:
        base_tension: dict[str, float] = {
            "crisis": 7.0,
            "mystery": 6.0,
            "emotion": 5.0,
            "choice": 6.5,
            "desire": 4.0,
            "none": 2.0,
        }
        strength_multiplier: dict[str, float] = {
            "strong": 1.2,
            "medium": 1.0,
            "weak": 0.7,
        }
        base = base_tension.get(hook_type, 5.0)
        mult = strength_multiplier.get(hook_strength, 1.0)
        tension = base * mult

        payoffs = rp_data.get("micro_payoffs", [])
        if isinstance(payoffs, list):
            tension += min(len(payoffs), self._config.payoff_cap) * 0.3

        return round(max(0.0, min(10.0, tension)), 1)

    def _payoff_covered(
        self,
        expected_payoffs: list[dict[str, Any]],
        actual_payoffs: list[dict[str, Any]],
    ) -> dict[str, Any]:
        actual_types = {
            str(p.get("payoff_type", p.get("type", ""))).lower()
            for p in actual_payoffs
            if isinstance(p, dict)
        }
        covered = []
        missing = []
        for ep in expected_payoffs:
            if not isinstance(ep, dict):
                continue
            ep_type = str(ep.get("payoff_type", "")).lower()
            if ep_type in actual_types:
                covered.append(ep)
            else:
                missing.append(ep)
        return {"covered": covered, "missing": missing}

    def _classify_suspense_resolution(
        self,
        entry: SuspenseTimelineEntry,
        rp_data: dict[str, Any],
    ) -> str:
        resolved_ids = {
            str(item).strip()
            for item in list(rp_data.get("resolved_suspense_ids", []) or [])
            if str(item).strip()
        }
        if entry.suspense_id in resolved_ids:
            return "direct"

        hook_type = self._normalise_hook_type(rp_data.get("hook_type", "none"))
        payoffs = rp_data.get("micro_payoffs", [])

        suspense_type = (
            entry.suspense_type.value
            if hasattr(entry.suspense_type, "value")
            else str(entry.suspense_type)
        )
        suspense_desc = (entry.suspense_description or "").lower()
        hook_desc = str(rp_data.get("hook_description", "") or "").lower()
        overlap_words = [word for word in suspense_desc.split() if len(word) > 1]
        if hook_type.lower() == suspense_type.lower() and overlap_words:
            if sum(1 for word in overlap_words if word in hook_desc) >= min(2, len(overlap_words)):
                return "direct"
        if hook_type.lower() == suspense_type.lower() and self._has_text_overlap(
            suspense_desc,
            hook_desc,
        ):
            return "direct"

        for payoff in payoffs:
            if isinstance(payoff, dict):
                desc = str(payoff.get("description", "")).lower()
                if overlap_words and any(word in desc for word in overlap_words):
                    return "indirect"
                if self._has_text_overlap(suspense_desc, desc):
                    return "indirect"

        return "none"

    def _build_plot_progression_report(
        self,
        current_chapter: int,
        chapter_outline: Any,
        *,
        is_main: bool = True,
    ) -> dict[str, Any]:
        if is_main:
            main_points: list[str] = []
            try:
                main_points = list(getattr(chapter_outline, "main_plot_points", []) or [])
            except Exception:
                pass
            self._main_plot_history.append(
                {
                    "chapter": current_chapter,
                    "points": main_points,
                }
            )
            return {
                "current_chapter": current_chapter,
                "plot_points_covered": len(main_points),
                "total_history": len(self._main_plot_history),
            }
        else:
            subplot_points: list[str] = []
            subplot_focus: str = ""
            try:
                subplot_points = list(getattr(chapter_outline, "subplot_points", []) or [])
                subplot_focus = str(getattr(chapter_outline, "subplot_focus", "") or "")
            except Exception:
                pass
            self._subplot_history.append(
                {
                    "chapter": current_chapter,
                    "subplot_focus": subplot_focus,
                    "points": subplot_points,
                }
            )
            return {
                "current_chapter": current_chapter,
                "subplot_focus": subplot_focus,
                "subplot_points_covered": len(subplot_points),
                "active_subplots": len(self._subplot_history),
            }

    def _check_turning_point_approaching(self, current_chapter: int) -> bool:
        return self._get_turning_point_distance(current_chapter) <= 2

    def _get_turning_point_distance(self, current_chapter: int) -> int:
        try:
            turning_points = self._get_field(self._blueprint, "key_turning_points", []) or []
            min_distance = 9999
            for tp in turning_points:
                tp_chapter = int(self._get_field(tp, "chapter_number", 0) or 0)
                if tp_chapter > current_chapter:
                    distance = tp_chapter - current_chapter
                    min_distance = min(min_distance, distance)
            return min_distance if min_distance < 9999 else 0
        except Exception as exc:
            _logger.warning("_get_turning_point_distance failed: %s", exc)
            return 0

    def _get_turning_point_type(self, current_chapter: int) -> str:
        try:
            turning_points = self._get_field(self._blueprint, "key_turning_points", []) or []
            nearest_tp = None
            min_distance = 9999
            for tp in turning_points:
                tp_chapter = int(self._get_field(tp, "chapter_number", 0) or 0)
                if tp_chapter > current_chapter:
                    distance = tp_chapter - current_chapter
                    if distance < min_distance:
                        min_distance = distance
                        nearest_tp = tp
            if nearest_tp:
                return str(self._get_field(nearest_tp, "description", "unknown"))
        except Exception as exc:
            _logger.warning("_get_turning_point_type failed: %s", exc)
        return ""

    def _mark_suspense_from_report(
        self,
        current_chapter: int,
        rp_data: dict[str, Any],
    ) -> None:
        hook_type = rp_data.get("hook_type", "none")
        hook_strength = rp_data.get("hook_strength", "weak")
        hook_desc = rp_data.get("hook_description", "")

        for entry in self._suspense_timeline:
            if entry.resolution_executed:
                continue
            resolution_type = self._classify_suspense_resolution(entry, rp_data)
            if resolution_type != "none":
                entry.resolution_executed = True
                entry.resolution_timing_deviation = current_chapter - (
                    entry.planned_resolution_chapter or current_chapter
                )
                _logger.info(
                    "suspense resolved: %s at chapter %d (%s)",
                    entry.suspense_id,
                    current_chapter,
                    resolution_type,
                )

        if hook_type != "none" and hook_strength in ("strong", "medium"):
            existing_ids = {
                e.suspense_id
                for e in self._suspense_timeline
                if not e.resolution_executed and e.setup_chapter == current_chapter
            }
            if not existing_ids:
                new_entry = SuspenseTimelineEntry(
                    suspense_id=f"s_{current_chapter}_{hook_type}",
                    suspense_type=HookType(hook_type),
                    suspense_description=hook_desc,
                    setup_chapter=current_chapter,
                    setup_strength=hook_strength,
                    setup_phase="closing",
                    setup_tension_expected=self._infer_tension_from_hook(
                        hook_type,
                        hook_strength,
                        rp_data,
                    ),
                    planned_resolution_chapter=current_chapter + 3,
                    resolution_window_start=current_chapter + 1,
                    resolution_window_end=current_chapter + 5,
                )
                self._suspense_timeline.append(new_entry)
                _logger.info(
                    "new suspense set up: %s at chapter %d",
                    new_entry.suspense_id,
                    current_chapter,
                )

    def _build_next_chapter_constraints(
        self,
        next_chapter: int,
        window_report: ReadingPowerTimelineWindowReport,
    ) -> NextChapterConstraints:
        constraints = NextChapterConstraints()

        constraints.recommended_hook_type = window_report.recommended_hook_type

        constraints.force_resolve_suspense_ids = [
            s.get("suspense_id", "") for s in window_report.force_resolve_suspense
        ]
        constraints.suspense_to_resolve = window_report.force_resolve_suspense

        if window_report.strand_distribution:
            lowest_strand = min(
                window_report.strand_distribution,
                key=lambda k: float(window_report.strand_distribution[k].rstrip("%")),
            )
            strand_labels = {
                "quest": "主线（Quest）",
                "fire": "感情线（Fire）",
                "constellation": "世界观线（Constellation）",
            }
            label = strand_labels.get(lowest_strand, lowest_strand)
            constraints.strand_recommendation = (
                f"建议加强{label}，当前占比仅 {window_report.strand_distribution[lowest_strand]}"
            )

        constraints.tension_target = window_report.phase_tension

        if window_report.turning_point_approaching:
            constraints.turning_point_warning = (
                f"关键转折点将在 {window_report.turning_point_distance} 章后到来"
                f"（{window_report.turning_point_type}）"
            )

        if window_report.consecutive_same_hook >= self._config.max_consecutive_same_hook:
            constraints.alternation_warning = (
                f"已连续 {window_report.consecutive_same_hook} 章使用同类型钩子，"
                f"建议切换钩子类型以避免单调"
            )

        upcoming_subplots = [
            h
            for h in self._subplot_history
            if h.get("subplot_focus") and abs(h.get("chapter", 0) - next_chapter) <= 2
        ]
        if upcoming_subplots:
            focuses = [h["subplot_focus"] for h in upcoming_subplots[-2:]]
            constraints.strand_recommendation += f"；临近章节支线聚焦：{' → '.join(focuses)}"

        constraints.execution_score = window_report.timeline_execution_score
        constraints.suspense_chain_score = window_report.suspense_chain_score

        constraints.alerts = list(window_report.alerts)

        return constraints

    def _get_next_resolve_suspense(self, current_chapter: int) -> SuspenseTimelineEntry | None:
        candidates = [
            e
            for e in self._suspense_timeline
            if not e.resolution_executed
            and e.setup_executed
            and (e.planned_resolution_chapter or 9999) <= current_chapter + 1
        ]
        if not candidates:
            return None
        return min(candidates, key=lambda e: e.planned_resolution_chapter or 9999)

    def _compute_timeline_execution_score(
        self,
        current_chapter: int,
        rp_data: dict[str, Any],
    ) -> float:
        score = 10.0

        for entry in self._suspense_timeline:
            if entry.resolution_executed or not entry.setup_executed:
                continue
            chapters_pending = current_chapter - entry.setup_chapter
            if chapters_pending > self._config.force_resolve_threshold:
                score -= 2.0
            elif chapters_pending > self._config.suspense_delay_threshold:
                score -= 1.0

        outline_hook_match = rp_data.get("outline_hook_match")
        if isinstance(outline_hook_match, dict):
            match_type = str(outline_hook_match.get("match_type", "") or "").lower()
            if match_type == "different":
                score -= 1.5
            elif match_type == "partial":
                score -= 0.5

        payoffs = rp_data.get("micro_payoffs", [])
        if isinstance(payoffs, list):
            score += min(len(payoffs), self._config.payoff_cap) * 0.3

        return round(max(0.0, min(10.0, score)), 1)

    def _compute_suspense_chain_score(self, current_chapter: int) -> float:
        if not self._suspense_timeline:
            return 5.0

        total = len(self._suspense_timeline)
        resolved = sum(1 for e in self._suspense_timeline if e.resolution_executed)
        resolution_rate = resolved / total if total > 0 else 0.0

        timing_scores = []
        for entry in self._suspense_timeline:
            if not entry.resolution_executed or not entry.planned_resolution_chapter:
                continue
            deviation = abs(entry.resolution_timing_deviation)
            if deviation == 0:
                timing_scores.append(1.0)
            elif deviation <= 1:
                timing_scores.append(0.8)
            elif deviation <= 2:
                timing_scores.append(0.5)
            else:
                timing_scores.append(0.2)

        avg_timing = sum(timing_scores) / len(timing_scores) if timing_scores else 0.5

        score = resolution_rate * 6.0 + avg_timing * 4.0
        return round(max(0.0, min(10.0, score)), 1)

    def _compute_composite_score(
        self,
        report: ReadingPowerTimelineWindowReport,
    ) -> float:
        weights = self._config.hook_strength_weights

        hook_score_map = {"strong": 8.0, "medium": 5.0, "weak": 2.0, "none": 0.0}
        last_hook = self._hook_type_history[-1] if self._hook_type_history else {}
        hook_strength = last_hook.get("hook_strength", "weak")
        hook_strength_score = hook_score_map.get(hook_strength, 2.0)

        recent_payoff_counts = [
            float(h.get("payoff_count", 0) or 0)
            for h in self._reading_power_history[-self._config.window_size :]
            if not h.get("is_fallback", False)
        ]
        avg_payoffs = (
            sum(recent_payoff_counts) / len(recent_payoff_counts) if recent_payoff_counts else 0.0
        )
        payoff_density = (
            min(avg_payoffs, self._config.payoff_cap)
            / max(
                self._config.payoff_cap,
                1,
            )
            * 10.0
        )

        suspense_timing_score = report.suspense_chain_score
        hook_alternation_score = report.alternation_score

        tension_deviation = report.tension_deviation
        tolerance = self._config.tension_deviation_tolerance
        tension_match_score = max(0.0, 10.0 - (tension_deviation / tolerance) * 5.0)

        score = (
            hook_strength_score * weights.get("hook_strength", 0.25)
            + payoff_density * weights.get("payoff_density", 0.20)
            + suspense_timing_score * weights.get("suspense_timing", 0.20)
            + hook_alternation_score * weights.get("hook_alternation", 0.15)
            + tension_match_score * weights.get("tension_match", 0.20)
        )
        return round(max(0.0, min(10.0, score)), 1)

    def _generate_timeline_alerts(
        self,
        report: ReadingPowerTimelineWindowReport,
        current_chapter: int,
    ) -> list[str]:
        alerts: list[str] = []

        if report.composite_score < self._config.critical_score_threshold:
            alerts.append(
                f"严重预警：追读力综合评分 {report.composite_score:.1f} "
                f"低于临界阈值 {self._config.critical_score_threshold}"
            )
        elif report.composite_score < self._config.warning_score_threshold:
            alerts.append(
                f"警告：追读力综合评分 {report.composite_score:.1f} "
                f"低于警告阈值 {self._config.warning_score_threshold}"
            )

        if self._config.enable_hook_alternation_check:
            if report.consecutive_same_hook >= self._config.max_consecutive_same_hook:
                alerts.append(f"钩子单调：已连续 {report.consecutive_same_hook} 章使用同类型钩子")

        if self._config.enable_tension_recovery:
            if report.tension_deviation > self._config.tension_deviation_tolerance:
                alerts.append(
                    f"张力偏离：当前张力 {report.current_tension:.1f} "
                    f"与预期 {report.phase_tension:.1f} 偏差过大"
                )

        if report.force_resolve_suspense:
            ids = [s.get("suspense_id", "?") for s in report.force_resolve_suspense]
            alerts.append(f"强制兑现：以下悬念必须尽快收束: {', '.join(ids)}")

        if report.turning_point_approaching:
            alerts.append(f"转折点临近：{report.turning_point_distance} 章后将迎来关键转折")

        for entry in self._suspense_timeline:
            if entry.resolution_executed or not entry.setup_executed:
                continue
            chapters_pending = current_chapter - entry.setup_chapter
            if chapters_pending >= self._config.suspense_delay_threshold:
                alerts.append(f"悬念延迟：{entry.suspense_id} 已设置 {chapters_pending} 章未兑现")

        return alerts

    @staticmethod
    def _normalise_rp_report(
        report: ReadingPowerReport | dict[str, Any],
    ) -> dict[str, Any]:
        if isinstance(report, dict):
            return report
        try:
            return report.model_dump(mode="json")
        except Exception:
            return {}

    @staticmethod
    def _is_fallback_rp_report(rp_data: dict[str, Any]) -> bool:
        return bool(
            rp_data.get("is_fallback")
            or str(rp_data.get("evaluation_status", "")).lower() == "fallback"
        )

    # ── Cross-chapter aggregation ────────────────────────────────────────

    def compute_reading_power_trend(
        self,
        *,
        window: int | None = None,
    ) -> dict[str, Any]:
        """Compute cross-chapter reading power trend statistics.

        Returns dict with:
            - mean: average overall_score
            - std: standard deviation (volatility)
            - trend: "rising" / "falling" / "stable"
            - min_score / max_score: range
            - min_chapter / max_chapter: where extremes occurred
            - recent_scores: last N scores for charting
        """
        scores: list[tuple[int, float]] = []
        for entry in self._reading_power_history:
            if entry.get("is_fallback", False):
                continue
            ch = entry.get("chapter", 0)
            sc = entry.get("overall_score", entry.get("score", 0.0))
            if ch > 0 and sc > 0:
                scores.append((ch, sc))

        if not scores:
            return {
                "mean": 0.0,
                "std": 0.0,
                "trend": "unknown",
                "min_score": 0.0,
                "max_score": 0.0,
                "min_chapter": 0,
                "max_chapter": 0,
                "recent_scores": [],
                "total_chapters_evaluated": 0,
            }

        scores.sort(key=lambda x: x[0])
        values = [s[1] for s in scores]
        n = len(values)
        mean = sum(values) / n
        std = (sum((v - mean) ** 2 for v in values) / n) ** 0.5 if n > 1 else 0.0

        # Trend: compare last 3 chapters average vs previous 3
        if n >= 6:
            recent_avg = sum(values[-3:]) / 3
            prev_avg = sum(values[-6:-3]) / 3
            delta = recent_avg - prev_avg
            trend = "rising" if delta > 0.5 else ("falling" if delta < -0.5 else "stable")
        elif n >= 2:
            trend = (
                "rising"
                if values[-1] > values[-2] + 0.3
                else ("falling" if values[-1] < values[-2] - 0.3 else "stable")
            )
        else:
            trend = "stable"

        min_idx = values.index(min(values))
        max_idx = values.index(max(values))
        recent_window = window or self._config.window_size

        return {
            "mean": round(mean, 1),
            "std": round(std, 1),
            "trend": trend,
            "min_score": min(values),
            "max_score": max(values),
            "min_chapter": scores[min_idx][0],
            "max_chapter": scores[max_idx][0],
            "recent_scores": [{"chapter": s[0], "score": s[1]} for s in scores[-recent_window:]],
            "total_chapters_evaluated": n,
        }
