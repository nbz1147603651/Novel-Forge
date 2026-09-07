"""String manipulation utilities."""

from __future__ import annotations

import re
from typing import Any


def trim_text(value: Any, limit: int) -> str:
    """Trim long text while keeping sentence-like boundaries when possible.
    
    Args:
        value: Text to trim
        limit: Maximum character count
        
    Returns:
        Trimmed text with ellipsis if truncated
        
    Examples:
        >>> trim_text("这是一个很长的句子。这是另一个句子。", 10)
        "这是一个很长的句子…"
    """
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    if limit <= 1:
        return text[:limit]

    # Split at sentence boundaries (Chinese and English punctuation)
    parts = [p.strip() for p in re.split(r"(?<=[。！？!?；;])", text) if p.strip()]
    if parts:
        kept: list[str] = []
        current = 0
        for part in parts:
            extra = len(part) + (0 if not kept else 1)
            if current + extra > limit - 1:
                break
            kept.append(part)
            current += extra
        # Only use truncated version if we've kept at least 60% of the limit
        if kept and current >= int(limit * 0.6):
            return " ".join(kept) + "…"
    return text[: limit - 1] + "…"


def extract_and_trim(
    obj: dict[str, Any] | Any,
    key: str,
    limit: int,
) -> str:
    """Extract a field from a dict and trim it.

    Args:
        obj: Source object (expected dict)
        key: Key to extract
        limit: Character limit for trimming

    Returns:
        Trimmed text, or empty string if obj is not a dict
        
    Examples:
        >>> char = {"name": "张三丰", "description": "一个很长的描述"}
        >>> extract_and_trim(char, "name", 3)
        "张三…"
    """
    if not isinstance(obj, dict):
        return ""
    value = str(obj.get(key, "") or "").strip()
    return trim_text(value, limit)


def clean_str(value: Any, *, limit: int | None = None) -> str:
    """Safely convert any value to a clean string.

    Handles all falsy values (None, False, 0, [], {}) by returning "".
    Optionally truncates long strings with an ellipsis, stripping trailing
    Chinese/English punctuation at the cut point.

    Args:
        value: Any value to convert
        limit: Optional max character count. If the cleaned string exceeds
            this limit, it is truncated with "…" and trailing punctuation
            is stripped at the cut point.

    Returns:
        Stripped string, or empty string if value is falsy

    Examples:
        >>> clean_str(None)
        ''
        >>> clean_str("  hello  ")
        'hello'
        >>> clean_str(False)
        ''
        >>> clean_str("这是一个很长的句子需要截断", limit=8)
        '这是一个很长…'
    """
    text = str(value or "").strip()
    if limit is not None and len(text) > limit:
        return text[: limit - 1].rstrip("，。、；： \n") + "…"
    return text


def carry_forward_text(item: Any) -> str:
    """Extract the human-readable text of a carry-forward item.

    Accepts the structured ``CarryForwardItem`` (or dict with ``text``), the
    legacy plain-string form, and bare strings. Returns "" for empty items so
    callers can ``filter`` directly. This is the single coercion point used by
    prompt-context builders and carry-forward verification so the structured
    schema upgrade does not require per-consumer rewrites.
    """
    if item is None:
        return ""
    if isinstance(item, str):
        return item.strip()
    if isinstance(item, dict):
        return clean_str(item.get("text") or item.get("item") or item.get("content"))
    # CarryForwardItem (pydantic model) — read its ``text`` attribute.
    text = getattr(item, "text", None)
    return clean_str(text)


def carry_forward_status(item: Any) -> str:
    """Return the normalized status of a carry-forward item.

    One of ``open`` / ``abandoned`` / ``deferred``. Defaults to ``open`` for
    legacy plain-string items (which carry no status metadata).
    """
    if isinstance(item, str):
        return "open"
    if isinstance(item, dict):
        status = clean_str(item.get("status")).lower()
        return status if status in {"open", "abandoned", "deferred"} else "open"
    status = clean_str(getattr(item, "status", "") or "").lower()
    return status if status in {"open", "abandoned", "deferred"} else "open"


