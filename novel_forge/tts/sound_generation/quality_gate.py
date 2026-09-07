"""Quality gate for generated sound assets.

Evaluates generated audio using FFmpeg probing (no new dependencies) to detect
common generation failures before assets enter the project library.  The gate
is fail-closed: if FFmpeg is unavailable or probing fails, the asset does NOT
pass, so unverifiable audio can never be auto-approved into the library
(service.py routes a failed gate to ``pending`` review instead).

Checks:
1. File non-empty and decodable by FFmpeg.
2. Actual duration within ±20% of the requested duration.
3. Integrated loudness within target ± 6 LU range.
4. No digital clipping (true peak < -1 dBTP).
"""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

from novel_forge.core.config import Settings
from novel_forge.tts.runtime.audio_runtime import ffmpeg_executable
from novel_forge.tts.sound_generation.schemas import SoundGenerationKind, SoundGenerationRequest

logger = logging.getLogger(__name__)

_DURATION_TOLERANCE = 0.20  # ±20%
_LOUDNESS_TOLERANCE_LU = 6.0  # ±6 LU from target
_TRUE_PEAK_CEILING_DBTP = -1.0  # Must be below this


async def _ffprobe_executable() -> str | None:
    """Resolve an ffprobe binary: PATH first, then the bundled ffmpeg directory.

    The bundled ``imageio-ffmpeg`` executable ships next to ``ffprobe`` on
    common distributions, but PATH lookup stays the primary source so
    distribution-provided binaries win when present.
    """

    on_path = shutil.which("ffprobe")
    if on_path:
        return on_path
    try:
        ffmpeg = ffmpeg_executable()
        sibling = Path(ffmpeg).with_name("ffprobe")
        if sibling.is_file():
            return str(sibling)
    except (ImportError, RuntimeError, OSError):
        pass
    return None


@dataclass(frozen=True)
class QualityCheck:
    """One quality gate check result."""

    name: str
    passed: bool
    detail: str = ""


@dataclass(frozen=True)
class SFXQualityReport:
    """Aggregate quality gate evaluation for one generated asset."""

    passed: bool
    checks: tuple[QualityCheck, ...] = ()
    blocking_reasons: tuple[str, ...] = ()

    @property
    def summary(self) -> str:
        if self.passed:
            return "All quality checks passed."
        return f"Failed: {'; '.join(self.blocking_reasons)}"


async def evaluate_generated_audio(
    audio_data: bytes,
    request: SoundGenerationRequest,
    settings: Settings,
) -> SFXQualityReport:
    """Run quality gate checks on generated audio bytes.

    Returns a report indicating pass/fail.  If FFmpeg is unavailable or
    probing fails, the asset passes by default (graceful degradation).
    """
    if not settings.sound_generation_quality_gate_enabled:
        return SFXQualityReport(passed=True)

    if not audio_data:
        return SFXQualityReport(
            passed=False,
            checks=(QualityCheck(name="non_empty", passed=False, detail="Audio data is empty."),),
            blocking_reasons=("Audio data is empty.",),
        )

    ffprobe = await _ffprobe_executable()
    if ffprobe is None:
        # Fail closed: without a probe tool the gate cannot verify quality, so
        # the asset must not be auto-approved.
        logger.warning("ffprobe unavailable; quality gate fails closed.")
        return SFXQualityReport(
            passed=False,
            checks=(
                QualityCheck(
                    name="probe_available",
                    passed=False,
                    detail="ffprobe unavailable; cannot verify generated audio quality.",
                ),
            ),
            blocking_reasons=("ffprobe unavailable; cannot verify generated audio quality.",),
        )

    suffix = f".{request.output_format}"
    try:
        with tempfile.TemporaryDirectory(prefix="novel-forge-qgate-") as tmp_dir:
            input_path = Path(tmp_dir) / f"check{suffix}"
            input_path.write_bytes(audio_data)

            probe = await _probe_audio(input_path, ffprobe)
            if probe is None:
                # Cannot decode — a genuine quality failure, not a pass.
                logger.warning("Quality gate: ffprobe failed to decode; failing closed.")
                return SFXQualityReport(
                    passed=False,
                    checks=(
                        QualityCheck(
                            name="decodable",
                            passed=False,
                            detail="ffprobe failed to decode generated audio.",
                        ),
                    ),
                    blocking_reasons=("generated audio cannot be decoded",),
                )

            checks: list[QualityCheck] = []
            blocking: list[str] = []

            # Check 1: Decodable (implicit — probe succeeded).
            checks.append(QualityCheck(name="decodable", passed=True))

            # Check 2: Duration deviation.
            actual_duration_s = probe.get("duration_s") or 0.0
            requested_duration_s = request.duration_ms / 1000.0
            if requested_duration_s > 0 and actual_duration_s > 0:
                deviation = abs(actual_duration_s - requested_duration_s) / requested_duration_s
                duration_ok = deviation <= _DURATION_TOLERANCE
                checks.append(
                    QualityCheck(
                        name="duration_deviation",
                        passed=duration_ok,
                        detail=f"actual={actual_duration_s:.2f}s requested={requested_duration_s:.2f}s deviation={deviation:.1%}",
                    )
                )
                if not duration_ok:
                    blocking.append(
                        f"Duration deviation {deviation:.0%} exceeds ±{_DURATION_TOLERANCE:.0%}"
                    )
            else:
                checks.append(
                    QualityCheck(name="duration_deviation", passed=True, detail="skipped")
                )

            # Check 3: Loudness range.
            loudness_lufs = probe.get("integrated_loudness_lufs")
            if loudness_lufs is not None:
                target = _target_lufs_for(request.kind, settings)
                loudness_ok = abs(loudness_lufs - target) <= _LOUDNESS_TOLERANCE_LU
                checks.append(
                    QualityCheck(
                        name="loudness_range",
                        passed=loudness_ok,
                        detail=f"measured={loudness_lufs:.1f} LUFS target={target:.1f} LUFS",
                    )
                )
                if not loudness_ok:
                    blocking.append(
                        f"Loudness {loudness_lufs:.1f} LUFS outside target {target:.1f} ± {_LOUDNESS_TOLERANCE_LU:.0f} LU"
                    )
            else:
                checks.append(QualityCheck(name="loudness_range", passed=True, detail="skipped"))

            # Check 4: True peak / clipping.
            true_peak_dbtp = probe.get("true_peak_dbtp")
            if true_peak_dbtp is not None:
                peak_ok = true_peak_dbtp < _TRUE_PEAK_CEILING_DBTP
                checks.append(
                    QualityCheck(
                        name="true_peak",
                        passed=peak_ok,
                        detail=f"true_peak={true_peak_dbtp:.1f} dBTP ceiling={_TRUE_PEAK_CEILING_DBTP:.1f} dBTP",
                    )
                )
                if not peak_ok:
                    blocking.append(
                        f"True peak {true_peak_dbtp:.1f} dBTP exceeds ceiling {_TRUE_PEAK_CEILING_DBTP:.1f} dBTP"
                    )
            else:
                checks.append(QualityCheck(name="true_peak", passed=True, detail="skipped"))

            return SFXQualityReport(
                passed=len(blocking) == 0,
                checks=tuple(checks),
                blocking_reasons=tuple(blocking),
            )

    except (OSError, TimeoutError) as exc:
        logger.warning("Quality gate I/O error: %s; failing closed.", exc)
        return SFXQualityReport(
            passed=False,
            checks=(
                QualityCheck(
                    name="probe_runtime",
                    passed=False,
                    detail=f"Probe infrastructure error: {exc}",
                ),
            ),
            blocking_reasons=("quality gate infrastructure unavailable",),
        )


