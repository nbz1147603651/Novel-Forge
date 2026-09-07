"""One-graph stem renderer with commercial two-pass loudness mastering."""

from __future__ import annotations

import hashlib
import json
import logging
import math
import re
import subprocess
from pathlib import Path

from novel_forge.tts.platform.schemas import MixEvent, MixPlan
from novel_forge.tts.runtime.audio_runtime import ffmpeg_executable
from novel_forge.tts.schemas import MixRenderEventResult, MixRenderReport

_log = logging.getLogger(__name__)


def _linear_gain(db: float) -> float:
    return math.pow(10.0, db / 20.0)


def _mix_filter(labels: list[str], output: str) -> str:
    if len(labels) == 1:
        return f"{labels[0]}anull[{output}]"
    joined = "".join(labels)
    return f"{joined}amix=inputs={len(labels)}:normalize=0:dropout_transition=0[{output}]"


def _event_filter(index: int, event: MixEvent, *, looped: bool) -> str:
    duration_s = max(0.001, (event.end_ms - event.start_ms) / 1000.0)
    filters = [
        f"atrim=duration={duration_s:.3f}",
        "asetpts=PTS-STARTPTS",
        "aresample=48000",
        "aformat=sample_fmts=fltp:channel_layouts=stereo",
        f"volume={_linear_gain(event.gain_db):.8f}",
    ]
    if event.fade_in_ms > 0:
        filters.append(f"afade=t=in:st=0:d={min(event.fade_in_ms / 1000.0, duration_s):.3f}")
    if event.fade_out_ms > 0:
        fade_s = min(event.fade_out_ms / 1000.0, duration_s)
        filters.append(f"afade=t=out:st={max(0.0, duration_s - fade_s):.3f}:d={fade_s:.3f}")
    filters.append(f"adelay={event.start_ms}:all=1")
    # ``looped`` is reflected in the input args; keeping it in the signature
    # makes the asset policy explicit at the filter boundary.
    _ = looped
    return f"[{index}:a]{','.join(filters)}[event{index}]"


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _event_result(
    event: MixEvent,
    *,
    path: Path | None,
    status: str,
    reason: str = "",
) -> MixRenderEventResult:
    source_hash = ""
    if path is not None and path.is_file() and path.stat().st_size > 0:
        source_hash = _file_sha256(path)
    rendered = status == "rendered"
    return MixRenderEventResult(
        event_id=event.event_id,
        asset_id=event.asset_id,
        bus=event.bus,
        status="rendered" if rendered else "failed",
        planned_start_ms=event.start_ms,
        planned_end_ms=event.end_ms,
        actual_start_ms=event.start_ms if rendered else None,
        actual_end_ms=event.end_ms if rendered else None,
        gain_db=event.gain_db,
        source_path=str(path) if path is not None else "",
        source_hash=source_hash,
        reason=reason,
    )


def _render_report(
    mix_plan: MixPlan,
    output_path: Path,
    event_results: list[MixRenderEventResult],
    *,
    passed: bool,
    error: str = "",
    stem_paths: dict[str, str] | None = None,
    measured_loudness: dict[str, str] | None = None,
) -> MixRenderReport:
    rendered_count = sum(item.status == "rendered" for item in event_results)
    failed_count = len(event_results) - rendered_count
    output_hash = _file_sha256(output_path) if passed else ""
    return MixRenderReport(
        chapter_number=mix_plan.chapter_number,
        renderer_plugin_id=mix_plan.renderer_plugin_id,
        status="completed" if passed else "failed",
        passed=passed,
        mastering_succeeded=passed,
        planned_event_count=len(event_results),
        rendered_event_count=rendered_count,
        failed_event_count=failed_count,
        duration_ms=max((item.planned_end_ms for item in event_results), default=0),
        output_path=str(output_path) if passed else "",
        output_hash=output_hash,
        events=event_results,
        error=error[:2000],
        stem_paths=stem_paths or {},
        measured_loudness=measured_loudness or {},
    )


def _command_error(stderr: bytes, fallback: str) -> str:
    detail = stderr.decode("utf-8", errors="replace").strip()
    return detail[-1600:] if detail else fallback


