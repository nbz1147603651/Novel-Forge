"""Tests for TTS synthesize audio step — resume, retry, concurrency."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

import novel_forge.tts.pipeline.synthesize_audio_step as synthesize_audio_module
from novel_forge.core.config import Settings
from novel_forge.core.exceptions import AuthenticationError, RateLimitError
from novel_forge.persistence.models import ProjectLayout
from novel_forge.tts.gateway.adapters.mock_adapter import MockTTSAdapter
from novel_forge.tts.gateway.factory import TTSAdapterRegistry
from novel_forge.tts.pipeline.synthesize_audio_step import (
    SynthesizeAudioInput,
    SynthesizeAudioStep,
    _effective_synthesis_concurrency,
)
from novel_forge.tts.schemas import (
    DubbingScript,
    DubbingSegment,
    EmotionTag,
    NarratorVoiceProfile,
    ParalinguisticTag,
    SegmentType,
    SynthesisResult,
    SynthesisStatus,
    TTSProgressState,
    TTSProvider,
    TTSResponse,
    VoiceCastEntry,
    VoiceCloneStatus,
    VoiceTeamContract,
)
from tests.helpers.faux_tts_adapter import FauxTTSAdapter, FauxTTSStep


@pytest.fixture
def settings() -> Settings:
    return Settings()


@pytest.fixture
def registry(settings: Settings) -> TTSAdapterRegistry:
    TTSAdapterRegistry.reset_instance()
    reg = TTSAdapterRegistry.get_instance(settings)
    yield reg
    TTSAdapterRegistry.reset_instance()


@pytest.fixture
def voice_team() -> VoiceTeamContract:
    return VoiceTeamContract(
        entries=[
            VoiceCastEntry(
                character_id="c1",
                character_name="Alice",
                voice_id="test-voice",
                clone_status=VoiceCloneStatus.READY,
            ),
        ]
    )


@pytest.fixture
def script() -> DubbingScript:
    return DubbingScript(
        chapter_number=1,
        segments=[
            DubbingSegment(
                segment_index=0,
                segment_type=SegmentType.NARRATION,
                text="旁白文本",
            ),
            DubbingSegment(
                segment_index=1,
                segment_type=SegmentType.DIALOGUE,
                character_id="c1",
                character_name="Alice",
                text="角色对白",
                emotion=EmotionTag.HAPPY,
            ),
            DubbingSegment(
                segment_index=2,
                segment_type=SegmentType.SILENCE,
                text="...",
            ),
        ],
    )


class TestSynthesizeAudioStep:
    """Test synthesis step execution."""

    async def test_basic_synthesis(
        self,
        registry: TTSAdapterRegistry,
        settings: Settings,
        voice_team: VoiceTeamContract,
        script: DubbingScript,
        tmp_path: Path,
    ) -> None:
        step = SynthesizeAudioStep(registry, settings=settings)
        input_data = SynthesizeAudioInput(
            chapter_number=1,
            script=script,
            voice_team=voice_team,
            provider=TTSProvider.MOCK,
            output_dir=tmp_path,
        )
        results = await step.run(input_data)

        assert len(results) == 3
        # Narration + dialogue should be completed, silence skipped
        assert results[0].status == SynthesisStatus.COMPLETED
        assert results[1].status == SynthesisStatus.COMPLETED
        assert results[2].status == SynthesisStatus.SKIPPED

    async def test_provider_voice_id_mismatch_is_rejected_before_audio_is_saved(
        self,
        tmp_path: Path,
    ) -> None:
        adapter = MockTTSAdapter(latency_ms=0)
        adapter.synthesize = AsyncMock(
            return_value=TTSResponse(
                audio_data=b"not-the-requested-voice",
                voice_id="unexpected-provider-fallback",
                model_id="mock-model",
            )
        )

        class Registry:
            def get_adapter(self, provider: TTSProvider) -> MockTTSAdapter:
                assert provider == TTSProvider.MOCK
                return adapter

        team = VoiceTeamContract(
            entries=[
                VoiceCastEntry(
                    character_id="c1",
                    character_name="Alice",
                    voice_id="locked-alice",
                    provider=TTSProvider.MOCK,
                    clone_status=VoiceCloneStatus.READY,
                    identity_locked=True,
                )
            ]
        )
        script = DubbingScript(
            chapter_number=1,
            segments=[
                DubbingSegment(
                    segment_index=0,
                    segment_type=SegmentType.DIALOGUE,
                    character_id="c1",
                    character_name="Alice",
                    text="别动。",
                )
            ],
        )
        step = SynthesizeAudioStep(Registry(), settings=Settings(_env_file=None))

        results = await step.run(
            SynthesizeAudioInput(
                chapter_number=1,
                script=script,
                voice_team=team,
                provider=TTSProvider.MOCK,
                output_dir=tmp_path,
                retry_limit=3,
            )
        )

        assert results[0].status == SynthesisStatus.FAILED
        assert results[0].failure_kind == "voice_identity"
        assert results[0].retry_count == 0
        assert not list(tmp_path.glob("segment_*"))

    def test_heavy_local_providers_are_forced_to_one_inference(self) -> None:
        assert _effective_synthesis_concurrency(TTSProvider.QWEN3, 8) == 1
        assert _effective_synthesis_concurrency(TTSProvider.COSYVOICE, 4) == 1
        assert _effective_synthesis_concurrency(TTSProvider.OPENVOICE, 4) == 1
        assert _effective_synthesis_concurrency(TTSProvider.MINIMAX, 4) == 4

    def test_project_default_speed_reaches_tts_request(
        self,
        registry: TTSAdapterRegistry,
        voice_team: VoiceTeamContract,
    ) -> None:
        configured = Settings(tts_default_speed=1.2)
        step = SynthesizeAudioStep(registry, settings=configured)
        segment = DubbingSegment(
            segment_index=0,
            segment_type=SegmentType.NARRATION,
            text="旁白文本",
        )

        request = step._build_tts_request(segment, {}, TTSProvider.MOCK)

        assert request.speed == 1.2

    def test_capability_plan_model_override_reaches_tts_request(
        self,
        registry: TTSAdapterRegistry,
    ) -> None:
        step = SynthesizeAudioStep(registry, settings=Settings())
        step._current_model_id = "plugin-selected-model"
        segment = DubbingSegment(
            segment_index=0,
            segment_type=SegmentType.NARRATION,
            text="旁白文本",
        )

        request = step._build_tts_request(segment, {}, TTSProvider.MOCK)

        assert request.model_id == "plugin-selected-model"

    def test_short_spoken_text_is_used_with_identity_stabilization(
        self,
        registry: TTSAdapterRegistry,
        voice_team: VoiceTeamContract,
    ) -> None:
        step = SynthesizeAudioStep(registry, settings=Settings())
        segment = DubbingSegment(
            segment_index=0,
            segment_type=SegmentType.DIALOGUE,
            character_id="c1",
            character_name="Alice",
            text="坐。",
            spoken_text="坐下说吧。",
            emotion=EmotionTag.SURPRISED,
            pitch_override=4,
            voice_effect={"pitch": 30, "intensity": 60, "timbre": -40},
        )
        voice_map = {entry.character_id: entry for entry in voice_team.entries}

        request = step._build_tts_request(segment, voice_map, TTSProvider.MINIMAX)

        assert request.text == "坐下说吧。"
        assert request.emotion == EmotionTag.NEUTRAL.value
        assert request.pitch == 0
        assert request.voice_effect.pitch == 0
        assert request.voice_effect.intensity == 0
        assert request.voice_effect.timbre == 0
        assert request.metadata["spoken_text_used"] is True
        assert request.metadata["voice_identity_lock"] is True
        assert request.metadata["short_utterance_stabilized"] is True

    def test_one_character_take_suppresses_emotion_and_segment_pitch(
        self,
        registry: TTSAdapterRegistry,
        voice_team: VoiceTeamContract,
    ) -> None:
        step = SynthesizeAudioStep(registry, settings=Settings())
        segment = DubbingSegment(
            segment_index=0,
            segment_type=SegmentType.DIALOGUE,
            character_id="c1",
            text="坐。",
            emotion=EmotionTag.ANGRY,
            pitch_override=4,
        )
        voice_map = {entry.character_id: entry for entry in voice_team.entries}

        request = step._build_tts_request(segment, voice_map, TTSProvider.MINIMAX)

        assert request.emotion == EmotionTag.NEUTRAL.value
        assert request.pitch == 0
        assert request.metadata["performance_emotion"] == EmotionTag.ANGRY.value
        assert request.metadata["short_utterance_stabilized"] is True

    def test_automatic_character_offsets_are_stabilized_for_minimax_short_dialogue(
        self,
        registry: TTSAdapterRegistry,
        voice_team: VoiceTeamContract,
    ) -> None:
        step = SynthesizeAudioStep(registry, settings=Settings(tts_default_speed=1.0))
        entry = voice_team.entries[0].model_copy(update={"speed_offset": 0.2, "pitch_offset": 4})
        segment = DubbingSegment(
            segment_index=0,
            segment_type=SegmentType.DIALOGUE,
            character_id="c1",
            text="是，我来了。",
        )

        request = step._build_tts_request(
            segment,
            {entry.character_id: entry},
            TTSProvider.MINIMAX,
        )

        assert request.speed == 1.0
        assert request.pitch == 0
        assert request.metadata["short_utterance_stabilized"] is True
        assert request.metadata["performance_offsets_manually_set"] is False

    def test_explicit_character_offsets_remain_exact_for_minimax(
        self,
        registry: TTSAdapterRegistry,
        voice_team: VoiceTeamContract,
    ) -> None:
        step = SynthesizeAudioStep(registry, settings=Settings(tts_default_speed=1.0))
        entry = voice_team.entries[0].model_copy(
            update={
                "speed_offset": 0.2,
                "pitch_offset": 4,
                "performance_offsets_manually_set": True,
            }
        )
        segment = DubbingSegment(
            segment_index=0,
            segment_type=SegmentType.DIALOGUE,
            character_id="c1",
            text="这是一句足够长的角色对白用于验证人工参数。",
        )

        request = step._build_tts_request(
            segment,
            {entry.character_id: entry},
            TTSProvider.MINIMAX,
        )

        assert request.speed == 1.2
        assert request.pitch == 4
        assert request.metadata["performance_offsets_manually_set"] is True

    def test_minimax_uses_native_emotion_without_automatic_slowdown(
        self,
        registry: TTSAdapterRegistry,
        voice_team: VoiceTeamContract,
    ) -> None:
        step = SynthesizeAudioStep(registry, settings=Settings(tts_default_speed=1.0))
        entry = voice_team.entries[0]
        segment = DubbingSegment(
            segment_index=0,
            segment_type=SegmentType.DIALOGUE,
            character_id="c1",
            text="这是一句有足够上下文的沉重对白。",
            emotion=EmotionTag.SAD,
        )

        request = step._build_tts_request(
            segment,
            {entry.character_id: entry},
            TTSProvider.MINIMAX,
        )

        assert request.speed == 1.0
        assert request.emotion == EmotionTag.SAD.value
        assert request.metadata["native_emotion_allowed"] is True

    def test_minimax_emits_documented_pause_markup_for_current_speech_models(
        self,
        registry: TTSAdapterRegistry,
    ) -> None:
        segment = DubbingSegment(
            segment_index=0,
            segment_type=SegmentType.NARRATION,
            text="门开了。",
            paralinguistic_tags=[
                ParalinguisticTag(tag_type="pause", position=0.5, duration_ms=350)
            ],
        )
        step = SynthesizeAudioStep(registry, settings=Settings())
        step._current_model_id = "speech-2.6-hd"

        legacy_request = step._build_tts_request(segment, {}, TTSProvider.MINIMAX)

        step._current_model_id = "speech-2.8-hd"
        speech_28_request = step._build_tts_request(segment, {}, TTSProvider.MINIMAX)

        assert legacy_request.text == "门开<#0.35#>了。"
        assert speech_28_request.text == "门开<#0.35#>了。"

    def test_narrator_profile_controls_are_exact(
        self,
        registry: TTSAdapterRegistry,
    ) -> None:
        step = SynthesizeAudioStep(registry, settings=Settings(tts_default_speed=1.2))
        step._current_narrator_profile = NarratorVoiceProfile(
            voice_id="narrator",
            provider=TTSProvider.MOCK,
            base_speed=0.9,
            pitch_offset=2,
            vol_offset=-0.15,
        )
        segment = DubbingSegment(
            segment_index=0,
            segment_type=SegmentType.NARRATION,
            text="旁白文本",
        )

        request = step._build_tts_request(segment, {}, TTSProvider.MOCK)

        assert request.speed == 0.9
        assert request.pitch == 2
        assert request.volume == 0.85

    async def test_resume_skips_completed(
        self,
        registry: TTSAdapterRegistry,
        settings: Settings,
        voice_team: VoiceTeamContract,
        script: DubbingScript,
        tmp_path: Path,
    ) -> None:
        # Pre-create segment 0 file
        seg_file = tmp_path / "seg_0000.mp3"
        seg_file.write_bytes(b"fake audio")

        step = SynthesizeAudioStep(registry, settings=settings)
        voice_map = {entry.character_id: entry for entry in voice_team.entries}
        segment_hash = step._request_hash(script.segments[0], voice_map, TTSProvider.MOCK)
        input_data = SynthesizeAudioInput(
            chapter_number=1,
            script=script,
            voice_team=voice_team,
            provider=TTSProvider.MOCK,
            output_dir=tmp_path,
            completed_segments=[0],
            completed_segment_hashes={"0": segment_hash},
        )
        results = await step.run(input_data)

        # Segment 0 should be marked completed (from resume)
        assert results[0].status == SynthesisStatus.COMPLETED
        assert results[0].audio_path == str(seg_file)

    async def test_resume_reuses_only_segments_with_matching_fingerprints(
        self,
        registry: TTSAdapterRegistry,
        settings: Settings,
        voice_team: VoiceTeamContract,
        script: DubbingScript,
        tmp_path: Path,
    ) -> None:
        """A partial checkpoint must preserve good audio and synthesize only the gap."""
        first_segment_file = tmp_path / "seg_0000.mp3"
        first_segment_file.write_bytes(b"previously completed audio")
        step = SynthesizeAudioStep(registry, settings=settings)
        voice_map = {entry.character_id: entry for entry in voice_team.entries}
        first_hash = step._request_hash(script.segments[0], voice_map, TTSProvider.MOCK)
        progress_calls: list[tuple[int, str]] = []

        results = await step.run(
            SynthesizeAudioInput(
                chapter_number=1,
                script=script,
                voice_team=voice_team,
                provider=TTSProvider.MOCK,
                output_dir=tmp_path,
                completed_segments=[0],
                completed_segment_hashes={"0": first_hash},
                on_segment_progress=lambda index, status: progress_calls.append((index, status)),
            )
        )

        assert results[0].audio_path == str(first_segment_file)
        assert (0, "synthesizing") not in progress_calls
        assert (1, "synthesizing") in progress_calls
        assert Path(results[1].audio_path).exists()

    async def test_resume_preserves_full_provider_evidence(
        self,
        registry: TTSAdapterRegistry,
        settings: Settings,
        voice_team: VoiceTeamContract,
        script: DubbingScript,
        tmp_path: Path,
    ) -> None:
        seg_file = tmp_path / "seg_0000.mp3"
        seg_file.write_bytes(b"previously completed audio")
        step = SynthesizeAudioStep(registry, settings=settings)
        voice_map = {entry.character_id: entry for entry in voice_team.entries}
        request_hash = step._request_hash(script.segments[0], voice_map, TTSProvider.MOCK)
        prior = SynthesisResult(
            segment_index=0,
            status=SynthesisStatus.COMPLETED,
            audio_path=str(seg_file),
            duration_ms=4321,
            provider=TTSProvider.MOCK,
            model_id="audited-model",
            voice_id="audited-voice",
            cost_usd=0.42,
            request_hash=request_hash,
            provider_metadata={"trace_id": "trace-1"},
        )

        results = await step.run(
            SynthesizeAudioInput(
                chapter_number=1,
                script=script,
                voice_team=voice_team,
                provider=TTSProvider.MOCK,
                output_dir=tmp_path,
                completed_segments=[0],
                completed_segment_hashes={"0": request_hash},
                completed_results=[prior],
            )
        )

        assert results[0].duration_ms == 4321
        assert results[0].cost_usd == 0.42
        assert results[0].model_id == "audited-model"
        assert results[0].provider_metadata == {"trace_id": "trace-1"}

    async def test_incremental_checkpoint_persists_full_result_evidence(
        self,
        registry: TTSAdapterRegistry,
        settings: Settings,
        voice_team: VoiceTeamContract,
        tmp_path: Path,
    ) -> None:
        layout = ProjectLayout(tmp_path)
        layout.ensure_dirs()
        script = DubbingScript(
            chapter_number=1,
            segments=[
                DubbingSegment(
                    segment_index=0,
                    segment_type=SegmentType.NARRATION,
                    text="需要原子保存证据的旁白。",
                )
            ],
        )

        result = (
            await SynthesizeAudioStep(registry, settings=settings, layout=layout).run(
                SynthesizeAudioInput(
                    chapter_number=1,
                    script=script,
                    voice_team=voice_team,
                    provider=TTSProvider.MOCK,
                    voice_team_hash="team-hash",
                    script_hash="script-hash",
                )
            )
        )[0]
        checkpoint = TTSProgressState.model_validate_json(
            layout.tts_progress_path_for_chapter(1).read_text(encoding="utf-8")
        )

        assert checkpoint.completed_segments == [0]
        assert checkpoint.segment_results[0].request_hash == result.request_hash
        assert checkpoint.segment_results[0].duration_ms == result.duration_ms
        assert checkpoint.segment_results[0].model_id == result.model_id
        assert checkpoint.segment_results[0].voice_id == result.voice_id

    async def test_dialogue_without_ready_cast_fails_instead_of_using_narrator(
        self,
        registry: TTSAdapterRegistry,
        settings: Settings,
        tmp_path: Path,
    ) -> None:
        script = DubbingScript(
            chapter_number=1,
            segments=[
                DubbingSegment(
                    segment_index=0,
                    segment_type=SegmentType.DIALOGUE,
                    character_id="missing-character",
                    character_name="失配角色",
                    text="这句不能由旁白代读。",
                )
            ],
        )

        results = await SynthesizeAudioStep(registry, settings=settings).run(
            SynthesizeAudioInput(
                chapter_number=1,
                script=script,
                voice_team=VoiceTeamContract(entries=[]),
                provider=TTSProvider.MOCK,
                output_dir=tmp_path,
            )
        )

        assert results[0].status == SynthesisStatus.FAILED
        assert "No ready voice" in results[0].error_message
        assert not list(tmp_path.glob("seg_*.mp3"))

    async def test_resume_rejects_changed_request_fingerprint(
        self,
        registry: TTSAdapterRegistry,
        settings: Settings,
        voice_team: VoiceTeamContract,
        script: DubbingScript,
        tmp_path: Path,
    ) -> None:
        seg_file = tmp_path / "seg_0000.mp3"
        seg_file.write_bytes(b"stale audio")
        progress_calls: list[tuple[int, str]] = []

        step = SynthesizeAudioStep(registry, settings=settings)
        results = await step.run(
            SynthesizeAudioInput(
                chapter_number=1,
                script=script,
                voice_team=voice_team,
                provider=TTSProvider.MOCK,
                output_dir=tmp_path,
                completed_segments=[0],
                completed_segment_hashes={"0": "outdated-request"},
                on_segment_progress=lambda index, status: progress_calls.append((index, status)),
            )
        )

        assert results[0].status == SynthesisStatus.COMPLETED
        assert (0, "synthesizing") in progress_calls
        assert results[0].request_hash != "outdated-request"

    async def test_segment_extension_matches_output_format(
        self,
        registry: TTSAdapterRegistry,
        settings: Settings,
        voice_team: VoiceTeamContract,
        script: DubbingScript,
        tmp_path: Path,
    ) -> None:
        wav_settings = settings.model_copy(update={"tts_output_format": "wav"})
        step = SynthesizeAudioStep(registry, settings=wav_settings)

        results = await step.run(
            SynthesizeAudioInput(
                chapter_number=1,
                script=script,
                voice_team=voice_team,
                provider=TTSProvider.MOCK,
                output_dir=tmp_path,
            )
        )

        assert Path(results[0].audio_path).suffix == ".wav"
        assert Path(results[1].audio_path).suffix == ".wav"

    async def test_segment_progress_callback(
        self,
        registry: TTSAdapterRegistry,
        settings: Settings,
        voice_team: VoiceTeamContract,
        script: DubbingScript,
        tmp_path: Path,
    ) -> None:
        progress_calls: list[tuple[int, str]] = []

        def on_progress(idx: int, status: str) -> None:
            progress_calls.append((idx, status))

        step = SynthesizeAudioStep(registry, settings=settings)
        input_data = SynthesizeAudioInput(
            chapter_number=1,
            script=script,
            voice_team=voice_team,
            provider=TTSProvider.MOCK,
            output_dir=tmp_path,
            on_segment_progress=on_progress,
        )
        await step.run(input_data)

        # 2 narration segments: each gets 'synthesizing' + 'completed' = 4 calls
        # 1 skipped segment: gets 'skipped' = 1 call
        # Total = 5 calls
        assert len(progress_calls) == 5
        statuses = {s for _, s in progress_calls}
        assert "synthesizing" in statuses
        assert "completed" in statuses
        assert "skipped" in statuses

    async def test_concurrency_limit(
        self,
        registry: TTSAdapterRegistry,
        settings: Settings,
        voice_team: VoiceTeamContract,
        tmp_path: Path,
    ) -> None:
        # Create a script with many segments
        segments = [
            DubbingSegment(segment_index=i, segment_type=SegmentType.NARRATION, text=f"文本{i}")
            for i in range(10)
        ]
        script = DubbingScript(chapter_number=1, segments=segments)

        step = SynthesizeAudioStep(registry, settings=settings)
        input_data = SynthesizeAudioInput(
            chapter_number=1,
            script=script,
            voice_team=voice_team,
            provider=TTSProvider.MOCK,
            output_dir=tmp_path,
            max_concurrent=2,
        )
        results = await step.run(input_data)

        assert len(results) == 10
        assert all(r.status == SynthesisStatus.COMPLETED for r in results)

    async def test_remote_synthesis_paces_retries_after_rate_limit(
        self,
        voice_team: VoiceTeamContract,
        script: DubbingScript,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Remote retries must consume paced slots and defer the shared queue."""
        # First synthesize() raises RateLimitError; every later call falls
        # back to the inherited MockTTSAdapter happy path (valid mock audio).
        adapter = FauxTTSAdapter(
            synthesize_steps=[FauxTTSStep(error=RateLimitError("provider rate limit exceeded"))],
            latency_ms=0,
            fallback_after_exhausted=True,
        )

        class Registry:
            def get_adapter(self, provider: TTSProvider) -> MockTTSAdapter:
                assert provider == TTSProvider.MINIMAX
                return adapter

        class RecordingPacer:
            def __init__(self) -> None:
                self.acquire_calls = 0
                self.cooldowns: list[int] = []

            async def acquire(self) -> None:
                self.acquire_calls += 1

            async def defer(self, cooldown_s: int) -> None:
                self.cooldowns.append(cooldown_s)

        pacer = RecordingPacer()
        monkeypatch.setattr(
            synthesize_audio_module,
            "SynthesisRequestPacer",
            lambda _rpm: pacer,
        )
        settings = Settings(tts_synthesis_rate_limit_cooldown_s=17)
        step = SynthesizeAudioStep(Registry(), settings=settings)
        minimax_team = voice_team.model_copy(
            update={
                "entries": [
                    voice_team.entries[0].model_copy(
                        update={"fallback_voice_ids": {"minimax": "test-voice"}}
                    )
                ]
            }
        )

        # The shared script fixture marks the dialogue HAPPY, which would
        # trigger the P3 multi-take refinement (an extra paced request).
        # Neutralize it so this test measures exactly the pacing semantics:
        # 1 failed + 1 in-segment retry + 1 dialogue = 3 acquired slots.
        neutral_script = script.model_copy(
            update={
                "segments": [
                    seg.model_copy(update={"emotion": EmotionTag.NEUTRAL})
                    if seg.segment_type == SegmentType.DIALOGUE
                    else seg
                    for seg in script.segments
                ]
            }
        )

        results = await step.run(
            SynthesizeAudioInput(
                chapter_number=1,
                script=neutral_script,
                voice_team=minimax_team,
                provider=TTSProvider.MINIMAX,
                output_dir=tmp_path,
                max_concurrent=1,
                retry_limit=1,
            )
        )

        assert all(result.status != SynthesisStatus.FAILED for result in results)
        assert any(
            result.used_fallback and result.fallback_reason == "provider_scoped_voice_mapping"
            for result in results
        )
        assert pacer.acquire_calls == 3
        assert pacer.cooldowns == [17]

    async def test_authentication_failure_is_not_retried(
        self,
        voice_team: VoiceTeamContract,
        tmp_path: Path,
    ) -> None:
        adapter = MockTTSAdapter(latency_ms=0)
        adapter.synthesize = AsyncMock(side_effect=AuthenticationError("invalid api key"))

        class Registry:
            def get_adapter(self, _provider: TTSProvider) -> MockTTSAdapter:
                return adapter

        step = SynthesizeAudioStep(Registry(), settings=Settings())
        results = await step.run(
            SynthesizeAudioInput(
                chapter_number=1,
                script=DubbingScript(
                    chapter_number=1,
                    segments=[
                        DubbingSegment(
                            segment_index=0,
                            segment_type=SegmentType.NARRATION,
                            text="测试",
                        )
                    ],
                ),
                voice_team=voice_team,
                provider=TTSProvider.MINIMAX,
                output_dir=tmp_path,
                retry_limit=3,
            )
        )

        assert adapter.synthesize.await_count == 1
        assert results[0].status == SynthesisStatus.FAILED
        assert results[0].failure_kind == "authentication"
        assert results[0].retryable is False
        assert results[0].retry_count == 0


