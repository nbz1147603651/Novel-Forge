"""Local editorial metrics used by chapter checks and whole-book audits."""

from __future__ import annotations

import re
from collections import Counter
from statistics import mean
from typing import Any

from novel_forge.editorial.schemas import EditorialContract, EditorialFinding
from novel_forge.editorial.signals import (
    detect_builtin_expression_overuse,
    expression_profiles_to_records,
)

_EXPLANATION_RE = re.compile(
    r"(意识到|明白|知道|懂得|意味着|代表着|象征着|仿佛在告诉|像是在说|这不是.*而是)"
)
_CONFIRMATION_RE = re.compile(r"(终于|圆满|不会再错过|再也不会|一切都结束|这一次)")
_DIALOGUE_RE = re.compile(r"(?:^|\n)[“\"]([^“”\"\n]{2,160})[”\"]")
_SENTENCE_SPLIT_RE = re.compile(r"[。！？!?；;]\s*")
_PARAGRAPH_SPLIT_RE = re.compile(r"\n\s*\n")  # Precompiled for paragraph splitting

# Cache for symbol regex patterns to avoid repeated re.escape() + re.compile()
_SYMBOL_PATTERN_CACHE: dict[str, re.Pattern[str]] = {}


def chapter_editorial_findings(
    *,
    chapter_number: int,
    chapter_text: str,
    contract: EditorialContract,
) -> list[EditorialFinding]:
    """Return deterministic local editorial findings for one chapter."""

    text = str(chapter_text or "")
    findings: list[EditorialFinding] = []

    expression_records = expression_profiles_to_records(contract.expression_channel_profiles)
    for hit in detect_builtin_expression_overuse(text, threshold=3, records=expression_records):
        findings.append(
            EditorialFinding(
                issue_type="expression_channel_overuse",
                severity="medium",
                chapter_number=chapter_number,
                summary=str(hit.get("reason") or "表达通道重复。"),
                evidence=list(hit.get("matched", []) or []),
                recommendation=str(hit.get("replacement_advice") or ""),
                confidence=0.82,
                metadata={"channel_id": hit.get("channel_id"), "count": hit.get("count")},
            )
        )

    explanation_hits = _dedupe([m.group(0) for m in _EXPLANATION_RE.finditer(text)])
    if len(explanation_hits) >= 4:
        findings.append(
            EditorialFinding(
                issue_type="explanation_density",
                severity="medium",
                chapter_number=chapter_number,
                summary=f"说明性/主题解释信号偏密，共命中 {len(explanation_hits)} 类表达。",
                evidence=explanation_hits[:8],
                recommendation="删去直接解释，改为动作、选择、场景阻力或对白留白。",
                confidence=0.74,
                metadata={"count": len(explanation_hits)},
            )
        )

    symbol_findings = detect_symbol_over_explanation(text=text, contract=contract)
    findings.extend(
        EditorialFinding(
            issue_type="symbol_over_explanation",
            severity="medium",
            chapter_number=chapter_number,
            summary=item["summary"],
            evidence=item["evidence"],
            recommendation="保留物件复现，删除或压缩含义解释。",
            confidence=0.70,
            metadata={"symbol": item["symbol"]},
        )
        for item in symbol_findings
    )

    findings.extend(detect_paragraph_visual_fatigue(chapter_number=chapter_number, text=text))

    confirmation_hits = _dedupe([m.group(0) for m in _CONFIRMATION_RE.finditer(text)])
    forbidden_hits = [
        phrase for phrase in contract.forbidden_confirmation_phrases if phrase and phrase in text
    ]
    if len(confirmation_hits) + len(forbidden_hits) >= 3:
        findings.append(
            EditorialFinding(
                issue_type="confirmation_scene_repetition",
                severity="low",
                chapter_number=chapter_number,
                summary="确认型圆满/主题回环表达偏密。",
                evidence=_dedupe([*confirmation_hits, *forbidden_hits])[:8],
                recommendation="确认场景只保留一次功能峰值，其余改成新行动、新信息或制度化后果。",
                confidence=0.68,
                metadata={"count": len(confirmation_hits) + len(forbidden_hits)},
            )
        )

    voice_finding = detect_voice_convergence(text=text)
    if voice_finding:
        findings.append(
            EditorialFinding(
                issue_type="voice_convergence",
                severity="low",
                chapter_number=chapter_number,
                summary=voice_finding["summary"],
                evidence=voice_finding["evidence"],
                recommendation="按 EditorialContract.character_voices 重写对白节奏和解释倾向。",
                confidence=voice_finding["confidence"],
                metadata=voice_finding,
            )
        )
    return findings


