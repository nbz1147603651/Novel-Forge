"""UI-observable stream aggregation shared by model-consuming domains."""

from __future__ import annotations

import time
import uuid
from typing import Any, Callable

from novel_forge.common.constants import TaskType
from novel_forge.gateway.types import ModelResponse, StreamChunk, to_stream_chunk

_STREAM_DELTA_FLUSH_INTERVAL_S = 0.02
_STREAM_DELTA_FORCE_FLUSH_CHARS = 256


async def route_with_observed_stream(
    router: Any,
    request: Any,
    *,
    on_step: Callable[[str, dict[str, Any]], None],
    task_type: TaskType,
    chapter: Any,
    attempt: int,
    max_attempts: int,
    stream_kind: str,
    output_kind: str,
    provider: str | None = None,
    stream_id: str | None = None,
    operation_id: str = "",
) -> ModelResponse:
    """Aggregate one provider stream while emitting bounded preview events."""

    stream_id = stream_id or uuid.uuid4().hex
    on_step(
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

    def _flush_segments(*, force: bool = False) -> None:
        if not pending_segments:
            return
        now = time.monotonic()
        if (
            not force
            and now - float(stream_state["last_flush_at"]) < _STREAM_DELTA_FLUSH_INTERVAL_S
        ):
            return
        segments = list(pending_segments)
        pending_segments.clear()
        stream_state["last_flush_at"] = now
        stream_state["pending_chars"] = 0
        on_step(
            "llm_stream_delta",
            {
                "stream_id": stream_id,
                "operation_id": operation_id,
                "task": task_type.value,
                "chapter": chapter,
                "attempt": attempt,
                "stream_kind": stream_kind,
                "output_kind": output_kind,
                "segments": [{"kind": kind, "text": text} for kind, text in segments],
                "text_length": int(stream_state["text_length"]),
                "reasoning_length": int(stream_state["reasoning_length"]),
            },
        )

    def _append_segment(kind: str, text: str) -> None:
        if pending_segments and pending_segments[-1][0] == kind:
            previous_kind, previous_text = pending_segments[-1]
            pending_segments[-1] = (previous_kind, previous_text + text)
        else:
            pending_segments.append((kind, text))

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
            on_step(
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
            _append_segment("content", chunk.content)
        if chunk.reasoning:
            reasoning_chunks.append(chunk.reasoning)
            stream_state["reasoning_length"] += len(chunk.reasoning)
            stream_state["pending_chars"] += len(chunk.reasoning)
            _append_segment("reasoning", chunk.reasoning)
        _flush_segments(force=int(stream_state["pending_chars"]) >= _STREAM_DELTA_FORCE_FLUSH_CHARS)

    try:
        if provider:
            response = await router.stream_route(request, provider=provider, on_chunk=_on_chunk)
        else:
            response = await router.stream_route(request, on_chunk=_on_chunk)
        _flush_segments(force=True)
        content = str(getattr(response, "content", "") or "") or "".join(chunks)
        reasoning = str(getattr(response, "thinking_content", "") or "") or "".join(
            reasoning_chunks
        )
        if content != response.content or reasoning != response.thinking_content:
            response = response.model_copy(
                update={"content": content, "thinking_content": reasoning}
            )
        on_step(
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
                "chars": len(content),
                "text": content,
                "text_length": len(content),
                "reasoning": reasoning,
                "reasoning_length": len(reasoning),
                "finish_reason": str(getattr(response, "finish_reason", "") or ""),
                "model_id": str(getattr(response, "model_id", "") or ""),
            },
        )
        return response
    except Exception as exc:
        _flush_segments(force=True)
        partial_text = "".join(chunks)
        partial_reasoning = "".join(reasoning_chunks)
        on_step(
            "llm_stream_error",
            {
                "stream_id": stream_id,
                "operation_id": operation_id,
                "task": task_type.value,
                "chapter": chapter,
                "attempt": attempt,
                "stream_kind": stream_kind,
                "output_kind": output_kind,
                "error": str(exc),
                "text": partial_text,
                "text_length": len(partial_text),
                "reasoning": partial_reasoning,
                "reasoning_length": len(partial_reasoning),
            },
        )
        raise


__all__ = ["route_with_observed_stream"]
