"""Current MiniMax T2A contract shared by the planner and HTTP adapter.

This module deliberately contains only documented, provider-facing behaviour.
Keeping the capability checks here prevents the script planner from emitting
markup that a selected MiniMax model would read literally.

References:
* https://platform.minimaxi.com/docs/api-reference/speech-t2a-websocket
* https://platform.minimaxi.com/docs/api-reference/speech-t2a-http
* https://platform.minimaxi.com/docs/guides/speech-t2a-websocket
* https://platform.minimaxi.com/docs/api-reference/music-generation
"""

from __future__ import annotations

from collections.abc import Iterable

MINIMAX_TTS_API_BASE_URL = "https://api.minimax.io/v1"
"""Official global (international) T2A HTTP endpoint."""

MINIMAX_TTS_CN_API_BASE_URL = "https://api.minimaxi.com/v1"
"""Official China-domestic T2A HTTP endpoint."""

MINIMAX_TTS_LOW_TTFA_API_BASE_URL = "https://api-uw.minimax.io/v1"
"""Official alternate endpoint optimized for time-to-first-audio."""

MINIMAX_TTS_WS_URL = "wss://api.minimaxi.com/ws/v1/t2a_v2"
"""Official WebSocket streaming T2A endpoint (China-domestic)."""

MINIMAX_TTS_WS_GLOBAL_URL = "wss://api.minimax.io/ws/v1/t2a_v2"
"""Official WebSocket streaming T2A endpoint (international)."""

MINIMAX_T2A_INTERJECTION_MODELS = frozenset({"speech-2.8-hd", "speech-2.8-turbo"})
"""Only Speech 2.8 accepts inline interjection tags in T2A text."""

MINIMAX_T2A_ALL_MODELS = frozenset(
    {
        "speech-2.8-hd",
        "speech-2.8-turbo",
        "speech-2.6-hd",
        "speech-2.6-turbo",
        "speech-02-hd",
        "speech-02-turbo",
        "speech-01-hd",
        "speech-01-turbo",
    }
)
"""All officially documented T2A model IDs (2026-07)."""

MINIMAX_T2A_CONTINUOUS_SOUND_MODELS = frozenset({"speech-2.8-hd", "speech-2.8-turbo"})
"""Models supporting the continuous_sound parameter for long-text prosody."""

MINIMAX_MUSIC_ALL_MODELS = frozenset(
    {
        "music-3.0",
        "music-2.6",
        "music-cover",
        "music-3.0-free",
        "music-2.6-free",
        "music-cover-free",
    }
)
"""All officially documented Music Generation model IDs (2026-07)."""

MINIMAX_MUSIC_DEFAULT_MODEL = "music-3.0"
"""Recommended default music generation model."""

MINIMAX_T2A_INTERJECTIONS = (
    "(laughs)",
    "(chuckle)",
    "(coughs)",
    "(clear-throat)",
    "(groans)",
    "(breath)",
    "(pant)",
    "(inhale)",
    "(exhale)",
    "(gasps)",
    "(sniffs)",
    "(sighs)",
    "(snorts)",
    "(burps)",
    "(lip-smacking)",
    "(humming)",
    "(hissing)",
    "(emm)",
    "(sneezes)",
)

MINIMAX_NATIVE_EMOTIONS = frozenset(
    {
        "calm",
        "happy",
        "sad",
        "angry",
        "fearful",
        "disgusted",
        "surprised",
        "fluent",
        "whisper",
    }
)
"""The nine documented Speech emotions; all other story labels are projected to these.

Model constraints:
- ``fluent`` and ``whisper`` are only effective on speech-2.6-hd / speech-2.6-turbo.
- speech-2.8-hd / speech-2.8-turbo do NOT support ``whisper``.
"""

MINIMAX_EMOTION_MODEL_RESTRICTIONS: dict[str, frozenset[str]] = {
    "whisper": frozenset({"speech-2.6-hd", "speech-2.6-turbo"}),
    "fluent": frozenset({"speech-2.6-hd", "speech-2.6-turbo"}),
}
"""Emotions that are only effective on specific model families."""

MINIMAX_NON_STREAMING_AUDIO_FORMATS = frozenset({"mp3", "wav", "flac"})
"""Formats accepted by the HTTP (non-streaming) T2A endpoint."""

MINIMAX_STREAMING_AUDIO_FORMATS = frozenset(
    {"mp3", "pcm", "flac", "wav", "pcmu_raw", "pcmu_wav", "opus"}
)
"""Formats accepted by the WebSocket streaming T2A endpoint."""

MINIMAX_MUSIC_AUDIO_FORMATS = frozenset({"mp3", "wav", "pcm"})
"""Formats accepted by the Music Generation endpoint."""

MINIMAX_MUSIC_SAMPLE_RATES = frozenset({16000, 24000, 32000, 44100})
"""Sample rates accepted by the Music Generation endpoint."""

_EMOTION_ALIASES = {
    "neutral": "calm",
    "happy": "happy",
    "sad": "sad",
    "angry": "angry",
    "fearful": "fearful",
    "surprised": "surprised",
    "disgusted": "disgusted",
    "fluent": "fluent",
    "whisper": "whisper",
    "tender": "calm",
    "mocking": "happy",  # 嘲讽带有戏谑，更接近愉悦而非平静
    "nostalgic": "sad",
    "anxious": "fearful",
    "contempt": "disgusted",  # 轻蔑接近厌恶
    "determined": "angry",  # 坚定带有力量感，接近愤怒的力度
    "playful": "happy",
}

