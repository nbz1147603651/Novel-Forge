"""Shared extraction of machine-readable diagnostics from job failures."""

from __future__ import annotations

import re
from typing import Any

_PROMPT_TASK_RE = re.compile(
    r"(?:prompt context validation failed for|promptbuilder\.render\()\s*([a-z0-9_]+)",
    re.IGNORECASE,
)


def prompt_task_from_error_payload(payload: dict[str, Any]) -> str:
    """Return the prompt task that actually failed, or an empty string.

    New exceptions expose the task under structured validation context.  Text
    parsing is retained only for already-persisted job history created before
    the structured error payload existed.
    """

    context = payload.get("context")
    if isinstance(context, dict):
        prompt_value = context.get("value")
        if context.get("field") == "prompt_context" and isinstance(prompt_value, dict):
            task = str(prompt_value.get("task") or "").strip()
            if task:
                return task

    raw_chain = payload.get("chain")
    chain = raw_chain if isinstance(raw_chain, list) else []
    haystack = "\n".join(
        [
            *(str(payload.get(key) or "") for key in ("title", "summary", "detail")),
            *(str(item) for item in chain),
        ]
    )
    match = _PROMPT_TASK_RE.search(haystack)
    return match.group(1).lower() if match else ""


__all__ = ["prompt_task_from_error_payload"]
