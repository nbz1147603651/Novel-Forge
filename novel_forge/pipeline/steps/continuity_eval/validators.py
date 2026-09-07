"""Validator functions for continuity evaluation.

Contains all module-level helper functions for forbidden element detection,
anchor term collection, narrative function inference, and related heuristics.
"""

from __future__ import annotations

import re
from typing import Any

from novel_forge.core.utils.string import carry_forward_text
from novel_forge.obs.logger import get_logger
from novel_forge.pipeline.steps.forbidden_sources import forbidden_base_text

_logger = get_logger(__name__)

_COMPACT_PUNCT_RE = re.compile(
    r"""[，。！？；：、"'（）《》〈〉【】『』「」·,.!?;:()\[\]{}<>\-—~…\s]+"""
)

_GENERIC_SENSORY_CHANNEL_TERMS = frozenset(
    {
        "声音",
        "声响",
        "响声",
        "动静",
        "气味",
        "味道",
        "触感",
    }
)


def _compact_forbidden_text(source: str) -> str:
    return _COMPACT_PUNCT_RE.sub("", source or "")


def _clean_forbidden_text(value: Any) -> str:
    if value is None:
        return ""
    # Structured carry-forward items project their text under ``text``.
    projected = carry_forward_text(value)
    if projected:
        return projected
    return str(value).strip()


_SOFT_FORBIDDEN_MARKERS = frozenset(
    {
        "仅用一次",
        "控制频率",
        "频率控制",
        "可沿用",
        "可复用",
        "有意回环",
        "刻意复用",
        "后续",
        "本章作为",
        "场景锚点",
        "剧情锚点",
    }
)


def _forbidden_base_text(source: str) -> str:
    """Return the semantic head of a forbidden element note.

    Plans often store audit notes such as ``外滩钟楼（场景锚点）——仅用一次``.
    The head term is what must be compared against chapter contracts and
    intentional callbacks; otherwise a required location/prop can be treated as
    a hard-ban because the explanatory suffix prevents contextual filtering.
    """

    return forbidden_base_text(source)


def _has_soft_forbidden_marker(source: str) -> bool:
    text = _clean_forbidden_text(source)
    return any(marker in text for marker in _SOFT_FORBIDDEN_MARKERS)


def _looks_like_rhetorical_imagery(
    element: str,
    rhetorical_hints: frozenset[str] | None = None,
) -> bool:
    text = _clean_forbidden_text(element)
    if not text:
        return False
    hints = rhetorical_hints or frozenset()
    return any(token in text for token in hints)


def _collect_known_anchor_terms(
    *,
    packet: Any | None = None,
    bridge: Any | None = None,
    plan: Any | None = None,
    extra_known_terms: list[str] | None = None,
) -> set[str]:
    terms: set[str] = set()

    def _add(value: Any) -> None:
        text = _clean_forbidden_text(value)
        if text:
            terms.add(text)

    if packet is not None:
        outline = getattr(packet, "chapter_outline", None)
        if outline is not None:
            _add(getattr(outline, "pov_character", ""))
            _add(getattr(outline, "setting", ""))
            for item in list(getattr(outline, "involved_characters", []) or []):
                _add(item)
        for item in list(getattr(packet, "known_characters", []) or []):
            _add(item)
        for profile in list(getattr(packet, "character_profiles", []) or []):
            if isinstance(profile, dict):
                _add(profile.get("name"))
            else:
                _add(getattr(profile, "name", ""))
        previous_exit = getattr(packet, "previous_exit_state", None)
        if previous_exit is not None:
            _add(getattr(previous_exit, "pov", ""))
            _add(getattr(previous_exit, "location", ""))
        bible_data = {"characters": list(getattr(packet, "character_profiles", []) or [])}
        canon_data = getattr(packet, "canon_context", None)
        from novel_forge.pipeline.long.services.anchor_terms import (
            _extract_anchor_terms_from_bible,
        )

        try:
            for term in _extract_anchor_terms_from_bible(bible_data, canon_data):
                _add(term)
        except Exception as exc:
            _logger.warning(
                "Failed to extract anchor terms from bible: %s, falling back to static sets",
                exc,
            )

    if bridge is not None:
        _add(getattr(bridge, "opening_pov", ""))
        _add(getattr(bridge, "opening_location", ""))

    if plan is not None:
        for item in list(getattr(plan, "intentional_callbacks", []) or []):
            _add(item)
            _add(_forbidden_base_text(str(item)))

    for item in list(extra_known_terms or []):
        _add(item)

    return terms


