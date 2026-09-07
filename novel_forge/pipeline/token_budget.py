"""Route-aware token budgeting helpers for pipeline model calls."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from novel_forge.core.constants import PipelineConstants, TaskType
from novel_forge.core.format_contracts import (
    ContractMode,
    OutputKind,
    resolve_task_format_contract,
)
from novel_forge.core.parsing.text_utils import calculate_safe_max_tokens
from novel_forge.core.parsing.token_utils import count_text_tokens
from novel_forge.gateway.profiles import get_model_max_output_tokens

STRUCTURED_JSON_MIN_OUTPUT_TOKENS = 4096
STRUCTURED_JSON_FULL_OBJECT_MIN_OUTPUT_TOKENS = 8192
STRUCTURED_JSON_MAX_OUTPUT_TOKENS = 16384


def _task_token_cap(task_type: TaskType) -> int | None:
    return PipelineConstants.TASK_TOKEN_CAPS.get(task_type.value)


def structured_json_output_floor(
    task_type: TaskType,
    *,
    contract_context: dict[str, Any] | None = None,
    include_contract_required_keys: bool = True,
    contract_mode_override: ContractMode | None = None,
) -> int:
    """Return the shared minimum completion budget for structured JSON tasks.

    This is a floor, not a hard cap.  Small JSON subtasks still get enough room
    to close a complete object, while task-specific caps can raise the floor
    for known bulky reports such as evaluation and humanize scans.
    """
    contract = resolve_task_format_contract(task_type, contract_context)
    if contract is None or contract.output_kind != OutputKind.JSON:
        return 0

    contract_mode = contract_mode_override or contract.effective_contract_mode
    if not include_contract_required_keys:
        contract_mode = ContractMode.FRAGMENT_OBJECT

    if contract_mode == ContractMode.FULL_OBJECT:
        floor = STRUCTURED_JSON_FULL_OBJECT_MIN_OUTPUT_TOKENS
    else:
        floor = STRUCTURED_JSON_MIN_OUTPUT_TOKENS

    task_cap = _task_token_cap(task_type)
    if task_cap is not None:
        floor = max(floor, int(task_cap))
    return max(STRUCTURED_JSON_MIN_OUTPUT_TOKENS, floor)


def route_output_limit(
    router: Any,
    task_type: TaskType,
    *,
    provider: str | None = None,
    model_id: str | None = None,
    fallback: int = 8192,
) -> int:
    """Return the output-token limit for the model currently routed to a task."""
    resolver = getattr(router, "output_limit_for_task", None)
    if callable(resolver):
        try:
            return int(resolver(task_type, provider=provider, model_id=model_id))
        except Exception:
            pass
    if model_id:
        return get_model_max_output_tokens(model_id)
    return fallback


def calculate_route_aware_max_tokens(
    router: Any,
    task_type: TaskType,
    target_output_chars: int,
    *,
    prompt_overhead: int = 2000,
    safety_margin: float = 0.85,
    min_tokens: int = 4096,
    max_cap: int | None = None,
    provider: str | None = None,
    model_id: str | None = None,
) -> int:
    """Calculate ``max_tokens`` using target output size and routed output capacity.

    ``route_output_limit`` returns a model's completion/output ceiling, not
    its total context window.  Do not subtract prompt overhead here; prompt
    overhead belongs to context-window feasibility checks.  Subtracting it
    from an output ceiling silently under-allocates completion space and can
    truncate strict JSON artifacts even when the model could have produced
    the full object.
    """
    json_floor = structured_json_output_floor(task_type)
    if json_floor > 0:
        min_tokens = max(int(min_tokens), json_floor)

    model_limit = route_output_limit(
        router,
        task_type,
        provider=provider,
        model_id=model_id,
    )
    if max_cap is not None:
        # Never let max_cap undercut the structured JSON floor;
        # a too-low cap would truncate guaranteed-complete JSON payloads.
        model_limit = min(model_limit, max(int(max_cap), json_floor))
    model_limit = max(1, int(model_limit))
    return calculate_safe_max_tokens(
        max(1, int(target_output_chars or 1)),
        prompt_overhead=0,
        model_limit=model_limit,
        safety_margin=safety_margin,
        min_tokens=min(max(1, int(min_tokens)), model_limit),
    )


def normalize_task_max_tokens(
    router: Any,
    task_type: TaskType,
    requested_max_tokens: int,
    *,
    provider: str | None = None,
    model_id: str | None = None,
    fallback: int = 8192,
    contract_context: dict[str, Any] | None = None,
    include_contract_required_keys: bool = True,
    contract_mode_override: ContractMode | None = None,
    max_cap: int | None = None,
) -> int:
    """Normalize a caller-supplied ``max_tokens`` through the shared policy.

    This protects direct ``_call_with_retry`` callers that pass legacy literals
    such as 1024/2048 instead of using ``calculate_route_aware_max_tokens``.
    """
    requested = max(1, int(requested_max_tokens or 1))
    model_limit = route_output_limit(
        router,
        task_type,
        provider=provider,
        model_id=model_id,
        fallback=fallback,
    )
    json_floor = structured_json_output_floor(
        task_type,
        contract_context=contract_context,
        include_contract_required_keys=include_contract_required_keys,
        contract_mode_override=contract_mode_override,
    )
    if max_cap is not None:
        # Never let max_cap undercut the structured JSON floor.
        model_limit = min(model_limit, max(int(max_cap), json_floor))
    model_limit = max(1, int(model_limit))
    if json_floor > 0:
        requested = max(requested, json_floor)
    return min(requested, model_limit)


def route_max_output_budget(
    router: Any,
    task_type: TaskType,
    *,
    provider: str | None = None,
    model_id: str | None = None,
    fallback: int = 8192,
    safety_margin: float = 1.0,
    min_tokens: int = 4096,
    max_cap: int | None = None,
) -> int:
    """Return a direct model-capacity completion budget for fragile outputs."""
    model_limit = route_output_limit(
        router,
        task_type,
        provider=provider,
        model_id=model_id,
        fallback=fallback,
    )
    if max_cap is not None:
        model_limit = min(model_limit, int(max_cap))
    model_limit = max(1, int(model_limit))
    budget = max(1, int(model_limit * max(0.0, float(safety_margin))))
    budget = min(model_limit, budget)
    if min_tokens > 0:
        budget = max(min(max(1, int(min_tokens)), model_limit), budget)
    return budget


def route_bounded_json_output_budget(
    router: Any,
    task_type: TaskType,
    *,
    provider: str | None = None,
    model_id: str | None = None,
    fallback: int = 8192,
    min_tokens: int = STRUCTURED_JSON_MIN_OUTPUT_TOKENS,
    max_cap: int = STRUCTURED_JSON_MAX_OUTPUT_TOKENS,
) -> int:
    """Return the shared budget for bounded structured JSON subtasks.

    Use this for split/fragment JSON calls whose schemas are intentionally
    bounded and should not inherit a routed model's very large completion cap.
    """

    return route_max_output_budget(
        router,
        task_type,
        provider=provider,
        model_id=model_id,
        fallback=fallback,
        min_tokens=min_tokens,
        max_cap=max_cap,
    )


@dataclass
class ContextWindowValidation:
    """Result of validating that a prompt fits within a model's context window."""

    fits: bool
    prompt_estimated_tokens: int
    context_window: int
    output_budget: int
    available_for_prompt: int
    overshoot_tokens: int = 0
    model_id: str = ""
    token_count_method: str = "unicode_heuristic"
    tokenizer_name: str = ""
    tokenizer_backed: bool = False
    token_count_exact: bool = False
    overflow_action: str = "none"
    hard_truncation_allowed: bool = False

    @property
    def utilization_ratio(self) -> float:
        """Ratio of prompt tokens to available space (0.0-1.0+, >1.0 = overflow)."""
        if self.available_for_prompt <= 0:
            return float(self.prompt_estimated_tokens)
        return self.prompt_estimated_tokens / self.available_for_prompt


