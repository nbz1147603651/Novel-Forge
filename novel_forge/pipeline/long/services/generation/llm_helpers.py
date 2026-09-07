"""LLM interaction helpers — retry logic, JSON parsing, error classification, routing utils."""

from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable, cast

from novel_forge.common.constants import TaskType
from novel_forge.common.utils import (
    clean_conversation_history,
    excerpt_text,
    unique_texts,
)
from novel_forge.core.format_contracts import (
    merge_required_keys_for_task,
    resolve_task_format_contract,
    should_validate_contract_schema,
    validate_json_output_contract,
)
from novel_forge.core.parsing.parse_utils import safe_parse_json
from novel_forge.core.parsing.response_schemas import validate_response_schema  # noqa: F401
from novel_forge.gateway.retry_policy import (
    classify_llm_error,
    compute_retry_backoff,
    compute_transient_retry_backoff,
    escalate_retry_tokens,
    is_transient_llm_error,
)
from novel_forge.model_runtime.streaming import route_with_observed_stream
from novel_forge.pipeline.long.services.generation.response_observation import ResponseObservation
from novel_forge.pipeline.long.services.task_output_adapters import apply_task_output_adapter
from novel_forge.pipeline.long.services.task_semantic_contracts import (
    validate_task_semantic_contract,
)

if TYPE_CHECKING:
    from novel_forge.gateway.router import ModelRouter


# ---------------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------------


def parse_json_object_response(raw_content: str) -> dict[str, Any]:
    """Parse model response and ensure top-level JSON object."""
    parsed = safe_parse_json(raw_content)
    if not isinstance(parsed, dict):
        raise TypeError("Model response JSON must be an object")
    return cast(dict[str, Any], parsed)


def unwrap_json_object_response(
    data: dict[str, Any],
    *,
    wrapper_keys: tuple[str, ...] = (),
) -> dict[str, Any]:
    """Return a nested JSON object when a task allows profile wrappers."""
    for key in wrapper_keys:
        nested = data.get(key)
        if isinstance(nested, dict):
            return nested
    return data


@dataclass(frozen=True)
class ResponseDamageAssessment:
    """Structured result for response damage checks."""

    is_severe: bool
    reasons: tuple[str, ...] = ()
    missing_optional_sections: tuple[str, ...] = ()
    raw_but_missing_sections: tuple[str, ...] = ()


_EXTRACT_CANON_OPTIONAL_TOP_LEVEL_KEYS = (
    "character_state_deltas",
    "relationship_deltas",
    "plot_thread_deltas",
    "structured_summary",
)


def raw_required_response_keys(
    task_type: TaskType,
    explicit_required_keys: tuple[str, ...] = (),
    context: dict[str, Any] | None = None,
) -> tuple[str, ...]:
    """Return keys that must be present before task-specific normalization.

    EXTRACT_CANON keeps several late sections as top-level contract fields for
    downstream shape consistency, but the step can derive them from
    ``canon_delta``/``creative_report``. Treating those late sections as raw
    hard failures would discard recoverable responses and bypass the normalizer.
    """
    keys = merge_required_keys_for_task(task_type, explicit_required_keys, context=context)
    if task_type == TaskType.EXTRACT_CANON:
        optional = set(_EXTRACT_CANON_OPTIONAL_TOP_LEVEL_KEYS)
        return tuple(key for key in keys if key not in optional)
    return keys


_EXTRACT_CANON_SECTION_CONTAINERS = (
    "canon_delta",
    "creative_report",
    "chapter_exit_state",
)
_EXTRACT_CANON_SECTION_ALIASES = {
    "plot_thread_deltas": ("plot_thread_updates",),
}


def _extract_canon_section_present(data: dict[str, Any], key: str) -> bool:
    """Return True when a late EXTRACT_CANON section survived parsing.

    Repair can leave the section under a semantic container such as
    ``chapter_exit_state`` instead of the contract's preferred top level.  That
    shape is recoverable by the extract normalizer, so the damage detector must
    not treat it as a lost tail.
    """
    aliases = _EXTRACT_CANON_SECTION_ALIASES.get(key, ())
    if key in data and data.get(key) is not None:
        return True
    if any(alias in data and data.get(alias) is not None for alias in aliases):
        return True

    for container_name in _EXTRACT_CANON_SECTION_CONTAINERS:
        container = data.get(container_name)
        if not isinstance(container, dict):
            continue
        if key in container and container.get(key) is not None:
            return True
        if any(alias in container and container.get(alias) is not None for alias in aliases):
            return True
    return False


