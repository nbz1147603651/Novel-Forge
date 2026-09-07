"""Pipeline helper utilities for common operations.

This module provides reusable helper functions for pipeline operations,
reducing code duplication and improving maintainability.
"""

from __future__ import annotations

from typing import Any, TypeVar

T = TypeVar("T")


def save_and_emit(
    storage: Any,
    path: Any,
    data: Any,
    event_name: str,
    on_step: Any,
) -> None:
    """Save data to storage and emit progress event.
    
    This is a common pattern in pipeline steps where we need to both
    persist data and notify progress listeners.
    
    Args:
        storage: Storage interface with save_json method
        path: Path to save the data
        data: Data object with model_dump method
        event_name: Name of the event to emit
        on_step: Callback to emit progress events
    """
    storage.save_json(path, data.model_dump(mode="json"))
    on_step(event_name, data)


def validate_threshold(
    value: float,
    threshold: float,
    min_val: float = 0.0,
    max_val: float = 10.0,
) -> bool:
    """Validate that a value meets a threshold.
    
    Args:
        value: The value to check
        threshold: The threshold to compare against
        min_val: Minimum allowed value for clamping
        max_val: Maximum allowed value for clamping
        
    Returns:
        True if value >= threshold (after clamping to valid range)
    """
    clamped_value = max(min_val, min(max_val, float(value)))
    return clamped_value >= float(threshold)


def normalize_threshold(
    raw: object,
    default: float = 7.0,
    min_val: float = 0.0,
    max_val: float = 10.0,
) -> float:
    """Normalize a threshold value to a valid range.
    
    Args:
        raw: Raw threshold value (may be any type)
        default: Default value if raw cannot be converted
        min_val: Minimum allowed value
        max_val: Maximum allowed value
        
    Returns:
        Normalized threshold value clamped to [min_val, max_val]
    """
    from numbers import Real
    
    if isinstance(raw, Real):
        threshold = float(raw)
    else:
        try:
            threshold = float(raw)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            threshold = default
    
    return max(min_val, min(max_val, threshold))


def has_prompt_leaks(report: Any | None) -> bool:
    """Check if a report contains prompt leaks.
    
    Args:
        report: Report object that may have prompt_leaks attribute
        
    Returns:
        True if report has non-empty prompt_leaks
    """
    if report is None:
        return False
    leaks = getattr(report, "prompt_leaks", [])
    return bool(leaks)


def format_repair_actions(actions: list[Any], limit: int = 3) -> list[str]:
    """Format repair actions as strings.
    
    Args:
        actions: List of action objects
        limit: Maximum number of actions to return
        
    Returns:
        List of formatted action strings
    """
    return [str(item).strip() for item in actions if str(item).strip()][:limit]


def join_repair_actions(actions: list[Any], separator: str = "；", limit: int = 3) -> str:
    """Join repair actions into a single string.
    
    Args:
        actions: List of action objects
        separator: String to join actions with
        limit: Maximum number of actions to include
        
    Returns:
        Joined action string
    """
    formatted = format_repair_actions(actions, limit)
    return separator.join(formatted)


def safe_get(
    obj: Any,
    key: str,
    default: Any = None,
) -> Any:
    """Safely get an attribute or dict key with a default.
    
    Args:
        obj: Object to get attribute/key from
        key: Attribute or key name
        default: Default value if not found
        
    Returns:
        Attribute/key value or default
    """
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def safe_bool(
    obj: Any,
    key: str,
    default: bool = False,
) -> bool:
    """Safely get a boolean attribute or dict key.
    
    Args:
        obj: Object to get attribute/key from
        key: Attribute or key name
        default: Default boolean value
        
    Returns:
        Boolean value
    """
    value = safe_get(obj, key, default)
    return bool(value)