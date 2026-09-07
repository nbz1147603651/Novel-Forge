"""Structured-output capability policy for gateway adapters.

This module is the single place where static model capability declarations,
provider defaults, and runtime unsupported caches are composed into a gateway
mode decision. Provider adapters should only translate the resulting mode into
their native API parameters.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Mapping

from novel_forge.gateway.model_capabilities import (
    capability_profile_path,
    load_model_capability_registry,
    resolve_model_capability,
)
from novel_forge.gateway.types import ModelRequest

logger = logging.getLogger(__name__)


class StructuredOutputMode(str, Enum):
    """Gateway-level structured-output generation controls."""

    JSON_SCHEMA_STRICT = "json_schema_strict"
    JSON_SCHEMA = "json_schema"
    JSON_OBJECT = "json_object"
    PROMPT_ONLY = "prompt_only"
    CONSTRAINED_DECODING = "constrained_decoding"


class StructuredOutputDialect(str, Enum):
    """Provider parameter dialect used to translate a policy decision."""

    OPENAI_RESPONSE_FORMAT = "openai_response_format"
    ANTHROPIC_OUTPUT_CONFIG = "anthropic_output_config"


class StructuredOutputSupport(str, Enum):
    """Static capability declaration state for one structured-output feature."""

    SUPPORTED = "supported"
    UNSUPPORTED = "unsupported"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class StructuredOutputCapabilities:
    """Static structured-output capabilities for one model profile entry."""

    json_schema: StructuredOutputSupport = StructuredOutputSupport.UNKNOWN
    json_mode: StructuredOutputSupport = StructuredOutputSupport.UNKNOWN
    tool_calling: StructuredOutputSupport = StructuredOutputSupport.UNKNOWN
    strict_schema: StructuredOutputSupport = StructuredOutputSupport.UNKNOWN
    constrained_decoding: StructuredOutputSupport = StructuredOutputSupport.UNKNOWN
    notes: str = ""


@dataclass(frozen=True)
class ProviderStructuredOutputDefaults:
    """Provider default capability hints used when static profile is unknown."""

    json_schema: bool
    json_object: bool
    reason: str
    strict_schema: bool = False
    hard_disabled: bool = False


@dataclass(frozen=True)
class StructuredOutputDecision:
    """Resolved structured-output mode for one request/model pair."""

    mode: StructuredOutputMode
    strict: bool = False
    reason: str = ""
    provider: str = ""
    model_id: str = ""
    profile_match: str = ""
    downgraded_from: StructuredOutputMode | None = None


@dataclass(frozen=True)
class StructuredOutputRequestPlan:
    """Provider kwargs and telemetry fields for one structured-output request."""

    kwargs: dict[str, Any]
    decision: StructuredOutputDecision
    native_control_keys: tuple[str, ...] = ()

    def has_native_controls(self, kwargs: Mapping[str, Any] | None = None) -> bool:
        """Return whether native structured-output controls are present."""

        source = kwargs if kwargs is not None else self.kwargs
        return any(key in source for key in self.native_control_keys)

    def without_native_controls(self, kwargs: Mapping[str, Any]) -> dict[str, Any]:
        """Return ``kwargs`` with native structured-output controls removed."""

        cleaned = dict(kwargs)
        for key in self.native_control_keys:
            cleaned.pop(key, None)
        return cleaned

    def with_runtime_fallback(
        self,
        *,
        reason: str = "runtime_unsupported_fallback",
    ) -> StructuredOutputRequestPlan:
        """Return a prompt-only fallback plan after provider-native rejection."""

        downgraded_from = (
            None if self.decision.mode == StructuredOutputMode.PROMPT_ONLY else self.decision.mode
        )
        fallback_decision = StructuredOutputDecision(
            mode=StructuredOutputMode.PROMPT_ONLY,
            strict=False,
            reason=reason,
            provider=self.decision.provider,
            model_id=self.decision.model_id,
            profile_match=self.decision.profile_match,
            downgraded_from=downgraded_from,
        )
        return StructuredOutputRequestPlan(
            kwargs={},
            decision=fallback_decision,
            native_control_keys=self.native_control_keys,
        )

    def response_fields(self) -> dict[str, str]:
        """Return ModelResponse structured-output telemetry fields."""

        return {
            "structured_output_mode": self.decision.mode.value,
            "structured_output_downgraded_from": (
                self.decision.downgraded_from.value if self.decision.downgraded_from else ""
            ),
            "structured_output_reason": self.decision.reason,
        }


_STRUCTURED_OUTPUT_UNSUPPORTED_CACHE: set[tuple[str, str]] = set()
_STRUCTURED_OUTPUT_UNSUPPORTED_LOCK = threading.Lock()

_OPENAI_JSON_SCHEMA_UNSUPPORTED_MODELS = {
    "gpt-4",
    "gpt-4-turbo",
    "gpt-4-turbo-preview",
    "gpt-3.5-turbo",
}
_OPENAI_JSON_SCHEMA_UNSUPPORTED_PREFIXES = (
    "gpt-3.5-",
    "gpt-4-",
)
_OPENAI_JSON_SCHEMA_SUPPORTED_PREFIXES = (
    "gpt-4o",
    "gpt-4.1",
    "gpt-4.5",
    "gpt-5",
    "o1",
    "o3",
    "o4",
)
_MINIMAX_JSON_SCHEMA_MODELS = {"minimax-text-01"}
_VOLCENGINE_ARK_JSON_SCHEMA_MODELS: frozenset[str] = frozenset(
    {
        "doubao-seed-2.0-pro",
        "doubao-seed-2.0-lite",
        "doubao-seed-2.0-mini",
        "doubao-seed-2.0-code",
    }
)
_VOLCENGINE_ARK_JSON_SCHEMA_PREFIXES: tuple[str, ...] = ("doubao-seed-2.0-",)
_ANTHROPIC_JSON_SCHEMA_MARKERS: tuple[str, ...] = (
    "fable-5",
    "claude-fable-5",
    "mythos-5",
    "mythos-preview",
    "claude-mythos",
    "opus-4",
    "claude-opus-4",
    "sonnet-5",
    "claude-sonnet-5",
    "sonnet-4",
    "claude-sonnet-4",
    "haiku-4",
    "claude-haiku-4",
)


def _support_from_value(value: Any) -> StructuredOutputSupport:
    if isinstance(value, StructuredOutputSupport):
        return value
    if isinstance(value, bool):
        return StructuredOutputSupport.SUPPORTED if value else StructuredOutputSupport.UNSUPPORTED
    normalized = str(value or "").strip().lower()
    if normalized in {"supported", "support", "yes", "true", "1"}:
        return StructuredOutputSupport.SUPPORTED
    if normalized in {"unsupported", "no", "false", "0"}:
        return StructuredOutputSupport.UNSUPPORTED
    return StructuredOutputSupport.UNKNOWN


def structured_output_profile_path() -> Path:
    """Return the default static model capability profile path."""

    return capability_profile_path()


def load_structured_output_profile(path: str | None = None) -> dict[str, Any]:
    """Load static model capability declarations.

    Missing or invalid profiles are treated as an empty declaration so gateway
    routing can continue with provider defaults.
    """

    return load_model_capability_registry(path)


def structured_output_capabilities_from_dict(
    data: dict[str, Any] | None,
) -> StructuredOutputCapabilities:
    """Parse the ``structured_output`` object from one model profile entry."""

    raw = data or {}
    return StructuredOutputCapabilities(
        json_schema=_support_from_value(raw.get("json_schema")),
        json_mode=_support_from_value(raw.get("json_mode")),
        tool_calling=_support_from_value(raw.get("tool_calling")),
        strict_schema=_support_from_value(raw.get("strict_schema")),
        constrained_decoding=_support_from_value(raw.get("constrained_decoding")),
        notes=str(raw.get("notes") or ""),
    )


def structured_output_capabilities_for_model(
    provider: str,
    model_id: str | None,
    *,
    profile: dict[str, Any] | None = None,
) -> tuple[StructuredOutputCapabilities, str]:
    """Return static structured-output capabilities and matched profile key."""

    data = profile if profile is not None else load_structured_output_profile()
    record = resolve_model_capability(provider, model_id, registry=data)
    if not record.profile_match:
        return StructuredOutputCapabilities(), ""
    return (
        structured_output_capabilities_from_dict(dict(record.structured_output)),
        record.profile_match,
    )


def supports_openai_json_schema(model_key: str) -> bool:
    """Return whether an OpenAI model should receive ``json_schema`` mode."""

    if not model_key:
        return True
    if model_key.startswith(_OPENAI_JSON_SCHEMA_SUPPORTED_PREFIXES):
        return True
    if model_key in _OPENAI_JSON_SCHEMA_UNSUPPORTED_MODELS:
        return False
    return not model_key.startswith(_OPENAI_JSON_SCHEMA_UNSUPPORTED_PREFIXES)


def volcengine_ark_supports_json_schema(model_key: str) -> bool:
    """Return whether a Volcengine Ark model accepts ``response_format=json_schema``."""

    if not model_key:
        return False
    lowered = model_key.strip().lower()
    if lowered in _VOLCENGINE_ARK_JSON_SCHEMA_MODELS:
        return True
    return any(lowered.startswith(prefix) for prefix in _VOLCENGINE_ARK_JSON_SCHEMA_PREFIXES)


def anthropic_supports_json_schema(model_key: str) -> bool:
    """Return whether an Anthropic model should receive ``output_config=json_schema``."""

    if not model_key:
        return False
    lowered = model_key.strip().lower()
    return any(marker in lowered for marker in _ANTHROPIC_JSON_SCHEMA_MARKERS)


def provider_structured_output_defaults(
    provider: str,
    model_id: str | None,
    *,
    thinking: bool = False,
) -> ProviderStructuredOutputDefaults:
    """Return provider-native structured-output defaults."""

    provider_key = str(provider or "").strip().lower()
    model_key = str(model_id or "").strip().lower()

    if provider_key == "openai":
        if supports_openai_json_schema(model_key):
            return ProviderStructuredOutputDefaults(
                True,
                True,
                "openai_json_schema_default",
                strict_schema=True,
            )
        return ProviderStructuredOutputDefaults(False, True, "openai_json_object_default")

    if provider_key == "tongyi":
        if thinking:
            return ProviderStructuredOutputDefaults(
                False,
                False,
                "tongyi_thinking_structured_output_disabled",
                hard_disabled=True,
            )
        return ProviderStructuredOutputDefaults(False, True, "tongyi_json_object_default")

    if provider_key == "ollama":
        return ProviderStructuredOutputDefaults(True, False, "ollama_json_schema_default")

    if provider_key == "deepseek":
        return ProviderStructuredOutputDefaults(False, True, "deepseek_json_object_default")

    if provider_key == "kimi":
        return ProviderStructuredOutputDefaults(False, True, "kimi_json_object_default")

    if provider_key == "mimo":
        if model_key in {"mimo-v2.5-pro", "mimo-v2.5"}:
            return ProviderStructuredOutputDefaults(False, True, "mimo_json_object_default")
        return ProviderStructuredOutputDefaults(False, False, "mimo_prompt_only_default")

    if provider_key == "minimax":
        if model_key in _MINIMAX_JSON_SCHEMA_MODELS:
            return ProviderStructuredOutputDefaults(True, False, "minimax_json_schema_allowlist")
        return ProviderStructuredOutputDefaults(False, False, "minimax_prompt_only_default")

    if provider_key == "volcengine_ark":
        if volcengine_ark_supports_json_schema(model_key):
            return ProviderStructuredOutputDefaults(True, True, "volcengine_ark_json_schema_family")
        return ProviderStructuredOutputDefaults(False, True, "volcengine_ark_json_object_default")

    if provider_key == "siliconflow":
        return ProviderStructuredOutputDefaults(False, True, "siliconflow_json_object_default")

    if provider_key == "custom":
        return ProviderStructuredOutputDefaults(False, False, "custom_prompt_only_default")

    if provider_key == "mock":
        # Mock responses are deterministic, contract-shaped fixtures produced
        # in-process; no external model is left to hand-write the JSON.
        return ProviderStructuredOutputDefaults(
            True,
            False,
            "mock_json_schema_default",
            strict_schema=True,
        )

    if provider_key == "anthropic":
        if anthropic_supports_json_schema(model_key):
            return ProviderStructuredOutputDefaults(
                True,
                False,
                "anthropic_json_schema_default",
                strict_schema=True,
            )
        return ProviderStructuredOutputDefaults(False, False, "anthropic_prompt_only_default")

    return ProviderStructuredOutputDefaults(False, False, "provider_prompt_only_default")


def is_structured_output_unsupported_error(exc: Exception) -> bool:
    """Return whether an exception indicates native structured-output rejection."""

    message = str(exc).lower()
    if not any(
        marker in message
        for marker in ("response_format", "output_config", "json_schema", "json_object")
    ):
        return False
    return any(
        marker in message
        for marker in (
            "unsupported",
            "not support",
            "not supported",
            "unknown",
            "unrecognized",
            "invalid",
            "not allowed",
            "extra inputs",
            "extra_forbidden",
            "permitted",
        )
    )


def is_structured_output_cached_unsupported(provider: str, model_id: str | None) -> bool:
    """Return whether this provider/model rejected native structured output earlier."""

    provider_key = str(provider or "").strip().lower()
    model_key = str(model_id or "").strip().lower()
    if not provider_key or not model_key:
        return False
    with _STRUCTURED_OUTPUT_UNSUPPORTED_LOCK:
        return (provider_key, model_key) in _STRUCTURED_OUTPUT_UNSUPPORTED_CACHE


def remember_structured_output_unsupported(provider: str, model_id: str | None) -> None:
    """Remember a runtime native-structured-output rejection for this model."""

    provider_key = str(provider or "").strip().lower()
    model_key = str(model_id or "").strip().lower()
    if not provider_key or not model_key:
        return
    with _STRUCTURED_OUTPUT_UNSUPPORTED_LOCK:
        _STRUCTURED_OUTPUT_UNSUPPORTED_CACHE.add((provider_key, model_key))


def _profile_allows(
    declared: StructuredOutputSupport,
    *,
    provider_default: bool,
) -> bool:
    if declared == StructuredOutputSupport.SUPPORTED:
        return True
    if declared == StructuredOutputSupport.UNSUPPORTED:
        return False
    return provider_default


def _target_from_capability(
    *,
    response_schema_strict: bool,
    strict_schema_compatible: bool,
) -> StructuredOutputMode:
    if response_schema_strict and strict_schema_compatible:
        return StructuredOutputMode.JSON_SCHEMA_STRICT
    return StructuredOutputMode.JSON_SCHEMA


def _first_mode_lower_than(
    target: StructuredOutputMode,
    *,
    json_schema_allowed: bool,
    json_object_allowed: bool,
    strict_schema_allowed: bool,
    response_schema_strict: bool,
    strict_schema_compatible: bool,
) -> StructuredOutputMode:
    if json_schema_allowed:
        if (
            target == StructuredOutputMode.JSON_SCHEMA_STRICT
            and response_schema_strict
            and strict_schema_compatible
            and strict_schema_allowed
        ):
            return StructuredOutputMode.JSON_SCHEMA_STRICT
        return StructuredOutputMode.JSON_SCHEMA
    if json_object_allowed:
        return StructuredOutputMode.JSON_OBJECT
    return StructuredOutputMode.PROMPT_ONLY


def resolve_structured_output_policy(
    *,
    provider: str,
    model_id: str | None,
    has_schema: bool,
    response_schema_strict: bool,
    strict_schema_compatible: bool,
    thinking: bool = False,
    runtime_unsupported: bool = False,
    profile: dict[str, Any] | None = None,
) -> StructuredOutputDecision:
    """Resolve the final structured-output mode for a gateway request."""

    provider_key = str(provider or "").strip().lower()
    model_key = str(model_id or "").strip().lower()
    if not has_schema:
        return StructuredOutputDecision(
            mode=StructuredOutputMode.PROMPT_ONLY,
            provider=provider_key,
            model_id=model_key,
            reason="request_has_no_json_schema",
        )
    if runtime_unsupported:
        return StructuredOutputDecision(
            mode=StructuredOutputMode.PROMPT_ONLY,
            provider=provider_key,
            model_id=model_key,
            reason="runtime_unsupported_cache",
            downgraded_from=_target_from_capability(
                response_schema_strict=response_schema_strict,
                strict_schema_compatible=strict_schema_compatible,
            ),
        )

    defaults = provider_structured_output_defaults(provider_key, model_key, thinking=thinking)
    if defaults.hard_disabled:
        return StructuredOutputDecision(
            mode=StructuredOutputMode.PROMPT_ONLY,
            provider=provider_key,
            model_id=model_key,
            reason=defaults.reason,
            downgraded_from=_target_from_capability(
                response_schema_strict=response_schema_strict,
                strict_schema_compatible=strict_schema_compatible,
            ),
        )

    capabilities, profile_match = structured_output_capabilities_for_model(
        provider_key,
        model_key,
        profile=profile,
    )
    json_schema_allowed = _profile_allows(
        capabilities.json_schema,
        provider_default=defaults.json_schema,
    )
    json_object_allowed = _profile_allows(
        capabilities.json_mode,
        provider_default=defaults.json_object,
    )
    strict_schema_allowed = _profile_allows(
        capabilities.strict_schema,
        provider_default=defaults.strict_schema,
    )
    target = _target_from_capability(
        response_schema_strict=response_schema_strict,
        strict_schema_compatible=strict_schema_compatible,
    )
    mode = _first_mode_lower_than(
        target,
        json_schema_allowed=json_schema_allowed,
        json_object_allowed=json_object_allowed,
        strict_schema_allowed=strict_schema_allowed,
        response_schema_strict=response_schema_strict,
        strict_schema_compatible=strict_schema_compatible,
    )
    strict = mode == StructuredOutputMode.JSON_SCHEMA_STRICT
    downgraded_from = target if mode != target else None
    return StructuredOutputDecision(
        mode=mode,
        strict=strict,
        provider=provider_key,
        model_id=model_key,
        profile_match=profile_match,
        downgraded_from=downgraded_from,
        reason=defaults.reason if not profile_match else f"profile:{profile_match}",
    )


def is_strict_json_schema_compatible(schema: dict[str, Any]) -> bool:
    """Return whether a JSON Schema can safely request provider strict mode."""

    if not isinstance(schema, dict) or not schema:
        return False

    schema_type = schema.get("type")
    if schema_type == "object" or "properties" in schema:
        properties = schema.get("properties")
        if not isinstance(properties, dict):
            return False
        if schema.get("additionalProperties") is not False:
            return False
        required = schema.get("required")
        if not isinstance(required, list) or set(required) != set(properties):
            return False
        return all(
            not isinstance(child_schema, dict)
            or _is_strict_json_schema_compatible_for_child(child_schema)
            for child_schema in properties.values()
        )

    if schema_type == "array":
        item_schema = schema.get("items")
        return not isinstance(item_schema, dict) or _is_strict_json_schema_compatible_for_child(
            item_schema
        )

    return True


def _is_strict_json_schema_compatible_for_child(schema: dict[str, Any]) -> bool:
    if not schema:
        return True
    return is_strict_json_schema_compatible(schema)


def _openai_json_schema_response_format(
    schema_name: str,
    schema: dict[str, Any],
    strict: bool,
) -> dict[str, Any]:
    return {
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": schema_name,
                "strict": strict,
                "schema": schema,
            },
        },
    }


def _openai_json_object_response_format() -> dict[str, Any]:
    return {"response_format": {"type": "json_object"}}


def _anthropic_json_schema_output_config(schema: dict[str, Any]) -> dict[str, Any]:
    return {
        "output_config": {
            "format": {
                "type": "json_schema",
                "schema": schema,
            },
        },
    }


def _translate_decision_to_provider_kwargs(
    *,
    dialect: StructuredOutputDialect,
    decision: StructuredOutputDecision,
    schema_name: str,
    schema: dict[str, Any] | None,
) -> tuple[dict[str, Any], tuple[str, ...]]:
    if schema is None:
        return {}, ()

    if dialect == StructuredOutputDialect.OPENAI_RESPONSE_FORMAT:
        if decision.mode in {
            StructuredOutputMode.JSON_SCHEMA,
            StructuredOutputMode.JSON_SCHEMA_STRICT,
        }:
            return (
                _openai_json_schema_response_format(schema_name, schema, decision.strict),
                ("response_format",),
            )
        if decision.mode == StructuredOutputMode.JSON_OBJECT:
            return _openai_json_object_response_format(), ("response_format",)
        return {}, ("response_format",)

    if dialect == StructuredOutputDialect.ANTHROPIC_OUTPUT_CONFIG:
        if decision.mode in {
            StructuredOutputMode.JSON_SCHEMA,
            StructuredOutputMode.JSON_SCHEMA_STRICT,
        }:
            return _anthropic_json_schema_output_config(schema), ("output_config",)
        return {}, ("output_config",)

    return {}, ()


def log_structured_output_decision(task: str, decision: StructuredOutputDecision) -> None:
    """Emit a structured-output policy event through the adapter logger."""

    if decision.downgraded_from is None:
        logger.debug(
            "structured_output_policy",
            extra={
                "provider": decision.provider,
                "model_id": decision.model_id,
                "task": task,
                "mode": decision.mode.value,
                "reason": decision.reason,
                "profile_match": decision.profile_match,
            },
        )
        return
    logger.info(
        "structured_output_policy_downgrade",
        extra={
            "provider": decision.provider,
            "model_id": decision.model_id,
            "task": task,
            "from_mode": decision.downgraded_from.value,
            "to_mode": decision.mode.value,
            "reason": decision.reason,
            "profile_match": decision.profile_match,
        },
    )


def build_structured_output_request_plan(
    request: ModelRequest,
    *,
    provider: str,
    model_id: str | None,
    dialect: StructuredOutputDialect,
) -> StructuredOutputRequestPlan:
    """Resolve policy and translate it into provider-native request kwargs."""

    schema = request.response_json_schema
    schema_name = request.response_schema_name or request.task_type.value
    provider_key = str(provider or "").strip().lower()
    model_key = str(model_id or "").strip().lower()
    strict_compatible = is_strict_json_schema_compatible(schema or {})
    decision = resolve_structured_output_policy(
        provider=provider_key,
        model_id=model_key,
        has_schema=bool(schema),
        response_schema_strict=bool(request.response_schema_strict),
        strict_schema_compatible=strict_compatible,
        thinking=bool(request.thinking),
        runtime_unsupported=is_structured_output_cached_unsupported(provider_key, model_key),
    )
    log_structured_output_decision(request.task_type.value, decision)
    kwargs, native_control_keys = _translate_decision_to_provider_kwargs(
        dialect=dialect,
        decision=decision,
        schema_name=schema_name,
        schema=schema,
    )
    return StructuredOutputRequestPlan(
        kwargs=kwargs,
        decision=decision,
        native_control_keys=native_control_keys,
    )


__all__ = [
    "StructuredOutputCapabilities",
    "StructuredOutputDecision",
    "StructuredOutputDialect",
    "StructuredOutputMode",
    "StructuredOutputRequestPlan",
    "StructuredOutputSupport",
    "anthropic_supports_json_schema",
    "build_structured_output_request_plan",
    "is_strict_json_schema_compatible",
    "is_structured_output_cached_unsupported",
    "is_structured_output_unsupported_error",
    "load_structured_output_profile",
    "log_structured_output_decision",
    "provider_structured_output_defaults",
    "remember_structured_output_unsupported",
    "resolve_structured_output_policy",
    "structured_output_capabilities_for_model",
    "structured_output_capabilities_from_dict",
    "structured_output_profile_path",
    "supports_openai_json_schema",
    "volcengine_ark_supports_json_schema",
]
