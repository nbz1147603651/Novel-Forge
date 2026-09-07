"""LLM service for handling LLM calls with retry logic."""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from functools import partial
from typing import TYPE_CHECKING, Any, Callable

from novel_forge.core.constants import PipelineConstants, TaskType
from novel_forge.core.exceptions import ContextLengthError, ModelGatewayError
from novel_forge.core.format_contracts import (
    ContractMode,
    FormatSchemaIssue,
    TextOutputContractError,
    collect_json_output_contract_issues,
    resolve_task_format_contract,
    should_validate_contract_schema,
    validate_json_output_contract,
    validate_text_output_contract,
)
from novel_forge.core.parsing.format_repair import (
    build_format_error_event,
    build_format_retry_directive,
    task_expects_json,
)
from novel_forge.core.parsing.text_utils import extract_text_content
from novel_forge.core.parsing.token_utils import count_text_tokens
from novel_forge.core.response_repair.json_blocks import repair_missing_colon_delimiters
from novel_forge.core.response_repair.orchestrator import (
    FormatRepairContext,
    FormatRepairResult,
    LLMRepairCallable,
    RepairCandidate,
    RepairRisk,
    RepairSource,
)
from novel_forge.core.utils.json import strip_markdown_fences
from novel_forge.core.utils.text_validation import count_chapter_words
from novel_forge.gateway.profiles import get_model_context_window, get_model_max_output_tokens
from novel_forge.gateway.types import ModelRequest, ModelResponse, StreamChunk, to_stream_chunk
from novel_forge.pipeline.long.services.generation import llm_helpers as llm_h
from novel_forge.pipeline.long.services.generation.response_observation import ResponseObservation
from novel_forge.pipeline.long.services.init.init_claim_semantic_repair import (
    INIT_CLAIM_SEMANTIC_PATCH_SCHEMA,
    apply_init_claim_semantic_patch,
    build_init_claim_semantic_repair_prompt,
    collect_init_claim_repair_targets,
)
from novel_forge.pipeline.long.services.task_output_adapters import (
    TaskOutputAdapterResult,
    apply_task_output_adapter,
)
from novel_forge.pipeline.long.services.task_semantic_contracts import (
    TaskSemanticContractError,
    validate_task_semantic_contract,
)
from novel_forge.pipeline.repair_orchestration.domains.format_response import (
    run_format_response_repair_v2,
)
from novel_forge.pipeline.steps.prompt_diagnostics import build_prompt_pressure_diagnostics
from novel_forge.pipeline.token_budget import normalize_task_max_tokens

if TYPE_CHECKING:
    from novel_forge.gateway.router import ModelRouter
    from novel_forge.prompts.builder import PromptBuilder

_logger = logging.getLogger(__name__)
_STREAM_DELTA_FLUSH_INTERVAL_S = 0.02
_STREAM_DELTA_FORCE_FLUSH_CHARS = 256
# JSON observation streams (e.g. TTS dubbing-script generation) are UI
# telemetry only — the pipeline consumes the fully aggregated response, so
# per-character granularity is pointless.  A 20 ms / 256-char cadence floods
# the Qt main thread with ~50 signal events/s and causes visible stutter on
# the voice-studio page.  Segments complete every few seconds and the progress
# bar is throttled to 400 ms, so a 300 ms / 1024-char cadence (~2-3 events/s)
# is more than enough.  TEXT_ONLY streams (chapter draft, per-character typing
# effect) keep the fast constants above.
_JSON_OBSERVATION_FLUSH_INTERVAL_S = 0.30
_JSON_OBSERVATION_FORCE_FLUSH_CHARS = 1024


class StreamOutputInflationError(Exception):
    """Raised when streaming output exceeds expected bounds (model repetition loop).

    The router's stream consumer propagates this exception instead of
    swallowing it, enabling early abort of runaway generation.
    """


_INIT_CLAIM_TASKS = frozenset(
    {
        TaskType.EXTRACT_INIT_COHERENCE_CLAIMS,
        TaskType.EXTRACT_BLUEPRINT_HOLISTIC_CLAIMS,
    }
)


def _has_recoverable_json_source(raw_content: str) -> bool:
    """Return whether format repair has actual structured content to recover.

    A blank/whitespace response contains no semantics. Sending it to a format
    repair model invites fabricated enum values and empty commit objects, which
    is especially unsafe for archive/state-write decisions.
    """
    stripped = strip_markdown_fences(str(raw_content or "")).strip()
    return bool(stripped) and ("{" in stripped or "[" in stripped)


