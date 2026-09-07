"""Tests for the four-layer forbidden element architecture."""

from __future__ import annotations

import tempfile
from pathlib import Path

from novel_forge.core.domain.bible_derived_provider import BibleDerivedProvider
from novel_forge.core.domain.forbidden_element_registry import ForbiddenElementRegistry
from novel_forge.pipeline.steps.continuity_eval_step import (
    _detect_forbidden_elements,
    _filter_forbidden_elements,
    _is_contextual_anchor_element,
    _looks_like_rhetorical_imagery,
)


class TestForbiddenElementRegistry:
    """Tests for layer 1 & 2 (project seeds + genre templates)."""

    def test_init_project_seeds_copies_template(self):
        with tempfile.TemporaryDirectory() as tmp:
            project_path = Path(tmp)
            path = ForbiddenElementRegistry.init_project_seeds(project_path, genre="chinese_fantasy")
            assert path.exists()
            registry = ForbiddenElementRegistry(project_path)
            assert registry.has_project_seeds
            assert len(registry.get_rhetorical_hints()) > 0
            assert len(registry.get_kinship_terms()) > 0
            assert len(registry.get_emotion_keywords()) > 0

    def test_init_project_seeds_fallback_to_universal(self):
        with tempfile.TemporaryDirectory() as tmp:
            project_path = Path(tmp)
            path = ForbiddenElementRegistry.init_project_seeds(project_path, genre="nonexistent")
            assert path.exists()
            registry = ForbiddenElementRegistry(project_path)
            assert registry.has_project_seeds

    def test_save_and_load_seeds(self):
        with tempfile.TemporaryDirectory() as tmp:
            project_path = Path(tmp)
            ForbiddenElementRegistry.save_seeds(
                project_path,
                {
                    "rhetorical_imagery_hints": ["月光", "寒意"],
                    "kinship_and_address_terms": ["父亲"],
                    "abstract_emotion_keywords": ["紧张"],
                },
                metadata={"test": True},
            )
            registry = ForbiddenElementRegistry(project_path)
            assert "月光" in registry.get_rhetorical_hints()
            assert "父亲" in registry.get_kinship_terms()
            assert "紧张" in registry.get_emotion_keywords()

    def test_save_seeds_merges_existing_template_terms(self):
        with tempfile.TemporaryDirectory() as tmp:
            project_path = Path(tmp)
            ForbiddenElementRegistry.init_project_seeds(project_path, genre="scifi")
            ForbiddenElementRegistry.save_seeds(
                project_path,
                {
                    "rhetorical_imagery_hints": ["量子回声"],
                    "kinship_and_address_terms": [],
                    "abstract_emotion_keywords": [],
                },
            )
            registry = ForbiddenElementRegistry(project_path)
            assert "舰长" in registry.get_kinship_terms()
            assert "量子回声" in registry.get_rhetorical_hints()

    def test_init_project_seeds_preserves_existing_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            project_path = Path(tmp)
            ForbiddenElementRegistry.save_seeds(
                project_path,
                {
                    "rhetorical_imagery_hints": ["自定义意象"],
                    "kinship_and_address_terms": ["自定义称谓"],
                    "abstract_emotion_keywords": ["自定义情绪"],
                },
                metadata={"custom": True},
            )
            ForbiddenElementRegistry.init_project_seeds(project_path, genre="scifi")
            registry = ForbiddenElementRegistry(project_path)
            assert "自定义意象" in registry.get_rhetorical_hints()
            assert "自定义称谓" in registry.get_kinship_terms()
            assert "自定义情绪" in registry.get_emotion_keywords()

    def test_empty_project_has_no_seeds(self):
        with tempfile.TemporaryDirectory() as tmp:
            registry = ForbiddenElementRegistry(Path(tmp))
            assert not registry.has_project_seeds
            assert registry.get_rhetorical_hints() == frozenset()


