"""Prompt size diagnostics shared by chapter-generation steps."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Callable

from novel_forge.core.parsing.token_utils import count_message_tokens, count_text_tokens


@dataclass(frozen=True)
class PromptPressureDiagnostics:
    """Approximate prompt pressure for one rendered request."""

    prompt_chars: int
    estimated_prompt_tokens: int
    token_count_method: str
    stage_cards_chars: int
    stage_cards_estimated_tokens: int
    stage_cards_token_ratio: float
    tokenizer_name: str = ""
    tokenizer_backed: bool = False
    token_count_exact: bool = False
    card_char_counts: dict[str, int] = field(default_factory=dict)
    card_token_counts: dict[str, int] = field(default_factory=dict)
    largest_context: str = ""
    context_window: int | None = None
    utilization_ratio: float | None = None
    status: str = "ok"
    advisory_overages: dict[str, int] = field(default_factory=dict)
    retrieval_selection: dict[str, Any] = field(default_factory=dict)
    source_artifact_id: str = ""
    output_reserve: int = 0
    available_for_prompt: int | None = None
    fits_context: bool | None = None
    overflow_action: str = "none"
    hard_truncation_allowed: bool = False

    def to_event_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "prompt_chars": self.prompt_chars,
            "estimated_prompt_tokens": self.estimated_prompt_tokens,
            "token_count_method": self.token_count_method,
            "tokenizer_name": self.tokenizer_name,
            "tokenizer_backed": self.tokenizer_backed,
            "token_count_exact": self.token_count_exact,
            "stage_cards_chars": self.stage_cards_chars,
            "stage_cards_estimated_tokens": self.stage_cards_estimated_tokens,
            "stage_cards_token_ratio": round(self.stage_cards_token_ratio, 4),
            "card_char_counts": dict(self.card_char_counts),
            "card_token_counts": dict(self.card_token_counts),
            "largest_context": self.largest_context,
            "status": self.status,
            "advisory_overages": dict(self.advisory_overages),
            "retrieval_selection": dict(self.retrieval_selection),
            "source_artifact_id": self.source_artifact_id,
            "output_reserve": self.output_reserve,
            "overflow_action": self.overflow_action,
            "hard_truncation_allowed": self.hard_truncation_allowed,
        }
        if self.context_window is not None:
            payload["context_window"] = self.context_window
        if self.utilization_ratio is not None:
            payload["utilization_ratio"] = round(self.utilization_ratio, 4)
        if self.available_for_prompt is not None:
            payload["available_for_prompt"] = self.available_for_prompt
        if self.fits_context is not None:
            payload["fits_context"] = self.fits_context
        return payload


def log_prompt_diagnostics(
    logger: logging.Logger,
    *,
    event: str,
    request: Any,
    context: dict[str, Any],
    settings: Any,
    enabled_attr: str,
    warn_attr: str,
    fallback_warn_tokens: int = 32000,
    chapter: Any = "?",
    on_event: Callable[[str, dict[str, Any]], None] | None = None,
    provider: str = "",
    model_id: str = "",
) -> PromptPressureDiagnostics | None:
    """Log approximate prompt size and largest context fields for a request."""

    if not getattr(settings, enabled_attr, True):
        return None

    diagnostics = build_prompt_pressure_diagnostics(
        request,
        context,
        provider=provider,
        model_id=model_id,
    )
    warn_tokens = _settings_int(settings, warn_attr, fallback_warn_tokens)
    log_fn = (
        logger.warning
        if warn_tokens and diagnostics.estimated_prompt_tokens >= warn_tokens
        else logger.info
    )
    log_fn(
        "%s | chapter=%s | prompt_chars=%d | rendered_prompt_tokens=%d | "
        "token_count_method=%s | tokenizer=%s | "
        "max_tokens=%d | warn_tokens=%d | stage_cards_snapshot_tokens=%d | "
        "snapshot_to_rendered_ratio=%.2f | card_snapshot_tokens=%s | largest_context=%s",
        event,
        chapter,
        diagnostics.prompt_chars,
        diagnostics.estimated_prompt_tokens,
        diagnostics.token_count_method,
        diagnostics.tokenizer_name or "none",
        request.max_tokens,
        warn_tokens,
        diagnostics.stage_cards_estimated_tokens,
        diagnostics.stage_cards_token_ratio,
        _format_counts(diagnostics.card_token_counts),
        diagnostics.largest_context,
    )
    if on_event is not None:
        on_event(
            event,
            {
                "chapter": chapter,
                "max_tokens": int(getattr(request, "max_tokens", 0) or 0),
                "warn_tokens": warn_tokens,
                **diagnostics.to_event_payload(),
            },
        )
    return diagnostics


def build_prompt_pressure_diagnostics(
    request: Any,
    context: dict[str, Any],
    *,
    context_window: int | None = None,
    info_ratio: float = 0.15,
    warn_ratio: float = 0.30,
    provider: str = "",
    model_id: str = "",
) -> PromptPressureDiagnostics:
    """Build a reusable prompt pressure snapshot for logs and step events."""

    prompt_text = "\n".join(_message_content(item) for item in request.messages)
    resolved_provider = str(provider or getattr(request, "provider_id", "") or "")
    resolved_model = str(model_id or getattr(request, "model_id", "") or "")
    prompt_count = count_message_tokens(
        request.messages,
        provider=resolved_provider,
        model_id=resolved_model,
    )
    estimated_tokens = prompt_count.tokens
    stage_cards = context.get("stage_cards")
    # This is a diagnostic snapshot, not a model-input budget.  Templates can
    # intentionally retain raw provenance in stage_cards while rendering only
    # a compact projection; use ``estimated_tokens`` above for actual pressure.
    stage_cards_chars = _json_char_size(stage_cards) if isinstance(stage_cards, dict) else 0
    stage_cards_tokens = (
        count_text_tokens(
            json.dumps(stage_cards, ensure_ascii=False, default=str),
            provider=resolved_provider,
            model_id=resolved_model,
        ).tokens
        if isinstance(stage_cards, dict)
        else 0
    )
    card_char_counts: dict[str, int] = {}
    card_token_counts: dict[str, int] = {}
    if isinstance(stage_cards, dict):
        for key, value in stage_cards.items():
            char_count = _json_char_size(value)
            card_char_counts[str(key)] = char_count
            card_token_counts[str(key)] = count_text_tokens(
                json.dumps(value, ensure_ascii=False, default=str),
                provider=resolved_provider,
                model_id=resolved_model,
            ).tokens
    context_budget = stage_cards.get("context_budget", {}) if isinstance(stage_cards, dict) else {}
    advisory_overages = (
        {
            str(key): int(value or 0)
            for key, value in context_budget.get("advisory_overages", {}).items()
        }
        if isinstance(context_budget, dict)
        and isinstance(context_budget.get("advisory_overages"), dict)
        else {}
    )
    retrieval = stage_cards.get("retrieval_evidence", {}) if isinstance(stage_cards, dict) else {}
    retrieval_selection = (
        dict(retrieval.get("selection", {}))
        if isinstance(retrieval, dict) and isinstance(retrieval.get("selection"), dict)
        else {}
    )
    source = stage_cards.get("source", {}) if isinstance(stage_cards, dict) else {}
    source_artifact_id = (
        str(source.get("source_artifact_id", "")) if isinstance(source, dict) else ""
    )

    utilization_ratio: float | None = None
    available_for_prompt: int | None = None
    fits_context: bool | None = None
    output_reserve = max(0, int(getattr(request, "max_tokens", 0) or 0))
    status = "ok"
    overflow_action = "none"
    if context_window and context_window > 0:
        available_for_prompt = max(0, int(context_window) - output_reserve)
        fits_context = estimated_tokens <= available_for_prompt
        utilization_ratio = (
            estimated_tokens / available_for_prompt
            if available_for_prompt > 0
            else float(estimated_tokens)
        )
        if not fits_context:
            status = "overflow"
            overflow_action = "route_larger_context_or_partition_complete_coverage"
        elif utilization_ratio >= warn_ratio:
            status = "warn"
        elif utilization_ratio >= info_ratio:
            status = "info"

    return PromptPressureDiagnostics(
        prompt_chars=len(prompt_text),
        estimated_prompt_tokens=estimated_tokens,
        token_count_method=prompt_count.method,
        tokenizer_name=prompt_count.tokenizer_name,
        tokenizer_backed=prompt_count.tokenizer_backed,
        token_count_exact=prompt_count.exact,
        stage_cards_chars=stage_cards_chars,
        stage_cards_estimated_tokens=stage_cards_tokens,
        stage_cards_token_ratio=(
            stage_cards_tokens / max(1, estimated_tokens) if stage_cards_tokens else 0.0
        ),
        card_char_counts=card_char_counts,
        card_token_counts=card_token_counts,
        largest_context=_context_size_snapshot(context),
        context_window=context_window,
        utilization_ratio=utilization_ratio,
        status=status,
        advisory_overages=advisory_overages,
        retrieval_selection=retrieval_selection,
        source_artifact_id=source_artifact_id,
        output_reserve=output_reserve,
        available_for_prompt=available_for_prompt,
        fits_context=fits_context,
        overflow_action=overflow_action,
        hard_truncation_allowed=False,
    )


def _json_char_size(value: Any) -> int:
    try:
        return len(json.dumps(value, ensure_ascii=False, default=str))
    except (TypeError, ValueError):
        return len(str(value))


def _estimate_prompt_tokens(text: str) -> int:
    """Backward-compatible wrapper around the shared token counter."""

    return count_text_tokens(text).tokens


def _message_content(message: Any) -> str:
    if isinstance(message, dict):
        return str(message.get("content", ""))
    return str(getattr(message, "content", ""))


def _context_size_snapshot(context: dict[str, Any], *, limit: int = 8) -> str:
    items: list[tuple[str, int]] = [(key, _json_char_size(value)) for key, value in context.items()]
    stage_cards = context.get("stage_cards")
    if isinstance(stage_cards, dict):
        items.extend(
            (f"stage_cards.{key}", _json_char_size(value)) for key, value in stage_cards.items()
        )
    sizes = sorted(
        items,
        key=lambda item: item[1],
        reverse=True,
    )
    return ",".join(f"{key}:{size}" for key, size in sizes[:limit])


def _format_counts(counts: dict[str, int], *, limit: int = 8) -> str:
    items = sorted(counts.items(), key=lambda item: item[1], reverse=True)
    return ",".join(f"{key}:{value}" for key, value in items[:limit])


def _settings_int(settings: Any, attr: str, default: int) -> int:
    raw = getattr(settings, attr, default)
    try:
        return max(0, int(raw))
    except (TypeError, ValueError):
        return default