def normalize_input_list(value: Any) -> list[str]:
    """Normalise various input formats into a deduplicated string list.

    Accepts list, string (split on ``; / \\n / ；``), or dict.
    
    Args:
        value: Input value (str, list, dict, or None)
        
    Returns:
        Deduplicated list of strings
        
    Examples:
        >>> normalize_input_list("a;b;c;b")
        ["a", "b", "c"]
        >>> normalize_input_list(["x", "y", "x"])
        ["x", "y"]
    """
    items: list[str] = []
    if isinstance(value, list):
        for item in value:
            text = str(item or "").strip()
            if text:
                items.append(text)
    elif isinstance(value, str):
        fragments = [part.strip() for part in re.split(r"[;\n；]+", value)]
        items.extend(fragment for fragment in fragments if fragment)
    elif isinstance(value, dict):
        for raw_key, raw_value in value.items():
            k = str(raw_key or "").strip()
            v = str(raw_value or "").strip()
            if k and v:
                items.append(f"{k}: {v}")
            elif k:
                items.append(k)
            elif v:
                items.append(v)

    # Deduplicate while preserving order
    deduped: list[str] = []
    seen: set[str] = set()
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        deduped.append(item)
    return deduped


def normalize_string_list(
    value: Any,
    *,
    max_chars: int = 220,
    split_long: bool = True,
) -> list[str]:
    """Normalize various input formats to a deduplicated string list.
    
    Args:
        value: Input value (str, list, dict, or None)
        max_chars: Maximum characters per item
        split_long: Whether to split long strings into chunks
        
    Returns:
        Deduplicated list of strings, each within max_chars limit
    """
    raw: list[str] = []
    
    if isinstance(value, list):
        for item in value:
            text = clean_str(item)
            if text:
                raw.append(text)
    elif isinstance(value, str):
        text = clean_str(value)
        if text:
            raw = [text]
    elif isinstance(value, dict):
        for key, val in value.items():
            key_text = clean_str(key)
            val_text = clean_str(val)
            if key_text and val_text:
                raw.append(f"{key_text}: {val_text}")
            elif key_text:
                raw.append(key_text)
            elif val_text:
                raw.append(val_text)
    
    if not raw:
        return []
    
    # Split long texts if needed
    normalized: list[str] = []
    if split_long:
        for item in raw:
            normalized.extend(_split_long_text(item, max_chars))
    else:
        normalized = [item[:max_chars] for item in raw if len(item) <= max_chars * 1.2]
    
    # Deduplicate while preserving order
    seen: set[str] = set()
    deduped: list[str] = []
    for item in normalized:
        if item in seen:
            continue
        seen.add(item)
        deduped.append(item)
    
    return deduped


def _split_long_text(text: str, max_chars: int) -> list[str]:
    """Split long text into manageable chunks at natural boundaries.
    
    Args:
        text: Text to split
        max_chars: Maximum characters per chunk
        
    Returns:
        List of text chunks
    """
    compact = " ".join(text.split())
    if len(compact) <= max_chars:
        return [compact]
    
    # Split at sentence boundaries
    parts = [p.strip() for p in re.split(r"[；;。！？!?]", compact) if p.strip()]
    
    if not parts:
        return [compact[:max_chars]]
    
    chunks: list[str] = []
    current = ""
    
    for part in parts:
        candidate = f"{current}；{part}" if current else part
        if len(candidate) <= max_chars:
            current = candidate
            continue
        if current:
            chunks.append(current)
        current = part[:max_chars]
    
    if current:
        chunks.append(current)
    
    return chunks or [compact[:max_chars]]


def estimate_tokens(text_length: int, *, buffer: float = 1.2) -> int:
    """Estimate token count from text length.
    
    For Chinese text: 1 character ≈ 2 tokens
    For English: 1 word ≈ 1-1.3 tokens, 4 characters ≈ 1 token
    
    Args:
        text_length: Length of text in characters
        buffer: Safety buffer multiplier (default 1.2 = 20% buffer)
        
    Returns:
        Estimated token count
        
    Examples:
        >>> estimate_tokens(100)  # Chinese text
        240
    """
    # Conservative estimate: Chinese ~2 tokens per char
    # This buffer ensures we don't exceed limits
    return int(text_length * 2 * buffer)


