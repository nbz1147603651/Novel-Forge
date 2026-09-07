"""Tests for loading historical stream data from events.jsonl."""

from __future__ import annotations

import json
from pathlib import Path

from novel_forge.desktop.stream_history import (
    load_stream_history,
)


def _write_events_jsonl(path: Path, entries: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for entry in entries:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _make_entry(ts: str, step: str, data: dict) -> dict:
    """Build an events.jsonl line matching ProjectRunLogger.log_step format."""
    return {
        "ts": ts,
        "level": "INFO",
        "event": "step",
        "data": {"step": step, "data": data},
    }


class TestLoadStreamHistory:

    def test_missing_dir_returns_empty(self, tmp_path: Path) -> None:
        result = load_stream_history(tmp_path / "nonexistent")
        assert result == []

    def test_missing_events_file_returns_empty(self, tmp_path: Path) -> None:
        # Directory exists but no events.jsonl.
        result = load_stream_history(tmp_path)
        assert result == []

    def test_single_stream_round(self, tmp_path: Path) -> None:
        entries = [
            _make_entry("2026-06-18T10:00:00Z", "llm_stream_start", {
                "stream_id": "s1", "task": "DRAFT_CHAPTER",
            }),
            _make_entry("2026-06-18T10:00:01Z", "llm_stream_delta", {
                "stream_id": "s1",
                "segments": [
                    {"kind": "reasoning", "text": "thinking"},
                    {"kind": "content", "text": "hello "},
                ],
            }),
            _make_entry("2026-06-18T10:00:02Z", "llm_stream_delta", {
                "stream_id": "s1",
                "segments": [{"kind": "content", "text": "world"}],
            }),
            _make_entry("2026-06-18T10:00:03Z", "llm_stream_end", {
                "stream_id": "s1",
            }),
        ]
        _write_events_jsonl(tmp_path / "events.jsonl", entries)
        result = load_stream_history(tmp_path)
        assert len(result) == 1
        stream = result[0]
        assert stream.stream_id == "s1"
        assert stream.task == "DRAFT_CHAPTER"
        assert stream.status == "complete"
        assert stream.text == "hello world"
        assert stream.reasoning_text == "thinking"
        assert len(stream.segments) == 2  # reasoning + content merged

    def test_multiple_streams_sorted_by_start(self, tmp_path: Path) -> None:
        entries = [
            _make_entry("2026-06-18T10:00:00Z", "llm_stream_start", {
                "stream_id": "s1", "task": "DRAFT",
            }),
            _make_entry("2026-06-18T10:00:05Z", "llm_stream_end", {"stream_id": "s1"}),
            _make_entry("2026-06-18T10:00:10Z", "llm_stream_start", {
                "stream_id": "s2", "task": "EDIT",
            }),
            _make_entry("2026-06-18T10:00:15Z", "llm_stream_end", {"stream_id": "s2"}),
        ]
        _write_events_jsonl(tmp_path / "events.jsonl", entries)
        result = load_stream_history(tmp_path)
        assert len(result) == 2
        assert result[0].stream_id == "s1"
        assert result[1].stream_id == "s2"

    def test_discarded_retry_round(self, tmp_path: Path) -> None:
        """Restart events mark the stream as discarded."""
        entries = [
            _make_entry("2026-06-18T10:00:00Z", "llm_stream_start", {
                "stream_id": "s1",
            }),
            _make_entry("2026-06-18T10:00:01Z", "llm_stream_delta", {
                "stream_id": "s1",
                "segments": [{"kind": "content", "text": "partial"}],
            }),
            _make_entry("2026-06-18T10:00:02Z", "llm_stream_error", {
                "stream_id": "s1", "error": "timeout",
            }),
            _make_entry("2026-06-18T10:00:03Z", "llm_stream_restart", {
                "stream_id": "s1",
                "error": "timeout",
            }),
        ]
        _write_events_jsonl(tmp_path / "events.jsonl", entries)
        result = load_stream_history(tmp_path)
        assert len(result) == 1
        stream = result[0]
        assert stream.discarded is True
        assert stream.status == "restarted"
        assert stream.text == "partial"

    def test_transport_reset_replaces_same_stream_without_marking_it_discarded(
        self,
        tmp_path: Path,
    ) -> None:
        entries = [
            _make_entry(
                "2026-06-18T10:00:00Z",
                "llm_stream_start",
                {"stream_id": "s1"},
            ),
            _make_entry(
                "2026-06-18T10:00:01Z",
                "llm_stream_delta",
                {
                    "stream_id": "s1",
                    "segments": [{"kind": "content", "text": "旧正文"}],
                },
            ),
            _make_entry(
                "2026-06-18T10:00:02Z",
                "llm_stream_restart",
                {"stream_id": "s1", "reset_output": True, "message": "切换备用路由"},
            ),
            _make_entry(
                "2026-06-18T10:00:03Z",
                "llm_stream_delta",
                {
                    "stream_id": "s1",
                    "segments": [{"kind": "content", "text": "新正文"}],
                },
            ),
            _make_entry(
                "2026-06-18T10:00:04Z",
                "llm_stream_end",
                {"stream_id": "s1"},
            ),
        ]
        _write_events_jsonl(tmp_path / "events.jsonl", entries)

        stream = load_stream_history(tmp_path)[0]

        assert stream.status == "complete"
        assert stream.discarded is False
        assert stream.text == "新正文"

    def test_legacy_delta_field_supported(self, tmp_path: Path) -> None:
        """Events without `segments` field use `delta` as content."""
        entries = [
            _make_entry("2026-06-18T10:00:00Z", "llm_stream_start", {
                "stream_id": "s1",
            }),
            _make_entry("2026-06-18T10:00:01Z", "llm_stream_delta", {
                "stream_id": "s1", "delta": "legacy",
            }),
            _make_entry("2026-06-18T10:00:02Z", "llm_stream_end", {
                "stream_id": "s1",
            }),
        ]
        _write_events_jsonl(tmp_path / "events.jsonl", entries)
        result = load_stream_history(tmp_path)
        assert len(result) == 1
        assert result[0].text == "legacy"

    def test_non_stream_events_ignored(self, tmp_path: Path) -> None:
        """Events for other steps should not be parsed."""
        entries = [
            _make_entry("2026-06-18T10:00:00Z", "run_log_started", {
                "run_id": "r1",
            }),
            _make_entry("2026-06-18T10:00:01Z", "llm_stream_start", {
                "stream_id": "s1",
            }),
            _make_entry("2026-06-18T10:00:02Z", "llm_stream_end", {
                "stream_id": "s1",
            }),
        ]
        _write_events_jsonl(tmp_path / "events.jsonl", entries)
        result = load_stream_history(tmp_path)
        assert len(result) == 1
        assert result[0].stream_id == "s1"

    def test_empty_file_returns_empty(self, tmp_path: Path) -> None:
        _write_events_jsonl(tmp_path / "events.jsonl", [])
        result = load_stream_history(tmp_path)
        assert result == []

    def test_invalid_json_lines_skipped(self, tmp_path: Path) -> None:
        path = tmp_path / "events.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        # Mix valid and invalid lines.
        with path.open("w", encoding="utf-8") as f:
            f.write("not valid json\n")
            f.write(json.dumps(_make_entry(
                "2026-06-18T10:00:00Z", "llm_stream_start",
                {"stream_id": "s1"},
            )) + "\n")
            f.write("also not json\n")
            f.write(json.dumps(_make_entry(
                "2026-06-18T10:00:01Z", "llm_stream_end",
                {"stream_id": "s1"},
            )) + "\n")
        result = load_stream_history(tmp_path)
        assert len(result) == 1
        assert result[0].stream_id == "s1"

    def test_interleaved_reasoning_and_content(self, tmp_path: Path) -> None:
        """Reasoning and content segments preserve their real interleaving order."""
        entries = [
            _make_entry("2026-06-18T10:00:00Z", "llm_stream_start", {
                "stream_id": "s1",
            }),
            _make_entry("2026-06-18T10:00:01Z", "llm_stream_delta", {
                "stream_id": "s1",
                "segments": [
                    {"kind": "reasoning", "text": "R1"},
                    {"kind": "content", "text": "C1"},
                    {"kind": "reasoning", "text": "R2"},
                ],
            }),
            _make_entry("2026-06-18T10:00:02Z", "llm_stream_end", {
                "stream_id": "s1",
            }),
        ]
        _write_events_jsonl(tmp_path / "events.jsonl", entries)
        result = load_stream_history(tmp_path)
        assert len(result) == 1
        stream = result[0]
        assert [s.kind for s in stream.segments] == ["reasoning", "content", "reasoning"]
        assert [s.text for s in stream.segments] == ["R1", "C1", "R2"]