class TestChapterVoiceSeed:
    """chapter_voice_seed anchors cross-segment voice consistency per chapter."""

    def test_seed_is_derived_from_voice_team_hash_and_chapter(
        self,
        registry: TTSAdapterRegistry,
    ) -> None:
        step = SynthesizeAudioStep(registry, settings=Settings())
        step._current_voice_team_hash = "abcdef1234567890"  # noqa: SLF001
        step._current_chapter_number = 7  # noqa: SLF001
        segment = DubbingSegment(
            segment_index=0,
            segment_type=SegmentType.NARRATION,
            text="旁白文本",
        )
        request = step._build_tts_request(segment, {}, TTSProvider.MOCK)
        assert request.metadata["chapter_voice_seed"] == "abcdef123456-ch7"

    def test_seed_is_stable_across_segments_in_same_chapter(
        self,
        registry: TTSAdapterRegistry,
    ) -> None:
        """Two segments in the same chapter/voice-team get the identical seed."""
        step = SynthesizeAudioStep(registry, settings=Settings())
        step._current_voice_team_hash = "teamhash123"  # noqa: SLF001
        step._current_chapter_number = 3  # noqa: SLF001
        seg1 = DubbingSegment(segment_index=0, segment_type=SegmentType.NARRATION, text="第一段")
        seg2 = DubbingSegment(segment_index=1, segment_type=SegmentType.DIALOGUE, text="第二段")
        r1 = step._build_tts_request(seg1, {}, TTSProvider.MOCK)
        r2 = step._build_tts_request(seg2, {}, TTSProvider.MOCK)
        assert r1.metadata["chapter_voice_seed"] == r2.metadata["chapter_voice_seed"]

    def test_seed_differs_across_chapters(
        self,
        registry: TTSAdapterRegistry,
    ) -> None:
        """Different chapters produce different seeds (prevents cross-chapter anchoring)."""
        step = SynthesizeAudioStep(registry, settings=Settings())
        step._current_voice_team_hash = "sameteamhash"  # noqa: SLF001
        segment = DubbingSegment(segment_index=0, segment_type=SegmentType.NARRATION, text="文本")
        step._current_chapter_number = 1  # noqa: SLF001
        r1 = step._build_tts_request(segment, {}, TTSProvider.MOCK)
        step._current_chapter_number = 2  # noqa: SLF001
        r2 = step._build_tts_request(segment, {}, TTSProvider.MOCK)
        assert r1.metadata["chapter_voice_seed"] != r2.metadata["chapter_voice_seed"]

    def test_seed_empty_when_voice_team_hash_missing(
        self,
        registry: TTSAdapterRegistry,
    ) -> None:
        """No voice_team_hash -> empty seed (audition takes, ad-hoc synthesis)."""
        step = SynthesizeAudioStep(registry, settings=Settings())
        # _current_voice_team_hash defaults to "" (not set)
        segment = DubbingSegment(segment_index=0, segment_type=SegmentType.NARRATION, text="文本")
        request = step._build_tts_request(segment, {}, TTSProvider.MOCK)
        assert request.metadata["chapter_voice_seed"] == ""

    def test_character_voice_keeps_its_bound_model(
        self,
        registry: TTSAdapterRegistry,
    ) -> None:
        step = SynthesizeAudioStep(registry, settings=Settings())
        step._current_model_id = "qwen-audio-3.0-tts-plus"  # noqa: SLF001
        segment = DubbingSegment(
            segment_index=0,
            segment_type=SegmentType.DIALOGUE,
            character_id="c1",
            character_name="Alice",
            text="我知道。",
        )
        entry = VoiceCastEntry(
            character_id="c1",
            character_name="Alice",
            provider=TTSProvider.BAILIAN,
            model_id="cosyvoice-v3.5-plus",
            voice_id="cosyvoice-v3.5-plus-vd-alice-1234",
            clone_status=VoiceCloneStatus.READY,
        )

        request = step._build_tts_request(segment, {"c1": entry}, TTSProvider.BAILIAN)

        assert request.voice_id == entry.voice_id
        assert request.model_id == entry.model_id

    async def test_pure_punctuation_narration_is_skipped(
        self,
        registry: TTSAdapterRegistry,
        settings: Settings,
        voice_team: VoiceTeamContract,
        tmp_path: Path,
    ) -> None:
        """Narration with only punctuation (no speakable chars) must be SKIPPED."""
        script = DubbingScript(
            chapter_number=1,
            segments=[
                DubbingSegment(
                    segment_index=0,
                    segment_type=SegmentType.NARRATION,
                    text="正常旁白文本",
                ),
                DubbingSegment(
                    segment_index=1,
                    segment_type=SegmentType.NARRATION,
                    text="————",
                ),
                DubbingSegment(
                    segment_index=2,
                    segment_type=SegmentType.NARRATION,
                    text="……",
                ),
            ],
        )
        step = SynthesizeAudioStep(registry, settings=settings)
        input_data = SynthesizeAudioInput(
            chapter_number=1,
            script=script,
            voice_team=voice_team,
            provider=TTSProvider.MOCK,
            output_dir=tmp_path,
        )
        results = await step.run(input_data)

        assert len(results) == 3
        assert results[0].status == SynthesisStatus.COMPLETED
        assert results[1].status == SynthesisStatus.SKIPPED
        assert results[2].status == SynthesisStatus.SKIPPED
