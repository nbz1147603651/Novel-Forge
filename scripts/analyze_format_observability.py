#!/usr/bin/env python3
"""Aggregate structured-output format telemetry from Novel Forge run logs."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from novel_forge.common.constants import TaskType  # noqa: E402
from novel_forge.core.format_contracts import OutputKind, get_task_format_contract  # noqa: E402


@dataclass
class FormatObservabilitySummary:
    runs_scanned: int = 0
    events_scanned: int = 0
    json_model_calls: int = 0
    format_validation_success_count: int = 0
    strict_parse_success_count: int = 0
    format_retry_count: int = 0
    local_safe_repair_count: int = 0
    local_lossy_repair_count: int = 0
    local_unsafe_repair_count: int = 0
    llm_repair_count: int = 0
    final_failure_count: int = 0
    structured_mode_counts: dict[str, int] = field(default_factory=dict)
    structured_downgrade_count: int = 0
    schema_issue_counts: dict[str, int] = field(default_factory=dict)
    task_counts: dict[str, int] = field(default_factory=dict)

    @property
    def estimated_strict_parse_success_count(self) -> int:
        # Project logs do not yet emit a strict-parse success event. This estimate
        # treats JSON model calls without format retry/repair/failure events as
        # strict successes.
        noisy = self.format_retry_count + self.local_safe_repair_count + self.local_lossy_repair_count
        noisy += self.local_unsafe_repair_count + self.llm_repair_count + self.final_failure_count
        return max(0, self.json_model_calls - noisy)

    @property
    def estimated_strict_parse_success_rate(self) -> float | None:
        if self.json_model_calls <= 0:
            return None
        return self.estimated_strict_parse_success_count / self.json_model_calls

    @property
    def strict_parse_success_rate(self) -> float | None:
        if self.json_model_calls <= 0:
            return None
        return self.strict_parse_success_count / self.json_model_calls

    @property
    def final_failure_rate(self) -> float | None:
        denominator = self.format_retry_count + self.final_failure_count
        if denominator <= 0:
            return None
        return self.final_failure_count / denominator


def _task_type_from_value(value: Any) -> TaskType | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        return TaskType(raw)
    except ValueError:
        pass
    upper = raw.upper()
    return TaskType.__members__.get(upper)


def _task_expects_json(task: Any) -> bool:
    task_type = _task_type_from_value(task)
    if task_type is None:
        return False
    contract = get_task_format_contract(task_type)
    return bool(contract is not None and contract.output_kind == OutputKind.JSON)


def _events_paths(path: Path) -> list[Path]:
    if path.is_file() and path.name == "events.jsonl":
        return [path]
    if (path / "events.jsonl").is_file():
        return [path / "events.jsonl"]
    logs_dir = path / "logs"
    if logs_dir.is_dir():
        return sorted(logs_dir.glob("*/events.jsonl"))
    return sorted(path.glob("*/events.jsonl"))


def _iter_events(events_path: Path) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    try:
        lines = events_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return events
    for line in lines:
        if not line.strip():
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            events.append(parsed)
    return events


def _step_payload(event: dict[str, Any]) -> tuple[str, dict[str, Any]] | None:
    if event.get("event") != "step":
        return None
    data = event.get("data")
    if not isinstance(data, dict):
        return None
    step = str(data.get("step") or "")
    payload = data.get("data")
    if not isinstance(payload, dict):
        payload = {}
    return step, payload


def _increment(mapping: dict[str, int], key: Any, amount: int = 1) -> None:
    clean = str(key or "").strip() or "unknown"
    mapping[clean] = mapping.get(clean, 0) + amount


def _record_schema_issues(summary: FormatObservabilitySummary, payload: dict[str, Any]) -> None:
    issues = payload.get("schema_issues")
    if not isinstance(issues, list):
        return
    for issue in issues:
        if not isinstance(issue, dict):
            continue
        _increment(summary.schema_issue_counts, issue.get("issue_type"))


def _record_structured_mode(summary: FormatObservabilitySummary, event: dict[str, Any]) -> None:
    data = event.get("data")
    if not isinstance(data, dict):
        return
    mode = data.get("structured_output_mode")
    if mode:
        _increment(summary.structured_mode_counts, mode)
    if data.get("structured_output_downgraded_from"):
        summary.structured_downgrade_count += 1


def analyze_format_observability(path: Path) -> FormatObservabilitySummary:
    summary = FormatObservabilitySummary()
    events_paths = _events_paths(path)
    summary.runs_scanned = len(events_paths)

    for events_path in events_paths:
        for event in _iter_events(events_path):
            summary.events_scanned += 1
            event_name = event.get("event")
            data = event.get("data")
            if event_name in {"api_call_done", "api_stream_done"} and isinstance(data, dict):
                if _task_expects_json(data.get("task")):
                    summary.json_model_calls += 1
                _record_structured_mode(summary, event)
                continue

            step_payload = _step_payload(event)
            if step_payload is None:
                continue
            step, payload = step_payload
            task = payload.get("task")
            if task:
                _increment(summary.task_counts, task)
            _record_schema_issues(summary, payload)

            if step == "format_retry":
                summary.format_retry_count += 1
            elif step == "format_validation_success":
                summary.format_validation_success_count += 1
                if str(payload.get("parse_source") or "") in {"strict", "strict_json"}:
                    summary.strict_parse_success_count += 1
            elif step == "format_repaired":
                source = str(payload.get("repair_source") or "")
                risk = str(payload.get("repair_risk") or "")
                if source == "llm_repair":
                    summary.llm_repair_count += 1
                elif risk == "safe":
                    summary.local_safe_repair_count += 1
                elif risk == "lossy":
                    summary.local_lossy_repair_count += 1
                elif risk == "unsafe":
                    summary.local_unsafe_repair_count += 1
            elif step == "format_retry_exhausted":
                summary.final_failure_count += 1
    return summary


def _render_markdown(summary: FormatObservabilitySummary) -> str:
    exact_strict_rate = summary.strict_parse_success_rate
    strict_rate = summary.estimated_strict_parse_success_rate
    failure_rate = summary.final_failure_rate
    lines = [
        "# Format Observability Summary",
        "",
        f"- runs_scanned: {summary.runs_scanned}",
        f"- events_scanned: {summary.events_scanned}",
        f"- json_model_calls: {summary.json_model_calls}",
        f"- format_validation_success_count: {summary.format_validation_success_count}",
        f"- strict_parse_success_count: {summary.strict_parse_success_count}",
        "- strict_parse_success_rate: "
        + ("n/a" if exact_strict_rate is None else f"{exact_strict_rate:.2%}"),
        f"- estimated_strict_parse_success_count: {summary.estimated_strict_parse_success_count}",
        "- estimated_strict_parse_success_rate: "
        + ("n/a" if strict_rate is None else f"{strict_rate:.2%}"),
        f"- format_retry_count: {summary.format_retry_count}",
        f"- local_safe_repair_count: {summary.local_safe_repair_count}",
        f"- local_lossy_repair_count: {summary.local_lossy_repair_count}",
        f"- llm_repair_count: {summary.llm_repair_count}",
        f"- final_failure_count: {summary.final_failure_count}",
        "- final_failure_rate: " + ("n/a" if failure_rate is None else f"{failure_rate:.2%}"),
        f"- structured_downgrade_count: {summary.structured_downgrade_count}",
    ]
    if summary.structured_mode_counts:
        lines.append("")
        lines.append("## Structured Modes")
        for key, value in sorted(summary.structured_mode_counts.items()):
            lines.append(f"- {key}: {value}")
    if summary.schema_issue_counts:
        lines.append("")
        lines.append("## Schema Issues")
        for key, value in sorted(summary.schema_issue_counts.items()):
            lines.append(f"- {key}: {value}")
    if summary.task_counts:
        lines.append("")
        lines.append("## Tasks With Format Events")
        for key, value in sorted(summary.task_counts.items()):
            lines.append(f"- {key}: {value}")
    return "\n".join(lines)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "path",
        type=Path,
        help="Run dir, events.jsonl, project root containing logs/, or logs dir.",
    )
    parser.add_argument("--json", action="store_true", help="Output JSON instead of Markdown.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    summary = analyze_format_observability(args.path)
    payload = asdict(summary)
    payload["strict_parse_success_rate"] = summary.strict_parse_success_rate
    payload["estimated_strict_parse_success_count"] = summary.estimated_strict_parse_success_count
    payload["estimated_strict_parse_success_rate"] = summary.estimated_strict_parse_success_rate
    payload["final_failure_rate"] = summary.final_failure_rate
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(_render_markdown(summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
