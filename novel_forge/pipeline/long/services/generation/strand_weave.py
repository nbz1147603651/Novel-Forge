"""Strand Weave — multi-thread narrative rhythm tracking.

Tracks the distribution balance of three canonical story strands:
- Quest  (主线) : combat / mission / exploration / progression
- Fire   (感情线): emotional relationships / bonding / romance
- Constellation (世界观线): faction / worldbuilding / lore revelation

The service detects rhythm imbalances (e.g. Quest-only fatigue,
Fire absence) and injects planning hints so the AI naturally weaves
under-represented strands into upcoming chapters.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import Field

from novel_forge.core.schemas.base import VersionedSchema
from novel_forge.obs.logger import get_logger
from novel_forge.pipeline.style_profile_helpers import (
    get_profile_section,
    get_section_value,
)

_log = get_logger("pipeline.long.services.strand_weave")


# ── Strand Type Enum ─────────────────────────────────────────────────────


class StrandType(str, Enum):
    """Canonical narrative strand categories."""

    QUEST = "quest"  # 主线：战斗/任务/探索/升级
    FIRE = "fire"  # 感情线：情感/暧昧/羁绊
    CONSTELLATION = "constellation"  # 世界观线：势力/阵营/世界观揭示


# ── Default thresholds (can be overridden by StyleProfile.strand_config) ───

_DEFAULT_STRAND_CONFIG: dict[StrandType, dict[str, int | float]] = {
    StrandType.QUEST: {
        "ideal_pct_low": 50,
        "ideal_pct_high": 65,
        "max_consecutive": 5,
        "max_absent_chapters": 8,
    },
    StrandType.FIRE: {
        "ideal_pct_low": 20,
        "ideal_pct_high": 30,
        "max_consecutive": 4,
        "max_absent_chapters": 10,
    },
    StrandType.CONSTELLATION: {
        "ideal_pct_low": 10,
        "ideal_pct_high": 20,
        "max_consecutive": 3,
        "max_absent_chapters": 15,
    },
}


# ── Strand Tracker Schema (persisted alongside chapter state) ────────────


class StrandEntry(VersionedSchema):
    """Record of the dominant strand for a single chapter."""

    chapter: int = Field(ge=1)
    dominant: StrandType
    secondary: list[StrandType] = Field(default_factory=list)


class StrandAlert(VersionedSchema):
    """A rhythm imbalance alert."""

    alert_type: str = Field(description="e.g. 'fatigue', 'absence', 'dominance'")
    strand: StrandType
    message: str
    severity: str = Field(default="warning")
    suggestion: str = Field(default="")


class StrandTracker(VersionedSchema):
    """Persistent strand distribution tracker."""

    history: list[StrandEntry] = Field(default_factory=list)

    @property
    def last_quest_chapter(self) -> int:
        return self._last_chapter_for(StrandType.QUEST)

    @property
    def last_fire_chapter(self) -> int:
        return self._last_chapter_for(StrandType.FIRE)

    @property
    def last_constellation_chapter(self) -> int:
        return self._last_chapter_for(StrandType.CONSTELLATION)

    def _last_chapter_for(self, strand: StrandType) -> int:
        for entry in reversed(self.history):
            if entry.dominant == strand or strand in entry.secondary:
                return entry.chapter
        return 0

    def record(
        self, chapter: int, dominant: StrandType, secondary: list[StrandType] | None = None
    ) -> None:
        """Record strand distribution for a completed chapter."""
        # Remove existing entry for this chapter (idempotent)
        self.history = [e for e in self.history if e.chapter != chapter]
        self.history.append(
            StrandEntry(chapter=chapter, dominant=dominant, secondary=secondary or [])
        )
        self.history.sort(key=lambda e: e.chapter)

    def distribution(self, window: int = 0) -> dict[StrandType, float]:
        """Compute strand distribution as percentages.

        Args:
            window: Number of recent chapters to consider (0 = all).
        """
        entries = self.history[-window:] if window > 0 else self.history
        if not entries:
            return {s: 0.0 for s in StrandType}

        counts: dict[StrandType, float] = {s: 0.0 for s in StrandType}
        for entry in entries:
            counts[entry.dominant] = counts.get(entry.dominant, 0.0) + 1.0
            for sec in entry.secondary:
                counts[sec] = counts.get(sec, 0.0) + 0.5

        total = sum(counts.values()) or 1.0
        return {s: round(counts.get(s, 0.0) / total * 100, 1) for s in StrandType}


# ── Analysis functions ───────────────────────────────────────────────────


def check_strand_balance(
    tracker: StrandTracker,
    current_chapter: int,
    *,
    config: dict[StrandType, dict[str, Any]] | None = None,
    window: int = 20,
) -> list[StrandAlert]:
    """Check for rhythm imbalances and return alerts.

    Args:
        tracker: Current strand tracker state.
        current_chapter: The chapter about to be written.
        config: Strand thresholds (defaults to _DEFAULT_STRAND_CONFIG).
        window: Rolling window size for distribution check.
    """
    cfg = config or _DEFAULT_STRAND_CONFIG
    alerts: list[StrandAlert] = []

    # ── 1. Absence check: has a strand been gone too long? ───────────────
    for strand_type in StrandType:
        strand_cfg = cfg.get(strand_type, {})
        max_absent = int(strand_cfg.get("max_absent_chapters", 15))
        last_ch = tracker._last_chapter_for(strand_type)
        gap = current_chapter - last_ch if last_ch > 0 else current_chapter
        if gap > max_absent:
            label = _strand_label(strand_type)
            alerts.append(
                StrandAlert(
                    alert_type="absence",
                    strand=strand_type,
                    severity="warning",
                    message=f"{label}已连续 {gap} 章未出现（阈值 {max_absent} 章）",
                    suggestion=f"建议在第 {current_chapter} 章安排{label}相关场景",
                )
            )

    # ── 2. Consecutive dominance check (fatigue) ─────────────────────────
    if tracker.history:
        recent = tracker.history[-10:]
        for strand_type in StrandType:
            strand_cfg = cfg.get(strand_type, {})
            max_consecutive = int(strand_cfg.get("max_consecutive", 5))
            consecutive = 0
            for entry in reversed(recent):
                if entry.dominant == strand_type:
                    consecutive += 1
                else:
                    break
            if consecutive >= max_consecutive:
                label = _strand_label(strand_type)
                alerts.append(
                    StrandAlert(
                        alert_type="fatigue",
                        strand=strand_type,
                        severity="warning",
                        message=f"{label}已连续 {consecutive} 章占主导（阈值 {max_consecutive} 章）",
                        suggestion="建议切换到其他情节线以避免节奏疲劳",
                    )
                )

    # ── 3. Distribution skew check ───────────────────────────────────────
    dist = tracker.distribution(window=window)
    for strand_type in StrandType:
        strand_cfg = cfg.get(strand_type, {})
        ideal_low = float(strand_cfg.get("ideal_pct_low", 0))
        ideal_high = float(strand_cfg.get("ideal_pct_high", 100))
        pct = dist.get(strand_type, 0.0)
        label = _strand_label(strand_type)
        if pct > ideal_high + 15:
            alerts.append(
                StrandAlert(
                    alert_type="dominance",
                    strand=strand_type,
                    severity="warning",
                    message=f"{label}占比 {pct:.0f}% 超出理想区间（{ideal_low}-{ideal_high}%）",
                    suggestion=f"降低{label}密度，增加其他情节线比重",
                )
            )

    return alerts


def build_strand_config_from_profile(strand_config: Any) -> dict[StrandType, dict[str, Any]]:
    """Convert a StyleProfile.strand_config to the internal config dict format.

    Preserves ideal_pct values from ``_DEFAULT_STRAND_CONFIG`` and overrides
    only the threshold values defined in ``StrandConfig``.

    Args:
        strand_config: A ``StrandConfig`` instance from ``ProjectStyleProfile.strand_config``
            (or any object with the ``quest_max_consecutive``, ``fire_max_absent``, and
            ``constellation_max_absent`` attributes).

    Returns:
        A ``dict[StrandType, dict]`` suitable for passing to
        ``check_strand_balance`` or ``build_strand_hint_for_planning``.
    """
    cfg: dict[StrandType, dict[str, Any]] = {k: dict(v) for k, v in _DEFAULT_STRAND_CONFIG.items()}
    cfg[StrandType.QUEST]["max_consecutive"] = int(
        get_section_value(
            strand_config,
            "quest_max_consecutive",
            cfg[StrandType.QUEST]["max_consecutive"],
        )
    )
    cfg[StrandType.FIRE]["max_absent_chapters"] = int(
        get_section_value(
            strand_config,
            "fire_max_absent",
            cfg[StrandType.FIRE]["max_absent_chapters"],
        )
    )
    cfg[StrandType.CONSTELLATION]["max_absent_chapters"] = int(
        get_section_value(
            strand_config,
            "constellation_max_absent",
            cfg[StrandType.CONSTELLATION]["max_absent_chapters"],
        )
    )
    return cfg


def build_strand_hint_for_planning(
    tracker: StrandTracker,
    current_chapter: int,
    *,
    config: dict[StrandType, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build a compact strand-weave hint for injection into planning prompts.

    Returns a dict ready to be merged into the template context.
    """
    alerts = check_strand_balance(tracker, current_chapter, config=config)
    dist = tracker.distribution(window=20)

    hint: dict[str, Any] = {
        "strand_distribution": {s.value: f"{pct:.0f}%" for s, pct in dist.items()},
        "strand_alerts": [
            {"strand": a.strand.value, "message": a.message, "suggestion": a.suggestion}
            for a in alerts
        ],
    }
    return hint


