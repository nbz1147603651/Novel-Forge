"""TTS Adapter Registry — creates and manages TTS provider adapters.

Follows the pattern of gateway/factory.py for LLM adapters.
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from novel_forge.core.config import Settings
from novel_forge.obs.logger import get_logger
from novel_forge.tts.gateway.base import TTSProviderAdapter
from novel_forge.tts.gateway.failures import TTSProviderFailurePolicy
from novel_forge.tts.gateway.fault_tolerant import FailureManagedTTSAdapter
from novel_forge.tts.gateway.resource_managed import (
    ResourceManagedTTSAdapter,
    is_local_tts_provider,
)
from novel_forge.tts.schemas import (
    TTSProvider,
    register_external_tts_provider,
    unregister_external_tts_provider,
)

_log = get_logger("tts.gateway.factory")

# provider -> (module_path, class_name)
_ADAPTER_MODULES: dict[str, tuple[str, str]] = {
    "minimax": ("novel_forge.tts.gateway.adapters.minimax_adapter", "MiniMaxTTSAdapter"),
    "mock": ("novel_forge.tts.gateway.adapters.mock_adapter", "MockTTSAdapter"),
    "dashscope": (
        "novel_forge.tts.gateway.adapters.dashscope_adapter",
        "DashScopeTTSAdapter",
    ),
    "bailian": (
        "novel_forge.tts.gateway.adapters.dashscope_adapter",
        "DashScopeTTSAdapter",
    ),
    "tencent": ("novel_forge.tts.gateway.adapters.tencent_adapter", "TencentTTSAdapter"),
    "volcengine_ark": (
        "novel_forge.tts.gateway.adapters.volcengine_ark_adapter",
        "VolcengineArkTTSAdapter",
    ),
    "mimo": ("novel_forge.tts.gateway.adapters.mimo_adapter", "MiMoTTSAdapter"),
    "local": ("novel_forge.tts.gateway.adapters.local_adapter", "LocalTTSAdapter"),
    "qwen3": ("novel_forge.tts.gateway.adapters.qwen3_adapter", "Qwen3TTSAdapter"),
    "cosyvoice": ("novel_forge.tts.gateway.adapters.cosyvoice_adapter", "CosyVoiceTTSAdapter"),
    "openvoice": ("novel_forge.tts.gateway.adapters.openvoice_adapter", "OpenVoiceTTSAdapter"),
}

_BUILTIN_ADAPTER_IDS = frozenset(_ADAPTER_MODULES)

AdapterKwargsFactory = Callable[[Settings], dict[str, Any]]
AdapterModelResolver = Callable[[Settings, str], str]


@dataclass(frozen=True)
class TTSAdapterMetadata:
    """Declarative metadata required for an end-to-end provider extension."""

    provider_id: str
    display_name: str
    default_models: tuple[str, ...] = ()
    settings_group: str = "none"
    is_local: bool = False
    default_voice_id: str = "default"


_ADAPTER_KWARGS_FACTORIES: dict[str, AdapterKwargsFactory] = {}
_ADAPTER_MODEL_RESOLVERS: dict[str, AdapterModelResolver] = {}
_ADAPTER_METADATA: dict[str, TTSAdapterMetadata] = {}


def registered_tts_provider_ids() -> tuple[str, ...]:
    """Return every built-in and externally registered transport id."""

    return tuple(sorted(_ADAPTER_MODULES))


def tts_adapter_metadata(provider: str | TTSProvider) -> TTSAdapterMetadata | None:
    """Return optional presentation/runtime metadata for a registered adapter."""

    provider_id = provider.value if isinstance(provider, TTSProvider) else provider
    return _ADAPTER_METADATA.get(provider_id.strip().lower())


def is_registered_local_tts_provider(provider: str | TTSProvider) -> bool:
    """Return whether a registered transport owns a local heavyweight runtime."""

    metadata = tts_adapter_metadata(provider)
    return bool(metadata and metadata.is_local)


def default_tts_voice_id(provider: str | TTSProvider) -> str:
    """Return the adapter-declared fallback voice without pipeline hard-coding."""

    metadata = tts_adapter_metadata(provider)
    return metadata.default_voice_id if metadata else "default"


def register_tts_adapter(
    provider_id: str,
    module_path: str,
    class_name: str,
    *,
    replace: bool = False,
    kwargs_factory: AdapterKwargsFactory | None = None,
    model_resolver: AdapterModelResolver | None = None,
    is_local: bool = False,
    display_name: str = "",
    default_models: tuple[str, ...] = (),
    settings_group: str = "none",
    default_voice_id: str = "default",
) -> None:
    """Register a transport adapter without editing the synthesis pipeline.

    Model-only additions should normally use the generic ``local`` sidecar and
    an audio-plugin manifest.  This hook is reserved for a genuinely new
    authentication or wire protocol supplied by a trusted Python package.
    """

    provider = provider_id.strip().lower()
    if not provider or not module_path.strip() or not class_name.strip():
        raise ValueError("provider_id, module_path and class_name are required")
    if provider in _ADAPTER_MODULES and not replace:
        raise ValueError(f"TTS adapter already registered: {provider}")
    _ADAPTER_MODULES[provider] = (module_path.strip(), class_name.strip())
    if kwargs_factory is not None:
        _ADAPTER_KWARGS_FACTORIES[provider] = kwargs_factory
    else:
        _ADAPTER_KWARGS_FACTORIES.pop(provider, None)
    if model_resolver is not None:
        _ADAPTER_MODEL_RESOLVERS[provider] = model_resolver
    else:
        _ADAPTER_MODEL_RESOLVERS.pop(provider, None)
    _ADAPTER_METADATA[provider] = TTSAdapterMetadata(
        provider_id=provider,
        display_name=display_name.strip() or provider.replace("-", " ").title(),
        default_models=tuple(item for item in default_models if item),
        settings_group=settings_group.strip() or "none",
        is_local=is_local,
        default_voice_id=default_voice_id.strip() or "default",
    )
    if provider not in _BUILTIN_ADAPTER_IDS:
        register_external_tts_provider(provider)


def unregister_tts_adapter(provider_id: str) -> None:
    """Remove an external adapter registration and all of its metadata."""

    provider = provider_id.strip().lower()
    if provider in _BUILTIN_ADAPTER_IDS:
        raise ValueError(f"Cannot unregister built-in TTS adapter: {provider}")
    _ADAPTER_MODULES.pop(provider, None)
    _ADAPTER_KWARGS_FACTORIES.pop(provider, None)
    _ADAPTER_MODEL_RESOLVERS.pop(provider, None)
    _ADAPTER_METADATA.pop(provider, None)
    unregister_external_tts_provider(provider)


def resolve_tts_model(
    settings: Settings,
    provider: str | TTSProvider,
    *,
    purpose: str = "formal",
) -> str:
    """Resolve the model from the provider-specific source of truth.

    ``tts_default_model`` remains the MiniMax/default-provider setting, while
    cloud Bailian and local sidecars have independent model identifiers.  A
    single resolver prevents local Qwen/CosyVoice deployments from silently
    receiving the MiniMax model id.
    """
    provider_name = provider.value if isinstance(provider, TTSProvider) else provider
    provider_name = provider_name.lower().strip()
    custom_resolver = _ADAPTER_MODEL_RESOLVERS.get(provider_name)
    if custom_resolver is not None:
        return str(custom_resolver(settings, purpose) or "")
    if provider_name in {TTSProvider.BAILIAN.value, TTSProvider.DASHSCOPE.value}:
        if purpose == "preview":
            return settings.tts_dashscope_preview_model or settings.tts_dashscope_model
        if purpose == "design":
            return settings.tts_dashscope_voice_design_model or settings.tts_dashscope_model
        if purpose == "clone":
            return settings.tts_dashscope_voice_clone_model or settings.tts_dashscope_model
        return settings.tts_dashscope_model
    if provider_name == TTSProvider.TENCENT.value:
        return settings.tts_tencent_voice_type
    if provider_name == TTSProvider.VOLCENGINE_ARK.value:
        return settings.tts_volcengine_model
    if provider_name == TTSProvider.MIMO.value:
        return settings.tts_mimo_model
    if provider_name == TTSProvider.LOCAL.value:
        return settings.tts_local_model
    if provider_name == TTSProvider.QWEN3.value:
        if purpose == "preview":
            return settings.tts_qwen3_preview_model
        return settings.tts_qwen3_formal_model
    if provider_name == TTSProvider.COSYVOICE.value:
        return settings.tts_cosyvoice_model
    if provider_name == TTSProvider.OPENVOICE.value:
        return settings.tts_openvoice_model
    if provider_name == TTSProvider.MOCK.value:
        return "mock-model"
    return settings.tts_default_model


class TTSAdapterRegistry:
    """Factory and cache for TTS provider adapters.

    Thread-safe singleton pattern for adapter reuse.
    """

    _instance: TTSAdapterRegistry | None = None
    _lock = threading.Lock()

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        # An ``httpx.AsyncClient`` binds its connection transport to the event
        # loop that first uses it.  Desktop workers deliberately create a new
        # loop per job, so a provider adapter must never be shared between two
        # of those loops.  Keep a small cache per running loop instead of one
        # process-wide provider cache.
        self._adapters_by_loop: dict[
            asyncio.AbstractEventLoop | None, dict[str, TTSProviderAdapter]
        ] = {}
        self._adapter_lock = threading.Lock()
        self._failure_policies: dict[str, TTSProviderFailurePolicy] = {}
        self._failure_policy_lock = threading.Lock()

    @classmethod
    def get_instance(cls, settings: Settings) -> TTSAdapterRegistry:
        """Get or create the singleton registry instance."""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls(settings)
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        """Reset the singleton (for testing or config reload)."""
        with cls._lock:
            if cls._instance is not None:
                cls._instance = None

    def get_adapter(self, provider: str | TTSProvider) -> TTSProviderAdapter:
        """Get or create an adapter for the given provider.

        Args:
            provider: Provider name string or TTSProvider enum.

        Returns:
            TTSProviderAdapter instance.

        Raises:
            ValueError: If provider is not supported.
        """
        provider_name = provider.value if isinstance(provider, TTSProvider) else provider
        provider_name = provider_name.lower().strip()

        try:
            loop: asyncio.AbstractEventLoop | None = asyncio.get_running_loop()
        except RuntimeError:
            # Keep the synchronous construction path usable for configuration
            # checks and unit tests.  Real I/O must happen from an async loop.
            loop = None

        with self._adapter_lock:
            adapters = self._adapters_by_loop.setdefault(loop, {})
            if provider_name not in adapters:
                adapter = self._create_adapter(provider_name)
                adapters[provider_name] = adapter
            return adapters[provider_name]

    def _create_adapter(self, provider: str) -> TTSProviderAdapter:
        """Create a new adapter instance for the given provider."""
        if provider not in _ADAPTER_MODULES:
            raise ValueError(
                f"Unknown TTS provider: {provider}. Supported: {list(_ADAPTER_MODULES.keys())}"
            )

        from novel_forge.common.adapter_loader import load_adapter_class  # noqa: PLC0415

        module_path, class_name = _ADAPTER_MODULES[provider]

        def _on_load_error(mod: str, cls: str, exc: BaseException) -> None:
            raise ValueError(f"Failed to load TTS adapter for {provider}: {exc}") from exc

        adapter_cls = load_adapter_class(module_path, class_name, on_error=_on_load_error)
        assert adapter_cls is not None  # _on_load_error raises before reaching here

        # Build adapter kwargs from settings
        kwargs = self._build_adapter_kwargs(provider)
        adapter: TTSProviderAdapter = adapter_cls(**kwargs)
        adapter = FailureManagedTTSAdapter(adapter, self.get_failure_policy(provider))
        if is_local_tts_provider(adapter.provider_type) or is_registered_local_tts_provider(
            provider
        ):
            adapter = ResourceManagedTTSAdapter(adapter, self._settings)
        return adapter

    def get_failure_policy(self, provider: str | TTSProvider) -> TTSProviderFailurePolicy:
        """Return the process-shared fault policy for one registered provider."""

        provider_name = provider.value if isinstance(provider, TTSProvider) else provider
        provider_name = provider_name.strip().lower()
        with self._failure_policy_lock:
            policy = self._failure_policies.get(provider_name)
            if policy is None:
                policy = TTSProviderFailurePolicy(
                    provider_name,
                    failure_threshold=self._settings.circuit_breaker_threshold,
                    recovery_timeout_s=self._settings.circuit_breaker_recovery_s,
                    enabled=self._settings.circuit_breaker_enabled,
                    rate_limit_cooldown_s=(self._settings.tts_synthesis_rate_limit_cooldown_s),
                )
                self._failure_policies[provider_name] = policy
            return policy

    def _build_adapter_kwargs(self, provider: str) -> dict[str, Any]:
        """Build constructor kwargs for an adapter from settings."""
        custom_factory = _ADAPTER_KWARGS_FACTORIES.get(provider)
        if custom_factory is not None:
            return dict(custom_factory(self._settings))
        kwargs: dict[str, Any] = {}

        if provider == "minimax":
            kwargs["api_key"] = self._settings.tts_minimax_api_key or self._settings.minimax_api_key
            kwargs["group_id"] = self._settings.tts_minimax_group_id
            kwargs["base_url"] = self._settings.tts_minimax_base_url
            kwargs["default_model"] = self._settings.tts_default_model
            kwargs["async_timeout_s"] = self._settings.tts_minimax_async_timeout_s
            kwargs["aigc_watermark_default"] = self._settings.tts_aigc_watermark_default
            kwargs["continuous_sound_default"] = self._settings.tts_minimax_continuous_sound_default
            kwargs["force_cbr_default"] = self._settings.tts_minimax_force_cbr
            kwargs["english_normalization_default"] = (
                self._settings.tts_minimax_english_normalization
            )
        elif provider == "mock":
            kwargs["latency_ms"] = 100.0  # Mock latency
        elif provider in ("dashscope", "bailian"):
            kwargs["api_key"] = self._settings.tts_dashscope_api_key
            kwargs["default_model"] = self._settings.tts_dashscope_model
            kwargs["voice_design_model"] = self._settings.tts_dashscope_voice_design_model
            kwargs["voice_clone_model"] = self._settings.tts_dashscope_voice_clone_model
            kwargs["base_url"] = self._settings.tts_dashscope_base_url
            kwargs["optimize_instructions"] = self._settings.tts_dashscope_optimize_instructions
            kwargs["voice_clone_enable_preprocess"] = (
                self._settings.tts_dashscope_voice_clone_enable_preprocess
            )
            kwargs["cny_per_usd"] = self._settings.tts_dashscope_cny_per_usd
        elif provider == "tencent":
            kwargs["secret_id"] = self._settings.tts_tencent_secret_id
            kwargs["secret_key"] = self._settings.tts_tencent_secret_key
            kwargs["default_voice_type"] = int(self._settings.tts_tencent_voice_type or "0")
            kwargs["project_id"] = self._settings.tts_tencent_project_id
            kwargs["primary_language"] = self._settings.tts_tencent_primary_language
            kwargs["emotion_intensity"] = self._settings.tts_tencent_emotion_intensity
            kwargs["segment_rate"] = self._settings.tts_tencent_segment_rate
            kwargs["enable_subtitle_default"] = self._settings.tts_subtitle_word_level
        elif provider == "volcengine_ark":
            kwargs.update(
                api_key=self._settings.volcengine_ark_api_key,
                resource_id=self._settings.tts_volcengine_resource_id,
                endpoint_url=self._settings.tts_volcengine_base_url,
                default_model=self._settings.tts_volcengine_model,
            )
        elif provider == "mimo":
            kwargs.update(
                api_key=self._settings.tts_mimo_api_key,
                base_url=self._settings.tts_mimo_base_url,
                default_model=self._settings.tts_mimo_model,
            )
        elif provider == "local":
            kwargs["base_url"] = self._settings.tts_local_base_url
            kwargs["api_key"] = self._settings.tts_local_api_key
            kwargs["default_model"] = self._settings.tts_local_model
        elif provider == "qwen3":
            kwargs.update(
                base_url=self._settings.tts_qwen3_base_url,
                api_key=self._settings.tts_qwen3_api_key,
                formal_model=self._settings.tts_qwen3_formal_model,
                preview_model=self._settings.tts_qwen3_preview_model,
                design_model=self._settings.tts_qwen3_design_model,
                clone_model=self._settings.tts_qwen3_clone_model,
            )
        elif provider == "cosyvoice":
            kwargs.update(
                base_url=self._settings.tts_cosyvoice_base_url,
                default_model=self._settings.tts_cosyvoice_model,
                mode=self._settings.tts_cosyvoice_mode,
                voice_store_dir=self._settings.tts_cosyvoice_voice_store,
                sample_rate=self._settings.tts_cosyvoice_sample_rate,
            )
        elif provider == "openvoice":
            kwargs.update(
                default_model=self._settings.tts_openvoice_model,
                checkpoint_dir=self._settings.tts_openvoice_checkpoint_dir,
                voice_store_dir=self._settings.tts_openvoice_voice_store,
                device=self._settings.tts_openvoice_device,
                language=self._settings.tts_openvoice_language,
            )

        return kwargs

    async def shutdown_current_loop(self) -> None:
        """Close adapters owned by the current event loop.

        This must be called before a desktop worker closes its private event
        loop.  Closing only the current loop's adapters also prevents one
        concurrent worker from closing another worker's HTTP connections.
        """
        loop = asyncio.get_running_loop()
        with self._adapter_lock:
            adapters = self._adapters_by_loop.pop(loop, {})
        for adapter in adapters.values():
            try:
                await adapter.shutdown()
            except Exception as exc:
                _log.warning("Error shutting down TTS adapter: %s", exc)

    async def shutdown_all(self) -> None:
        """Shutdown adapters that are safe to close from the current loop.

        Adapters owned by other running loops are intentionally left alone;
        cross-loop ``aclose`` has the same failure mode this registry avoids.
        """
        await self.shutdown_current_loop()
        with self._adapter_lock:
            detached_adapters = self._adapters_by_loop.pop(None, {})
        for adapter in detached_adapters.values():
            try:
                await adapter.shutdown()
            except Exception as exc:
                _log.warning("Error shutting down detached TTS adapter: %s", exc)

    def get_default_provider(self) -> TTSProvider:
        """Get the default TTS provider from settings."""
        provider_str = self._settings.tts_default_provider.lower().strip()
        if provider_str not in _ADAPTER_MODULES:
            return TTSProvider.MOCK
        try:
            return TTSProvider(provider_str)
        except ValueError:
            return TTSProvider.MOCK