def _strict_json_object_or_none(raw_content: str) -> dict[str, Any] | None:
    """Parse a complete JSON object without invoking lossy repair strategies."""

    try:
        parsed = json.loads(strip_markdown_fences(str(raw_content or "")), strict=False)
    except (json.JSONDecodeError, TypeError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _gateway_failure_categories(exc: BaseException) -> tuple[str, ...]:
    """Read router failure categories from their durable exception boundary."""

    raw_categories = getattr(exc, "failure_categories", None)
    if raw_categories is None:
        context = getattr(exc, "context", None)
        if isinstance(context, dict):
            raw_categories = context.get("failure_categories")
    if not isinstance(raw_categories, (list, tuple)):
        return ()
    return tuple(category for item in raw_categories if (category := str(item or "").strip()))


def _retry_needs_more_output_budget(
    error: BaseException,
    *,
    finish_reason: str | None,
) -> bool:
    """Only increase generation tokens when there is evidence of truncation.

    Enum, schema, duplicate-key, and semantic-contract failures do not become
    more correct when the same extraction is given a larger output allowance.
    """

    if finish_reason == "length":
        return True
    if finish_reason:
        return False
    if not isinstance(error, json.JSONDecodeError):
        return False

    source = error.doc.rstrip()
    if not source or error.pos < max(0, len(source) - 2):
        return False
    unclosed_container = source.count("{") > source.count("}") or source.count("[") > source.count(
        "]"
    )
    return unclosed_container or error.msg.startswith("Unterminated")


def _chapter_numbers_from_contract_items(items: Any) -> set[int]:
    numbers: set[int] = set()
    if not isinstance(items, list):
        return numbers
    for item in items:
        if not isinstance(item, dict):
            continue
        raw_chapter_number = item.get("chapter_number")
        if raw_chapter_number is None:
            continue
        try:
            numbers.add(int(raw_chapter_number))
        except (TypeError, ValueError):
            continue
    return numbers


def _expected_chapter_contract_numbers(current_context: dict[str, Any]) -> set[int]:
    outline = current_context.get("outline")
    if not isinstance(outline, dict):
        return set()
    batch = outline.get("contract_batch")
    if isinstance(batch, dict):
        raw_numbers = batch.get("chapter_numbers")
        if isinstance(raw_numbers, list):
            numbers = _chapter_numbers_from_contract_items(
                [{"chapter_number": item} for item in raw_numbers]
            )
            if numbers:
                return numbers
    return _chapter_numbers_from_contract_items(outline.get("chapters"))


def _accept_partial_chapter_contract_repair(
    candidate: RepairCandidate,
    *,
    current_context: dict[str, Any],
    finish_reason: str | None,
) -> bool:
    """Allow partial local contract repair only when init can backfill the missing batch rows."""
    items = candidate.data.get("chapter_contracts")
    if not isinstance(items, list) or not any(isinstance(item, dict) for item in items):
        return False
    parsed_numbers = _chapter_numbers_from_contract_items(items)
    expected_numbers = _expected_chapter_contract_numbers(current_context)
    if finish_reason == "length" and (
        not expected_numbers or not expected_numbers.issubset(parsed_numbers)
    ):
        return False
    outline = current_context.get("outline")
    if not isinstance(outline, dict):
        return False
    scaffold = outline.get("contract_scaffold")
    chapters = outline.get("chapters")
    can_backfill = (
        isinstance(scaffold, list)
        and bool(scaffold)
        and isinstance(chapters, list)
        and bool(chapters)
    )
    if not can_backfill:
        return False
    coverage = candidate.data.get("coverage")
    if not isinstance(coverage, dict):
        coverage = {}
        candidate.data["coverage"] = coverage
    coverage["local_fallback_accepted"] = True
    coverage["partial_format_repair_accepted"] = True
    return True


class _RepairedResponseValidator:
    """Callable validator object for repaired JSON payloads.

    Keeping this as a concrete object avoids Pylance self-reference inference
    issues with loop-local validator functions.
    """

    def __init__(
        self,
        *,
        service: Any,
        task_type: TaskType,
        response_content: str,
        response_finish_reason: str | None,
        current_context: dict[str, Any],
        adapter_results: list[TaskOutputAdapterResult],
        effective_required_keys: tuple[str, ...],
        validate_full_contract_schema: bool,
    ) -> None:
        self._service = service
        self._task_type = task_type
        self._response_content = response_content
        self._response_finish_reason = response_finish_reason
        self._current_context = current_context
        self._adapter_results = adapter_results
        self._effective_required_keys = effective_required_keys
        self._validate_full_contract_schema = validate_full_contract_schema
        self.last_schema_issues: tuple[FormatSchemaIssue, ...] = ()

    def __call__(self, candidate_data: dict[str, Any]) -> None:
        self.last_schema_issues = ()
        try:
            self._service._validate_repaired_response_data(
                candidate_data,
                task_type=self._task_type,
                response_content=self._response_content,
                response_finish_reason=self._response_finish_reason,
                current_context=self._current_context,
                adapter_results=self._adapter_results,
                effective_required_keys=self._effective_required_keys,
                validate_full_contract_schema=self._validate_full_contract_schema,
            )
        except (KeyError, TypeError, ValueError) as exc:
            self.last_schema_issues = self._service._collect_format_schema_issues(
                task_type=self._task_type,
                candidate_data=candidate_data,
                current_context=self._current_context,
                effective_required_keys=self._effective_required_keys,
                validate_full_contract_schema=self._validate_full_contract_schema,
                error=exc,
            )
            raise


class _FormatRepairLLMCallback:
    """Callable wrapper for the optional dedicated LLM format repair call."""

    def __init__(
        self,
        *,
        service: LLMService,
        base_request: Any,
        task_type: TaskType,
        attempt: int,
        max_attempts: int,
        required_keys: tuple[str, ...],
        raw_content: str,
        response: Any,
        current_max_tokens: int,
        contract_context_items: tuple[tuple[str, Any], ...],
        contract_mode_override: ContractMode | None,
        observation: ResponseObservation,
    ) -> None:
        self._service = service
        self._base_request = base_request
        self._task_type = task_type
        self._attempt = attempt
        self._max_attempts = max_attempts
        self._required_keys = required_keys
        self._raw_content = raw_content
        self._response = response
        self._current_max_tokens = current_max_tokens
        self._contract_context_items = contract_context_items
        self._contract_mode_override = contract_mode_override
        self._observation = observation

    async def __call__(self, prompt: str) -> str:
        self._observation.emit("repairing", repair_source="llm", response=self._response)
        return await self._service._call_format_repair_llm(
            base_request=self._base_request,
            prompt=prompt,
            task_type=self._task_type,
            attempt=self._attempt,
            max_attempts=self._max_attempts,
            required_keys=self._required_keys,
            raw_content=self._raw_content,
            response=self._response,
            current_max_tokens=self._current_max_tokens,
            contract_context=dict(self._contract_context_items),
            contract_mode_override=self._contract_mode_override,
        )


class LLMService:
    """Service for managing LLM calls with automatic retry logic.

    Retry strategy (applied progressively on failure):
    L1 — Token escalation for truncation/parse damage, up to the model output limit.
    L2 — Thinking budget awareness: when thinking=True, the model's reasoning
         tokens consume part of max_tokens, reducing content capacity.
    L3 — Truncation fallback: if finish_reason=length and thinking is active,
         disable thinking on retry to reclaim the token budget for content.
    """

    @staticmethod
    def estimate_tokens(text: str, *, provider: str = "", model_id: str = "") -> int:
        """Count with the routed tokenizer, falling back to one shared heuristic."""

        return count_text_tokens(text, provider=provider, model_id=model_id).tokens

    @staticmethod
    def _get_context_window(provider_id: str, model_id: str) -> int | None:
        """Return the estimated context window size for a model, or None if unknown.

        Uses the shared gateway model profile table so pre-flight checks stay
        aligned with routing and max-token helpers.
        """
        if not model_id:
            return None
        return get_model_context_window(model_id)

    def _resolve_preflight_model(
        self,
        task_type: TaskType,
        request: Any,
        *,
        provider: str | None,
        model_id: str | None,
    ) -> tuple[str | None, str | None, int | None]:
        resolved_provider = provider
        resolved_model = model_id if model_id else getattr(request, "model_id", None)
        if not resolved_model:
            resolved_model = self._router.resolve_model_id_for_task(
                task_type,
                provider=provider,
                model_id=model_id,
            )
        window = self._get_context_window(resolved_provider or "", resolved_model or "")
        return resolved_provider, resolved_model, window

    @staticmethod
    def _trim_optional_prompt_context(
        context: dict[str, Any],
    ) -> tuple[dict[str, Any], tuple[str, ...]]:
        """Trim only optional examples/history projections; preserve authority boundaries."""

        compacted = dict(context)
        changed: list[str] = []
        list_limits = {
            "style_golden_examples": 0,
            "previous_chapters": 2,
            "chapter_summaries": 4,
            "related_chapters_context": 4,
            "prior_character_snapshots": 4,
            "prior_relationships": 6,
            "prior_plot_threads": 6,
        }
        for key, limit in list_limits.items():
            value = compacted.get(key)
            if not isinstance(value, list) or len(value) <= limit:
                continue
            compacted[key] = value[-limit:] if limit else []
            changed.append(key)
        # Never trim user_intent, retrieval/research evidence, immutable facts,
        # forbidden reveal boundaries, or contract/source cards here.
        return compacted, tuple(changed)

    def _fit_optional_context_at_preflight(
        self,
        *,
        task_type: TaskType,
        request: Any,
        context: dict[str, Any],
        provider: str | None,
        model_id: str | None,
        rebuild: Callable[[dict[str, Any]], Any],
    ) -> tuple[dict[str, Any], Any]:
        """Rebuild once after optional trimming when routed context use reaches 80%."""

        resolved_provider, resolved_model, window = self._resolve_preflight_model(
            task_type,
            request,
            provider=provider,
            model_id=model_id,
        )
        if not window:
            return context, request
        diagnostics = build_prompt_pressure_diagnostics(
            request,
            context,
            context_window=window,
            provider=resolved_provider or "",
            model_id=resolved_model or "",
        )
        if diagnostics.utilization_ratio is None or diagnostics.utilization_ratio < 0.80:
            return context, request
        compacted, trimmed = self._trim_optional_prompt_context(context)
        if not trimmed:
            return context, request
        rebuilt = rebuild(compacted)
        self._on_step(
            "prompt_context_optional_trimmed",
            {
                "task": task_type.value,
                "trimmed_fields": list(trimmed),
                "utilization_ratio_before": diagnostics.utilization_ratio,
                "protected_fields": [
                    "user_intent",
                    "research_evidence_pack",
                    "retrieval_evidence_pack",
                    "forbidden_reveal_boundaries",
                ],
                "next_action_if_still_oversized": (
                    "shrink_complete_batch_then_route_larger_context"
                ),
            },
        )
        return compacted, rebuilt

    def _emit_prompt_pressure_preflight(
        self,
        *,
        task_type: TaskType,
        request: Any,
        context: dict[str, Any],
        provider: str | None,
        model_id: str | None,
        attempt: int,
        chapter: Any,
        event_name: str = "prompt_pressure",
    ) -> None:
        resolved_provider, resolved_model, context_window = self._resolve_preflight_model(
            task_type,
            request,
            provider=provider,
            model_id=model_id,
        )
        diagnostics = build_prompt_pressure_diagnostics(
            request,
            context,
            context_window=context_window,
            provider=resolved_provider or "",
            model_id=resolved_model or "",
            info_ratio=float(
                getattr(self._settings, "long_prompt_pressure_info_ratio", 0.15) or 0.15
            ),
            warn_ratio=float(
                getattr(self._settings, "long_prompt_pressure_warn_ratio", 0.30) or 0.30
            ),
        )
        payload = diagnostics.to_event_payload()
        payload.update(
            {
                "input_token_estimate": diagnostics.estimated_prompt_tokens,
                "provider": resolved_provider or "",
                "model": resolved_model or "",
                "task": task_type.value,
                "attempt": attempt,
                "chapter": chapter,
                "max_tokens": getattr(request, "max_tokens", 0),
                "preflight_oversized": diagnostics.fits_context is False,
                "required_context_preserved": True,
                "hard_truncation_allowed": False,
            }
        )
        if payload["preflight_oversized"]:
            _logger.warning(
                "pre-flight overflow: prompt=%d available=%s context_window=%d "
                "for %s:%s | action=%s | required_context_preserved=true",
                diagnostics.estimated_prompt_tokens,
                diagnostics.available_for_prompt,
                context_window,
                resolved_provider,
                resolved_model,
                diagnostics.overflow_action,
            )
        self._on_step(event_name, payload)
        if payload["preflight_oversized"] and bool(
            getattr(self._settings, "long_prompt_preflight_block_oversized", True)
        ):
            raise ContextLengthError(
                "pre-flight context overflow before provider call: "
                f"task={task_type.value}, prompt_tokens={diagnostics.estimated_prompt_tokens}, "
                f"available_for_prompt={diagnostics.available_for_prompt}, "
                f"model={resolved_provider}:{resolved_model}; "
                "required_action=route_larger_context_or_partition_complete_coverage"
            )

    def __init__(
        self,
        router: ModelRouter,
        builder: PromptBuilder,
        on_step: Callable[[str, Any], None],
        settings: Any | None = None,
        task_circuit_breaker: Any | None = None,
    ):
        self._router = router
        self._builder = builder
        self._on_step = on_step
        self._settings = settings
        self._task_circuit_breaker = task_circuit_breaker or getattr(
            router, "task_circuit_breaker", None
        )

    @staticmethod
    def _parse_provider_model_override(value: str) -> tuple[str | None, str | None]:
        """Parse a provider:model override string."""
        raw = str(value or "").strip()
        if not raw:
            return None, None
        if ":" not in raw:
            return None, raw
        provider, model_id = raw.split(":", 1)
        return provider.strip() or None, model_id.strip() or None

    def _format_repair_enabled(self) -> bool:
        return bool(getattr(self._settings, "llm_format_repair_enabled", True))

    def _format_repair_max_tokens(self, minimum_tokens: int | None = None) -> int:
        value = int(getattr(self._settings, "llm_format_repair_max_tokens", 4096) or 4096)
        if minimum_tokens is not None:
            value = max(value, int(minimum_tokens or 0))
        return max(512, value)

    def _format_repair_raw_char_limit(self) -> int:
        value = int(getattr(self._settings, "llm_format_repair_raw_char_limit", 60000) or 60000)
        return max(1000, value)

    def _format_retry_raw_char_limit(self) -> int:
        value = int(getattr(self._settings, "llm_format_retry_raw_char_limit", 6000) or 6000)
        return max(1000, value)

    def _format_repair_attempts(self) -> int:
        configured = int(getattr(self._settings, "llm_format_retry_attempts", 2) or 2)
        return max(2, min(5, configured))

    def _json_observation_stream_enabled(self) -> bool:
        return bool(getattr(self._settings, "long_streaming_json_observation_enabled", True))

    # Tasks with very large prompts that are prone to stream timeouts.
    # These benefit more from non-streaming route() which has lower overhead.
    # The built-in set is always excluded; settings can append more tasks via
    # ``long_streaming_json_excluded_tasks`` (comma-separated) but cannot
    # remove built-in entries.
    _STREAM_OBSERVATION_EXCLUDED_TASKS: frozenset[str] = frozenset(
        {
            "extract_candidate_state_deltas",
            "adjudicate_state_delta",
            "adjudicate_final_state",
            "extract_canon",
            "extract_chapter_summary_exit",
        }
    )

    def _stream_observation_excluded_tasks(self) -> frozenset[str]:
        """Return the effective stream-observation exclusion set.

        Starts from the built-in set (always excluded) and appends any
        user-configured task names from ``long_streaming_json_excluded_tasks``.
        """
        extra_raw = str(getattr(self._settings, "long_streaming_json_excluded_tasks", "") or "")
        extra = {token.strip() for token in extra_raw.split(",") if token.strip()}
        if not extra:
            return self._STREAM_OBSERVATION_EXCLUDED_TASKS
        return self._STREAM_OBSERVATION_EXCLUDED_TASKS | frozenset(extra)

    async def _route_with_observed_stream(
        self,
        request: ModelRequest,
        *,
        task_type: TaskType,
        provider: str | None,
        chapter: Any,
        attempt: int,
        max_attempts: int,
        stream_kind: str,
        output_kind: str,
        stream_id: str | None = None,
        operation_id: str = "",
    ) -> ModelResponse:
        """Route through provider streaming for UI observation, returning final response.

        The pipeline still consumes only the fully aggregated response returned by
        the router.  For JSON tasks this is intentionally observation-only:
        intermediate chunks are not parsed, validated, repaired, or persisted.
        """
        stream_id = stream_id or uuid.uuid4().hex
        self._on_step(
            "llm_stream_start",
            {
                "stream_id": stream_id,
                "operation_id": operation_id,
                "task": task_type.value,
                "chapter": chapter,
                "attempt": attempt,
                "max_attempts": max_attempts,
                "stream_kind": stream_kind,
                "output_kind": output_kind,
            },
        )

        chunks: list[str] = []
        reasoning_chunks: list[str] = []
        pending_segments: list[tuple[str, str]] = []
        stream_state = {
            "text_length": 0,
            "reasoning_length": 0,
            "last_flush_at": 0.0,
            "pending_chars": 0,
        }
        # Output inflation circuit breaker: if accumulated content exceeds
        # max_tokens * 4 chars (generous JSON expansion), the model is likely
        # in a repetition loop.  Abort early instead of waiting for max_tokens.
        _max_tokens = int(getattr(request, "max_tokens", 0) or 0)
        _inflation_char_limit = _max_tokens * 4 if _max_tokens > 0 else 0

        def _flush_segments(*, force: bool = False) -> None:
            if not pending_segments:
                return
            now = time.monotonic()
            last_flush_at = float(stream_state["last_flush_at"])
            if not force and now - last_flush_at < _JSON_OBSERVATION_FLUSH_INTERVAL_S:
                return
            segments_snapshot = list(pending_segments)
            pending_segments.clear()
            stream_state["last_flush_at"] = now
            stream_state["pending_chars"] = 0
            self._on_step(
                "llm_stream_delta",
                {
                    "stream_id": stream_id,
                    "task": task_type.value,
                    "chapter": chapter,
                    "attempt": attempt,
                    "stream_kind": stream_kind,
                    "output_kind": output_kind,
                    "segments": [{"kind": kind, "text": text} for kind, text in segments_snapshot],
                    "operation_id": operation_id,
                    "text_length": int(stream_state["text_length"]),
                    "reasoning_length": int(stream_state["reasoning_length"]),
                },
            )

        def _on_chunk(chunk_or_text: StreamChunk | str) -> None:
            chunk = to_stream_chunk(chunk_or_text)
            if chunk.reset:
                _flush_segments(force=True)
                chunks.clear()
                reasoning_chunks.clear()
                pending_segments.clear()
                stream_state.update(
                    text_length=0,
                    reasoning_length=0,
                    pending_chars=0,
                )
                self._on_step(
                    "llm_stream_restart",
                    {
                        "stream_id": stream_id,
                        "operation_id": operation_id,
                        "task": task_type.value,
                        "chapter": chapter,
                        "attempt": attempt,
                        "stream_kind": stream_kind,
                        "output_kind": output_kind,
                        "message": chunk.reset_reason or "流式连接已重启。",
                        "reset_output": True,
                    },
                )
                return
            if chunk.content:
                chunks.append(chunk.content)
                stream_state["text_length"] += len(chunk.content)
                stream_state["pending_chars"] += len(chunk.content)
                if pending_segments and pending_segments[-1][0] == "content":
                    prev_kind, prev_text = pending_segments[-1]
                    pending_segments[-1] = (prev_kind, prev_text + chunk.content)
                else:
                    pending_segments.append(("content", chunk.content))
            if chunk.reasoning:
                reasoning_chunks.append(chunk.reasoning)
                stream_state["reasoning_length"] += len(chunk.reasoning)
                stream_state["pending_chars"] += len(chunk.reasoning)
                if pending_segments and pending_segments[-1][0] == "reasoning":
                    prev_kind, prev_text = pending_segments[-1]
                    pending_segments[-1] = (prev_kind, prev_text + chunk.reasoning)
                else:
                    pending_segments.append(("reasoning", chunk.reasoning))
            # Inflation circuit breaker: abort runaway generation early.
            if (
                _inflation_char_limit > 0
                and int(stream_state["text_length"]) > _inflation_char_limit
            ):
                raise StreamOutputInflationError(
                    f"Stream output exceeded {_inflation_char_limit} chars "
                    f"(max_tokens={_max_tokens}, task={task_type.value}). "
                    f"Likely model repetition loop — aborting."
                )
            _flush_segments(
                force=int(stream_state["pending_chars"]) >= _JSON_OBSERVATION_FORCE_FLUSH_CHARS
            )

        try:
            if provider:
                response = await self._router.stream_route(
                    request,
                    provider=provider,
                    on_chunk=_on_chunk,
                )
            else:
                response = await self._router.stream_route(request, on_chunk=_on_chunk)
            _flush_segments(force=True)
            response_content = str(getattr(response, "content", "") or "") or "".join(chunks)
            reasoning_text = str(getattr(response, "thinking_content", "") or "") or "".join(
                reasoning_chunks
            )
            if response_content != response.content or reasoning_text != response.thinking_content:
                response = response.model_copy(
                    update={
                        "content": response_content,
                        "thinking_content": reasoning_text,
                    }
                )
            self._on_step(
                "llm_stream_end",
                {
                    "stream_id": stream_id,
                    "operation_id": operation_id,
                    **({"validation_status": "validating"} if operation_id else {}),
                    "task": task_type.value,
                    "chapter": chapter,
                    "attempt": attempt,
                    "stream_kind": stream_kind,
                    "output_kind": output_kind,
                    "chars": len(response_content),
                    "text": response_content,
                    "text_length": len(response_content),
                    "reasoning": reasoning_text,
                    "reasoning_length": len(reasoning_text),
                    "finish_reason": str(getattr(response, "finish_reason", "") or ""),
                    "model_id": str(getattr(response, "model_id", "") or ""),
                },
            )
            return response
        except Exception as exc:
            _flush_segments(force=True)
            partial_text = "".join(chunks)
            partial_reasoning = "".join(reasoning_chunks)
            if (
                isinstance(exc, ModelGatewayError)
                and partial_text
                and not str(getattr(exc, "partial_text", "") or "")
            ):
                exc.attach_partial_stream(
                    text=partial_text,
                    reasoning=partial_reasoning,
                )
            self._on_step(
                "llm_stream_error",
                {
                    "stream_id": stream_id,
                    "task": task_type.value,
                    "chapter": chapter,
                    "attempt": attempt,
                    "stream_kind": stream_kind,
                    "output_kind": output_kind,
                    "error": str(exc),
                    "operation_id": operation_id,
                    "text": partial_text,
                    "text_length": len(partial_text),
                    "reasoning": partial_reasoning,
                    "reasoning_length": len(partial_reasoning),
                },
            )
            raise

    def _repair_event_error(
        self,
        repair_error: BaseException | None,
        diagnostics: dict[str, Any],
    ) -> BaseException:
        """Return a readable error object for repair telemetry."""
        if repair_error is not None:
            return repair_error
        for key in (
            "task_specific_local_fallback_error",
            "strict_error",
            "strict_validation_error",
            "local_safe_parse_json_error",
            "local_safe_parse_json_validation_error",
        ):
            value = diagnostics.get(key)
            if value:
                return ValueError(str(value))
        return ValueError("Model response required format repair")

    def _collect_format_schema_issues(
        self,
        *,
        task_type: TaskType,
        candidate_data: dict[str, Any],
        current_context: dict[str, Any],
        effective_required_keys: tuple[str, ...],
        validate_full_contract_schema: bool,
        error: BaseException,
    ) -> tuple[FormatSchemaIssue, ...]:
        """Collect contract issues for schema-aware retry telemetry."""

        if isinstance(error, TaskSemanticContractError):
            return error.issues

        contract = resolve_task_format_contract(task_type, current_context)
        validate_contract_schema = should_validate_contract_schema(
            contract,
            include_contract_required_keys=validate_full_contract_schema,
        )
        try:
            issues = collect_json_output_contract_issues(
                task_type,
                candidate_data,
                context=current_context,
                explicit_required_keys=effective_required_keys,
                include_contract_required_keys=False,
                validate_allowed_keys=validate_contract_schema,
                validate_json_schema=validate_contract_schema,
            )
        except Exception:
            return ()
        if issues:
            return tuple(issues)
        if isinstance(error, KeyError):
            text = str(error).strip().strip("'\"")
            if text:
                return (
                    FormatSchemaIssue(
                        path="$",
                        issue_type="contract_validation",
                        expected="valid task response",
                        actual="key_error",
                        message=text,
                    ),
                )
        return ()

    def _validate_repaired_response_data(
        self,
        candidate_data: dict[str, Any],
        *,
        task_type: TaskType,
        response_content: str,
        response_finish_reason: str | None,
        current_context: dict[str, Any],
        adapter_results: list[TaskOutputAdapterResult],
        effective_required_keys: tuple[str, ...],
        validate_full_contract_schema: bool,
    ) -> None:
        """Validate and normalize one parsed/repaired task response payload."""
        if task_type == TaskType.EXTRACT_CANON:
            damage_error = self._extract_canon_damage_error(
                response_content,
                candidate_data,
                finish_reason=response_finish_reason,
            )
            if damage_error is not None:
                raise damage_error
        adapter_result = apply_task_output_adapter(
            candidate_data,
            task_type,
            context=current_context,
        )
        if adapter_result.changed:
            adapter_results.append(adapter_result)
        llm_h.apply_task_response_defaults(candidate_data, task_type)
        llm_h.ensure_required_response_keys(candidate_data, effective_required_keys)
        contract = resolve_task_format_contract(task_type, current_context)
        validate_contract_schema = should_validate_contract_schema(
            contract,
            include_contract_required_keys=validate_full_contract_schema,
        )
        if validate_full_contract_schema:
            llm_h.validate_response_schema(candidate_data, task_type)
        validate_json_output_contract(
            task_type,
            candidate_data,
            context=current_context,
            explicit_required_keys=effective_required_keys,
            include_contract_required_keys=False,
            validate_allowed_keys=validate_contract_schema,
            validate_json_schema=validate_contract_schema,
        )
        validate_task_semantic_contract(candidate_data, task_type)

    def _extract_canon_damage_error(
        self,
        response_content: str,
        candidate_data: dict[str, Any],
        *,
        finish_reason: str | None,
    ) -> ValueError | None:
        if not bool(getattr(self._settings, "extract_canon_abort_on_severe_damage", True)):
            return None
        damage = llm_h.assess_extract_canon_response_damage(
            response_content,
            candidate_data,
            finish_reason=finish_reason,
            missing_section_threshold=int(
                getattr(
                    self._settings,
                    "extract_canon_severe_damage_missing_section_threshold",
                    2,
                )
                or 2
            ),
        )
        if not damage.is_severe:
            return None
        return ValueError(
            "Severely damaged EXTRACT_CANON payload: "
            f"reasons={list(damage.reasons)}, "
            f"missing_optional_sections={list(damage.missing_optional_sections)}, "
            f"raw_but_missing_sections={list(damage.raw_but_missing_sections)}"
        )

    async def _call_format_repair_llm(
        self,
        *,
        base_request: Any,
        prompt: str,
        task_type: TaskType,
        attempt: int,
        max_attempts: int,
        required_keys: tuple[str, ...],
        raw_content: str,
        response: Any,
        current_max_tokens: int,
        contract_context: dict[str, Any] | None = None,
        contract_mode_override: ContractMode | None = None,
    ) -> str:
        """Route a dedicated LLM request that repairs malformed JSON only."""
        contract = resolve_task_format_contract(task_type, contract_context)
        effective_contract_mode = contract_mode_override or (
            contract.effective_contract_mode if contract else None
        )
        contract_mode = effective_contract_mode.value if effective_contract_mode else ""
        directive = build_format_retry_directive(
            task_type=task_type,
            attempt=attempt,
            max_attempts=max_attempts,
            error=ValueError("Local format repair strategy miss; requesting repair LLM"),
            required_keys=required_keys,
            raw_content=raw_content,
            raw_excerpt_limit=self._format_retry_raw_char_limit(),
            context=contract_context,
            contract_mode_override=contract_mode_override,
        )
        payload = build_format_error_event(
            directive,
            raw_content=raw_content,
            finish_reason=getattr(response, "finish_reason", None),
            model_id=getattr(response, "model_id", None),
            max_tokens=current_max_tokens,
        )
        payload["repair_action"] = "llm_format_repair_requested"
        self._on_step("format_repair_strategy_miss", payload)

        model_override = str(
            getattr(self._settings, "llm_format_repair_model", "")
            or getattr(self._settings, "repair_model", "")
            or ""
        ).strip()
        provider_hint, model_hint = self._parse_provider_model_override(model_override)
        repair_system_prompt = (
            "你是 Novel Forge 的 JSON 格式修复模块。只修复格式，不生成新剧情，"
            "最终只返回一个可被 json.loads 解析的 JSON 对象。"
        )
        retry_temperature = float(
            getattr(self._settings, "llm_format_retry_temperature", 0.0) or 0.0
        )
        repair_attempts = self._format_repair_attempts()
        repair_prompt = prompt
        repair_max_tokens = self._format_repair_max_tokens(current_max_tokens)
        last_exc: Exception | None = None
        last_content = ""

        for repair_attempt in range(1, repair_attempts + 1):
            repair_messages = [
                {"role": "system", "content": repair_system_prompt},
                {"role": "user", "content": repair_prompt},
            ]
            repair_request = base_request.model_copy(
                update={
                    "messages": repair_messages,
                    "max_tokens": repair_max_tokens,
                    "temperature": retry_temperature,
                    "temperature_jitter_allowed": False,
                    "model_id": model_hint,
                    "thinking": False,
                    "multi_turn": False,
                }
            )
            if provider_hint:
                repair_response = await self._router.route(repair_request, provider=provider_hint)
            else:
                repair_response = await self._router.route(repair_request)

            last_content = str(getattr(repair_response, "content", "") or "")
            finish_reason = getattr(repair_response, "finish_reason", None)
            response_model_id = getattr(repair_response, "model_id", None) or model_hint
            try:
                if finish_reason == "length":
                    raise ValueError("Format repair response hit token limit")
                parsed = json.loads(strip_markdown_fences(last_content), strict=False)
                if not isinstance(parsed, dict):
                    raise TypeError("Format repair response JSON must be an object")
                return last_content
            except (json.JSONDecodeError, TypeError, ValueError) as exc:
                last_exc = exc
                if repair_attempt >= repair_attempts:
                    break

                model_limit = (
                    get_model_max_output_tokens(str(response_model_id))
                    if response_model_id
                    else None
                )
                new_max = llm_h.escalate_retry_tokens(
                    repair_max_tokens,
                    task_type.value,
                    model_max_tokens=model_limit,
                )
                if new_max <= repair_max_tokens:
                    break

                backoff = llm_h.compute_retry_backoff(repair_attempt)
                self._on_step(
                    "token_escalation",
                    {
                        "task": task_type.value,
                        "attempt": repair_attempt,
                        "max_attempts": repair_attempts,
                        "max_retries": max(0, repair_attempts - 1),
                        "old_max_tokens": repair_max_tokens,
                        "new_max_tokens": new_max,
                        "thinking_disabled": False,
                        "model_limit": model_limit,
                        "backoff_seconds": backoff,
                        "error": str(exc),
                        "repair_action": "llm_format_repair",
                        "repair_attempt": repair_attempt,
                        "contract_mode": contract_mode,
                    },
                )
                repair_max_tokens = new_max
                repair_prompt = (
                    f"{prompt}\n\n"
                    "## 专用格式修复重试\n"
                    f"上一次格式修复输出仍未通过 JSON 自检：{type(exc).__name__}: {exc}\n"
                    f"finish_reason：{finish_reason or '未知'}\n"
                    "请不要续写上一段，必须重新输出一个完整 JSON 对象。\n"
                    "上一次修复输出片段：\n"
                    "```text\n"
                    f"{last_content[:4000]}\n"
                    "```"
                )
                await asyncio.sleep(backoff)

        if last_exc is not None:
            # Final rule-based fallback: try repairing missing colon delimiters
            # on the last LLM output before giving up.
            if last_content and isinstance(last_exc, json.JSONDecodeError):
                stripped = strip_markdown_fences(last_content)
                repaired = repair_missing_colon_delimiters(stripped)
                if repaired != stripped:
                    try:
                        parsed = json.loads(repaired, strict=False)
                        if isinstance(parsed, dict):
                            self._on_step(
                                "format_repair_local_fallback",
                                {
                                    "task": task_type.value,
                                    "repair_action": "missing_colon_delimiter_fallback",
                                    "original_error": str(last_exc),
                                },
                            )
                            return repaired
                    except (json.JSONDecodeError, TypeError, ValueError):
                        pass
                # Second fallback: extract outermost {...} JSON object from
                # the response (handles cases where the model wraps JSON in
                # extra prose or markdown artifacts).
                brace_start = stripped.find("{")
                brace_end = stripped.rfind("}")
                if brace_start >= 0 and brace_end > brace_start:
                    candidate = stripped[brace_start : brace_end + 1]
                    if candidate != stripped:
                        try:
                            parsed = json.loads(candidate, strict=False)
                            if isinstance(parsed, dict):
                                self._on_step(
                                    "format_repair_local_fallback",
                                    {
                                        "task": task_type.value,
                                        "repair_action": "extract_json_object_fallback",
                                        "original_error": str(last_exc),
                                    },
                                )
                                return candidate
                        except (json.JSONDecodeError, TypeError, ValueError):
                            pass
            raise last_exc
        return last_content

    def _init_claim_semantic_patch_max_tokens(self, target_count: int) -> int:
        """Bound a path-only patch well below a full Claims extraction budget."""

        estimated = 768 + max(1, target_count) * 192
        configured_cap = self._format_repair_max_tokens()
        return max(1024, min(8192, configured_cap, estimated))

    async def _repair_init_claim_semantics(
        self,
        *,
        base_request: ModelRequest,
        task_type: TaskType,
        candidate_data: dict[str, Any],
        issues: tuple[FormatSchemaIssue, ...],
        validator: _RepairedResponseValidator,
        attempt: int,
        max_attempts: int,
    ) -> FormatRepairResult | None:
        """Re-adjudicate only rejected Claim paths and validate the whole batch."""

        targets = collect_init_claim_repair_targets(candidate_data, issues)
        issue_paths = {str(issue.path or "") for issue in issues}
        target_paths = {target.path for target in targets}
        if not targets or issue_paths != target_paths:
            return None

        prompt = build_init_claim_semantic_repair_prompt(candidate_data, targets)
        model_override = str(
            getattr(self._settings, "llm_format_repair_model", "")
            or getattr(self._settings, "repair_model", "")
            or ""
        ).strip()
        provider_hint, model_hint = self._parse_provider_model_override(model_override)
        max_tokens = self._init_claim_semantic_patch_max_tokens(len(targets))
        repair_request = base_request.model_copy(
            update={
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "你是 Novel Forge 的 Claim 语义字段裁决器。"
                            "只能根据已给 Claim 证据修复指定路径，不得改写或补写剧情。"
                            "最终只输出符合指定 JSON Schema 的完整对象。"
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
                "max_tokens": max_tokens,
                "temperature": float(
                    getattr(self._settings, "llm_format_retry_temperature", 0.0) or 0.0
                ),
                "temperature_jitter_allowed": False,
                "model_id": model_hint,
                "thinking": False,
                "multi_turn": False,
                "response_json_schema": INIT_CLAIM_SEMANTIC_PATCH_SCHEMA,
                "response_schema_name": "init_claim_semantic_patch",
                "response_schema_strict": True,
                # Prompt-only providers remain eligible. Requiring native schema
                # support here would reopen the all-routes-failed failure mode.
                "require_native_structured_output": False,
            }
        )
        event_base = {
            "task": task_type.value,
            "attempt": attempt,
            "max_attempts": max_attempts,
            "target_count": len(targets),
            "target_paths": sorted(target_paths),
            "max_tokens": max_tokens,
            "repair_action": "init_claim_semantic_patch",
        }
        self._on_step("claim_semantic_repair_requested", event_base)

        response = None
        repaired_data: dict[str, Any] | None = None
        patch_attempt_limit = 2
        last_error = ""
        for patch_attempt in range(1, patch_attempt_limit + 1):
            current_request = repair_request
            if patch_attempt > 1:
                current_request = repair_request.model_copy(
                    update={
                        "messages": [
                            *repair_request.messages,
                            {
                                "role": "user",
                                "content": (
                                    "上一版局部补丁被契约层拒绝："
                                    f"{last_error}。请重新核对 enum_contract，"
                                    "仍只返回原 targets 的完整 repairs；不得改目标、"
                                    "不得返回完整 Claims。"
                                ),
                            },
                        ]
                    }
                )
            try:
                if provider_hint:
                    response = await self._router.route(
                        current_request,
                        provider=provider_hint,
                    )
                else:
                    response = await self._router.route(current_request)
                finish_reason = getattr(response, "finish_reason", None)
                if finish_reason == "length":
                    raise ValueError("Init claim semantic patch hit its token limit")
                patch_payload = _strict_json_object_or_none(
                    str(getattr(response, "content", "") or "")
                )
                if patch_payload is None:
                    raise ValueError("Init claim semantic patch must be one complete JSON object")
                repaired_data = apply_init_claim_semantic_patch(
                    candidate_data,
                    targets,
                    patch_payload,
                )
                validator(repaired_data)
                break
            except (ModelGatewayError, asyncio.TimeoutError, ConnectionError, TimeoutError) as exc:
                self._on_step(
                    "claim_semantic_repair_failed",
                    {
                        **event_base,
                        "patch_attempt": patch_attempt,
                        "error": f"{type(exc).__name__}: {exc}",
                    },
                )
                return None
            except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                if patch_attempt < patch_attempt_limit:
                    self._on_step(
                        "claim_semantic_repair_retry",
                        {
                            **event_base,
                            "patch_attempt": patch_attempt,
                            "patch_attempt_limit": patch_attempt_limit,
                            "error": last_error,
                        },
                    )
                    continue
                self._on_step(
                    "claim_semantic_repair_failed",
                    {
                        **event_base,
                        "patch_attempt": patch_attempt,
                        "patch_attempt_limit": patch_attempt_limit,
                        "error": last_error,
                    },
                )
                return None

        if response is None or repaired_data is None:
            return None

        self._on_step(
            "claim_semantic_repair_succeeded",
            {
                **event_base,
                "patch_attempt": patch_attempt,
                "model_id": str(getattr(response, "model_id", "") or model_hint or ""),
            },
        )
        return FormatRepairResult(
            success=True,
            source=RepairSource.LLM,
            data=repaired_data,
            repaired_text=json.dumps(repaired_data, ensure_ascii=False),
            repair_action="init_claim_semantic_patch",
            strategy="evidence_bounded_init_claim_semantic_patch",
            risk=RepairRisk.SAFE,
            diagnostics={
                "semantic_patch_target_count": len(targets),
                "semantic_patch_paths": sorted(target_paths),
            },
        )

    async def call_text_stream_with_retry(
        self,
        task_type: TaskType,
        context: dict[str, Any],
        *,
        max_tokens: int = PipelineConstants.INITIAL_MAX_TOKENS,
        temperature: float = 0.7,
        top_p: float | None = None,
        max_retries: int = 2,
        prior_messages: list[dict[str, str]] | None = None,
        thinking: bool = False,
        multi_turn: bool = False,
        provider: str | None = None,
        model_id: str | None = None,
        _capture_raw: list[str] | None = None,
        validate_text_output: bool = True,
    ) -> str:
        """Call a TEXT_ONLY task through router streaming and validate final text."""
        if task_expects_json(task_type):
            result = await self.call_with_retry(
                task_type,
                context,
                max_tokens=max_tokens,
                temperature=temperature,
                top_p=top_p,
                max_retries=max_retries,
                prior_messages=prior_messages,
                thinking=thinking,
                multi_turn=multi_turn,
                provider=provider,
                model_id=model_id,
                _capture_raw=_capture_raw,
            )
            return str(result)

        if self._task_circuit_breaker is not None:
            self._task_circuit_breaker.check(task_type)

        current_context = dict(context)
        current_max_tokens = normalize_task_max_tokens(
            self._router,
            task_type,
            max_tokens,
            provider=provider,
            model_id=model_id,
            contract_context=current_context,
        )
        last_exc: Exception | None = None
        effective_max_retries = max(1, max_retries)
        chapter = current_context.get("chapter_number", current_context.get("chapter", ""))
        operation_id = uuid.uuid4().hex

        for attempt in range(1, effective_max_retries + 1):
            request = self._builder.build(
                task_type,
                current_context,
                max_tokens=current_max_tokens,
                temperature=temperature,
                top_p=top_p,
                prior_messages=prior_messages,
                thinking=thinking,
                multi_turn=multi_turn,
            )
            if attempt == 1:
                current_context, request = self._fit_optional_context_at_preflight(
                    task_type=task_type,
                    request=request,
                    context=current_context,
                    provider=provider,
                    model_id=model_id,
                    rebuild=partial(
                        self._builder.build,
                        task_type,
                        max_tokens=current_max_tokens,
                        temperature=temperature,
                        top_p=top_p,
                        prior_messages=prior_messages,
                        thinking=thinking,
                        multi_turn=multi_turn,
                    ),
                )
            if model_id:
                request = request.model_copy(update={"model_id": model_id})
            if attempt == 1:
                self._emit_prompt_pressure_preflight(
                    task_type=task_type,
                    request=request,
                    context=current_context,
                    provider=provider,
                    model_id=model_id,
                    attempt=attempt,
                    chapter=chapter,
                    event_name="prompt_pressure",
                )

            stream_id = uuid.uuid4().hex
            self._on_step(
                "llm_stream_start",
                {
                    "stream_id": stream_id,
                    "operation_id": operation_id,
                    "stream_kind": "text",
                    "output_kind": "text",
                    "task": task_type.value,
                    "chapter": chapter,
                    "attempt": attempt,
                    "max_attempts": effective_max_retries,
                },
            )

            chunks: list[str] = []
            reasoning_chunks: list[str] = []
            # Order-preserving segment buffer.  Each entry is a (kind, text)
            # tuple where kind is "content" or "reasoning".  Same-kind
            # consecutive chunks merge into the last segment so the UI
            # receives the real interleaving order without fragmentation.
            pending_segments: list[tuple[str, str]] = []
            stream_state = {
                "text_length": 0,
                "reasoning_length": 0,
                "last_flush_at": 0.0,
                "pending_chars": 0,
            }

            def _flush_segments(
                *,
                force: bool = False,
                _buffer: list[tuple[str, str]] = pending_segments,
                _state: dict[str, Any] = stream_state,
                _stream_id: str = stream_id,
                _attempt: int = attempt,
            ) -> None:
                if not _buffer:
                    return
                now = time.monotonic()
                last_flush_at = float(_state["last_flush_at"])
                if not force and now - last_flush_at < _STREAM_DELTA_FLUSH_INTERVAL_S:
                    return
                segments_snapshot = list(_buffer)
                _buffer.clear()
                _state["last_flush_at"] = now
                _state["pending_chars"] = 0
                self._on_step(
                    "llm_stream_delta",
                    {
                        "stream_id": _stream_id,
                        "operation_id": operation_id,
                        "stream_kind": "text",
                        "output_kind": "text",
                        "task": task_type.value,
                        "chapter": chapter,
                        "attempt": _attempt,
                        "segments": [
                            {"kind": kind, "text": text} for kind, text in segments_snapshot
                        ],
                        "text_length": int(_state["text_length"]),
                        "reasoning_length": int(_state["reasoning_length"]),
                    },
                )

            def _on_chunk(
                chunk_or_text: StreamChunk | str,
                _chunks: list[str] = chunks,
                _reasoning_chunks: list[str] = reasoning_chunks,
                _buffer: list[tuple[str, str]] = pending_segments,
                _state: dict[str, Any] = stream_state,
                _stream_id: str = stream_id,
                _attempt: int = attempt,
            ) -> None:
                # Mock routers in tests may pass plain strings; normalize.
                chunk = to_stream_chunk(chunk_or_text)
                if chunk.reset:
                    _flush_segments(force=True)
                    _chunks.clear()
                    _reasoning_chunks.clear()
                    _buffer.clear()
                    _state.update(
                        text_length=0,
                        reasoning_length=0,
                        pending_chars=0,
                    )
                    self._on_step(
                        "llm_stream_restart",
                        {
                            "stream_id": _stream_id,
                            "operation_id": operation_id,
                            "stream_kind": "text",
                            "output_kind": "text",
                            "task": task_type.value,
                            "chapter": chapter,
                            "attempt": _attempt,
                            "message": chunk.reset_reason or "流式连接已重启。",
                            "reset_output": True,
                        },
                    )
                    return
                if chunk.content:
                    _chunks.append(chunk.content)
                    _state["text_length"] += len(chunk.content)
                    _state["pending_chars"] += len(chunk.content)
                    if _buffer and _buffer[-1][0] == "content":
                        prev_kind, prev_text = _buffer[-1]
                        _buffer[-1] = (prev_kind, prev_text + chunk.content)
                    else:
                        _buffer.append(("content", chunk.content))
                if chunk.reasoning:
                    _reasoning_chunks.append(chunk.reasoning)
                    _state["reasoning_length"] += len(chunk.reasoning)
                    _state["pending_chars"] += len(chunk.reasoning)
                    if _buffer and _buffer[-1][0] == "reasoning":
                        prev_kind, prev_text = _buffer[-1]
                        _buffer[-1] = (prev_kind, prev_text + chunk.reasoning)
                    else:
                        _buffer.append(("reasoning", chunk.reasoning))
                _flush_segments(
                    force=int(_state["pending_chars"]) >= _STREAM_DELTA_FORCE_FLUSH_CHARS
                )

            try:
                if provider:
                    response = await self._router.stream_route(
                        request,
                        provider=provider,
                        on_chunk=_on_chunk,
                    )
                else:
                    response = await self._router.stream_route(request, on_chunk=_on_chunk)
                _flush_segments(force=True)
                response_content = str(getattr(response, "content", "") or "") or "".join(chunks)
                text = extract_text_content(response_content)
                if validate_text_output:
                    try:
                        text = validate_text_output_contract(
                            task_type,
                            response_content,
                            text,
                            min_chars=1,
                        )
                    except TextOutputContractError as exc:
                        raw = response_content.strip()
                        reason = "空内容" if not raw else "格式无效"
                        raise ModelGatewayError(
                            f"{task_type.value} TEXT 流式输出{reason}：{exc}",
                            is_transient=True,
                        ) from exc
                finish_reason = str(getattr(response, "finish_reason", "") or "")
                completion_tokens = int(getattr(response, "completion_tokens", 0) or 0)
                if validate_text_output and finish_reason == "length":
                    target_words = int(current_context.get("target_word_count", 0) or 0)
                    text_chars = count_chapter_words(text)
                    if target_words > 0 and text_chars < target_words * 0.3:
                        raise ModelGatewayError(
                            f"{task_type.value} TEXT 流式输出被截断且过短（{text_chars} 字符 < "
                            f"目标 {target_words} 的 30%，finish_reason={finish_reason}, "
                            f"completion_tokens={completion_tokens}）",
                            is_transient=True,
                        )
                if _capture_raw is not None and not _capture_raw:
                    _capture_raw.append(response_content)
                if self._task_circuit_breaker is not None:
                    self._task_circuit_breaker.record_success(task_type)
                reasoning_text = str(getattr(response, "thinking_content", "") or "") or "".join(
                    reasoning_chunks
                )
                self._on_step(
                    "llm_stream_end",
                    {
                        "stream_id": stream_id,
                        "operation_id": operation_id,
                        "stream_kind": "text",
                        "output_kind": "text",
                        "task": task_type.value,
                        "chapter": chapter,
                        "attempt": attempt,
                        "chars": len(text),
                        "text": text,
                        "text_length": len(text),
                        "reasoning": reasoning_text,
                        "reasoning_length": len(reasoning_text),
                        "finish_reason": finish_reason,
                    },
                )
                return text
            except (ModelGatewayError, asyncio.TimeoutError, ConnectionError, TimeoutError) as exc:
                last_exc = exc
                _flush_segments(force=True)
                partial_text = "".join(chunks)
                partial_reasoning = "".join(reasoning_chunks)
                self._on_step(
                    "llm_stream_error",
                    {
                        "stream_id": stream_id,
                        "operation_id": operation_id,
                        "stream_kind": "text",
                        "output_kind": "text",
                        "task": task_type.value,
                        "chapter": chapter,
                        "attempt": attempt,
                        "error": str(exc),
                        "text": partial_text,
                        "text_length": len(partial_text),
                        "reasoning": partial_reasoning,
                        "reasoning_length": len(partial_reasoning),
                    },
                )
                if attempt < effective_max_retries and llm_h.is_transient_llm_error(exc):
                    backoff = llm_h.compute_transient_retry_backoff(exc, attempt)
                    self._on_step(
                        "llm_stream_restart",
                        {
                            "stream_id": stream_id,
                            "operation_id": operation_id,
                            "stream_kind": "text",
                            "output_kind": "text",
                            "task": task_type.value,
                            "chapter": chapter,
                            "attempt": attempt,
                            "next_attempt": attempt + 1,
                            "backoff_seconds": backoff,
                            "text": partial_text,
                            "text_length": len(partial_text),
                            "reasoning": partial_reasoning,
                            "reasoning_length": len(partial_reasoning),
                        },
                    )
                    await asyncio.sleep(backoff)
                    continue
                if self._task_circuit_breaker is not None:
                    self._task_circuit_breaker.record_failure(task_type)
                raise
            except ValueError as exc:
                last_exc = exc
                _flush_segments(force=True)
                partial_text = "".join(chunks)
                partial_reasoning = "".join(reasoning_chunks)
                self._on_step(
                    "llm_stream_error",
                    {
                        "stream_id": stream_id,
                        "operation_id": operation_id,
                        "stream_kind": "text",
                        "output_kind": "text",
                        "task": task_type.value,
                        "chapter": chapter,
                        "attempt": attempt,
                        "error": str(exc),
                        "text": partial_text,
                        "text_length": len(partial_text),
                        "reasoning": partial_reasoning,
                        "reasoning_length": len(partial_reasoning),
                    },
                )
                if attempt < effective_max_retries:
                    self._on_step(
                        "llm_stream_restart",
                        {
                            "stream_id": stream_id,
                            "operation_id": operation_id,
                            "stream_kind": "text",
                            "output_kind": "text",
                            "task": task_type.value,
                            "chapter": chapter,
                            "attempt": attempt,
                            "next_attempt": attempt + 1,
                            "backoff_seconds": 0.0,
                            "text": partial_text,
                            "text_length": len(partial_text),
                            "reasoning": partial_reasoning,
                            "reasoning_length": len(partial_reasoning),
                        },
                    )
                    await asyncio.sleep(0)
                    continue
                raise

        if last_exc is not None:
            raise last_exc
        raise ModelGatewayError(f"streaming call failed for task={task_type.value}")

    async def call_with_retry(
        self,
        task_type: TaskType,
        context: dict[str, Any],
        *,
        max_tokens: int = PipelineConstants.INITIAL_MAX_TOKENS,
        temperature: float = 0.7,
        top_p: float | None = None,
        required_keys: tuple[str, ...] = (),
        max_retries: int = 2,
        prior_messages: list[dict[str, str]] | None = None,
        thinking: bool = False,
        multi_turn: bool = False,
        provider: str | None = None,
        model_id: str | None = None,
        _capture_raw: list[str] | None = None,
        include_contract_required_keys: bool = True,
    ) -> dict[str, Any] | str:
        """Call LLM and parse JSON response, with automatic retry on failure.

        On JSON parse failure the service:
        1. Escalates max_tokens (L1) only for truncation/parse damage.
        2. Disables thinking on truncation to reclaim token budget (L3).

        Schema and semantic failures keep the current output budget. Init Claim
        enum mismatches first receive an evidence-bounded, exact-path LLM patch;
        a failed patch falls back to a fresh extraction retry.
        """
        effective_max_retries = max_retries
        if task_expects_json(task_type):
            configured_attempts = int(
                getattr(self._settings, "llm_format_retry_attempts", max_retries) or max_retries
            )
            effective_max_retries = max(max_retries, configured_attempts)

        # ── Task circuit breaker check ───────────────────────────
        # Reject the call early if this TaskType's circuit is open.
        if self._task_circuit_breaker is not None:
            self._task_circuit_breaker.check(task_type)

        last_exc: Exception | None = None
        current_context = dict(context)
        current_max_tokens = max_tokens
        current_temperature = temperature
        current_temperature_jitter_allowed = True
        current_thinking = thinking
        contract = resolve_task_format_contract(task_type, current_context)
        effective_contract_mode = contract.effective_contract_mode if contract else None
        if task_expects_json(task_type) and not include_contract_required_keys:
            effective_contract_mode = ContractMode.FRAGMENT_OBJECT
        contract_mode = effective_contract_mode.value if effective_contract_mode else ""
        effective_required_keys = (
            llm_h.raw_required_response_keys(task_type, required_keys, context=current_context)
            if include_contract_required_keys
            else required_keys
        )
        normalized_max_tokens = normalize_task_max_tokens(
            self._router,
            task_type,
            current_max_tokens,
            provider=provider,
            model_id=model_id,
            contract_context=current_context,
            include_contract_required_keys=include_contract_required_keys,
            contract_mode_override=effective_contract_mode,
        )
        if normalized_max_tokens > current_max_tokens:
            self._on_step(
                "token_budget_normalized",
                {
                    "task": task_type.value,
                    "old_max_tokens": current_max_tokens,
                    "new_max_tokens": normalized_max_tokens,
                    "contract_mode": contract_mode,
                },
            )
            current_max_tokens = normalized_max_tokens

        # ── Streaming delegation for TEXT_ONLY tasks ────────────────────────
        # When streaming is enabled and the task does not expect JSON, route
        # through ``call_text_stream_with_retry`` so the desktop UI receives
        # ``llm_stream_*`` events (逐字流式) instead of only ``model_call_update``.
        # This covers long-form repair steps (continuity/causal/reading-power)
        # that call ``call_with_retry`` with a TEXT_ONLY contract.
        if not task_expects_json(task_type) and bool(
            getattr(self._settings, "long_streaming_text_enabled", True)
        ):
            return await self.call_text_stream_with_retry(
                task_type,
                context,
                max_tokens=max_tokens,
                temperature=temperature,
                max_retries=max_retries,
                prior_messages=prior_messages,
                thinking=thinking,
                multi_turn=multi_turn,
                provider=provider,
                model_id=model_id,
                _capture_raw=_capture_raw,
                validate_text_output=True,
            )

        operation_id = uuid.uuid4().hex
        _stream_timed_out = False
        best_partial_text = ""
        best_partial_reasoning = ""
        best_partial_provider = ""
        best_partial_model_id = ""
        best_partial_transport_error: ModelGatewayError | None = None
        for attempt in range(1, effective_max_retries + 1):
            observation = ResponseObservation(
                self._on_step,
                task_type.value,
                operation_id,
                uuid.uuid4().hex,
                attempt,
                effective_max_retries,
            )
            partial_transport_error: ModelGatewayError | None = None
            request = self._builder.build(
                task_type,
                current_context,
                max_tokens=current_max_tokens,
                temperature=current_temperature,
                top_p=top_p,
                prior_messages=prior_messages,
                thinking=current_thinking,
                multi_turn=multi_turn,
            )
            if attempt == 1:
                current_context, request = self._fit_optional_context_at_preflight(
                    task_type=task_type,
                    request=request,
                    context=current_context,
                    provider=provider,
                    model_id=model_id,
                    rebuild=partial(
                        self._builder.build,
                        task_type,
                        max_tokens=current_max_tokens,
                        temperature=current_temperature,
                        top_p=top_p,
                        prior_messages=prior_messages,
                        thinking=current_thinking,
                        multi_turn=multi_turn,
                    ),
                )
            if not current_temperature_jitter_allowed:
                request = request.model_copy(update={"temperature_jitter_allowed": False})
            if model_id:
                request = request.model_copy(update={"model_id": model_id})

            # ── Pre-flight token estimation (attempt 1 only) ─────────
            if attempt == 1:
                self._emit_prompt_pressure_preflight(
                    task_type=task_type,
                    request=request,
                    context=current_context,
                    provider=provider,
                    model_id=model_id,
                    attempt=attempt,
                    chapter=current_context.get(
                        "chapter_number", current_context.get("chapter", "")
                    ),
                    event_name="preflight_token_estimate",
                )

            use_json_observation_stream = (
                not _stream_timed_out
                and task_expects_json(task_type)
                and self._json_observation_stream_enabled()
                and task_type.value not in self._stream_observation_excluded_tasks()
                and callable(getattr(self._router, "stream_route", None))
            )
            try:
                if use_json_observation_stream:
                    response = await self._route_with_observed_stream(
                        request,
                        task_type=task_type,
                        provider=provider,
                        chapter=current_context.get(
                            "chapter_number",
                            current_context.get("chapter", ""),
                        ),
                        attempt=attempt,
                        max_attempts=effective_max_retries,
                        stream_kind="json_observation",
                        output_kind="json",
                        stream_id=observation.stream_id,
                        operation_id=operation_id,
                    )
                elif provider:
                    response = await self._router.route(request, provider=provider)
                else:
                    response = await self._router.route(request)
            except asyncio.CancelledError:
                if task_expects_json(task_type):
                    observation.emit("failed", error=ValueError("任务已取消，未完成校验"))
                raise
            except (ModelGatewayError, asyncio.TimeoutError, ConnectionError, TimeoutError) as exc:
                last_exc = exc
                if isinstance(exc, ModelGatewayError):
                    candidate_partial = str(getattr(exc, "partial_text", "") or "")
                    if len(candidate_partial) > len(best_partial_text):
                        best_partial_text = candidate_partial
                        best_partial_reasoning = str(getattr(exc, "partial_reasoning", "") or "")
                        best_partial_provider = str(getattr(exc, "partial_provider", "") or "")
                        best_partial_model_id = str(getattr(exc, "partial_model_id", "") or "")
                        best_partial_transport_error = exc
                failure_categories = _gateway_failure_categories(exc)
                # Stream path timed out — fall back to non-streaming route()
                if use_json_observation_stream and (
                    isinstance(exc, (asyncio.TimeoutError, TimeoutError))
                    or "timeout" in failure_categories
                ):
                    _stream_timed_out = True
                    self._on_step(
                        "json_stream_fallback_to_route",
                        {
                            "task": task_type.value,
                            "attempt": attempt,
                            "reason": "stream_timeout",
                        },
                    )
                # ── Stream inflation (model repetition loop) ────────────
                # Router wraps runaway-output aborts in ModelGatewayError with
                # failure_categories containing "stream_inflation".  These are
                # NOT transient provider faults, but a temperature-0 retry is
                # the cheapest recovery before burning fallback routes.
                _inflation_failure = "stream_inflation" in failure_categories
                if attempt < effective_max_retries and (
                    llm_h.is_transient_llm_error(exc) or _inflation_failure
                ):
                    if task_expects_json(task_type):
                        observation.emit("retrying", error=exc)
                    backoff = llm_h.compute_transient_retry_backoff(exc, attempt)
                    if _inflation_failure:
                        current_temperature = 0.0
                        current_temperature_jitter_allowed = False
                        current_thinking = False
                    self._on_step(
                        "retry_transient_error",
                        {
                            "task": task_type.value,
                            "attempt": attempt,
                            "max_retries": max(0, effective_max_retries - 1),
                            "max_attempts": effective_max_retries,
                            "backoff_seconds": backoff,
                            "error": str(exc),
                            "contract_mode": contract_mode,
                            "stream_fallback": _stream_timed_out,
                            "retry_strategy": (
                                "temperature_zero" if _inflation_failure else "transient"
                            ),
                        },
                    )
                    await asyncio.sleep(backoff)
                    continue
                if best_partial_text and best_partial_transport_error is not None:
                    partial_transport_error = (
                        exc if isinstance(exc, ModelGatewayError) else best_partial_transport_error
                    )
                    response = ModelResponse(
                        content=best_partial_text,
                        thinking_content=best_partial_reasoning,
                        model_id=best_partial_model_id,
                        finish_reason="stream_error_partial",
                    )
                    self._on_step(
                        "json_stream_partial_recovery_started",
                        {
                            "task": task_type.value,
                            "attempt": attempt,
                            "max_attempts": effective_max_retries,
                            "text_length": len(best_partial_text),
                            "reasoning_length": len(best_partial_reasoning),
                            "provider": best_partial_provider,
                            "model_id": best_partial_model_id,
                            "failure_categories": list(failure_categories),
                        },
                    )
                else:
                    # Record failure for circuit breaker (non-transient or last attempt)
                    if task_expects_json(task_type):
                        observation.emit("failed", error=exc)
                    if self._task_circuit_breaker is not None:
                        self._task_circuit_breaker.record_failure(task_type)
                    raise

            try:
                response_content = str(getattr(response, "content", "") or "")
                response_finish_reason = getattr(response, "finish_reason", None)
                response_model_id = getattr(response, "model_id", None)
                schema_issues_for_retry: tuple[FormatSchemaIssue, ...] = ()
                if not task_expects_json(task_type):
                    text = validate_text_output_contract(
                        task_type,
                        response_content,
                        extract_text_content(response_content),
                        min_chars=1,
                    )
                    if _capture_raw is not None and not _capture_raw:
                        _capture_raw.append(response_content)
                    if self._task_circuit_breaker is not None:
                        self._task_circuit_breaker.record_success(task_type)
                    return text
                observation.emit("validating", response=response)
                if task_expects_json(task_type) and response_finish_reason == "length":
                    raise ValueError(
                        "Model response hit max_tokens before completing a structured JSON "
                        f"payload (completion_tokens={getattr(response, 'completion_tokens', 0)}, "
                        f"max_tokens={current_max_tokens})"
                    )
                adapter_results: list[TaskOutputAdapterResult] = []
                repair_validator = _RepairedResponseValidator(
                    service=self,
                    task_type=task_type,
                    response_content=response_content,
                    response_finish_reason=response_finish_reason,
                    current_context=current_context,
                    adapter_results=adapter_results,
                    effective_required_keys=effective_required_keys,
                    validate_full_contract_schema=include_contract_required_keys,
                )

                def _accept_local_repair(
                    candidate: RepairCandidate,
                    *,
                    _current_context: dict[str, Any] = current_context,
                    _finish_reason: str | None = response_finish_reason,
                ) -> bool:
                    if (
                        candidate.risk == RepairRisk.LOSSY
                        and task_type == TaskType.PLAN_CHAPTER_CONTRACTS
                    ):
                        return _accept_partial_chapter_contract_repair(
                            candidate,
                            current_context=_current_context,
                            finish_reason=_finish_reason,
                        )
                    if candidate.risk == RepairRisk.LOSSY and task_type in {
                        TaskType.PLAN_OUTLINE,
                        TaskType.ADJUDICATE_CONTRACT_COHERENCE,
                    }:
                        # Fragment-object mode (blueprint fragments) can tolerate
                        # lossy repair because missing fields are filled by later
                        # fragments.  Full-object mode cannot.
                        if effective_contract_mode == ContractMode.FRAGMENT_OBJECT:
                            return True
                        return False
                    return True

                def _classify_raw_required_key_loss(
                    candidate: RepairCandidate,
                    missing_raw_keys: tuple[str, ...],
                    *,
                    _response_content: str = response_content,
                    _finish_reason: str | None = response_finish_reason,
                ) -> BaseException | None:
                    del missing_raw_keys
                    if task_type != TaskType.EXTRACT_CANON:
                        return None
                    return self._extract_canon_damage_error(
                        _response_content,
                        candidate.data,
                        finish_reason=_finish_reason,
                    )

                contract_context_items = tuple(current_context.items())
                strict_claim_candidate = (
                    _strict_json_object_or_none(response_content)
                    if task_type in _INIT_CLAIM_TASKS
                    else None
                )

                llm_format_repair: LLMRepairCallable | None = None
                if (
                    task_expects_json(task_type)
                    and self._format_repair_enabled()
                    and attempt >= effective_max_retries
                    and _has_recoverable_json_source(response_content)
                    and strict_claim_candidate is None
                ):
                    llm_format_repair = _FormatRepairLLMCallback(
                        service=self,
                        base_request=request,
                        task_type=task_type,
                        attempt=attempt,
                        max_attempts=effective_max_retries,
                        required_keys=effective_required_keys,
                        raw_content=response_content,
                        response=response,
                        current_max_tokens=current_max_tokens,
                        contract_context_items=contract_context_items,
                        contract_mode_override=effective_contract_mode,
                        observation=observation,
                    )
                elif (
                    task_expects_json(task_type)
                    and self._format_repair_enabled()
                    and attempt >= effective_max_retries
                    and not _has_recoverable_json_source(response_content)
                ):
                    self._on_step(
                        "format_repair_skipped",
                        {
                            "task": task_type.value,
                            "attempt": attempt,
                            "max_attempts": effective_max_retries,
                            "reason": "empty_or_unstructured_source",
                            "finish_reason": response_finish_reason or "",
                            "model_id": response_model_id or "",
                            "contract_mode": contract_mode,
                        },
                    )

                if _strict_json_object_or_none(response_content) is None:
                    observation.emit("repairing", repair_source="local", response=response)
                repair_result = await run_format_response_repair_v2(
                    FormatRepairContext(
                        task_type=task_type,
                        raw_content=response_content,
                        error=None,
                        required_keys=effective_required_keys,
                        attempt=attempt,
                        max_attempts=effective_max_retries,
                        finish_reason=response_finish_reason,
                        model_id=response_model_id,
                        max_tokens=current_max_tokens,
                        raw_char_limit=self._format_repair_raw_char_limit(),
                        contract_context=current_context,
                        include_contract_required_keys=include_contract_required_keys,
                        contract_mode_override=effective_contract_mode,
                    ),
                    validator=repair_validator,
                    accept_local_repair=_accept_local_repair,
                    classify_raw_required_key_loss=_classify_raw_required_key_loss,
                    llm_repair=llm_format_repair,
                )
                if not repair_result.success or repair_result.data is None:
                    schema_issues_for_retry = repair_validator.last_schema_issues
                    if (
                        self._format_repair_enabled()
                        and strict_claim_candidate is not None
                        and schema_issues_for_retry
                    ):
                        observation.emit("repairing", repair_source="llm", response=response)
                        semantic_result = await self._repair_init_claim_semantics(
                            base_request=request,
                            task_type=task_type,
                            candidate_data=strict_claim_candidate,
                            issues=schema_issues_for_retry,
                            validator=repair_validator,
                            attempt=attempt,
                            max_attempts=effective_max_retries,
                        )
                        if semantic_result is not None:
                            repair_result = semantic_result
                    if not repair_result.success or repair_result.data is None:
                        error = repair_result.error or ValueError(
                            "Model response JSON repair failed"
                        )
                        raise error

                assert repair_result.data is not None
                data = repair_result.data
                if adapter_results:
                    adapter_result = adapter_results[-1]
                    self._on_step(
                        "task_output_normalized",
                        {
                            "task": task_type.value,
                            "adapter": adapter_result.adapter,
                            "changed_keys": list(adapter_result.changed_keys),
                            "metadata": adapter_result.metadata or {},
                            "attempt": attempt,
                        },
                    )
                if repair_result.source is not None and repair_result.source != RepairSource.STRICT:
                    directive = build_format_retry_directive(
                        task_type=task_type,
                        attempt=attempt,
                        max_attempts=effective_max_retries,
                        error=self._repair_event_error(
                            repair_result.error,
                            repair_result.diagnostics,
                        ),
                        required_keys=effective_required_keys,
                        raw_content=response_content,
                        raw_excerpt_limit=self._format_retry_raw_char_limit(),
                        context=current_context,
                        contract_mode_override=effective_contract_mode,
                    )
                    repaired_payload = build_format_error_event(
                        directive,
                        raw_content=response_content,
                        finish_reason=response_finish_reason,
                        model_id=response_model_id,
                        max_tokens=current_max_tokens,
                    )
                    repaired_payload["repaired"] = True
                    repaired_payload["repair_action"] = repair_result.repair_action
                    repaired_payload["repair_source"] = repair_result.source.value
                    repaired_payload["repair_strategy"] = repair_result.strategy
                    repaired_payload["repair_risk"] = repair_result.risk.value
                    repaired_payload["repair_diagnostics"] = repair_result.diagnostics
                    self._on_step("format_repaired", repaired_payload)
                parse_source = (
                    repair_result.source.value if repair_result.source is not None else ""
                )
                self._on_step(
                    "format_validation_success",
                    {
                        "stream_id": observation.stream_id,
                        "operation_id": operation_id,
                        "task": task_type.value,
                        "attempt": attempt,
                        "max_attempts": effective_max_retries,
                        "contract_id": contract.contract_id if contract else "",
                        "contract_mode": contract_mode,
                        "parse_source": parse_source,
                        "repair_strategy": repair_result.strategy or "",
                        "repair_risk": repair_result.risk.value,
                        "schema_issue_count": len(repair_validator.last_schema_issues),
                        "finish_reason": response_finish_reason or "",
                        "model_id": response_model_id or "",
                    },
                )
                observation.emit(
                    "validated", data=data, repair_source=parse_source, response=response
                )
                if _capture_raw is not None and not _capture_raw:
                    _capture_raw.append(json.dumps(data, ensure_ascii=False))
                if self._task_circuit_breaker is not None:
                    self._task_circuit_breaker.record_success(task_type)
                if partial_transport_error is not None:
                    self._on_step(
                        "json_stream_partial_recovery_succeeded",
                        {
                            "task": task_type.value,
                            "attempt": attempt,
                            "text_length": len(response_content),
                            "model_id": response_model_id or "",
                            "parse_source": parse_source,
                        },
                    )
                return data
            except asyncio.CancelledError:
                observation.emit("failed", error=ValueError("任务已取消，未完成校验"))
                raise
            except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
                last_exc = exc
                if partial_transport_error is not None:
                    observation.emit("failed", error=exc, response=response)
                    self._on_step(
                        "json_stream_partial_recovery_rejected",
                        {
                            "task": task_type.value,
                            "attempt": attempt,
                            "max_attempts": effective_max_retries,
                            "text_length": len(response_content),
                            "error_type": type(exc).__name__,
                            "error": str(exc),
                        },
                    )
                    last_exc = partial_transport_error
                    break
                if attempt < effective_max_retries:
                    if task_expects_json(task_type):
                        observation.emit("retrying", error=exc, response=response)
                    model_limit = (
                        get_model_max_output_tokens(response_model_id)
                        if response_model_id
                        else None
                    )

                    # ── L3: Truncation fallback ──────────────────
                    # If the model hit its token limit AND thinking is
                    # active, disable thinking on the next retry so the
                    # full token budget goes to content instead of
                    # internal reasoning.
                    if response_finish_reason == "length" and current_thinking:
                        current_thinking = False

                    # ── L1: Evidence-based token escalation ─────────────
                    # Semantic/schema failures need a focused correction, not
                    # a larger copy of the same extraction response.
                    should_escalate_tokens = _retry_needs_more_output_budget(
                        exc,
                        finish_reason=response_finish_reason,
                    )
                    new_max = current_max_tokens
                    if should_escalate_tokens:
                        new_max = llm_h.escalate_retry_tokens(
                            current_max_tokens,
                            task_type.value,
                            model_max_tokens=model_limit,
                        )
                    retry_temperature = float(
                        getattr(self._settings, "llm_format_retry_temperature", 0.0) or 0.0
                    )
                    if task_expects_json(task_type):
                        directive = build_format_retry_directive(
                            task_type=task_type,
                            attempt=attempt,
                            max_attempts=effective_max_retries,
                            error=exc,
                            required_keys=effective_required_keys,
                            raw_content=response_content,
                            raw_excerpt_limit=self._format_retry_raw_char_limit(),
                            context=current_context,
                            contract_mode_override=effective_contract_mode,
                            schema_issues=schema_issues_for_retry,
                        )
                        current_context = {
                            **context,
                            "_format_retry_instruction": directive.instruction,
                        }
                        current_temperature = retry_temperature
                        current_temperature_jitter_allowed = False
                        self._on_step(
                            "format_retry",
                            build_format_error_event(
                                directive,
                                raw_content=response_content,
                                finish_reason=response_finish_reason,
                                model_id=response_model_id,
                                max_tokens=current_max_tokens,
                                next_max_tokens=new_max,
                                retry_temperature=retry_temperature,
                            ),
                        )

                    backoff = llm_h.compute_retry_backoff(attempt)
                    retry_budget_payload = {
                        "task": task_type.value,
                        "attempt": attempt,
                        "max_attempts": effective_max_retries,
                        "max_retries": max(0, effective_max_retries - 1),
                        "old_max_tokens": current_max_tokens,
                        "new_max_tokens": new_max,
                        "thinking_disabled": thinking and not current_thinking,
                        "model_limit": model_limit,
                        "backoff_seconds": backoff,
                        "error": str(exc),
                        "contract_mode": contract_mode,
                    }
                    if new_max > current_max_tokens:
                        self._on_step("token_escalation", retry_budget_payload)
                    else:
                        retry_budget_payload["reason"] = "non_truncation_format_failure"
                        self._on_step("format_retry_budget_held", retry_budget_payload)
                    current_max_tokens = new_max
                    await asyncio.sleep(backoff)
                    continue
                if task_expects_json(task_type):
                    observation.emit("failed", error=exc, response=response)
                    directive = build_format_retry_directive(
                        task_type=task_type,
                        attempt=attempt,
                        max_attempts=effective_max_retries,
                        error=exc,
                        required_keys=effective_required_keys,
                        raw_content=getattr(response, "content", ""),
                        raw_excerpt_limit=self._format_retry_raw_char_limit(),
                        context=current_context,
                        contract_mode_override=effective_contract_mode,
                        schema_issues=schema_issues_for_retry,
                    )
                    self._on_step(
                        "format_retry_exhausted",
                        build_format_error_event(
                            directive,
                            raw_content=getattr(response, "content", ""),
                            finish_reason=getattr(response, "finish_reason", None),
                            model_id=getattr(response, "model_id", None),
                            max_tokens=current_max_tokens,
                        ),
                    )

        if last_exc is None:
            raise RuntimeError("Unexpected retry state: no error captured")
        if self._task_circuit_breaker is not None:
            self._task_circuit_breaker.record_failure(task_type)
        raise last_exc