# ── Strand inference (rule-based heuristic from chapter content) ─────────

_QUEST_KEYWORDS = frozenset(
    {
        "战斗",
        "打斗",
        "战",
        "杀",
        "追击",
        "逃跑",
        "突围",
        "任务",
        "冒险",
        "探索",
        "升级",
        "修炼",
        "突破",
        "炼制",
        "铸造",
        "寻找",
        "调查",
        "搜查",
        "侦查",
        "考核",
        "比试",
        "切磋",
    }
)

_FIRE_KEYWORDS = frozenset(
    {
        "感情",
        "爱",
        "暧昧",
        "心动",
        "牵手",
        "拥抱",
        "亲吻",
        "羁绊",
        "信任",
        "默契",
        "陪伴",
        "守护",
        "承诺",
        "相思",
        "嫉妒",
        "背叛",
        "重逢",
        "别离",
        "思念",
        "告白",
        "心痛",
    }
)

_CONSTELLATION_KEYWORDS = frozenset(
    {
        "势力",
        "阵营",
        "组织",
        "家族",
        "门派",
        "帝国",
        "朝廷",
        "世界",
        "历史",
        "传说",
        "远古",
        "规则",
        "秘密",
        "真相",
        "阴谋",
        "政治",
        "联盟",
        "背景",
        "起源",
        "禁忌",
        "格局",
    }
)


