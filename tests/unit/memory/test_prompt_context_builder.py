from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from novel_forge.memory.prompt_context_builder import build_prompt_memory_context


class _MotifTracker:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def get_motifs_for_prompt(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(dict(kwargs))
        return {"active_motifs": ["纸灰"]}


class _SummaryService:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def get_summary_for_context(self, **kwargs: Any) -> str:
        self.calls.append(dict(kwargs))
        return "前三章摘要"


def test_prompt_context_builder_injects_motif_summary_and_critique_context() -> None:
    tracker = _MotifTracker()
    summary = _SummaryService()
    episodic_memory = object()
    critique_calls: list[dict[str, Any]] = []

    def critique_builder(**kwargs: Any) -> str:
        critique_calls.append(dict(kwargs))
        return "避免重复上一章误判"

    result = build_prompt_memory_context(
        current_chapter=7,
        settings=SimpleNamespace(memory_motif_related_lookback_chapters=4),
        motif_tracker=tracker,
        summary_service=summary,
        episodic_memory=episodic_memory,
        summary_granularity="volume",
        critique_context_builder=critique_builder,
    )

    assert result == {
        "motif_context": {"active_motifs": ["纸灰"]},
        "summary_context": "前三章摘要",
        "critique_context": "避免重复上一章误判",
    }
    assert tracker.calls == [
        {"current_chapter": 7, "related_lookback_chapters": 4}
    ]
    assert summary.calls == [{"current_chapter": 7, "granularity": "volume"}]
    assert critique_calls == [
        {"current_chapter": 7, "episodic_memory": episodic_memory}
    ]


def test_prompt_context_builder_uses_legacy_summary_without_summary_service() -> None:
    result = build_prompt_memory_context(
        current_chapter=5,
        settings=SimpleNamespace(memory_motif_related_lookback_chapters=2),
        include_motifs=False,
        include_critiques=False,
        legacy_summary_getter=lambda chapter: f"第{chapter - 1}章摘要",
    )

    assert result == {"summary_context": "第4章摘要"}


def test_prompt_context_builder_skips_disabled_sections() -> None:
    result = build_prompt_memory_context(
        current_chapter=5,
        settings=SimpleNamespace(memory_motif_related_lookback_chapters=2),
        motif_tracker=_MotifTracker(),
        summary_service=_SummaryService(),
        episodic_memory=object(),
        include_motifs=False,
        include_summaries=False,
        include_critiques=False,
        critique_context_builder=lambda **_: "unused",
    )

    assert result == {}
