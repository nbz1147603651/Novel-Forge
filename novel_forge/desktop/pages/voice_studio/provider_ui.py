"""Declarative provider presentation metadata for Voice Studio.

Runtime capabilities still come from the provider adapter.  This module owns
only stable presentation facts so platform labels, models and settings groups
do not drift across the header and settings tab.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from novel_forge.tts.gateway.factory import (
    registered_tts_provider_ids,
    tts_adapter_metadata,
)
from novel_forge.tts.platform.config import registry_from_settings
from novel_forge.tts.platform.registry import AudioPluginRegistry
from novel_forge.tts.platform.schemas import AudioCapability
from novel_forge.tts.schemas import TTSProvider

if TYPE_CHECKING:
    from novel_forge.core.config import Settings


@dataclass(frozen=True)
class ProviderUiSpec:
    provider: TTSProvider
    label: str
    models: tuple[str, ...]
    settings_group: str


PROVIDER_UI_SPECS: tuple[ProviderUiSpec, ...] = (
    ProviderUiSpec(
        TTSProvider.MINIMAX,
        "MiniMax",
        (
            "speech-2.8-hd",
            "speech-2.8-turbo",
            "speech-2.6-hd",
            "speech-2.6-turbo",
            "speech-02-hd",
            "speech-02-turbo",
            "speech-01-hd",
            "speech-01-turbo",
        ),
        "minimax",
    ),
    ProviderUiSpec(
        TTSProvider.BAILIAN,
        "阿里百炼",
        (
            "qwen-audio-3.0-tts-plus",
            "qwen-audio-3.0-tts-flash",
            "cosyvoice-v3.5-plus",
            "cosyvoice-v3.5-flash",
            "qwen3-tts-flash",
            "qwen3-tts-instruct-flash",
            "qwen3-tts-vc-2026-01-22",
            "qwen3-tts-vd-2026-01-26",
            "cosyvoice-v3-plus",
            "cosyvoice-v3-flash",
        ),
        "dashscope",
    ),
    ProviderUiSpec(
        TTSProvider.DASHSCOPE,
        "DashScope",
        (
            "qwen-audio-3.0-tts-plus",
            "qwen-audio-3.0-tts-flash",
            "cosyvoice-v3.5-plus",
            "cosyvoice-v3.5-flash",
            "qwen3-tts-flash",
            "qwen3-tts-instruct-flash",
            "qwen3-tts-vc-2026-01-22",
            "qwen3-tts-vd-2026-01-26",
            "cosyvoice-v3-plus",
            "cosyvoice-v3-flash",
        ),
        "dashscope",
    ),
    ProviderUiSpec(
        TTSProvider.TENCENT, "腾讯云", ("0", "1001", "1002", "1003", "1004", "1005"), "tencent"
    ),
    ProviderUiSpec(
        TTSProvider.VOLCENGINE_ARK,
        "火山方舟·豆包语音",
        ("doubao-seed-tts-2.0",),
        "volcengine_ark",
    ),
    ProviderUiSpec(TTSProvider.MIMO, "Xiaomi MiMo", ("mimo-v2.5-tts",), "mimo"),
    ProviderUiSpec(
        TTSProvider.LOCAL,
        "本地兼容服务",
        (
            "default",
            "Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign",
            "Qwen/Qwen3-TTS-12Hz-1.7B-Base",
            "FunAudioLLM/CosyVoice3-0.5B",
        ),
        "local",
    ),
    ProviderUiSpec(
        TTSProvider.QWEN3,
        "Qwen3-TTS（本地）",
        ("Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice",),
        "qwen3",
    ),
    ProviderUiSpec(
        TTSProvider.COSYVOICE,
        "CosyVoice 3",
        ("FunAudioLLM/Fun-CosyVoice3-0.5B-2512",),
        "cosyvoice",
    ),
    ProviderUiSpec(TTSProvider.OPENVOICE, "OpenVoice V2", ("openvoice-v2",), "openvoice"),
    ProviderUiSpec(TTSProvider.MOCK, "模拟平台", ("mock-model",), "none"),
)

_BY_PROVIDER = {item.provider.value: item for item in PROVIDER_UI_SPECS}


def provider_ui_spec(provider: str | TTSProvider) -> ProviderUiSpec:
    key = provider.value if isinstance(provider, TTSProvider) else provider.strip().lower()
    known = _BY_PROVIDER.get(key)
    if known is not None:
        return known
    metadata = tts_adapter_metadata(key)
    if metadata is not None:
        return ProviderUiSpec(
            provider=TTSProvider(key),
            label=metadata.display_name,
            models=metadata.default_models,
            settings_group=metadata.settings_group,
        )
    return _BY_PROVIDER[TTSProvider.MOCK.value]


def all_provider_ui_specs() -> tuple[ProviderUiSpec, ...]:
    """Merge built-in presentation with trusted external adapter registrations."""

    specs = list(PROVIDER_UI_SPECS)
    known = {item.provider.value for item in specs}
    specs.extend(
        provider_ui_spec(provider_id)
        for provider_id in registered_tts_provider_ids()
        if provider_id not in known
    )
    return tuple(specs)


def provider_models(
    provider: str | TTSProvider,
    settings: Settings | None = None,
) -> list[str]:
    spec = provider_ui_spec(provider)
    provider_id = "dashscope" if spec.provider == TTSProvider.BAILIAN else spec.provider.value
    # Tencent's native selector is VoiceType, while its capability manifest's
    # model_id describes the API product.  Keep that platform-native field
    # instead of leaking a generic model identifier into the combo box.
    registry = (
        registry_from_settings(settings) if settings is not None else AudioPluginRegistry.builtins()
    )
    registered = (
        []
        if spec.provider == TTSProvider.TENCENT
        else [
            manifest.model_id
            for manifest in registry.candidates(AudioCapability.SPEECH_SYNTHESIS)
            if manifest.provider_id == provider_id
            and "formal" in manifest.tags
            and manifest.model_id
        ]
    )
    return list(dict.fromkeys([*registered, *spec.models]))
