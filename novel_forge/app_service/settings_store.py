"""UI-neutral persistence for Engine-managed settings and model profiles."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from novel_forge.core.config import get_writable_env_path, reset_settings
from novel_forge.gateway.profiles import ProfilesConfig, get_profiles_path
from novel_forge.persistence.filesystem import atomic_write_text

_DEPRECATED_ENV_KEYS: frozenset[str] = frozenset({
    "NOVEL_FORGE_TASK_ROUTING",
    "NOVEL_FORGE_TASK_FALLBACK_ROUTING",
})


@dataclass(frozen=True)
class SettingsSaveResult:
    """Result returned after a durable settings persistence attempt."""

    profiles_path: Path
    env_path: Path | None
    blocked_tasks: list[str] = field(default_factory=list)


class SettingsStore:
    """Persist Engine-managed settings without importing a desktop client."""

    def __init__(
        self,
        *,
        profiles_path: Path | None = None,
        env_path: Path | None = None,
    ) -> None:
        self._profiles_path = profiles_path or get_profiles_path()
        self._env_path = env_path or get_writable_env_path()
        self._loader: Any | None = None

    @property
    def profiles_path(self) -> Path:
        return self._profiles_path

    @property
    def env_path(self) -> Path:
        return self._env_path

    def save(
        self,
        *,
        config: ProfilesConfig,
        env_pairs: dict[str, str],
        blocked_tasks: list[str] | None = None,
        strip_env_keys: set[str] | None = None,
    ) -> SettingsSaveResult:
        config.save(self._profiles_path)
        env_path = self._env_path if (env_pairs or self._env_path.exists()) else None
        if env_path is not None:
            self.merge_env(env_path, env_pairs, strip_keys=strip_env_keys)
        return SettingsSaveResult(
            profiles_path=self._profiles_path,
            env_path=env_path,
            blocked_tasks=list(blocked_tasks or []),
        )

    @staticmethod
    def merge_env(
        env_path: Path,
        new_map: dict[str, str],
        *,
        strip_keys: set[str] | None = None,
    ) -> None:
        """Merge managed values while retaining comments and unmanaged keys."""
        strip = _DEPRECATED_ENV_KEYS | frozenset(strip_keys or ())
        clean_map = {key: value for key, value in new_map.items() if key not in strip}
        env_path.parent.mkdir(parents=True, exist_ok=True)
        original = env_path.read_text(encoding="utf-8") if env_path.exists() else ""
        result_lines: list[str] = []
        written_keys: set[str] = set()

        for original_line in original.splitlines():
            stripped = original_line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                result_lines.append(original_line)
                continue
            key = stripped.split("=", 1)[0].strip()
            if key in strip:
                continue
            if key in clean_map:
                result_lines.append(f"{key}={clean_map[key]}")
                written_keys.add(key)
            else:
                result_lines.append(original_line)

        for key, value in clean_map.items():
            if key not in written_keys:
                result_lines.append(f"{key}={value}")

        atomic_write_text(env_path, "\n".join(result_lines) + "\n")

    def reload(self) -> None:
        """Invalidate settings caches after a persisted configuration change."""
        reset_settings()
        if self._loader is not None:
            self._loader.reload()

    def has_changed(self) -> bool:
        """Return whether a backing settings source has changed."""
        from novel_forge.core.infra.settings_loader import UnifiedSettingsLoader

        if self._loader is None:
            self._loader = UnifiedSettingsLoader()
        return self._loader.has_changed()
