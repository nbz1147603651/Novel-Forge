"""E2E tests for episodic_context flow: chapter_runner → VolumeAuditInput → VolumeAuditStep → LLM payload → template rendering."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from novel_forge.common.constants import TaskType
from novel_forge.core.schemas.outline import VolumeOutline
from novel_forge.memory.episodic import EpisodicMemory
from novel_forge.memory.integration import MemoryContext
from novel_forge.pipeline.steps.volume_step import VolumeAuditInput, VolumeAuditStep
from novel_forge.prompts.builder import PromptBuilder


def _make_volume(title: str = "觉醒", arc_goal: str = "主角觉醒") -> VolumeOutline:
    return VolumeOutline(
        volume_number=1,
        title=title,
        start_chapter=1,
        end_chapter=10,
        arc_goal=arc_goal,
    )


@pytest.fixture
def mock_router():
    return MagicMock()


@pytest.fixture
def mock_builder():
    return MagicMock()


@pytest.fixture
def mock_settings():
    settings = MagicMock()
    settings.temp_volume_audit = 0.7
    return settings


@pytest.fixture
def step(mock_router, mock_builder, mock_settings):
    return VolumeAuditStep(
        router=mock_router,
        builder=mock_builder,
        settings=mock_settings,
    )


class TestFullEpisodicFlowToLLM:
    """Test the complete flow from EpisodicMemory → LLM payload."""

    async def test_full_episodic_flow_to_llm(self, step) -> None:
        """Flow: EpisodicMemory.search_similar_volumes → MemoryContext → episodic_context → VolumeAuditInput → payload."""
        # 1. Create mock EpisodicMemory with search_similar_volumes returning 2 results
        mock_episodic = AsyncMock(spec=EpisodicMemory)
        mock_episodic.search_similar_volumes.return_value = [
            {
                "volume_number": 3,
                "relevance_score": 0.85,
                "consistency_score": 8.5,
                "carry_over_characters": ["林晚", "周临"],
                "carry_over_items": ["神秘符号"],
            },
            {
                "volume_number": 7,
                "relevance_score": 0.72,
                "consistency_score": 7.8,
                "carry_over_characters": ["林晚"],
                "carry_over_items": [],
            },
        ]

        # 2. Create mock MemoryContext with episodic_memory
        mock_memory_context = MagicMock(spec=MemoryContext)
        mock_memory_context.episodic_memory = mock_episodic

        # 3. Simulate chapter_runner building episodic_context (lines 673-700)
        volume = _make_volume(title="秘境探险", arc_goal="主角探索秘境")
        query_text = volume.title or volume.arc_goal

        episodic_context: dict | None = None
        if mock_memory_context is not None and mock_memory_context.episodic_memory is not None:
            try:
                if query_text:
                    episodic_context = {
                        "similar_volumes": await mock_memory_context.episodic_memory.search_similar_volumes(
                            query_text=query_text,
                            top_k=3,
                        )
                    }
            except Exception:
                pass

        # 4. Create VolumeAuditInput with episodic_context
        audit_input = VolumeAuditInput(
            volume=volume,
            story_synopsis="Test synopsis",
            chapter_summaries=[{"chapter": 1, "summary": "Ch1"}],
            timeline_events=[],
            active_characters=[],
            active_foreshadowing=[],
            world_fact_keys=[],
            episodic_context=episodic_context,
        )

        assert audit_input.episodic_context is not None
        assert len(audit_input.episodic_context["similar_volumes"]) == 2

        # 5. Mock VolumeAuditStep._call_with_retry to capture payload
        captured_payload = None

        async def capture_call(task_type, payload, **kwargs):
            nonlocal captured_payload
            captured_payload = payload
            return {
                "volume_number": 1,
                "volume_title": "Test",
                "chapter_range": "1-10",
                "volume_summary": "Test summary",
                "consistency_score": 8.0,
                "consistency_issues": [],
                "carry_over_characters": [],
                "retire_characters": [],
                "carry_over_items": [],
                "retire_items": [],
                "carry_over_world_fact_keys": [],
                "retire_world_fact_keys": [],
                "carry_over_foreshadowing_ids": [],
                "resolved_foreshadowing_ids": [],
                "next_volume_focus": "",
                "token_optimization_notes": [],
                "milestone_status": [],
            }

        # 6. Assert payload contains episodic_context with similar_volumes
        with patch.object(step, "_call_with_retry", side_effect=capture_call):
            await step._execute(audit_input)

        assert captured_payload is not None
        assert "episodic_context" in captured_payload
        assert "similar_volumes" in captured_payload["episodic_context"]
        assert len(captured_payload["episodic_context"]["similar_volumes"]) == 2

        # Verify the structure of similar_volumes entries
        vols = captured_payload["episodic_context"]["similar_volumes"]
        assert vols[0]["volume_number"] == 3
        assert vols[0]["relevance_score"] == 0.85
        assert vols[1]["volume_number"] == 7
        assert vols[1]["relevance_score"] == 0.72

        # Verify search was called with correct args
        mock_episodic.search_similar_volumes.assert_called_once_with(
            query_text="秘境探险",
            top_k=3,
        )


class TestTemplateRendersEpisodicContext:
    """Test that volume_audit.j2 template correctly renders episodic_context."""

    def test_template_renders_episodic_context(self) -> None:
        """Template renders episodic_context with similar_volumes containing volume_number and relevance_score."""
        builder = PromptBuilder()

        episodic_context = {
            "similar_volumes": [
                {
                    "volume_number": 3,
                    "relevance_score": 0.85,
                    "consistency_score": 8.5,
                    "carry_over_characters": ["林晚", "周临"],
                    "carry_over_items": ["神秘符号"],
                },
                {
                    "volume_number": 7,
                    "relevance_score": 0.72,
                    "consistency_score": 7.8,
                    "carry_over_characters": ["林晚"],
                    "carry_over_items": [],
                },
            ],
        }

        context = {
            "volume": {
                "volume_number": 1,
                "title": "觉醒",
                "start_chapter": 1,
                "end_chapter": 10,
                "arc_goal": "主角觉醒",
            },
            "story_synopsis": "一个关于觉醒与成长的小说。",
            "chapter_summaries": [
                {"chapter": 1, "summary": "主角首次发现自己有异能"},
            ],
            "timeline_events": [
                {"chapter": 1, "event": "异能觉醒"},
            ],
            "active_characters": [
                {"name": "林晚", "alive": True, "location": "城市", "inventory": ["手机"]},
            ],
            "active_foreshadowing": [
                {"id": "FS-01", "description": "神秘符号", "status": "active"},
            ],
            "world_fact_keys": ["近未来城市", "异能者存在"],
            "episodic_context": episodic_context,
        }

        rendered = builder.render(TaskType.VOLUME_AUDIT, context)

        # 1. Assert rendered prompt contains "历史相似卷参考"
        assert "历史相似卷参考" in rendered

        # 2. Assert rendered prompt contains volume_number and relevance_score from episodic_context
        assert "第3卷（相似度: 0.85）" in rendered
        assert "第7卷（相似度: 0.72）" in rendered

        # Verify consistency_score and carry_over are also rendered
        assert "一致性评分: 8.5" in rendered
        assert "一致性评分: 7.8" in rendered
        assert "林晚、周临" in rendered
        assert "林晚" in rendered
