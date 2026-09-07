"""Curated audio model metadata and provider-to-model routing defaults."""

from __future__ import annotations

from novel_forge.tts.sound_generation.models.catalog import (
    SOUND_MODEL_CATALOG,
    SoundModelDescriptor,
    default_model_for,
)

__all__ = ["SOUND_MODEL_CATALOG", "SoundModelDescriptor", "default_model_for"]