def calculate_safe_max_tokens(
    target_output_chars: int,
    *,
    prompt_overhead: int = 2000,
    model_limit: int = 16384,
    safety_margin: float = 0.85,
    min_tokens: int = 4096,
) -> int:
    """Calculate safe max_tokens considering prompt length overhead.
    
    When generating long content, the actual available completion space is
    model_limit - prompt_tokens. This function calculates max_tokens that
    leaves room for both the prompt and a safety buffer.
    
    Args:
        target_output_chars: Target output length in characters (for Chinese text)
        prompt_overhead: Estimated tokens consumed by the prompt/context
            (default 2000 ≈ 1000 Chinese chars of prompt)
        model_limit: Maximum tokens supported by the model (default 16384)
        safety_margin: Fraction of available space to use (default 0.85)
        min_tokens: Minimum tokens to allocate (default 4096)
    
    Returns:
        Safe max_tokens value that accounts for prompt overhead
    
    Examples:
        >>> calculate_safe_max_tokens(5000)  # 5k Chinese chars target
        ~8500 (leaves room for 2k prompt tokens)
    """
    estimated_output_tokens = estimate_tokens(target_output_chars, buffer=1.1)
    available = model_limit - prompt_overhead
    safe_limit = int(available * safety_margin)
    return max(min_tokens, min(estimated_output_tokens, safe_limit, model_limit))


def normalize_chinese_quotes(text: str) -> str:
    """Normalize quote marks in Chinese prose text.

    Converts ASCII straight double quotes to proper Chinese double quotation
    marks (paired \u201c…\u201d), which is the standard for Chinese fiction
    dialogue.  Also converts any remaining paired ASCII single quotes used
    as dialogue markers to Chinese single quotation marks (\u2018…\u2019).

    The function uses simple parity-based pairing: the first unmatched quote
    becomes an opening mark, the next becomes a closing mark, and so on.
    """
    # ── Pass 1: paired ASCII double quotes → Chinese double quotes ──
    result: list[str] = []
    opening = True
    for ch in text:
        if ch == '"':
            result.append('\u201c' if opening else '\u201d')
            opening = not opening
        else:
            result.append(ch)
    text = ''.join(result)

    # ── Pass 2: paired ASCII single quotes used as dialogue ──────────
    # Only convert when surrounded by CJK context (avoids breaking
    # English apostrophes like "it's").
    _CJK_AROUND = re.compile(
        r"(?<=[\u4e00-\u9fff\u3000-\u303f\uff00-\uffef\u201c\u201d])"
        r"'"
        r"|"
        r"'"
        r"(?=[\u4e00-\u9fff\u3000-\u303f\uff00-\uffef\u201c\u201d])"
    )
    # Collect positions of CJK-adjacent single quotes
    positions: set[int] = set()
    for m in _CJK_AROUND.finditer(text):
        positions.add(m.start())
    if positions:
        out: list[str] = []
        sq_opening = True
        for i, ch in enumerate(text):
            if ch == "'" and i in positions:
                out.append('\u2018' if sq_opening else '\u2019')
                sq_opening = not sq_opening
            else:
                out.append(ch)
        text = ''.join(out)

    return text


# Detect whether a string looks like Chinese prose rather than raw JSON.
_CJK_RE = re.compile(r'[\u4e00-\u9fff]')


def _maybe_normalize_quotes(text: str) -> str:
    """Apply quote normalization only when text looks like Chinese prose.

    Avoids mutating JSON passthrough such as {"status": "成功"}, while still
    normalizing short story lines like 她说: "走吧。".
    """
    stripped = text.strip()
    if (stripped.startswith('{') and stripped.endswith('}')) or (
        stripped.startswith('[') and stripped.endswith(']')
    ):
        return text
    if '"' not in text and "'" not in text:
        return text
    if len(_CJK_RE.findall(text[:300])) >= 2:
        return normalize_chinese_quotes(text)
    return text


