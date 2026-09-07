"""Task-level adapters for structured LLM outputs.

These adapters run after generic JSON parsing/repair and before task response
schemas or downstream Pydantic models. They only normalize recoverable shape
drift at the TaskType boundary and never author missing narrative semantics.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from novel_forge.common.constants import TaskType
from novel_forge.core.format_contracts import resolve_task_format_contract
from novel_forge.core.schemas.outline import OUTLINE_TITLE_REPAIR_PLACEHOLDER
from novel_forge.core.utils.type_coerce import coerce_text_list, stringify_text_value
from novel_forge.pipeline.long.services.blueprint.blueprint_payloads import (
    pre_normalize_blueprint_payload,
)
from novel_forge.pipeline.long.services.claim_field_policy import (
    CHARACTER_KNOWLEDGE_COVERAGE_VALUES,
    CLAIM_ENUM_VALUES,
)

_BLUEPRINT_RUNTIME_METADATA_KEYS = frozenset({"schema_version", "created_at"})
_POLICY_TEXT_KEYS = ("policy", "rule", "description", "requirement", "text", "content")
_TITLE_POLICY_KEYS = ("max_reuse", "allowed_repeated_titles", "naming_strategy")
_DENOUEMENT_BUDGET_TEXT_ALIASES = {
    "required_new_functions": ("required_new_functions_description",),
    "forbidden_repeats": ("forbidden_repeats_description",),
}
_REVELATION_LADDER_TEXT_KEYS = (
    "name",
    "label",
    "title",
    "value",
    "text",
    "description",
    "content",
    "policy",
    "rule",
    "summary",
)
_REVELATION_LADDER_TEXT_FIELDS = (
    "thread",
    "stage",
    "trigger",
    "allowed_disclosure",
    "required_action_consequence",
)
_REVELATION_LADDER_OUTPUT_KEYS = (
    "thread",
    "stage",
    "stage_order",
    "target_chapter",
    "trigger",
    "allowed_disclosure",
    "required_action_consequence",
)
_SYMBOL_POLICY_EXPLANATION_POLICY_ALIASES = {
    "never": "never_explain",
    "never_explain": "never_explain",
    "no_explain": "never_explain",
    "forbid_explain": "never_explain",
    "explain_once": "explain_once",
    "once": "explain_once",
    "single": "explain_once",
    "explain_on_escalation": "explain_on_escalation",
    "on_escalation": "explain_on_escalation",
    "escalation": "explain_on_escalation",
    "free": "free",
    "unlimited": "free",
}
_BOOK_CONSISTENCY_ISSUE_TASKS = frozenset(
    {
        TaskType.BOOK_CONSISTENCY,
        TaskType.BOOK_CONSISTENCY_NAMING,
        TaskType.BOOK_CONSISTENCY_TIMELINE,
        TaskType.BOOK_CONSISTENCY_WORLD_RULE,
        TaskType.BOOK_CONSISTENCY_CHARACTER_STATE,
        TaskType.BOOK_CONSISTENCY_PLOT_THREAD,
        TaskType.BOOK_CONSISTENCY_NARRATIVE_DRIFT,
    }
)
_BOOK_CONSISTENCY_ISSUES_ONLY_TASKS = _BOOK_CONSISTENCY_ISSUE_TASKS - {TaskType.BOOK_CONSISTENCY}
_MACRO_GUARD_DIMENSION_KEYS = (
    "outline_alignment",
    "character_arc_consistency",
    "pacing_curve",
    "foreshadowing_recovery",
    "thematic_cohesion",
)
_INIT_KNOWLEDGE_BOUNDARY_KEYS = (
    "known_facts",
    "suspected",
    "misbeliefs",
    "secrets_kept",
    "sensory_access_rules",
)
_INIT_KNOWLEDGE_BOUNDARY_KEY_SET = frozenset(_INIT_KNOWLEDGE_BOUNDARY_KEYS)
_INIT_COHERENCE_ONTOLOGY_TOP_LEVEL_KEYS = (
    "genre_tags",
    "narrative_modes",
    "project_ontology",
)
_INIT_COHERENCE_ONTOLOGY_TOP_LEVEL_KEY_SET = frozenset(_INIT_COHERENCE_ONTOLOGY_TOP_LEVEL_KEYS)
_INIT_COHERENCE_ONTOLOGY_LIST_KEY_HINTS: dict[str, tuple[str, ...]] = {
    "domains": ("domain",),
    "entity_types": ("entity_type",),
    "state_axes": ("axis",),
    "relationship_axes": ("axis",),
    "payoff_types": ("payoff_type", "type"),
    "irreversible_event_markers": ("marker", "event_type"),
    "temporal_markers": ("marker", "temporal_marker"),
}
_JSON_SCHEMA_LEAK_TOP_LEVEL_KEYS = frozenset(
    {
        "$schema",
        "type",
        "properties",
        "items",
        "required",
        "additionalProperties",
        "minItems",
        "maxItems",
    }
)


@dataclass(frozen=True)
class TaskOutputAdapterResult:
    """Result from applying a task output adapter."""

    changed: bool = False
    adapter: str = ""
    changed_keys: tuple[str, ...] = ()
    metadata: dict[str, Any] | None = None


def apply_task_output_adapter(
    data: dict[str, Any],
    task_type: TaskType,
    *,
    context: dict[str, Any] | None = None,
) -> TaskOutputAdapterResult:
    """Normalize parsed model output for the given task type in-place."""
    if task_type in {TaskType.PLAN_CHAPTER, TaskType.PLAN_CHAPTER_SCENES}:
        return _adapt_plan_requirement_envelopes(data, task_type)
    if task_type == TaskType.PLAN_OUTLINE:
        return _adapt_plan_outline_response(data, context=context or {})
    if task_type in {TaskType.PLAN_OUTLINE_BATCH, TaskType.PLAN_OUTLINE_CONTINUE}:
        return _adapt_plan_outline_batch_response(data)
    if task_type in {TaskType.GENERATE_CONFIG, TaskType.POLISH_CONFIG}:
        return _adapt_creative_config_response(data, task_type, context=context or {})
    if task_type == TaskType.INIT_COHERENCE_ONTOLOGY:
        return _adapt_init_coherence_ontology_response(data)
    if task_type in {
        TaskType.EXTRACT_INIT_COHERENCE_CLAIMS,
        TaskType.EXTRACT_BLUEPRINT_HOLISTIC_CLAIMS,
    }:
        return _adapt_init_claims_response(data, task_type)
    if task_type == TaskType.EXTRACT_CANDIDATE_STATE_DELTAS:
        return _adapt_candidate_state_deltas_response(data)
    if task_type == TaskType.REFINE_INIT_ARTIFACTS_FROM_SYNOPSIS:
        return _adapt_init_creative_refinement_response(data)
    if task_type in _BOOK_CONSISTENCY_ISSUE_TASKS:
        return _adapt_book_consistency_response(data, task_type)
    if task_type == TaskType.BOOK_CONSISTENCY_VERIFY:
        return _adapt_book_consistency_verify_response(data, context=context or {})
    if task_type == TaskType.GUARD_CONSTRAINT_CHECK:
        return _adapt_guard_constraint_check_response(data)
    if task_type == TaskType.MACRO_GUARD_AUDIT:
        return _adapt_macro_guard_audit_response(data)
    if task_type == TaskType.VOLUME_AUDIT:
        return _adapt_volume_audit_response(data)
    if task_type in {
        TaskType.INIT_CHARACTER_BIBLE,
        TaskType.INIT_CHARACTER_PROFILE_BATCH,
        TaskType.INTRODUCE_CHARACTER,
    }:
        return _adapt_character_profile_response(data, task_type)
    if task_type == TaskType.INIT_KNOWLEDGE_BOUNDARIES:
        return _adapt_init_knowledge_boundaries_response(data)
    if task_type in {
        TaskType.DERIVE_EDITORIAL_CONTRACT,
        TaskType.DERIVE_EDITORIAL_STRUCTURE,
        TaskType.DERIVE_EDITORIAL_STYLE_CONSTRAINTS,
    }:
        return _adapt_editorial_text_lists(data, task_type)
    if task_type == TaskType.ENRICH_CHARACTER:
        return _adapt_enrich_character_response(data)
    if task_type == TaskType.EXTRACT_RELATIONSHIP_DELTAS:
        return _adapt_relationship_deltas_response(data)
    return TaskOutputAdapterResult()


def _adapt_plan_requirement_envelopes(
    data: dict[str, Any],
    task_type: TaskType,
) -> TaskOutputAdapterResult:
    """Remove input-only guidance copies from otherwise valid PLAN payloads.

    ``GuidanceRequirement`` extends ``RequirementSemantics`` with
    ``requirement_id`` and ``text``. Prompt-only structured-output providers can
    therefore copy those two source-card fields into the narrower nested
    ``requirement`` envelope. The values are redundant there: provenance and
    fulfillment policy already live in the six RequirementSemantics fields,
    while the narrative wording belongs to the owning reference/contract.

    Only this known inheritance drift is removed. Any other unknown nested key
    remains in place so the closed response schema still rejects it.
    """

    payload = data.get("scene_plan") if task_type == TaskType.PLAN_CHAPTER_SCENES else data
    if not isinstance(payload, dict):
        return TaskOutputAdapterResult()

    changed_paths: list[str] = []
    guidance_removed = "guidance_requirements" in payload
    if guidance_removed:
        payload.pop("guidance_requirements", None)
        changed_paths.append("guidance_requirements")

    duplicate_fields_removed = 0

    cross_scene = payload.get("cross_scene_intent")
    references = (
        cross_scene.get("cross_scene_references") if isinstance(cross_scene, dict) else None
    )
    if isinstance(references, list):
        for index, reference in enumerate(references):
            if not isinstance(reference, dict):
                continue
            requirement = reference.get("requirement")
            if not isinstance(requirement, dict):
                continue
            for key in ("requirement_id", "text"):
                if key not in requirement:
                    continue
                requirement.pop(key, None)
                duplicate_fields_removed += 1
                changed_paths.append(
                    f"cross_scene_intent.cross_scene_references[{index}].requirement.{key}"
                )

    required_literals = payload.get("required_literals")
    if isinstance(required_literals, list):
        for index, literal in enumerate(required_literals):
            if not isinstance(literal, dict):
                continue
            requirement = literal.get("requirement")
            if not isinstance(requirement, dict):
                continue
            for key in ("requirement_id", "text"):
                if key not in requirement:
                    continue
                requirement.pop(key, None)
                duplicate_fields_removed += 1
                changed_paths.append(f"required_literals[{index}].requirement.{key}")

    if not changed_paths:
        return TaskOutputAdapterResult()

    changed_keys = (
        ("scene_plan",)
        if task_type == TaskType.PLAN_CHAPTER_SCENES
        else tuple(
            key
            for key in ("guidance_requirements", "cross_scene_intent", "required_literals")
            if key in {path.split(".", 1)[0].split("[", 1)[0] for path in changed_paths}
        )
    )
    return TaskOutputAdapterResult(
        changed=True,
        adapter="plan_requirement_envelope_normalizer",
        changed_keys=changed_keys,
        metadata={
            "task": task_type.value,
            "input_guidance_removed": guidance_removed,
            "duplicate_requirement_fields_removed": duplicate_fields_removed,
            "changed_paths": changed_paths,
        },
    )


def _has_adapter_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, dict)):
        return bool(value)
    return True


def _adapt_init_coherence_ontology_response(data: dict[str, Any]) -> TaskOutputAdapterResult:
    """Fold recoverable ontology-fragment drift back into ``project_ontology``."""

    before = _top_level_fingerprint(data)
    ontology_raw = data.get("project_ontology")
    ontology = dict(ontology_raw) if isinstance(ontology_raw, dict) else {}
    moved_semantic_keys: list[str] = []
    dropped_keys: list[str] = []

    if _has_adapter_value(ontology_raw) and not isinstance(ontology_raw, dict):
        ontology["raw_project_ontology"] = stringify_text_value(ontology_raw)

    for key in list(data):
        if key in _INIT_COHERENCE_ONTOLOGY_TOP_LEVEL_KEY_SET:
            continue
        value = data.pop(key, None)
        if key in _JSON_SCHEMA_LEAK_TOP_LEVEL_KEYS or key in _BLUEPRINT_RUNTIME_METADATA_KEYS:
            dropped_keys.append(key)
            continue
        if not _has_adapter_value(value):
            dropped_keys.append(key)
            continue
        if not _has_adapter_value(ontology.get(key)):
            ontology[key] = value
        else:
            overflow = ontology.setdefault("_adapter_notes", [])
            if isinstance(overflow, list):
                overflow.append({key: value})
            else:
                ontology["_adapter_notes"] = [overflow, {key: value}]
        moved_semantic_keys.append(key)

    data["genre_tags"] = coerce_text_list(
        data.get("genre_tags"),
        preferred_keys=("tag", "genre", "name", "label", "value", "text"),
    )
    data["narrative_modes"] = coerce_text_list(
        data.get("narrative_modes"),
        preferred_keys=("name", "mode", "label", "title", "value", "text", "description"),
    )

    for key, preferred_keys in _INIT_COHERENCE_ONTOLOGY_LIST_KEY_HINTS.items():
        if key in ontology:
            ontology[key] = coerce_text_list(
                ontology.get(key),
                preferred_keys=preferred_keys,
            )

    terminology = ontology.get("terminology")
    if isinstance(terminology, dict):
        ontology["terminology"] = {
            str(key): stringify_text_value(value)
            for key, value in terminology.items()
            if str(key).strip()
        }
    elif "terminology" in ontology:
        text = stringify_text_value(terminology)
        ontology["terminology"] = {"notes": text} if text else {}

    data["project_ontology"] = ontology

    after = _top_level_fingerprint(data)
    if before == after:
        return TaskOutputAdapterResult()
    changed_keys = tuple(
        key for key in sorted(set(before) | set(after)) if before.get(key) != after.get(key)
    )
    return TaskOutputAdapterResult(
        changed=True,
        adapter="init_coherence_ontology_shape_normalizer",
        changed_keys=changed_keys,
        metadata={
            "moved_semantic_keys": moved_semantic_keys,
            "dropped_keys": dropped_keys,
        },
    )


def _adapt_init_knowledge_boundaries_response(data: dict[str, Any]) -> TaskOutputAdapterResult:
    """Remove recoverable role-object leakage inside knowledge boundary objects."""

    characters = data.get("characters")
    if not isinstance(characters, list):
        return TaskOutputAdapterResult()

    before = _top_level_fingerprint(data)
    characters_normalized = 0
    nested_fields_promoted = 0
    extra_keys_removed = 0

    for item in characters:
        if not isinstance(item, dict):
            continue
        boundary = item.get("knowledge_boundaries")
        if not isinstance(boundary, dict):
            continue
        normalized, meta = _normalize_init_knowledge_boundary(boundary)
        if not meta["changed"]:
            continue
        item["knowledge_boundaries"] = normalized
        characters_normalized += 1
        nested_fields_promoted += meta["nested_fields_promoted"]
        extra_keys_removed += meta["extra_keys_removed"]

    after = _top_level_fingerprint(data)
    if before == after:
        return TaskOutputAdapterResult()
    return TaskOutputAdapterResult(
        changed=True,
        adapter="init_knowledge_boundaries_shape_normalizer",
        changed_keys=("characters",),
        metadata={
            "characters_normalized": characters_normalized,
            "nested_fields_promoted": nested_fields_promoted,
            "extra_keys_removed": extra_keys_removed,
        },
    )


def _normalize_init_knowledge_boundary(
    boundary: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    nested = boundary.get("knowledge_boundaries")
    nested_boundary = nested if isinstance(nested, dict) else {}
    normalized: dict[str, Any] = {}
    nested_fields_promoted = 0

    for key in _INIT_KNOWLEDGE_BOUNDARY_KEYS:
        if _has_boundary_value(boundary.get(key)):
            normalized[key] = boundary[key]
            continue
        if key in nested_boundary:
            normalized[key] = nested_boundary[key]
            nested_fields_promoted += 1
        elif key in boundary:
            normalized[key] = boundary[key]

    extra_keys_removed = sum(1 for key in boundary if key not in _INIT_KNOWLEDGE_BOUNDARY_KEY_SET)
    changed = bool(extra_keys_removed or nested_fields_promoted)
    if not changed:
        return boundary, {
            "changed": False,
            "nested_fields_promoted": 0,
            "extra_keys_removed": 0,
        }
    return normalized, {
        "changed": True,
        "nested_fields_promoted": nested_fields_promoted,
        "extra_keys_removed": extra_keys_removed,
    }


def _has_boundary_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, dict)):
        return bool(value)
    return True


def _adapt_creative_config_response(
    data: dict[str, Any],
    task_type: TaskType,
    *,
    context: dict[str, Any],
) -> TaskOutputAdapterResult:
    """Drop fields that are not valid for the active config mode."""
    before = _top_level_fingerprint(data)
    contract = resolve_task_format_contract(task_type, context)
    allowed_keys = tuple(getattr(contract, "allowed_top_level_keys", ()) or ())
    if allowed_keys:
        allowed = set(allowed_keys)
        for key in list(data):
            if key not in allowed:
                data.pop(key, None)

    after = _top_level_fingerprint(data)
    if before == after:
        return TaskOutputAdapterResult()
    return TaskOutputAdapterResult(
        changed=True,
        adapter="creative_config_allowed_key_normalizer",
        changed_keys=tuple(
            key for key in sorted(set(before) | set(after)) if before.get(key) != after.get(key)
        ),
        metadata={"task": task_type.value, "mode": str(context.get("mode") or "")},
    )


# Per-claim enum fields that are safe to normalize for case/whitespace only.
# Invalid values must remain visible so the LLM retry path can correct them.
_CLAIM_ENUM_FIELDS = {
    key: values for key, values in CLAIM_ENUM_VALUES.items() if key != "claim_type"
}
_INIT_CLAIM_TOP_LEVEL_ALIASES: dict[str, tuple[str, ...]] = {
    "claims": ("Claims", "claim_list", "ClaimList", "items", "results"),
    "summary": ("Summary", "summaries", "SummaryText", "summary_text"),
}


def _normalize_top_level_aliases(
    data: dict[str, Any],
    aliases: dict[str, tuple[str, ...]],
) -> bool:
    """Collapse known top-level alias/case drift into canonical response keys."""
    changed = False
    for canonical, alias_values in aliases.items():
        alias_tokens = {canonical.lower(), *(alias.lower() for alias in alias_values)}
        for key in list(data):
            if key == canonical:
                continue
            if key.lower() not in alias_tokens:
                continue
            if canonical not in data:
                data[canonical] = data[key]
            data.pop(key, None)
            changed = True
    return changed


def _normalize_claim_enum_value(
    claim: dict[str, Any],
    field: str,
    valid_values: frozenset[str],
) -> bool:
    """Normalize a valid enum token without hiding invalid model output."""
    if field not in claim:
        return False
    raw = claim[field]
    if not isinstance(raw, str):
        return False
    normalized = raw.strip().lower()
    if normalized in valid_values:
        if normalized != raw:
            claim[field] = normalized
            return True
    return False


def _normalize_claim_character_knowledge_coverage(claim: dict[str, Any]) -> bool:
    """Normalize valid awareness tokens without replacing unknown semantics."""
    raw = claim.get("character_knowledge_coverage")
    if not isinstance(raw, dict):
        return False
    changed = False
    for name, awareness in list(raw.items()):
        if not isinstance(awareness, str):
            continue
        token = awareness.strip().lower()
        if token in CHARACTER_KNOWLEDGE_COVERAGE_VALUES:
            if token != awareness:
                raw[name] = token
                changed = True
    return changed


_CLAIM_TYPE_VALUES = frozenset(
    {
        "state",
        "event",
        "payoff",
        "dependency",
        "relationship",
        "world_rule",
        "knowledge",
        "promise",
        "other",
    }
)


def _normalize_claim_scalar(claim: dict[str, Any]) -> bool:
    """Normalize explicitly convertible claim scalars without defaulting."""
    changed = False

    raw_conf = claim.get("confidence") if "confidence" in claim else None
    if isinstance(raw_conf, str):
        try:
            parsed_confidence = float(raw_conf)
        except (TypeError, ValueError):
            pass
        else:
            if 0.0 <= parsed_confidence <= 1.0:
                claim["confidence"] = parsed_confidence
                changed = True

    raw_ct = claim.get("claim_type") if "claim_type" in claim else None
    if isinstance(raw_ct, str):
        token = raw_ct.strip().lower()
        if token in _CLAIM_TYPE_VALUES and token != raw_ct:
            claim["claim_type"] = token
            changed = True

    raw_irr = claim.get("irreversible") if "irreversible" in claim else None
    if isinstance(raw_irr, str):
        token = raw_irr.strip().lower()
        if token in {"true", "false"}:
            claim["irreversible"] = token == "true"
            changed = True

    return changed


def _normalize_claim_structural_fields(claim: dict[str, Any]) -> bool:
    """Normalize transport-only fields without authoring narrative meaning."""

    if isinstance(claim.get("metadata"), dict):
        return False
    claim["metadata"] = {}
    return True


def _normalize_claim_foreshadow_chapters(claim: dict[str, Any]) -> bool:
    """Normalize explicit numeric foreshadow anchors without filling omissions."""
    if "foreshadow_chapters" not in claim:
        return False

    raw = claim.get("foreshadow_chapters")
    raw_items: list[Any] = raw if isinstance(raw, list) else [raw]
    normalized: list[int] = []
    for item in raw_items:
        if isinstance(item, bool):
            continue
        try:
            chapter = int(item)
        except (TypeError, ValueError):
            return False
        if chapter >= 1 and chapter not in normalized:
            normalized.append(chapter)
        elif chapter < 1:
            return False
    normalized.sort()
    if normalized != raw:
        claim["foreshadow_chapters"] = normalized
        return True
    return False


def _normalize_init_claim_enums(claims: list[Any]) -> tuple[bool, int]:
    """Normalize enum-bearing string fields on every claim. Returns (changed, count)."""
    changed = False
    count = 0
    for item in claims:
        if not isinstance(item, dict):
            continue
        if _normalize_claim_character_knowledge_coverage(item):
            changed = True
            count += 1
        for field, valid in _CLAIM_ENUM_FIELDS.items():
            if _normalize_claim_enum_value(item, field, valid):
                changed = True
                count += 1
        if _normalize_claim_scalar(item):
            changed = True
            count += 1
        if _normalize_claim_structural_fields(item):
            changed = True
            count += 1
        if _normalize_claim_foreshadow_chapters(item):
            changed = True
            count += 1
    return changed, count


def _adapt_init_claims_response(
    data: dict[str, Any],
    task_type: TaskType,
) -> TaskOutputAdapterResult:
    """Normalize init-claim extractors: shape and per-claim enum drift."""
    before = _top_level_fingerprint(data)
    _normalize_top_level_aliases(data, _INIT_CLAIM_TOP_LEVEL_ALIASES)
    claims = data.get("claims")
    if isinstance(claims, dict):
        data["claims"] = [claims]
    if "summary" in data:
        data["summary"] = stringify_text_value(data.get("summary"))

    raw_claims = data.get("claims")
    normalized_claims: list[Any] = raw_claims if isinstance(raw_claims, list) else []
    enum_changed, enum_count = _normalize_init_claim_enums(normalized_claims)

    after = _top_level_fingerprint(data)
    shape_changed = before != after
    if not shape_changed and not enum_changed:
        return TaskOutputAdapterResult()
    return TaskOutputAdapterResult(
        changed=True,
        adapter="init_claims_shape_normalizer",
        changed_keys=tuple(
            key for key in sorted(set(before) | set(after)) if before.get(key) != after.get(key)
        ),
        metadata={
            "task": task_type.value,
            "enum_normalized_fields": enum_count,
        },
    )


def _adapt_candidate_state_deltas_response(data: dict[str, Any]) -> TaskOutputAdapterResult:
    """Normalize recoverable candidate alias drift before schema validation."""
    before = _top_level_fingerprint(data)
    raw_candidates = data.get("candidates")
    if isinstance(raw_candidates, dict):
        raw_candidates = [raw_candidates]
        data["candidates"] = raw_candidates
    if not isinstance(raw_candidates, list):
        return TaskOutputAdapterResult()

    normalized_candidates: list[Any] = []
    for item in raw_candidates:
        if not isinstance(item, dict):
            normalized_candidates.append(item)
            continue
        candidate = dict(item)
        if not stringify_text_value(candidate.get("summary")):
            for key in ("description", "desc", "rationale", "change_summary"):
                summary = stringify_text_value(candidate.get(key))
                if summary:
                    candidate["summary"] = summary
                    break
        normalized_evidence = _normalize_candidate_evidence_aliases(candidate)
        if normalized_evidence is not None:
            candidate["evidence"] = normalized_evidence
        normalized_candidates.append(candidate)
    data["candidates"] = normalized_candidates

    after = _top_level_fingerprint(data)
    if before == after:
        return TaskOutputAdapterResult()
    return TaskOutputAdapterResult(
        changed=True,
        adapter="candidate_state_delta_alias_normalizer",
        changed_keys=tuple(
            key for key in sorted(set(before) | set(after)) if before.get(key) != after.get(key)
        ),
        metadata={"candidate_count": len(normalized_candidates)},
    )


def _normalize_candidate_evidence_aliases(candidate: dict[str, Any]) -> list[Any] | None:
    raw_evidence = candidate.get("evidence")
    if raw_evidence in (None, ""):
        for key in ("quote", "evidence_quote", "evidence_text", "text_quote"):
            quote = stringify_text_value(candidate.get(key))
            if quote:
                raw_evidence = [{"quote": quote}]
                break
    if raw_evidence is None:
        return None
    if isinstance(raw_evidence, str):
        return [{"quote": raw_evidence}]
    if isinstance(raw_evidence, dict):
        raw_evidence = [raw_evidence]
    if not isinstance(raw_evidence, list):
        return None

    normalized: list[Any] = []
    for evidence in raw_evidence:
        if isinstance(evidence, str):
            quote = stringify_text_value(evidence)
            if quote:
                normalized.append({"quote": quote})
            continue
        if not isinstance(evidence, dict):
            continue
        evidence_item = dict(evidence)
        if not stringify_text_value(evidence_item.get("quote")):
            for key in ("evidence", "text", "content", "matched"):
                quote = stringify_text_value(evidence_item.get(key))
                if quote:
                    evidence_item["quote"] = quote
                    break
        normalized.append(evidence_item)
    return normalized


def _adapt_init_creative_refinement_response(data: dict[str, Any]) -> TaskOutputAdapterResult:
    """Normalize explicit init-refinement fields without inventing a no-op plan."""
    before = _top_level_fingerprint(data)
    for key in ("suggestions", "repair_scope", "patches", "preserve", "risks"):
        value = data.get(key)
        if key not in data:
            continue
        if isinstance(value, dict):
            data[key] = [value]
        elif not isinstance(value, list):
            data[key] = [stringify_text_value(value)]
    if "summary" in data:
        data["summary"] = stringify_text_value(data.get("summary"))

    after = _top_level_fingerprint(data)
    if before == after:
        return TaskOutputAdapterResult()
    return TaskOutputAdapterResult(
        changed=True,
        adapter="init_creative_refinement_noop_fallback",
        changed_keys=tuple(
            key for key in sorted(set(before) | set(after)) if before.get(key) != after.get(key)
        ),
        metadata={"task": TaskType.REFINE_INIT_ARTIFACTS_FROM_SYNOPSIS.value},
    )


_CHARACTER_SCALAR_TEXT_FIELDS = frozenset(
    {
        "name",
        "role",
        "age",
        "gender",
        "status",
        "time_layer",
    }
)
_CHARACTER_NARRATIVE_TEXT_FIELDS = frozenset(
    {
        "social_status",
        "abilities",
        "appearance",
        "personality",
        "backstory",
        "arc",
        "notes",
    }
)
_CHARACTER_TEXT_FIELDS = _CHARACTER_SCALAR_TEXT_FIELDS | _CHARACTER_NARRATIVE_TEXT_FIELDS


def _adapt_character_profile_response(
    data: dict[str, Any],
    task_type: TaskType,
) -> TaskOutputAdapterResult:
    before = _top_level_fingerprint(data)
    if task_type == TaskType.INIT_CHARACTER_BIBLE:
        character_bible = data.get("character_bible")
        if not isinstance(character_bible, dict):
            return TaskOutputAdapterResult()
        characters = character_bible.get("characters")
        if isinstance(characters, list):
            for profile in characters:
                _normalize_character_profile(profile)
    elif task_type == TaskType.INIT_CHARACTER_PROFILE_BATCH:
        for key in _JSON_SCHEMA_LEAK_TOP_LEVEL_KEYS:
            data.pop(key, None)
        profiles = data.get("character_profiles")
        if isinstance(profiles, list):
            profiles[:] = [profile for profile in profiles if profile is not None]
            for profile in profiles:
                _normalize_character_profile(profile)
    elif task_type == TaskType.INTRODUCE_CHARACTER:
        _normalize_character_profile(data)

    after = _top_level_fingerprint(data)
    if before == after:
        return TaskOutputAdapterResult()
    return TaskOutputAdapterResult(
        changed=True,
        adapter="character_profile_text_shape_normalizer",
        changed_keys=tuple(
            key for key in sorted(set(before) | set(after)) if before.get(key) != after.get(key)
        ),
        metadata={"task": task_type.value},
    )


def _adapt_enrich_character_response(data: dict[str, Any]) -> TaskOutputAdapterResult:
    """Normalize ENRICH_CHARACTER output: flatten nested text fields at top level."""
    before = _top_level_fingerprint(data)
    _normalize_character_profile(data)
    after = _top_level_fingerprint(data)
    if before == after:
        return TaskOutputAdapterResult()
    return TaskOutputAdapterResult(
        changed=True,
        adapter="enrich_character_text_shape_normalizer",
        changed_keys=tuple(
            key for key in sorted(set(before) | set(after)) if before.get(key) != after.get(key)
        ),
        metadata={"task": TaskType.ENRICH_CHARACTER.value},
    )


def _normalize_character_profile(profile: Any) -> None:
    if not isinstance(profile, dict):
        return
    for field_name in _CHARACTER_TEXT_FIELDS:
        if field_name in profile and not isinstance(profile[field_name], str):
            profile[field_name] = _stringify_character_text_value(profile[field_name])
    relationships = profile.get("relationships")
    if isinstance(relationships, dict):
        for key, value in list(relationships.items()):
            if not isinstance(value, str):
                relationships[key] = _stringify_character_text_value(value)


def _stringify_character_text_value(value: Any) -> str:
    return stringify_text_value(value)


def _adapt_editorial_text_lists(
    data: dict[str, Any],
    task_type: TaskType,
) -> TaskOutputAdapterResult:
    """Normalize recoverable editorial shape drift before strict schema checks."""

    before = _top_level_fingerprint(data)
    for field_name in (
        "theme_policies",
        "forbidden_confirmation_phrases",
        "revision_priorities",
        "time_bridge_policies",
    ):
        if field_name in data:
            data[field_name] = coerce_text_list(
                data.get(field_name),
                preferred_keys=_POLICY_TEXT_KEYS,
            )
    if task_type in {TaskType.DERIVE_EDITORIAL_CONTRACT, TaskType.DERIVE_EDITORIAL_STRUCTURE}:
        if "denouement_budget" in data:
            data["denouement_budget"] = _normalize_denouement_budget_payload(
                data.get("denouement_budget")
            )
        if "title_policy" in data:
            data["title_policy"] = _normalize_title_policy_payload(data.get("title_policy"))
        if "revelation_ladder" in data:
            data["revelation_ladder"] = _normalize_revelation_ladder_payload(
                data.get("revelation_ladder")
            )
    if "symbol_policies" in data:
        data["symbol_policies"] = _normalize_symbol_policies_payload(data.get("symbol_policies"))
    after = _top_level_fingerprint(data)
    if before == after:
        return TaskOutputAdapterResult()
    return TaskOutputAdapterResult(
        changed=True,
        adapter="editorial_text_list_shape_normalizer",
        changed_keys=tuple(
            key for key in sorted(set(before) | set(after)) if before.get(key) != after.get(key)
        ),
        metadata={"task": task_type.value},
    )


def _normalize_denouement_budget_payload(value: Any) -> Any:
    """Canonicalize known denouement aliases without inventing missing policy."""

    if not isinstance(value, dict):
        return value
    source = dict(value)
    normalized = dict(source)
    for field_name, aliases in _DENOUEMENT_BUDGET_TEXT_ALIASES.items():
        values = (
            coerce_text_list(source.get(field_name), preferred_keys=_POLICY_TEXT_KEYS)
            if field_name in source
            else []
        )
        found = field_name in source
        for alias in aliases:
            if alias not in source:
                continue
            found = True
            normalized.pop(alias, None)
            values.extend(coerce_text_list(source.get(alias), preferred_keys=_POLICY_TEXT_KEYS))
        if found:
            normalized[field_name] = list(dict.fromkeys(values))
    return normalized


def _normalize_title_policy_payload(value: Any) -> Any:
    if not isinstance(value, dict):
        return value
    source = dict(value)
    aliases = (
        "reuse_limit",
        "max_repeat",
        "max_repeats",
        "maximum_reuse",
        "maximum_repeats",
    )
    if "max_reuse" not in source:
        for alias in aliases:
            if alias in source:
                source["max_reuse"] = source[alias]
                break
    allow_reuse = source.get("allow_reuse")
    allow_repeated = source.get("allow_repeated_titles")
    if allow_reuse is False or allow_repeated is False:
        source["max_reuse"] = 1
    if "max_reuse" in source and not isinstance(source.get("max_reuse"), bool):
        try:
            source["max_reuse"] = max(1, min(int(source["max_reuse"]), 10))
        except (TypeError, ValueError):
            pass

    if "allowed_repeated_titles" not in source:
        for alias in ("allowed_titles", "repeatable_titles", "repeated_titles"):
            if alias in source:
                source["allowed_repeated_titles"] = source[alias]
                break
    if "allowed_repeated_titles" in source:
        source["allowed_repeated_titles"] = coerce_text_list(
            source.get("allowed_repeated_titles"),
            preferred_keys=("title", "name", "label", "value", "text"),
        )
    if "naming_strategy" in source:
        source["naming_strategy"] = stringify_text_value(source.get("naming_strategy"))
    return {key: source[key] for key in _TITLE_POLICY_KEYS if key in source}


def _normalize_revelation_ladder_payload(value: Any) -> Any:
    items = _revelation_ladder_items(value)
    if items is None:
        return value

    normalized: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            return value
        entry: dict[str, Any] = {}
        for field_name in _REVELATION_LADDER_TEXT_FIELDS:
            text = _preferred_revelation_ladder_text(item.get(field_name))
            if text:
                entry[field_name] = text

        raw_stage_order = item.get("stage_order") or item.get("order")
        if raw_stage_order is not None:
            entry["stage_order"] = _safe_positive_int(raw_stage_order) or raw_stage_order
        raw_target_chapter = (
            item.get("target_chapter")
            if item.get("target_chapter") is not None
            else item.get("chapter", item.get("chapter_number"))
        )
        if raw_target_chapter is not None:
            entry["target_chapter"] = _safe_int(raw_target_chapter, default=-1)
        normalized.append(
            {key: entry[key] for key in _REVELATION_LADDER_OUTPUT_KEYS if key in entry}
        )
    return normalized


def _revelation_ladder_items(value: Any) -> list[Any] | None:
    if isinstance(value, list):
        return value
    if not isinstance(value, dict):
        return None
    if any(
        key in value
        for key in (
            *_REVELATION_LADDER_OUTPUT_KEYS,
            "order",
            "chapter",
            "chapter_number",
        )
    ):
        return [value]
    values = list(value.values())
    if values and all(isinstance(item, dict) for item in values):
        return values
    return None


def _preferred_revelation_ladder_text(value: Any) -> str:
    if isinstance(value, dict):
        for key in _REVELATION_LADDER_TEXT_KEYS:
            text = stringify_text_value(value.get(key))
            if text:
                return text
    return stringify_text_value(value)


def _normalize_symbol_policies_payload(value: Any) -> Any:
    if not isinstance(value, list):
        return value
    normalized: list[Any] = []
    for item in value:
        if not isinstance(item, dict):
            normalized.append(item)
            continue
        fixed = dict(item)
        if "explanation_policy" in fixed:
            fixed["explanation_policy"] = _normalize_symbol_explanation_policy(
                fixed.get("explanation_policy")
            )
        if "max_explicit_explanations" in fixed:
            parsed = _safe_int(fixed.get("max_explicit_explanations"), default=-1)
            if parsed >= 0:
                fixed["max_explicit_explanations"] = max(0, min(parsed, 10))
        if fixed.get("explanation_policy") == "never_explain":
            fixed["max_explicit_explanations"] = 0
        normalized.append(fixed)
    return normalized


def _normalize_symbol_explanation_policy(value: Any) -> str:
    key = (
        str(value or "")
        .strip()
        .strip("\"'`，。,.；;：: ")
        .lower()
        .replace("-", "_")
        .replace(" ", "_")
    )
    return _SYMBOL_POLICY_EXPLANATION_POLICY_ALIASES.get(key, key)


def _adapt_plan_outline_batch_response(data: dict[str, Any]) -> TaskOutputAdapterResult:
    chapters = data.get("chapters")
    if not isinstance(chapters, list):
        return TaskOutputAdapterResult()

    before = _top_level_fingerprint(data)
    normalized: list[Any] = []
    changed = False
    index = 0
    while index < len(chapters):
        item = chapters[index]
        if (
            isinstance(item, dict)
            and _is_outline_beats_fragment(item)
            and index + 1 < len(chapters)
            and isinstance(chapters[index + 1], dict)
            and "chapter_number" in chapters[index + 1]
            and "beats_summary" not in chapters[index + 1]
        ):
            merged = dict(item)
            merged.update(chapters[index + 1])
            item = merged
            index += 1
            changed = True

        if isinstance(item, dict):
            if _normalize_outline_chapter_contract_fields(item):
                changed = True

        normalized.append(item)
        index += 1

    if changed:
        data["chapters"] = normalized

    after = _top_level_fingerprint(data)
    if before == after:
        return TaskOutputAdapterResult()
    return TaskOutputAdapterResult(
        changed=True,
        adapter="plan_outline_batch_shape_normalizer",
        changed_keys=tuple(
            key for key in sorted(set(before) | set(after)) if before.get(key) != after.get(key)
        ),
    )


def _normalize_outline_chapter_contract_fields(chapter: dict[str, Any]) -> bool:
    """Normalize ChapterOutline shape without authoring missing semantics."""
    before = dict(chapter)

    if _ensure_outline_chapter_title(chapter):
        pass

    _normalize_present_outline_fields(chapter)
    _project_outline_identity_aliases(chapter)

    allowed_fields = _outline_chapter_allowed_fields()
    if allowed_fields:
        for key in list(chapter):
            if key not in allowed_fields:
                chapter.pop(key, None)

    return chapter != before


def _normalize_present_outline_fields(chapter: dict[str, Any]) -> None:
    text_fields = ("goal", "subplot_focus", "setting", "notes")
    list_fields = (
        "beats_summary",
        "main_plot_points",
        "subplot_points",
        "element_focus",
        "required_character_ids",
        "support_character_ids",
        "involved_character_ids",
        "involved_characters",
        "involved_character_names",
        "scene_design_goals",
    )
    for key in text_fields:
        if key in chapter:
            chapter[key] = stringify_text_value(chapter.get(key))
    for key in list_fields:
        if key in chapter:
            chapter[key] = _to_str_list(chapter.get(key))
    if "element_focus" in chapter:
        chapter["element_focus"] = chapter["element_focus"][:3]
    if "chapter_number" in chapter:
        parsed = _safe_positive_int(chapter.get("chapter_number"))
        if parsed:
            chapter["chapter_number"] = parsed
    if "expected_word_count" in chapter:
        parsed = _safe_positive_int(chapter.get("expected_word_count"))
        if parsed:
            chapter["expected_word_count"] = parsed
    if "pov_switch" in chapter:
        chapter["pov_switch"] = _to_bool(chapter.get("pov_switch"), default=False)
    if isinstance(chapter.get("cast_plan"), dict):
        cast_plan = {
            key: chapter["cast_plan"][key]
            for key in (
                "pov_entity_id",
                "required_character_ids",
                "support_character_ids",
                "mention_only_entity_ids",
                "forbidden_active_character_ids",
            )
            if key in chapter["cast_plan"]
        }
        if "pov_entity_id" in cast_plan:
            cast_plan["pov_entity_id"] = stringify_text_value(cast_plan.get("pov_entity_id"))
        for key in (
            "required_character_ids",
            "support_character_ids",
            "mention_only_entity_ids",
            "forbidden_active_character_ids",
        ):
            if key in cast_plan:
                cast_plan[key] = _to_str_list(cast_plan.get(key))
        chapter["cast_plan"] = cast_plan
    if isinstance(chapter.get("emotional_plan"), dict):
        emotional_plan = {
            key: chapter["emotional_plan"][key]
            for key in (
                "subject_entity_id",
                "entry_state",
                "pressure_source",
                "relationship_choice",
                "turning_emotion",
                "exit_aftertaste",
                "expression_channels",
            )
            if key in chapter["emotional_plan"]
        }
        for key in (
            "subject_entity_id",
            "entry_state",
            "pressure_source",
            "relationship_choice",
            "turning_emotion",
            "exit_aftertaste",
        ):
            if key in emotional_plan:
                emotional_plan[key] = stringify_text_value(emotional_plan.get(key))
        if "expression_channels" in emotional_plan:
            emotional_plan["expression_channels"] = _to_str_list(
                emotional_plan.get("expression_channels")
            )
        chapter["emotional_plan"] = emotional_plan
    if "expected_hook" in chapter:
        chapter["expected_hook"] = _normalize_outline_hook(chapter.get("expected_hook"))
    if "expected_payoffs" in chapter:
        chapter["expected_payoffs"] = _normalize_outline_payoffs(chapter.get("expected_payoffs"))


def _project_outline_identity_aliases(chapter: dict[str, Any]) -> None:
    pov_id = stringify_text_value(chapter.get("pov_character_id") or chapter.get("pov_entity_id"))
    pov_name = stringify_text_value(
        chapter.get("pov_character_name") or chapter.get("pov_character")
    )
    if pov_id:
        chapter["pov_character_id"] = pov_id
    if pov_name:
        chapter["pov_character_name"] = pov_name
        chapter.setdefault("pov_character", pov_name)


def _outline_chapter_allowed_fields() -> set[str]:
    contract = resolve_task_format_contract(TaskType.PLAN_OUTLINE_BATCH)
    schema = getattr(contract, "json_schema", None)
    if not isinstance(schema, dict):
        return set()
    chapters_schema = schema.get("properties", {}).get("chapters")
    if not isinstance(chapters_schema, dict):
        return set()
    item_schema = chapters_schema.get("items")
    if not isinstance(item_schema, dict):
        return set()
    properties = item_schema.get("properties")
    if not isinstance(properties, dict):
        return set()
    return {str(key) for key in properties}


def _normalize_outline_hook(value: Any) -> Any:
    if isinstance(value, dict):
        result = {
            key: value[key]
            for key in ("hook_type", "hook_strength", "hook_description")
            if key in value
        }
        if "hook_description" not in result and "description" in value:
            result["hook_description"] = value.get("description")
        for key in ("hook_type", "hook_strength", "hook_description"):
            if key in result:
                result[key] = stringify_text_value(result.get(key))
        return result
    return value


def _normalize_outline_payoffs(value: Any) -> Any:
    raw_items: list[Any]
    if isinstance(value, list):
        raw_items = value
    elif value in (None, "", {}):
        return value
    else:
        raw_items = [value]

    payoffs: list[dict[str, str]] = []
    for item in raw_items:
        if isinstance(item, dict):
            payoff_type = stringify_text_value(item.get("payoff_type"))
            description = stringify_text_value(item.get("description") or item.get("payoff"))
        else:
            return value
        payoffs.append({"payoff_type": payoff_type, "description": description})
    return payoffs


def _is_outline_beats_fragment(item: dict[str, Any]) -> bool:
    keys = {str(key) for key in item}
    return bool(keys) and keys <= {"beats_summary"} and isinstance(item.get("beats_summary"), list)


def _ensure_outline_chapter_title(chapter: dict[str, Any]) -> bool:
    if str(chapter.get("title") or "").strip():
        return False
    chapter["title"] = OUTLINE_TITLE_REPAIR_PLACEHOLDER
    return True


def _adapt_book_consistency_response(
    data: dict[str, Any],
    task_type: TaskType,
) -> TaskOutputAdapterResult:
    before = _top_level_fingerprint(data)

    if "issues" not in data:
        return TaskOutputAdapterResult()
    issues_raw = data.get("issues")
    if isinstance(issues_raw, dict):
        issues_iterable = list(issues_raw.values())
    elif isinstance(issues_raw, list):
        issues_iterable = issues_raw
    else:
        return TaskOutputAdapterResult()
    data["issues"] = [_normalize_book_consistency_issue(item) for item in issues_iterable]

    if task_type in _BOOK_CONSISTENCY_ISSUES_ONLY_TASKS:
        for key in list(data):
            if key != "issues":
                data.pop(key, None)
    else:
        if "repair_plan" in data:
            data["repair_plan"] = _normalize_book_consistency_repair_plan(
                data.get("repair_plan"),
            )
        if "summary" in data:
            data["summary"] = stringify_text_value(data.get("summary"))
        if "consistency_score" in data:
            data["consistency_score"] = _numeric_if_convertible(data.get("consistency_score"))

    after = _top_level_fingerprint(data)
    if before == after:
        return TaskOutputAdapterResult()
    return TaskOutputAdapterResult(
        changed=True,
        adapter="book_consistency_shape_normalizer",
        changed_keys=tuple(
            key for key in sorted(set(before) | set(after)) if before.get(key) != after.get(key)
        ),
        metadata={"task": task_type.value},
    )


def _normalize_book_consistency_issue(item: Any) -> Any:
    if not isinstance(item, dict):
        return item
    normalized: dict[str, Any] = {}
    category = stringify_text_value(item.get("category")) or stringify_text_value(
        item.get("issue_type")
    )
    if category:
        normalized["category"] = category
    issue_type = stringify_text_value(item.get("issue_type")) or category
    if issue_type:
        normalized["issue_type"] = issue_type
    for key in (
        "issue_id",
        "severity",
        "location",
        "evidence",
        "description",
        "suggestion",
        "fix_mode",
        "fix_action",
        "handoff_notes",
    ):
        if key in item:
            normalized[key] = stringify_text_value(item.get(key))
    for key in ("primary_chapter", "paragraph_index"):
        if key in item:
            normalized[key] = _integer_if_convertible(item.get(key))
    for key in ("chapters_involved", "paragraph_span"):
        if key in item:
            normalized[key] = _integer_list_if_convertible(item.get(key))
    if "confidence" in item:
        normalized["confidence"] = _numeric_if_convertible(item.get("confidence"))
    if "evidence_pairs" in item:
        normalized["evidence_pairs"] = _normalize_book_consistency_evidence_pairs(
            item.get("evidence_pairs")
        )
    if "verification_questions" in item:
        normalized["verification_questions"] = _to_str_list(item.get("verification_questions"))
    if "linked_issue_refs" in item:
        normalized["linked_issue_refs"] = _normalize_book_consistency_linked_refs(
            item.get("linked_issue_refs")
        )
    return normalized


def _normalize_book_consistency_evidence_pairs(
    value: Any,
) -> Any:
    if not isinstance(value, list):
        return value
    pairs: list[Any] = []
    for pair in value:
        if not isinstance(pair, dict):
            pairs.append(pair)
            continue
        normalized = dict(pair)
        if "chapter_number" in normalized:
            normalized["chapter_number"] = _integer_if_convertible(normalized.get("chapter_number"))
        for key in ("evidence", "claim"):
            if key in normalized:
                normalized[key] = stringify_text_value(normalized.get(key))
        pairs.append(normalized)
    return pairs


def _normalize_book_consistency_linked_refs(value: Any) -> Any:
    if not isinstance(value, list):
        return value
    refs: list[Any] = []
    for ref in value:
        if not isinstance(ref, dict):
            refs.append(ref)
            continue
        normalized = dict(ref)
        for key in ("chapter_number", "index"):
            if key in normalized:
                normalized[key] = _integer_if_convertible(normalized.get(key))
        if "lane" in normalized:
            normalized["lane"] = stringify_text_value(normalized.get("lane"))
        refs.append(normalized)
    return refs


def _normalize_book_consistency_repair_plan(value: Any) -> Any:
    if not isinstance(value, list):
        return value
    plans: list[Any] = []
    for plan in value:
        if not isinstance(plan, dict):
            plans.append(plan)
            continue
        normalized = dict(plan)
        if "chapter_number" in normalized:
            normalized["chapter_number"] = _integer_if_convertible(normalized.get("chapter_number"))
        if "issue_ids" in normalized:
            normalized["issue_ids"] = _to_str_list(normalized.get("issue_ids"))
        for key in ("priority", "strategy"):
            if key in normalized:
                normalized[key] = stringify_text_value(normalized.get(key))
        plans.append(normalized)
    return plans


def _adapt_book_consistency_verify_response(
    data: dict[str, Any],
    *,
    context: dict[str, Any],
) -> TaskOutputAdapterResult:
    before = _top_level_fingerprint(data)
    issues_raw = data.get("verified_issues")
    if isinstance(issues_raw, dict):
        issues_iterable = list(issues_raw.values())
    elif isinstance(issues_raw, list):
        issues_iterable = issues_raw
    else:
        return TaskOutputAdapterResult()

    del context
    if any(
        not isinstance(item, dict) or not _is_recoverable_book_consistency_verify_item(item)
        for item in issues_iterable
    ):
        return TaskOutputAdapterResult()
    data["verified_issues"] = [
        _normalize_book_consistency_verified_issue(item) for item in issues_iterable
    ]

    after = _top_level_fingerprint(data)
    if before == after:
        return TaskOutputAdapterResult()
    return TaskOutputAdapterResult(
        changed=True,
        adapter="book_consistency_verify_shape_normalizer",
        changed_keys=tuple(
            key for key in sorted(set(before) | set(after)) if before.get(key) != after.get(key)
        ),
    )


def _is_recoverable_book_consistency_verify_item(item: dict[str, Any]) -> bool:
    """Return true when a verify item has enough location signal to normalize."""
    return bool(
        stringify_text_value(item.get("issue_id"))
        and stringify_text_value(item.get("status"))
        and (
            _safe_positive_int(item.get("paragraph_index")) > 0
            or _safe_positive_int(item.get("chapter_number")) > 0
            or stringify_text_value(item.get("location"))
            or isinstance(item.get("repair_scope"), dict)
        )
    )


def _normalize_book_consistency_verified_issue(
    item: dict[str, Any],
) -> dict[str, Any]:
    normalized: dict[str, Any] = {}
    for key in (
        "issue_id",
        "status",
        "description",
        "severity",
        "evidence",
        "location",
        "anchor_type",
        "adjudication_notes",
        "fix_mode",
        "fix_action",
        "rejection_reason",
    ):
        if key in item:
            normalized[key] = stringify_text_value(item.get(key))
    for key in ("confidence", "location_confidence"):
        if key in item:
            normalized[key] = _numeric_if_convertible(item.get(key))
    for key in ("paragraph_index",):
        if key in item:
            normalized[key] = _integer_if_convertible(item.get(key))
    if "paragraph_span" in item:
        normalized["paragraph_span"] = _integer_list_if_convertible(item.get("paragraph_span"))
    if "evidence_pairs" in item:
        normalized["evidence_pairs"] = _normalize_book_consistency_evidence_pairs(
            item.get("evidence_pairs")
        )
    if "repair_scope" in item:
        normalized["repair_scope"] = _normalize_book_consistency_repair_scope(
            item.get("repair_scope")
        )
    if "postconditions" in item:
        normalized["postconditions"] = _normalize_book_consistency_postconditions(
            item.get("postconditions")
        )
    return normalized


def _normalize_book_consistency_repair_scope(value: Any) -> Any:
    if not isinstance(value, dict):
        return value
    scope = dict(value)
    if "target" in scope:
        scope["target"] = stringify_text_value(scope.get("target"))
    for key in ("allowed_changes", "forbidden_changes", "preserve"):
        if key in scope:
            scope[key] = _to_str_list(scope.get(key))
    return scope


def _normalize_book_consistency_postconditions(value: Any) -> Any:
    if not isinstance(value, list):
        return value
    postconditions: list[Any] = []
    for item in value:
        if not isinstance(item, dict):
            postconditions.append(item)
            continue
        normalized = dict(item)
        for key in ("check", "expected"):
            if key in normalized:
                normalized[key] = stringify_text_value(normalized.get(key))
        postconditions.append(normalized)
    return postconditions


def _adapt_guard_constraint_check_response(data: dict[str, Any]) -> TaskOutputAdapterResult:
    before = _top_level_fingerprint(data)
    if "evidence" in data:
        data["evidence"] = stringify_text_value(data.get("evidence"))
    if "status" in data:
        data["status"] = stringify_text_value(data.get("status"))
    if "confidence" in data:
        data["confidence"] = _numeric_if_convertible(data.get("confidence"))
    if "notes" in data:
        data["notes"] = stringify_text_value(data.get("notes"))
    after = _top_level_fingerprint(data)
    if before == after:
        return TaskOutputAdapterResult()
    return TaskOutputAdapterResult(
        changed=True,
        adapter="guard_constraint_check_shape_normalizer",
        changed_keys=tuple(
            key for key in sorted(set(before) | set(after)) if before.get(key) != after.get(key)
        ),
    )


def _adapt_macro_guard_audit_response(data: dict[str, Any]) -> TaskOutputAdapterResult:
    before = _top_level_fingerprint(data)
    if "dimensions" not in data:
        return TaskOutputAdapterResult()
    dimensions = data.get("dimensions")
    if isinstance(dimensions, dict):
        data["dimensions"] = {
            key: _numeric_if_convertible(dimensions[key])
            for key in _MACRO_GUARD_DIMENSION_KEYS
            if key in dimensions
        }
    if "recommended_action" in data:
        data["recommended_action"] = stringify_text_value(data.get("recommended_action"))
    if "drift_score" in data:
        data["drift_score"] = _numeric_if_convertible(data.get("drift_score"))
    if "findings" in data:
        data["findings"] = _normalize_macro_guard_findings(data.get("findings"))
    if "adjustment_plan" in data:
        data["adjustment_plan"] = _normalize_macro_guard_adjustment_plan(
            data.get("adjustment_plan")
        )
    if "confidence" in data:
        data["confidence"] = _numeric_if_convertible(data.get("confidence"))
    if "reasoning" not in data and "summary" in data:
        data["reasoning"] = stringify_text_value(data.get("summary"))
    elif "reasoning" in data:
        data["reasoning"] = stringify_text_value(data.get("reasoning"))
    data.pop("summary", None)
    after = _top_level_fingerprint(data)
    if before == after:
        return TaskOutputAdapterResult()
    return TaskOutputAdapterResult(
        changed=True,
        adapter="macro_guard_audit_shape_normalizer",
        changed_keys=tuple(
            key for key in sorted(set(before) | set(after)) if before.get(key) != after.get(key)
        ),
    )


def _normalize_macro_guard_findings(value: Any) -> Any:
    if not isinstance(value, list):
        return value
    findings: list[Any] = []
    text_fields = ("severity", "type", "description")
    for item in value:
        if not isinstance(item, dict):
            findings.append(item)
            continue
        normalized = dict(item)
        for key in text_fields:
            if key in normalized:
                normalized[key] = stringify_text_value(normalized.get(key))
        if "evidence" in normalized:
            normalized["evidence"] = _to_str_list(normalized.get("evidence"))
        findings.append(normalized)
    return findings


def _normalize_macro_guard_adjustment_plan(value: Any) -> Any:
    if not isinstance(value, dict):
        return value
    plan = dict(value)
    if "window_size" in plan:
        plan["window_size"] = _integer_if_convertible(plan.get("window_size"))
    for key in ("strategy", "target_outline_v", "reasoning"):
        if key in plan:
            plan[key] = stringify_text_value(plan.get(key))
    if "adjusted_chapter_goals" in plan:
        plan["adjusted_chapter_goals"] = _normalize_macro_guard_adjusted_goals(
            plan.get("adjusted_chapter_goals")
        )
    return plan


def _normalize_macro_guard_adjusted_goals(value: Any) -> Any:
    if not isinstance(value, list):
        return value
    goals: list[Any] = []
    for item in value:
        if not isinstance(item, dict):
            goals.append(item)
            continue
        normalized = dict(item)
        if "chapter_number" in normalized:
            normalized["chapter_number"] = _integer_if_convertible(normalized.get("chapter_number"))
        if "goal" not in normalized and "new_goal" in normalized:
            normalized["goal"] = normalized.pop("new_goal")
        for key in ("goal", "notes", "old_goal"):
            if key in normalized:
                normalized[key] = stringify_text_value(normalized.get(key))
        goals.append(normalized)
    return goals


def _adapt_volume_audit_response(data: dict[str, Any]) -> TaskOutputAdapterResult:
    before = _top_level_fingerprint(data)
    for field_name in (
        "carry_over_characters",
        "retire_characters",
        "carry_over_items",
        "retire_items",
        "carry_over_world_fact_keys",
        "retire_world_fact_keys",
        "carry_over_foreshadowing_ids",
        "resolved_foreshadowing_ids",
        "token_optimization_notes",
    ):
        if field_name in data:
            data[field_name] = _to_str_list(data[field_name])
    milestone_status = data.get("milestone_status")
    if "milestone_status" in data and isinstance(milestone_status, dict):
        data["milestone_status"] = list(milestone_status.values())
    after = _top_level_fingerprint(data)
    if before == after:
        return TaskOutputAdapterResult()
    changed_keys = tuple(
        key for key in sorted(set(before) | set(after)) if before.get(key) != after.get(key)
    )
    return TaskOutputAdapterResult(
        changed=True,
        adapter="volume_audit_shape_normalizer",
        changed_keys=changed_keys,
    )


def _to_str_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if item is not None and str(item).strip()]
    if isinstance(value, dict):
        return [
            str(item).strip() for item in value.values() if item is not None and str(item).strip()
        ]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _adapt_plan_outline_response(
    data: dict[str, Any],
    *,
    context: dict[str, Any],
) -> TaskOutputAdapterResult:
    total_chapters = _total_chapters_from_context_or_payload(context, data)
    before = _top_level_fingerprint(data)
    working_data = {
        key: value for key, value in data.items() if key not in _BLUEPRINT_RUNTIME_METADATA_KEYS
    }
    normalized = pre_normalize_blueprint_payload(working_data, total_chapters=total_chapters)
    if not isinstance(normalized, dict):
        return TaskOutputAdapterResult()
    requested_keys = _blueprint_fragment_required_keys(context)
    if requested_keys:
        normalized = {key: normalized[key] for key in requested_keys if key in normalized}
    else:
        contract = resolve_task_format_contract(TaskType.PLAN_OUTLINE, context)
        allowed_keys = tuple(getattr(contract, "allowed_top_level_keys", ()) or ())
        if allowed_keys:
            normalized = {key: normalized[key] for key in allowed_keys if key in normalized}
    after = _top_level_fingerprint(normalized)
    if before == after:
        return TaskOutputAdapterResult()

    changed_keys = tuple(
        key for key in sorted(set(before) | set(after)) if before.get(key) != after.get(key)
    )
    data.clear()
    data.update(normalized)
    return TaskOutputAdapterResult(
        changed=True,
        adapter="plan_outline_blueprint_normalizer",
        changed_keys=changed_keys,
        metadata={"total_chapters": total_chapters},
    )


def _adapt_relationship_deltas_response(data: dict[str, Any]) -> TaskOutputAdapterResult:
    """Normalize common LLM shape drift in EXTRACT_RELATIONSHIP_DELTAS output.

    The most frequent drift is placing ``characters`` at the delta top level
    instead of inside the ``relationship`` object.  This adapter migrates the
    field to its schema-mandated location so downstream validation passes
    without a costly LLM retry.
    """
    deltas = data.get("relationship_deltas")
    if not isinstance(deltas, list):
        return TaskOutputAdapterResult()

    migrated_count = 0
    pair_id_backfilled = 0

    for delta in deltas:
        if not isinstance(delta, dict):
            continue
        relationship = delta.get("relationship")
        if not isinstance(relationship, dict):
            # If the delta has characters but no relationship object, wrap it.
            characters = delta.get("characters")
            if isinstance(characters, list) and len(characters) >= 2:
                delta["relationship"] = {"characters": characters}
                del delta["characters"]
                migrated_count += 1
            continue

        # Primary fix: move characters from delta level into relationship.
        if "characters" not in relationship and "characters" in delta:
            relationship["characters"] = delta.pop("characters")
            migrated_count += 1

        # Backfill pair_id inside relationship when missing.
        chars = relationship.get("characters")
        if isinstance(chars, list) and len(chars) >= 2 and not relationship.get("pair_id"):
            relationship["pair_id"] = f"{chars[0]}__{chars[1]}"
            pair_id_backfilled += 1

        # Backfill delta-level pair_id from relationship.characters.
        if not delta.get("pair_id") and isinstance(chars, list) and len(chars) >= 2:
            delta["pair_id"] = f"{chars[0]}__{chars[1]}"
            pair_id_backfilled += 1

    # Only report changed when structural migration occurred (characters moved).
    # pair_id backfill is a silent enrichment that doesn't affect schema validity.
    if migrated_count == 0:
        return TaskOutputAdapterResult()
    return TaskOutputAdapterResult(
        changed=True,
        adapter="relationship_deltas_shape_normalizer",
        changed_keys=("relationship_deltas",),
        metadata={
            "characters_migrated": migrated_count,
            "pair_id_backfilled": pair_id_backfilled,
        },
    )


def _top_level_fingerprint(data: dict[str, Any]) -> dict[str, str]:
    return {key: repr(value) for key, value in data.items()}


def _blueprint_fragment_required_keys(context: dict[str, Any]) -> tuple[str, ...]:
    request = context.get("blueprint_fragment_request")
    if not isinstance(request, dict):
        return ()
    keys = tuple(
        str(key).strip()
        for key in request.get("required_keys") or ()
        if isinstance(key, str) and key.strip()
    )
    return tuple(dict.fromkeys(keys))


def _total_chapters_from_context_or_payload(
    context: dict[str, Any],
    payload: dict[str, Any],
) -> int:
    for source in (
        context,
        context.get("outline") if isinstance(context.get("outline"), dict) else None,
        payload,
    ):
        if not isinstance(source, dict):
            continue
        total = _safe_positive_int(source.get("total_chapters"))
        if total:
            return total

    discovered = _max_chapter_reference(payload)
    return discovered or 1


def _safe_positive_int(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    try:
        number = int(value)
    except (TypeError, ValueError):
        return 0
    return number if number > 0 else 0


def _safe_int(value: Any, *, default: int = 0) -> int:
    if isinstance(value, bool):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _to_int_list(value: Any) -> list[int]:
    if not isinstance(value, list):
        return []
    normalized: list[int] = []
    for item in value:
        number = _safe_int(item, default=0)
        if number:
            normalized.append(number)
    return normalized


def _to_float(value: Any, *, default: float = 0.0) -> float:
    if isinstance(value, bool):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _numeric_if_convertible(value: Any) -> Any:
    if isinstance(value, bool | int | float):
        return value
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return value
    return value


def _integer_if_convertible(value: Any) -> Any:
    if isinstance(value, bool | int):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return value
    return value


def _integer_list_if_convertible(value: Any) -> Any:
    if not isinstance(value, list):
        return value
    return [_integer_if_convertible(item) for item in value]


def _to_bool(value: Any, *, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        text = value.strip().lower()
        if text in {"true", "yes", "1", "是", "允许", "切换"}:
            return True
        if text in {"false", "no", "0", "否", "不", "不切换"}:
            return False
    return default


def _max_chapter_reference(value: Any) -> int:
    max_seen = 0

    def visit(item: Any, key_hint: str = "") -> None:
        nonlocal max_seen
        if isinstance(item, dict):
            for key, child in item.items():
                visit(child, str(key))
            return
        if isinstance(item, list):
            for child in item:
                visit(child, key_hint)
            return
        if key_hint not in {
            "chapter",
            "chapter_number",
            "chapter_start",
            "chapter_end",
            "trigger_chapter",
            "resolution_chapter",
            "introduce_chapter",
            "resolve_chapter",
            "involved_chapters",
        }:
            return
        number = _safe_positive_int(item)
        if not number and isinstance(item, str):
            import re

            match = re.search(r"\d+", item)
            if match:
                number = int(match.group(0))
        max_seen = max(max_seen, number)

    visit(value)
    return max_seen
