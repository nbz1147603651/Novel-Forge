"""Unit tests for the resolve-speakers API endpoint."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from novel_forge.api.routes import tts
from novel_forge.core.config import Settings
from novel_forge.persistence.filesystem import FileSystemStorage
from novel_forge.persistence.models import ProjectLayout
from novel_forge.tts.schemas import DubbingScript, DubbingSegment, SegmentType


def _runtime(tmp_path):
    return SimpleNamespace(
        settings=Settings(_env_file=None, tts_default_provider="mock"),
        storage=FileSystemStorage(tmp_path),
    )


def _write_script(layout: ProjectLayout, chapter: int, script: DubbingScript) -> None:
    path = layout.tts_dubbing_script_path(chapter)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(script.model_dump_json(), encoding="utf-8")


def _write_voice_team(layout: ProjectLayout) -> None:
    team_data = {
        "entries": [
            {"character_id": "c1", "character_name": "林小满", "voice_id": "v1"},
            {"character_id": "c2", "character_name": "苏晚", "voice_id": "v2"},
        ],
        "narrator_voice_id": "narrator_001",
    }
    layout.tts_voice_team_path.parent.mkdir(parents=True, exist_ok=True)
    layout.tts_voice_team_path.write_text(
        json.dumps(team_data, ensure_ascii=False), encoding="utf-8"
    )


def _make_script_with_unresolved() -> DubbingScript:
    return DubbingScript(
        chapter_number=1,
        segments=[
            DubbingSegment(
                segment_index=0,
                segment_type=SegmentType.NARRATION,
                text="夜色渐深。",
            ),
            DubbingSegment(
                segment_index=1,
                segment_type=SegmentType.DIALOGUE,
                text="别碰它。",
                character_id="",
                character_name="",
            ),
            DubbingSegment(
                segment_index=2,
                segment_type=SegmentType.DIALOGUE,
                text="我来。",
                character_id="",
                character_name="",
            ),
        ],
        metadata={
            "speaker_adjudication": {
                "status": "needs_review",
                "unresolved_segment_indices": [1, 2],
                "decisions": [],
            }
        },
    )


async def test_resolve_speakers_assigns_character(tmp_path) -> None:
    """Resolving a segment assigns character_id and updates metadata."""
    runtime = _runtime(tmp_path)
    layout = ProjectLayout(runtime.storage.project_dir("proj"))
    layout.ensure_dirs()
    _write_voice_team(layout)
    _write_script(layout, 1, _make_script_with_unresolved())

    result = await tts.resolve_speakers(
        "proj",
        1,
        tts.ResolveSpeakersRequest(
            resolutions=[
                tts.SegmentResolution(segment_index=1, character_id="c1"),
            ]
        ),
        runtime,
    )

    assert result["remaining_count"] == 1
    assert 2 in result["remaining_unresolved"]
    script_data = result["script"]
    seg1 = script_data["segments"][1]
    assert seg1["character_id"] == "c1"
    assert seg1["character_name"] == "林小满"
    assert seg1["segment_type"] == "dialogue"


async def test_resolve_speakers_convert_to_narration(tmp_path) -> None:
    """Empty character_id converts segment to narration."""
    runtime = _runtime(tmp_path)
    layout = ProjectLayout(runtime.storage.project_dir("proj"))
    layout.ensure_dirs()
    _write_voice_team(layout)
    _write_script(layout, 1, _make_script_with_unresolved())

    result = await tts.resolve_speakers(
        "proj",
        1,
        tts.ResolveSpeakersRequest(
            resolutions=[
                tts.SegmentResolution(segment_index=1, character_id=""),
            ]
        ),
        runtime,
    )

    script_data = result["script"]
    seg1 = script_data["segments"][1]
    assert seg1["segment_type"] == "narration"
    assert seg1["character_id"] == ""


async def test_resolve_all_speakers_sets_status_passed(tmp_path) -> None:
    """Resolving all unresolved segments sets adjudication status to passed."""
    runtime = _runtime(tmp_path)
    layout = ProjectLayout(runtime.storage.project_dir("proj"))
    layout.ensure_dirs()
    _write_voice_team(layout)
    _write_script(layout, 1, _make_script_with_unresolved())

    result = await tts.resolve_speakers(
        "proj",
        1,
        tts.ResolveSpeakersRequest(
            resolutions=[
                tts.SegmentResolution(segment_index=1, character_id="c1"),
                tts.SegmentResolution(segment_index=2, character_id="c2"),
            ]
        ),
        runtime,
    )

    assert result["remaining_count"] == 0
    assert result["remaining_unresolved"] == []
    adjudication = result["script"]["metadata"]["speaker_adjudication"]
    assert adjudication["status"] == "passed"
    assert adjudication["unresolved_segment_indices"] == []


async def test_resolve_speakers_invalid_index_raises(tmp_path) -> None:
    """Out-of-range segment_index raises 422."""
    runtime = _runtime(tmp_path)
    layout = ProjectLayout(runtime.storage.project_dir("proj"))
    layout.ensure_dirs()
    _write_voice_team(layout)
    _write_script(layout, 1, _make_script_with_unresolved())

    with pytest.raises(HTTPException) as exc_info:
        await tts.resolve_speakers(
            "proj",
            1,
            tts.ResolveSpeakersRequest(
                resolutions=[
                    tts.SegmentResolution(segment_index=99, character_id="c1"),
                ]
            ),
            runtime,
        )
    assert exc_info.value.status_code == 422


async def test_resolve_speakers_missing_script_raises_404(tmp_path) -> None:
    """Missing script file raises 404."""
    runtime = _runtime(tmp_path)
    layout = ProjectLayout(runtime.storage.project_dir("proj"))
    layout.ensure_dirs()

    with pytest.raises(HTTPException) as exc_info:
        await tts.resolve_speakers(
            "proj",
            1,
            tts.ResolveSpeakersRequest(resolutions=[]),
            runtime,
        )
    assert exc_info.value.status_code == 404


async def test_resolve_speakers_inner_thought_type(tmp_path) -> None:
    """segment_type='inner_thought' sets the correct type."""
    runtime = _runtime(tmp_path)
    layout = ProjectLayout(runtime.storage.project_dir("proj"))
    layout.ensure_dirs()
    _write_voice_team(layout)
    _write_script(layout, 1, _make_script_with_unresolved())

    result = await tts.resolve_speakers(
        "proj",
        1,
        tts.ResolveSpeakersRequest(
            resolutions=[
                tts.SegmentResolution(
                    segment_index=2, character_id="c2", segment_type="inner_thought"
                ),
            ]
        ),
        runtime,
    )

    seg2 = result["script"]["segments"][2]
    assert seg2["segment_type"] == "inner_thought"
    assert seg2["character_id"] == "c2"
    assert seg2["character_name"] == "苏晚"
