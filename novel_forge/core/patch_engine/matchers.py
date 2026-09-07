"""Matcher chain for PatchExecutorV2.

Each matcher implements a single strategy for locating ``original`` text in a
search window.  The executor walks the chain from strongest to weakest,
short-circuiting on first match.

Match result:
    (start_offset, end_offset, match_count)
    — offsets are *byte* positions in the search window string.
    — match_count is the total number of occurrences found.
"""

from __future__ import annotations

from typing import NamedTuple

# ── Shared normalisation table ────────────────────────────────────────────────
_PUNCT_NORMALIZE = str.maketrans(
    {
        "\uff0c": ",", "\u3002": ".", "\uff01": "!", "\uff1f": "?",
        "\uff1a": ":", "\uff1b": ";", "\u2018": "'", "\u2019": "'",
        "\u201c": '"', "\u201d": '"', "\u2026": "...", "\u300c": "[",
        "\u300d": "]", "\u300e": "[", "\u300f": "]",
    }
)

# Combined translate table that strips whitespace AND normalises punctuation
# in a single C-level pass.  Used by normalized_match() to avoid the previous
# per-character Python loop that created O(n) tuple objects.
#
# All 29 Unicode whitespace characters recognised by str.split() are mapped
# to None (deletion).  This guarantees identical behaviour to the legacy
# _norm_strip() helper which uses "".join(text.split()).
_WHITESPACE_STRIP_TABLE: dict[int, str | None] = {
    # ASCII whitespace
    ord(" "): None,
    ord("\t"): None,
    ord("\n"): None,
    ord("\r"): None,
    ord("\x0b"): None,  # vertical tab
    ord("\x0c"): None,  # form feed
    ord("\x1c"): None,  # file separator
    ord("\x1d"): None,  # group separator
    ord("\x1e"): None,  # record separator
    ord("\x1f"): None,  # unit separator
    ord("\x85"): None,  # next line (C1 control)
    # Unicode whitespace
    ord("\u00a0"): None,  # no-break space
    ord("\u1680"): None,  # ogham space mark
    ord("\u2000"): None,  # en quad
    ord("\u2001"): None,  # em quad
    ord("\u2002"): None,  # en space
    ord("\u2003"): None,  # em space
    ord("\u2004"): None,  # three-per-em space
    ord("\u2005"): None,  # four-per-em space
    ord("\u2006"): None,  # six-per-em space
    ord("\u2007"): None,  # figure space
    ord("\u2008"): None,  # punctuation space
    ord("\u2009"): None,  # thin space
    ord("\u200a"): None,  # hair space
    ord("\u2028"): None,  # line separator
    ord("\u2029"): None,  # paragraph separator
    ord("\u202f"): None,  # narrow no-break space
    ord("\u205f"): None,  # medium mathematical space
    ord("\u3000"): None,  # ideographic space (CJK fullwidth)
}
_WHITESPACE_STRIP_TABLE.update(_PUNCT_NORMALIZE)


class MatchResult(NamedTuple):
    """Successful match in the search window."""

    start: int          # start offset in the window
    end: int            # end offset (exclusive) in the window
    match_count: int    # total occurrences found (for ambiguity check)
    matcher_name: str   # which matcher produced this result


def _norm_strip(text: str) -> str:
    """Strip whitespace and normalise full-width punctuation."""
    return "".join(text.split()).translate(_PUNCT_NORMALIZE)


# ─────────────────────────────────────────────────────────────────────────────
# Matcher interface:  match(window, original) -> MatchResult | None
# ─────────────────────────────────────────────────────────────────────────────


def exact_match(window: str, original: str) -> MatchResult | None:
    """Exact substring match. O(n)."""
    idx = window.find(original)
    if idx < 0:
        return None
    count = window.count(original)
    return MatchResult(start=idx, end=idx + len(original), match_count=count, matcher_name="exact")


def trimmed_line_match(window: str, original: str) -> MatchResult | None:
    """Line-level trim match — strip leading/trailing whitespace per line."""
    trimmed_orig = "\n".join(ln.strip() for ln in original.splitlines())
    trimmed_win = "\n".join(ln.strip() for ln in window.splitlines())
    idx = trimmed_win.find(trimmed_orig)
    if idx < 0:
        return None
    # Map back to untrimmed offsets
    _map_char_to_orig(window, trimmed_win, idx)
    count = trimmed_win.count(trimmed_orig)
    # We need the real offsets in the original window — build a char map
    real_start, real_end = _map_trimmed_span(window, trimmed_win, idx, idx + len(trimmed_orig))
    return MatchResult(start=real_start, end=real_end, match_count=count, matcher_name="trimmed_line")


