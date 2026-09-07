"""Model-aware token counting utilities.

Pre-flight counts prefer a real local tokenizer when the routed model exposes
one.  Provider-reported usage remains the billing source of truth after a
request completes.  Providers without a locally available tokenizer use one
shared, conservative Unicode heuristic instead of maintaining contradictory
``chars / N`` formulas throughout the codebase.
"""

from __future__ import annotations

import functools
import math
import unicodedata
from dataclasses import dataclass
from typing import Any, Iterable, Mapping


@dataclass(frozen=True)
class TokenCount:
    """One token count plus provenance describing how it was produced."""

    tokens: int
    method: str
    tokenizer_name: str = ""
    tokenizer_backed: bool = False
    exact: bool = False


@dataclass(frozen=True)
class _TokenizerHandle:
    encoder: Any
    name: str
    exact_for_model: bool


def _is_cjk(character: str) -> bool:
    codepoint = ord(character)
    return (
        0x3400 <= codepoint <= 0x4DBF
        or 0x4E00 <= codepoint <= 0x9FFF
        or 0xF900 <= codepoint <= 0xFAFF
        or 0x3040 <= codepoint <= 0x30FF
        or 0xAC00 <= codepoint <= 0xD7AF
    )


def _unicode_heuristic_tokens(text: str) -> int:
    """Conservatively estimate mixed CJK/Latin text with one shared rule."""

    if not text:
        return 0

    cjk_chars = 0
    latin_or_digit_chars = 0
    other_visible_chars = 0
    whitespace_runs = 0
    in_whitespace = False
    for character in text:
        if character.isspace():
            if not in_whitespace:
                whitespace_runs += 1
            in_whitespace = True
            continue
        in_whitespace = False
        if _is_cjk(character):
            cjk_chars += 1
        elif character.isascii() and (character.isalnum() or character == "_"):
            latin_or_digit_chars += 1
        elif unicodedata.category(character).startswith("C"):
            # Control/format characters normally do not consume a standalone
            # token, but their surrounding text still does.
            continue
        else:
            other_visible_chars += 1

    estimate = (
        cjk_chars * 1.5
        + latin_or_digit_chars / 3.6
        + other_visible_chars * 0.75
        + whitespace_runs * 0.25
    )
    return max(1, math.ceil(estimate))


def _looks_like_openai_model(provider: str, model_id: str) -> bool:
    clean_provider = str(provider or "").strip().lower().replace("-", "_")
    clean_model = str(model_id or "").strip().lower()
    if clean_provider:
        return clean_provider == "openai" or clean_provider.startswith("openai:")
    return clean_model.startswith(("gpt-", "chatgpt-", "o1", "o3", "o4"))


@functools.lru_cache(maxsize=64)
def _load_tokenizer(provider: str, model_id: str) -> _TokenizerHandle | None:
    """Load a model tokenizer, falling back cleanly if its assets are unavailable.

    ``tiktoken`` is supplied by the ``openai`` project extra.  Unknown newer
    OpenAI aliases use the compatible ``o200k_base`` vocabulary but are
    explicitly marked non-exact for that model instead of pretending the
    provider's billing count is known. On a fresh installation, ``tiktoken``
    may populate its own on-disk vocabulary cache; offline/cache failures are
    contained and use the Unicode fallback.
    """

    if not model_id or not _looks_like_openai_model(provider, model_id):
        return None
    try:
        import tiktoken  # type: ignore[import-not-found]
    except ImportError:
        return None

    try:
        encoder = tiktoken.encoding_for_model(model_id)
        return _TokenizerHandle(
            encoder=encoder,
            name=str(getattr(encoder, "name", model_id)),
            exact_for_model=True,
        )
    except KeyError:
        clean_model = model_id.lower()
        if clean_model.startswith(("gpt-5", "gpt-4.1", "gpt-4o", "o1", "o3", "o4")):
            try:
                encoder = tiktoken.get_encoding("o200k_base")
            except Exception:
                return None
            return _TokenizerHandle(
                encoder=encoder,
                name="o200k_base",
                exact_for_model=False,
            )
    except Exception:
        return None
    return None


