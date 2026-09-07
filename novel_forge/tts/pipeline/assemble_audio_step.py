"""Assemble Audio pipeline step.

Combines synthesized audio segments into a complete chapter audio file,
with silence gaps, BGM mixing, SFX overlay, and accurate subtitle generation
powered by PlaybackTimeline.
"""

from __future__ import annotations

import asyncio
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from novel_forge.core.config import Settings
from novel_forge.core.local_model_resources import (
    LocalMemoryClass,
    LocalResourcePriority,
    LocalResourceRequest,
    configure_local_model_resources,
)
from novel_forge.obs.logger import get_logger
from novel_forge.persistence.models import ProjectLayout
from novel_forge.pipeline.steps.base import StepEventCallback, TracedStep
from novel_forge.tts.pipeline.multitrack_renderer import render_mix_plan
from novel_forge.tts.pipeline.pause_marker_injection import uses_native_pause_timing
from novel_forge.tts.pipeline.timeline_builder import PlaybackTimeline, build_timeline
from novel_forge.tts.pipeline.transition_policy import DEFAULT_SPEECH_TRANSITION_POLICY
from novel_forge.tts.platform.schemas import MixPlan, SpeechTimeline
from novel_forge.tts.runtime.audio_runtime import (
    configure_pydub,
    ffmpeg_executable,
    normalize_audio_file,
)
from novel_forge.tts.runtime.storage_limits import ensure_project_audio_write
from novel_forge.tts.schemas import (
    ChapterAudioResult,
    DubbingScript,
    MixRenderReport,
    SegmentType,
    SynthesisResult,
    SynthesisStatus,
)

_log = get_logger("tts.pipeline.assemble_audio")

# pydub availability flag — graceful fallback when not installed
_HAS_PYDUB = configure_pydub()
if _HAS_PYDUB:
    from pydub import AudioSegment  # type: ignore[import-untyped]


@dataclass
class AssembleAudioInput:
    """Input for audio assembly."""

    chapter_number: int
    script: DubbingScript
    segment_results: list[SynthesisResult]
    output_dir: Path | None = None
    # ── BGM/SFX 混音支持 ─────────────────────────────────────────────────────
    bgm_library_path: Path | None = None  # BGM 素材库目录
    sfx_library_path: Path | None = None  # SFX 素材库目录
    resolved_sound_paths: dict[tuple[str, int], Path] | None = None
    """Resolved project-library assets keyed by (soundscape|bgm|sfx, cue index)."""
    enable_bgm_mixing: bool = True  # 是否启用 BGM 混音
    enable_sfx_mixing: bool = True  # 是否启用 SFX 混音
    # ── 兼容旧字段 ───────────────────────────────────────────────────────────
    bgm_path: Path | None = None
    bgm_volume: float = 0.3
    # ── Subtitle generation ──────────────────────────────────────────────────
    generate_subtitles: bool = True
    mix_plan: MixPlan | None = None
    # Token-level ASR alignment timeline; when provided and word-level
    # subtitles are enabled, the SRT is generated from word timestamps.
    speech_timeline: SpeechTimeline | None = None