def assess_extract_canon_response_damage(
    raw_content: str,
    data: dict[str, Any],
    *,
    finish_reason: str | None = None,
    missing_section_threshold: int = 2,
) -> ResponseDamageAssessment:
    """Detect severely damaged EXTRACT_CANON payloads that should not propagate.

    We intentionally tolerate minor repair drift (for example, a single omitted
    optional top-level field) because the current contract only requires the
    non-derivable core sections.  What we want to stop is the heavier failure
    mode where the raw response clearly continues with later sections, but the
    repaired JSON has dropped them — a strong signal that the parser only
    rescued a truncated prefix.
    """
    missing_optional_sections = tuple(
        key
        for key in _EXTRACT_CANON_OPTIONAL_TOP_LEVEL_KEYS
        if not _extract_canon_section_present(data, key)
    )
    raw_but_missing_sections = tuple(
        key
        for key in _EXTRACT_CANON_OPTIONAL_TOP_LEVEL_KEYS
        if not _extract_canon_section_present(data, key) and f'"{key}"' in raw_content
    )

    reasons: list[str] = []
    threshold = max(1, int(missing_section_threshold or 1))

    if finish_reason == "length" and missing_optional_sections:
        reasons.append("length_truncation_with_missing_sections")
    if len(raw_but_missing_sections) >= threshold:
        reasons.append("raw_sections_lost_after_parse")

    return ResponseDamageAssessment(
        is_severe=bool(reasons),
        reasons=tuple(reasons),
        missing_optional_sections=missing_optional_sections,
        raw_but_missing_sections=raw_but_missing_sections,
    )


_LIST_FIELDS = frozenset(
    {
        "volumes",
        "narrative_phases",
        "key_turning_points",
        "character_arcs",
        "subplot_plan",
        "suspense_schedule",
    }
)


_INIT_COHERENCE_PROFILE_TASKS = frozenset(
    {
        TaskType.DERIVE_INIT_COHERENCE_PROFILE,
        TaskType.REFINE_INIT_COHERENCE_PROFILE,
    }
)
_INIT_COHERENCE_ADJUDICATION_TASKS = frozenset(
    {
        TaskType.ADJUDICATE_INIT_CONFLICT_CANDIDATES,
        TaskType.ADJUDICATE_BLUEPRINT_COHERENCE,
        TaskType.ADJUDICATE_OUTLINE_INHERITANCE,
    }
)


def ensure_required_response_keys(data: dict[str, Any], required_keys: tuple[str, ...]) -> None:
    """Validate required top-level keys in model response.

    Performs only exact/case-insensitive key normalization. Missing required
    keys are contract failures and must go through retry/format repair instead
    of being locally synthesized.
    """
    data_lower = {k.lower(): k for k in data.keys()}

    for key in required_keys:
        key_lower = key.lower()
        if key_lower not in data_lower:
            raise KeyError(f"Missing required response key: {key}")

        original_key = data_lower[key_lower]
        if original_key != key:
            data[key] = data.pop(original_key)


def apply_task_response_defaults(data: dict[str, Any], task_type: TaskType) -> None:
    """Normalize exact response projections before strict key checks.

    Missing LLM-authored decisions intentionally remain missing so format
    validation can request a corrected model response.
    """
    if task_type in _INIT_COHERENCE_PROFILE_TASKS:
        _repair_init_coherence_profile_shape(data)

    if task_type in _INIT_COHERENCE_ADJUDICATION_TASKS:
        # These values identify the transport envelope; they do not decide
        # whether a conflict exists or which repair should be accepted.
        data.setdefault("schema_version", "audit_v2")
        data.setdefault("dimension", "init_coherence")
        # Repair-side keys only carry meaning when the verdict asks for repair.
        # Models legitimately omit them for accept/defer/ambiguous verdicts;
        # synthesizing empty defaults prevents a required-key retry storm on a
        # benign response (observed: KeyError retries for missing source_refs /
        # repair_scope / preserve / change_intent / blocked on accept batches).
        verdict = str(data.get("verdict") or "").strip().lower()
        if not verdict or verdict in {"accept", "defer", "ambiguous"}:
            data.setdefault("source_refs", [])
            data.setdefault("repair_scope", [])
            data.setdefault("preserve", [])
            data.setdefault("change_intent", "")
            data.setdefault("blocked", False)