def count_text_tokens(
    text: str,
    *,
    provider: str = "",
    model_id: str = "",
) -> TokenCount:
    """Count text tokens with a routed tokenizer when locally available."""

    clean_text = str(text or "")
    if not clean_text:
        return TokenCount(tokens=0, method="empty", exact=True)

    handle = _load_tokenizer(str(provider or ""), str(model_id or ""))
    if handle is not None:
        try:
            tokens = len(handle.encoder.encode(clean_text, disallowed_special=()))
        except TypeError:
            tokens = len(handle.encoder.encode(clean_text))
        except Exception:
            tokens = 0
        if tokens > 0:
            return TokenCount(
                tokens=tokens,
                method=(
                    "model_tokenizer" if handle.exact_for_model else "compatible_model_tokenizer"
                ),
                tokenizer_name=handle.name,
                tokenizer_backed=True,
                exact=handle.exact_for_model,
            )

    return TokenCount(
        tokens=_unicode_heuristic_tokens(clean_text),
        method="unicode_heuristic",
        tokenizer_backed=False,
        exact=False,
    )


def count_message_tokens(
    messages: Iterable[Mapping[str, Any] | Any],
    *,
    provider: str = "",
    model_id: str = "",
) -> TokenCount:
    """Count chat messages while retaining tokenization provenance.

    Message contents and roles are tokenized with the real model tokenizer
    where available.  A small per-message allowance covers provider chat
    framing.  Because providers may change that hidden framing, a pre-flight
    message count is never labelled billing-exact; the response usage object
    remains authoritative after the call.
    """

    normalized: list[tuple[str, str, str]] = []
    for message in messages:
        if isinstance(message, Mapping):
            role = str(message.get("role", ""))
            content = str(message.get("content", ""))
            name = str(message.get("name", "") or "")
        else:
            role = str(getattr(message, "role", ""))
            content = str(getattr(message, "content", ""))
            name = str(getattr(message, "name", "") or "")
        normalized.append((role, content, name))

    if not normalized:
        return TokenCount(tokens=0, method="empty", exact=True)

    rendered = "\n".join(
        f"{role}\n{name}\n{content}" if name else f"{role}\n{content}"
        for role, content, name in normalized
    )
    base = count_text_tokens(rendered, provider=provider, model_id=model_id)
    # Chat templates use hidden boundary tokens. Four tokens per message plus
    # final assistant priming is conservative for context-window preflight.
    framing_tokens = len(normalized) * 4 + 3
    return TokenCount(
        tokens=base.tokens + framing_tokens,
        method=(f"{base.method}_with_chat_framing" if base.method != "empty" else "chat_framing"),
        tokenizer_name=base.tokenizer_name,
        tokenizer_backed=base.tokenizer_backed,
        exact=False,
    )


def estimate_chinese_tokens(text: str) -> int:
    """Backward-compatible conservative estimate for Chinese/mixed text."""

    return count_text_tokens(text).tokens


def estimate_text_length_tokens(text_length: int, *, cjk_ratio: float = 1.0) -> int:
    """Estimate a character-only budget when the source text is unavailable.

    This helper is intentionally labelled an estimate: a real tokenizer needs
    the actual text, not only its length.
    """

    length = max(0, int(text_length or 0))
    ratio = min(1.0, max(0.0, float(cjk_ratio)))
    return math.ceil(length * (ratio * 1.5 + (1.0 - ratio) / 3.6))


def estimate_dict_tokens(data: dict[str, Any], max_depth: int = 3) -> int:
    """Estimate total tokens for leaf values in a structured payload."""

    if max_depth <= 0:
        return 0

    total = 0
    for value in data.values():
        if isinstance(value, str):
            total += estimate_chinese_tokens(value)
        elif isinstance(value, dict):
            total += estimate_dict_tokens(value, max_depth - 1)
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, str):
                    total += estimate_chinese_tokens(item)
                elif isinstance(item, dict):
                    total += estimate_dict_tokens(item, max_depth - 1)
    return total


__all__ = (
    "TokenCount",
    "count_message_tokens",
    "count_text_tokens",
    "estimate_chinese_tokens",
    "estimate_dict_tokens",
    "estimate_text_length_tokens",
)
