"""Storage-boundary tests for project-local audio artifacts."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from novel_forge.core.config import Settings
from novel_forge.persistence.models import ProjectLayout
from novel_forge.tts.pipeline.assemble_audio_step import AssembleAudioStep
from novel_forge.tts.runtime.storage_limits import (
    ProjectAudioStorageLimitError,
    ensure_project_audio_write,
)


def test_settings_rejects_audio_model_directory_inside_project_storage(tmp_path: Path) -> None:
    storage_root = tmp_path / "data"

    with pytest.raises(ValidationError, match="模型权重不能保存到 data/<项目>/ 目录"):
        Settings(
            _env_file=None,
            storage_root=storage_root,
            audio_models_root=str(storage_root / "demo" / "tts" / "models"),
            sound_generation_stable_audio_models_dir=str(tmp_path / "application-models"),
        )


def test_project_audio_write_rejects_large_or_outside_artifacts(tmp_path: Path) -> None:
    layout = ProjectLayout(tmp_path / "data" / "demo")
    layout.ensure_dirs()
    settings = Settings(
        _env_file=None,
        storage_root=tmp_path / "data",
        audio_models_root=str(tmp_path / "application-models"),
        sound_generation_stable_audio_models_dir=str(tmp_path / "stable-audio-models"),
        tts_project_max_storage_mb=64,
        tts_project_max_file_mb=8,
    )

    with pytest.raises(ProjectAudioStorageLimitError, match="单文件上限"):
        ensure_project_audio_write(
            layout=layout,
            settings=settings,
            destination=layout.tts_audio_dir(1) / "too-large.mp3",
            incoming_bytes=9 * 1024 * 1024,
            artifact="测试音频",
        )

    with pytest.raises(ProjectAudioStorageLimitError, match="项目音频目录之外"):
        ensure_project_audio_write(
            layout=layout,
            settings=settings,
            destination=layout.root / "models" / "weight.bin",
            incoming_bytes=1024,
            artifact="模型",
        )


def test_project_audio_write_accounts_for_existing_project_audio(tmp_path: Path) -> None:
    layout = ProjectLayout(tmp_path / "data" / "demo")
    layout.ensure_dirs()
    existing = layout.tts_audio_dir(1) / "existing.mp3"
    existing.parent.mkdir(parents=True, exist_ok=True)
    existing.write_bytes(b"x" * (63 * 1024 * 1024))
    settings = Settings(
        _env_file=None,
        storage_root=tmp_path / "data",
        audio_models_root=str(tmp_path / "application-models"),
        sound_generation_stable_audio_models_dir=str(tmp_path / "stable-audio-models"),
        tts_project_max_storage_mb=64,
        tts_project_max_file_mb=8,
    )

    with pytest.raises(ProjectAudioStorageLimitError, match="项目上限"):
        ensure_project_audio_write(
            layout=layout,
            settings=settings,
            destination=layout.tts_audio_dir(1) / "new.mp3",
            incoming_bytes=2 * 1024 * 1024,
            artifact="测试音频",
        )


def test_assembly_requires_a_project_layout_or_explicit_output_path() -> None:
    step = AssembleAudioStep(settings=Settings(_env_file=None))

    with pytest.raises(RuntimeError, match="必须提供 ProjectLayout"):
        step._get_output_dir(1)  # noqa: SLF001
