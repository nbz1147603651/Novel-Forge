"""Structured-output gateway policy tests."""

from __future__ import annotations

from novel_forge.common.constants import TaskType
from novel_forge.gateway.structured_output import (
    StructuredOutputDialect,
    StructuredOutputMode,
    StructuredOutputSupport,
    build_structured_output_request_plan,
    resolve_structured_output_policy,
    structured_output_capabilities_from_dict,
)
from novel_forge.gateway.types import ModelRequest

_STRICT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["summary"],
    "properties": {"summary": {"type": "string"}},
}


def test_capability_profile_parses_structured_output_support_values() -> None:
    caps = structured_output_capabilities_from_dict(
        {
            "json_schema": "supported",
            "json_mode": False,
            "tool_calling": True,
            "strict_schema": "unsupported",
            "constrained_decoding": "unknown",
        }
    )

    assert caps.json_schema == StructuredOutputSupport.SUPPORTED
    assert caps.json_mode == StructuredOutputSupport.UNSUPPORTED
    assert caps.tool_calling == StructuredOutputSupport.SUPPORTED
    assert caps.strict_schema == StructuredOutputSupport.UNSUPPORTED
    assert caps.constrained_decoding == StructuredOutputSupport.UNKNOWN


def test_policy_selects_json_schema_strict_when_provider_and_schema_allow_it() -> None:
    decision = resolve_structured_output_policy(
        provider="openai",
        model_id="gpt-4o",
        has_schema=True,
        response_schema_strict=True,
        strict_schema_compatible=True,
    )

    assert decision.mode == StructuredOutputMode.JSON_SCHEMA_STRICT
    assert decision.strict is True
    assert decision.downgraded_from is None


def test_policy_downgrades_profile_json_schema_unsupported_to_json_object() -> None:
    profile = {
        "models": {
            "deepseek/deepseek-v4-flash": {
                "structured_output": {
                    "json_schema": "unsupported",
                    "json_mode": "supported",
                    "strict_schema": "unsupported",
                }
            }
        }
    }

    decision = resolve_structured_output_policy(
        provider="deepseek",
        model_id="deepseek-v4-flash",
        has_schema=True,
        response_schema_strict=True,
        strict_schema_compatible=True,
        profile=profile,
    )

    assert decision.mode == StructuredOutputMode.JSON_OBJECT
    assert decision.downgraded_from == StructuredOutputMode.JSON_SCHEMA_STRICT
    assert decision.profile_match == "deepseek/deepseek-v4-flash"


def test_policy_uses_tongyi_json_object_default() -> None:
    decision = resolve_structured_output_policy(
        provider="tongyi",
        model_id="qwen-turbo",
        has_schema=True,
        response_schema_strict=True,
        strict_schema_compatible=True,
        profile={"models": {}},
    )

    assert decision.mode == StructuredOutputMode.JSON_OBJECT
    assert decision.downgraded_from == StructuredOutputMode.JSON_SCHEMA_STRICT
    assert decision.reason == "tongyi_json_object_default"


def test_policy_uses_kimi_documented_json_object_by_default() -> None:
    decision = resolve_structured_output_policy(
        provider="kimi",
        model_id="moonshot-v1",
        has_schema=True,
        response_schema_strict=True,
        strict_schema_compatible=True,
        profile={"models": {}},
    )

    assert decision.mode == StructuredOutputMode.JSON_OBJECT
    assert decision.strict is False
    assert decision.downgraded_from == StructuredOutputMode.JSON_SCHEMA_STRICT
    assert decision.reason == "kimi_json_object_default"


def test_minimax_m27_highspeed_is_prompt_only_from_verified_profile() -> None:
    decision = resolve_structured_output_policy(
        provider="minimax",
        model_id="MiniMax-M2.7-highspeed",
        has_schema=True,
        response_schema_strict=True,
        strict_schema_compatible=True,
    )

    assert decision.mode == StructuredOutputMode.PROMPT_ONLY
    assert decision.downgraded_from == StructuredOutputMode.JSON_SCHEMA_STRICT
    assert "minimax/MiniMax-M2.7-highspeed" in decision.profile_match


def test_unknown_custom_endpoint_does_not_receive_optimistic_json_schema() -> None:
    decision = resolve_structured_output_policy(
        provider="custom",
        model_id="vendor-private-model",
        has_schema=True,
        response_schema_strict=True,
        strict_schema_compatible=True,
        profile={"models": {}, "model_families": []},
    )

    assert decision.mode == StructuredOutputMode.PROMPT_ONLY
    assert decision.reason == "custom_prompt_only_default"


