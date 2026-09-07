"""Unit tests for voice strategy — minor characters prefer system voices (P3-4).

Covers:
- is_minor_character role classification (side roles vs protagonists)
- _assign_voice_for_character falls back to system/default voices for
  minor characters instead of billable AI design
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from novel_forge.tts.pipeline.build_voice_team_step import is_minor_character
from novel_forge.tts.schemas import TTSProvider

# ─── is_minor_character ──────────────────────────────────────────────────────


class TestIsMinorCharacter:
    def test_side_roles_are_minor(self) -> None:
        for role in ("龙套", "路人甲", "配角", "客串角色", "次要角色", "npc", "supporting"):
            assert is_minor_character({"role": role})

    def test_protagonists_never_minor(self) -> None:
        for role in ("主角", "重要配角", "protagonist", "main character", "lead"):
            assert not is_minor_character({"role": role})

    def test_missing_role_is_not_minor(self) -> None:
        assert not is_minor_character({})
        assert not is_minor_character({"role": ""})
        assert not is_minor_character({"name": "张三"})

    def test_role_label_fallback(self) -> None:
        assert is_minor_character({"role_label": "龙套"})
        assert not is_minor_character({"role_label": "主角"})


# ─── _assign_voice_for_character minor fallback ──────────────────────────────


class TestMinorVoiceAssignment:
    @pytest.mark.asyncio
    async def test_minor_character_never_reaches_billable_design(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Minor characters must never reach billable AI design."""
        from novel_forge.tts.pipeline.build_voice_team_step import BuildVoiceTeamStep

        settings = SimpleNamespace(
            tts_default_model="speech-2.8-hd",
            tts_clone_model="speech-2.8-hd",
            tts_design_model="speech-2.8-hd",
        )
        step = BuildVoiceTeamStep(SimpleNamespace(), settings=settings)
        monkeypatch.setattr(
            "novel_forge.tts.pipeline.build_voice_team_step.resolve_tts_model",
            lambda settings, provider, purpose="formal": "speech-2.8-hd",
        )

        billable_hit = {"count": 0}

        async def _billable(*args: object, **kwargs: object) -> None:
            billable_hit["count"] += 1
            raise AssertionError("billable path must not run for minor characters")

        adapter = SimpleNamespace(
            provider_type=TTSProvider.MINIMAX,
            design_voice=_billable,
            clone_voice=_billable,
            prepare_clone_source=_billable,
        )
        character = {
            "character_id": "c1",
            "name": "店小二",
            "role": "龙套",
            "gender": "male",
            "voice_description": "",
        }

        entry = await step._assign_voice_for_character(
            character,
            adapter=adapter,
            provider=TTSProvider.MINIMAX,
            available_voices=[],
            voice_design_enabled=True,
            voice_library=None,
        )
        assert billable_hit["count"] == 0
        assert entry.voice_source == "system"
        assert entry.voice_id