class TestBibleDerivedProvider:
    """Tests for layer 3 (Bible derivation)."""

    def test_derive_from_bible_with_characters(self):
        provider = BibleDerivedProvider()
        bible = {
            "themes": ["月光下的孤独", "权力的欲望"],
            "tone": "黑暗、压抑",
            "era": "古代王朝",
        }
        characters = [
            {
                "relationships": {
                    "李师父": "他是主角的师父，教导武艺",
                    "王夫人": "主角的母亲",
                },
                "social_status": "六品翰林",
                "personality": "冷静、警觉",
                "notes": "",
            }
        ]
        result = provider.derive_from_bible(bible, characters=characters)

        assert "月光" in result["rhetorical_imagery_hints"]
        assert "师父" in result["kinship_and_address_terms"]
        assert "母亲" in result["kinship_and_address_terms"]
        assert "警觉" in result["abstract_emotion_keywords"]
        assert "冷静" in result["abstract_emotion_keywords"]

    def test_derive_from_bible_no_bible_returns_empty(self):
        provider = BibleDerivedProvider()
        result = provider.derive_from_bible({})
        assert result == {
            "rhetorical_imagery_hints": [],
            "kinship_and_address_terms": [],
            "abstract_emotion_keywords": [],
        }

    def test_derive_from_packet_with_story_bible(self):
        provider = BibleDerivedProvider()

        class FakePacket:
            character_profiles = [
                {
                    "relationships": {"师父": "他是我的师父"},
                    "social_status": "王爷",
                    "personality": "愤怒",
                }
            ]
            story_bible = {
                "themes": ["雪夜的背叛", "月光下的寒意"],
                "tone": "寒冷",
            }

        result = provider.derive_from_packet(FakePacket())
        assert "师父" in result["kinship_and_address_terms"]
        assert "王爷" in result["kinship_and_address_terms"]
        assert "愤怒" in result["abstract_emotion_keywords"]
        assert "寒意" in result["rhetorical_imagery_hints"]


class TestContinuityEvalStepNoHardcode:
    """Tests that continuity_eval_step works without hard-coded constants."""

    def test_looks_like_rhetorical_imagery_with_hints(self):
        assert _looks_like_rhetorical_imagery("月光", rhetorical_hints=frozenset({"月光"}))
        assert not _looks_like_rhetorical_imagery("月光", rhetorical_hints=frozenset())
        assert not _looks_like_rhetorical_imagery("月光")

    def test_is_contextual_anchor_element_with_kinship(self):
        assert _is_contextual_anchor_element("父亲", kinship_terms=frozenset({"父亲"}))
        assert not _is_contextual_anchor_element("父亲", kinship_terms=frozenset())

    def test_filter_forbidden_elements_with_emotion_keywords(self):
        elements = ["警觉", "月光", "父亲"]
        filtered = _filter_forbidden_elements(
            elements,
            emotion_keywords=frozenset({"警觉"}),
            kinship_terms=frozenset({"父亲"}),
        )
        assert "警觉" not in filtered
        assert "父亲" not in filtered
        assert "月光" in filtered

    def test_filter_forbidden_elements_no_sets_permissive(self):
        elements = ["警觉", "月光", "父亲"]
        filtered = _filter_forbidden_elements(elements)
        assert "警觉" in filtered
        assert "月光" in filtered
        assert "父亲" in filtered

    def test_detect_forbidden_elements_no_hardcoded(self):
        text = "月光洒在窗前，父亲坐在一旁。"
        found = _detect_forbidden_elements(
            text,
            ["月光", "父亲"],
            kinship_terms=frozenset({"父亲"}),
            rhetorical_hints=frozenset({"月光"}),
        )
        assert len(found) == 1
        assert found[0][0] == "月光"

    def test_detect_forbidden_elements_empty_sets(self):
        text = "月光洒在窗前，父亲坐在一旁。"
        found = _detect_forbidden_elements(text, ["月光", "父亲"])
        assert len(found) == 2
