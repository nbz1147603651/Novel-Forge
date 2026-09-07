"""Shared output-format contracts between prompts and response parsers.

This module defines task-level output contracts so prompt-side constraints and
parser-side validation stay aligned.
"""

from __future__ import annotations

import re
from copy import deepcopy
from dataclasses import dataclass, replace
from enum import Enum
from typing import Any

from pydantic import BaseModel

from novel_forge.common.constants import TaskType
from novel_forge.core.authoring import AuthoringReply
from novel_forge.core.domain.character_boundary import canonical_character_names


class OutputKind(str, Enum):
    """Supported high-level output kinds."""

    JSON = "json"
    TEXT = "text"


class ContractMode(str, Enum):
    """Semantic output contract mode used by prompt and parser layers."""

    FULL_OBJECT = "full_object"
    PARTIAL_OBJECT = "partial_object"
    PATCH_PLAN = "patch_plan"
    FRAGMENT_OBJECT = "fragment_object"
    TEXT_ONLY = "text_only"


@dataclass(frozen=True)
class TaskFormatContract:
    """Output-format contract for one task type."""

    output_kind: OutputKind
    required_top_level_keys: tuple[str, ...] = ()
    enforce_required_keys: bool = False
    allowed_top_level_keys: tuple[str, ...] = ()
    schema_model: str = ""
    json_schema: dict[str, Any] | None = None
    response_schema_model: type[BaseModel] | None = None
    schema_source: str = ""
    schema_strength: str = ""
    text_policy: str = "default"
    task_family: str = "generic"
    strict_mode: bool = True
    contract_mode: ContractMode | None = None
    require_native_structured_output: bool = False

    @property
    def contract_id(self) -> str:
        """Stable identifier used in prompt telemetry."""
        suffix = self.schema_model or self.output_kind.value
        return f"{self.task_family}:{suffix}"

    @property
    def effective_contract_mode(self) -> ContractMode:
        """Return the semantic contract mode for this contract."""
        if self.contract_mode is not None:
            return self.contract_mode
        if self.output_kind == OutputKind.TEXT:
            return ContractMode.TEXT_ONLY
        if not self.enforce_required_keys:
            return ContractMode.PARTIAL_OBJECT
        return ContractMode.FULL_OBJECT


@dataclass(frozen=True)
class FormatSchemaIssue:
    """Machine-readable schema/contract validation issue."""

    path: str
    issue_type: str
    expected: str = ""
    actual: str = ""
    message: str = ""

    def as_dict(self) -> dict[str, str]:
        """Return a JSON-safe representation for logs and retry prompts."""

        return {
            "path": self.path,
            "issue_type": self.issue_type,
            "expected": self.expected,
            "actual": self.actual,
            "message": self.message,
        }


class TextOutputContractError(ValueError):
    """Raised when a TEXT task returns an output that is not usable prose."""


_TEXT_JSON_STARTS = ("{", "[")
_TEXT_JSON_ENDS = ("}", "]")
_TEXT_HEADING_RE = r"^\s{0,3}#{1,6}\s+\S+"
_TEXT_LEADING_HEADING_RE = re.compile(
    r"\A(?:\ufeff)?(?:[ \t]*\r?\n)*[ \t]{0,3}#{1,6}[ \t]+[^\r\n]*(?:\r?\n|$)"
)
_TEXT_LIST_RE = r"^\s*(?:[-*+]\s+|\d+[.)、]\s+)"
_TEXT_BLOCKED_PATTERNS = (
    "scene_intent",
    "scene_id",
    "opening_contract",
    "opening_bridge",
    "closing_contract",
    "required_outcome",
    "required_state_transitions",
    "required_literals",
    "exit_target_state",
    "entry_state_refs",
    "sensory_notes",
    "emotional_beat",
    "bridge_summary",
    "输出格式",
    "只返回 JSON",
    "本章目标",
    "承接上章",
)

_SHORT_CONFIG_KEYS = (
    "characters_hint",
    "conflict_hint",
    "ending_style",
    "extra_instructions",
    "genre",
    "language",
    "length_target",
    "max_edit_rounds",
    "opening_style",
    "pov_hint",
    "project_id",
    "theme",
    "title",
    "tone",
    "world_hint",
    "writing_mode",
)
_LONG_CONFIG_KEYS = (
    "chapters_per_volume",
    "characters_hint",
    "conflict_hint",
    "ending_style",
    "extra_instructions",
    "genre",
    "language",
    "opening_style",
    "polish_hint",
    "pov_hint",
    "premise",
    "project_id",
    "title",
    "tone",
    "total_chapters",
    "volume_mode",
    "words_per_chapter",
    "world_hint",
)
_AI_CONFIG_METADATA_KEYS = ("polish_suggestions", "creative_note")
_SPEC_ENRICH_KEYS = (
    "title",
    "genre",
    "theme",
    "tone",
    "length_target",
    "language",
    "characters_hint",
    "world_hint",
    "conflict_hint",
    "pov_hint",
    "opening_style",
    "ending_style",
    "extra_instructions",
)
_BLUEPRINT_ELEMENT_SELECT_KEYS = (
    "genre_inference",
    "required_ids",
    "extension_ids",
    "extension_selection",
    "focus_constraints",
    "selector_summary",
)
_RUNTIME_METADATA_SCHEMA_KEYS = frozenset({"schema_version", "created_at"})
_PROMPT_SCHEMA_METADATA_KEYS = (
    "$schema",
    "type",
    "properties",
    "items",
    "required",
    "additionalProperties",
    "minItems",
    "maxItems",
)


def _strip_outer_markdown_fence(text: str) -> str:
    stripped = str(text or "").strip()
    if not stripped.startswith("```") or not stripped.endswith("```"):
        return stripped
    lines = stripped.splitlines()
    if len(lines) < 2:
        return stripped
    if not lines[0].lstrip().startswith("```"):
        return stripped
    if not lines[-1].strip().endswith("```"):
        return stripped
    return "\n".join(lines[1:-1]).strip()


def _looks_like_json_container(text: str) -> bool:
    stripped = str(text or "").strip()
    return (
        len(stripped) >= 2 and stripped[0] in _TEXT_JSON_STARTS and stripped[-1] in _TEXT_JSON_ENDS
    )


def _simple_object_schema(contract: TaskFormatContract) -> dict[str, Any]:
    """Build a minimal JSON Schema from a task contract."""
    if contract.output_kind != OutputKind.JSON:
        return {}

    keys = tuple(
        dict.fromkeys((*contract.required_top_level_keys, *contract.allowed_top_level_keys))
    )
    schema: dict[str, Any] = {
        "type": "object",
        "required": list(contract.required_top_level_keys),
        "properties": {key: {} for key in keys},
    }
    if contract.allowed_top_level_keys:
        schema["additionalProperties"] = False
    return schema


def _schema_has_shape(schema: Any) -> bool:
    """Return True when a schema node carries useful type/shape information."""
    if not isinstance(schema, dict):
        return False
    return any(
        key in schema for key in ("type", "$ref", "anyOf", "oneOf", "allOf", "items", "properties")
    )


def _collect_local_ref_names(value: Any, *, defs: dict[str, Any]) -> set[str]:
    """Collect local #/$defs references reachable from *value*."""
    pending: list[str] = []
    seen: set[str] = set()

    def visit(node: Any) -> None:
        if isinstance(node, dict):
            ref = node.get("$ref")
            if isinstance(ref, str) and ref.startswith("#/$defs/"):
                name = ref.rsplit("/", 1)[-1]
                if name not in seen and name in defs:
                    pending.append(name)
            for child in node.values():
                visit(child)
        elif isinstance(node, list):
            for child in node:
                visit(child)

    visit(value)
    while pending:
        name = pending.pop()
        if name in seen:
            continue
        seen.add(name)
        visit(defs.get(name))
    return seen


def _prune_json_schema_defs(schema: dict[str, Any]) -> dict[str, Any]:
    """Keep only local JSON Schema definitions referenced by selected properties."""
    defs = schema.get("$defs")
    if not isinstance(defs, dict):
        return schema

    body = {key: value for key, value in schema.items() if key != "$defs"}
    needed = _collect_local_ref_names(body, defs=defs)
    if not needed:
        return body

    pruned = dict(body)
    pruned["$defs"] = {name: defs[name] for name in sorted(needed) if name in defs}
    return pruned


def _schema_for_prompt_display(
    schema: dict[str, Any],
    *,
    protected_top_level_keys: set[str] | None = None,
) -> dict[str, Any]:
    """Return a prompt-facing schema stripped of keys that LLMs are likely to
    parrot back as data.

    Strips runtime metadata (``schema_version`` / ``created_at``) and the
    four JSON-Schema constraint keywords (``type`` / ``required`` /
    ``additionalProperties`` / ``minItems``) **only on schema-metadata
    nodes** — dicts that look like a JSON-Schema descriptor (containing
    ``properties`` / ``items`` / ``$ref`` / ``oneOf`` / ``anyOf`` /
    ``allOf``). On primitive leaf nodes (``{"type": "string"}``) the
    ``type`` is preserved so the LLM still sees field type guidance.

    Field names declared inside ``properties`` are user-controlled and
    are never treated as schema keywords, even when they happen to be
    named ``type`` / ``required`` / etc.
    """
    cleaned = deepcopy(schema)
    protected_root = protected_top_level_keys or set()
    runtime_meta = set(_RUNTIME_METADATA_SCHEMA_KEYS)
    parrot_keys = {"type", "required", "additionalProperties", "minItems"}
    schema_node_markers = {
        "properties",
        "items",
        "$ref",
        "oneOf",
        "anyOf",
        "allOf",
    }

    def is_schema_metadata_node(node: dict[str, Any]) -> bool:
        return any(marker in node for marker in schema_node_markers)

    def visit_schema_node(node: dict[str, Any], *, is_root: bool) -> None:
        blocked = runtime_meta.copy()
        if is_schema_metadata_node(node):
            blocked |= parrot_keys - (protected_root if is_root else set())
        for key in list(node.keys()):
            if key in blocked:
                node.pop(key, None)

        properties = node.get("properties")
        if isinstance(properties, dict):
            for meta_field in runtime_meta:
                properties.pop(meta_field, None)
            required = node.get("required")
            if isinstance(required, list):
                node["required"] = [
                    key for key in required if not (isinstance(key, str) and key in runtime_meta)
                ]
            for child in properties.values():
                if isinstance(child, dict):
                    visit_schema_node(child, is_root=False)

        items = node.get("items")
        if isinstance(items, dict):
            visit_schema_node(items, is_root=False)
        elif isinstance(items, list):
            for child in items:
                if isinstance(child, dict):
                    visit_schema_node(child, is_root=False)

        for combinator in ("oneOf", "anyOf", "allOf"):
            children = node.get(combinator)
            if isinstance(children, list):
                for child in children:
                    if isinstance(child, dict):
                        visit_schema_node(child, is_root=False)

        defs = node.get("$defs") or node.get("definitions")
        if isinstance(defs, dict):
            for child in defs.values():
                if isinstance(child, dict):
                    visit_schema_node(child, is_root=False)

    if isinstance(cleaned, dict):
        visit_schema_node(cleaned, is_root=True)
    return cleaned


