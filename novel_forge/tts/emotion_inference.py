"""Deterministic emotion inference from text — shared by pipeline and TTS modules.

Single source of truth for keyword-based emotion classification.  Both the
chapter finalize stage (TTS metadata extraction) and the TTS script generation
step consume this module so that emotion labels stay consistent across the
creation → dubbing boundary.
"""

from __future__ import annotations

from novel_forge.tts.schemas import EmotionTag

# Keyword groups ordered by EmotionTag enum definition.  This supersedes the
# former duplicate keyword tables that lived inline in the finalize and
# generate-script stages.
#
# NOTE: Single-character keywords that commonly appear in neutral/descriptive
# contexts (e.g. "气" in "空气/气氛/湿气", "惊" in "惊鸿/惊艳") have been
# upgraded to multi-character phrases to eliminate false positives.
_EMOTION_KEYWORDS: dict[EmotionTag, list[str]] = {
    EmotionTag.HAPPY: [
        "开心", "高兴", "快乐", "欢喜", "喜悦", "欢笑", "笑出声",
        "乐呵", "愉悦", "欣喜", "欣慰",
    ],
    EmotionTag.SAD: [
        "哭泣", "悲伤", "伤心", "难过", "哀伤", "落泪", "泪眼",
        "悲痛", "心酸", "凄然", "哀痛",
    ],
    EmotionTag.ANGRY: [
        "愤怒", "怒气", "气愤", "暴怒", "怒吼", "怒骂", "恼怒",
        "暴跳", "愤恨", "怒斥", "愤懑", "咬牙切齿",
    ],
    EmotionTag.FEARFUL: [
        "害怕", "恐惧", "惊恐", "惧怕", "颤抖", "畏惧", "胆寒",
        "毛骨悚然", "不寒而栗", "心慌",
    ],
    EmotionTag.SURPRISED: [
        "惊讶", "吃惊", "意外", "震惊", "诧异", "愕然",
        "不可思议", "出乎意料",
    ],
    EmotionTag.TENDER: [
        "温柔", "柔声", "轻声", "柔和", "温情", "怜爱",
        "慈爱", "柔情",
    ],
    EmotionTag.WHISPER: ["低声", "耳语", "悄悄", "细语", "呢喃", "低语"],
    EmotionTag.NOSTALGIC: [
        "往事", "当年", "曾经", "回忆", "怀念", "追忆",
        "旧日", "往昔",
    ],
    EmotionTag.ANXIOUS: [
        "不安", "紧张", "担忧", "焦虑", "焦急", "慌张",
        "忐忑", "坐立不安", "心急",
    ],
    EmotionTag.DETERMINED: [
        "坚定", "坚决", "绝不", "誓死", "铁了心",
        "义无反顾", "破釜沉舟",
    ],
}

# High-arousal emotions that should be suppressed in opening narration.
_HIGH_AROUSAL_EMOTIONS: frozenset[EmotionTag] = frozenset(
    {EmotionTag.ANGRY, EmotionTag.FEARFUL, EmotionTag.SURPRISED}
)


def infer_dominant_emotion(text: str) -> EmotionTag:
    """Return the dominant emotion detected in *text* via keyword matching.

    This is a pure, deterministic function — no LLM calls.  It scans the text
    for the first matching emotion keyword group (ordered by ``EmotionTag``
    enum definition) and returns that tag.  Returns ``EmotionTag.NEUTRAL``
    when no keywords match.
    """
    for emotion, keywords in _EMOTION_KEYWORDS.items():
        if any(kw in text for kw in keywords):
            return emotion
    return EmotionTag.NEUTRAL


def infer_emotion_with_context(
    text: str,
    *,
    chapter_number: int = 0,
    segment_position: int = 0,
    total_segments: int = 1,
    is_narration: bool = True,
) -> tuple[EmotionTag, float]:
    """Infer emotion with narrative-position awareness.

    Applies the "opening calm principle": when the segment is in the opening
    of a chapter (especially chapter 1), high-arousal emotions on narration
    are downgraded to neutral/tender with capped intensity, because opening
    narration should establish atmosphere rather than perform peak emotion.

    Args:
        text: The segment text to analyze.
        chapter_number: 1-based chapter number (0 = unknown).
        segment_position: 0-based position of this segment in the chapter.
        total_segments: Total segment count in the chapter.
        is_narration: Whether this is a narration segment (vs dialogue).

    Returns:
        A tuple of (emotion, intensity).
    """
    emotion = infer_dominant_emotion(text)
    intensity = 0.5

    if not is_narration:
        # Dialogue segments keep their inferred emotion without position cap.
        return emotion, intensity

    # Determine if we are in the "opening zone" of the chapter.
    # Only apply position-aware capping when we have meaningful segment count.
    if total_segments <= 1:
        return emotion, intensity

    is_first_chapter = chapter_number == 1
    is_very_early = segment_position < 3
    position_ratio = segment_position / max(1, total_segments)
    is_early_zone = position_ratio < 0.1

    if is_first_chapter and is_very_early and emotion in _HIGH_AROUSAL_EMOTIONS:
        # Opening of chapter 1: suppress high-arousal on narration.
        return EmotionTag.NEUTRAL, 0.3

    if is_early_zone and emotion in _HIGH_AROUSAL_EMOTIONS:
        # Early zone of any chapter: cap intensity.
        return emotion, min(intensity, 0.4)

    return emotion, intensity
