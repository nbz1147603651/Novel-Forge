"""Tests for ForbiddenElementRegistry.reinit_project_seeds()."""

from __future__ import annotations

from pathlib import Path

import yaml

from novel_forge.core.domain.forbidden_element_registry import ForbiddenElementRegistry


class TestReinitProjectSeeds:
    """Tests for reinit_project_seeds() method."""

    def test_reinit_overwrites_existing_file(self, tmp_path: Path) -> None:
        """reinit_project_seeds should overwrite an existing seeds file."""
        seeds_path = tmp_path / "config" / "forbidden_element_seeds.yaml"
        seeds_path.parent.mkdir(parents=True)
        original_content = {"rhetorical_imagery_hints": ["old_content"]}
        seeds_path.write_text(yaml.safe_dump(original_content), encoding="utf-8")

        ForbiddenElementRegistry.reinit_project_seeds(tmp_path, "universal_minimal")

        loaded = yaml.safe_load(seeds_path.read_text(encoding="utf-8"))
        assert loaded != original_content
        assert "月光" in str(loaded.get("rhetorical_imagery_hints", []))

    def test_reinit_creates_file_if_missing(self, tmp_path: Path) -> None:
        """reinit_project_seeds should create seeds file if it doesn't exist."""
        result = ForbiddenElementRegistry.reinit_project_seeds(tmp_path, "universal_minimal")

        assert result.exists()
        loaded = yaml.safe_load(result.read_text(encoding="utf-8"))
        assert "rhetorical_imagery_hints" in loaded

    def test_reinit_clears_merged_cache(self, tmp_path: Path) -> None:
        """reinit_project_seeds should clear cached merged data in existing registry."""
        registry = ForbiddenElementRegistry(tmp_path)
        registry.merge_sources()

        assert registry._merged_cache is not None

        ForbiddenElementRegistry.reinit_project_seeds(tmp_path, "universal_minimal")

        assert registry._merged_cache is None

    def test_reinit_returns_seeds_path(self, tmp_path: Path) -> None:
        """reinit_project_seeds should return the seeds path."""
        result = ForbiddenElementRegistry.reinit_project_seeds(tmp_path, "universal_minimal")

        expected = tmp_path / "config" / "forbidden_element_seeds.yaml"
        assert result == expected

    def test_reinit_falls_back_to_universal_minimal(self, tmp_path: Path) -> None:
        """reinit_project_seeds should fallback to universal_minimal for unknown genre."""
        ForbiddenElementRegistry.reinit_project_seeds(tmp_path, "nonexistent_genre")

        seeds_path = tmp_path / "config" / "forbidden_element_seeds.yaml"
        loaded = yaml.safe_load(seeds_path.read_text(encoding="utf-8"))
        assert "rhetorical_imagery_hints" in loaded