_LANGUAGE_BOOST_ALIASES = {
    "auto": "auto",
    "zh": "Chinese",
    "zh-cn": "Chinese",
    "zh-hans": "Chinese",
    "chinese": "Chinese",
    "yue": "Chinese,Yue",
    "zh-yue": "Chinese,Yue",
    "chinese,yue": "Chinese,Yue",
    "en": "English",
    "english": "English",
    "ar": "Arabic",
    "arabic": "Arabic",
    "ru": "Russian",
    "russian": "Russian",
    "es": "Spanish",
    "spanish": "Spanish",
    "fr": "French",
    "french": "French",
    "pt": "Portuguese",
    "pt-br": "Portuguese",
    "pt-pt": "Portuguese",
    "portuguese": "Portuguese",
    "de": "German",
    "german": "German",
    "tr": "Turkish",
    "turkish": "Turkish",
    "nl": "Dutch",
    "dutch": "Dutch",
    "uk": "Ukrainian",
    "ukrainian": "Ukrainian",
    "vi": "Vietnamese",
    "vietnamese": "Vietnamese",
    "id": "Indonesian",
    "indonesian": "Indonesian",
    "ja": "Japanese",
    "japanese": "Japanese",
    "it": "Italian",
    "italian": "Italian",
    "ko": "Korean",
    "korean": "Korean",
    "th": "Thai",
    "thai": "Thai",
    "pl": "Polish",
    "polish": "Polish",
    "ro": "Romanian",
    "romanian": "Romanian",
    "el": "Greek",
    "greek": "Greek",
    "cs": "Czech",
    "czech": "Czech",
    "fi": "Finnish",
    "finnish": "Finnish",
    "hi": "Hindi",
    "hindi": "Hindi",
    "bg": "Bulgarian",
    "bulgarian": "Bulgarian",
    "da": "Danish",
    "danish": "Danish",
    "he": "Hebrew",
    "hebrew": "Hebrew",
    "ms": "Malay",
    "malay": "Malay",
    "fa": "Persian",
    "persian": "Persian",
    "sk": "Slovak",
    "slovak": "Slovak",
    "sv": "Swedish",
    "swedish": "Swedish",
    "hr": "Croatian",
    "croatian": "Croatian",
    "fil": "Filipino",
    "filipino": "Filipino",
    "hu": "Hungarian",
    "hungarian": "Hungarian",
    "no": "Norwegian",
    "norwegian": "Norwegian",
    "sl": "Slovenian",
    "slovenian": "Slovenian",
    "ca": "Catalan",
    "catalan": "Catalan",
    "nn": "Nynorsk",
    "no-nn": "Nynorsk",
    "nynorsk": "Nynorsk",
    "ta": "Tamil",
    "tamil": "Tamil",
    "af": "Afrikaans",
    "afrikaans": "Afrikaans",
}

_PRICE_USD_PER_CHARACTER = {
    "speech-2.8-hd": 0.0001,
    "speech-2.6-hd": 0.0001,
    "speech-02-hd": 0.0001,
    "speech-01-hd": 0.0001,
    "speech-2.8-turbo": 0.00006,
    "speech-2.6-turbo": 0.00006,
    "speech-02-turbo": 0.00006,
    "speech-01-turbo": 0.00006,
}


def minimax_model_id(model_id: str) -> str:
    """Return a normalized model name without making future models invalid."""

    return str(model_id or "").strip().lower()


def minimax_supports_interjections(model_id: str) -> bool:
    """Whether a selected T2A model accepts inline interjection tags."""

    return minimax_model_id(model_id) in MINIMAX_T2A_INTERJECTION_MODELS


def minimax_native_emotion(value: str) -> str:
    """Project a story emotion to MiniMax's nine native emotions."""

    normalized = str(value or "").strip().lower()
    return _EMOTION_ALIASES.get(normalized, "")


def minimax_emotion_allowed_for_model(emotion: str, model_id: str) -> bool:
    """Check whether an emotion is effective on the given model.

    Returns True if the emotion has no model restriction or the model is in
    the allowed set.  Returns False when the emotion is restricted (e.g.
    ``whisper`` on speech-2.8).
    """
    normalized_emotion = str(emotion or "").strip().lower()
    normalized_model = minimax_model_id(model_id)
    allowed_models = MINIMAX_EMOTION_MODEL_RESTRICTIONS.get(normalized_emotion)
    if allowed_models is None:
        return True
    return normalized_model in allowed_models


def minimax_language_boost(value: str) -> str:
    """Return an official ``language_boost`` enum value, falling back to auto."""

    normalized = str(value or "").strip().lower().replace("_", "-")
    return _LANGUAGE_BOOST_ALIASES.get(normalized, "auto")


def minimax_valid_interjections(tags: Iterable[str]) -> list[str]:
    """Keep only documented T2A interjection tags, preserving their order."""

    return [str(tag) for tag in tags if str(tag) in MINIMAX_T2A_INTERJECTIONS]


def minimax_estimated_t2a_cost_usd(model_id: str, character_count: int) -> float:
    """Estimate current pay-as-you-go T2A cost from the selected model tier."""

    per_character = _PRICE_USD_PER_CHARACTER.get(minimax_model_id(model_id), 0.0001)
    return max(0, int(character_count)) * per_character
