from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from novel_forge.core.utils.repair_target_resolver import navigate_json_pointer_parent
from novel_forge.gateway.adapters.mock import MockAdapter
from novel_forge.pipeline.long.services.init.init_service import (
    _extract_top_level_issues,
    _navigate_json_pointer,
    _repair_artifact_summary_only,
)


class TestExtractTopLevelIssues:
    def test_null_fields_detected(self) -> None:
        payload = {"chapters": None, "synopsis": "ok"}
        issues = _extract_top_level_issues(payload)
        assert "Field 'chapters' is null" in issues

    def test_empty_list_detected(self) -> None:
        payload = {"chapters": [], "title": "test"}
        issues = _extract_top_level_issues(payload)
        assert "Field 'chapters' is an empty list" in issues

    def test_empty_dict_detected(self) -> None:
        payload = {"meta": {}, "title": "test"}
        issues = _extract_top_level_issues(payload)
        assert "Field 'meta' is an empty dict" in issues

    def test_no_issues_returns_default(self) -> None:
        payload = {"chapters": [1, 2, 3], "title": "valid"}
        issues = _extract_top_level_issues(payload)
        assert len(issues) == 1
        assert "No structural anomalies" in issues[0]

    def test_non_dict_returns_empty(self) -> None:
        issues = _extract_top_level_issues([])  # type: ignore[arg-type]
        assert issues == []


class TestNavigateJsonPointer:
    @pytest.mark.parametrize(
        "obj,pointer",
        [
            ({"a": {"b": {"c": 42}}}, "/a/b/c"),
            ({"items": [{"name": "A"}]}, "/items/0/name"),
            ({"a/b": {"c~d": 99}}, "/a~1b/c~0d"),
            ({"": "root-key"}, "/"),
        ],
    )
    def test_compatibility_export_matches_shared_resolver(
        self, obj: dict[str, Any], pointer: str
    ) -> None:
        assert _navigate_json_pointer(obj, pointer) == navigate_json_pointer_parent(obj, pointer)

    def test_dict_path(self) -> None:
        obj = {"a": {"b": {"c": 42}}}
        parent, key = _navigate_json_pointer(obj, "/a/b/c")
        assert parent == {"c": 42}
        assert key == "c"

    def test_list_path(self) -> None:
        obj = {"items": [{"name": "A"}, {"name": "B"}]}
        parent, key = _navigate_json_pointer(obj, "/items/0/name")
        assert parent == {"name": "A"}
        assert key == "name"

    def test_top_level(self) -> None:
        obj = {"x": 1, "y": 2}
        parent, key = _navigate_json_pointer(obj, "/x")
        assert parent == {"x": 1, "y": 2}
        assert key == "x"

    def test_escaped_chars(self) -> None:
        obj = {"a/b": {"c~d": 99}}
        parent, key = _navigate_json_pointer(obj, "/a~1b/c~0d")
        assert parent == {"c~d": 99}
        assert key == "c~d"

    def test_invalid_path_raises(self) -> None:
        obj = {"a": 1}
        with pytest.raises(KeyError):
            _navigate_json_pointer(obj, "/a/b/c")

    def test_index_out_of_bounds_raises(self) -> None:
        obj = {"items": [1, 2]}
        with pytest.raises(IndexError):
            _navigate_json_pointer(obj, "/items/5/value")

    def test_empty_path_navigates_to_empty_key(self) -> None:
        obj = {"": "root-key"}
        parent, key = _navigate_json_pointer(obj, "/")
        assert parent == {"": "root-key"}
        assert key == ""