class AssembleAudioStep(TracedStep[AssembleAudioInput, ChapterAudioResult]):
    """Assemble synthesized segments into complete chapter audio.

    Features:
    - Pydub-based concatenation with proper silence gaps
    - BGM overlay with fade-in/fade-out and volume control
    - SFX cue mixing at specified trigger times
    - Accurate subtitle generation via PlaybackTimeline
    - Graceful fallback when pydub is not installed
    """

    # Default silence gap between segments (ms)
    DEFAULT_SEGMENT_GAP_MS = DEFAULT_SPEECH_TRANSITION_POLICY.default_gap_ms
    # Longer gap between paragraphs
    PARAGRAPH_GAP_MS = DEFAULT_SPEECH_TRANSITION_POLICY.paragraph_gap_ms
    # Scene transition gap (from script or default)
    DEFAULT_TRANSITION_GAP_MS = 1500
    # ── Context-aware dialogue gaps (P1) ──────────────────────────────────────
    # Rapid continuation when the same speaker keeps talking.
    SAME_SPEAKER_GAP_MS = DEFAULT_SPEECH_TRANSITION_POLICY.same_speaker_gap_ms
    # Natural turn-taking beat when switching between speakers.
    SPEAKER_SWITCH_GAP_MS = DEFAULT_SPEECH_TRANSITION_POLICY.speaker_switch_gap_ms
    # Transition from narration into dialogue.
    NARRATION_TO_DIALOGUE_GAP_MS = DEFAULT_SPEECH_TRANSITION_POLICY.narration_to_dialogue_gap_ms
    # Settling from dialogue back into narration.
    DIALOGUE_TO_NARRATION_GAP_MS = DEFAULT_SPEECH_TRANSITION_POLICY.dialogue_to_narration_gap_ms
    # ── P5: Rapid exchange detection ─────────────────────────────────────────
    # When both segments are short dialogue (< 20 chars) with different
    # speakers, use a tighter gap to simulate natural rapid back-and-forth.
    RAPID_EXCHANGE_GAP_MS = DEFAULT_SPEECH_TRANSITION_POLICY.rapid_exchange_gap_ms
    RAPID_EXCHANGE_MAX_CHARS = DEFAULT_SPEECH_TRANSITION_POLICY.rapid_exchange_max_chars

    def __init__(
        self,
        *,
        settings: Settings,
        layout: ProjectLayout | None = None,
        on_step: StepEventCallback | None = None,
    ) -> None:
        super().__init__(settings=settings, on_step=on_step)
        self._layout = layout

    @property
    def step_name(self) -> str:
        return "tts_assemble_audio"

    async def _execute(self, input_data: AssembleAudioInput) -> ChapterAudioResult:
        broker = configure_local_model_resources(self._settings)
        async with broker.lease(
            LocalResourceRequest(
                workload="audio_render",
                label=f"第 {input_data.chapter_number} 章本地混音与母带处理",
                memory_class=LocalMemoryClass.LIGHT,
                accelerator=False,
                cpu_heavy=True,
                priority=LocalResourcePriority.BACKGROUND,
                timeout_s=float(self._settings.local_model_resource_wait_timeout_s),
            )
        ):
            return await self._execute_with_lease(input_data)

    async def _execute_with_lease(
        self,
        input_data: AssembleAudioInput,
    ) -> ChapterAudioResult:
        """Assemble audio segments into complete chapter."""
        _log.info(
            "Assembling chapter %d: %d segments, bgm=%s, sfx=%s, pydub=%s",
            input_data.chapter_number,
            len(input_data.segment_results),
            input_data.enable_bgm_mixing,
            input_data.enable_sfx_mixing,
            _HAS_PYDUB,
        )

        output_dir = input_data.output_dir or self._get_output_dir(input_data.chapter_number)
        output_dir.mkdir(parents=True, exist_ok=True)
        script = input_data.script.model_copy(deep=True)
        # Build the playback timeline once and share it with every consumer in
        # this step (cue timing + subtitles) instead of recomputing it per use.
        playback_timeline = build_timeline(script, input_data.segment_results)
        self._resolve_cue_timing(
            script,
            input_data.segment_results,
            timeline=playback_timeline,
        )

        # Collect successful segments
        valid_segment_indices = {segment.segment_index for segment in script.segments}
        successful_results = sorted(
            [
                r
                for r in input_data.segment_results
                if (
                    r.status == SynthesisStatus.COMPLETED
                    and r.audio_path
                    and r.segment_index in valid_segment_indices
                )
            ],
            key=lambda result: result.segment_index,
        )

        if not successful_results:
            _log.warning("No successful segments for chapter %d", input_data.chapter_number)
            return ChapterAudioResult(
                chapter_number=input_data.chapter_number,
                script=script,
                segment_results=input_data.segment_results,
                is_complete=False,
            )

        # A complete MixPlan is rendered once from independent stems.  This
        # avoids generation-lossy MP3→MP3 layering and lets background beds be
        # side-chain ducked against the final voice bus.  Older projects keep
        # the established compatibility path below.
        assembled_path = output_dir / "chapter_full.mp3"
        if self._layout is not None:
            estimated_output_bytes = sum(
                Path(result.audio_path).stat().st_size
                for result in successful_results
                if Path(result.audio_path).is_file()
            )
            ensure_project_audio_write(
                layout=self._layout,
                settings=self._settings,
                destination=assembled_path,
                incoming_bytes=estimated_output_bytes,
                artifact=f"第 {input_data.chapter_number} 章合成音频",
            )
        smart_rendered = False
        render_report: MixRenderReport | None = None
        mix_plan = input_data.mix_plan
        if mix_plan is not None:
            render_report = await asyncio.to_thread(
                render_mix_plan,
                mix_plan,
                assembled_path,
            )
            smart_rendered = render_report.passed
        if mix_plan is not None:
            total_duration = max(
                (event.end_ms for event in mix_plan.events),
                default=0,
            )
        else:
            transition_gaps = self._build_transition_gap_map(script)
            total_duration = await self._concatenate_audio(
                successful_results,
                assembled_path,
                script,
                transition_gaps,
            )

        # BGM mixing (if enabled and suggestions exist)
        resolved_sound_paths = input_data.resolved_sound_paths or {}
        if mix_plan is None and input_data.enable_sfx_mixing and script.soundscapes:
            soundscape_path = await self._mix_soundscapes(
                assembled_path,
                script,
                resolved_sound_paths,
                output_dir,
            )
            if soundscape_path:
                assembled_path = soundscape_path

        if mix_plan is None and input_data.enable_bgm_mixing and script.bgm_suggestions:
            bgm_path = await self._mix_bgm(
                assembled_path,
                script,
                input_data.bgm_library_path,
                resolved_sound_paths,
                output_dir,
            )
            if bgm_path:
                assembled_path = bgm_path

        # SFX mixing (if enabled and cues exist)
        if mix_plan is None and input_data.enable_sfx_mixing and script.sfx_cues:
            sfx_path = await self._mix_sfx(
                assembled_path,
                script,
                input_data.sfx_library_path,
                resolved_sound_paths,
                output_dir,
            )
            if sfx_path:
                assembled_path = sfx_path

        # Final delivery mastering keeps dialogue, generated ambience, and
        # imported assets on a predictable loudness target across chapters.
        mastered = smart_rendered
        if mix_plan is None:
            master_path = output_dir / "chapter_master.mp3"
            mastered = await asyncio.to_thread(normalize_audio_file, assembled_path, master_path)
            if mastered:
                assembled_path = master_path

        # Generate subtitles using accurate timeline
        subtitle_path = ""
        if input_data.generate_subtitles:
            subtitle_path = str(output_dir / "chapter.srt")
            self._generate_subtitles(
                script,
                input_data.segment_results,
                subtitle_path,
                timeline=playback_timeline,
                speech_timeline=input_data.speech_timeline,
            )

        # Compute total cost
        total_cost = sum(r.cost_usd for r in input_data.segment_results)

        # Check completeness
        required_segment_indices = {
            segment.segment_index
            for segment in script.segments
            if segment.segment_type
            in (SegmentType.NARRATION, SegmentType.DIALOGUE, SegmentType.INNER_THOUGHT)
        }
        result_by_index = {result.segment_index: result for result in input_data.segment_results}
        speech_complete = bool(required_segment_indices) and all(
            result_by_index.get(index, SynthesisResult(segment_index=index)).status
            == SynthesisStatus.COMPLETED
            for index in required_segment_indices
        )
        render_complete = render_report.passed if render_report is not None else True
        is_complete = speech_complete and render_complete and assembled_path.is_file()

        result = ChapterAudioResult(
            chapter_number=input_data.chapter_number,
            script=script,
            segment_results=input_data.segment_results,
            assembled_audio_path=str(assembled_path) if assembled_path.is_file() else "",
            subtitle_path=subtitle_path,
            total_duration_ms=total_duration,
            total_cost_usd=total_cost,
            is_complete=is_complete,
            mix_render_report=render_report,
            metadata={
                "audio_pipeline": {
                    "decoder": "pydub+bundled-ffmpeg" if _HAS_PYDUB else "single-file-fallback",
                    "soundscape_requested": bool(script.soundscapes),
                    "bgm_requested": bool(script.bgm_suggestions),
                    "sfx_requested": bool(script.sfx_cues),
                    "mastered": mastered,
                    "smart_multitrack_rendered": smart_rendered,
                    "render_integrity_passed": (
                        render_report.passed if render_report is not None else None
                    ),
                    "rendered_event_count": (
                        render_report.rendered_event_count if render_report is not None else 0
                    ),
                    "failed_render_event_count": (
                        render_report.failed_event_count if render_report is not None else 0
                    ),
                    "loudness_target_lufs": -16 if mastered else None,
                    "true_peak_db": -1.5 if mastered else None,
                },
                "mix_plan": (
                    {
                        "renderer_plugin_id": input_data.mix_plan.renderer_plugin_id,
                        "asset_count": len(input_data.mix_plan.assets),
                        "event_count": len(input_data.mix_plan.events),
                    }
                    if input_data.mix_plan is not None
                    else {}
                ),
            },
        )

        _log.info(
            "Chapter %d assembled: %d ms, complete=%s, cost=$%.4f",
            input_data.chapter_number,
            total_duration,
            is_complete,
            total_cost,
        )

        return result

    # ─── Audio concatenation ────────────────────────────────────────────────

    async def _concatenate_audio(
        self,
        results: list[SynthesisResult],
        output_path: Path,
        script: DubbingScript,
        transition_gaps: dict[int, int] | None = None,
    ) -> int:
        """Concatenate audio segments with proper silence gaps.

        Uses pydub for correct audio concatenation when available;
        falls back to raw byte copy (single file) when pydub is absent.
        """
        transition_gaps = transition_gaps or {}

        if _HAS_PYDUB:
            return await self._concatenate_pydub(results, output_path, script, transition_gaps)
        return await self._concatenate_fallback(results, output_path, script, transition_gaps)

    async def _concatenate_pydub(
        self,
        results: list[SynthesisResult],
        output_path: Path,
        script: DubbingScript,
        transition_gaps: dict[int, int],
    ) -> int:
        """Single-pass FFmpeg assembly with context-aware silence insertion.

        Gap selection priority:
        1. Explicit scene-transition gap (from script annotations)
        2. Cross-paragraph gap (800ms)
        3. Context-aware dialogue gap (P1): same-speaker / speaker-switch /
           narration<->dialogue transitions
        4. Default intra-paragraph gap (300ms)

        P2-4: the whole chapter is decoded and encoded exactly once through an
        FFmpeg ``filter_complex`` concat graph (all inputs resampled to a
        common 48 kHz stereo layout), replacing the previous full-memory pydub
        assembly with its generation-lossy MP3->MP3 re-encode.
        """
        duration_ms = 0
        native_pause_timing = uses_native_pause_timing(script)
        inputs: list[str] = []
        filters: list[str] = []
        prev_paragraph = -1
        prev_segment: Any = None
        source_index = 0

        def _add_source(source_path: Path | None, *, gap_ms: int = 0) -> None:
            nonlocal source_index
            if source_path is None and gap_ms <= 0:
                return
            label = f"in{source_index}"
            if source_path is None:
                inputs.extend(
                    [
                        "-f",
                        "lavfi",
                        "-t",
                        f"{gap_ms / 1000.0:.3f}",
                        "-i",
                        "anullsrc=channel_layout=stereo:sample_rate=48000",
                    ]
                )
            else:
                inputs.extend(["-i", str(source_path)])
            filters.append(
                f"[{source_index}:a]aresample=48000,"
                f"aformat=sample_fmts=fltp:channel_layouts=stereo[{label}]"
            )
            source_index += 1

        for result in results:
            segment = script.segments[result.segment_index]
            current_paragraph = segment.source_paragraph

            if native_pause_timing:
                # The spoken MiniMax input already carries all structural
                # pauses.  Keep the legacy concat path acoustically equivalent
                # to the MixPlan renderer rather than producing double gaps.
                gap_ms = 0
            elif result.segment_index in transition_gaps:
                gap_ms = transition_gaps[result.segment_index]
            elif prev_paragraph >= 0 and current_paragraph != prev_paragraph:
                gap_ms = self.PARAGRAPH_GAP_MS
            elif prev_segment is not None:
                gap_ms = self._contextual_gap_ms(prev_segment, segment)
            else:
                gap_ms = 0

            if gap_ms > 0:
                _add_source(None, gap_ms=gap_ms)

            audio_path = Path(result.audio_path)
            if not audio_path.exists() or audio_path.stat().st_size <= 0:
                raise RuntimeError(
                    f"第 {result.segment_index + 1} 段音频文件缺失或为空：{audio_path}"
                )
            try:
                seg_audio = self._load_audio(audio_path)
            except Exception as exc:
                raise RuntimeError(
                    f"无法解码第 {result.segment_index + 1} 段音频：{audio_path.name}；"
                    "已停止装配，避免导出无声章节。"
                ) from exc
            result.duration_ms = len(seg_audio)
            duration_ms += len(seg_audio) + gap_ms
            _add_source(audio_path)

            prev_paragraph = current_paragraph
            prev_segment = segment

        if source_index == 0:
            raise RuntimeError("没有可装配的音频片段")

        concat_inputs = "".join(f"[in{i}]" for i in range(source_index))
        filters.append(f"{concat_inputs}concat=n={source_index}:v=0:a=1[out]")
        fmt = "mp3" if output_path.suffix == ".mp3" else "wav"
        command = [
            ffmpeg_executable(),
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            *inputs,
            "-filter_complex",
            ";".join(filters),
            "-map",
            "[out]",
        ]
        if fmt == "mp3":
            command.extend(["-c:a", "libmp3lame", "-b:a", "192k"])
        else:
            command.extend(["-c:a", "pcm_s16le"])
        command.append(str(output_path))
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await asyncio.wait_for(process.communicate(), timeout=600.0)
        if process.returncode != 0:
            detail = stderr.decode("utf-8", errors="replace")[-500:]
            raise RuntimeError(f"FFmpeg 单遍装配失败：{detail}")

        _log.info("Concat assembly: %d ms, %d segments", duration_ms, len(results))
        return duration_ms

    def _contextual_gap_ms(self, prev_segment: Any, curr_segment: Any) -> int:
        """Compute a context-aware silence gap between two adjacent segments.

        Spoken segments (dialogue / inner_thought) get tighter or wider gaps
        depending on whether the speaker continues or switches, and whether
        the transition crosses a narration↔dialogue boundary.  This makes
        rapid dialogue exchanges feel connected while narration transitions
        breathe naturally.

        P4 enhancement: when transitioning from narration into high-emotion
        dialogue (intensity > 0.6), an extra settling beat is added so the
        listener perceives a natural "breath" before the character speaks.
        """
        return DEFAULT_SPEECH_TRANSITION_POLICY.gap_ms(
            prev_segment,
            curr_segment,
            paragraph_changed=False,
        )

    @staticmethod
    def _decoder_for_audio_path(audio_path: Path) -> str:
        """Return a decoder hint that does not require a separate ffprobe binary."""
        return {
            ".mp3": "mp3",
            ".wav": "pcm_s16le",
            ".flac": "flac",
            ".aac": "aac",
            ".m4a": "aac",
            ".ogg": "vorbis",
        }.get(audio_path.suffix.lower(), "mp3")

    @classmethod
    def _load_audio(cls, audio_path: Path) -> Any:
        """Decode an audio file through bundled FFmpeg without calling ffprobe."""

        return AudioSegment.from_file(
            str(audio_path),
            codec=cls._decoder_for_audio_path(audio_path),
        )

    async def _concatenate_fallback(
        self,
        results: list[SynthesisResult],
        output_path: Path,
        script: DubbingScript,
        transition_gaps: dict[int, int],
    ) -> int:
        """Fallback: copy the first result or concatenate raw bytes."""
        if len(results) == 1:
            src = Path(results[0].audio_path)
            if src.exists():
                shutil.copy2(src, output_path)
                return results[0].duration_ms

        raise RuntimeError(
            "多片段音频装配需要 pydub 与 FFmpeg；当前运行时缺失，已停止以避免生成损坏文件"
        )

    # ─── Transition gap map ─────────────────────────────────────────────────

    def _build_transition_gap_map(self, script: DubbingScript) -> dict[int, int]:
        """Build a map of segment_index -> gap_ms for scene transitions."""
        gaps: dict[int, int] = {}
        for segment in script.segments:
            transition = segment.transition
            if transition and transition.gap_ms > 0:
                gaps[segment.segment_index] = transition.gap_ms
        return gaps

    def _resolve_cue_timing(
        self,
        script: DubbingScript,
        results: list[SynthesisResult],
        timeline: PlaybackTimeline | None = None,
    ) -> None:
        """Resolve stable segment anchors against the measured playback timeline.

        ``timeline`` is reused from the caller when provided so a chapter only
        pays for one timeline construction across cue timing and subtitles.
        """

        playback = timeline if timeline is not None else build_timeline(script, results)
        entries = {entry.segment_index: entry for entry in playback.entries}
        chapter_end = max((entry.end_ms for entry in playback.entries), default=0)
        # Store measured voiced spans on the working script so BGM and
        # ambience can be ducked only while narration/dialogue is audible.
        # ``script`` is a private copy made by _execute(), never the persisted
        # authoring script, so this timing enrichment cannot invalidate resume
        # fingerprints or source text checks.
        for segment in script.segments:
            entry = entries.get(segment.segment_index)
            if entry is not None:
                segment.start_ms = entry.start_ms
                segment.end_ms = entry.end_ms
        for bgm_cue in script.bgm_suggestions:
            if bgm_cue.start_segment_index in entries:
                bgm_cue.start_ms = entries[bgm_cue.start_segment_index].start_ms
            if bgm_cue.end_segment_index in entries:
                bgm_cue.end_ms = entries[bgm_cue.end_segment_index].end_ms
            elif bgm_cue.end_segment_index is None and bgm_cue.end_ms == 0:
                bgm_cue.end_ms = chapter_end
        for sfx_cue in script.sfx_cues:
            if sfx_cue.trigger_segment_index in entries:
                sfx_cue.trigger_ms = max(
                    0,
                    entries[sfx_cue.trigger_segment_index].start_ms + sfx_cue.offset_ms,
                )
        for soundscape_cue in script.soundscapes:
            if soundscape_cue.start_segment_index in entries:
                soundscape_cue.start_ms = entries[soundscape_cue.start_segment_index].start_ms
            if soundscape_cue.end_segment_index in entries:
                soundscape_cue.end_ms = entries[soundscape_cue.end_segment_index].end_ms
            elif soundscape_cue.end_segment_index is None and soundscape_cue.end_ms == 0:
                soundscape_cue.end_ms = chapter_end

    # ─── BGM mixing ─────────────────────────────────────────────────────────

    async def _mix_bgm(
        self,
        audio_path: Path,
        script: DubbingScript,
        bgm_library_path: Path | None,
        resolved_sound_paths: dict[tuple[str, int], Path],
        output_dir: Path,
    ) -> Path | None:
        """Mix BGM into the assembled audio using pydub overlay."""
        if not _HAS_PYDUB:
            _log.debug("pydub not available, skipping BGM mix")
            return None
        if not resolved_sound_paths and (not bgm_library_path or not bgm_library_path.exists()):
            _log.debug("BGM library path not set, skipping BGM mix")
            return None

        _log.info(
            "BGM mixing: %d suggestions, library=%s",
            len(script.bgm_suggestions),
            bgm_library_path,
        )

        try:
            base_audio = self._load_audio(audio_path)
        except Exception as exc:
            _log.warning("Failed to load base audio for BGM mix: %s", exc)
            return None

        mixed = False
        for cue_index, bgm in enumerate(script.bgm_suggestions):
            bgm_file = resolved_sound_paths.get(("bgm", cue_index))
            if bgm_file is None and bgm_library_path is not None:
                bgm_file = self._find_bgm_file(bgm, bgm_library_path)
            if not bgm_file:
                continue
            try:
                bgm_audio = self._load_audio(bgm_file)
                # Trim BGM to cover the requested range
                start_ms = bgm.start_ms or 0
                end_ms = bgm.end_ms or len(base_audio)
                start_ms = max(0, min(start_ms, len(base_audio)))
                end_ms = max(start_ms, min(end_ms, len(base_audio)))
                duration_needed = end_ms - start_ms
                if duration_needed <= 0:
                    continue
                if len(bgm_audio) > duration_needed:
                    bgm_audio = bgm_audio[:duration_needed]
                elif bgm.loop and len(bgm_audio) < duration_needed and len(bgm_audio) > 0:
                    repeats = (duration_needed // len(bgm_audio)) + 1
                    bgm_audio = bgm_audio * repeats
                    bgm_audio = bgm_audio[:duration_needed]

                # Establish the normal BGM bed first.  Dialogue-specific
                # ducking is applied below, rather than reducing the entire
                # track whenever a cue happens to contain dialogue.
                vol_db = -int((1.0 - bgm.volume) * 20)
                bgm_audio = bgm_audio + vol_db

                # Fade in/out
                bgm_audio = bgm_audio.fade_in(min(bgm.fade_in_ms, len(bgm_audio) // 3)).fade_out(
                    min(bgm.fade_out_ms, len(bgm_audio) // 3)
                )
                bgm_audio = self._duck_under_spoken_segments(
                    bgm_audio,
                    script,
                    layer_start_ms=start_ms,
                    ducking_db=bgm.ducking_db,
                )

                base_audio = base_audio.overlay(bgm_audio, position=start_ms)
                mixed = True
            except Exception as exc:
                _log.warning("BGM mix failed for %s: %s", bgm_file, exc)

        if not mixed:
            return None

        output = output_dir / "chapter_with_bgm.mp3"
        base_audio.export(str(output), format="mp3", bitrate="192k")
        return output

    # ─── SFX mixing ─────────────────────────────────────────────────────────

    async def _mix_sfx(
        self,
        audio_path: Path,
        script: DubbingScript,
        sfx_library_path: Path | None,
        resolved_sound_paths: dict[tuple[str, int], Path],
        output_dir: Path,
    ) -> Path | None:
        """Mix SFX cues into the assembled audio using pydub overlay."""
        if not _HAS_PYDUB:
            _log.debug("pydub not available, skipping SFX mix")
            return None
        if not resolved_sound_paths and (not sfx_library_path or not sfx_library_path.exists()):
            _log.debug("SFX library path not set, skipping SFX mix")
            return None

        _log.info(
            "SFX mixing: %d cues, library=%s",
            len(script.sfx_cues),
            sfx_library_path,
        )

        try:
            base_audio = self._load_audio(audio_path)
        except Exception as exc:
            _log.warning("Failed to load base audio for SFX mix: %s", exc)
            return None

        mixed = False
        for cue_index, sfx in enumerate(script.sfx_cues):
            sfx_file = resolved_sound_paths.get(("sfx", cue_index))
            if sfx_file is None and sfx_library_path is not None:
                sfx_file = self._find_sfx_file(sfx, sfx_library_path)
            if not sfx_file:
                continue
            try:
                sfx_audio = self._load_audio(sfx_file)
                if sfx.duration_ms > 0 and len(sfx_audio) > sfx.duration_ms:
                    sfx_audio = sfx_audio[: sfx.duration_ms]
                vol_db = -int((1.0 - sfx.volume) * 20)
                sfx_audio = sfx_audio + vol_db
                position = max(0, min(sfx.trigger_ms, len(base_audio)))
                base_audio = base_audio.overlay(sfx_audio, position=position)
                mixed = True
            except Exception as exc:
                _log.warning("SFX mix failed for %s: %s", sfx_file, exc)

        if not mixed:
            return None

        output = output_dir / "chapter_with_sfx.mp3"
        base_audio.export(str(output), format="mp3", bitrate="192k")
        return output

    async def _mix_soundscapes(
        self,
        audio_path: Path,
        script: DubbingScript,
        resolved_sound_paths: dict[tuple[str, int], Path],
        output_dir: Path,
    ) -> Path | None:
        """Layer resolved, loopable environmental ambience beneath narration."""
        if not _HAS_PYDUB:
            _log.debug("pydub not available, skipping soundscape mix")
            return None
        try:
            base_audio = self._load_audio(audio_path)
        except Exception as exc:
            _log.warning("Failed to load base audio for soundscape mix: %s", exc)
            return None

        mixed = False
        for cue_index, cue in enumerate(script.soundscapes):
            asset_path = resolved_sound_paths.get(("soundscape", cue_index))
            if asset_path is None:
                continue
            try:
                ambience = self._load_audio(asset_path)
                start_ms = max(0, min(cue.start_ms, len(base_audio)))
                end_ms = cue.end_ms or len(base_audio)
                end_ms = max(start_ms, min(end_ms, len(base_audio)))
                duration_needed = end_ms - start_ms
                if duration_needed <= 0 or len(ambience) <= 0:
                    continue
                if len(ambience) > duration_needed:
                    ambience = ambience[:duration_needed]
                elif cue.loop:
                    ambience = self._fit_loop_with_crossfade(ambience, duration_needed)
                elif len(ambience) < duration_needed:
                    duration_needed = len(ambience)
                volume_db = -int((1.0 - cue.volume) * 20)
                ambience = ambience + volume_db
                ambience = ambience.fade_in(min(cue.fade_in_ms, len(ambience) // 3))
                ambience = ambience.fade_out(min(cue.fade_out_ms, len(ambience) // 3))
                ambience = self._duck_under_spoken_segments(
                    ambience,
                    script,
                    layer_start_ms=start_ms,
                    ducking_db=cue.ducking_db,
                )
                base_audio = base_audio.overlay(ambience, position=start_ms)
                mixed = True
            except Exception as exc:
                _log.warning("Soundscape mix failed for %s: %s", asset_path, exc)

        if not mixed:
            return None
        output = output_dir / "chapter_with_soundscape.mp3"
        base_audio.export(str(output), format="mp3", bitrate="192k")
        return output

    @staticmethod
    def _fit_loop_with_crossfade(audio: Any, duration_ms: int, *, crossfade_ms: int = 80) -> Any:
        """Extend a generated ambience bed without audible hard-loop clicks."""
        if duration_ms <= 0 or len(audio) <= 0:
            return audio[:0]
        if len(audio) >= duration_ms:
            return audio[:duration_ms]
        result = audio
        while len(result) < duration_ms:
            overlap = min(crossfade_ms, len(audio) // 4, len(result) // 4)
            if overlap > 0:
                result = result.append(audio, crossfade=overlap)
            else:
                result += audio
        return result[:duration_ms]

    def _duck_under_spoken_segments(
        self,
        layer: Any,
        script: DubbingScript,
        *,
        layer_start_ms: int,
        ducking_db: float,
    ) -> Any:
        """Apply smooth, timeline-aware ducking to one background layer.

        This is deliberately deterministic and independent of a live audio
        sidechain plugin: the existing measured TTS timeline is the sidechain
        signal.  Adjacent spoken spans are merged before attenuation so short
        narration pauses do not cause audible volume pumping.
        """
        if ducking_db <= 0 or len(layer) <= 0:
            return layer
        spans: list[tuple[int, int]] = []
        layer_end_ms = layer_start_ms + len(layer)
        spoken_types = {
            SegmentType.NARRATION,
            SegmentType.DIALOGUE,
            SegmentType.INNER_THOUGHT,
        }
        for segment in script.segments:
            if segment.segment_type not in spoken_types or segment.end_ms <= segment.start_ms:
                continue
            start_ms = max(layer_start_ms, segment.start_ms)
            end_ms = min(layer_end_ms, segment.end_ms)
            if end_ms > start_ms:
                spans.append((start_ms - layer_start_ms, end_ms - layer_start_ms))
        if not spans:
            return layer

        merged: list[tuple[int, int]] = []
        for start_ms, end_ms in sorted(spans):
            if merged and start_ms <= merged[-1][1] + 80:
                merged[-1] = (merged[-1][0], max(merged[-1][1], end_ms))
            else:
                merged.append((start_ms, end_ms))

        ducked = AudioSegment.empty()
        cursor_ms = 0
        for start_ms, end_ms in merged:
            ducked += layer[cursor_ms:start_ms]
            spoken_slice = layer[start_ms:end_ms]
            ramp_ms = min(80, len(spoken_slice) // 2)
            if ramp_ms <= 0:
                ducked += spoken_slice - ducking_db
            else:
                fade_in = spoken_slice[:ramp_ms].fade(
                    from_gain=0.0,
                    to_gain=-ducking_db,
                    start=0,
                    duration=ramp_ms,
                )
                middle = spoken_slice[ramp_ms:-ramp_ms] - ducking_db
                fade_out = spoken_slice[-ramp_ms:].fade(
                    from_gain=-ducking_db,
                    to_gain=0.0,
                    start=0,
                    duration=ramp_ms,
                )
                ducked += fade_in + middle + fade_out
            cursor_ms = end_ms
        return ducked + layer[cursor_ms:]

    # ─── File matching helpers ───────────────────────────────────────────────

    def _find_bgm_file(self, bgm: Any, bgm_library_path: Path) -> Path | None:
        """Find a BGM file matching the suggestion by mood or track_name."""
        if not bgm_library_path.exists():
            return None
        search_terms = [bgm.mood, bgm.track_name]
        for term in search_terms:
            if not term:
                continue
            for file in bgm_library_path.glob("*.mp3"):
                if term.lower() in file.stem.lower():
                    return file
        return None

    def _find_sfx_file(self, sfx: Any, sfx_library_path: Path) -> Path | None:
        """Find an SFX file matching the cue by effect_name or description."""
        if not sfx_library_path.exists():
            return None
        search_terms = [sfx.effect_name, sfx.description]
        for term in search_terms:
            if not term:
                continue
            for ext in ("*.mp3", "*.wav"):
                for file in sfx_library_path.glob(ext):
                    if term.lower() in file.stem.lower():
                        return file
        return None

    # ─── Silence generation ──────────────────────────────────────────────────

    def _generate_silence_bytes(self, duration_ms: int) -> bytes:
        """Generate silence bytes using pydub when available."""
        if _HAS_PYDUB:
            import io

            buf = io.BytesIO()
            AudioSegment.silent(duration=duration_ms).export(buf, format="mp3")
            return buf.getvalue()
        return b""

    # ─── Subtitle generation (timeline-based) ────────────────────────────────

    def _generate_subtitles(
        self,
        script: DubbingScript,
        results: list[SynthesisResult],
        output_path: str,
        timeline: PlaybackTimeline | None = None,
        speech_timeline: SpeechTimeline | None = None,
    ) -> None:
        """Generate SRT subtitle file using accurate PlaybackTimeline.

        When word-level subtitles are enabled and a token-level alignment
        timeline is provided, the SRT carries per-word timestamps instead of
        whole-segment blocks (P3-1).
        """
        if speech_timeline is not None and bool(
            getattr(self._settings, "tts_subtitle_word_level", False)
        ):
            self._generate_word_level_subtitles(speech_timeline, output_path)
            return
        playback = timeline if timeline is not None else build_timeline(script, results)

        subtitles: list[str] = []
        subtitle_index = 1

        for entry in playback.entries:
            start_time = self._format_srt_time(entry.start_ms)
            end_time = self._format_srt_time(entry.end_ms)

            if entry.segment_type == SegmentType.DIALOGUE:
                text = f"\u3010{entry.character_name}\u3011{entry.text}"
            elif entry.segment_type == SegmentType.INNER_THOUGHT:
                text = f"\uff08{entry.character_name}\u00b7\u5185\u5fc3\uff09{entry.text}"
            else:
                text = entry.text

            subtitles.append(f"{subtitle_index}")
            subtitles.append(f"{start_time} --> {end_time}")
            subtitles.append(text)
            subtitles.append("")
            subtitle_index += 1

        Path(output_path).write_text("\n".join(subtitles), encoding="utf-8")

    def _generate_word_level_subtitles(
        self,
        speech_timeline: SpeechTimeline,
        output_path: str,
    ) -> None:
        """Generate token-level SRT from the ASR-aligned speech timeline."""
        alignments = {item.segment_index: item for item in speech_timeline.alignments}
        subtitles: list[str] = []
        subtitle_index = 1
        for segment_index in sorted(alignments):
            tokens = alignments[segment_index].tokens
            if not tokens:
                continue
            for token in tokens:
                text = str(token.text or "").strip()
                if not text:
                    continue
                subtitles.append(f"{subtitle_index}")
                subtitles.append(
                    f"{self._format_srt_time(token.start_ms)} --> "
                    f"{self._format_srt_time(token.end_ms)}"
                )
                subtitles.append(text)
                subtitles.append("")
                subtitle_index += 1
        Path(output_path).write_text("\n".join(subtitles), encoding="utf-8")

    def _format_srt_time(self, ms: int) -> str:
        """Format milliseconds as SRT timestamp: HH:MM:SS,mmm"""
        hours = ms // 3_600_000
        ms %= 3_600_000
        minutes = ms // 60_000
        ms %= 60_000
        seconds = ms // 1_000
        milliseconds = ms % 1_000
        return f"{hours:02d}:{minutes:02d}:{seconds:02d},{milliseconds:03d}"

    def _get_output_dir(self, chapter_number: int) -> Path:
        """Get output directory for chapter audio."""
        if self._layout:
            return self._layout.tts_audio_dir(chapter_number)
        raise RuntimeError("正式音频写入必须提供 ProjectLayout 或显式 output_dir")