def _repair_init_coherence_profile_shape(data: dict[str, Any]) -> None:
    """Lift top-level coherence profile fields when the model nests them."""
    ontology = data.get("project_ontology")
    if not isinstance(ontology, dict):
        return

    for key in ("narrative_modes", "conflict_lens", "extraction_guidance", "summary"):
        if key not in data and key in ontology:
            data[key] = ontology.pop(key)


def reject_damaged_local_json_repair(
    *,
    task_type: TaskType,
    strict_json_error: json.JSONDecodeError | None,
    finish_reason: str | None,
) -> bool:
    """Return True when a repaired JSON prefix should be retried instead of accepted."""
    if strict_json_error is None or finish_reason != "length":
        return False
    return task_type in {
        TaskType.PLAN_CHAPTER_CONTRACTS,
        TaskType.ADJUDICATE_CONTRACT_COHERENCE,
    }


async def route_json_object_with_retry(
    router: Any,
    request: Any,
    *,
    task_type: TaskType,
    context: dict[str, Any] | None = None,
    required_keys: tuple[str, ...] = (),
    include_contract_required_keys: bool = True,
    retry_temperature: float = 0.0,
    wrapper_keys: tuple[str, ...] = (),
    on_step: Callable[[str, dict[str, Any]], None] | None = None,
    observe_stream: bool = False,
    chapter: Any = "",
    escalation_request_update: Callable[[Exception, Any | None, Any], dict[str, Any] | None]
    | None = None,
) -> dict[str, Any]:
    """Route a JSON task, validate its structure, and retry once at lower temperature."""
    effective_required_keys = (
        raw_required_response_keys(task_type, required_keys, context=context)
        if include_contract_required_keys
        else required_keys
    )
    contract = resolve_task_format_contract(task_type, context)
    validate_contract_schema = should_validate_contract_schema(
        contract,
        include_contract_required_keys=include_contract_required_keys,
    )
    current_request = request
    last_exc: Exception | None = None
    last_response: Any | None = None
    max_attempts = 3 if escalation_request_update is not None else 2
    operation_id = uuid.uuid4().hex

    async def _route(current: Any, *, observation: ResponseObservation) -> Any:
        if (
            observe_stream
            and on_step is not None
            and callable(getattr(router, "stream_route", None))
        ):
            return await route_with_observed_stream(
                router,
                current,
                on_step=on_step,
                task_type=task_type,
                chapter=chapter,
                attempt=observation.attempt,
                max_attempts=max_attempts,
                stream_kind="json_observation",
                output_kind="json",
                stream_id=observation.stream_id,
                operation_id=operation_id,
            )
        return await router.route(current)

    for attempt in range(2):
        observation = ResponseObservation(
            on_step or (lambda _step, _data: None),
            task_type.value,
            operation_id,
            uuid.uuid4().hex,
            attempt + 1,
            max_attempts,
        )
        try:
            last_response = await _route(current_request, observation=observation)
            observation.emit("validating", response=last_response)
            parsed = unwrap_json_object_response(
                parse_json_object_response(last_response.content),
                wrapper_keys=wrapper_keys,
            )
            adapter_result = apply_task_output_adapter(parsed, task_type, context=context or {})
            if adapter_result.changed:
                observation.emit(
                    "repairing",
                    repair_source="local_repair",
                    response=last_response,
                )
            apply_task_response_defaults(parsed, task_type)
            if include_contract_required_keys:
                validate_response_schema(parsed, task_type)
            validate_json_output_contract(
                task_type,
                parsed,
                context=context,
                explicit_required_keys=effective_required_keys,
                include_contract_required_keys=False,
                validate_allowed_keys=validate_contract_schema,
                validate_json_schema=validate_contract_schema,
            )
            if effective_required_keys:
                ensure_required_response_keys(parsed, effective_required_keys)
            validate_task_semantic_contract(parsed, task_type)
            observation.emit("validated", data=parsed, response=last_response)
            return parsed
        except asyncio.CancelledError:
            observation.emit("failed", error=ValueError("任务已取消，未完成校验"))
            raise
        except Exception as exc:
            last_exc = exc
            if attempt == 0:
                observation.emit("retrying", error=exc, response=last_response)
                current_request = current_request.model_copy(
                    update={
                        "temperature": float(retry_temperature),
                        "temperature_jitter_allowed": False,
                    }
                )
                continue
            break

    if escalation_request_update is not None and last_exc is not None:
        escalation_update = escalation_request_update(last_exc, last_response, current_request)
        if escalation_update:
            observation.emit("retrying", error=last_exc, response=last_response)
            observation = ResponseObservation(
                on_step or (lambda _step, _data: None),
                task_type.value,
                operation_id,
                uuid.uuid4().hex,
                max_attempts,
                max_attempts,
            )
            escalated_request = current_request.model_copy(update=escalation_update)
            try:
                last_response = await _route(escalated_request, observation=observation)
                observation.emit("validating", response=last_response)
                parsed = unwrap_json_object_response(
                    parse_json_object_response(last_response.content),
                    wrapper_keys=wrapper_keys,
                )
                adapter_result = apply_task_output_adapter(parsed, task_type, context=context or {})
                if adapter_result.changed:
                    observation.emit(
                        "repairing",
                        repair_source="local_repair",
                        response=last_response,
                    )
                apply_task_response_defaults(parsed, task_type)
                if include_contract_required_keys:
                    validate_response_schema(parsed, task_type)
                validate_json_output_contract(
                    task_type,
                    parsed,
                    context=context,
                    explicit_required_keys=effective_required_keys,
                    include_contract_required_keys=False,
                    validate_allowed_keys=validate_contract_schema,
                    validate_json_schema=validate_contract_schema,
                )
                if effective_required_keys:
                    ensure_required_response_keys(parsed, effective_required_keys)
                validate_task_semantic_contract(parsed, task_type)
                observation.emit("validated", data=parsed, response=last_response)
                return parsed
            except asyncio.CancelledError:
                observation.emit("failed", error=ValueError("任务已取消，未完成校验"))
                raise
            except Exception as exc:
                observation.emit("failed", error=exc, response=last_response)
                raise

    assert last_exc is not None
    observation.emit("failed", error=last_exc, response=last_response)
    raise last_exc


