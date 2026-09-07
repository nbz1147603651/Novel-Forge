"""Pluggable generation of BGM, ambience, and one-shot sound effects.

This package intentionally owns model catalogues and provider boundaries while
``tts.sound_library`` remains the single source of truth for local assets.
Model weights are never committed to the project repository; local runtimes are
selected through Settings and their generated outputs are project-owned assets.
"""

from __future__ import annotations

from novel_forge.tts.sound_generation.schemas import (
    SoundGenerationKind,
    SoundGenerationRequest,
    SoundGenerationSummary,
)
from novel_forge.tts.sound_generation.service import SoundGenerationService

__all__ = [
    "SoundGenerationKind",
    "SoundGenerationRequest",
    "SoundGenerationService",
    "SoundGenerationSummary",
]
