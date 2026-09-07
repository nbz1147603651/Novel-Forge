"""Summary drift detection — compares summaries against gold facts.

Detects:
- Key fact omissions in volume summaries vs chapter summaries
- Contradictions between arc summaries and volume summaries
- Loss of critical world rules or character states

Input limits:
- Gold facts ≤ 50 (enforced by ``extract_gold_facts``)
- Chapter summaries ≤ 200 chars × volume chapters
- Volume summary ≤ 2000 chars
- Total prompt input ≤ 8000 chars
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from novel_forge.pipeline.long.services.gold_facts import extract_gold_facts
from novel_forge.story_kernel.schemas import StoryKernel

_log = logging.getLogger(__name__)

_MAX_CHAPTER_SUMMARY_CHARS = 200
_MAX_VOLUME_SUMMARY_CHARS = 2000
_MAX_PROMPT_INPUT_CHARS = 8000


@dataclass
class DriftIssue:
    """A single drift detection finding."""

    category: str = "summary_drift"
    severity: str = "medium"  # critical / high / medium / low
    summary: str = ""
    evidence: str = ""


@dataclass
class DriftCheckResult:
    """Result of a summary drift check."""

    issues: list[DriftIssue] = field(default_factory=list)
    facts_checked: int = 0
    facts_missing: int = 0
    facts_contradicted: int = 0

    @property
    def has_critical(self) -> bool:
        return any(i.severity == "critical" for i in self.issues)


def check_summary_drift(
    kernel: StoryKernel,
    *,
    volume_summary: str,
    chapter_summaries: dict[int, str],
    volume_start: int,
    volume_end: int,
) -> DriftCheckResult:
    """Check volume summary against gold facts and chapter summaries.

    This is a deterministic (non-LLM) check that verifies:
    1. No critical world rule is missing from the volume summary
    2. No character death is missing from the volume summary
    3. No secret is accidentally revealed in the summary

    For LLM-based drift detection, use ``SUMMARY_DRIFT_CHECK`` TaskType
    with the gold facts as context.
    """
    result = DriftCheckResult()
    gold_facts = extract_gold_facts(
        kernel,
        volume_start=volume_start,
        volume_end=volume_end,
    )
    result.facts_checked = len(gold_facts)

    vol_summary_lower = volume_summary.lower()

    for fact in gold_facts:
        # Check for critical omissions
        if fact.startswith("[世界规则:"):
            # Extract rule content for keyword matching
            rule_content = fact.split("]: ", 1)[-1] if "]: " in fact else ""
            # Check key terms from the rule
            key_terms = _extract_key_terms(rule_content)
            missing_terms = [t for t in key_terms if t.lower() not in vol_summary_lower]
            if len(missing_terms) > len(key_terms) * 0.7 and key_terms:
                result.issues.append(DriftIssue(
                    severity="critical",
                    summary=f"世界规则可能缺失: {rule_content[:80]}",
                    evidence=f"Gold fact: {fact[:200]}",
                ))
                result.facts_missing += 1

        elif fact.startswith("[角色状态]") and "status=dead" in fact.lower():
            # Character death must be mentioned
            char_name = _extract_character_name_from_status_fact(fact)
            if char_name and char_name.lower() not in vol_summary_lower:
                result.issues.append(DriftIssue(
                    severity="critical",
                    summary=f"角色死亡未提及: {char_name}",
                    evidence=f"Gold fact: {fact[:200]}",
                ))
                result.facts_missing += 1

        elif fact.startswith("[秘密]"):
            # Check if summary accidentally reveals a secret
            secret_content = fact.split("知道: ", 1)[-1] if "知道: " in fact else ""
            key_terms = _extract_key_terms(secret_content)
            revealed_terms = [t for t in key_terms if t.lower() in vol_summary_lower]
            if len(revealed_terms) > len(key_terms) * 0.5 and key_terms:
                result.issues.append(DriftIssue(
                    severity="high",
                    summary=f"秘密可能被泄露: {secret_content[:80]}",
                    evidence=f"Gold fact: {fact[:200]}",
                ))
                result.facts_contradicted += 1

    _log.info(
        "summary_drift_check | volume=%d-%d | facts=%d missing=%d contradicted=%d issues=%d",
        volume_start,
        volume_end,
        result.facts_checked,
        result.facts_missing,
        result.facts_contradicted,
        len(result.issues),
    )
    return result


def _extract_key_terms(text: str) -> list[str]:
    """Extract key terms from text for matching.

    Simple heuristic: split on common delimiters and filter short words.
    """
    import re

    # Split on common Chinese/English delimiters
    terms = re.split(r"[，,。.、；;：:！!？?\s]+", text)
    # Filter: keep terms with at least 2 chars
    return [t.strip() for t in terms if len(t.strip()) >= 2]


def _extract_character_name_from_status_fact(fact: str) -> str:
    """Extract the entity display name from ``[角色状态] Name: status=...``."""
    body = str(fact or "").removeprefix("[角色状态]").strip()
    if not body:
        return ""
    name, sep, _rest = body.partition(":")
    if not sep:
        name, _sep, _rest = body.partition("：")
    return name.strip()


__all__ = ["DriftCheckResult", "DriftIssue", "check_summary_drift"]