# ---------------------------------------------------------------------------
# Router-level task resolution helpers (shared by short_runner & chapter_runner)
# ---------------------------------------------------------------------------


def resolve_task_provider_model(
    router: ModelRouter,
    task_type: TaskType,
) -> tuple[str, str]:
    """Resolve effective provider/model for a task using router defaults/overrides."""
    override = router.task_route_overrides.get(task_type)
    provider = (override.provider if override else router.default_provider).strip()

    model_id = ""
    if override is not None and override.model_id:
        model_id = override.model_id.strip()
    else:
        adapter = router.get_adapter(provider)
        if adapter and adapter.default_model:
            model_id = adapter.default_model.strip()

    return provider.lower(), model_id.lower()


def is_option_enabled_for_task(
    router: ModelRouter,
    *,
    capability: str,
    enabled: bool,
    allowed_providers_raw: str,
    allowed_models_raw: str,
    task_type: TaskType,
) -> bool:
    """Resolve feature-option flags from task routing override or global allow-lists.

    Checks routing overrides first (thinking/multi_turn), then falls back
    to global enable/disable with provider/model allow-lists.
    """
    from novel_forge.pipeline.long.services.blueprint import outline_helpers as outline_h

    override = router.task_route_overrides.get(task_type)
    if override is not None:
        if capability == "thinking":
            return bool(override.thinking)
        if capability == "multi_turn":
            return bool(override.multi_turn)
        return False

    if not enabled:
        return False
    provider, model_id = resolve_task_provider_model(router, task_type)
    allowed_providers = outline_h.parse_csv_items(allowed_providers_raw)
    allowed_models = outline_h.parse_csv_items(allowed_models_raw)
    return outline_h.is_target_allowed(
        provider,
        model_id,
        allowed_providers=allowed_providers,
        allowed_models=allowed_models,
    )


__all__ = [
    "ResponseDamageAssessment",
    "assess_extract_canon_response_damage",
    "classify_llm_error",
    "clean_conversation_history",
    "compute_retry_backoff",
    "compute_transient_retry_backoff",
    "ensure_required_response_keys",
    "escalate_retry_tokens",
    "excerpt_text",
    "is_transient_llm_error",
    "is_option_enabled_for_task",
    "parse_json_object_response",
    "resolve_task_provider_model",
    "route_with_observed_stream",
    "route_json_object_with_retry",
    "unwrap_json_object_response",
    "unique_texts",
    "validate_response_schema",
]