def render_mix_plan(mix_plan: MixPlan, output_path: Path) -> MixRenderReport:
    """Render every planned voice, bed, ambience and SFX event in one graph."""

    assets = {asset.asset_id: asset for asset in mix_plan.assets}
    renderable: list[tuple[MixEvent, Path]] = []
    missing: dict[str, tuple[Path | None, str]] = {}
    for event in mix_plan.events:
        asset = assets.get(event.asset_id)
        path = Path(asset.path).expanduser() if asset is not None else None
        if asset is None:
            missing[event.event_id] = (None, "mix_plan_asset_missing")
            continue
        assert path is not None
        if asset is not None and path.is_file() and path.stat().st_size > 0:
            renderable.append((event, path))
        else:
            missing[event.event_id] = (path, "source_audio_missing_or_empty")
    if missing:
        output_path.unlink(missing_ok=True)
        renderable_paths = {event.event_id: path for event, path in renderable}
        results = [
            _event_result(
                event,
                path=missing.get(event.event_id, (renderable_paths.get(event.event_id), ""))[0],
                status="failed",
                reason=(
                    missing[event.event_id][1]
                    if event.event_id in missing
                    else "render_aborted_due_to_incomplete_event_graph"
                ),
            )
            for event in mix_plan.events
        ]
        return _render_report(
            mix_plan,
            output_path,
            results,
            passed=False,
            error="MixPlan contains missing or empty source audio.",
        )
    voice_events = [item for item in renderable if item[0].bus == "voice"]
    if not voice_events:
        output_path.unlink(missing_ok=True)
        results = [
            _event_result(
                event,
                path=path,
                status="failed",
                reason="voice_bus_missing",
            )
            for event, path in renderable
        ]
        return _render_report(
            mix_plan,
            output_path,
            results,
            passed=False,
            error="MixPlan has no renderable voice event.",
        )

    command = [ffmpeg_executable(), "-hide_banner", "-loglevel", "error", "-y"]
    graph: list[str] = []
    bus_labels: dict[str, list[str]] = {"voice": [], "bed": [], "sfx": []}
    bed_ducking = 0.0
    sfx_ducking = 0.0
    for index, (event, path) in enumerate(renderable):
        looped = event.bus in {"bgm", "soundscape"}
        if looped:
            command.extend(["-stream_loop", "-1"])
        command.extend(["-i", str(path)])
        graph.append(_event_filter(index, event, looped=looped))
        label = f"[event{index}]"
        if event.bus == "voice":
            bus_labels["voice"].append(label)
        elif event.bus in {"bgm", "soundscape"}:
            bus_labels["bed"].append(label)
            bed_ducking = max(bed_ducking, event.duck_under_voice_db)
        else:
            bus_labels["sfx"].append(label)
            sfx_ducking = max(sfx_ducking, event.duck_under_voice_db)

    graph.append(_mix_filter(bus_labels["voice"], "voice"))
    final_labels: list[str] = []
    key_count = int(bool(bus_labels["bed"])) + int(bool(bus_labels["sfx"] and sfx_ducking))
    voice_key_labels: list[str] = []
    voice_output = "[voice]"
    if key_count:
        split_labels = ["[voiceout]", *(f"[voicekey{index}]" for index in range(key_count))]
        graph.append(f"[voice]asplit={key_count + 1}{''.join(split_labels)}")
        voice_output = "[voiceout]"
        voice_key_labels = split_labels[1:]
    if bus_labels["bed"]:
        graph.append(_mix_filter(bus_labels["bed"], "bed"))
        ratio = min(20.0, max(2.0, 1.0 + bed_ducking / 1.5))
        voice_key = voice_key_labels.pop(0)
        graph.append(
            f"[bed]{voice_key}sidechaincompress=threshold=0.025:ratio={ratio:.2f}:"
            "attack=80:release=320[bedduck]"
        )
        final_labels.extend([voice_output, "[bedduck]"])
    else:
        final_labels.append(voice_output)
    if bus_labels["sfx"]:
        graph.append(_mix_filter(bus_labels["sfx"], "sfx"))
        if sfx_ducking:
            voice_key = voice_key_labels.pop(0)
            ratio = min(12.0, max(2.0, 1.0 + sfx_ducking / 1.5))
            graph.append(
                f"[sfx]{voice_key}sidechaincompress=threshold=0.025:ratio={ratio:.2f}:"
                "attack=20:release=180[sfxduck]"
            )
            final_labels.append("[sfxduck]")
        else:
            final_labels.append("[sfx]")
    two_pass = mix_plan.mastering_mode == "two_pass_loudnorm"

    total_duration_s = max(event.end_ms for event, _path in renderable) / 1000.0
    output_path.parent.mkdir(parents=True, exist_ok=True)
    render_target = (
        output_path.with_name(f".{output_path.stem}.premaster.wav") if two_pass else output_path
    )
    # Stem export maps pre-loudnorm bus copies from the same event-graph render
    # that creates the premaster.  In two-pass mode only the compact premaster
    # is analysed/mastered afterwards, so stems do not re-run the graph.
    stem_outputs: list[tuple[str, Path]] = []
    export_stems = bool(mix_plan.export_stems)
    stem_labels: dict[str, str] = {}
    if export_stems:
        # Split each bus's final label: one copy becomes the stem output, the
        # other feeds premaster. final_labels is rewritten to reference the
        # premaster-bound copies.
        if voice_output:
            graph.append(f"{voice_output}asplit=2[voice_stem][voice_to_mix]")
            stem_labels["voice"] = "[voice_stem]"
            final_labels = [s.replace(voice_output, "[voice_to_mix]") for s in final_labels]
        if bus_labels["bed"]:
            graph.append("[bedduck]asplit=2[bed_stem][bed_to_mix]")
            stem_labels["bed"] = "[bed_stem]"
            final_labels = [s.replace("[bedduck]", "[bed_to_mix]") for s in final_labels]
        if bus_labels["sfx"]:
            sfx_final = "[sfxduck]" if sfx_ducking else "[sfx]"
            graph.append(f"{sfx_final}asplit=2[sfx_stem][sfx_to_mix]")
            stem_labels["sfx"] = "[sfx_stem]"
            final_labels = [s.replace(sfx_final, "[sfx_to_mix]") for s in final_labels]
    # Premaster mix + (single_pass) loudnorm appended after optional stem splits
    # so the rewritten final_labels are consumed correctly.
    graph.append(_mix_filter(final_labels, "premaster"))
    if not two_pass:
        graph.append(
            f"[premaster]loudnorm=I={mix_plan.target_lufs:.1f}:LRA=11:"
            f"TP={mix_plan.true_peak_db:.1f}[master]"
        )
    command.extend(
        [
            "-filter_complex",
            ";".join(graph),
            "-map",
            "[premaster]" if two_pass else "[master]",
            "-t",
            f"{total_duration_s:.3f}",
            "-ar",
            "48000",
        ]
    )
    if two_pass:
        command.extend(["-c:a", "pcm_s24le", str(render_target)])
    else:
        command.extend(["-c:a", "libmp3lame", "-b:a", "192k", str(render_target)])
    # Append stem outputs as additional FFmpeg outputs (raw bus mix, wav).
    if export_stems:
        for stem_name, stem_label in stem_labels.items():
            stem_path = output_path.with_name(f"{output_path.stem}.{stem_name}_stem.wav")
            command.extend(
                [
                    "-map",
                    stem_label,
                    "-t",
                    f"{total_duration_s:.3f}",
                    "-ar",
                    "48000",
                    "-c:a",
                    "pcm_s24le" if two_pass else "pcm_s16le",
                    str(stem_path),
                ]
            )
            stem_outputs.append((stem_name, stem_path))
    try:
        completed = subprocess.run(command, capture_output=True, check=False, timeout=900)
    except (OSError, subprocess.SubprocessError) as exc:
        render_target.unlink(missing_ok=True)
        output_path.unlink(missing_ok=True)
        results = [
            _event_result(event, path=path, status="failed", reason="renderer_process_failed")
            for event, path in renderable
        ]
        return _render_report(
            mix_plan,
            output_path,
            results,
            passed=False,
            error=str(exc),
        )
    if completed.returncode != 0 or not render_target.is_file():
        render_target.unlink(missing_ok=True)
        output_path.unlink(missing_ok=True)
        results = [
            _event_result(event, path=path, status="failed", reason="renderer_graph_failed")
            for event, path in renderable
        ]
        return _render_report(
            mix_plan,
            output_path,
            results,
            passed=False,
            error=_command_error(completed.stderr, "FFmpeg mix graph failed."),
        )
    if not two_pass:
        passed = output_path.stat().st_size > 0
        # Collect stem paths only when all requested stems actually landed on disk.
        resolved_stem_paths: dict[str, str] = {}
        if passed and export_stems:
            for stem_name, stem_path in stem_outputs:
                if stem_path.is_file() and stem_path.stat().st_size > 0:
                    resolved_stem_paths[stem_name] = str(stem_path)
                else:
                    # A missing stem downgrades pass to a warning but does not
                    # fail the whole render - the master is still valid.
                    _log.warning(
                        "Stem %s for chapter %d was not produced",
                        stem_name,
                        mix_plan.chapter_number,
                    )
        results = [
            _event_result(
                event,
                path=path,
                status="rendered" if passed else "failed",
                reason="" if passed else "master_output_empty",
            )
            for event, path in renderable
        ]
        return _render_report(
            mix_plan,
            output_path,
            results,
            passed=passed,
            error="" if passed else "FFmpeg produced an empty master.",
            stem_paths=resolved_stem_paths,
        )
    try:
        mastered, error, measured = _master_two_pass(render_target, output_path, mix_plan)
        resolved_stem_paths = {
            stem_name: str(stem_path)
            for stem_name, stem_path in stem_outputs
            if stem_path.is_file() and stem_path.stat().st_size > 0
        }
        if not mastered:
            output_path.unlink(missing_ok=True)
        results = [
            _event_result(
                event,
                path=path,
                status="rendered" if mastered else "failed",
                reason="" if mastered else "two_pass_mastering_failed",
            )
            for event, path in renderable
        ]
        return _render_report(
            mix_plan,
            output_path,
            results,
            passed=mastered,
            error=error,
            stem_paths=resolved_stem_paths,
            measured_loudness=measured,
        )
    finally:
        render_target.unlink(missing_ok=True)


