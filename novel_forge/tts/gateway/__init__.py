"""TTS Gateway package — provider-agnostic TTS adapter layer."""

from __future__ import annotations

from novel_forge.tts.gateway.base import TTSProviderAdapter
from novel_forge.tts.gateway.factory import (
    TTSAdapterMetadata,
    TTSAdapterRegistry,
    register_tts_adapter,
    registered_tts_provider_ids,
    unregister_tts_adapter,
)
from novel_forge.tts.gateway.failures import (
    TTSFailureDecision,
    TTSFailureKind,
    TTSProviderFailure,
    TTSProviderFailurePolicy,
    classify_tts_failure,
)
from novel_forge.tts.gateway.fault_tolerant import FailureManagedTTSAdapter

__all__ = [
    "TTSAdapterMetadata",
    "TTSAdapterRegistry",
    "TTSProviderAdapter",
    "FailureManagedTTSAdapter",
    "register_tts_adapter",
    "registered_tts_provider_ids",
    "unregister_tts_adapter",
    "TTSFailureDecision",
    "TTSFailureKind",
    "TTSProviderFailure",
    "TTSProviderFailurePolicy",
    "classify_tts_failure",
]
