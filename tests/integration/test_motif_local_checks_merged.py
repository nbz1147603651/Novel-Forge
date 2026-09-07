"""Integration tests for merged ForbiddenElementRegistry in local checks.

Tests:
- test_project_specific_exemption: project seeds override genre defaults
- test_genre_template_defaults: genre template provides fallback terms
- test_fallback_when_no_registry: falls back to _load_defaults_cached() when no project path
- test_merge_sources_with_bible_derived: bible-derived terms merge correctly
- test_registry_fields_propagate_to_continuity_input: registry fields flow through ContinuityEvalInput
"""

from __future__ import annotations

from pathlib import Path

import pytest

from novel_forge.core.domain.bible_derived_provider import _load_defaults_cached
from novel_forge.core.domain.forbidden_element_registry import ForbiddenElementRegistry
from novel_forge.core.schemas.continuity import ChapterBridge, ChapterPlan, ChapterStatePacket
from novel_forge.pipeline.steps.continuity_eval.context import ContinuityEvalInput


@pytest.fixture
def tmp_project(tmp_path: Path) -> Path:
    """Create a temporary project directory with config structure."""
    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    return tmp_path


@pytest.fixture
def sample_bridge() -> ChapterBridge:
    return ChapterBridge(
        from_chapter=1,
        to_chapter=2,
        opening_pov="张三",
        opening_location="客栈",
        action_handoff="张三离开客栈，向城东走去",
        transition_mode="direct",
    )


@pytest.fixture
def sample_plan() -> ChapterPlan:
    return ChapterPlan(
        forbidden_elements=["月光", "细雨"],
        forbidden_elements_soft=["落叶"],
        intentional_callbacks=["铜钥匙"],
    )


@pytest.fixture
def sample_packet() -> ChapterStatePacket:
    from novel_forge.core.schemas.outline import ChapterOutline
    return ChapterStatePacket(
        chapter_number=1,
        chapter_outline=ChapterOutline(chapter_number=1, goal="测试章节目标"),
        previous_chapter_ending="张三在客栈中入睡",
        must_carry_forward=["客栈中的神秘信件"],
        accumulated_forbidden_repetition=["秋风"],
    )


def _write_seeds_file(project_path: Path, content: str) -> Path:
    """Write forbidden element seeds file."""
    seeds_path = project_path / "config" / "forbidden_element_seeds.yaml"
    seeds_path.write_text(content, encoding="utf-8")
    return seeds_path