def _master_two_pass(
    premaster: Path, output: Path, plan: MixPlan
) -> tuple[bool, str, dict[str, str]]:
    base_filter = f"loudnorm=I={plan.target_lufs:.1f}:LRA=11:TP={plan.true_peak_db:.1f}"
    analysis_command = [
        ffmpeg_executable(),
        "-hide_banner",
        "-nostats",
        "-i",
        str(premaster),
        "-af",
        base_filter + ":print_format=json",
        "-f",
        "null",
        "-",
    ]
    measured: dict[str, str] = {}
    try:
        analysis = subprocess.run(
            analysis_command,
            capture_output=True,
            check=False,
            timeout=300,
        )
        stderr = analysis.stderr.decode("utf-8", errors="replace")
        blocks = re.findall(r"\{[^{}]*\"input_i\"[^{}]*\}", stderr, flags=re.DOTALL)
        if blocks:
            payload = json.loads(blocks[-1])
            measured = {str(key): str(value) for key, value in payload.items()}
    except (OSError, ValueError, subprocess.SubprocessError):
        measured = {}
    mastering_filter = base_filter
    required = {"input_i", "input_lra", "input_tp", "input_thresh", "target_offset"}
    if required.issubset(measured):
        mastering_filter += (
            f":measured_I={measured['input_i']}:measured_LRA={measured['input_lra']}"
            f":measured_TP={measured['input_tp']}:measured_thresh={measured['input_thresh']}"
            f":offset={measured['target_offset']}:linear=true"
        )
    command = [
        ffmpeg_executable(),
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(premaster),
        "-af",
        mastering_filter,
        "-ar",
        "48000",
        "-c:a",
        "libmp3lame",
        "-b:a",
        "192k",
        str(output),
    ]
    try:
        completed = subprocess.run(command, capture_output=True, check=False, timeout=600)
    except (OSError, subprocess.SubprocessError) as exc:
        return False, str(exc), measured
    passed = completed.returncode == 0 and output.is_file() and output.stat().st_size > 0
    return (
        passed,
        "" if passed else _command_error(completed.stderr, "FFmpeg two-pass mastering failed."),
        measured,
    )
