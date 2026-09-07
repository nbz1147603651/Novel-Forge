"""Shared evidence anchors for split LLM tasks."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any


def _compact_value(value: Any, *, max_string_chars: int, max_items: int, depth: int) -> Any:
    if depth <= 0:
        return _stringify(value, max_string_chars=max_string_chars)
    if isinstance(value, str):
        text = value.strip()
        return text if len(text) <= max_string_chars else f"{text[:max_string_chars]}..."
    if isinstance(value, Mapping):
        compact: dict[str, Any] = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= max_items:
                compact["__truncated_keys__"] = len(value) - max_items
                break
            compact[str(key)] = _compact_value(
                item,
                max_string_chars=max_string_chars,
                max_items=max_items,
                depth=depth - 1,
            )
        return compact
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray, str)):
        compact_list = [
            _compact_value(
                item,
                max_string_chars=max_string_chars,
                max_items=max_items,
                depth=depth - 1,
            )
            for item in list(value)[:max_items]
        ]
        if len(value) > max_items:
            compact_list.append({"__truncated_items__": len(value) - max_items})
        return compact_list
    if hasattr(value, "model_dump"):
        return _compact_value(
            value.model_dump(mode="json"),
            max_string_chars=max_string_chars,
            max_items=max_items,
            depth=depth - 1,
        )
    return value


def _stringify(value: Any, *, max_string_chars: int) -> str:
    try:
        text = json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)
    except TypeError:
        text = str(value)
    text = text.strip()
    return text if len(text) <= max_string_chars else f"{text[:max_string_chars]}..."


def build_shared_evidence_anchor(
    anchor_name: str,
    evidence: Mapping[str, Any],
    *,
    source_keys: Sequence[str] | None = None,
    policy: str = "所有并行/分片任务必须以这些共享来源为共同依据；维度特需上下文只能补充，不得覆盖锚点事实。",
    max_string_chars: int = 700,
    max_items: int = 8,
    depth: int = 3,
) -> dict[str, Any]:
    """Build a compact shared-evidence block for split prompts."""
    selected_keys = list(source_keys or evidence.keys())
    selected = {key: evidence.get(key) for key in selected_keys if key in evidence}
    raw = json.dumps(selected, ensure_ascii=False, sort_keys=True, default=str)
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
    return {
        "anchor_name": anchor_name,
        "anchor_hash": digest,
        "shared_source_keys": list(selected.keys()),
        "evidence_excerpt": _compact_value(
            selected,
            max_string_chars=max_string_chars,
            max_items=max_items,
            depth=depth,
        ),
        "policy": policy,
    }


__all__ = ["build_shared_evidence_anchor"]
