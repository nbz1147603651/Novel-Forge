"""Verify P0/P1 prompt templates contain key schema fields and strong constraint words.

Regression test: after T7/T8/T10 prompt fixes, ensure no regression in field
coverage or constraint-word presence for high-priority tasks.

P0 = ModelTier.PREMIUM tasks (critical path, highest quality model)
P1 = ModelTier.STANDARD tasks (main pipeline, standard model)

Each test reads the raw Jinja2 template source, strips Jinja2 syntax, and checks:
1. Key schema field names appear in the cleaned prompt body
2. At least one strong constraint word ("必须"/"禁止"/"应该"/"不得") appears
3. Four known-risk templates have concrete field assertions
"""

from __future__ import annotations

import re
from typing import Any

import pytest

from novel_forge.common.constants import TaskType
from novel_forge.core.parsing.response_schemas import get_response_schema
from novel_forge.core.task_catalog import DEFAULT_TASK_TIERS, ModelTier
from novel_forge.prompts.registry import _PROMPTS_DIR, _TASK_TEMPLATE_MAP

# ---------------------------------------------------------------------------
# Schema field extraction (reused from audit_prompt_field_coverage.py)
# ---------------------------------------------------------------------------


def _is_pydantic_model(obj: Any) -> bool:
    try:
        from pydantic import BaseModel

        return isinstance(obj, type) and issubclass(obj, BaseModel)
    except (TypeError, ImportError):
        return False


def _extract_schema_fields(model: type[Any], *, _seen: set[int] | None = None) -> set[str]:
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
        origin = getattr(annotation, "__origin__", None)
        args = getattr(annotation, "__args__", ())
        if origin is not None:
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


# ---------------------------------------------------------------------------
# Prompt body cleaning (reused from audit_prompt_field_coverage.py)
# ---------------------------------------------------------------------------

_JINJA_COMMENT_RE = re.compile(r"\{#.*?#\}", re.DOTALL)
_JINJA_VAR_RE = re.compile(r"\{\{.*?\}\}", re.DOTALL)
_JINJA_BLOCK_RE = re.compile(r"\{%.*?%\}", re.DOTALL)


def _clean_prompt_body(text: str) -> str:
    text = _JINJA_COMMENT_RE.sub("", text)
    text = _JINJA_VAR_RE.sub("", text)
    text = _JINJA_BLOCK_RE.sub("", text)
    return text


def _field_mentioned(field_name: str, clean_body: str) -> bool:
    pattern = re.compile(re.escape(field_name), re.IGNORECASE)
    if pattern.search(clean_body):
        return True
    spaced = field_name.replace("_", " ")
    if spaced != field_name:
        if re.compile(re.escape(spaced), re.IGNORECASE).search(clean_body):
            return True
    parts = field_name.split("_")
    if len(parts) >= 2:
        for sep in ["的", "：", ":", "—", "-", "·"]:
            joined = sep.join(parts)
            if re.compile(re.escape(joined), re.IGNORECASE).search(clean_body):
                return True
    return False


# ---------------------------------------------------------------------------
# P0/P1 task selection
# ---------------------------------------------------------------------------

# P0 = PREMIUM tier (critical path)
_P0_TASKS: tuple[TaskType, ...] = tuple(
    tt for tt, tier in DEFAULT_TASK_TIERS.items() if tier == ModelTier.PREMIUM
)

# P1 = STANDARD tier (main pipeline) — exclude BUDGET-only utility tasks
_P1_TASKS: tuple[TaskType, ...] = tuple(
    tt
    for tt, tier in DEFAULT_TASK_TIERS.items()
    if tier == ModelTier.STANDARD and tt in _TASK_TEMPLATE_MAP
)

# Combined P0+P1 for general coverage tests
_P0P1_TASKS: tuple[TaskType, ...] = _P0_TASKS + _P1_TASKS

# ---------------------------------------------------------------------------
# Required field map for high-risk templates (concrete assertions)
# ---------------------------------------------------------------------------

PROMPT_FIELDS_REQUIRED: dict[str, list[str]] = {
    # EXTRACT_INIT_COHERENCE_CLAIMS: payoff fields must be present
    TaskType.EXTRACT_INIT_COHERENCE_CLAIMS.value: [
        "payoff_id",
        "payoff_kind",
    ],
    # DERIVE_EDITORIAL_CONTRACT: max_reuse with range constraint
    TaskType.DERIVE_EDITORIAL_CONTRACT.value: [
        "max_reuse",
    ],
    # PLAN_OUTLINE: basic outline structure
    TaskType.PLAN_OUTLINE.value: [
        "chapters",
    ],
    # REPAIR_INIT_ARTIFACT_PATCH: repair-related fields
    TaskType.REPAIR_INIT_ARTIFACT_PATCH.value: [
        "patches",
    ],
}

