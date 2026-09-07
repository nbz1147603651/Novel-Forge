"""Guarded LLM strategy advisor for repeated repair failures."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from novel_forge.common.constants import TaskType
from novel_forge.pipeline.long.services.generation.llm_service import LLMService

_VALID_STRATEGIES = {"patch", "fulltext", "rewrite"}


@dataclass(frozen=True)
class RepairStrategyDiagnosis:
    preferred_strategy: str
    confidence: float
    reason: str
    root_causes: list[str]
    risk_flags: list[str]
    diagnostic_summary: str

    def to_guidance(self) -> dict[str, Any]:
        return {
            "preferred_strategy": self.preferred_strategy,
            "confidence": self.confidence,
            "reason": self.reason,
            "root_causes": list(self.root_causes),
            "risk_flags": list(self.risk_flags),
            "diagnostic_summary": self.diagnostic_summary,
        }


def _high_or_critical_count(issues: list[dict[str, Any]]) -> int:
    return sum(
        1
        for issue in issues
        if str(issue.get("severity", "") or "").lower() in {"high", "critical"}
    )


def _memory_success_rate(memory_guidance: dict[str, Any]) -> float | None:
    raw = memory_guidance.get("success_rate")
    if raw is None:
        stats = memory_guidance.get("stats")
        if isinstance(stats, dict):
            raw = stats.get("success_rate")
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def should_run_strategy_advisor(
    *,
    mode: str,
    repeated_issue_guard: dict[str, Any] | None,
    rollback_history: list[dict[str, Any]],
    memory_guidance: dict[str, Any],
    must_fix_issues: list[dict[str, Any]],
) -> bool:
    normalized_mode = str(mode or "guarded").lower()
    if normalized_mode == "off":
        return False
    if normalized_mode == "always":
        return True
    success_rate = _memory_success_rate(memory_guidance)
    return bool(
        repeated_issue_guard
        or len(rollback_history) >= 2
        or (success_rate is not None and success_rate < 0.3)
        or _high_or_critical_count(must_fix_issues) >= 2
    )


def normalize_strategy_diagnosis(data: Any) -> RepairStrategyDiagnosis | None:
    if not isinstance(data, dict):
        return None
    preferred_strategy = str(data.get("preferred_strategy", "") or "").strip().lower()
    if preferred_strategy not in _VALID_STRATEGIES:
        return None
    try:
        confidence = float(data.get("confidence", 0.0) or 0.0)
    except (TypeError, ValueError):
        return None
    confidence = max(0.0, min(1.0, confidence))
    root_causes_raw = data.get("root_causes")
    risk_flags_raw = data.get("risk_flags")
    root_causes = [str(item) for item in root_causes_raw] if isinstance(root_causes_raw, list) else []
    risk_flags = [str(item) for item in risk_flags_raw] if isinstance(risk_flags_raw, list) else []
    return RepairStrategyDiagnosis(
        preferred_strategy=preferred_strategy,
        confidence=confidence,
        reason=str(data.get("reason", "") or ""),
        root_causes=root_causes,
        risk_flags=risk_flags,
        diagnostic_summary=str(data.get("diagnostic_summary", "") or ""),
    )


async def diagnose_repair_strategy(
    *,
    router: Any,
    builder: Any,
    settings: Any,
    on_step: Callable[[str, Any], None],
    dimension: str,
    chapter_number: int,
    round_number: int,
    current_score: float,
    previous_score: float | None,
    issues: list[dict[str, Any]],
    must_fix_issues: list[dict[str, Any]],
    issue_attempts: dict[str, int],
    rollback_history: list[dict[str, Any]],
    memory_guidance: dict[str, Any],
    repeated_issue_guard: dict[str, Any] | None,
) -> RepairStrategyDiagnosis | None:
    service = LLMService(router=router, builder=builder, on_step=on_step, settings=settings)
    context = {
        "dimension": dimension,
        "chapter_number": chapter_number,
        "round_number": round_number,
        "current_score": current_score,
        "previous_score": previous_score,
        "issues": issues,
        "must_fix_issues": must_fix_issues,
        "issue_attempts": dict(issue_attempts),
        "rollback_history": list(rollback_history),
        "memory_guidance": dict(memory_guidance),
        "repeated_issue_guard": repeated_issue_guard or {},
    }
    result = await service.call_with_retry(
        TaskType.REPAIR_STRATEGY_DIAGNOSE,
        context,
        max_tokens=1200,
        temperature=0.2,
        max_retries=2,
        thinking=False,
    )
    return normalize_strategy_diagnosis(result)
