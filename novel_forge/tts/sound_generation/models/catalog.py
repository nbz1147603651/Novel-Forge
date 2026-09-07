"""One maintainable catalogue of supported and planned sound-generation models.

Entries describe capabilities and licensing posture; they do not download model
weights.  Adding a model means registering its descriptor and a provider adapter
without touching the chapter pipeline.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from novel_forge.tts.sound_generation.schemas import SoundGenerationKind


@dataclass(frozen=True)
class SoundModelDescriptor:
    """Static capability and operational metadata for one model."""

    model_id: str
    provider_id: str
    display_name: str
    supported_kinds: frozenset[SoundGenerationKind]
    default_max_duration_s: int
    execution: str
    license_summary: str
    integration_status: str
    default_commercial_use_status: Literal["cleared", "review_required", "restricted"] = (
        "review_required"
    )
    repository_id: str = ""
    model_page_url: str = ""
    estimated_download_bytes: int = 0
    requires_access_approval: bool = False


SOUND_MODEL_CATALOG: dict[str, SoundModelDescriptor] = {
    "music-2.6": SoundModelDescriptor(
        model_id="music-2.6",
        provider_id="minimax_music",
        display_name="MiniMax Music 2.6",
        supported_kinds=frozenset({SoundGenerationKind.BGM, SoundGenerationKind.SOUNDSCAPE}),
        default_max_duration_s=240,
        execution="cloud_api",
        license_summary="Commercial API; usage and output rights follow the active MiniMax account terms.",
        integration_status="built_in",
    ),
    "small-sfx": SoundModelDescriptor(
        model_id="small-sfx",
        provider_id="stable_audio",
        display_name="Stable Audio 3 Small-SFX",
        supported_kinds=frozenset(
            {SoundGenerationKind.SFX, SoundGenerationKind.SOUNDSCAPE}
        ),
        # The model is variable-length and can generate much longer audio, but
        # audiobook sound beds are generated as reviewable 30–120s loops and
        # extended with crossfades during mixdown.
        default_max_duration_s=120,
        execution="local_cli",
        license_summary="Stable AI Community License; review commercial-use terms before distribution.",
        integration_status="built_in",
        repository_id="stabilityai/stable-audio-3-small-sfx",
        model_page_url="https://huggingface.co/stabilityai/stable-audio-3-small-sfx",
        estimated_download_bytes=2_270_000_000,
        requires_access_approval=True,
    ),
    "small-music": SoundModelDescriptor(
        model_id="small-music",
        provider_id="stable_audio",
        display_name="Stable Audio 3 Small-Music",
        supported_kinds=frozenset({SoundGenerationKind.BGM}),
        default_max_duration_s=120,
        execution="local_cli",
        license_summary="Stable AI Community License; review commercial-use terms before distribution.",
        integration_status="built_in",
        repository_id="stabilityai/stable-audio-3-small-music",
        model_page_url="https://huggingface.co/stabilityai/stable-audio-3-small-music",
        estimated_download_bytes=2_270_000_000,
        requires_access_approval=True,
    ),
    "ace-step-1.5": SoundModelDescriptor(
        model_id="ace-step-1.5",
        provider_id="ace_step",
        display_name="ACE-Step 1.5",
        supported_kinds=frozenset({SoundGenerationKind.BGM}),
        default_max_duration_s=285,
        execution="local_cli",
        license_summary="MIT license for the ACE-Step 1.5 repository; validate model-weight terms before release.",
        integration_status="built_in",
        repository_id="ace-step/ACE-Step-1.5",
        model_page_url="https://huggingface.co/ace-step/ACE-Step-1.5",
        estimated_download_bytes=4_000_000_000,
    ),
    "musicgen": SoundModelDescriptor(
        model_id="musicgen",
        provider_id="audiocraft",
        display_name="Meta MusicGen / AudioCraft",
        supported_kinds=frozenset({SoundGenerationKind.BGM}),
        default_max_duration_s=30,
        execution="external_runtime",
        license_summary="Research/legacy option; verify the exact checkpoint licence before commercial use.",
        integration_status="adapter_required",
        default_commercial_use_status="restricted",
    ),
}


def default_model_for(kind: SoundGenerationKind, provider_id: str) -> str:
    """Return the first production-ready default for a provider and sound role."""
    for descriptor in SOUND_MODEL_CATALOG.values():
        if (
            descriptor.provider_id == provider_id
            and kind in descriptor.supported_kinds
            and descriptor.integration_status == "built_in"
        ):
            return descriptor.model_id
    raise ValueError(f"No built-in sound model for provider={provider_id!r}, kind={kind.value!r}")
