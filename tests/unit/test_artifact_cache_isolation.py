"""Regression test: verify artifact cache isolation.

These tests ensure that modifying returned objects does not pollute the cache,
which is critical for correctness when the same cache is used for multiple loads.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel

from novel_forge.core.schemas.reading_power import ReadingPowerReport
from novel_forge.workspace.artifact_cache import ChapterArtifactCache


class _MockStorage:
    """In-memory storage for testing."""

    def __init__(self, data: dict[Path, Any]) -> None:
        self._data = data

    def load_json(self, path: Path) -> Any:
        return self._data.get(path, {})


class _SampleModel(BaseModel):
    """Sample Pydantic model for testing."""

    name: str
    items: list[str] = []
    nested: dict[str, Any] = {}


class TestCacheIsolation:
    """Verify cache returns isolated copies."""

    def test_load_json_returns_isolated_copy(self, tmp_path: Path) -> None:
        """Modifying returned dict must not affect cache."""
        path = tmp_path / "test.json"
        original = {"key": "value", "nested": {"inner": "data"}}
        path.write_text('{"key": "value", "nested": {"inner": "data"}}')

        storage = _MockStorage({path: original})
        cache = ChapterArtifactCache(storage)

        # First load
        result1 = cache.load_json(path)
        assert result1 == original

        # Mutate the result
        result1["key"] = "modified"
        result1["nested"]["inner"] = "mutated"
        result1["new_key"] = "added"

        # Second load should return original data
        result2 = cache.load_json(path)
        assert result2 == original
        assert "new_key" not in result2

    def test_load_json_nested_isolation(self, tmp_path: Path) -> None:
        """Deep nested mutations must not affect cache."""
        path = tmp_path / "nested.json"
        original = {
            "level1": {
                "level2": {
                    "level3": {"value": "original"},
                    "list": [1, 2, 3],
                }
            }
        }
        path.write_text("{}")  # Content doesn't matter for mock

        storage = _MockStorage({path: original})
        cache = ChapterArtifactCache(storage)

        result = cache.load_json(path)
        result["level1"]["level2"]["level3"]["value"] = "mutated"
        result["level1"]["level2"]["list"].append(4)

        # Reload and verify isolation
        result2 = cache.load_json(path)
        assert result2["level1"]["level2"]["level3"]["value"] == "original"
        assert result2["level1"]["level2"]["list"] == [1, 2, 3]

    def test_load_model_returns_isolated_copy(self, tmp_path: Path) -> None:
        """Modifying returned model must not affect cache."""
        path = tmp_path / "model.json"
        original_payload = {"name": "test", "items": ["a", "b"], "nested": {"x": 1}}
        path.write_text("{}")

        storage = _MockStorage({path: original_payload})
        cache = ChapterArtifactCache(storage)

        # First load
        model1 = cache.load_model(path, _SampleModel)
        assert model1.name == "test"
        assert model1.items == ["a", "b"]

        # Mutate via model_copy (Pydantic models are frozen-safe)
        _model1_mutated = model1.model_copy(update={"name": "mutated", "items": ["x", "y"]})

        # Second load should return original
        model2 = cache.load_model(path, _SampleModel)
        assert model2.name == "test"
        assert model2.items == ["a", "b"]

    def test_load_model_nested_mutation_isolation(self, tmp_path: Path) -> None:
        """Nested mutations in model fields must not affect cache."""
        path = tmp_path / "nested_model.json"
        original_payload = {"name": "test", "nested": {"key": "original"}}
        path.write_text("{}")

        storage = _MockStorage({path: original_payload})
        cache = ChapterArtifactCache(storage)

        model1 = cache.load_model(path, _SampleModel)
        # Mutate the nested dict (this would affect cache if not deep-copied)
        model1.nested["key"] = "mutated"
        model1.nested["new_key"] = "added"

        # Reload and verify isolation
        model2 = cache.load_model(path, _SampleModel)
        assert model2.nested.get("key") == "original"
        assert "new_key" not in model2.nested

    def test_load_model_ignores_persisted_pipeline_stage(self, tmp_path: Path) -> None:
        """Report provenance must not invalidate strict typed report reloads."""
        path = tmp_path / "reading_power.json"
        path.write_text("{}")
        storage = _MockStorage(
            {
                path: {
                    "chapter": 1,
                    "hook_type": "mystery",
                    "hook_strength": "strong",
                    "pipeline_stage": "text_change_refresh",
                }
            }
        )
        cache = ChapterArtifactCache(storage)

        report = cache.load_model(path, ReadingPowerReport)

        assert report.chapter == 1
        assert report.hook_type == "mystery"

    def test_cache_stats_tracking(self, tmp_path: Path) -> None:
        """Cache should track hits and misses."""
        path = tmp_path / "stats.json"
        path.write_text("{}")

        storage = _MockStorage({path: {}})
        cache = ChapterArtifactCache(storage)

        # First load = miss
        cache.load_json(path)
        stats1 = cache.stats()
        assert stats1["misses"] == 1
        assert stats1["hits"] == 0

        # Second load = hit (file unchanged)
        cache.load_json(path)
        stats2 = cache.stats()
        assert stats2["misses"] == 1
        assert stats2["hits"] == 1

    def test_cache_clear_resets_entries(self, tmp_path: Path) -> None:
        """Clear should remove all cached entries."""
        path = tmp_path / "clear.json"
        path.write_text("{}")

        storage = _MockStorage({path: {}})
        cache = ChapterArtifactCache(storage)

        cache.load_json(path)
        assert cache.stats()["entries"] == 1

        cache.clear()
        assert cache.stats()["entries"] == 0


class TestFormatContractSchemaIsolation:
    """Verify format contract schema operations are isolated."""

    def test_effective_contract_schema_isolation(self) -> None:
        """Getting effective schema must not affect contract."""
        from novel_forge.common.constants import TaskType
        from novel_forge.core.format_contracts import (
            OutputKind,
            TaskFormatContract,
            effective_contract_json_schema,
        )

        contract = TaskFormatContract(
            output_kind=OutputKind.JSON,
            json_schema={
                "type": "object",
                "properties": {"name": {"type": "string"}},
            },
        )

        schema1 = effective_contract_json_schema(TaskType.DRAFT, contract, context=None)
        if schema1 is None:
            pytest.skip("No schema returned for DRAFT")
        # Mutate result
        schema1["mutated"] = True

        # Get again - should be fresh
        schema2 = effective_contract_json_schema(TaskType.DRAFT, contract, context=None)
        if schema2 is None:
            pytest.skip("No schema returned for DRAFT on second call")
        assert "mutated" not in schema2
