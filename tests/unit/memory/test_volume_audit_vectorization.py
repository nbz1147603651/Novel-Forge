"""Tests for VolumeAuditIndex and volume audit vectorization interface."""

from __future__ import annotations

import pytest

from novel_forge.memory.episodic import EpisodicMemory, VolumeAuditIndex


@pytest.fixture
def episodic_memory() -> EpisodicMemory:
    return EpisodicMemory(use_mock_embeddings=True)


class TestVolumeAuditIndex:
    def test_volume_audit_index_structure(self) -> None:
        entry = VolumeAuditIndex(
            volume_number=1,
            volume_title="第一卷：开端",
            chapter_range="1-20",
            volume_summary="讲述主角从平凡到觉醒的转变",
            consistency_score=8.5,
            consistency_issues=["时间线轻微矛盾", "角色称呼前后不一"],
            carry_over_characters=["张三", "李四"],
            carry_over_items=["青铜剑", "神秘地图"],
            carry_over_world_fact_keys=["修炼体系", "宗门等级"],
            carry_over_foreshadowing_ids=["foreshadow_001", "foreshadow_002"],
            next_volume_focus="主角的成长与宗门试炼",
        )
        sig = entry.signature()
        assert len(sig) == 32
        assert all(c in "0123456789abcdef" for c in sig)

    def test_volume_audit_signature_uniqueness(self) -> None:
        entry1 = VolumeAuditIndex(
            volume_number=1,
            volume_title="第一卷",
            chapter_range="1-20",
            volume_summary="相同的摘要内容" * 10,
            consistency_score=8.0,
            consistency_issues=[],
            carry_over_characters=[],
            carry_over_items=[],
            carry_over_world_fact_keys=[],
            carry_over_foreshadowing_ids=[],
            next_volume_focus="",
        )
        entry2 = VolumeAuditIndex(
            volume_number=2,
            volume_title="第一卷",
            chapter_range="1-20",
            volume_summary="相同的摘要内容" * 10,
            consistency_score=8.0,
            consistency_issues=[],
            carry_over_characters=[],
            carry_over_items=[],
            carry_over_world_fact_keys=[],
            carry_over_foreshadowing_ids=[],
            next_volume_focus="",
        )
        assert entry1.signature() != entry2.signature()
        assert entry1.signature() == entry1.signature()


class TestSearchSimilarVolumes:
    async def test_search_similar_volumes_empty(self, episodic_memory: EpisodicMemory) -> None:
        results = await episodic_memory.search_similar_volumes(
            query_text="寻找类似卷审计",
            top_k=3,
            min_relevance=0.5,
        )
        assert results == []
