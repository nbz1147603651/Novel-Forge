"""Provider contract for music, ambience, and sound-effect generation."""

from __future__ import annotations

from abc import ABC, abstractmethod

from novel_forge.tts.sound_generation.schemas import (
    GeneratedSoundAsset,
    SoundGenerationRequest,
)


class SoundGenerationError(RuntimeError):
    """A recoverable error in one generated sound cue."""


class SoundGenerationProvider(ABC):
    """Provider-neutral interface used by sound generation orchestration."""

    @property
    @abstractmethod
    def provider_id(self) -> str:
        """Stable provider identifier used in persisted provenance."""

    @abstractmethod
    async def generate(self, request: SoundGenerationRequest) -> GeneratedSoundAsset:
        """Generate a single audio asset without writing project files."""
