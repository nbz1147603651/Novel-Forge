"""Unit tests for multi-candidate voice A/B preview service (P1).

Covers:
- build_preview_plan deterministic candidate selection
  (language/gender hard filters, age conflict, personality soft match)
- generate_candidate_previews synthesis, caching and failure isolation
- confirm_preview persistence back into the voice team
- streaming synthesis path (synthesize_streaming preference)
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from novel_forge.core.config import Settings
from novel_forge.persistence.models import ProjectLayout
from novel_forge.tts.gateway.factory import TTSAdapterRegistry
from novel_forge.tts.schemas import (
    TTSProvider,
    TTSRequest,
    TTSResponse,
    VoiceCastEntry,
    VoiceCloneStatus,
    VoiceTeamContract,
)
from novel_forge.tts.services.voice_preview import (
    DEFAULT_CANDIDATE_COUNT,
    _sample_text_for_character,
    build_preview_plan,
    confirm_preview,
    generate_candidate_previews,
)

# 目录：带 personality / age_hint / voice_description 的完整画像字段
_CATALOG: list[dict[str, Any]] = [
    {
        "voice_id": "f-warm",
        "name": "温暖女声",
        "gender": "female",
        "age_hint": "青年",
        "personality": "warm",
        "voice_description": "温暖温柔",
    },
    {
        "voice_id": "f-calm",
        "name": "沉稳女声",
        "gender": "female",
        "age_hint": "中年",
        "personality": "calm",
        "voice_description": "沉稳冷静",
    },
    {
        "voice_id": "f-playful",
        "name": "俏皮女声",
        "gender": "female",
        "age_hint": "少女",
        "personality": "playful",
        "voice_description": "活泼俏皮",
    },
    {
        "voice_id": "f-elder",
        "name": "苍老女声",
        "gender": "female",
        "age_hint": "老年",
        "personality": "calm",
        "voice_description": "苍老沉稳",
    },
    {
        "voice_id": "m-calm",
        "name": "沉稳男声",
        "gender": "male",
        "age_hint": "中年",
        "personality": "calm",
        "voice_description": "沉稳",
    },
]


class _FakeAdapter:
    """Minimal TTS adapter double tracking synthesize/streaming calls."""

    def __init__(self, voices: list[dict[str, Any]] | None = None) -> None:
        self.voices = voices if voices is not None else [dict(item) for item in _CATALOG]
        self.synthesize_calls = 0
        self.streaming_calls = 0
        self.fail_voice_ids: set[str] = set()

    async def list_system_voices(
        self,
        *,
        gender: str | None = None,
        language: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        voices = self.voices
        if gender:
            voices = [v for v in voices if v.get("gender") == gender]
        return voices[:limit]

    async def synthesize(self, request: TTSRequest) -> TTSResponse:
        self.synthesize_calls += 1
        if request.voice_id in self.fail_voice_ids:
            raise RuntimeError("provider exploded")
        return TTSResponse(
            audio_data=b"\xff\xfb" * 64,
            model_id=request.model_id,
            voice_id=request.voice_id,
            audio_format="mp3",
        )

    async def synthesize_streaming(
        self,
        request: TTSRequest,
        *,
        ws_url: str = "",
        on_chunk: Any = None,
    ) -> TTSResponse:
        self.streaming_calls += 1
        if on_chunk is not None:
            result = on_chunk(b"\xff\xfb" * 16)
            if hasattr(result, "__await__"):
                await result
        return await self.synthesize(request)


class _FakeRegistry:
    def __init__(self, adapter: _FakeAdapter) -> None:
        self._adapter = adapter

    def get_adapter(self, provider: str | TTSProvider) -> _FakeAdapter:
        return self._adapter


def _install_registry(monkeypatch: pytest.MonkeyPatch, adapter: _FakeAdapter) -> None:
    monkeypatch.setattr(
        TTSAdapterRegistry, "get_instance", lambda settings: _FakeRegistry(adapter)
    )


def _make_context(
    tmp_path: Any,
    monkeypatch: pytest.MonkeyPatch,
    adapter: _FakeAdapter | None = None,
) -> tuple[Settings, ProjectLayout, _FakeAdapter]:
    settings = Settings(_env_file=None, tts_default_provider="mock")
    layout = ProjectLayout(tmp_path)
    layout.ensure_dirs()
    adapter = adapter or _FakeAdapter()
    _install_registry(monkeypatch, adapter)
    return settings, layout, adapter


def _write_team(layout: ProjectLayout, *, voice_id: str = "old-v") -> VoiceTeamContract:
    team = VoiceTeamContract(
        entries=[
            VoiceCastEntry(
                character_id="c1",
                character_name="林小满",
                voice_id=voice_id,
                provider=TTSProvider.MOCK,
            )
        ]
    )
    layout.tts_voice_team_path.parent.mkdir(parents=True, exist_ok=True)
    layout.tts_voice_team_path.write_text(team.model_dump_json(), encoding="utf-8")
    return team


def _characters(*profiles: dict[str, str]) -> list[dict[str, Any]]:
    if not profiles:
        profiles = ({},)
    return [
        {
            "character_id": profile.get("character_id", "c1"),
            "name": profile.get("name", "林小满"),
            "gender": profile.get("gender", "female"),
            "age": profile.get("age", ""),
            "personality": profile.get("personality", ""),
        }
        for profile in profiles
    ]


# ─── build_preview_plan: 确定性候选选择 ─────────────────────────────────────


class TestBuildPreviewPlan:
    async def test_picks_three_candidates_per_character(self, tmp_path, monkeypatch) -> None:
        settings, layout, _adapter = _make_context(tmp_path, monkeypatch)
        plans = await build_preview_plan(
            layout=layout, settings=settings, characters=_characters()
        )
        assert len(plans) == 1
        plan = plans[0]
        assert plan.character_id == "c1"
        assert plan.character_name == "林小满"
        assert len(plan.candidates) == DEFAULT_CANDIDATE_COUNT
        assert plan.voice_id == plan.candidates[0].voice_id
        assert plan.speed == float(settings.tts_default_speed)

    async def test_gender_hard_filter(self, tmp_path, monkeypatch) -> None:
        settings, layout, _adapter = _make_context(tmp_path, monkeypatch)
        plans = await build_preview_plan(
            layout=layout, settings=settings, characters=_characters({"gender": "male"})
        )
        assert plans[0].candidates
        assert all(candidate.gender == "male" for candidate in plans[0].candidates)

    async def test_personality_soft_match_ranks_first(self, tmp_path, monkeypatch) -> None:
        settings, layout, _adapter = _make_context(tmp_path, monkeypatch)
        plans = await build_preview_plan(
            layout=layout,
            settings=settings,
            characters=_characters({"personality": "温柔温暖"}),
        )
        assert plans[0].candidates[0].voice_id == "f-warm"
        assert "性格画像匹配" in plans[0].candidates[0].match_reasons

    async def test_age_conflict_ranked_last(self, tmp_path, monkeypatch) -> None:
        settings, layout, _adapter = _make_context(tmp_path, monkeypatch)
        # 少年角色：少女音色（f-playful）无冲突，苍老音色（f-elder）冲突 → 排最后
        plans = await build_preview_plan(
            layout=layout,
            settings=settings,
            characters=_characters({"age": "少年", "personality": ""}),
            candidate_count=4,
        )
        candidates = plans[0].candidates
        assert len(candidates) == 4
        assert candidates[-1].voice_id == "f-elder"

    async def test_skips_character_without_id(self, tmp_path, monkeypatch) -> None:
        settings, layout, _adapter = _make_context(tmp_path, monkeypatch)
        plans = await build_preview_plan(
            layout=layout,
            settings=settings,
            characters=[{"name": "无名"}],
        )
        assert plans == []

    async def test_candidate_count_respected(self, tmp_path, monkeypatch) -> None:
        settings, layout, _adapter = _make_context(tmp_path, monkeypatch)
        plans = await build_preview_plan(
            layout=layout,
            settings=settings,
            characters=_characters(),
            candidate_count=2,
        )
        assert len(plans[0].candidates) == 2

    async def test_default_sample_text_mentions_character(self, tmp_path, monkeypatch) -> None:
        settings, layout, _adapter = _make_context(tmp_path, monkeypatch)
        plans = await build_preview_plan(
            layout=layout, settings=settings, characters=_characters()
        )
        assert "林小满" in plans[0].sample_text
        assert 30 <= len(plans[0].sample_text) <= 80

    async def test_explicit_sample_text_used(self, tmp_path, monkeypatch) -> None:
        settings, layout, _adapter = _make_context(tmp_path, monkeypatch)
        plans = await build_preview_plan(
            layout=layout,
            settings=settings,
            characters=_characters(),
            sample_text="自定义试听台词。",
        )
        assert plans[0].sample_text == "自定义试听台词。"


# ─── generate_candidate_previews: 批量合成与缓存 ─────────────────────────────


class TestGenerateCandidatePreviews:
    async def test_synthesizes_every_candidate(self, tmp_path, monkeypatch) -> None:
        settings, layout, adapter = _make_context(tmp_path, monkeypatch)
        plans = await build_preview_plan(
            layout=layout, settings=settings, characters=_characters()
        )
        generated = await generate_candidate_previews(
            layout=layout, settings=settings, plan=plans[0]
        )
        assert adapter.synthesize_calls == DEFAULT_CANDIDATE_COUNT
        for candidate in generated.candidates:
            assert candidate.sample_path
            assert not candidate.error
            assert Path(candidate.sample_path).is_file()

    async def test_cache_reused_on_second_generation(self, tmp_path, monkeypatch) -> None:
        settings, layout, adapter = _make_context(tmp_path, monkeypatch)
        plans = await build_preview_plan(
            layout=layout, settings=settings, characters=_characters()
        )
        await generate_candidate_previews(layout=layout, settings=settings, plan=plans[0])
        calls_after_first = adapter.synthesize_calls
        generated = await generate_candidate_previews(
            layout=layout, settings=settings, plan=plans[0]
        )
        assert adapter.synthesize_calls == calls_after_first
        for candidate in generated.candidates:
            assert candidate.sample_path

    async def test_failure_isolated_per_candidate(self, tmp_path, monkeypatch) -> None:
        settings, layout, adapter = _make_context(tmp_path, monkeypatch)
        adapter.fail_voice_ids = {"f-warm"}
        plans = await build_preview_plan(
            layout=layout, settings=settings, characters=_characters()
        )
        generated = await generate_candidate_previews(
            layout=layout, settings=settings, plan=plans[0]
        )
        failed = [c for c in generated.candidates if c.voice_id == "f-warm"][0]
        assert failed.error
        assert not failed.sample_path
        others = [c for c in generated.candidates if c.voice_id != "f-warm"]
        assert others
        assert all(c.sample_path for c in others)

    async def test_streaming_preferred_when_requested(self, tmp_path, monkeypatch) -> None:
        settings, layout, adapter = _make_context(tmp_path, monkeypatch)
        plans = await build_preview_plan(
            layout=layout, settings=settings, characters=_characters()
        )
        await generate_candidate_previews(
            layout=layout, settings=settings, plan=plans[0], streaming=True
        )
        assert adapter.streaming_calls == DEFAULT_CANDIDATE_COUNT
        assert adapter.synthesize_calls == DEFAULT_CANDIDATE_COUNT

    async def test_non_streaming_falls_back_to_plain(self, tmp_path, monkeypatch) -> None:
        settings, layout, adapter = _make_context(tmp_path, monkeypatch)
        plans = await build_preview_plan(
            layout=layout, settings=settings, characters=_characters()
        )
        await generate_candidate_previews(
            layout=layout, settings=settings, plan=plans[0], streaming=False
        )
        assert adapter.streaming_calls == 0
        assert adapter.synthesize_calls == DEFAULT_CANDIDATE_COUNT

    async def test_progress_callback(self, tmp_path, monkeypatch) -> None:
        settings, layout, _adapter = _make_context(tmp_path, monkeypatch)
        plans = await build_preview_plan(
            layout=layout, settings=settings, characters=_characters()
        )
        events: list[tuple[int, int]] = []
        await generate_candidate_previews(
            layout=layout,
            settings=settings,
            plan=plans[0],
            on_progress=lambda done, total: events.append((done, total)),
        )
        assert events == [(0, 3), (1, 3), (2, 3), (3, 3)]


# ─── confirm_preview: 写回配音团队 ───────────────────────────────────────────


class TestConfirmPreview:
    def test_updates_voice_identity_and_approval(self, tmp_path) -> None:
        settings = Settings(_env_file=None, tts_default_provider="mock")
        layout = ProjectLayout(tmp_path)
        layout.ensure_dirs()
        _write_team(layout)
        team = confirm_preview(
            layout=layout,
            settings=settings,
            character_id="c1",
            voice_id="f-warm",
            speed=1.0,
            volume=1.0,
            sample_text="试听文本。",
            sample_path="/tmp/preview.mp3",
        )
        entry = VoiceTeamContract.model_validate(team).get_entry("c1")
        assert entry is not None
        assert entry.voice_id == "f-warm"
        assert entry.voice_source == "system"
        assert entry.clone_status == VoiceCloneStatus.READY
        assert entry.approval_status == "approved"
        assert entry.provider == TTSProvider.MOCK

    def test_speed_volume_written_to_manual_overrides(self, tmp_path) -> None:
        settings = Settings(_env_file=None, tts_default_provider="mock")
        layout = ProjectLayout(tmp_path)
        layout.ensure_dirs()
        _write_team(layout)
        default_speed = float(settings.tts_default_speed)
        team = confirm_preview(
            layout=layout,
            settings=settings,
            character_id="c1",
            voice_id="f-warm",
            speed=default_speed + 0.1,
            volume=1.2,
        )
        entry = VoiceTeamContract.model_validate(team).get_entry("c1")
        assert entry is not None
        profile = entry.performance_profile
        assert profile is not None
        assert profile.manual_overrides.speed_offset == pytest.approx(0.1)
        assert profile.manual_overrides.vol_offset == pytest.approx(0.2)
        assert profile.derivation_source == "manual"

    def test_preview_variant_persisted(self, tmp_path) -> None:
        settings = Settings(_env_file=None, tts_default_provider="mock")
        layout = ProjectLayout(tmp_path)
        layout.ensure_dirs()
        _write_team(layout)
        team = confirm_preview(
            layout=layout,
            settings=settings,
            character_id="c1",
            voice_id="f-warm",
            sample_text="试听文本。",
            sample_path="/tmp/preview.mp3",
        )
        entry = VoiceTeamContract.model_validate(team).get_entry("c1")
        assert entry is not None
        variant = entry.get_variant_preview("identity")
        assert variant.audio_path == "/tmp/preview.mp3"
        assert variant.text == "试听文本。"

    def test_persisted_to_disk(self, tmp_path) -> None:
        settings = Settings(_env_file=None, tts_default_provider="mock")
        layout = ProjectLayout(tmp_path)
        layout.ensure_dirs()
        _write_team(layout)
        confirm_preview(
            layout=layout, settings=settings, character_id="c1", voice_id="f-warm"
        )
        reloaded = VoiceTeamContract.model_validate_json(
            layout.tts_voice_team_path.read_text(encoding="utf-8")
        )
        assert reloaded.get_entry("c1") is not None
        assert reloaded.get_entry("c1").voice_id == "f-warm"  # type: ignore[union-attr]

    def test_confirmation_reset_after_entry_mutation(self, tmp_path) -> None:
        settings = Settings(_env_file=None, tts_default_provider="mock")
        layout = ProjectLayout(tmp_path)
        layout.ensure_dirs()
        team = _write_team(layout)
        team.confirmed = True
        team.confirmed_at = team.updated_at
        layout.tts_voice_team_path.write_text(team.model_dump_json(), encoding="utf-8")
        updated = confirm_preview(
            layout=layout, settings=settings, character_id="c1", voice_id="f-warm"
        )
        reloaded = VoiceTeamContract.model_validate(updated)
        assert reloaded.confirmed is False

    def test_missing_team_raises(self, tmp_path) -> None:
        settings = Settings(_env_file=None, tts_default_provider="mock")
        layout = ProjectLayout(tmp_path)
        layout.ensure_dirs()
        with pytest.raises(ValueError, match="配音团队"):
            confirm_preview(
                layout=layout, settings=settings, character_id="c1", voice_id="f-warm"
            )

    def test_missing_character_raises(self, tmp_path) -> None:
        settings = Settings(_env_file=None, tts_default_provider="mock")
        layout = ProjectLayout(tmp_path)
        layout.ensure_dirs()
        _write_team(layout)
        with pytest.raises(ValueError, match="不在配音团队"):
            confirm_preview(
                layout=layout, settings=settings, character_id="nobody", voice_id="f-warm"
            )


# ─── 杂项 ────────────────────────────────────────────────────────────────────


def test_sample_text_helper_contains_name() -> None:
    text = _sample_text_for_character("阿明")
    assert "阿明" in text
    assert len(text) > 30


def test_plan_schema_validation() -> None:
    from novel_forge.tts.services.voice_preview import VoicePreviewCandidate

    candidate = VoicePreviewCandidate(voice_id="v1", voice_name="测试音色")
    assert candidate.gender == "neutral"
    with pytest.raises(ValidationError):
        VoicePreviewCandidate(voice_id="", voice_name="")  # type: ignore[arg-type]
