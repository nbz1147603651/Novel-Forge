"""Script generation phases — independently testable pipeline stages.

Each phase module exposes a single entry-point coroutine (or function) that
receives the shared step infrastructure via the ``step`` parameter and returns
the transformed :class:`DubbingScript`.

Author: novel-forge
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from novel_forge.tts.schemas import (
        DubbingScript as DubbingScript,
    )
    from novel_forge.tts.schemas import (
        VoiceTeamContract as VoiceTeamContract,
    )


@dataclass(frozen=True)
class ScriptCompletenessReport:
    """Result of the script completeness gate evaluation."""

    passed: bool
    spoken_text_coverage: float = 0.0
    """Fraction of rewritable segments that have non-empty spoken_text."""
    emotion_differentiation: float = 0.0
    """Fraction of segments with non-neutral emotion."""
    voice_assignment_coverage: float = 0.0
    """Fraction of dialogue segments with a character_id assigned."""
    failures: list[str] = field(default_factory=list)
    """Human-readable descriptions of gate failures."""


class ScriptCompletenessError(Exception):
    """Raised when a script fails the completeness gate and must not be persisted.

    The ``report`` attribute carries the structured failure details so callers
    (workspace execution layer, API) can surface actionable diagnostics.
    The optional ``script`` attribute carries the failed script for draft
    persistence and human review.
    """

    def __init__(self, report: ScriptCompletenessReport, script: object | None = None) -> None:
        self.report = report
        self.script = script
        summary = "; ".join(report.failures) if report.failures else "completeness gate failed"
        super().__init__(f"配音脚本未通过完整度门禁: {summary}")


__all__ = [
    "ScriptCompletenessError",
    "ScriptCompletenessReport",
]
