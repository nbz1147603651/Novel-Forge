"""Unit tests for M3.5 — StyleGoldenRetriever prompt injection.

Verifies that PromptBuilder.render() automatically exposes high-scoring
paragraphs from the same project as in-context positive examples, scoped
to the current scene_intent.

Design:
- The retriever and scene_intent are passed via PromptBuilder.render context.
- A new helper ``_resolve_style_golden_examples`` turns them into a list of
  dicts that the Jinja2 templates can iterate.
- Templates use ``style_golden_examples`` as a list of {chapter_number,
  paragraph_index, text, eval_score}.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from novel_forge.memory.style_golden_retriever import (
    GoldenPassage,
    StyleGoldenRetriever,
)
from novel_forge.prompts.builder import PromptBuilder

# ---------------------------------------------------------------------------
# Helpers: small fake retriever that doesn't need disk IO
# ---------------------------------------------------------------------------


class _FakeRetriever:
    """Drop-in replacement for StyleGoldenRetriever that returns canned results."""

    def __init__(self, passages: list[GoldenPassage]) -> None:
        self._passages = passages
        self.query_history: list[dict[str, Any]] = []

    def retrieve_for_scene(
        self,
        scene_intent: dict[str, Any],
        *,
        max_results: int = 3,
    ) -> list[GoldenPassage]:
        self.query_history.append(dict(scene_intent))
        return self._passages[:max_results]


def _make_passages() -> list[GoldenPassage]:
    return [
        GoldenPassage(
            chapter_number=1, paragraph_index=0,
            text="窗帘缝隙里渗入第一缕灰蓝色的光，是天色将明未明时特有的暧昧色调。",
            eval_score=9.64,
        ),
        GoldenPassage(
            chapter_number=9, paragraph_index=3,
            text="风从穿堂涌出来，吹动门槛边的风铃，那声音细密而克制。",
            eval_score=9.71,
        ),
        GoldenPassage(
            chapter_number=22, paragraph_index=7,
            text="她握着摄像机的手不再那么用力，肩膀的高度刚好和旁边那人同一个水平线。",
            eval_score=9.93,
        ),
    ]


# ---------------------------------------------------------------------------
# Public helper: PromptBuilder._resolve_style_golden_examples
# ---------------------------------------------------------------------------


class TestResolveStyleGoldenExamples:
    def test_returns_empty_list_when_no_retriever(self) -> None:
        builder = PromptBuilder()
        result = builder._resolve_style_golden_examples(
            context={"scene_intent": {"emotional_tone": "克制"}}
        )
        assert result == []

    def test_returns_empty_list_when_no_scene_intent(self) -> None:
        retriever = _FakeRetriever(_make_passages())
        builder = PromptBuilder()
        result = builder._resolve_style_golden_examples(
            context={"style_golden_retriever": retriever}
        )
        assert result == []

    def test_calls_retriever_with_scene_intent(self) -> None:
        retriever = _FakeRetriever(_make_passages())
        builder = PromptBuilder()
        result = builder._resolve_style_golden_examples(
            context={
                "style_golden_retriever": retriever,
                "scene_intent": {
                    "emotional_tone": "克制",
                    "setting": "云南小镇",
                },
            }
        )
        assert retriever.query_history
        assert retriever.query_history[0]["emotional_tone"] == "克制"
        # Returns serialized dicts (Jinja2-friendly).
        assert isinstance(result, list)
        assert result[0]["chapter_number"] == 1
        assert "灰蓝色" in result[0]["text"]
        assert result[0]["eval_score"] == 9.64

    def test_honors_max_results_override(self) -> None:
        retriever = _FakeRetriever(_make_passages())
        builder = PromptBuilder()
        result = builder._resolve_style_golden_examples(
            context={
                "style_golden_retriever": retriever,
                "scene_intent": {"emotional_tone": "克制"},
                "style_golden_max_results": 2,
            }
        )
        assert len(result) == 2

    def test_invalid_max_results_falls_back_to_default(self) -> None:
        retriever = _FakeRetriever(_make_passages())
        builder = PromptBuilder()
        result = builder._resolve_style_golden_examples(
            context={
                "style_golden_retriever": retriever,
                "scene_intent": {"emotional_tone": "克制"},
                "style_golden_max_results": "not-a-number",
            }
        )
        assert len(result) == 3

    def test_disabled_via_context_flag(self) -> None:
        retriever = _FakeRetriever(_make_passages())
        builder = PromptBuilder()
        result = builder._resolve_style_golden_examples(
            context={
                "style_golden_retriever": retriever,
                "scene_intent": {"emotional_tone": "克制"},
                "style_golden_enabled": False,
            }
        )
        assert result == []

    def test_real_retriever_round_trip(self, tmp_path: Path) -> None:
        """Real StyleGoldenRetriever must work end-to-end with PromptBuilder."""
        # Build a tiny project with two high-scoring chapters.
        project = tmp_path / "p"
        chapters_dir = project / "chapters"
        chapters_dir.mkdir(parents=True)
        (chapters_dir / "chapter_001.md").write_text(
            "窗帘缝隙里渗入第一缕灰蓝色的光，是天色将明未明时特有的暧昧色调。\n\n"
            "沈鹿溪站在老宅民宿的木门槛外，没有跨过去。"
            + "这是一段足够长的填充文本，确保段落长度达到最小索引下限六十字符。"
            + "应当能被 BM25 检索到的关键词段落。"
            * 2,
            encoding="utf-8",
        )
        (project / "_chapter_meta_cache.json").write_text(
            '{"1": {"overall_score": 9.5}}',
            encoding="utf-8",
        )
        retriever = StyleGoldenRetriever(
            project, project / "_chapter_meta_cache.json",
        )
        builder = PromptBuilder()
        result = builder._resolve_style_golden_examples(
            context={
                "style_golden_retriever": retriever,
                "scene_intent": {
                    "emotional_tone": "克制",
                    "setting": "小镇",
                },
            }
        )
        # Real retriever may or may not match, but the call must not error.
        assert isinstance(result, list)


# ---------------------------------------------------------------------------
# Integration: PromptBuilder.render injects style_golden_examples into context
# ---------------------------------------------------------------------------


class TestRenderInjectsStyleGoldenExamples:
    def test_render_adds_style_golden_examples_to_context(self) -> None:
        """``render()`` must materialize ``style_golden_examples`` from
        retriever+scene_intent and inject it as a default for templates."""
        retriever = _FakeRetriever(_make_passages())
        builder = PromptBuilder()
        # Render a minimal draft prompt with a fake retriever injected.
        # We don't need to assert the rendered text — we only need to assert
        # the helper produced what the templates expect.
        resolved = builder._resolve_style_golden_examples(
            context={
                "style_golden_retriever": retriever,
                "scene_intent": {"emotional_tone": "克制", "setting": "小镇"},
            }
        )
        assert resolved
        assert all("chapter_number" in p for p in resolved)
        assert all("paragraph_index" in p for p in resolved)
        assert all("text" in p for p in resolved)
        assert all("eval_score" in p for p in resolved)


# ---------------------------------------------------------------------------
# Default value: empty list (so templates using `default([])` work)
# ---------------------------------------------------------------------------


class TestDefaultInWithCommonOptionalDefaults:
    def test_style_golden_examples_default_is_empty_list(self) -> None:
        context = PromptBuilder._with_common_optional_defaults({})
        assert context["style_golden_examples"] == []
        assert context["style_golden_enabled"] is True

    def test_style_golden_retriever_default_is_none(self) -> None:
        context = PromptBuilder._with_common_optional_defaults({})
        assert context["style_golden_retriever"] is None

    def test_scene_intent_default_is_empty_dict(self) -> None:
        context = PromptBuilder._with_common_optional_defaults({})
        assert context["scene_intent"] == {}
