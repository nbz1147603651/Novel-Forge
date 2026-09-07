"""Synthesize Audio pipeline step.

Executes TTS synthesis for each segment in a dubbing script,
with concurrency control and retry logic.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import subprocess
import time
import wave
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from novel_forge.core.config import Settings
from novel_forge.core.exceptions import ModelGatewayError, RateLimitError
from novel_forge.core.local_model_resources import (
    LocalMemoryClass,
    LocalResourcePriority,
    LocalResourceRequest,
    configure_local_model_resources,
)
from novel_forge.obs.logger import get_logger
from novel_forge.persistence.filesystem import atomic_write_json
from novel_forge.persistence.models import ProjectLayout
from novel_forge.pipeline.steps.base import StepEventCallback, TracedStep
from novel_forge.tts.gateway.base import TTSProviderAdapter
from novel_forge.tts.gateway.factory import (
    TTSAdapterRegistry,
    default_tts_voice_id,
    is_registered_local_tts_provider,
    resolve_tts_model,
)
from novel_forge.tts.gateway.failures import (
    TTSFailureDecision,
    TTSFailureKind,
    TTSProviderFailure,
    TTSProviderFailurePolicy,
    classify_tts_failure,
)
from novel_forge.tts.gateway.fault_tolerant import FailureManagedTTSAdapter
from novel_forge.tts.pipeline.minimax_wave_pool import MiniMaxWavePool, WaveOutcome
from novel_forge.tts.pipeline.multi_take import take_count
from novel_forge.tts.pipeline.segment_quality import (
    evaluate_synthesized_segment,
    trustworthy_duration_ms,
)
from novel_forge.tts.platform.expression_adapter import ExpressionAdapter, load_expression_adapter
from novel_forge.tts.platform.field_mapping import provider_language_value
from novel_forge.tts.platform.synthesis_capabilities import native_synthesis_request_extension
from novel_forge.tts.runtime.audio_runtime import (
    apply_portable_controls,
    configure_pydub,
    ffmpeg_executable,
    is_short_spoken_utterance,
    needs_portable_controls,
)
from novel_forge.tts.runtime.performance_policy import (
    ResolvedCharacterPerformance,
    is_extreme_short_utterance,
    resolve_character_performance,
)
from novel_forge.tts.runtime.speakable_text import normalize_speakable_text
from novel_forge.tts.runtime.storage_limits import ensure_project_audio_write
from novel_forge.tts.schemas import (
    EMOTION_MAPPINGS,
    DubbingScript,
    EmotionTag,
    ParalinguisticTag,
    SegmentType,
    SynthesisResult,
    SynthesisStatus,
    TTSProvider,
    TTSRequest,
    VoiceTeamContract,
)

_log = get_logger("tts.pipeline.synthesize_audio")

# MiniMax T2A interjection tag → internal ParalinguisticTag type.
# This is a protocol format mapping (MiniMax API contract), not a compensation parameter.
_MINIMAX_TAG_TO_TYPE: dict[str, str] = {
    "(breath)": "breath",
    "(laughs)": "laugh",
    "(chuckle)": "chuckle",
    "(coughs)": "cough",
    "(clear-throat)": "clear_throat",
    "(groans)": "groan",
    "(pant)": "pant",
    "(inhale)": "inhale",
    "(exhale)": "exhale",
    "(sighs)": "sigh",
    "(gasps)": "choke",
    "(sniffs)": "sniff",
    "(snorts)": "snort",
    "(lip-smacking)": "lip_smacking",
    "(humming)": "hum",
    "(hissing)": "hiss",
    "(emm)": "hesitate",
    "(sneezes)": "sneeze",
}

_DEFAULT_VOICE_BY_PROVIDER: dict[TTSProvider, str] = {
    TTSProvider.MINIMAX: "Chinese (Mandarin)_Reliable_Executive",
    TTSProvider.BAILIAN: "longanlingxin",
    TTSProvider.DASHSCOPE: "longanlingxin",
    TTSProvider.TENCENT: "1004",
    TTSProvider.VOLCENGINE_ARK: "zh_female_vv_uranus_bigtts",
    TTSProvider.MIMO: "mimo_default",
    TTSProvider.LOCAL: "default",
    TTSProvider.QWEN3: "Vivian",
    TTSProvider.COSYVOICE: "中文女",
    TTSProvider.OPENVOICE: "openvoice-base",
    TTSProvider.MOCK: "mock-male-1",
}


class SynthesisRequestPacer:
    """Serialize remote TTS request starts to a bounded request rate.

    The synthesis semaphore limits in-flight work but does not constrain how
    quickly a provider receives requests.  This pacer deliberately has no
    burst allowance: queued segments start evenly throughout the minute, and
    retries reserve a new slot as well.
    """

    def __init__(self, requests_per_minute: int) -> None:
        if requests_per_minute < 1:
            raise ValueError("requests_per_minute must be at least 1")
        self._interval_s = 60.0 / requests_per_minute
        self._next_request_at = 0.0
        self._cooldown_until = 0.0
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        """Wait for the next safe provider request slot."""
        await self.acquire_batch(1)

    async def acquire_batch(self, count: int) -> None:
        """Reserve a simultaneous request batch without exceeding the RPM budget.

        A MiniMax wave contains several requests that should begin together.
        Reserving the entire batch advances the next available slot by its
        aggregate rate budget, instead of serialising requests within a wave.
        """
        if count < 1:
            raise ValueError("count must be at least 1")
        while True:
            async with self._lock:
                now = time.monotonic()
                scheduled_at = max(now, self._next_request_at, self._cooldown_until)
                self._next_request_at = scheduled_at + self._interval_s * count

            delay_s = scheduled_at - time.monotonic()
            if delay_s > 0:
                await asyncio.sleep(delay_s)

            # A different worker can discover a provider limit while this
            # coroutine is waiting.  In that case, reserve a fresh slot after
            # the shared cooldown instead of sending a stale queued request.
            async with self._lock:
                if time.monotonic() >= self._cooldown_until:
                    return

    async def defer(self, cooldown_s: float) -> None:
        """Pause new requests after a provider reports an exhausted quota."""
        if cooldown_s <= 0:
            return
        async with self._lock:
            self._cooldown_until = max(self._cooldown_until, time.monotonic() + cooldown_s)


def _uses_remote_tts_provider(provider: TTSProvider) -> bool:
    """Return whether this provider needs cloud request pacing."""
    if is_registered_local_tts_provider(provider):
        return False
    return provider not in {
        TTSProvider.MOCK,
        TTSProvider.LOCAL,
        TTSProvider.QWEN3,
        TTSProvider.COSYVOICE,
        TTSProvider.OPENVOICE,
    }


def _effective_synthesis_concurrency(provider: TTSProvider, requested: int) -> int:
    """Keep heavyweight local runtimes serial while preserving cloud throughput."""
    if is_registered_local_tts_provider(provider) or provider in {
        TTSProvider.LOCAL,
        TTSProvider.QWEN3,
        TTSProvider.COSYVOICE,
        TTSProvider.OPENVOICE,
    }:
        return 1
    return max(1, requested)


@dataclass
class SynthesisRunContext:
    """Per-invocation transient state for one synthesis run.

    Consolidates all ``self._current_*`` attributes into a single typed
    container, improving concurrency safety and testability.

    Author: novel-forge
    """

    narrator_profile: Any = None
    narrator_voice_id: str = ""
    model_id: str = ""
    execution_plan_id: str = ""
    route_plugin_id: str = ""
    route_plugin_version: str = ""
    route_endpoint: str = ""
    chapter_number: int = 0
    voice_team_hash: str = ""
    context_segments: list[Any] = field(default_factory=list)


@dataclass
class SynthesizeAudioInput:
    """Input for audio synthesis."""

    chapter_number: int
    script: DubbingScript
    voice_team: VoiceTeamContract
    provider: TTSProvider = TTSProvider.MOCK
    model_id: str = ""
    """Optional model selected by the capability plan for this provider."""
    execution_plan_id: str = ""
    route_plugin_id: str = ""
    route_plugin_version: str = ""
    route_endpoint: str = ""
    output_dir: Path | None = None
    max_concurrent: int = 4
    retry_limit: int = 3
    completed_segments: list[int] | None = None  # For resume
    completed_segment_hashes: dict[str, str] | None = None
    """Persisted request hashes. A segment resumes only on an exact match."""
    completed_results: list[SynthesisResult] | None = None
    """Full prior results used to preserve duration, cost and provider evidence."""
    on_segment_progress: Any = None  # Callable(segment_idx, status_str) -> None
    # ── 旁白动态适配 ────────────────────────────────────────────────────────
    narrator_voice_id: str = ""
    """旁白音色 ID（优先于 voice_team 和默认值）"""
    narrator_profile: Any = None  # NarratorVoiceProfile
    """旁白声音画像（用于动态语速/音量调整）"""
    voice_team_hash: str = ""
    script_hash: str = ""
    persist_progress: bool = True
    """Audition takes use isolated files and must not mutate formal resume state."""
    context_script: DubbingScript | None = None
    """Full chapter context when ``script`` contains only one audition segment."""


class SynthesizeAudioStep(TracedStep[SynthesizeAudioInput, list[SynthesisResult]]):
    """Synthesize audio for all segments in a dubbing script.

    Features:
    - Concurrent synthesis with configurable parallelism
    - Per-segment retry on failure
    - Resume support (skip completed segments)
    - DLQ for persistent failures
    """

    def __init__(
        self,
        registry: TTSAdapterRegistry,
        *,
        settings: Settings,
        layout: ProjectLayout | None = None,
        on_step: StepEventCallback | None = None,
    ) -> None:
        super().__init__(settings=settings, on_step=on_step)
        self._registry = registry
        self._resource_broker = configure_local_model_resources(settings)
        self._layout = layout
        self._storage_write_lock = asyncio.Lock()
        self._fallback_failure_policies: dict[str, TTSProviderFailurePolicy] = {}
        # TTS cost budget gate. None when both limits are 0 (disabled), so the
        # per-segment check/record calls are skipped entirely. Historically TTS
        # spend bypassed the LLM SpendingTracker; this closes that gap.
        from novel_forge.tts.runtime.budget import build_tts_spending_tracker

        self._spending_tracker = build_tts_spending_tracker(
            monthly_limit=settings.tts_monthly_cost_budget_usd,
            book_limit=settings.tts_book_cost_budget_usd,
        )

    # ── Backward-compatible property proxies for tests that set _current_* ──
    # Production code reads from self._run_ctx; these properties allow legacy
    # test code (step._current_model_id = "x") to keep working.

    def _ensure_run_ctx(self) -> SynthesisRunContext:
        if not hasattr(self, "_run_ctx"):
            self._run_ctx = SynthesisRunContext()
        return self._run_ctx

    @property
    def _current_model_id(self) -> str:
        return self._ensure_run_ctx().model_id

    @_current_model_id.setter
    def _current_model_id(self, value: str) -> None:
        self._ensure_run_ctx().model_id = value

    @property
    def _current_narrator_profile(self) -> Any:
        return self._ensure_run_ctx().narrator_profile

    @_current_narrator_profile.setter
    def _current_narrator_profile(self, value: Any) -> None:
        self._ensure_run_ctx().narrator_profile = value

    @property
    def _current_narrator_voice_id(self) -> str:
        return self._ensure_run_ctx().narrator_voice_id

    @_current_narrator_voice_id.setter
    def _current_narrator_voice_id(self, value: str) -> None:
        self._ensure_run_ctx().narrator_voice_id = value

    @property
    def _current_voice_team_hash(self) -> str:
        return self._ensure_run_ctx().voice_team_hash

    @_current_voice_team_hash.setter
    def _current_voice_team_hash(self, value: str) -> None:
        self._ensure_run_ctx().voice_team_hash = value

    @property
    def _current_chapter_number(self) -> int:
        return self._ensure_run_ctx().chapter_number

    @_current_chapter_number.setter
    def _current_chapter_number(self, value: int) -> None:
        self._ensure_run_ctx().chapter_number = value

    @property
    def _current_execution_plan_id(self) -> str:
        return self._ensure_run_ctx().execution_plan_id

    @_current_execution_plan_id.setter
    def _current_execution_plan_id(self, value: str) -> None:
        self._ensure_run_ctx().execution_plan_id = value

    @property
    def _current_route_plugin_id(self) -> str:
        return self._ensure_run_ctx().route_plugin_id

    @_current_route_plugin_id.setter
    def _current_route_plugin_id(self, value: str) -> None:
        self._ensure_run_ctx().route_plugin_id = value

    @property
    def _current_route_plugin_version(self) -> str:
        return self._ensure_run_ctx().route_plugin_version

    @_current_route_plugin_version.setter
    def _current_route_plugin_version(self, value: str) -> None:
        self._ensure_run_ctx().route_plugin_version = value

    @property
    def _current_route_endpoint(self) -> str:
        return self._ensure_run_ctx().route_endpoint

    @_current_route_endpoint.setter
    def _current_route_endpoint(self, value: str) -> None:
        self._ensure_run_ctx().route_endpoint = value

    @property
    def _current_context_segments(self) -> list[Any]:
        return self._ensure_run_ctx().context_segments

    @_current_context_segments.setter
    def _current_context_segments(self, value: list[Any]) -> None:
        self._ensure_run_ctx().context_segments = value

    @property
    def step_name(self) -> str:
        return "tts_synthesize_audio"

    async def _execute(
        self,
        input_data: SynthesizeAudioInput,
    ) -> list[SynthesisResult]:
        """Synthesize all segments with concurrency control.

        Features:
        - Concurrent synthesis with configurable parallelism
        - Per-segment retry on failure with exponential backoff
        - Resume support (skip completed segments + persist progress)
        - Detailed per-segment logging (latency, retries)
        - Dynamic narrator adaptation based on NarratorVoiceProfile
        """
        import time as _time

        effective_concurrency = _effective_synthesis_concurrency(
            input_data.provider,
            input_data.max_concurrent,
        )
        _log.info(
            "Synthesizing chapter %d: %d segments, provider=%s, max_concurrent=%d, "
            "requests_per_minute=%s, retry_limit=%d",
            input_data.chapter_number,
            len(input_data.script.segments),
            input_data.provider.value,
            effective_concurrency,
            (
                self._settings.tts_synthesis_requests_per_minute
                if _uses_remote_tts_provider(input_data.provider)
                else "local"
            ),
            input_data.retry_limit,
        )

        # Store narrator profile for _build_tts_request access.  A caller may
        # bypass the workspace facade, so retain the TTL guard at the step
        # boundary as well.
        narrator_profile = input_data.narrator_profile
        if narrator_profile is not None and getattr(narrator_profile, "is_expired", False):
            _log.warning("Ignoring expired narrator voice profile during synthesis")
            narrator_profile = None
        # Consolidate per-invocation transient state into a single context object.
        self._run_ctx = SynthesisRunContext(
            narrator_profile=narrator_profile,
            narrator_voice_id=input_data.narrator_voice_id,
            model_id=input_data.model_id,
            execution_plan_id=input_data.execution_plan_id,
            route_plugin_id=input_data.route_plugin_id,
            route_plugin_version=input_data.route_plugin_version,
            route_endpoint=input_data.route_endpoint,
            chapter_number=input_data.chapter_number,
            voice_team_hash=input_data.voice_team_hash,
            context_segments=list((input_data.context_script or input_data.script).segments),
        )

        # Load the declarative expression adapter for the active provider.
        # All emotion compensation, tone_hint rules, and vocal direction
        # bridging parameters come from JSON profiles — no hardcoded values.
        self._expression_adapter: ExpressionAdapter = load_expression_adapter(
            input_data.provider.value
        )

        adapter = self._registry.get_adapter(input_data.provider)
        output_dir = input_data.output_dir or self._get_output_dir(input_data.chapter_number)
        output_dir.mkdir(parents=True, exist_ok=True)

        completed = set(input_data.completed_segments or [])
        completed_results = {
            result.segment_index: result for result in (input_data.completed_results or [])
        }
        results: list[SynthesisResult] = []
        semaphore = asyncio.Semaphore(effective_concurrency)
        # MiniMax's reference production contract is deliberately wave-based:
        # one attempt for every item in a wave, then a shared retry wave.  Keep
        # the decision here, before ``synthesize_segment`` closes over it, so a
        # segment never quietly re-enables its generic inline retry loop.
        use_wave_pool = input_data.provider == TTSProvider.MINIMAX and bool(
            getattr(self._settings, "tts_wave_pool_enabled", True)
        )
        # The outer semaphore bounds *segments*.  A high-emotion segment can
        # additionally generate multi-takes, so it is not sufficient to bound
        # actual provider requests.  This independent gate keeps every MiniMax
        # HTTP request, including optional takes, within the active wave cap.
        provider_call_limit = (
            self._settings.tts_wave_pool_size if use_wave_pool else effective_concurrency
        )
        provider_call_semaphore = asyncio.Semaphore(provider_call_limit)
        request_pacer = (
            SynthesisRequestPacer(self._settings.tts_synthesis_requests_per_minute)
            if _uses_remote_tts_provider(input_data.provider)
            else None
        )

        async def _reserve_minimax_wave_start(_round_no: int, item_count: int) -> None:
            """Reserve one shared rate slot for a MiniMax wave before it starts."""
            if request_pacer is None:
                return
            acquire_batch = getattr(request_pacer, "acquire_batch", None)
            if callable(acquire_batch):
                await acquire_batch(item_count)
                return
            # Test doubles and external pacer implementations written before
            # batch reservation still retain their safe one-slot semantics.
            for _ in range(item_count):
                await request_pacer.acquire()

        on_progress = input_data.on_segment_progress
        progress_lock = asyncio.Lock()
        # Throttle progress persistence: write every 5 segments or 30s, not
        # per-segment, to reduce disk I/O contention with concurrent LLM tasks.
        _persist_counter = [0]
        _persist_last_time = [_time.monotonic()]
        _PERSIST_SEGMENT_INTERVAL = 5
        _PERSIST_TIME_INTERVAL_S = 30.0
        _total_segments = len(input_data.script.segments)

        # Build character -> voice_id map
        voice_map: dict[str, Any] = {}
        fallback_character_ids: set[str] = set()
        for entry in input_data.voice_team.entries:
            if not entry.is_ready or entry.is_expired:
                continue
            if entry.provider == input_data.provider and entry.voice_id:
                voice_map[entry.character_id] = entry
                continue
            fallback_voice_id = str(
                entry.fallback_voice_ids.get(input_data.provider.value, "")
            ).strip()
            if fallback_voice_id:
                fallback_character_ids.add(entry.character_id)
                voice_map[entry.character_id] = entry.model_copy(
                    update={
                        "provider": input_data.provider,
                        "voice_id": fallback_voice_id,
                    }
                )

        async def synthesize_segment(segment_position: int) -> SynthesisResult:
            """Synthesize a single segment with retry."""
            segment = input_data.script.segments[segment_position]
            segment_idx = segment.segment_index

            # Skip non-synthesizable segments
            if segment.segment_type in (SegmentType.BGM, SegmentType.SFX, SegmentType.SILENCE):
                if on_progress:
                    on_progress(segment_idx, "skipped")
                return SynthesisResult(
                    segment_index=segment_idx,
                    status=SynthesisStatus.SKIPPED,
                )

            # Final safety net: skip segments with no speakable content.
            # This catches pure-punctuation narration that survived all upstream
            # merge/filter passes (e.g. cross-paragraph isolated segments).
            _synth_text = str(getattr(segment, "synthesis_text", "") or segment.text)
            if _synth_text.strip() and not normalize_speakable_text(_synth_text):
                _log.info(
                    "Segment %d: skipping synthesis — text has no speakable content: %r",
                    segment_idx,
                    _synth_text[:40],
                )
                if on_progress:
                    on_progress(segment_idx, "skipped")
                return SynthesisResult(
                    segment_index=segment_idx,
                    status=SynthesisStatus.SKIPPED,
                )

            # A dialogue whose cast entry is unavailable must never silently
            # fall through to the narrator.  Wrong-speaker audio is more costly
            # to discover than an explicit resumable segment failure.
            if segment.segment_type in (SegmentType.DIALOGUE, SegmentType.INNER_THOUGHT) and (
                not segment.character_id or segment.character_id not in voice_map
            ):
                error = (
                    f"No ready voice verified for spoken segment {segment.segment_index}; "
                    "review its speaker or rebuild the voice team before resuming."
                )
                if on_progress:
                    on_progress(segment_idx, "failed")
                return SynthesisResult(
                    segment_index=segment_idx,
                    status=SynthesisStatus.FAILED,
                    provider=input_data.provider,
                    error_message=error,
                )

            # Skip already completed only when the artifact still exists.  A
            # checkpoint can outlive a manually deleted/corrupted audio file.
            segment_path = self._get_segment_path(output_dir, segment_idx)
            request_hash = self._request_hash(segment, voice_map, input_data.provider)
            prior_result = completed_results.get(segment_idx)
            prior_path = (
                Path(prior_result.audio_path)
                if prior_result is not None and prior_result.audio_path
                else segment_path
            )
            persisted_hashes = input_data.completed_segment_hashes
            # A legacy checkpoint without request fingerprints is deliberately
            # not trusted.  Re-synthesizing those segments is safer than
            # accidentally reusing audio whose text, voice, or controls have
            # since changed.
            resume_hash_matches = bool(
                persisted_hashes and persisted_hashes.get(str(segment_idx), "") == request_hash
            )
            if (
                segment_idx in completed
                and resume_hash_matches
                and prior_path.exists()
                and prior_path.stat().st_size > 0
            ):
                if on_progress:
                    on_progress(segment_idx, "completed")
                _log.debug("Segment %d: skipped (already completed, resume)", segment_idx)
                if prior_result is not None:
                    return prior_result.model_copy(
                        update={
                            "status": SynthesisStatus.COMPLETED,
                            "audio_path": str(prior_path),
                            "request_hash": request_hash,
                        }
                    )
                request = self._build_tts_request(segment, voice_map, input_data.provider)
                return SynthesisResult(
                    segment_index=segment_idx,
                    status=SynthesisStatus.COMPLETED,
                    audio_path=str(prior_path),
                    duration_ms=self._probe_duration_ms(prior_path),
                    provider=input_data.provider,
                    model_id=request.model_id,
                    voice_id=request.voice_id,
                    requested_voice_id=request.voice_id,
                    request_hash=request_hash,
                    route_plugin_id=input_data.route_plugin_id,
                    route_plugin_version=input_data.route_plugin_version,
                    route_endpoint=input_data.route_endpoint,
                )
            if segment_idx in completed and not resume_hash_matches:
                _log.info("Segment %d: resume fingerprint changed; synthesizing again", segment_idx)

            async with semaphore:
                # Signal start of synthesis
                if on_progress:
                    on_progress(segment_idx, "synthesizing")
                seg_start = _time.monotonic()
                result = await self._synthesize_with_retry(
                    adapter=adapter,
                    segment=segment,
                    segment_idx=segment_idx,
                    voice_map=voice_map,
                    output_dir=output_dir,
                    provider=input_data.provider,
                    # MiniMax failures must finish the current wave before a
                    # retry is attempted.  The MiniMaxWavePool below owns the
                    # retry budget; generic providers retain per-segment retry.
                    retry_limit=0 if use_wave_pool else input_data.retry_limit,
                    request_pacer=request_pacer,
                    initial_request_paced=use_wave_pool,
                    voice_team_hash=input_data.voice_team_hash,
                    provider_call_semaphore=provider_call_semaphore,
                )
                if (
                    result.status == SynthesisStatus.COMPLETED
                    and segment.character_id in fallback_character_ids
                ):
                    result = result.model_copy(
                        update={
                            "used_fallback": True,
                            "fallback_reason": "provider_scoped_voice_mapping",
                        }
                    )
                seg_elapsed = (_time.monotonic() - seg_start) * 1000

                # Log per-segment result
                _log.info(
                    "Segment %d: status=%s, latency=%.0fms, retries=%d, duration=%dms",
                    segment_idx,
                    result.status.value,
                    seg_elapsed,
                    result.retry_count,
                    result.duration_ms,
                )

                # Persist progress for resume (throttled to reduce I/O)
                if result.status == SynthesisStatus.COMPLETED and input_data.persist_progress:
                    _persist_counter[0] += 1
                    _now = _time.monotonic()
                    _is_last = _persist_counter[0] >= _total_segments
                    _should_persist = (
                        _is_last
                        or _persist_counter[0] % _PERSIST_SEGMENT_INTERVAL == 0
                        or (_now - _persist_last_time[0]) >= _PERSIST_TIME_INTERVAL_S
                    )
                    if _should_persist:
                        _persist_last_time[0] = _now
                        async with progress_lock:
                            await self._persist_progress(input_data, result)

                # Signal completion
                if on_progress:
                    on_progress(segment_idx, result.status.value)
                return result

        # Launch concurrent synthesis.
        # MiniMax uses the wave pool (reference Phase-4 model): 3 calls per
        # wave, wave-synchronous starts, no inline failure retry, unified
        # retry rounds after all waves.  Other providers keep the token-bucket
        # style full-queue scheduling.
        positions = list(range(len(input_data.script.segments)))
        # BGM/SFX/silence records are timeline instructions, not provider
        # requests.  Keep them out of MiniMax waves so a batch reservation
        # accounts only for real HTTP calls; otherwise a chapter rich in sound
        # cues consumes phantom RPM slots and can delay every following wave.
        provider_positions = [
            position
            for position in positions
            if input_data.script.segments[position].segment_type
            not in (SegmentType.BGM, SegmentType.SFX, SegmentType.SILENCE)
        ]
        provider_position_set = set(provider_positions)
        timeline_only_positions = [
            position for position in positions if position not in provider_position_set
        ]

        def _wave_retry_delay(
            round_no: int, pending_outcomes: list[WaveOutcome[Any, Any]]
        ) -> float:
            """Exponential settle for retry rounds, longer when rate-limited.

            Rate-limit evidence comes from either a FAILED result carrying a
            rate-limit failure kind or a raised RateLimitError; otherwise a
            shorter base applies so transient failures retry faster.
            """
            base = 20.0
            for outcome in pending_outcomes:
                result = outcome.result
                if (
                    isinstance(result, SynthesisResult)
                    and result.status == SynthesisStatus.FAILED
                    and "rate" in str(result.failure_kind).lower()
                ):
                    base = 20.0
                    break
                if isinstance(outcome.error, RateLimitError):
                    base = 20.0
                    break
            else:
                base = 10.0
            return min(float(base) * (2.0 ** max(0, round_no - 1)), 80.0)

        # Wave-pool outcomes store exceptions typed as Exception; the fallback
        # gather path may surface any BaseException (e.g. cancellation).
        raw_results: list[SynthesisResult | BaseException | None]
        if use_wave_pool:
            _log.info(
                "MiniMax wave pool: wave_size=%d retry_rounds=%d delay=%.0fs",
                self._settings.tts_wave_pool_size,
                self._settings.tts_wave_pool_retry_rounds,
                self._settings.tts_wave_retry_delay_seconds,
            )
            pool: MiniMaxWavePool[int, SynthesisResult] = MiniMaxWavePool[int, SynthesisResult](
                wave_size=self._settings.tts_wave_pool_size,
                max_retry_rounds=self._settings.tts_wave_pool_retry_rounds,
                retry_delay_s=self._settings.tts_wave_retry_delay_seconds,
                # Adaptive settle (P2-2): rate-limited failures back off
                # exponentially while ordinary failures retry sooner.
                retry_delay_fn=_wave_retry_delay,
            )
            timeline_results = await asyncio.gather(
                *(synthesize_segment(position) for position in timeline_only_positions),
                return_exceptions=True,
            )
            raw_results_by_position: dict[int, SynthesisResult | BaseException | None] = {
                position: result
                for position, result in zip(
                    timeline_only_positions,
                    timeline_results,
                    strict=True,
                )
            }
            outcomes = await pool.run(
                provider_positions,
                synthesize_segment,
                # Only retryable failures join the post-wave retry rounds.
                # Non-retryable outcomes (e.g. authentication errors) must
                # stay untouched so callers see the original decision.
                is_failure=lambda r: (
                    isinstance(r, SynthesisResult)
                    and r.status == SynthesisStatus.FAILED
                    and bool(r.retryable)
                ),
                before_wave=_reserve_minimax_wave_start,
            )
            for outcome in outcomes:
                # The actual retry count belongs to the wave outcome, not to
                # the single-attempt segment worker.  Preserve it on the final
                # result so diagnostics and resumable progress tell the truth.
                if isinstance(outcome.result, SynthesisResult):
                    raw_results_by_position[outcome.item] = outcome.result.model_copy(
                        update={"retry_count": max(0, outcome.attempts - 1)}
                    )
                else:
                    # Prefer the last result (even a FAILED one carries
                    # structured failure evidence); exceptions are only used
                    # when the call itself raised.
                    raw_results_by_position[outcome.item] = outcome.error
            raw_results = [raw_results_by_position.get(position) for position in positions]
        else:
            tasks = [asyncio.create_task(synthesize_segment(i)) for i in positions]
            raw_results = await asyncio.gather(*tasks, return_exceptions=True)

        for i, result in enumerate(raw_results):
            segment_idx = input_data.script.segments[i].segment_index
            if isinstance(result, Exception):
                _log.error("Segment %d synthesis failed: %s", segment_idx, result)
                results.append(
                    SynthesisResult(
                        segment_index=segment_idx,
                        status=SynthesisStatus.FAILED,
                        error_message=str(result),
                    )
                )
            elif isinstance(result, SynthesisResult):
                results.append(result)
            else:
                results.append(
                    SynthesisResult(
                        segment_index=segment_idx,
                        status=SynthesisStatus.FAILED,
                        error_message="Unknown result type",
                    )
                )

        # Sort by segment index
        results.sort(key=lambda r: r.segment_index)

        # Log summary
        completed_count = sum(1 for r in results if r.status == SynthesisStatus.COMPLETED)
        failed_count = sum(1 for r in results if r.status == SynthesisStatus.FAILED)
        skipped_count = sum(1 for r in results if r.status == SynthesisStatus.SKIPPED)
        total_cost = sum(r.cost_usd for r in results)

        _log.info(
            "Synthesis complete: %d completed, %d failed, %d skipped, total_cost=$%.4f",
            completed_count,
            failed_count,
            skipped_count,
            total_cost,
        )

        return results

    # ── Quality-fix progressive adjustment (D2 fix) ──────────────────────────
    # When the segment quality gate fails (e.g. duration too short, cps too
    # high), blindly retrying with identical parameters wastes provider quota
    # and never converges.  Instead we apply a zero-LLM-cost progressive
    # adjustment: each quality-fix level reduces speed incrementally so the
    # provider produces longer, more natural audio.
    #
    # Extension point: when ``quality_fix_level`` exceeds the parameter
    # adjustment range (currently 2 levels), a future implementation can invoke
    # the ``TTS_REPAIR_SEGMENT`` TaskType to rewrite ``synthesis_text`` via LLM
    # (see plan-sess_b9b11525 stage 2b).  The hook is intentionally left as a
    # no-op fallback so callers never break.

    _QUALITY_FIX_SPEED_DELTAS: tuple[float, ...] = (0.15, 0.10)  # cumulative 0.25

    def _apply_quality_fix_adjustments(
        self,
        request: TTSRequest,
        quality_fix_level: int,
    ) -> TTSRequest:
        """Return a copy of *request* with progressive speed reduction.

        Level 1: speed -= 0.15  (e.g. 1.00 → 0.85)
        Level 2: speed -= 0.10  (cumulative 0.25, e.g. 1.00 → 0.75)
        Level 3+: no further parameter change (LLM rewrite extension point).
        """
        if quality_fix_level <= 0:
            return request
        speed_delta = sum(self._QUALITY_FIX_SPEED_DELTAS[:quality_fix_level])
        if speed_delta <= 0:
            return request
        new_speed = max(0.5, request.speed - speed_delta)
        if new_speed == request.speed:
            return request
        _log.info(
            "Quality-fix level %d: speed %.2f → %.2f",
            quality_fix_level,
            request.speed,
            new_speed,
        )
        return request.model_copy(update={"speed": new_speed})

    async def _synthesize_with_retry(
        self,
        adapter: TTSProviderAdapter,
        segment: Any,
        segment_idx: int,
        voice_map: dict[str, Any],
        output_dir: Path,
        provider: TTSProvider,
        retry_limit: int,
        request_pacer: SynthesisRequestPacer | None = None,
        initial_request_paced: bool = False,
        voice_team_hash: str = "",
        provider_call_semaphore: asyncio.Semaphore | None = None,
    ) -> SynthesisResult:
        """Synthesize a segment with retry logic.

        After a quality-gate failure the request is rebuilt with progressive
        parameter adjustments (``_apply_quality_fix_adjustments``) so each
        retry is meaningfully different from the previous attempt.
        """
        # Build initial TTS request
        request = self._build_tts_request(segment, voice_map, provider)
        failure_policy = self._get_failure_policy(provider)

        last_error = ""
        last_decision = TTSFailureDecision(
            TTSFailureKind.INTERNAL,
            retryable=True,
            counts_toward_breaker=False,
            error_code="internal",
        )
        last_attempt = 0
        quality_fix_level = 0  # incremented on each quality-gate failure
        for attempt in range(retry_limit + 1):
            last_attempt = attempt
            # Apply progressive adjustments after quality-gate failures (D2).
            if quality_fix_level > 0:
                request = self._apply_quality_fix_adjustments(request, quality_fix_level)
            try:
                if request_pacer is not None and not initial_request_paced:
                    await request_pacer.acquire()

                # Budget gate: raise before consuming provider quota so a
                # capped account doesn't keep billing after the limit trips.
                if self._spending_tracker is not None:
                    self._spending_tracker.check_budget(voice_team_hash=voice_team_hash)

                async def _call_provider(_request: TTSRequest = request) -> Any:
                    adapter_policy = getattr(adapter, "failure_policy", None)
                    if (
                        isinstance(adapter, FailureManagedTTSAdapter)
                        or adapter_policy is failure_policy
                    ):
                        return await adapter.synthesize(_request)

                    # Bind ``request`` via default arg to avoid B023 (closure
                    # capturing a loop variable).  The closure is called
                    # immediately, but the explicit binding makes the intent
                    # clear and silences the linter.
                    _bound_request = _request

                    async def _provider_call(_req: TTSRequest = _bound_request) -> Any:
                        provider_response = await adapter.synthesize(_req)
                        if not provider_response.audio_data:
                            raise ModelGatewayError(
                                "TTS provider returned empty audio data",
                                is_transient=True,
                            )
                        return provider_response

                    return await failure_policy.execute(_provider_call)

                if provider_call_semaphore is None:
                    response = await _call_provider()
                else:
                    async with provider_call_semaphore:
                        response = await _call_provider()

                actual_voice_id = str(response.voice_id or request.voice_id).strip()
                if request.voice_id and actual_voice_id != request.voice_id:
                    raise TTSProviderFailure(
                        provider.value,
                        TTSFailureDecision(
                            TTSFailureKind.VOICE_IDENTITY,
                            retryable=False,
                            counts_toward_breaker=False,
                            error_code="voice_identity_mismatch",
                        ),
                        (
                            "TTS 平台返回了与请求不一致的音色："
                            f"requested={request.voice_id!r}, actual={actual_voice_id!r}。"
                            "为防止角色中途换人，已拒绝该片段。"
                        ),
                    )

                # Save audio to file
                audio_path = self._get_segment_path(output_dir, segment_idx, response.audio_format)
                response_format = response.audio_format or request.output_format
                async with self._resource_broker.lease(
                    LocalResourceRequest(
                        workload="audio_postprocess",
                        label=f"第 {segment_idx + 1} 段本地声学参数处理",
                        memory_class=LocalMemoryClass.LIGHT,
                        accelerator=False,
                        cpu_heavy=True,
                        priority=LocalResourcePriority.BACKGROUND,
                        timeout_s=float(self._settings.local_model_resource_wait_timeout_s),
                    )
                ):
                    audio_data = await asyncio.to_thread(
                        apply_portable_controls,
                        response.audio_data,
                        request=request,
                        audio_format=response_format,
                    )
                temp_path = audio_path.with_name(f".{audio_path.stem}.tmp{audio_path.suffix}")
                async with self._storage_write_lock:
                    if self._layout is not None:
                        ensure_project_audio_write(
                            layout=self._layout,
                            settings=self._settings,
                            destination=audio_path,
                            incoming_bytes=len(audio_data),
                            artifact=f"第 {segment_idx + 1} 段配音",
                        )
                    temp_path.write_bytes(audio_data)
                # Provider-reported duration is not blindly trusted.  Some
                # providers (observed with MiniMax ``extra_info.audio_length``)
                # return a wrong-unit value (e.g. character count) that is
                # non-zero yet far too small, which previously slipped past
                # ``response.duration_ms or fallback`` and tripped the quality
                # gate's min-duration check on every blind retry.  We now
                # sanity-check the reported value and fall back to a local
                # FFmpeg probe when it is implausible.
                if needs_portable_controls(request):
                    # Local atempo/volume post-processing changes the real
                    # duration of the written file, so the provider value
                    # (which describes pre-controls audio) never applies —
                    # always probe the file we actually wrote.
                    duration_ms = self._probe_duration_ms(temp_path)
                else:
                    trusted_ms = trustworthy_duration_ms(response.duration_ms, request.text)
                    if trusted_ms is not None:
                        duration_ms = trusted_ms
                    else:
                        duration_ms = self._probe_duration_ms(temp_path)
                        if duration_ms <= 0 < response.duration_ms:
                            # Provider gave a value but it failed the trust
                            # check, and the local probe also failed.  Keep the
                            # provider value as a last resort so the quality
                            # gate can make its own decision, but surface the
                            # discrepancy.
                            _log.warning(
                                "Segment %d: provider duration_ms=%d failed trust "
                                "check and local probe returned 0; using provider value",
                                segment_idx,
                                response.duration_ms,
                            )
                            duration_ms = response.duration_ms
                quality_warnings: list[str] = []
                if self._settings.tts_segment_quality_enabled:
                    s = self._settings
                    # Only probe loudness when at least one threshold is tightened
                    # off its disabled default, to avoid a per-segment FFmpeg
                    # subprocess when no advisory check is active.
                    loudness_enabled = (
                        s.tts_segment_min_rms_db > -100.0
                        or s.tts_segment_max_peak_db < 0.0
                        or s.tts_segment_max_silence_ratio < 1.0
                    )
                    decision = evaluate_synthesized_segment(
                        request,
                        response,
                        duration_ms=duration_ms,
                        audio_size_bytes=temp_path.stat().st_size,
                        minimum_duration_ms=s.tts_segment_min_duration_ms,
                        maximum_characters_per_second=s.tts_segment_max_characters_per_second,
                        audio_path=temp_path if loudness_enabled else None,
                        min_rms_db=s.tts_segment_min_rms_db
                        if s.tts_segment_min_rms_db > -100.0
                        else None,
                        max_peak_db=s.tts_segment_max_peak_db
                        if s.tts_segment_max_peak_db < 0.0
                        else None,
                        max_silence_ratio=(
                            s.tts_segment_max_silence_ratio
                            if s.tts_segment_max_silence_ratio < 1.0
                            else None
                        ),
                    )
                    quality_warnings = list(decision.warnings)
                    if not decision.passed:
                        temp_path.unlink(missing_ok=True)
                        raise ValueError("片段质量门未通过：" + " ".join(decision.warnings))
                temp_path.replace(audio_path)

                # ── Multi-take refinement (P3-3) ───────────────────────────
                # Emotionally intense or key-dialogue segments generate
                # additional takes; the best-scoring take wins.  Extra takes
                # are best-effort — any failure leaves the committed take in
                # place.
                takes_needed = take_count(
                    segment,
                    enabled=self._settings.tts_multi_take_enabled,
                    min_intensity=self._settings.tts_multi_take_min_intensity,
                    max_takes=self._settings.tts_multi_take_max_takes,
                )
                if takes_needed > 1:
                    refined = await self._multi_take_refine(
                        adapter=adapter,
                        segment=segment,
                        segment_idx=segment_idx,
                        output_dir=output_dir,
                        provider=provider,
                        request=request,
                        request_pacer=request_pacer,
                        voice_team_hash=voice_team_hash,
                        audio_path=audio_path,
                        first_duration_ms=duration_ms,
                        first_warnings=quality_warnings,
                        extra_takes=takes_needed - 1,
                        provider_call_semaphore=provider_call_semaphore,
                    )
                    if refined is not None:
                        duration_ms, quality_warnings = refined

                result = SynthesisResult(
                    segment_index=segment_idx,
                    audio_path=str(audio_path),
                    duration_ms=duration_ms,
                    status=SynthesisStatus.COMPLETED,
                    provider=provider,
                    model_id=response.model_id,
                    voice_id=actual_voice_id,
                    requested_voice_id=request.voice_id,
                    cost_usd=response.cost_usd,
                    latency_ms=response.latency_ms,
                    retry_count=attempt,
                    request_hash=self._request_hash(segment, voice_map, provider),
                    output_hash=hashlib.sha256(audio_data).hexdigest(),
                    route_plugin_id=str(
                        getattr(self, "_run_ctx", None)
                        and self._run_ctx.route_plugin_id
                        or ""
                        or ""
                    ),
                    route_plugin_version=str(
                        getattr(self, "_run_ctx", None)
                        and self._run_ctx.route_plugin_version
                        or ""
                        or ""
                    ),
                    route_endpoint=str(
                        getattr(self, "_run_ctx", None) and self._run_ctx.route_endpoint or "" or ""
                    ),
                    quality_passed=True,
                    quality_warnings=quality_warnings,
                    provider_metadata={
                        **dict(response.metadata),
                        "execution_plan_id": str(
                            getattr(self, "_run_ctx", None)
                            and self._run_ctx.execution_plan_id
                            or ""
                            or ""
                        ),
                    },
                    take_evidence=response.take_evidence,
                )
                # Record spend after the take is committed to disk. Cached /
                # mock takes (cost_usd == 0) are no-ops inside record().
                if self._spending_tracker is not None:
                    self._spending_tracker.record(
                        response.cost_usd,
                        voice_team_hash=voice_team_hash,
                    )
                return result

            except Exception as exc:
                last_error = str(exc)
                last_decision = (
                    exc.decision
                    if isinstance(exc, TTSProviderFailure)
                    else classify_tts_failure(exc, provider_boundary=False)
                )
                # Detect quality-gate failure to drive progressive adjustment.
                # The quality gate raises ValueError("片段质量门未通过：...") when
                # duration/cps/loudness checks fail.  Incrementing the fix level
                # ensures the next retry uses adjusted parameters (D2 fix).
                # Provider-status failures ("供应商报告非完成状态") are excluded:
                # speed reduction cannot fix a provider-side status issue and
                # only wastes quota (observed: DashScope finish_reason bug).
                if "片段质量门未通过" in last_error and "供应商报告非完成状态" not in last_error:
                    quality_fix_level += 1
                if request_pacer is not None and last_decision.kind == TTSFailureKind.RATE_LIMIT:
                    cooldown_s = max(
                        self._settings.tts_synthesis_rate_limit_cooldown_s,
                        last_decision.retry_after_s,
                    )
                    await request_pacer.defer(cooldown_s)
                    _log.warning(
                        "Segment %d hit provider rate limit; pausing queued TTS requests for %ds",
                        segment_idx,
                        cooldown_s,
                    )
                _log.warning(
                    "Segment %d synthesis attempt %d failed: kind=%s retryable=%s error=%s",
                    segment_idx,
                    attempt + 1,
                    last_decision.kind.value,
                    last_decision.retryable,
                    exc,
                )
                if not last_decision.retryable:
                    break
                if attempt < retry_limit:
                    # Exponential backoff
                    await asyncio.sleep(0.5 * (2**attempt))

        return SynthesisResult(
            segment_index=segment_idx,
            status=SynthesisStatus.FAILED,
            provider=provider,
            requested_voice_id=request.voice_id,
            error_message=last_error,
            failure_kind=last_decision.kind.value,
            retryable=last_decision.retryable,
            retry_after_s=last_decision.retry_after_s,
            retry_count=last_attempt,
            provider_metadata={
                "failure_kind": last_decision.kind.value,
                "provider_error_code": last_decision.error_code,
                "execution_plan_id": str(
                    getattr(self, "_run_ctx", None) and self._run_ctx.execution_plan_id or "" or ""
                ),
            },
            route_plugin_id=str(
                getattr(self, "_run_ctx", None) and self._run_ctx.route_plugin_id or "" or ""
            ),
            route_plugin_version=str(
                getattr(self, "_run_ctx", None) and self._run_ctx.route_plugin_version or "" or ""
            ),
            route_endpoint=str(
                getattr(self, "_run_ctx", None) and self._run_ctx.route_endpoint or "" or ""
            ),
        )

    async def _multi_take_refine(
        self,
        *,
        adapter: TTSProviderAdapter,
        segment: Any,
        segment_idx: int,
        output_dir: Path,
        provider: TTSProvider,
        request: TTSRequest,
        request_pacer: SynthesisRequestPacer | None,
        voice_team_hash: str,
        audio_path: Path,
        first_duration_ms: int,
        first_warnings: list[str],
        extra_takes: int,
        provider_call_semaphore: asyncio.Semaphore | None = None,
    ) -> tuple[int, list[str]] | None:
        """Generate *extra_takes* additional takes and keep the best one.

        The first take is already committed at *audio_path*.  Additional
        takes are synthesized with the same request, written to unique temp
        paths, and scored with duration plausibility + emotion-energy
        consistency (:mod:`tts.pipeline.multi_take`).  When a later take
        scores strictly higher it atomically replaces the committed file.

        Extra takes are best-effort: any failure (provider error, quality
        gate, format mismatch) discards that take and keeps the committed
        one.  Returns the winning ``(duration_ms, warnings)`` or ``None``
        when no extra take could be produced.
        """
        from novel_forge.tts.pipeline.multi_take import (  # noqa: PLC0415
            TakeEvidence,
            select_best_take,
        )
        from novel_forge.tts.pipeline.segment_quality import (
            _measure_segment_loudness,  # noqa: PLC0415
        )

        first_rms, _, _ = _measure_segment_loudness(audio_path)
        candidates: list[tuple[Any, TakeEvidence]] = [
            (
                segment,
                TakeEvidence(
                    audio_path=audio_path,
                    duration_ms=first_duration_ms,
                    quality_warnings=tuple(first_warnings),
                    rms_dbfs=first_rms,
                ),
            )
        ]
        failure_policy = self._get_failure_policy(provider)
        committed_format = audio_path.suffix.lstrip(".")

        # ── Concurrent extra takes (P1-5) ─────────────────────────────────────
        # Additional takes are independent network calls; generate them in
        # parallel (bounded by the pacer rate limit and a small semaphore) so
        # multi-take selection stops serialising the chapter.
        extra_semaphore = asyncio.Semaphore(max(1, min(extra_takes, 2)))

        async def _produce_extra_take(
            take_idx: int,
        ) -> tuple[Any, TakeEvidence] | None:
            """Generate one extra take; returns None when it must be discarded."""
            async with extra_semaphore:
                temp_path = audio_path.with_name(
                    f".{audio_path.stem}.take{take_idx + 2}{audio_path.suffix}"
                )
                try:
                    if request_pacer is not None:
                        await request_pacer.acquire()
                    if self._spending_tracker is not None:
                        self._spending_tracker.check_budget(voice_team_hash=voice_team_hash)

                    async def _call_provider() -> Any:
                        adapter_policy = getattr(adapter, "failure_policy", None)
                        if (
                            isinstance(adapter, FailureManagedTTSAdapter)
                            or adapter_policy is failure_policy
                        ):
                            return await adapter.synthesize(request)

                        _bound_request = request

                        async def _extra_call(_req: TTSRequest = _bound_request) -> Any:
                            provider_response = await adapter.synthesize(_req)
                            if not provider_response.audio_data:
                                raise ModelGatewayError(
                                    "TTS provider returned empty audio data",
                                    is_transient=True,
                                )
                            return provider_response

                        return await failure_policy.execute(_extra_call)

                    if provider_call_semaphore is None:
                        response = await _call_provider()
                    else:
                        async with provider_call_semaphore:
                            response = await _call_provider()

                    actual_voice_id = str(response.voice_id or request.voice_id).strip()
                    if request.voice_id and actual_voice_id != request.voice_id:
                        raise TTSProviderFailure(
                            provider.value,
                            TTSFailureDecision(
                                TTSFailureKind.VOICE_IDENTITY,
                                retryable=False,
                                counts_toward_breaker=False,
                                error_code="voice_identity_mismatch",
                            ),
                            (
                                "TTS 平台返回了与请求不一致的音色："
                                f"requested={request.voice_id!r}, actual={actual_voice_id!r}。"
                                "已丢弃该额外 take。"
                            ),
                        )

                    # 仅保留与已提交 take 同格式的额外 take，避免覆盖后后缀与内容不符。
                    response_format = response.audio_format or request.output_format
                    if (response_format or committed_format) != committed_format:
                        _log.debug(
                            "Segment %d: extra take %d skipped (format %r != %r)",
                            segment_idx,
                            take_idx + 2,
                            response_format,
                            committed_format,
                        )
                        return None

                    async with self._resource_broker.lease(
                        LocalResourceRequest(
                            workload="audio_postprocess",
                            label=f"第 {segment_idx + 1} 段本地声学参数处理（take {take_idx + 2}）",
                            memory_class=LocalMemoryClass.LIGHT,
                            accelerator=False,
                            cpu_heavy=True,
                            priority=LocalResourcePriority.BACKGROUND,
                            timeout_s=float(self._settings.local_model_resource_wait_timeout_s),
                        )
                    ):
                        audio_data = await asyncio.to_thread(
                            apply_portable_controls,
                            response.audio_data,
                            request=request,
                            audio_format=response_format,
                        )
                    async with self._storage_write_lock:
                        if self._layout is not None:
                            ensure_project_audio_write(
                                layout=self._layout,
                                settings=self._settings,
                                destination=temp_path,
                                incoming_bytes=len(audio_data),
                                artifact=f"第 {segment_idx + 1} 段配音（take {take_idx + 2}）",
                            )
                        temp_path.write_bytes(audio_data)
                    if self._spending_tracker is not None:
                        self._spending_tracker.record(
                            response.cost_usd,
                            voice_team_hash=voice_team_hash,
                        )

                    if needs_portable_controls(request):
                        duration_ms = self._probe_duration_ms(temp_path)
                    else:
                        trusted_ms = trustworthy_duration_ms(response.duration_ms, request.text)
                        if trusted_ms is not None:
                            duration_ms = trusted_ms
                        else:
                            duration_ms = self._probe_duration_ms(temp_path)

                    warnings: list[str] = []
                    if self._settings.tts_segment_quality_enabled:
                        s = self._settings
                        loudness_enabled = (
                            s.tts_segment_min_rms_db > -100.0
                            or s.tts_segment_max_peak_db < 0.0
                            or s.tts_segment_max_silence_ratio < 1.0
                        )
                        decision = evaluate_synthesized_segment(
                            request,
                            response,
                            duration_ms=duration_ms,
                            audio_size_bytes=temp_path.stat().st_size,
                            minimum_duration_ms=s.tts_segment_min_duration_ms,
                            maximum_characters_per_second=s.tts_segment_max_characters_per_second,
                            audio_path=temp_path if loudness_enabled else None,
                            min_rms_db=s.tts_segment_min_rms_db
                            if s.tts_segment_min_rms_db > -100.0
                            else None,
                            max_peak_db=s.tts_segment_max_peak_db
                            if s.tts_segment_max_peak_db < 0.0
                            else None,
                            max_silence_ratio=(
                                s.tts_segment_max_silence_ratio
                                if s.tts_segment_max_silence_ratio < 1.0
                                else None
                            ),
                        )
                        warnings = list(decision.warnings)
                        if not decision.passed:
                            temp_path.unlink(missing_ok=True)
                            return None

                    rms, _, _ = _measure_segment_loudness(temp_path)
                    return (
                        segment,
                        TakeEvidence(
                            audio_path=temp_path,
                            duration_ms=duration_ms,
                            quality_warnings=tuple(warnings),
                            rms_dbfs=rms,
                        ),
                    )
                except Exception as exc:
                    _log.warning(
                        "Segment %d: extra take %d/%d failed; keeping committed take: %s",
                        segment_idx,
                        take_idx + 2,
                        extra_takes + 1,
                        exc,
                    )
                    temp_path.unlink(missing_ok=True)
                    return None

        if extra_takes > 0:
            outcomes = await asyncio.gather(
                *(_produce_extra_take(index) for index in range(extra_takes))
            )
            for outcome in outcomes:
                if outcome is not None:
                    candidates.append(outcome)

        if len(candidates) <= 1:
            return None

        _best_segment, best = select_best_take(candidates)
        # 清理未选中的额外 take 临时文件。
        for _cand_segment, evidence in candidates:
            if evidence.audio_path != audio_path and evidence.audio_path != best.audio_path:
                evidence.audio_path.unlink(missing_ok=True)

        if best.audio_path == audio_path:
            _log.debug(
                "Segment %d: multi-take kept the first take (%dms)",
                segment_idx,
                best.duration_ms,
            )
            return (best.duration_ms, list(best.quality_warnings))

        best.audio_path.replace(audio_path)
        _log.info(
            "Segment %d: multi-take selected extra take (%dms, warnings=%d)",
            segment_idx,
            best.duration_ms,
            len(best.quality_warnings),
        )
        return (best.duration_ms, list(best.quality_warnings))

    def _get_failure_policy(self, provider: TTSProvider) -> TTSProviderFailurePolicy:
        """Support lightweight test/custom registries while keeping one shared policy."""

        factory = getattr(self._registry, "get_failure_policy", None)
        if callable(factory):
            candidate = factory(provider)
            if isinstance(candidate, TTSProviderFailurePolicy):
                return candidate
            raise TypeError("get_failure_policy() must return TTSProviderFailurePolicy")
        policy = self._fallback_failure_policies.get(provider.value)
        if policy is None:
            policy = TTSProviderFailurePolicy(
                provider.value,
                failure_threshold=self._settings.circuit_breaker_threshold,
                recovery_timeout_s=self._settings.circuit_breaker_recovery_s,
                enabled=self._settings.circuit_breaker_enabled,
                rate_limit_cooldown_s=self._settings.tts_synthesis_rate_limit_cooldown_s,
            )
            self._fallback_failure_policies[provider.value] = policy
        return policy

    def _build_tts_request(
        self,
        segment: Any,
        voice_map: dict[str, Any],
        provider: TTSProvider,
    ) -> TTSRequest:
        """Build TTS request from segment and voice team.

        Supports dynamic narrator adaptation based on NarratorVoiceProfile.
        """
        # Get narrator profile from input_data if available
        narrator_profile = (
            getattr(self, "_run_ctx", None) and self._run_ctx.narrator_profile or None
        )
        narrator_voice_id = (
            getattr(self, "_run_ctx", None) and self._run_ctx.narrator_voice_id or ""
        )
        synthesis_text = str(getattr(segment, "synthesis_text", "") or segment.text)
        # ``spoken_text`` may expand a one-character source line into a more
        # natural phrase, but it remains a short-source stability case.  Base
        # identity controls must therefore stay neutral even after expansion.
        is_short_utterance = is_short_spoken_utterance(str(segment.text))

        # Determine voice_id and base offsets
        voice_id = ""
        voice_model_id = ""
        speed_offset = 0.0
        pitch_offset = 0
        vol_offset = 0.0
        resolved_performance: ResolvedCharacterPerformance | None = None

        is_character_voice = bool(segment.character_id and segment.character_id in voice_map)
        if is_character_voice:
            # Character dialogue
            entry = voice_map[segment.character_id]
            voice_id = entry.voice_id
            voice_model_id = str(getattr(entry, "model_id", "") or "")
        else:
            # Narration or inner thought - use narrator profile
            voice_id = narrator_voice_id or _DEFAULT_VOICE_BY_PROVIDER.get(
                provider,
                default_tts_voice_id(provider),
            )

            # Apply narrator profile if available
            if narrator_profile:
                voice_id = narrator_profile.voice_id or voice_id
                voice_model_id = str(getattr(narrator_profile, "model_id", "") or "")
                pitch_offset = narrator_profile.pitch_offset
                vol_offset = narrator_profile.vol_offset
                # Apply narration distance to volume
                if narrator_profile.narration_distance == "close":
                    vol_offset += 0.05  # Closer = slightly louder
                elif narrator_profile.narration_distance == "distant":
                    vol_offset -= 0.05  # Distant = slightly quieter

        # Apply segment overrides.  Explicit zero values are meaningful for
        # volume/pitch and must not be replaced by ``or`` defaults.
        if segment.speed_override is not None:
            base_speed = segment.speed_override
        elif narrator_profile and not is_character_voice:
            base_speed = narrator_profile.base_speed
        else:
            base_speed = self._settings.tts_default_speed
        if is_character_voice:
            resolved_performance = resolve_character_performance(
                entry,
                provider,
                text=str(segment.text),
                default_speed=self._settings.tts_default_speed,
                speed_override=segment.speed_override,
                pitch_override=segment.pitch_override,
                vol_override=segment.vol_override,
                emotion=segment.emotion,
                scene_context=str(getattr(segment, "scene_context", "") or ""),
                tone_hint=str(getattr(segment, "tone_hint", "") or ""),
            )
            speed = resolved_performance.speed
            pitch = resolved_performance.pitch
            volume = resolved_performance.volume
            speed_offset = resolved_performance.speed_offset
            pitch_offset = resolved_performance.pitch_offset
            vol_offset = resolved_performance.vol_offset
        else:
            speed = base_speed + speed_offset
            segment_pitch = (
                0
                if is_short_utterance
                else (segment.pitch_override if segment.pitch_override is not None else 0)
            )
            pitch = segment_pitch + pitch_offset
            volume = (
                segment.vol_override if segment.vol_override is not None else 1.0
            ) + vol_offset

        # Apply narrator emotion-based dynamic adaptation.
        # Skip when the expression profile already has a compensation rule for
        # this emotion (the adapter handles it via speed/vol deltas + tags).
        _expr_adapter = getattr(self, "_expression_adapter", None)
        _narrator_emotion_key = (
            (segment.emotion.value if hasattr(segment.emotion, "value") else str(segment.emotion))
            if segment.emotion
            else ""
        )
        _adapter_handles_emotion = (
            _expr_adapter is not None
            and _narrator_emotion_key
            and _expr_adapter.has_compensation_rule(_narrator_emotion_key)
        )
        if (
            narrator_profile
            and not is_character_voice
            and segment.emotion
            and not _adapter_handles_emotion
        ):
            emotion_str = _narrator_emotion_key
            # Apply emotion speed modifier
            if hasattr(narrator_profile, "emotion_speed_modifiers"):
                speed_modifier = narrator_profile.emotion_speed_modifiers.get(emotion_str, 1.0)
                speed *= speed_modifier
            # Apply emotion volume modifier
            if hasattr(narrator_profile, "emotion_volume_modifiers"):
                volume_modifier = narrator_profile.emotion_volume_modifiers.get(emotion_str, 1.0)
                volume *= volume_modifier

        # Clamp values
        speed = max(0.5, min(2.0, speed))
        pitch = max(-12, min(12, pitch))
        volume = max(0.0, min(2.0, volume))

        # Keep the selected voice as the stable identity anchor.  Earlier
        # versions stacked provider emotion, pitch shifts and voice effects,
        # which can make one actor sound like a different person from line to
        # line.  Emotion now shapes cadence/loudness gently; pitch remains a
        # cast-level/manual control.
        emotion_tags: list[str] = []
        is_extreme_short = is_extreme_short_utterance(str(segment.text))
        if not is_character_voice and segment.emotion != EmotionTag.NEUTRAL:
            mapping = EMOTION_MAPPINGS.get(segment.emotion)
            if mapping:
                # Expression scale driven by declarative profile config
                _intensity = float(getattr(segment, "emotion_intensity", 0.5))
                if _expr_adapter is not None:
                    expression_scale = _expr_adapter.resolve_expression_scale(
                        _intensity,
                        is_short=is_short_utterance,
                        is_extreme_short=is_extreme_short,
                    )
                else:
                    # Fallback when adapter unavailable
                    if is_extreme_short:
                        expression_scale = 0.0
                    elif is_short_utterance:
                        expression_scale = 0.10 + 0.20 * _intensity
                    else:
                        expression_scale = 0.15 + 0.35 * _intensity
                speed += mapping.speed_offset * expression_scale
                volume += mapping.vol_offset * expression_scale

        if narrator_profile and not is_character_voice:
            speed = max(
                narrator_profile.speed_range_low, min(narrator_profile.speed_range_high, speed)
            )
        speed = max(0.5, min(2.0, speed))
        pitch = max(-12, min(12, pitch))
        volume = max(0.0, min(2.0, volume))
        selected_model_id = (
            voice_model_id
            or str(getattr(self, "_run_ctx", None) and self._run_ctx.model_id or "" or "")
            or resolve_tts_model(self._settings, provider)
        )

        # ── voice_tags 自动注入 + 情绪降级补偿（P0）──
        # Tags and compensation deltas come from the declarative expression profile.
        _injected_tags: list[ParalinguisticTag] = []
        _compensation_speed_delta = 0.0
        _compensation_vol_delta = 0.0
        if (
            _expr_adapter is not None
            and not segment.paralinguistic_tags
            and segment.emotion != EmotionTag.NEUTRAL
            and not is_extreme_short
        ):
            _emotion_key = (
                segment.emotion.value if hasattr(segment.emotion, "value") else str(segment.emotion)
            )
            _comp = _expr_adapter.compensate_emotion(
                _emotion_key,
                intensity=float(getattr(segment, "emotion_intensity", 0.5)),
                is_short=is_short_utterance,
                is_extreme_short=is_extreme_short,
            )
            _compensation_speed_delta = _comp.speed_delta
            _compensation_vol_delta = _comp.vol_delta
            if _comp.inject_tags:
                from novel_forge.tts.platform.minimax_contract import (
                    minimax_supports_interjections,
                )

                if minimax_supports_interjections(selected_model_id):
                    for idx, tag_str in enumerate(_comp.inject_tags[:2]):
                        tag_type = _MINIMAX_TAG_TO_TYPE.get(tag_str, "")
                        if tag_type:
                            _injected_tags.append(
                                ParalinguisticTag(
                                    tag_type=tag_type,
                                    position=0.0 if idx == 0 else 1.0,
                                )
                            )
        # Apply emotion compensation deltas from the expression profile
        speed += _compensation_speed_delta
        volume += _compensation_vol_delta

        # ── Dialogue coherence gradient (P0) ──
        # Consume the previous segment's emotional state to smooth transitions
        # between consecutive spoken lines.  Only fires for character voices
        # (narration is already smooth due to longer segments and lower
        # emotional variance).
        _coherence_speed, _coherence_vol, _coherence_reason = self._dialogue_coherence_adjustment(
            segment,
            is_character_voice=is_character_voice,
            is_short=is_short_utterance,
            is_extreme_short=is_extreme_short,
        )
        speed += _coherence_speed
        volume += _coherence_vol

        # Determine synthesis text — SSML, emotion-tagged, or plain
        from novel_forge.tts.pipeline.ssml_builder import (
            build_minimax_emotion_text,
            build_ssml,
            should_use_ssml,
        )

        _seg_update: dict[str, Any] = {"text": synthesis_text}
        if _injected_tags:
            _seg_update["paralinguistic_tags"] = _injected_tags
        synthesis_segment = segment.model_copy(update=_seg_update)
        synth_text = synthesis_text
        if should_use_ssml(synthesis_segment, provider):
            synth_text = build_ssml(synthesis_segment, voice_id, provider=provider)
        elif provider == TTSProvider.MINIMAX and (
            synthesis_segment.paralinguistic_tags or synthesis_segment.stress_words
        ):
            synth_text = build_minimax_emotion_text(
                synthesis_segment,
                model_id=selected_model_id,
            )

        platform_extensions = getattr(segment, "platform_extensions", None) or {}
        provider_extension = platform_extensions.get(provider.value, {})
        if not isinstance(provider_extension, dict):
            provider_extension = {}
        # Provider-private controls live in a declarative platform profile.
        # This keeps MiniMax word timing/CBR out of other adapters and gives a
        # future provider one narrow extension point instead of another branch
        # in generic synthesis orchestration.
        provider_extension = {
            **native_synthesis_request_extension(
                provider.value,
                word_subtitles_enabled=self._settings.tts_subtitle_word_level,
                force_cbr_enabled=bool(getattr(self._settings, "tts_minimax_force_cbr", True)),
            ),
            **provider_extension,
        }
        # Derive a chapter-voice-seed: a stable per-(voice-team, chapter)
        # identifier. It does NOT auto-generate timbre_weights (the MiniMax
        # timbre_weights contract has no internal spec or test fixture, so
        # fabricating values risks API rejection or unintended voice drift).
        # Instead it anchors cross-segment consistency: the seed is identical
        # for every segment of the same character in the same chapter, so any
        # downstream consumer (or future timbre_weights population) can key off
        # it deterministically. Users who want explicit timbre_weights still
        # set them via platform_extensions, which take precedence below.
        chapter_voice_seed = ""
        voice_team_hash = (
            getattr(self, "_run_ctx", None) and self._run_ctx.voice_team_hash or "" or ""
        )
        chapter_number = getattr(self, "_run_ctx", None) and self._run_ctx.chapter_number or 0 or 0
        if voice_team_hash:
            chapter_voice_seed = f"{voice_team_hash[:12]}-ch{chapter_number}"
        language_boost = str(
            provider_extension.get("language_boost")
            or provider_language_value(
                provider.value,
                str(getattr(segment, "language_code", "auto") or "auto"),
                str(
                    getattr(segment, "language_boost", "")
                    or getattr(self._settings, "tts_minimax_language_boost", "auto")
                    or "auto"
                ),
            )
        )
        voice_effect = provider_extension.get("voice_effect") or (
            getattr(segment, "voice_effect", None) or {}
        )
        vocal_direction = getattr(segment, "vocal_direction", None)
        dump_direction = getattr(vocal_direction, "model_dump", None)
        vocal_direction_payload = dump_direction(mode="json") if callable(dump_direction) else {}
        if vocal_direction_payload:
            vocal_direction_payload["resonance"] = 0.0
            if is_short_utterance:
                vocal_direction_payload.update(
                    {
                        "delivery_style": "natural",
                        "energy": 0.5,
                        "articulation": 0.65,
                        "breathiness": 0.15,
                        "tension": 0.3,
                        "intimacy": 0.5,
                    }
                )
            # sub_emotion 情感渐变：当次情绪与主情绪不同时，微调 energy/tension
            # Deltas sourced from declarative expression profile.
            sub_emotion = getattr(segment, "sub_emotion", None)
            if sub_emotion is not None and sub_emotion != segment.emotion and not is_extreme_short:
                sub_key = sub_emotion.value if hasattr(sub_emotion, "value") else str(sub_emotion)
                if _expr_adapter is not None:
                    energy_delta, tension_delta = _expr_adapter.resolve_sub_emotion_delta(sub_key)
                else:
                    energy_delta, tension_delta = 0.0, 0.0
                if energy_delta:
                    vocal_direction_payload["energy"] = max(
                        0.0,
                        min(1.0, float(vocal_direction_payload.get("energy", 0.5)) + energy_delta),
                    )
                if tension_delta:
                    vocal_direction_payload["tension"] = max(
                        0.0,
                        min(
                            1.0, float(vocal_direction_payload.get("tension", 0.3)) + tension_delta
                        ),
                    )
        if hasattr(voice_effect, "model_dump"):
            voice_effect = voice_effect.model_dump(mode="json")
        else:
            voice_effect = dict(voice_effect)
        # ``voice_modify`` is a timbre transformer, not an acting control.
        # Preserve explicit spatial/device effects, but keep generated
        # intensity/timbre/pitch neutral so emotion cannot drift identity.
        if not str(voice_effect.get("sound_effects") or ""):
            voice_effect["pitch"] = 0
            voice_effect["intensity"] = 0
            voice_effect["timbre"] = 0

        # 极短句(≤4字符)剥离情绪； moderate 短句(5-8字符)保留情绪但降低强度
        effective_emotion = EmotionTag.NEUTRAL if is_extreme_short else segment.emotion

        # ── P1: tone_hint consumption via expression profile ──
        # Converts tone_hint text into parameter deltas + optional prefix tag.
        _tone_hint_text = str(getattr(segment, "tone_hint", "") or "")
        if _expr_adapter is not None and _tone_hint_text and not is_extreme_short:
            _tone_result = _expr_adapter.resolve_tone_hint(_tone_hint_text)
            if _tone_result.matched:
                speed += _tone_result.speed_delta
                volume += _tone_result.vol_delta
                if (
                    _tone_result.inject_prefix_tag
                    and _tone_result.inject_prefix_tag not in synth_text
                ):
                    synth_text = f"{_tone_result.inject_prefix_tag} {synth_text}"

        # ── P2: VocalDirection bridging via expression profile ──
        # Bridges 6D vocal direction parameters to speed/vol micro-offsets.
        if _expr_adapter is not None and vocal_direction_payload:
            _voice_identity_locked = (
                segment.speed_override is not None or segment.pitch_override is not None
            )
            _bridge_result = _expr_adapter.bridge_vocal_direction(
                vocal_direction_payload,
                is_short=is_short_utterance,
                is_extreme_short=is_extreme_short,
                identity_lock=_voice_identity_locked,
            )
            if _bridge_result.applied:
                speed += _bridge_result.speed_delta
                volume += _bridge_result.vol_delta
                for _btag in _bridge_result.inject_tags:
                    if _btag and _btag not in synth_text:
                        synth_text = f"{_btag} {synth_text}"

        # Final clamp after all expression adaptations
        speed = max(0.5, min(2.0, speed))
        volume = max(0.0, min(2.0, volume))

        return TTSRequest(
            text=synth_text,
            voice_id=voice_id,
            model_id=selected_model_id,
            speed=speed,
            volume=volume,
            pitch=pitch,
            output_format=self._settings.tts_output_format,
            sample_rate=self._settings.tts_sample_rate,
            bitrate=int(getattr(self._settings, "tts_minimax_bitrate", 128000)),
            channel=int(getattr(self._settings, "tts_minimax_channel", 1)),
            emotion=(
                effective_emotion.value
                if hasattr(effective_emotion, "value")
                else str(effective_emotion or "neutral")
            ),
            emotion_tags=emotion_tags,
            pronunciation_overrides=list(getattr(segment, "pronunciation_overrides", None) or []),
            language_boost=language_boost,
            voice_effect=voice_effect,
            provider=provider,
            metadata={
                "resource_priority": "background",
                "resource_label": f"第 {segment.segment_index + 1} 段本地语音合成",
                "tone_hint": str(getattr(segment, "tone_hint", "") or ""),
                "speaker_kind": "角色" if is_character_voice else "旁白",
                "character_name": str(getattr(segment, "character_name", "") or ""),
                "performance_emotion": (
                    segment.emotion.value
                    if hasattr(segment.emotion, "value")
                    else str(segment.emotion or "neutral")
                ),
                "source_text": str(segment.text),
                "spoken_text_used": synthesis_text != str(segment.text),
                "short_utterance_stabilized": bool(
                    resolved_performance.short_utterance_stabilized
                    if resolved_performance is not None
                    else is_short_utterance
                ),
                "performance_offsets_manually_set": bool(
                    resolved_performance and resolved_performance.manual_fields
                ),
                "performance_manual_fields": (
                    list(resolved_performance.manual_fields)
                    if resolved_performance is not None
                    else []
                ),
                "performance_offset_sources": (
                    {
                        "speed": resolved_performance.speed_source,
                        "pitch": resolved_performance.pitch_source,
                        "volume": resolved_performance.volume_source,
                    }
                    if resolved_performance is not None
                    else {}
                ),
                "performance_provider_guard_applied": bool(
                    resolved_performance and resolved_performance.provider_guard_applied
                ),
                "performance_warnings": (
                    list(resolved_performance.warnings) if resolved_performance is not None else []
                ),
                "voice_identity_lock": True,
                "native_emotion_allowed": bool(
                    provider == TTSProvider.MINIMAX
                    and effective_emotion != EmotionTag.NEUTRAL
                    and not is_extreme_short
                ),
                "performance_context": self._performance_context(segment),
                "dialogue_coherence": _coherence_reason,
                "sub_emotion": (
                    segment.sub_emotion.value
                    if getattr(segment, "sub_emotion", None) is not None
                    and hasattr(segment.sub_emotion, "value")
                    else ""
                ),
                "narrator_distance": str(getattr(segment, "narrator_distance", "") or ""),
                "language_code": str(getattr(segment, "language_code", "auto") or "auto"),
                "language_runs": [
                    item.model_dump(mode="json")
                    for item in (getattr(segment, "language_runs", None) or [])
                ],
                "vocal_direction": vocal_direction_payload,
                "vocal_tags": [
                    item.model_dump(mode="json")
                    for item in (
                        [] if is_short_utterance else (getattr(segment, "vocal_tags", None) or [])
                    )
                ],
                "paralinguistic_tags": [
                    item.model_dump(mode="json")
                    for item in (getattr(synthesis_segment, "paralinguistic_tags", None) or [])
                ],
                "platform_extension": provider_extension,
                "chapter_voice_seed": chapter_voice_seed,
                "stress_words": list(getattr(segment, "stress_words", None) or []),
            },
        )

    def _performance_context(self, segment: Any) -> dict[str, str]:
        """Project neighboring lines without adding them to synthesized text."""
        segments = list(
            getattr(self, "_run_ctx", None) and self._run_ctx.context_segments or [] or []
        )
        position = next(
            (
                index
                for index, item in enumerate(segments)
                if item.segment_index == segment.segment_index
            ),
            -1,
        )
        if position < 0:
            return {}

        def project(item: Any) -> str:
            text = str(getattr(item, "synthesis_text", "") or item.text).strip()
            return text[:80]

        previous = segments[position - 1] if position > 0 else None
        following = segments[position + 1] if position + 1 < len(segments) else None
        return {
            "previous_text": project(previous) if previous is not None else "",
            "previous_speaker": (
                str(previous.character_name or "旁白") if previous is not None else ""
            ),
            "next_text": project(following) if following is not None else "",
            "next_speaker": (
                str(following.character_name or "旁白") if following is not None else ""
            ),
        }

    # ── Dialogue coherence: emotion gradient across adjacent segments ─────────
    # When characters exchange dialogue, each segment's emotion is computed
    # independently.  This creates abrupt transitions (e.g. angry → neutral
    # with no settling).  The gradient mechanism dampens the *delta* between
    # consecutive spoken segments so the listener perceives a natural emotional
    # arc rather than a hard cut.

    _DIALOGUE_GRADIENT_DAMPING = 0.45
    """How much of the previous segment's emotional momentum carries into the
    current segment's speed/vol adjustment.  0.0 = fully independent (old
    behaviour), 1.0 = fully inherited.  0.45 gives a perceptible bridge
    without making unrelated speakers sound emotionally coupled."""

    _SPEAKER_SWITCH_SETTLING_SPEED = 0.03
    """Micro speed reduction applied when switching speakers after a
    high-intensity segment, simulating the natural 'beat' before a reply."""

    def _dialogue_coherence_adjustment(
        self,
        segment: Any,
        *,
        is_character_voice: bool,
        is_short: bool,
        is_extreme_short: bool,
    ) -> tuple[float, float, str]:
        """Return (speed_delta, vol_delta, reason) for cross-segment coherence.

        Only applies to spoken segments (dialogue / inner_thought) that are NOT
        extreme-short (those are already stripped of expression).  The adjustment
        is deliberately small so it never overrides the author's explicit
        per-segment controls.
        """
        if not is_character_voice or is_extreme_short:
            return 0.0, 0.0, ""

        segments = list(
            getattr(self, "_run_ctx", None) and self._run_ctx.context_segments or [] or []
        )
        position = next(
            (
                index
                for index, item in enumerate(segments)
                if item.segment_index == segment.segment_index
            ),
            -1,
        )
        if position <= 0:
            return 0.0, 0.0, ""

        previous = segments[position - 1]
        # Only bridge between spoken segments (dialogue/inner_thought).
        spoken_types = {SegmentType.DIALOGUE, SegmentType.INNER_THOUGHT}
        prev_type = getattr(previous, "segment_type", None)
        curr_type = getattr(segment, "segment_type", None)
        if prev_type not in spoken_types or curr_type not in spoken_types:
            return 0.0, 0.0, ""

        prev_emotion = getattr(previous, "emotion", EmotionTag.NEUTRAL)
        curr_emotion = getattr(segment, "emotion", EmotionTag.NEUTRAL)
        prev_intensity = float(getattr(previous, "emotion_intensity", 0.5) or 0.5)
        curr_intensity = float(getattr(segment, "emotion_intensity", 0.5) or 0.5)

        prev_mapping = EMOTION_MAPPINGS.get(prev_emotion)
        curr_mapping = EMOTION_MAPPINGS.get(curr_emotion)
        if prev_mapping is None or curr_mapping is None:
            return 0.0, 0.0, ""

        speed_delta = 0.0
        vol_delta = 0.0
        reasons: list[str] = []

        prev_char_id = str(getattr(previous, "character_id", "") or "")
        curr_char_id = str(getattr(segment, "character_id", "") or "")
        same_speaker = bool(prev_char_id) and prev_char_id == curr_char_id

        # Case 1: Same speaker, emotion changed → dampen the transition.
        # The new emotion's speed/vol offset is partially inherited from the
        # previous emotional state, creating a gradient rather than a step.
        if same_speaker and prev_emotion != curr_emotion and not is_short:
            # Compute the raw delta the emotion mapping would apply.
            raw_speed_delta = curr_mapping.speed_offset - prev_mapping.speed_offset
            raw_vol_delta = curr_mapping.vol_offset - prev_mapping.vol_offset
            # Dampen: move only (1 - damping) of the full delta.
            damping = self._DIALOGUE_GRADIENT_DAMPING * prev_intensity
            speed_delta = -raw_speed_delta * damping * 0.5
            vol_delta = -raw_vol_delta * damping * 0.5
            reasons.append("same_speaker_emotion_gradient")

        # Case 2: Speaker switch after high-intensity → micro settling beat.
        # The new speaker's first line gets a tiny speed reduction so it doesn't
        # burst in at full tempo right after an emotionally charged line.
        if not same_speaker and prev_intensity > 0.6 and curr_intensity < prev_intensity:
            intensity_gap = prev_intensity - curr_intensity
            settling = self._SPEAKER_SWITCH_SETTLING_SPEED * min(1.0, intensity_gap * 2)
            speed_delta -= settling
            reasons.append("speaker_switch_settling")

        # Case 3 (P5): Rapid exchange momentum boost.
        # When both lines are short (< 20 chars) and speakers differ, add a
        # tiny speed boost to maintain conversational momentum — people speak
        # faster in quick back-and-forth exchanges.
        if not same_speaker and not is_short:
            prev_text = str(getattr(previous, "text", "") or "")
            curr_text = str(getattr(segment, "text", "") or "")
            if len(prev_text) <= 20 and len(curr_text) <= 20:
                speed_delta += 0.02
                reasons.append("rapid_exchange_momentum")

        reason = "+".join(reasons) if reasons else ""
        return speed_delta, vol_delta, reason

    def _request_hash(
        self,
        segment: Any,
        voice_map: dict[str, Any],
        provider: TTSProvider,
    ) -> str:
        """Fingerprint the exact text/voice/parameters used for synthesis."""
        request = self._build_tts_request(segment, voice_map, provider)
        request_payload = request.model_dump(mode="json")
        metadata = request_payload.get("metadata")
        if isinstance(metadata, dict):
            # Neighbor context is already covered by the chapter script hash.
            # Excluding it here keeps the per-segment hash deterministic when
            # tooling computes it outside an active chapter run.
            metadata.pop("performance_context", None)
        request_payload["execution_route"] = {
            "plan_id": str(
                getattr(self, "_run_ctx", None) and self._run_ctx.execution_plan_id or "" or ""
            ),
            "plugin_id": str(
                getattr(self, "_run_ctx", None) and self._run_ctx.route_plugin_id or "" or ""
            ),
            "plugin_version": str(
                getattr(self, "_run_ctx", None) and self._run_ctx.route_plugin_version or "" or ""
            ),
            "endpoint": str(
                getattr(self, "_run_ctx", None) and self._run_ctx.route_endpoint or "" or ""
            ),
        }
        payload = json.dumps(request_payload, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def _probe_duration_ms(self, audio_path: Path) -> int:
        """Best-effort duration probe for providers that omit duration metadata.

        Resolution order (cheapest first): ffprobe format probe -> pydub full
        decode -> WAV header parsing.  The ffprobe path reads only the file
        header instead of decoding the whole stream, which keeps the probe
        fast for large segments.
        """
        probed = self._probe_duration_ms_ffprobe(audio_path)
        if probed is not None:
            return probed
        try:
            configure_pydub()
            from pydub import AudioSegment  # type: ignore[import-untyped]

            decoder = {
                ".mp3": "mp3",
                ".wav": "pcm_s16le",
                ".flac": "flac",
                ".aac": "aac",
                ".m4a": "aac",
                ".ogg": "vorbis",
            }.get(audio_path.suffix.lower(), "mp3")
            return len(AudioSegment.from_file(str(audio_path), codec=decoder))
        except Exception:
            pass
        if audio_path.suffix.lower() == ".wav":
            try:
                with wave.open(str(audio_path), "rb") as handle:
                    frames = handle.getnframes()
                    rate = handle.getframerate()
                    return int(frames * 1000 / rate) if rate else 0
            except (OSError, wave.Error, ZeroDivisionError):
                return 0
        return 0

    def _probe_duration_ms_ffprobe(self, audio_path: Path) -> int | None:
        """Read duration with ffprobe (header-only, no full decode)."""
        ffprobe = self._ffprobe_executable()
        if ffprobe is None:
            return None
        try:
            result = subprocess.run(
                [
                    ffprobe,
                    "-v",
                    "quiet",
                    "-show_entries",
                    "format=duration",
                    "-of",
                    "csv=p=0",
                    str(audio_path),
                ],
                capture_output=True,
                text=True,
                timeout=10.0,
                check=False,
            )
            if result.returncode != 0:
                return None
            seconds = float(result.stdout.strip())
            return max(0, round(seconds * 1000))
        except (ValueError, OSError, subprocess.SubprocessError, TimeoutError):
            return None

    @staticmethod
    def _ffprobe_executable() -> str | None:
        """Resolve ffprobe: PATH first, then the bundled ffmpeg directory."""
        import shutil

        on_path = shutil.which("ffprobe")
        if on_path:
            return on_path
        try:
            sibling = Path(ffmpeg_executable()).with_name("ffprobe")
            if sibling.is_file():
                return str(sibling)
        except (ImportError, RuntimeError, OSError):
            pass
        return None

    def _get_output_dir(self, chapter_number: int) -> Path:
        """Get output directory for chapter audio."""
        if self._layout:
            return self._layout.tts_audio_dir(chapter_number)
        raise RuntimeError("正式音频写入必须提供 ProjectLayout 或显式 output_dir")

    def _get_segment_path(
        self, output_dir: Path, segment_idx: int, response_format: str = ""
    ) -> Path:
        """Get path for segment audio file."""
        output_format = (
            str(response_format or self._settings.tts_output_format or "mp3").strip().lower()
        )
        if output_format not in {"mp3", "wav", "flac", "pcm"}:
            output_format = "mp3"
        return output_dir / f"seg_{segment_idx:04d}.{output_format}"

    async def _persist_progress(
        self,
        input_data: SynthesizeAudioInput,
        result: SynthesisResult,
    ) -> None:
        """Persist synthesis progress for resume support."""
        if not self._layout:
            return
        try:
            from novel_forge.tts.schemas import TTSProgressState

            progress_path_factory = getattr(
                self._layout,
                "tts_progress_path_for_chapter",
                None,
            )
            progress_path = (
                progress_path_factory(input_data.chapter_number)
                if callable(progress_path_factory)
                else self._layout.tts_progress_path
            )
            progress_path.parent.mkdir(parents=True, exist_ok=True)

            # Load existing progress
            existing: TTSProgressState | None = None
            if progress_path.exists():
                data = json.loads(progress_path.read_text(encoding="utf-8"))
                existing = TTSProgressState.model_validate(data)

            checkpoint_matches = bool(
                existing is not None
                and existing.chapter_number == input_data.chapter_number
                and existing.voice_team_hash == input_data.voice_team_hash
                and existing.script_hash == input_data.script_hash
                and existing.provider == input_data.provider
            )
            preserve_reusable = bool(
                existing is not None
                and existing.voice_team_hash == input_data.voice_team_hash
                and existing.provider == input_data.provider
            )
            if not checkpoint_matches:
                if not preserve_reusable:
                    existing = TTSProgressState(
                        chapter_number=input_data.chapter_number,
                        voice_team_done=True,
                        script_done=True,
                        voice_team_hash=input_data.voice_team_hash,
                        script_hash=input_data.script_hash,
                        provider=input_data.provider,
                    )
                else:
                    assert existing is not None
                    existing.chapter_number = input_data.chapter_number
                    existing.voice_team_done = True
                    existing.script_done = True
                    existing.script_hash = input_data.script_hash
                    existing.completed_segments = []
                    existing.completed_segment_hashes = {}
                    existing.segment_results = []
                    existing.last_error = ""
            assert existing is not None

            existing.voice_team_hash = input_data.voice_team_hash
            existing.script_hash = input_data.script_hash
            existing.provider = input_data.provider

            segment_idx = result.segment_index

            # Add completed segment and its full audit record.  This write runs
            # immediately after the audio file is atomically promoted, so a
            # process crash cannot leave only an index/hash while losing cost,
            # duration, quality and provider trace evidence.
            if segment_idx not in existing.completed_segments:
                existing.completed_segments.append(segment_idx)
            existing.segment_results = [
                item for item in existing.segment_results if item.segment_index != segment_idx
            ]
            existing.segment_results.append(result)
            existing.segment_results.sort(key=lambda item: item.segment_index)

            # Store a request fingerprint as well as the index.  The executor
            # invalidates the whole checkpoint on script/team/provider changes;
            # this per-segment map is useful for diagnostics and future partial
            # recovery tooling.
            segment = next(
                (item for item in input_data.script.segments if item.segment_index == segment_idx),
                None,
            )
            if segment is None:
                raise ValueError(f"Unknown script segment index: {segment_idx}")
            voice_map = {
                entry.character_id: entry
                for entry in input_data.voice_team.entries
                if entry.is_ready and not entry.is_expired and entry.voice_id
            }
            existing.completed_segment_hashes[str(segment_idx)] = self._request_hash(
                segment,
                voice_map,
                input_data.provider,
            )
            # Index by content identity for reuse across script edits.
            if segment.segment_uid and result.audio_path:
                from novel_forge.tts.schemas import ReusableTakeRecord

                existing.reusable_takes[segment.segment_uid] = ReusableTakeRecord(
                    segment_uid=segment.segment_uid,
                    segment_index_at_creation=segment_idx,
                    audio_path=str(result.audio_path),
                    duration_ms=result.duration_ms,
                    request_hash=result.request_hash,
                    voice_id=result.voice_id,
                    output_hash=result.output_hash,
                    cost_usd=result.cost_usd,
                )
            atomic_write_json(progress_path, existing.model_dump(mode="json"))
        except Exception as exc:
            _log.warning("Failed to persist TTS progress: %s", exc)