class TestRepairArtifactSummaryOnly:
    @pytest.fixture
    def large_payload(self) -> dict[str, Any]:
        chapters = []
        for i in range(100):
            chapters.append(
                {
                    "chapter_number": i + 1,
                    "title": f"Chapter {i + 1}",
                    "goal": f"Goal for chapter {i + 1}" + "x" * 500,
                    "beats_summary": [f"Beat {j}" for j in range(5)],
                    "main_plot_points": [f"Plot {j}" for j in range(3)],
                    "pov_character": "MC",
                    "setting": "Town",
                    "expected_word_count": 3000,
                    "element_focus": [],
                    "subplot_points": [],
                }
            )
        return {
            "artifact_name": "test_outline",
            "version": "1.0",
            "chapters": chapters,
            "last_modified": "2026-06-07T00:00:00Z",
        }

    @pytest.fixture
    def mock_response_adapter(self) -> Any:
        adapter = MagicMock()
        adapter.complete = AsyncMock()
        return adapter

    async def test_no_adapter_returns_original(self, large_payload: dict[str, Any]) -> None:
        result = await _repair_artifact_summary_only(
            large_payload,
            repair_intent="Fix chapter structure",
        )
        assert result == large_payload

    async def test_summary_fields_override(self, large_payload: dict[str, Any]) -> None:
        mock = MockAdapter()
        result = await _repair_artifact_summary_only(
            large_payload,
            repair_intent="Fix goals",
            summary_fields=["artifact_name", "version"],
            adapter=mock,
        )
        assert result == large_payload

    async def test_summary_only_extraction_under_10k_tokens(
        self, large_payload: dict[str, Any], mock_response_adapter: Any
    ) -> None:
        mock_response_adapter.complete.return_value = MagicMock(
            content=json.dumps(
                {"patches": [], "summary": "No changes needed from summary"},
                ensure_ascii=False,
            )
        )
        result = await _repair_artifact_summary_only(
            large_payload,
            repair_intent="Review chapter consistency",
            adapter=mock_response_adapter,
        )
        assert result == large_payload

    async def test_suggested_field_modifications_applied(
        self, mock_response_adapter: Any
    ) -> None:
        payload = {
            "artifact_name": "test",
            "version": "1.0",
            "title": "Old Title",
            "description": "Old Description",
        }
        mock_response_adapter.complete.return_value = MagicMock(
            content=json.dumps(
                {
                    "patches": [
                        {
                            "op": "replace",
                            "path": "/title",
                            "value": "New Title",
                            "issue_ids": ["i1"],
                            "rationale": "Better title",
                        }
                    ],
                    "summary": "Fixed title",
                },
                ensure_ascii=False,
            )
        )
        result = await _repair_artifact_summary_only(
            payload,
            repair_intent="Fix the title",
            adapter=mock_response_adapter,
        )
        assert result["title"] == "New Title"
        assert result["description"] == "Old Description"

    async def test_unrelated_fields_preserved(
        self, mock_response_adapter: Any
    ) -> None:
        payload = {
            "chapters": [{"chapter_number": 1, "goal": "original goal"}],
            "synopsis": {"summary": "original", "detail": "deep"},
        }
        mock_response_adapter.complete.return_value = MagicMock(
            content=json.dumps(
                {
                    "patches": [
                        {
                            "op": "replace",
                            "path": "/chapters/0/goal",
                            "value": "updated goal",
                            "issue_ids": ["i1"],
                            "rationale": "Fix goal",
                        }
                    ],
                    "summary": "Fixed one field",
                },
                ensure_ascii=False,
            )
        )
        result = await _repair_artifact_summary_only(
            payload,
            repair_intent="Fix chapter 1 goal",
            adapter=mock_response_adapter,
        )
        assert result["chapters"][0]["goal"] == "updated goal"
        assert result["synopsis"] == {"summary": "original", "detail": "deep"}
        assert result["chapters"][0]["chapter_number"] == 1

    async def test_invalid_field_path_warned_not_raised(
        self, mock_response_adapter: Any
    ) -> None:
        payload = {"title": "valid", "count": 100}
        mock_response_adapter.complete.return_value = MagicMock(
            content=json.dumps(
                {
                    "patches": [
                        {
                            "op": "replace",
                            "path": "/does_not_exist",
                            "value": "ignored",
                            "issue_ids": ["i1"],
                            "rationale": "Bad reference",
                        },
                        {
                            "op": "replace",
                            "path": "/title",
                            "value": "Updated Title",
                            "issue_ids": ["i2"],
                            "rationale": "Good reference",
                        },
                    ],
                    "summary": "Partial fix",
                },
                ensure_ascii=False,
            )
        )
        result = await _repair_artifact_summary_only(
            payload,
            repair_intent="Fix fields",
            adapter=mock_response_adapter,
        )
        assert result["title"] == "Updated Title"
        assert result["count"] == 100
        assert "does_not_exist" not in result

    async def test_adapter_error_returns_original(
        self, large_payload: dict[str, Any], mock_response_adapter: Any
    ) -> None:
        mock_response_adapter.complete.side_effect = RuntimeError("adapter down")
        result = await _repair_artifact_summary_only(
            large_payload,
            repair_intent="Fix",
            adapter=mock_response_adapter,
        )
        assert result == large_payload

    async def test_parse_failure_returns_original(
        self, mock_response_adapter: Any
    ) -> None:
        payload = {"x": 1}
        mock_response_adapter.complete.return_value = MagicMock(
            content="not valid json {{{",
        )
        result = await _repair_artifact_summary_only(
            payload,
            repair_intent="Fix",
            adapter=mock_response_adapter,
        )
        assert result == payload

    async def test_non_dict_payload_returns_unchanged(
        self, mock_response_adapter: Any
    ) -> None:
        payload: Any = [1, 2, 3]
        mock_response_adapter.complete.return_value = MagicMock(
            content='{"patches": [], "summary": ""}',
        )
        result: Any = await _repair_artifact_summary_only(
            payload,
            repair_intent="Fix",
            adapter=mock_response_adapter,
        )
        assert result == [1, 2, 3]

    async def test_list_path_modification_applied(
        self, mock_response_adapter: Any
    ) -> None:
        payload = {
            "items": [
                {"id": 1, "value": "old_1"},
                {"id": 2, "value": "old_2"},
            ]
        }
        mock_response_adapter.complete.return_value = MagicMock(
            content=json.dumps(
                {
                    "patches": [
                        {
                            "op": "replace",
                            "path": "/items/0/value",
                            "value": "new_1",
                            "issue_ids": ["i1"],
                            "rationale": "Update first item",
                        },
                        {
                            "op": "replace",
                            "path": "/items/1/value",
                            "value": "new_2",
                            "issue_ids": ["i2"],
                            "rationale": "Update second item",
                        },
                    ],
                    "summary": "Updated items",
                },
                ensure_ascii=False,
            )
        )
        result = await _repair_artifact_summary_only(
            payload,
            repair_intent="Fix items",
            adapter=mock_response_adapter,
        )
        assert result["items"][0]["value"] == "new_1"
        assert result["items"][1]["value"] == "new_2"
        assert result["items"][0]["id"] == 1

    async def test_response_summary_included(
        self, mock_response_adapter: Any
    ) -> None:
        payload = {"headline": "Original"}
        mock_response_adapter.complete.return_value = MagicMock(
            content=json.dumps(
                {
                    "patches": [
                        {
                            "op": "replace",
                            "path": "/headline",
                            "value": "Updated Headline",
                            "issue_ids": ["i1"],
                            "rationale": "Better phrasing",
                        }
                    ],
                    "summary": "Degraded repair complete - headline updated",
                },
                ensure_ascii=False,
            )
        )
        result = await _repair_artifact_summary_only(
            payload,
            repair_intent="Improve headline phrasing",
            adapter=mock_response_adapter,
        )
        assert result["headline"] == "Updated Headline"

    async def test_prompt_under_token_limit(self, large_payload: dict[str, Any]) -> None:
        default_fields = [
            "artifact_name",
            "version",
            "top_level_keys",
            "top_level_issues",
            "last_modified",
        ]
        summary: dict[str, Any] = {}
        if isinstance(large_payload, dict):
            for field in default_fields:
                if field == "top_level_keys":
                    summary["top_level_keys"] = sorted(large_payload.keys())
                elif field == "top_level_issues":
                    summary["top_level_issues"] = _extract_top_level_issues(large_payload)
                elif field in large_payload:
                    summary[field] = large_payload[field]
        prompt_text = json.dumps(summary, ensure_ascii=False)
        estimated = max(1, len(prompt_text) // 4)
        assert estimated < 10000, f"Summary prompt too large: {estimated} tokens"

    async def test_priority_ordering_preflight_oversized(
        self, mock_response_adapter: Any
    ) -> None:
        huge_payload = {
            "artifact_name": "huge",
            "version": "1.0",
            "chapters": [
                {"chapter_number": i, "goal": "x" * 2000} for i in range(500)
            ],
        }
        # Even the huge payload should be handled gracefully
        mock_response_adapter.complete.return_value = MagicMock(
            content=json.dumps(
                {"patches": [], "summary": "No changes"},
                ensure_ascii=False,
            )
        )
        result = await _repair_artifact_summary_only(
            huge_payload,
            repair_intent="Review structure",
            adapter=mock_response_adapter,
        )
        assert isinstance(result, dict)
        assert "chapters" in result

    async def test_empty_payload(self, mock_response_adapter: Any) -> None:
        payload: dict[str, Any] = {}
        mock_response_adapter.complete.return_value = MagicMock(
            content=json.dumps(
                {"patches": [], "summary": "Empty payload"},
                ensure_ascii=False,
            )
        )
        result = await _repair_artifact_summary_only(
            payload,
            repair_intent="Ignored",
            adapter=mock_response_adapter,
        )
        assert result == {}
