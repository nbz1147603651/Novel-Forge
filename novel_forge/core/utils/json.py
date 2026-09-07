"""JSON parsing and repair utilities."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from enum import Enum
from typing import Any

from novel_forge.core.response_repair import (
    repair_array_item_separators,
    repair_missing_colon_delimiters,
    repair_object_key_only_members,
    repair_object_separators,
)

logger = logging.getLogger(__name__)


# Max characters to trim when repairing truncated JSON
_MAX_JSON_REPAIR_TRIM = 8192

# Regex patterns for Markdown fences
_FENCE_RE = re.compile(
    r"^```(?:json|JSON)?\s*\n(.*?)\n\s*```\s*$",
    re.DOTALL,
)
_FENCE_BLOCK_RE = re.compile(
    r"```(?:json|JSON)?\s*\n(.*?)\n\s*```",
    re.DOTALL,
)

# Characters that may legally follow a backslash inside a JSON string.
# JSON spec (RFC 8259): \", \\, \/, \b, \f, \n, \r, \t, \uXXXX
_VALID_JSON_ESCAPE_CHARS: frozenset[str] = frozenset({'"', "\\", "/", "b", "f", "n", "r", "t", "u"})
_HEX_DIGITS: frozenset[str] = frozenset("0123456789abcdefABCDEF")
_ARRAY_OBJECT_ITEM_START_KEYS: frozenset[str] = frozenset(
    {
        "chapter_end",
        "chapter_number",
        "chapter_start",
        "event",
        "name",
    }
)


class JSONRepairMode(str, Enum):
    """JSON repair aggressiveness level."""
    STRICT = "strict"          # Max 5% of chars can be trimmed
    LENIENT = "lenient"        # Max 30% of chars can be trimmed  
    AGGRESSIVE = "aggressive"  # Max 50% of chars can be trimmed


@dataclass
class JSONRepairResult:
    """Result of JSON repair attempt."""
    success: bool
    json_text: str | None
    error: str | None
    trimmed_chars: int
    trim_ratio: float
    mode_used: JSONRepairMode


def strip_markdown_fences(text: str) -> str:
    """Remove Markdown code-block fences from text.
    
    Removes `` ```json ... ``` `` style fences. If text is not wrapped
    in fences, returns it unchanged.
    
    Args:
        text: Input text possibly wrapped in fences
        
    Returns:
        Text with fences removed
        
    Examples:
        >>> strip_markdown_fences('```json\\n{"a": 1}\\n```')
        '{"a": 1}'
    """
    text = text.strip()
    m = _FENCE_RE.match(text)
    if m:
        return m.group(1).strip()
    
    # Fallback: handle partial fences
    if text.startswith("```"):
        lines = text.split("\n")
        if lines[0].strip().startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        return "\n".join(lines).strip()
    
    return text


def try_repair_json(
    text: str,
    mode: JSONRepairMode = JSONRepairMode.LENIENT,
) -> JSONRepairResult:
    """Attempt to repair truncated or malformed JSON.
    
    Common LLM failure: response is cut off mid-JSON due to token limits,
    leaving unclosed strings, brackets, or braces. This function attempts
    automatic repair by progressively trimming the broken tail and closing
    open brackets/braces.
    
    Args:
        text: JSON text to repair
        mode: Repair aggressiveness level
        
    Returns:
        JSONRepairResult with success status and diagnostics
        
    Examples:
        >>> result = try_repair_json('{"a": 1,')
        >>> result.success
        True
        >>> result.trim_ratio
        0.0
    """
    original_len = len(text)
    
    # Determine max trim based on mode
    max_trim_ratio = {
        JSONRepairMode.STRICT: 0.05,
        JSONRepairMode.LENIENT: 0.30,
        JSONRepairMode.AGGRESSIVE: 0.50,
    }[mode]
    
    max_trim = int(original_len * max_trim_ratio)
    # Absolute cap to prevent O(n²) on very large inputs
    _ABSOLUTE_TRIM_CAP = 8192
    max_trim = min(max_trim, _ABSOLUTE_TRIM_CAP)
    
    # Quick check: already valid?
    try:
        json.loads(text, strict=False)
        return JSONRepairResult(
            success=True,
            json_text=text,
            error=None,
            trimmed_chars=0,
            trim_ratio=0.0,
            mode_used=mode,
        )
    except json.JSONDecodeError:
        pass
    
    # Strategy: keep removing the last non-whitespace character(s) until
    # we can close all open brackets/braces.
    # We maintain a running bracket state and adjust incrementally when
    # trimming characters, avoiding re-scanning the entire string.
    candidate = text.rstrip()

    # Initial full scan to build bracket state
    def _scan_brackets(s: str) -> tuple[int, int, bool, bool]:
        """Return (open_braces, open_brackets, in_string, escape)."""
        ob, obr, ins, esc = 0, 0, False, False
        for ch in s:
            if esc:
                esc = False
                continue
            if ch == "\\":
                esc = True
                continue
            if ch == '"':
                ins = not ins
                continue
            if ins:
                continue
            if ch == "{":
                ob += 1
            elif ch == "}":
                ob -= 1
            elif ch == "[":
                obr += 1
            elif ch == "]":
                obr -= 1
        return ob, obr, ins, esc

    open_braces, open_brackets, in_string, _ = _scan_brackets(candidate)

    for _ in range(max_trim + 1):
        # Remove trailing comma
        while candidate.endswith(","):
            candidate = candidate[:-1].rstrip()
            # Comma doesn't affect bracket state

        if not in_string:
            # Not inside a string — try closing brackets/braces
            attempt = candidate
            while attempt.endswith(","):
                attempt = attempt[:-1].rstrip()
            attempt += "]" * max(open_brackets, 0)
            attempt += "}" * max(open_braces, 0)

            try:
                json.loads(attempt, strict=False)
                trimmed = original_len - len(candidate)
                trim_ratio = trimmed / max(1, original_len)

                if trimmed > 0:
                    _log_repair(trimmed, original_len, trim_ratio)

                return JSONRepairResult(
                    success=True,
                    json_text=attempt,
                    error=None,
                    trimmed_chars=trimmed,
                    trim_ratio=trim_ratio,
                    mode_used=mode,
                )
            except json.JSONDecodeError:
                pass

        # Trim one more character — adjust bracket state incrementally
        if not candidate:
            break
        removed = candidate[-1]
        candidate = candidate[:-1].rstrip()

        # Reverse the effect of the removed character on bracket state.
        # This is an approximation — we skip escape/string context tracking
        # on the removed char because the outer _scan_brackets already
        # computed the correct state.  On each trim we do a lightweight
        # re-scan only if needed (when we've trimmed many characters).
        if removed == '"':
            # Could toggle string state; do a full rescan to be safe.
            open_braces, open_brackets, in_string, _ = _scan_brackets(candidate)
        elif not in_string:
            if removed == "{":
                open_braces -= 1
            elif removed == "}":
                open_braces += 1
            elif removed == "[":
                open_brackets -= 1
            elif removed == "]":
                open_brackets += 1
    
    # All repair attempts failed
    return JSONRepairResult(
        success=False,
        json_text=None,
        error=f"Failed to repair JSON after trimming {max_trim} characters",
        trimmed_chars=0,
        trim_ratio=0.0,
        mode_used=mode,
    )


def _try_repair_json(text: str) -> str:
    """Legacy function for backward compatibility.
    
    Attempts repair with LENIENT mode and returns repaired text or original.
    """
    result = try_repair_json(text, mode=JSONRepairMode.LENIENT)
    return result.json_text or text


def _escape_unescaped_quotes(text: str) -> str:
    """Attempt to escape unescaped quotes in JSON string values.
    
    This is a heuristic approach to fix common LLM errors where quotes
    inside string values are not properly escaped.
    
    Args:
        text: JSON text with potentially unescaped quotes
        
    Returns:
        Text with escaped quotes
    """
    result = []
    in_string = False
    escape_next = False
    
    for i, char in enumerate(text):
        if escape_next:
            result.append(char)
            escape_next = False
            continue
        
        if char == '\\':
            result.append(char)
            escape_next = True
            continue
        
        if char == '"':
            # Check if this quote is part of a JSON key/value delimiter
            # vs. a quote inside a string value
            if in_string:
                # Look ahead to see if this looks like end of value
                next_char = text[i+1:i+2]
                if next_char in (',', '}', ']', ''):
                    result.append(char)
                    in_string = False
                else:
                    # Likely an unescaped quote inside string
                    result.append('\\"')
            else:
                result.append(char)
                in_string = True
        else:
            result.append(char)
    
    return ''.join(result)


def _extract_balanced_json(text: str) -> str | None:
    """Extract the longest balanced JSON object/array from arbitrary text.

    Useful when models prepend/append explanations around JSON.
    
    Args:
        text: Text containing JSON
        
    Returns:
        Extracted JSON string or None if not found
    """
    text = text.strip()
    if not text:
        return None

    best: str | None = None
    best_len = 0

    for start in range(len(text)):
        first = text[start]
        if first not in "{[":
            continue

        stack: list[str] = []
        in_string = False
        escape_next = False

        for index in range(start, len(text)):
            char = text[index]
            if escape_next:
                escape_next = False
                continue
            if char == "\\":
                escape_next = True
                continue
            if char == '"':
                in_string = not in_string
                continue
            if in_string:
                continue
            if char in "{[":
                stack.append(char)
                continue
            if char in "}]":
                if not stack:
                    break
                opener = stack.pop()
                if (opener == "{" and char != "}") or (opener == "[" and char != "]"):
                    break
                if not stack:
                    candidate = text[start : index + 1]
                    if len(candidate) > best_len:
                        best = candidate
                        best_len = len(candidate)
                    break
    
    return best


def safe_parse_json(text: str) -> Any:
    """Parse a JSON string that may be wrapped in Markdown fences.

    Strips `` ```json ... ``` `` wrappers before parsing. If the JSON is 
    truncated (common with LLM token limits), attempts automatic repair 
    before raising.
    
    Also handles invalid control characters that may appear in LLM responses
    by using strict=False.
    
    Args:
        text: JSON text, possibly Markdown-wrapped or truncated
        
    Returns:
        Parsed JSON object
        
    Raises:
        json.JSONDecodeError: If parsing fails after all repair attempts
        
    Examples:
        >>> safe_parse_json('{"a": 1}')
        {'a': 1}
        >>> safe_parse_json('```json\\n{"a": 1}\\n```')
        {'a': 1}
    """
    cleaned = strip_markdown_fences(text)

    # Try direct parsing first (use strict=False to allow control characters)
    original_exc: json.JSONDecodeError | None = None
    try:
        return json.loads(cleaned, strict=False)
    except json.JSONDecodeError as exc:
        original_exc = exc

    # Fast path: "Extra data" caused by a premature closing brace before sibling
    # key-value pairs.  Pattern: {...}},"key": ...}  where the outer object was
    # closed one level too early.  Repair by removing the spurious extra } at
    # (exc.pos - 1) so all sibling fields merge back into a single root object.
    if (
        original_exc is not None
        and original_exc.msg == "Extra data"
        and original_exc.pos > 0
        and cleaned[original_exc.pos - 1] == "}"
        and original_exc.pos < len(cleaned)
        and cleaned[original_exc.pos] in {",", "，"}
    ):
        _spliced = cleaned[: original_exc.pos - 1] + cleaned[original_exc.pos :]
        try:
            return json.loads(_fix_fullwidth_separators(_spliced), strict=False)
        except json.JSONDecodeError:
            pass
    else:
        _spliced = None

    _root_split_repaired = _fix_root_object_split_siblings(cleaned)
    if _root_split_repaired != cleaned:
        _root_split_fullwidth_repaired = _fix_fullwidth_separators(_root_split_repaired)
        try:
            return json.loads(_root_split_fullwidth_repaired, strict=False)
        except json.JSONDecodeError:
            pass
        try:
            return json.loads(_root_split_repaired, strict=False)
        except json.JSONDecodeError:
            pass

    # Collect candidate JSON payloads from mixed model output
    candidates: list[str] = []

    # Some long outline responses close the chapter array/root too early and
    # then keep writing a field that belongs to the still-open chapter object:
    #   ... "expected_hook": {...}]}, "expected_payoffs": [...]
    # Try this structural repair before generic truncation repair, otherwise
    # the truncation path may accept a valid prefix and drop the final chapter.
    def _append_directly_valid_candidate(candidate: str) -> None:
        if candidate == cleaned:
            return
        try:
            json.loads(candidate, strict=False)
        except json.JSONDecodeError:
            return
        candidates.append(candidate)

    _premature_sibling_repaired = _fix_premature_object_close_before_sibling_key(cleaned)
    _append_directly_valid_candidate(_premature_sibling_repaired)

    _premature_sibling_after_mismatch_repair = _fix_premature_object_close_before_sibling_key(
        _fix_mismatched_closers_v2(cleaned)
    )
    _append_directly_valid_candidate(_premature_sibling_after_mismatch_repair)

    candidates.append(cleaned)
    # If the Extra-data fast-path created a spliced candidate that still needs
    # further repair (e.g. additional mismatched closers further in the text),
    # add it to the candidate pool so the preprocess strategies can fix it.
    if _spliced is not None:
        candidates.append(_spliced)
    if _root_split_repaired != cleaned:
        candidates.append(_root_split_repaired)
    
    # Try extracting from Markdown blocks
    for match in _FENCE_BLOCK_RE.finditer(text):
        block = match.group(1).strip()
        if block:
            candidates.append(block)
    
    # Try extracting balanced JSON
    extracted = _extract_balanced_json(text)
    root_like_cleaned = cleaned.startswith("{") or cleaned.startswith("[")
    if extracted and (
        not root_like_cleaned or len(extracted) >= max(32, int(len(cleaned) * 0.6))
    ):
        candidates.append(extracted)

    # Deduplicate candidates
    deduped_candidates: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        if candidate not in seen:
            seen.add(candidate)
            deduped_candidates.append(candidate)

    # Try multiple repair strategies for each candidate.
    #
    # Design rationale — keep the list short and targeted:
    # • "identity" must stay first so valid JSON never triggers the warning log.
    # • "truncation_repair" handles the commonest LLM failure (token cutoff)
    #   without touching content — just closes unclosed brackets/braces.
    # • "newline_kv_sep+truncation" fixes the \n-as-separator pattern where
    #   Chinese LLMs write {"k1": "v1\nk2": "v2"} instead of using commas.
    # • "array_key_transition+truncation" fixes a common cutoff glitch where
    #   an array value is followed by the next object key but the closing `]`
    #   is missing, e.g. `"beats_summary": ["a", "b", "next_key": ...`.
    # • "newline_kv_sep+missing_val_quote_anywhere+truncation" additionally
    #   repairs bare string values after ":" in minified one-line JSON output.
    # • "curly_quotes+missing_val_quote+fullwidth+truncation" is the
    #   comprehensive Chinese-LLM strategy: normalise smart-quotes first
    #   (so in_string tracking is reliable) → insert missing opening quotes
    #   → replace fullwidth separators → close truncated brackets.
    # • "object_as_array+truncation" covers the { "a", "b" } structural mistake.
    #
    # Strategies deliberately omitted to reduce false-positive risk:
    # • quote_escape / quote_escape+truncation: aggressively escapes '"' by
    #   peeking at surrounding characters, which frequently garbles legitimate
    #   Chinese content containing natural quotation marks.
    # • fix_fullwidth_sep alone and fix_missing_value_quote alone: both are
    #   unreliable without curly-quote pre-normalisation, and are strict subsets
    #   of the comprehensive strategy above — retaining them only adds noise.
    # • object_as_array without truncation: dominated by the +truncation variant
    #   (which short-circuits harmlessly when no truncation is detected).
    # Each strategy: (name, preprocess_fn).  The preprocess_fn transforms
    # the text BEFORE truncation repair.  "identity" skips repair entirely
    # (direct json.loads only).  All others feed into _try_repair_json after
    # deduplication — many preprocessing functions are no-ops on clean input,
    # so caching avoids redundant O(n × max_trim) repair work.
    _IDENTITY = object()  # sentinel — skip truncation repair

    preprocess_strategies: list[tuple[str, Any]] = [
        ("identity", _IDENTITY),
        (
            "extra_quote_after_composite+truncation",
            lambda t: _fix_extra_quote_after_composite_value(t),
        ),
        (
            "extra_closing_paren_after_composite+truncation",
            lambda t: _fix_extra_closing_paren_after_composite_value(t),
        ),
        # • "missing_object_opener_in_array+truncation" must run before generic
        #   object/array repairs.  When an array item loses only its opening "{",
        #   later repairs may parse by merging sibling item keys into one object,
        #   which is valid JSON but silently loses earlier repeated fields.
        (
            "missing_object_opener_in_array+truncation",
            lambda t: _fix_missing_object_opener_in_array(t),
        ),
        # • "inline_kv_sep+truncation" must run before generic truncation.
        #   Otherwise a malformed trailing relationship entry can be "repaired"
        #   by dropping the entire final character object.
        (
            "extra_key_open_quote+missing_key_open_quote+truncation",
            lambda t: _fix_missing_key_open_quote(_fix_extra_key_open_quote(t)),
        ),
        (
            "unquoted_object_keys+truncation",
            lambda t: _fix_unquoted_object_keys(t),
        ),
        (
            "unquoted_object_keys+curly_quotes+missing_val_quote+fullwidth+truncation",
            lambda t: _fix_fullwidth_separators(
                _fix_missing_value_quotes(
                    _fix_curly_double_quotes(_fix_unquoted_object_keys(t))
                )
            ),
        ),
        (
            "inline_kv_sep+truncation",
            lambda t: _fix_inline_kv_separator(t),
        ),
        (
            "invalid_escapes+close_truncated_string",
            lambda t: _fix_invalid_escapes_then_close_truncated_string(t),
        ),
        ("truncation_repair", lambda t: t),
        # • "missing_object_braces+truncation" handles Chinese LLMs that flatten
        #   all character objects into one.  Must run early because later
        #   strategies may successfully parse but lose data.
        (
            "missing_object_braces+truncation",
            lambda t: _fix_missing_object_braces(t),
        ),
        # • "single_quote_only+truncation" handles the common pattern where Chinese
        #   LLMs emit single-quoted keys/values like {'key': 'value'} instead of
        #   {"key": "value"}.  Applied early because it's a frequent failure mode
        #   and _fix_single_quotes_simple is a near-no-op on valid JSON.
        (
            "single_quote_only+truncation",
            lambda t: _fix_single_quotes_simple(t),
        ),
        (
            "array_key_transition+truncation",
            lambda t: _fix_missing_array_closer_before_object_key(t),
        ),
        (
            "root_object_split_siblings+truncation",
            lambda t: _fix_root_object_split_siblings(t),
        ),
        (
            "missing_object_closer+truncation",
            lambda t: _fix_missing_object_closer_before_array_element(t),
        ),
        (
            "extra_object_closer_before_array_element+truncation",
            lambda t: _fix_extra_object_closer_before_array_element(t),
        ),
        (
            "missing_object_closer+stateful_inner_quote+truncation",
            lambda t: _escape_inner_quotes_in_strings_stateful(
                _fix_missing_object_closer_before_array_element(t)
            ),
        ),
        (
            "mismatched_closers+truncation",
            lambda t: _fix_mismatched_closers(t),
        ),
        # • "mismatched_closers_v2+truncation" handles the case where LLMs emit
        #   } where ] is required (INSERT ] before the }) or ] where } is required
        #   (DELETE the stray ]).  This is distinct from mismatched_closers which
        #   REPLACES the wrong closer — the insert/delete strategy preserves the
        #   original closer count and is correct when the LLM simply forgot to
        #   close an array before returning to parent objects.
        (
            "mismatched_closers_v2+truncation",
            lambda t: _fix_mismatched_closers_v2(t),
        ),
        # • "invalid_escapes+truncation" handles LLMs that emit \' or other
        #   non-standard escape sequences (e.g. \-, \!, \() inside JSON strings.
        #   Applied early because it is targeted (only modifies chars inside
        #   strings) and low-risk (never corrupts valid JSON).
        (
            "invalid_escapes+truncation",
            lambda t: _fix_invalid_escape_sequences(t),
        ),
        (
            "array_item_separator+truncation",
            lambda t: repair_array_item_separators(t),
        ),
        (
            "object_separator+truncation",
            lambda t: repair_object_separators(t),
        ),
        (
            "object_key_only_member+truncation",
            lambda t: repair_object_key_only_members(t),
        ),
        (
            "missing_colon_delimiter+truncation",
            lambda t: repair_missing_colon_delimiters(t),
        ),
        (
            "array_item_separator+inline_kv_sep+truncation",
            lambda t: _fix_inline_kv_separator(repair_array_item_separators(t)),
        ),
        # • "unquoted_array_elements+truncation" handles the pattern where Chinese
        #   LLMs emit array items as bare text lines without surrounding quotes or
        #   comma separators.  It intentionally runs after structural array item
        #   repair so accidental quoted object openers like },"{"key"... do not
        #   get swallowed as prose strings.
        (
            "unquoted_array_elements+truncation",
            lambda t: _fix_unquoted_array_elements(t),
        ),
        (
            "newline_kv_sep+missing_val_quote_anywhere+truncation",
            lambda t: _fix_missing_value_quotes(_fix_newline_kv_separator(t)),
        ),
        (
            "newline_or_inline_kv_sep+missing_val_quote_anywhere+truncation",
            lambda t: _fix_missing_value_quotes(
                _fix_inline_kv_separator(_fix_newline_kv_separator(t))
            ),
        ),
        ("newline_kv_sep+truncation", lambda t: _fix_newline_kv_separator(t)),
        (
            "newline_or_inline_kv_sep+truncation",
            lambda t: _fix_inline_kv_separator(_fix_newline_kv_separator(t)),
        ),
        (
            "newline_kv_sep+curly_quotes+missing_val_quote+fullwidth+truncation",
            lambda t: _fix_fullwidth_separators(
                _fix_missing_value_quotes(
                    _fix_curly_double_quotes(_fix_newline_kv_separator(t))
                )
            ),
        ),
        (
            "newline_kv_sep+curly_quotes+missing_val_quote+inner_quote+fullwidth+truncation",
            lambda t: _fix_fullwidth_separators(
                _escape_inner_quotes_in_quoted_values(
                    _fix_missing_value_quotes(
                        _fix_curly_double_quotes(_fix_newline_kv_separator(t))
                    )
                )
            ),
        ),
        (
            "curly_quotes+missing_val_quote+fullwidth+truncation",
            lambda t: _fix_fullwidth_separators(
                _fix_missing_value_quotes(_fix_curly_double_quotes(t))
            ),
        ),
        (
            "curly_quotes+missing_val_quote+inner_quote+fullwidth+truncation",
            lambda t: _fix_fullwidth_separators(
                _escape_inner_quotes_in_quoted_values(
                    _fix_missing_value_quotes(_fix_curly_double_quotes(t))
                )
            ),
        ),
        ("object_as_array+truncation", lambda t: _repair_object_as_array(t)),
        (
            "missing_key_close_quote+truncation",
            lambda t: _fix_missing_key_close_quote(t),
        ),
        (
            "missing_key_open_quote+truncation",
            lambda t: _fix_missing_key_open_quote(t),
        ),
        (
            "missing_key_quotes+truncation",
            lambda t: _fix_missing_key_close_quote(_fix_missing_key_open_quote(t)),
        ),
        (
            "object_as_array+missing_key_close_quote+truncation",
            lambda t: _fix_missing_key_close_quote(_repair_object_as_array(t)),
        ),
        (
            "single_quote+curly_quotes+missing_val_quote+fullwidth+truncation",
            lambda t: _fix_fullwidth_separators(
                _fix_missing_value_quotes(
                    _fix_curly_double_quotes(_fix_single_quotes_simple(t))
                )
            ),
        ),
        (
            "single_quote+curly_quotes+missing_val_quote+inner_quote+fullwidth+truncation",
            lambda t: _fix_fullwidth_separators(
                _escape_inner_quotes_in_quoted_values(
                    _fix_missing_value_quotes(
                        _fix_curly_double_quotes(_fix_single_quotes_simple(t))
                    )
                )
            ),
        ),
        # • "stateful_inner_quote+truncation" uses a state-machine lookahead to escape
        #   unescaped ASCII double-quotes inside string values in BOTH single-line
        #   (minified) and multi-line JSON.  The existing line-based strategies only
        #   handle the "key": "value" pattern; they miss bare array-element strings
        #   and minified responses.  This strategy is tried late (after cheaper
        #   alternatives) because it modifies more content and is slightly riskier.
        (
            "stateful_inner_quote+truncation",
            lambda t: _escape_inner_quotes_in_strings_stateful(t),
        ),
        (
            "stateful_inner_quote+fullwidth+truncation",
            lambda t: _fix_fullwidth_separators(_escape_inner_quotes_in_strings_stateful(t)),
        ),
        (
            "curly_quotes+stateful_inner_quote+fullwidth+truncation",
            lambda t: _fix_fullwidth_separators(
                _escape_inner_quotes_in_strings_stateful(_fix_curly_double_quotes(t))
            ),
        ),
        # • "invalid_escapes+stateful_inner_quote+truncation" handles the
        #   combined case where an LLM response has BOTH invalid escapes (\')
        #   AND unescaped inner double-quotes.  invalid_escapes runs first so
        #   the stateful scanner sees clean escape sequences.
        (
            "invalid_escapes+stateful_inner_quote+truncation",
            lambda t: _escape_inner_quotes_in_strings_stateful(_fix_invalid_escape_sequences(t)),
        ),
        (
            "invalid_escapes+stateful_inner_quote+fullwidth+truncation",
            lambda t: _fix_fullwidth_separators(
                _escape_inner_quotes_in_strings_stateful(_fix_invalid_escape_sequences(t))
            ),
        ),
    ]

    # ── Deduplicating strategy loop ─────────────────────────────────
    # _try_repair_json is O(n × max_trim) — expensive for large input.
    # Many preprocess functions are no-ops on clean text, producing identical
    # input for repair.  Cache results to avoid redundant work.
    _repair_cache: dict[str, str] = {}

    for candidate in deduped_candidates:
        for strategy_name, preprocess_fn in preprocess_strategies:
            try:
                if preprocess_fn is _IDENTITY:
                    # Direct parse — no truncation repair
                    result = json.loads(candidate, strict=False)
                    return result

                preprocessed = preprocess_fn(candidate)
                if preprocessed == candidate and strategy_name != "truncation_repair":
                    continue

                # Deduplicate: skip if this exact text was already repaired
                if preprocessed in _repair_cache:
                    repaired = _repair_cache[preprocessed]
                else:
                    repaired = _try_repair_json(preprocessed)
                    _repair_cache[preprocessed] = repaired

                result = json.loads(repaired, strict=False)
                logger.warning(
                    "json_parse succeeded via repair strategy=%s (input_len=%d)",
                    strategy_name,
                    len(text),
                )
                return result
            except (json.JSONDecodeError, Exception):
                continue

    # All attempts failed, re-raise original error
    if original_exc is not None:
        raise original_exc
    raise json.JSONDecodeError("No valid JSON found after repair attempts", text, 0)


def _fix_fullwidth_separators(text: str) -> str:
    """Replace fullwidth punctuation used as JSON separators outside strings.

    Chinese LLMs occasionally emit fullwidth commas (U+FF0C '，') as list/object
    separators instead of ASCII ',' which is invalid JSON.  This function replaces
    them only *outside* of string literals so that '，' inside string values is
    preserved unchanged.
    """
    _FULLWIDTH_MAP = {"，": ","}
    result: list[str] = []
    in_string = False
    escape_next = False
    for ch in text:
        if escape_next:
            result.append(ch)
            escape_next = False
            continue
        if ch == "\\":
            result.append(ch)
            escape_next = True
            continue
        if ch == '"':
            in_string = not in_string
            result.append(ch)
            continue
        if not in_string and ch in _FULLWIDTH_MAP:
            result.append(_FULLWIDTH_MAP[ch])
        else:
            result.append(ch)
    return "".join(result)


def _fix_extra_quote_after_composite_value(text: str) -> str:
    """Drop a stray quote inserted between a composite value and its delimiter.

    A common minified-output glitch is ``... "items":[...]"}``, where an extra
    quote appears after a closed array/object before the containing object
    continues or closes.  A quote cannot legally start at that position, so this
    state-machine repair is narrow and leaves string content untouched.
    """
    if '"' not in text:
        return text

    result: list[str] = []
    in_string = False
    escape_next = False
    changed = False
    length = len(text)

    def _previous_significant() -> str:
        for item in reversed(result):
            if item not in " \t\r\n":
                return item
        return ""

    def _next_significant(pos: int) -> str:
        j = pos + 1
        while j < length and text[j] in " \t\r\n":
            j += 1
        return text[j] if j < length else ""

    for index, char in enumerate(text):
        if escape_next:
            result.append(char)
            escape_next = False
            continue

        if char == "\\":
            result.append(char)
            escape_next = True
            continue

        if char == '"':
            if not in_string:
                previous = _previous_significant()
                next_char = _next_significant(index)
                if previous in "]}":
                    if next_char in ",，}]":
                        changed = True
                        continue
            in_string = not in_string
            result.append(char)
            continue

        result.append(char)

    return "".join(result) if changed else text


def _fix_extra_closing_paren_after_composite_value(text: str) -> str:
    """Drop a stray ``)`` inserted after a composite JSON value.

    Example: ``{"items":[...]),"summary":"..."}`` should be
    ``{"items":[...],"summary":"..."}``. Parentheses are not valid JSON syntax
    outside strings, so this only removes ``)`` when it follows a closed array
    or object and the next significant character is another delimiter.
    """
    if ")" not in text:
        return text

    result: list[str] = []
    in_string = False
    escape_next = False
    changed = False
    length = len(text)

    def _previous_significant() -> str:
        for item in reversed(result):
            if item not in " \t\r\n":
                return item
        return ""

    def _next_significant(pos: int) -> str:
        j = pos + 1
        while j < length and text[j] in " \t\r\n":
            j += 1
        return text[j] if j < length else ""

    for index, char in enumerate(text):
        if escape_next:
            result.append(char)
            escape_next = False
            continue

        if char == "\\":
            result.append(char)
            escape_next = True
            continue

        if char == '"':
            in_string = not in_string
            result.append(char)
            continue

        if not in_string and char == ")":
            previous = _previous_significant()
            next_char = _next_significant(index)
            if previous in "]}" and next_char in ",，]}":
                changed = True
                continue

        result.append(char)

    return "".join(result) if changed else text


def _fix_root_object_split_siblings(text: str) -> str:
    """Merge sibling root wrappers back into one JSON object.

    Some model outputs continue top-level siblings inside an unnecessary object
    wrapper, or close the root object too early and then continue with another
    object wrapper, e.g.::

        {"canon_delta": {...}, {"character_state_deltas": [...]}}
        {"canon_delta": {...}}, {"character_state_deltas": [...]}

    The valid shape is a single root object:

        {"canon_delta": {...}, "character_state_deltas": [...]}

    This repair only fires around a root-level comma followed by ``{ "key":``.
    Nested adjacent objects are left to the more specific array/object repair
    strategies.
    """
    if not text or text.lstrip()[:1] != "{":
        return text

    result: list[str] = []
    stack: list[str] = []
    in_string = False
    escape_next = False
    skip_open_index = -1
    i = 0
    length = len(text)

    def _peek_object_key(pos: int) -> bool:
        j = pos
        while j < length and text[j] in " \t\r\n":
            j += 1
        if j >= length or text[j] != '"':
            return False
        j += 1
        key_escape = False
        while j < length:
            ch = text[j]
            if key_escape:
                key_escape = False
                j += 1
                continue
            if ch == "\\":
                key_escape = True
                j += 1
                continue
            if ch == '"':
                break
            j += 1
        if j >= length:
            return False
        j += 1
        while j < length and text[j] in " \t\r\n":
            j += 1
        return j < length and text[j] == ":"

    def _split_sibling_after(pos: int) -> tuple[bool, int]:
        j = pos
        while j < length and text[j] in " \t\r\n":
            j += 1
        if j >= length or text[j] not in {",", "，"}:
            return (False, -1)
        j += 1
        while j < length and text[j] in " \t\r\n":
            j += 1
        if j < length and text[j] == "{" and _peek_object_key(j + 1):
            return (True, j)
        if j < length and _peek_object_key(j):
            return (True, -1)
        return (False, -1)

    while i < length:
        ch = text[i]

        if escape_next:
            result.append(ch)
            escape_next = False
            i += 1
            continue

        if ch == "\\":
            result.append(ch)
            escape_next = True
            i += 1
            continue

        if ch == '"':
            in_string = not in_string
            result.append(ch)
            i += 1
            continue

        if in_string:
            result.append(ch)
            i += 1
            continue

        if ch == "{" and i == skip_open_index:
            skip_open_index = -1
            i += 1
            continue

        if ch in "{[":
            stack.append(ch)
            result.append(ch)
            i += 1
            continue

        if ch in "}]":
            if stack:
                top = stack[-1]
                if top == "{" and ch == "}":
                    if len(stack) == 1:
                        has_sibling, sibling_open = _split_sibling_after(i + 1)
                        if has_sibling:
                            skip_open_index = sibling_open
                            i += 1
                            continue
                    stack.pop()
                    if len(stack) == 1:
                        has_sibling, sibling_open = _split_sibling_after(i + 1)
                        if has_sibling:
                            skip_open_index = sibling_open
                elif top == "[" and ch == "]":
                    stack.pop()
            result.append(ch)
            i += 1
            continue

        result.append(ch)
        i += 1

    return "".join(result)


def _fix_mismatched_closers(text: str) -> str:
    """Replace mismatched closing brackets/braces with the correct counterpart.

    LLMs occasionally emit ``]`` where ``}`` is required (or vice-versa), e.g.::

        "motivation": {"short_term_goal": "X", "internal_conflict": "Y"],

    The ``]`` should be ``}`` because the opening was ``{``.  This function
    walks the text outside string literals and, whenever a closing character
    does not match the top of the opener stack, replaces it with the correct
    one.
    """
    result = list(text)
    stack: list[str] = []  # opener characters
    in_string = False
    escape_next = False

    for i, ch in enumerate(result):
        if escape_next:
            escape_next = False
            continue
        if ch == "\\":
            escape_next = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch in "{[":
            stack.append(ch)
        elif ch in "}]":
            if not stack:
                continue
            opener = stack[-1]
            expected = "}" if opener == "{" else "]"
            if ch != expected:
                result[i] = expected
            stack.pop()

    return "".join(result)


def _fix_mismatched_closers_v2(text: str) -> str:
    """Fix mismatched closers by INSERT/DELETE rather than replacement.

    Unlike ``_fix_mismatched_closers`` (which *replaces* the wrong closer),
    this function handles the two failure modes independently:

    * ``}`` where ``]`` is expected — **INSERT** ``]`` before the ``}``
      so that the array is closed *and* the parent object closer is kept.
      Example: ``"known_facts":["a","b"]}}`` becomes ``"known_facts":["a","b"]}}``
      while ``"known_facts":["a","b"}}`` becomes ``"known_facts":["a","b"]}}``.

    * ``]`` where ``}`` is expected — **DELETE** the stray ``]``
      so the object stays open for subsequent fields.
      Example: ``"physical_state":{"loc":"X"}],"emotion":`` becomes
      ``"physical_state":{"loc":"X"},"emotion":``.

    The dual case is also handled: when an array is closed by ``]`` while
    the parent object is still unclosed (``[..., {"description":"..."]``),
    INSERT the missing ``}`` so the object closes first. The discriminator
    is the most recent non-whitespace, non-string closer before the
    ``]`` — if it is ``}`` the ``]`` is a stray (DELETE), otherwise the
    object was never closed (INSERT ``}``).
    """
    result: list[str] = []
    stack: list[str] = []
    in_string = False
    escape_next = False
    last_closer: str | None = None

    for ch in text:
        if escape_next:
            result.append(ch)
            escape_next = False
            continue
        if ch == "\\":
            result.append(ch)
            escape_next = True
            continue
        if ch == '"':
            in_string = not in_string
            result.append(ch)
            continue
        if in_string:
            result.append(ch)
            continue
        if ch in " \t\r\n":
            result.append(ch)
            continue
        if ch in "{[":
            stack.append(ch)
            last_closer = None
            result.append(ch)
        elif ch == "}":
            if stack and stack[-1] == "[":
                result.append("]")
                stack.pop()
                if stack and stack[-1] == "{":
                    stack.pop()
            elif stack:
                stack.pop()
            last_closer = "}"
            result.append(ch)
        elif ch == "]":
            if stack and stack[-1] == "{":
                if last_closer == "}":
                    last_closer = "]"
                    continue
                result.append("}")
                stack.pop()
                result.append("]")
                if stack and stack[-1] == "[":
                    stack.pop()
                last_closer = "]"
                continue
            if stack and stack[-1] == "[":
                stack.pop()
            last_closer = "]"
            result.append(ch)
        else:
            result.append(ch)

    return "".join(result)


def _fix_premature_object_close_before_sibling_key(text: str) -> str:
    """Remove an object closer emitted before another sibling key.

    Long JSON outputs sometimes produce this shape near the end of an array
    object::

        "expected_hook": {...}}, "expected_payoffs": [...]

    The second ``}`` closes the chapter object too early; the following
    ``"expected_payoffs":`` is clearly another field in that same object.
    Dropping only that premature closer preserves the object and lets the
    normal trailing closers finish the array/root structure.
    """
    if not text or all(marker not in text for marker in (',"', ', "')):
        return text

    result: list[str] = []
    stack: list[str] = []
    in_string = False
    escape_next = False
    changed = False
    i = 0
    length = len(text)

    def _peek_object_key_name(pos: int) -> str | None:
        j = pos
        while j < length and text[j] in " \t\r\n":
            j += 1
        if j >= length or text[j] != '"':
            return None
        j += 1
        key_start = j
        key_escape = False
        while j < length:
            ch = text[j]
            if key_escape:
                key_escape = False
                j += 1
                continue
            if ch == "\\":
                key_escape = True
                j += 1
                continue
            if ch == '"':
                break
            j += 1
        if j >= length:
            return None
        key = text[key_start:j]
        j += 1
        while j < length and text[j] in " \t\r\n":
            j += 1
        if j < length and text[j] == ":":
            return key
        return None

    def _peek_object_key(pos: int) -> bool:
        return _peek_object_key_name(pos) is not None

    def _next_sibling_key(pos: int) -> str | None:
        j = pos
        while j < length and text[j] in " \t\r\n":
            j += 1
        if j >= length or text[j] not in {",", "，"}:
            return None
        return _peek_object_key_name(j + 1)

    def _next_is_sibling_key(pos: int) -> bool:
        return _next_sibling_key(pos) is not None

    while i < length:
        ch = text[i]

        if escape_next:
            result.append(ch)
            escape_next = False
            i += 1
            continue

        if ch == "\\":
            result.append(ch)
            escape_next = True
            i += 1
            continue

        if ch == '"':
            in_string = not in_string
            result.append(ch)
            i += 1
            continue

        if in_string:
            result.append(ch)
            i += 1
            continue

        if ch in "{[":
            stack.append(ch)
            result.append(ch)
            i += 1
            continue

        if ch == "}":
            if stack and stack[-1] == "{":
                next_key = _next_sibling_key(i + 1)
                if (
                    len(stack) >= 2
                    and stack[-2] == "["
                    and next_key is not None
                    and next_key not in _ARRAY_OBJECT_ITEM_START_KEYS
                ):
                    changed = True
                    i += 1
                    continue
                stack.pop()
            result.append(ch)
            i += 1
            continue

        if ch == "]":
            if stack and stack[-1] == "{" and len(stack) >= 2 and stack[-2] == "[":
                j = i + 1
                while j < length and text[j] in " \t\r\n":
                    j += 1
                if j < length and text[j] == "}" and _next_is_sibling_key(j + 1):
                    changed = True
                    i = j + 1
                    continue
            if stack and stack[-1] == "[":
                stack.pop()
            result.append(ch)
            i += 1
            continue

        result.append(ch)
        i += 1

    return "".join(result) if changed else text


def _fix_extra_object_closer_before_array_element(text: str) -> str:
    """Remove redundant ``}`` emitted between adjacent array objects.

    LLMs occasionally close one level too far between objects, e.g.::

        [{"name": "A"}}, {"name": "B"}]

    While scanning outside strings, if a ``}`` appears while the current
    container is an array and the next token is ``, { "key": ... }``, that
    closer cannot be valid for the array element.  Dropping only that closer
    preserves the following element and avoids replacing it with ``]``.
    """
    result: list[str] = []
    stack: list[str] = []
    in_string = False
    escape_next = False
    i = 0
    length = len(text)

    def _peek_object_key(pos: int) -> bool:
        j = pos
        while j < length and text[j] in " \t\r\n":
            j += 1
        if j >= length or text[j] != '"':
            return False
        j += 1
        key_escape = False
        while j < length:
            ch = text[j]
            if key_escape:
                key_escape = False
                j += 1
                continue
            if ch == "\\":
                key_escape = True
                j += 1
                continue
            if ch == '"':
                break
            j += 1
        if j >= length:
            return False
        j += 1
        while j < length and text[j] in " \t\r\n":
            j += 1
        return j < length and text[j] == ":"

    def _next_is_array_object(pos: int) -> bool:
        j = pos
        while j < length and text[j] in " \t\r\n":
            j += 1
        if j >= length or text[j] != ",":
            return False
        j += 1
        while j < length and text[j] in " \t\r\n":
            j += 1
        return j < length and text[j] == "{" and _peek_object_key(j + 1)

    while i < length:
        ch = text[i]

        if escape_next:
            result.append(ch)
            escape_next = False
            i += 1
            continue

        if ch == "\\":
            result.append(ch)
            escape_next = True
            i += 1
            continue

        if ch == '"':
            in_string = not in_string
            result.append(ch)
            i += 1
            continue

        if in_string:
            result.append(ch)
            i += 1
            continue

        if ch in "{[":
            stack.append(ch)
            result.append(ch)
            i += 1
            continue

        if ch in "}]":
            if stack:
                top = stack[-1]
                if (top == "{" and ch == "}") or (top == "[" and ch == "]"):
                    stack.pop()
                    result.append(ch)
                    i += 1
                    continue
                if top == "[" and ch == "}" and _next_is_array_object(i + 1):
                    i += 1
                    continue
            result.append(ch)
            i += 1
            continue

        result.append(ch)
        i += 1

    return "".join(result)


def _fix_missing_object_braces(text: str) -> str:
    """Reconstruct character objects when LLM flattens them into one.

    Chinese LLMs may emit all character fields inside a single ``{...}``
    instead of separate objects.  This finds each ``"name":`` at depth 1
    (inside the character array but not inside nested objects), extracts
    the field segment, and wraps it in ``{...}``.
    """
    import re

    arr_match = re.search(r'"characters"\s*:\s*\[', text)
    if not arr_match:
        return text

    arr_start = arr_match.end()

    # Find the matching ] for this array (look for ]}} pattern)
    arr_end_match = re.search(r'\]\s*\}\s*\}', text[arr_start:])
    if not arr_end_match:
        return text
    array_end = arr_start + arr_end_match.start()
    array_body = text[arr_start:array_end]
    after_array = text[array_end:]

    # Find "name": positions at depth 1 (inside the flattened object)
    name_positions: list[int] = []
    i = 0
    depth = 0
    in_string = False
    escape = False
    while i < len(array_body):
        ch = array_body[i]
        if escape:
            escape = False
            i += 1
            continue
        if ch == '\\':
            escape = True
            i += 1
            continue
        if ch == '"':
            # Check for "name": BEFORE toggling in_string
            if not in_string and depth == 1:
                if array_body[i:i+6] == '"name"':
                    rest = array_body[i+6:].lstrip()
                    if rest.startswith(':'):
                        name_positions.append(i)
            in_string = not in_string
            i += 1
            continue
        if in_string:
            i += 1
            continue
        if ch == '{':
            depth += 1
        elif ch == '}':
            depth -= 1
        elif ch == '[':
            depth += 1
        elif ch == ']':
            depth -= 1
        i += 1

    if len(name_positions) <= 1:
        return text

    # Reconstruct: each segment from "name": to next "name": becomes a character
    chars: list[str] = []
    for idx, pos in enumerate(name_positions):
        next_pos = name_positions[idx + 1] if idx + 1 < len(name_positions) else len(array_body)
        segment = array_body[pos:next_pos].rstrip(' ,')
        chars.append('{' + segment + '}')

    return text[:arr_start] + ','.join(chars) + after_array


def _fix_unquoted_array_elements(text: str) -> str:
    """Quote bare (unquoted) string elements inside JSON arrays.

    Some Chinese LLMs output array items as plain unquoted text lines without
    surrounding quotes or comma separators, for example::

        "known_facts": [
          风伏京的金丝异能可反向梳理蜃气；
          她察觉到自身"非人"倾向正在加剧。
        ]

    This function wraps each bare element in double-quotes (escaping any
    embedded ``"`` characters) and inserts missing comma separators between
    consecutive elements.
    """
    result: list[str] = []
    stack: list[str] = []          # '{' or '['
    in_string = False
    escape_next = False
    # Whether the most recently completed array element was emitted;
    # parallel to ``stack``, only meaningful when stack entry is ``[``.
    array_had_value: list[bool] = []
    i = 0
    length = len(text)

    def _is_json_value_start(idx: int) -> bool:
        if idx >= length:
            return False
        c = text[idx]
        if c in '"[{':
            return True
        if c in '-0123456789':
            return True
        for lit in ('true', 'false', 'null'):
            if text.startswith(lit, idx):
                end = idx + len(lit)
                if end >= length or text[end] in ' \t\r\n,}]':
                    return True
        return False

    while i < length:
        ch = text[i]

        if escape_next:
            result.append(ch)
            escape_next = False
            i += 1
            continue

        if ch == '\\':
            result.append(ch)
            escape_next = True
            i += 1
            continue

        if ch == '"':
            in_string = not in_string
            result.append(ch)
            # Exiting a string while inside an array → mark that a value was emitted
            if not in_string and stack and stack[-1] == '[':
                array_had_value[-1] = True
            i += 1
            continue

        if in_string:
            result.append(ch)
            i += 1
            continue

        if ch in '{[':
            stack.append(ch)
            array_had_value.append(False)
            result.append(ch)
            i += 1
            continue

        if ch in '}]':
            if stack:
                stack.pop()
                array_had_value.pop()
            # Closing a nested container inside an array → mark parent value emitted
            if stack and stack[-1] == '[':
                array_had_value[-1] = True
            result.append(ch)
            i += 1
            continue

        if ch == ',':
            if stack and stack[-1] == '[':
                array_had_value[-1] = False  # Reset: next value not yet seen
            result.append(ch)
            i += 1
            continue

        if ch in ' \t\r\n':
            result.append(ch)
            i += 1
            continue

        # Non-whitespace outside a string — check for bare array element
        if stack and stack[-1] == '[' and not _is_json_value_start(i):
            # Bare unquoted element: collect everything to end of line
            if array_had_value[-1]:
                result.append(',')
            j = i
            elem_chars: list[str] = []
            while j < length and text[j] not in '\r\n':
                c = text[j]
                if c == '"':
                    elem_chars.append('\\"')
                elif c == '\\':
                    if j + 1 < length:
                        nc = text[j + 1]
                        if nc in _VALID_JSON_ESCAPE_CHARS:
                            elem_chars.append(c)
                            elem_chars.append(nc)
                            j += 2
                            continue
                        else:
                            elem_chars.append('\\\\')
                            elem_chars.append(nc)
                            j += 2
                            continue
                    else:
                        elem_chars.append('\\\\')
                else:
                    elem_chars.append(c)
                j += 1

            elem = ''.join(elem_chars).rstrip()
            if elem:
                result.append('"')
                result.append(elem)
                result.append('"')
                array_had_value[-1] = True
            i = j
            continue

        result.append(ch)
        i += 1

    return ''.join(result)


def _fix_missing_array_closer_before_object_key(text: str) -> str:
    """Insert a missing ``]`` when an array unexpectedly transitions to object key syntax.

    Typical malformed fragment from long, token-capped LLM outputs::

        "beats_summary": ["A", "B", "main_plot_points": [...]

    The comma before ``"main_plot_points":`` indicates object-key syntax, but
    the parser is still inside an array context.  We conservatively insert ``]``
    right before that comma when:
    1) current container is an array, and
    2) lookahead is ``"some_key" :`` outside strings.
    """
    result: list[str] = []
    stack: list[str] = []
    in_string = False
    escape_next = False
    i = 0
    length = len(text)

    while i < length:
        ch = text[i]

        if escape_next:
            result.append(ch)
            escape_next = False
            i += 1
            continue

        if ch == "\\":
            result.append(ch)
            escape_next = True
            i += 1
            continue

        if ch == '"':
            in_string = not in_string
            result.append(ch)
            i += 1
            continue

        if not in_string and ch in "{[":
            stack.append(ch)
            result.append(ch)
            i += 1
            continue

        if not in_string and ch in "}]":
            if stack:
                top = stack[-1]
                if (top == "{" and ch == "}") or (top == "[" and ch == "]"):
                    stack.pop()
            result.append(ch)
            i += 1
            continue

        if not in_string and ch == "," and stack and stack[-1] == "[":
            j = i + 1
            while j < length and text[j] in " \t\r\n":
                j += 1

            key_like = False
            if j < length and text[j] == '"':
                k = j + 1
                key_escape = False
                while k < length:
                    key_char = text[k]
                    if key_escape:
                        key_escape = False
                        k += 1
                        continue
                    if key_char == "\\":
                        key_escape = True
                        k += 1
                        continue
                    if key_char == '"':
                        break
                    k += 1
                if k < length and text[k] == '"':
                    m = k + 1
                    while m < length and text[m] in " \t\r\n":
                        m += 1
                    key_like = m < length and text[m] == ":"

            if key_like:
                # We are likely missing the array closer right before this comma.
                result.append("]")
                stack.pop()

            result.append(ch)
            i += 1
            continue

        result.append(ch)
        i += 1

    return "".join(result)


def _fix_missing_object_closer_before_array_element(text: str) -> str:
    """Insert a missing ``}`` when a nested object's close brace is omitted
    before the next array element — a common LLM truncation pattern.

    Typical malformed fragment from LLM outputs::

        "relationships": {
            "沈照夜": "ally",
            "崔氏族人": "沉重负担"
        , {"name": "沈照夜", "role": "deuteragonist"}

    The ``}`` closing the ``relationships`` object is missing, so the parser
    sees the next ``{`` as a *key* inside the still-open object, producing
    "Expecting property name enclosed in double quotes".

    Strategy: scan the text tracking nesting depth.  When a string value
    ends with ``"`` and is immediately followed by ``, {`` where the ``{``
    looks like a new array element (i.e. ``"key":`` follows), AND the
    current open container count exceeds what the closers can account for,
    insert the appropriate number of ``}`` closers.
    """
    result: list[str] = []
    stack: list[str] = []
    in_string = False
    escape_next = False
    i = 0
    length = len(text)

    def _peek_object_key(pos: int) -> bool:
        """Check if position *pos* starts a JSON object key pattern ``"k":``."""
        j = pos
        while j < length and text[j] in " \t\r\n":
            j += 1
        if j >= length or text[j] != '"':
            return False
        k = j + 1
        key_escape = False
        while k < length:
            ch = text[k]
            if key_escape:
                key_escape = False
                k += 1
                continue
            if ch == "\\":
                key_escape = True
                k += 1
                continue
            if ch == '"':
                break
            k += 1
        if k >= length or text[k] != '"':
            return False
        m = k + 1
        while m < length and text[m] in " \t\r\n":
            m += 1
        return m < length and text[m] == ":"

    def _count_open_beyond_closers(from_pos: int, up_to: int) -> int:
        """Count unclosed ``{`` in result[from_pos:up_to] accounting for closers."""
        depth = 0
        s: list[str] = []
        for ch in result[from_pos:up_to]:
            if ch == "{" and (not s or s[-1] != "STR"):
                s.append("{")
                depth += 1
            elif ch == "}" and s and s[-1] == "{":
                s.pop()
                depth -= 1
        return max(0, depth) if s else 0

    while i < length:
        ch = text[i]

        if escape_next:
            result.append(ch)
            escape_next = False
            i += 1
            continue

        if ch == "\\":
            result.append(ch)
            escape_next = True
            i += 1
            continue

        if ch == '"':
            if in_string:
                in_string = False
                result.append(ch)
                i += 1
                j = i
                while j < length and text[j] in " \t\r\n":
                    j += 1
                if j < length and text[j] == ",":
                    k = j + 1
                    while k < length and text[k] in " \t\r\n":
                        k += 1
                    if k < length and text[k] == "{" and _peek_object_key(k + 1):
                        array_depth = sum(1 for s in stack if s == "[")
                        object_depth = len(stack) - array_depth
                        if object_depth >= 3:
                            closers_needed = object_depth - 2
                            for _ in range(closers_needed):
                                result.append("}")
                                if stack and stack[-1] == "{":
                                    stack.pop()
                            result.append(",")
                            result.append(" ")
                            i = k
                            continue
                continue
            in_string = True
            result.append(ch)
            i += 1
            continue

        if not in_string and ch in "{[":
            stack.append(ch)
            result.append(ch)
            i += 1
            continue

        if not in_string and ch in "}]":
            popped = False
            if stack:
                top = stack[-1]
                if (top == "{" and ch == "}") or (top == "[" and ch == "]"):
                    stack.pop()
                    popped = True
            result.append(ch)
            i += 1
            # After closing a nested object, detect pattern where a parent
            # object closer is missing before the next array element:
            #   "v2"}, {"data":   ← inner } present, outer } missing
            if ch == "}" and popped:
                j = i
                while j < length and text[j] in " \t\r\n":
                    j += 1
                if j < length and text[j] == ",":
                    k = j + 1
                    while k < length and text[k] in " \t\r\n":
                        k += 1
                    if k < length and text[k] == "{" and _peek_object_key(k + 1):
                        array_depth = sum(1 for s in stack if s == "[")
                        object_depth = len(stack) - array_depth
                        if object_depth >= 2:
                            closers_needed = object_depth - 1
                            for _ in range(closers_needed):
                                result.append("}")
                                if stack and stack[-1] == "{":
                                    stack.pop()
                            result.append(",")
                            result.append(" ")
                            i = k
                            continue
            continue

        result.append(ch)
        i += 1

    return "".join(result)


_JSON_NUMBER_PREFIX_RE = re.compile(
    r"-?(?:0|[1-9]\d*)(?:\.\d+)?(?:[eE][+-]?\d+)?"
)
"""Matches a JSON number prefix at the start of a candidate value."""

_QUOTED_VALUE_LINE_RE = re.compile(
    r'^(\s*"(?:[^"\\]|\\.)*"\s*:\s*)"(?P<body>.*)"(\s*[,}\]]?\s*)$',
)
"""Matches single-line JSON string values.

Group 1 is the `"key": "` prefix, group "body" is the raw value content, and
group 3 is the optional trailing delimiter.
"""


def _fix_missing_value_quotes(text: str) -> str:
    """Insert missing opening quotes for bare string values after ``:``.

    Some LLMs emit object values without an opening quote, especially in
    minified one-line payloads, e.g.::

        {"k":"v1\nk2": "v2", "k3": 裸字符串值}

    The previous line-oriented implementation only handled pretty-printed JSON.
    This state-machine implementation works for both single-line and multi-line
    JSON-like output by scanning the whole payload and repairing bare values
    wherever ``:`` appears outside quoted strings.
    """
    def _is_json_literal_start(raw: str, index: int) -> bool:
        if index >= len(raw):
            return False
        first = raw[index]
        if first in '"[{':
            return True
        if first in "-0123456789":
            m = _JSON_NUMBER_PREFIX_RE.match(raw[index:])
            if m is None:
                return False
            end = index + m.end()
            if end >= len(raw):
                return True
            return raw[end] in " \t\r\n,}]"
        for literal in ("true", "false", "null"):
            if raw.startswith(literal, index):
                end = index + len(literal)
                if end >= len(raw):
                    return True
                if raw[end] in " \t\r\n,}]":
                    return True
        return False

    result: list[str] = []
    i = 0
    length = len(text)
    in_string = False
    escape_next = False

    while i < length:
        ch = text[i]
        if escape_next:
            result.append(ch)
            escape_next = False
            i += 1
            continue
        if ch == "\\":
            result.append(ch)
            escape_next = True
            i += 1
            continue
        if ch == '"':
            in_string = not in_string
            result.append(ch)
            i += 1
            continue

        if not in_string and ch == ":":
            result.append(ch)
            i += 1

            while i < length and text[i] in " \t\r\n":
                result.append(text[i])
                i += 1

            if i >= length:
                break

            starter = text[i]
            if starter in ",}]" or _is_json_literal_start(text, i):
                continue

            # Bare string value: wrap until the next structural delimiter.
            result.append('"')
            while i < length and text[i] not in ",}]":
                result.append(text[i])
                i += 1

            while result and result[-1] in " \t\r\n":
                result.pop()
            if result and result[-1] == '"':
                result.pop()
            result.append('"')
            continue

        result.append(ch)
        i += 1

    return "".join(result)


def _escape_inner_quotes_in_strings_stateful(text: str) -> str:
    """Escape unescaped ASCII double-quotes inside JSON string values using a state machine.

    Handles both single-line (minified) and multi-line JSON.  The existing
    ``_escape_inner_quotes_in_quoted_values`` only works on lines matching the
    ``"key": "value"`` pattern — it cannot repair array string elements or
    minified JSON where everything is on one line.

    Lookahead heuristic: after a ``"`` inside a string, if the next
    non-whitespace character is NOT a JSON structural delimiter
    (``:``, ``,``, ``}``, ``]``, or another ``"``), treat the quote as an
    unescaped inner quote and escape it.

    Conservative: quotes followed by ``"`` are left as string-end to avoid
    corrupting JSON with missing commas (a different error category).

    This handles LLM output such as::

        {"continuity_errors": ["文中描述"风伏京"在此存在矛盾"]}

    Where inner ASCII quotes around Chinese names/phrases are not escaped.

    Also handles the ``""`` (double-quote pair at end of value) pattern::

        ["昨夜宴会异象有"数人同时目睹"",]

    Where a Chinese closing quote sits right before the JSON string closer.
    A double-lookahead determines whether the second ``"`` is followed by a
    structural delimiter (``,:}]``), meaning the *first* ``"`` is inner
    content and should be escaped.
    """
    result: list[str] = []
    i = 0
    n = len(text)
    in_string = False
    escape_next = False

    while i < n:
        ch = text[i]
        if escape_next:
            result.append(ch)
            escape_next = False
            i += 1
            continue
        if ch == "\\":
            result.append(ch)
            escape_next = True
            i += 1
            continue
        if ch == '"':
            if not in_string:
                in_string = True
                result.append(ch)
            else:
                # Lookahead: skip whitespace and peek at next structural char.
                # If the char is a JSON delimiter → string end.
                # Otherwise this quote is content and should be escaped.
                j = i + 1
                while j < n and text[j] in " \t\r\n":
                    j += 1
                if j >= n or text[j] in ',:}]':
                    in_string = False
                    result.append(ch)
                elif text[j] == '"':
                    # Double-lookahead: "X"" could be a Chinese closing quote
                    # followed by the real JSON string closer.
                    # Check what comes after the SECOND quote.
                    k = j + 1
                    while k < n and text[k] in " \t\r\n":
                        k += 1
                    if k >= n or text[k] in ',:}]':
                        # Pattern: ..."内容"",  → first " is inner Chinese
                        # quote, second " is the real string closer.
                        result.append('\\"')
                    else:
                        # Pattern: ..."value" "next_key": → first " ends
                        # current string, second " opens a new one.
                        in_string = False
                        result.append(ch)
                else:
                    # Inner quote — escape it and stay in string
                    result.append('\\"')
            i += 1
            continue
        result.append(ch)
        i += 1

    return "".join(result)


def _escape_inner_quotes_in_quoted_values(text: str) -> str:
    """Escape bare ASCII quotes inside already-quoted JSON string values.

    Preserves Chinese typographic quotes ``"`` (U+201C) and ``"`` (U+201D)
    as content inside the string value. Only escapes bare ASCII ``"`` (U+0022).

    This handles responses like::

        "motivation": "需要排除"个人造假"的可能性",

    Where the Chinese quotes are meant as emphasis within the string value,
    not as JSON delimiters. The outer ASCII quotes mark the JSON string,
    while the inner Chinese quotes are preserved as-is.
    """
    _LEFT_CURLY = "\u201c"
    _RIGHT_CURLY = "\u201d"
    _PLACEHOLDER_LEFT = "\u0001"
    _PLACEHOLDER_RIGHT = "\u0002"

    fixed_lines: list[str] = []
    for line in text.split("\n"):
        match = _QUOTED_VALUE_LINE_RE.match(line)
        if not match:
            fixed_lines.append(line)
            continue

        body = match.group("body")
        body = body.replace(_LEFT_CURLY, _PLACEHOLDER_LEFT).replace(_RIGHT_CURLY, _PLACEHOLDER_RIGHT)

        escaped_body_chars: list[str] = []
        escape_next = False
        for ch in body:
            if escape_next:
                escaped_body_chars.append(ch)
                escape_next = False
                continue
            if ch == "\\":
                escaped_body_chars.append(ch)
                escape_next = True
                continue
            if ch == '"':
                escaped_body_chars.append('\\"')
                continue
            escaped_body_chars.append(ch)

        escaped_body = "".join(escaped_body_chars)
        escaped_body = escaped_body.replace(_PLACEHOLDER_LEFT, _LEFT_CURLY).replace(_PLACEHOLDER_RIGHT, _RIGHT_CURLY)

        fixed_lines.append(
            match.group(1) + '"' + escaped_body + '"' + match.group(3)
        )

    return "\n".join(fixed_lines)


def _fix_curly_double_quotes(text: str) -> str:
    """Normalize Unicode curly/smart double-quote pairs to ASCII double-quotes.

    Chinese LLMs sometimes emit LEFT DOUBLE QUOTATION MARK (U+201C ``\u201c``)
    and RIGHT DOUBLE QUOTATION MARK (U+201D ``\u201d``) as string delimiters or
    typographic quotes inside JSON output.  These Unicode characters are not
    valid JSON string delimiters (only U+0022 ``"`` is), which breaks both
    ``in_string`` tracking in other repair functions and standard JSON parsers.

    This is intentionally a simple unconditional replacement — it is meant to
    run *before* other context-aware repair functions so that consistent ASCII
    quote tracking becomes possible.  ``_fix_missing_value_quotes`` is then able
    to escape any resulting unescaped ``"`` characters within bare string values.
    """
    return text.replace("\u201c", '"').replace("\u201d", '"')


def _fix_single_quotes(text: str) -> str:
    """Normalize single-quote JSON keys/values to standard double-quote JSON.

    Some LLMs emit single quotes instead of double quotes for JSON keys and
    string values, e.g. {'name': "it's fine"} or {'key': 'value'}.  This is
    invalid JSON (only U+0022 ``"`` is valid as a string delimiter).

    The function preserves single quotes *inside* string values where they
    represent contractions or apostrophes (e.g. "it's fine" → "it's fine").
    """
    result: list[str] = []
    i = 0
    while i < len(text):
        ch = text[i]
        if ch == "'":
            if i == 0 or text[i - 1] in " \t\n{" or (i > 0 and text[i - 1] in ":,"):
                quote_pos = i + 1
                if quote_pos < len(text) and text[quote_pos] in " \t\n{":
                    j = quote_pos + 1
                    while j < len(text):
                        if text[j] == "\\":
                            j += 2
                            continue
                        if text[j] == "'":
                            j += 1
                            if j < len(text) and text[j] in ":,} \t\n":
                                result.append('"')
                                i = quote_pos
                                break
                        j += 1
                    else:
                        result.append("'")
                        i += 1
                else:
                    result.append('"')
                    i += 1
            else:
                result.append(ch)
                i += 1
        else:
            result.append(ch)
            i += 1
    return "".join(result)


def _fix_single_quotes_simple(text: str) -> str:
    """Convert single-quote JSON to double-quote JSON using placeholder technique.

    This simpler approach:
    1. Replace single quotes inside double-quoted strings with a placeholder
    2. Replace all remaining single quotes with double quotes
    3. Restore the placeholder back to single quotes
    """
    SINGLE_IN_DOUBLE = "\u0003"
    SINGLE_IN_DOUBLE_ESCAPED = "\\'"
    result: list[str] = []
    in_string = False
    i = 0
    while i < len(text):
        ch = text[i]
        if ch == "\\" and i + 1 < len(text):
            if text[i + 1] == "'":
                if in_string:
                    result.append(SINGLE_IN_DOUBLE_ESCAPED)
                else:
                    result.append("'")
                i += 2
                continue
            result.append(ch)
            i += 1
            continue
        if ch == '"':
            in_string = not in_string
            result.append(ch)
            i += 1
            continue
        if ch == "'" and in_string:
            result.append(SINGLE_IN_DOUBLE)
            i += 1
            continue
        if ch == "'" and not in_string:
            result.append('"')
            i += 1
            continue
        result.append(ch)
        i += 1
    output = "".join(result)
    output = output.replace(SINGLE_IN_DOUBLE, "'")
    return output


def _fix_invalid_escape_sequences(text: str) -> str:
    """Drop invalid JSON escape sequences inside string values.

    LLMs sometimes emit ``\\'`` (backslash + single-quote) or other
    non-standard escapes such as ``\\-``, ``\\!``, ``\\(``, ``\\)`` inside
    JSON strings.  In JSON (RFC 8259) the only legal escape sequences are::

        \\"  \\\\  \\/  \\b  \\f  \\n  \\r  \\t  \\uXXXX

    Any other ``\\X`` is repaired by dropping the backslash and keeping ``X``.
    Sequences outside of string values are left untouched.
    """
    result: list[str] = []
    in_string = False
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if in_string and ch == "\\" and i + 1 < n:
            next_ch = text[i + 1]
            if next_ch == "u":
                hex_part = text[i + 2 : i + 6]
                if len(hex_part) == 4 and all(item in _HEX_DIGITS for item in hex_part):
                    result.append(ch)
                    result.append(next_ch)
                    result.append(hex_part)
                    i += 6
                    continue
                # Incomplete or malformed unicode escape, often caused by
                # length truncation. Keep the visible characters as literal text.
                result.append(next_ch)
                i += 2
                continue
            if next_ch in _VALID_JSON_ESCAPE_CHARS:
                # Valid escape — keep both characters and advance past them so
                # that an escaped double-quote does NOT toggle in_string below.
                result.append(ch)
                result.append(next_ch)
                i += 2
                continue
            else:
                # Invalid escape — drop the backslash, preserve the character.
                result.append(next_ch)
                i += 2
                continue
        if ch == '"':
            in_string = not in_string
        result.append(ch)
        i += 1
    return "".join(result)


def _fix_invalid_escapes_then_close_truncated_string(text: str) -> str:
    """Repair invalid escapes, then close only if that revealed a truncation.

    Generic string-closing is intentionally too risky for long payloads: a
    model may be cut thousands of characters inside a prose field, and accepting
    that partial field can hide data loss.  This helper is narrower: it only
    closes an open string when invalid escape repair actually changed the text,
    which covers the observed ``\\u`` cutoff without turning every mid-string
    truncation into valid JSON.
    """
    fixed = _fix_invalid_escape_sequences(text)
    if fixed == text:
        return text

    stack: list[str] = []
    in_string = False
    escape_next = False
    for ch in fixed:
        if escape_next:
            escape_next = False
            continue
        if in_string and ch == "\\":
            escape_next = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch in "{[":
            stack.append(ch)
            continue
        if ch in "}]":
            if not stack:
                continue
            opener = stack[-1]
            if (opener == "{" and ch == "}") or (opener == "[" and ch == "]"):
                stack.pop()

    if not in_string:
        return fixed

    attempt = fixed[:-1] if escape_next and fixed.endswith("\\") else fixed
    suffix = "".join("}" if opener == "{" else "]" for opener in reversed(stack))
    return attempt + '"' + suffix


_OBJECT_AS_ARRAY_RE = re.compile(
    r'(\{)\s*("(?:[^"\\]|\\.)*"(?:\s*,\s*"(?:[^"\\]|\\.)*")*)\s*(\})',
)
"""
Matches JSON objects whose values are bare quoted strings with no `:` delimiter,
e.g. { "fact1", "fact2" } — a common LLM mistake when new_world_facts should
be an array.  Converts them to arrays so downstream normalizers can handle them.
"""


def _repair_object_as_array(text: str) -> str:
    """Convert bare-string pseudo-objects into JSON arrays.

    LLMs occasionally emit ``{ "item1", "item2" }`` instead of the expected
    ``{"key": "value"}`` dict or ``["item1", "item2"]`` list.  The result is
    invalid JSON.  This repair replaces every such sub-object with a syntactically
    valid array while leaving intact all real key-value objects.
    """
    return _OBJECT_AS_ARRAY_RE.sub(lambda m: "[" + m.group(2) + "]", text)


# Matches `"key_name:` (missing closing quote before colon) where the value
# starts with [, { or ".  Common with Chinese LLMs that drop one quote.
_MISSING_KEY_CLOSE_QUOTE_RE = re.compile(r'"([a-z_][a-z0-9_]*):(\s*[\[\{"])')
_EXTRA_KEY_OPEN_QUOTE_RE = re.compile(
    r'(?P<prefix>[{,]\s*)""(?P<key>[A-Za-z_][A-Za-z0-9_]*)"\s*:'
)


def _fix_missing_key_close_quote(text: str) -> str:
    """Fix ``"key_name:value`` → ``"key_name": value`` pattern.

    Some LLMs emit JSON keys without the closing double-quote before the
    colon, e.g. ``"new_characters:[]`` instead of ``"new_characters": []``.
    """
    return _MISSING_KEY_CLOSE_QUOTE_RE.sub(r'"\1":\2', text)


def _fix_extra_key_open_quote(text: str) -> str:
    """Fix ``{""key": value}`` -> ``{"key": value}`` in object key position."""

    return _EXTRA_KEY_OPEN_QUOTE_RE.sub(r'\g<prefix>"\g<key>":', text)


def _fix_unquoted_object_keys(text: str) -> str:
    """Quote JSON5-style bare ASCII object keys.

    Some providers occasionally return JavaScript/JSON5-like objects such as
    ``{world_rules:{era:"当代"}}``.  This repair is intentionally narrow: it
    only fires outside strings, when the parser state is inside an object that
    is expecting a key, and only for identifier-like ASCII keys immediately
    followed by a colon.
    """
    result: list[str] = []
    stack: list[str] = []
    in_string = False
    escape_next = False
    expect_key_stack: list[bool] = []
    i = 0
    length = len(text)

    def _identifier_end(pos: int) -> int:
        if pos >= length:
            return pos
        first = text[pos]
        if not (first == "_" or "a" <= first <= "z" or "A" <= first <= "Z"):
            return pos
        j = pos + 1
        while j < length:
            ch = text[j]
            if ch == "_" or "a" <= ch <= "z" or "A" <= ch <= "Z" or "0" <= ch <= "9":
                j += 1
                continue
            break
        return j

    def _object_expects_key() -> bool:
        return bool(stack and stack[-1] == "{" and expect_key_stack and expect_key_stack[-1])

    while i < length:
        ch = text[i]

        if escape_next:
            result.append(ch)
            escape_next = False
            i += 1
            continue

        if ch == "\\":
            result.append(ch)
            escape_next = True
            i += 1
            continue

        if ch == '"':
            in_string = not in_string
            result.append(ch)
            i += 1
            continue

        if in_string:
            result.append(ch)
            i += 1
            continue

        if ch == "{":
            stack.append(ch)
            expect_key_stack.append(True)
            result.append(ch)
            i += 1
            continue

        if ch == "[":
            stack.append(ch)
            expect_key_stack.append(False)
            result.append(ch)
            i += 1
            continue

        if ch in "}]":
            if stack:
                stack.pop()
                expect_key_stack.pop()
            result.append(ch)
            i += 1
            continue

        if ch == ",":
            if stack and stack[-1] == "{":
                expect_key_stack[-1] = True
            result.append(ch)
            i += 1
            continue

        if ch == ":":
            if stack and stack[-1] == "{":
                expect_key_stack[-1] = False
            result.append(ch)
            i += 1
            continue

        if ch in " \t\r\n":
            result.append(ch)
            i += 1
            continue

        if _object_expects_key():
            key_end = _identifier_end(i)
            if key_end > i:
                j = key_end
                while j < length and text[j] in " \t\r\n":
                    j += 1
                if j < length and text[j] == ":":
                    result.append('"')
                    result.append(text[i:key_end])
                    result.append('"')
                    result.append(text[key_end : j + 1])
                    expect_key_stack[-1] = False
                    i = j + 1
                    continue

        result.append(ch)
        i += 1

    return "".join(result)


def _fix_missing_key_open_quote(text: str) -> str:
    """Fix ``key_name": value`` → ``"key_name": value`` in object key position.

    Long minified JSON responses sometimes drop the opening quote of an ASCII
    field name after a comma, for example ``,"event":"...'",weave_notes":"...``.
    The repair is intentionally narrow: it only fires outside strings, while an
    object is expecting a key, and only for identifier-like keys followed by
    ``"`` and ``:``.
    """
    result: list[str] = []
    stack: list[str] = []
    in_string = False
    escape_next = False
    expect_key_stack: list[bool] = []
    i = 0
    length = len(text)

    def _identifier_end(pos: int) -> int:
        if pos >= length:
            return pos
        first = text[pos]
        if not (first == "_" or "a" <= first <= "z" or "A" <= first <= "Z"):
            return pos
        j = pos + 1
        while j < length:
            ch = text[j]
            if ch == "_" or "a" <= ch <= "z" or "A" <= ch <= "Z" or "0" <= ch <= "9":
                j += 1
                continue
            break
        return j

    def _object_expects_key() -> bool:
        return bool(stack and stack[-1] == "{" and expect_key_stack and expect_key_stack[-1])

    while i < length:
        ch = text[i]

        if escape_next:
            result.append(ch)
            escape_next = False
            i += 1
            continue

        if ch == "\\":
            result.append(ch)
            escape_next = True
            i += 1
            continue

        if ch == '"':
            in_string = not in_string
            result.append(ch)
            i += 1
            continue

        if in_string:
            result.append(ch)
            i += 1
            continue

        if ch == "{":
            stack.append(ch)
            expect_key_stack.append(True)
            result.append(ch)
            i += 1
            continue

        if ch == "[":
            stack.append(ch)
            expect_key_stack.append(False)
            result.append(ch)
            i += 1
            continue

        if ch in "}]":
            if stack:
                stack.pop()
                expect_key_stack.pop()
            result.append(ch)
            i += 1
            continue

        if ch == ",":
            if stack and stack[-1] == "{":
                expect_key_stack[-1] = True
            result.append(ch)
            i += 1
            continue

        if ch == ":":
            if stack and stack[-1] == "{":
                expect_key_stack[-1] = False
            result.append(ch)
            i += 1
            continue

        if ch in " \t\r\n":
            result.append(ch)
            i += 1
            continue

        if _object_expects_key():
            key_end = _identifier_end(i)
            if key_end > i and key_end < length and text[key_end] == '"':
                j = key_end + 1
                while j < length and text[j] in " \t\r\n":
                    j += 1
                if j < length and text[j] == ":":
                    result.append('"')
                    result.append(text[i:key_end])
                    result.append('"')
                    i = key_end + 1
                    continue

        result.append(ch)
        i += 1

    return "".join(result)


_NEWLINE_KV_SEP_RE = re.compile(r'\\n([^"\\]{1,50})":')
_INLINE_KV_SEP_RE = re.compile(r'([；;])([^"\\{}\[\]:,\r\n]{1,50})":')
_SINGLE_QUOTED_INLINE_KV_SEP_RE = re.compile(
    r"'\s*,\s*'([^\"\\{}\[\]:,\r\n]{1,50})\":"
)


def _fix_newline_kv_separator(text: str) -> str:
    """Fix LLM pattern where JSON object entries are separated by \\n instead of commas.

    Some LLMs output::

        {"key1": "value1\\nkey2": "value2"}

    instead of::

        {"key1": "value1", "key2": "value2"}

    The ``\\n`` (JSON newline escape sequence) is used as a separator between
    key-value pairs inside an object, causing the parser to see a completed
    string followed by an unexpected ``':'`` delimiter.
    """
    return _NEWLINE_KV_SEP_RE.sub(r'", "\1":', text)


def _fix_inline_kv_separator(text: str) -> str:
    """Fix inline relationship entries folded into a previous string value.

    Character-bible responses sometimes write several mapping entries as one
    semicolon-delimited value::

        "沈照夜": "亦敌亦友；太平公主": "权力博弈"

    or accidentally mix Python-style single quotes into a JSON string::

        "陈默": "表兄弟', '沈知微": "工作对接关系"

    The semicolon is content for the previous relationship value, while the
    following short label should become the next object key.  This turns the
    fragment into::

        "沈照夜": "亦敌亦友；", "太平公主": "权力博弈"
    """

    def _replace(match: re.Match[str]) -> str:
        separator = match.group(1)
        key = match.group(2).strip()
        if not key:
            return match.group(0)
        return f'{separator}", "{key}":'

    repaired = _INLINE_KV_SEP_RE.sub(_replace, text)
    return _SINGLE_QUOTED_INLINE_KV_SEP_RE.sub(r'", "\1":', repaired)


def _log_repair(trimmed: int, original_len: int, ratio: float) -> None:
    """Log JSON repair operation."""
    level = logging.WARNING if ratio > 0.3 else logging.DEBUG
    logger.log(
        level,
        "json_repair trimmed %d/%d chars (%.0f%%) to produce valid JSON",
        trimmed,
        original_len,
        ratio * 100,
    )


def _fix_missing_object_opener_in_array(text: str) -> str:
    text = re.sub(r'(")}\s*,\s*"name"\s*:', r'"},{"name":', text)
    text = re.sub(r'}\s*,\s*"name"\s*:', r'},{"name":', text)
    # Some long minified arrays of objects lose only the opening brace for the
    # next item, while the item's key/value content is still intact:
    #   [{"chapter_end":15},"chapter_end":40,"chapter_start":16,...}]
    # Turn the flattened key into a new object opener so the existing closing
    # brace after the value can be preserved.
    item_start_keys = ("chapter_end", "chapter_number", "chapter_start", "event")
    item_key_alt = "|".join(item_start_keys)
    text = re.sub(rf'}}\s*,\s*"({item_key_alt})"\s*:', r'},{"\1":', text)
    # LLMs sometimes put an empty string placeholder between array objects and
    # then flatten the next object's first key, e.g.
    # [{"source_type":"main_plot"},"","source_type":"subplot", ...}]
    # This is common in subplot `weave_links`; convert the placeholder into an
    # object opener while leaving ordinary string arrays untouched.
    object_keys = (
        "source_type",
        "source_ref",
        "target_subplot",
        "trigger_chapter",
        "link_type",
        "description",
    )
    key_alt = "|".join(object_keys)
    text = re.sub(rf'(\[|,)\s*""\s*,\s*"({key_alt})"\s*:', r'\1{"\2":', text)
    return text
