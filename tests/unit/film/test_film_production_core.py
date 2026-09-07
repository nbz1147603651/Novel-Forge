"""Unit tests for the 映界 production core: media vault, renderer, QC and jobs."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import pytest

from novel_forge.film import qc as qc_module
from novel_forge.film import rendering as rendering_module
from novel_forge.film.jobs import FilmJobManager
from novel_forge.film.media import FilmMediaVault
from novel_forge.film.qc import FilmQcEngine
from novel_forge.film.rendering import FilmRenderer, FilmRenderError, build_srt
from novel_forge.film.schemas import (
    DeliveryManifest,
    FilmJobKind,
    FilmJobState,
    FilmShot,
    FilmStudioState,
    FilmTimeline,
    MediaArtifact,
    MediaArtifactKind,
    ProductionBible,
    QcCheckStatus,
    Screenplay,
)
from novel_forge.persistence.models import ProjectLayout


def _layout(tmp_path: Path) -> ProjectLayout:
    layout = ProjectLayout(tmp_path / "project")
    layout.ensure_dirs()
    return layout


def _state(**overrides: Any) -> FilmStudioState:
    base: dict[str, Any] = {
        "project_id": "demo",
        "project_title": "演示项目",
        "production_bible": ProductionBible(project_id="demo", title="演示项目"),
        "screenplay": Screenplay(title="演示项目"),
        "timeline": FilmTimeline(name="master"),
        "shots": [
            FilmShot(
                shot_id="sh-01",
                scene_id="sc-01",
                shot_number=1,
                duration_s=5.0,
                dialogue="你好，世界",
                selected_asset_url="mock://shot-1.mp4",
            )
        ],
    }
    base.update(overrides)
    return FilmStudioState(**base)


def _artifact(local_path: str, *, duration_s: float = 5.0) -> MediaArtifact:
    return MediaArtifact(
        artifact_id="art-1",
        kind=MediaArtifactKind.VIDEO,
        subject_ref="sh-01",
        local_path=local_path,
        duration_s=duration_s,
        width=1280,
        height=720,
    )


# ---------------------------------------------------------------------------
# Job ledger
# ---------------------------------------------------------------------------


def test_job_acquire_is_idempotent_for_active_jobs() -> None:
    manager = FilmJobManager()
    state = _state()

    state, first, created = manager.acquire(
        state, kind=FilmJobKind.GENERATE_SHOT, target_id="sh-01", provider_id="bailian"
    )
    assert created
    state, second, created_again = manager.acquire(
        state, kind=FilmJobKind.GENERATE_SHOT, target_id="sh-01", provider_id="bailian"
    )
    assert not created_again
    assert second.job_id == first.job_id
    assert len(state.jobs) == 1


def test_job_lifecycle_and_cancel() -> None:
    manager = FilmJobManager()
    state = _state()
    state, job, _ = manager.acquire(state, kind=FilmJobKind.RENDER_MASTER, target_id="master")

    state = manager.mark_running(state, job)
    running = state.jobs[0]
    assert running.state == FilmJobState.RUNNING
    assert running.attempts == 1

    state, cancelled = manager.cancel(state, running.job_id)
    assert cancelled
    assert state.jobs[0].state == FilmJobState.CANCELLED
    state, cancelled_twice = manager.cancel(state, running.job_id)
    assert not cancelled_twice


def test_job_recovery_requeues_within_budget_and_fails_when_exhausted() -> None:
    manager = FilmJobManager()
    state = _state()
    state, cheap, _ = manager.acquire(state, kind=FilmJobKind.GENERATE_SHOT, target_id="sh-01")
    state, spent, _ = manager.acquire(
        state, kind=FilmJobKind.GENERATE_SHOT, target_id="sh-02", max_attempts=1
    )
    state = manager.mark_running(state, cheap)
    state = manager.mark_running(state, spent)

    recovered, resumable = manager.recover(state)

    assert [job.job_id for job in resumable] == [cheap.job_id]
    by_id = {job.job_id: job for job in recovered.jobs}
    assert by_id[cheap.job_id].state == FilmJobState.QUEUED
    assert by_id[spent.job_id].state == FilmJobState.FAILED
    assert "重试预算" in by_id[spent.job_id].error_message


# ---------------------------------------------------------------------------
# Media vault
# ---------------------------------------------------------------------------


async def test_materialize_downloads_checksums_and_probes(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    payload = b"fake-video-bytes"

    async def fake_download(url: str, dest: Path) -> None:
        dest.write_bytes(payload)

    async def fake_probe(_path: Path) -> dict[str, Any]:
        return {
            "format": {"duration": "4.5"},
            "streams": [
                {
                    "codec_type": "video",
                    "codec_name": "h264",
                    "width": 1920,
                    "height": 1080,
                    "avg_frame_rate": "24000/1001",
                },
                {"codec_type": "audio"},
            ],
        }

    vault = FilmMediaVault(layout, downloader=fake_download, prober=fake_probe)
    artifact = await vault.materialize(
        "https://cdn.example.com/shot.mp4?token=x",
        kind=MediaArtifactKind.VIDEO,
        subject_ref="sh-01",
        provider_id="bailian",
    )

    assert artifact.checksum == hashlib.sha256(payload).hexdigest()
    assert artifact.size_bytes == len(payload)
    assert artifact.duration_s == pytest.approx(4.5)
    assert (artifact.width, artifact.height) == (1920, 1080)
    assert artifact.fps == pytest.approx(23.976, abs=0.01)
    assert artifact.has_audio is True
    assert artifact.source_url.startswith("https://")
    assert Path(artifact.local_path).is_file()
    assert artifact.local_path.endswith(".mp4")


async def test_materialize_rejects_empty_url(tmp_path: Path) -> None:
    vault = FilmMediaVault(_layout(tmp_path))
    with pytest.raises(ValueError):
        await vault.materialize("", kind=MediaArtifactKind.VIDEO, subject_ref="sh-01")


# ---------------------------------------------------------------------------
# Renderer
# ---------------------------------------------------------------------------


def test_build_srt_formats_entries() -> None:
    text = build_srt([(0.0, 2.5, "第一句"), (2.5, 2.0, "无效"), (2.5, 5.0, "第二句")])
    assert "00:00:00,000 --> 00:00:02,500" in text
    assert "第一句" in text and "第二句" in text
    assert text.count("\n\n") == 1  # two blocks, invalid entry dropped
    assert "无效" not in text


async def test_render_master_pipeline_assembles_segments_mix_and_subtitles(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(rendering_module, "ffmpeg_executable", lambda: "/usr/bin/ffmpeg")
    layout = _layout(tmp_path)
    clip = layout.root / "film" / "media" / "sh-01.mp4"
    clip.parent.mkdir(parents=True, exist_ok=True)
    clip.write_bytes(b"clip")
    state = _state(media_artifacts=[_artifact(str(clip))])

    commands: list[list[str]] = []

    async def fake_runner(args: list[str]) -> tuple[int, str]:
        commands.append(args)
        return 0, ""

    async def fake_prober(_path: Path) -> float:
        return 5.0

    renderer = FilmRenderer(layout, runner=fake_runner, prober=fake_prober)
    manifest = await renderer.render_master(state)

    assert isinstance(manifest, DeliveryManifest)
    assert manifest.shot_count == 1
    assert manifest.duration_s == pytest.approx(5.0)
    assert Path(manifest.master_video_path).name == "master.mp4"
    subtitle = Path(manifest.subtitle_path)
    assert subtitle.is_file()
    assert "你好，世界" in subtitle.read_text(encoding="utf-8")
    # normalize → concat → compose master (subtitles burn) → no dialogue mix
    assert len(commands) == 3
    assert "mpegts" in commands[0]
    assert "concat" in commands[1]
    assert any("subtitles=" in part for part in commands[2])


async def test_render_master_without_local_material_raises(tmp_path: Path) -> None:
    renderer = FilmRenderer(_layout(tmp_path))
    with pytest.raises(FilmRenderError):
        await renderer.render_master(_state())


# ---------------------------------------------------------------------------
# QC engine
# ---------------------------------------------------------------------------


async def test_qc_shot_fails_when_not_materialized(tmp_path: Path) -> None:
    engine = FilmQcEngine(_layout(tmp_path))
    report = await engine.check_shot(_state(), "sh-01")
    assert report.passed is False
    assert report.checks[0].name == "materialized"


async def test_qc_shot_passes_for_valid_artifact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(qc_module, "ffmpeg_executable", lambda: "/usr/bin/ffmpeg")
    layout = _layout(tmp_path)
    clip = layout.root / "film" / "media" / "sh-01.mp4"
    clip.parent.mkdir(parents=True, exist_ok=True)
    clip.write_bytes(b"clip")
    state = _state(media_artifacts=[_artifact(str(clip))])

    signal_output = "\n".join(
        f"[Parsed_metadata_1 @ 0x1] lavfi.signalstats.YAVG={value}"
        for value in ("120.5", "8.2", "96.0", "101.3")
    )

    async def fake_signal_runner(_args: list[str]) -> tuple[int, str]:
        return 0, signal_output

    engine = FilmQcEngine(layout, signal_runner=fake_signal_runner)
    report = await engine.check_shot(state, "sh-01")

    assert report.passed is True
    names = [check.name for check in report.checks]
    assert "duration_drift" in names and "black_frames" in names
    black = next(check for check in report.checks if check.name == "black_frames")
    assert black.status == QcCheckStatus.WARN
    assert black.metric == pytest.approx(0.25)


async def test_qc_shot_fails_when_mostly_black(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(qc_module, "ffmpeg_executable", lambda: "/usr/bin/ffmpeg")
    layout = _layout(tmp_path)
    clip = layout.root / "film" / "media" / "sh-01.mp4"
    clip.parent.mkdir(parents=True, exist_ok=True)
    clip.write_bytes(b"clip")
    state = _state(media_artifacts=[_artifact(str(clip))])

    signal_output = "\n".join(
        f"[Parsed_metadata_1 @ 0x1] lavfi.signalstats.YAVG={value}"
        for value in ("2.0", "3.1", "120.0", "1.4")
    )

    async def fake_signal_runner(_args: list[str]) -> tuple[int, str]:
        return 0, signal_output

    report = await FilmQcEngine(layout, signal_runner=fake_signal_runner).check_shot(state, "sh-01")
    assert report.passed is False
    black = next(check for check in report.checks if check.name == "black_frames")
    assert black.status == QcCheckStatus.FAIL


async def test_qc_master_checks_delivery_files(tmp_path: Path) -> None:
    layout = _layout(tmp_path)
    master = layout.root / "film" / "exports" / "delivery" / "master.mp4"
    master.parent.mkdir(parents=True, exist_ok=True)
    master.write_bytes(b"master")
    manifest = DeliveryManifest(
        project_id="demo",
        master_video_path=str(master),
        subtitle_path=str(layout.root / "missing.srt"),
        otio_path=str(layout.root / "missing.otio"),
        duration_s=10.0,
    )
    report = await FilmQcEngine(layout).check_master(manifest)
    assert report.passed is True
    subtitles = next(check for check in report.checks if check.name == "subtitles")
    assert subtitles.status == QcCheckStatus.WARN
