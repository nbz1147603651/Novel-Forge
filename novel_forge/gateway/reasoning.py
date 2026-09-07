"""Provider-neutral reasoning-mode helpers.

The desktop stores one normalized mode while adapters translate it to each
provider's request dialect.  ``thinking`` remains supported as a legacy
boolean, but it is no longer expressive enough to be the source of truth.
"""

from __future__ import annotations

from typing import Final

DISABLED_THINKING_MODES: Final[frozenset[str]] = frozenset(
    {"off", "none", "disabled", "unsupported"}
)
ENABLED_THINKING_MODES: Final[frozenset[str]] = frozenset(
    {
        "on",
        "enabled",
        "adaptive",
        "minimal",
        "low",
        "medium",
        "high",
        "xhigh",
        "max",
        "forced",
    }
)
EFFORT_THINKING_MODES: Final[frozenset[str]] = frozenset(
    {"minimal", "low", "medium", "high", "xhigh", "max"}
)

_ALIASES: Final[dict[str, str]] = {
    "false": "off",
    "true": "on",
    "disable": "off",
    "disabled": "off",
    "enable": "on",
    "enabled": "on",
    "auto": "adaptive",
    "default": "",
    "maximum": "max",
}


def normalize_thinking_mode(value: object, *, thinking: bool = False) -> str:
    """Return a stable application mode, falling back to the legacy flag."""

    raw = str(value or "").strip().lower().replace("-", "_")
    raw = _ALIASES.get(raw, raw)
    if raw in DISABLED_THINKING_MODES | ENABLED_THINKING_MODES:
        return raw
    return "on" if thinking else "off"


def thinking_mode_enabled(value: object, *, thinking: bool = False) -> bool:
    """Whether a normalized mode requests reasoning output/compute."""

    return normalize_thinking_mode(value, thinking=thinking) not in DISABLED_THINKING_MODES


def reasoning_effort_for_mode(value: object) -> str:
    """Return the portable effort value carried by a normalized mode."""

    mode = normalize_thinking_mode(value)
    return mode if mode in EFFORT_THINKING_MODES else ""


__all__ = [
    "DISABLED_THINKING_MODES",
    "EFFORT_THINKING_MODES",
    "ENABLED_THINKING_MODES",
    "normalize_thinking_mode",
    "reasoning_effort_for_mode",
    "thinking_mode_enabled",
]
