"""SSML builder — converts DubbingSegment annotations to SSML markup.

Generates Speech Synthesis Markup Language only for providers whose concrete
endpoint accepts it. Other providers receive their own native annotations.
"""

from __future__ import annotations

from novel_forge.tts.platform.minimax_contract import minimax_supports_interjections
from novel_forge.tts.schemas import (
    DubbingSegment,
    EmotionTag,
    ParalinguisticTag,
    TTSProvider,
)

# Emotion → SSML prosody attribute mapping
_EMOTION_PROSODY: dict[EmotionTag, dict[str, str]] = {
    EmotionTag.NEUTRAL: {"rate": "medium", "pitch": "+0st", "volume": "medium"},
    EmotionTag.HAPPY: {"rate": "medium", "pitch": "+2st", "volume": "loud"},
    EmotionTag.SAD: {"rate": "slow", "pitch": "-3st", "volume": "soft"},
    EmotionTag.ANGRY: {"rate": "fast", "pitch": "+3st", "volume": "x-loud"},
    EmotionTag.FEARFUL: {"rate": "fast", "pitch": "+1st", "volume": "soft"},
    EmotionTag.SURPRISED: {"rate": "medium", "pitch": "+3st", "volume": "loud"},
    EmotionTag.DISGUSTED: {"rate": "slow", "pitch": "-1st", "volume": "medium"},
    EmotionTag.TENDER: {"rate": "slow", "pitch": "-1st", "volume": "soft"},
    EmotionTag.MOCKING: {"rate": "medium", "pitch": "+1st", "volume": "medium"},
    EmotionTag.WHISPER: {"rate": "x-slow", "pitch": "+0st", "volume": "x-soft"},
    EmotionTag.NOSTALGIC: {"rate": "slow", "pitch": "-1st", "volume": "soft"},
    EmotionTag.ANXIOUS: {"rate": "fast", "pitch": "+1st", "volume": "medium"},
    EmotionTag.CONTEMPT: {"rate": "slow", "pitch": "+0st", "volume": "loud"},
    EmotionTag.DETERMINED: {"rate": "medium", "pitch": "+1st", "volume": "loud"},
    EmotionTag.PLAYFUL: {"rate": "medium", "pitch": "+2st", "volume": "loud"},
}

# Providers that support SSML
_SSML_PROVIDERS: frozenset[TTSProvider] = frozenset(
    {
        TTSProvider.TENCENT,
    }
)


def should_use_ssml(segment: DubbingSegment, provider: TTSProvider) -> bool:
    """Determine whether SSML should be used for this segment/provider combo."""
    return provider in _SSML_PROVIDERS


def build_ssml(segment: DubbingSegment, voice_id: str, *, provider: TTSProvider) -> str:
    """Build SSML markup for a dubbing segment.

    Args:
        segment: The dubbing segment with emotion/paralinguistic annotations.
        voice_id: Target voice identifier.
        provider: TTS provider (determines SSML dialect support).

    Returns:
        Complete SSML document string.
    """
    prosody = _EMOTION_PROSODY.get(segment.emotion, _EMOTION_PROSODY[EmotionTag.NEUTRAL])

    parts: list[str] = []
    parts.append(
        '<speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis" xml:lang="zh-CN">'
    )

    # Pre-text paralinguistic tags (pauses, breaths at sentence start)
    pre_tags = [t for t in segment.paralinguistic_tags if t.position < 0.2]
    for tag in pre_tags:
        parts.append(_render_paralinguistic(tag))

    # Main prosody wrapper
    rate = _apply_speed_override(prosody["rate"], segment.speed_override)
    parts.append(f'<prosody rate="{rate}" pitch="{prosody["pitch"]}" volume="{prosody["volume"]}">')

    # Text with inline paralinguistic tags
    text = segment.text
    mid_tags = [t for t in segment.paralinguistic_tags if 0.2 <= t.position <= 0.8]

    if mid_tags:
        # Insert tags at approximate positions
        parts.append(_insert_inline_tags(text, mid_tags))
    else:
        # Stress word emphasis
        if segment.stress_words:
            parts.append(_apply_stress_words(text, segment.stress_words))
        else:
            parts.append(_escape_xml(text))

    parts.append("</prosody>")

    # Post-text paralinguistic tags (trailing pauses, sighs)
    post_tags = [t for t in segment.paralinguistic_tags if t.position > 0.8]
    for tag in post_tags:
        parts.append(_render_paralinguistic(tag))

    parts.append("</speak>")
    return "".join(parts)


