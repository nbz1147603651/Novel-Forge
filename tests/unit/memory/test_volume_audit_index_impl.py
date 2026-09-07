"""Tests for VolumeAuditIndex persistence in EpisodicMemory."""

from __future__ import annotations

import pytest

from novel_forge.core.schemas.volume import VolumeAuditReport
from novel_forge.memory.episodic import EpisodicMemory


@pytest.fixture
def episodic_memory() -> EpisodicMemory:
    return EpisodicMemory(use_mock_embeddings=True)


def _make_report(
    volume_number: int = 1,
    volume_title: str = "Test Volume",
    chapter_range: str = "1-10",
    volume_summary: str = "A test volume summary for indexing.",
    consistency_score: float = 8.5,
    consistency_issues: list[str] | None = None,
    carry_over_characters: list[str] | None = None,
    carry_over_items: list[str] | None = None,
    carry_over_world_fact_keys: list[str] | None = None,
    carry_over_foreshadowing_ids: list[str] | None = None,
    next_volume_focus: str = "Focus on next arc",
) -> VolumeAuditReport:
    return VolumeAuditReport(
        volume_number=volume_number,
        volume_title=volume_title,
        chapter_range=chapter_range,
        volume_summary=volume_summary,
        consistency_score=consistency_score,
        consistency_issues=consistency_issues or [],
        carry_over_characters=carry_over_characters or [],
        carry_over_items=carry_over_items or [],
        carry_over_world_fact_keys=carry_over_world_fact_keys or [],
        carry_over_foreshadowing_ids=carry_over_foreshadowing_ids or [],
        next_volume_focus=next_volume_focus,
    )


class TestIndexVolumeAudit:
    @pytest.mark.asyncio
    async def test_index_volume_audit(self, episodic_memory: EpisodicMemory) -> None:
        report = _make_report()
        sig = await episodic_memory.index_volume_audit(report)

        assert sig is not None
        assert isinstance(sig, str)
        assert len(sig) == 32  # MD5 hex digest length
        assert sig in episodic_memory._volume_audit_index

        entry = episodic_memory._volume_audit_index[sig]
        assert entry.volume_number == 1
        assert entry.volume_title == "Test Volume"
        assert entry.chapter_range == "1-10"
        assert entry.consistency_score == 8.5
        assert entry.embedding  # embedding was generated

    @pytest.mark.asyncio
    async def test_index_volume_audit_idempotent(
        self, episodic_memory: EpisodicMemory
    ) -> None:
        report = _make_report()
        sig1 = await episodic_memory.index_volume_audit(report)
        sig2 = await episodic_memory.index_volume_audit(report)

        assert sig1 == sig2
        assert len(episodic_memory._volume_audit_index) == 1

    @pytest.mark.asyncio
    async def test_index_volume_audit_persistence(
        self, episodic_memory: EpisodicMemory
    ) -> None:
        report = _make_report(
            volume_number=2,
            consistency_score=7.0,
            chapter_range="11-20",
        )
        sig = await episodic_memory.index_volume_audit(report)

        vector_store = episodic_memory._vector_store
        assert vector_store is not None

        entry = episodic_memory._volume_audit_index[sig]
        results = vector_store.search(entry.embedding, top_k=1)
        assert len(results) == 1
        found_sig, _score, metadata = results[0]
        assert found_sig == sig
        assert metadata["volume_number"] == 2
        assert metadata["consistency_score"] == 7.0
        assert metadata["chapter_range"] == "11-20"

    @pytest.mark.asyncio
    async def test_index_volume_audit_with_empty_report(
        self, episodic_memory: EpisodicMemory
    ) -> None:
        report = _make_report(
            volume_summary="",
            volume_title="",
            chapter_range="",
            next_volume_focus="",
        )
        sig = await episodic_memory.index_volume_audit(report)

        assert sig is not None
        assert sig in episodic_memory._volume_audit_index
        entry = episodic_memory._volume_audit_index[sig]
        assert entry.volume_summary == ""
        assert entry.embedding is not None