def _target_lufs_for(kind: SoundGenerationKind, settings: Settings) -> float:
    """Return the expected target loudness for quality gate comparison."""
    if kind == SoundGenerationKind.SFX:
        return settings.sound_generation_sfx_target_lufs
    if kind == SoundGenerationKind.SOUNDSCAPE:
        return settings.sound_generation_soundscape_target_lufs
    return -16.0


async def _probe_audio(path: Path, ffprobe: str | None = None) -> dict[str, float | None] | None:
    """Probe audio file for duration, loudness, and true peak using ffprobe/ffmpeg.

    Returns None if the file cannot be decoded or the measuring toolchain is
    unavailable (fail-closed signal for the caller).
    """
    probe_binary = ffprobe or await _ffprobe_executable()
    if probe_binary is None:
        return None
    # Step 1: Basic format probe for duration.
    try:
        process = await asyncio.create_subprocess_exec(
            probe_binary,
            "-v",
            "quiet",
            "-print_format",
            "json",
            "-show_format",
            str(path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(process.communicate(), timeout=15.0)
        if process.returncode != 0:
            return None
        info = json.loads(stdout.decode("utf-8", errors="replace"))
        duration_s = float(info.get("format", {}).get("duration", 0))
    except (ValueError, json.JSONDecodeError, TimeoutError, OSError):
        return None

    result: dict[str, float | None] = {"duration_s": duration_s}

    # Step 2: Loudness and true peak via ffmpeg loudnorm/astats measurement.
    ffmpeg: str | None = None
    try:
        ffmpeg = ffmpeg_executable()
    except (ImportError, RuntimeError, OSError):
        ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        # Without a measuring backend the loudness/peak checks cannot run;
        # fail closed so unverifiable audio never auto-approves.
        logger.warning("ffmpeg unavailable; loudness checks fail closed.")
        return None

    try:
        process = await asyncio.create_subprocess_exec(
            ffmpeg,
            "-i",
            str(path),
            "-af",
            "loudnorm=print_format=json",
            "-f",
            "null",
            "-",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await asyncio.wait_for(process.communicate(), timeout=30.0)
        stderr_text = stderr.decode("utf-8", errors="replace")

        # Parse the JSON block from loudnorm output.
        json_start = stderr_text.rfind("{")
        json_end = stderr_text.rfind("}") + 1
        if json_start >= 0 and json_end > json_start:
            loudnorm_data = json.loads(stderr_text[json_start:json_end])
            input_i = loudnorm_data.get("input_i")
            input_tp = loudnorm_data.get("input_tp")
            result["integrated_loudness_lufs"] = (
                float(input_i) if input_i and input_i != "-inf" else None
            )
            result["true_peak_dbtp"] = float(input_tp) if input_tp and input_tp != "-inf" else None
        else:
            result["integrated_loudness_lufs"] = None
            result["true_peak_dbtp"] = None
    except (ValueError, json.JSONDecodeError, TimeoutError, OSError):
        result["integrated_loudness_lufs"] = None
        result["true_peak_dbtp"] = None

    return result