def detect_title_repetition(
    titles: list[str],
    *,
    contract: EditorialContract | None = None,
) -> list[EditorialFinding]:
    """Detect chapter title reuse beyond the project title policy."""

    counts = Counter(title.strip() for title in titles if title and title.strip())
    max_reuse = contract.title_policy.max_reuse if contract else 2
    allowed = set(contract.title_policy.allowed_repeated_titles) if contract else set()
    findings: list[EditorialFinding] = []
    for title, count in counts.items():
        allowed_count = max_reuse if contract is None or title in allowed else 1
        if count <= allowed_count:
            continue
        findings.append(
            EditorialFinding(
                issue_type="title_repetition",
                severity="low",
                summary=f"章节标题「{title}」重复 {count} 次，目录辨识度下降。",
                evidence=[title],
                recommendation="保留核心回环标题，其余改为节点性标题。",
                confidence=0.95,
                metadata={
                    "title": title,
                    "count": count,
                    "max_reuse": max_reuse,
                    "allowed_repeated": title in allowed,
                },
            )
        )
    return findings


def detect_denouement_overrun(
    *,
    completed_chapters: list[int],
    contract: EditorialContract,
) -> list[EditorialFinding]:
    """Detect whether completed chapters have exceeded the main climax aftermath budget."""

    if not completed_chapters:
        return []
    main = contract.main_climax()
    last_chapter = max(completed_chapters)
    over_by = last_chapter - (main.chapter_number + main.expected_aftermath_chapters)
    if over_by <= 0:
        return []
    return [
        EditorialFinding(
            issue_type="denouement_overrun",
            severity="high" if over_by >= 5 else "medium",
            summary=(
                f"主高潮在第 {main.chapter_number} 章，预计余波 "
                f"{main.expected_aftermath_chapters} 章；当前已超出 {over_by} 章。"
            ),
            evidence=[main.description],
            recommendation="压缩高潮后重复确认场景，保留余波后果、关系制度化、终场意象等单次功能节点。",
            confidence=0.90,
            metadata={
                "main_climax_chapter": main.chapter_number,
                "expected_aftermath_chapters": main.expected_aftermath_chapters,
                "last_chapter": last_chapter,
                "over_by": over_by,
            },
        )
    ]


def detect_symbol_over_explanation(
    *,
    text: str,
    contract: EditorialContract,
) -> list[dict[str, Any]]:
    """Detect symbol mentions that are too close to explicit explanatory language."""

    findings: list[dict[str, Any]] = []
    for policy in contract.symbol_policies:
        symbol = policy.symbol.strip()
        if not symbol or symbol not in text:
            continue
        # Use cached compiled pattern to avoid repeated re.escape() + re.compile()
        pattern = _SYMBOL_PATTERN_CACHE.get(symbol)
        if pattern is None:
            pattern = re.compile(re.escape(symbol))
            _SYMBOL_PATTERN_CACHE[symbol] = pattern
        evidence: list[str] = []
        for match in pattern.finditer(text):
            window = text[max(0, match.start() - 50) : min(len(text), match.end() + 80)]
            if _EXPLANATION_RE.search(window):
                evidence.append(window.strip())
        if len(evidence) > policy.max_explicit_explanations:
            findings.append(
                {
                    "symbol": symbol,
                    "summary": f"象征物「{symbol}」附近解释性句子超过预算。",
                    "evidence": _dedupe(evidence)[:6],
                }
            )
    return findings


