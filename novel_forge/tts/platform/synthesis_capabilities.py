"""Provider-scoped synthesis features that must never leak into generic TTS.

``field_mapping`` describes how portable performance directions reach an
adapter.  This module covers a different boundary: provider-private input
syntax and response/encoding controls.  A future provider is added by
declaring one profile here (and, when needed, a matching rewrite-profile JSON
file); generic orchestration remains unaware of vendor marker syntax.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

NativePauseSyntax = Literal["none", "minimax_hash"]


@dataclass(frozen=True)
class ProviderSynthesisCapabilities:
    """Only the provider-native controls verified by its adapter contract."""

    provider_id: str
    native_pause_syntax: NativePauseSyntax = "none"
    native_word_subtitles: bool = False
    force_cbr: bool = False

    @property
    def uses_native_pause_timing(self) -> bool:
        return self.native_pause_syntax != "none"


_DEFAULT = ProviderSynthesisCapabilities(provider_id="default")
_CAPABILITIES: dict[str, ProviderSynthesisCapabilities] = {
    "minimax": ProviderSynthesisCapabilities(
        provider_id="minimax",
        native_pause_syntax="minimax_hash",
        native_word_subtitles=True,
        force_cbr=True,
    ),
}


def provider_synthesis_capabilities(provider_id: str) -> ProviderSynthesisCapabilities:
    """Return a conservative profile for the selected provider."""

    normalized = str(provider_id or "").strip().lower().replace("-", "_")
    return _CAPABILITIES.get(normalized, _DEFAULT)


def providers_with_native_pause_markers() -> frozenset[str]:
    """Return provider ids whose input marker syntax is implemented."""

    return frozenset(
        provider_id
        for provider_id, capabilities in _CAPABILITIES.items()
        if capabilities.uses_native_pause_timing
    )


def native_synthesis_request_extension(
    provider_id: str,
    *,
    word_subtitles_enabled: bool,
    force_cbr_enabled: bool,
) -> dict[str, object]:
    """Build only controls the selected provider has declared as native.

    Callers merge an explicit segment ``platform_extension`` afterwards, so a
    diagnostic or model-specific override remains possible without exposing a
    MiniMax-only field to another adapter.
    """

    capabilities = provider_synthesis_capabilities(provider_id)
    extension: dict[str, object] = {}
    if capabilities.native_word_subtitles:
        extension.update(
            {
                "subtitle_enable": bool(word_subtitles_enabled),
                "subtitle_type": "word",
            }
        )
    if capabilities.force_cbr:
        extension["force_cbr"] = bool(force_cbr_enabled)
    return extension


__all__ = [
    "NativePauseSyntax",
    "ProviderSynthesisCapabilities",
    "native_synthesis_request_extension",
    "provider_synthesis_capabilities",
    "providers_with_native_pause_markers",
]
