"""Tests for HumanizeLibraryRetriever — union fuse, BM25, cosine."""

from __future__ import annotations

import pytest

from novel_forge.core.schemas.humanize_library import HumanizeLibraryEntry
from novel_forge.memory.humanize_library_store import (
    HumanizeLibrary,
    seed_builtin_patterns,
)
from novel_forge.memory.humanize_retrieval import (
    HumanizeLibraryRetriever,
    iter_sentences,
    normalize_scores,
    union_fuse,
)

# ---------------------------------------------------------------------------
# Sentence splitting
# ---------------------------------------------------------------------------


class TestIterSentences:
    def test_chinese_sentences(self) -> None:
        text = "这是第一句。这是第二句！这是第三句？"
        result = iter_sentences(text)
        assert len(result) == 3
        assert result[0] == "这是第一句。"
        assert result[1] == "这是第二句！"
        assert result[2] == "这是第三句？"

    def test_newline_split(self) -> None:
        text = "第一行\n第二行\n第三行"
        result = iter_sentences(text)
        assert len(result) == 3

    def test_empty_text(self) -> None:
        assert iter_sentences("") == []
        assert iter_sentences("   ") == []

    def test_mixed_content(self) -> None:
        text = "他说：'你好。'然后走了。"
        result = iter_sentences(text)
        assert len(result) >= 1


# ---------------------------------------------------------------------------
# Score normalization
# ---------------------------------------------------------------------------


class TestNormalizeScores:
    def test_basic_normalization(self) -> None:
        scores = [0.0, 5.0, 10.0]
        result = normalize_scores(scores)
        assert result == [0.0, 0.5, 1.0]

    def test_empty_list(self) -> None:
        assert normalize_scores([]) == []

    def test_identical_scores(self) -> None:
        result = normalize_scores([5.0, 5.0, 5.0])
        assert result == [0.0, 0.0, 0.0]

    def test_single_score(self) -> None:
        result = normalize_scores([42.0])
        assert result == [0.0]

    def test_negative_scores(self) -> None:
        result = normalize_scores([-10.0, 0.0, 10.0])
        assert result == [0.0, 0.5, 1.0]


# ---------------------------------------------------------------------------
# Union fuse
# ---------------------------------------------------------------------------


class TestUnionFuse:
    def test_basic_fuse(self) -> None:
        fts_hits = [("b", 12.0), ("d", 8.5), ("a", 3.0)]
        vec_hits = [("a", 0.9), ("b", 0.7), ("c", 0.5)]
        result = union_fuse(vec_hits, fts_hits)

        assert "a" in result
        assert "b" in result
        assert "c" in result
        assert "d" in result
        assert all(0 <= v <= 1 for v in result.values())

    def test_max_fusion(self) -> None:
        # 'a' has BM25=0.0 (normalized) and cosine=0.9 → max = 0.9
        fts_hits = [("a", 0.0)]  # All same → normalized to 0.0
        vec_hits = [("a", 0.9)]
        result = union_fuse(vec_hits, fts_hits)
        assert result["a"] == pytest.approx(0.9)

    def test_empty_inputs(self) -> None:
        assert union_fuse([], []) == {}

    def test_only_fts(self) -> None:
        fts_hits = [("a", 10.0), ("b", 5.0)]
        result = union_fuse([], fts_hits)
        assert "a" in result
        assert "b" in result
        assert result["a"] == 1.0  # max BM25
        assert result["b"] == 0.0  # min BM25

    def test_only_vec(self) -> None:
        vec_hits = [("a", 0.9), ("b", 0.3)]
        result = union_fuse(vec_hits, [])
        assert result["a"] == pytest.approx(0.9)
        assert result["b"] == pytest.approx(0.3)

    def test_cosine_clipping(self) -> None:
        # Cosine > 1.0 should be clipped
        vec_hits = [("a", 1.5)]
        result = union_fuse(vec_hits, [])
        assert result["a"] == 1.0

        # Cosine < 0 should be clipped
        vec_hits = [("b", -0.5)]
        result = union_fuse(vec_hits, [])
        assert result["b"] == 0.0