def _schema_with_contract_keys(
    contract: TaskFormatContract,
    *,
    base_schema: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Overlay task-contract top-level keys onto a typed base schema."""
    if contract.output_kind != OutputKind.JSON:
        return {}
    if not base_schema:
        return _simple_object_schema(contract)

    schema = deepcopy(base_schema)
    schema.setdefault("type", "object")
    properties = dict(schema.get("properties") or {})
    # Preserve the explicitly declared allowed-key order in provider schemas.
    # It is also the canonical order rendered into prompts and checked by the
    # format-contract verifier.  Required-only contracts retain their required
    # order until they are explicitly promoted to a closed envelope.
    contract_keys = (
        tuple(dict.fromkeys((*contract.allowed_top_level_keys, *contract.required_top_level_keys)))
        if contract.allowed_top_level_keys
        else contract.required_top_level_keys
    )
    if contract_keys:
        properties = {key: properties.get(key, {}) for key in contract_keys}
    schema["properties"] = properties
    schema["required"] = list(contract.required_top_level_keys)
    if contract.allowed_top_level_keys:
        schema["additionalProperties"] = False
    return _prune_json_schema_defs(schema)


def _response_schema_model_for_task(task_type: TaskType) -> type[BaseModel] | None:
    """Return the response-envelope Pydantic model for a task when one exists."""
    try:
        from novel_forge.core.parsing.response_schemas import get_response_schema
    except Exception:
        return None

    return get_response_schema(task_type)


def _schema_from_response_schema_model(
    model: type[BaseModel] | None,
) -> dict[str, Any] | None:
    """Return a model JSON schema without letting model failures escape."""
    if model is None:
        return None
    try:
        schema = model.model_json_schema()
    except Exception:
        return None
    return schema if isinstance(schema, dict) else None


def _response_schema_for_task(task_type: TaskType) -> dict[str, Any] | None:
    """Return the response-envelope JSON Schema for a task when one exists."""
    return _schema_from_response_schema_model(_response_schema_model_for_task(task_type))


_STRICT_RESPONSE_MODEL_TOP_LEVEL_TASKS: frozenset[TaskType] = frozenset(
    {
        # Canonical initialization artifacts are consumed as the bounded source
        # of truth for all later planning and chapter stages.  Do not let model
        # commentary or undeclared metadata cross this artifact boundary.
        TaskType.INIT_STORY_BIBLE,
        TaskType.INIT_CREATIVE_DIRECTION_CANDIDATES,
        TaskType.INIT_CREATIVE_DIRECTION_SELECT,
        TaskType.INIT_ENTITY_REGISTRY,
        TaskType.INIT_NARRATIVE_CONTRACT,
        TaskType.PLAN_OUTLINE,
        TaskType.PLAN_OUTLINE_BATCH,
        TaskType.PLAN_OUTLINE_CONTINUE,
        TaskType.PLAN_CHAPTER,
        TaskType.PLAN_CHAPTER_SCENES,
        TaskType.PLAN_CHAPTER_CONTRACTS,
        # These are the long-chapter lifecycle handoffs.  A field that is not
        # declared here must not silently enter the next stage, the repair
        # ledger, or canonical state through Pydantic's permissive extras.
        TaskType.BRIDGE_CHAPTER,
        TaskType.CHECK_CHAPTER,
        TaskType.CHECK_ALIGNMENT,
        TaskType.CHECK_CONTINUITY,
        TaskType.VALIDATE_CAUSAL,
        TaskType.EVALUATE_READING_POWER,
        TaskType.EXTRACT_CANON,
        TaskType.EXTRACT_CHAPTER_SUMMARY_EXIT,
        TaskType.EXTRACT_CANON_DELTA,
        TaskType.EXTRACT_CREATIVE_REPORT,
        TaskType.EXTRACT_CHARACTER_STATE_DELTAS,
        TaskType.EXTRACT_RELATIONSHIP_DELTAS,
        TaskType.EXTRACT_PLOT_THREAD_DELTAS,
        TaskType.EXTRACT_EXPRESSION_OBSERVATIONS,
        # Initialization repair patches can modify the artifacts consumed by
        # every later chapter. Keep their envelope closed just like the
        # planning contracts; debug/prose spillover must not cross this
        # upstream repair boundary.
        TaskType.REPAIR_INIT_ARTIFACT_PATCH,
        TaskType.ADJUDICATE_CONTRACT_COHERENCE,
        TaskType.ADJUDICATE_CONTRACT_COMPLETION,
        TaskType.ADJUDICATE_STATE_DELTA,
        TaskType.ADJUDICATE_FACT_CONFLICT,
        TaskType.ADJUDICATE_FINAL_STATE,
    }
)


def _model_field_names(model: type[BaseModel] | None) -> tuple[str, ...]:
    if model is None:
        return ()
    return tuple(str(name) for name in model.model_fields)


def _schema_property_names(schema: dict[str, Any] | None) -> tuple[str, ...]:
    if not isinstance(schema, dict):
        return ()
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        return ()
    return tuple(str(name) for name in properties)


def _ordered_contract_keys(
    required_keys: tuple[str, ...],
    allowed_keys: tuple[str, ...],
) -> tuple[str, ...]:
    return tuple(dict.fromkeys((*required_keys, *allowed_keys)))


def _resolved_contract_mode(contract: TaskFormatContract) -> ContractMode:
    return contract.effective_contract_mode


def _infer_contract_schema_source(
    contract: TaskFormatContract,
    response_schema_model: type[BaseModel] | None,
) -> str:
    if contract.schema_source:
        return contract.schema_source
    if response_schema_model is not None:
        return "pydantic_model"
    if contract.json_schema is not None:
        return "explicit_json_schema"
    if contract.output_kind == OutputKind.JSON:
        return "generated_contract_schema"
    return ""


def _infer_contract_schema_strength(
    contract: TaskFormatContract,
    response_schema_model: type[BaseModel] | None,
) -> str:
    if contract.schema_strength:
        return contract.schema_strength
    if contract.output_kind != OutputKind.JSON:
        return ""
    if contract.allowed_top_level_keys or _schema_restricts_top_level_object(contract.json_schema):
        return "strong"
    if response_schema_model is not None or contract.json_schema is not None:
        return "medium"
    return "weak"


def _enrich_task_format_contract(
    task_type: TaskType,
    contract: TaskFormatContract | None,
) -> TaskFormatContract | None:
    """Attach schema metadata derived from the central response-schema registry."""

    if contract is None or contract.output_kind != OutputKind.JSON:
        return contract

    response_schema_model = contract.response_schema_model
    if response_schema_model is None and (
        contract.json_schema is None or task_type in _STRICT_RESPONSE_MODEL_TOP_LEVEL_TASKS
    ):
        response_schema_model = _response_schema_model_for_task(task_type)
    allowed_top_level_keys = contract.allowed_top_level_keys
    if (
        not allowed_top_level_keys
        and response_schema_model is not None
        and task_type in _STRICT_RESPONSE_MODEL_TOP_LEVEL_TASKS
    ):
        allowed_top_level_keys = _schema_property_names(contract.json_schema)
        if not allowed_top_level_keys:
            allowed_top_level_keys = _ordered_contract_keys(
                contract.required_top_level_keys,
                _model_field_names(response_schema_model),
            )

    enriched = replace(
        contract,
        response_schema_model=response_schema_model,
        allowed_top_level_keys=allowed_top_level_keys,
        contract_mode=_resolved_contract_mode(contract),
    )
    return replace(
        enriched,
        schema_source=_infer_contract_schema_source(enriched, response_schema_model),
        schema_strength=_infer_contract_schema_strength(enriched, response_schema_model),
    )


def _blueprint_fragment_base_schema() -> dict[str, Any] | None:
    """Return the canonical NarrativeBlueprint schema for fragment contracts."""
    try:
        from novel_forge.core.schemas.outline import NarrativeBlueprint
    except Exception:
        return None

    try:
        schema = NarrativeBlueprint.model_json_schema()
    except Exception:
        return None
    return schema if isinstance(schema, dict) else None


def _plan_outline_fragment_response_model(block_key: str) -> type[BaseModel] | None:
    """Return a strong fragment envelope model for PLAN_OUTLINE blocks."""

    try:
        from novel_forge.core.schemas.outline import (
            PlanOutlineCharacterArcsFragment,
            PlanOutlineEndingFragment,
            PlanOutlineOverviewFragment,
            PlanOutlinePhasesFragment,
            PlanOutlineSubplotsFragment,
            PlanOutlineSuspenseFragment,
            PlanOutlineTurningPointsFragment,
        )
    except Exception:
        return None

    models: dict[str, type[BaseModel]] = {
        "overview": PlanOutlineOverviewFragment,
        "phases": PlanOutlinePhasesFragment,
        "turning_points": PlanOutlineTurningPointsFragment,
        "character_arcs": PlanOutlineCharacterArcsFragment,
        "subplots": PlanOutlineSubplotsFragment,
        "suspense": PlanOutlineSuspenseFragment,
        "ending": PlanOutlineEndingFragment,
    }
    return models.get(str(block_key or "").strip())


def _chapter_contracts_schema() -> dict[str, Any] | None:
    """Return envelope JSON Schema for ``PLAN_CHAPTER_CONTRACTS``.

    Wraps ``ChapterContract.model_json_schema()`` as the *items* schema of
    the ``chapter_contracts`` array so the LLM output is constrained at
    generation time — not only validated at repair time.
    """
    try:
        from novel_forge.narrative_state.schemas import ChapterContract
    except Exception:
        return None

    try:
        item_schema = _prune_json_schema_defs(ChapterContract.model_json_schema())
    except Exception:
        return None
    if not isinstance(item_schema, dict):
        return None
    return _typed_object_schema(
        required=("chapter_contracts",),
        properties={"chapter_contracts": _array_of(item_schema)},
        additional_properties=False,
    )


def _audit_report_v2_compat_schema() -> dict[str, Any]:
    """Return the public audit_v2 envelope plus legacy init repair compatibility keys."""
    locator_schema = {
        "type": "object",
        "required": ["target_format", "role"],
        "properties": {
            "target_format": {
                "type": "string",
                "enum": [
                    "json_artifact",
                    "prose_text",
                    "markdown_document",
                    "prompt_response",
                    "multi_chapter_set",
                    "manual_only",
                ],
            },
            "role": {"type": "string", "enum": ["repair", "reference"]},
            "surface": {"type": "string"},
            "artifact": {"type": "string"},
            "json_pointer": {"type": "string"},
            "field": {"type": "string"},
            "chapter_number": {"type": ["integer", "null"]},
            "chapter_range": {"type": "array", "items": {"type": "integer"}},
            "claim_id": {"type": "string"},
            "expected_type": {"type": "string"},
            "task_type": {"type": "string"},
            "contract_path": {"type": "string"},
            "missing_key": {"type": "string"},
            "schema_error": {"type": "string"},
            "scene_id": {"type": "string"},
            "paragraph_start": {"type": "integer"},
            "paragraph_end": {"type": "integer"},
            "quote": {"type": "string"},
            "context_before": {"type": "string"},
            "context_after": {"type": "string"},
            "text_hash": {"type": "string"},
            "heading_path": {"type": "array", "items": {"type": "string"}},
            "section_index": {"type": ["integer", "null"]},
            "issue_thread_id": {"type": "string"},
            "chapter_set": {"type": "array", "items": {"type": "integer"}},
            "evidence_pairs": {"type": "array", "items": {"type": "object"}},
            "manual_review_reason": {"type": "string"},
            "confidence": {"type": "number"},
        },
        "additionalProperties": True,
    }
    evidence_schema = {
        "type": "object",
        "required": ["quote", "source"],
        "properties": {
            "quote": {"type": "string"},
            "source": {"type": "string"},
            "locator": locator_schema,
            "confidence": {"type": "number"},
        },
        "additionalProperties": True,
    }
    issue_schema = {
        "type": "object",
        "required": [
            "issue_id",
            "issue_type",
            "severity",
            "blocking",
            "summary",
            "description",
            "evidence",
            "repair_targets",
            "reference_targets",
            "repair_intent",
        ],
        "properties": {
            "issue_id": {"type": "string"},
            "id": {"type": "string"},
            "dimension": {"type": "string"},
            "issue_type": {"type": "string"},
            "severity": {"type": "string", "enum": ["critical", "high", "medium", "low"]},
            "blocking": {"type": "boolean"},
            "summary": {"type": "string"},
            "description": {"type": "string"},
            "evidence": {"type": "array", "items": evidence_schema},
            "repair_targets": {"type": "array", "items": locator_schema},
            "reference_targets": {"type": "array", "items": locator_schema},
            "repair_intent": {
                "type": "object",
                "required": ["operation", "target_policy", "rationale"],
                "properties": {
                    "operation": {
                        "type": "string",
                        "enum": [
                            "replace",
                            "field_replace",
                            "window_rewrite",
                            "json_patch",
                            "schema_patch",
                            "split_targets",
                            "manual_review",
                        ],
                    },
                    "target_policy": {"type": "string"},
                    "rationale": {"type": "string"},
                    "preserve": {"type": "array", "items": {"type": "string"}},
                    "allowed_strategies": {"type": "array", "items": {"type": "string"}},
                },
                "additionalProperties": True,
            },
            "postconditions": {"type": "array", "items": {"type": "object"}},
            "metadata": {"type": "object"},
        },
        "additionalProperties": True,
    }
    return {
        "type": "object",
        "required": [
            "schema_version",
            "dimension",
            "verdict",
            "score",
            "issues",
            "summary",
            "metadata",
            "source_refs",
            "repair_scope",
            "preserve",
            "change_intent",
            "blocked",
        ],
        "properties": {
            "schema_version": {"type": "string", "const": "audit_v2"},
            "dimension": {"type": "string"},
            "verdict": {
                "type": "string",
                "enum": ["accept", "needs_repair", "reject", "ambiguous", "defer"],
            },
            "score": {"type": "number"},
            "issues": {"type": "array", "items": issue_schema},
            "summary": {"type": "string"},
            "metadata": {"type": "object"},
            "source_refs": {"type": "array"},
            "repair_scope": {"type": "array"},
            "preserve": {"type": "array"},
            "change_intent": {"type": "string"},
            "blocked": {"type": "boolean"},
        },
        "additionalProperties": True,
    }


def _typed_contract_schema(
    task_type: TaskType,
    contract: TaskFormatContract,
    *,
    base_schema: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the prompt/runtime JSON Schema for a task contract."""
    schema = (
        base_schema
        if base_schema is not None
        else _schema_from_response_schema_model(contract.response_schema_model)
        or _response_schema_for_task(task_type)
    )
    return _schema_with_contract_keys(contract, base_schema=schema)


def _character_voice_whitelist_from_context(context: dict[str, Any] | None) -> list[str]:
    if not isinstance(context, dict):
        return []

    explicit = list(canonical_character_names(context.get("voice_character_whitelist")))
    if explicit:
        return explicit
    return list(canonical_character_names(context.get("character_bible")))


def _editorial_character_voices_schema_for_context(
    schema: dict[str, Any],
    context: dict[str, Any] | None,
) -> dict[str, Any]:
    dynamic_schema = deepcopy(schema)
    names = _character_voice_whitelist_from_context(context)
    if not names:
        return dynamic_schema
    try:
        voice_item_schema = dynamic_schema["properties"]["character_voices"]["items"]
        voice_item_schema["properties"]["character"] = {"type": "string", "enum": names}
    except (KeyError, TypeError):
        return dynamic_schema
    return dynamic_schema


def _character_relationship_matrix_schema(
    context: dict[str, Any] | None,
) -> dict[str, Any]:
    """Return a typed relationship schema with a strict repair-time name boundary."""

    endpoint_schema: dict[str, Any] = {"type": "string", "minLength": 1}
    names = list(canonical_character_names(context.get("character_roster", []))) if context else []
    if (
        names
        and isinstance(context, dict)
        and context.get("relationship_generation_phase") == "repair"
    ):
        endpoint_schema = {"type": "string", "enum": names}
    item_schema = _typed_object_schema(
        required=(
            "character_a",
            "character_b",
            "relation_type",
            "description",
            "confidence",
        ),
        properties={
            "character_a": deepcopy(endpoint_schema),
            "character_b": deepcopy(endpoint_schema),
            "relation_type": _string_enum_schema(
                "relationship",
                "romantic_tension",
                "family",
                "mentor_student",
                "professional",
                "alliance",
                "rivalry",
                "antagonism",
                "community",
                "identity_link",
            ),
            "identity_link_type": _string_enum_schema(
                "",
                "reincarnation_of",
                "mistaken_as",
                "alias_of",
                "related_to",
            ),
            "description": {"type": "string", "minLength": 1},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        },
        additional_properties=False,
    )
    return _typed_object_schema(
        required=("relationship_matrix",),
        properties={"relationship_matrix": _array_of(item_schema)},
        additional_properties=False,
    )


def _character_arc_plan_schema() -> dict[str, Any]:
    item_schema = _typed_object_schema(
        required=("name", "arc"),
        properties={
            "name": {"type": "string", "minLength": 1},
            "arc": {"type": "string", "minLength": 1},
        },
        additional_properties=True,
    )
    return _typed_object_schema(
        required=("character_arcs",),
        properties={"character_arcs": _array_of(item_schema)},
        additional_properties=False,
    )


def _known_character_names_from_context(context: dict[str, Any] | None) -> list[str]:
    if not context:
        return []
    for key in ("character_roster", "known_characters"):
        names = canonical_character_names(context.get(key, []))
        if names:
            return list(names)
    character_bible = context.get("character_bible")
    return list(canonical_character_names(character_bible))


def _character_state_deltas_schema(context: dict[str, Any] | None) -> dict[str, Any]:
    names = _known_character_names_from_context(context)
    name_schema: dict[str, Any] = {"type": "string", "minLength": 1}
    if names:
        name_schema = {"type": "string", "enum": names}
    return _typed_object_schema(
        required=("character_state_deltas",),
        properties={
            "character_state_deltas": _array_of(
                _typed_object_schema(
                    required=("name",),
                    properties={"name": name_schema},
                    additional_properties=True,
                )
            )
        },
        additional_properties=False,
    )


def _relationship_deltas_schema(context: dict[str, Any] | None) -> dict[str, Any]:
    names = _known_character_names_from_context(context)
    name_schema: dict[str, Any] = {"type": "string", "minLength": 1}
    if names:
        name_schema = {"type": "string", "enum": names}
    relationship_schema = _typed_object_schema(
        required=("characters",),
        properties={
            "characters": {
                "type": "array",
                "items": name_schema,
                "minItems": 2,
                "maxItems": 2,
            }
        },
        additional_properties=True,
    )
    return _typed_object_schema(
        required=("relationship_deltas",),
        properties={
            "relationship_deltas": _array_of(
                _typed_object_schema(
                    required=("relationship",),
                    properties={"relationship": relationship_schema},
                    additional_properties=True,
                )
            )
        },
        additional_properties=False,
    )


def _extract_canon_schema(
    contract: TaskFormatContract,
    context: dict[str, Any] | None,
) -> dict[str, Any]:
    properties: dict[str, Any] = {key: {} for key in contract.required_top_level_keys}
    properties["character_state_deltas"] = _character_state_deltas_schema(context)["properties"][
        "character_state_deltas"
    ]
    properties["relationship_deltas"] = _relationship_deltas_schema(context)["properties"][
        "relationship_deltas"
    ]
    return _typed_object_schema(
        required=contract.required_top_level_keys,
        properties=properties,
        # Task adapters add normalized chapter_number/source fields before validation.
        additional_properties=True,
    )


def effective_contract_json_schema(
    task_type: TaskType,
    contract: TaskFormatContract | None,
    context: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Return the runtime JSON Schema for a resolved task contract.

    The helper keeps the existing TaskFormatContract top-level key overlay and
    pruning behavior even when the schema source is a Pydantic response model.
    """
    if contract is None or contract.output_kind != OutputKind.JSON:
        return None

    if task_type == TaskType.INIT_CHARACTER_RELATIONSHIP_MATRIX:
        return _character_relationship_matrix_schema(context)
    if task_type == TaskType.INIT_CHARACTER_ARC_PLAN:
        return _character_arc_plan_schema()
    if task_type == TaskType.EXTRACT_CHARACTER_STATE_DELTAS:
        return _character_state_deltas_schema(context)
    if task_type == TaskType.EXTRACT_RELATIONSHIP_DELTAS:
        return _relationship_deltas_schema(context)
    if task_type == TaskType.EXTRACT_CANON:
        schema = _extract_canon_schema(contract, context)
        return (
            _schema_with_contract_keys(contract, base_schema=schema)
            if contract.allowed_top_level_keys
            else schema
        )

    if contract.json_schema is not None:
        if task_type == TaskType.DERIVE_EDITORIAL_CHARACTER_VOICES:
            schema = _editorial_character_voices_schema_for_context(
                contract.json_schema,
                context,
            )
        else:
            schema = deepcopy(contract.json_schema)
        # Explicit schemas historically bypassed the contract-key overlay.
        # That let a contract whose envelope was already marked closed by the
        # response-model registry advertise ``additionalProperties: true`` to
        # provider-native structured output.  Overlay only when the contract
        # owns an allowed-key set: schemas that intentionally expose optional
        # fields without a closed envelope retain their declared shape.
        if contract.allowed_top_level_keys:
            return _schema_with_contract_keys(contract, base_schema=schema)
        return schema

    model_schema = _schema_from_response_schema_model(contract.response_schema_model)
    if model_schema is not None:
        return _schema_with_contract_keys(contract, base_schema=model_schema)

    if task_type == TaskType.PLAN_OUTLINE:
        return _typed_contract_schema(
            task_type,
            contract,
            base_schema=_blueprint_fragment_base_schema(),
        )
    return _typed_contract_schema(task_type, contract)


def _typed_object_schema(
    *,
    required: tuple[str, ...],
    properties: dict[str, Any],
    additional_properties: bool = False,
) -> dict[str, Any]:
    """Build a JSON Schema object with explicit nested properties."""
    return {
        "type": "object",
        "required": list(required),
        "properties": properties,
        "additionalProperties": additional_properties,
    }


def _array_of(item_schema: dict[str, Any]) -> dict[str, Any]:
    return {"type": "array", "items": item_schema}


def _string_enum_schema(*values: str) -> dict[str, Any]:
    return {"type": "string", "enum": list(values)}


def _pattern_id_schema() -> dict[str, Any]:
    """Compact pattern ID schema for prompt and contract validation.

    ``HumanizeScanStep`` now asks the model to adjudicate local/library
    candidates rather than invent IDs, so the prompt contract only needs to
    reject prose-like drift while keeping custom library IDs valid.
    """
    return {
        "type": "string",
        "pattern": r"^[a-z][a-z0-9_]{1,64}$",
        "description": "Use the pattern_id from an input candidate.",
    }


_STRING_SCHEMA = {"type": "string"}
_OUTLINE_TITLE_SCHEMA = {
    "type": "string",
    "minLength": 2,
    "maxLength": 14,
    "pattern": r"^(?!.*(?:第\s*[\d一二三四五六七八九十百]+\s*章))(?!.*[，,。；;：:、！？!?\n\r])(?!.*(?:——|--|…)).+$",
}
_NUMBER_SCHEMA = {"type": "number"}
_INTEGER_SCHEMA = {"type": "integer"}
_NULLABLE_INTEGER_SCHEMA = {"type": ["integer", "null"]}
_BOOLEAN_SCHEMA = {"type": "boolean"}
_STRING_ARRAY_SCHEMA = {"type": "array", "items": _STRING_SCHEMA}
_INTEGER_ARRAY_SCHEMA = {"type": "array", "items": _INTEGER_SCHEMA}
_GENERIC_OBJECT_SCHEMA = {"type": "object"}
_SUMMARY_DRIFT_CHECK_SCHEMA = _typed_object_schema(
    required=(
        "issues",
        "facts_checked",
        "facts_missing",
        "facts_contradicted",
    ),
    properties={
        "issues": _array_of(_GENERIC_OBJECT_SCHEMA),
        "facts_checked": _INTEGER_SCHEMA,
        "facts_missing": _INTEGER_SCHEMA,
        "facts_contradicted": _INTEGER_SCHEMA,
        "summary": _STRING_SCHEMA,
    },
)
_CRITIC_CONTINUITY_ISSUE_SCHEMA = _typed_object_schema(
    required=("severity", "summary", "evidence", "suggested_fix", "confidence"),
    properties={
        "severity": _string_enum_schema("critical", "high", "medium", "low"),
        "summary": _STRING_SCHEMA,
        "evidence": _STRING_SCHEMA,
        "suggested_fix": _STRING_SCHEMA,
        "confidence": _NUMBER_SCHEMA,
        "location": _STRING_SCHEMA,
        "issue_type": _STRING_SCHEMA,
    },
)
_CRITIC_CONTINUITY_SCHEMA = _typed_object_schema(
    required=("issues",),
    properties={"issues": _array_of(_CRITIC_CONTINUITY_ISSUE_SCHEMA)},
)
_TITLE_POLICY_SCHEMA = _typed_object_schema(
    required=("max_reuse", "allowed_repeated_titles", "naming_strategy"),
    properties={
        "max_reuse": {"type": "integer", "minimum": 1, "maximum": 10},
        "allowed_repeated_titles": _STRING_ARRAY_SCHEMA,
        "naming_strategy": _STRING_SCHEMA,
    },
    additional_properties=False,
)
_EDITORIAL_SYMBOL_POLICY_SCHEMA = _typed_object_schema(
    required=(
        "symbol",
        "narrative_function",
        "explanation_policy",
        "escalation_rule",
        "max_explicit_explanations",
    ),
    properties={
        "symbol": {"type": "string", "minLength": 1},
        "narrative_function": {"type": "string", "minLength": 1},
        "explanation_policy": {
            "type": "string",
            "enum": [
                "never_explain",
                "explain_once",
                "explain_on_escalation",
                "free",
            ],
        },
        "escalation_rule": _STRING_SCHEMA,
        "max_explicit_explanations": {"type": "integer", "minimum": 0, "maximum": 10},
    },
    additional_properties=True,
)
_EDITORIAL_REVELATION_STEP_SCHEMA = _typed_object_schema(
    required=(
        "thread",
        "stage",
        "stage_order",
        "target_chapter",
        "trigger",
        "allowed_disclosure",
        "required_action_consequence",
    ),
    properties={
        "thread": {"type": "string", "minLength": 1},
        "stage": {"type": "string", "minLength": 1},
        "stage_order": {"type": "integer", "minimum": 1},
        "target_chapter": {"type": "integer", "minimum": 0},
        "trigger": {"type": "string", "minLength": 1},
        "allowed_disclosure": {"type": "string", "minLength": 1},
        "required_action_consequence": {"type": "string", "minLength": 1},
    },
    additional_properties=False,
)
_EDITORIAL_DENOUEMENT_BUDGET_SCHEMA = _typed_object_schema(
    required=(
        "expected_chapters",
        "max_confirmation_scenes",
        "required_new_functions",
        "forbidden_repeats",
    ),
    properties={
        "expected_chapters": {"type": "integer", "minimum": 0, "maximum": 80},
        "max_confirmation_scenes": {"type": "integer", "minimum": 0, "maximum": 20},
        "required_new_functions": {
            "type": "array",
            "items": _STRING_SCHEMA,
            "minItems": 1,
        },
        "forbidden_repeats": _STRING_ARRAY_SCHEMA,
    },
    additional_properties=False,
)
_EDITORIAL_CHARACTER_VOICE_PROFILE_SCHEMA = _typed_object_schema(
    required=(
        "character",
        "sentence_profile",
        "explanation_bias",
        "emotion_syntax",
        "signature_moves",
        "taboo_patterns",
        "sample_lines",
    ),
    properties={
        "character": {"type": "string", "minLength": 1},
        "sentence_profile": {"type": "string", "minLength": 1},
        "explanation_bias": {"type": "string", "minLength": 1},
        "emotion_syntax": {"type": "string", "minLength": 1},
        "signature_moves": _STRING_ARRAY_SCHEMA,
        "taboo_patterns": _STRING_ARRAY_SCHEMA,
        "sample_lines": _STRING_ARRAY_SCHEMA,
    },
    additional_properties=False,
)
_EDITORIAL_CHARACTER_VOICES_SCHEMA = _typed_object_schema(
    required=("character_voices",),
    properties={"character_voices": _array_of(_EDITORIAL_CHARACTER_VOICE_PROFILE_SCHEMA)},
    additional_properties=False,
)
_EDITORIAL_STRUCTURE_SCHEMA = _typed_object_schema(
    required=(
        "climax_markers",
        "denouement_budget",
        "revelation_ladder",
        "time_bridge_policies",
        "title_policy",
    ),
    properties={
        "climax_markers": _array_of(_GENERIC_OBJECT_SCHEMA),
        "denouement_budget": _EDITORIAL_DENOUEMENT_BUDGET_SCHEMA,
        "revelation_ladder": _array_of(_EDITORIAL_REVELATION_STEP_SCHEMA),
        "time_bridge_policies": _STRING_ARRAY_SCHEMA,
        "title_policy": _TITLE_POLICY_SCHEMA,
    },
    additional_properties=False,
)
_EDITORIAL_CONTRACT_SCHEMA = _typed_object_schema(
    required=(
        "character_voices",
        "climax_markers",
        "denouement_budget",
        "theme_policies",
        "symbol_policies",
        "scene_resistance_rules",
        "revelation_ladder",
        "editorial_element_directives",
        "time_bridge_policies",
        "title_policy",
    ),
    properties={
        "character_voices": _array_of(_GENERIC_OBJECT_SCHEMA),
        "climax_markers": _array_of(_GENERIC_OBJECT_SCHEMA),
        "denouement_budget": _EDITORIAL_DENOUEMENT_BUDGET_SCHEMA,
        "theme_policies": _STRING_ARRAY_SCHEMA,
        "symbol_policies": _array_of(_EDITORIAL_SYMBOL_POLICY_SCHEMA),
        "scene_resistance_rules": _array_of(_GENERIC_OBJECT_SCHEMA),
        "revelation_ladder": _array_of(_EDITORIAL_REVELATION_STEP_SCHEMA),
        "editorial_element_directives": _array_of(_GENERIC_OBJECT_SCHEMA),
        "time_bridge_policies": _STRING_ARRAY_SCHEMA,
        "title_policy": _TITLE_POLICY_SCHEMA,
        "project_title": _STRING_SCHEMA,
        "expression_channel_budget": {"type": "object", "additionalProperties": True},
        "expression_channel_profiles": _array_of(_GENERIC_OBJECT_SCHEMA),
        "body_signal_budget_per_high_emotion_scene": _INTEGER_SCHEMA,
        "forbidden_confirmation_phrases": _STRING_ARRAY_SCHEMA,
        "revision_priorities": _STRING_ARRAY_SCHEMA,
    },
    additional_properties=False,
)
_EDITORIAL_STYLE_CONSTRAINTS_SCHEMA = _typed_object_schema(
    required=(
        "theme_policies",
        "symbol_policies",
        "scene_resistance_rules",
        "expression_channel_budget",
        "expression_channel_profiles",
        "body_signal_budget_per_high_emotion_scene",
        "forbidden_confirmation_phrases",
        "revision_priorities",
    ),
    properties={
        "theme_policies": _STRING_ARRAY_SCHEMA,
        "symbol_policies": _array_of(_EDITORIAL_SYMBOL_POLICY_SCHEMA),
        "scene_resistance_rules": _array_of(_GENERIC_OBJECT_SCHEMA),
        "expression_channel_budget": {"type": ["object", "string"]},
        "expression_channel_profiles": _array_of(_GENERIC_OBJECT_SCHEMA),
        "body_signal_budget_per_high_emotion_scene": {"type": ["integer", "number", "string"]},
        "forbidden_confirmation_phrases": _STRING_ARRAY_SCHEMA,
        "revision_priorities": _STRING_ARRAY_SCHEMA,
    },
    additional_properties=False,
)
_DELTA_TYPE_SCHEMA = {
    "type": "string",
    "enum": [
        "event",
        "relationship",
        "item",
        "knowledge",
        "alias",
        "promise",
        "world_rule",
        "character_state",
        "other",
    ],
}
_ADJUDICATION_VERDICT_SCHEMA = {
    "type": "string",
    "enum": ["accept", "reject", "ambiguous", "needs_repair", "defer"],
}
_ADJUDICATION_SEVERITY_SCHEMA = {
    "type": "string",
    "enum": ["low", "medium", "high", "critical"],
}
_TARGET_COVERAGE_STATUS_SCHEMA = {
    "type": "string",
    "enum": ["covered", "partial", "not_covered", "overreached", "needs_repair"],
}
_ADJUDICATION_ISSUE_KIND_SCHEMA = {
    "type": "string",
    "enum": ["none", "evidence", "contract_overreach", "mapping", "format", "state_path", "other"],
}
_ADJUDICATION_REPAIR_KIND_SCHEMA = {
    "type": "string",
    "enum": ["none", "text", "mapping"],
}
_STATE_EVIDENCE_SPAN_SCHEMA = _typed_object_schema(
    required=("quote",),
    properties={
        "quote": _STRING_SCHEMA,
        "paragraph_index": _INTEGER_SCHEMA,
    },
)
_CANDIDATE_STATE_DELTA_SCHEMA = _typed_object_schema(
    required=("delta_type", "summary", "proposed_delta", "evidence"),
    properties={
        "candidate_id": _STRING_SCHEMA,
        "chapter_number": _INTEGER_SCHEMA,
        "delta_type": _DELTA_TYPE_SCHEMA,
        "summary": _STRING_SCHEMA,
        "entity_ids": _STRING_ARRAY_SCHEMA,
        "covered_target_ids": _STRING_ARRAY_SCHEMA,
        "proposed_delta": {"type": "object", "additionalProperties": True},
        "evidence": _array_of(_STATE_EVIDENCE_SPAN_SCHEMA),
        "extraction_notes": _STRING_SCHEMA,
    },
    additional_properties=True,
)
_CANDIDATE_STATE_DELTAS_SCHEMA = _typed_object_schema(
    required=("candidates",),
    properties={"candidates": _array_of(_CANDIDATE_STATE_DELTA_SCHEMA)},
    additional_properties=True,
)
_ENTITY_REFERENCE_ADJUDICATION_ITEM_SCHEMA = _typed_object_schema(
    required=(
        "mention",
        "verdict",
        "selected_entity_id",
        "selected_canonical_name",
        "candidate_entity_ids",
        "evidence_refs",
        "rationale",
        "requires_independent_review",
    ),
    properties={
        "mention": _STRING_SCHEMA,
        "verdict": {
            "type": "string",
            "enum": ["resolved", "ambiguous", "new_entity", "insufficient_evidence"],
        },
        "selected_entity_id": _STRING_SCHEMA,
        "selected_canonical_name": _STRING_SCHEMA,
        "candidate_entity_ids": _STRING_ARRAY_SCHEMA,
        "evidence_refs": _STRING_ARRAY_SCHEMA,
        "rationale": _STRING_SCHEMA,
        "requires_independent_review": {"type": "boolean"},
    },
    additional_properties=False,
)
_ENTITY_REFERENCE_ADJUDICATION_SCHEMA = _typed_object_schema(
    required=("decisions", "summary"),
    properties={
        "decisions": _array_of(_ENTITY_REFERENCE_ADJUDICATION_ITEM_SCHEMA),
        "summary": _STRING_SCHEMA,
    },
    additional_properties=False,
)
_TTS_SCRIPT_SEGMENT_ADJUDICATION_ITEM_SCHEMA = _typed_object_schema(
    required=(
        "candidate_id",
        "segment_role",
        "verdict",
        "character_id",
        "confidence",
        "evidence",
        "rationale",
    ),
    properties={
        "candidate_id": _STRING_SCHEMA,
        "segment_role": _string_enum_schema("dialogue", "inner_thought", "narration", "ambiguous"),
        "verdict": _string_enum_schema("resolved", "ambiguous", "unknown"),
        "character_id": _STRING_SCHEMA,
        "confidence": _NUMBER_SCHEMA,
        "evidence": _STRING_SCHEMA,
        "rationale": _STRING_SCHEMA,
    },
    additional_properties=False,
)
_TTS_SCRIPT_SEGMENT_ADJUDICATION_SCHEMA = _typed_object_schema(
    required=("decisions", "summary"),
    properties={
        "decisions": _array_of(_TTS_SCRIPT_SEGMENT_ADJUDICATION_ITEM_SCHEMA),
        "summary": _STRING_SCHEMA,
    },
    additional_properties=False,
)
_TTS_DUBBING_REVIEW_TAG_SCHEMA = _typed_object_schema(
    required=("tag_type", "position", "duration_ms", "intensity", "description"),
    properties={
        "tag_type": _string_enum_schema(
            "pause",
            "silence",
            "emphasis",
            "stutter",
            "breath",
            "laugh",
            "chuckle",
            "cough",
            "clear_throat",
            "groan",
            "pant",
            "inhale",
            "exhale",
            "choke",
            "sniff",
            "sigh",
            "snort",
            "hum",
            "hiss",
            "hesitate",
            "sneeze",
        ),
        "position": _NUMBER_SCHEMA,
        "duration_ms": _INTEGER_SCHEMA,
        "intensity": _NUMBER_SCHEMA,
        "description": _STRING_SCHEMA,
    },
    additional_properties=False,
)
_TTS_DUBBING_REVIEW_DECISION_SCHEMA = _typed_object_schema(
    required=(
        "segment_index",
        "verdict",
        "issue_types",
        "confidence",
        "evidence",
        "rationale",
        "recommended_segment_type",
        "recommended_character_id",
        "recommended_emotion",
        "recommended_tone_hint",
        "recommended_paralinguistic_tags",
    ),
    properties={
        "segment_index": _INTEGER_SCHEMA,
        "verdict": _string_enum_schema("revise_performance", "manual_review"),
        "issue_types": {
            "type": "array",
            "items": _string_enum_schema(
                "speaker_ownership",
                "acoustic_role",
                "emotional_intent",
                "performance_naturalness",
                "tts_stability",
                "paralinguistic_overuse",
                "novel_text_advisory",
            ),
        },
        "confidence": _NUMBER_SCHEMA,
        "evidence": _STRING_SCHEMA,
        "rationale": _STRING_SCHEMA,
        "recommended_segment_type": _string_enum_schema(
            "unchanged", "narration", "dialogue", "inner_thought"
        ),
        "recommended_character_id": _STRING_SCHEMA,
        "recommended_emotion": _string_enum_schema(
            "unchanged",
            "neutral",
            "happy",
            "sad",
            "angry",
            "fearful",
            "surprised",
            "disgusted",
            "tender",
            "mocking",
            "whisper",
            "nostalgic",
            "anxious",
            "contempt",
            "determined",
            "playful",
        ),
        "recommended_tone_hint": _STRING_SCHEMA,
        "recommended_paralinguistic_tags": _array_of(_TTS_DUBBING_REVIEW_TAG_SCHEMA),
    },
    additional_properties=False,
)
_TTS_DUBBING_REVIEW_SCHEMA = _typed_object_schema(
    required=("decisions", "reviewed_segment_count", "overall_verdict", "summary"),
    properties={
        "decisions": _array_of(_TTS_DUBBING_REVIEW_DECISION_SCHEMA),
        "reviewed_segment_count": _INTEGER_SCHEMA,
        "overall_verdict": _string_enum_schema("passed", "needs_review"),
        "summary": _STRING_SCHEMA,
    },
    additional_properties=False,
)
_TTS_DUBBING_STYLE_PROFILE_SCHEMA = _typed_object_schema(
    required=(
        "language",
        "confidence",
        "narration_traits",
        "dialogue_traits",
        "rhythm_rules",
        "pause_rules",
        "performance_direction_rules",
        "sound_design_rules",
        "forbidden_tendencies",
    ),
    properties={
        "language": _STRING_SCHEMA,
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "narration_traits": _STRING_ARRAY_SCHEMA,
        "dialogue_traits": _STRING_ARRAY_SCHEMA,
        "rhythm_rules": _STRING_ARRAY_SCHEMA,
        "pause_rules": _STRING_ARRAY_SCHEMA,
        "performance_direction_rules": _STRING_ARRAY_SCHEMA,
        "sound_design_rules": _STRING_ARRAY_SCHEMA,
        "forbidden_tendencies": _STRING_ARRAY_SCHEMA,
    },
    additional_properties=False,
)
_TTS_SPOKEN_REWRITE_ITEM_SCHEMA = _typed_object_schema(
    required=("segment_index", "spoken_text", "confidence"),
    properties={
        "segment_index": _INTEGER_SCHEMA,
        "spoken_text": _STRING_SCHEMA,
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
    },
    additional_properties=False,
)
_TTS_SPOKEN_REWRITE_SCHEMA = _typed_object_schema(
    required=("rewrites",),
    properties={"rewrites": _array_of(_TTS_SPOKEN_REWRITE_ITEM_SCHEMA)},
    additional_properties=False,
)
_TTS_EMOTION_LABEL_ITEM_SCHEMA = _typed_object_schema(
    required=("segment_index", "emotion", "intensity"),
    properties={
        "segment_index": _INTEGER_SCHEMA,
        "emotion": _STRING_SCHEMA,
        "sub_emotion": {"type": ["string", "null"]},
        "intensity": {"type": "number", "minimum": 0, "maximum": 1},
    },
    additional_properties=False,
)
_TTS_EMOTION_LABEL_SCHEMA = _typed_object_schema(
    required=("emotions",),
    properties={"emotions": _array_of(_TTS_EMOTION_LABEL_ITEM_SCHEMA)},
    additional_properties=False,
)
_TTS_VOICE_MATCH_ADJUDICATION_ITEM_SCHEMA = _typed_object_schema(
    required=(
        "character_id",
        "verdict",
        "selected_voice_id",
        "audition_voice_ids",
        "confidence",
        "reason",
    ),
    properties={
        "character_id": _STRING_SCHEMA,
        "verdict": _string_enum_schema("approve", "review", "reject"),
        "selected_voice_id": _STRING_SCHEMA,
        "audition_voice_ids": _STRING_ARRAY_SCHEMA,
        "confidence": _NUMBER_SCHEMA,
        "reason": _STRING_SCHEMA,
    },
    additional_properties=False,
)
_TTS_VOICE_MATCH_ADJUDICATION_SCHEMA = _typed_object_schema(
    required=("decisions", "summary"),
    properties={
        "decisions": _array_of(_TTS_VOICE_MATCH_ADJUDICATION_ITEM_SCHEMA),
        "summary": _STRING_SCHEMA,
    },
    additional_properties=False,
)
_STATE_DELTA_ADJUDICATION_SCHEMA = _typed_object_schema(
    required=("candidate_id", "verdict", "severity", "rationale"),
    properties={
        "candidate_id": _STRING_SCHEMA,
        "verdict": _ADJUDICATION_VERDICT_SCHEMA,
        "severity": _ADJUDICATION_SEVERITY_SCHEMA,
        "confidence": _NUMBER_SCHEMA,
        "rationale": _STRING_SCHEMA,
        "covered_target_ids": _STRING_ARRAY_SCHEMA,
        "coverage_status": _TARGET_COVERAGE_STATUS_SCHEMA,
        "issue_kind": _ADJUDICATION_ISSUE_KIND_SCHEMA,
        "repair_kind": _ADJUDICATION_REPAIR_KIND_SCHEMA,
        "evidence_quotes": _STRING_ARRAY_SCHEMA,
        "affected_state_paths": _STRING_ARRAY_SCHEMA,
        "repair_instruction": _STRING_SCHEMA,
        "pending_reason": _STRING_SCHEMA,
    },
    additional_properties=True,
)
_FINAL_STATE_ADJUDICATION_SCHEMA = _typed_object_schema(
    required=(
        "verdict",
        "accepted_candidate_ids",
        "pending_candidate_ids",
        "repair_candidate_ids",
        "should_block_archive",
        "summary",
    ),
    properties={
        "chapter_number": _INTEGER_SCHEMA,
        "verdict": _ADJUDICATION_VERDICT_SCHEMA,
        "severity": _ADJUDICATION_SEVERITY_SCHEMA,
        "confidence": _NUMBER_SCHEMA,
        "accepted_candidate_ids": _STRING_ARRAY_SCHEMA,
        "rejected_candidate_ids": _STRING_ARRAY_SCHEMA,
        "pending_candidate_ids": _STRING_ARRAY_SCHEMA,
        "repair_candidate_ids": _STRING_ARRAY_SCHEMA,
        "state_updates": _array_of(_GENERIC_OBJECT_SCHEMA),
        "pending_items": _array_of(_GENERIC_OBJECT_SCHEMA),
        "repair_issues": _array_of(_GENERIC_OBJECT_SCHEMA),
        "target_coverage": _array_of(_GENERIC_OBJECT_SCHEMA),
        "coverage_matrix": _array_of(_GENERIC_OBJECT_SCHEMA),
        "should_block_archive": _BOOLEAN_SCHEMA,
        "summary": _STRING_SCHEMA,
    },
    additional_properties=True,
)
_CONTRACT_COMPLETION_REPAIR_DECISION_SCHEMA = {
    "type": "string",
    "enum": ["continue", "repair", "replan", "repair_or_replan"],
}
_CONTRACT_COMPLETION_ADJUDICATION_SCHEMA = _typed_object_schema(
    required=(
        "verdict",
        "severity",
        "rationale",
        "repair_or_replan_decision",
        "missing_required_progressions",
        "missing_knowledge_ops",
        "forbidden_progression_hits",
        "future_leak_hits",
        "evidence_quotes",
        "should_block_archive",
        "contract_completion_score",
    ),
    properties={
        "verdict": _ADJUDICATION_VERDICT_SCHEMA,
        "severity": _ADJUDICATION_SEVERITY_SCHEMA,
        "rationale": _STRING_SCHEMA,
        "repair_or_replan_decision": _CONTRACT_COMPLETION_REPAIR_DECISION_SCHEMA,
        "missing_required_progressions": _STRING_ARRAY_SCHEMA,
        "missing_knowledge_ops": _STRING_ARRAY_SCHEMA,
        "forbidden_progression_hits": _STRING_ARRAY_SCHEMA,
        "future_leak_hits": _STRING_ARRAY_SCHEMA,
        "cognitive_constraint_hits": _STRING_ARRAY_SCHEMA,
        "unexpected_progressions": _STRING_ARRAY_SCHEMA,
        "unaccepted_knowledge_ops": _STRING_ARRAY_SCHEMA,
        "evidence_quotes": _STRING_ARRAY_SCHEMA,
        "should_block_archive": _BOOLEAN_SCHEMA,
        "contract_completion_score": _NUMBER_SCHEMA,
    },
    additional_properties=False,
)


_MACRO_GUARD_ADJUSTED_CHAPTER_GOAL_SCHEMA = _typed_object_schema(
    required=("chapter_number", "goal"),
    properties={
        "chapter_number": _INTEGER_SCHEMA,
        "goal": _STRING_SCHEMA,
        "notes": _STRING_SCHEMA,
    },
)

_CONFIG_INTEGER_KEYS = frozenset(
    {
        "length_target",
        "max_edit_rounds",
        "total_chapters",
        "words_per_chapter",
        "chapters_per_volume",
    }
)
_CREATIVE_NOTE_SCHEMA = _typed_object_schema(
    required=(
        "core_pitch",
        "preserved_constraints",
        "field_rationales",
        "risks",
        "next_moves",
        "anti_drift_check",
    ),
    properties={
        "core_pitch": _STRING_SCHEMA,
        "design_intent": _STRING_SCHEMA,
        "preserved_constraints": _STRING_ARRAY_SCHEMA,
        "field_rationales": {"type": "object", "additionalProperties": _STRING_SCHEMA},
        "risks": _STRING_ARRAY_SCHEMA,
        "next_moves": _STRING_ARRAY_SCHEMA,
        "anti_drift_check": _STRING_SCHEMA,
    },
    additional_properties=False,
)

_CHARACTER_ROLE_SCHEMA = {
    "type": "string",
    "enum": ["protagonist", "antagonist", "deuteragonist", "supporting", "minor"],
}
_CHARACTER_STATUS_SCHEMA = {
    "type": "string",
    "enum": ["active", "dormant", "retired"],
}
_CHARACTER_TIME_LAYER_SCHEMA = {
    "type": "string",
    "enum": ["modern", "past", "cross_temporal", "memory_only", "default"],
}
_CHARACTER_RELATIONSHIPS_SCHEMA = {
    "type": "object",
    "additionalProperties": _STRING_SCHEMA,
}
_CHARACTER_VISUAL_IDENTITY_SCHEMA = _typed_object_schema(
    required=("facial_anchors", "silhouette", "body_language"),
    properties={
        "facial_anchors": _STRING_ARRAY_SCHEMA,
        "silhouette": _STRING_SCHEMA,
        "body_language": _STRING_SCHEMA,
        "costume_palette": _STRING_ARRAY_SCHEMA,
        "signature_props": _STRING_ARRAY_SCHEMA,
        "continuity_rules": _STRING_ARRAY_SCHEMA,
        "forbidden_drift": _STRING_ARRAY_SCHEMA,
    },
    additional_properties=False,
)
_TTS_VOICE_HINTS_SCHEMA = _typed_object_schema(
    required=("timbre", "register", "cadence"),
    properties={
        "timbre": _STRING_SCHEMA,
        "register": _STRING_SCHEMA,
        "cadence": _STRING_SCHEMA,
        "accent": _STRING_SCHEMA,
        "emotion_range": _STRING_ARRAY_SCHEMA,
        "pronunciation_notes": _STRING_ARRAY_SCHEMA,
    },
    additional_properties=False,
)
_ENRICH_CHARACTER_SCHEMA = _typed_object_schema(
    required=("appearance", "personality", "backstory", "arc"),
    properties={
        "appearance": _STRING_SCHEMA,
        "personality": _STRING_SCHEMA,
        "backstory": _STRING_SCHEMA,
        "arc": _STRING_SCHEMA,
        "relationships": _CHARACTER_RELATIONSHIPS_SCHEMA,
        "voice": _STRING_SCHEMA,
        "gender": _STRING_SCHEMA,
        "social_status": _STRING_SCHEMA,
        "abilities": _STRING_SCHEMA,
        "name": _STRING_SCHEMA,
        "notes": _STRING_SCHEMA,
    },
    additional_properties=False,
)
_CHARACTER_PROFILE_KEYS = (
    "name",
    "role",
    "age",
    "gender",
    "status",
    "time_layer",
    "social_status",
    "abilities",
    "appearance",
    "personality",
    "backstory",
    "arc",
    "relationships",
    "voice",
    "visual_identity",
    "tts_voice_hints",
    "notes",
)
_CHARACTER_PROFILE_REQUIRED_KEYS = ("name",)
_CHARACTER_PROFILE_SCHEMA = _typed_object_schema(
    required=_CHARACTER_PROFILE_REQUIRED_KEYS,
    properties={
        "name": _STRING_SCHEMA,
        "role": _CHARACTER_ROLE_SCHEMA,
        "age": _STRING_SCHEMA,
        "gender": _STRING_SCHEMA,
        "status": _CHARACTER_STATUS_SCHEMA,
        "time_layer": _CHARACTER_TIME_LAYER_SCHEMA,
        "social_status": _STRING_SCHEMA,
        "abilities": _STRING_SCHEMA,
        "appearance": _STRING_SCHEMA,
        "personality": _STRING_SCHEMA,
        "backstory": _STRING_SCHEMA,
        "arc": _STRING_SCHEMA,
        "relationships": _CHARACTER_RELATIONSHIPS_SCHEMA,
        "voice": _STRING_SCHEMA,
        "visual_identity": _CHARACTER_VISUAL_IDENTITY_SCHEMA,
        "tts_voice_hints": _TTS_VOICE_HINTS_SCHEMA,
        "notes": _STRING_SCHEMA,
    },
    additional_properties=False,
)
_CHARACTER_BIBLE_SCHEMA = _typed_object_schema(
    required=("character_bible",),
    properties={
        "character_bible": _typed_object_schema(
            required=("characters",),
            properties={
                "characters": {
                    "type": "array",
                    "items": _CHARACTER_PROFILE_SCHEMA,
                    "minItems": 1,
                }
            },
            additional_properties=False,
        )
    },
    additional_properties=False,
)
_CHARACTER_PROFILE_BATCH_SCHEMA = _typed_object_schema(
    required=("character_profiles",),
    properties={
        "character_profiles": {
            "type": "array",
            "items": _CHARACTER_PROFILE_SCHEMA,
            "minItems": 1,
        }
    },
    additional_properties=False,
)

_INTRODUCE_CHARACTER_SCHEMA = _typed_object_schema(
    required=(
        "name",
        "role",
        "gender",
        "social_status",
        "abilities",
        "appearance",
        "personality",
        "backstory",
        "arc",
        "relationships",
        "voice",
        "notes",
    ),
    properties={
        "name": _STRING_SCHEMA,
        "role": _STRING_SCHEMA,
        "gender": _STRING_SCHEMA,
        "social_status": _STRING_SCHEMA,
        "abilities": _STRING_SCHEMA,
        "appearance": _STRING_SCHEMA,
        "personality": _STRING_SCHEMA,
        "backstory": _STRING_SCHEMA,
        "arc": _STRING_SCHEMA,
        "relationships": {
            "type": "object",
            "additionalProperties": _STRING_SCHEMA,
        },
        "voice": _STRING_SCHEMA,
        "notes": _STRING_SCHEMA,
    },
    additional_properties=False,
)


def _config_field_schema(key: str) -> dict[str, Any]:
    """Return the canonical JSON Schema node for an AI creative-config field."""
    if key in _CONFIG_INTEGER_KEYS:
        return deepcopy(_INTEGER_SCHEMA)
    if key == "polish_suggestions":
        return deepcopy(_STRING_ARRAY_SCHEMA)
    if key == "creative_note":
        return deepcopy(_CREATIVE_NOTE_SCHEMA)
    return deepcopy(_STRING_SCHEMA)


def _config_contract_schema(
    keys: tuple[str, ...],
    *,
    required: tuple[str, ...],
) -> dict[str, Any]:
    """Build a mode-aware creative-config schema from the same key set as the contract."""
    return _typed_object_schema(
        required=required,
        properties={key: _config_field_schema(key) for key in keys},
        additional_properties=False,
    )


_OUTLINE_HOOK_SCHEMA = _typed_object_schema(
    required=("hook_type", "hook_strength", "hook_description"),
    properties={
        "hook_type": _STRING_SCHEMA,
        "hook_strength": _STRING_SCHEMA,
        "hook_description": _STRING_SCHEMA,
    },
    additional_properties=False,
)
_OUTLINE_PAYOFF_SCHEMA = _typed_object_schema(
    required=("payoff_type", "description"),
    properties={
        "payoff_type": _STRING_SCHEMA,
        "description": _STRING_SCHEMA,
    },
    additional_properties=False,
)
_READING_POWER_STRENGTH_SCHEMA = _string_enum_schema("strong", "medium", "weak")
_READING_POWER_MICRO_PAYOFF_SCHEMA = _typed_object_schema(
    required=("description",),
    properties={
        "payoff_type": _string_enum_schema(
            "information",
            "relationship",
            "ability",
            "resource",
            "recognition",
            "emotion",
            "clue",
        ),
        "type": _string_enum_schema(
            "information",
            "relationship",
            "ability",
            "resource",
            "recognition",
            "emotion",
            "clue",
        ),
        "description": _STRING_SCHEMA,
        "strength": _READING_POWER_STRENGTH_SCHEMA,
    },
    additional_properties=True,
)
_READING_POWER_OUTLINE_HOOK_MATCH_SCHEMA = _typed_object_schema(
    required=(),
    properties={
        "matched": _BOOLEAN_SCHEMA,
        "match_type": _string_enum_schema("exact", "partial", "different"),
        "reason": _STRING_SCHEMA,
    },
    additional_properties=True,
)
_READING_POWER_OUTLINE_PAYOFF_COVERAGE_SCHEMA = _typed_object_schema(
    required=(),
    properties={
        "covered_count": _INTEGER_SCHEMA,
        "total_expected": _INTEGER_SCHEMA,
        "coverage_ratio": _NUMBER_SCHEMA,
        "missing": _STRING_ARRAY_SCHEMA,
        "unexpected": _STRING_ARRAY_SCHEMA,
    },
    additional_properties=True,
)
_READING_POWER_EVAL_REQUIRED_KEYS = (
    "hook_type",
    "hook_strength",
    "hook_description",
    "prev_hook_fulfilled",
    "micro_payoffs",
    "is_transition",
    "next_chapter_reason",
    "information_pacing",
    "main_plot_depth",
    "tension_match",
    "character_drive",
)
_READING_POWER_EVAL_SCHEMA = _typed_object_schema(
    required=_READING_POWER_EVAL_REQUIRED_KEYS,
    properties={
        "hook_type": _string_enum_schema(
            "crisis",
            "mystery",
            "emotion",
            "choice",
            "desire",
            "none",
        ),
        "hook_strength": _READING_POWER_STRENGTH_SCHEMA,
        "hook_description": _STRING_SCHEMA,
        "prev_hook_fulfilled": _BOOLEAN_SCHEMA,
        "micro_payoffs": _array_of(_READING_POWER_MICRO_PAYOFF_SCHEMA),
        "is_transition": _BOOLEAN_SCHEMA,
        "next_chapter_reason": _STRING_SCHEMA,
        "outline_hook_match": _READING_POWER_OUTLINE_HOOK_MATCH_SCHEMA,
        "outline_payoff_coverage": _READING_POWER_OUTLINE_PAYOFF_COVERAGE_SCHEMA,
        "resolved_suspense_ids": _STRING_ARRAY_SCHEMA,
        "unresolved_suspense_ids": _STRING_ARRAY_SCHEMA,
        "information_pacing": _string_enum_schema("rushed", "balanced", "slow", "stagnant"),
        "information_pacing_score": _NUMBER_SCHEMA,
        "main_plot_depth": _string_enum_schema("deep", "moderate", "surface", "stalled"),
        "main_plot_advancement_notes": _STRING_SCHEMA,
        "tension_match": _string_enum_schema("matched", "elevated", "depressed"),
        "tension_match_score": _NUMBER_SCHEMA,
        "revelation_count": _INTEGER_SCHEMA,
        "revelation_over_budget": _BOOLEAN_SCHEMA,
        "consecutive_main_plot_stall": _INTEGER_SCHEMA,
        "character_drive": _string_enum_schema("strong", "moderate", "weak"),
        "character_drive_notes": _STRING_SCHEMA,
        "suggestions": _STRING_ARRAY_SCHEMA,
    },
    additional_properties=True,
)
_HUMANIZE_PATTERN_HIT_SCHEMA = _typed_object_schema(
    required=(
        "pattern_id",
        "pattern_name",
        "category",
        "severity",
        "evidence_quote",
        "paragraph_index",
        "suggestion",
        "confidence",
        "actionable",
        "source",
        "span_start",
        "span_end",
    ),
    properties={
        "pattern_id": _pattern_id_schema(),
        "pattern_name": _STRING_SCHEMA,
        "category": _STRING_SCHEMA,
        "severity": _string_enum_schema("critical", "high", "medium", "low"),
        "evidence_quote": _STRING_SCHEMA,
        # Some providers emit null when the quote is unique but they did not
        # count paragraphs. HumanizeScanStep resolves this from evidence/span
        # before the report is persisted, so treat null as recoverable drift.
        "paragraph_index": _NULLABLE_INTEGER_SCHEMA,
        "suggestion": _STRING_SCHEMA,
        "confidence": _NUMBER_SCHEMA,
        "actionable": _BOOLEAN_SCHEMA,
        "source": _string_enum_schema("local", "llm", "merged", "library", "library_user"),
        "span_start": _NULLABLE_INTEGER_SCHEMA,
        "span_end": _NULLABLE_INTEGER_SCHEMA,
    },
    additional_properties=False,
)
_HUMANIZE_REPORT_REQUIRED_KEYS = (
    "total_hits",
    "hits_by_category",
    "critical_hits",
    "pattern_hits",
    "summary",
)
_HUMANIZE_REPORT_SCHEMA = _typed_object_schema(
    required=_HUMANIZE_REPORT_REQUIRED_KEYS,
    properties={
        "source_text_hash": _STRING_SCHEMA,
        "chapter_number": _INTEGER_SCHEMA,
        "total_hits": _INTEGER_SCHEMA,
        "hits_by_category": {
            "type": "object",
            "additionalProperties": _INTEGER_SCHEMA,
        },
        "critical_hits": _INTEGER_SCHEMA,
        "pattern_hits": _array_of(_HUMANIZE_PATTERN_HIT_SCHEMA),
        "humanize_score": _NUMBER_SCHEMA,
        "summary": _STRING_SCHEMA,
    },
    additional_properties=False,
)
_OUTLINE_CAST_PLAN_SCHEMA = _typed_object_schema(
    required=(
        "pov_entity_id",
        "required_character_ids",
        "support_character_ids",
        "mention_only_entity_ids",
        "forbidden_active_character_ids",
    ),
    properties={
        "pov_entity_id": _STRING_SCHEMA,
        "required_character_ids": _STRING_ARRAY_SCHEMA,
        "support_character_ids": _STRING_ARRAY_SCHEMA,
        "mention_only_entity_ids": _STRING_ARRAY_SCHEMA,
        "forbidden_active_character_ids": _STRING_ARRAY_SCHEMA,
    },
    additional_properties=False,
)
_OUTLINE_EMOTIONAL_PLAN_SCHEMA = _typed_object_schema(
    required=(
        "subject_entity_id",
        "entry_state",
        "pressure_source",
        "relationship_choice",
        "turning_emotion",
        "exit_aftertaste",
        "expression_channels",
    ),
    properties={
        "subject_entity_id": _STRING_SCHEMA,
        "entry_state": _STRING_SCHEMA,
        "pressure_source": _STRING_SCHEMA,
        "relationship_choice": _STRING_SCHEMA,
        "turning_emotion": _STRING_SCHEMA,
        "exit_aftertaste": _STRING_SCHEMA,
        "expression_channels": _STRING_ARRAY_SCHEMA,
    },
    additional_properties=False,
)
_PLAN_OUTLINE_CHAPTER_KEYS = (
    "chapter_number",
    "title",
    "goal",
    "beats_summary",
    "main_plot_points",
    "subplot_points",
    "subplot_focus",
    "element_focus",
    "pov_character_id",
    "pov_character_name",
    "pov_character",
    "pov_switch",
    "setting",
    "expected_word_count",
    "involved_character_ids",
    "required_character_ids",
    "support_character_ids",
    "involved_character_names",
    "involved_characters",
    "cast_plan",
    "emotional_plan",
    "scene_design_goals",
    "notes",
    "expected_hook",
    "expected_payoffs",
)
_PLAN_OUTLINE_CHAPTER_SCHEMA = _typed_object_schema(
    required=_PLAN_OUTLINE_CHAPTER_KEYS,
    properties={
        "chapter_number": {"type": "integer", "minimum": 1},
        "title": _STRING_SCHEMA,
        "goal": _STRING_SCHEMA,
        "beats_summary": _STRING_ARRAY_SCHEMA,
        "main_plot_points": _STRING_ARRAY_SCHEMA,
        "subplot_points": _STRING_ARRAY_SCHEMA,
        "subplot_focus": _STRING_SCHEMA,
        "element_focus": _STRING_ARRAY_SCHEMA,
        "pov_character_id": _STRING_SCHEMA,
        "pov_character_name": _STRING_SCHEMA,
        "pov_character": _STRING_SCHEMA,
        "pov_switch": _BOOLEAN_SCHEMA,
        "setting": _STRING_SCHEMA,
        "expected_word_count": {"type": "integer", "minimum": 500},
        "involved_character_ids": _STRING_ARRAY_SCHEMA,
        "required_character_ids": _STRING_ARRAY_SCHEMA,
        "support_character_ids": _STRING_ARRAY_SCHEMA,
        "involved_character_names": _STRING_ARRAY_SCHEMA,
        "involved_characters": _STRING_ARRAY_SCHEMA,
        "cast_plan": _OUTLINE_CAST_PLAN_SCHEMA,
        "emotional_plan": _OUTLINE_EMOTIONAL_PLAN_SCHEMA,
        "scene_design_goals": _STRING_ARRAY_SCHEMA,
        "notes": _STRING_SCHEMA,
        "time_anchor": _STRING_SCHEMA,
        "time_span": _STRING_SCHEMA,
        "time_gap_from_prev": _STRING_SCHEMA,
        "countdown_state": _STRING_SCHEMA,
        "is_flashback": _BOOLEAN_SCHEMA,
        "expected_hook": _OUTLINE_HOOK_SCHEMA,
        "expected_payoffs": _array_of(_OUTLINE_PAYOFF_SCHEMA),
    },
    additional_properties=False,
)
_PLAN_OUTLINE_BATCH_SCHEMA = _typed_object_schema(
    required=("chapters",),
    properties={
        "chapters": {
            "type": "array",
            "items": _PLAN_OUTLINE_CHAPTER_SCHEMA,
            "minItems": 1,
        }
    },
    additional_properties=False,
)
_POLISH_OUTLINE_CHAPTER_PATCH_SCHEMA = _typed_object_schema(
    required=("chapter_number",),
    properties={
        "chapter_number": {"type": "integer", "minimum": 1},
        "title": _STRING_SCHEMA,
        "goal": _STRING_SCHEMA,
        "beats_summary": _STRING_ARRAY_SCHEMA,
        "main_plot_points": _STRING_ARRAY_SCHEMA,
        "subplot_points": _STRING_ARRAY_SCHEMA,
        "subplot_focus": _STRING_SCHEMA,
        "element_focus": _STRING_ARRAY_SCHEMA,
        "pov_character_id": _STRING_SCHEMA,
        "pov_character_name": _STRING_SCHEMA,
        "pov_character": _STRING_SCHEMA,
        "pov_switch": _BOOLEAN_SCHEMA,
        "setting": _STRING_SCHEMA,
        "expected_word_count": {"type": "integer", "minimum": 500},
        "involved_character_ids": _STRING_ARRAY_SCHEMA,
        "required_character_ids": _STRING_ARRAY_SCHEMA,
        "support_character_ids": _STRING_ARRAY_SCHEMA,
        "involved_character_names": _STRING_ARRAY_SCHEMA,
        "involved_characters": _STRING_ARRAY_SCHEMA,
        "cast_plan": _OUTLINE_CAST_PLAN_SCHEMA,
        "emotional_plan": _OUTLINE_EMOTIONAL_PLAN_SCHEMA,
        "scene_design_goals": _STRING_ARRAY_SCHEMA,
        "notes": _STRING_SCHEMA,
        "time_anchor": _STRING_SCHEMA,
        "time_span": _STRING_SCHEMA,
        "time_gap_from_prev": _STRING_SCHEMA,
        "countdown_state": _STRING_SCHEMA,
        "is_flashback": _BOOLEAN_SCHEMA,
        "expected_hook": _OUTLINE_HOOK_SCHEMA,
        "expected_payoffs": _array_of(_OUTLINE_PAYOFF_SCHEMA),
    },
    additional_properties=False,
)
_POLISH_OUTLINE_SCHEMA = _typed_object_schema(
    required=("adjusted_chapters", "polish_suggestions"),
    properties={
        "adjusted_chapters": _array_of(_POLISH_OUTLINE_CHAPTER_PATCH_SCHEMA),
        "polish_suggestions": _STRING_ARRAY_SCHEMA,
    },
    additional_properties=False,
)
_POLISH_OUTLINE_TITLE_REPAIR_CHAPTER_SCHEMA = _typed_object_schema(
    required=("chapter_number", "title"),
    properties={
        "chapter_number": {"type": "integer", "minimum": 1},
        "title": _OUTLINE_TITLE_SCHEMA,
    },
    additional_properties=False,
)
_POLISH_OUTLINE_TITLE_REPAIR_SCHEMA = _typed_object_schema(
    required=("adjusted_chapters", "polish_suggestions"),
    properties={
        "adjusted_chapters": _array_of(_POLISH_OUTLINE_TITLE_REPAIR_CHAPTER_SCHEMA),
        "polish_suggestions": _STRING_ARRAY_SCHEMA,
    },
    additional_properties=False,
)

_BOOK_CONSISTENCY_EVIDENCE_PAIR_SCHEMA = _typed_object_schema(
    required=("chapter_number", "evidence", "claim"),
    properties={
        "chapter_number": _INTEGER_SCHEMA,
        "evidence": _STRING_SCHEMA,
        "claim": _STRING_SCHEMA,
    },
)

_BOOK_CONSISTENCY_LINKED_REF_SCHEMA = _typed_object_schema(
    required=("chapter_number", "lane", "index"),
    properties={
        "chapter_number": _INTEGER_SCHEMA,
        "lane": _STRING_SCHEMA,
        "index": _INTEGER_SCHEMA,
    },
)

_BOOK_CONSISTENCY_ISSUE_SCHEMA = _typed_object_schema(
    required=(
        "issue_id",
        "category",
        "severity",
        "chapters_involved",
        "primary_chapter",
        "issue_type",
        "location",
        "paragraph_index",
        "paragraph_span",
        "evidence",
        "description",
        "suggestion",
        "fix_mode",
        "fix_action",
        "confidence",
        "evidence_pairs",
        "verification_questions",
        "handoff_notes",
        "linked_issue_refs",
    ),
    properties={
        "issue_id": _STRING_SCHEMA,
        "category": _STRING_SCHEMA,
        "severity": _STRING_SCHEMA,
        "chapters_involved": _INTEGER_ARRAY_SCHEMA,
        "primary_chapter": _INTEGER_SCHEMA,
        "issue_type": _STRING_SCHEMA,
        "location": _STRING_SCHEMA,
        "paragraph_index": _INTEGER_SCHEMA,
        "paragraph_span": _INTEGER_ARRAY_SCHEMA,
        "evidence": _STRING_SCHEMA,
        "description": _STRING_SCHEMA,
        "suggestion": _STRING_SCHEMA,
        "fix_mode": _STRING_SCHEMA,
        "fix_action": _STRING_SCHEMA,
        "confidence": _NUMBER_SCHEMA,
        "evidence_pairs": _array_of(_BOOK_CONSISTENCY_EVIDENCE_PAIR_SCHEMA),
        "verification_questions": _STRING_ARRAY_SCHEMA,
        "handoff_notes": _STRING_SCHEMA,
        "linked_issue_refs": _array_of(_BOOK_CONSISTENCY_LINKED_REF_SCHEMA),
    },
)

_BOOK_CONSISTENCY_REPAIR_PLAN_SCHEMA = _typed_object_schema(
    required=("chapter_number", "issue_ids", "priority", "strategy"),
    properties={
        "chapter_number": _INTEGER_SCHEMA,
        "issue_ids": _STRING_ARRAY_SCHEMA,
        "priority": _STRING_SCHEMA,
        "strategy": _STRING_SCHEMA,
    },
)

_BOOK_CONSISTENCY_SCHEMA = _typed_object_schema(
    required=("issues", "repair_plan", "summary", "consistency_score"),
    properties={
        "issues": _array_of(_BOOK_CONSISTENCY_ISSUE_SCHEMA),
        "repair_plan": _array_of(_BOOK_CONSISTENCY_REPAIR_PLAN_SCHEMA),
        "summary": _STRING_SCHEMA,
        "consistency_score": _NUMBER_SCHEMA,
    },
)

_BOOK_CONSISTENCY_ISSUES_ONLY_SCHEMA = _typed_object_schema(
    required=("issues",),
    properties={"issues": _array_of(_BOOK_CONSISTENCY_ISSUE_SCHEMA)},
)

_BOOK_CONSISTENCY_REPAIR_SCOPE_SCHEMA = _typed_object_schema(
    required=("target", "allowed_changes", "forbidden_changes", "preserve"),
    properties={
        "target": _STRING_SCHEMA,
        "allowed_changes": _STRING_ARRAY_SCHEMA,
        "forbidden_changes": _STRING_ARRAY_SCHEMA,
        "preserve": _STRING_ARRAY_SCHEMA,
    },
)

_BOOK_CONSISTENCY_POSTCONDITION_SCHEMA = _typed_object_schema(
    required=("check", "expected"),
    properties={"check": _STRING_SCHEMA, "expected": _STRING_SCHEMA},
)

_BOOK_CONSISTENCY_VERIFIED_ISSUE_SCHEMA = _typed_object_schema(
    required=(
        "issue_id",
        "status",
        "description",
        "severity",
        "confidence",
        "evidence",
        "paragraph_index",
        "paragraph_span",
        "location",
        "anchor_type",
        "location_confidence",
        "evidence_pairs",
        "adjudication_notes",
        "repair_scope",
        "fix_mode",
        "fix_action",
        "postconditions",
        "rejection_reason",
    ),
    properties={
        "issue_id": _STRING_SCHEMA,
        "status": _STRING_SCHEMA,
        "description": _STRING_SCHEMA,
        "severity": _STRING_SCHEMA,
        "confidence": _NUMBER_SCHEMA,
        "evidence": _STRING_SCHEMA,
        "paragraph_index": _INTEGER_SCHEMA,
        "paragraph_span": _INTEGER_ARRAY_SCHEMA,
        "location": _STRING_SCHEMA,
        "anchor_type": _STRING_SCHEMA,
        "location_confidence": _NUMBER_SCHEMA,
        "evidence_pairs": _array_of(_BOOK_CONSISTENCY_EVIDENCE_PAIR_SCHEMA),
        "adjudication_notes": _STRING_SCHEMA,
        "repair_scope": _BOOK_CONSISTENCY_REPAIR_SCOPE_SCHEMA,
        "fix_mode": _STRING_SCHEMA,
        "fix_action": _STRING_SCHEMA,
        "postconditions": _array_of(_BOOK_CONSISTENCY_POSTCONDITION_SCHEMA),
        "rejection_reason": _STRING_SCHEMA,
    },
)

_BOOK_CONSISTENCY_VERIFY_SCHEMA = _typed_object_schema(
    required=("verified_issues",),
    properties={"verified_issues": _array_of(_BOOK_CONSISTENCY_VERIFIED_ISSUE_SCHEMA)},
)

_KNOWLEDGE_BOUNDARIES_CHARACTER_SCHEMA = _typed_object_schema(
    required=("character_id", "name", "knowledge_boundaries"),
    properties={
        "character_id": _STRING_SCHEMA,
        "name": _STRING_SCHEMA,
        "knowledge_boundaries": _typed_object_schema(
            required=(
                "known_facts",
                "suspected",
                "misbeliefs",
                "secrets_kept",
                "sensory_access_rules",
            ),
            properties={
                "known_facts": _STRING_ARRAY_SCHEMA,
                "suspected": _STRING_ARRAY_SCHEMA,
                "misbeliefs": _STRING_ARRAY_SCHEMA,
                "secrets_kept": _STRING_ARRAY_SCHEMA,
                "sensory_access_rules": _STRING_ARRAY_SCHEMA,
            },
            additional_properties=False,
        ),
    },
    additional_properties=False,
)

_KNOWLEDGE_BOUNDARIES_SCHEMA = _typed_object_schema(
    required=("characters",),
    properties={
        "characters": _array_of(_KNOWLEDGE_BOUNDARIES_CHARACTER_SCHEMA),
    },
    additional_properties=False,
)

_KNOWLEDGE_DELTA_SCHEMA = _typed_object_schema(
    required=("entity_id", "fact", "knowledge_type", "visibility", "source_chapter"),
    properties={
        "entity_id": _STRING_SCHEMA,
        "fact": _STRING_SCHEMA,
        "knowledge_type": _STRING_SCHEMA,
        "visibility": _STRING_SCHEMA,
        "source_chapter": _INTEGER_SCHEMA,
    },
    additional_properties=False,
)

_KNOWLEDGE_DELTAS_SCHEMA = _typed_object_schema(
    required=("knowledge_deltas",),
    properties={
        "knowledge_deltas": _array_of(_KNOWLEDGE_DELTA_SCHEMA),
    },
    additional_properties=False,
)

_REPAIR_KNOWLEDGE_BOUNDARY_SCHEMA = _typed_object_schema(
    required=(),
    properties={
        "repaired_text": _STRING_SCHEMA,
        "changes": _array_of(_GENERIC_OBJECT_SCHEMA),
    },
    additional_properties=False,
)

_KNOWLEDGE_BOUNDARY_AUDIT_ISSUE_SCHEMA = _typed_object_schema(
    required=(
        "decision",
        "issue_type",
        "severity",
        "confidence",
        "evidence_quote",
        "entry_id",
        "repair_goal",
        "paragraph_start",
        "paragraph_end",
        "reason",
    ),
    properties={
        "decision": _string_enum_schema(
            "not_leak",
            "legitimate_reveal",
            "leak",
            "premature_reveal",
            "unsupported_knowledge_gain",
            "ambiguous",
        ),
        "issue_type": _string_enum_schema(
            "knowledge_leak",
            "premature_reveal",
            "unsupported_knowledge_gain",
            "knowledge_boundary_ambiguous",
            "none",
        ),
        "severity": _string_enum_schema("critical", "high", "medium", "low", "info"),
        "confidence": _NUMBER_SCHEMA,
        "evidence_quote": _STRING_SCHEMA,
        "entry_id": _STRING_SCHEMA,
        "repair_goal": _STRING_SCHEMA,
        "paragraph_start": _INTEGER_SCHEMA,
        "paragraph_end": _INTEGER_SCHEMA,
        "reason": _STRING_SCHEMA,
    },
    additional_properties=False,
)

_KNOWLEDGE_BOUNDARY_AUDIT_SCHEMA = _typed_object_schema(
    required=("verdict", "issues"),
    properties={
        "verdict": _string_enum_schema("pass", "issues_found", "ambiguous", "error"),
        "issues": _array_of(_KNOWLEDGE_BOUNDARY_AUDIT_ISSUE_SCHEMA),
    },
    additional_properties=False,
)

_EDITORIAL_FINDING_SCHEMA = _typed_object_schema(
    required=(
        "issue_type",
        "severity",
        "chapter_number",
        "summary",
        "evidence",
        "recommendation",
        "confidence",
        "metadata",
    ),
    properties={
        "issue_type": _STRING_SCHEMA,
        "severity": _STRING_SCHEMA,
        "chapter_number": _INTEGER_SCHEMA,
        "summary": _STRING_SCHEMA,
        "evidence": _STRING_ARRAY_SCHEMA,
        "recommendation": _STRING_SCHEMA,
        "confidence": _NUMBER_SCHEMA,
        "metadata": {"type": "object", "additionalProperties": _STRING_SCHEMA},
    },
)

_EDITORIAL_AUDIT_SCHEMA = _typed_object_schema(
    required=("summary", "findings", "revision_plan", "metrics"),
    properties={
        "summary": _STRING_SCHEMA,
        "findings": _array_of(_EDITORIAL_FINDING_SCHEMA),
        "revision_plan": _STRING_ARRAY_SCHEMA,
        "metrics": _typed_object_schema(
            required=("editorial_score",),
            properties={"editorial_score": _NUMBER_SCHEMA},
        ),
    },
)

_MACRO_GUARD_DIMENSIONS_SCHEMA = _typed_object_schema(
    required=(
        "outline_alignment",
        "character_arc_consistency",
        "pacing_curve",
        "foreshadowing_recovery",
        "thematic_cohesion",
    ),
    properties={
        "outline_alignment": _NUMBER_SCHEMA,
        "character_arc_consistency": _NUMBER_SCHEMA,
        "pacing_curve": _NUMBER_SCHEMA,
        "foreshadowing_recovery": _NUMBER_SCHEMA,
        "thematic_cohesion": _NUMBER_SCHEMA,
    },
)
_MACRO_GUARD_FINDING_SCHEMA = _typed_object_schema(
    required=("severity", "type", "description", "evidence"),
    properties={
        "severity": _STRING_SCHEMA,
        "type": _STRING_SCHEMA,
        "description": _STRING_SCHEMA,
        "evidence": _STRING_ARRAY_SCHEMA,
    },
)
_MACRO_GUARD_ADJUSTMENT_PLAN_SCHEMA = _typed_object_schema(
    required=("window_size", "strategy", "target_outline_v", "adjusted_chapter_goals", "reasoning"),
    properties={
        "window_size": _INTEGER_SCHEMA,
        "strategy": _STRING_SCHEMA,
        "target_outline_v": _STRING_SCHEMA,
        "adjusted_chapter_goals": _array_of(_MACRO_GUARD_ADJUSTED_CHAPTER_GOAL_SCHEMA),
        "reasoning": _STRING_SCHEMA,
    },
)
_MACRO_GUARD_SCHEMA = _typed_object_schema(
    required=(
        "dimensions",
        "recommended_action",
        "drift_score",
        "findings",
        "adjustment_plan",
        "confidence",
        "reasoning",
    ),
    properties={
        "dimensions": _MACRO_GUARD_DIMENSIONS_SCHEMA,
        "recommended_action": _STRING_SCHEMA,
        "drift_score": _NUMBER_SCHEMA,
        "findings": _array_of(_MACRO_GUARD_FINDING_SCHEMA),
        "adjustment_plan": _MACRO_GUARD_ADJUSTMENT_PLAN_SCHEMA,
        "confidence": _NUMBER_SCHEMA,
        "reasoning": _STRING_SCHEMA,
    },
)


def _contains_markdown_heading(text: str) -> bool:
    return bool(re.search(_TEXT_HEADING_RE, text, re.MULTILINE))


def _looks_like_list_protocol(text: str) -> bool:
    lines = [line for line in str(text or "").splitlines() if line.strip()]
    if not lines:
        return False
    checked = lines[: min(3, len(lines))]
    return sum(1 for line in checked if re.match(_TEXT_LIST_RE, line)) >= min(2, len(checked))


def strip_leading_markdown_headings(text: str) -> str:
    """Remove model-added opening Markdown chapter headings from prose.

    TEXT tasks are prompted to start directly with narrative prose, but some
    models still prepend a harmless line such as ``# 第一章 峰会初逢``.  The
    downstream archive guard already treats that as a cleanup case, so the
    shared contract does the same before checking for real mid-body headings or
    leaked planning structure.
    """
    source = str(text or "")
    stripped = source
    while True:
        match = _TEXT_LEADING_HEADING_RE.match(stripped)
        if match is None:
            return stripped
        stripped = stripped[match.end() :].lstrip("\ufeff\r\n \t")


def find_text_output_contract_violations(task_type: TaskType, text: str) -> list[str]:
    """Return TEXT-output policy violations for prose tasks."""
    source = str(text or "")
    stripped = source.strip()
    violations: list[str] = []

    if "```" in source:
        violations.append("contains Markdown code fence")
    if _contains_markdown_heading(source):
        violations.append("contains Markdown heading")
    if _looks_like_list_protocol(source):
        violations.append("looks like an instruction/list protocol")

    lowered = source.lower()
    for token in _TEXT_BLOCKED_PATTERNS:
        if token.lower() in lowered:
            violations.append(f"contains planning/system token: {token}")

    try:
        from novel_forge.core.domain.guardrails import detect_prompt_leaks

        leaks = detect_prompt_leaks(source, max_hits=6)
    except Exception:
        leaks = []
    if leaks:
        sample = "；".join(leaks[:3])
        violations.append(f"contains prompt/planning leak: {sample}")

    if stripped.startswith(("说明：", "修改说明", "以下是", "自检", "检查结果")):
        violations.append("starts with meta/instructional prose")

    return violations


def validate_text_output_contract(
    task_type: TaskType,
    raw_content: str,
    extracted_text: str,
    *,
    min_chars: int = 1,
) -> str:
    """Validate and normalize a TEXT task response.

    TEXT contracts intentionally remain lightweight: they reject only outputs
    that are clearly not prose, such as empty text or an unextracted JSON/list
    payload. Domain-specific word-count and quality gates stay in the calling
    steps.
    """
    contract = get_task_format_contract(task_type)
    if contract is None or contract.output_kind != OutputKind.TEXT:
        return str(extracted_text or "")

    raw_source = str(raw_content or "")
    extracted_source = str(extracted_text or "")
    if "```" in raw_source or "```" in extracted_source:
        raise TextOutputContractError(
            f"{task_type.value} TEXT output violates prose contract: contains Markdown code fence"
        )

    text = strip_leading_markdown_headings(_strip_outer_markdown_fence(extracted_source))
    if len(text.strip()) < max(1, int(min_chars or 1)):
        raise TextOutputContractError(
            f"{task_type.value} TEXT output is empty or shorter than {min_chars} characters"
        )

    raw_stripped = _strip_outer_markdown_fence(raw_source)
    if _looks_like_json_container(text):
        raise TextOutputContractError(
            f"{task_type.value} TEXT output still looks like JSON/list instead of prose"
        )
    if raw_stripped == text and _looks_like_json_container(raw_stripped):
        raise TextOutputContractError(
            f"{task_type.value} TEXT output returned a structured payload without prose content"
        )
    violations = find_text_output_contract_violations(task_type, text)
    if violations:
        raise TextOutputContractError(
            f"{task_type.value} TEXT output violates prose contract: " + " | ".join(violations[:6])
        )
    return text


def config_contract_keys_for_mode(mode: str | None) -> tuple[str, ...]:
    """Return AI creative-config keys for a desktop mode."""
    normalized = str(mode or "").strip().lower()
    if normalized == "long":
        return _LONG_CONFIG_KEYS
    if normalized == "short":
        return _SHORT_CONFIG_KEYS
    return tuple(dict.fromkeys((*_SHORT_CONFIG_KEYS, *_LONG_CONFIG_KEYS)))


def _dynamic_config_contract(
    task_type: TaskType, context: dict[str, Any] | None
) -> TaskFormatContract:
    context = context or {}
    mode = str(context.get("mode") or "").strip().lower()
    keys = (*config_contract_keys_for_mode(mode), *_AI_CONFIG_METADATA_KEYS)
    partial_output = task_type == TaskType.POLISH_CONFIG or bool(
        context.get("allow_partial_config_output")
    )
    if partial_output:
        editable_keys = tuple(
            key
            for key in context.get("editable_config_fields") or ()
            if key in config_contract_keys_for_mode(mode)
        )
        allowed_keys = (*editable_keys, *_AI_CONFIG_METADATA_KEYS) if editable_keys else keys
        return TaskFormatContract(
            OutputKind.JSON,
            required_top_level_keys=(),
            enforce_required_keys=False,
            allowed_top_level_keys=allowed_keys,
            schema_model=f"{task_type.value}_{mode or 'any'}_partial",
            json_schema=_config_contract_schema(allowed_keys, required=()),
            response_schema_model=_response_schema_model_for_task(task_type),
            task_family="creative_config",
            strict_mode=False,
            contract_mode=ContractMode.PARTIAL_OBJECT,
        )
    return TaskFormatContract(
        OutputKind.JSON,
        required_top_level_keys=keys,
        enforce_required_keys=True,
        allowed_top_level_keys=keys,
        schema_model=f"{task_type.value}_{mode or 'any'}",
        json_schema=_config_contract_schema(keys, required=keys),
        response_schema_model=_response_schema_model_for_task(task_type),
        task_family="creative_config",
        strict_mode=True,
        contract_mode=ContractMode.FULL_OBJECT,
    )


def _dynamic_plan_outline_contract(context: dict[str, Any] | None) -> TaskFormatContract | None:
    request = (context or {}).get("blueprint_fragment_request")
    if not isinstance(request, dict):
        return None
    keys = tuple(str(key).strip() for key in request.get("required_keys") or () if str(key).strip())
    if not keys:
        return None
    block_key = str(request.get("block_key") or "fragment").strip() or "fragment"
    response_schema_model = _plan_outline_fragment_response_model(block_key)
    contract = TaskFormatContract(
        OutputKind.JSON,
        required_top_level_keys=keys,
        enforce_required_keys=True,
        allowed_top_level_keys=keys,
        schema_model=f"plan_outline_{block_key}",
        response_schema_model=response_schema_model,
        schema_source="pydantic_model" if response_schema_model else "dynamic_fragment_schema",
        schema_strength="strong" if response_schema_model else "medium",
        task_family="planning",
        strict_mode=True,
        contract_mode=ContractMode.FRAGMENT_OBJECT,
    )
    return replace(
        contract,
        json_schema=_typed_contract_schema(
            TaskType.PLAN_OUTLINE,
            contract,
            base_schema=_schema_from_response_schema_model(response_schema_model)
            or _blueprint_fragment_base_schema(),
        ),
    )


_TASK_FORMAT_CONTRACTS: dict[TaskType, TaskFormatContract] = {
    # Initialization + planning (long form)
    TaskType.SPEC_ENRICH: TaskFormatContract(
        OutputKind.JSON,
        required_top_level_keys=_SPEC_ENRICH_KEYS,
        enforce_required_keys=True,
        allowed_top_level_keys=_SPEC_ENRICH_KEYS,
        schema_model="spec_enrich",
        task_family="initialization",
        contract_mode=ContractMode.FULL_OBJECT,
    ),
    TaskType.INIT_STORY_BIBLE: TaskFormatContract(
        OutputKind.JSON,
        required_top_level_keys=("story_bible",),
        enforce_required_keys=True,
        contract_mode=ContractMode.FULL_OBJECT,
    ),
    TaskType.INIT_CREATIVE_DIRECTION_CANDIDATES: TaskFormatContract(
        OutputKind.JSON,
        required_top_level_keys=("candidates",),
        allowed_top_level_keys=("candidates",),
        enforce_required_keys=True,
        schema_model="creative_direction_candidate_batch",
        task_family="initialization",
        contract_mode=ContractMode.FULL_OBJECT,
    ),
    TaskType.INIT_CREATIVE_DIRECTION_SELECT: TaskFormatContract(
        OutputKind.JSON,
        required_top_level_keys=(
            "selected_candidate_id",
            "diversity_score",
            "confidence",
            "need_third_candidate",
            "selection_reason",
        ),
        allowed_top_level_keys=(
            "selected_candidate_id",
            "diversity_score",
            "confidence",
            "need_third_candidate",
            "selection_reason",
        ),
        enforce_required_keys=True,
        schema_model="creative_direction_decision",
        task_family="initialization",
        contract_mode=ContractMode.FULL_OBJECT,
    ),
    TaskType.LOCATIONS_FIELD_BACKFILL: TaskFormatContract(
        OutputKind.JSON,
        required_top_level_keys=(),
        enforce_required_keys=False,
        contract_mode=ContractMode.PARTIAL_OBJECT,
    ),
    TaskType.INIT_STORY_CORE_PREMISE: TaskFormatContract(
        OutputKind.JSON,
        required_top_level_keys=("story_core",),
        enforce_required_keys=True,
        contract_mode=ContractMode.FRAGMENT_OBJECT,
    ),
    TaskType.INIT_STORY_WORLD_RULES: TaskFormatContract(
        OutputKind.JSON,
        required_top_level_keys=("world_rules",),
        enforce_required_keys=True,
        contract_mode=ContractMode.FRAGMENT_OBJECT,
    ),
    TaskType.INIT_STORY_CONTINUITY_RULES: TaskFormatContract(
        OutputKind.JSON,
        required_top_level_keys=("continuity_rules",),
        enforce_required_keys=True,
        contract_mode=ContractMode.FRAGMENT_OBJECT,
    ),
    TaskType.INIT_STORY_THEMES_AND_SYMBOLS: TaskFormatContract(
        OutputKind.JSON,
        required_top_level_keys=("themes_and_symbols",),
        enforce_required_keys=True,
        contract_mode=ContractMode.FRAGMENT_OBJECT,
    ),
    TaskType.INIT_CHARACTER_BIBLE: TaskFormatContract(
        OutputKind.JSON,
        required_top_level_keys=("character_bible",),
        enforce_required_keys=True,
        schema_model="character_bible",
        json_schema=_CHARACTER_BIBLE_SCHEMA,
        task_family="initialization",
        contract_mode=ContractMode.FULL_OBJECT,
    ),
    TaskType.INIT_CHARACTER_ROSTER: TaskFormatContract(
        OutputKind.JSON,
        required_top_level_keys=("character_roster",),
        enforce_required_keys=True,
        contract_mode=ContractMode.FRAGMENT_OBJECT,
    ),
    TaskType.INIT_CHARACTER_PROFILE_BATCH: TaskFormatContract(
        OutputKind.JSON,
        required_top_level_keys=("character_profiles",),
        enforce_required_keys=True,
        schema_model="character_profiles",
        json_schema=_CHARACTER_PROFILE_BATCH_SCHEMA,
        task_family="initialization",
        contract_mode=ContractMode.FRAGMENT_OBJECT,
    ),
    TaskType.INIT_CHARACTER_RELATIONSHIP_MATRIX: TaskFormatContract(
        OutputKind.JSON,
        required_top_level_keys=("relationship_matrix",),
        enforce_required_keys=True,
        contract_mode=ContractMode.FRAGMENT_OBJECT,
    ),
    TaskType.INIT_CHARACTER_ARC_PLAN: TaskFormatContract(
        OutputKind.JSON,
        required_top_level_keys=("character_arcs",),
        enforce_required_keys=True,
        contract_mode=ContractMode.FRAGMENT_OBJECT,
    ),
    TaskType.PROFILE_STYLE: TaskFormatContract(
        OutputKind.JSON,
        required_top_level_keys=("modules", "source_elements", "summary", "global_style"),
        allowed_top_level_keys=("modules", "source_elements", "summary", "global_style"),
        enforce_required_keys=True,
        contract_mode=ContractMode.FRAGMENT_OBJECT,
    ),
    TaskType.PROFILE_STRUCTURE: TaskFormatContract(
        OutputKind.JSON,
        required_top_level_keys=(
            "hook_config",
            "strand_config",
            "micro_payoff_config",
            "cool_point_config",
        ),
        allowed_top_level_keys=(
            "hook_config",
            "strand_config",
            "micro_payoff_config",
            "cool_point_config",
        ),
        enforce_required_keys=True,
        contract_mode=ContractMode.FRAGMENT_OBJECT,
    ),
    TaskType.DERIVE_EDITORIAL_CONTRACT: TaskFormatContract(
        OutputKind.JSON,
        required_top_level_keys=(
            "character_voices",
            "climax_markers",
            "denouement_budget",
            "theme_policies",
            "symbol_policies",
            "scene_resistance_rules",
            "revelation_ladder",
            "editorial_element_directives",
            "time_bridge_policies",
            "title_policy",
        ),
        allowed_top_level_keys=(
            "character_voices",
            "climax_markers",
            "denouement_budget",
            "theme_policies",
            "symbol_policies",
            "scene_resistance_rules",
            "revelation_ladder",
            "editorial_element_directives",
            "time_bridge_policies",
            "title_policy",
            "project_title",
            "expression_channel_budget",
            "expression_channel_profiles",
            "body_signal_budget_per_high_emotion_scene",
            "forbidden_confirmation_phrases",
            "revision_priorities",
        ),
        enforce_required_keys=True,
        json_schema=_EDITORIAL_CONTRACT_SCHEMA,
        contract_mode=ContractMode.FULL_OBJECT,
    ),
    TaskType.DERIVE_EDITORIAL_CHARACTER_VOICES: TaskFormatContract(
        OutputKind.JSON,
        required_top_level_keys=("character_voices",),
        allowed_top_level_keys=("character_voices",),
        enforce_required_keys=True,
        json_schema=_EDITORIAL_CHARACTER_VOICES_SCHEMA,
        contract_mode=ContractMode.FRAGMENT_OBJECT,
    ),
    TaskType.DERIVE_EDITORIAL_STRUCTURE: TaskFormatContract(
        OutputKind.JSON,
        required_top_level_keys=(
            "climax_markers",
            "denouement_budget",
            "revelation_ladder",
            "time_bridge_policies",
            "title_policy",
        ),
        allowed_top_level_keys=(
            "climax_markers",
            "denouement_budget",
            "revelation_ladder",
            "time_bridge_policies",
            "title_policy",
        ),
        enforce_required_keys=True,
        json_schema=_EDITORIAL_STRUCTURE_SCHEMA,
        contract_mode=ContractMode.FRAGMENT_OBJECT,
    ),
    TaskType.DERIVE_EDITORIAL_STYLE_CONSTRAINTS: TaskFormatContract(
        OutputKind.JSON,
        required_top_level_keys=(
            "theme_policies",
            "symbol_policies",
            "scene_resistance_rules",
            "expression_channel_budget",
            "expression_channel_profiles",
            "body_signal_budget_per_high_emotion_scene",
            "forbidden_confirmation_phrases",
            "revision_priorities",
        ),
        allowed_top_level_keys=(
            "theme_policies",
            "symbol_policies",
            "scene_resistance_rules",
            "expression_channel_budget",
            "expression_channel_profiles",
            "body_signal_budget_per_high_emotion_scene",
            "forbidden_confirmation_phrases",
            "revision_priorities",
        ),
        enforce_required_keys=True,
        json_schema=_EDITORIAL_STYLE_CONSTRAINTS_SCHEMA,
        contract_mode=ContractMode.FRAGMENT_OBJECT,
    ),
    TaskType.DERIVE_EDITORIAL_ELEMENT_DIRECTIVES: TaskFormatContract(
        OutputKind.JSON,
        required_top_level_keys=("editorial_element_directives",),
        allowed_top_level_keys=("editorial_element_directives",),
        enforce_required_keys=True,
        contract_mode=ContractMode.FRAGMENT_OBJECT,
    ),
    TaskType.EXTRACT_EXPRESSION_OBSERVATIONS: TaskFormatContract(
        OutputKind.JSON,
        required_top_level_keys=("observations", "skipped_reason", "source_text_hash"),
        allowed_top_level_keys=("observations", "skipped_reason", "source_text_hash"),
        enforce_required_keys=True,
        schema_model="expression_observation_extraction",
        task_family="checking",
        contract_mode=ContractMode.FRAGMENT_OBJECT,
    ),
    TaskType.BLUEPRINT_ELEMENT_SELECT: TaskFormatContract(
        OutputKind.JSON,
        required_top_level_keys=_BLUEPRINT_ELEMENT_SELECT_KEYS,
        enforce_required_keys=True,
        allowed_top_level_keys=_BLUEPRINT_ELEMENT_SELECT_KEYS,
        schema_model="blueprint_element_select",
        task_family="planning",
        contract_mode=ContractMode.FULL_OBJECT,
    ),
    TaskType.PLAN_OUTLINE: TaskFormatContract(
        OutputKind.JSON,
        required_top_level_keys=(
            "synopsis",
            "volume_mode",
            "volumes",
            "narrative_phases",
            "key_turning_points",
            "character_arcs",
            "subplot_plan",
            "suspense_schedule",
            "ending_strategy",
            "emotional_arcs",
            "causal_chains",
            "subplot_collisions",
            "subversion_points",
            "chapter_rhythm_curve",
        ),
        enforce_required_keys=True,
        allowed_top_level_keys=(
            "synopsis",
            "volume_mode",
            "volumes",
            "narrative_phases",
            "key_turning_points",
            "character_arcs",
            "subplot_plan",
            "suspense_schedule",
            "ending_strategy",
            "emotional_arcs",
            "causal_chains",
            "subplot_collisions",
            "subversion_points",
            "chapter_rhythm_curve",
        ),
        schema_model="narrative_blueprint",
        task_family="planning",
        contract_mode=ContractMode.FULL_OBJECT,
    ),
    TaskType.PLAN_OUTLINE_BATCH: TaskFormatContract(
        OutputKind.JSON,
        required_top_level_keys=("chapters",),
        enforce_required_keys=True,
        allowed_top_level_keys=("chapters",),
        schema_model="plan_outline_batch",
        json_schema=_PLAN_OUTLINE_BATCH_SCHEMA,
        task_family="planning",
        contract_mode=ContractMode.FRAGMENT_OBJECT,
    ),
    TaskType.ADJUDICATE_BLUEPRINT_COHERENCE: TaskFormatContract(
        OutputKind.JSON,
        required_top_level_keys=(
            "schema_version",
            "dimension",
            "verdict",
            "score",
            "issues",
            "summary",
            "metadata",
            "source_refs",
            "repair_scope",
            "preserve",
            "change_intent",
            "blocked",
        ),
        enforce_required_keys=True,
        json_schema=_audit_report_v2_compat_schema(),
        contract_mode=ContractMode.FULL_OBJECT,
    ),
    TaskType.ADJUDICATE_OUTLINE_INHERITANCE: TaskFormatContract(
        OutputKind.JSON,
        required_top_level_keys=(
            "schema_version",
            "dimension",
            "verdict",
            "score",
            "issues",
            "summary",
            "metadata",
            "source_refs",
            "repair_scope",
            "preserve",
            "change_intent",
            "blocked",
        ),
        enforce_required_keys=True,
        json_schema=_audit_report_v2_compat_schema(),
        contract_mode=ContractMode.FULL_OBJECT,
    ),
    TaskType.PLAN_OUTLINE_CONTINUE: TaskFormatContract(
        OutputKind.JSON,
        required_top_level_keys=("chapters",),
        enforce_required_keys=True,
        allowed_top_level_keys=("chapters",),
        schema_model="plan_outline_batch",
        json_schema=_PLAN_OUTLINE_BATCH_SCHEMA,
        task_family="planning",
        contract_mode=ContractMode.FRAGMENT_OBJECT,
    ),
    TaskType.INIT_ENTITY_REGISTRY: TaskFormatContract(
        OutputKind.JSON,
        required_top_level_keys=("entities",),
        enforce_required_keys=True,
        contract_mode=ContractMode.FULL_OBJECT,
    ),
    TaskType.INIT_NARRATIVE_CONTRACT: TaskFormatContract(
        OutputKind.JSON,
        required_top_level_keys=(
            "world_rules",
            "character_arcs",
            "plot_threads",
            "promise_plan",
            "notes",
        ),
        enforce_required_keys=True,
        contract_mode=ContractMode.FULL_OBJECT,
    ),
    TaskType.PLAN_CHAPTER_CONTRACTS: TaskFormatContract(
        OutputKind.JSON,
        required_top_level_keys=("chapter_contracts",),
        enforce_required_keys=True,
        json_schema=_chapter_contracts_schema(),
        contract_mode=ContractMode.FULL_OBJECT,
    ),
    TaskType.SYNTHESIZE_INIT_RESEARCH_DOSSIER: TaskFormatContract(
        OutputKind.JSON,
        required_top_level_keys=(
            "summary",
            "real_world_constraints",
            "terminology",
            "inspiration_notes",
            "uncertainty_notes",
            "source_refs",
        ),
        allowed_top_level_keys=(
            "summary",
            "real_world_constraints",
            "terminology",
            "inspiration_notes",
            "uncertainty_notes",
            "source_refs",
            "warnings",
        ),
        enforce_required_keys=True,
        contract_mode=ContractMode.FULL_OBJECT,
    ),
    TaskType.GROUND_OUTLINE_RESEARCH: TaskFormatContract(
        OutputKind.JSON,
        required_top_level_keys=(
            "summary",
            "global_notes",
            "chapter_notes",
            "fact_risks",
            "terminology",
            "source_refs",
        ),
        allowed_top_level_keys=(
            "summary",
            "global_notes",
            "chapter_notes",
            "fact_risks",
            "terminology",
            "source_refs",
            "warnings",
        ),
        enforce_required_keys=True,
        contract_mode=ContractMode.FULL_OBJECT,
    ),
    TaskType.PLAN_INIT_RESEARCH_QUERIES: TaskFormatContract(
        OutputKind.JSON,
        required_top_level_keys=(
            "queries",
            "knowledge_gaps",
        ),
        allowed_top_level_keys=(
            "queries",
            "knowledge_gaps",
            "warnings",
        ),
        enforce_required_keys=True,
        contract_mode=ContractMode.FULL_OBJECT,
    ),
    TaskType.SYNTHESIZE_MODEL_PRIOR_RESEARCH: TaskFormatContract(
        OutputKind.JSON,
        required_top_level_keys=(
            "notes",
            "terminology",
            "uncertainty_notes",
        ),
        allowed_top_level_keys=(
            "notes",
            "terminology",
            "uncertainty_notes",
            "warnings",
        ),
        enforce_required_keys=True,
        contract_mode=ContractMode.FULL_OBJECT,
    ),
    TaskType.DERIVE_INIT_COHERENCE_PROFILE: TaskFormatContract(
        OutputKind.JSON,
        required_top_level_keys=(
            "genre_tags",
            "narrative_modes",
            "project_ontology",
            "conflict_lens",
            "extraction_guidance",
            "summary",
        ),
        allowed_top_level_keys=(
            "genre_tags",
            "narrative_modes",
            "project_ontology",
            "conflict_lens",
            "extraction_guidance",
            "summary",
        ),
        enforce_required_keys=True,
        contract_mode=ContractMode.FULL_OBJECT,
    ),
    TaskType.REFINE_INIT_COHERENCE_PROFILE: TaskFormatContract(
        OutputKind.JSON,
        required_top_level_keys=(
            "genre_tags",
            "narrative_modes",
            "project_ontology",
            "conflict_lens",
            "extraction_guidance",
            "summary",
        ),
        allowed_top_level_keys=(
            "genre_tags",
            "narrative_modes",
            "project_ontology",
            "conflict_lens",
            "extraction_guidance",
            "summary",
        ),
        enforce_required_keys=True,
        contract_mode=ContractMode.FULL_OBJECT,
    ),
    TaskType.INIT_COHERENCE_ONTOLOGY: TaskFormatContract(
        OutputKind.JSON,
        required_top_level_keys=("genre_tags", "narrative_modes", "project_ontology"),
        allowed_top_level_keys=("genre_tags", "narrative_modes", "project_ontology"),
        enforce_required_keys=True,
        contract_mode=ContractMode.FRAGMENT_OBJECT,
    ),
    TaskType.INIT_COHERENCE_EXTRACTION_GUIDE: TaskFormatContract(
        OutputKind.JSON,
        required_top_level_keys=("extraction_guidance",),
        enforce_required_keys=True,
        contract_mode=ContractMode.FRAGMENT_OBJECT,
    ),
    TaskType.INIT_COHERENCE_CONFLICT_RULES: TaskFormatContract(
        OutputKind.JSON,
        required_top_level_keys=("conflict_lens",),
        enforce_required_keys=True,
        contract_mode=ContractMode.FRAGMENT_OBJECT,
    ),
    TaskType.INIT_COHERENCE_PAYOFF_RULES: TaskFormatContract(
        OutputKind.JSON,
        required_top_level_keys=("payoff_types", "summary"),
        enforce_required_keys=True,
        contract_mode=ContractMode.FRAGMENT_OBJECT,
    ),
    TaskType.EXTRACT_INIT_COHERENCE_CLAIMS: TaskFormatContract(
        OutputKind.JSON,
        required_top_level_keys=("claims", "coverage_status", "unprocessed_source_refs"),
        allowed_top_level_keys=(
            "claims",
            "coverage_status",
            "unprocessed_source_refs",
            "summary",
        ),
        enforce_required_keys=True,
        contract_mode=ContractMode.FRAGMENT_OBJECT,
    ),
    TaskType.EXTRACT_BLUEPRINT_HOLISTIC_CLAIMS: TaskFormatContract(
        OutputKind.JSON,
        required_top_level_keys=("claims", "coverage_status", "unprocessed_source_refs"),
        allowed_top_level_keys=(
            "claims",
            "coverage_status",
            "unprocessed_source_refs",
            "summary",
        ),
        enforce_required_keys=True,
        contract_mode=ContractMode.FRAGMENT_OBJECT,
    ),
    TaskType.ADJUDICATE_INIT_CONFLICT_CANDIDATES: TaskFormatContract(
        OutputKind.JSON,
        required_top_level_keys=(
            "schema_version",
            "dimension",
            "verdict",
            "score",
            "issues",
            "summary",
            "metadata",
            "source_refs",
            "repair_scope",
            "preserve",
            "change_intent",
            "blocked",
        ),
        enforce_required_keys=True,
        json_schema=_audit_report_v2_compat_schema(),
        contract_mode=ContractMode.FULL_OBJECT,
    ),
    TaskType.ADJUDICATE_CONTRACT_COHERENCE: TaskFormatContract(
        OutputKind.JSON,
        required_top_level_keys=(
            "verdict",
            "issues",
            "source_refs",
            "repair_scope",
            "preserve",
            "change_intent",
            "blocked",
            "summary",
        ),
        enforce_required_keys=True,
        contract_mode=ContractMode.FULL_OBJECT,
    ),
    TaskType.REPAIR_INIT_ARTIFACT_PATCH: TaskFormatContract(
        OutputKind.JSON,
        required_top_level_keys=("patches", "summary"),
        enforce_required_keys=True,
        contract_mode=ContractMode.PATCH_PLAN,
    ),
    TaskType.REFINE_INIT_ARTIFACTS_FROM_SYNOPSIS: TaskFormatContract(
        OutputKind.JSON,
        required_top_level_keys=(
            "suggestions",
            "repair_scope",
            "patches",
            "preserve",
            "risks",
            "summary",
        ),
        enforce_required_keys=True,
        contract_mode=ContractMode.PATCH_PLAN,
    ),
    TaskType.PLAN_CHAPTER: TaskFormatContract(
        OutputKind.JSON,
        required_top_level_keys=(
            "scene_intents",
            "world_rule_applications",
            "opening_contract",
            "closing_contract",
            "required_state_transitions",
            "required_literals",
            "chapter_type",
            "emotional_arc",
            "relationship_evolution",
            "forbidden_elements",
            "forbidden_elements_soft",
            "forbidden_elements_quota",
            "intentional_callbacks",
            "foreshadowing_plan",
            "key_revelations",
            "cross_scene_intent",
        ),
        enforce_required_keys=True,
        contract_mode=ContractMode.FULL_OBJECT,
    ),
    TaskType.PLAN_CHAPTER_SCENES: TaskFormatContract(
        OutputKind.JSON,
        required_top_level_keys=("scene_plan",),
        enforce_required_keys=True,
        contract_mode=ContractMode.FRAGMENT_OBJECT,
    ),
    TaskType.VALIDATE_SCENE_PLAN: TaskFormatContract(
        OutputKind.JSON,
        required_top_level_keys=("valid", "issues", "parallel_groups", "serial_edges", "summary"),
        enforce_required_keys=True,
        contract_mode=ContractMode.FULL_OBJECT,
    ),
    TaskType.BRIDGE_CHAPTER: TaskFormatContract(
        OutputKind.JSON,
        required_top_level_keys=(
            "opening_time",
            "opening_location",
            "opening_pov",
            "transition_mode",
            "emotional_carryover",
            "action_handoff",
            "causal_link",
            "pending_questions",
            "forbidden_repetition",
            "opening_acceptance_criteria",
        ),
        enforce_required_keys=True,
        contract_mode=ContractMode.FULL_OBJECT,
    ),
    TaskType.CHECK_ALIGNMENT: TaskFormatContract(
        OutputKind.JSON,
        required_top_level_keys=(
            "alignment_score",
            "risk_level",
            "summary",
            "findings",
            "missing_main_points",
            "supportive_subplot_points",
            "weak_subplot_points",
            "repair_actions",
        ),
        enforce_required_keys=True,
        contract_mode=ContractMode.FULL_OBJECT,
    ),
    TaskType.ELEMENT_PROGRESS_ARBITER: TaskFormatContract(
        OutputKind.JSON,
        required_top_level_keys=("status", "confidence", "reason"),
        enforce_required_keys=True,
        contract_mode=ContractMode.FULL_OBJECT,
    ),
    TaskType.CHECK_CHAPTER: TaskFormatContract(
        OutputKind.JSON,
        required_top_level_keys=(
            "risk_level",
            "summary",
            "prompt_leaks",
            "factual_errors",
            "continuity_errors",
            "expression_errors",
            "repair_actions",
            "forbidden_element_findings",
        ),
        enforce_required_keys=True,
        contract_mode=ContractMode.FULL_OBJECT,
    ),
    TaskType.CHECK_EDITORIAL: TaskFormatContract(
        OutputKind.JSON,
        required_top_level_keys=("summary", "findings", "revision_plan", "metrics"),
        enforce_required_keys=True,
        json_schema=_EDITORIAL_AUDIT_SCHEMA,
        schema_model="editorial_audit",
        task_family="checking",
        contract_mode=ContractMode.FULL_OBJECT,
    ),
    TaskType.CHECK_CONTINUITY: TaskFormatContract(
        OutputKind.JSON,
        required_top_level_keys=("continuity_score", "summary", "issues"),
        enforce_required_keys=True,
        contract_mode=ContractMode.FULL_OBJECT,
    ),
    TaskType.VALIDATE_CAUSAL: TaskFormatContract(
        OutputKind.JSON,
        required_top_level_keys=("causal_score", "summary", "causal_link_verified", "issues"),
        enforce_required_keys=True,
        contract_mode=ContractMode.FULL_OBJECT,
    ),
    TaskType.EXTRACT_CANON: TaskFormatContract(
        OutputKind.JSON,
        required_top_level_keys=(
            "canon_delta",
            "creative_report",
            "chapter_exit_state",
            "character_state_deltas",
            "relationship_deltas",
            "plot_thread_deltas",
            "structured_summary",
        ),
        enforce_required_keys=True,
        contract_mode=ContractMode.FULL_OBJECT,
        # The legacy all-in-one extraction is too large to safely hand-write
        # JSON on a prompt-only provider.  The split path is the default; when
        # an operator explicitly disables it, require a provider-native JSON
        # control rather than consuming a doomed monolithic request.
        require_native_structured_output=True,
    ),
    TaskType.EXTRACT_CHAPTER_SUMMARY_EXIT: TaskFormatContract(
        OutputKind.JSON,
        required_top_level_keys=("chapter_exit_state", "structured_summary"),
        enforce_required_keys=True,
        contract_mode=ContractMode.FRAGMENT_OBJECT,
    ),
    TaskType.EXTRACT_CANON_DELTA: TaskFormatContract(
        OutputKind.JSON,
        required_top_level_keys=("canon_delta",),
        enforce_required_keys=True,
        contract_mode=ContractMode.FRAGMENT_OBJECT,
    ),
    TaskType.EXTRACT_CREATIVE_REPORT: TaskFormatContract(
        OutputKind.JSON,
        required_top_level_keys=("creative_report",),
        enforce_required_keys=True,
        contract_mode=ContractMode.FRAGMENT_OBJECT,
    ),
    TaskType.EXTRACT_CHARACTER_STATE_DELTAS: TaskFormatContract(
        OutputKind.JSON,
        required_top_level_keys=("character_state_deltas",),
        enforce_required_keys=True,
        contract_mode=ContractMode.FRAGMENT_OBJECT,
    ),
    TaskType.EXTRACT_RELATIONSHIP_DELTAS: TaskFormatContract(
        OutputKind.JSON,
        required_top_level_keys=("relationship_deltas",),
        enforce_required_keys=True,
        contract_mode=ContractMode.FRAGMENT_OBJECT,
    ),
    TaskType.EXTRACT_PLOT_THREAD_DELTAS: TaskFormatContract(
        OutputKind.JSON,
        required_top_level_keys=("plot_thread_deltas",),
        enforce_required_keys=True,
        contract_mode=ContractMode.FRAGMENT_OBJECT,
    ),
    TaskType.EXTRACT_CANDIDATE_STATE_DELTAS: TaskFormatContract(
        OutputKind.JSON,
        required_top_level_keys=("candidates",),
        enforce_required_keys=True,
        contract_mode=ContractMode.FRAGMENT_OBJECT,
        json_schema=_CANDIDATE_STATE_DELTAS_SCHEMA,
        require_native_structured_output=True,
    ),
    TaskType.ADJUDICATE_ENTITY_REFERENCES: TaskFormatContract(
        OutputKind.JSON,
        required_top_level_keys=("decisions", "summary"),
        enforce_required_keys=True,
        json_schema=_ENTITY_REFERENCE_ADJUDICATION_SCHEMA,
        contract_mode=ContractMode.FULL_OBJECT,
        # The adjudicator performs strict local parity, candidate, evidence,
        # and canonical-name validation. Prompt-only routes are therefore safe
        # and must remain eligible when no configured provider supports native schemas.
        require_native_structured_output=False,
    ),
    TaskType.ADJUDICATE_STATE_DELTA: TaskFormatContract(
        OutputKind.JSON,
        required_top_level_keys=("candidate_id", "verdict", "severity", "rationale"),
        enforce_required_keys=True,
        json_schema=_STATE_DELTA_ADJUDICATION_SCHEMA,
        contract_mode=ContractMode.FULL_OBJECT,
        require_native_structured_output=True,
    ),
    TaskType.ADJUDICATE_CONTRACT_COMPLETION: TaskFormatContract(
        OutputKind.JSON,
        required_top_level_keys=(
            "verdict",
            "severity",
            "rationale",
            "repair_or_replan_decision",
            "missing_required_progressions",
            "missing_knowledge_ops",
            "forbidden_progression_hits",
            "future_leak_hits",
            "evidence_quotes",
            "should_block_archive",
            "contract_completion_score",
        ),
        allowed_top_level_keys=(
            "verdict",
            "severity",
            "rationale",
            "repair_or_replan_decision",
            "missing_required_progressions",
            "missing_knowledge_ops",
            "forbidden_progression_hits",
            "future_leak_hits",
            "cognitive_constraint_hits",
            "unexpected_progressions",
            "unaccepted_knowledge_ops",
            "evidence_quotes",
            "should_block_archive",
            "contract_completion_score",
        ),
        enforce_required_keys=True,
        json_schema=_CONTRACT_COMPLETION_ADJUDICATION_SCHEMA,
        contract_mode=ContractMode.FULL_OBJECT,
        require_native_structured_output=True,
    ),
    TaskType.ADJUDICATE_FACT_CONFLICT: TaskFormatContract(
        OutputKind.JSON,
        required_top_level_keys=("verdict", "severity", "rationale"),
        enforce_required_keys=True,
        contract_mode=ContractMode.FULL_OBJECT,
    ),
    TaskType.ADJUDICATE_FINAL_STATE: TaskFormatContract(
        OutputKind.JSON,
        required_top_level_keys=(
            "verdict",
            "accepted_candidate_ids",
            "pending_candidate_ids",
            "repair_candidate_ids",
            "should_block_archive",
            "summary",
        ),
        enforce_required_keys=True,
        json_schema=_FINAL_STATE_ADJUDICATION_SCHEMA,
        contract_mode=ContractMode.FULL_OBJECT,
        # This output controls state writes and archive blocking. Prompt-only
        # JSON is not an acceptable boundary for an irreversible commit step;
        # route it to JSON object/schema capable fallbacks before any API call.
        require_native_structured_output=True,
    ),
    TaskType.VOLUME_AUDIT: TaskFormatContract(
        OutputKind.JSON,
        required_top_level_keys=(
            "volume_number",
            "volume_summary",
            "milestone_status",
            "carry_over_characters",
            "carry_over_items",
            "carry_over_world_fact_keys",
            "carry_over_foreshadowing_ids",
            "next_volume_focus",
        ),
        enforce_required_keys=True,
        contract_mode=ContractMode.FULL_OBJECT,
    ),
    # Short form
    TaskType.SHORT_BLUEPRINT: TaskFormatContract(
        OutputKind.JSON,
        required_top_level_keys=(
            "synopsis",
            "anchor_elements",
            "narrative_phases",
            "turning_points",
            "character_arcs",
            "emotional_arc",
            "ending_strategy",
        ),
        enforce_required_keys=True,
        contract_mode=ContractMode.FULL_OBJECT,
    ),
    TaskType.BEATS: TaskFormatContract(
        OutputKind.JSON,
        required_top_level_keys=("beats",),
        enforce_required_keys=True,
        contract_mode=ContractMode.FULL_OBJECT,
    ),
    TaskType.EVALUATE: TaskFormatContract(
        OutputKind.JSON,
        required_top_level_keys=(
            "scores",
            "overall_score",
            "passed",
            "threshold",
            "summary",
            "repair_suggestions",
        ),
        enforce_required_keys=True,
        contract_mode=ContractMode.FULL_OBJECT,
    ),
    TaskType.SHORT_CREATIVE_SUMMARY: TaskFormatContract(
        OutputKind.JSON,
        required_top_level_keys=(
            "characters",
            "narrative_analysis",
            "thematic_analysis",
            "creative_highlights",
            "improvement_suggestions",
            "beat_fulfillment",
        ),
        enforce_required_keys=True,
        contract_mode=ContractMode.FULL_OBJECT,
    ),
    # Text generation
    TaskType.DRAFT: TaskFormatContract(OutputKind.TEXT, contract_mode=ContractMode.TEXT_ONLY),
    TaskType.EDIT: TaskFormatContract(OutputKind.TEXT, contract_mode=ContractMode.TEXT_ONLY),
    TaskType.DRAFT_CHAPTER: TaskFormatContract(
        OutputKind.TEXT, contract_mode=ContractMode.TEXT_ONLY
    ),
    TaskType.DRAFT_SCENE: TaskFormatContract(OutputKind.TEXT, contract_mode=ContractMode.TEXT_ONLY),
    TaskType.EDIT_CHAPTER: TaskFormatContract(
        OutputKind.TEXT, contract_mode=ContractMode.TEXT_ONLY
    ),
    TaskType.WAVE_CHAPTER: TaskFormatContract(
        OutputKind.TEXT, contract_mode=ContractMode.TEXT_ONLY
    ),
    TaskType.POLISH_CHAPTER: TaskFormatContract(
        OutputKind.TEXT, contract_mode=ContractMode.TEXT_ONLY
    ),
    TaskType.POLISH_SUBPLOT: TaskFormatContract(
        OutputKind.JSON,
        required_top_level_keys=("subplots",),
        enforce_required_keys=True,
        contract_mode=ContractMode.FRAGMENT_OBJECT,
    ),
    TaskType.REPAIR_CAUSAL: TaskFormatContract(
        OutputKind.TEXT, contract_mode=ContractMode.TEXT_ONLY
    ),
    TaskType.REPAIR_READING_POWER: TaskFormatContract(
        OutputKind.TEXT, contract_mode=ContractMode.TEXT_ONLY
    ),
    TaskType.REPAIR_GUARDRAIL: TaskFormatContract(
        OutputKind.TEXT, contract_mode=ContractMode.TEXT_ONLY
    ),
    TaskType.REPAIR_ADJUDICATED_ISSUE: TaskFormatContract(
        OutputKind.TEXT, contract_mode=ContractMode.TEXT_ONLY
    ),
    # RECONCILE_ENTITIES: LLM-driven entity reconciliation for unresolved
    # chapter-contract references.  Outputs a JSON array of resolution objects.
    TaskType.RECONCILE_ENTITIES: TaskFormatContract(
        OutputKind.JSON,
        required_top_level_keys=("resolutions",),
        enforce_required_keys=True,
        json_schema={
            "type": "object",
            "required": ["resolutions"],
            "properties": {
                "resolutions": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": ["unresolved_name", "resolution", "confidence", "reasoning"],
                        "properties": {
                            "unresolved_name": {"type": "string"},
                            "resolution": {
                                "type": "string",
                                "enum": ["alias", "new_entity", "remove"],
                            },
                            "canonical_name": {"type": ["string", "null"]},
                            "entity_type": {"type": ["string", "null"]},
                            "relationship": {"type": ["string", "null"]},
                            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                            "reasoning": {"type": "string"},
                        },
                        "additionalProperties": False,
                    },
                }
            },
            "additionalProperties": False,
        },
        contract_mode=ContractMode.FULL_OBJECT,
    ),
    # Structured repair/compression helpers
    TaskType.REPAIR_STRATEGY_DIAGNOSE: TaskFormatContract(
        OutputKind.JSON,
        required_top_level_keys=(
            "preferred_strategy",
            "confidence",
            "reason",
            "root_causes",
            "risk_flags",
            "diagnostic_summary",
        ),
        enforce_required_keys=True,
        contract_mode=ContractMode.FULL_OBJECT,
    ),
    # REPAIR_CONTINUITY: Template outputs pure TEXT (not JSON); revised_text is
    # produced internally by ContinuityRepairStep._extract_revised_text().
    TaskType.REPAIR_CONTINUITY: TaskFormatContract(
        OutputKind.TEXT, contract_mode=ContractMode.TEXT_ONLY
    ),
    # PATCH_CHAPTER outputs a JSON patch plan; revised_text is produced internally
    # by PatchExecutorV2 or apply_patches().
    TaskType.PATCH_CHAPTER: TaskFormatContract(
        OutputKind.JSON,
        required_top_level_keys=("patches",),
        enforce_required_keys=True,
        contract_mode=ContractMode.PATCH_PLAN,
    ),
    TaskType.CONTEXT_COMPRESS: TaskFormatContract(
        OutputKind.JSON,
        required_top_level_keys=("items",),
        enforce_required_keys=True,
        contract_mode=ContractMode.FRAGMENT_OBJECT,
    ),
    TaskType.PLOT_GUARD_JUDGE: TaskFormatContract(
        OutputKind.JSON,
        required_top_level_keys=(
            "decision",
            "risk_level",
            "outline_action",
            "entity_actions",
            "next_chapter_constraints",
            "reasoning_brief",
            "targeted_repairs",
        ),
        enforce_required_keys=True,
        contract_mode=ContractMode.FULL_OBJECT,
    ),
    TaskType.GUARD_CONSTRAINT_CHECK: TaskFormatContract(
        OutputKind.JSON,
        required_top_level_keys=("status", "confidence", "evidence", "notes"),
        enforce_required_keys=True,
        contract_mode=ContractMode.FULL_OBJECT,
    ),
    TaskType.KNOWLEDGE_BOUNDARY_AUDIT: TaskFormatContract(
        OutputKind.JSON,
        required_top_level_keys=("verdict", "issues"),
        enforce_required_keys=True,
        json_schema=_KNOWLEDGE_BOUNDARY_AUDIT_SCHEMA,
        schema_model="knowledge_boundary_audit",
        task_family="checking",
        contract_mode=ContractMode.FULL_OBJECT,
    ),
    TaskType.AUDIT_POV_DRIFT: TaskFormatContract(
        OutputKind.JSON,
        required_top_level_keys=("verdict", "issues"),
        enforce_required_keys=True,
        contract_mode=ContractMode.FULL_OBJECT,
    ),
    TaskType.INIT_KNOWLEDGE_BOUNDARIES: TaskFormatContract(
        OutputKind.JSON,
        required_top_level_keys=("characters",),
        enforce_required_keys=True,
        json_schema=_KNOWLEDGE_BOUNDARIES_SCHEMA,
        schema_model="init_knowledge_boundaries",
        task_family="initialization",
        contract_mode=ContractMode.FULL_OBJECT,
    ),
    TaskType.EXTRACT_KNOWLEDGE_DELTAS: TaskFormatContract(
        OutputKind.JSON,
        required_top_level_keys=("knowledge_deltas",),
        enforce_required_keys=True,
        json_schema=_KNOWLEDGE_DELTAS_SCHEMA,
        schema_model="extract_knowledge_deltas",
        task_family="checking",
        contract_mode=ContractMode.FULL_OBJECT,
    ),
    TaskType.REPAIR_KNOWLEDGE_BOUNDARY: TaskFormatContract(
        OutputKind.JSON,
        required_top_level_keys=(),
        enforce_required_keys=False,
        json_schema=_REPAIR_KNOWLEDGE_BOUNDARY_SCHEMA,
        schema_model="repair_knowledge_boundary",
        task_family="repair",
        contract_mode=ContractMode.PARTIAL_OBJECT,
    ),
    # Book-level audit
    TaskType.BOOK_CONSISTENCY: TaskFormatContract(
        OutputKind.JSON,
        required_top_level_keys=("issues", "repair_plan", "summary", "consistency_score"),
        enforce_required_keys=True,
        json_schema=_BOOK_CONSISTENCY_SCHEMA,
        schema_model="book_consistency",
        task_family="book_audit",
        contract_mode=ContractMode.FULL_OBJECT,
    ),
    TaskType.BOOK_CONSISTENCY_NAMING: TaskFormatContract(
        OutputKind.JSON,
        required_top_level_keys=("issues",),
        enforce_required_keys=True,
        json_schema=_BOOK_CONSISTENCY_ISSUES_ONLY_SCHEMA,
        schema_model="book_consistency_issues",
        task_family="book_audit",
        contract_mode=ContractMode.FULL_OBJECT,
    ),
    TaskType.BOOK_CONSISTENCY_TIMELINE: TaskFormatContract(
        OutputKind.JSON,
        required_top_level_keys=("issues",),
        enforce_required_keys=True,
        json_schema=_BOOK_CONSISTENCY_ISSUES_ONLY_SCHEMA,
        schema_model="book_consistency_issues",
        task_family="book_audit",
        contract_mode=ContractMode.FULL_OBJECT,
    ),
    TaskType.BOOK_CONSISTENCY_WORLD_RULE: TaskFormatContract(
        OutputKind.JSON,
        required_top_level_keys=("issues",),
        enforce_required_keys=True,
        json_schema=_BOOK_CONSISTENCY_ISSUES_ONLY_SCHEMA,
        schema_model="book_consistency_issues",
        task_family="book_audit",
        contract_mode=ContractMode.FULL_OBJECT,
    ),
    TaskType.BOOK_CONSISTENCY_CHARACTER_STATE: TaskFormatContract(
        OutputKind.JSON,
        required_top_level_keys=("issues",),
        enforce_required_keys=True,
        json_schema=_BOOK_CONSISTENCY_ISSUES_ONLY_SCHEMA,
        schema_model="book_consistency_issues",
        task_family="book_audit",
        contract_mode=ContractMode.FULL_OBJECT,
    ),
    TaskType.BOOK_CONSISTENCY_PLOT_THREAD: TaskFormatContract(
        OutputKind.JSON,
        required_top_level_keys=("issues",),
        enforce_required_keys=True,
        json_schema=_BOOK_CONSISTENCY_ISSUES_ONLY_SCHEMA,
        schema_model="book_consistency_issues",
        task_family="book_audit",
        contract_mode=ContractMode.FULL_OBJECT,
    ),
    TaskType.BOOK_CONSISTENCY_NARRATIVE_DRIFT: TaskFormatContract(
        OutputKind.JSON,
        required_top_level_keys=("issues",),
        enforce_required_keys=True,
        json_schema=_BOOK_CONSISTENCY_ISSUES_ONLY_SCHEMA,
        schema_model="book_consistency_issues",
        task_family="book_audit",
        contract_mode=ContractMode.FULL_OBJECT,
    ),
    # Book-level per-chapter verification (stage 2)
    TaskType.BOOK_CONSISTENCY_VERIFY: TaskFormatContract(
        OutputKind.JSON,
        required_top_level_keys=("verified_issues",),
        enforce_required_keys=True,
        json_schema=_BOOK_CONSISTENCY_VERIFY_SCHEMA,
        schema_model="book_consistency_verify",
        task_family="book_audit",
        contract_mode=ContractMode.FULL_OBJECT,
    ),
    TaskType.BOOK_EDITORIAL_AUDIT: TaskFormatContract(
        OutputKind.JSON,
        required_top_level_keys=("summary", "findings", "revision_plan", "metrics"),
        enforce_required_keys=True,
        json_schema=_EDITORIAL_AUDIT_SCHEMA,
        schema_model="editorial_audit",
        task_family="book_audit",
        contract_mode=ContractMode.FULL_OBJECT,
    ),
    TaskType.BOOK_EDITORIAL_STRUCTURE_AUDIT: TaskFormatContract(
        OutputKind.JSON,
        required_top_level_keys=("summary", "findings", "revision_plan", "metrics"),
        enforce_required_keys=True,
        json_schema=_EDITORIAL_AUDIT_SCHEMA,
        schema_model="editorial_audit",
        task_family="book_audit",
        contract_mode=ContractMode.FULL_OBJECT,
    ),
    TaskType.BOOK_EDITORIAL_VOICE_AUDIT: TaskFormatContract(
        OutputKind.JSON,
        required_top_level_keys=("summary", "findings", "revision_plan", "metrics"),
        enforce_required_keys=True,
        json_schema=_EDITORIAL_AUDIT_SCHEMA,
        schema_model="editorial_audit",
        task_family="book_audit",
        contract_mode=ContractMode.FULL_OBJECT,
    ),
    TaskType.BOOK_EDITORIAL_LANGUAGE_AUDIT: TaskFormatContract(
        OutputKind.JSON,
        required_top_level_keys=("summary", "findings", "revision_plan", "metrics"),
        enforce_required_keys=True,
        json_schema=_EDITORIAL_AUDIT_SCHEMA,
        schema_model="editorial_audit",
        task_family="book_audit",
        contract_mode=ContractMode.FULL_OBJECT,
    ),
    TaskType.BOOK_EDITORIAL_THEME_SYMBOL_AUDIT: TaskFormatContract(
        OutputKind.JSON,
        required_top_level_keys=("summary", "findings", "revision_plan", "metrics"),
        enforce_required_keys=True,
        json_schema=_EDITORIAL_AUDIT_SCHEMA,
        schema_model="editorial_audit",
        task_family="book_audit",
        contract_mode=ContractMode.FULL_OBJECT,
    ),
    TaskType.BOOK_EDITORIAL_ELEMENT_AUDIT: TaskFormatContract(
        OutputKind.JSON,
        required_top_level_keys=("summary", "findings", "revision_plan", "metrics"),
        enforce_required_keys=True,
        json_schema=_EDITORIAL_AUDIT_SCHEMA,
        schema_model="editorial_audit",
        task_family="book_audit",
        contract_mode=ContractMode.FULL_OBJECT,
    ),
    TaskType.MACRO_GUARD_AUDIT: TaskFormatContract(
        OutputKind.JSON,
        required_top_level_keys=(
            "dimensions",
            "recommended_action",
            "drift_score",
            "findings",
            "adjustment_plan",
            "confidence",
            "reasoning",
        ),
        enforce_required_keys=True,
        json_schema=_MACRO_GUARD_SCHEMA,
        schema_model="macro_guard_audit",
        task_family="checking",
        contract_mode=ContractMode.FULL_OBJECT,
    ),
    TaskType.SUMMARY_DRIFT_CHECK: TaskFormatContract(
        OutputKind.JSON,
        required_top_level_keys=(
            "issues",
            "facts_checked",
            "facts_missing",
            "facts_contradicted",
        ),
        allowed_top_level_keys=(
            "issues",
            "facts_checked",
            "facts_missing",
            "facts_contradicted",
            "summary",
        ),
        enforce_required_keys=True,
        json_schema=_SUMMARY_DRIFT_CHECK_SCHEMA,
        task_family="checking",
        contract_mode=ContractMode.FULL_OBJECT,
    ),
    # Memory: summaries
    TaskType.SUMMARIZE_CHAPTER: TaskFormatContract(
        OutputKind.JSON,
        required_top_level_keys=("summary",),
        enforce_required_keys=True,
        contract_mode=ContractMode.FRAGMENT_OBJECT,
    ),
    TaskType.SUMMARIZE_VOLUME: TaskFormatContract(
        OutputKind.JSON,
        required_top_level_keys=("summary",),
        enforce_required_keys=True,
        contract_mode=ContractMode.FRAGMENT_OBJECT,
    ),
    TaskType.SUMMARIZE_ARC: TaskFormatContract(
        OutputKind.JSON,
        required_top_level_keys=("summary",),
        enforce_required_keys=True,
        contract_mode=ContractMode.FRAGMENT_OBJECT,
    ),
    TaskType.SUMMARIZE_SCENE: TaskFormatContract(
        OutputKind.JSON,
        required_top_level_keys=("summary",),
        enforce_required_keys=True,
        contract_mode=ContractMode.FRAGMENT_OBJECT,
    ),
    # Memory: motif extraction
    TaskType.EXTRACT_MOTIFS: TaskFormatContract(
        OutputKind.JSON,
        required_top_level_keys=("motifs",),
        enforce_required_keys=True,
        contract_mode=ContractMode.FRAGMENT_OBJECT,
    ),
    # Memory: critic agents
    TaskType.CRITIC_CONTINUITY: TaskFormatContract(
        OutputKind.JSON,
        required_top_level_keys=("issues",),
        enforce_required_keys=True,
        schema_model="critic_continuity",
        json_schema=_CRITIC_CONTINUITY_SCHEMA,
        task_family="memory",
        contract_mode=ContractMode.FULL_OBJECT,
    ),
    TaskType.CRITIC_CHARACTER: TaskFormatContract(
        OutputKind.JSON,
        required_top_level_keys=("issues",),
        enforce_required_keys=True,
        contract_mode=ContractMode.FULL_OBJECT,
    ),
    TaskType.CRITIC_CAUSAL: TaskFormatContract(
        OutputKind.JSON,
        required_top_level_keys=("causal_breaks",),
        enforce_required_keys=True,
        contract_mode=ContractMode.FULL_OBJECT,
    ),
    TaskType.CRITIC_STRENGTHS: TaskFormatContract(
        OutputKind.JSON,
        required_top_level_keys=("strengths",),
        enforce_required_keys=True,
        contract_mode=ContractMode.FULL_OBJECT,
    ),
    # Memory: compression
    TaskType.ADAPTIVE_COMPRESS: TaskFormatContract(
        OutputKind.JSON,
        required_top_level_keys=("items",),
        enforce_required_keys=True,
        contract_mode=ContractMode.FRAGMENT_OBJECT,
    ),
    TaskType.VERIFY_COMPRESSION: TaskFormatContract(
        OutputKind.JSON,
        required_top_level_keys=("quality_score", "recommendation"),
        enforce_required_keys=True,
        contract_mode=ContractMode.FULL_OBJECT,
    ),
    # Character & outline tools
    TaskType.ENRICH_CHARACTER: TaskFormatContract(
        OutputKind.JSON,
        required_top_level_keys=("appearance", "personality", "backstory", "arc"),
        enforce_required_keys=True,
        contract_mode=ContractMode.FRAGMENT_OBJECT,
        allowed_top_level_keys=(
            "appearance",
            "personality",
            "backstory",
            "arc",
            "relationships",
            "voice",
            "gender",
            "social_status",
            "abilities",
            "name",
            "notes",
        ),
        json_schema=_ENRICH_CHARACTER_SCHEMA,
    ),
    TaskType.ADJUDICATE_CHARACTER_INTRODUCTION: TaskFormatContract(
        OutputKind.JSON,
        required_top_level_keys=("decisions", "summary"),
        enforce_required_keys=True,
        contract_mode=ContractMode.FULL_OBJECT,
    ),
    TaskType.INTRODUCE_CHARACTER: TaskFormatContract(
        OutputKind.JSON,
        required_top_level_keys=(
            "name",
            "role",
            "gender",
            "social_status",
            "abilities",
            "appearance",
            "personality",
            "backstory",
            "arc",
            "relationships",
            "voice",
            "notes",
        ),
        enforce_required_keys=True,
        json_schema=_INTRODUCE_CHARACTER_SCHEMA,
        contract_mode=ContractMode.FULL_OBJECT,
    ),
    TaskType.ADJUST_OUTLINE: TaskFormatContract(
        OutputKind.JSON,
        required_top_level_keys=("adjusted_chapters", "adjustment_summary"),
        enforce_required_keys=True,
        contract_mode=ContractMode.PATCH_PLAN,
    ),
    # AI creative tools (dynamic strict JSON, resolved with render/call context)
    TaskType.AUTHORING_CHAT: TaskFormatContract(
        OutputKind.JSON, required_top_level_keys=("reply", "proposals", "actions"),
        allowed_top_level_keys=("reply", "proposals", "actions"), enforce_required_keys=True,
        response_schema_model=AuthoringReply, json_schema=AuthoringReply.model_json_schema(),
        schema_model="AuthoringReply", contract_mode=ContractMode.FULL_OBJECT,
    ),
    TaskType.GENERATE_CONFIG: TaskFormatContract(
        OutputKind.JSON,
        required_top_level_keys=(*_SHORT_CONFIG_KEYS, *_AI_CONFIG_METADATA_KEYS),
        enforce_required_keys=True,
        allowed_top_level_keys=(*_SHORT_CONFIG_KEYS, *_AI_CONFIG_METADATA_KEYS),
        schema_model="generate_config_short",
        json_schema=_config_contract_schema(
            (*_SHORT_CONFIG_KEYS, *_AI_CONFIG_METADATA_KEYS),
            required=(*_SHORT_CONFIG_KEYS, *_AI_CONFIG_METADATA_KEYS),
        ),
        task_family="creative_config",
        contract_mode=ContractMode.FULL_OBJECT,
    ),
    TaskType.POLISH_CONFIG: TaskFormatContract(
        OutputKind.JSON,
        required_top_level_keys=(),
        enforce_required_keys=False,
        allowed_top_level_keys=(*config_contract_keys_for_mode(None), *_AI_CONFIG_METADATA_KEYS),
        schema_model="polish_config_partial",
        json_schema=_config_contract_schema(
            (*config_contract_keys_for_mode(None), *_AI_CONFIG_METADATA_KEYS),
            required=(),
        ),
        task_family="creative_config",
        strict_mode=False,
        contract_mode=ContractMode.PARTIAL_OBJECT,
    ),
    TaskType.POLISH_OUTLINE: TaskFormatContract(
        OutputKind.JSON,
        required_top_level_keys=("adjusted_chapters", "polish_suggestions"),
        enforce_required_keys=True,
        allowed_top_level_keys=("adjusted_chapters", "polish_suggestions"),
        schema_model="polish_outline",
        json_schema=_POLISH_OUTLINE_SCHEMA,
        task_family="planning",
        contract_mode=ContractMode.PATCH_PLAN,
    ),
    TaskType.REVIEW_FUTURE_OUTLINE: TaskFormatContract(
        OutputKind.JSON,
        required_top_level_keys=(
            "selected",
            "facts",
            "user_intent",
            "motivation",
            "causality",
            "promises",
            "contracts",
            "creative_gain",
            "confidence",
        ),
        enforce_required_keys=True,
        task_family="planning",
        contract_mode=ContractMode.FULL_OBJECT,
    ),
    # Reading power eval
    TaskType.EVALUATE_READING_POWER: TaskFormatContract(
        OutputKind.JSON,
        required_top_level_keys=_READING_POWER_EVAL_REQUIRED_KEYS,
        enforce_required_keys=True,
        schema_model="reading_power_eval",
        json_schema=_READING_POWER_EVAL_SCHEMA,
        task_family="checking",
        contract_mode=ContractMode.FULL_OBJECT,
    ),
    TaskType.REPAIR_SEMANTIC_VERIFY: TaskFormatContract(
        output_kind=OutputKind.JSON,
        required_top_level_keys=("issue_resolved", "confidence", "reasoning"),
        enforce_required_keys=True,
        contract_mode=ContractMode.FULL_OBJECT,
    ),
    # Humanize scan
    TaskType.HUMANIZE_SCAN: TaskFormatContract(
        OutputKind.JSON,
        required_top_level_keys=_HUMANIZE_REPORT_REQUIRED_KEYS,
        enforce_required_keys=True,
        schema_model="humanize_report",
        json_schema=_HUMANIZE_REPORT_SCHEMA,
        task_family="checking",
        contract_mode=ContractMode.FULL_OBJECT,
        # Blank scans have been observed when prompt-only models consume the
        # completion budget in hidden reasoning.  Select a JSON-capable route
        # before starting this per-chapter call.
        require_native_structured_output=True,
    ),
    # Humanize paragraph-level rewrite
    TaskType.HUMANIZE_PARAGRAPH_REWRITE: TaskFormatContract(
        OutputKind.TEXT,
        required_top_level_keys=(),
        enforce_required_keys=False,
        task_family="checking",
        contract_mode=ContractMode.TEXT_ONLY,
    ),
    # ── TTS / Voice ───────────────────────────────────────────────────────────
    TaskType.TTS_BUILD_NARRATOR_PROFILE: TaskFormatContract(
        OutputKind.JSON,
        required_top_level_keys=(
            "voice_type",
            "base_speed",
            "emotional_range",
            "narration_distance",
            "style_keywords",
        ),
        enforce_required_keys=True,
        task_family="tts",
        contract_mode=ContractMode.FULL_OBJECT,
    ),
    TaskType.TTS_GENERATE_DUBBING_SCRIPT: TaskFormatContract(
        OutputKind.JSON,
        required_top_level_keys=(
            "segments",
            "bgm_suggestions",
            "sfx_cues",
            "soundscapes",
            "scene_transitions",
        ),
        enforce_required_keys=True,
        task_family="tts",
        contract_mode=ContractMode.FULL_OBJECT,
    ),
    TaskType.TTS_ADJUDICATE_SCRIPT_SEGMENTS: TaskFormatContract(
        OutputKind.JSON,
        required_top_level_keys=("decisions", "summary"),
        enforce_required_keys=True,
        json_schema=_TTS_SCRIPT_SEGMENT_ADJUDICATION_SCHEMA,
        task_family="tts",
        contract_mode=ContractMode.FULL_OBJECT,
        # All TTS tasks perform comprehensive local response validation
        # (shape checks, index coverage, confidence bounds, semantic alignment).
        # Prompt-only routes are therefore safe and must remain eligible when
        # no configured provider supports native schemas.
        require_native_structured_output=False,
    ),
    TaskType.TTS_REVIEW_DUBBING_SCRIPT: TaskFormatContract(
        OutputKind.JSON,
        required_top_level_keys=(
            "decisions",
            "reviewed_segment_count",
            "overall_verdict",
            "summary",
        ),
        enforce_required_keys=True,
        json_schema=_TTS_DUBBING_REVIEW_SCHEMA,
        task_family="tts",
        contract_mode=ContractMode.FULL_OBJECT,
        require_native_structured_output=False,
    ),
    TaskType.TTS_ANALYZE_DUBBING_STYLE: TaskFormatContract(
        OutputKind.JSON,
        required_top_level_keys=(
            "language",
            "confidence",
            "narration_traits",
            "dialogue_traits",
            "rhythm_rules",
            "pause_rules",
            "performance_direction_rules",
            "sound_design_rules",
            "forbidden_tendencies",
        ),
        enforce_required_keys=True,
        json_schema=_TTS_DUBBING_STYLE_PROFILE_SCHEMA,
        task_family="tts",
        contract_mode=ContractMode.FULL_OBJECT,
        require_native_structured_output=False,
    ),
    TaskType.TTS_REWRITE_SPOKEN_TEXT: TaskFormatContract(
        OutputKind.JSON,
        required_top_level_keys=("rewrites",),
        enforce_required_keys=True,
        json_schema=_TTS_SPOKEN_REWRITE_SCHEMA,
        task_family="tts",
        contract_mode=ContractMode.FULL_OBJECT,
        require_native_structured_output=False,
    ),
    TaskType.TTS_EMOTION_LABEL: TaskFormatContract(
        OutputKind.JSON,
        required_top_level_keys=("emotions",),
        enforce_required_keys=True,
        json_schema=_TTS_EMOTION_LABEL_SCHEMA,
        task_family="tts",
        contract_mode=ContractMode.FULL_OBJECT,
        require_native_structured_output=False,
    ),
    TaskType.TTS_ADJUDICATE_VOICE_MATCH: TaskFormatContract(
        OutputKind.JSON,
        required_top_level_keys=("decisions", "summary"),
        enforce_required_keys=True,
        json_schema=_TTS_VOICE_MATCH_ADJUDICATION_SCHEMA,
        task_family="tts",
        contract_mode=ContractMode.FULL_OBJECT,
        require_native_structured_output=False,
    ),
    TaskType.TTS_SOUND_DESIGN: TaskFormatContract(
        OutputKind.JSON,
        required_top_level_keys=(
            "sfx_cues",
            "bgm_needs",
            "soundscapes",
            "scene_transitions",
        ),
        enforce_required_keys=True,
        task_family="tts",
        contract_mode=ContractMode.FULL_OBJECT,
        require_native_structured_output=False,
    ),
    TaskType.ADAPT_SCREENPLAY: TaskFormatContract(
        OutputKind.JSON,
        required_top_level_keys=("title", "scenes"),
        enforce_required_keys=True,
        task_family="film",
        contract_mode=ContractMode.FULL_OBJECT,
        require_native_structured_output=False,
    ),
    TaskType.COMPLIANCE_CHECK: TaskFormatContract(
        OutputKind.JSON,
        required_top_level_keys=("findings", "summary"),
        enforce_required_keys=True,
        task_family="film",
        contract_mode=ContractMode.FULL_OBJECT,
        require_native_structured_output=False,
    ),
    TaskType.VISION_QC_SCORE: TaskFormatContract(
        OutputKind.JSON,
        required_top_level_keys=("dimensions", "overall_score", "summary"),
        enforce_required_keys=True,
        task_family="film",
        contract_mode=ContractMode.FULL_OBJECT,
        require_native_structured_output=False,
    ),
    TaskType.DRAMA_SERIES_PLAN: TaskFormatContract(
        OutputKind.JSON,
        required_top_level_keys=(
            "title",
            "total_episodes",
            "three_acts",
            "paywall_beats",
            "thrill_matrix",
            "waveform_stages",
            "antagonist_system",
        ),
        enforce_required_keys=True,
        task_family="film",
        contract_mode=ContractMode.FULL_OBJECT,
        require_native_structured_output=False,
    ),
    TaskType.EPISODE_OUTLINE: TaskFormatContract(
        OutputKind.JSON,
        required_top_level_keys=("episodes",),
        enforce_required_keys=True,
        task_family="film",
        contract_mode=ContractMode.FULL_OBJECT,
        require_native_structured_output=False,
    ),
    TaskType.EPISODE_SCREENPLAY: TaskFormatContract(
        OutputKind.JSON,
        required_top_level_keys=("episode_number", "scenes"),
        enforce_required_keys=True,
        task_family="film",
        contract_mode=ContractMode.FULL_OBJECT,
        require_native_structured_output=False,
    ),
    TaskType.FILM_SHOT_LAYOUT: TaskFormatContract(
        OutputKind.JSON,
        required_top_level_keys=("scenes",),
        enforce_required_keys=True,
        task_family="film",
        contract_mode=ContractMode.FULL_OBJECT,
        require_native_structured_output=False,
    ),
    TaskType.COMIC_PANEL_LAYOUT: TaskFormatContract(
        OutputKind.JSON,
        required_top_level_keys=("pages",),
        enforce_required_keys=True,
        task_family="comic",
        contract_mode=ContractMode.FULL_OBJECT,
        require_native_structured_output=False,
    ),
}


def get_task_format_contract(task_type: TaskType) -> TaskFormatContract | None:
    """Return output-format contract for one task, if defined."""
    return _enrich_task_format_contract(task_type, _TASK_FORMAT_CONTRACTS.get(task_type))


def _is_polish_outline_title_repair_context(context: dict[str, Any] | None) -> bool:
    if not isinstance(context, dict):
        return False
    if str(context.get("polish_mode") or "").strip() == "title_repair":
        return True
    focus_fields = context.get("focus_fields")
    if isinstance(focus_fields, list):
        normalized = {str(item).strip() for item in focus_fields if str(item).strip()}
        return normalized == {"title"}
    return False


def resolve_task_format_contract(
    task_type: TaskType,
    context: dict[str, Any] | None = None,
) -> TaskFormatContract | None:
    """Return the effective task contract, including dynamic runtime fields.

    Uses a module-level cache for the common case where context is None,
    avoiding repeated deepcopy and schema enrichment for the same task type.
    """
    # Fast-path: cache for context-free resolution (most common in pipeline)
    if context is None:
        cached = _resolve_cache.get(task_type)
        if cached is not None:
            return cached

    if task_type in {TaskType.GENERATE_CONFIG, TaskType.POLISH_CONFIG}:
        contract = _dynamic_config_contract(task_type, context)
        result = replace(
            contract,
            json_schema=effective_contract_json_schema(task_type, contract, context),
        )
    elif task_type == TaskType.PLAN_OUTLINE:
        fragment_contract = _dynamic_plan_outline_contract(context)
        if fragment_contract:
            result = fragment_contract
        else:
            plan_outline_contract = get_task_format_contract(task_type)
            if plan_outline_contract:
                result = replace(
                    plan_outline_contract,
                    json_schema=_typed_contract_schema(
                        task_type,
                        plan_outline_contract,
                        base_schema=_blueprint_fragment_base_schema(),
                    ),
                )
            else:
                result = None
    elif task_type == TaskType.POLISH_OUTLINE and _is_polish_outline_title_repair_context(context):
        polish_outline_contract = get_task_format_contract(task_type)
        if polish_outline_contract:
            result = replace(
                polish_outline_contract,
                schema_model="polish_outline_title_repair",
                json_schema=_POLISH_OUTLINE_TITLE_REPAIR_SCHEMA,
            )
        else:
            result = None
    else:
        base_contract = get_task_format_contract(task_type)
        if base_contract and base_contract.output_kind == OutputKind.JSON:
            result = replace(
                base_contract,
                json_schema=effective_contract_json_schema(task_type, base_contract, context),
            )
        else:
            result = base_contract

    # Cache the result for context-free case
    if context is None and result is not None:
        _resolve_cache[task_type] = result
    return result


# Module-level cache for resolve_task_format_contract when context is None.
# Avoids repeated deepcopy and schema enrichment for the same task type.
_resolve_cache: dict[TaskType, TaskFormatContract] = {}


def merge_required_keys_for_task(
    task_type: TaskType,
    explicit_required_keys: tuple[str, ...] = (),
    context: dict[str, Any] | None = None,
) -> tuple[str, ...]:
    """Merge explicit required keys with strict task-contract keys."""
    contract = resolve_task_format_contract(task_type, context)
    contract_keys: tuple[str, ...] = ()
    if contract and contract.output_kind == OutputKind.JSON and contract.enforce_required_keys:
        contract_keys = contract.required_top_level_keys

    merged: list[str] = []
    seen: set[str] = set()
    for key in (*explicit_required_keys, *contract_keys):
        clean = str(key or "").strip()
        if not clean or clean in seen:
            continue
        seen.add(clean)
        merged.append(clean)
    return tuple(merged)


def _schema_restricts_top_level_object(schema: dict[str, Any] | None) -> bool:
    """Return True when a schema carries strict top-level object boundaries."""
    if not isinstance(schema, dict):
        return False
    resolved = _resolve_local_schema_ref(schema, schema)
    return bool(
        resolved.get("type") == "object"
        and isinstance(resolved.get("properties"), dict)
        and resolved.get("additionalProperties") is False
    )


def should_validate_contract_schema(
    contract: TaskFormatContract | None,
    *,
    include_contract_required_keys: bool,
) -> bool:
    """Decide whether local retry/repair must enforce a contract JSON Schema.

    ``include_contract_required_keys=False`` is used by fragment calls that pass
    only the current subtask's explicit keys. It should not become a blanket
    schema bypass: strict fragment/patch schemas still define the normal parser
    contract and must be checked locally.
    """
    if contract is None or contract.output_kind != OutputKind.JSON or not contract.json_schema:
        return False
    if include_contract_required_keys:
        return True

    mode = contract.effective_contract_mode
    if mode == ContractMode.PARTIAL_OBJECT:
        return False
    if mode in {ContractMode.FRAGMENT_OBJECT, ContractMode.PATCH_PLAN}:
        return bool(
            contract.allowed_top_level_keys
            or contract.schema_model
            or _schema_restricts_top_level_object(contract.json_schema)
        )
    return False


def _json_value_type(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__


def _matches_json_schema_type(value: Any, expected: Any) -> bool:
    expected_types = expected if isinstance(expected, list) else [expected]
    for item in expected_types:
        if item == "null" and value is None:
            return True
        if item == "boolean" and isinstance(value, bool):
            return True
        if item == "integer" and isinstance(value, int) and not isinstance(value, bool):
            return True
        if item == "number" and isinstance(value, (int, float)) and not isinstance(value, bool):
            return True
        if item == "string" and isinstance(value, str):
            return True
        if item == "array" and isinstance(value, list):
            return True
        if item == "object" and isinstance(value, dict):
            return True
    return False


def _resolve_local_schema_ref(
    schema: dict[str, Any], root_schema: dict[str, Any]
) -> dict[str, Any]:
    ref = schema.get("$ref")
    if not isinstance(ref, str) or not ref.startswith("#/$defs/"):
        return schema
    defs = root_schema.get("$defs")
    if not isinstance(defs, dict):
        return schema
    name = ref.rsplit("/", 1)[-1]
    target = defs.get(name)
    if isinstance(target, dict):
        merged = {key: value for key, value in schema.items() if key != "$ref"}
        resolved = deepcopy(target)
        resolved.update(merged)
        return resolved
    return schema


def _validate_json_schema_subset(
    value: Any,
    schema: dict[str, Any],
    path: str = "$",
    *,
    root_schema: dict[str, Any] | None = None,
) -> list[str]:
    """Validate the JSON Schema subset emitted by TaskFormatContract."""
    if not schema:
        return []

    root = root_schema or schema
    schema = _resolve_local_schema_ref(schema, root)

    any_of = schema.get("anyOf")
    if isinstance(any_of, list) and any_of:
        branch_errors: list[list[str]] = []
        for branch in any_of:
            if not isinstance(branch, dict):
                continue
            branch_result = _validate_json_schema_subset(value, branch, path, root_schema=root)
            if not branch_result:
                return []
            branch_errors.append(branch_result)
        return branch_errors[0] if branch_errors else []

    errors: list[str] = []
    expected_type = schema.get("type")
    if expected_type and not _matches_json_schema_type(value, expected_type):
        expected = (
            "/".join(expected_type) if isinstance(expected_type, list) else str(expected_type)
        )
        return [f"{path}: expected {expected}, got {_json_value_type(value)}"]

    enum_values = schema.get("enum")
    if isinstance(enum_values, list) and enum_values and value not in enum_values:
        allowed = ", ".join(str(item) for item in enum_values)
        errors.append(f"{path}: expected one of [{allowed}], got {value!r}")

    if isinstance(value, str):
        min_length = schema.get("minLength")
        if isinstance(min_length, int) and len(value) < min_length:
            errors.append(f"{path}: expected length >= {min_length}, got {len(value)}")
        max_length = schema.get("maxLength")
        if isinstance(max_length, int) and len(value) > max_length:
            errors.append(f"{path}: expected length <= {max_length}, got {len(value)}")
        pattern = schema.get("pattern")
        if isinstance(pattern, str) and not re.search(pattern, value):
            errors.append(f"{path}: does not match pattern `{pattern}`")

    if isinstance(value, int | float) and not isinstance(value, bool):
        minimum = schema.get("minimum")
        if isinstance(minimum, int | float) and value < minimum:
            errors.append(f"{path}: expected >= {minimum}, got {value}")
        maximum = schema.get("maximum")
        if isinstance(maximum, int | float) and value > maximum:
            errors.append(f"{path}: expected <= {maximum}, got {value}")

    if isinstance(value, dict):
        required = schema.get("required")
        if isinstance(required, list):
            for key in required:
                if isinstance(key, str) and key not in value:
                    errors.append(f"{path}: missing required property `{key}`")

        raw_properties = schema.get("properties")
        properties = raw_properties if isinstance(raw_properties, dict) else {}
        if properties:
            for key, child_schema in properties.items():
                if key not in value or not isinstance(child_schema, dict):
                    continue
                errors.extend(
                    _validate_json_schema_subset(
                        value[key],
                        child_schema,
                        f"{path}.{key}",
                        root_schema=root,
                    )
                )

        if schema.get("additionalProperties") is False:
            extra = sorted(str(key) for key in value if key not in properties)
            for key in extra:
                errors.append(f"{path}: unexpected property `{key}`")
        elif isinstance(schema.get("additionalProperties"), dict):
            extra_schema = schema["additionalProperties"]
            for key in sorted((key for key in value if key not in properties), key=str):
                errors.extend(
                    _validate_json_schema_subset(
                        value[key],
                        extra_schema,
                        f"{path}.{key}",
                        root_schema=root,
                    )
                )

    if isinstance(value, list):
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, item in enumerate(value):
                errors.extend(
                    _validate_json_schema_subset(
                        item,
                        item_schema,
                        f"{path}[{index}]",
                        root_schema=root,
                    )
                )

    return errors


def _schema_error_to_issue(message: str) -> FormatSchemaIssue:
    path = "$"
    detail = message
    if ":" in message:
        raw_path, detail = message.split(":", 1)
        path = raw_path.strip() or "$"
        detail = detail.strip()

    if "missing required property `" in detail:
        key = detail.split("missing required property `", 1)[-1].split("`", 1)[0]
        return FormatSchemaIssue(
            path=f"{path}.{key}" if path != "$" else f"$.{key}",
            issue_type="missing_key",
            expected="required property",
            actual="missing",
            message=message,
        )
    if "unexpected property `" in detail:
        key = detail.split("unexpected property `", 1)[-1].split("`", 1)[0]
        return FormatSchemaIssue(
            path=f"{path}.{key}" if path != "$" else f"$.{key}",
            issue_type="extra_key",
            expected="allowed property",
            actual=key,
            message=message,
        )
    if detail.startswith("expected one of "):
        return FormatSchemaIssue(
            path=path,
            issue_type="enum_mismatch",
            expected=detail.split(", got ", 1)[0].removeprefix("expected "),
            actual=detail.split(", got ", 1)[-1] if ", got " in detail else "",
            message=message,
        )
    if detail.startswith("expected ") and ", got " in detail:
        expected, actual = detail.removeprefix("expected ").split(", got ", 1)
        return FormatSchemaIssue(
            path=path,
            issue_type="type_mismatch",
            expected=expected,
            actual=actual,
            message=message,
        )
    return FormatSchemaIssue(path=path, issue_type="schema_violation", message=message)


def _deduplicate_schema_issues(issues: list[FormatSchemaIssue]) -> list[FormatSchemaIssue]:
    """Collapse duplicate contract/schema reports while preserving first-source priority."""

    deduplicated: list[FormatSchemaIssue] = []
    seen: set[tuple[str, str]] = set()
    for issue in issues:
        key = (issue.path, issue.issue_type)
        if key in seen:
            continue
        seen.add(key)
        deduplicated.append(issue)
    return deduplicated


def collect_json_output_contract_issues(
    task_type: TaskType,
    data: dict[str, Any],
    *,
    context: dict[str, Any] | None = None,
    explicit_required_keys: tuple[str, ...] = (),
    include_contract_required_keys: bool = True,
    validate_allowed_keys: bool = True,
    validate_json_schema: bool = True,
) -> list[FormatSchemaIssue]:
    """Collect machine-readable JSON contract/schema issues for one response."""

    contract = resolve_task_format_contract(task_type, context)
    if contract is None or contract.output_kind != OutputKind.JSON:
        return []

    required_keys = (
        merge_required_keys_for_task(
            task_type,
            explicit_required_keys,
            context=context,
        )
        if include_contract_required_keys
        else tuple(
            dict.fromkeys(
                key.strip()
                for key in explicit_required_keys
                if isinstance(key, str) and key.strip()
            )
        )
    )
    issues: list[FormatSchemaIssue] = []
    for key in required_keys:
        if key not in data:
            issues.append(
                FormatSchemaIssue(
                    path=f"$.{key}",
                    issue_type="missing_key",
                    expected="required top-level key",
                    actual="missing",
                    message=f"Missing required response key: {key}",
                )
            )

    if validate_allowed_keys and contract.allowed_top_level_keys:
        allowed = set(contract.allowed_top_level_keys)
        for key in sorted(str(key) for key in data if key not in allowed):
            issues.append(
                FormatSchemaIssue(
                    path=f"$.{key}",
                    issue_type="extra_key",
                    expected="allowed top-level key",
                    actual=key,
                    message=f"Unexpected response key: {key}",
                )
            )

    if validate_json_schema and contract.json_schema:
        schema_errors = _validate_json_schema_subset(data, contract.json_schema)
        issues.extend(_schema_error_to_issue(error) for error in schema_errors)
    return _deduplicate_schema_issues(issues)


def validate_json_output_contract(
    task_type: TaskType,
    data: dict[str, Any],
    *,
    context: dict[str, Any] | None = None,
    explicit_required_keys: tuple[str, ...] = (),
    include_contract_required_keys: bool = True,
    validate_allowed_keys: bool = True,
    validate_json_schema: bool = True,
) -> None:
    """Validate top-level JSON contract requirements for one response."""
    contract = resolve_task_format_contract(task_type, context)
    if contract is None or contract.output_kind != OutputKind.JSON:
        return

    issues = collect_json_output_contract_issues(
        task_type,
        data,
        context=context,
        explicit_required_keys=explicit_required_keys,
        include_contract_required_keys=include_contract_required_keys,
        validate_allowed_keys=validate_allowed_keys,
        validate_json_schema=validate_json_schema,
    )
    missing = [
        issue.path.removeprefix("$.")
        for issue in issues
        if issue.issue_type == "missing_key" and issue.expected == "required top-level key"
    ]
    if missing:
        raise KeyError("Missing required response key(s): " + ", ".join(missing))

    extra = [
        issue.path.removeprefix("$.")
        for issue in issues
        if issue.issue_type == "extra_key" and issue.expected == "allowed top-level key"
    ]
    if extra:
        raise KeyError("Unexpected response key(s): " + ", ".join(extra))

    schema_issues = [
        issue
        for issue in issues
        if not (issue.issue_type == "missing_key" and issue.expected == "required top-level key")
        and not (issue.issue_type == "extra_key" and issue.expected == "allowed top-level key")
    ]
    if schema_issues:
        schema_errors = [issue.message for issue in schema_issues]
        preview = "; ".join(schema_errors[:8])
        remaining = len(schema_errors) - 8
        suffix = f"; ... +{remaining} more" if remaining > 0 else ""
        raise ValueError("JSON schema validation failed: " + preview + suffix)


def render_prompt_contract_block(
    task_type: TaskType,
    context: dict[str, Any] | None = None,
    *,
    prompt_locale: str = "zh",
) -> str:
    """Render a unified prompt-side format contract block for JSON tasks."""
    contract = resolve_task_format_contract(task_type, context)
    if contract is None:
        return ""

    is_zh_prompt = str(prompt_locale or "zh").lower().startswith("zh")
    contract_mode = contract.effective_contract_mode
    if contract_mode == ContractMode.TEXT_ONLY:
        if not is_zh_prompt:
            return "\n".join(
                [
                    "## Unified Format Contract (system injected)",
                    f"- contract_id: `{contract.contract_id}`",
                    f"- contract_mode: `{contract_mode.value}`",
                    "- Output must be plain fiction prose or revised prose text.",
                    "- Do not output chapter titles or heading wrappers such as "
                    "`# Chapter 1 Title`; start directly with the first narrative sentence.",
                    "- Do not output JSON, Markdown code fences, headings, lists, edit notes, "
                    "self-checks, or meta comments.",
                    "- Do not leak planning/system fields such as `scene_intent`, "
                    "`opening_contract`, `opening_bridge`, `required_outcome`, or "
                    "`exit_target_state`.",
                    "- If internal self-checking finds any of the above, rewrite first; "
                    "the final answer must contain only archivable prose.",
                ]
            )
        return "\n".join(
            [
                "## 统一格式契约（系统注入）",
                f"- contract_id: `{contract.contract_id}`",
                f"- contract_mode: `{contract_mode.value}`",
                "- 输出必须是纯小说正文或修订后的正文文本。",
                "- 不要输出章名/题名；禁止 `# 第一章 标题`、`第 1 章《标题》` 等开头包装，直接从第一句叙事正文开始。",
                "- 禁止 JSON、Markdown 代码块、标题、清单、修改说明、自检过程和元注释。",
                "- 禁止泄露规划/系统字段，例如 `scene_intent`、`opening_contract`、`opening_bridge`、`required_outcome`、`exit_target_state`。",
                "- 若内部自检发现上述内容，必须先重写，最终只输出可归档正文。",
            ]
        )

    if is_zh_prompt:
        lines = [
            "## 统一格式契约（系统注入）",
            f"- contract_id: `{contract.contract_id}`",
            f"- contract_mode: `{contract_mode.value}`",
            "- 输出必须是单一 JSON 对象，可被 `json.loads` 直接解析。",
            "- 禁止 Markdown 代码块、注释、解释性前后缀文本。",
            "- 字段名和字符串必须使用英文双引号；禁止 Python dict、单引号和省略号。",
            '- 字符串内容引用台词、术语或标题时，使用中文引号「」或转义 `\\"`，禁止裸英文双引号。',
            "- 数组元素和对象字段之间必须使用英文逗号，禁止用换行直接连接相邻字符串。",
            "- 禁止把结构字段泄漏到文本字段中；不要在 `description`、`notes` 等字符串里写 `.field: value`、`field=value` 来替代 JSON key。",
        ]
    else:
        lines = [
            "## Unified Format Contract (system injected)",
            f"- contract_id: `{contract.contract_id}`",
            f"- contract_mode: `{contract_mode.value}`",
            "- Output must be one JSON object parseable by `json.loads`.",
            "- Do not output Markdown code fences, comments, or explanatory prefixes/suffixes.",
            "- JSON keys and strings must use double quotes; do not use Python dict syntax, "
            "single-quoted keys/strings, or ellipses.",
            "- When string content mentions dialogue, terms, or titles, use single quotation "
            'marks inside the string or escaped `\\"`; do not use bare inner double quotes.',
            "- Use commas between array items and object fields; do not concatenate adjacent "
            "strings with line breaks.",
            "- Do not leak structural fields into text fields; do not write `.field: value` "
            "or `field=value` inside `description`, `notes`, or similar strings as a "
            "substitute for JSON keys.",
        ]

    if contract_mode == ContractMode.FULL_OBJECT:
        lines.append(
            "- 本任务要求完整对象：必须一次性输出契约声明的全部顶层字段。"
            if is_zh_prompt
            else "- This task requires a full object: output all contract-declared top-level fields."
        )
    elif contract_mode == ContractMode.PARTIAL_OBJECT:
        lines.append(
            "- 本任务要求局部对象：只输出本轮新增或修改的允许字段；不要回显未修改字段。"
            if is_zh_prompt
            else "- This task requires a partial object: output only newly added or modified "
            "allowed fields; do not echo unchanged fields."
        )
    elif contract_mode == ContractMode.PATCH_PLAN:
        lines.append(
            "- 本任务要求补丁计划：输出可应用的修改计划字段，不要重写完整上游对象或正文。"
            if is_zh_prompt
            else "- This task requires a patch plan: output applicable change-plan fields; "
            "do not rewrite the full upstream object or prose."
        )
    elif contract_mode == ContractMode.FRAGMENT_OBJECT:
        lines.append(
            "- 本任务要求片段对象：只输出当前子任务负责的结构片段，不要补全整份上游 artifact。"
            if is_zh_prompt
            else "- This task requires a fragment object: output only the structural fragment "
            "owned by this subtask; do not complete the whole upstream artifact."
        )

    if contract.required_top_level_keys:
        separator = "、" if is_zh_prompt else ", "
        keys_text = separator.join(f"`{key}`" for key in contract.required_top_level_keys)
        lines.append(
            f"- 顶层必须包含字段：{keys_text}。缺失会触发解析重试。"
            if is_zh_prompt
            else f"- Top-level required fields: {keys_text}. Missing fields trigger parse retry."
        )
    if contract.allowed_top_level_keys:
        separator = "、" if is_zh_prompt else ", "
        keys_text = separator.join(f"`{key}`" for key in contract.allowed_top_level_keys)
        lines.append(
            f"- 顶层只允许字段：{keys_text}。不要新增其他顶层 key。"
            if is_zh_prompt
            else f"- Top-level allowed fields only: {keys_text}. Do not add other top-level keys."
        )

    raw_schema = contract.json_schema or _simple_object_schema(contract)
    schema = _schema_for_prompt_display(
        raw_schema,
        protected_top_level_keys=set(contract.required_top_level_keys),
    )
    if schema:
        import json

        lines.extend(
            (
                [
                    "- 下方 JSON Schema 仅供理解校验规则，不是输出样例；其中 "
                    + "、".join(f"`{key}`" for key in _PROMPT_SCHEMA_METADATA_KEYS)
                    + " 等 schema 元字段绝不能作为最终输出 JSON 的 key。",
                    "- JSON Schema（顶层约束，运行时会按此验证）：",
                    json.dumps(schema, ensure_ascii=False),
                ]
                if is_zh_prompt
                else [
                    "- The JSON Schema below explains validation rules; it is not an output "
                    "example. Schema metadata keys such as "
                    + ", ".join(f"`{key}`" for key in _PROMPT_SCHEMA_METADATA_KEYS)
                    + " must never appear as final output JSON keys.",
                    "- JSON Schema (top-level constraints; runtime validates against this):",
                    json.dumps(schema, ensure_ascii=False),
                ]
            )
        )

    return "\n".join(lines)