def infer_dominant_strand(
    chapter_text: str,
    *,
    plan: Any | None = None,
    strand_keywords: dict[str, frozenset[str]] | None = None,
    style_profile: Any | None = None,
) -> tuple[StrandType, list[StrandType]]:
    """Infer dominant and secondary strands from chapter text.

    Uses keyword frequency as a heuristic. When a chapter plan is available,
    `chapter_type` is also considered (e.g. 'emotional' → Fire boost).

    Args:
        chapter_text: The chapter content to analyze.
        plan: Optional chapter plan with chapter_type field.
        strand_keywords: Optional custom keywords dict with keys
            'quest', 'fire', 'constellation' mapping to frozensets.
            If None, uses default keywords from _QUEST_KEYWORDS etc.
        style_profile: Optional ProjectStyleProfile to extract
            strand_keywords from strand_keywords_config.

    Returns:
        Tuple of (dominant_strand, secondary_strands).
    """
    if strand_keywords is None and style_profile is not None:
        strand_keywords = _load_strand_keywords_from_style_profile(style_profile)
    if strand_keywords is None:
        strand_keywords = {
            "quest": _QUEST_KEYWORDS,
            "fire": _FIRE_KEYWORDS,
            "constellation": _CONSTELLATION_KEYWORDS,
        }

    text = str(chapter_text or "")
    quest_kw = strand_keywords.get("quest", _QUEST_KEYWORDS)
    fire_kw = strand_keywords.get("fire", _FIRE_KEYWORDS)
    constellation_kw = strand_keywords.get("constellation", _CONSTELLATION_KEYWORDS)

    quest_score = sum(1 for kw in quest_kw if kw in text)
    fire_score = sum(1 for kw in fire_kw if kw in text)
    constellation_score = sum(1 for kw in constellation_kw if kw in text)

    if plan is not None:
        chapter_type = str(getattr(plan, "chapter_type", "") or "").lower()
        if chapter_type == "emotional":
            fire_score += 3
        elif chapter_type == "discovery":
            constellation_score += 3
        elif chapter_type in ("crisis", ""):
            quest_score += 2

    scores = {
        StrandType.QUEST: quest_score,
        StrandType.FIRE: fire_score,
        StrandType.CONSTELLATION: constellation_score,
    }
    sorted_strands = sorted(scores, key=lambda s: scores[s], reverse=True)
    dominant = sorted_strands[0]
    secondary = [s for s in sorted_strands[1:] if scores[s] > 0]
    return dominant, secondary


def _load_strand_keywords_from_style_profile(
    style_profile: Any,
) -> dict[str, frozenset[str]] | None:
    """Load strand keywords from StyleProfile.strand_keywords_config."""
    if style_profile is None:
        return None
    kw_config = get_profile_section(style_profile, "strand_keywords_config")
    if kw_config is None:
        return None
    quest_keywords = list(get_section_value(kw_config, "quest_keywords", []) or [])
    fire_keywords = list(get_section_value(kw_config, "fire_keywords", []) or [])
    constellation_keywords = list(
        get_section_value(kw_config, "constellation_keywords", []) or []
    )
    if not (quest_keywords or fire_keywords or constellation_keywords):
        return None
    return {
        "quest": frozenset(quest_keywords) if quest_keywords else _QUEST_KEYWORDS,
        "fire": frozenset(fire_keywords) if fire_keywords else _FIRE_KEYWORDS,
        "constellation": (
            frozenset(constellation_keywords)
            if constellation_keywords
            else _CONSTELLATION_KEYWORDS
        ),
    }


# ── Helpers ──────────────────────────────────────────────────────────────


def _strand_label(strand: StrandType) -> str:
    return {
        StrandType.QUEST: "主线（Quest）",
        StrandType.FIRE: "感情线（Fire）",
        StrandType.CONSTELLATION: "世界观线（Constellation）",
    }.get(strand, strand.value)
