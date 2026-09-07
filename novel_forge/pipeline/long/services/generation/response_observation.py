"""Per-call structured-response feedback, isolated across concurrent calls."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

ResponseValidationStatus = Literal["validating", "repairing", "retrying", "validated", "failed"]


@dataclass(frozen=True)
class ResponseObservation:
    on_step: Callable[[str, dict[str, Any]], None]
    task: str
    operation_id: str
    stream_id: str
    attempt: int
    max_attempts: int

    def emit(
        self,
        status: ResponseValidationStatus,
        *,
        data: dict[str, Any] | None = None,
        repair_source: str = "",
        error: BaseException | None = None,
        response: Any = None,
    ) -> None:
        payload: dict[str, Any] = {
            "stream_id": self.stream_id,
            "operation_id": self.operation_id,
            "task": self.task,
            "attempt": self.attempt,
            "max_attempts": self.max_attempts,
            "output_kind": "json",
            "validation_status": status,
            "repair_source": {"local_repair": "local", "llm_repair": "llm"}.get(
                repair_source, repair_source
            ),
        }
        if error is not None:
            payload.update(error_type=type(error).__name__, error=str(error))
        if response is not None:
            payload.update(
                finish_reason=str(getattr(response, "finish_reason", "") or ""),
                model_id=str(getattr(response, "model_id", "") or ""),
                structured_output_mode=str(getattr(response, "structured_output_mode", "") or ""),
            )
        if data is not None:
            # Authoritative validated snapshot, never a reassembly of UI deltas.
            text = json.dumps(data, ensure_ascii=False)
            payload.update(text=text, text_length=len(text))
        self.on_step("llm_stream_validation", payload)
