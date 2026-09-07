"""POV drift audit service — detects unmarked multi-character interior switching.

Pattern: local candidate rules → LLM adjudication → ReviewFinding + RepairTicket.

Scope: ONLY unmarked multi-character interior perspective switching.
Does NOT enforce full grammar POV, first-person grammar, or omniscient-mode guidelines.
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass, field

from novel_forge.core.review.review_contracts import (
    compile_repair_tickets_from_findings,
    source_text_hash,
)
from novel_forge.core.schemas.review import RepairTicket, ReviewFinding

_logger = logging.getLogger(__name__)

# ── Chinese interior verbs that signal internal perspective access ──
_INTERIOR_VERBS = [
    "想", "觉得", "明白", "意识到", "知道", "害怕", "恨", "疑惑", "看出", "听出",
    "心想", "暗想", "感到", "感觉", "察觉", "领悟", "猜到", "猜出",
    "心中", "心里", "内心", "暗自", "暗道",
]

# Generic fallback regex: character name (2-4 Chinese chars) followed by interior verb.
# NOTE: Greedy {2,4} can eat the first char of a verb (e.g. 心想).
# Use ``_build_interior_pattern`` with known names for accurate matching.
_INTERIOR_PATTERN = re.compile(
    r"([\u4e00-\u9fff]{2,4})\s*(?:" + "|".join(re.escape(v) for v in _INTERIOR_VERBS) + r")"
)

# Paragraph separator
_PARA_SPLIT = re.compile(r"\n\s*\n|\n")

# POV section separator (standard marker in the novel)
_POV_SEPARATOR = "---"


def _build_interior_pattern(known_names: set[str]) -> re.Pattern[str]:
    """Build a regex that matches *known* character names followed by interior verbs.

    Known names are embedded literally (longest-first) so that the greedy
    ``{2,4}`` quantifier cannot swallow the first character of a verb.
    A generic fallback captures any 2–4 char name + verb combination.
    """
    parts: list[str] = []
    # Known-character patterns (longest first to avoid prefix ambiguity)
    for name in sorted(known_names, key=len, reverse=True):
        verbs_alt = "|".join(re.escape(v) for v in _INTERIOR_VERBS)
        parts.append(f"({re.escape(name)})\\s*(?:{verbs_alt})")
    # Generic fallback
    verbs_alt = "|".join(re.escape(v) for v in _INTERIOR_VERBS)
    parts.append(f"([\\u4e00-\\u9fff]{{2,4}})\\s*(?:{verbs_alt})")
    return re.compile("|".join(parts))


def _extract_interior_chars(
    paragraph: str,
    known_names: set[str],
) -> list[tuple[str, int, int]]:
    """Return ``(character_name, match_start, match_end)`` for every interior-access hit."""
    pattern = _build_interior_pattern(known_names)
    results: list[tuple[str, int, int]] = []
    for m in pattern.finditer(paragraph):
        # Pick the first non-None capturing group (known name or generic fallback)
        char_name = ""
        for g in m.groups():
            if g:
                char_name = g.strip()
                break
        if char_name:
            results.append((char_name, m.start(), m.end()))
    return results


@dataclass
class PovDriftCandidate:
    """A candidate POV drift detected by local rules."""

    paragraph_index: int
    paragraph_text: str
    characters_with_interior: list[str]
    """Characters found with interior access in this paragraph."""
    pov_character: str
    """Expected POV character for this segment."""
    evidence_quotes: list[str] = field(default_factory=list)
    """Short text snippets showing the drift."""
    severity: str = "medium"
    """Severity based on scope: 'high' for limited, 'medium' for omniscient."""


@dataclass(frozen=True)
class PovDriftAuditResult:
    """Result of POV drift audit."""

    verdict: str = "pass"
    """'pass' | 'drift_detected' | 'error'."""
    candidates: list[PovDriftCandidate] = field(default_factory=list)
    findings: list[ReviewFinding] = field(default_factory=list)
    repair_tickets: list[RepairTicket] = field(default_factory=list)
    details: str = ""


def detect_pov_drift_candidates(
    text: str,
    *,
    pov_character: str,
    pov_scope: str = "limited",
    known_characters: list[str] | None = None,
) -> list[PovDriftCandidate]:
    """Scan chapter text for paragraphs with unmarked multi-character interior access.

    Parameters
    ----------
    text:
        Full chapter text.
    pov_character:
        Expected POV character for the current segment.
    pov_scope:
        'limited' or 'omniscient'. Limited increases severity.
    known_characters:
        List of known character names for better matching.
    """
    if not text or not pov_character:
        return []

    paragraphs = _PARA_SPLIT.split(text)
    candidates: list[PovDriftCandidate] = []
    known = set(known_characters or [])
    known.add(pov_character)

    for para_idx, paragraph in enumerate(paragraphs):
        paragraph = paragraph.strip()
        if not paragraph or len(paragraph) < 10:
            continue

        # Skip if paragraph contains POV separator (intentional switch)
        if _POV_SEPARATOR in paragraph:
            continue

        # Find all character + interior verb matches using known-character-first patterns
        interior_chars: list[str] = []
        evidence: list[str] = []

        for char_name, m_start, m_end in _extract_interior_chars(paragraph, known):
            if not char_name or char_name == pov_character:
                continue
            # Check if this is a known character
            if known and char_name not in known:
                continue
            interior_chars.append(char_name)
            # Extract short evidence snippet
            start = max(0, m_start - 5)
            end = min(len(paragraph), m_end + 15)
            evidence.append(paragraph[start:end])

        # If multiple non-POV characters have interior access in same paragraph
        unique_others = list(set(interior_chars))
        if len(unique_others) >= 1:
            # Check if POV character ALSO has interior access (mixed POV)
            pov_has_interior = bool(
                re.search(
                    re.escape(pov_character) + r"\s*(?:" + "|".join(re.escape(v) for v in _INTERIOR_VERBS) + r")",
                    paragraph,
                )
            )
            if pov_has_interior or len(unique_others) >= 2:
                severity = "high" if pov_scope == "limited" else "medium"
                candidates.append(
                    PovDriftCandidate(
                        paragraph_index=para_idx,
                        paragraph_text=paragraph[:500],
                        characters_with_interior=unique_others,
                        pov_character=pov_character,
                        evidence_quotes=evidence[:3],
                        severity=severity,
                    )
                )

    return candidates


def findings_from_pov_candidates(
    candidates: list[PovDriftCandidate],
    *,
    chapter_number: int,
    current_text: str,
    source_module: str = "pov_drift_audit",
) -> list[ReviewFinding]:
    """Convert POV drift candidates into ReviewFinding objects."""
    text_hash = source_text_hash(current_text)
    findings: list[ReviewFinding] = []

    for candidate in candidates:
        finding_id = f"pov_drift_{chapter_number}_{candidate.paragraph_index}"
        evidence = "；".join(candidate.evidence_quotes[:2]) if candidate.evidence_quotes else ""
        summary = (
            f"第{candidate.paragraph_index}段：非POV角色 "
            f"{', '.join(candidate.characters_with_interior)} 获得内心视角，"
            f"但POV角色为 {candidate.pov_character}，未见 '---' 切换标记"
        )

        findings.append(
            ReviewFinding(
                finding_id=finding_id,
                chapter_number=chapter_number,
                review_mode="full_review",
                review_round=1,
                source_module=source_module,
                dimension="pov",
                issue_type="unmarked_interior_switch",
                severity=candidate.severity,
                confidence=0.7,
                summary=summary,
                evidence_quote=evidence,
                paragraph_start=candidate.paragraph_index,
                paragraph_end=candidate.paragraph_index,
                anchor_type="paragraph_scope",
                repair_goal=(
                    f"移除非POV角色({', '.join(candidate.characters_with_interior)})"
                    f"的内心描写，或添加 '---' POV切换标记"
                ),
                suggested_mode="window",
                source_text_hash=text_hash,
                signature=hashlib.sha256(
                    f"pov_drift_{candidate.paragraph_index}_{candidate.pov_character}".encode()
                ).hexdigest()[:16],
                metadata={
                    "characters_with_interior": candidate.characters_with_interior,
                    "pov_character": candidate.pov_character,
                },
            )
        )

    return findings


def repair_tickets_from_pov_findings(
    findings: list[ReviewFinding],
) -> list[RepairTicket]:
    """Compile repair tickets from POV drift findings."""
    if not findings:
        return []
    return compile_repair_tickets_from_findings(findings)


async def run_pov_drift_audit(
    *,
    current_text: str,
    chapter_number: int,
    pov_character: str,
    pov_scope: str = "limited",
    known_characters: list[str] | None = None,
) -> PovDriftAuditResult:
    """Run POV drift audit on chapter text.

    Returns candidates as findings with repair tickets.
    LLM adjudication is optional — if no candidates are found, returns early.
    """
    if not current_text or not pov_character:
        return PovDriftAuditResult(
            verdict="pass",
            details="skipped: no text or no pov_character",
        )

    try:
        candidates = detect_pov_drift_candidates(
            current_text,
            pov_character=pov_character,
            pov_scope=pov_scope,
            known_characters=known_characters,
        )

        if not candidates:
            return PovDriftAuditResult(
                verdict="pass",
                details=f"no POV drift candidates in {len(_PARA_SPLIT.split(current_text))} paragraphs",
            )

        findings = findings_from_pov_candidates(
            candidates,
            chapter_number=chapter_number,
            current_text=current_text,
        )
        tickets = repair_tickets_from_pov_findings(findings)

        return PovDriftAuditResult(
            verdict="drift_detected",
            candidates=candidates,
            findings=findings,
            repair_tickets=tickets,
            details=f"{len(candidates)} POV drift candidate(s) detected",
        )
    except Exception as exc:
        _logger.warning(
            "pov_drift_audit_failed | chapter=%d | error=%s",
            chapter_number,
            exc,
        )
        return PovDriftAuditResult(
            verdict="error",
            details=str(exc),
        )