def detect_voice_convergence(*, text: str) -> dict[str, Any] | None:
    """Lightweight dialogue-shape convergence detector.

    This is intentionally local and conservative: it flags chapters where many
    dialogue lines have nearly identical sentence length and punctuation rhythm.
    """

    lines = [m.group(1).strip() for m in _DIALOGUE_RE.finditer(text)]
    if len(lines) < 8:
        return None
    lengths = [len(line) for line in lines]
    avg = mean(lengths)
    if avg <= 0:
        return None
    similar = sum(1 for length in lengths if abs(length - avg) <= max(6, avg * 0.18))
    question_ratio = sum(1 for line in lines if "？" in line or "?" in line) / len(lines)
    if similar / len(lines) < 0.65 or question_ratio > 0.55:
        return None
    return {
        "summary": "对白句长和标点节奏过于接近，存在角色声纹趋同风险。",
        "evidence": lines[:6],
        "confidence": 0.62,
        "dialogue_count": len(lines),
        "similar_ratio": round(similar / len(lines), 3),
    }


def detect_paragraph_visual_fatigue(
    *,
    chapter_number: int,
    text: str,
    max_chars: int = 520,
    max_sentences: int = 9,
) -> list[EditorialFinding]:
    """Detect prose paragraphs that are visually too dense for publication review."""

    findings: list[EditorialFinding] = []
    for index, paragraph in enumerate(_split_paragraphs(text), start=1):
        paragraph_text = paragraph.strip()
        char_count = len(paragraph_text)
        sentence_count = len([item for item in _SENTENCE_SPLIT_RE.split(paragraph_text) if item])
        if char_count < max_chars and sentence_count < max_sentences:
            continue
        severity = "medium" if char_count >= max_chars * 1.4 or sentence_count >= 14 else "low"
        findings.append(
            EditorialFinding(
                issue_type="paragraph_visual_fatigue",
                severity=severity,
                chapter_number=chapter_number,
                summary=f"第 {index} 段过长，形成整段视觉疲劳。",
                evidence=[_preview_paragraph(paragraph_text)],
                recommendation="按动作转折、对白插入、信息揭示或情绪落点拆成 2-4 个段落。",
                confidence=0.86,
                metadata={
                    "paragraph_index": index,
                    "char_count": char_count,
                    "sentence_count": sentence_count,
                    "max_chars": max_chars,
                    "max_sentences": max_sentences,
                },
            )
        )
    return findings


def editorial_metrics_payload(
    *,
    chapter_text: str,
    contract: EditorialContract,
) -> dict[str, Any]:
    """Return compact numeric metrics for reports."""

    text = str(chapter_text or "")
    sentences = [item for item in _SENTENCE_SPLIT_RE.split(text) if item.strip()]
    explanation_count = len(list(_EXPLANATION_RE.finditer(text)))
    long_paragraphs = detect_paragraph_visual_fatigue(chapter_number=0, text=text)
    return {
        "chars": len(text),
        "sentence_count": len(sentences),
        "explanation_count": explanation_count,
        "explanation_per_100_sentences": round(
            (explanation_count / max(1, len(sentences))) * 100,
            2,
        ),
        "builtin_expression_overuse": detect_builtin_expression_overuse(
            text,
            threshold=3,
            records=expression_profiles_to_records(contract.expression_channel_profiles),
        ),
        "symbol_count": {
            policy.symbol: text.count(policy.symbol)
            for policy in contract.symbol_policies
            if policy.symbol
        },
        "paragraph_visual_fatigue_count": len(long_paragraphs),
    }


def _split_paragraphs(text: str) -> list[str]:
    source = str(text or "").strip()
    if not source:
        return []
    # Use precompiled regex pattern instead of re.split with string pattern
    paragraphs = [part.strip() for part in _PARAGRAPH_SPLIT_RE.split(source) if part.strip()]
    if len(paragraphs) <= 1:
        paragraphs = [part.strip() for part in source.splitlines() if part.strip()]
    return paragraphs


def _preview_paragraph(paragraph: str, limit: int = 160) -> str:
    text = re.sub(r"\s+", " ", paragraph).strip()
    if len(text) <= limit:
        return text
    return f"{text[:limit]}..."


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        item = str(value or "").strip()
        if not item or item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result