def validate_prompt_fits_context(
    prompt_text: str,
    router: Any,
    task_type: TaskType,
    *,
    provider: str | None = None,
    model_id: str | None = None,
    chars_per_token: float | None = None,
    safety_margin: float = 0.90,
    output_reserve: int | None = None,
) -> ContextWindowValidation:
    """Pre-flight check: validate that total prompt fits within model context window.

    Counts ``prompt_text`` with the routed model tokenizer when available,
    looks up the model's total context window, subtracts the output token
    budget, and checks whether the prompt fits in the remaining space.

    Args:
        prompt_text: The full assembled prompt (system + user + context).
        router: Model router for resolving provider/model.
        task_type: The task type for route resolution.
        provider: Optional provider override.
        model_id: Optional model ID override (bypasses route resolution).
        chars_per_token: Legacy explicit override. When omitted, use the routed
            model tokenizer when available and the shared Unicode fallback
            otherwise.
        safety_margin: Fraction of context window to treat as usable
            (default 0.90 = 90%).
        output_reserve: If set, reserve this many tokens for output; otherwise
            auto-calculate from route.

    Returns:
        ContextWindowValidation with fit status and details.
    """
    from novel_forge.gateway.profiles import get_model_context_window

    resolved_model = model_id or ""
    resolver = getattr(router, "resolve_model_id_for_task", None)
    if not resolved_model and callable(resolver):
        try:
            resolved_model = str(resolver(task_type, provider=provider, model_id=model_id) or "")
        except Exception:
            resolved_model = ""

    if chars_per_token is not None:
        ratio = max(0.1, float(chars_per_token))
        prompt_estimated_tokens = max(1, math.ceil(len(prompt_text) / ratio))
        token_method = "explicit_chars_per_token_override"
        tokenizer_name = ""
        tokenizer_backed = False
        token_count_exact = False
    else:
        token_count = count_text_tokens(
            prompt_text,
            provider=provider or "",
            model_id=resolved_model,
        )
        prompt_estimated_tokens = max(1, token_count.tokens)
        token_method = token_count.method
        tokenizer_name = token_count.tokenizer_name
        tokenizer_backed = token_count.tokenizer_backed
        token_count_exact = token_count.exact

    context_window = get_model_context_window(resolved_model)

    if output_reserve is None:
        output_reserve = route_output_limit(
            router, task_type, provider=provider, model_id=resolved_model
        )

    available_for_prompt = max(0, int(context_window * safety_margin) - output_reserve)

    fits = prompt_estimated_tokens <= available_for_prompt
    overshoot = max(0, prompt_estimated_tokens - available_for_prompt)

    return ContextWindowValidation(
        fits=fits,
        prompt_estimated_tokens=prompt_estimated_tokens,
        context_window=context_window,
        output_budget=output_reserve,
        available_for_prompt=available_for_prompt,
        overshoot_tokens=overshoot,
        model_id=resolved_model,
        token_count_method=token_method,
        tokenizer_name=tokenizer_name,
        tokenizer_backed=tokenizer_backed,
        token_count_exact=token_count_exact,
        overflow_action=("none" if fits else "route_larger_context_or_partition_complete_coverage"),
        hard_truncation_allowed=False,
    )
