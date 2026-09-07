"""FFmpeg-based master rendering, subtitles and delivery packaging for 映界.

This is the missing "final mile" of the film pipeline: selected shots plus
inherited dialogue audio become one playable master with burned subtitles and
a delivery manifest.  All heavy lifting is delegated to the open-source
FFmpeg binary (system PATH or the bundled ``imageio-ffmpeg`` build), so the
project owns orchestration while codec, muxing and filter maintenance stay
upstream.

The renderer is dependency-injected: tests pass a fake runner that records
command lines, production uses the real async subprocess runner.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path

from novel_forge.persistence.models import ProjectLayout

from .media import ffmpeg_executable
from .schemas import (
    DeliveryManifest,
    DeliveryMediaType,
    FilmShot,
    FilmStudioState,
    MediaArtifact,
    MediaArtifactKind,
)

FfmpegRunner = Callable[[list[str]], Awaitable[tuple[int, str]]]
DurationProber = Callable[[Path], Awaitable[float]]


class FilmRenderError(RuntimeError):
    """Render failure safe to surface through the Engine API."""


async def _default_runner(args: list[str]) -> tuple[int, str]:
    ffmpeg = ffmpeg_executable()
    if ffmpeg is None:
        raise FilmRenderError("未找到可用的 FFmpeg，可安装 imageio-ffmpeg 或系统 FFmpeg")
    try:
        process = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await process.communicate()
    except OSError as exc:
        raise FilmRenderError(f"FFmpeg 启动失败: {exc}") from exc
    return int(process.returncode or 0), stderr.decode("utf-8", errors="replace")[-2000:]


def srt_timestamp(seconds: float) -> str:
    millis = max(0, int(round(seconds * 1000)))
    hours, remainder = divmod(millis, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, ms = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{ms:03d}"


def build_srt(entries: list[tuple[float, float, str]]) -> str:
    """Render SRT text from ordered ``(start_s, end_s, text)`` entries."""

    blocks: list[str] = []
    index = 0
    for start, end, text in entries:
        content = text.strip()
        if not content or end <= start:
            continue
        index += 1
        blocks.append(f"{index}\n{srt_timestamp(start)} --> {srt_timestamp(end)}\n{content}\n")
    return "\n".join(blocks)


class FilmRenderer:
    """Turns locked shots + dialogue audio into a master and delivery pack."""

    def __init__(
        self,
        layout: ProjectLayout,
        *,
        runner: FfmpegRunner | None = None,
        prober: DurationProber | None = None,
        width: int = 1920,
        height: int = 1080,
        frame_rate: float = 24.0,
    ) -> None:
        self.layout = layout
        self._run = runner or _default_runner
        self._probe_duration = prober
        self.width = width
        self.height = height
        self.frame_rate = frame_rate

    @property
    def render_dir(self) -> Path:
        return self.layout.root / "film" / "render"

    @property
    def delivery_dir(self) -> Path:
        return self.layout.root / "film" / "exports" / "delivery"

    async def render_master(
        self,
        state: FilmStudioState,
        *,
        burn_subtitles: bool = True,
    ) -> DeliveryManifest:
        """Normalize, concatenate, mix audio and subtitle the master cut."""

        clips = self._collect_video_clips(state)
        if not clips:
            raise FilmRenderError("没有可渲染的本地镜头素材，请先落盘并锁定已选镜头")
        self.render_dir.mkdir(parents=True, exist_ok=True)
        self.delivery_dir.mkdir(parents=True, exist_ok=True)

        segments = await self._normalize_segments(clips)
        picture_path = await self._concatenate(segments)

        subtitle_path = self.delivery_dir / "master.srt"
        subtitle_path.write_text(build_srt(self._subtitle_entries(clips)), encoding="utf-8")

        dialogue_inputs = self._dialogue_inputs(state)
        master_path = self.delivery_dir / "master.mp4"
        await self._compose_master(
            picture_path,
            dialogue_inputs,
            subtitle_path if burn_subtitles else None,
            master_path,
        )

        duration = await self._master_duration(master_path, clips)
        audio_path = await self._export_dialogue_mix(dialogue_inputs, duration)
        manifest = DeliveryManifest(
            project_id=state.project_id,
            title=state.project_title,
            master_video_path=str(master_path),
            master_audio_path=str(audio_path) if audio_path else "",
            subtitle_path=str(subtitle_path),
            otio_path=str(self.layout.root / "film" / "exports" / "timeline.otio"),
            duration_s=duration,
            width=self.width,
            height=self.height,
            frame_rate=self.frame_rate,
            shot_count=len(clips),
            clip_paths=[artifact.local_path for _, artifact in clips],
        )
        return manifest.register_artifact(
            DeliveryMediaType.FILM,
            str(master_path),
            item_count=len(clips),
            note="master cut",
        )

    # ------------------------------------------------------------------
    # Plan assembly
    # ------------------------------------------------------------------

    def _collect_video_clips(self, state: FilmStudioState) -> list[tuple[FilmShot, MediaArtifact]]:
        """Resolve timeline-ordered shots to materialized local artifacts."""

        artifacts = {
            item.subject_ref: item
            for item in state.media_artifacts
            if item.kind == MediaArtifactKind.VIDEO
        }
        ordered: list[tuple[FilmShot, MediaArtifact]] = []
        for shot in state.shots:
            if not shot.selected_asset_url:
                continue
            artifact = artifacts.get(shot.shot_id)
            if artifact is None:
                continue
            ordered.append((shot, artifact))
        return ordered

    def _subtitle_entries(
        self, clips: list[tuple[FilmShot, MediaArtifact]]
    ) -> list[tuple[float, float, str]]:
        entries: list[tuple[float, float, str]] = []
        cursor = 0.0
        for shot, _ in clips:
            if shot.dialogue.strip():
                entries.append((cursor, cursor + shot.duration_s, shot.dialogue))
            cursor += shot.duration_s
        return entries

    def _dialogue_inputs(self, state: FilmStudioState) -> list[tuple[float, Path]]:
        """Dialogue clips as ``(start_s, path)``; unplaced clips queue after
        the previous one so chapter-level TTS masters still land on cut."""

        inputs: list[tuple[float, Path]] = []
        cursor = 0.0
        for track in state.timeline.tracks:
            if track.kind not in {"dialogue", "audio"}:
                continue
            for clip in track.clips:
                if clip.media_kind not in {"dialogue", "audio"} or not clip.source_url:
                    continue
                start = clip.start_s if clip.start_s > 0 else cursor
                inputs.append((start, Path(clip.source_url)))
                cursor = max(cursor, start) + clip.duration_s
        return inputs

    # ------------------------------------------------------------------
    # FFmpeg command assembly
    # ------------------------------------------------------------------

    def _normalize_filter(self) -> str:
        return (
            f"scale={self.width}:{self.height}:force_original_aspect_ratio=decrease,"
            f"pad={self.width}:{self.height}:(ow-iw)/2:(oh-ih)/2,"
            f"fps={self.frame_rate:g},format=yuv420p"
        )

    async def _normalize_segments(self, clips: list[tuple[FilmShot, MediaArtifact]]) -> list[Path]:
        segments: list[Path] = []
        for index, (shot, artifact) in enumerate(clips):
            segment = self.render_dir / f"segment-{index:04d}.ts"
            args = [
                "-y",
                "-i",
                artifact.local_path,
                "-vf",
                self._normalize_filter(),
                "-an",
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-f",
                "mpegts",
                str(segment),
            ]
            await self._execute(args, f"镜头归一化失败: {shot.shot_id}")
            segments.append(segment)
        return segments

    async def _concatenate(self, segments: list[Path]) -> Path:
        list_file = self.render_dir / "concat.txt"
        list_file.write_text(
            "\n".join(f"file '{segment.as_posix()}'" for segment in segments),
            encoding="utf-8",
        )
        picture = self.render_dir / "picture.mp4"
        await self._execute(
            ["-y", "-f", "concat", "-safe", "0", "-i", str(list_file), "-c", "copy", str(picture)],
            "镜头拼接失败",
        )
        return picture

    async def _compose_master(
        self,
        picture: Path,
        dialogue_inputs: list[tuple[float, Path]],
        subtitle_path: Path | None,
        master_path: Path,
    ) -> None:
        args: list[str] = ["-y", "-i", str(picture)]
        for _, audio in dialogue_inputs:
            args.extend(["-i", str(audio)])
        filter_parts: list[str] = []
        if subtitle_path is not None:
            escaped = str(subtitle_path).replace("\\", "/").replace(":", r"\:")
            filter_parts.append(f"[0:v]subtitles='{escaped}'[v]")
        audio_label = ""
        if dialogue_inputs:
            labels: list[str] = []
            for index, (start, _) in enumerate(dialogue_inputs):
                delay_ms = int(round(start * 1000))
                label = f"a{index}"
                filter_parts.append(f"[{index + 1}:a]adelay={delay_ms}|{delay_ms}[{label}]")
                labels.append(f"[{label}]")
            filter_parts.append(
                f"{''.join(labels)}amix=inputs={len(dialogue_inputs)}:duration=first"
                ":normalize=0[mix]"
            )
            audio_label = "[mix]"
        if filter_parts:
            args.extend(["-filter_complex", ";".join(filter_parts)])
        args.extend(["-map", "[v]" if subtitle_path is not None else "0:v"])
        if audio_label:
            args.extend(["-map", audio_label, "-c:a", "aac", "-b:a", "192k"])
        args.extend(["-c:v", "libx264", "-preset", "veryfast", "-shortest", str(master_path)])
        await self._execute(args, "成片合成失败")

    async def _export_dialogue_mix(
        self, dialogue_inputs: list[tuple[float, Path]], duration: float
    ) -> Path | None:
        if not dialogue_inputs:
            return None
        mix_path = self.delivery_dir / "dialogue_mix.wav"
        args: list[str] = ["-y"]
        for _, audio in dialogue_inputs:
            args.extend(["-i", str(audio)])
        labels: list[str] = []
        filter_parts: list[str] = []
        for index, (start, _) in enumerate(dialogue_inputs):
            delay_ms = int(round(start * 1000))
            filter_parts.append(f"[{index}:a]adelay={delay_ms}|{delay_ms}[m{index}]")
            labels.append(f"[m{index}]")
        filter_parts.append(
            f"{''.join(labels)}amix=inputs={len(dialogue_inputs)}:duration=longest:normalize=0[mix]"
        )
        args.extend(["-filter_complex", ";".join(filter_parts), "-map", "[mix]"])
        if duration > 0:
            args.extend(["-t", f"{duration:.3f}"])
        args.append(str(mix_path))
        await self._execute(args, "对白混音导出失败")
        return mix_path

    async def _master_duration(
        self, master_path: Path, clips: list[tuple[FilmShot, MediaArtifact]]
    ) -> float:
        if self._probe_duration is not None:
            return float(await self._probe_duration(master_path))
        return float(sum(shot.duration_s for shot, _ in clips))

    async def _execute(self, args: list[str], message: str) -> None:
        ffmpeg = ffmpeg_executable()
        if ffmpeg is None:
            raise FilmRenderError("未找到可用的 FFmpeg，无法渲染成片")
        code, stderr = await self._run([ffmpeg, *args])
        if code != 0:
            raise FilmRenderError(f"{message}（ffmpeg exit={code}）: {stderr[-400:]}")
