#!/usr/bin/env python3
"""Audit prompt field coverage: compare response schema fields vs prompt body mentions.

Scans all registered Jinja2 templates, extracts schema field names from the
corresponding Pydantic response-envelope model, and checks whether each field
is mentioned in the prompt body (excluding Jinja comments and variable refs).

Usage:
    python scripts/audit_prompt_field_coverage.py --all
    python scripts/audit_prompt_field_coverage.py --task EXTRACT_INIT_COHERENCE_CLAIMS
    python scripts/audit_prompt_field_coverage.py --all --strict
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Bootstrap repo path
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT))

from novel_forge.common.constants import TaskType  # noqa: E402
from novel_forge.core.parsing.response_schemas import get_response_schema  # noqa: E402
from novel_forge.prompts.registry import _PROMPTS_DIR, _TASK_TEMPLATE_MAP  # noqa: E402

# ---------------------------------------------------------------------------
# Schema field extraction (depth-first Pydantic model traversal)
# ---------------------------------------------------------------------------


def _extract_schema_fields(model: type[Any], *, _seen: set[str] | None = None) -> set[str]:
    """Recursively extract all field names from a Pydantic model.

    Traverses nested Pydantic models via ``model_fields`` and collects every
    leaf field name.  Handles ``list[SubModel]`` and bare ``SubModel`` types.
    """
    if _seen is None:
        _seen = set()

    model_id = id(model)
    if model_id in _seen:
        return set()
    _seen.add(model_id)

    fields: set[str] = set()
    model_fields = getattr(model, "model_fields", None)
    if not model_fields:
        return fields

    for name, field_info in model_fields.items():
        fields.add(name)
        annotation = field_info.annotation
        if annotation is None:
            continue

        # Unwrap Annotated[X, ...]
        origin = getattr(annotation, "__origin__", None)
        args = getattr(annotation, "__args__", ())

        if origin is not None:
            # list[SubModel], Optional[SubModel], etc.
            for arg in args:
                inner_origin = getattr(arg, "__origin__", None)
                inner_args = getattr(arg, "__args__", ())
                if inner_origin is not None:
                    for inner_arg in inner_args:
                        if _is_pydantic_model(inner_arg):
                            fields |= _extract_schema_fields(inner_arg, _seen=_seen)
                elif _is_pydantic_model(arg):
                    fields |= _extract_schema_fields(arg, _seen=_seen)
        elif _is_pydantic_model(annotation):
            fields |= _extract_schema_fields(annotation, _seen=_seen)

    return fields


def _is_pydantic_model(obj: Any) -> bool:
    """Check if *obj* is a Pydantic BaseModel subclass."""
    try:
        from pydantic import BaseModel

        return isinstance(obj, type) and issubclass(obj, BaseModel)
    except (TypeError, ImportError):
        return False


# ---------------------------------------------------------------------------
# Prompt body field mention detection
# ---------------------------------------------------------------------------

# Patterns to strip from Jinja2 templates before scanning
_JINJA_COMMENT_RE = re.compile(r"\{#.*?#\}", re.DOTALL)
_JINJA_VAR_RE = re.compile(r"\{\{.*?\}\}", re.DOTALL)
_JINJA_BLOCK_RE = re.compile(r"\{%.*?%\}", re.DOTALL)


def _clean_prompt_body(text: str) -> str:
    """Remove Jinja2 syntax that should not count as field mentions."""
    # Remove Jinja comments {# ... #}
    text = _JINJA_COMMENT_RE.sub("", text)
    # Remove Jinja variable refs {{ ... }}
    text = _JINJA_VAR_RE.sub("", text)
    # Remove Jinja block tags {% ... %} (but keep the content between them)
    text = _JINJA_BLOCK_RE.sub("", text)
    return text


def _field_mentioned(field_name: str, clean_body: str) -> bool:
    """Check whether a schema field name is mentioned in the cleaned prompt body.

    Matches the field name as a whole word (case-insensitive), allowing for
    Chinese context around it.  Underscore-separated names like ``payoff_id``
    match both ``payoff_id`` and ``payoff id`` (Chinese prompts sometimes use
    spaces).
    """
    # Direct match (case-insensitive)
    pattern = re.compile(re.escape(field_name), re.IGNORECASE)
    if pattern.search(clean_body):
        return True

    # Also try with underscores replaced by spaces (common in Chinese prompts)
    spaced = field_name.replace("_", " ")
    if spaced != field_name:
        pattern_spaced = re.compile(re.escape(spaced), re.IGNORECASE)
        if pattern_spaced.search(clean_body):
            return True

    # Try matching just the last segment of snake_case (e.g. "payoff" from "payoff_id")
    # Only for compound names to avoid false positives on common words
    parts = field_name.split("_")
    if len(parts) >= 2:
        # Match the full compound with Chinese punctuation separators
        for sep in ["的", "：", ":", "—", "-", "·"]:
            joined = sep.join(parts)
            if re.compile(re.escape(joined), re.IGNORECASE).search(clean_body):
                return True

    return False


# ---------------------------------------------------------------------------
# Core audit logic
# ---------------------------------------------------------------------------


def _build_task_template_map() -> dict[str, tuple[TaskType, ...]]:
    """Build template_path → (TaskType, ...) mapping."""
    mapping: dict[str, list[TaskType]] = {}
    for task_type, template in _TASK_TEMPLATE_MAP.items():
        mapping.setdefault(template, []).append(task_type)
    return {template: tuple(tasks) for template, tasks in mapping.items()}


def audit_task_type(task_type: TaskType) -> dict[str, Any]:
    """Audit one TaskType: compare schema fields vs prompt mentions.

    Returns a dict with:
        task_type, template, schema_fields, mentioned_fields,
        unmentioned_fields, schema_field_count, mentioned_count
    """
    template_path = _TASK_TEMPLATE_MAP.get(task_type)
    if template_path is None:
        return {
            "task_type": task_type.value,
            "template": "<not registered>",
            "schema_fields": [],
            "mentioned_fields": [],
            "unmentioned_fields": [],
            "schema_field_count": 0,
            "mentioned_count": 0,
            "has_schema": False,
        }

    full_path = _PROMPTS_DIR / template_path
    if not full_path.exists():
        return {
            "task_type": task_type.value,
            "template": template_path,
            "schema_fields": [],
            "mentioned_fields": [],
            "unmentioned_fields": [],
            "schema_field_count": 0,
            "mentioned_count": 0,
            "has_schema": False,
            "error": "template file not found",
        }

    # Get response schema model
    schema_model = get_response_schema(task_type)
    if schema_model is None:
        return {
            "task_type": task_type.value,
            "template": template_path,
            "schema_fields": [],
            "mentioned_fields": [],
            "unmentioned_fields": [],
            "schema_field_count": 0,
            "mentioned_count": 0,
            "has_schema": False,
        }

    # Extract schema fields
    schema_fields = sorted(_extract_schema_fields(schema_model))

    # Read and clean prompt body
    prompt_text = full_path.read_text(encoding="utf-8")
    clean_body = _clean_prompt_body(prompt_text)

    # Check which fields are mentioned
    mentioned = []
    unmentioned = []
    for field in schema_fields:
        if _field_mentioned(field, clean_body):
            mentioned.append(field)
        else:
            unmentioned.append(field)

    return {
        "task_type": task_type.value,
        "template": template_path,
        "schema_fields": schema_fields,
        "mentioned_fields": mentioned,
        "unmentioned_fields": unmentioned,
        "schema_field_count": len(schema_fields),
        "mentioned_count": len(mentioned),
        "has_schema": True,
    }


# ---------------------------------------------------------------------------
# Output rendering
# ---------------------------------------------------------------------------


def _render_markdown_table(results: list[dict[str, Any]], *, strict: bool = False) -> str:
    """Render results as a markdown table."""
    lines = [
        "# Prompt Field Coverage Audit",
        "",
        "Generated by `audit_prompt_field_coverage.py`",
        f"Templates scanned: {len(results)}",
        f"Tasks with response schema: {sum(1 for r in results if r['has_schema'])}",
        "",
    ]

    # Summary stats
    total_schema = sum(r["schema_field_count"] for r in results if r["has_schema"])
    total_mentioned = sum(r["mentioned_count"] for r in results if r["has_schema"])
    total_unmentioned = sum(len(r["unmentioned_fields"]) for r in results if r["has_schema"])
    coverage_pct = f"{total_mentioned / total_schema * 100:.1f}%" if total_schema > 0 else "N/A"

    lines.extend(
        [
            "## Summary",
            "",
            f"- Total schema fields: {total_schema}",
            f"- Mentioned in prompts: {total_mentioned}",
            f"- Not mentioned: {total_unmentioned}",
            f"- Overall coverage: {coverage_pct}",
            "",
        ]
    )

    # Per-task table (only tasks with schemas)
    lines.extend(
        [
            "## Per-Task Coverage",
            "",
            "| TaskType | Template | Schema Fields | Mentioned | Coverage | Unmentioned Fields |",
            "|----------|----------|--------------|-----------|----------|-------------------|",
        ]
    )

    strict_violations: list[str] = []

    for r in sorted(results, key=lambda x: x["task_type"]):
        if not r["has_schema"]:
            continue

        schema_count = r["schema_field_count"]
        mentioned_count = r["mentioned_count"]
        unmentioned = r["unmentioned_fields"]
        coverage = f"{mentioned_count / schema_count * 100:.0f}%" if schema_count > 0 else "N/A"

        unmentioned_text = ", ".join(unmentioned) if unmentioned else "-"
        if strict and unmentioned:
            strict_violations.append(f"- `{r['task_type']}`: {unmentioned_text}")

        lines.append(
            f"| `{r['task_type']}` | `{r['template']}` | {schema_count} | "
            f"{mentioned_count} | {coverage} | {unmentioned_text} |"
        )

    lines.append("")

    # Strict mode violations
    if strict and strict_violations:
        lines.extend(
            [
                "## Strict Mode Violations",
                "",
                "The following tasks have unmentioned schema fields:",
                "",
                *strict_violations,
                "",
            ]
        )

    # Tasks without response schemas
    no_schema = [r for r in results if not r["has_schema"]]
    if no_schema:
        lines.extend(
            [
                "## Tasks Without Response Schema",
                "",
                "These tasks do not have a registered response-envelope model.",
                "",
                "| TaskType | Template | Reason |",
                "|----------|----------|--------|",
            ]
        )
        for r in sorted(no_schema, key=lambda x: x["task_type"]):
            reason = r.get("error", "no response_schema_model registered")
            lines.append(f"| `{r['task_type']}` | `{r['template']}` | {reason} |")
        lines.append("")

    return "\n".join(lines)


def _render_single_task(result: dict[str, Any]) -> str:
    """Render detailed output for a single task."""
    lines = [
        f"# Field Coverage: `{result['task_type']}`",
        "",
        f"- Template: `{result['template']}`",
        f"- Has response schema: {result['has_schema']}",
        "",
    ]

    if not result["has_schema"]:
        lines.append("No response-envelope schema registered for this task.")
        return "\n".join(lines)

    schema_count = result["schema_field_count"]
    mentioned_count = result["mentioned_count"]
    coverage = f"{mentioned_count / schema_count * 100:.0f}%" if schema_count > 0 else "N/A"

    lines.extend(
        [
            f"- Schema fields: {schema_count}",
            f"- Mentioned: {mentioned_count}",
            f"- Coverage: {coverage}",
            "",
            "## Schema Fields",
            "",
        ]
    )

    for field in result["schema_fields"]:
        status = "✅" if field in result["mentioned_fields"] else "❌"
        lines.append(f"- {status} `{field}`")

    if result["unmentioned_fields"]:
        lines.extend(
            [
                "",
                "## Unmentioned Fields (potential risk)",
                "",
            ]
        )
        for field in result["unmentioned_fields"]:
            lines.append(f"- `{field}`")

    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--all",
        action="store_true",
        help="Audit all registered task templates.",
    )
    parser.add_argument(
        "--task",
        type=str,
        default=None,
        help="Audit a single TaskType (e.g. EXTRACT_INIT_COHERENCE_CLAIMS).",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit non-zero if any task has unmentioned schema fields.",
    )
    args = parser.parse_args(argv)

    if not args.all and args.task is None:
        parser.error("Specify --all or --task <TaskType>")

    results: list[dict[str, Any]] = []

    if args.task:
        task_name = args.task.strip()
        # Try both the enum value (lowercase) and the enum name (uppercase)
        try:
            task_type = TaskType(task_name)
        except ValueError:
            try:
                task_type = TaskType[task_name.upper()]
            except KeyError:
                print(f"Error: unknown TaskType '{task_name}'", file=sys.stderr)
                return 1
        result = audit_task_type(task_type)
        print(_render_single_task(result))
        if args.strict and result["has_schema"] and result["unmentioned_fields"]:
            return 1
        return 0

    # --all mode
    for task_type in sorted(TaskType, key=lambda t: t.value):
        if task_type not in _TASK_TEMPLATE_MAP:
            continue
        results.append(audit_task_type(task_type))

    print(_render_markdown_table(results, strict=args.strict))

    if args.strict:
        for r in results:
            if r["has_schema"] and r["unmentioned_fields"]:
                return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
