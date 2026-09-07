"""Provider field mappings for vendor-neutral vocal direction tags."""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field

from novel_forge.tts.platform.minimax_contract import minimax_language_boost


class FieldSupport(str, Enum):
    NATIVE = "native"
    PORTABLE = "portable"
    DIRECTOR_ONLY = "director_only"
    UNSUPPORTED = "unsupported"


class VocalDirectionField(str, Enum):
    EMOTION = "emotion"
    TONE = "tone"
    SPEED = "speed"
    VOLUME = "volume"
    PITCH = "pitch"
    STRESS = "stress"
    PARALINGUISTIC = "paralinguistic"
    SPEED_CURVE = "speed_curve"
    PRONUNCIATION = "pronunciation"
    LANGUAGE = "language"
    DELIVERY_STYLE = "delivery_style"
    ENERGY = "energy"
    ARTICULATION = "articulation"
    BREATHINESS = "breathiness"
    RESONANCE = "resonance"
    TENSION = "tension"
    INTIMACY = "intimacy"
    SPATIAL_EFFECT = "spatial_effect"


class ProviderFieldMapping(BaseModel):
    support: FieldSupport
    target_field: str = ""
    note: str = ""


class ProviderFieldProfile(BaseModel):
    provider_id: str
    mappings: dict[VocalDirectionField, ProviderFieldMapping] = Field(default_factory=dict)

    def mapping_for(self, field: VocalDirectionField) -> ProviderFieldMapping:
        return self.mappings.get(
            field,
            ProviderFieldMapping(
                support=FieldSupport.DIRECTOR_ONLY,
                note="保留为可审计导演提示，当前适配器不宣称原生执行。",
            ),
        )


def _mapping(
    support: FieldSupport,
    target: str = "",
    note: str = "",
) -> ProviderFieldMapping:
    return ProviderFieldMapping(support=support, target_field=target, note=note)


_BASE = {
    VocalDirectionField.SPEED: _mapping(FieldSupport.PORTABLE, "speed"),
    VocalDirectionField.VOLUME: _mapping(FieldSupport.PORTABLE, "volume"),
    VocalDirectionField.PITCH: _mapping(FieldSupport.PORTABLE, "pitch"),
    VocalDirectionField.TONE: _mapping(FieldSupport.DIRECTOR_ONLY, "metadata.tone_hint"),
    VocalDirectionField.DELIVERY_STYLE: _mapping(
        FieldSupport.DIRECTOR_ONLY, "metadata.vocal_direction.delivery_style"
    ),
    VocalDirectionField.ENERGY: _mapping(
        FieldSupport.DIRECTOR_ONLY, "metadata.vocal_direction.energy"
    ),
    VocalDirectionField.ARTICULATION: _mapping(
        FieldSupport.DIRECTOR_ONLY, "metadata.vocal_direction.articulation"
    ),
    VocalDirectionField.BREATHINESS: _mapping(
        FieldSupport.DIRECTOR_ONLY, "metadata.vocal_direction.breathiness"
    ),
    VocalDirectionField.RESONANCE: _mapping(
        FieldSupport.DIRECTOR_ONLY, "metadata.vocal_direction.resonance"
    ),
    VocalDirectionField.TENSION: _mapping(
        FieldSupport.DIRECTOR_ONLY, "metadata.vocal_direction.tension"
    ),
    VocalDirectionField.INTIMACY: _mapping(
        FieldSupport.DIRECTOR_ONLY, "metadata.vocal_direction.intimacy"
    ),
}


def _profile(provider_id: str, **overrides: ProviderFieldMapping) -> ProviderFieldProfile:
    mappings = dict(_BASE)
    mappings.update({VocalDirectionField(key): value for key, value in overrides.items()})
    return ProviderFieldProfile(provider_id=provider_id, mappings=mappings)