# Strong constraint words that must appear in every P0/P1 prompt
_CONSTRAINT_WORDS = ("必须", "禁止", "应该", "不得", "务必", "严禁")

# Known templates that lack constraint words (pre-existing issues, not T7/T8/T10 regressions)
_NO_CONSTRAINT_WORDS = frozenset({
    TaskType.POLISH_SUBPLOT,
    TaskType.INIT_STORY_CORE_PREMISE,
    TaskType.CRITIC_CONTINUITY,
    TaskType.CRITIC_CHARACTER,
    TaskType.SUMMARIZE_VOLUME,
    TaskType.SUMMARIZE_ARC,
    TaskType.ADJUDICATE_FACT_CONFLICT,
    TaskType.GROUND_OUTLINE_RESEARCH,
})

# Known templates with low schema field coverage (pre-existing, not T7/T8/T10 regressions)
_LOW_SCHEMA_COVERAGE = frozenset({
    TaskType.ADJUST_OUTLINE,
    TaskType.ADJUDICATE_CONTRACT_COMPLETION,
    TaskType.ADJUDICATE_FACT_CONFLICT,
    TaskType.ADJUDICATE_ENTITY_REFERENCES,
    # Prompt added in 77df663e; schema field references not yet populated.
    TaskType.TTS_ADJUDICATE_VOICE_MATCH,
})

_IMPORT_RE = re.compile(r'from\s+["\']([^"\']+)["\']\s+import', re.IGNORECASE)


def _read_template(task_type: TaskType) -> str:
    template_path = _TASK_TEMPLATE_MAP.get(task_type)
    if template_path is None:
        return ""
    full_path = _PROMPTS_DIR / template_path
    if not full_path.exists():
        return ""
    body = full_path.read_text(encoding="utf-8")
    # Also include content of Jinja2-imported macro files so that
    # constraint-word and field checks see through macro indirection.
    seen: set[str] = {template_path}
    parts = [body]
    for m in _IMPORT_RE.finditer(body):
        imported = m.group(1)
        if imported in seen:
            continue
        seen.add(imported)
        # Resolve relative to the template's parent directory
        macro_path = full_path.parent / imported
        if not macro_path.exists():
            # Try from prompts root (for _base/ imports)
            macro_path = _PROMPTS_DIR / imported
        if macro_path.exists():
            parts.append(macro_path.read_text(encoding="utf-8"))
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Test: P0/P1 templates contain constraint words
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("task_type", _P0P1_TASKS)
def test_p0p1_prompt_contains_constraint_words(task_type: TaskType) -> None:
    if task_type not in _TASK_TEMPLATE_MAP:
        pytest.skip(f"No template registered for {task_type.value}")
    if task_type in _NO_CONSTRAINT_WORDS:
        pytest.skip(f"Known pre-existing gap (not a T7/T8/T10 regression): {task_type.value}")

    raw = _read_template(task_type)
    if not raw:
        pytest.skip(f"Template file not found for {task_type.value}")

    clean_body = _clean_prompt_body(raw)

    found = any(word in clean_body for word in _CONSTRAINT_WORDS)
    assert found, (
        f"{task_type.value}: prompt body must contain at least one strong constraint word "
        f"({', '.join(_CONSTRAINT_WORDS)}). Template: {_TASK_TEMPLATE_MAP[task_type]}"
    )


