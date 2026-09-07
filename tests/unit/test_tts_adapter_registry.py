"""Tests for TTS adapter registry — factory registration, lazy loading."""

from __future__ import annotations

import asyncio

import pytest

from novel_forge.core.config import Settings
from novel_forge.tts.gateway.factory import (
    _ADAPTER_MODULES,
    TTSAdapterRegistry,
    register_tts_adapter,
    registered_tts_provider_ids,
    resolve_tts_model,
    tts_adapter_metadata,
    unregister_tts_adapter,
)
from novel_forge.tts.gateway.resource_managed import ResourceManagedTTSAdapter
from novel_forge.tts.schemas import TTSProvider


@pytest.fixture(autouse=True)
def reset_registry() -> None:
    """Reset singleton between tests."""
    TTSAdapterRegistry.reset_instance()
    yield
    TTSAdapterRegistry.reset_instance()


@pytest.fixture
def settings() -> Settings:
    """Create test settings."""
    return Settings(
        _env_file=None,
        tts_default_provider="minimax",
        minimax_api_key="test-key",
        tts_minimax_api_key="test-tts-key",
        tts_minimax_group_id="test-group",
        tts_dashscope_api_key="test-dashscope-key",
        tts_tencent_secret_id="test-secret-id",
        tts_tencent_secret_key="test-secret-key",
        volcengine_ark_api_key="test-volcengine-key",
        tts_mimo_api_key="test-mimo-key",
    )


class TestAdapterModules:
    """Test adapter module registration."""

    def test_all_providers_registered(self) -> None:
        assert "minimax" in _ADAPTER_MODULES
        assert "mock" in _ADAPTER_MODULES
        assert "dashscope" in _ADAPTER_MODULES
        assert "bailian" in _ADAPTER_MODULES
        assert "tencent" in _ADAPTER_MODULES
        assert "volcengine_ark" in _ADAPTER_MODULES
        assert "mimo" in _ADAPTER_MODULES
        assert "local" in _ADAPTER_MODULES
        assert "qwen3" in _ADAPTER_MODULES
        assert "cosyvoice" in _ADAPTER_MODULES
        assert "openvoice" in _ADAPTER_MODULES

    def test_module_paths_valid(self) -> None:
        for _provider, (module_path, class_name) in _ADAPTER_MODULES.items():
            assert module_path.startswith("novel_forge.tts.")
            assert class_name.endswith("Adapter") or class_name.endswith("TTSAdapter")

    def test_new_transport_adapter_can_be_registered_without_pipeline_edit(self) -> None:
        register_tts_adapter(
            "custom-test",
            "novel_forge.tts.gateway.adapters.mock_adapter",
            "MockTTSAdapter",
            kwargs_factory=lambda _settings: {"latency_ms": 7.0},
            model_resolver=lambda _settings, purpose: f"custom-{purpose}",
            display_name="Custom Test",
            default_models=("custom-formal",),
            default_voice_id="custom-voice",
        )
        try:
            assert _ADAPTER_MODULES["custom-test"] == (
                "novel_forge.tts.gateway.adapters.mock_adapter",
                "MockTTSAdapter",
            )
            assert TTSProvider("custom-test").value == "custom-test"
            assert "custom-test" in registered_tts_provider_ids()
            assert resolve_tts_model(Settings(_env_file=None), "custom-test") == "custom-formal"
            assert tts_adapter_metadata("custom-test").display_name == "Custom Test"
            from novel_forge.desktop.pages.voice_studio.provider_ui import provider_ui_spec

            assert provider_ui_spec("custom-test").label == "Custom Test"
            adapter = TTSAdapterRegistry.get_instance(Settings(_env_file=None)).get_adapter(
                "custom-test"
            )
            assert adapter.provider_name == "mock"
        finally:
            unregister_tts_adapter("custom-test")

        with pytest.raises(ValueError):
            TTSProvider("custom-test")