def test_policy_runtime_unsupported_cache_overrides_static_capability() -> None:
    profile = {
        "models": {
            "tongyi/qwen-turbo": {
                "structured_output": {
                    "json_schema": "supported",
                    "json_mode": "supported",
                    "strict_schema": "supported",
                }
            }
        }
    }

    decision = resolve_structured_output_policy(
        provider="tongyi",
        model_id="qwen-turbo",
        has_schema=True,
        response_schema_strict=True,
        strict_schema_compatible=True,
        runtime_unsupported=True,
        profile=profile,
    )

    assert decision.mode == StructuredOutputMode.PROMPT_ONLY
    assert decision.reason == "runtime_unsupported_cache"


def test_policy_hard_disables_tongyi_thinking_even_with_profile_support() -> None:
    profile = {
        "models": {
            "tongyi/qwen-turbo": {
                "structured_output": {
                    "json_schema": "supported",
                    "json_mode": "supported",
                    "strict_schema": "supported",
                }
            }
        }
    }

    decision = resolve_structured_output_policy(
        provider="tongyi",
        model_id="qwen-turbo",
        has_schema=True,
        response_schema_strict=False,
        strict_schema_compatible=False,
        thinking=True,
        profile=profile,
    )

    assert decision.mode == StructuredOutputMode.PROMPT_ONLY
    assert decision.reason == "tongyi_thinking_structured_output_disabled"


def test_request_plan_translates_openai_json_schema_strict() -> None:
    request = ModelRequest(
        task_type=TaskType.EVALUATE,
        messages=[{"role": "user", "content": "x"}],
        response_json_schema=_STRICT_SCHEMA,
        response_schema_name="evaluate_response",
        response_schema_strict=True,
    )

    plan = build_structured_output_request_plan(
        request,
        provider="openai",
        model_id="gpt-4o",
        dialect=StructuredOutputDialect.OPENAI_RESPONSE_FORMAT,
    )

    assert plan.decision.mode == StructuredOutputMode.JSON_SCHEMA_STRICT
    assert plan.has_native_controls()
    assert plan.kwargs["response_format"]["type"] == "json_schema"
    assert plan.kwargs["response_format"]["json_schema"]["name"] == "evaluate_response"
    assert plan.kwargs["response_format"]["json_schema"]["strict"] is True


def test_request_plan_translates_anthropic_output_config() -> None:
    request = ModelRequest(
        task_type=TaskType.EVALUATE,
        messages=[{"role": "user", "content": "x"}],
        response_json_schema=_STRICT_SCHEMA,
        response_schema_strict=True,
    )

    plan = build_structured_output_request_plan(
        request,
        provider="anthropic",
        model_id="claude-sonnet-4-5",
        dialect=StructuredOutputDialect.ANTHROPIC_OUTPUT_CONFIG,
    )

    assert plan.decision.mode == StructuredOutputMode.JSON_SCHEMA_STRICT
    assert plan.has_native_controls()
    assert plan.kwargs["output_config"]["format"]["type"] == "json_schema"


def test_request_plan_no_schema_is_observable_prompt_only() -> None:
    request = ModelRequest(
        task_type=TaskType.DRAFT_CHAPTER,
        messages=[{"role": "user", "content": "x"}],
    )

    plan = build_structured_output_request_plan(
        request,
        provider="openai",
        model_id="gpt-4o",
        dialect=StructuredOutputDialect.OPENAI_RESPONSE_FORMAT,
    )

    assert plan.kwargs == {}
    assert plan.response_fields() == {
        "structured_output_mode": "prompt_only",
        "structured_output_downgraded_from": "",
        "structured_output_reason": "request_has_no_json_schema",
    }


def test_request_plan_runtime_fallback_removes_native_controls() -> None:
    request = ModelRequest(
        task_type=TaskType.EVALUATE,
        messages=[{"role": "user", "content": "x"}],
        response_json_schema=_STRICT_SCHEMA,
    )
    plan = build_structured_output_request_plan(
        request,
        provider="openai",
        model_id="gpt-4o",
        dialect=StructuredOutputDialect.OPENAI_RESPONSE_FORMAT,
    )

    fallback = plan.with_runtime_fallback()

    assert fallback.kwargs == {}
    assert fallback.without_native_controls({"response_format": {}, "model": "gpt-4o"}) == {
        "model": "gpt-4o"
    }
    assert fallback.response_fields() == {
        "structured_output_mode": "prompt_only",
        "structured_output_downgraded_from": "json_schema_strict",
        "structured_output_reason": "runtime_unsupported_fallback",
    }