def extract_text_content(content: str) -> str:
    """Extract text from potential JSON wrapper.
    
    Many models return content wrapped in JSON like {"title": "...", "content": "..."}.
    This function extracts the actual text content.
    
    Args:
        content: Raw model output, possibly JSON-wrapped
        
    Returns:
        Extracted text content or original if not JSON-wrapped
        
    Examples:
        >>> extract_text_content('{"content": "hello"}')
        "hello"
        >>> extract_text_content('hello')
        "hello"
    """
    import json as _json

    from novel_forge.core.utils.json import strip_markdown_fences
    
    cleaned = strip_markdown_fences(content).strip()
    
    # Detect JSON wrapper — try to extract prose text from various structures
    if cleaned.startswith('{'):
        try:
            data = _json.loads(cleaned, strict=False)
            if isinstance(data, dict):
                extracted = _extract_prose_from_dict(data)
                if extracted is not None:
                    return _maybe_normalize_quotes(extracted)
        except (_json.JSONDecodeError, KeyError):
            pass
    
    # Strip trailing modification summary blocks that models sometimes append
    # despite instructions (e.g. "修改说明摘要：\n- …")
    content = _strip_trailing_edit_summary(content)

    # Normalize quote marks if content is Chinese prose
    content = _maybe_normalize_quotes(content)

    # Return original content if not JSON wrapped
    return content


# ── Well-known field names that commonly hold the main prose text ──────────
# Ordered by specificity: more specific keys are preferred.
_PROSE_FIELD_PRIORITY: list[str] = [
    "content",
    "full_chapter",
    "chapter_text",
    "text",
    "revised_text",
    "full_text",
    "body",
    "chapter",
    "story",
    "draft",
]


def _extract_prose_from_dict(data: dict[str, Any]) -> str | None:
    """Recursively extract prose from a JSON dict returned by the model.

    Strategy:
    1. Check top-level keys against _PROSE_FIELD_PRIORITY.
    2. If none found, recurse into single-key nested dicts (e.g. {"chapter_6": {...}}).
    3. Among candidate strings, pick the longest one (likely the full chapter).
    """
    # Direct match: check priority fields at this level
    for key in _PROSE_FIELD_PRIORITY:
        if key in data:
            val = data[key]
            if isinstance(val, str):
                return val

    # Collect all top-level string values long enough to be prose
    long_strings: list[str] = []
    nested_dicts: list[dict[str, Any]] = []

    for val in data.values():
        if isinstance(val, str) and len(val) >= 200:
            long_strings.append(val)
        elif isinstance(val, dict):
            nested_dicts.append(val)

    # Recurse into nested dicts (e.g. {"chapter_6": {"full_chapter": "..."}})
    for sub in nested_dicts:
        result = _extract_prose_from_dict(sub)
        if result is not None:
            return result

    # Fall back to the longest string if it looks like prose (not JSON-like itself)
    if long_strings:
        longest = max(long_strings, key=len)
        if not longest.lstrip().startswith('{'):
            return longest

    return None


# Patterns that signal the start of a model-generated modification summary block.
# The block always appears at a paragraph boundary and is never legitimate story text.
_EDIT_SUMMARY_PATTERNS = re.compile(
    r"\n+[\（(]?\s*(?:修改说明摘要|本次修改|调整如下|修改摘要|changelog|change\s+summary)"
    r"\s*[：:：]",
    re.IGNORECASE,
)


def _strip_trailing_edit_summary(text: str) -> str:
    """Remove a trailing edit-summary block that models sometimes append after story text.

    The heuristic: if the matched header appears in the **last 30 %** of the text,
    treat everything from that point onward as a spurious summary and drop it.
    Only truncates when the match is clearly in the tail to avoid accidentally
    clipping a story scene that happens to use similar phrasing.
    """
    m = _EDIT_SUMMARY_PATTERNS.search(text)
    if m and m.start() >= len(text) * 0.7:
        return text[: m.start()].rstrip()
    return text
