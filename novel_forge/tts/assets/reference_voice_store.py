"""Durable local reference-voice profiles without gateway imports."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from novel_forge.persistence.filesystem import atomic_write_bytes, atomic_write_json


class ReferenceVoiceStore:
    """Store only durable, serializable voice assets — never GPU objects."""

    def __init__(self, root: str) -> None:
        self._root = Path(root).expanduser()

    def save_reference(self, voice_id: str, reference: str, **metadata: Any) -> dict[str, Any]:
        path = Path(reference).expanduser()
        if not path.is_file():
            raise ValueError("Native voice cloning requires a local reference audio file")
        if path.suffix.lower() not in {".wav", ".mp3", ".m4a", ".flac", ".ogg"}:
            raise ValueError("Reference audio must be wav, mp3, m4a, flac or ogg")
        if path.stat().st_size > 32 * 1024 * 1024:
            raise ValueError("Reference audio must not exceed 32 MB")

        key = self._key(voice_id)
        reference_dir = self._root / "references"
        reference_path = reference_dir / f"{key}{path.suffix.lower()}"
        atomic_write_bytes(reference_path, path.read_bytes())
        profile = {
            "voice_id": voice_id,
            "reference_audio_path": str(reference_path),
            **metadata,
        }
        atomic_write_json(self._profile_path(voice_id), profile)
        return profile

    def load(self, voice_id: str) -> dict[str, Any] | None:
        path = self._profile_path(voice_id)
        if not path.is_file():
            return None
        import json

        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else None
        except (OSError, ValueError):
            return None

    def update(self, voice_id: str, **updates: Any) -> dict[str, Any]:
        profile = self.load(voice_id) or {"voice_id": voice_id}
        profile.update(updates)
        atomic_write_json(self._profile_path(voice_id), profile)
        return profile

    def asset_path(self, voice_id: str, suffix: str) -> Path:
        return self._root / "assets" / f"{self._key(voice_id)}{suffix}"

    def _profile_path(self, voice_id: str) -> Path:
        return self._root / "profiles" / f"{self._key(voice_id)}.json"

    @staticmethod
    def _key(voice_id: str) -> str:
        return hashlib.sha256(voice_id.encode("utf-8")).hexdigest()[:32]