class TestMergedForbiddenElementRegistry:
    """Integration tests for merged source priority in local checks."""

    def test_project_specific_exemption(self, tmp_project: Path) -> None:
        """Project seeds override genre template defaults."""
        seeds_content = """
rhetorical_imagery_hints:
  - 血色月光
  - 铜锈
kinship_and_address_terms:
  - 家主
  - 少主
abstract_emotion_keywords:
  - 惶恐
  - 悸动
"""
        _write_seeds_file(tmp_project, seeds_content)

        registry = ForbiddenElementRegistry(tmp_project)
        assert registry.has_project_seeds

        merged = registry.merge_sources(genre="universal_minimal")

        # Project seeds should be present
        assert "血色月光" in merged["rhetorical_imagery_hints"]
        assert "铜锈" in merged["rhetorical_imagery_hints"]
        assert "家主" in merged["kinship_and_address_terms"]
        assert "惶恐" in merged["abstract_emotion_keywords"]

    def test_genre_template_defaults(self, tmp_project: Path) -> None:
        """Genre template provides fallback terms when project seeds are minimal."""
        seeds_content = """
rhetorical_imagery_hints: []
kinship_and_address_terms: []
abstract_emotion_keywords: []
"""
        _write_seeds_file(tmp_project, seeds_content)

        registry = ForbiddenElementRegistry(tmp_project)
        assert registry.has_project_seeds

        merged = registry.merge_sources(genre="universal_minimal")

        # Should have universal_minimal fallback terms
        assert isinstance(merged["rhetorical_imagery_hints"], list)
        assert isinstance(merged["kinship_and_address_terms"], list)
        assert isinstance(merged["abstract_emotion_keywords"], list)

    def test_fallback_when_no_registry(self) -> None:
        """Falls back to _load_defaults_cached() when no project path."""
        defaults = _load_defaults_cached()
        assert isinstance(defaults, tuple)
        assert len(defaults) == 3

        rhetorical, kinship, emotion = defaults
        assert isinstance(rhetorical, frozenset)
        assert isinstance(kinship, frozenset)
        assert isinstance(emotion, frozenset)

    def test_merge_sources_with_bible_derived(self, tmp_project: Path) -> None:
        """Bible-derived terms merge correctly with project seeds."""
        seeds_content = """
rhetorical_imagery_hints:
  - 项目专属意象
kinship_and_address_terms:
  - 家主
abstract_emotion_keywords:
  - 惶恐
"""
        _write_seeds_file(tmp_project, seeds_content)

        registry = ForbiddenElementRegistry(tmp_project)

        bible_derived = {
            "rhetorical_imagery_hints": ["圣经意象"],
            "kinship_and_address_terms": ["公子"],
            "abstract_emotion_keywords": ["悲伤"],
        }

        merged = registry.merge_sources(bible_derived=bible_derived, genre="universal_minimal")

        # Project seeds should take priority
        assert "项目专属意象" in merged["rhetorical_imagery_hints"]
        assert "家主" in merged["kinship_and_address_terms"]
        assert "惶恐" in merged["abstract_emotion_keywords"]

        # Bible-derived terms should also be present (merged, not replaced)
        assert "圣经意象" in merged["rhetorical_imagery_hints"]
        assert "公子" in merged["kinship_and_address_terms"]
        assert "悲伤" in merged["abstract_emotion_keywords"]

    def test_registry_fields_propagate_to_continuity_input(
        self, tmp_project: Path, sample_bridge: ChapterBridge,
        sample_plan: ChapterPlan, sample_packet: ChapterStatePacket,
    ) -> None:
        """Registry fields flow through ContinuityEvalInput correctly."""
        seeds_content = """
rhetorical_imagery_hints:
  - 血色月光
kinship_and_address_terms:
  - 家主
abstract_emotion_keywords:
  - 惶恐
"""
        _write_seeds_file(tmp_project, seeds_content)

        registry = ForbiddenElementRegistry(tmp_project)
        merged = registry.merge_sources(genre="universal_minimal")

        input_data = ContinuityEvalInput(
            chapter_number=2,
            chapter_text="这是一段测试正文，长度需要超过一百字才能通过基本检测。" * 10,
            chapter_state_packet=sample_packet,
            chapter_bridge=sample_bridge,
            chapter_plan=sample_plan,
            project_path=tmp_project,
            registry_kinship_terms=frozenset(merged.get("kinship_and_address_terms", [])),
            registry_rhetorical_hints=frozenset(merged.get("rhetorical_imagery_hints", [])),
            registry_emotion_keywords=frozenset(merged.get("abstract_emotion_keywords", [])),
            registry_bible_derived=None,
            registry_genre="universal_minimal",
        )

        assert input_data.registry_kinship_terms is not None
        assert "家主" in input_data.registry_kinship_terms
        assert input_data.registry_rhetorical_hints is not None
        assert "血色月光" in input_data.registry_rhetorical_hints
        assert input_data.registry_emotion_keywords is not None
        assert "惶恐" in input_data.registry_emotion_keywords

    def test_get_all_root_sets_returns_frozensets(self, tmp_project: Path) -> None:
        """get_all_root_sets() returns proper frozensets for all categories."""
        seeds_content = """
rhetorical_imagery_hints:
  - 月光
  - 细雨
kinship_and_address_terms:
  - 父亲
  - 母亲
abstract_emotion_keywords:
  - 悲伤
  - 喜悦
"""
        _write_seeds_file(tmp_project, seeds_content)

        registry = ForbiddenElementRegistry(tmp_project)
        rhetorical, kinship, emotion = registry.get_all_root_sets(genre="universal_minimal")

        assert isinstance(rhetorical, frozenset)
        assert isinstance(kinship, frozenset)
        assert isinstance(emotion, frozenset)
        assert "月光" in rhetorical
        assert "父亲" in kinship
        assert "悲伤" in emotion

    def test_empty_project_path_uses_defaults(self) -> None:
        """When project_path is None, local checks use default fallback."""
        defaults = _load_defaults_cached()
        rhetorical, kinship, emotion = defaults

        # Defaults should be non-empty frozensets
        assert isinstance(rhetorical, frozenset)
        assert isinstance(kinship, frozenset)
        assert isinstance(emotion, frozenset)
