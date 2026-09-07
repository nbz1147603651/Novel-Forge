"""Small utility helpers shared by memory integration modules."""

from __future__ import annotations

from pathlib import Path
from typing import Any


def plan_field(plan: Any, key: str, default: Any = None) -> Any:
    if plan is None:
        return default
    if isinstance(plan, dict):
        return plan.get(key, default)
    return getattr(plan, key, default)


def plan_list_field(plan: Any, key: str) -> list[dict[str, Any]]:
    value = plan_field(plan, key, [])
    if not isinstance(value, list | tuple):
        return []
    result: list[dict[str, Any]] = []
    for item in value:
        if isinstance(item, dict):
            result.append(dict(item))
        elif hasattr(item, "model_dump"):
            dumped = item.model_dump(mode="json")
            if isinstance(dumped, dict):
                result.append(dumped)
        else:
            result.append({"summary": str(item)})
    return result


def safe_load_json(storage: Any, path: Path) -> dict[str, Any]:
    try:
        if storage.exists(path):
            payload = storage.load_json(path)
            return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}
    return {}


__all__ = ("plan_field", "plan_list_field", "safe_load_json")
