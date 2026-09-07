"""Bailian (DashScope) TTS vocal-tag contract shared by planner and review.

Mirrors ``minimax_contract.py``: keeps provider-facing capability facts in one
place so the script pipeline never persists paralinguistic markup that the
selected Bailian model would read literally or silently drop.

Capability model (derived from the live adapter tables to avoid drift):

* AIGC-tag models (``dashscope_adapter._AIGC_TAG_MODELS``: qwen-audio-3.0-tts
  plus/flash, cosyvoice-v3-flash/plus, cosyvoice-v2) interpret a fixed set of
  inline performance control tags (``[laughing]`` / ``[sighing]`` / ...) when
  ``enable_aigc_tag`` is on, so script-level vocal tags mapping onto those
  controls are kept.
* All other Bailian models (qwen3-tts-flash, cosyvoice-v3.5-*, ...) express
  emotion through the natural-language ``instruction`` channel; inline vocal
  tags are not interpreted, so only structural timing tags (pause/silence)
  survive the platform contract.

References:
* https://help.aliyun.com/zh/model-studio/qwen-tts
* https://help.aliyun.com/zh/model-studio/cosyvoice-large-model-for-speech-synthesis
"""

from __future__ import annotations

from novel_forge.tts.gateway.adapters import dashscope_adapter

# Structural timing tags are platform-neutral: they are realized by the
# pipeline's prosody layer (duration_ms / position) rather than rendered as
# inline text markup, so every Bailian model keeps them.
_DASHSCOPE_STRUCTURAL_VOCAL_TAGS = frozenset({"pause", "silence"})

DASHSCOPE_AIGC_TAG_MODELS = frozenset(dashscope_adapter._AIGC_TAG_MODELS)
"""Bailian models that interpret inline AIGC performance control tags."""

DASHSCOPE_AIGC_VOCAL_TAGS = (
    frozenset(dashscope_adapter._QWEN_AUDIO_VOCAL_TAGS) | _DASHSCOPE_STRUCTURAL_VOCAL_TAGS
)
"""Vocal tag types performable by AIGC-tag models (performance + structural).

Derived from the adapter's ``_QWEN_AUDIO_VOCAL_TAGS`` mapping (laugh/laughter/
sigh/breath/cough/cry/whisper) plus the structural pause/silence tags.
"""

# Provider alias normalization, consistent with rewrite/expression profiles:
# several historical provider IDs all resolve to the Bailian platform.
_DASHSCOPE_PROVIDER_ALIASES = {
    "dashscope": "bailian",
    "qwen3": "bailian",
    "cosyvoice": "bailian",
}


def normalize_bailian_provider(provider_id: str) -> str:
    """Normalize a provider ID, resolving Bailian aliases (dashscope/qwen3/...)."""

    normalized = str(provider_id or "").strip().lower().replace("-", "_")
    return _DASHSCOPE_PROVIDER_ALIASES.get(normalized, normalized)


def is_bailian_provider(provider_id: str) -> bool:
    """Whether the provider ID refers to the Alibaba Bailian (DashScope) platform."""

    return normalize_bailian_provider(provider_id) == "bailian"


def dashscope_model_id(model_id: str) -> str:
    """Return a normalized model name without making future models invalid."""

    return str(model_id or "").strip().lower()


def dashscope_supports_aigc_tags(model_id: str) -> bool:
    """Whether the selected Bailian model interprets inline AIGC vocal tags."""

    return dashscope_model_id(model_id) in DASHSCOPE_AIGC_TAG_MODELS


def dashscope_supported_vocal_tags(model_id: str) -> frozenset[str]:
    """Vocal tag types the selected Bailian model can actually perform.

    AIGC-tag models keep the adapter's performable set (laugh/laughter/sigh/
    breath/cough/cry/whisper) plus structural pause/silence.  Instruction-only
    models (qwen3-tts-flash, cosyvoice-v3.5-*, ...) keep just pause/silence —
    every other dramatic intent must travel through the instruction channel.
    """

    if dashscope_supports_aigc_tags(model_id):
        return DASHSCOPE_AIGC_VOCAL_TAGS
    return _DASHSCOPE_STRUCTURAL_VOCAL_TAGS
