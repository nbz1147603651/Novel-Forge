"""Patch engine v2 — transactional, verifiable chapter patch execution.

Public API:
    PatchExecutorV2     — batch executor with dry-run, conflict check, failure codes
    PatchOperation      — single patch descriptor
    PatchApplyResult    — batch result with diagnostics
    PatchFailure        — per-patch failure detail
    FailureCode         — enum of failure reasons
    MatchPolicy         — enum of match policies
"""

from novel_forge.core.patch_engine.executor import PatchExecutorV2
from novel_forge.core.patch_engine.models import (
    FailureCode,
    MatchPolicy,
    PatchApplyResult,
    PatchFailure,
    PatchOperation,
)

__all__ = [
    "FailureCode",
    "MatchPolicy",
    "PatchApplyResult",
    "PatchExecutorV2",
    "PatchFailure",
    "PatchOperation",
]
