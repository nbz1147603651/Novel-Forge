"""Post-repair semantic verification: LLM-based check that issues are truly resolved."""

from __future__ import annotations

import logging
from typing import Any

from novel_forge.core.exceptions import ModelGatewayError

_logger = logging.getLogger(__name__)

_FALLBACK_RESULT: dict[str, Any] = {
    "issue_resolved": None,
    "confidence": 0.0,
    "reasoning": "fallback",
}


def _extract_snippet(text: str, max_chars: int = 500) -> str:
    """Extract a representative snippet from repaired text."""
    if not text:
        return ""
    if len(text) <= max_chars:
        return text
    snippet = text[:max_chars]
    last_period = snippet.rfind("。")
    last_question = snippet.rfind("？")
    last_exclaim = snippet.rfind("！")
    break_point = max(last_period, last_question, last_exclaim)
    if break_point > max_chars // 3:
        return snippet[: break_point + 1]
    return snippet + "…"


async def verify_issue_resolved_semantic(
    *,
    issue_description: str,
    issue_evidence: str,
    repaired_text: str,
    repair_action: str = "",
    runtime: Any,
    max_tokens: int = 512,
    temperature: float = 0.1,
) -> dict[str, Any]:
    """Call LLM to verify whether a repaired issue is semantically resolved.

    Args:
        issue_description: Original issue description.
        issue_evidence: Original evidence snippet that demonstrated the issue.
        repaired_text: The repaired text snippet to evaluate.
        repair_action: Description of the repair action taken (optional).
        runtime: RuntimeServices instance providing router, builder, settings.
        max_tokens: Maximum output tokens for the verification call.
        temperature: Sampling temperature (low for deterministic output).

    Returns:
        dict with keys: issue_resolved (bool|None), confidence (float), reasoning (str).
        On LLM failure, returns {"issue_resolved": None, "confidence": 0.0, "reasoning": "fallback"}.
    """
    from novel_forge.core.constants import TaskType
    from novel_forge.pipeline.steps.base import PipelineStep

    settings = runtime.settings

    if not getattr(settings, "long_book_audit_semantic_verify_enabled", False):
        _logger.debug("repair_semantic_verify: disabled by config, skipping")
        return dict(_FALLBACK_RESULT)

    budget = getattr(settings, "long_book_audit_semantic_check_budget_tokens", 5000)
    if budget <= 0:
        _logger.debug("repair_semantic_verify: budget exhausted, skipping")
        return dict(_FALLBACK_RESULT)

    ctx: dict[str, Any] = {
        "issue_description": issue_description,
        "issue_evidence": issue_evidence,
        "repaired_text": _extract_snippet(repaired_text, max_chars=800),
        "repair_action": repair_action,
    }

    class _SemanticVerifyStep(PipelineStep[dict[str, Any], Any]):
        @property
        def step_name(self) -> str:
            return "repair_semantic_verify"

        async def _execute(self, input_data: Any) -> Any:
            return await self._call_with_retry(
                TaskType.REPAIR_SEMANTIC_VERIFY,
                input_data,
                max_tokens=max_tokens,
                temperature=temperature,
                required_keys=("issue_resolved", "confidence", "reasoning"),
            )

    step = _SemanticVerifyStep(
        runtime.router,
        runtime.builder,
        settings=settings,
        trace=None,
    )

    try:
        result = await step._execute(ctx)
    except (ModelGatewayError, Exception) as exc:
        _logger.warning(
            "repair_semantic_verify: LLM call failed (%s), falling back to string matching",
            exc,
        )
        return dict(_FALLBACK_RESULT)

    if not isinstance(result, dict):
        _logger.warning(
            "repair_semantic_verify: unexpected result type %s, falling back", type(result)
        )
        return dict(_FALLBACK_RESULT)

    issue_resolved = result.get("issue_resolved")
    confidence = result.get("confidence", 0.0)
    reasoning = result.get("reasoning", "")

    if not isinstance(issue_resolved, bool):
        if isinstance(issue_resolved, str):
            issue_resolved = issue_resolved.strip().lower() in ("true", "yes", "1")
        else:
            _logger.warning("repair_semantic_verify: invalid issue_resolved type, falling back")
            return dict(_FALLBACK_RESULT)

    try:
        confidence = float(confidence)
        confidence = max(0.0, min(1.0, confidence))
    except (TypeError, ValueError):
        confidence = 0.0

    if not isinstance(reasoning, str):
        reasoning = str(reasoning) if reasoning else ""

    return {
        "issue_resolved": issue_resolved,
        "confidence": confidence,
        "reasoning": reasoning[:500],  # Cap reasoning length
    }


def verify_issue_resolved_string_match(
    issue_evidence: str,
    repaired_text: str,
) -> dict[str, Any]:
    """Fallback string-matching verification (existing behavior).

    Checks whether the original evidence still appears in the repaired text.
    This is the non-LLM fallback that continues when semantic verification
    is disabled or the LLM call fails.

    Args:
        issue_evidence: Original evidence snippet.
        repaired_text: The repaired text.

    Returns:
        dict with keys: issue_resolved (bool|None), confidence (float), reasoning (str).
    """
    import re

    if not issue_evidence or len(issue_evidence) < 2:
        return {
            "issue_resolved": True,
            "confidence": 0.5,
            "reasoning": "evidence too short to verify",
        }

    norm_evidence = re.sub(r"\s+", "", issue_evidence)
    norm_text = re.sub(r"\s+", "", repaired_text)

    if norm_evidence not in norm_text:
        return {
            "issue_resolved": True,
            "confidence": 0.6,
            "reasoning": "evidence string no longer present in repaired text",
        }

    return {
        "issue_resolved": False,
        "confidence": 0.7,
        "reasoning": f"evidence still present: {issue_evidence[:60]}",
    }


async def verify_repair_semantic(
    *,
    issue: dict[str, Any],
    repaired_text: str,
    runtime: Any,
) -> dict[str, Any]:
    """High-level entry point: verify a single repair result.

    Tries LLM semantic verification first (if enabled), falls back to
    string matching if LLM fails or is disabled.

    Args:
        issue: The original issue dict with description, evidence, etc.
        repaired_text: The repaired text to verify.
        runtime: RuntimeServices instance.

    Returns:
        dict with keys: issue_resolved, confidence, reasoning, method_used.
        method_used is "semantic" or "string_match".
    """
    issue_description = str(issue.get("description", "") or "")
    issue_evidence = str(issue.get("evidence", "") or "")
    repair_action = str(issue.get("fix_action", "") or "")

    if not issue_description:
        return {
            "issue_resolved": None,
            "confidence": 0.0,
            "reasoning": "no issue description available",
            "method_used": "fallback",
        }

    # Try semantic verification first
    semantic_result = await verify_issue_resolved_semantic(
        issue_description=issue_description,
        issue_evidence=issue_evidence,
        repaired_text=repaired_text,
        repair_action=repair_action,
        runtime=runtime,
    )

    if semantic_result["issue_resolved"] is not None:
        semantic_result["method_used"] = "semantic"
        return semantic_result

    # Fallback to string matching
    string_result = verify_issue_resolved_string_match(issue_evidence, repaired_text)
    string_result["method_used"] = "string_match"
    return string_result
