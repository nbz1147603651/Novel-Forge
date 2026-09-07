"""Benchmark: ArtifactCache baseline — measure existing CachedFileSystemStorage hit rate.

Simulates a typical chapter run (5+ LLM calls, each reading different project files)
across two sequential chapters. Measures cache hit rate to decide if an additional
ArtifactCache layer is needed.

Decision gate: hit rate < 30% → add cache; hit rate >= 30% → skip (existing cache sufficient).
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import pytest

from novel_forge.persistence.filesystem import CachedFileSystemStorage

# ---------------------------------------------------------------------------
# Instrumented storage wrapper
# ---------------------------------------------------------------------------

class _InstrumentedCachedStorage(CachedFileSystemStorage):
    """Wraps CachedFileSystemStorage to track hit/miss statistics.

    Detects cache hits by checking if the file's mtime_ns+size match the
    cached entry *before* calling super().load_json() — if they match and
    entry exists, it's a hit (super() will return from cache). Otherwise it's
    a miss (super() will read from disk and cache).
    """

    def __init__(self, root: Path, *, max_entries: int = 256) -> None:
        super().__init__(root, max_entries=max_entries)
        self.hits: int = 0
        self.misses: int = 0
        self.total_reads: int = 0

    def load_json(self, path: Path) -> dict[str, Any]:
        self.total_reads += 1
        # Check if cache would hit: same logic as parent
        stat = path.stat()
        key = str(path.resolve(strict=False))
        with self._cache_lock:
            cached = self._json_cache.get(key)
            if (
                cached is not None
                and cached.mtime_ns == stat.st_mtime_ns
                and cached.size == stat.st_size
            ):
                self.hits += 1
            else:
                self.misses += 1
        return super().load_json(path)

    def load_text(self, path: Path) -> str:
        self.total_reads += 1
        stat = path.stat()
        key = str(path.resolve(strict=False))
        with self._cache_lock:
            cached = self._text_cache.get(key)
            if (
                cached is not None
                and cached.mtime_ns == stat.st_mtime_ns
                and cached.size == stat.st_size
            ):
                self.hits += 1
            else:
                self.misses += 1
        return super().load_text(path)

    @property
    def hit_rate(self) -> float:
        total = self.hits + self.misses
        return self.hits / total if total > 0 else 0.0

    def report(self) -> dict[str, Any]:
        return {
            "total_reads": self.total_reads,
            "hits": self.hits,
            "misses": self.misses,
            "hit_rate_pct": round(self.hit_rate * 100, 1),
            "json_cache_entries": len(self._json_cache),
            "text_cache_entries": len(self._text_cache),
        }


# ---------------------------------------------------------------------------
# Typical project data (realistic sizes)
# ---------------------------------------------------------------------------

def _make_spec_data() -> dict[str, Any]:
    """Typical spec.json (~2KB)."""
    return {
        "title": "记忆回收师",
        "genre": "scifi",
        "theme": "记忆与身份的边界",
        "tone": "dark",
        "length_target": 120000,
        "language": "zh",
        "characters_hint": "记忆回收师林远，发现自己前半生被改写",
        "world_hint": "近未来赛博朋克，记忆可以交易",
        "premise": "在一个记忆可以提取、交易、改写的世界里，顶级记忆回收师林远接到一个匿名委托：回收一段被深度加密的记忆。在破解过程中，他发现这段记忆属于自己的前半生——但内容与他的记忆完全不同。",
        "additional_notes": "需要探讨记忆与身份的关系，主角内心的挣扎",
    }


def _make_story_bible_data() -> dict[str, Any]:
    """Typical story_bible.json (~8KB)."""
    return {
        "world_building": {
            "setting": "2089年，新上海。记忆交易已成为合法产业。",
            "technology": [
                "记忆提取器 MK-VII",
                "神经植入芯片",
                "记忆加密协议",
                "量子记忆存储",
            ],
            "organizations": [
                {"name": "记忆管理局", "description": "政府监管机构"},
                {"name": "暗网记忆交易所", "description": "地下记忆黑市"},
            ],
        },
        "plot_arcs": [
            {"arc": "主线：记忆真相", "chapters": "1-24", "status": "active"},
            {"arc": "支线：暗网阴谋", "chapters": "5-18", "status": "active"},
        ],
        "themes": ["记忆与身份", "真相与谎言", "自由意志"],
        "tone_guide": "阴郁但有希望，赛博朋克美学",
    }


def _make_character_bible_data() -> dict[str, Any]:
    """Typical character_bible.json (~6KB)."""
    return {
        "characters": [
            {
                "name": "林远",
                "role": "主角",
                "gender": "男",
                "personality": "冷静、理性、内心矛盾",
                "backstory": "顶级记忆回收师，曾是记忆管理局特工。3年前辞职独立执业。",
                "status": "独立记忆回收师",
                "arc": "发现自己真实身份，面对记忆被改写的真相",
                "notes": "拥有特殊的记忆解析能力",
            },
            {
                "name": "苏晴",
                "role": "女主",
                "gender": "女",
                "personality": "聪明、果断、有秘密",
                "backstory": "记忆管理局现任特工，与林远有过去的纠葛。",
                "status": "记忆管理局高级特工",
                "arc": "在职责与感情之间做出选择",
                "notes": "精通记忆加密技术",
            },
            {
                "name": "陈默",
                "role": "反派",
                "gender": "男",
                "personality": "深沉、有魅力、目的性强",
                "backstory": "暗网记忆交易所的幕后掌控者。",
                "status": "暗网领袖",
                "arc": "揭示其真实目的与林远的关系",
                "notes": "掌握记忆改写的核心技术",
            },
        ]
    }


def _make_outline_data() -> dict[str, Any]:
    """Typical outline.json (~10KB)."""
    chapters = []
    for i in range(1, 25):
        chapters.append({
            "chapter_number": i,
            "title": f"第{i}章 {'初醒' if i == 1 else '迷局' if i == 2 else '暗流' if i == 3 else '记忆碎片'}",
            "summary": "本章推进主线剧情，主角发现新的线索。章节内容涉及记忆回收技术的细节描写。",
            "pov_character": "林远" if i % 3 != 0 else "苏晴",
            "key_events": [f"事件{i}A：发现线索", f"事件{i}B：遭遇阻碍"],
            "element_focus": ["记忆与身份", "真相与谎言"] if i % 2 == 0 else ["自由意志"],
        })
    return {"chapters": chapters, "total_chapters": 24, "words_per_chapter": 5000}


def _make_style_profile_data() -> dict[str, Any]:
    """Typical style_profile.json (~3KB)."""
    return {
        "style_name": "赛博朋克悬疑",
        "modules": [
            {
                "name": "叙事视角",
                "rules": ["限制性第三人称", "内心独白适度使用"],
            },
            {
                "name": "语言风格",
                "rules": ["短句为主", "技术术语自然融入", "环境描写注重光影"],
            },
        ],
        "tone_parameters": {
            "tension": 0.7,
            "mystery": 0.8,
            "action": 0.4,
        },
    }


def _make_chapter_plan(chapter_num: int) -> dict[str, Any]:
    """Typical chapter plan (~2KB)."""
    return {
        "chapter_number": chapter_num,
        "scenes": [
            {
                "scene_number": 1,
                "setting": "记忆回收工作室",
                "characters": ["林远"],
                "purpose": "引入本章冲突",
                "key_beats": ["收到神秘委托", "分析记忆碎片"],
            },
            {
                "scene_number": 2,
                "setting": "暗网据点",
                "characters": ["林远", "陈默"],
                "purpose": "推进主线",
                "key_beats": ["与陈默对峙", "发现关键线索"],
            },
        ],
        "pov_character": "林远",
        "target_wordcount": 5000,
    }


def _make_state_packet(chapter_num: int) -> dict[str, Any]:
    """Typical state_packet (~4KB)."""
    return {
        "chapter_number": chapter_num,
        "canon_state": {
            "current_chapter": chapter_num,
            "active_threads": ["记忆真相", "暗网阴谋"],
        },
        "recent_summary": f"前{chapter_num - 1}章概要：主角逐步发现记忆被改写的线索。",
        "bridge_context": f"第{chapter_num - 1}章结尾：林远决定深入调查。",
    }


# ---------------------------------------------------------------------------
# Simulate a chapter run's file access pattern
# ---------------------------------------------------------------------------

def _simulate_chapter_run(
    storage: _InstrumentedCachedStorage,
    layout_paths: dict[str, Path],
    chapter_num: int,
    *,
    writes_enabled: bool = True,
) -> None:
    """Simulate the file reads (and optional writes) of a typical chapter run.

    A chapter run has ~7 pipeline stages, each making LLM calls that read
    project artifacts. The pattern below mirrors real execution:

    1. Planning: read spec + bible + outline + style + state_packet
    2. Bridge: read outline + previous chapter
    3. Draft: read plan + state_packet + style
    4. Quality check: read draft + character_bible + style
    5. Continuity repair: read draft + character_bible + previous chapter
    6. Extract: read draft + character_bible
    7. Evaluate: read draft + style + outline
    """
    spec_path = layout_paths["spec"]
    bible_path = layout_paths["story_bible"]
    char_path = layout_paths["character_bible"]
    outline_path = layout_paths["outline"]
    style_path = layout_paths["style_profile"]
    plan_path = layout_paths["chapter_plan"]
    state_path = layout_paths["state_packet"]
    draft_path = layout_paths["draft"]
    prev_chapter_path = layout_paths["prev_chapter"]

    # --- Stage 1: Planning (LLM call 1) ---
    storage.load_json(spec_path)
    storage.load_json(bible_path)
    storage.load_json(char_path)
    storage.load_json(outline_path)
    storage.load_json(style_path)

    # --- Stage 2: Bridge (LLM call 2) ---
    storage.load_json(outline_path)
    if prev_chapter_path.exists():
        storage.load_text(prev_chapter_path)

    # --- Stage 3: Draft (LLM call 3) ---
    storage.load_json(plan_path)
    storage.load_json(state_path)
    storage.load_json(style_path)
    # Draft writes a file (not cached, not counted)
    if writes_enabled:
        storage.save_text(draft_path, f"第{chapter_num}章初稿内容..." * 200)

    # --- Stage 4: Quality check (LLM call 4) ---
    storage.load_text(draft_path)
    storage.load_json(char_path)
    storage.load_json(style_path)

    # --- Stage 5: Continuity repair (LLM call 5) ---
    storage.load_text(draft_path)
    storage.load_json(char_path)
    if prev_chapter_path.exists():
        storage.load_text(prev_chapter_path)

    # --- Stage 6: Extract (LLM call 6) ---
    storage.load_text(draft_path)
    storage.load_json(char_path)

    # --- Stage 7: Evaluate (LLM call 7) ---
    storage.load_text(draft_path)
    storage.load_json(style_path)
    storage.load_json(outline_path)


# ---------------------------------------------------------------------------
# Setup helpers
# ---------------------------------------------------------------------------

def _setup_project_dir(project_dir: Path) -> dict[str, Path]:
    """Create a realistic project directory with typical files."""
    project_dir.mkdir(parents=True, exist_ok=True)
    (project_dir / "chapters").mkdir(exist_ok=True)
    (project_dir / "plans").mkdir(exist_ok=True)
    (project_dir / "states").mkdir(exist_ok=True)
    (project_dir / "drafts").mkdir(exist_ok=True)

    paths: dict[str, Path] = {}

    # Core project files
    paths["spec"] = project_dir / "spec.json"
    paths["spec"].write_text(json.dumps(_make_spec_data(), ensure_ascii=False, indent=2))

    paths["story_bible"] = project_dir / "story_bible.json"
    paths["story_bible"].write_text(json.dumps(_make_story_bible_data(), ensure_ascii=False, indent=2))

    paths["character_bible"] = project_dir / "character_bible.json"
    paths["character_bible"].write_text(json.dumps(_make_character_bible_data(), ensure_ascii=False, indent=2))

    paths["outline"] = project_dir / "outline.json"
    paths["outline"].write_text(json.dumps(_make_outline_data(), ensure_ascii=False, indent=2))

    paths["style_profile"] = project_dir / "style_profile.json"
    paths["style_profile"].write_text(json.dumps(_make_style_profile_data(), ensure_ascii=False, indent=2))

    # Chapter-specific files (created per chapter)
    paths["project_dir"] = project_dir
    return paths


def _setup_chapter_files(paths: dict[str, Path], chapter_num: int) -> dict[str, Path]:
    """Create chapter-specific files for a given chapter number."""
    project_dir = paths["project_dir"]

    plan_path = project_dir / "plans" / f"chapter_{chapter_num:03d}_plan.json"
    plan_path.write_text(json.dumps(_make_chapter_plan(chapter_num), ensure_ascii=False, indent=2))
    paths["chapter_plan"] = plan_path

    state_path = project_dir / "states" / f"chapter_{chapter_num:03d}_state_packet.json"
    state_path.write_text(json.dumps(_make_state_packet(chapter_num), ensure_ascii=False, indent=2))
    paths["state_packet"] = state_path

    draft_dir = project_dir / "drafts" / f"chapter_{chapter_num:03d}"
    draft_dir.mkdir(exist_ok=True)
    paths["draft"] = draft_dir / "v1_wave.md"

    if chapter_num > 1:
        prev_path = project_dir / "chapters" / f"chapter_{chapter_num - 1:03d}.md"
        if not prev_path.exists():
            prev_path.write_text(f"第{chapter_num - 1}章内容。\n" * 500)
        paths["prev_chapter"] = prev_path
    else:
        paths["prev_chapter"] = Path("/nonexistent")  # no previous chapter

    return paths


# ---------------------------------------------------------------------------
# Decision gate
# ---------------------------------------------------------------------------

HIT_RATE_THRESHOLD = 0.30  # 30%


def decision_gate(hit_rate: float) -> str:
    """Return 'add_cache' if existing cache is insufficient, 'skip' otherwise."""
    if hit_rate < HIT_RATE_THRESHOLD:
        return "add_cache"
    return "skip"


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestArtifactCacheBaseline:
    """Benchmark existing CachedFileSystemStorage hit rate for chapter runs."""

    def test_single_chapter_run_baseline(self, tmp_path: Path) -> None:
        """Run 1 chapter, measure first-run cache behavior.

        First run: all files are cold → expect 0% hit rate (all misses).
        This establishes the baseline for a single chapter.
        """
        project_dir = tmp_path / "project"
        paths = _setup_project_dir(project_dir)
        paths = _setup_chapter_files(paths, chapter_num=1)

        storage = _InstrumentedCachedStorage(tmp_path, max_entries=256)

        # First run — all cold
        _simulate_chapter_run(storage, paths, chapter_num=1)

        report = storage.report()
        print(f"\n{'='*60}")
        print("SINGLE CHAPTER RUN — FIRST (COLD) RUN")
        print(f"{'='*60}")
        print(f"  Total reads:     {report['total_reads']}")
        print(f"  Cache hits:      {report['hits']}")
        print(f"  Cache misses:    {report['misses']}")
        print(f"  Hit rate:        {report['hit_rate_pct']}%")
        print(f"  JSON cache size: {report['json_cache_entries']}")
        print(f"  Text cache size: {report['text_cache_entries']}")

        # First run is always cold — should have 0% or near-0% hits
        # (some hits possible if the same file is read twice in one run)
        assert report["total_reads"] > 0

    def test_two_chapter_run_baseline(self, tmp_path: Path) -> None:
        """Run 2 sequential chapters, measure cross-chapter cache hit rate.

        Chapter 1: cold run (all misses).
        Chapter 2: spec/bible/outline/style should be cached → significant hits.
        This is the realistic scenario for sequential chapter generation.
        """
        project_dir = tmp_path / "project"
        paths = _setup_project_dir(project_dir)

        storage = _InstrumentedCachedStorage(tmp_path, max_entries=256)

        # --- Chapter 1: Cold run ---
        paths = _setup_chapter_files(paths, chapter_num=1)
        _simulate_chapter_run(storage, paths, chapter_num=1)
        ch1_report = storage.report()

        # --- Chapter 2: Warm run (shared files cached) ---
        paths = _setup_chapter_files(paths, chapter_num=2)
        _simulate_chapter_run(storage, paths, chapter_num=2)
        ch2_report = storage.report()

        # Per-chapter stats
        ch2_reads = ch2_report["total_reads"] - ch1_report["total_reads"]
        ch2_hits = ch2_report["hits"] - ch1_report["hits"]
        ch2_misses = ch2_report["misses"] - ch1_report["misses"]
        ch2_hit_rate = ch2_hits / ch2_reads if ch2_reads > 0 else 0.0

        print(f"\n{'='*60}")
        print("TWO CHAPTER RUN — SEQUENTIAL")
        print(f"{'='*60}")
        print("\n  Chapter 1 (cold):")
        print(f"    Total reads:  {ch1_report['total_reads']}")
        print(f"    Hits:         {ch1_report['hits']}")
        print(f"    Misses:       {ch1_report['misses']}")
        print(f"    Hit rate:     {ch1_report['hit_rate_pct']}%")
        print("\n  Chapter 2 (warm):")
        print(f"    Total reads:  {ch2_reads}")
        print(f"    Hits:         {ch2_hits}")
        print(f"    Misses:       {ch2_misses}")
        print(f"    Hit rate:     {round(ch2_hit_rate * 100, 1)}%")
        print("\n  Cumulative:")
        print(f"    Total reads:  {ch2_report['total_reads']}")
        print(f"    Total hits:   {ch2_report['hits']}")
        print(f"    Total misses: {ch2_report['misses']}")
        print(f"    Overall hit:  {ch2_report['hit_rate_pct']}%")

        assert ch2_reads > 0
        # Chapter 2 should have higher hit rate than chapter 1
        assert ch2_hit_rate > 0, "Chapter 2 should benefit from cached files"

    def test_five_chapter_run_baseline(self, tmp_path: Path) -> None:
        """Run 5 sequential chapters, measure steady-state hit rate.

        This is the most realistic benchmark — a user running chapters 1-5.
        The cumulative hit rate after 5 chapters tells us if the cache is
        effective enough or if we need an additional ArtifactCache layer.
        """
        project_dir = tmp_path / "project"
        paths = _setup_project_dir(project_dir)

        storage = _InstrumentedCachedStorage(tmp_path, max_entries=256)

        per_chapter_stats: list[dict[str, Any]] = []

        for ch in range(1, 6):
            prev_total = storage.total_reads
            prev_hits = storage.hits
            prev_misses = storage.misses

            paths = _setup_chapter_files(paths, chapter_num=ch)
            _simulate_chapter_run(storage, paths, chapter_num=ch)

            ch_reads = storage.total_reads - prev_total
            ch_hits = storage.hits - prev_hits
            ch_misses = storage.misses - prev_misses
            ch_rate = ch_hits / ch_reads if ch_reads > 0 else 0.0

            per_chapter_stats.append({
                "chapter": ch,
                "reads": ch_reads,
                "hits": ch_hits,
                "misses": ch_misses,
                "hit_rate_pct": round(ch_rate * 100, 1),
            })

        overall = storage.report()

        print(f"\n{'='*60}")
        print("FIVE CHAPTER RUN — SEQUENTIAL (MAIN BENCHMARK)")
        print(f"{'='*60}")
        print("\n  Per-chapter breakdown:")
        print(f"  {'Ch':>4} {'Reads':>7} {'Hits':>7} {'Misses':>8} {'Hit%':>7}")
        print(f"  {'-'*4} {'-'*7} {'-'*7} {'-'*8} {'-'*7}")
        for s in per_chapter_stats:
            print(
                f"  {s['chapter']:>4} {s['reads']:>7} {s['hits']:>7}"
                f" {s['misses']:>8} {s['hit_rate_pct']:>6.1f}%"
            )

        print("\n  Cumulative (5 chapters):")
        print(f"    Total reads:     {overall['total_reads']}")
        print(f"    Total hits:      {overall['hits']}")
        print(f"    Total misses:    {overall['misses']}")
        print(f"    Overall hit rate:{overall['hit_rate_pct']}%")
        print(f"    JSON cache size: {overall['json_cache_entries']}")
        print(f"    Text cache size: {overall['text_cache_entries']}")

        # Decision gate
        overall_hit_rate = overall["hit_rate_pct"] / 100.0
        decision = decision_gate(overall_hit_rate)

        print(f"\n  {'='*40}")
        print(f"  DECISION GATE: hit_rate={overall['hit_rate_pct']}%")
        print(f"  Threshold: {HIT_RATE_THRESHOLD * 100}%")
        print(f"  Decision:  {decision}")
        print(f"  {'='*40}\n")

        assert overall["total_reads"] > 0
        # The decision is informational — test always passes
        # but the decision is captured in the output

    def test_decision_gate_skip_high_hit_rate(self) -> None:
        """Decision gate returns 'skip' when hit rate >= 30%."""
        assert decision_gate(0.50) == "skip"
        assert decision_gate(0.30) == "skip"
        assert decision_gate(1.0) == "skip"

    def test_decision_gate_add_low_hit_rate(self) -> None:
        """Decision gate returns 'add_cache' when hit rate < 30%."""
        assert decision_gate(0.0) == "add_cache"
        assert decision_gate(0.10) == "add_cache"
        assert decision_gate(0.29) == "add_cache"

    def test_cache_invalidation_on_file_change(self, tmp_path: Path) -> None:
        """Verify CachedFileSystemStorage invalidates on file modification.

        This confirms the existing cache has mtime-based invalidation,
        meaning a new ArtifactCache would be redundant.
        """
        project_dir = tmp_path / "project"
        paths = _setup_project_dir(project_dir)
        spec_path = paths["spec"]

        storage = _InstrumentedCachedStorage(tmp_path, max_entries=256)

        # First read — miss
        data1 = storage.load_json(spec_path)
        assert storage.misses == 1
        assert storage.hits == 0

        # Second read — hit (same file, same mtime)
        data2 = storage.load_json(spec_path)
        assert storage.hits == 1
        assert storage.misses == 1
        assert data1 == data2

        # Modify file
        import time
        time.sleep(0.01)  # ensure mtime changes
        data1["title"] = "修改后的标题"
        storage.save_json(spec_path, data1)

        # Third read — should get updated data (cache invalidated by save)
        data3 = storage.load_json(spec_path)
        assert data3["title"] == "修改后的标题"

        report = storage.report()
        print("\n  Cache invalidation test:")
        print(f"    Reads: {report['total_reads']}, Hits: {report['hits']}, Misses: {report['misses']}")
        print(f"    After save+load: title={data3['title']}")

    def test_deepcopy_isolation(self, tmp_path: Path) -> None:
        """Verify cached data is deep-copied (mutations don't affect cache).

        This is the key safety property that makes the existing cache correct.
        """
        project_dir = tmp_path / "project"
        paths = _setup_project_dir(project_dir)
        spec_path = paths["spec"]

        storage = _InstrumentedCachedStorage(tmp_path, max_entries=256)

        # Read and mutate
        data1 = storage.load_json(spec_path)
        data1["mutated"] = True

        # Read again — should NOT have the mutation
        data2 = storage.load_json(spec_path)
        assert "mutated" not in data2, "deepcopy isolation failed — cache leaked mutation"
        assert data2["title"] == _make_spec_data()["title"]

        print("\n  deepcopy isolation: PASSED — mutations don't leak to cache")

    def test_print_baseline_report(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        """Generate the full baseline report for evidence capture.

        This is the main benchmark entry point — it runs 5 chapters and
        prints a structured report suitable for evidence files.
        """
        project_dir = tmp_path / "project"
        paths = _setup_project_dir(project_dir)

        storage = _InstrumentedCachedStorage(tmp_path, max_entries=256)

        per_chapter: list[dict[str, Any]] = []
        t_start = time.perf_counter()

        for ch in range(1, 6):
            ch_start = time.perf_counter()
            prev_reads = storage.total_reads
            prev_hits = storage.hits

            paths = _setup_chapter_files(paths, chapter_num=ch)
            _simulate_chapter_run(storage, paths, chapter_num=ch)

            ch_elapsed = time.perf_counter() - ch_start
            ch_reads = storage.total_reads - prev_reads
            ch_hits = storage.hits - prev_hits
            ch_rate = ch_hits / ch_reads if ch_reads > 0 else 0.0

            per_chapter.append({
                "chapter": ch,
                "reads": ch_reads,
                "hits": ch_hits,
                "misses": ch_reads - ch_hits,
                "hit_rate_pct": round(ch_rate * 100, 1),
                "elapsed_ms": round(ch_elapsed * 1000, 1),
            })

        total_elapsed = time.perf_counter() - t_start
        overall = storage.report()
        overall_hit_rate = overall["hit_rate_pct"] / 100.0
        decision = decision_gate(overall_hit_rate)

        print(f"\n{'='*70}")
        print("ARTIFACT CACHE BASELINE REPORT")
        print(f"{'='*70}")
        print("Date: 2026-06-06")
        print("Storage: CachedFileSystemStorage (mtime + size validation, LRU 256)")
        print("Request cache: _ChapterDataCache (per-chapter, no mtime)")
        print(f"{'='*70}")
        print("\nBenchmark: 5 sequential chapter runs")
        print("File access pattern: ~7 LLM calls per chapter, reading:")
        print("  spec.json, story_bible.json, character_bible.json,")
        print("  outline.json, style_profile.json, chapter plan,")
        print("  state_packet, draft, previous chapter")
        print(f"\nTotal elapsed: {round(total_elapsed * 1000, 1)}ms")
        print("\nPer-chapter breakdown:")
        print(f"  {'Ch':>4} {'Reads':>7} {'Hits':>7} {'Misses':>8} {'Hit%':>7} {'Time':>8}")
        print(f"  {'-'*4} {'-'*7} {'-'*7} {'-'*8} {'-'*7} {'-'*8}")
        for s in per_chapter:
            print(
                f"  {s['chapter']:>4} {s['reads']:>7} {s['hits']:>7}"
                f" {s['misses']:>8} {s['hit_rate_pct']:>6.1f}%"
                f" {s['elapsed_ms']:>7.1f}ms"
            )

        print("\nCumulative results:")
        print(f"  Total reads:     {overall['total_reads']}")
        print(f"  Total hits:      {overall['hits']}")
        print(f"  Total misses:    {overall['misses']}")
        print(f"  Overall hit rate:{overall['hit_rate_pct']}%")
        print(f"  JSON cache size: {overall['json_cache_entries']}")
        print(f"  Text cache size: {overall['text_cache_entries']}")

        print(f"\n{'='*70}")
        print("DECISION GATE")
        print(f"{'='*70}")
        print(f"  Measured hit rate:  {overall['hit_rate_pct']}%")
        print(f"  Threshold:          {HIT_RATE_THRESHOLD * 100}%")
        print(f"  Decision:           {decision}")
        if decision == "skip":
            print("  Reason: Existing CachedFileSystemStorage provides sufficient")
            print("  caching. Cross-chapter reads of spec/bible/outline/style are")
            print("  served from LRU cache. No additional ArtifactCache needed.")
        else:
            print("  Reason: Hit rate below threshold. Consider adding per-worker")
            print("  ArtifactCache with mtime invalidation.")
        print(f"{'='*70}\n")

        # Assert baseline sanity
        assert overall["total_reads"] > 0, "No reads recorded"
        assert overall["json_cache_entries"] > 0, "No JSON cache entries"
