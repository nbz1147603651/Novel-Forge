"""Canonical TTS delivery gate tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from novel_forge.tts.schemas import ChapterAudioResult, DubbingScript
from novel_forge.tts.services.delivery import (
    TTSDeliveryNotReadyError,
    delivery_blocking_reasons,
    require_delivery_ready,
)


def _result(path: Path, **updates) -> ChapterAudioResult:
    return ChapterAudioResult(
        chapter_number=1,
        script=DubbingScript(chapter_number=1),
        assembled_audio_path=str(path),
        is_complete=True,
        delivery_ready=True,
    ).model_copy(update=updates)


def test_delivery_gate_accepts_only_existing_nonempty_master(tmp_path: Path) -> None:
    master = tmp_path / "master.mp3"
    master.write_bytes(b"audio")

    assert require_delivery_ready(_result(master)).assembled_audio_path == str(master)


def test_delivery_gate_rechecks_artifact_and_staleness_at_consumption_time(tmp_path: Path) -> None:
    missing = _result(tmp_path / "missing.mp3")
    stale_path = tmp_path / "stale.mp3"
    stale_path.write_bytes(b"audio")
    stale = _result(stale_path, metadata={"assembly_stale": True})

    assert delivery_blocking_reasons(missing) == ("missing_assembled_audio",)
    assert delivery_blocking_reasons(stale) == ("assembly_stale",)
    with pytest.raises(TTSDeliveryNotReadyError):
        require_delivery_ready(stale)


def test_delivery_gate_does_not_trust_complete_without_delivery_decision(tmp_path: Path) -> None:
    master = tmp_path / "master.mp3"
    master.write_bytes(b"audio")
    result = _result(master, delivery_ready=False, delivery_blocking_reasons=[])

    with pytest.raises(TTSDeliveryNotReadyError) as exc_info:
        require_delivery_ready(result)

    assert exc_info.value.reasons == ("delivery_not_ready",)