def _collect_anchor_texts(
    *,
    packet: Any | None = None,
    bridge: Any | None = None,
    plan: Any | None = None,
    extra_anchor_texts: list[str] | None = None,
) -> list[str]:
    texts: list[str] = []
    seen: set[str] = set()

    def _add(value: Any) -> None:
        text = _clean_forbidden_text(value)
        if text and text not in seen:
            seen.add(text)
            texts.append(text)

    if packet is not None:
        for item in list(getattr(packet, "must_carry_forward", []) or []):
            _add(item)
        _add(getattr(packet, "previous_chapter_ending", ""))
        outline = getattr(packet, "chapter_outline", None)
        if outline is not None:
            _add(getattr(outline, "goal", ""))
        previous_exit = getattr(packet, "previous_exit_state", None)
        if previous_exit is not None:
            for item in list(getattr(previous_exit, "open_questions", []) or []):
                _add(item)
            for item in list(getattr(previous_exit, "must_carry_forward", []) or []):
                _add(item)

    if bridge is not None:
        _add(getattr(bridge, "action_handoff", ""))
        _add(getattr(bridge, "emotional_carryover", ""))
        _add(getattr(bridge, "bridge_summary", ""))
        for item in list(getattr(bridge, "pending_questions", []) or []):
            _add(item)
        for item in list(getattr(bridge, "opening_acceptance_criteria", []) or []):
            _add(item)
        causal = getattr(bridge, "causal_link", None)
        if causal is not None:
            _add(getattr(causal, "previous_event", ""))
            _add(getattr(causal, "causal_mechanism", ""))
            _add(getattr(causal, "unresolved_question", ""))
            for item in list(getattr(causal, "open_threads", []) or []):
                _add(item)

    if plan is not None:
        _add(getattr(plan, "opening_contract", ""))
        _add(getattr(plan, "closing_contract", ""))
        for item in list(getattr(plan, "required_state_transitions", []) or []):
            _add(item)
        for item in list(getattr(plan, "key_revelations", []) or []):
            _add(item)

    for item in list(extra_anchor_texts or []):
        _add(item)

    return texts


def _is_contextual_anchor_element(
    element: str,
    *,
    packet: Any | None = None,
    bridge: Any | None = None,
    plan: Any | None = None,
    extra_anchor_texts: list[str] | None = None,
    extra_known_terms: list[str] | None = None,
    kinship_terms: frozenset[str] | None = None,
    rhetorical_hints: frozenset[str] | None = None,
) -> bool:
    stripped = _clean_forbidden_text(element)
    if not stripped:
        return False

    kinship = kinship_terms or frozenset()
    if stripped in kinship:
        return True

    known_terms = _collect_known_anchor_terms(
        packet=packet,
        bridge=bridge,
        plan=plan,
        extra_known_terms=extra_known_terms,
    )
    if stripped in known_terms:
        return True

    if _looks_like_rhetorical_imagery(stripped, rhetorical_hints=rhetorical_hints):
        return False

    compact_element = _compact_forbidden_text(stripped)
    if len(compact_element) < 2:
        return False

    for term in known_terms:
        compact_term = _compact_forbidden_text(term)
        if len(compact_term) >= 2 and (
            compact_element in compact_term or compact_term in compact_element
        ):
            return True

    for anchor_text in _collect_anchor_texts(
        packet=packet,
        bridge=bridge,
        plan=plan,
        extra_anchor_texts=extra_anchor_texts,
    ):
        if compact_element in _compact_forbidden_text(anchor_text):
            return True

    return False


