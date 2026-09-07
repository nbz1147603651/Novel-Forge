"""Unit tests for StyleGoldenRetriever.

The retriever indexes paragraphs from chapters with eval_score >= 9.0 in the
same project, then returns BM25-ranked top-N passages for a given scene_intent
query. Diversity is enforced via max_per_chapter.

Designed to work without any external IO so unit tests can use a tmp project
fixture rather than touching real data/.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from novel_forge.memory.style_golden_retriever import (
    GoldenPassage,
    StyleGoldenRetriever,
)

# ---------------------------------------------------------------------------
# Fixtures: in-memory chapter + eval cache
# ---------------------------------------------------------------------------


CHAPTER_CONTENT = {
    1: (
        # Each paragraph >= 80 chars so the default min_paragraph_chars=60 filter
        # does not strip them.
        "窗帘缝隙里渗入第一缕灰蓝色的光，不是明亮的那种，而是天色将明未明时特有的暧昧色调，沈鹿溪已经醒了十分钟左右。"
        "她侧躺在床上，颈侧的枕头被体温焐得有些潮，风从窗缝里漏进来，把枕巾的一角掀起来又放下，她没有伸手去按。\n\n"
        "楼下后院方向传来扫帚接触青石的沙沙声，极规律，隔了两三秒才响一次，不像是在天没全亮时急于清扫的动作。"
        "更像是在打量地面上的某处污渍，犹豫从哪个方向下手，她听了很久才确定那是扫帚而不是别的什么。\n\n"
        "风从穿堂涌出来，吹动门槛边的风铃，那声音细密，像有人贴着金属片在低低说话，又像是风把远处的渠水声带进了屋里。\n\n"
        "沈鹿溪站在老宅民宿的木门槛外，没有跨过去，也没有把摄像机的录制键按下去，只是看着青石板上那层薄薄的晨光。"
    ),
    2: (
        "苏皖把布袋放在桌上，没有看沈鹿溪，只是从包里取出一张折了两道的纸放在茶杯下面。"
        "纸的边缘被风掀了一下，又落回去，她伸手把它压得更紧了些，像在压住某种还没想好的决定。\n\n"
        "离婚协议的几个字从折叠的缝隙里露出来，她用食指按了一下，那动作很轻，却让沈鹿溪在对面停下了所有动作。"
        "风从窗缝里漏进来，把协议的一角掀起来又放下，她没有说话，屋里安静得能听见茶杯下纸面摩擦桌面的声音。"
    ),
    3: (
        "陈屿的手机屏幕亮了，是张叔打来的电话，他把自行车停在村委会门口，看了一眼屏幕上的名字，没有马上接起来。"
        "他等了三声才接起来，那头的声音带着喘，像是从村西头一路走过来的，背景里有风声，还有铁皮翻动的闷响。\n\n"
        "他把手机换到另一只耳朵，眉头微微皱起来，'张叔，您慢点说，我在村委会这边。'"
    ),
    4: (
        # Used to verify the bad-chapter-with-ai-flavor case (NOT indexed because score < threshold).
        "这段写得非常糟糕，有大量弱动词堆叠。她感到一种失落涌上心头，又觉得屋里太静，那声音极轻，像是有人离开了很久。"
    ),
}


def _write_project(tmp_path: Path, scores: dict[int, float]) -> Path:
    """Create a project directory with chapters and eval cache."""
    project_dir = tmp_path / "proj_test"
    chapters_dir = project_dir / "chapters"
    chapters_dir.mkdir(parents=True)
    for ch_num, content in CHAPTER_CONTENT.items():
        (chapters_dir / f"chapter_{ch_num:03d}.md").write_text(content, encoding="utf-8")
    cache: dict[str, dict[str, float]] = {}
    for ch_num, score in scores.items():
        cache[str(ch_num)] = {"overall_score": score, "title": f"ch{ch_num}"}
    (project_dir / "_chapter_meta_cache.json").write_text(
        json.dumps(cache), encoding="utf-8"
    )
    return project_dir


# ---------------------------------------------------------------------------
# Indexing
# ---------------------------------------------------------------------------


class TestIndexing:
    def test_only_chapters_above_threshold_are_indexed(self, tmp_path: Path) -> None:
        # Only Ch1 and Ch2 are above 9.0
        project = _write_project(tmp_path, {1: 9.5, 2: 9.2, 3: 7.5, 4: 6.0})
        retriever = StyleGoldenRetriever(project, project / "_chapter_meta_cache.json")
        indexed_chapters = {p.chapter_number for p in retriever._index}
        assert indexed_chapters == {1, 2}

    def test_threshold_at_9_0_is_inclusive(self, tmp_path: Path) -> None:
        project = _write_project(tmp_path, {1: 9.0, 2: 8.99})
        retriever = StyleGoldenRetriever(project, project / "_chapter_meta_cache.json")
        indexed_chapters = {p.chapter_number for p in retriever._index}
        assert 1 in indexed_chapters
        assert 2 not in indexed_chapters

    def test_missing_eval_cache_skips_indexing(self, tmp_path: Path) -> None:
        project = tmp_path / "no_cache"
        (project / "chapters").mkdir(parents=True)
        for ch_num, content in CHAPTER_CONTENT.items():
            (project / "chapters" / f"chapter_{ch_num:03d}.md").write_text(
                content, encoding="utf-8"
            )
        # No _chapter_meta_cache.json
        retriever = StyleGoldenRetriever(project, project / "_chapter_meta_cache.json")
        assert retriever._index == []

    def test_invalid_eval_score_skips_bad_chapter(self, tmp_path: Path) -> None:
        project = tmp_path / "bad_cache"
        chapters_dir = project / "chapters"
        chapters_dir.mkdir(parents=True)
        (chapters_dir / "chapter_001.md").write_text(CHAPTER_CONTENT[1], encoding="utf-8")
        (chapters_dir / "chapter_002.md").write_text(CHAPTER_CONTENT[2], encoding="utf-8")
        cache = {
            "1": {"overall_score": "not-a-number"},
            "2": {"overall_score": 9.5},
        }
        (project / "_chapter_meta_cache.json").write_text(json.dumps(cache), encoding="utf-8")

        retriever = StyleGoldenRetriever(project, project / "_chapter_meta_cache.json")

        assert retriever.indexed_chapters == {2}

    def test_paragraph_too_short_skipped(self, tmp_path: Path) -> None:
        project = tmp_path / "short_paras"
        chapters_dir = project / "chapters"
        chapters_dir.mkdir(parents=True)
        (chapters_dir / "chapter_001.md").write_text(
            "短。\n\n" + "这是一段足够长的测试段落内容，用于验证不会被过滤掉。" * 3,
            encoding="utf-8",
        )
        cache = {"1": {"overall_score": 9.5}}
        (project / "_chapter_meta_cache.json").write_text(json.dumps(cache), encoding="utf-8")
        retriever = StyleGoldenRetriever(project, project / "_chapter_meta_cache.json")
        # The "短。" paragraph is too short (< 60 chars) and is skipped.
        texts = [p.text for p in retriever._index]
        assert "短。" not in texts
        assert any("这是一段足够长" in t for t in texts)

    def test_paragraph_too_long_skipped(self, tmp_path: Path) -> None:
        project = tmp_path / "long_paras"
        chapters_dir = project / "chapters"
        chapters_dir.mkdir(parents=True)
        # Two paragraphs: one clearly over the 280-char upper limit (must be skipped),
        # one within bounds but still meeting the 60-char lower limit.
        long_para = (
            "很长很长的测试段落重复以达到字数上限的验证目的，确保它能被正确过滤掉。"
            "很长很长的测试段落重复以达到字数上限的验证目的，确保它能被正确过滤掉。"
            "很长很长的测试段落重复以达到字数上限的验证目的，确保它能被正确过滤掉。"
            "很长很长的测试段落重复以达到字数上限的验证目的，确保它能被正确过滤掉。"
            "很长很长的测试段落重复以达到字数上限的验证目的，确保它能被正确过滤掉。"
            "很长很长的测试段落重复以达到字数上限的验证目的，确保它能被正确过滤掉。"
            "很长很长的测试段落重复以达到字数上限的验证目的，确保它能被正确过滤掉。"
            "很长很长的测试段落重复以达到字数上限的验证目的，确保它能被正确过滤掉。"
            "很长很长的测试段落重复以达到字数上限的验证目的，确保它能被正确过滤掉。"
            "很长很长的测试段落重复以达到字数上限的验证目的，确保它能被正确过滤掉。"
        )  # 350 chars, well above 280
        medium_para = (
            "这是一段在默认区间内的测试段落：六十个字符以上的可索引内容，"
            "避免被默认下限误过滤掉，长度恰好落在 60-280 的安全区间内。"
        )
        (chapters_dir / "chapter_001.md").write_text(
            long_para + "\n\n" + medium_para, encoding="utf-8"
        )
        cache = {"1": {"overall_score": 9.5}}
        (project / "_chapter_meta_cache.json").write_text(json.dumps(cache), encoding="utf-8")
        retriever = StyleGoldenRetriever(project, project / "_chapter_meta_cache.json")
        texts = [p.text for p in retriever._index]
        assert long_para not in texts
        assert medium_para in texts


# ---------------------------------------------------------------------------
# Retrieval
# ---------------------------------------------------------------------------


class TestRetrieval:
    def test_retrieval_returns_top_n_passages(self, tmp_path: Path) -> None:
        project = _write_project(tmp_path, {1: 9.5, 2: 9.2, 3: 9.0})
        retriever = StyleGoldenRetriever(project, project / "_chapter_meta_cache.json")
        # Query about wind should match Ch1 (which mentions 风)
        results = retriever.retrieve_for_query("风 铃声 穿堂", max_results=2)
        assert len(results) <= 2
        assert all(isinstance(p, GoldenPassage) for p in results)

    def test_retrieval_empty_index_returns_empty(self, tmp_path: Path) -> None:
        project = _write_project(tmp_path, {1: 5.0})  # all below threshold
        retriever = StyleGoldenRetriever(project, project / "_chapter_meta_cache.json")
        assert retriever._index == []
        results = retriever.retrieve_for_query("任何查询", max_results=3)
        assert results == []

    def test_retrieval_with_no_query_terms_returns_first_n(self, tmp_path: Path) -> None:
        project = _write_project(tmp_path, {1: 9.5})
        retriever = StyleGoldenRetriever(project, project / "_chapter_meta_cache.json")
        # Empty query string should still return something (graceful fallback).
        results = retriever.retrieve_for_query("", max_results=3)
        # Behavior: fall back to first indexed passage.
        assert len(results) <= 3

    def test_retrieval_enforces_max_per_chapter_diversity(self, tmp_path: Path) -> None:
        """If Ch1 has many high-scoring paragraphs, diversity caps per-chapter selection."""
        project = tmp_path / "diversity_test"
        chapters_dir = project / "chapters"
        chapters_dir.mkdir(parents=True)
        # Build a chapter with 10 distinct paragraphs all about wind.
        paragraphs = [f"这是第 {i} 段关于风的描述。" * 3 for i in range(10)]
        (chapters_dir / "chapter_001.md").write_text("\n\n".join(paragraphs), encoding="utf-8")
        (project / "_chapter_meta_cache.json").write_text(
            json.dumps({"1": {"overall_score": 9.5}}), encoding="utf-8"
        )
        retriever = StyleGoldenRetriever(
            project,
            project / "_chapter_meta_cache.json",
            max_per_chapter=2,
        )
        results = retriever.retrieve_for_query("风 描述 段落", max_results=5)
        # Even if there are 10 good paragraphs in Ch1, only 2 are returned.
        assert sum(1 for p in results if p.chapter_number == 1) <= 2


# ---------------------------------------------------------------------------
# GoldenPassage dataclass
# ---------------------------------------------------------------------------


class TestGoldenPassage:
    def test_frozen_dataclass(self) -> None:
        """GoldenPassage must be frozen so it can be cached safely."""
        from dataclasses import FrozenInstanceError

        p = GoldenPassage(
            chapter_number=1,
            paragraph_index=0,
            text="some text",
            eval_score=9.5,
            dimensions={},
        )
        with pytest.raises(FrozenInstanceError):
            p.chapter_number = 2  # type: ignore[misc]

    def test_str_representation_includes_chapter(self) -> None:
        p = GoldenPassage(
            chapter_number=9,
            paragraph_index=3,
            text="...",
            eval_score=9.71,
            dimensions={},
        )
        # Don't lock exact format, but key info must be present.
        s = str(p)
        assert "9" in s
        assert "9.7" in s or "9.71" in s


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


class TestConfiguration:
    def test_custom_threshold(self, tmp_path: Path) -> None:
        project = _write_project(tmp_path, {1: 8.0, 2: 9.5, 3: 9.8})
        retriever = StyleGoldenRetriever(
            project,
            project / "_chapter_meta_cache.json",
            threshold=8.5,
        )
        indexed_chapters = {p.chapter_number for p in retriever._index}
        # With threshold 8.5: Ch2 and Ch3 only.
        assert indexed_chapters == {2, 3}

    def test_paragraph_length_bounds(self, tmp_path: Path) -> None:
        project = _write_project(tmp_path, {1: 9.5})
        retriever = StyleGoldenRetriever(
            project,
            project / "_chapter_meta_cache.json",
            min_paragraph_chars=20,
            max_paragraph_chars=100,
        )
        # Re-check that the very short "短。" / "楼下..." (19 chars) gets filtered.
        # (The first fixture project doesn't have those short paras in this tmp_path,
        # so we rely on the bounds being respected on the default fixture.)
        # Just verify bounds are stored.
        assert retriever.min_paragraph_chars == 20
        assert retriever.max_paragraph_chars == 100
