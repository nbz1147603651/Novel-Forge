"""Tests for the finished audiobook delivery package export (Batch 1, P0-1).

Covers the plan acceptance criteria:
- the export produces the complete package structure (per-chapter mp3 copies,
  TOC/cover ``metadata.json``, segment-level ``assembly_report.md``);
- the assembly report carries segment numbers + timestamps so a re-record
  target can be located without re-listening to whole chapters;
- the sequential chapter gate refuses export until every chapter in the
  exported range is assembled and delivery-confirmed, reporting exactly which
  chapters the author has to confirm ("第 N 章确认后继续");
- control-plane replay resolves pre-recorded human decisions without UI
  interaction (CallbackDecisionProvider preseeded matching).
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path
from typing import Any

import pytest

from novel_forge.persistence.filesystem import FileSystemStorage
from novel_forge.persistence.models import ProjectLayout
from novel_forge.pipeline.long.human_decision import (
    CallbackDecisionProvider,
    HumanDecisionOption,
    HumanDecisionRequest,
)
from novel_forge.tts.platform.schemas import SpeechTimeline, SpeechTimelineEntry
from novel_forge.tts.schemas import (
    ChapterAudioResult,
    DubbingScript,
    DubbingSegment,
    MixRenderEventResult,
    MixRenderReport,
)
from novel_forge.workspace.tts_ops.execution_export import (
    _atomic_convert_audio,
    execute_export_audio_delivery,
    execute_export_audiobook_delivery,
    execute_export_audiobook_package,
)


def _seed_layout(tmp_path: Path, *, project_id: str) -> ProjectLayout:
    storage = FileSystemStorage(tmp_path)
    layout = ProjectLayout(storage.project_dir(project_id))
    layout.root.mkdir(parents=True, exist_ok=True)
    layout.spec_path.write_text(
        json.dumps(
            {"title": "测试有声书", "author": "测试作者", "language": "zh"},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    layout.outline_path.write_text(
        json.dumps(
            {
                "title": "测试有声书",
                "chapters": [
                    {"chapter_number": 1, "title": "开局"},
                    {"chapter_number": 2, "title": "转折"},
                    {"chapter_number": 3, "title": "终局"},
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return layout


def _seed_chapter_audio(
    layout: ProjectLayout,
    chapter_number: int,
    *,
    delivery_ready: bool = True,
    is_complete: bool = True,
    write_assembled: bool = True,
    write_timeline: bool = True,
    write_stems: bool = False,
) -> None:
    assembled_path = layout.tts_dir / "assembled" / f"chapter_{chapter_number:03d}.mp3"
    if write_assembled:
        assembled_path.parent.mkdir(parents=True, exist_ok=True)
        assembled_path.write_bytes(b"FAKE-MP3-BYTES-" + str(chapter_number).encode())

    segments = [
        DubbingSegment(
            segment_index=0,
            segment_type="narration",
            character_name="",
            text="夜色降临，城门缓缓关闭。",
        ),
        DubbingSegment(
            segment_index=1,
            segment_type="dialogue",
            character_id="hero",
            character_name="林远",
            text="我们必须今晚出发。",
        ),
    ]
    stem_paths: dict[str, str] = {}
    if write_stems:
        for stem_name in ("voice", "bed", "sfx"):
            stem_path = layout.tts_dir / "assembled" / f"chapter_{chapter_number:03d}_{stem_name}.wav"
            stem_path.write_bytes(f"{stem_name}-stem".encode())
            stem_paths[stem_name] = str(stem_path)
    result = ChapterAudioResult(
        chapter_number=chapter_number,
        script=DubbingScript(chapter_number=chapter_number, segments=segments),
        assembled_audio_path=str(assembled_path) if write_assembled else "",
        total_duration_ms=65_432,
        total_cost_usd=0.012345,
        is_complete=is_complete,
        delivery_ready=delivery_ready,
        mix_render_report=(
            MixRenderReport(
                chapter_number=chapter_number,
                status="completed",
                passed=True,
                mastering_succeeded=True,
                planned_event_count=1,
                rendered_event_count=1,
                output_path=str(assembled_path),
                output_hash="a" * 64,
                events=[
                    MixRenderEventResult(
                        event_id="voice:0",
                        asset_id="voice:0",
                        bus="voice",
                        status="rendered",
                        planned_start_ms=0,
                        planned_end_ms=65_432,
                    )
                ],
                stem_paths=stem_paths,
            )
            if stem_paths
            else None
        ),
    )
    result_path = layout.tts_audio_result_path(chapter_number)
    result_path.parent.mkdir(parents=True, exist_ok=True)
    result_path.write_text(
        result.model_dump_json(indent=2), encoding="utf-8"
    )

    if write_timeline:
        timeline = SpeechTimeline(
            chapter_number=chapter_number,
            entries=[
                SpeechTimelineEntry(
                    segment_index=0,
                    text="夜色降临，城门缓缓关闭。",
                    start_ms=0,
                    end_ms=32_100,
                    alignment_status="aligned",
                ),
                SpeechTimelineEntry(
                    segment_index=1,
                    text="我们必须今晚出发。",
                    start_ms=32_100,
                    end_ms=65_432,
                    alignment_status="segment_fallback",
                ),
            ],
            total_duration_ms=65_432,
        )
        timeline_path = layout.tts_speech_timeline_path(chapter_number)
        timeline_path.parent.mkdir(parents=True, exist_ok=True)
        timeline_path.write_text(timeline.model_dump_json(indent=2), encoding="utf-8")


def test_atomic_audio_conversion_keeps_requested_extension_on_temporary_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "master.mp3"
    source.write_bytes(b"master")
    destination = tmp_path / "deliveries" / "chapter.wav"
    observed: list[Path] = []

    def fake_convert(
        input_path: Path,
        output_path: Path,
        *,
        format: str,
        target_lufs: float | None,
    ) -> bool:
        assert input_path == source
        assert format == "wav"
        assert target_lufs == -14.0
        observed.append(output_path)
        output_path.write_bytes(b"wav-delivery")
        return True

    monkeypatch.setattr(
        "novel_forge.workspace.tts_ops.execution_export.convert_audio_for_export",
        fake_convert,
    )

    assert _atomic_convert_audio(
        source,
        destination,
        format="wav",
        target_lufs=-14.0,
    )
    assert observed[0].suffix == ".wav"
    assert observed[0].name.endswith(".partial.wav")
    assert destination.read_bytes() == b"wav-delivery"
    assert not list(destination.parent.glob(".*.partial*"))


@pytest.mark.asyncio
async def test_export_audiobook_package_produces_full_structure(tmp_path) -> None:
    layout = _seed_layout(tmp_path, project_id="book")
    (layout.root / "cover.png").write_bytes(b"COVER-PNG")
    _seed_chapter_audio(layout, 1, write_stems=True)
    _seed_chapter_audio(layout, 2)

    result = await execute_export_audiobook_package(
        project_id="book", layout=layout
    )

    payload = result.result
    assert "error" not in payload
    package_dir = Path(payload["package_dir"])
    assert package_dir.is_dir()
    assert payload["chapters"] == [1, 2]

    # Per-chapter mp3 copies reuse the assembled masters (no re-render).
    chapters_dir = package_dir / "chapters"
    copied = sorted(path.name for path in chapters_dir.iterdir())
    assert len(copied) == 2
    assert copied[0].startswith("001_") and copied[0].endswith(".mp3")
    assert copied[1].startswith("002_") and copied[1].endswith(".mp3")
    assert (chapters_dir / copied[0]).read_bytes() == b"FAKE-MP3-BYTES-1"
    assert (chapters_dir / copied[1]).read_bytes() == b"FAKE-MP3-BYTES-2"
    # Source masters stay untouched.
    assert (layout.tts_dir / "assembled" / "chapter_001.mp3").is_file()

    # Cover copied + TOC metadata complete.
    assert (package_dir / "cover.png").is_file()
    metadata = json.loads((package_dir / "metadata.json").read_text(encoding="utf-8"))
    assert metadata["package_format"] == "novel_forge.audiobook/v1"
    assert metadata["title"] == "测试有声书"
    assert metadata["author"] == "测试作者"
    assert metadata["cover"] == "cover.png"
    assert metadata["chapter_count"] == 2
    assert metadata["total_duration_ms"] == 130_864
    toc = metadata["chapters"]
    assert [entry["chapter_number"] for entry in toc] == [1, 2]
    assert toc[0]["title"] == "开局"
    assert toc[0]["file"] == f"chapters/{copied[0]}"
    assert toc[0]["duration_ms"] == 65_432
    assert toc[0]["delivery_ready"] is True
    assert toc[0]["stems"] == {
        "voice": "stems/001_voice_stem.wav",
        "bed": "stems/001_bed_stem.wav",
        "sfx": "stems/001_sfx_stem.wav",
    }
    assert (package_dir / toc[0]["stems"]["voice"]).read_bytes() == b"voice-stem"

    # Assembly report carries segment numbers + timestamps for re-record lookup.
    report = (package_dir / "assembly_report.md").read_text(encoding="utf-8")
    assert "有声书汇编报告：测试有声书" in report
    assert "| 段号 |" in report
    assert "00:00:32.100" in report  # segment 1 boundary from the timeline
    assert "林远" in report  # dialogue speaker resolved from the script
    assert "旁白" in report  # narration default speaker
    # fallback-aligned segments must be flagged for manual review
    assert "segment_fallback" in report or "回退" in report

    assert payload["metadata"]["package_id"] == package_dir.name


@pytest.mark.asyncio
async def test_durable_voice_delivery_exports_publish_downloadable_files_atomically(tmp_path) -> None:
    layout = _seed_layout(tmp_path, project_id="book")
    _seed_chapter_audio(layout, 1, write_stems=True)
    _seed_chapter_audio(layout, 2)
    local_chapter_destination = tmp_path / "author-downloads" / "chapter-one.mp3"

    engine_chapter = await execute_export_audio_delivery(
        project_id="book",
        layout=layout,
        scope="chapter",
        chapter_number=1,
        format="mp3",
    )
    engine_chapter_file = layout.root / "exports" / "voice" / "chapter_001.mp3"
    assert engine_chapter.result["export_filename"] == engine_chapter_file.name
    assert engine_chapter_file.read_bytes() == b"FAKE-MP3-BYTES-1"

    local_chapter = await execute_export_audio_delivery(
        project_id="book",
        layout=layout,
        scope="chapter",
        chapter_number=1,
        format="mp3",
        destination=local_chapter_destination,
    )
    assert local_chapter.result["export_filename"] == "chapter_001.mp3"
    assert local_chapter_destination.read_bytes() == b"FAKE-MP3-BYTES-1"
    assert not list(local_chapter_destination.parent.glob("*.partial"))

    audiobook = await execute_export_audiobook_delivery(project_id="book", layout=layout)
    package_file = layout.root / "exports" / "voice" / audiobook.result["export_filename"]
    assert package_file.is_file()
    assert not list(package_file.parent.glob("*.partial"))
    with zipfile.ZipFile(package_file) as archive:
        names = archive.namelist()
    assert any(name.endswith("/metadata.json") for name in names)
    assert any("/chapters/001_" in name for name in names)


@pytest.mark.asyncio
async def test_audio_delivery_rejects_stale_chapter_master_before_copying(tmp_path) -> None:
    layout = _seed_layout(tmp_path, project_id="book")
    _seed_chapter_audio(layout, 1)
    chapter_path = layout.chapter_path(1)
    chapter_path.parent.mkdir(parents=True, exist_ok=True)
    chapter_path.write_text("这是已经修改过的终稿。", encoding="utf-8")

    result_path = layout.tts_audio_result_path(1)
    result_payload = json.loads(result_path.read_text(encoding="utf-8"))
    result_payload["metadata"] = {"source_text_hash": "outdated-source-hash"}
    result_path.write_text(
        json.dumps(result_payload, ensure_ascii=False),
        encoding="utf-8",
    )
    destination = tmp_path / "author-downloads" / "stale.mp3"

    export = await execute_export_audio_delivery(
        project_id="book",
        layout=layout,
        scope="chapter",
        chapter_number=1,
        format="mp3",
        destination=destination,
    )

    assert export.result["error_code"] == "stale_chapter_audio"
    assert not destination.exists()


@pytest.mark.asyncio
async def test_export_audiobook_gate_reports_missing_chapters(tmp_path) -> None:
    layout = _seed_layout(tmp_path, project_id="book")
    _seed_chapter_audio(layout, 1)
    _seed_chapter_audio(layout, 3)  # chapter 2 missing entirely

    result = await execute_export_audiobook_package(
        project_id="book", layout=layout
    )

    payload = result.result
    assert payload["error_code"] == "audiobook_export_gate_required"
    assert payload["gate_chapters"] == [2]
    assert "第 2 章尚未确认" in payload["error"] or "2 章尚未确认" in payload["error"]
    # Nothing exported while the gate is open.
    assert not layout.tts_audiobook_export_dir.exists()


@pytest.mark.asyncio
async def test_export_audiobook_gate_blocks_undelivered_chapter(tmp_path) -> None:
    layout = _seed_layout(tmp_path, project_id="book")
    _seed_chapter_audio(layout, 1)
    _seed_chapter_audio(layout, 2, delivery_ready=False)

    result = await execute_export_audiobook_package(
        project_id="book", layout=layout
    )

    payload = result.result
    assert payload["error_code"] == "audiobook_export_gate_required"
    assert payload["gate_chapters"] == [2]
    reasons = {blocker["reason"] for blocker in payload["gate_blockers"]}
    assert "not_delivery_ready" in reasons

    # Relaxing the gate to "synthesis complete" lets it through.
    relaxed = await execute_export_audiobook_package(
        project_id="book", layout=layout, require_delivery_ready=False
    )
    assert "error" not in relaxed.result
    assert relaxed.result["chapters"] == [1, 2]


@pytest.mark.asyncio
async def test_export_audiobook_no_chapters_error(tmp_path) -> None:
    layout = _seed_layout(tmp_path, project_id="book")

    result = await execute_export_audiobook_package(
        project_id="book", layout=layout
    )

    payload = result.result
    assert payload["error_code"] == "audiobook_export_no_chapters"


@pytest.mark.asyncio
async def test_export_audiobook_explicit_chapter_subset(tmp_path) -> None:
    layout = _seed_layout(tmp_path, project_id="book")
    _seed_chapter_audio(layout, 1)
    _seed_chapter_audio(layout, 2)
    _seed_chapter_audio(layout, 3)

    result = await execute_export_audiobook_package(
        project_id="book", layout=layout, chapter_numbers=[1, 2]
    )

    payload = result.result
    assert "error" not in payload
    assert payload["chapters"] == [1, 2]
    metadata = payload["metadata"]
    assert metadata["chapter_count"] == 2
    assert payload["metadata"]["total_duration_ms"] == 130_864


def _gate_request(*, decision_id: str, chapter_number: int, kind: str = "chapter_delivery_gate") -> HumanDecisionRequest:
    return HumanDecisionRequest(
        decision_id=decision_id,
        kind=kind,
        project_id="book",
        chapter_number=chapter_number,
        title="章节交付门控",
        message=f"第 {chapter_number} 章确认后继续？",
        options=(
            HumanDecisionOption(id="continue_after_confirmation", label="确认后继续"),
            HumanDecisionOption(id="abort", label="中止"),
        ),
        default_option="continue_after_confirmation",
        timeout_seconds=1,
    )


@pytest.mark.asyncio
async def test_callback_decision_provider_replays_preseeded_gate_decision() -> None:
    """Resumed work units must resolve "第 N 章确认后继续" without UI again."""

    requested: list[dict[str, Any]] = []
    provider = CallbackDecisionProvider(
        requested.append,
        preseeded_decisions=[
            {
                "decision_id": "gate-ch2",
                "kind": "chapter_delivery_gate",
                "chapter_number": 2,
                "choice": "continue_after_confirmation",
            }
        ],
    )

    # Exact decision_id match wins.
    exact = await provider.request_decision(
        _gate_request(decision_id="gate-ch2", chapter_number=2)
    )
    assert exact.choice == "continue_after_confirmation"

    # (kind, chapter_number) fallback match survives id changes across restarts.
    replayed = await provider.request_decision(
        _gate_request(decision_id="fresh-id-after-restart", chapter_number=2)
    )
    assert replayed.choice == "continue_after_confirmation"

    # Unmatched requests still go to the UI (and time out to the default).
    fallback = await provider.request_decision(
        _gate_request(decision_id="other", chapter_number=9, kind="other_kind")
    )
    assert fallback.choice == "continue_after_confirmation"
    assert len(requested) == 1