class TestTTSAdapterRegistry:
    """Test TTSAdapterRegistry singleton."""

    def test_singleton(self, settings: Settings) -> None:
        r1 = TTSAdapterRegistry.get_instance(settings)
        r2 = TTSAdapterRegistry.get_instance(settings)
        assert r1 is r2

    def test_reset(self, settings: Settings) -> None:
        r1 = TTSAdapterRegistry.get_instance(settings)
        TTSAdapterRegistry.reset_instance()
        r2 = TTSAdapterRegistry.get_instance(settings)
        assert r1 is not r2

    def test_get_mock_adapter(self, settings: Settings) -> None:
        registry = TTSAdapterRegistry.get_instance(settings)
        adapter = registry.get_adapter("mock")
        assert adapter.provider_name == "mock"
        assert adapter.provider_type == TTSProvider.MOCK

    def test_get_minimax_adapter(self, settings: Settings) -> None:
        registry = TTSAdapterRegistry.get_instance(settings)
        adapter = registry.get_adapter("minimax")
        assert adapter.provider_name == "minimax"
        assert adapter.provider_type == TTSProvider.MINIMAX

    def test_minimax_adapter_kwargs_include_the_configured_official_endpoint(
        self,
        settings: Settings,
    ) -> None:
        configured = settings.model_copy(
            update={"tts_minimax_base_url": "https://api-uw.minimax.io/v1"}
        )
        registry = TTSAdapterRegistry.get_instance(configured)

        kwargs = registry._build_adapter_kwargs("minimax")  # noqa: SLF001

        assert kwargs["base_url"] == "https://api-uw.minimax.io/v1"

    def test_unknown_provider_raises(self, settings: Settings) -> None:
        registry = TTSAdapterRegistry.get_instance(settings)
        with pytest.raises(ValueError, match="Unknown TTS provider"):
            registry.get_adapter("nonexistent")

    def test_adapter_cached(self, settings: Settings) -> None:
        registry = TTSAdapterRegistry.get_instance(settings)
        a1 = registry.get_adapter("mock")
        a2 = registry.get_adapter("mock")
        assert a1 is a2

    def test_async_adapters_are_isolated_per_event_loop(self, settings: Settings) -> None:
        """A Desktop worker must not reuse an HTTP client from a closed worker loop."""
        registry = TTSAdapterRegistry.get_instance(settings)
        first_loop = asyncio.new_event_loop()
        second_loop = asyncio.new_event_loop()

        async def get_minimax_adapter() -> object:
            return registry.get_adapter("minimax")

        try:
            first = first_loop.run_until_complete(get_minimax_adapter())
            second = second_loop.run_until_complete(get_minimax_adapter())
            assert first is not second

            first_loop.run_until_complete(registry.shutdown_current_loop())
            second_loop.run_until_complete(registry.shutdown_current_loop())
        finally:
            first_loop.close()
            second_loop.close()

    def test_get_default_provider(self, settings: Settings) -> None:
        registry = TTSAdapterRegistry.get_instance(settings)
        default = registry.get_default_provider()
        assert default == TTSProvider.MINIMAX

    def test_get_default_provider_invalid_fallback(self) -> None:
        settings = Settings(tts_default_provider="invalid")
        registry = TTSAdapterRegistry.get_instance(settings)
        default = registry.get_default_provider()
        assert default == TTSProvider.MOCK

    def test_local_adapter(self, settings: Settings) -> None:
        registry = TTSAdapterRegistry.get_instance(settings)
        adapter = registry.get_adapter("local")
        assert isinstance(adapter, ResourceManagedTTSAdapter)
        assert adapter.provider_name == "local"
        assert adapter.provider_type == TTSProvider.LOCAL

    def test_native_open_source_adapters_are_lazy_loaded(self, settings: Settings) -> None:
        registry = TTSAdapterRegistry.get_instance(settings)
        assert registry.get_adapter("cosyvoice").provider_type == TTSProvider.COSYVOICE
        # OpenVoice imports Torch/MeloTTS only when executing a real request.
        assert registry.get_adapter("openvoice").provider_type == TTSProvider.OPENVOICE

    def test_qwen3_adapter_is_lazy_loaded(self, settings: Settings) -> None:
        registry = TTSAdapterRegistry.get_instance(settings)
        adapter = registry.get_adapter("qwen3")
        assert isinstance(adapter, ResourceManagedTTSAdapter)
        assert adapter.provider_type == TTSProvider.QWEN3

    def test_new_cloud_adapters_are_lazy_loaded(self, settings: Settings) -> None:
        registry = TTSAdapterRegistry.get_instance(settings)
        assert registry.get_adapter("volcengine_ark").provider_type == TTSProvider.VOLCENGINE_ARK
        assert registry.get_adapter("mimo").provider_type == TTSProvider.MIMO


def test_provider_specific_model_resolution() -> None:
    settings = Settings(
        tts_default_model="speech-2.8-hd",
        tts_dashscope_model="cosyvoice-v3-plus",
        tts_dashscope_preview_model="qwen3-tts-flash",
        tts_tencent_voice_type="1001",
        tts_local_model="Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign",
        tts_qwen3_formal_model="Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice",
        tts_qwen3_preview_model="Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice",
        tts_volcengine_model="doubao-seed-tts-2.0",
        tts_mimo_model="mimo-v2.5-tts",
    )

    assert resolve_tts_model(settings, TTSProvider.MINIMAX) == "speech-2.8-hd"
    assert resolve_tts_model(settings, TTSProvider.BAILIAN) == "cosyvoice-v3-plus"
    assert (
        resolve_tts_model(settings, TTSProvider.BAILIAN, purpose="preview")
        == "qwen3-tts-flash"
    )
    assert resolve_tts_model(settings, TTSProvider.TENCENT) == "1001"
    assert resolve_tts_model(settings, TTSProvider.VOLCENGINE_ARK) == "doubao-seed-tts-2.0"
    assert resolve_tts_model(settings, TTSProvider.MIMO) == "mimo-v2.5-tts"
    assert resolve_tts_model(settings, TTSProvider.LOCAL) == "Qwen/Qwen3-TTS-12Hz-1.7B-VoiceDesign"
    assert resolve_tts_model(settings, TTSProvider.QWEN3).endswith("1.7B-CustomVoice")
    assert resolve_tts_model(settings, TTSProvider.QWEN3, purpose="preview").endswith(
        "0.6B-CustomVoice"
    )
