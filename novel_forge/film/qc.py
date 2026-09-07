"""Executable quality gates for 映界 media.

QC is not a human checkbox here: every shot and master is checked against
probeable facts — file presence, decoded duration, resolution, duration drift
against the planned shot length and black-frame ratio.  Provider success only
ever means "bytes arrived"; these checks decide whether the bytes are usable.

FFmpeg/ffprobe do the measurement; this module owns thresholds and verdicts.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Awaitable, Callable
from pathlib import Path

from novel_forge.persistence.models import ProjectLayout

from .media import FilmMediaVault, ffmpeg_executable
from .schemas import (
    DeliveryManifest,
    FilmStudioState,
    MediaArtifactKind,
    QcCheck,
    QcCheckStatus,
    QcReport,
)

SignalRunner = Callable[[list[str]], Awaitable[tuple[int, str]]]

_DURATION_DRIFT_TOLERANCE = 0.30
_BLACK_LUMA_THRESHOLD = 16.0  # YAVG is reported in the 0-255 luma domain
_BLACK_FRAME_FAIL_RATIO = 0.5
_YAVG_PATTERN = re.compile(r"lavfi\.signalstats\.YAVG=([0-9.]+)")


async def _default_signal_runner(args: list[str]) -> tuple[int, str]:
    ffmpeg = ffmpeg_executable()
    if ffmpeg is None:
        return -1, ""
    try:
        process = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        stdout, _ = await asyncio.wait_for(process.communicate(), timeout=180)
    except (OSError, asyncio.TimeoutError):
        return -1, ""
    return int(process.returncode or 0), stdout.decode("utf-8", errors="replace")


def _check(
    name: str, status: QcCheckStatus, detail: str = "", metric: float | None = None
) -> QcCheck:
    return QcCheck(name=name, status=status, detail=detail, metric=metric)


class FilmQcEngine:
    """Automated shot/master quality gates backed by ffprobe and ffmpeg."""

    def __init__(
        self,
        layout: ProjectLayout,
        *,
        vault: FilmMediaVault | None = None,
        signal_runner: SignalRunner | None = None,
    ) -> None:
        self.layout = layout
        self.vault = vault or FilmMediaVault(layout)
        self._signal_runner = signal_runner or _default_signal_runner

    async def check_shot(self, state: FilmStudioState, shot_id: str) -> QcReport:
        shot = next((item for item in state.shots if item.shot_id == shot_id), None)
        if shot is None:
            return QcReport(
                target_id=shot_id,
                target_type="shot",
                checks=[_check("shot_exists", QcCheckStatus.FAIL, "镜头不存在")],
            )
        artifact = next(
            (
                item
                for item in state.media_artifacts
                if item.subject_ref == shot_id and item.kind == MediaArtifactKind.VIDEO
            ),
            None,
        )
        checks: list[QcCheck] = []
        if artifact is None:
            checks.append(_check("materialized", QcCheckStatus.FAIL, "镜头尚未落盘为本地素材"))
            return QcReport(target_id=shot_id, target_type="shot", checks=checks)
        checks.append(_check("materialized", QcCheckStatus.PASS, artifact.local_path))

        path = Path(artifact.local_path)
        if not path.is_file() or path.stat().st_size == 0:
            checks.append(_check("file_readable", QcCheckStatus.FAIL, "本地素材文件缺失或为空"))
            return QcReport(target_id=shot_id, target_type="shot", checks=checks)
        checks.append(
            _check("file_readable", QcCheckStatus.PASS, metric=float(path.stat().st_size))
        )

        if artifact.duration_s <= 0:
            checks.append(_check("duration", QcCheckStatus.FAIL, "无法解析视频时长"))
        else:
            drift = abs(artifact.duration_s - shot.duration_s) / max(shot.duration_s, 0.1)
            status = (
                QcCheckStatus.PASS if drift <= _DURATION_DRIFT_TOLERANCE else QcCheckStatus.FAIL
            )
            checks.append(
                _check(
                    "duration_drift",
                    status,
                    f"实际 {artifact.duration_s:.1f}s / 计划 {shot.duration_s:.1f}s",
                    metric=round(drift, 3),
                )
            )

        if artifact.width <= 0 or artifact.height <= 0:
            checks.append(_check("resolution", QcCheckStatus.WARN, "未能探测到分辨率"))
        else:
            checks.append(
                _check(
                    "resolution",
                    QcCheckStatus.PASS,
                    f"{artifact.width}x{artifact.height}",
                    metric=float(artifact.width * artifact.height),
                )
            )

        black_ratio = await self._black_frame_ratio(path)
        if black_ratio is not None:
            status = (
                QcCheckStatus.FAIL
                if black_ratio >= _BLACK_FRAME_FAIL_RATIO
                else QcCheckStatus.WARN
                if black_ratio >= 0.2
                else QcCheckStatus.PASS
            )
            checks.append(
                _check(
                    "black_frames",
                    status,
                    "黑帧占比异常" if status != QcCheckStatus.PASS else "黑帧正常",
                    metric=round(black_ratio, 3),
                )
            )

        passed = all(item.status != QcCheckStatus.FAIL for item in checks)
        return QcReport(target_id=shot_id, target_type="shot", passed=passed, checks=checks)

    async def check_master(self, manifest: DeliveryManifest) -> QcReport:
        checks: list[QcCheck] = []
        master = Path(manifest.master_video_path)
        if manifest.master_video_path and master.is_file() and master.stat().st_size > 0:
            checks.append(_check("master_file", QcCheckStatus.PASS, str(master)))
        else:
            checks.append(_check("master_file", QcCheckStatus.FAIL, "成片文件缺失"))
        if manifest.duration_s > 0:
            checks.append(_check("master_duration", QcCheckStatus.PASS, metric=manifest.duration_s))
        else:
            checks.append(_check("master_duration", QcCheckStatus.FAIL, "成片时长为 0"))
        subtitle = Path(manifest.subtitle_path)
        checks.append(
            _check(
                "subtitles",
                QcCheckStatus.PASS if subtitle.is_file() else QcCheckStatus.WARN,
                str(subtitle) if subtitle.is_file() else "字幕文件缺失",
            )
        )
        otio = Path(manifest.otio_path)
        checks.append(
            _check(
                "otio_exchange",
                QcCheckStatus.PASS if otio.is_file() else QcCheckStatus.WARN,
                str(otio) if otio.is_file() else "OTIO 交换文件缺失",
            )
        )
        passed = all(item.status != QcCheckStatus.FAIL for item in checks)
        return QcReport(target_id="master", target_type="master", passed=passed, checks=checks)

    async def _black_frame_ratio(self, path: Path) -> float | None:
        """Sample average luma per frame via ffmpeg signalstats; None when
        ffmpeg is unavailable so QC degrades instead of failing."""

        ffmpeg = ffmpeg_executable()
        if ffmpeg is None:
            return None
        args = [
            ffmpeg,
            "-hide_banner",
            "-i",
            str(path),
            "-vf",
            "signalstats,metadata=mode=print:key=lavfi.signalstats.YAVG",
            "-f",
            "null",
            "-",
        ]
        code, output = await self._signal_runner(args)
        if code != 0 and not output:
            return None
        total = 0
        black = 0
        for line in output.splitlines():
            match = _YAVG_PATTERN.search(line)
            if match is None:
                continue
            value = float(match.group(1))
            total += 1
            if value <= _BLACK_LUMA_THRESHOLD:
                black += 1
        if total == 0:
            return None
        return black / total
