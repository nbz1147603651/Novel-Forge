"""Compatibility imports for the shared user-intent projection."""

from __future__ import annotations

from novel_forge.core.user_intent import (
    USER_INTENT_AUTHORITY_ORDER,
    build_user_intent_card,
    candidate_intent_conflicts,
    enforce_explicit_input_on_story_spec,
)

__all__ = (
    "USER_INTENT_AUTHORITY_ORDER",
    "build_user_intent_card",
    "candidate_intent_conflicts",
    "enforce_explicit_input_on_story_spec",
)
