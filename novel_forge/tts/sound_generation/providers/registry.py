"""Provider selection and construction for generated sound assets."""

from __future__ import annotations

from typing import Callable

from novel_forge.core.config import Settings
from novel_forge.core.local_model_resources import configure_local_model_resources
from novel_forge.tts.sound_generation.models.catalog import (
    SOUND_MODEL_CATALOG,
    default_model_for,
)
from novel_forge.tts.sound_generation.providers.ace_step_cli import ACEStepCliProvider
from novel_forge.tts.sound_generation.providers.base import SoundGenerationProvider
from novel_forge.tts.sound_generation.providers.minimax_music import MiniMaxMusicProvider
from novel_forge.tts.sound_generation.providers.stable_audio_cli import StableAudioCliProvider
from novel_forge.tts.sound_generation.schemas import SoundGenerationKind, SoundGenerationRequest


class SoundGenerationRegistry:
    """Central factory; provider adapters never leak into the chapter pipeline."""

    def __init__(
        self,
        settings: Settings,
        *,
        provider_factory: Callable[[str], SoundGenerationProvider] | None = None,
    ) -> None:
        self._settings = settings
        configure_local_model_resources(settings)
        self._provider_factory = provider_factory

    def prepare_request(self, request: SoundGenerationRequest) -> SoundGenerationRequest:
        """Resolve auto-routing and a model ID before invoking a provider."""
        provider_id = self._resolve_provider_id(request)
        model_id = request.model_id or self._configured_model_for(request.kind, provider_id)
        descriptor = SOUND_MODEL_CATALOG.get(model_id)
        if descriptor is not None and request.kind not in descriptor.supported_kinds:
            raise ValueError(f"Model {model_id} does not support {request.kind.value}")
        return request.model_copy(update={"provider": provider_id, "model_id": model_id})

    def get_provider(
        self,
        provider_id: str,
        *,
        endpoint: str = "",
    ) -> SoundGenerationProvider:
        """Build an adapter from the frozen route endpoint when one is supplied."""
        if self._provider_factory is not None:
            return self._provider_factory(provider_id)
        if provider_id == "minimax_music":
            return MiniMaxMusicProvider(
                api_key=self._settings.sound_generation_minimax_api_key
                or self._settings.tts_minimax_api_key
                or self._settings.minimax_api_key,
                endpoint=endpoint or self._settings.sound_generation_minimax_music_endpoint,
                default_model=self._settings.sound_generation_minimax_music_model,
                timeout_s=float(self._settings.sound_generation_timeout_s),
            )
        if provider_id == "stable_audio":
            return StableAudioCliProvider(
                command=self._settings.sound_generation_stable_audio_command,
                models_dir=self._settings.sound_generation_stable_audio_models_dir,
                huggingface_token=self._settings.sound_generation_huggingface_token,
                timeout_s=float(self._settings.sound_generation_timeout_s),
            )
        if provider_id == "ace_step":
            command = self._settings.sound_generation_ace_step_command
            if not command:
                raise ValueError(
                    "ACE-Step provider selected but NOVEL_FORGE_SOUND_GENERATION_ACE_STEP_COMMAND "
                    "is not configured. Install ACE-Step and set the command path."
                )
            return ACEStepCliProvider(
                command=command,
                models_dir=self._settings.sound_generation_ace_step_models_dir,
                timeout_s=float(self._settings.sound_generation_timeout_s),
            )
        raise ValueError(
            f"No built-in sound-generation adapter for {provider_id!r}. "
            "Register a provider adapter before selecting it."
        )

    def _resolve_provider_id(self, request: SoundGenerationRequest) -> str:
        if request.provider and request.provider != "auto":
            return request.provider
        if request.model_id and request.model_id in SOUND_MODEL_CATALOG:
            return SOUND_MODEL_CATALOG[request.model_id].provider_id
        configured = self._settings.sound_generation_default_provider.strip().lower()
        if configured and configured != "auto":
            return configured
        has_minimax_key = bool(
            self._settings.sound_generation_minimax_api_key
            or self._settings.tts_minimax_api_key
            or self._settings.minimax_api_key
        )
        if request.kind in {SoundGenerationKind.BGM, SoundGenerationKind.SOUNDSCAPE}:
            if has_minimax_key:
                return "minimax_music"
            # Prefer ACE-Step for BGM when configured (MIT, 4GB VRAM, up to 285s).
            if (
                request.kind == SoundGenerationKind.BGM
                and self._settings.sound_generation_ace_step_command
            ):
                return "ace_step"
            raise ValueError(
                "长环境声和 BGM 需要 MiniMax Music API Key 或本地 ACE-Step/Stable Audio；"
                "配置后会自动路由，短 SFX 继续使用本地 Stable Audio 3 Small-SFX。"
            )
        return "stable_audio"

    def _configured_model_for(self, kind: SoundGenerationKind, provider_id: str) -> str:
        if provider_id == "minimax_music":
            return self._settings.sound_generation_minimax_music_model
        if provider_id == "stable_audio":
            if kind == SoundGenerationKind.BGM:
                return self._settings.sound_generation_stable_audio_music_model
            return self._settings.sound_generation_stable_audio_sfx_model
        return default_model_for(kind, provider_id)
