"""Tests for ForbiddenElementRegistry hybrid registry with merged source deduplication."""

from __future__ import annotations

import unicodedata
from pathlib import Path

from novel_forge.core.domain.forbidden_element_registry import ForbiddenElementRegistry


class TestMergeDeduplication:
    """test_merge_deduplication: deduplication across sources with different items."""

    def test_merge_deduplication(self, tmp_path: Path) -> None:
        """Items appearing in multiple sources appear only once."""
        seeds_path = tmp_path / "config"
        seeds_path.mkdir(parents=True)
        (seeds_path / "forbidden_element_seeds.yaml").write_text(
            "rhetorical_imagery_hints:\n  - 月光\n  - 光芒\n",
            encoding="utf-8",
        )
        registry = ForbiddenElementRegistry(tmp_path)

        bible_derived = {
            "rhetorical_imagery_hints": ["月光", "灯光"],  # 月光 is duplicate
            "kinship_and_address_terms": ["父亲"],
            "abstract_emotion_keywords": [],
        }

        merged = registry.merge_sources(bible_derived=bible_derived)
        rhetorical = merged["rhetorical_imagery_hints"]

        assert rhetorical.count("月光") == 1
        assert "光芒" in rhetorical
        assert "灯光" in rhetorical

    def test_fallback_universal_minimal(self, tmp_path: Path) -> None:
        """test_fallback_universal_minimal: universal_minimal loads when no project seeds exist."""
        registry = ForbiddenElementRegistry(tmp_path)

        merged = registry.merge_sources()
        assert "月光" in merged["rhetorical_imagery_hints"]
        assert "父亲" in merged["kinship_and_address_terms"]

    def test_normalize_unicode(self, tmp_path: Path) -> None:
        """test_normalize_unicode: NFC normalization and whitespace stripping."""
        # NFD form "é" (e + combining acute) should normalize to NFC "é"
        seeds_path = tmp_path / "config"
        seeds_path.mkdir(parents=True)
        (seeds_path / "forbidden_element_seeds.yaml").write_text(
            "rhetorical_imagery_hints:\n  - '咖啡 '  # trailing space\n",
            encoding="utf-8",
        )
        registry = ForbiddenElementRegistry(tmp_path)

        # NFD normalized input
        nfd_item = unicodedata.normalize("NFD", "café")
        merged = registry.merge_sources(
            bible_derived={
                "rhetorical_imagery_hints": [nfd_item],
                "kinship_and_address_terms": [],
                "abstract_emotion_keywords": [],
            }
        )

        # Should be NFC normalized and stripped
        assert any("café" in item and not item.endswith(" ") for item in merged["rhetorical_imagery_hints"])

    def test_lazy_loading(self, tmp_path: Path) -> None:
        """test_lazy_loading: YAML is not read until merge_sources() is called."""
        registry = ForbiddenElementRegistry(tmp_path)

        # _merged_cache should be None before first call
        assert registry._merged_cache is None

        registry.merge_sources()

        # After call, should be cached
        assert registry._merged_cache is not None

    def test_priority_override(self, tmp_path: Path) -> None:
        """test_priority_override: project seeds take priority over bible-derived and templates."""
        seeds_path = tmp_path / "config"
        seeds_path.mkdir(parents=True)
        # Project seed customizes "月光" to "自定义月光"
        (seeds_path / "forbidden_element_seeds.yaml").write_text(
            "rhetorical_imagery_hints:\n  - 自定义月光\n",
            encoding="utf-8",
        )
        registry = ForbiddenElementRegistry(tmp_path)

        merged = registry.merge_sources(
            bible_derived={
                "rhetorical_imagery_hints": ["月光"],
                "kinship_and_address_terms": [],
                "abstract_emotion_keywords": [],
            }
        )

        # Project seed should appear, template/bible-derived should not override
        assert "自定义月光" in merged["rhetorical_imagery_hints"]

    def test_get_all_root_sets(self, tmp_path: Path) -> None:
        """test_get_all_root_sets: returns correct tuple of frozensets."""
        seeds_path = tmp_path / "config"
        seeds_path.mkdir(parents=True)
        (seeds_path / "forbidden_element_seeds.yaml").write_text(
            "rhetorical_imagery_hints:\n  - 月光\n  - 光芒\n"
            "kinship_and_address_terms:\n  - 父亲\n"
            "abstract_emotion_keywords:\n  - 紧张\n",
            encoding="utf-8",
        )
        registry = ForbiddenElementRegistry(tmp_path)

        rhetorical, kinship, emotion = registry.get_all_root_sets()

        assert isinstance(rhetorical, frozenset)
        assert isinstance(kinship, frozenset)
        assert isinstance(emotion, frozenset)
        assert "月光" in rhetorical
        assert "父亲" in kinship
        assert "紧张" in emotion

    def test_longest_first_sorting(self, tmp_path: Path) -> None:
        """Compound words appear before their prefixes after sorting."""
        seeds_path = tmp_path / "config"
        seeds_path.mkdir(parents=True)
        (seeds_path / "forbidden_element_seeds.yaml").write_text(
            "rhetorical_imagery_hints:\n  - 父\n  - 父亲\n  - 哥哥\n  - 哥\n",
            encoding="utf-8",
        )
        registry = ForbiddenElementRegistry(tmp_path)

        merged = registry.merge_sources()
        rhetorical = merged["rhetorical_imagery_hints"]

        # Longer items should come first
        idx_father = rhetorical.index("父亲")
        idx_fu = rhetorical.index("父")
        assert idx_father < idx_fu

        idx_brother = rhetorical.index("哥哥")
        idx_ge = rhetorical.index("哥")
        assert idx_brother < idx_ge