def normalized_match(window: str, original: str) -> MatchResult | None:
    """Whitespace + punctuation normalised match with position mapping.

    Uses ``str.translate()`` (C-level) for normalisation instead of a
    per-character Python loop, then builds a compact ``int`` position map
    only when a match is found.  This avoids creating O(n) tuple objects
    on every call.
    """
    norm_orig = _norm_strip(original)
    if not norm_orig:
        return None

    # Fast-path: C-level translate strips whitespace + normalises punctuation
    # in a single pass, producing the normalised text directly.
    norm_text = window.translate(_WHITESPACE_STRIP_TABLE)
    ni = norm_text.find(norm_orig)
    if ni < 0:
        return None

    count = _count_normalized(norm_text, norm_orig)

    # Build position map only now that we know a match exists.
    # pos_map[k] = index in `window` of the k-th surviving character.
    # Handles multi-char replacements (e.g. \u2026 -> "...") by repeating
    # the original index for each output character.
    pos_map: list[int] = []
    for i, ch in enumerate(window):
        replacement = ch.translate(_WHITESPACE_STRIP_TABLE)
        if replacement:
            for _ in replacement:
                pos_map.append(i)

    if ni + len(norm_orig) - 1 >= len(pos_map):
        return None
    real_start = pos_map[ni]
    real_end = pos_map[ni + len(norm_orig) - 1] + 1
    return MatchResult(start=real_start, end=real_end, match_count=count, matcher_name="normalized")


def block_anchor_match(window: str, original: str, *, min_anchor: int = 12) -> MatchResult | None:
    """First/last N chars as anchors + middle similarity check.

    Useful when the LLM slightly paraphrases the middle but preserves the
    opening and closing phrases.
    """
    if len(original) < min_anchor * 3:
        return None

    head = original[:min_anchor]
    tail = original[-min_anchor:]

    head_idx = window.find(head)
    if head_idx < 0:
        return None

    # Search for tail AFTER head
    tail_search_start = head_idx + len(head)
    tail_idx = window.find(tail, tail_search_start)
    if tail_idx < 0:
        return None

    candidate_end = tail_idx + len(tail)
    candidate = window[head_idx:candidate_end]

    # Sanity: candidate should not be excessively longer than original
    if len(candidate) > len(original) * 1.8:
        return None

    # Check middle similarity via character overlap ratio
    mid_orig = _norm_strip(original[min_anchor:-min_anchor])
    mid_cand = _norm_strip(candidate[min_anchor: len(candidate) - min_anchor])
    if not mid_orig:
        return MatchResult(start=head_idx, end=candidate_end, match_count=1, matcher_name="block_anchor")

    overlap = sum(1 for c in mid_orig if c in mid_cand) / len(mid_orig)
    if overlap < 0.6:
        return None

    return MatchResult(start=head_idx, end=candidate_end, match_count=1, matcher_name="block_anchor")


# ── Matcher chain ─────────────────────────────────────────────────────────────

# Ordered from strongest (most precise) to weakest.
MATCHER_CHAIN = [
    exact_match,
    trimmed_line_match,
    normalized_match,
    block_anchor_match,
]


def run_matcher_chain(window: str, original: str) -> MatchResult | None:
    """Run matchers in priority order, return first hit."""
    for matcher in MATCHER_CHAIN:
        result = matcher(window, original)
        if result is not None:
            return result
    return None


# ── Internal helpers ──────────────────────────────────────────────────────────


def _count_normalized(norm_text: str, norm_orig: str) -> int:
    """Count non-overlapping occurrences of norm_orig in norm_text."""
    count = 0
    start = 0
    while True:
        idx = norm_text.find(norm_orig, start)
        if idx < 0:
            break
        count += 1
        start = idx + len(norm_orig)
    return count


def _map_trimmed_span(
    original_text: str,
    trimmed_text: str,
    trim_start: int,
    trim_end: int,
) -> tuple[int, int]:
    """Map a span in trimmed text back to original text offsets.

    Builds a character index mapping from trimmed positions to original positions.
    """
    # Build mapping: for each char in trimmed_text, what's its position in original_text?
    orig_lines = original_text.splitlines(keepends=True)
    trim_lines = trimmed_text.splitlines(keepends=True)

    char_map: list[int] = []  # char_map[trimmed_pos] = original_pos
    orig_offset = 0
    trim_offset = 0

    for orig_line, trim_line in zip(orig_lines, trim_lines, strict=False):
        # Leading whitespace in original line
        stripped = orig_line.lstrip()
        leading = len(orig_line) - len(stripped)

        for i, _ in enumerate(trim_line):
            char_map.append(orig_offset + leading + i)
        orig_offset += len(orig_line)
        trim_offset += len(trim_line)

    if trim_start >= len(char_map) or trim_end > len(char_map):
        # Fallback: return conservative bounds
        return 0, len(original_text)

    real_start = char_map[trim_start]
    real_end = char_map[min(trim_end - 1, len(char_map) - 1)] + 1
    return real_start, real_end


def _map_char_to_orig(original: str, trimmed: str, idx: int) -> int:
    """Map a single position from trimmed text to original text. Helper stub."""
    return idx  # conservative; real mapping done by _map_trimmed_span
