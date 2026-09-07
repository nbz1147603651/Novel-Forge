"""Tests for v2 ChapterOutcome extraction normalization."""

from __future__ import annotations

from novel_forge.core.schemas.chapter import ChapterOutcome
from novel_forge.pipeline.steps.extract_step import ExtractCanonDeltaStep


def test_extract_step_normalizes_v2_outcome_payload() -> None:
    raw = {
        "canon_delta": {
            "source_chapter": 3,
            "character_updates": {
                "林远": {
                    "alive": True,
                    "location": "裂缝入口",
                    "emotional_state": "紧张",
                    "inventory": ["怀表"],
                    "knowledge": ["裂缝会回应怀表"],
                }
            },
            "new_events": ["林远触碰裂缝后看见未来残影"],
            "chapter_summary": "林远正式接触裂缝。",
        },
        "creative_report": {
            "must_carry_forward": ["未来残影的含义必须在下一章回应"],
            "bridge_hints": ["下一章直接承接残影后果"],
        },
        "chapter_exit_state": {
            "chapter_number": 3,
            "time_marker": "第一天，深夜",
            "location": "裂缝入口",
            "pov": "林远",
            "active_goals": ["确认残影身份"],
            "open_questions": ["残影是不是未来的自己"],
        },
        "character_state_deltas": [
            {
                "name": "林远",
                "change_summary": "开始主动追索未来残影真相",
                "to_state": {
                    "name": "林远",
                    "alive": True,
                    "location": "裂缝入口",
                    "emotional_state": "紧张",
                    "inventory": ["怀表"],
                    "knowledge": ["裂缝会回应怀表"],
                },
            }
        ],
        "relationship_deltas": [
            {
                "pair_id": "林远__老守夜人",
                "change_summary": "林远对老守夜人更警惕",
                "relationship": {
                    "pair_id": "林远__老守夜人",
                    "characters": ["林远", "老守夜人"],
                    "trust": 0.3,
                    "tension": 0.8,
                },
            }
        ],
        "plot_thread_deltas": [
            {
                "thread_id": "future_echo",
                "change_summary": "未来残影线程被开启",
                "thread": {"thread_id": "future_echo", "title": "未来残影"},
            }
        ],
        "structured_summary": "本章让裂缝第一次直接给出未来回声。",
    }

    normalized = ExtractCanonDeltaStep._normalize_extracted_payload(raw, chapter_number=3)
    outcome = ChapterOutcome.model_validate(normalized)

    assert outcome.chapter_exit_state.location == "裂缝入口"
    assert outcome.character_state_deltas[0].to_state.location == "裂缝入口"
    assert outcome.relationship_deltas[0].relationship.trust == 0.3
    assert outcome.plot_thread_deltas[0].thread.title == "未来残影"
    assert outcome.structured_summary == "本章让裂缝第一次直接给出未来回声。"


def test_extract_step_recovers_embedded_top_level_fields_and_splits_relationship_names() -> None:
    raw = {
        "canon_delta": {
            "source_chapter": 8,
            "character_updates": {
                "周明": {
                    "alive": True,
                    "location": "西官仓耳房",
                    "emotional_state": "冷静",
                },
                "creative_report": {
                    "must_carry_forward": ["周明必须解释自己为何离开耳房"],
                },
                "chapter_exit_state": {
                    "chapter_number": 8,
                    "location": "西官仓耳房",
                    "pov": "周明",
                },
                "relationship_deltas": [
                    {
                        "change_summary": "双方彻底摊牌",
                        "relationship": {
                            "characters": "周明 & 王秉忠",
                            "trust": 0.1,
                            "tension": 0.9,
                        },
                    }
                ],
                "structured_summary": "周明与王秉忠公开对质。",
            },
        }
    }

    normalized = ExtractCanonDeltaStep._normalize_extracted_payload(raw, chapter_number=8)
    outcome = ChapterOutcome.model_validate(normalized)

    assert list(outcome.character_updates.keys()) == ["周明"]
    assert outcome.creative_report.must_carry_forward == ["周明必须解释自己为何离开耳房"]
    assert outcome.chapter_exit_state.location == "西官仓耳房"
    assert outcome.relationship_deltas[0].relationship.characters == ["周明", "王秉忠"]
    assert outcome.structured_summary == "周明与王秉忠公开对质。"
