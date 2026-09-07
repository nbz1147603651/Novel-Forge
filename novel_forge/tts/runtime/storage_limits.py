"""Project-local audio storage boundaries and size budgets."""

from __future__ import annotations

import os
from pathlib import Path

from novel_forge.core.config import Settings
from novel_forge.persistence.models import ProjectLayout

_MEBIBYTE = 1024 * 1024


class ProjectAudioStorageLimitError(RuntimeError):
    """Raised before a project-local audio artifact would exceed its budget."""


def ensure_project_audio_write(
    *,
    layout: ProjectLayout,
    settings: Settings,
    destination: Path,
    incoming_bytes: int,
    artifact: str,
) -> None:
    """Ensure a write stays inside ``tts/`` and within the configured budget."""
    if incoming_bytes < 0:
        raise ValueError("incoming_bytes must not be negative")

    tts_root = layout.tts_dir.resolve(strict=False)
    resolved_destination = destination.resolve(strict=False)
    try:
        resolved_destination.relative_to(tts_root)
    except ValueError as exc:
        raise ProjectAudioStorageLimitError(
            f"拒绝将{artifact}写入项目音频目录之外：{destination}"
        ) from exc

    max_file_bytes = settings.tts_project_max_file_mb * _MEBIBYTE
    if incoming_bytes > max_file_bytes:
        raise ProjectAudioStorageLimitError(
            f"{artifact}大小为{_format_bytes(incoming_bytes)}，超过单文件上限"
            f"{settings.tts_project_max_file_mb} MB。"
        )

    current_bytes = _directory_size(tts_root)
    replaced_bytes = destination.stat().st_size if destination.is_file() else 0
    max_project_bytes = settings.tts_project_max_storage_mb * _MEBIBYTE
    projected_bytes = current_bytes - replaced_bytes + incoming_bytes
    if projected_bytes > max_project_bytes:
        raise ProjectAudioStorageLimitError(
            f"{artifact}会使项目音频占用达到{_format_bytes(projected_bytes)}，超过"
            f"项目上限{settings.tts_project_max_storage_mb} MB。"
        )


def _directory_size(root: Path) -> int:
    if not root.is_dir():
        return 0
    total = 0
    for current_root, _dirs, files in os.walk(root):
        for filename in files:
            path = Path(current_root) / filename
            try:
                total += path.stat().st_size
            except OSError:
                continue
    return total


def _format_bytes(value: int) -> str:
    return f"{value / _MEBIBYTE:.1f} MB"
