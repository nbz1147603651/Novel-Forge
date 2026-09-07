"""Tests for forbidden_element_defaults.yaml and ForbiddenElementDefaults model.

Covers:
- Loading and parsing of forbidden_element_defaults.yaml
- No duplicate items across all 3 lists
- All items meet minimum length requirement (>= 2 chars)
- All 5 genre templates pass schema validation
- ForbiddenElementDefaults Pydantic model validation
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from novel_forge.core.domain.bible_derived_provider import (
    BibleDerivedProvider,
    ForbiddenElementDefaults,
)

_TEMPLATES_DIR = Path(__file__).parents[2] / "novel_forge" / "templates" / "forbidden_elements"
_DEFAULTS_PATH = Path(__file__).parents[2] / "novel_forge" / "core" / "domain" / "forbidden_element_defaults.yaml"


class TestForbiddenElementDefaults_YAMLLoading:
    def test_load_valid_yaml(self) -> None:
        defaults = ForbiddenElementDefaults.from_yaml(_DEFAULTS_PATH)
        assert len(defaults.rhetorical_imagery_hints) >= 30
        assert len(defaults.kinship_and_address_terms) >= 34
        assert len(defaults.abstract_emotion_keywords) >= 23

    def test_load_returns_frozensets_from_provider(self) -> None:
        provider = BibleDerivedProvider()
        assert isinstance(provider._rhetorical_roots, frozenset)
        assert isinstance(provider._kinship_roots, frozenset)
        assert isinstance(provider._emotion_roots, frozenset)
        assert len(provider._rhetorical_roots) >= 30
        assert len(provider._kinship_roots) >= 34
        assert len(provider._emotion_roots) >= 23


class TestForbiddenElementDefaults_Validation:
    def test_no_duplicates(self) -> None:
        defaults = ForbiddenElementDefaults.from_yaml(_DEFAULTS_PATH)
        all_items = (
            defaults.rhetorical_imagery_hints
            + defaults.kinship_and_address_terms
            + defaults.abstract_emotion_keywords
        )
        assert len(all_items) == len(set(all_items)), "Found duplicate items across lists"

    def test_defaults_all_items_min_length(self) -> None:
        defaults = ForbiddenElementDefaults.from_yaml(_DEFAULTS_PATH)
        for item in (
            defaults.rhetorical_imagery_hints
            + defaults.kinship_and_address_terms
            + defaults.abstract_emotion_keywords
        ):
            assert len(item) >= 2, f"Item too short: {item!r}"

    def test_duplicate_across_lists_raises(self) -> None:
        with pytest.raises(ValueError, match="Duplicate item"):
            ForbiddenElementDefaults._validate_no_cross_list_duplicates(
                ["父亲", "母亲"],
                ["父亲", "弟弟"],
                ["情绪词"],
            )


class TestGenreTemplates_SchemaValidation:
    GENRE_TEMPLATES = [
        "universal_minimal.yaml",
        "chinese_fantasy.yaml",
        "modern_urban.yaml",
        "scifi.yaml",
        "western_fantasy.yaml",
    ]

    def test_all_genre_templates_valid_structure(self) -> None:
        for template_name in self.GENRE_TEMPLATES:
            template_path = _TEMPLATES_DIR / template_name
            assert template_path.exists(), f"Template not found: {template_name}"

            raw = yaml.safe_load(template_path.read_text(encoding="utf-8"))
            assert isinstance(raw, dict), f"{template_name}: expected dict"
            assert "rhetorical_imagery_hints" in raw
            assert "kinship_and_address_terms" in raw
            assert "abstract_emotion_keywords" in raw
            assert isinstance(raw["rhetorical_imagery_hints"], list)
            assert isinstance(raw["kinship_and_address_terms"], list)
            assert isinstance(raw["abstract_emotion_keywords"], list)

    def test_all_genre_templates_load_as_defaults(self) -> None:
        for template_name in self.GENRE_TEMPLATES:
            template_path = _TEMPLATES_DIR / template_name
            raw = yaml.safe_load(template_path.read_text(encoding="utf-8"))

            defaults = ForbiddenElementDefaults(
                rhetorical_imagery_hints=raw.get("rhetorical_imagery_hints", []),
                kinship_and_address_terms=raw.get("kinship_and_address_terms", []),
                abstract_emotion_keywords=raw.get("abstract_emotion_keywords", []),
            )
            assert defaults.rhetorical_imagery_hints is not None
            assert defaults.kinship_and_address_terms is not None
            assert defaults.abstract_emotion_keywords is not None
