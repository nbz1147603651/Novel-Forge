"""Audio plugin registry with optional user-manifest discovery."""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path

from novel_forge.obs.logger import get_logger
from novel_forge.tts.platform.catalog import builtin_audio_plugin_manifests
from novel_forge.tts.platform.schemas import (
    AudioCapability,
    AudioLocationPolicy,
    AudioPluginManifest,
    AudioProjectConstraints,
)

_log = get_logger("tts.platform.registry")


class AudioPluginRegistry:
    """Single source of truth for model capabilities.

    The registry owns descriptors only.  Loading a descriptor never imports a
    model framework, opens a network connection, or downloads model weights.
    """

    def __init__(self, manifests: Iterable[AudioPluginManifest] = ()) -> None:
        self._manifests: dict[str, AudioPluginManifest] = {}
        for manifest in manifests:
            self.register(manifest)

    @classmethod
    def builtins(cls) -> "AudioPluginRegistry":
        return cls(builtin_audio_plugin_manifests())

    def register(self, manifest: AudioPluginManifest, *, replace: bool = False) -> None:
        if manifest.plugin_id in self._manifests and not replace:
            raise ValueError(f"Audio plugin already registered: {manifest.plugin_id}")
        self._manifests[manifest.plugin_id] = manifest

    def get(self, plugin_id: str) -> AudioPluginManifest | None:
        return self._manifests.get(plugin_id)

    def require(self, plugin_id: str) -> AudioPluginManifest:
        manifest = self.get(plugin_id)
        if manifest is None:
            raise KeyError(f"Unknown audio plugin: {plugin_id}")
        return manifest

    def all(self) -> list[AudioPluginManifest]:
        return sorted(
            self._manifests.values(),
            key=lambda item: (-item.quality.priority, item.display_name.lower()),
        )

    def candidates(
        self,
        capability: AudioCapability,
        *,
        languages: Iterable[str] = (),
        constraints: AudioProjectConstraints | None = None,
    ) -> list[AudioPluginManifest]:
        language_list = [item for item in languages if item and item != "auto"]
        result: list[AudioPluginManifest] = []
        for manifest in self._manifests.values():
            if not manifest.supplies(capability):
                continue
            if manifest.integration_status not in {"built_in", "installed", "available"}:
                continue
            if constraints is not None:
                if manifest.plugin_id in constraints.disabled_plugins:
                    continue
                if (
                    constraints.location_policy == AudioLocationPolicy.LOCAL_ONLY
                    and manifest.runtime.is_cloud
                ):
                    continue
                if (
                    constraints.location_policy == AudioLocationPolicy.CLOUD_ONLY
                    and not manifest.runtime.is_cloud
                ):
                    continue
            if language_list and not all(
                manifest.capabilities.supports_language(language) for language in language_list
            ):
                continue
            result.append(manifest)
        return sorted(
            result,
            key=lambda item: (-item.quality.priority, -item.quality.quality_score),
        )

    def load_manifest_dirs(self, directories: Iterable[str | Path]) -> list[str]:
        """Load explicit JSON manifests without executing third-party code."""

        loaded: list[str] = []
        for raw_dir in directories:
            directory = Path(raw_dir).expanduser()
            if not directory.is_dir():
                continue
            for path in sorted(directory.glob("*.audio-plugin.json")):
                try:
                    payload = json.loads(path.read_text(encoding="utf-8"))
                    manifest = AudioPluginManifest.model_validate(payload)
                    self.register(manifest, replace=True)
                    loaded.append(manifest.plugin_id)
                except Exception as exc:
                    _log.warning("Ignoring invalid audio plugin manifest %s: %s", path, exc)
        return loaded
