"""Tests for ExtractCanonDeltaStep payload normalization safeguards."""

from __future__ import annotations

from novel_forge.core.schemas.chapter import ChapterOutcome
from novel_forge.pipeline.steps.extract_step import ExtractCanonDeltaStep


def test_normalize_extracted_payload_handles_common_shape_errors() -> None:
    raw = {
        "canon_delta": {
            "source_chapter": 10,
            "character_updates": {
                "林屿": {
                    "alive": True,
                    "location": "第七幅涂鸦正上方",
                    "inventory": {"腕表": "指针停在13:07", "针芯": "游丝微颤"},
                    "knowledge": "掌握‘今知彼亦知我在’",
                }
            },
            "new_events": [
                "老陈在林屿手账上写下“a better share”",
                "铜铃再次响起，象征时间重启可能",
            ],
            "foreshadowing_updates": [
                {
                    "id": "fs_bell",
                    "description": "铜铃意象推进",
                    "planted_chapter": 8,
                    "status": "confirmed",
                }
            ],
            "new_world_facts": {},
        },
        "chapter_summary": "本章围绕‘彼此可见’展开。",
    }

    normalized = ExtractCanonDeltaStep._normalize_extracted_payload(raw, chapter_number=10)
    extracted = ChapterOutcome.model_validate(normalized)

    char_state = extracted.character_updates["林屿"]
    assert char_state.name == "林屿"
    assert isinstance(char_state.inventory, list)
    assert isinstance(char_state.knowledge, list)
    assert extracted.new_events[0].event
    assert extracted.foreshadowing_updates[0].status == "paid"
    assert extracted.creative_report.new_characters == []
    assert extracted.chapter_summary == "本章围绕‘彼此可见’展开。"


def test_normalize_extracted_payload_supports_flat_legacy_payload() -> None:
    raw = {
        "source_chapter": "11",
        "character_updates": [{"name": "阿宁", "inventory": "旧钥匙"}],
        "new_events": "阿宁打开旧档案室铁门",
        "foreshadowing_updates": "墙上旧纹样再次出现",
        "new_world_facts": {"门后": "旧档案室"},
        "chapter_summary": "阿宁发现关键档案。",
        "new_locations": "旧档案室",
        "creative_highlights": "压迫感氛围稳定",
    }

    normalized = ExtractCanonDeltaStep._normalize_extracted_payload(raw, chapter_number=11)
    extracted = ChapterOutcome.model_validate(normalized)

    assert extracted.source_chapter == 11
    assert extracted.character_updates["阿宁"].inventory == ["旧钥匙"]
    assert extracted.new_events[0].event == "阿宁打开旧档案室铁门"
    assert extracted.creative_report.new_locations == ["旧档案室"]
    assert extracted.creative_report.creative_highlights == ["压迫感氛围稳定"]
