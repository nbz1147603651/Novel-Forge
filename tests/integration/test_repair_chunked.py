"""Integration & unit tests for chunked repair of init artifacts.

Covers T20: _repair_artifact_chunked, _split_payload_by_chapter,
_merge_repaired_chunks, _detect_artifact_type, _call_repair_for_chunk.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Awaitable, Callable
from unittest.mock import AsyncMock

import pytest

from novel_forge.core.constants import TaskType
from novel_forge.gateway.types import ModelRequest, ModelResponse
from novel_forge.pipeline.long.services.init.init_service import (
    _call_repair_for_chunk,
    _detect_artifact_type,
    _merge_repaired_chunks,
    _repair_artifact_chunked,
    _split_payload_by_chapter,
)
from novel_forge.pipeline.token_budget import structured_json_output_floor

# ── Test data builders ────────────────────────────────────────────


def _outline_payload(num_chapters: int = 3) -> dict[str, Any]:
    return {
        "artifact_name": "story_outline",
        "version": "1.0",
        "total_chapters": num_chapters,
        "chapters": [
            {
                "chapter_number": i + 1,
                "title": f"Chapter {i + 1}",
                "synopsis": f"Synopsis of chapter {i + 1}",
                "goal": f"Goal {i + 1}",
                "beats_summary": [f"Beat {i + 1}.1", f"Beat {i + 1}.2"],
            }
            for i in range(num_chapters)
        ],
    }


def _chapter_contracts_payload(num_contracts: int = 3) -> dict[str, Any]:
    return {
        "chapter_contracts": [
            {
                "chapter_id": i + 1,
                "narrative_hook": f"Hook {i + 1}",
                "closing_beat": f"Close {i + 1}",
                "scene_count": 3,
            }
            for i in range(num_contracts)
        ],
        "coverage": {"total": num_contracts, "generated": num_contracts},
    }


def _blueprint_payload() -> dict[str, Any]:
    return {
        "artifact_name": "narrative_blueprint",
        "version": "1.0",
        "synopsis": "A synopsis",
        "key_turning_points": [],
        "narrative_phases": [],
    }


# ── MiniMockAdapter (records calls + returns controlled responses) ─


class _MiniMockAdapter:
    """Mock adapter that records every call and returns a controlled response."""

    def __init__(self, response_content: str = '{"patches": [], "summary": "ok"}'):
        self.response_content = response_content
        self.calls: list[ModelRequest] = []
        self.response = ModelResponse(
            content=response_content,
            model_id="mock-model",
            prompt_tokens=100,
            completion_tokens=10,
            total_tokens=110,
            latency_ms=1.0,
            cost_usd=0.0,
        )

    async def complete(self, request: ModelRequest) -> ModelResponse:
        self.calls.append(request)
        return self.response


# ── Unit tests: _detect_artifact_type ─────────────────────────────


class TestDetectArtifactType:
    def test_outline_detected(self):
        payload = _outline_payload(3)
        assert _detect_artifact_type(payload) == "outline"

    def test_chapter_contracts_detected(self):
        payload = _chapter_contracts_payload(3)
        assert _detect_artifact_type(payload) == "chapter_contracts"

    def test_blueprint_detected(self):
        payload = _blueprint_payload()
        assert _detect_artifact_type(payload) == "blueprint"

    def test_empty_payload_is_blueprint(self):
        assert _detect_artifact_type({}) == "blueprint"

    def test_non_dict_is_blueprint(self):
        assert _detect_artifact_type([]) == "blueprint"

    def test_chapters_without_chapter_number_is_blueprint(self):
        payload = {"chapters": [{"not_chapter_number": 1}]}
        assert _detect_artifact_type(payload) == "blueprint"


# ── Unit tests: _split_payload_by_chapter ─────────────────────────


class TestSplitPayloadByChapter:
    def test_outline_one_chunk_per_chapter(self):
        payload = _outline_payload(3)
        chunks = _split_payload_by_chapter(payload, "outline", max_chunks=4)
        assert len(chunks) == 3
        for i, chunk in enumerate(chunks):
            assert len(chunk["chapters"]) == 1
            assert chunk["chapters"][0]["chapter_number"] == i + 1
            # Top-level metadata preserved
            assert chunk["artifact_name"] == "story_outline"
            assert chunk["version"] == "1.0"

    def test_outline_max_chunks_groups_chapters(self):
        payload = _outline_payload(10)
        chunks = _split_payload_by_chapter(payload, "outline", max_chunks=4)
        assert len(chunks) == 4
        total_chapters = sum(len(c["chapters"]) for c in chunks)
        assert total_chapters == 10

    def test_outline_single_chapter(self):
        payload = _outline_payload(1)
        chunks = _split_payload_by_chapter(payload, "outline", max_chunks=4)
        assert len(chunks) == 1
        assert chunks[0]["chapters"][0]["chapter_number"] == 1

    def test_chapter_contracts_one_chunk_per_contract(self):
        payload = _chapter_contracts_payload(3)
        chunks = _split_payload_by_chapter(payload, "chapter_contracts", max_chunks=4)
        assert len(chunks) == 3
        for i, chunk in enumerate(chunks):
            assert len(chunk["chapter_contracts"]) == 1
            assert chunk["chapter_contracts"][0]["chapter_id"] == i + 1
            # Metadata preserved
            assert chunk["coverage"]["total"] == 3

    def test_chapter_contracts_max_chunks_groups(self):
        payload = _chapter_contracts_payload(8)
        chunks = _split_payload_by_chapter(payload, "chapter_contracts", max_chunks=3)
        assert len(chunks) == 3
        total_contracts = sum(len(c["chapter_contracts"]) for c in chunks)
        assert total_contracts == 8

    def test_blueprint_not_split(self):
        payload = _blueprint_payload()
        chunks = _split_payload_by_chapter(payload, "blueprint", max_chunks=4)
        assert len(chunks) == 1
        assert chunks[0] is payload  # same reference (no copy for non-chunkable)

    def test_empty_chapters_returns_original(self):
        payload: dict[str, Any] = {"chapters": [], "meta": "data"}
        chunks = _split_payload_by_chapter(payload, "outline", max_chunks=4)
        assert len(chunks) == 1
        assert chunks[0] is payload

    def test_missing_chapters_key_returns_original(self):
        payload = _blueprint_payload()
        chunks = _split_payload_by_chapter(payload, "outline", max_chunks=4)
        assert len(chunks) == 1

    def test_max_chunks_limit_enforced_exact(self):
        """22 chapters, max_chunks=4 → 4 chunks: 6+6+5+5."""
        payload = _outline_payload(22)
        chunks = _split_payload_by_chapter(payload, "outline", max_chunks=4)
        assert len(chunks) == 4
        sizes = [len(c["chapters"]) for c in chunks]
        assert sum(sizes) == 22
        assert sizes == [6, 6, 5, 5]

    def test_chapter_contracts_grouping_distribution(self):
        """7 contracts, max_chunks=3 → 3 chunks: 3+2+2."""
        payload = _chapter_contracts_payload(7)
        chunks = _split_payload_by_chapter(payload, "chapter_contracts", max_chunks=3)
        assert len(chunks) == 3
        sizes = [len(c["chapter_contracts"]) for c in chunks]
        assert sizes == [3, 2, 2]


# ── Unit tests: _merge_repaired_chunks ────────────────────────────


class TestMergeRepairedChunks:
    def test_outline_merge_basic(self):
        original = _outline_payload(3)
        # Simulate chunk repair: modify chapter 1 and 2
        chunk0 = _outline_payload(3)
        chunk0["chapters"] = [
            {
                "chapter_number": 1,
                "title": "Fixed Chapter 1",
                "synopsis": "Fixed synopsis",
                "goal": "Fixed goal",
                "beats_summary": ["Fixed Beat"],
            }
        ]
        chunk1 = _outline_payload(3)
        chunk1["chapters"] = [
            {
                "chapter_number": 2,
                "title": "Fixed Chapter 2",
                "synopsis": "Fixed synopsis 2",
                "goal": "Fixed goal 2",
                "beats_summary": ["Fixed Beat 2"],
            }
        ]

        merged, warnings = _merge_repaired_chunks(original, [chunk0, chunk1], "outline")
        assert len(warnings) == 0
        assert merged["chapters"][0]["title"] == "Fixed Chapter 1"
        assert merged["chapters"][1]["title"] == "Fixed Chapter 2"
        assert merged["chapters"][2]["title"] == "Chapter 3"  # unchanged

    def test_chapter_contracts_merge_basic(self):
        original = _chapter_contracts_payload(3)
        chunk0 = _chapter_contracts_payload(3)
        chunk0["chapter_contracts"] = [
            {"chapter_id": 1, "narrative_hook": "Fixed Hook 1", "closing_beat": "Close 1", "scene_count": 4}
        ]
        chunk1 = _chapter_contracts_payload(3)
        chunk1["chapter_contracts"] = [
            {"chapter_id": 3, "narrative_hook": "Fixed Hook 3", "closing_beat": "Close 3", "scene_count": 5}
        ]

        merged, warnings = _merge_repaired_chunks(original, [chunk0, chunk1], "chapter_contracts")
        assert len(warnings) == 0
        assert merged["chapter_contracts"][0]["narrative_hook"] == "Fixed Hook 1"
        assert merged["chapter_contracts"][0]["scene_count"] == 4
        assert merged["chapter_contracts"][1]["narrative_hook"] == "Hook 2"  # unchanged
        assert merged["chapter_contracts"][2]["narrative_hook"] == "Fixed Hook 3"
        assert merged["chapter_contracts"][2]["scene_count"] == 5

    def test_disjoint_field_conflict_warning(self):
        """Both chunk 0 and chunk 1 modify chapter 1 → warning, first wins."""
        original = _outline_payload(2)
        chunk0 = _outline_payload(2)
        chunk0["chapters"] = [
            {"chapter_number": 1, "title": "From Chunk 0", "synopsis": "S0", "goal": "G0", "beats_summary": ["B0"]}
        ]
        chunk1 = _outline_payload(2)
        chunk1["chapters"] = [
            {"chapter_number": 1, "title": "From Chunk 1", "synopsis": "S1", "goal": "G1", "beats_summary": ["B1"]}
        ]

        merged, warnings = _merge_repaired_chunks(original, [chunk0, chunk1], "outline")
        assert len(warnings) == 1
        assert "chunked_repair_conflict" in warnings[0]
        assert "chapter=1" in warnings[0]
        assert "retaining_first" in warnings[0]
        # First chunk wins
        assert merged["chapters"][0]["title"] == "From Chunk 0"
        assert merged["chapters"][0]["synopsis"] == "S0"

    def test_blueprint_noop_merge(self):
        original = _blueprint_payload()
        merged, warnings = _merge_repaired_chunks(original, [], "blueprint")
        assert len(warnings) == 0
        assert merged == original

    def test_empty_chapter_list(self):
        original: dict[str, Any] = {"chapters": [], "meta": "x"}
        merged, warnings = _merge_repaired_chunks(original, [], "outline")
        assert len(warnings) == 0
        assert merged == original

    def test_merge_preserves_non_chapter_fields(self):
        """Top-level fields other than chapters are preserved from original."""
        original = _outline_payload(2)
        chunk0 = _outline_payload(2)
        chunk0["chapters"] = [
            {"chapter_number": 1, "title": "Fixed", "synopsis": "S", "goal": "G", "beats_summary": ["B"]}
        ]

        merged, _ = _merge_repaired_chunks(original, [chunk0], "outline")
        assert merged["artifact_name"] == "story_outline"
        assert merged["version"] == "1.0"
        assert merged["total_chapters"] == 2


# ── Unit tests: _call_repair_for_chunk ────────────────────────────


class TestCallRepairForChunk:
    @pytest.mark.asyncio
    async def test_basic_call_records_and_returns(self):
        adapter = _MiniMockAdapter()
        payload = _outline_payload(1)
        result = await _call_repair_for_chunk(payload, "fix stuff", adapter, chunk_index=0)
        assert len(adapter.calls) == 1
        call = adapter.calls[0]
        assert call.task_type == TaskType.REPAIR_INIT_ARTIFACT_PATCH
        assert call.max_tokens == max(
            2048,
            structured_json_output_floor(TaskType.REPAIR_INIT_ARTIFACT_PATCH),
        )
        assert call.temperature == 0.15
        assert isinstance(result, dict)
        assert "chapters" in result

    @pytest.mark.asyncio
    async def test_adapter_error_returns_original(self):
        adapter = _MiniMockAdapter()
        adapter.complete = AsyncMock(side_effect=RuntimeError("adapter boom"))
        payload = _outline_payload(1)
        result = await _call_repair_for_chunk(payload, "fix", adapter, chunk_index=0)
        assert result == payload  # unchanged on error

    @pytest.mark.asyncio
    async def test_parse_failure_returns_original(self):
        adapter = _MiniMockAdapter(response_content="not valid json {{{")
        payload = _outline_payload(1)
        result = await _call_repair_for_chunk(payload, "fix", adapter, chunk_index=0)
        assert result == payload

    @pytest.mark.asyncio
    async def test_applies_patches_to_chunk(self):
        adapter = _MiniMockAdapter(
            response_content=json.dumps(
                {
                    "patches": [
                        {"op": "replace", "path": "/chapters/0/title", "value": "Repaired Title"}
                    ],
                    "summary": "patched",
                }
            )
        )
        payload = _outline_payload(1)
        result = await _call_repair_for_chunk(payload, "fix", adapter, chunk_index=0)
        assert result["chapters"][0]["title"] == "Repaired Title"

    @pytest.mark.asyncio
    async def test_invalid_patch_skipped_gracefully(self):
        adapter = _MiniMockAdapter(
            response_content=json.dumps(
                {
                    "patches": [
                        {"op": "replace", "path": "/nonexistent/field", "value": "ignored"}
                    ],
                    "summary": "patched",
                }
            )
        )
        payload = _outline_payload(1)
        result = await _call_repair_for_chunk(payload, "fix", adapter, chunk_index=0)
        # Should return chunk unchanged since path doesn't exist
        assert result["chapters"][0]["title"] == "Chapter 1"  # original


# ── Integration tests: _repair_artifact_chunked ───────────────────


class TestRepairArtifactChunked:
    @pytest.mark.asyncio
    async def test_outline_full_flow_three_chapters(self):
        """3 chapters with max_chunks=4 → 3 chunks, each independently repaired."""
        adapter = _MiniMockAdapter(
            response_content=json.dumps({"patches": [], "summary": "ok"})
        )
        payload = _outline_payload(3)

        result = await _repair_artifact_chunked(
            payload,
            chunk_by="chapter",
            repair_intent="fix outline issues",
            adapter=adapter,
            max_chunks_per_repair=4,
        )

        assert result["chunks_processed"] == 3
        assert "repaired" in result
        assert len(adapter.calls) == 3
        # Each call should have independent chunk data
        for call in adapter.calls:
            prompt_text = call.messages[0]["content"]
            assert "chapter" in prompt_text.lower()

    @pytest.mark.asyncio
    async def test_outline_max_chunks_limits_calls(self):
        """10 chapters with max_chunks=3 → 3 chunks, 3 adapter calls."""
        adapter = _MiniMockAdapter(
            response_content=json.dumps({"patches": [], "summary": "ok"})
        )
        payload = _outline_payload(10)

        result = await _repair_artifact_chunked(
            payload,
            repair_intent="fix outline",
            adapter=adapter,
            max_chunks_per_repair=3,
        )

        assert result["chunks_processed"] == 3
        assert len(adapter.calls) == 3

    @pytest.mark.asyncio
    async def test_chapter_contracts_full_flow(self):
        """5 contracts with max_chunks=4 → 4 chunks (grouped to respect limit)."""
        adapter = _MiniMockAdapter(
            response_content=json.dumps({"patches": [], "summary": "ok"})
        )
        payload = _chapter_contracts_payload(5)

        result = await _repair_artifact_chunked(
            payload,
            repair_intent="fix contracts",
            adapter=adapter,
            max_chunks_per_repair=4,
        )

        assert result["chunks_processed"] == 4
        assert len(adapter.calls) == 4

    @pytest.mark.asyncio
    async def test_blueprint_routes_to_noop(self):
        """Blueprint has no per-chapter structure → returns unchanged with note."""
        adapter = _MiniMockAdapter()
        payload = _blueprint_payload()

        result = await _repair_artifact_chunked(
            payload,
            repair_intent="fix blueprint",
            adapter=adapter,
        )

        assert result["chunks_processed"] == 0
        assert result["note"] == "blueprint_not_chunked"
        assert result["repaired"] == payload
        assert len(adapter.calls) == 0  # no adapter call

    @pytest.mark.asyncio
    async def test_no_adapter_returns_unchanged(self):
        payload = _outline_payload(5)
        result = await _repair_artifact_chunked(
            payload,
            repair_intent="fix",
            adapter=None,
        )
        assert result["chunks_processed"] == 0
        assert result["repaired"] == payload

    @pytest.mark.asyncio
    async def test_single_chapter_no_chunking(self):
        """Single chapter → 1 chunk, 1 adapter call, no warn of conflict."""
        adapter = _MiniMockAdapter(
            response_content=json.dumps(
                {
                    "patches": [
                        {"op": "replace", "path": "/chapters/0/title", "value": "Repaired Solo"}
                    ],
                    "summary": "ok",
                }
            )
        )
        payload = _outline_payload(1)

        result = await _repair_artifact_chunked(
            payload,
            repair_intent="fix title",
            adapter=adapter,
        )

        assert result["chunks_processed"] == 1
        assert result["repaired"]["chapters"][0]["title"] == "Repaired Solo"
        assert len(adapter.calls) == 1

    @pytest.mark.asyncio
    async def test_chapter_contracts_with_real_changes(self):
        """Verify chunked repair correctly applies and merges chapter-level changes."""
        def _response_for_chapter(chapter_id: int) -> str:
            return json.dumps(
                {
                    "patches": [
                        {
                            "op": "replace",
                            "path": "/chapter_contracts/0/narrative_hook",
                            "value": f"Repaired Hook {chapter_id}",
                        }
                    ],
                    "summary": f"Fixed chapter {chapter_id}",
                }
            )

        adapter = _MiniMockAdapter()
        call_responses: list[str] = []
        original_complete: Callable[[ModelRequest], Awaitable[ModelResponse]] = adapter.complete

        async def _tracking_complete(request: ModelRequest) -> ModelResponse:
            call_responses.append("called")
            idx = len(call_responses) - 1
            chapter_id = idx + 1
            adapter.response_content = _response_for_chapter(chapter_id)
            adapter.response = ModelResponse(
                content=adapter.response_content,
                model_id="mock",
                prompt_tokens=100,
                completion_tokens=10,
                total_tokens=110,
                latency_ms=1.0,
                cost_usd=0.0,
            )
            return await original_complete(request)

        adapter.complete = _tracking_complete

        payload = _chapter_contracts_payload(3)
        result = await _repair_artifact_chunked(
            payload,
            repair_intent="fix hooks",
            adapter=adapter,
            max_chunks_per_repair=4,
        )

        assert result["chunks_processed"] == 3
        assert len(call_responses) == 3
        repaired = result["repaired"]
        assert repaired["chapter_contracts"][0]["narrative_hook"] == "Repaired Hook 1"
        assert repaired["chapter_contracts"][1]["narrative_hook"] == "Repaired Hook 2"
        assert repaired["chapter_contracts"][2]["narrative_hook"] == "Repaired Hook 3"

    @pytest.mark.asyncio
    async def test_merge_conflict_detected_integration(self):
        """If two chunks both touch the same chapter, warning is logged."""
        # Simulate: chapter 1 appears in both chunk 0 and chunk 1 responses
        # This requires overriding the merge behavior, which is tested in unit.
        # Here we just verify the integration calls go through fine.
        adapter = _MiniMockAdapter(
            response_content=json.dumps({"patches": [], "summary": "ok"})
        )
        payload = _outline_payload(3)
        result = await _repair_artifact_chunked(
            payload,
            repair_intent="fix",
            adapter=adapter,
            max_chunks_per_repair=4,
        )
        assert result["chunks_processed"] == 3
        assert "merge_warnings" in result


# ── Parallel chunked repair (gather): concurrency + order preservation ──


class TestRepairArtifactChunkedParallel:
    @pytest.mark.asyncio
    async def test_chunked_repair_runs_chunks_concurrently(self):
        """Multiple chunks overlap in flight instead of running serially."""
        active = 0
        peak = 0

        class _ConcurrentAdapter(_MiniMockAdapter):
            async def complete(self, request: ModelRequest) -> ModelResponse:
                nonlocal active, peak
                active += 1
                peak = max(peak, active)
                await asyncio.sleep(0.01)
                active -= 1
                return self.response

        adapter = _ConcurrentAdapter()
        payload = _outline_payload(4)
        result = await _repair_artifact_chunked(
            payload,
            repair_intent="fix",
            adapter=adapter,
            max_chunks_per_repair=4,
        )

        assert result["chunks_processed"] == 4
        assert peak >= 2  # at least two chunk repairs were in flight together

    @pytest.mark.asyncio
    async def test_chunked_repair_merges_in_chunk_order_under_out_of_order_completion(
        self,
    ):
        """A slow first chunk must not reorder the merged result."""

        def _response_for_chunk_index(idx: int) -> str:
            # Each split chunk carries exactly one chapter at /chapters/0; the
            # call index only selects which title value the mock returns.
            return json.dumps(
                {
                    "patches": [
                        {
                            "op": "replace",
                            "path": "/chapters/0/title",
                            "value": f"Repaired {idx}",
                        }
                    ],
                    "summary": "ok",
                }
            )

        class _SlowFirstAdapter(_MiniMockAdapter):
            def __init__(self) -> None:
                super().__init__()
                self._call_index = 0

            async def complete(self, request: ModelRequest) -> ModelResponse:
                idx = self._call_index
                self._call_index += 1
                if idx == 0:
                    await asyncio.sleep(0.03)
                self.response_content = _response_for_chunk_index(idx)
                self.response = ModelResponse(
                    content=self.response_content,
                    model_id="mock",
                    prompt_tokens=100,
                    completion_tokens=10,
                    total_tokens=110,
                    latency_ms=1.0,
                    cost_usd=0.0,
                )
                return self.response

        adapter = _SlowFirstAdapter()
        payload = _outline_payload(3)
        result = await _repair_artifact_chunked(
            payload,
            repair_intent="fix",
            adapter=adapter,
            max_chunks_per_repair=4,
        )

        repaired = result["repaired"]
        # gather preserves argument order: chunk 0's (slow) result still lands
        # in slot 0 and patches chapter 1, not a later chapter.
        assert repaired["chapters"][0]["title"] == "Repaired 0"
        assert repaired["chapters"][1]["title"] == "Repaired 1"
        assert repaired["chapters"][2]["title"] == "Repaired 2"
