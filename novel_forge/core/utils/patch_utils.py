"""Surgical patch utilities for chapter-level text repair.

Instead of sending the full chapter to the LLM and getting the full chapter back,
patch-mode works in three steps:

1. **Locate** — resolve each issue's location hint or evidence text into paragraph indices.
2. **Window** — extract those paragraphs + context_size neighbours as a short text block.
3. **Apply** — after the LLM returns {original, replacement} pairs, replace each exact
   substring in the full chapter text (string replace, no regex).

Token savings vs. full-text repair (3000-char chapter):
- Full: ~4500 tok in + ~4500 tok out ≈ 9000 total
- Patch (3 issues, 4 paras each): ~1800 tok in + ~400 tok out ≈ 2200 total  (~75 % less)
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from novel_forge.core.patch_engine.models import PatchOperation

__all__ = [
    "split_paragraphs",
    "resolve_location_hint",
    "resolve_paragraph_locally",
    "extract_context_window",
    "apply_patches",
    "build_issue_windows",
    "patches_from_dicts",
    "recalibrate_issue_anchors",
    "normalize_post_patch_punctuation",
]


def split_paragraphs(text: str) -> list[str]:
    """Split chapter text into paragraphs.

    Handles both double-newline separation (standard) and single-newline
    Chinese novel format (each dialogue or indented line is its own paragraph).
    """
    # Primary split: blank lines
    chunks = re.split(r"\n{2,}", text.strip())
    paragraphs: list[str] = []
    for chunk in chunks:
        chunk = chunk.strip()
        if chunk:
            paragraphs.append(chunk)
    # Fallback: if the whole text came back as one block, split on single newlines
    if len(paragraphs) == 1 and "\n" in paragraphs[0]:
        lines = [ln.strip() for ln in paragraphs[0].splitlines() if ln.strip()]
        if len(lines) > 1:
            return lines
    return paragraphs if paragraphs else [text.strip()]


def resolve_location_hint(paragraphs: list[str], hint: str) -> list[int]:
    """Convert a vague location description to a list of paragraph indices (0-based).

    Supported patterns:
    - "开头"/"开头段落"            → [0, 1]
    - "开头1-2段" / "开头N段"      → [0 … N-1]
    - "结尾"/"结尾部分"/"结束"     → [n-2, n-1]
    - "第N段"                       → [N-1]
    - "中间"/"中段"                 → [mid-1, mid, mid+1]
    - anything else               → [0]   (safe fallback = opening)
    """
    hint = (hint or "").strip()
    n = len(paragraphs)
    if not hint or n == 0:
        return [0]

    # 开头 N–M 段 or 开头 N 段
    m = re.search(r"开头.*?(\d+)\s*[－–\-~]\s*(\d+)?\s*段", hint)
    if m:
        end_idx = int(m.group(2) or m.group(1)) - 1
        return list(range(min(end_idx + 1, n)))

    # 结尾 N–M 段 or 结尾 N 段（取最后 M 段）
    m = re.search(r"(?:结尾|末尾|尾段).*?(\d+)\s*[－–\-~]\s*(\d+)?\s*段", hint)
    if m:
        tail_count = int(m.group(2) or m.group(1))
        tail_count = max(1, min(tail_count, n))
        return list(range(n - tail_count, n))

    if "开头" in hint:
        return [0, min(1, n - 1)]

    if any(kw in hint for kw in ("结尾", "结束", "末尾")):
        return [max(0, n - 2), n - 1]

    # 第 N-M 段
    m = re.search(r"第\s*(\d+)\s*[－–\-~]\s*(\d+)\s*段", hint)
    if m:
        start = int(m.group(1)) - 1
        end = int(m.group(2)) - 1
        start = max(0, min(start, n - 1))
        end = max(0, min(end, n - 1))
        if start > end:
            start, end = end, start
        return list(range(start, end + 1))

    # 第 N 段
    m = re.search(r"第\s*(\d+)\s*段", hint)
    if m:
        idx = int(m.group(1)) - 1
        return [max(0, min(idx, n - 1))]

    if any(kw in hint for kw in ("中间", "中段", "中部")):
        mid = n // 2
        return [max(0, mid - 1), mid, min(n - 1, mid + 1)]

    return [0]


def _normalize_chars(text: str) -> str:
    """Strip non-CJK/alpha-numeric chars and lowercase for matching."""
    return re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]", "", str(text or "")).lower()


def _char_ngrams(text: str, n: int = 3) -> set[str]:
    """Return character n-gram set from pre-normalised text."""
    return {text[i : i + n] for i in range(len(text) - n + 1)} if len(text) >= n else set()


def resolve_paragraph_locally(
    paragraphs: list[str],
    *,
    evidence: str = "",
    location: str = "",
    llm_hint: int = 0,
) -> tuple[list[int], str, float]:
    """Unified local paragraph resolver used by all repair pipelines.

    Combines the best strategies from patch_utils, causal_repair_step, and
    book_consistency_step into a single entry point.

    Returns ``(target_indices_0based, anchor_type, confidence)``.

    Resolution priority:
    1. Evidence exact substring (normalised, ≥4 chars → conf 0.95; 2-3 chars unambiguous ≤3 hits → conf 0.75)
    2. Evidence 3-gram fuzzy overlap ≥ 0.25 → confidence = min(0.80, overlap)
    3. Location string parsing ("第N段") → confidence 0.85
    4. Location keyword search in paragraphs → confidence 0.50
    5. LLM hint (if valid range) → confidence 0.25
    6. Fallback → [0], confidence 0.1
    """
    n = len(paragraphs)
    if not paragraphs:
        return [], "none", 0.0

    # ── Phase 1: evidence exact substring ──────────────────────────────
    if evidence:
        norm_ev = _normalize_chars(evidence)
        if len(norm_ev) >= 2:
            exact_hits: list[int] = []
            for i, para in enumerate(paragraphs):
                if norm_ev in _normalize_chars(para):
                    exact_hits.append(i)
            if exact_hits:
                # Short evidence (2-3 chars) is only reliable when unambiguous (≤3 matches).
                # High-frequency names that appear in 4+ paragraphs cannot pinpoint location.
                if len(norm_ev) < 4 and len(exact_hits) > 3:
                    pass  # too ambiguous — fall through to next phases
                else:
                    # Prefer the hit closest to LLM hint (0-based)
                    hint0 = max(0, llm_hint - 1) if llm_hint > 0 else 0
                    exact_hits.sort(key=lambda idx: abs(idx - hint0))
                    conf = 0.95 if len(norm_ev) >= 4 else 0.75
                    return exact_hits[:1], "evidence_exact", conf

    # ── Phase 2: evidence 3-gram fuzzy overlap ─────────────────────────
    if evidence:
        norm_ev = _normalize_chars(evidence)
        ev_grams = _char_ngrams(norm_ev, 3)
        if len(ev_grams) >= 3:
            scored: list[tuple[float, int]] = []
            for i, para in enumerate(paragraphs):
                p_grams = _char_ngrams(_normalize_chars(para), 3)
                if not p_grams:
                    continue
                overlap = len(ev_grams & p_grams) / len(ev_grams)
                if overlap >= 0.25:
                    scored.append((overlap, i))
            if scored:
                scored.sort(key=lambda x: -x[0])
                best_score = scored[0][0]
                # Include paragraphs within 80% of the best as potential span
                threshold = best_score * 0.80
                targets = sorted(idx for sc, idx in scored if sc >= threshold)
                return targets, "evidence_fuzzy", min(0.80, best_score)

    # ── Phase 3: location "第N段" parsing ──────────────────────────────
    if location:
        parsed = resolve_location_hint(paragraphs, location)
        if parsed != [0] or "开头" in location:
            # resolve_location_hint returns [0] as generic fallback — avoid using
            # it unless context actually says "开头"
            has_explicit = bool(re.search(r"第\s*\d+\s*段", location))
            conf = 0.85 if has_explicit else 0.60
            return parsed, "location_parsed", conf

    # ── Phase 4: location keywords in paragraph text ───────────────────
    if location:
        keywords = [w for w in re.split(r"[，。、；\s（）]+", location) if len(w) >= 2]
        if keywords:
            best_idx = -1
            best_hits = 0
            for i, para in enumerate(paragraphs):
                hits = sum(1 for kw in keywords if kw in para)
                if hits > best_hits:
                    best_hits = hits
                    best_idx = i
            if best_idx >= 0 and best_hits >= 1:
                return [best_idx], "location_keyword", 0.50

    # ── Phase 5: LLM hint (trust-but-verify) ──────────────────────────
    if llm_hint > 0:
        idx0 = llm_hint - 1  # 1-based → 0-based
        if 0 <= idx0 < n:
            return [idx0], "llm_hint", 0.25

    # ── Phase 6: fallback ─────────────────────────────────────────────
    return [0], "fallback", 0.10


def extract_context_window(
    paragraphs: list[str],
    target_indices: list[int],
    context_size: int = 2,
) -> tuple[str, int, int]:
    """Return (window_text, first_para_idx, last_para_idx).

    Expands target indices by *context_size* paragraphs on each side so the LLM
    has enough narrative context to write naturally without seeing the full chapter.
    """
    if not target_indices or not paragraphs:
        return "\n\n".join(paragraphs), 0, len(paragraphs) - 1

    first = max(0, min(target_indices) - context_size)
    last = min(len(paragraphs) - 1, max(target_indices) + context_size)
    window = paragraphs[first : last + 1]
    return "\n\n".join(window), first, last


# ── Normalized (fuzzy) patch matching helpers ─────────────────────────────────

# Translate common full-width punctuation to ASCII equivalents for comparison.
_PUNCT_NORMALIZE = str.maketrans({
    '\uff0c': ',', '\u3002': '.', '\uff01': '!', '\uff1f': '?',
    '\uff1a': ':', '\uff1b': ';', '\u2018': "'", '\u2019': "'",
    '\u201c': '"', '\u201d': '"', '\u2026': '...', '\u300c': '[',
    '\u300d': ']', '\u300e': '[', '\u300f': ']',
})


def _norm_for_match(text: str) -> str:
    """Strip whitespace and normalize punctuation for fuzzy matching."""
    return "".join(text.split()).translate(_PUNCT_NORMALIZE)


def _normalized_replace(text: str, original: str, replacement: str) -> str | None:
    """Find *original* in *text* using normalized comparison and replace first occurrence.

    Builds a parallel mapping of (original_position → normalized_char) so that the
    match position in normalized space can be mapped back to the original text.
    Returns the modified text, or ``None`` if the normalized original is not found.

    This covers the common LLM failure mode where the LLM returns the original
    passage with minor punctuation or whitespace differences, causing exact-match
    to fail and triggering a costly full-text repair fallback.
    """
    norm_orig = _norm_for_match(original)
    if not norm_orig:
        return None

    # Build list of (start_idx_in_text, normalized_char) for non-whitespace chars.
    norm_map: list[tuple[int, str]] = []
    for i, ch in enumerate(text):
        norm_ch = _norm_for_match(ch)
        if norm_ch:
            norm_map.append((i, norm_ch))

    norm_text = "".join(c for _, c in norm_map)
    ni = norm_text.find(norm_orig)
    if ni < 0:
        return None

    orig_start = norm_map[ni][0]
    orig_end = norm_map[ni + len(norm_orig) - 1][0] + 1
    return text[:orig_start] + replacement + text[orig_end:]


# ─────────────────────────────────────────────────────────────────────────────


def apply_patches(
    text: str,
    patches: list[dict[str, Any]],
    *,
    paragraphs: list[str] | None = None,
) -> tuple[str, int]:
    """Apply a list of ``{original, replacement}`` patches to *text*.

    Returns ``(revised_text, num_applied)``.

    **Paragraph-anchored mode** (preferred): if a patch has ``para_start`` and
    ``para_end`` keys, the ``original`` search is restricted to only those
    paragraphs.  This prevents the "first match anywhere" problem when the same
    phrase appears in multiple paragraphs.

    **Editable-subwindow mode**: if a patch additionally provides
    ``editable_para_start`` / ``editable_para_end``, the replacement is only
    allowed inside that narrower subwindow; context paragraphs inside the outer
    window remain read-only.

    **Legacy mode**: if no paragraph anchor is provided, falls back to global
    ``str.replace(..., 1)`` on the full text.

    Only patches whose ``original`` is an exact substring are applied.
    Missing patches are silently skipped — callers can inspect
    ``num_applied`` vs ``len(patches)`` to detect failures.
    """
    applied = 0
    result = text
    # Pre-split paragraphs once for anchored patches
    if paragraphs is None:
        paragraphs = split_paragraphs(text) if any(
            "para_start" in p for p in patches if isinstance(p, dict)
        ) else None

    for patch in patches:
        original = (patch.get("original") or "").strip()
        replacement = patch.get("replacement") or ""
        if not original:
            continue

        p_start = patch.get("para_start")
        p_end = patch.get("para_end")
        e_start = patch.get("editable_para_start")
        e_end = patch.get("editable_para_end")

        if paragraphs is not None and p_start is not None and p_end is not None:
            # ── Paragraph-anchored application ──
            # Only search within the specified paragraph window
            try:
                p_start = max(0, int(p_start))
                p_end = min(len(paragraphs) - 1, int(p_end))
            except (TypeError, ValueError):
                p_start, p_end = 0, len(paragraphs) - 1

            # Optional editable subwindow (context paragraphs read-only)
            try:
                if e_start is not None and e_end is not None:
                    e_start = max(p_start, int(e_start))
                    e_end = min(p_end, int(e_end))
                else:
                    e_start, e_end = p_start, p_end
            except (TypeError, ValueError):
                e_start, e_end = p_start, p_end

            if e_start > e_end:
                e_start, e_end = p_start, p_end

            editable_window = "\n\n".join(paragraphs[e_start : e_end + 1])
            if original in editable_window:
                revised_editable = editable_window.replace(original, replacement, 1)
            else:
                # Normalized fallback: ignore punctuation/whitespace differences.
                revised_editable = _normalized_replace(editable_window, original, replacement)  # type: ignore[assignment]
                if revised_editable is None:
                    # Not found even with normalization — skip this patch
                    continue
            prefix_ctx = "\n\n".join(paragraphs[p_start:e_start])
            suffix_ctx = "\n\n".join(paragraphs[e_end + 1 : p_end + 1])
            revised_window_parts = [p for p in (prefix_ctx, revised_editable, suffix_ctx) if p]
            revised_window = "\n\n".join(revised_window_parts)
            # Reconstruct full text with the modified anchored window
            before = "\n\n".join(paragraphs[:p_start])
            after = "\n\n".join(paragraphs[p_end + 1 :])
            parts = [p for p in (before, revised_window, after) if p]
            result = "\n\n".join(parts)
            # Re-split for subsequent patches
            paragraphs = split_paragraphs(result)
            applied += 1
            # If not found in editable window, DON'T search globally — skip
            continue

        # ── Legacy global application ──
        if original in result:
            result = result.replace(original, replacement, 1)
        else:
            # Normalized fallback for legacy mode
            norm_result = _normalized_replace(result, original, replacement)
            if norm_result is None:
                continue
            result = norm_result
        applied += 1

    result = normalize_post_patch_punctuation(result)
    return result, applied


def build_issue_windows(
    paragraphs: list[str],
    issues: list[Any],
    context_size: int = 2,
) -> list[dict[str, Any]]:
    """For each issue, resolve its location and extract a paragraph window.

    Returns a list of dicts::

        {
            "issue":       <original issue object>,
            "window_text": str,   # extracted paragraphs for LLM
            "para_start":  int,   # first paragraph index included (0-based)
            "para_end":    int,   # last paragraph index included (0-based)
            "editable_para_start": int,  # exact target span start (0-based)
            "editable_para_end": int,    # exact target span end (0-based)
            "target_indices": list[int],  # the actual target paragraph indices
        }

    Priority for location resolution:
    1. ``evidence`` field (exact text excerpt) — search paragraphs for a match
    2. ``location`` field (vague hint like "开头1-2段")
    3. Default → opening paragraph
    """
    result: list[dict[str, Any]] = []
    for issue in issues:
        evidence: str = (
            getattr(issue, "evidence_quote", "")
            or getattr(issue, "evidence", "")
            or ""
        ).strip()
        location: str = (getattr(issue, "location", "") or "").strip()
        para_start = getattr(issue, "paragraph_start", 0) or 0
        para_end = getattr(issue, "paragraph_end", 0) or 0
        target_indices: list[int]
        anchor_type: str
        location_confidence: float

        if para_start:
            try:
                start0 = max(0, min(int(para_start) - 1, len(paragraphs) - 1))
                end_source = int(para_end) if para_end else int(para_start)
                end0 = max(0, min(end_source - 1, len(paragraphs) - 1))
            except (TypeError, ValueError):
                start0 = end0 = 0
            if start0 > end0:
                start0, end0 = end0, start0
            target_indices = list(range(start0, end0 + 1))
            anchor_type = getattr(issue, "anchor_type", "") or "explicit_para"
            try:
                location_confidence = float(
                    getattr(issue, "location_confidence", 0.0) or 0.92
                )
            except (TypeError, ValueError):
                location_confidence = 0.92
        else:
            target_indices, anchor_type, location_confidence = resolve_paragraph_locally(
                paragraphs,
                evidence=evidence,
                location=location,
            )

        cleaned_targets = sorted(
            {
                max(0, min(int(idx), len(paragraphs) - 1))
                for idx in target_indices
            }
        ) or [0]

        window_text, p_start, p_end = extract_context_window(
            paragraphs, cleaned_targets, context_size=context_size
        )
        result.append(
            {
                "issue": issue,
                "window_text": window_text,
                "para_start": p_start,
                "para_end": p_end,
                "editable_para_start": min(cleaned_targets),
                "editable_para_end": max(cleaned_targets),
                "target_indices": cleaned_targets,
                "anchor_type": anchor_type,
                "location_confidence": location_confidence,
            }
        )
    return result


# ── Dict → PatchOperation conversion helper ──────────────────────────────────


def patches_from_dicts(
    raw_patches: list[dict[str, Any]],
    issue_windows: list[dict[str, Any]] | None = None,
) -> list["PatchOperation"]:
    """Convert LLM-returned patch dicts to typed PatchOperation objects.

    Optionally enriches patches with paragraph anchors from ``issue_windows``
    when the patch dict is missing ``para_start``/``para_end``.

    Lazy-imports PatchOperation to avoid circular dependency at module level.
    """
    from novel_forge.core.patch_engine.models import PatchOperation  # noqa: F811

    ops: list[PatchOperation] = []
    for i, raw in enumerate(raw_patches):
        if not isinstance(raw, dict):
            continue
        original = (raw.get("original") or "").strip()
        if not original:
            continue
        replacement = raw.get("replacement") or ""

        # Try to find matching issue window for paragraph anchors
        p_start = raw.get("para_start")
        p_end = raw.get("para_end")
        e_start = raw.get("editable_para_start")
        e_end = raw.get("editable_para_end")

        if (p_start is None or p_end is None) and issue_windows:
            for iw in issue_windows:
                if original[:40] in iw["window_text"]:
                    p_start = p_start if p_start is not None else iw["para_start"]
                    p_end = p_end if p_end is not None else iw["para_end"]
                    e_start = e_start if e_start is not None else iw["editable_para_start"]
                    e_end = e_end if e_end is not None else iw["editable_para_end"]
                    break

        ops.append(PatchOperation(
            patch_id=raw.get("patch_id", f"patch_{i}"),
            issue_id=raw.get("issue_id", ""),
            original=original,
            replacement=replacement,
            para_start=_safe_int(p_start),
            para_end=_safe_int(p_end),
            editable_para_start=_safe_int(e_start),
            editable_para_end=_safe_int(e_end),
            replace_all=bool(raw.get("replace_all", False)),
        ))
    return ops


def _safe_int(val: Any) -> int | None:
    """Convert to int or return None."""
    if val is None:
        return None
    try:
        return int(val)
    except (TypeError, ValueError):
        return None


# ── Anchor Recalibration ────────────────────────────────────────────────────


def recalibrate_issue_anchors(
    issues: list[Any],
    current_text: str,
    *,
    confidence_floor: float = 0.5,
) -> list[dict[str, Any]]:
    """Re-resolve paragraph anchors for *issues* against *current_text*.

    After a repair round modifies the chapter text, the original
    ``evidence_quote`` / ``paragraph_start`` anchors may be stale.
    This function re-runs :func:`resolve_paragraph_locally` on the
    current text and returns a list of recalibration result dicts.

    Each dict contains:
    - ``issue``: the original issue object (unchanged)
    - ``paragraph_start``: new 1-based start (0 = unknown)
    - ``paragraph_end``: new 1-based end
    - ``anchor_type``: new anchor type string
    - ``location_confidence``: new confidence (0.0-1.0)
    - ``anchor_degraded``: True if confidence < confidence_floor

    Callers should use these results to update issue fields before
    passing them to the next repair step.
    """
    paragraphs = split_paragraphs(current_text)
    results: list[dict[str, Any]] = []

    for issue in issues:
        evidence = getattr(issue, "evidence_quote", "") or getattr(issue, "evidence", "") or ""
        location = getattr(issue, "location", "") or ""
        old_para_start = getattr(issue, "paragraph_start", 0) or 0
        old_confidence = getattr(issue, "location_confidence", 0.0) or 0.0

        # Only recalibrate if there's something to resolve
        if not evidence and not location and old_para_start <= 0:
            results.append({
                "issue": issue,
                "paragraph_start": old_para_start,
                "paragraph_end": getattr(issue, "paragraph_end", 0) or 0,
                "anchor_type": getattr(issue, "anchor_type", "") or "",
                "location_confidence": old_confidence,
                "anchor_degraded": False,
            })
            continue

        # Resolve using the 6-phase fallback chain on current text
        llm_hint = old_para_start if old_para_start > 0 else 0
        target_indices, anchor_type, confidence = resolve_paragraph_locally(
            paragraphs,
            evidence=evidence,
            location=location,
            llm_hint=llm_hint,
        )

        # Convert 0-based indices to 1-based
        new_para_start = (min(target_indices) + 1) if target_indices else 0
        new_para_end = (max(target_indices) + 1) if target_indices else 0

        anchor_degraded = confidence < confidence_floor

        results.append({
            "issue": issue,
            "paragraph_start": new_para_start,
            "paragraph_end": new_para_end,
            "anchor_type": "anchor_degraded" if anchor_degraded else anchor_type,
            "location_confidence": confidence,
            "anchor_degraded": anchor_degraded,
        })

    return results


# ── Post-patch punctuation normalization ─────────────────────────────────────

# Order matters: 3-char pattern before 2-char patterns.
_PUNCT_FIXES: list[tuple[str, str]] = [
    ("。，。", "。"),
    ("。，", "。"),
    ("！，", "！"),
    ("？，", "？"),
]

_OPEN_TO_CLOSE: dict[str, str] = {
    "\u201c": "\u201d",
    "\u300c": "\u300d",
    '"': '"',
}

_DUP_SEPARATORS = frozenset("。！？，；")
_MIN_DUP_LEN = 6
_MAX_DUP_LEN = 30


def _split_by_quotes(text: str) -> list[tuple[str, bool]]:
    """Split *text* into ``(segment, is_quoted)`` pairs.

    Recognised quote pairs: ``\u201c…\u201d``, ``\u300c…\u300d``, ``"…"``.
    """
    segments: list[tuple[str, bool]] = []
    i = 0
    n = len(text)
    buf: list[str] = []

    while i < n:
        ch = text[i]
        close_ch = _OPEN_TO_CLOSE.get(ch)
        if close_ch is not None:
            if buf:
                segments.append(("".join(buf), False))
                buf = []
            quote_buf = [ch]
            j = i + 1
            while j < n:
                quote_buf.append(text[j])
                if text[j] == close_ch:
                    break
                j += 1
            segments.append(("".join(quote_buf), True))
            i = j + 1
        else:
            buf.append(ch)
            i += 1

    if buf:
        segments.append(("".join(buf), False))

    return segments


def _fix_punctuation_sequences(text: str) -> str:
    """Replace impossible punctuation sequences in *text*."""
    for bad, good in _PUNCT_FIXES:
        text = text.replace(bad, good)
    return text


def _deduplicate_consecutive_fragments(text: str) -> str:
    """Remove consecutive duplicate fragments (≥ 6 chars) in *text*.

    Handles two patterns:
    1. **Directly adjacent**: ``FRAGMENT + FRAGMENT`` → ``FRAGMENT``
    2. **Punctuation-separated**: ``FRAGMENT + punct + FRAGMENT`` → ``FRAGMENT``
       where *punct* is a single Chinese punctuation character.

    Only fragments of at least ``_MIN_DUP_LEN`` characters are considered to
    avoid false positives on common short phrases.
    """
    i = 0
    text_len = len(text)
    while i < text_len - _MIN_DUP_LEN:
        upper = min(_MAX_DUP_LEN, (text_len - i) // 2)
        found_dup = False

        for frag_len in range(upper, _MIN_DUP_LEN - 1, -1):
            if text[i : i + frag_len] == text[i + frag_len : i + 2 * frag_len]:
                text = text[: i + frag_len] + text[i + 2 * frag_len :]
                text_len = len(text)
                found_dup = True
                break

        if found_dup:
            continue

        for frag_len in range(upper, _MIN_DUP_LEN - 1, -1):
            sep_pos = i + frag_len
            if sep_pos >= text_len:
                break
            mid_char = text[sep_pos]
            if mid_char in _DUP_SEPARATORS:
                after_sep = sep_pos + 1
                if text[i : i + frag_len] == text[after_sep : after_sep + frag_len]:
                    text = text[: i + frag_len] + text[after_sep + frag_len :]
                    text_len = len(text)
                    found_dup = True
                    break

        if not found_dup:
            i += 1

    return text


def normalize_post_patch_punctuation(text: str) -> str:
    """Normalize impossible punctuation sequences and deduplicate text fragments
    introduced by the patch pipeline.

    **Punctuation fixes** (applied outside dialogue quotes only):

    - ``"。，"`` → ``"。"``  — period-comma → period wins
    - ``"。，。"`` → ``"。"`` — period-comma-period → period
    - ``"！，"`` → ``"！"``  — exclamation-comma → exclamation wins
    - ``"？，"`` → ``"？"``  — question-comma → question wins

    **Deduplication** (outside quotes, fragments ≥ 6 chars):

    - Directly adjacent: ``"清漪举杯时的手腕清漪举杯时的手腕微微下沉"``
      → ``"清漪举杯时的手腕微微下沉"``
    - Punctuation-separated: ``"杯沿恰好避开烛火的光晕。杯沿恰好避开烛火的光晕"``
      → ``"杯沿恰好避开烛火的光晕"``

    Dialogue quotes (``\u201c…\u201d``, ``\u300c…\u300d``, ``"…"``) are never
    modified.

    Examples
    --------
    >>> normalize_post_patch_punctuation("像是不起眼的杂役。，可她能看见")
    '像是不起眼的杂役。可她能看见'
    >>> normalize_post_patch_punctuation("清漪举杯时的手腕清漪举杯时的手腕微微下沉")
    '清漪举杯时的手腕微微下沉'
    >>> normalize_post_patch_punctuation('\\u201c。，\\u201d不可修改')
    '\\u201c。，\\u201d不可修改'
    """
    if not text or len(text) < 2:
        return text

    segments = _split_by_quotes(text)
    parts: list[str] = []
    for segment, is_quoted in segments:
        if is_quoted:
            parts.append(segment)
        else:
            segment = _fix_punctuation_sequences(segment)
            segment = _deduplicate_consecutive_fragments(segment)
            parts.append(segment)
    return "".join(parts)
