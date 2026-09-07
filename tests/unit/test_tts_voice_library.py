"""Regression tests for cross-project designed-voice reuse."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock

from novel_forge.common.constants import TaskType
from novel_forge.core.config import Settings
from novel_forge.persistence.models import ProjectLayout
from novel_forge.tts.assets.voice_library import VoiceLibrary, VoiceLibraryEntry, VoiceLibraryUsage
from novel_forge.tts.assets.voice_semantic_index import build_voice_semantic_scores
from novel_forge.tts.gateway.adapters.mock_adapter import MockTTSAdapter
from novel_forge.tts.gateway.factory import TTSAdapterRegistry
from novel_forge.tts.pipeline.build_voice_team_step import BuildVoiceTeamInput, BuildVoiceTeamStep
from novel_forge.tts.schemas import (
    TTSProvider,
    VoiceCastEntry,
    VoiceCloneStatus,
    VoiceDesignResponse,
    VoiceTeamContract,
)
from novel_forge.workspace.tts_ops.execution import execute_build_voice_team


def _character(*, character_id: str = "c1", name: str = "林远") -> dict[str, str]:
    return {
        "character_id": character_id,
        "name": name,
        "gender": "male",
        "age": "青年",
        "role": "protagonist",
        "personality": "沉稳克制",
        "voice_description": "低沉、克制、短句表达",
    }


class _Registry:
    def __init__(self, adapter: MockTTSAdapter) -> None:
        self._adapter = adapter

    def get_adapter(self, provider: TTSProvider) -> MockTTSAdapter:
        assert provider == TTSProvider.MOCK
        return self._adapter


async def test_library_hit_creates_a_valid_cast_entry_and_records_usage() -> None:
    expires_at = datetime.now(timezone.utc) + timedelta(days=1)
    library = VoiceLibrary(
        [
            VoiceLibraryEntry(
                voice_id="designed-voice",
                provider=TTSProvider.MOCK,
                gender="male",
                personality="沉稳",
                voice_description="低沉、克制",
                expires_at=expires_at,
            )
        ]
    )
    usage: list[VoiceLibraryUsage] = []
    step = BuildVoiceTeamStep(object(), settings=Settings(_env_file=None))

    entry = await step._assign_voice_for_character(
        _character(),
        MockTTSAdapter(latency_ms=0),
        TTSProvider.MOCK,
        voice_library=library,
        library_usage=usage,
    )

    assert entry.voice_source == "library"
    assert entry.voice_id == "designed-voice"
    assert entry.expires_at == expires_at
    assert usage == [VoiceLibraryUsage(voice_id="designed-voice", provider=TTSProvider.MOCK)]

    changed_character = _character()
    changed_character["personality"] = "活泼外向"
    assert not step._can_reuse_existing_entry(entry, changed_character, TTSProvider.MOCK)


def test_library_ignores_expired_entries_and_requires_more_than_gender() -> None:
    library = VoiceLibrary(
        [
            VoiceLibraryEntry(
                voice_id="expired",
                provider=TTSProvider.MOCK,
                gender="male",
                personality="沉稳",
                expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
            ),
            VoiceLibraryEntry(
                voice_id="gender-only",
                provider=TTSProvider.MOCK,
                gender="male",
            ),
        ]
    )

    assert library.find_match(_character(), TTSProvider.MOCK) is None


def test_semantic_scores_rank_candidates_but_cannot_bypass_hard_constraints() -> None:
    library = VoiceLibrary(
        [
            VoiceLibraryEntry(
                voice_id="calm-male",
                provider=TTSProvider.MOCK,
                gender="male",
                age_hint="青年",
                role="supporting",
                personality="沉稳克制",
            ),
            VoiceLibraryEntry(
                voice_id="bright-male",
                provider=TTSProvider.MOCK,
                gender="male",
                age_hint="青年",
                role="supporting",
                personality="活泼明亮",
            ),
            VoiceLibraryEntry(
                voice_id="wrong-gender",
                provider=TTSProvider.MOCK,
                gender="female",
                age_hint="青年",
                role="supporting",
                personality="沉稳克制",
            ),
        ]
    )
    character = _character()
    character["role"] = "other"

    match = library.find_match(
        character,
        TTSProvider.MOCK,
        semantic_scores={
            "calm-male": 0.88,
            "bright-male": 0.62,
            "wrong-gender": 0.99,
        },
    )

    assert match is not None
    assert match.voice_id == "calm-male"


async def test_voice_semantic_index_returns_scores_in_mock_mode(tmp_path: Path) -> None:
    settings = Settings(
        _env_file=None,
        storage_root=tmp_path,
        memory_use_mock_embeddings=True,
        memory_vector_store_backend="in_memory",
    )
    library = VoiceLibrary(
        [
            VoiceLibraryEntry(
                voice_id="voice-a",
                provider=TTSProvider.MOCK,
                gender="male",
                age_hint="青年",
                personality="沉稳克制",
            ),
            VoiceLibraryEntry(
                voice_id="voice-b",
                provider=TTSProvider.MOCK,
                gender="male",
                age_hint="青年",
                personality="活泼明亮",
            ),
        ]
    )

    scores = await build_voice_semantic_scores(
        settings=settings,
        library=library,
        characters=[_character()],
        provider=TTSProvider.MOCK,
        provider_voices=[
            {
                "voice_id": "system-c",
                "name": "实时目录音色",
                "gender": "male",
                "age": "青年",
                "tags": ["沉稳"],
            }
        ],
    )

    assert set(scores.library["c1"]) == {"voice-a", "voice-b"}
    assert set(scores.catalog["c1"]) == {"system-c"}
    assert all(0.0 <= score <= 1.0 for score in scores.library["c1"].values())
    assert scores.catalog_fingerprint


def test_removed_live_catalog_voice_is_not_silently_reused() -> None:
    step = BuildVoiceTeamStep(object(), settings=Settings(_env_file=None))
    entry = VoiceCastEntry(
        character_id="c1",
        character_name="林远",
        voice_id="removed-system-voice",
        provider=TTSProvider.MOCK,
        clone_status=VoiceCloneStatus.READY,
        voice_source="system",
    )

    reusable = step._can_reuse_existing_entry(
        entry,
        _character(),
        TTSProvider.MOCK,
        available_voices=[{"voice_id": "current-system-voice", "gender": "male"}],
        catalog_sync_ok=True,
    )

    assert reusable is False


def test_library_treats_missed_activation_as_expired() -> None:
    library = VoiceLibrary(
        [
            VoiceLibraryEntry(
                voice_id="unactivated",
                provider=TTSProvider.MOCK,
                gender="male",
                age_hint="青年",
                personality="沉稳",
                activation_deadline=datetime.now(timezone.utc) - timedelta(seconds=1),
            )
        ]
    )

    assert library.find_match(_character(), TTSProvider.MOCK) is None


def test_library_age_matching_does_not_treat_unrelated_shao_as_youth() -> None:
    library = VoiceLibrary(
        [
            VoiceLibraryEntry(
                voice_id="young-voice",
                provider=TTSProvider.MOCK,
                gender="male",
                age_hint="青年",
            )
        ]
    )
    character = {
        "character_id": "c1",
        "name": "江野",
        "gender": "male",
        "personality": "很少说话",
    }

    assert library.find_match(character, TTSProvider.MOCK) is None
    assert (
        library.find_match(
            _character(),
            TTSProvider.MOCK,
            excluded_voice_ids={"young-voice"},
        )
        is None
    )


async def test_one_library_voice_is_not_auto_assigned_to_two_characters() -> None:
    library = VoiceLibrary(
        [
            VoiceLibraryEntry(
                voice_id="shared-designed-voice",
                provider=TTSProvider.MOCK,
                gender="male",
                age_hint="青年",
                personality="沉稳",
                voice_description="低沉克制",
            )
        ]
    )
    input_data = BuildVoiceTeamInput(
        characters=[
            _character(character_id="c1", name="林远"),
            _character(character_id="c2", name="顾衡"),
        ],
        default_provider=TTSProvider.MOCK,
        parallel_clone=True,
        voice_library=library,
    )
    step = BuildVoiceTeamStep(
        _Registry(MockTTSAdapter(latency_ms=0)),
        settings=Settings(_env_file=None),
    )

    team = await step._execute(input_data)

    assert sum(entry.voice_id == "shared-designed-voice" for entry in team.entries) == 1
    # 新优先级：音色库匹配失败后先走系统目录（零成本），AI 设计默认关闭
    assert {entry.voice_source for entry in team.entries} == {"library", "system"}


async def test_selected_character_bypasses_existing_voice_reuse() -> None:
    adapter = MockTTSAdapter(latency_ms=0)
    existing = VoiceTeamContract(
        entries=[
            VoiceCastEntry(
                character_id="c1",
                character_name="林远",
                voice_id="mock-male-1",
                provider=TTSProvider.MOCK,
                clone_status=VoiceCloneStatus.READY,
                voice_source="system",
            ),
            VoiceCastEntry(
                character_id="c2",
                character_name="顾衡",
                voice_id="mock-male-2",
                provider=TTSProvider.MOCK,
                clone_status=VoiceCloneStatus.READY,
                voice_source="system",
            ),
        ],
        default_provider=TTSProvider.MOCK,
    )
    input_data = BuildVoiceTeamInput(
        characters=[_character(), _character(character_id="c2", name="顾衡")],
        default_provider=TTSProvider.MOCK,
        existing_team=existing,
        parallel_clone=False,
        voice_library=VoiceLibrary(),
        rebuild_character_ids=["c1"],
    )
    events: list[tuple[str, dict[str, object]]] = []
    step = BuildVoiceTeamStep(
        _Registry(adapter),
        settings=Settings(_env_file=None),
        on_step=lambda name, data: events.append((name, data)),
    )

    team = await step._execute(input_data)

    rebuilt = team.get_entry("c1")
    assert rebuilt is not None
    # 新优先级：音色库无匹配→系统目录命中（零成本），不再默认走 AI 设计
    assert rebuilt.voice_source == "system"
    assert team.get_entry("c2") is not None
    semantic_start = next(data for name, data in events if name == "voice_semantic_retrieval_start")
    assert semantic_start["characters"] == 1
    assert semantic_start["total_characters"] == 2


async def test_parallel_build_emits_incremental_completed_counts() -> None:
    events: list[tuple[str, dict[str, object]]] = []
    input_data = BuildVoiceTeamInput(
        characters=[
            _character(character_id="c1", name="林远"),
            _character(character_id="c2", name="顾衡"),
        ],
        default_provider=TTSProvider.MOCK,
        parallel_clone=True,
        voice_library=VoiceLibrary(),
    )
    step = BuildVoiceTeamStep(
        _Registry(MockTTSAdapter(latency_ms=0)),
        settings=Settings(_env_file=None),
        on_step=lambda name, data: events.append((name, data)),
    )

    team = await step._execute(input_data)

    event_names = [name for name, _ in events]
    assert event_names.index("voice_catalog_sync_start") < event_names.index(
        "voice_catalog_sync_done"
    )
    assert event_names.index("voice_semantic_retrieval_start") < event_names.index(
        "voice_semantic_retrieval_done"
    )
    assert event_names.index("voice_assignment_batch_start") < event_names.index(
        "build_voice_team_progress"
    )
    progress = [data for name, data in events if name == "build_voice_team_progress"]
    assert [item["completed"] for item in progress] == [1, 2]
    assert all(item["total"] == 2 for item in progress)
    assert {item["character_name"] for item in progress} == {"林远", "顾衡"}
    assert len(team.entries) == 2


async def test_normal_rebuild_llm_reviews_reusable_low_confidence_entry() -> None:
    events: list[tuple[str, dict[str, object]]] = []
    existing_entry = VoiceCastEntry(
        character_id="c1",
        character_name="林远",
        voice_id="mock-male-1",
        provider=TTSProvider.MOCK,
        clone_status=VoiceCloneStatus.READY,
        voice_source="system",
        match_score=0.48,
        match_warnings=["角色特质证据不足，建议试听确认"],
        performance_offsets_manually_set=True,
        clone_prompt="低沉、克制、短句表达",
    )
    library = VoiceLibrary(
        [
            VoiceLibraryEntry(
                voice_id="better-library-voice",
                provider=TTSProvider.MOCK,
                gender="male",
                age_hint="青年",
                role="protagonist",
                personality="沉稳克制",
                voice_description="低沉、克制、短句表达",
            ),
        ]
    )
    input_data = BuildVoiceTeamInput(
        characters=[_character()],
        default_provider=TTSProvider.MOCK,
        existing_team=VoiceTeamContract(
            entries=[existing_entry],
            default_provider=TTSProvider.MOCK,
        ),
        parallel_clone=False,
        voice_library=library,
    )
    step = BuildVoiceTeamStep(
        _Registry(MockTTSAdapter(latency_ms=0)),
        settings=Settings(
            _env_file=None,
            tts_voice_llm_adjudication_enabled=True,
            tts_voice_llm_adjudication_auto_select_score=0.85,
        ),
        router=object(),
        builder=object(),
        on_step=lambda name, data: events.append((name, data)),
    )
    step._call_with_retry = AsyncMock(  # type: ignore[method-assign]
        return_value={
            "decisions": [
                {
                    "character_id": "c1",
                    "verdict": "approve",
                    "selected_voice_id": "better-library-voice",
                    "audition_voice_ids": [],
                    "confidence": 0.94,
                    "reason": "稳定声线与角色底色更一致。",
                }
            ],
            "summary": "已纠正可复用旧角色的低置信度匹配。",
        }
    )

    team = await step._execute(input_data)

    updated = team.get_entry("c1")
    assert updated is not None
    assert updated.voice_id == "better-library-voice"
    assert updated.llm_adjudication_status == "recast"
    semantic_start = next(data for name, data in events if name == "voice_semantic_retrieval_start")
    assert semantic_start["characters"] == 1
    progress = [data for name, data in events if name == "build_voice_team_progress"]
    assert progress[0]["status"] == "reused"
    assert any(name == "character_voice_recast" for name, _ in events)


async def test_llm_voice_adjudication_marks_low_confidence_match_for_audition() -> None:
    events: list[tuple[str, dict[str, object]]] = []
    entry = VoiceCastEntry(
        character_id="c1",
        character_name="林远",
        voice_id="library-calm-male",
        provider=TTSProvider.MOCK,
        clone_status=VoiceCloneStatus.READY,
        voice_source="library",
        match_score=0.58,
        match_reasons=["性别硬约束一致"],
        match_warnings=["角色特质证据不足，建议试听确认"],
    )
    untouched_entry = entry.model_copy(
        update={
            "character_id": "c2",
            "character_name": "顾衡",
            "voice_id": "library-calm-male-2",
        }
    )
    team = VoiceTeamContract(entries=[entry, untouched_entry], default_provider=TTSProvider.MOCK)
    step = BuildVoiceTeamStep(
        object(),
        settings=Settings(
            _env_file=None,
            tts_voice_llm_adjudication_enabled=True,
            tts_voice_llm_adjudication_min_match_score=0.72,
        ),
        router=object(),
        builder=object(),
        on_step=lambda name, data: events.append((name, data)),
    )
    step._call_with_retry = AsyncMock(  # type: ignore[method-assign]
        return_value={
            "decisions": [
                {
                    "character_id": "c1",
                    "verdict": "review",
                    "selected_voice_id": "",
                    "audition_voice_ids": ["library-calm-male"],
                    "confidence": 0.81,
                    "reason": "沉稳特质有依据，但音色质地信息不足。",
                }
            ],
            "summary": "建议试听后确认。",
        }
    )

    await step._apply_llm_voice_adjudications(
        team,
        [_character(), _character(character_id="c2", name="顾衡")],
        character_ids={"c1"},
    )

    assert entry.voice_id == "library-calm-male"
    assert entry.provider == TTSProvider.MOCK
    assert entry.llm_adjudication_status == "needs_audition"
    assert entry.llm_adjudication_confidence == 0.81
    assert entry.audition_candidate_voice_ids == ["library-calm-male"]
    assert any("LLM 建议对比试听（81%）" in item for item in entry.match_warnings)
    assert untouched_entry.llm_adjudication_status == "not_reviewed"
    assert step._call_with_retry.await_args.args[0] == TaskType.TTS_ADJUDICATE_VOICE_MATCH
    candidates = step._call_with_retry.await_args.args[1]["stage_cards"]["candidates"]
    assert [candidate["character_id"] for candidate in candidates] == ["c1"]
    assert any(name == "tts_voice_llm_adjudication_done" for name, _ in events)


async def test_llm_voice_adjudication_recasts_from_hard_filtered_whitelist() -> None:
    events: list[tuple[str, dict[str, object]]] = []
    entry = VoiceCastEntry(
        character_id="c1",
        character_name="林远",
        voice_id="current-system-voice",
        provider=TTSProvider.MOCK,
        clone_status=VoiceCloneStatus.READY,
        voice_source="system",
        match_score=0.52,
        match_warnings=["角色特质证据不足，建议试听确认"],
        preview_audio_path="old-preview.wav",
        preview_text="旧试听",
    )
    team = VoiceTeamContract(entries=[entry], default_provider=TTSProvider.MOCK)
    library = VoiceLibrary(
        [
            VoiceLibraryEntry(
                voice_id="calm-male-voice",
                provider=TTSProvider.MOCK,
                gender="male",
                age_hint="青年",
                role="protagonist",
                personality="沉稳克制",
                voice_description="低沉、克制、短句表达",
            ),
            VoiceLibraryEntry(
                voice_id="wrong-gender-voice",
                provider=TTSProvider.MOCK,
                gender="female",
                age_hint="青年",
                personality="沉稳克制",
            ),
        ]
    )
    usage: list[VoiceLibraryUsage] = []
    step = BuildVoiceTeamStep(
        object(),
        settings=Settings(
            _env_file=None,
            tts_voice_llm_adjudication_enabled=True,
            tts_voice_llm_adjudication_auto_select_score=0.85,
        ),
        router=object(),
        builder=object(),
        on_step=lambda name, data: events.append((name, data)),
    )
    step._call_with_retry = AsyncMock(  # type: ignore[method-assign]
        return_value={
            "decisions": [
                {
                    "character_id": "c1",
                    "verdict": "approve",
                    "selected_voice_id": "calm-male-voice",
                    "audition_voice_ids": [],
                    "confidence": 0.93,
                    "reason": "年龄、气质和说话节奏更贴合角色。",
                }
            ],
            "summary": "已在合规候选中改配。",
        }
    )

    await step._apply_llm_voice_adjudications(
        team,
        [_character()],
        character_ids={"c1"},
        voice_library=library,
        library_usage=usage,
    )

    updated = team.get_entry("c1")
    assert updated is not None
    assert updated.voice_id == "calm-male-voice"
    assert updated.llm_adjudication_status == "recast"
    assert updated.llm_adjudication_confidence == 0.93
    assert updated.preview_audio_path == ""
    assert updated.preview_text == ""
    assert usage == [VoiceLibraryUsage(voice_id="calm-male-voice", provider=TTSProvider.MOCK)]
    prompt_candidates = step._call_with_retry.await_args.args[1]["stage_cards"]["candidates"]
    offered_ids = {item["voice_id"] for item in prompt_candidates[0]["candidate_voices"]}
    assert offered_ids == {"current-system-voice", "calm-male-voice"}
    assert any(name == "character_voice_recast" for name, _ in events)


async def test_llm_voice_adjudication_rejects_hallucinated_voice_id() -> None:
    events: list[tuple[str, dict[str, object]]] = []
    entry = VoiceCastEntry(
        character_id="c1",
        character_name="林远",
        voice_id="current-system-voice",
        provider=TTSProvider.MOCK,
        clone_status=VoiceCloneStatus.READY,
        voice_source="system",
        match_score=0.4,
        match_warnings=["需复核"],
    )
    team = VoiceTeamContract(entries=[entry], default_provider=TTSProvider.MOCK)
    step = BuildVoiceTeamStep(
        object(),
        settings=Settings(_env_file=None, tts_voice_llm_adjudication_enabled=True),
        router=object(),
        builder=object(),
        on_step=lambda name, data: events.append((name, data)),
    )
    step._call_with_retry = AsyncMock(  # type: ignore[method-assign]
        return_value={
            "decisions": [
                {
                    "character_id": "c1",
                    "verdict": "approve",
                    "selected_voice_id": "invented-voice-id",
                    "audition_voice_ids": [],
                    "confidence": 0.99,
                    "reason": "幻觉候选不应生效。",
                }
            ],
            "summary": "",
        }
    )

    await step._apply_llm_voice_adjudications(team, [_character()], character_ids={"c1"})

    assert entry.voice_id == "current-system-voice"
    assert entry.llm_adjudication_status == "needs_audition"
    assert any("无效选角" in warning for warning in entry.match_warnings)
    done = next(data for name, data in events if name == "tts_voice_llm_adjudication_done")
    assert done["invalid_output_count"] == 1
    assert not any(name == "character_voice_recast" for name, _ in events)


async def test_llm_voice_adjudication_never_recasts_identity_locked_character() -> None:
    entry = VoiceCastEntry(
        character_id="c1",
        character_name="林远",
        voice_id="locked-voice",
        provider=TTSProvider.MOCK,
        clone_status=VoiceCloneStatus.READY,
        voice_source="system",
        match_score=0.5,
        match_warnings=["需复核"],
        identity_locked=True,
    )
    team = VoiceTeamContract(entries=[entry], default_provider=TTSProvider.MOCK)
    library = VoiceLibrary(
        [
            VoiceLibraryEntry(
                voice_id="alternative-voice",
                provider=TTSProvider.MOCK,
                gender="male",
                age_hint="青年",
                personality="沉稳克制",
                voice_description="低沉、克制",
            )
        ]
    )
    step = BuildVoiceTeamStep(
        object(),
        settings=Settings(_env_file=None, tts_voice_llm_adjudication_enabled=True),
        router=object(),
        builder=object(),
    )
    step._call_with_retry = AsyncMock(  # type: ignore[method-assign]
        return_value={
            "decisions": [
                {
                    "character_id": "c1",
                    "verdict": "approve",
                    "selected_voice_id": "alternative-voice",
                    "audition_voice_ids": [],
                    "confidence": 0.98,
                    "reason": "替代候选更贴合，但角色已锁定。",
                }
            ],
            "summary": "",
        }
    )

    await step._apply_llm_voice_adjudications(
        team,
        [_character()],
        character_ids={"c1"},
        voice_library=library,
    )

    assert entry.voice_id == "locked-voice"
    assert entry.llm_adjudication_status == "needs_audition"
    assert entry.audition_candidate_voice_ids == ["alternative-voice", "locked-voice"]
    assert any("声纹已锁定" in warning for warning in entry.match_warnings)


class _FailingDesignAdapter(MockTTSAdapter):
    async def design_voice(self, request):  # type: ignore[no-untyped-def]
        return VoiceDesignResponse(
            provider=TTSProvider.MOCK,
            status=VoiceCloneStatus.FAILED,
            message="quota exhausted",
        )


class _TemporaryDesignAdapter(MockTTSAdapter):
    async def design_voice(self, request):  # type: ignore[no-untyped-def]
        return VoiceDesignResponse(
            voice_id="temporary-designed-voice",
            provider=TTSProvider.MOCK,
            status=VoiceCloneStatus.READY,
            activation_deadline=datetime.now(timezone.utc) + timedelta(days=7),
        )


async def test_design_activation_deadline_reaches_team_and_library() -> None:
    new_entries: list[VoiceLibraryEntry] = []
    step = BuildVoiceTeamStep(object(), settings=Settings(_env_file=None))

    entry = await step._assign_voice_for_character(
        _character(),
        _TemporaryDesignAdapter(latency_ms=0),
        TTSProvider.MOCK,
        voice_library=VoiceLibrary(),
        new_library_entries=new_entries,
    )

    assert entry.activation_deadline is not None
    assert new_entries[0].activation_deadline == entry.activation_deadline


async def test_design_fallback_event_only_emits_after_an_attempt() -> None:
    events: list[tuple[str, dict[str, str]]] = []
    step = BuildVoiceTeamStep(
        object(),
        settings=Settings(_env_file=None),
        on_step=lambda name, data: events.append((name, data)),
    )

    fallback = await step._assign_voice_for_character(
        _character(),
        _FailingDesignAdapter(latency_ms=0),
        TTSProvider.MOCK,
    )
    assert fallback.voice_source == "system"
    assert any(name == "voice_design_fallback" for name, _ in events)

    events.clear()
    disabled = await step._assign_voice_for_character(
        _character(),
        MockTTSAdapter(latency_ms=0),
        TTSProvider.MOCK,
        voice_design_enabled=False,
    )
    assert disabled.voice_source == "system"
    assert not any(name == "voice_design_fallback" for name, _ in events)


class _NoCatalogDesignAdapter(MockTTSAdapter):
    """Mock adapter with empty system catalog, forcing AI design as last resort."""

    async def list_system_voices(self, *, gender=None, language=None, limit=100):  # type: ignore[no-untyped-def]
        return []


async def test_execution_persists_designs_and_library_usage(tmp_path: Path, monkeypatch) -> None:
    # 使用无系统目录的 adapter，强制触发 AI 设计路径（测试持久化流程）
    adapter = _NoCatalogDesignAdapter(latency_ms=0)
    monkeypatch.setattr(
        TTSAdapterRegistry,
        "get_instance",
        classmethod(lambda cls, settings: _Registry(adapter)),
    )
    settings = Settings(
        _env_file=None,
        storage_root=tmp_path,
        tts_default_provider="mock",
        tts_parallel_voice_clone=False,
        tts_voice_library_scope="global_with_names",  # cross-project reuse
        tts_voice_design_enabled=True,  # 显式启用 AI 设计以测试持久化路径
    )
    first_layout = ProjectLayout(tmp_path / "first")
    second_layout = ProjectLayout(tmp_path / "second")
    first_layout.ensure_dirs()
    second_layout.ensure_dirs()
    events: list[str] = []

    first = await execute_build_voice_team(
        project_id="first",
        characters=[_character()],
        settings=settings,
        layout=first_layout,
        provider="mock",
        on_step_progress=lambda event, data: events.append(event),
    )
    assert VoiceTeamContract.model_validate(first.result).entries[0].voice_source == "designed"
    assert "voice_library_saved" in events

    stored = VoiceLibrary.load(tmp_path)
    assert len(stored.entries) == 1
    assert stored.entries[0].usage_count == 0

    second = await execute_build_voice_team(
        project_id="second",
        characters=[_character(character_id="c2", name="顾衡")],
        settings=settings,
        layout=second_layout,
        provider="mock",
        on_step_progress=lambda event, data: events.append(event),
    )
    assert VoiceTeamContract.model_validate(second.result).entries[0].voice_source == "library"
    assert "voice_library_hit" in events

    stored = VoiceLibrary.load(tmp_path)
    assert stored.entries[0].usage_count == 1


async def test_execution_builds_narrator_only_team_when_no_cast_exists(tmp_path: Path) -> None:
    """Narrator-only chapters are valid one-click TTS input, not an error."""

    layout = ProjectLayout(tmp_path / "narrator_only")
    layout.ensure_dirs()
    events: list[str] = []

    result = await execute_build_voice_team(
        project_id="narrator_only",
        characters=[],
        settings=Settings(
            _env_file=None,
            storage_root=tmp_path,
            tts_default_provider="mock",
            tts_default_model="mock-tts",
        ),
        layout=layout,
        narrator_voice_id="mock-narrator",
        provider="mock",
        on_step_progress=lambda event, _data: events.append(event),
    )

    team = VoiceTeamContract.model_validate(result.result)
    assert team.entries == []
    assert team.narrator_voice_id == "mock-narrator"
    assert team.narrator_provider == TTSProvider.MOCK
    assert team.default_provider == TTSProvider.MOCK
    assert (
        VoiceTeamContract.model_validate_json(
            layout.tts_voice_team_path.read_text(encoding="utf-8")
        )
        == team
    )
    assert events == [
        "voice_team_runtime_check_start",
        "voice_team_runtime_check_done",
        "tts_voice_team_narrator_only",
    ]


# ── Tiered matching regression tests ──────────────────────────────────────


def test_find_match_tier1_exact_name_reuse_across_projects() -> None:
    """The same character name + provider is an exact reuse, no re-design.

    This is the core cost-saver: a voice designed for '沈岸' in project A
    must be reused when project B builds the same character, instead of
    paying for another voice_design call.
    """
    library = VoiceLibrary(
        [
            VoiceLibraryEntry(
                voice_id="shen-an-voice",
                provider=TTSProvider.MOCK,
                character_name="沈岸",
                gender="男",
                age_hint="28",
                role="protagonist",
            )
        ]
    )
    # Same name, different project context -- still an exact hit.
    character = {
        "character_id": "p2_shen_an",
        "name": "沈岸",
        "gender": "男",
        "age": "28",
        "role": "protagonist",
    }
    match = library.find_match(character, TTSProvider.MOCK)
    assert match is not None
    assert match.voice_id == "shen-an-voice"


def test_exact_name_reuse_never_overrides_gender_or_age_hard_constraints() -> None:
    library = VoiceLibrary(
        [
            VoiceLibraryEntry(
                voice_id="stale-shen-an-voice",
                provider=TTSProvider.MOCK,
                character_name="沈岸",
                gender="female",
                age_hint="老年",
                role="protagonist",
                personality="活泼明亮",
            )
        ]
    )

    match = library.find_match(
        {
            "name": "沈岸",
            "gender": "male",
            "age": "28",
            "role": "protagonist",
            "personality": "沉稳克制",
        },
        TTSProvider.MOCK,
    )

    assert match is None


def test_find_match_tier2_structured_trait_match_different_name() -> None:
    """Different name but identical gender+age+role is a safe reuse.

    A voice designed for one 'male / young / minor' character can serve
    another with the same demographic profile without re-designing.
    """
    library = VoiceLibrary(
        [
            VoiceLibraryEntry(
                voice_id="male-young-minor",
                provider=TTSProvider.MOCK,
                character_name="张昊",
                gender="male",
                age_hint="30",
                role="minor",
            )
        ]
    )
    character = {
        "character_id": "c_new",
        "name": "李明",
        "gender": "male",
        "age": "30",
        "role": "minor",
    }
    match = library.find_match(character, TTSProvider.MOCK)
    assert match is not None
    assert match.voice_id == "male-young-minor"


def test_find_match_prefers_activated_voice_over_unactivated() -> None:
    """When two entries match, the activated one wins (no 7-day deletion risk)."""
    now = datetime.now(timezone.utc)
    library = VoiceLibrary(
        [
            VoiceLibraryEntry(
                voice_id="unactivated-voice",
                provider=TTSProvider.MOCK,
                character_name="沈岸",
                gender="男",
                age_hint="28",
                role="protagonist",
                activation_deadline=now + timedelta(days=3),
            ),
            VoiceLibraryEntry(
                voice_id="activated-voice",
                provider=TTSProvider.MOCK,
                character_name="沈岸",
                gender="男",
                age_hint="28",
                role="protagonist",
                activation_deadline=None,
            ),
        ]
    )
    character = {"name": "沈岸", "gender": "男", "age": "28", "role": "protagonist"}
    match = library.find_match(character, TTSProvider.MOCK)
    assert match is not None
    assert match.voice_id == "activated-voice"


def test_find_by_design_prompt_dedup_prevents_redundant_design() -> None:
    """find_by_design_prompt returns a voice with the same design brief.

    Two characters that produce the same canonical design prompt should reuse
    one voice instead of triggering two billable design_voice calls.
    """
    prompt = "请只为有声书角色设计稳定...性别：男；年龄：28"
    library = VoiceLibrary(
        [
            VoiceLibraryEntry(
                voice_id="deduped-voice",
                provider=TTSProvider.MOCK,
                character_name="沈岸",
                voice_design_prompt=prompt,
            )
        ]
    )
    match = library.find_by_design_prompt(prompt, TTSProvider.MOCK)
    assert match is not None
    assert match.voice_id == "deduped-voice"

    # A different prompt must not match.
    assert library.find_by_design_prompt("different prompt", TTSProvider.MOCK) is None
    # Empty prompt is a no-op.
    assert library.find_by_design_prompt("", TTSProvider.MOCK) is None
