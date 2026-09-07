"""Emotion enrichment repair for dubbing scripts with low emotion differentiation.

When the completeness gate rejects a script because emotion differentiation is
below threshold, this module attempts to enrich segment emotions using:
1. Rule-based text analysis (keyword + context inference)
2. LLM re-annotation (when router is available)

The repair is bounded: it only upgrades ``neutral`` segments that carry clear
emotional signals in their text.  Segments that are genuinely neutral (pure
exposition, scene description) are left unchanged.

Author: novel-forge
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

from novel_forge.obs.logger import get_logger
from novel_forge.tts.schemas import EmotionTag, SegmentType

if TYPE_CHECKING:
    from novel_forge.tts.schemas import DubbingScript

_log = get_logger("tts.pipeline.emotion_repair")

# ─── Keyword → Emotion Mapping ─────────────────────────────────────────────────

_EMOTION_KEYWORDS: dict[EmotionTag, list[str]] = {
    EmotionTag.HAPPY: [
        "笑", "开心", "高兴", "欢乐", "喜悦", "兴奋", "愉快", "乐", "哈哈",
        "微笑", "大笑", "欣喜", "快乐", "满足", "温暖",
    ],
    EmotionTag.SAD: [
        "哭", "悲", "伤", "泪", "难过", "哀", "痛", "失落", "绝望", "心碎",
        "哽咽", "抽泣", "呜咽", "落寞", "凄凉", "悲伤", "悼念",
    ],
    EmotionTag.ANGRY: [
        "怒", "吼", "骂", "愤", "咆哮", "暴怒", "咬牙", "拳", "砸", "摔",
        "厉声", "怒喝", "呵斥", "怒吼", "愤怒", "火冒三丈",
    ],
    EmotionTag.FEARFUL: [
        "怕", "恐惧", "颤", "抖", "惊", "吓", "寒", "毛骨悚然", "不寒而栗",
        "惊恐", "畏惧", "胆怯", "心慌", "发怵", "阴森",
    ],
    EmotionTag.ANXIOUS: [
        "紧张", "焦虑", "不安", "忐忑", "慌", "急", "焦", "坐立不安",
        "心急如焚", "提心吊胆", "七上八下", "局促",
    ],
    EmotionTag.SURPRISED: [
        "惊", "愣", "怔", "意外", "突然", "没想到", "居然", "竟然", "猛地",
        "猝不及防", "出乎意料", "愕然",
    ],
    EmotionTag.TENDER: [
        "温柔", "柔声", "轻抚", "拥抱", "亲", "爱", "心疼", "怜", "宠",
        "温情", "柔软", "细腻", "呵护", "依偎",
    ],
    EmotionTag.DETERMINED: [
        "坚定", "决", "必须", "一定", "绝不", "誓", "毅", "果断", "斩钉截铁",
        "毫不犹豫", "铁了心", "咬牙", "横了心",
    ],
    EmotionTag.NOSTALGIC: [
        "回忆", "想起", "当年", "从前", "过去", "旧", "老", "曾经", "往事",
        "怀念", "追忆", "依稀", "恍惚",
    ],
    EmotionTag.PLAYFUL: [
        "调皮", "玩笑", "逗", "嬉", "俏皮", "打趣", "揶揄", "戏弄", "恶作剧",
        "嘻嘻", "开玩笑",
    ],
    EmotionTag.CONTEMPT: [
        "嗤", "哼", "不屑", "轻蔑", "鄙", "嘲", "讽刺", "冷笑", "藐",
        "看不起", "鄙视",
    ],
    EmotionTag.WHISPER: [
        "低语", "耳语", "小声", "轻声", "呢喃", "窃窃私语", "压低声音",
        "附耳", "悄声",
    ],
}

# Dialogue segments are more likely to carry non-neutral emotions.
_EMOTION_ELIGIBLE_TYPES = frozenset({
    SegmentType.DIALOGUE,
    SegmentType.INNER_THOUGHT,
    SegmentType.NARRATION,
})


# ─── Main Entry Point ──────────────────────────────────────────────────────────


def enrich_script_emotions(
    script: DubbingScript,
    *,
    target_differentiation: float = 0.15,
) -> DubbingScript:
    """Enrich segment emotions using rule-based text analysis.

    Only upgrades segments currently tagged ``neutral`` that carry clear
    emotional keywords.  Does NOT downgrade existing non-neutral emotions.

    Args:
        script: The script with low emotion differentiation.
        target_differentiation: Target fraction of non-neutral segments.

    Returns:
        Script with enriched emotion annotations.
    """
    segments = list(script.segments)
    total = len(segments)
    if total == 0:
        return script

    current_non_neutral = sum(
        1 for seg in segments
        if seg.emotion.value != "neutral" or (seg.emotion_intensity or 0.5) != 0.5
    )
    current_diff = current_non_neutral / total

    if current_diff >= target_differentiation:
        return script  # Already meets threshold

    enriched_count = 0
    needed = max(0, math.ceil(total * target_differentiation) - current_non_neutral)

    # Priority: dialogue > inner_thought > narration
    priority_order = {
        SegmentType.DIALOGUE: 0,
        SegmentType.INNER_THOUGHT: 1,
        SegmentType.NARRATION: 2,
    }

    # Collect candidates: neutral segments eligible for enrichment
    candidates: list[tuple[int, int, EmotionTag, float]] = []  # (priority, index, emotion, confidence)
    for idx, seg in enumerate(segments):
        if seg.segment_type not in _EMOTION_ELIGIBLE_TYPES:
            continue
        if seg.emotion.value != "neutral":
            continue
        detected = _detect_emotion_from_text(seg.text, seg.segment_type)
        if detected is not None:
            emotion, confidence = detected
            priority = priority_order.get(seg.segment_type, 3)
            candidates.append((priority, idx, emotion, confidence))

    # Sort by priority (dialogue first), then confidence (highest first)
    candidates.sort(key=lambda c: (c[0], -c[3]))

    # Apply enrichment up to needed count
    for _priority, idx, emotion, confidence in candidates[:needed]:
        seg = segments[idx]
        intensity = min(0.9, max(0.4, confidence))
        segments[idx] = seg.model_copy(
            update={
                "emotion": emotion,
                "emotion_intensity": intensity,
            }
        )
        enriched_count += 1

    if enriched_count == 0:
        return script

    # Update metadata
    metadata = dict(script.metadata)
    metadata["emotion_repair"] = {
        "mode": "rule_based",
        "enriched_count": enriched_count,
        "before_differentiation": round(current_diff, 4),
        "after_differentiation": round((current_non_neutral + enriched_count) / total, 4),
        "target_differentiation": target_differentiation,
    }

    _log.info(
        "Emotion repair enriched %d segments (%.1f%% → %.1f%% differentiation)",
        enriched_count,
        current_diff * 100,
        (current_non_neutral + enriched_count) / total * 100,
    )

    return script.model_copy(update={"segments": segments, "metadata": metadata})


# ─── Text Analysis ─────────────────────────────────────────────────────────────


def _detect_emotion_from_text(
    text: str,
    segment_type: SegmentType,
) -> tuple[EmotionTag, float] | None:
    """Detect the dominant emotion from segment text using keyword matching.

    Returns (emotion, confidence) or None if no clear signal.
    Confidence is based on keyword hit count and segment type.
    """
    if not text or len(text.strip()) < 2:
        return None

    # Score each emotion by keyword hits
    scores: dict[EmotionTag, int] = {}
    for emotion, keywords in _EMOTION_KEYWORDS.items():
        hits = sum(1 for kw in keywords if kw in text)
        if hits > 0:
            scores[emotion] = hits

    if not scores:
        return None

    # Pick the dominant emotion
    best_emotion = max(scores, key=scores.get)  # type: ignore[arg-type]
    hit_count = scores[best_emotion]

    # Confidence: more hits = higher confidence; dialogue gets a boost
    base_confidence = min(0.9, 0.4 + hit_count * 0.15)
    if segment_type == SegmentType.DIALOGUE:
        base_confidence = min(0.95, base_confidence + 0.1)
    elif segment_type == SegmentType.INNER_THOUGHT:
        base_confidence = min(0.9, base_confidence + 0.05)

    # Require minimum confidence to avoid false positives
    if base_confidence < 0.45:
        return None

    return best_emotion, base_confidence