def _classify_issue_source(
    forbidden_element: str,
    *,
    motif_context: dict[str, Any] | None = None,
    bible_anchor_terms: list[str] | None = None,
) -> str:
    """Determine the classification source for a forbidden element.

    Priority chain:
      1. "llm" — element came from motif_context (LLM-derived motif tracking)
      2. "rule" — element matched bible_anchor_terms (rule-based bible derivation)
      3. "algorithm" — element came from string matching only (no LLM or bible source)

    Args:
        forbidden_element: The forbidden element text to classify.
        motif_context: Optional motif context dict with active_motifs list.
        bible_anchor_terms: Optional list of bible-derived anchor terms.

    Returns:
        One of: "llm", "rule", "algorithm"
    """
    text = _clean_forbidden_text(forbidden_element)
    if not text:
        return "algorithm"

    # Check motif_context (LLM-derived)
    if motif_context:
        active_motifs = motif_context.get("active_motifs", [])
        if isinstance(active_motifs, list):
            for motif in active_motifs:
                if isinstance(motif, dict):
                    name = _clean_forbidden_text(motif.get("name", ""))
                    if name and name in text:
                        return "llm"

    # Check bible_anchor_terms (rule-based)
    if bible_anchor_terms:
        for term in bible_anchor_terms:
            clean_term = _clean_forbidden_text(term)
            if clean_term and (clean_term in text or text in clean_term):
                return "rule"

    return "algorithm"


def _filter_forbidden_elements(
    elements: list[str],
    *,
    packet: Any | None = None,
    bridge: Any | None = None,
    plan: Any | None = None,
    extra_anchor_texts: list[str] | None = None,
    extra_known_terms: list[str] | None = None,
    motif_context: dict[str, Any] | None = None,
    bible_anchor_terms: list[str] | None = None,
    emotion_keywords: frozenset[str] | None = None,
    kinship_terms: frozenset[str] | None = None,
    rhetorical_hints: frozenset[str] | None = None,
) -> list[str]:
    """Strip non-rhetorical items from a forbidden list.

    Called at detection time so both newly-generated chapters and re-evaluation
    of already-stored plan files are protected by the same rule.

    Priority chain (highest to lowest):
      1. motif_context non-empty and valid → Motif category filtering
      2. bible_anchor_terms non-empty → dynamic address whitelist
      3. registry-derived kinship / rhetorical sets
      4. All unavailable → permissive pass-through (no hard-coded constants)
    """
    active_motifs: list[dict[str, Any]] = []
    if motif_context:
        try:
            raw = motif_context.get("active_motifs", [])
            if isinstance(raw, list):
                active_motifs = [m for m in raw if isinstance(m, dict)]
            else:
                _logger.warning(
                    "motif_context['active_motifs'] has invalid type %s, falling back",
                    type(raw).__name__,
                )
        except Exception as exc:
            _logger.warning(
                "Failed to parse motif_context: %s, falling back",
                exc,
            )

    _RHETORICAL_CATEGORIES = frozenset({"意象", "感官", "颜色", "声音", "动作"})
    _THEMATIC_CATEGORIES = frozenset({"主题", "符号"})

    def _match_motif(text: str) -> dict[str, Any] | None:
        for motif in active_motifs:
            name = _clean_forbidden_text(motif.get("name", ""))
            if name and name in text:
                return motif
        return None

    def _legacy_filter(text: str) -> bool:
        return _is_contextual_anchor_element(
            text,
            packet=packet,
            bridge=bridge,
            plan=plan,
            extra_anchor_texts=extra_anchor_texts,
            extra_known_terms=extra_known_terms or bible_anchor_terms,
            kinship_terms=kinship_terms,
            rhetorical_hints=rhetorical_hints,
        )

    emotion_kw = emotion_keywords or frozenset()
    filtered: list[str] = []
    seen: set[str] = set()

    _rhetorical_frozen = rhetorical_hints or frozenset()

    for item in elements:
        text = _clean_forbidden_text(item)
        if not text or text in emotion_kw:
            continue
        base_text = _forbidden_base_text(text)
        # A bare sensory channel word is too broad to be actionable as a hard
        # forbidden element. Keep concrete phrases such as "声音低沉" or explicit
        # registry hints, but do not flag ordinary narration like "她的声音压低".
        if text in _GENERIC_SENSORY_CHANNEL_TERMS and text not in _rhetorical_frozen:
            continue

        if base_text and base_text != text and _legacy_filter(base_text):
            continue
        if (
            base_text
            and _has_soft_forbidden_marker(text)
            and (_legacy_filter(base_text) or _legacy_filter(text))
        ):
            continue

        if active_motifs:
            matched = _match_motif(text)
            if matched is not None:
                # The motif tracker category is authored/adjudicated by the LLM.
                # Local anchor and suffix rules may protect exact P0 terms in the
                # fallback path, but must not overwrite narrative meaning here.
                category = matched.get("category", "")

                if category in _RHETORICAL_CATEGORIES:
                    if _legacy_filter(text):
                        continue
                elif category in _THEMATIC_CATEGORIES:
                    continue
                else:
                    continue
            elif _legacy_filter(text):
                continue
        elif _legacy_filter(text):
            continue

        if text in seen:
            continue
        seen.add(text)
        filtered.append(text)
    _logger.debug(
        "forbidden_filter_summary | total=%d | passed=%d | filtered_out=%d",
        len(elements),
        len(filtered),
        len(elements) - len(filtered),
    )
    return filtered