# ---------------------------------------------------------------------------
# Retriever
# ---------------------------------------------------------------------------


class TestRetriever:
    def test_retrieve_empty_library(self) -> None:
        lib = HumanizeLibrary.in_memory()
        retriever = HumanizeLibraryRetriever()
        result = retriever.retrieve(lib, "这是一段测试文本。")
        assert result == []

    def test_retrieve_with_seeded_library(self) -> None:
        lib = HumanizeLibrary.in_memory()
        seed_builtin_patterns(lib)
        retriever = HumanizeLibraryRetriever(sim_threshold=0.0)
        # Text that should match some patterns
        text = "这种显著性的 inflation 是非常值得关注的。"
        result = retriever.retrieve(lib, text)
        # Should return some results (BM25 matches)
        assert isinstance(result, list)

    def test_retrieve_returns_dicts(self) -> None:
        lib = HumanizeLibrary.in_memory()
        lib.add(
            HumanizeLibraryEntry(
                pattern_id="lib_user_aabb0011",
                pattern_name="测试模式",
                category="测试",
                severity="high",
                detection_method="regex",
                source="user",
                keywords=["测试", "模式"],
                example_phrases=["这是一个测试"],
            )
        )
        retriever = HumanizeLibraryRetriever(sim_threshold=0.0)
        result = retriever.retrieve(lib, "这是一个测试文本，用于测试模式匹配。")
        assert isinstance(result, list)
        if result:
            assert "pattern_id" in result[0]
            assert "pattern_name" in result[0]
            assert "category" in result[0]
            assert "severity" in result[0]
            assert "evidence_quote" in result[0]
            assert "source" in result[0]
            assert "similarity" in result[0]

    def test_retrieve_handles_exception(self) -> None:
        """Retriever should return empty list on any exception."""
        retriever = HumanizeLibraryRetriever()
        # Pass something that's not a HumanizeLibrary
        result = retriever.retrieve("not_a_library", "text")  # type: ignore[arg-type]
        assert result == []

    def test_retrieve_with_embedder(self) -> None:
        """Test retrieval with a fake embedder."""
        from tests.unit._fake_zvec_humanize import FakeZvecStore

        lib = HumanizeLibrary.in_memory()
        lib.add(
            HumanizeLibraryEntry(
                pattern_id="lib_user_aabb0011",
                pattern_name="测试模式",
                category="测试",
                severity="high",
                detection_method="regex",
                source="user",
                keywords=["测试"],
            )
        )
        fake_zvec = FakeZvecStore()
        fake_zvec.add("lib_user_aabb0011", [1.0, 0.0], {"pattern_id": "lib_user_aabb0011"})
        lib.setup_zvec(fake_zvec)

        class FakeEmbedder:
            def embed(self, texts: list[str]) -> list[list[float]]:
                return [[1.0, 0.0] for _ in texts]

        retriever = HumanizeLibraryRetriever(embedder=FakeEmbedder(), sim_threshold=0.0)
        result = retriever.retrieve(lib, "测试文本。")
        assert isinstance(result, list)

    def test_deterministic_regex_scans_all_occurrences_without_global_top_k(self) -> None:
        lib = HumanizeLibrary.in_memory()
        lib.add(
            HumanizeLibraryEntry(
                pattern_id="lib_user_aabb0011",
                pattern_name="转折模板",
                category="模板句式",
                severity="high",
                detection_method="regex",
                detection_config={"patterns": [r"不是[^。]{1,20}而是[^。]{1,20}"]},
                source="user",
            )
        )
        text = "不是风停了，而是门关了。\n\n不是他迟到，而是钟快了。"

        result = HumanizeLibraryRetriever(sim_threshold=0.99).retrieve(lib, text, top_k=1)

        hits = [item for item in result if item["pattern_id"] == "lib_user_aabb0011"]
        assert len(hits) == 2
        assert {item["paragraph_index"] for item in hits} == {0, 1}
        assert all(item["match_method"] == "deterministic_regex" for item in hits)
        assert all(isinstance(item["span_start"], int) for item in hits)

    def test_exact_examples_are_exhaustive_library_candidates(self) -> None:
        lib = HumanizeLibrary.in_memory()
        lib.add(
            HumanizeLibraryEntry(
                pattern_id="lib_user_aabb0011",
                pattern_name="自定义口头禅",
                category="重复表达",
                severity="medium",
                detection_method="mixed",
                example_phrases=["说到底还是这样"],
                source="user",
            )
        )

        result = HumanizeLibraryRetriever(sim_threshold=1.0).retrieve(
            lib,
            "说到底还是这样。\n隔了一段。说到底还是这样。",
            top_k=1,
        )

        exact = [item for item in result if item["match_method"] == "deterministic_example"]
        assert len(exact) == 2

    def test_project_scoped_entries_do_not_leak(self) -> None:
        lib = HumanizeLibrary.in_memory()
        lib.add(
            HumanizeLibraryEntry(
                pattern_id="lib_user_aabb0011",
                pattern_name="项目专用",
                category="项目规则",
                severity="high",
                detection_method="regex",
                detection_config={"pattern": "秘密短语"},
                source="user",
                project_id="project-a",
            )
        )
        retriever = HumanizeLibraryRetriever()

        assert retriever.retrieve(lib, "秘密短语", project_id="project-b") == []
        assert retriever.retrieve(lib, "秘密短语", project_id="project-a")

    def test_unattached_vector_store_does_not_waste_embedding_calls(self) -> None:
        lib = HumanizeLibrary.in_memory()
        lib.add(
            HumanizeLibraryEntry(
                pattern_id="lib_user_aabb0011",
                pattern_name="语义模式",
                category="语义",
                severity="medium",
                detection_method="vector_only",
                source="user",
                notes="寻找意思相近但字面不同的表达",
            )
        )

        class CountingEmbedder:
            def __init__(self) -> None:
                self.calls = 0

            def embed(self, texts: list[str]) -> list[list[float]]:
                self.calls += 1
                return [[1.0, 0.0] for _text in texts]

        embedder = CountingEmbedder()
        HumanizeLibraryRetriever(embedder=embedder).retrieve(lib, "完全不同的正文。")

        assert embedder.calls == 0

    def test_window_embeddings_are_batched_when_vector_index_is_attached(self) -> None:
        from tests.unit._fake_zvec_humanize import FakeZvecStore

        lib = HumanizeLibrary.in_memory()
        entry = HumanizeLibraryEntry(
            pattern_id="lib_user_aabb0011",
            pattern_name="语义模式",
            category="语义",
            severity="medium",
            detection_method="vector_only",
            source="user",
            notes="寻找意思相近但字面不同的表达",
        )
        lib.add(entry)
        fake_zvec = FakeZvecStore()
        fake_zvec.add(entry.pattern_id, [1.0, 0.0], {"pattern_id": entry.pattern_id})
        lib.setup_zvec(fake_zvec)

        class BatchEmbedder:
            def __init__(self) -> None:
                self.calls: list[list[str]] = []

            def embed(self, texts: list[str]) -> list[list[float]]:
                self.calls.append(list(texts))
                return [[1.0, 0.0] for _text in texts]

        embedder = BatchEmbedder()
        HumanizeLibraryRetriever(embedder=embedder, sim_threshold=0.0).retrieve(
            lib,
            "第一段不同说法。\n\n第二段另一种说法。",
        )

        assert len(embedder.calls) == 1
        assert len(embedder.calls[0]) == 2
