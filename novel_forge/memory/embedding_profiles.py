"""Resolve embedding model settings from configured model profiles."""

from __future__ import annotations

from typing import Any

from novel_forge.gateway.embedding_config import is_embedding_model
from novel_forge.obs.logger import get_logger

_log = get_logger("memory.embedding_profiles")


def get_embedding_config_from_profiles(
    profile_id: str | None = None,
) -> dict[str, Any] | None:
    """Return embedding model configuration from model profiles, if available."""
    try:
        from novel_forge.gateway.profiles import load_or_import_profiles

        profiles_config = load_or_import_profiles()

        if profile_id and profile_id != "auto":
            profile = profiles_config.get_profile(profile_id)
            if profile and is_embedding_model(profile.provider, profile.model_id):
                return _profile_to_embedding_config(profile)
            _log.warning("specified_profile_not_embedding | profile_id=%s", profile_id)
            return None

        for profile in profiles_config.profiles:
            if is_embedding_model(profile.provider, profile.model_id):
                return _profile_to_embedding_config(profile)

        _log.info("no_embedding_profile_found | all_profiles_checked")
        return None

    except Exception as exc:
        _log.warning("failed_to_load_embedding_profile | error=%s", exc)
        return None


def _profile_to_embedding_config(profile: Any) -> dict[str, Any]:
    config: dict[str, Any] = {
        "provider": str(profile.provider),
        "model": str(profile.model_id),
    }
    if getattr(profile, "api_key", ""):
        config["api_key"] = str(profile.api_key)
    if getattr(profile, "base_url", ""):
        config["base_url"] = str(profile.base_url)

    _log.info(
        "found_embedding_profile | provider=%s | model=%s",
        profile.provider,
        profile.model_id,
    )
    return config