def _is_substring_of_known_proper_noun(
    matched_text: str,
    anchor_terms: frozenset[str],
) -> bool:
    """Check if *matched_text* is a true substring of any known anchor term.

    Prevents false positives on compound proper nouns, e.g.:
    - "月光" (forbidden imagery) should NOT trigger when text contains "月光宝盒"
      (a proper noun / artifact name).
    - "王爷" should NOT trigger when text contains "镇北王爷".

    Bidirectional check: matched_text in anchor_term OR anchor_term in matched_text.
    Requires anchor_term to be strictly longer than matched_text for the
    ``matched_text in anchor_term`` direction, to avoid self-matches.

    Args:
        matched_text: The text that was matched by forbidden-element detection.
        anchor_terms: Known proper nouns (character names, locations, artifacts, etc.).

    Returns:
        True if matched_text is a substring of (or contains) a known proper noun.
    """
    if not matched_text or not anchor_terms:
        return False
    for term in anchor_terms:
        if not term or len(term) < 2:
            continue
        # matched_text is a proper substring of a longer anchor term
        if matched_text in term and len(term) > len(matched_text):
            return True
        # anchor_term is a proper substring of matched_text (containment)
        if term in matched_text and len(matched_text) > len(term):
            return True
    return False


def _detect_forbidden_elements(
    text: str,
    forbidden_elements: list[str],
    *,
    packet: Any | None = None,
    bridge: Any | None = None,
    plan: Any | None = None,
    extra_anchor_texts: list[str] | None = None,
    extra_known_terms: list[str] | None = None,
    motif_context: dict[str, Any] | None = None,
    bible_anchor_terms: list[str] | None = None,
    emotion_keywords: frozenset[str] | None = None,
    kinship_terms: frozenset[str] | None = None,
    rhetorical_hints: frozenset[str] | None = None,
) -> list[tuple[str, str]]:
    """Detect forbidden elements with high-precision local matching.

    Returns list of (forbidden_element, matched_text) tuples.
    """
    if not text or not forbidden_elements:
        return []
    # Strip abstract emotion labels before matching — these describe character
    # mental states and must not generate false violations.
    forbidden_elements = _filter_forbidden_elements(
        forbidden_elements,
        packet=packet,
        bridge=bridge,
        plan=plan,
        extra_anchor_texts=extra_anchor_texts,
        extra_known_terms=extra_known_terms,
        motif_context=motif_context,
        bible_anchor_terms=bible_anchor_terms,
        emotion_keywords=emotion_keywords,
        kinship_terms=kinship_terms,
        rhetorical_hints=rhetorical_hints,
    )
    if not forbidden_elements:
        return []

    def _compact(source: str) -> str:
        return _compact_forbidden_text(source)

    def _gap_for_length(length: int) -> int:
        if length <= 4:
            return 1
        if length <= 8:
            return 2
        return 3

    def _max_span_for_length(length: int) -> int:
        return length + max(2, length // 3)

    def _build_relaxed_pattern(element_text: str) -> re.Pattern[str]:
        gap = _gap_for_length(len(element_text))
        parts: list[str] = []
        for idx, ch in enumerate(element_text):
            parts.append(re.escape(ch))
            if idx < len(element_text) - 1:
                parts.append(rf".{{0,{gap}}}")
        return re.compile("".join(parts))

    found: list[tuple[str, str]] = []
    normalized = "".join(text.split())
    compact_text = _compact(text)

    anchor_terms = frozenset(
        _collect_known_anchor_terms(
            packet=packet,
            bridge=bridge,
            plan=plan,
            extra_known_terms=extra_known_terms,
        )
    )

    for element in forbidden_elements:
        if not element or not element.strip():
            continue

        element_head = _forbidden_base_text(element.strip())
        if not element_head:
            continue

        norm_element = "".join(element_head.strip().split())
        compact_element = _compact(norm_element)
        if not compact_element:
            continue

        # Exact hit (ignoring whitespace/punctuation noise).
        if compact_element in compact_text or norm_element in normalized:
            matched = element_head.strip()
            if not _is_substring_of_known_proper_noun(matched, anchor_terms):
                found.append((matched, matched))
            continue

        # Relaxed local variant matching for 4+ char elements only.
        if len(compact_element) >= 4:
            pattern = _build_relaxed_pattern(compact_element)
            match = pattern.search(compact_text)
            if match and (match.end() - match.start()) <= _max_span_for_length(
                len(compact_element)
            ):
                matched_text = compact_text[match.start() : match.end()]
                if not _is_substring_of_known_proper_noun(matched_text, anchor_terms):
                    found.append((element_head.strip(), matched_text))

    return found


def _get_paragraph_index(text: str, matched_text: str) -> int:
    """Return the 0-based paragraph index where matched_text first appears.

    Paragraphs are split on double newlines (\n\n), falling back to single
    newlines if no double newlines exist.
    """
    if not text or not matched_text:
        return 0
    if "\n\n" in text:
        paragraphs = text.split("\n\n")
    else:
        paragraphs = text.split("\n")
    for idx, para in enumerate(paragraphs):
        if matched_text in para:
            return idx
    return 0


def _check_handoff_impact(matched_element: str, action_handoff: str) -> bool:
    """Check whether the matched forbidden element appears in the action_handoff.

    Returns True if removing/replacing the matched element would break the
    承接 (handoff) from the previous chapter.
    """
    if not matched_element or not action_handoff:
        return False
    compact_re = re.compile(r"[，。！？；：、\s]+")
    return compact_re.sub("", matched_element) in compact_re.sub("", action_handoff)


def _check_closing_impact(matched_element: str, closing_contract: str) -> bool:
    """Check whether the matched forbidden element appears in the closing_contract.

    Returns True if removing/replacing the matched element would break the
    closing contract of the chapter.
    """
    if not matched_element or not closing_contract:
        return False
    compact_re = re.compile(r"[，。！？；：、\s]+")
    return compact_re.sub("", matched_element) in compact_re.sub("", closing_contract)


# Environment-related keywords used to detect atmospheric passages.
_ENV_KEYWORDS = frozenset(
    {
        "风",
        "雨",
        "雪",
        "雾",
        "云",
        "月",
        "日",
        "光",
        "影",
        "夜",
        "晨",
        "黄昏",
        "天空",
        "大地",
        "山",
        "河",
        "湖",
        "海",
        "树",
        "花",
        "草",
        "街",
        "巷",
        "灯",
        "烛",
        "火",
        "烟",
        "霜",
        "露",
        "雷",
        "电",
        "星",
        "暗",
        "明",
        "冷",
        "热",
        "暖",
        "寒",
        "凉",
        "温",
        "静",
        "寂",
        "幽",
        "深",
        "远",
    }
)


def _infer_narrative_function(para_idx: int, total_paras: int, context: str) -> str:
    """Infer the narrative function of a forbidden element based on its position and context.

    Pure heuristic — no LLM calls.

    Args:
        para_idx: 0-based paragraph index where the element appears.
        total_paras: Total number of paragraphs in the chapter.
        context: Surrounding text (~50 chars) around the matched element.

    Returns:
        A short Chinese string describing the inferred narrative function.
    """
    # Opening 3 paragraphs →承接上一章余波
    if para_idx < 3:
        return "承接上一章余波"

    # Closing 2 paragraphs → 落实章末契约
    if para_idx >= total_paras - 2:
        return "落实章末契约"

    # Dialogue nearby (quotes in context) → 表达角色情绪
    if "「" in context or "」" in context or '"' in context or "'" in context or "说" in context:
        return "表达角色情绪"

    # Environment description (environment keywords in context) → 营造氛围
    if any(kw in context for kw in _ENV_KEYWORDS):
        return "营造氛围"

    # Default → 暗示心理状态
    return "暗示心理状态"
