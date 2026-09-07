"""Retry and token-escalation policy for free-form gateway text calls.

Unlike schema-bound pipeline steps, interactive Engine operations sometimes
need a plain-text response.  This shared helper gives every such caller the
same transient retry and truncation handling without coupling the policy to a
particular desktop client.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from novel_forge.gateway.profiles import get_model_max_output_tokens
from novel_forge.gateway.retry_policy import (
    compute_retry_backoff,
    escalate_retry_tokens,
    is_transient_llm_error,
)

if TYPE_CHECKING:
    from novel_forge.gateway.router import ModelRouter
    from novel_forge.gateway.types import ModelRequest, ModelResponse

__all__ = ["call_text_with_retry"]

_logger = logging.getLogger(__name__)
_DEFAULT_MAX_RETRIES = 2


def _is_transient_error_chain(exc: Exception) -> bool:
    pending: list[BaseException] = [exc]
    seen: set[int] = set()
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        if isinstance(current, Exception) and is_transient_llm_error(current):
            return True
        cause = getattr(current, "__cause__", None)
        context = getattr(current, "__context__", None)
        if cause is not None:
            pending.append(cause)
        if context is not None and context is not cause:
            pending.append(context)
    return False


def _truncation_error_message(
    *,
    task_type: str,
    attempt: int,
    max_attempts: int,
    max_tokens: int,
    next_max_tokens: int,
    finish_reason: object,
    model_id: object,
    model_limit: int | None,
    content_chars: int,
) -> str:
    retry_note = (
        "已达到当前可用输出上限"
        if next_max_tokens <= max_tokens
        else "已用尽本次自动重试次数"
    )
    return (
        "模型输出被截断，已停止返回半截文本。"
        f"{retry_note}：task={task_type}, attempt={attempt}/{max_attempts}, "
        f"max_tokens={max_tokens}, next_max_tokens={next_max_tokens}, "
        f"finish_reason={finish_reason or 'unknown'}, model={model_id or 'unknown'}, "
        f"model_limit={model_limit if model_limit is not None else 'unknown'}, "
        f"content_chars={content_chars}。"
        "请缩短选区、减少上下文，或提高模型输出上限后重试。"
    )


async def call_text_with_retry(
    router: ModelRouter,
    request: ModelRequest,
    *,
    max_retries: int = _DEFAULT_MAX_RETRIES,
) -> ModelResponse:
    """Route a plain-text request with transient retries and safe token escalation."""

    current = request
    last_exc: Exception | None = None
    max_attempts = max_retries + 1
    for attempt in range(1, max_attempts + 1):
        try:
            response = await router.route(current)
            content = str(getattr(response, "content", "") or "").strip()
            finish_reason = getattr(response, "finish_reason", None)
            if str(finish_reason or "").lower() == "length":
                model_id = getattr(response, "model_id", None) or current.model_id
                model_limit = get_model_max_output_tokens(str(model_id)) if model_id else None
                new_max = escalate_retry_tokens(
                    current.max_tokens,
                    current.task_type.value,
                    model_max_tokens=model_limit,
                )
                if attempt < max_attempts and new_max > current.max_tokens:
                    _logger.info(
                        "Token escalation: %s %d → %d (attempt %d/%d, model_limit=%s)",
                        current.task_type.value,
                        current.max_tokens,
                        new_max,
                        attempt,
                        max_retries + 1,
                        model_limit,
                    )
                    current = current.model_copy(update={"max_tokens": new_max})
                    await asyncio.sleep(compute_retry_backoff(attempt))
                    continue
                raise ValueError(
                    _truncation_error_message(
                        task_type=current.task_type.value,
                        attempt=attempt,
                        max_attempts=max_attempts,
                        max_tokens=current.max_tokens,
                        next_max_tokens=new_max,
                        finish_reason=finish_reason,
                        model_id=model_id,
                        model_limit=model_limit,
                        content_chars=len(content),
                    )
                )
            if not content:
                raise ValueError(f"Empty response for task {current.task_type.value}")
            return response
        except Exception as exc:
            last_exc = exc
            if attempt < max_attempts and _is_transient_error_chain(exc):
                _logger.warning(
                    "Transient error on attempt %d/%d for %s: %s",
                    attempt,
                    max_attempts,
                    current.task_type.value,
                    exc,
                )
                await asyncio.sleep(compute_retry_backoff(attempt))
                continue
            raise
    if last_exc is not None:
        raise last_exc
    raise RuntimeError("call_text_with_retry: unexpected fallthrough")
