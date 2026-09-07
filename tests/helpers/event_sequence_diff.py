"""Event sequence diff harness for behavior-snapshot tests.

Compares the ordered sequence of pipeline events (step names + key data
fields) between a baseline run and a post-refactor run, so that refactoring
can be verified to preserve the pipeline's behavioural ordering.

Noise fields (timestamps, run IDs, token counts, durations) are stripped
before comparison to avoid false positives from non-deterministic values.

Usage::

    from tests.helpers.event_sequence_diff import extract_event_sequence, compare_event_sequences

    baseline = extract_event_sequence(run_dir / "pipeline.jsonl")
    current = extract_event_sequence(run_dir / "pipeline.jsonl")
    diffs = compare_event_sequences(baseline, current)
    assert not diffs, "Event sequence mismatch:\n" + "\n".join(diffs)
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_EVENT_NOISE_KEYS: frozenset[str] = frozenset({
    "ts",
    "run_id",
    "session_id",
    "trace_id",
    "span_id",
    "request_id",
    "started_at",
    "ended_at",
    "duration_ms",
    "latency_ms",
    "elapsed_ms",
    "prompt_tokens",
    "completion_tokens",
    "total_tokens",
    "tokens_used",
    "cost_usd",
    "word_count",
    "char_count",
    "text_length",
    "report_length",
})

_STEP_DATA_NOISE_KEYS: frozenset[str] = frozenset({
    "duration_ms",
    "latency_ms",
    "elapsed_ms",
    "tokens",
    "cost",
    "word_count",
    "char_count",
    "text_length",
    "report_length",
    "log_file",
    "run_dir",
    "file_path",
    "error_traceback",
})


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    result: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            result.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return result


def _strip_noise(data: Any, noise_keys: frozenset[str]) -> Any:
    if isinstance(data, dict):
        return {
            k: _strip_noise(v, noise_keys)
            for k, v in data.items()
            if k not in noise_keys
        }
    if isinstance(data, list):
        return [_strip_noise(item, noise_keys) for item in data]
    return data


def extract_event_sequence(
    events_path: Path,
    *,
    include_data: bool = True,
) -> list[dict[str, Any]]:
    raw_events = _load_jsonl(events_path)
    result: list[dict[str, Any]] = []
    for evt in raw_events:
        event_name = evt.get("event", "")
        data = evt.get("data", {}) or {}
        step_name = ""
        if isinstance(data, dict):
            step_name = str(data.get("step", ""))
        entry: dict[str, Any] = {"event": event_name}
        if step_name:
            entry["step"] = step_name
        if include_data and isinstance(data, dict):
            cleaned = _strip_noise(data, _STEP_DATA_NOISE_KEYS)
            if step_name and "step" in cleaned:
                cleaned.pop("step")
            if cleaned:
                entry["data"] = cleaned
        result.append(entry)
    return result


def extract_step_sequence(events_path: Path) -> list[str]:
    events = _load_jsonl(events_path)
    steps: list[str] = []
    for evt in events:
        if evt.get("event") != "step":
            continue
        data = evt.get("data", {}) or {}
        if isinstance(data, dict):
            step = data.get("step")
            if step:
                steps.append(str(step))
    return steps


def compare_event_sequences(
    baseline: list[dict[str, Any]],
    current: list[dict[str, Any]],
) -> list[str]:
    diffs: list[str] = []
    max_len = max(len(baseline), len(current))
    for i in range(max_len):
        b = baseline[i] if i < len(baseline) else None
        c = current[i] if i < len(current) else None
        if b is None:
            diffs.append(f"  + [{i}] extra in current: event={c.get('event')} step={c.get('step', '')}")
            continue
        if c is None:
            diffs.append(f"  - [{i}] missing in current: event={b.get('event')} step={b.get('step', '')}")
            continue
        if b.get("event") != c.get("event"):
            diffs.append(
                f"  ~ [{i}] event mismatch: baseline={b.get('event')} → current={c.get('event')}"
            )
            continue
        if b.get("step") != c.get("step"):
            diffs.append(
                f"  ~ [{i}] step mismatch: baseline={b.get('step')} → current={c.get('step')}"
            )
            continue
        b_data = b.get("data", {})
        c_data = c.get("data", {})
        if b_data != c_data:
            b_keys = set(b_data.keys()) if isinstance(b_data, dict) else set()
            c_keys = set(c_data.keys()) if isinstance(c_data, dict) else set()
            only_baseline = sorted(b_keys - c_keys)
            only_current = sorted(c_keys - b_keys)
            changed = sorted(k for k in (b_keys & c_keys) if b_data[k] != c_data[k])
            parts: list[str] = []
            if only_baseline:
                parts.append(f"only in baseline: {only_baseline}")
            if only_current:
                parts.append(f"only in current: {only_current}")
            if changed:
                parts.append(f"changed: {changed}")
            diffs.append(
                f"  ~ [{i}] data mismatch (step={b.get('step', '')}): {'; '.join(parts)}"
            )
    return diffs


def assert_event_sequences_match(
    baseline_path: Path,
    current_path: Path,
) -> None:
    baseline = extract_event_sequence(baseline_path)
    current = extract_event_sequence(current_path)
    diffs = compare_event_sequences(baseline, current)
    if diffs:
        details = "\n".join(diffs)
        raise AssertionError(
            f"Event sequence mismatch ({len(diffs)} differences):\n{details}"
        )


def assert_step_sequences_match(
    baseline_path: Path,
    current_path: Path,
) -> None:
    baseline = extract_step_sequence(baseline_path)
    current = extract_step_sequence(current_path)
    diffs: list[str] = []
    max_len = max(len(baseline), len(current))
    for i in range(max_len):
        b = baseline[i] if i < len(baseline) else None
        c = current[i] if i < len(current) else None
        if b is None:
            diffs.append(f"  + [{i}] extra step in current: {c}")
        elif c is None:
            diffs.append(f"  - [{i}] missing step in current: {b}")
        elif b != c:
            diffs.append(f"  ~ [{i}] step mismatch: {b} → {c}")
    if diffs:
        details = "\n".join(diffs)
        raise AssertionError(
            f"Step sequence mismatch ({len(diffs)} differences):\n{details}"
        )
