"""Unit tests for Voice Studio background workers."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from novel_forge.core.config import Settings
from novel_forge.desktop.pages.voice_studio.workers import (
    AssembleChapterAudioWorker,
    ExportAudiobookWorker,
    ExportAudioWorker,
)
from novel_forge.persistence.models import ProjectLayout
from novel_forge.workspace.execution_result import ExecutionResult


def _layout(tmp_path: Path) -> ProjectLayout:
    return ProjectLayout(project_dir=tmp_path / "project")


def test_assemble_worker_stores_fast_flag(tmp_path: Path) -> None:
    """``AssembleChapterAudioWorker(fast=True)`` must record the flag for ``_run_async``."""
    settings = Settings(_env_file=None, tts_default_provider="mock")
    worker = AssembleChapterAudioWorker(
        project_id="proj",
        chapter_number=1,
        settings=settings,
        layout=_layout(tmp_path),
        fast=True,
    )
    assert worker._fast is True


def test_assemble_worker_defaults_to_full_reassemble(tmp_path: Path) -> None:
    """Without ``fast=True`` the worker must run the full-quality pipeline."""
    settings = Settings(_env_file=None, tts_default_provider="mock")
    worker = AssembleChapterAudioWorker(
        project_id="proj",
        chapter_number=1,
        settings=settings,
        layout=_layout(tmp_path),
    )
    assert worker._fast is False


@pytest.mark.asyncio
async def test_assemble_worker_passes_fast_flag_to_execution(tmp_path: Path) -> None:
    """``_run_async`` must forward ``self._fast`` to ``execute_reassemble_chapter_audio``."""
    settings = Settings(_env_file=None, tts_default_provider="mock")
    worker = AssembleChapterAudioWorker(
        project_id="proj",
        chapter_number=1,
        settings=settings,
        layout=_layout(tmp_path),
        fast=True,
    )

    captured: dict[str, Any] = {}

    async def _fake_reassemble(**kwargs: Any) -> ExecutionResult[dict[str, Any]]:
        captured.update(kwargs)
        return ExecutionResult(
            project_id=str(kwargs.get("project_id", "")),
            result={"chapter_number": 1, "assembled_audio_path": "/tmp/out.mp3"},
        )

    with patch(
        "novel_forge.desktop.pages.voice_studio.workers.execute_reassemble_chapter_audio",
        side_effect=_fake_reassemble,
    ):
        await worker._run_async()

    assert captured.get("fast") is True, "fast=True must be forwarded to the execution function"
    assert captured.get("chapter_number") == 1
    assert captured.get("project_id") == "proj"


@pytest.mark.asyncio
async def test_assemble_worker_full_reassemble_passes_fast_false(tmp_path: Path) -> None:
    """Default worker must NOT pass fast=True (full quality reassemble)."""
    settings = Settings(_env_file=None, tts_default_provider="mock")
    worker = AssembleChapterAudioWorker(
        project_id="proj",
        chapter_number=2,
        settings=settings,
        layout=_layout(tmp_path),
    )

    captured: dict[str, Any] = {}

    async def _fake_reassemble(**kwargs: Any) -> ExecutionResult[dict[str, Any]]:
        captured.update(kwargs)
        return ExecutionResult(
            project_id=str(kwargs.get("project_id", "")),
            result={"chapter_number": 2, "assembled_audio_path": "/tmp/out.mp3"},
        )

    with patch(
        "novel_forge.desktop.pages.voice_studio.workers.execute_reassemble_chapter_audio",
        side_effect=_fake_reassemble,
    ):
        await worker._run_async()

    assert captured.get("fast") is False


# ── ExportAudioWorker tests ─────────────────────────────────────────────


def test_export_worker_stores_format_and_lufs(tmp_path: Path) -> None:
    settings = Settings(_env_file=None, tts_default_provider="mock")
    worker = ExportAudioWorker(
        project_id="proj",
        chapter_number=1,
        export_format="wav",
        settings=settings,
        layout=_layout(tmp_path),
        target_lufs=-14.0,
        include_srt=True,
    )
    assert worker._export_format == "wav"
    assert worker._target_lufs == -14.0
    assert worker._include_srt is True


def test_export_worker_defaults(tmp_path: Path) -> None:
    settings = Settings(_env_file=None, tts_default_provider="mock")
    worker = ExportAudioWorker(
        project_id="proj",
        chapter_number=1,
        settings=settings,
        layout=_layout(tmp_path),
    )
    assert worker._export_format == "mp3"
    assert worker._target_lufs is None
    assert worker._include_srt is False


@pytest.mark.asyncio
async def test_export_worker_delegates_chapter_delivery_to_shared_engine_path(tmp_path: Path) -> None:
    """PySide only chooses the local destination; the shared service owns export work."""
    layout = _layout(tmp_path)
    settings = Settings(_env_file=None, tts_default_provider="mock")
    destination = tmp_path / "exported.wav"

    worker = ExportAudioWorker(
        project_id="proj",
        chapter_number=1,
        export_format="wav",
        output_path=str(destination),
        settings=settings,
        layout=layout,
        target_lufs=-14.0,
    )
    captured: dict[str, Any] = {}
    completions: list[dict[str, Any]] = []
    worker.signals.audio_completed.connect(completions.append)

    async def fake_delivery(**kwargs: Any) -> ExecutionResult[dict[str, Any]]:
        captured.update(kwargs)
        return ExecutionResult(project_id="proj", result={"export_filename": "exported.wav"})

    with patch(
        "novel_forge.desktop.pages.voice_studio.workers.execute_export_audio_delivery",
        side_effect=fake_delivery,
    ):
        await worker._run_async()

    assert captured == {
        "project_id": "proj",
        "layout": layout,
        "scope": "chapter",
        "chapter_number": 1,
        "format": "wav",
        "include_subtitles": False,
        "target_lufs": -14.0,
        "destination": destination,
    }
    assert completions == [{"export_path": str(destination)}]


@pytest.mark.asyncio
async def test_export_worker_delegates_book_archive_to_shared_engine_path(tmp_path: Path) -> None:
    """The PySide all-chapters action preserves its local ZIP chooser through the adapter."""
    layout = _layout(tmp_path)
    settings = Settings(_env_file=None, tts_default_provider="mock")
    destination = tmp_path / "export.zip"

    worker = ExportAudioWorker(
        project_id="proj",
        chapter_number=0,
        export_format="zip",
        output_path=str(destination),
        settings=settings,
        layout=layout,
        include_srt=True,
    )
    captured: dict[str, Any] = {}

    async def fake_delivery(**kwargs: Any) -> ExecutionResult[dict[str, Any]]:
        captured.update(kwargs)
        return ExecutionResult(project_id="proj", result={"export_filename": "export.zip"})

    with patch(
        "novel_forge.desktop.pages.voice_studio.workers.execute_export_audio_delivery",
        side_effect=fake_delivery,
    ):
        await worker._run_async()

    assert captured == {
        "project_id": "proj",
        "layout": layout,
        "scope": "book",
        "chapter_number": None,
        "format": "zip",
        "include_subtitles": True,
        "target_lufs": None,
        "destination": destination,
    }


@pytest.mark.asyncio
async def test_audiobook_worker_delegates_to_shared_atomic_delivery(tmp_path: Path) -> None:
    settings = Settings(_env_file=None, tts_default_provider="mock")
    layout = _layout(tmp_path)
    worker = ExportAudiobookWorker(project_id="proj", settings=settings, layout=layout)
    captured: dict[str, Any] = {}
    completions: list[dict[str, Any]] = []
    worker.signals.audio_completed.connect(completions.append)

    async def fake_delivery(**kwargs: Any) -> ExecutionResult[dict[str, Any]]:
        captured.update(kwargs)
        return ExecutionResult(
            project_id="proj",
            result={
                "export_filename": "proj_audiobook_20260812.zip",
                "package_id": "20260812",
                "chapters": [1, 2],
            },
        )

    with patch(
        "novel_forge.desktop.pages.voice_studio.workers.execute_export_audiobook_delivery",
        side_effect=fake_delivery,
    ):
        await worker._run_async()

    assert captured["project_id"] == "proj"
    assert captured["layout"] is layout
    assert completions == [
        {
            "export_path": str(layout.root / "exports" / "voice" / "proj_audiobook_20260812.zip"),
            "package_id": "20260812",
            "chapter_count": 2,
        }
    ]