def build_minimax_emotion_text(
    segment: DubbingSegment,
    *,
    model_id: str = "speech-2.8-hd",
) -> str:
    """Build MiniMax text with supported inline sound and pause tags.

    Emotion itself is sent through ``voice_setting.emotion`` by the adapter.
    MiniMax 2.8 sound tags use forms such as ``(laughs)`` and fixed pauses use
    ``<#0.30#>``; XML/SSML wrappers would otherwise be spoken literally.

    Stress words are highlighted by inserting micro-pauses (``<#0.05#>``)
    before and after the word, creating rhythmic emphasis without altering
    the word's pronunciation.
    """
    text = segment.synthesis_text
    tags = sorted(segment.paralinguistic_tags, key=lambda item: item.position)
    if tags:
        parts: list[str] = []
        cursor = 0
        for tag in tags:
            position = max(cursor, min(len(text), int(len(text) * tag.position)))
            if position > cursor:
                parts.append(text[cursor:position])
            rendered = _render_minimax_tag(
                tag,
                supports_interjections=minimax_supports_interjections(model_id),
            )
            if rendered:
                parts.append(rendered)
            cursor = position
        parts.append(text[cursor:])
        text = "".join(parts)

    # Stress words: insert micro-pauses to create rhythmic emphasis
    if segment.stress_words:
        for word in segment.stress_words:
            if word and word in text:
                text = text.replace(word, f"<#0.05#>{word}<#0.05#>", 1)

    return text


def _render_minimax_tag(
    tag: ParalinguisticTag,
    *,
    supports_interjections: bool,
) -> str:
    sound_tags = {
        "breath": "(breath)",
        "laugh": "(laughs)",
        "chuckle": "(chuckle)",
        "cough": "(coughs)",
        "clear_throat": "(clear-throat)",
        "groan": "(groans)",
        "pant": "(pant)",
        "inhale": "(inhale)",
        "exhale": "(exhale)",
        "sigh": "(sighs)",
        "choke": "(gasps)",
        "sniff": "(sniffs)",
        "snort": "(snorts)",
        "lip_smacking": "(lip-smacking)",
        "hum": "(humming)",
        "hiss": "(hissing)",
        "hesitate": "(emm)",
        "sneeze": "(sneezes)",
    }
    if tag.tag_type in {"pause", "silence", "stutter", "emphasis"}:
        seconds = max(0.01, min(99.99, (tag.duration_ms or 250) / 1000))
        return f"<#{seconds:.2f}#>"
    # Fixed pause markers are supported by current T2A models, while sound
    # interjections are only valid for Speech 2.8.  Never let a legacy model
    # read a sound tag as literal punctuation.
    return sound_tags.get(tag.tag_type, "") if supports_interjections else ""


# ─── Internal helpers ────────────────────────────────────────────────────────


def _render_paralinguistic(tag: ParalinguisticTag) -> str:
    """Render a paralinguistic tag to SSML."""
    if tag.tag_type in ("pause", "silence"):
        duration = tag.duration_ms or 300
        return f'<break time="{duration}ms"/>'
    if tag.tag_type == "breath":
        duration = max(200, tag.duration_ms or 250)
        return f'<break time="{duration}ms"/>'
    if tag.tag_type == "emphasis":
        return '<emphasis level="strong">'
    if tag.tag_type == "laugh":
        return '<break time="200ms"/>'  # Most TTS engines can't synthesize laughter
    if tag.tag_type in ("sigh", "choke"):
        return f'<break time="{max(200, tag.duration_ms or 300)}ms"/>'
    if tag.tag_type == "hum":
        return '<break time="150ms"/>'
    if tag.tag_type == "stutter":
        return '<break time="100ms"/>'
    return f'<break time="{tag.duration_ms or 200}ms"/>'


def _insert_inline_tags(text: str, tags: list[ParalinguisticTag]) -> str:
    """Insert paralinguistic SSML tags at approximate text positions."""
    if not tags or not text:
        return _escape_xml(text)

    result_parts: list[str] = []
    cursor = 0

    for tag in sorted(tags, key=lambda t: t.position):
        pos = int(len(text) * tag.position)
        pos = max(cursor, min(pos, len(text)))

        if pos > cursor:
            result_parts.append(_escape_xml(text[cursor:pos]))
        result_parts.append(_render_paralinguistic(tag))
        cursor = pos

    if cursor < len(text):
        result_parts.append(_escape_xml(text[cursor:]))

    return "".join(result_parts)


def _apply_stress_words(text: str, stress_words: list[str]) -> str:
    """Wrap stress words with emphasis tags."""
    result = _escape_xml(text)
    for word in stress_words:
        escaped_word = _escape_xml(word)
        if escaped_word in result:
            result = result.replace(
                escaped_word,
                f'<emphasis level="strong">{escaped_word}</emphasis>',
                1,
            )
    return result


def _apply_speed_override(rate: str, speed_override: float | None) -> str:
    """Adjust SSML rate attribute based on speed override."""
    if speed_override is None:
        return rate

    speed_map = {
        "x-slow": 0.5,
        "slow": 0.75,
        "medium": 1.0,
        "fast": 1.25,
        "x-fast": 1.5,
    }

    base = speed_map.get(rate, 1.0)
    adjusted = base * speed_override

    if adjusted < 0.6:
        return "x-slow"
    if adjusted < 0.85:
        return "slow"
    if adjusted < 1.15:
        return "medium"
    if adjusted < 1.4:
        return "fast"
    return "x-fast"


def _escape_xml(text: str) -> str:
    """Escape XML special characters in text."""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )
