"""Continuity evaluation step — split module.

Re-exports ContinuityEvalInput and ContinuityEvalStep for backward compatibility.
"""

from __future__ import annotations

from novel_forge.pipeline.steps.continuity_eval.context import ContinuityEvalInput
from novel_forge.pipeline.steps.continuity_eval.core import ContinuityEvalStep

__all__ = ["ContinuityEvalInput", "ContinuityEvalStep"]
