"""E2E: full humanize library pipeline — seed → retrieve → scan context → report → bump."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from novel_forge.core.schemas.humanize import HumanizePatternHit, HumanizeReport
from novel_forge.core.schemas.humanize_library import (
    LIBRARY_BUILTIN_ENTRIES,
    HumanizeLibraryEntry,
)
from novel_forge.memory.humanize_library_store import (
    HumanizeLibrary,
    seed_builtin_patterns,
)
from novel_forge.memory.humanize_retrieval import HumanizeLibraryRetriever
from novel_forge.pipeline.steps.humanize_scan_step import (
    HumanizeScanInput,
    HumanizeScanStep,
)

_AI_LADEN_TEXT = (
    "林远站在钟楼的阴影里，值得注意的是，这个发现具有里程碑意义的价值。"
    "他凝望着远方的灯火，内心深处感受到一种前所未有的震撼。"
    "不可否认的是，这一关键时刻将成为整个故事的分水岭。"
    "灰尘扑面而来，他沉思着推开那扇古旧的木门。"
    "从根本上说，这一切都是命运的安排。"
    "走廊尽头传来微弱的声响，他迈开步子走向那个未知的房间。"
    "远处的钟声敲响了十二下，新的篇章即将开启。"
)


@pytest.fixture
def e2e_library(tmp_path: Path) -> HumanizeLibrary:
    lib = HumanizeLibrary.from_path(tmp_path / "e2e_lib")
    seed_builtin_patterns(lib)
    lib.add(
        HumanizeLibraryEntry(
            pattern_id="lib_user_00000001",
            pattern_name="AI填充词检测",
            category="AI写作痕迹",
            severity="high",
            detection_method="regex",
            source="user",
            keywords=["值得注意的是", "不可否认的是", "从根本上说", "AI写作"],
        )
    )
    return lib


def test_seed_produces_all_current_builtins(e2e_library: HumanizeLibrary) -> None:
    stats = e2e_library.stats()
    assert stats.total == len(LIBRARY_BUILTIN_ENTRIES) + 1
    assert stats.regex_count == 26 + 1
    assert stats.llm_only_count == 4
    assert stats.user_count == 1


def test_retrieval_finds_hits(e2e_library: HumanizeLibrary) -> None:
    retriever = HumanizeLibraryRetriever(sim_threshold=0.0)
    hits = retriever.retrieve(e2e_library, _AI_LADEN_TEXT, top_k=10)
    assert len(hits) >= 1, "Expected at least 1 BM25 hit from keyword overlap"

    hit_ids = {h["pattern_id"] for h in hits}
    assert "lib_user_00000001" in hit_ids

    for hit in hits:
        assert "pattern_id" in hit
        assert "pattern_name" in hit
        assert "evidence_quote" in hit
        assert "similarity" in hit


def test_build_llm_context_includes_library_hits(e2e_library: HumanizeLibrary) -> None:
    retriever = HumanizeLibraryRetriever(sim_threshold=0.0)
    hits = retriever.retrieve(e2e_library, _AI_LADEN_TEXT, top_k=10)
    assert hits, "Need hits for context test"

    long_quote_hit = {
        **hits[0],
        "evidence_quote": "A" * 200,
    }
    padded_hits = [long_quote_hit] + hits[1:]

    scan_input = HumanizeScanInput(
        chapter_number=1,
        chapter_text=_AI_LADEN_TEXT,
        library_hits=padded_hits,
        library_enabled=True,
    )
    context = HumanizeScanStep._build_llm_context(scan_input)

    assert "library_hits" in context
    assert len(context["library_hits"]) == len(padded_hits)
    assert context["library_enabled"] is True

    first_ctx_hit = context["library_hits"][0]
    assert len(first_ctx_hit["evidence_quote"]) <= 200


def test_humanize_report_accepts_library_source() -> None:
    hit = HumanizePatternHit(
        pattern_id="filler_phrases",
        pattern_name="填充短语",
        category="元语言",
        severity="high",
        evidence_quote="值得注意的是",
        source="library",
        confidence=0.9,
    )
    report = HumanizeReport(
        chapter_number=1,
        total_hits=1,
        pattern_hits=[hit],
        humanize_score=7.0,
    )
    assert report.pattern_hits[0].source == "library"
    assert report.total_hits == 1


def test_bump_hits_from_report_increments_count(
    e2e_library: HumanizeLibrary,
) -> None:

    class _FakeReport:
        def __init__(self, hits: list[dict[str, Any]]) -> None:
            self.hits = hits

    fake_report = _FakeReport(
        hits=[{"pattern_id": "lib_user_00000001", "score": 0.9}]
    )

    before = e2e_library.get("lib_user_00000001")
    assert before is not None
    original_hit_count = before.hit_count

    bumped = e2e_library.bump_hits_from_report(fake_report, chapter=1)
    assert bumped == 1

    after = e2e_library.get("lib_user_00000001")
    assert after is not None
    assert after.hit_count == original_hit_count + 1
    assert after.last_hit_chapter == 1
    assert after.first_seen_at is not None
    assert after.last_seen_at is not None


def test_stats_reflect_updated_hit_count(e2e_library: HumanizeLibrary) -> None:
    e2e_library.bump_hit("lib_user_00000001", chapter=3, score=0.8)
    e2e_library.bump_hit("lib_user_00000001", chapter=5, score=0.7)

    entry = e2e_library.get("lib_user_00000001")
    assert entry is not None
    assert entry.hit_count == 2
    assert entry.last_hit_chapter == 5

    stats = e2e_library.stats()
    assert stats.total == len(LIBRARY_BUILTIN_ENTRIES) + 1
    assert stats.user_count == 1
    assert stats.last_updated_at is not None


def test_full_pipeline_round_trip(tmp_path: Path) -> None:
    lib = HumanizeLibrary.from_path(tmp_path / "roundtrip_lib")
    seed_builtin_patterns(lib)

    lib.add(
        HumanizeLibraryEntry(
            pattern_id="lib_user_00000002",
            pattern_name="测试模式集成",
            category="集成测试",
            severity="medium",
            detection_method="regex",
            source="user",
            keywords=["集成测试", "测试模式集成"],
        )
    )

    text = "这段文字包含集成测试关键词，用于验证完整的检索→报告→命中流程。"

    retriever = HumanizeLibraryRetriever(sim_threshold=0.0)
    retriever.retrieve(lib, text, top_k=10)

    class _FakeReport:
        def __init__(self, hits: list[dict[str, Any]]) -> None:
            self.hits = hits

    fake_report = _FakeReport(
        hits=[{"pattern_id": "lib_user_00000002", "score": 0.85}]
    )

    bumped = lib.bump_hits_from_report(fake_report, chapter=7)
    assert bumped == 1

    entry = lib.get("lib_user_00000002")
    assert entry is not None
    assert entry.hit_count == 1
    assert entry.last_hit_chapter == 7

    stats = lib.stats()
    assert stats.total == len(LIBRARY_BUILTIN_ENTRIES) + 1

    lib.close()
