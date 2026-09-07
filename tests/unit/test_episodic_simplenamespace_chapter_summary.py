"""Regression test for the ``SimpleNamespace`` ``chapter_summary`` bug.

Background
----------
``MemoryContext._index_episodic_for_finalize`` constructs two
``SimpleNamespace`` outcomes depending on the shape of ``chapter_result``:

* rich path (``hasattr(chapter_result, "chapter_summary")``) — full ChapterOutcome
  fields are forwarded
* legacy canon_delta path (``chapter_result.canon_delta`` only) — only
  ``canon_delta`` / ``creative_report`` / ``alignment_report`` / ``text`` /
  ``plan`` were forwarded

Before the fix, the canon_delta path produced a ``SimpleNamespace`` that
**lacked** ``chapter_summary`` and ``source_chapter``. When the resulting
namespace reached ``EpisodicMemory.index_chapter`` it accessed
``outcome.chapter_summary`` unconditionally and raised::

    AttributeError: 'types.SimpleNamespace' object has no attribute 'chapter_summary'

Production evidence (``data/弈局谋心/logs/.../python.log``)::
    ERROR | novel_forge.memory.context | finalize_chapter_memory task 'episodic'
        failed | chapter=1 | error='types.SimpleNamespace' object has no
        attribute 'chapter_summary'

This test pins both layers of the fix:

1. ``EpisodicMemory.index_chapter`` is defensive against an outcome object
   missing ``chapter_summary`` (defensive guard in episodic.py).
2. ``MemoryContext._index_episodic_for_finalize`` always forwards a
   ``chapter_summary`` on the canon_delta path (real fix in integration.py).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from novel_forge.memory.episodic import EpisodicMemory
from novel_forge.memory.integration import MemoryContext

# ───────────────────────────── fixtures ─────────────────────────────


@pytest.fixture
def memory() -> EpisodicMemory:
    return EpisodicMemory(
        embedding_config={"model": "fake-embedding", "dimensions": 8},
        use_mock_embeddings=True,
    )


def _make_legacy_canon_delta_outcome(chapter: int) -> SimpleNamespace:
    """Mimic the *pre-fix* canon_delta SimpleNamespace shape produced by
    ``MemoryContext._index_episodic_for_finalize`` — no chapter_summary,
    no source_chapter, no character_updates, no new_events.
    """
    return SimpleNamespace(
        canon_delta=SimpleNamespace(chapter=chapter, events=[]),
        creative_report=None,
        alignment_report=None,
        text=f"Chapter {chapter} 正文",
        plan=None,
    )


# ──────────────────────── defensive guard in episodic.py ─────────────


@pytest.mark.asyncio
async def test_index_chapter_tolerates_outcome_without_chapter_summary(
    memory: EpisodicMemory,
) -> None:
    """Pre-fix legacy canon_delta SimpleNamespaces lacked chapter_summary.
    The defensive guard in ``index_chapter`` should make this a no-op for
    the chapter-level entry instead of raising AttributeError.
    """
    outcome = _make_legacy_canon_delta_outcome(chapter=1)

    # Must not raise. The legacy outcome has no chapter_summary, so no
    # chapter-level entry is produced, but ``new_events`` loop is also
    # empty, so the call should return an empty list.
    signatures = await memory.index_chapter(outcome)

    assert signatures == []


@pytest.mark.asyncio
async def test_index_chapter_falls_back_to_text_when_summary_missing(
    memory: EpisodicMemory,
) -> None:
    """When ``chapter_summary`` is missing but ``text`` is present, the
    episodic entry's event_summary should fall back to the text prefix
    so the chapter is still retrievable from semantic search.
    """
    outcome = SimpleNamespace(
        chapter_summary="",
        source_chapter=2,
        character_updates={},
        new_events=[],
        text="红烛光影里,沈清漪合上眼眸,她听见殿梁上传来幽蛹针牵引丝线的细响。",
        creative_report=None,
        alignment_report=None,
        plan=None,
    )

    signatures = await memory.index_chapter(outcome)

    # Should produce exactly one chapter-level entry from the text fallback.
    assert len(signatures) >= 1
    stats = memory.get_memory_stats()
    assert stats["chapter_level_entries"] >= 1


# ───── canonical outcome shape with all fields (regression baseline) ─


def _make_canonical_outcome(chapter: int, summary: str, text: str = ""):
    @dataclass
    class FakeOutcome:
        chapter_summary: str = ""
        source_chapter: int = 0
        character_updates: dict = field(default_factory=dict)
        new_events: list = field(default_factory=list)
        text: str = ""
        creative_report: Any = None
        alignment_report: Any = None
        plan: Any = None

    return FakeOutcome(
        chapter_summary=summary,
        source_chapter=chapter,
        text=text or summary,
    )


@pytest.mark.asyncio
async def test_index_chapter_canonical_outcome_unchanged(
    memory: EpisodicMemory,
) -> None:
    """Sanity: the canonical happy-path outcome is unaffected by the fix."""
    outcome = _make_canonical_outcome(chapter=1, summary="章节 1 摘要", text="章节 1 正文")
    signatures = await memory.index_chapter(outcome)
    assert len(signatures) >= 1


# ─── MemoryContext._index_episodic_for_finalize contract (real fix) ──


@dataclass
class _FakeChapterOutcome:
    """Stand-in for a ChapterOutcome nested inside a ChapterResult.canon_delta.

    Carries the inner fields that the canon_delta branch must read from
    canon_delta (NOT from the outer ChapterResult container): chapter_summary,
    source_chapter, character_updates, new_events, creative_report,
    alignment_report.
    """

    chapter: int = 0
    summary: str = ""
    chapter_summary: str = ""
    source_chapter: int = 0
    character_updates: dict = field(default_factory=dict)
    new_events: list = field(default_factory=list)
    creative_report: Any = None
    alignment_report: Any = None


@dataclass
class _FakeChapterResult:
    """Legacy ChapterResult shape that triggered the AttributeError before
    the fix. Deliberately has NO ``chapter_summary`` field — its absence is
    what makes ``hasattr(chapter_result, "chapter_summary")`` False in
    MemoryContext._index_episodic_for_finalize, sending the test through
    the canon_delta path (where the P0-R1 fix lives).
    """

    canon_delta: Any = None


@dataclass
class _FakeRichChapterResult:
    """ChapterResult with rich fields (has ``chapter_summary``). Used to
    exercise the rich path of ``_index_episodic_for_finalize`` (the
    ``has_rich_fields`` branch), which already worked but is locked by a
    regression test so the P0-R1 fix does not regress it.
    """

    canon_delta: Any = None
    chapter_summary: str = ""
    source_chapter: int = 0
    character_updates: dict = field(default_factory=dict)
    new_events: list = field(default_factory=list)
    creative_report: Any = None
    alignment_report: Any = None


def _make_memory_context_for_test(episodic: EpisodicMemory) -> MemoryContext:
    """Build a MemoryContext with the minimum viable state to exercise
    ``_index_episodic_for_finalize``.  All storage / project / outline
    fields are stubbed — we only need ``_field``, ``_episodic_memory``,
    and ``_load_chapter_plan_for_memory``.
    """
    ctx = MemoryContext()
    ctx._episodic_memory = episodic
    ctx._load_chapter_plan_for_memory = lambda _n: None  # type: ignore[assignment]
    return ctx


@pytest.mark.asyncio
async def test_index_episodic_for_finalize_canon_delta_path_includes_chapter_summary(
    memory: EpisodicMemory,
) -> None:
    """Regression: with the fix, the canon_delta branch of
    ``_index_episodic_for_finalize`` must forward ``chapter_summary`` on the
    constructed ``SimpleNamespace`` so that ``index_chapter`` does not
    raise AttributeError.

    Before the fix this test fails with::

        AttributeError: 'types.SimpleNamespace' object has no attribute
        'chapter_summary'
    """
    ctx = _make_memory_context_for_test(memory)

    chapter_result = _FakeChapterResult(
        canon_delta=_FakeChapterOutcome(chapter=1, summary="delta summary", source_chapter=1),
    )

    # Should not raise. The chapter gets indexed via the text fallback.
    await ctx._index_episodic_for_finalize(
        chapter_number=1,
        text="合卺酒送至面前,金杯在烛光下泛着暖色。",
        creative_report_text="creative summary",
        chapter_result=chapter_result,
        chapter_plan=None,
    )

    stats = memory.get_memory_stats()
    assert stats["chapter_level_entries"] >= 1


@pytest.mark.asyncio
async def test_index_episodic_for_finalize_canon_delta_path_uses_caller_chapter_summary(
    memory: EpisodicMemory,
) -> None:
    """When the chapter_result exposes both canon_delta AND a
    chapter_summary field, the rich path is taken and chapter_summary is
    forwarded verbatim — no regression in the rich path either.
    """
    ctx = _make_memory_context_for_test(memory)

    chapter_result = _FakeRichChapterResult(
        canon_delta=_FakeChapterOutcome(chapter=2, summary="delta"),
        chapter_summary="rich 路径摘要：红烛婚殿博弈开局",
    )

    await ctx._index_episodic_for_finalize(
        chapter_number=2,
        text="合卺酒送至面前",
        creative_report_text="",
        chapter_result=chapter_result,
        chapter_plan=None,
    )

    stats = memory.get_memory_stats()
    assert stats["chapter_level_entries"] >= 1


# ─── spy assertion: SimpleNamespace passed to index_chapter carries
#     chapter_summary on the canon_delta path (locks the fix shape) ───


@pytest.mark.asyncio
async def test_canon_delta_path_simplenamespace_has_chapter_summary_attr() -> None:
    """Spy on ``EpisodicMemory.index_chapter`` to inspect the SimpleNamespace
    actually forwarded.  Locks the contract that the fix must satisfy:
    the constructed namespace MUST expose ``chapter_summary`` and
    ``source_chapter``.
    """
    memory = EpisodicMemory(
        embedding_config={"model": "fake-embedding", "dimensions": 8},
        use_mock_embeddings=True,
    )
    captured: list[Any] = []
    spy = AsyncMock(side_effect=lambda outcome: captured.append(outcome) or [])

    memory.index_chapter = spy  # type: ignore[method-assign]

    ctx = _make_memory_context_for_test(memory)
    chapter_result = _FakeChapterResult(
        canon_delta=_FakeChapterOutcome(chapter=2, summary="delta", source_chapter=2),
    )

    await ctx._index_episodic_for_finalize(
        chapter_number=3,
        text="幽蛹针的丝线在殿梁上无声垂落",
        creative_report_text="",
        chapter_result=chapter_result,
        chapter_plan=None,
    )

    assert len(captured) == 1, "index_chapter should have been called exactly once"
    outcome = captured[0]
    # The two attributes that were missing in the pre-fix bug:
    assert hasattr(outcome, "chapter_summary"), (
        "canon_delta path must forward chapter_summary (regression: was missing)"
    )
    assert hasattr(outcome, "source_chapter"), (
        "canon_delta path must forward source_chapter (regression: was missing)"
    )
    # source_chapter now reads from canon_delta (value=2), not fallback to chapter_number (value=3)
    assert outcome.source_chapter == 2


@pytest.mark.asyncio
async def test_canon_delta_path_reads_inner_fields_not_container() -> None:
    """Regression: the canon_delta branch must read character_updates /
    new_events / chapter_summary / source_chapter from canon_delta (the inner
    ChapterOutcome), NOT from the outer ChapterResult container.

    Before the fix, these fields were read from chapter_result (which does not
    have them), silently returning defaults ({} / [] / None) and dropping all
    episodic entity/event data for every finalized chapter.
    """
    memory = EpisodicMemory(
        embedding_config={"model": "fake-embedding", "dimensions": 8},
        use_mock_embeddings=True,
    )
    captured: list[Any] = []
    spy = AsyncMock(side_effect=lambda outcome: captured.append(outcome) or [])
    memory.index_chapter = spy  # type: ignore[method-assign]

    ctx = _make_memory_context_for_test(memory)

    inner_character_updates = {"林沐": SimpleNamespace(name="林沐", state="alive")}
    inner_new_events = [SimpleNamespace(type="event", summary="合卺酒")]
    inner_outcome = _FakeChapterOutcome(
        chapter=5,
        summary="inner summary",
        chapter_summary="inner chapter_summary",
        source_chapter=5,
        character_updates=inner_character_updates,
        new_events=inner_new_events,
    )
    # Outer container deliberately has NO character_updates / new_events /
    # chapter_summary — mimics real ChapterResult which lacks these fields.
    chapter_result = _FakeChapterResult(canon_delta=inner_outcome)

    await ctx._index_episodic_for_finalize(
        chapter_number=5,
        text="正文内容",
        creative_report_text="",
        chapter_result=chapter_result,
        chapter_plan=None,
    )

    assert len(captured) == 1
    outcome = captured[0]
    assert outcome.character_updates is inner_character_updates, (
        "character_updates must come from canon_delta, not container defaults"
    )
    assert outcome.new_events is inner_new_events, (
        "new_events must come from canon_delta, not container defaults"
    )
    assert outcome.chapter_summary == "inner chapter_summary", (
        "chapter_summary must come from canon_delta"
    )
    assert outcome.source_chapter == 5, (
        "source_chapter must come from canon_delta"
    )
