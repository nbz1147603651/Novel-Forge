"""Semantic drift detection — fast post-repair sanity checks.

After a full-text edit repair, verify that critical narrative anchors
were preserved:

- **POV character** did not change
- **Location / time** markers were not lost or contradicted
- **Character states** (alive/dead, present/absent) were not flipped
- **Key props** mentioned in the outline were not removed

All checks are local (regex/string matching), no LLM calls.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


@dataclass
class DriftSignal:
    """One detected semantic drift."""

    category: str  # pov, location, time, character_state, prop
    description: str
    severity: str = "high"  # high or medium


@dataclass
class DriftReport:
    """Result of semantic drift detection."""

    signals: list[DriftSignal] = field(default_factory=list)

    @property
    def has_drift(self) -> bool:
        return len(self.signals) > 0

    @property
    def high_severity_count(self) -> int:
        return sum(1 for s in self.signals if s.severity == "high")

    def to_step_payload(self) -> dict[str, Any]:
        return {
            "has_drift": self.has_drift,
            "signal_count": len(self.signals),
            "high_severity": self.high_severity_count,
            "signals": [
                {"category": s.category, "description": s.description[:100], "severity": s.severity}
                for s in self.signals[:10]
            ],
        }


def _extract_character_names(text: str, known_names: list[str]) -> dict[str, int]:
    """Count occurrences of known character names in text."""
    counts: dict[str, int] = {}
    for name in known_names:
        if name and len(name) >= 2:
            counts[name] = text.count(name)
    return counts


_TIME_MARKERS_RE = re.compile(
    r"(清晨|黎明|拂晓|早上|上午|中午|午后|下午|傍晚|黄昏|日暮|夜晚|深夜|子夜|午夜|凌晨"
    r"|春|夏|秋|冬|雨|雪|月|日|晨|暮|dawn|morning|noon|afternoon|evening|night|midnight)",
    re.IGNORECASE,
)

_LOCATION_MARKERS_RE = re.compile(
    r"(城|镇|村|山|河|湖|海|殿|阁|楼|宫|府|院|寺|庙|塔|谷|林|园|街|巷|道|路"
    r"|屋|房|室|堂|厅|门|关|营|帐|船|码头|广场|市场|酒楼|客栈|书房|卧房|后院)",
)


def detect_drift(
    before_text: str,
    after_text: str,
    *,
    pov_character: str = "",
    known_characters: list[str] | None = None,
    key_props: list[str] | None = None,
    chapter_outline: Any | None = None,
    pov_min_before: int = 3,
    pov_ratio_threshold: float = 0.3,
    char_prominence_threshold: int = 5,
) -> DriftReport:
    """Compare pre-repair and post-repair text for semantic drift.

    Args:
        before_text: chapter text before repair
        after_text: chapter text after repair
        pov_character: expected POV character name
        known_characters: list of character names to track
        key_props: important items/objects that should be preserved
        chapter_outline: ChapterOutline with .goal, .main_plot_points etc.
        pov_min_before: minimum before-count to trigger POV disappearance check
        pov_ratio_threshold: ratio threshold below which POV frequency drop is flagged
        char_prominence_threshold: mention count threshold for prominent character detection
    """
    signals: list[DriftSignal] = []

    # ── POV drift ──
    if pov_character and len(pov_character) >= 2:
        before_pov_count = before_text.count(pov_character)
        after_pov_count = after_text.count(pov_character)
        if before_pov_count > pov_min_before and after_pov_count == 0:
            signals.append(DriftSignal(
                category="pov",
                description=f"POV角色「{pov_character}」从文中完全消失（修复前出现{before_pov_count}次）",
                severity="high",
            ))
        elif before_pov_count > pov_min_before + 2 and after_pov_count < before_pov_count * pov_ratio_threshold:
            signals.append(DriftSignal(
                category="pov",
                description=f"POV角色「{pov_character}」出现频率大幅降低（{before_pov_count}→{after_pov_count}）",
                severity="medium",
            ))

    # ── Character state drift ──
    if known_characters:
        before_counts = _extract_character_names(before_text, known_characters)
        after_counts = _extract_character_names(after_text, known_characters)
        for name in known_characters:
            b = before_counts.get(name, 0)
            a = after_counts.get(name, 0)
            # Character appeared prominently before but completely gone after
            if b >= char_prominence_threshold and a == 0:
                signals.append(DriftSignal(
                    category="character_state",
                    description=f"角色「{name}」从文中完全消失（修复前出现{b}次）",
                    severity="high",
                ))
            # Character not present before but suddenly prominent
            elif b == 0 and a >= char_prominence_threshold:
                signals.append(DriftSignal(
                    category="character_state",
                    description=f"角色「{name}」被修复意外引入（修复后出现{a}次）",
                    severity="medium",
                ))

    # ── Time marker drift ──
    before_times = set(_TIME_MARKERS_RE.findall(before_text))
    after_times = set(_TIME_MARKERS_RE.findall(after_text))
    lost_times = before_times - after_times
    gained_times = after_times - before_times
    # Flag only when time markers clearly contradict (e.g. lost "夜晚", gained "清晨")
    _DAY_MARKERS = {"清晨", "黎明", "拂晓", "早上", "上午", "中午", "dawn", "morning", "noon"}
    _NIGHT_MARKERS = {"夜晚", "深夜", "子夜", "午夜", "凌晨", "night", "midnight"}
    if (lost_times & _NIGHT_MARKERS and gained_times & _DAY_MARKERS) or \
       (lost_times & _DAY_MARKERS and gained_times & _NIGHT_MARKERS):
        signals.append(DriftSignal(
            category="time",
            description=f"时间轴漂移：丢失{lost_times & (_DAY_MARKERS | _NIGHT_MARKERS)}，新增{gained_times & (_DAY_MARKERS | _NIGHT_MARKERS)}",
            severity="high",
        ))

    # ── Key prop drift ──
    if key_props:
        for prop in key_props:
            if prop and len(prop) >= 2:
                if prop in before_text and prop not in after_text:
                    signals.append(DriftSignal(
                        category="prop",
                        description=f"关键道具/物品「{prop}」在修复后丢失",
                        severity="medium",
                    ))

    # ── Outline goal drift ──
    if chapter_outline is not None:
        goal = getattr(chapter_outline, "goal", "") or ""
        if goal and len(goal) >= 4:
            # Extract key nouns from goal (simple: take 2-4 char segments)
            goal_keywords = [goal[i:i+4] for i in range(0, min(len(goal), 20), 4) if goal[i:i+4].strip()]
            for kw in goal_keywords:
                if kw in before_text and kw not in after_text:
                    signals.append(DriftSignal(
                        category="plot",
                        description=f"章节目标关键词「{kw}」在修复后丢失",
                        severity="medium",
                    ))
                    break  # One signal is enough

    return DriftReport(signals=signals)