PROVIDER_FIELD_PROFILES: dict[str, ProviderFieldProfile] = {
    "minimax": _profile(
        "minimax",
        emotion=_mapping(FieldSupport.NATIVE, "emotion"),
        stress=_mapping(FieldSupport.NATIVE, "text/dml"),
        paralinguistic=_mapping(FieldSupport.NATIVE, "text/dml"),
        pronunciation=_mapping(FieldSupport.NATIVE, "pronunciation_dict"),
        language=_mapping(FieldSupport.NATIVE, "language_boost"),
        spatial_effect=_mapping(FieldSupport.NATIVE, "voice_modify.sound_effects"),
        energy=_mapping(
            FieldSupport.DIRECTOR_ONLY,
            "metadata.vocal_direction.energy",
            "MiniMax voice_modify 会改变声线本体；AI 不自动把能量映射为 intensity。",
        ),
        resonance=_mapping(
            FieldSupport.DIRECTOR_ONLY,
            "metadata.vocal_direction.resonance",
            "MiniMax voice_modify 会改变声线本体；AI 不自动把共鸣映射为 timbre。",
        ),
    ),
    "qwen3": _profile(
        "qwen3",
        emotion=_mapping(FieldSupport.NATIVE, "emotion/instruct"),
        tone=_mapping(FieldSupport.NATIVE, "instruct"),
        pronunciation=_mapping(FieldSupport.NATIVE, "ref_text/instruct"),
        language=_mapping(FieldSupport.NATIVE, "language"),
        delivery_style=_mapping(FieldSupport.NATIVE, "instruct"),
        energy=_mapping(FieldSupport.NATIVE, "instruct"),
        articulation=_mapping(FieldSupport.NATIVE, "instruct"),
        breathiness=_mapping(FieldSupport.NATIVE, "instruct"),
        resonance=_mapping(FieldSupport.NATIVE, "instruct"),
        tension=_mapping(FieldSupport.NATIVE, "instruct"),
        intimacy=_mapping(FieldSupport.NATIVE, "instruct"),
        stress=_mapping(FieldSupport.NATIVE, "instruct"),
        paralinguistic=_mapping(FieldSupport.DIRECTOR_ONLY, "instruct"),
    ),
    "tencent": _profile(
        "tencent",
        emotion=_mapping(FieldSupport.NATIVE, "emotion_category"),
        stress=_mapping(FieldSupport.NATIVE, "ssml.emphasis"),
        paralinguistic=_mapping(FieldSupport.NATIVE, "ssml.break"),
        pronunciation=_mapping(FieldSupport.NATIVE, "ssml.phoneme"),
        language=_mapping(FieldSupport.DIRECTOR_ONLY),
    ),
    "volcengine_ark": _profile(
        "volcengine_ark",
        emotion=_mapping(FieldSupport.NATIVE, "req_params.additions.context_texts"),
        tone=_mapping(FieldSupport.NATIVE, "req_params.additions.context_texts"),
        delivery_style=_mapping(FieldSupport.NATIVE, "req_params.additions.context_texts"),
        energy=_mapping(FieldSupport.NATIVE, "req_params.additions.context_texts"),
        speed=_mapping(FieldSupport.NATIVE, "audio_params.speech_rate"),
        volume=_mapping(FieldSupport.NATIVE, "audio_params.loudness_rate"),
        pitch=_mapping(FieldSupport.NATIVE, "req_params.additions.post_process.pitch"),
        language=_mapping(FieldSupport.NATIVE, "req_params.additions.explicit_language"),
    ),
    "mimo": _profile(
        "mimo",
        emotion=_mapping(FieldSupport.NATIVE, "messages[user].content"),
        tone=_mapping(FieldSupport.NATIVE, "messages[user].content"),
        delivery_style=_mapping(FieldSupport.NATIVE, "messages[user].content"),
        energy=_mapping(FieldSupport.NATIVE, "messages[user].content"),
        stress=_mapping(FieldSupport.NATIVE, "messages[user].content"),
        paralinguistic=_mapping(FieldSupport.NATIVE, "messages[assistant].content"),
        language=_mapping(FieldSupport.NATIVE, "messages[user].content"),
    ),
    "dashscope": _profile(
        "dashscope",
        emotion=_mapping(FieldSupport.NATIVE, "input.instruction"),
        tone=_mapping(FieldSupport.NATIVE, "input.instruction"),
        paralinguistic=_mapping(FieldSupport.NATIVE, "input.text[rich_language_tag]"),
        pronunciation=_mapping(FieldSupport.NATIVE, "input.hot_fix.pronunciation"),
        language=_mapping(FieldSupport.NATIVE, "input.language_hints[0]"),
        delivery_style=_mapping(FieldSupport.NATIVE, "input.instruction"),
    ),
    "bailian": _profile(
        "bailian",
        emotion=_mapping(FieldSupport.NATIVE, "input.instruction"),
        tone=_mapping(FieldSupport.NATIVE, "input.instruction"),
        paralinguistic=_mapping(FieldSupport.NATIVE, "input.text[rich_language_tag]"),
        pronunciation=_mapping(FieldSupport.NATIVE, "input.hot_fix.pronunciation"),
        language=_mapping(FieldSupport.NATIVE, "input.language_hints[0]"),
        delivery_style=_mapping(FieldSupport.NATIVE, "input.instruction"),
    ),
    "cosyvoice": _profile(
        "cosyvoice",
        emotion=_mapping(FieldSupport.NATIVE, "instruct_text"),
        tone=_mapping(FieldSupport.NATIVE, "instruct_text"),
        language=_mapping(FieldSupport.NATIVE, "cross_lingual_text"),
        delivery_style=_mapping(FieldSupport.NATIVE, "instruct_text"),
        energy=_mapping(FieldSupport.NATIVE, "instruct_text"),
    ),
    "openvoice": _profile(
        "openvoice",
        language=_mapping(FieldSupport.NATIVE, "language"),
        emotion=_mapping(FieldSupport.DIRECTOR_ONLY),
    ),
    "local": _profile("local"),
    "mock": _profile("mock"),
}


def provider_field_profile(provider_id: str) -> ProviderFieldProfile:
    return PROVIDER_FIELD_PROFILES.get(provider_id, _profile(provider_id))


def provider_language_value(provider_id: str, language_code: str, legacy: str = "auto") -> str:
    """Translate a neutral language code only at the adapter boundary."""

    code = str(language_code or "auto").strip().lower().replace("_", "-")
    if code in {"", "auto"}:
        return legacy or "auto"
    if provider_id == "minimax":
        return minimax_language_boost(code)
    if provider_id == "qwen3":
        return {
            "zh": "Chinese",
            "zh-cn": "Chinese",
            "yue": "Cantonese",
            "en": "English",
            "ja": "Japanese",
            "ko": "Korean",
            "fr": "French",
            "de": "German",
            "es": "Spanish",
            "pt": "Portuguese",
            "ru": "Russian",
        }.get(code, code)
    return code