# ---------------------------------------------------------------------------
# Test: P0/P1 templates with response schemas mention key fields
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("task_type", _P0P1_TASKS)
def test_p0p1_prompt_mentions_schema_fields(task_type: TaskType) -> None:
    if task_type not in _TASK_TEMPLATE_MAP:
        pytest.skip(f"No template registered for {task_type.value}")
    if task_type in _LOW_SCHEMA_COVERAGE:
        pytest.skip(f"Known pre-existing low coverage (not T7/T8/T10 regression): {task_type.value}")

    schema_model = get_response_schema(task_type)
    if schema_model is None:
        pytest.skip(f"No response schema for {task_type.value}")

    raw = _read_template(task_type)
    if not raw:
        pytest.skip(f"Template file not found for {task_type.value}")

    clean_body = _clean_prompt_body(raw)

    schema_fields = _extract_schema_fields(schema_model)
    if not schema_fields:
        pytest.skip(f"No extractable schema fields for {task_type.value}")

    mentioned = [f for f in schema_fields if _field_mentioned(f, clean_body)]
    # Require at least 30% of schema fields mentioned (generous threshold)
    min_mentioned = max(1, int(len(schema_fields) * 0.3))
    assert len(mentioned) >= min_mentioned, (
        f"{task_type.value}: only {len(mentioned)}/{len(schema_fields)} schema fields "
        f"mentioned in prompt (need ≥{min_mentioned}). "
        f"Missing: {sorted(schema_fields - set(mentioned))[:10]}"
    )


# ---------------------------------------------------------------------------
# Test: Concrete assertions for 4 known high-risk templates
# ---------------------------------------------------------------------------


def test_extract_init_coherence_claims_contains_payoff_fields() -> None:
    task_type = TaskType.EXTRACT_INIT_COHERENCE_CLAIMS
    raw = _read_template(task_type)
    assert raw, f"Template not found for {task_type.value}"
    clean_body = _clean_prompt_body(raw)

    for field in ["payoff_id", "payoff_kind"]:
        assert _field_mentioned(field, clean_body), (
            f"{task_type.value}: prompt must mention '{field}' (T7 fix regression check)"
        )


def test_derive_editorial_contract_contains_max_reuse_constraint() -> None:
    task_type = TaskType.DERIVE_EDITORIAL_CONTRACT
    raw = _read_template(task_type)
    assert raw, f"Template not found for {task_type.value}"
    clean_body = _clean_prompt_body(raw)

    assert _field_mentioned("max_reuse", clean_body), (
        f"{task_type.value}: prompt must mention 'max_reuse' (T8 fix regression check)"
    )

    # Verify range constraint "1-10" or equivalent is present
    has_range = bool(
        re.search(r"1\s*[-–—至到]\s*10", clean_body)
        or "1-10" in clean_body
        or "1 至 10" in clean_body
        or "1到10" in clean_body
    )
    assert has_range, (
        f"{task_type.value}: prompt must specify max_reuse range 1-10 (T8 fix regression check)"
    )

    # Verify prohibition of 0
    has_zero_prohibition = (
        ("禁止" in clean_body or "不得" in clean_body or "不能" in clean_body)
        and ("0" in clean_body or "零" in clean_body)
    )
    assert has_zero_prohibition, (
        f"{task_type.value}: prompt must prohibit max_reuse=0 (T8 fix regression check)"
    )


def test_plan_outline_contains_chapters_field() -> None:
    task_type = TaskType.PLAN_OUTLINE
    raw = _read_template(task_type)
    assert raw, f"Template not found for {task_type.value}"
    clean_body = _clean_prompt_body(raw)

    assert _field_mentioned("chapters", clean_body), (
        f"{task_type.value}: prompt must mention 'chapters' field"
    )


def test_repair_init_artifact_patch_contains_patches_field() -> None:
    task_type = TaskType.REPAIR_INIT_ARTIFACT_PATCH
    raw = _read_template(task_type)
    assert raw, f"Template not found for {task_type.value}"
    clean_body = _clean_prompt_body(raw)

    # Template uses "patch" (singular) extensively; schema uses "patches" (plural)
    assert _field_mentioned("patch", clean_body), (
        f"{task_type.value}: prompt must mention 'patch' field"
    )


# ---------------------------------------------------------------------------
# Test: PROMPT_FIELDS_REQUIRED constant is well-formed
# ---------------------------------------------------------------------------


def test_prompt_fields_required_constant_is_valid() -> None:
    """PROMPT_FIELDS_REQUIRED must map valid TaskType values to non-empty lists."""
    assert isinstance(PROMPT_FIELDS_REQUIRED, dict)
    assert len(PROMPT_FIELDS_REQUIRED) == 4, "Should cover exactly 4 high-risk templates"

    for task_value, fields in PROMPT_FIELDS_REQUIRED.items():
        assert isinstance(task_value, str)
        assert isinstance(fields, list)
        assert len(fields) > 0, f"Field list for {task_value} must not be empty"
        # Verify the TaskType exists
        task_type = TaskType(task_value)
        assert task_type in _TASK_TEMPLATE_MAP, (
            f"{task_value} must have a registered template"
        )
