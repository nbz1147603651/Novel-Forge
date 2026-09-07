"""Build a real speech timeline from synthesized audio and alignment plugins."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
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
from novel_forge.pipeline.steps.base import StepEventCallback, TracedStep
from novel_forge.tts.pipeline.timeline_builder import TimelineEntry, build_timeline
from novel_forge.tts.platform.registry import AudioPluginRegistry
from novel_forge.tts.platform.routing import route_segment
from novel_forge.tts.platform.schemas import (
    AlignmentResult,
    AlignmentToken,
    AudioExecutionPlan,
    AudioExecutionStage,
    SpeechTimeline,
    SpeechTimelineEntry,
    TimelineGranularity,
)
from novel_forge.tts.platform.sidecar import AudioSidecarClient
from novel_forge.tts.schemas import (
    DubbingScript,
    DubbingSegment,
    SegmentType,
    SynthesisResult,
    SynthesisStatus,
)

_log = get_logger("tts.pipeline.align_speech_timeline")


# ── ASR/alignment result disk cache (P3-3) ────────────────────────────────────
# Repair rounds re-synthesize only failing segments, but every previous pass
# paid full ASR + alignment for the whole chapter.  Persisting per-engine
# results keyed on the audio file lets a re-run skip engines it already
# answered, without keeping the whole chapter's memory footprint.


def _asr_cache_dir(settings: Settings) -> Path:
    return Path(settings.storage_root) / "tts" / "asr_cache"


# Bounded cache size: keep at most this many entries per provider/language
# directory; the oldest files (by mtime) are evicted first.  Prevents the
# cache from growing without bound across long-running projects.
_ASR_CACHE_MAX_ENTRIES = 512


def _audio_file_sha256(audio_path: Path) -> str:
    try:
        digest = hashlib.sha256()
        with audio_path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1 << 20), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError:
        return ""


def _asr_cache_path(
    settings: Settings,
    plugin_id: str,
    audio_path: Path,
    *,
    operation: str,
    language: str,
    text: str,
) -> Path:
    key_payload = json.dumps(
        {
            "op": operation,
            "plugin": plugin_id,
            "audio": _audio_file_sha256(audio_path),
            "lang": (language or "auto").strip().lower(),
            "text": text,
        },
        sort_keys=True,
        ensure_ascii=False,
    )
    key = hashlib.sha256(key_payload.encode("utf-8")).hexdigest()[:24]
    return _asr_cache_dir(settings) / f"{plugin_id}_{key}.json"


def _asr_cache_load(cache_path: Path, *, ttl_hours: int) -> dict[str, Any] | None:
    try:
        if not cache_path.is_file():
            return None
        payload = json.loads(cache_path.read_text(encoding="utf-8"))
        saved_at = datetime.fromisoformat(str(payload.get("saved_at") or ""))
        age_hours = (datetime.now(timezone.utc) - saved_at).total_seconds() / 3600.0
        if age_hours > ttl_hours:
            return None
        result = payload.get("result")
        return result if isinstance(result, dict) else None
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return None


def _asr_cache_save(cache_path: Path, result: dict[str, Any]) -> None:
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "saved_at": datetime.now(timezone.utc).isoformat(),
            "result": result,
        }
        cache_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        _enforce_asr_cache_capacity(cache_path.parent)
    except OSError as exc:
        _log.warning("Unable to persist ASR cache entry: %s", exc)


def _enforce_asr_cache_capacity(cache_dir: Path) -> None:
    """Evict oldest cache files once the directory exceeds the size cap."""
    try:
        entries = [
            item for item in cache_dir.iterdir() if item.is_file() and item.suffix == ".json"
        ]
        if len(entries) <= _ASR_CACHE_MAX_ENTRIES:
            return
        entries.sort(key=lambda item: item.stat().st_mtime)
        for stale in entries[: len(entries) - _ASR_CACHE_MAX_ENTRIES]:
            stale.unlink(missing_ok=True)
    except OSError as exc:
        _log.warning("Unable to enforce ASR cache capacity: %s", exc)


@dataclass
class AlignSpeechTimelineInput:
    chapter_number: int
    script: DubbingScript
    segment_results: list[SynthesisResult]
    execution_plan: AudioExecutionPlan
    # Repair-mode incremental alignment: when set, only these segment indices
    # run the full ASR + forced-alignment path; every other segment inherits
    # its alignment from ``previous_timeline``.  Keeps the repair round of the
    # synthesis loop from re-running the whole chapter's offline ASR stack.
    repair_segment_indices: list[int] | None = None
    previous_timeline: SpeechTimeline | None = None


class AlignSpeechTimelineStep(TracedStep[AlignSpeechTimelineInput, SpeechTimeline]):
    """Run ASR verification and known-text alignment after voice synthesis."""

    def __init__(
        self,
        *,
        settings: Settings,
        registry: AudioPluginRegistry,
        on_step: StepEventCallback | None = None,
    ) -> None:
        super().__init__(settings=settings, on_step=on_step)
        self._registry = registry
        self._logger = _log
        self._clients: dict[str, AudioSidecarClient] = {}
        self._unavailable: set[str] = set()
        self._unavailable_endpoints: set[str] = set()
        self._broker = configure_local_model_resources(settings)
        self._active_accelerator = "cpu"

    @property
    def step_name(self) -> str:
        return "tts_align_speech_timeline"

    async def _execute(self, input_data: AlignSpeechTimelineInput) -> SpeechTimeline:
        self._execution_plan = input_data.execution_plan
        self._active_accelerator = str(input_data.execution_plan.hardware.accelerator or "cpu")
        playback = build_timeline(input_data.script, input_data.segment_results)
        result_map = {
            result.segment_index: result
            for result in input_data.segment_results
            if result.status == SynthesisStatus.COMPLETED and result.audio_path
        }
        segment_map = {segment.segment_index: segment for segment in input_data.script.segments}
        repair_indices = (
            set(input_data.repair_segment_indices)
            if input_data.repair_segment_indices is not None
            else None
        )
        inherited_alignments: dict[int, AlignmentResult] = {}
        inherited_entries: dict[int, SpeechTimelineEntry] = {}
        if repair_indices is not None and input_data.previous_timeline is not None:
            inherited_alignments = {
                item.segment_index: item
                for item in input_data.previous_timeline.alignments
                if item.segment_index not in repair_indices
            }
            inherited_entries = {
                item.segment_index: item
                for item in input_data.previous_timeline.entries
                if item.segment_index not in repair_indices
            }
        alignments: list[AlignmentResult] = []
        entries: list[SpeechTimelineEntry] = []
        try:
            # Concurrent alignment with bounded parallelism (P1-2): cloud ASR /
            # alignment engines run in parallel up to the configured cap while
            # local models stay serialized by their own resource leases.
            max_concurrent = max(
                1,
                min(
                    len(playback.entries),
                    int(getattr(self._settings, "tts_alignment_max_concurrent", 2) or 2),
                ),
            )
            _semaphore = asyncio.Semaphore(max_concurrent)

            async def _process_entry(
                timeline_entry: TimelineEntry,
            ) -> tuple[AlignmentResult, SpeechTimelineEntry] | None:
                """Align or inherit one timeline entry."""
                async with _semaphore:
                    segment = segment_map.get(timeline_entry.segment_index)
                    if segment is None:
                        return None
                    inherited = inherited_alignments.get(timeline_entry.segment_index)
                    if inherited is not None:
                        # Repair mode: reuse the previously validated alignment
                        # for segments that did not need regeneration.  Tokens
                        # and coverage are authoritative from the earlier pass;
                        # the entry status is marked so consumers can tell the
                        # inherited rows apart from freshly aligned ones.
                        prior_entry = inherited_entries.get(timeline_entry.segment_index)
                        return inherited, SpeechTimelineEntry(
                            segment_index=timeline_entry.segment_index,
                            language=(
                                prior_entry.language
                                if prior_entry is not None
                                else inherited.language
                            ),
                            text=segment.synthesis_text,
                            start_ms=timeline_entry.start_ms,
                            end_ms=timeline_entry.end_ms,
                            tokens=inherited.tokens,
                            alignment_status="inherited",
                        )
                    synthesis = result_map.get(timeline_entry.segment_index)
                    if synthesis is None or segment.segment_type not in {
                        SegmentType.NARRATION,
                        SegmentType.DIALOGUE,
                        SegmentType.INNER_THOUGHT,
                    }:
                        alignment = self._fallback_alignment(
                            timeline_entry.segment_index,
                            segment.synthesis_text,
                            segment.language_code,
                            timeline_entry.start_ms,
                            timeline_entry.end_ms,
                            "没有可对齐的人声音频。",
                        )
                    else:
                        alignment = await self._align_segment(
                            segment=segment,
                            segment_index=segment.segment_index,
                            audio_path=Path(synthesis.audio_path),
                            start_ms=timeline_entry.start_ms,
                            end_ms=timeline_entry.end_ms,
                            plan=input_data.execution_plan,
                        )
                    return alignment, SpeechTimelineEntry(
                        segment_index=timeline_entry.segment_index,
                        language=alignment.language,
                        text=segment.synthesis_text,
                        start_ms=timeline_entry.start_ms,
                        end_ms=timeline_entry.end_ms,
                        tokens=alignment.tokens,
                        alignment_status=alignment.status,
                    )

            processed = await asyncio.gather(*(_process_entry(entry) for entry in playback.entries))
            # ``gather`` preserves input order, so alignment rows stay sorted
            # exactly like the serial path used to produce them.
            for outcome in processed:
                if outcome is None:
                    continue
                alignment, entry = outcome
                alignments.append(alignment)
                entries.append(entry)
        finally:
            await self._close_clients()
        return SpeechTimeline(
            chapter_number=input_data.chapter_number,
            entries=entries,
            alignments=alignments,
            total_duration_ms=playback.total_duration_ms,
            execution_plan=input_data.execution_plan,
        )

    async def _align_segment(
        self,
        *,
        segment: DubbingSegment,
        segment_index: int,
        audio_path: Path,
        start_ms: int,
        end_ms: int,
        plan: AudioExecutionPlan,
    ) -> AlignmentResult:
        expected_text = segment.synthesis_text
        routed_units = route_segment(segment, plan)
        routed_languages = list(
            dict.fromkeys(unit.language for unit in routed_units if unit.language != "auto")
        )
        language = routed_languages[0] if len(routed_languages) == 1 else "auto"
        asr_assignment = plan.assignment_for(AudioExecutionStage.ASR)
        align_assignment = plan.assignment_for(AudioExecutionStage.ALIGN)
        recognized_text = ""
        text_error_rate: float | None = None
        asr_candidates = list(
            dict.fromkeys(
                [
                    *(unit.asr_plugin_id for unit in routed_units),
                    (
                        asr_assignment.primary.plugin_id
                        if asr_assignment and asr_assignment.primary
                        else ""
                    ),
                    *(
                        [item.plugin_id for item in asr_assignment.fallbacks]
                        if asr_assignment
                        else []
                    ),
                ]
            )
        )
        for asr_plugin_id in (item for item in asr_candidates if item):
            try:
                asr_payload = await self._transcribe(
                    asr_plugin_id,
                    audio_path,
                    language=language,
                    expected_text=expected_text,
                )
                recognized_text = str(asr_payload.get("text") or "")
                if recognized_text:
                    text_error_rate = self._text_error_rate(expected_text, recognized_text)
                break
            except Exception as exc:
                _log.info("ASR verification unavailable for segment %d: %s", segment_index, exc)
                self._mark_unavailable(asr_plugin_id)

        candidates = [unit.aligner_plugin_id for unit in routed_units if unit.aligner_plugin_id]
        candidates.extend(
            fallback for unit in routed_units for fallback in unit.fallback_plugin_ids
        )
        if align_assignment is not None and align_assignment.primary is not None:
            candidates.extend(
                [
                    align_assignment.primary.plugin_id,
                    *(item.plugin_id for item in align_assignment.fallbacks),
                ]
            )
        errors: list[str] = []
        for plugin_id in dict.fromkeys(item for item in candidates if item):
            try:
                payload = await self._align(
                    plugin_id,
                    audio_path,
                    text=expected_text,
                    language=language,
                )
                tokens = self._normalize_tokens(payload, absolute_start_ms=start_ms)
                if not tokens:
                    raise ValueError("alignment response contains no timing items")
                coverage = self._coverage(expected_text, tokens)
                result = AlignmentResult(
                    segment_index=segment_index,
                    plugin_id=plugin_id,
                    language=language or "auto",
                    expected_text=expected_text,
                    recognized_text=recognized_text,
                    status="aligned",
                    segment_start_ms=start_ms,
                    segment_end_ms=end_ms,
                    tokens=tokens,
                    coverage=coverage,
                    text_error_rate=text_error_rate,
                )
                # Conditional validator (P0-2): run the second engine pass only
                # when the primary alignment already shows evidence of trouble.
                # Healthy segments keep the single-pass path, halving engine
                # calls for well-behaved audio.
                if coverage < self._settings.tts_min_alignment_coverage or (
                    text_error_rate is not None
                    and text_error_rate > self._settings.tts_max_text_error_rate
                ):
                    await self._validate_alignment(result, audio_path=audio_path, plan=plan)
                return result
            except Exception as exc:
                errors.append(f"{plugin_id}: {exc}")
                self._mark_unavailable(plugin_id)
        return self._fallback_alignment(
            segment_index,
            expected_text,
            language,
            start_ms,
            end_ms,
            "；".join(errors) or "没有配置强制对齐插件。",
            recognized_text=recognized_text,
            text_error_rate=text_error_rate,
        )

    async def _validate_alignment(
        self,
        result: AlignmentResult,
        *,
        audio_path: Path,
        plan: AudioExecutionPlan,
    ) -> None:
        assignment = plan.assignment_for(AudioExecutionStage.ALIGNMENT_VALIDATOR)
        if assignment is None or assignment.primary is None:
            return
        plugin_id = assignment.primary.plugin_id
        try:
            payload = await self._align(
                plugin_id,
                audio_path,
                text=result.expected_text,
                language=result.language,
            )
            validator_tokens = self._normalize_tokens(
                payload,
                absolute_start_ms=result.segment_start_ms,
            )
        except Exception as exc:
            _log.info(
                "Alignment validator unavailable for segment %d: %s", result.segment_index, exc
            )
            return
        deltas = [
            abs(primary.start_ms - validator.start_ms)
            for primary, validator in zip(result.tokens, validator_tokens, strict=False)
        ]
        if deltas:
            deltas.sort()
            index = min(len(deltas) - 1, max(0, round(0.95 * (len(deltas) - 1))))
            result.validator_p95_delta_ms = float(deltas[index])
            result.validator_plugin_id = plugin_id

    async def _transcribe(
        self,
        plugin_id: str,
        audio_path: Path,
        *,
        language: str,
        expected_text: str,
    ) -> dict[str, Any]:
        if not self._settings.tts_asr_cache_enabled:
            request = self._resource_request(plugin_id, workload="asr", operation="转写核验")
            if request is None:
                return await self._client_for(plugin_id).transcribe(
                    audio_path,
                    language=language or "auto",
                    expected_text=expected_text,
                )
            async with self._broker.lease(request):
                return await self._client_for(plugin_id).transcribe(
                    audio_path,
                    language=language or "auto",
                    expected_text=expected_text,
                )
        cache_path = _asr_cache_path(
            self._settings,
            plugin_id,
            audio_path,
            operation="asr",
            language=language,
            text=expected_text,
        )
        cached = _asr_cache_load(
            cache_path,
            ttl_hours=self._settings.tts_asr_cache_ttl_hours,
        )
        if cached is not None:
            return cached
        request = self._resource_request(plugin_id, workload="asr", operation="转写核验")
        if request is None:
            payload = await self._client_for(plugin_id).transcribe(
                audio_path,
                language=language or "auto",
                expected_text=expected_text,
            )
        else:
            async with self._broker.lease(request):
                payload = await self._client_for(plugin_id).transcribe(
                    audio_path,
                    language=language or "auto",
                    expected_text=expected_text,
                )
        _asr_cache_save(cache_path, payload)
        return payload

    async def _align(
        self,
        plugin_id: str,
        audio_path: Path,
        *,
        text: str,
        language: str,
    ) -> dict[str, Any]:
        if not self._settings.tts_asr_cache_enabled:
            request = self._resource_request(
                plugin_id,
                workload="forced_alignment",
                operation="强制对齐",
            )
            if request is None:
                return await self._client_for(plugin_id).align(
                    audio_path,
                    text=text,
                    language=language or "auto",
                )
            async with self._broker.lease(request):
                return await self._client_for(plugin_id).align(
                    audio_path,
                    text=text,
                    language=language or "auto",
                )
        cache_path = _asr_cache_path(
            self._settings,
            plugin_id,
            audio_path,
            operation="align",
            language=language,
            text=text,
        )
        cached = _asr_cache_load(
            cache_path,
            ttl_hours=self._settings.tts_asr_cache_ttl_hours,
        )
        if cached is not None:
            return cached
        request = self._resource_request(
            plugin_id,
            workload="forced_alignment",
            operation="强制对齐",
        )
        if request is None:
            payload = await self._client_for(plugin_id).align(
                audio_path,
                text=text,
                language=language or "auto",
            )
        else:
            async with self._broker.lease(request):
                payload = await self._client_for(plugin_id).align(
                    audio_path,
                    text=text,
                    language=language or "auto",
                )
        _asr_cache_save(cache_path, payload)
        return payload

    def _resource_request(
        self,
        plugin_id: str,
        *,
        workload: str,
        operation: str,
    ) -> LocalResourceRequest | None:
        manifest = self._registry.require(plugin_id)
        runtime = manifest.runtime
        if runtime.is_cloud:
            return None
        accelerator = (
            self._active_accelerator in {"cuda", "mps"}
            and self._active_accelerator in runtime.accelerators
        )
        try:
            memory_class = LocalMemoryClass(runtime.memory_class.value)
        except ValueError:
            memory_class = LocalMemoryClass.MEDIUM
        return LocalResourceRequest(
            workload=workload,
            label=f"{manifest.display_name} · {operation}",
            memory_class=memory_class,
            accelerator=accelerator,
            cpu_heavy=not accelerator,
            priority=LocalResourcePriority.BACKGROUND,
            timeout_s=float(self._settings.local_model_resource_wait_timeout_s),
        )

    def _client_for(self, plugin_id: str) -> AudioSidecarClient:
        if plugin_id in self._unavailable:
            raise RuntimeError("plugin was unavailable earlier in this run")
        existing = self._clients.get(plugin_id)
        if existing is not None:
            return existing
        manifest = self._registry.require(plugin_id)
        base_url = self._frozen_endpoint(plugin_id)
        if not base_url:
            raise RuntimeError("frozen route has no sidecar endpoint")
        if base_url in self._unavailable_endpoints:
            raise RuntimeError("sidecar endpoint was unavailable earlier in this run")
        api_key = (
            self._settings.audio_qwen3_asr_api_key if manifest.provider_id == "qwen3-asr" else ""
        )
        client = AudioSidecarClient(
            base_url,
            api_key=api_key,
            timeout_s=180.0,
            connect_timeout_s=1.0,
        )
        self._clients[plugin_id] = client
        return client

    def _mark_unavailable(self, plugin_id: str) -> None:
        self._unavailable.add(plugin_id)
        endpoint = self._frozen_endpoint(plugin_id)
        if endpoint:
            self._unavailable_endpoints.add(endpoint)

    def _frozen_endpoint(self, plugin_id: str) -> str:
        plan = getattr(self, "_execution_plan", None)
        if plan is None:
            return ""
        for assignment in plan.routes:
            if assignment.primary is not None and assignment.primary.plugin_id == plugin_id:
                return str(assignment.primary.endpoint or "").strip()
            for fallback in assignment.fallbacks:
                if fallback.plugin_id == plugin_id:
                    return str(fallback.endpoint or "").strip()
        return ""

    async def _close_clients(self) -> None:
        clients = list(self._clients.values())
        self._clients.clear()
        for client in clients:
            try:
                await client.aclose()
            except Exception:
                pass

    @staticmethod
    def _normalize_tokens(
        payload: dict[str, Any],
        *,
        absolute_start_ms: int,
    ) -> list[AlignmentToken]:
        raw_items = payload.get("items") or payload.get("words") or payload.get("tokens") or []
        if not isinstance(raw_items, list):
            return []
        tokens: list[AlignmentToken] = []
        for raw in raw_items:
            if not isinstance(raw, dict):
                continue
            text = str(raw.get("text") or raw.get("word") or raw.get("token") or "")
            if not text:
                continue
            start = AlignSpeechTimelineStep._time_ms(raw, "start")
            end = AlignSpeechTimelineStep._time_ms(raw, "end")
            if end < start:
                continue
            unit_raw = str(raw.get("unit") or "token")
            try:
                unit = TimelineGranularity(unit_raw)
            except ValueError:
                unit = TimelineGranularity.TOKEN
            confidence_raw = raw.get("confidence")
            confidence = float(confidence_raw) if confidence_raw is not None else None
            tokens.append(
                AlignmentToken(
                    text=text,
                    start_ms=absolute_start_ms + start,
                    end_ms=absolute_start_ms + end,
                    confidence=confidence,
                    start_char=raw.get("start_char"),
                    end_char=raw.get("end_char"),
                    unit=unit,
                )
            )
        return tokens

    @staticmethod
    def _time_ms(raw: dict[str, Any], prefix: str) -> int:
        millisecond_key = f"{prefix}_ms"
        if millisecond_key in raw:
            return max(0, round(float(raw[millisecond_key])))
        value = float(raw.get(prefix, 0.0))
        return max(0, round(value * 1000))

    @staticmethod
    def _coverage(expected_text: str, tokens: list[AlignmentToken]) -> float:
        expected = AlignSpeechTimelineStep._normalize_text(expected_text)
        aligned = AlignSpeechTimelineStep._normalize_text("".join(item.text for item in tokens))
        if not expected:
            return 1.0
        return min(1.0, len(aligned) / len(expected))

    @staticmethod
    def _text_error_rate(expected: str, actual: str) -> float:
        left = AlignSpeechTimelineStep._normalize_text(expected)
        right = AlignSpeechTimelineStep._normalize_text(actual)
        if not left:
            return 0.0 if not right else 1.0
        previous = list(range(len(right) + 1))
        for left_index, left_char in enumerate(left, start=1):
            current = [left_index]
            for right_index, right_char in enumerate(right, start=1):
                current.append(
                    min(
                        current[-1] + 1,
                        previous[right_index] + 1,
                        previous[right_index - 1] + (left_char != right_char),
                    )
                )
            previous = current
        return min(1.0, previous[-1] / len(left))

    @staticmethod
    def _normalize_text(text: str) -> str:
        return re.sub(r"[^\w\u3400-\u9fff]+", "", text, flags=re.UNICODE).lower()

    @staticmethod
    def _fallback_alignment(
        segment_index: int,
        text: str,
        language: str,
        start_ms: int,
        end_ms: int,
        error: str,
        *,
        recognized_text: str = "",
        text_error_rate: float | None = None,
    ) -> AlignmentResult:
        return AlignmentResult(
            segment_index=segment_index,
            language=language or "auto",
            expected_text=text,
            recognized_text=recognized_text,
            status="segment_fallback",
            segment_start_ms=start_ms,
            segment_end_ms=end_ms,
            coverage=0.0,
            text_error_rate=text_error_rate,
            error=error[:1000],
        )


def apply_speech_timeline_to_script(
    script: DubbingScript,
    timeline: SpeechTimeline,
) -> DubbingScript:
    """Resolve semantic segment anchors before generating/mixing sound assets."""

    entries = {entry.segment_index: entry for entry in timeline.entries}

    def start_for(index: int | None, fallback: int) -> int:
        entry = entries.get(index) if index is not None else None
        return entry.start_ms if entry is not None else fallback

    def end_for(index: int | None, fallback: int) -> int:
        entry = entries.get(index) if index is not None else None
        return entry.end_ms if entry is not None else (fallback or timeline.total_duration_ms)

    bgm = [
        cue.model_copy(
            update={
                "start_ms": start_for(cue.start_segment_index, cue.start_ms),
                "end_ms": end_for(cue.end_segment_index, cue.end_ms),
            }
        )
        for cue in script.bgm_suggestions
    ]
    soundscapes = [
        cue.model_copy(
            update={
                "start_ms": start_for(cue.start_segment_index, cue.start_ms),
                "end_ms": end_for(cue.end_segment_index, cue.end_ms),
            }
        )
        for cue in script.soundscapes
    ]
    sfx = [
        cue.model_copy(
            update={
                "trigger_ms": max(
                    0,
                    start_for(cue.trigger_segment_index, cue.trigger_ms) + cue.offset_ms,
                )
            }
        )
        for cue in script.sfx_cues
    ]
    return script.model_copy(
        update={
            "bgm_suggestions": bgm,
            "soundscapes": soundscapes,
            "sfx_cues": sfx,
            "metadata": {
                **script.metadata,
                "speech_timeline_resolved": True,
                "speech_timeline_schema": timeline.schema_version,
            },
        }
    )
