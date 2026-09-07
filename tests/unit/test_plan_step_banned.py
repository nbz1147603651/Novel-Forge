"""Tests for PlanStep merging style_profile banned_phrases into hard_forbidden_elements."""

from __future__ import annotations

from novel_forge.core.schemas.continuity import ChapterBridge, ChapterStatePacket
from novel_forge.core.schemas.outline import ChapterOutline
from novel_forge.pipeline.steps.plan_step import PlanChapterStep


def _make_outline() -> ChapterOutline:
    return ChapterOutline(
        chapter_number=1,
        title="开场",
        goal="建立世界观",
        pov_character="主角",
        setting="起始地点",
        expected_word_count=3000,
    )


def _make_packet() -> ChapterStatePacket:
    return ChapterStatePacket(
        chapter_number=1,
        chapter_outline=_make_outline(),
        canon_context={},
        bridge=ChapterBridge(
            to_chapter=1,
            bridge_summary="开场",
        ),
    )


class TestPlanStep_StyleBannedPhrases:
    def test_style_banned_phrases_merged_into_hard_forbidden(self) -> None:
        style_profile = {
            "global_style": {
                "banned_phrases": ["陈词滥调一", "陈词滥调二"],
            }
        }
        normalized = PlanChapterStep._normalize_plan_payload(
            {},
            outline=_make_outline(),
            packet=_make_packet(),
            style_profile=style_profile,
        )
        forbidden = normalized.get("forbidden_elements", [])
        assert "陈词滥调一" in forbidden
        assert "陈词滥调二" in forbidden

    def test_missing_global_style(self) -> None:
        style_profile = {"other_field": "value"}
        normalized = PlanChapterStep._normalize_plan_payload(
            {},
            outline=_make_outline(),
            packet=_make_packet(),
            style_profile=style_profile,
        )
        forbidden = normalized.get("forbidden_elements", [])
        assert "陈词滥调" not in str(forbidden)

    def test_none_style_profile(self) -> None:
        normalized = PlanChapterStep._normalize_plan_payload(
            {},
            outline=_make_outline(),
            packet=_make_packet(),
            style_profile=None,
        )
        forbidden = normalized.get("forbidden_elements", [])
        assert isinstance(forbidden, list)

    def test_empty_banned_phrases(self) -> None:
        style_profile = {"global_style": {"banned_phrases": []}}
        normalized = PlanChapterStep._normalize_plan_payload(
            {},
            outline=_make_outline(),
            packet=_make_packet(),
            style_profile=style_profile,
        )
        forbidden = normalized.get("forbidden_elements", [])
        assert isinstance(forbidden, list)

    def test_combined_with_bridge_forbidden_repetition(self) -> None:
        packet = ChapterStatePacket(
            chapter_number=1,
            chapter_outline=_make_outline(),
            canon_context={},
            bridge=ChapterBridge(
                to_chapter=1,
                bridge_summary="开场",
                forbidden_repetition=["桥接禁用词"],
            ),
        )
        style_profile = {
            "global_style": {
                "banned_phrases": ["风格禁用词"],
            }
        }
        normalized = PlanChapterStep._normalize_plan_payload(
            {},
            outline=_make_outline(),
            packet=packet,
            style_profile=style_profile,
        )
        forbidden = normalized.get("forbidden_elements", [])
        soft = normalized.get("forbidden_elements_soft", [])
        assert "桥接禁用词" not in forbidden
        assert "桥接禁用词" in soft
        assert "风格禁用词" in forbidden
        assert {
            (item["text"], item["source"], item["level"])
            for item in normalized.get("forbidden_element_sources", [])
        } >= {
            ("桥接禁用词", "bridge", "soft"),
            ("风格禁用词", "style", "hard"),
        }

    def test_banned_phrases_truncated_to_120_chars(self) -> None:
        long_phrase = "X" * 200
        style_profile = {"global_style": {"banned_phrases": [long_phrase]}}
        normalized = PlanChapterStep._normalize_plan_payload(
            {},
            outline=_make_outline(),
            packet=_make_packet(),
            style_profile=style_profile,
        )
        forbidden = normalized.get("forbidden_elements", [])
        assert all(len(p) <= 120 for p in forbidden)

    def test_narrative_anchor_forbidden_moves_to_intentional_callbacks(self) -> None:
        normalized = PlanChapterStep._normalize_plan_payload(
            {
                "opening_contract": "以青铜怀表开启本章，直接承接上一章遗物线索。",
                "closing_contract": "章末让青铜怀表再次出现，作为下一章钩子。",
                "foreshadowing_plan": ["点明青铜怀表内藏地图夹层。"],
                "forbidden_elements": ["青铜怀表", "冷风"],
            },
            outline=_make_outline(),
            packet=_make_packet(),
            style_profile=None,
        )

        assert "青铜怀表" in normalized.get("intentional_callbacks", [])
        assert "青铜怀表" not in normalized.get("forbidden_elements", [])
        assert "冷风" in normalized.get("forbidden_elements", [])

    def test_owned_event_protects_matching_forbidden_anchor(self) -> None:
        normalized = PlanChapterStep._normalize_plan_payload(
            {
                "scene_intents": [
                    {
                        "summary": "梦境规则开始失衡",
                        "owned_events": ["梦境三层的时间规则让他陷入危险"],
                    }
                ],
                "forbidden_elements": ["梦境三层的时间规则让他陷入危险（改写为具体感知）"],
            },
            outline=_make_outline(),
            packet=_make_packet(),
            style_profile=None,
        )

        assert "梦境三层的时间规则让他陷入危险（改写为具体感知）" not in normalized.get(
            "forbidden_elements", []
        )

    def test_quota_forbidden_is_not_hard_when_required_by_plan(self) -> None:
        normalized = PlanChapterStep._normalize_plan_payload(
            {
                "closing_contract": "章末必须回到外滩钟楼，让老照片成为下一章交接点。",
                "scene_intents": [
                    {
                        "summary": "在外滩钟楼前完成交接",
                        "required_outcome": "外滩钟楼老照片被交到主角手里",
                    }
                ],
                "forbidden_elements": [
                    "外滩钟楼（场景锚点）——仅用一次",
                    "冷风",
                ],
            },
            outline=_make_outline(),
            packet=_make_packet(),
            style_profile=None,
        )

        assert "外滩钟楼（场景锚点）——仅用一次" not in normalized.get("forbidden_elements", [])
        assert "外滩钟楼（场景锚点）——仅用一次" in normalized.get("forbidden_elements_quota", [])
        assert "冷风" in normalized.get("forbidden_elements", [])

    def test_non_anchor_forbidden_stays_forbidden(self) -> None:
        normalized = PlanChapterStep._normalize_plan_payload(
            {
                "opening_contract": "直接切入争执现场。",
                "closing_contract": "章末留下新的行动压力。",
                "forbidden_elements": ["冷雨"],
            },
            outline=_make_outline(),
            packet=_make_packet(),
            style_profile=None,
        )

        assert "冷雨" not in normalized.get("intentional_callbacks", [])
        assert "冷雨" in normalized.get("forbidden_elements", [])
