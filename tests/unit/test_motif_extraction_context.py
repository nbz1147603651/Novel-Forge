"""Tests for context-enriched motif extraction (chapter_outline in prompt)."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from novel_forge.common.constants import TaskType
from novel_forge.memory.motif import Motif, MotifTracker


class _CapturingBuilder:
    """Fake builder that captures the context dict passed to build()."""

    def __init__(self) -> None:
        self.captured_context: dict | None = None

    def build(self, task_type: TaskType, context: dict, **kwargs):
        self.captured_context = dict(context)
        return SimpleNamespace(task_type=task_type, context=context, kwargs=kwargs)


class _FakeRouter:
    def __init__(self, content: str | list[str]) -> None:
        self.contents = content if isinstance(content, list) else [content]
        self.last_request = None
        self.route_calls = 0

    async def route(self, request):
        self.last_request = request
        index = min(self.route_calls, len(self.contents) - 1)
        self.route_calls += 1
        return SimpleNamespace(content=self.contents[index])


def _make_tracker_with_captures(
    router_content: str = "{}",
) -> tuple[MotifTracker, _CapturingBuilder, _FakeRouter]:
    builder = _CapturingBuilder()
    router = _FakeRouter(router_content)
    tracker = MotifTracker(router=router, builder=builder)
    return tracker, builder, router


class TestOutlineFieldsInPrompt:
    """When chapter_outline is passed, verify the prompt dict contains
    chapter_goal, pov_character, element_focus."""

    @pytest.mark.asyncio
    async def test_outline_fields_in_prompt(self) -> None:
        outline = {
            "goal": "揭示主角的身世之谜",
            "pov_character": "林远",
            "element_focus": ["悬念", "伏笔回收"],
            "chapter_contract": "本章必须让金镯成为身份线索",
            "required_outcomes": ["林远发现刻痕", "反派转移账册"],
            "motif_requirements": {"金镯": "身份与债务"},
        }
        tracker, builder, router = _make_tracker_with_captures(
            json.dumps({"motifs": []}, ensure_ascii=False)
        )

        await tracker.extract_from_chapter(5, "第5章正文", chapter_outline=outline)

        ctx = builder.captured_context
        assert ctx is not None
        assert ctx["chapter_goal"] == "揭示主角的身世之谜"
        assert ctx["pov_character"] == "林远"
        assert ctx["element_focus"] == ["悬念", "伏笔回收"]
        assert ctx["motif_extraction_context"]["chapter_contract"] == "本章必须让金镯成为身份线索"
        assert "林远发现刻痕" in ctx["motif_extraction_context"]["required_outcomes"]
        assert "金镯" in ctx["motif_extraction_context"]["motif_requirements"]


class TestNoOutlineBackwardCompat:
    """When chapter_outline is None, verify the prompt dict has empty strings
    for the 3 fields (not None, not missing)."""

    @pytest.mark.asyncio
    async def test_no_outline_backward_compat(self) -> None:
        tracker, builder, router = _make_tracker_with_captures(
            json.dumps({"motifs": []}, ensure_ascii=False)
        )

        await tracker.extract_from_chapter(3, "第3章正文", chapter_outline=None)

        ctx = builder.captured_context
        assert ctx is not None
        assert ctx["chapter_goal"] == ""
        assert ctx["pov_character"] == ""
        assert ctx["element_focus"] == []


class TestElementFocusProjection:
    """The producer keeps element focus structured until template rendering."""

    @pytest.mark.asyncio
    async def test_element_focus_remains_a_list(self) -> None:
        outline = {
            "goal": "战斗场景",
            "pov_character": "沈照夜",
            "element_focus": ["动作描写", "感官细节", "节奏控制"],
        }
        tracker, builder, router = _make_tracker_with_captures(
            json.dumps({"motifs": []}, ensure_ascii=False)
        )

        await tracker.extract_from_chapter(10, "第10章正文", chapter_outline=outline)

        ctx = builder.captured_context
        assert ctx is not None
        assert ctx["element_focus"] == ["动作描写", "感官细节", "节奏控制"]

    @pytest.mark.asyncio
    async def test_element_focus_empty_list(self) -> None:
        outline = {
            "goal": "日常场景",
            "pov_character": "林远",
            "element_focus": [],
        }
        tracker, builder, router = _make_tracker_with_captures(
            json.dumps({"motifs": []}, ensure_ascii=False)
        )

        await tracker.extract_from_chapter(7, "第7章正文", chapter_outline=outline)

        ctx = builder.captured_context
        assert ctx is not None
        assert ctx["element_focus"] == []


class TestIntentionalClassificationWithContext:
    """Mock LLM response that sets is_intentional=True when chapter_goal
    matches motif theme."""

    @pytest.mark.asyncio
    async def test_intentional_classification_with_context(self) -> None:
        outline = {
            "goal": "月光下的告别仪式",
            "pov_character": "林远",
            "element_focus": ["意象"],
        }
        llm_response = json.dumps(
            {
                "motifs": [
                    {
                        "motif_id": "motif_001",
                        "name": "月光",
                        "category": "意象",
                        "description": "月光贯穿告别场景",
                        "thematic_meaning": "月光→主人公内心转变的隐喻",
                        "is_intentional": True,
                        "occurrences": [
                            {
                                "text": "月光洒在林远脸上，他最后一次仰望这片天空。",
                                "paragraph_index": 5,
                            }
                        ],
                    }
                ]
            },
            ensure_ascii=False,
        )
        tracker, builder, router = _make_tracker_with_captures(llm_response)

        occurrences = await tracker.extract_from_chapter(12, "第12章正文", chapter_outline=outline)

        # Verify the prompt contained the context
        ctx = builder.captured_context
        assert ctx["chapter_goal"] == "月光下的告别仪式"

        # Verify the LLM response was parsed correctly
        assert len(occurrences) == 1
        assert occurrences[0].motif_id == "motif_001"
        assert tracker.motifs["motif_001"].name == "月光"
        assert tracker.motifs["motif_001"].is_intentional is True


@pytest.mark.asyncio
async def test_existing_motifs_include_classification_evidence_in_prompt() -> None:
    tracker, builder, _router = _make_tracker_with_captures(
        json.dumps({"motifs": []}, ensure_ascii=False)
    )
    tracker._motifs["motif_gold"] = Motif(
        motif_id="motif_gold",
        name="金镯",
        category="符号",
        description="家族旧案的关键物证",
        thematic_meaning="身份与债务的象征",
        occurrence_count=4,
        first_appearance_chapter=1,
        last_appearance_chapter=8,
        associated_characters=["令昭"],
        metadata={
            "category_confidence": 0.92,
            "secondary_categories": ["意象"],
        },
    )

    await tracker.extract_from_chapter(9, "第9章正文")

    ctx = builder.captured_context
    assert ctx is not None
    existing = ctx["existing_motifs"][0]
    assert existing["motif_id"] == "motif_gold"
    assert existing["thematic_meaning"] == "身份与债务的象征"
    assert existing["occurrence_count"] == 4
    assert existing["category_confidence"] == 0.92
    assert existing["secondary_categories"] == ["意象"]
    assert ctx["motif_category_distribution"] == {"符号": {"count": 1, "percentage": 100}}


class TestUnintentionalClassificationWithoutContext:
    """Mock LLM response without context — default behavior unchanged."""

    @pytest.mark.asyncio
    async def test_unintentional_classification_without_context(self) -> None:
        llm_response = json.dumps(
            {
                "motifs": [
                    {
                        "motif_id": "motif_002",
                        "name": "铜铃",
                        "category": "声音",
                        "description": "门上的铜铃随风作响",
                        "thematic_meaning": "铜铃声暗示不安",
                        "is_intentional": False,
                        "occurrences": [
                            {
                                "text": "门上的铜铃叮当作响。",
                                "paragraph_index": 2,
                            }
                        ],
                    }
                ]
            },
            ensure_ascii=False,
        )
        tracker, builder, router = _make_tracker_with_captures(llm_response)

        occurrences = await tracker.extract_from_chapter(8, "第8章正文", chapter_outline=None)

        # Verify no context was injected
        ctx = builder.captured_context
        assert ctx["chapter_goal"] == ""
        assert ctx["pov_character"] == ""
        assert ctx["element_focus"] == []

        # Verify parsing still works
        assert len(occurrences) == 1
        assert occurrences[0].motif_id == "motif_002"
        assert tracker.motifs["motif_002"].name == "铜铃"
        assert tracker.motifs["motif_002"].is_intentional is False


@pytest.mark.asyncio
async def test_extraction_cache_invalidates_when_chapter_text_changes() -> None:
    first_response = json.dumps(
        {
            "motifs": [
                {
                    "motif_id": "motif_010",
                    "name": "月影",
                    "category": "意象",
                    "occurrences": [{"text": "旧稿里月影落在窗边。"}],
                }
            ]
        },
        ensure_ascii=False,
    )
    second_response = json.dumps(
        {
            "motifs": [
                {
                    "motif_id": "motif_011",
                    "name": "铜铃",
                    "category": "声音",
                    "occurrences": [{"text": "新稿里铜铃忽然响起。"}],
                }
            ]
        },
        ensure_ascii=False,
    )
    tracker, _builder, router = _make_tracker_with_captures([first_response, second_response])

    first = await tracker._llm_extract_motifs(9, "旧稿正文", None)
    cached = await tracker._llm_extract_motifs(9, "旧稿正文", None)
    refreshed = await tracker._llm_extract_motifs(9, "新稿正文", None)

    assert router.route_calls == 2
    assert [occ.motif_id for occ in first] == ["motif_010"]
    assert [occ.motif_id for occ in cached] == ["motif_010"]
    assert [occ.motif_id for occ in refreshed] == ["motif_011"]
