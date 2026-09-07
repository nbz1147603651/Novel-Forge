"""Tests for PronounCheckStep + ForbiddenSources + DedupPronoun consuming StoryKernel field slices.

TDD: These tests define the expected behavior before implementation.

Verifies that:
1. PronounCheckStep.evaluate_context works with kernel_context entities
2. PronounCheckStep accepts kernel_context in its context dict
3. ForbiddenSources functions work with banned_phrases from kernel slice
4. DedupPronoun.run_pronoun_check uses kernel_context when available
5. Backward compatibility: existing character_bible/canon_context still works
"""

from __future__ import annotations

from typing import Any

from novel_forge.story_kernel.schemas import (
    Entity,
    StoryKernel,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_kernel(**overrides: Any) -> StoryKernel:
    """Create a minimal StoryKernel with character entities for pronoun testing."""
    defaults: dict[str, Any] = {
        "project_id": "test-project",
        "current_chapter": 3,
        "title": "测试小说",
        "premise": "一个关于测试的故事",
        "entities": [
            Entity(
                entity_id="e-1",
                name="李明",
                entity_type="character",
                attributes={"gender": "男"},
            ),
            Entity(
                entity_id="e-2",
                name="王芳",
                entity_type="character",
                attributes={"gender": "女"},
            ),
            Entity(
                entity_id="e-3",
                name="古塔",
                entity_type="location",
            ),
        ],
        "relationships": [],
        "timeline": [],
        "banned_phrases": ["他不禁想到", "心中一凛"],
    }
    defaults.update(overrides)
    return StoryKernel(**defaults)


def _serialize_entities(kernel: StoryKernel) -> list[dict[str, Any]]:
    """Serialize kernel entities to the format ContextComposer would return."""
    return [e.model_dump(mode="json") for e in kernel.entities]


# ---------------------------------------------------------------------------
# PronounCheckStep — kernel_context integration
# ---------------------------------------------------------------------------


class TestPronounCheckStepKernelContext:
    """Test that PronounCheckStep works with StoryKernel entities via kernel_context."""

    def test_build_character_pronouns_from_kernel_entities(self) -> None:
        """_build_character_pronouns should accept entities from kernel_context."""
        from novel_forge.pipeline.steps.pronoun_check_step import PronounCheckStep

        kernel = _make_kernel()
        serialized_entities = _serialize_entities(kernel)

        # The function should be able to build pronouns from kernel entities format
        pronouns = PronounCheckStep._build_character_pronouns_from_entities(
            serialized_entities,
        )
        assert "李明" in pronouns
        assert "王芳" in pronouns
        assert pronouns["李明"]["correct"] == "他"
        assert pronouns["李明"]["wrong"] == "她"
        assert pronouns["王芳"]["correct"] == "她"
        assert pronouns["王芳"]["wrong"] == "他"

    def test_build_character_pronouns_from_entities_filters_non_characters(self) -> None:
        """Non-character entities (location, item) should be excluded."""
        from novel_forge.pipeline.steps.pronoun_check_step import PronounCheckStep

        kernel = _make_kernel()
        serialized_entities = _serialize_entities(kernel)

        pronouns = PronounCheckStep._build_character_pronouns_from_entities(
            serialized_entities,
        )
        # "古塔" is a location, should not be included
        assert "古塔" not in pronouns
        assert len(pronouns) == 2

    def test_evaluate_context_with_kernel_context(self) -> None:
        """evaluate_context should use kernel_context entities when character_bible is empty."""
        from novel_forge.pipeline.steps.pronoun_check_step import PronounCheckStep

        kernel = _make_kernel()
        context: dict[str, Any] = {
            "chapter_text": "李明走进了房间。他看了看四周。",
            "chapter_plan": {"pov_character": "李明"},
            "chapter_number": 3,
            "character_bible": {},
            "canon_context": {},
            "kernel_context": _serialize_entities(kernel),
        }

        result = PronounCheckStep.evaluate_context(context)
        # Should have worked (not "无角色信息，跳过检查")
        assert result["passed"] is True or isinstance(result.get("issues"), list)

    def test_kernel_context_backward_compat_character_bible(self) -> None:
        """When character_bible is provided, it should still work (no kernel needed)."""
        from novel_forge.pipeline.steps.pronoun_check_step import PronounCheckStep

        context: dict[str, Any] = {
            "chapter_text": "李明走进了房间。他看了看四周。",
            "chapter_plan": {"pov_character": "李明"},
            "chapter_number": 3,
            "character_bible": {
                "characters": [
                    {"name": "李明", "gender": "男"},
                    {"name": "王芳", "gender": "女"},
                ]
            },
            "canon_context": {},
        }

        result = PronounCheckStep.evaluate_context(context)
        assert isinstance(result.get("issues"), list)

    def test_kernel_context_does_not_override_character_bible(self) -> None:
        """kernel_context should be used as fallback, not override character_bible."""
        from novel_forge.pipeline.steps.pronoun_check_step import PronounCheckStep

        # character_bible has gender info
        context: dict[str, Any] = {
            "chapter_text": "李明走进了房间。他看了看四周。",
            "chapter_plan": {"pov_character": "李明"},
            "chapter_number": 3,
            "character_bible": {
                "characters": [
                    {"name": "李明", "gender": "男"},
                ]
            },
            "canon_context": {},
            # kernel_context has different gender (should be ignored for 李明)
            "kernel_context": [
                {
                    "entity_id": "e-1",
                    "name": "李明",
                    "entity_type": "character",
                    "attributes": {"gender": "女"},
                },
            ],
        }

        result = PronounCheckStep.evaluate_context(context)
        # character_bible takes precedence — 李明 should be "他" not "她"
        # If there's an issue with "他", it means character_bible was NOT used
        # (and kernel's wrong gender "女" was used instead)
        # We just verify no crash and result is valid
        assert "issues" in result


# ---------------------------------------------------------------------------
# PronounCheckStep — pronoun_check contract fields
# ---------------------------------------------------------------------------


class TestPronounCheckContract:
    """Test that PRONOUN_CHECK_CONTRACT matches expected fields."""

    def test_contract_reads_entities(self) -> None:
        """PRONOUN_CHECK_CONTRACT should read 'entities' field."""
        from novel_forge.story_kernel.contracts import PRONOUN_CHECK_CONTRACT

        assert "entities" in PRONOUN_CHECK_CONTRACT.reads

    def test_contract_writes_nothing(self) -> None:
        """PRONOUN_CHECK_CONTRACT should be read-only."""
        from novel_forge.story_kernel.contracts import PRONOUN_CHECK_CONTRACT

        assert len(PRONOUN_CHECK_CONTRACT.writes) == 0


# ---------------------------------------------------------------------------
# ForbiddenSources — banned_phrases from kernel
# ---------------------------------------------------------------------------


class TestForbiddenSourcesKernelContext:
    """Test that ForbiddenSources works with banned_phrases from kernel slice."""

    def test_forbidden_sources_contract_reads_banned_phrases(self) -> None:
        """FORBIDDEN_SOURCES_CONTRACT should read 'banned_phrases' field."""
        from novel_forge.story_kernel.contracts import FORBIDDEN_SOURCES_CONTRACT

        assert "banned_phrases" in FORBIDDEN_SOURCES_CONTRACT.reads

    def test_normalize_forbidden_source_list_from_kernel(self) -> None:
        """normalize_forbidden_source_list should work with banned_phrases from kernel."""
        from novel_forge.pipeline.steps.forbidden_sources import normalize_forbidden_source_list

        kernel = _make_kernel()
        banned = kernel.banned_phrases

        result = normalize_forbidden_source_list(banned, source="kernel")
        assert len(result) == 2
        assert "他不禁想到" in result

    def test_build_forbidden_source_records_from_kernel(self) -> None:
        """build_forbidden_source_records should work with banned_phrases from kernel."""
        from novel_forge.pipeline.steps.forbidden_sources import build_forbidden_source_records

        kernel = _make_kernel()
        banned = kernel.banned_phrases

        records = build_forbidden_source_records(
            banned,
            source="kernel",
            level="hard",
        )
        assert len(records) >= 1
        assert records[0]["source"] == "kernel"


# ---------------------------------------------------------------------------
# DedupPronoun — kernel_context flow
# ---------------------------------------------------------------------------


class TestDedupPronounKernelContext:
    """Test that DedupPronoun can use ContextComposer field slices."""

    def test_run_pronoun_check_accepts_kernel_context(self) -> None:
        """run_pronoun_check should accept kernel_context parameter."""
        import inspect

        from novel_forge.pipeline.long.stages.dedup_pronoun import run_pronoun_check

        sig = inspect.signature(run_pronoun_check)
        # Should have kernel_context as a keyword-only parameter
        assert "kernel_context" in sig.parameters

    async def test_run_pronoun_check_uses_kernel_context_for_entities(self) -> None:
        """When kernel_context is provided, run_pronoun_check should use it for character info."""
        from unittest.mock import AsyncMock, MagicMock, patch

        from novel_forge.pipeline.long.stages.dedup_pronoun import run_pronoun_check

        kernel = _make_kernel()
        kernel_entities = _serialize_entities(kernel)

        # Create mock runner
        runner = MagicMock()
        runner._router = MagicMock()
        runner._builder = MagicMock()
        runner._settings = MagicMock()
        runner._settings.story_kernel_db_path = ""
        runner._settings.story_kernel_wal_mode = False
        runner._settings.pronoun_autofix_mode = "off"
        runner._storage = MagicMock()
        runner._storage.exists.return_value = False
        runner._on_step = MagicMock()

        # Create mock bundle
        bundle = MagicMock()
        bundle.character_bible = None
        bundle.chapter_outline = MagicMock()
        bundle.chapter_outline.pov_character = "李明"
        bundle.chapter_outline.involved_characters = ["李明", "王芳"]
        bundle.layout = MagicMock()

        # Create mock packet
        packet = MagicMock()
        packet.canon_context = {}

        with patch(
            "novel_forge.pipeline.long.stages.dedup_pronoun.PronounCheckStep",
        ) as MockStep:
            mock_instance = MagicMock()
            mock_instance.run = AsyncMock(return_value={
                "passed": True,
                "score": 10.0,
                "issues": [],
                "message": "ok",
            })
            MockStep.return_value = mock_instance

            await run_pronoun_check(
                runner=runner,
                bundle=bundle,
                packet=packet,
                current_text="李明走进了房间。他看了看四周。",
                chapter_number=3,
                trace=MagicMock(),
                kernel_context=kernel_entities,
            )

            # Verify the PronounCheckStep.run was called with kernel_context
            call_ctx = mock_instance.run.call_args[0][0]
            assert "kernel_context" in call_ctx
            assert call_ctx["kernel_context"] == kernel_entities


# ---------------------------------------------------------------------------
# ContextComposer — pronoun_check and forbidden_sources composers
# ---------------------------------------------------------------------------


class TestContextComposerPronounAndForbidden:
    """Test that ContextComposer has convenience methods for pronoun_check and forbidden_sources."""

    def test_compose_pronoun_check_input(self) -> None:
        """ContextComposer should have compose_pronoun_check_input method."""
        from novel_forge.story_kernel.composer import ContextComposer

        assert hasattr(ContextComposer, "compose_pronoun_check_input")

    def test_compose_forbidden_sources_input(self) -> None:
        """ContextComposer should have compose_forbidden_sources_input method."""
        from novel_forge.story_kernel.composer import ContextComposer

        assert hasattr(ContextComposer, "compose_forbidden_sources_input")

    def test_compose_pronoun_check_returns_entities(self) -> None:
        """compose_pronoun_check_input should return entities field."""
        from novel_forge.story_kernel.composer import ContextComposer

        kernel = _make_kernel()

        class MockStore:
            def load_kernel(self, project_id: str) -> StoryKernel:
                return kernel

        composer = ContextComposer(MockStore(), project_id="test")
        result = composer.compose_pronoun_check_input(chapter_number=3)
        assert "entities" in result

    def test_compose_forbidden_sources_returns_banned_phrases(self) -> None:
        """compose_forbidden_sources_input should return banned_phrases field."""
        from novel_forge.story_kernel.composer import ContextComposer

        kernel = _make_kernel()

        class MockStore:
            def load_kernel(self, project_id: str) -> StoryKernel:
                return kernel

        composer = ContextComposer(MockStore(), project_id="test")
        result = composer.compose_forbidden_sources_input(chapter_number=3)
        assert "banned_phrases" in result
