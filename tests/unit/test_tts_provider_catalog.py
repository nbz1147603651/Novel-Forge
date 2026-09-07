from __future__ import annotations

from novel_forge.tts.platform.provider_catalog import (
    normalize_tts_provider_id,
    tts_provider_capabilities,
    tts_provider_catalog,
    tts_provider_spec,
)
from novel_forge.tts.schemas import TTSFeature, TTSProvider


def test_provider_catalog_uses_unique_stable_ids_and_parameter_ids() -> None:
    catalog = tts_provider_catalog()

    provider_ids = [provider.provider_id for provider in catalog]
    assert len(provider_ids) == len(set(provider_ids))
    assert {provider.value for provider in TTSProvider} <= {
        *provider_ids,
        "bailian",
    }

    for provider in catalog:
        parameter_ids = [setting.parameter_id for setting in provider.settings]
        model_ids = [model.model_id for model in provider.models]
        assert len(parameter_ids) == len(set(parameter_ids))
        assert len(model_ids) == len(set(model_ids))
        assert all(parameter_id for parameter_id in parameter_ids)
        assert provider.default_model or not provider.models


def test_provider_aliases_converge_on_one_dashscope_contract() -> None:
    assert normalize_tts_provider_id("bailian") == "dashscope"
    assert normalize_tts_provider_id("阿里百炼 / DashScope") == "dashscope"
    assert tts_provider_spec(TTSProvider.BAILIAN) is tts_provider_spec(TTSProvider.DASHSCOPE)


def test_catalog_exposes_provider_specific_settings_without_secrets() -> None:
    minimax = tts_provider_spec("minimax")
    dashscope = tts_provider_spec("dashscope")
    tencent = tts_provider_spec("tencent")

    assert minimax is not None
    assert dashscope is not None
    assert tencent is not None
    assert {setting.parameter_id for setting in minimax.settings} >= {
        "tts-minimax-english-normalization",
        "tts-minimax-force-cbr",
        "tts-minimax-continuous-sound",
    }
    assert {setting.parameter_id for setting in dashscope.settings} >= {
        "tts-dashscope-voice-clone-model",
        "tts-dashscope-voice-design-model",
        "tts-dashscope-optimize-instructions",
    }
    assert {setting.parameter_id for setting in tencent.settings} >= {
        "tts-tencent-project-id",
        "tts-tencent-primary-language",
        "tts-tencent-emotion-intensity",
        "tts-tencent-segment-rate",
    }
    assert all(not setting.default_value for setting in minimax.settings if setting.is_secret)


def test_model_dependent_capabilities_do_not_overpromise() -> None:
    speech_28 = tts_provider_capabilities("minimax", "speech-2.8-hd")
    speech_26 = tts_provider_capabilities("minimax", "speech-2.6-hd")
    qwen_audio = tts_provider_capabilities("dashscope", "qwen-audio-3.0-tts-plus")
    qwen_instruct = tts_provider_capabilities("dashscope", "qwen3-tts-instruct-flash")

    assert TTSFeature.PARALINGUISTIC in speech_28.synthesis_features
    assert TTSFeature.PARALINGUISTIC not in speech_26.synthesis_features
    assert qwen_audio.voice_clone is True
    assert qwen_audio.voice_design is True
    assert TTSFeature.PARALINGUISTIC in qwen_audio.synthesis_features
    assert TTSFeature.INSTRUCTION_CONTROL in qwen_instruct.synthesis_features
    assert TTSFeature.SPEED not in qwen_instruct.synthesis_features


def test_native_but_unadapted_models_remain_visible_and_unselectable() -> None:
    mimo = tts_provider_spec("mimo")

    assert mimo is not None
    assert mimo.models_for("design") == ()
    assert mimo.models_for("clone") == ()
    assert any(model.voice_design and not model.adapter_supported for model in mimo.models)
    assert any(model.voice_clone and not model.adapter_supported for model in mimo.models)


def test_production_cloud_providers_link_to_primary_documentation() -> None:
    for provider in tts_provider_catalog():
        if provider.is_local or provider.adapter_status == "mock":
            continue
        assert provider.official_docs_url.startswith("https://")
