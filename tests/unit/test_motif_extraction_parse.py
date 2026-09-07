"""Regression tests for MotifTracker LLM extraction parsing."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from novel_forge.memory.base import MotifOccurrence
from novel_forge.memory.motif import AUTO_FORGET_RETIRE_REASON, Motif, MotifTracker


class _FakeBuilder:
    def build(self, task_type, context, **kwargs):
        return SimpleNamespace(task_type=task_type, context=context, kwargs=kwargs)


class _FakeRouter:
    def __init__(self, content: str) -> None:
        self.content = content

    async def route(self, request):
        return SimpleNamespace(content=self.content)


def test_record_occurrence_fallback_name_uses_text_snippet() -> None:
    tracker = MotifTracker(router=_FakeRouter("{}"), builder=_FakeBuilder())

    tracker._record_occurrence(
        MotifOccurrence(
            motif_id="motif_999",
            chapter_number=4,
            text_snippet="月光落在旧井边，照出一枚裂开的铜钱",
        )
    )

    assert tracker.motifs["motif_999"].name == "月光落在旧井边，照出一枚裂开的铜钱"


def test_record_occurrence_fallback_name_uses_id_suffix_without_snippet() -> None:
    tracker = MotifTracker(router=_FakeRouter("{}"), builder=_FakeBuilder())

    tracker._record_occurrence(
        MotifOccurrence(
            motif_id="motif_999",
            chapter_number=4,
            text_snippet="",
        )
    )

    assert tracker.motifs["motif_999"].name == "999"


@pytest.mark.asyncio
async def test_extract_motifs_accepts_string_occurrences() -> None:
    content = json.dumps(
        [
            {
                "motif_id": "motif_084",
                "category": "意象",
                "name": "纸条",
                "occurrences": [
                    "角门门缝中发现写着河北道仓场的纸条",
                    "三张纸条纸质相同、墨色一致",
                ],
            }
        ],
        ensure_ascii=False,
    )
    tracker = MotifTracker(router=_FakeRouter(content), builder=_FakeBuilder())

    occurrences = await tracker.extract_from_chapter(22, "第22章正文")

    assert len(occurrences) == 2
    assert occurrences[0].text_snippet == "角门门缝中发现写着河北道仓场的纸条"
    assert occurrences[0].context == occurrences[0].text_snippet
    assert tracker.motifs["motif_084"].name == "纸条"
    assert tracker.motifs["motif_084"].occurrence_count == 2
    assert tracker.motifs["motif_084"].last_appearance_chapter == 22


@pytest.mark.asyncio
async def test_motif_extraction_cache_stats_tracks_hit_miss_and_eviction() -> None:
    content = json.dumps(
        [
            {
                "motif_id": "motif_084",
                "category": "意象",
                "name": "纸条",
                "occurrences": ["纸条第一次出现"],
            }
        ],
        ensure_ascii=False,
    )
    tracker = MotifTracker(router=_FakeRouter(content), builder=_FakeBuilder())
    tracker._EXTRACTION_CACHE_MAX_SIZE = 1

    await tracker._llm_extract_motifs(1, "第一章正文")
    await tracker._llm_extract_motifs(1, "第一章正文")
    await tracker._llm_extract_motifs(2, "第二章正文")

    assert tracker.get_cache_stats() == {
        "hits": 1,
        "misses": 2,
        "evictions": 1,
        "size": 1,
        "max_size": 1,
    }


@pytest.mark.asyncio
async def test_extract_motifs_normalizes_dict_occurrence_characters() -> None:
    content = json.dumps(
        {
            "motifs": [
                {
                    "motif_id": "motif_085",
                    "category": "符号",
                    "name": "铜印",
                    "occurrences": [
                        {
                            "paragraph_index": "3",
                            "context": "沈照夜袖中露出缠枝莲纹铜印",
                            "associated_characters": "沈照夜",
                        }
                    ],
                }
            ]
        },
        ensure_ascii=False,
    )
    tracker = MotifTracker(router=_FakeRouter(content), builder=_FakeBuilder())

    occurrences = await tracker.extract_from_chapter(23, "第23章正文")

    assert len(occurrences) == 1
    assert occurrences[0].paragraph_index == 3
    assert occurrences[0].text_snippet == "沈照夜袖中露出缠枝莲纹铜印"
    assert occurrences[0].associated_characters == ["沈照夜"]
    assert tracker.motifs["motif_085"].associated_characters == ["沈照夜"]


@pytest.mark.asyncio
async def test_extract_motifs_accepts_keyed_motif_object_with_text_field() -> None:
    content = json.dumps(
        {
            "motif_002": {
                "category": "意象",
                "name": "账册",
                "occurrences": [
                    {"chapter": 30, "text": "原始账册上有太平公主私库的印鉴。"},
                    {"chapter": 30, "text": "公文与新卷并排放着。"},
                ],
            }
        },
        ensure_ascii=False,
    )
    tracker = MotifTracker(router=_FakeRouter(content), builder=_FakeBuilder())

    occurrences = await tracker.extract_from_chapter(30, "第30章正文")

    assert len(occurrences) == 2
    assert [occ.motif_id for occ in occurrences] == ["motif_002", "motif_002"]
    assert occurrences[0].text_snippet == "原始账册上有太平公主私库的印鉴。"
    assert occurrences[0].context == "原始账册上有太平公主私库的印鉴。"
    assert tracker.motifs["motif_002"].name == "账册"
    assert tracker.motifs["motif_002"].last_appearance_chapter == 30


@pytest.mark.asyncio
async def test_extract_motifs_persists_classification_metadata() -> None:
    content = json.dumps(
        {
            "motifs": [
                {
                    "motif_id": "motif_086",
                    "category": "符号",
                    "category_confidence": 0.91,
                    "category_reason": "铜印是贯穿案件的权力凭证，而非普通道具",
                    "secondary_categories": ["意象", "颜色", "不存在"],
                    "motif_role": "core_symbol",
                    "importance_score": 0.89,
                    "name": "铜印",
                    "occurrences": [{"text": "铜印压在密信角落。"}],
                }
            ]
        },
        ensure_ascii=False,
    )
    tracker = MotifTracker(router=_FakeRouter(content), builder=_FakeBuilder())

    occurrences = await tracker.extract_from_chapter(24, "第24章正文")

    assert len(occurrences) == 1
    motif = tracker.motifs["motif_086"]
    assert motif.category == "符号"
    assert motif.metadata["category_confidence"] == 0.91
    assert motif.metadata["category_reason"] == "铜印是贯穿案件的权力凭证，而非普通道具"
    assert motif.metadata["secondary_categories"] == ["意象", "颜色"]
    assert motif.metadata["category_status"] == "valid"
    assert motif.metadata["motif_role"] == "core_symbol"
    assert motif.metadata["importance_score"] == 0.89


@pytest.mark.asyncio
async def test_extract_motifs_reclassifies_existing_when_confident() -> None:
    first = json.dumps(
        {
            "motifs": [
                {
                    "motif_id": "motif_087",
                    "category": "意象",
                    "category_confidence": 0.45,
                    "name": "金镯",
                    "occurrences": [{"text": "金镯在灯下晃了一下。"}],
                }
            ]
        },
        ensure_ascii=False,
    )
    tracker = MotifTracker(router=_FakeRouter(first), builder=_FakeBuilder())
    await tracker.extract_from_chapter(25, "第25章正文")

    second = json.dumps(
        {
            "motifs": [
                {
                    "motif_id": "motif_087",
                    "category": "符号",
                    "category_confidence": 0.93,
                    "category_reason": "金镯反复绑定母女旧案和身份确认",
                    "name": "金镯",
                    "occurrences": [{"text": "她终于认出金镯内侧的刻痕。"}],
                }
            ]
        },
        ensure_ascii=False,
    )
    tracker._router = _FakeRouter(second)
    await tracker.extract_from_chapter(26, "第26章正文")

    motif = tracker.motifs["motif_087"]
    assert motif.category == "符号"
    assert motif.metadata["category_confidence"] == 0.93
    assert motif.metadata["category_reclassified"] is True
    assert motif.metadata["category_history"][-1]["from"] == "意象"


def test_prune_ephemeral_motifs_auto_retires_only_non_important() -> None:
    tracker = MotifTracker(router=_FakeRouter("{}"), builder=_FakeBuilder())
    tracker._motifs = {
        "one_off": Motif(
            motif_id="one_off",
            name="碎玻璃星光",
            category="意象",
            occurrence_count=1,
            first_appearance_chapter=2,
            last_appearance_chapter=2,
            metadata={"motif_role": "one_off_rhetoric", "importance_score": 0.2},
        ),
        "core": Motif(
            motif_id="core",
            name="金镯",
            category="符号",
            occurrence_count=1,
            first_appearance_chapter=1,
            last_appearance_chapter=1,
            metadata={"motif_role": "core_symbol", "importance_score": 0.9},
        ),
    }

    stats = tracker.prune_ephemeral_motifs(
        20,
        forget_after_chapters=12,
        max_occurrences=1,
        importance_threshold=0.35,
    )

    assert stats["count"] == 1
    assert tracker.motifs["one_off"].retired is True
    assert tracker.motifs["one_off"].metadata["retired_reason"] == AUTO_FORGET_RETIRE_REASON
    assert tracker.motifs["core"].retired is False


def test_auto_forgotten_motif_reactivates_when_seen_again() -> None:
    tracker = MotifTracker(router=_FakeRouter("{}"), builder=_FakeBuilder())
    tracker._motifs["one_off"] = Motif(
        motif_id="one_off",
        name="碎玻璃星光",
        category="意象",
        occurrence_count=1,
        first_appearance_chapter=2,
        last_appearance_chapter=2,
        retired=True,
        metadata={
            "motif_role": "one_off_rhetoric",
            "importance_score": 0.2,
            "retired_reason": AUTO_FORGET_RETIRE_REASON,
        },
    )

    tracker._record_occurrence(
        MotifOccurrence(
            motif_id="one_off",
            chapter_number=21,
            text_snippet="碎玻璃星光又一次落在她掌心。",
        )
    )

    motif = tracker.motifs["one_off"]
    assert motif.retired is False
    assert motif.metadata["retired_reason"] == ""
    assert motif.metadata["reactivated_chapter"] == 21
