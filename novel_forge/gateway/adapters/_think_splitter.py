"""Stateful streaming splitter for ``<think>…</think>`` blocks.

Some providers (notably Tongyi / DashScope thinking models) embed the
chain-of-thought inside the *content* field of stream deltas as
``<think>…</think>`` blocks, interleaved with the final answer.  Unlike
DeepSeek-R1 (which exposes a separate ``reasoning_content`` field), these
must be separated from the content stream in real time.

The challenge: a ``<think>`` or ```` </think> ```` tag may be split across
two consecutive stream chunks (e.g. ``"<thi"`` then ``"nk>..."``).  A naive
``content.replace("<think>", "")`` on each chunk fails because the tag never
appears intact in a single chunk.

This module provides :class:`ThinkStreamSplitter`, a small state machine
that:

* Buffers partial tag prefixes (``<t``, ``</t``, …) until the next chunk
  disambiguates them.
* Emits ``(kind, text)`` tuples preserving the real interleaving order of
  reasoning and content as produced by the model.
* Gracefully handles unclosed ``<think>`` blocks (treats trailing text as
  reasoning) and stray ``</think>`` without an opening tag (treats it as
  content).

The splitter is stateless across instances but maintains per-instance
state across :meth:`feed` calls.  It is NOT thread-safe; one instance per
stream.

Example
-------
>>> s = ThinkStreamSplitter()
>>> s.feed("hello <th")
[("content", "hello ")]
>>> s.feed("ink>reasoning here</think> world")
[("reasoning", "reasoning here"), ("content", " world")]
>>> s.finish()
[]
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field

# The two tags we recognise.  Kept short for fast prefix matching.
_OPEN_TAG = "<think>"
_CLOSE_TAG = "</think>"
# The longest tag we track; partial prefixes shorter than this are buffered.
_MAX_TAG_LEN = max(len(_OPEN_TAG), len(_CLOSE_TAG))  # 8

# Sorter kinds for emitted segments.
KIND_CONTENT = "content"
KIND_REASONING = "reasoning"


@dataclass
class _Pending:
    """Accumulator for text of the current segment being built.

    ``kind`` flips between content and reasoning as tags are encountered.
    ``buffer`` holds the partial tag prefix that has not yet been resolved
    into a full tag or plain content.
    """

    kind: str = KIND_CONTENT
    text: list[str] = field(default_factory=list)
    # Partial tag prefix awaiting disambiguation (e.g. "<th", "</").
    pending_tag: str = ""


@dataclass
class ThinkStreamSplitter:
    """Split a stream of content chunks into ordered (kind, text) segments.

    The splitter maintains a small amount of state between :meth:`feed`
    calls:

    * The current segment kind (content or reasoning).
    * Any partial tag prefix that straddles a chunk boundary.

    Call :meth:`feed` for each incoming chunk and iterate the returned
    list of ``(kind, text)`` tuples.  When the stream ends, call
    :meth:`finish` to flush any buffered partial tag (treated as content
    or reasoning depending on the current state).
    """

    _state: _Pending = field(default_factory=_Pending)

    def feed(self, chunk: str) -> list[tuple[str, str]]:
        """Process one chunk and return completed segments.

        Returns a list of ``(kind, text)`` tuples in the order they were
        produced.  May return an empty list if the chunk was entirely
        absorbed into a partial tag prefix.  Text that is not part of a
        pending tag prefix is flushed at the end of each call so callers
        receive segments promptly for real-time streaming.
        """
        if not chunk:
            return []
        segments: list[tuple[str, str]] = []
        work = self._pending_tag_str() + chunk
        self._state.pending_tag = ""
        cursor = 0
        while cursor < len(work):
            emitted, advanced, new_kind = self._scan_once(work, cursor)
            if emitted is not None:
                self._emit(segments, emitted)
            if new_kind is not None:
                # A tag was matched; flush current text and flip kind.
                self._flush_current(segments)
                self._state.kind = new_kind
            if advanced == 0:
                # Remaining text is a partial tag prefix — buffer it.
                self._state.pending_tag = work[cursor:]
                break
            cursor += advanced
        # Flush any completed text (not the pending tag prefix) so callers
        # get segments without waiting for the next kind transition.
        self._flush_current(segments)
        return segments

    def finish(self) -> list[tuple[str, str]]:
        """Flush any buffered partial tag at end of stream.

        Returns the final segments.  A buffered partial tag is treated as
        plain text of the current segment kind (an unclosed ``<think>`` at
        EOF means the trailing text is reasoning; a partial ``</thi`` at
        EOF is just content).
        """
        segments: list[tuple[str, str]] = []
        if self._state.pending_tag:
            self._state.text.append(self._state.pending_tag)
            self._state.pending_tag = ""
        self._flush_current(segments)
        return segments

    # ── Internal helpers ──────────────────────────────────────────────

    def _pending_tag_str(self) -> str:
        """Return the buffered partial tag prefix (may be empty)."""
        tag = self._state.pending_tag
        self._state.pending_tag = ""
        return tag

    def _scan_once(
        self, work: str, cursor: int
    ) -> tuple[str | None, int, str | None]:
        """Scan from ``cursor`` and return (text_or_None, advance, new_kind).

        Tag recognition is state-aware: ``<think>`` is only treated as an
        opening tag when in CONTENT mode; ``</think>`` is only treated as a
        closing tag when in REASONING mode.  This prevents stray close tags
        in content from being consumed, and prevents inner ``<think>``
        inside reasoning from fragmenting it.

        * If plain text precedes the next tag, ``text`` is that text and
          ``advance`` moves past it; ``new_kind`` is None.
        * If a tag starts at ``cursor``, ``text`` is None, ``advance`` is
          the tag length, and ``new_kind`` is the kind to switch to.
        * If a partial tag prefix reaches the end of ``work``, ``text`` is
          None, ``advance`` is 0 (caller buffers the remainder).
        """
        if work[cursor] != "<":
            # Plain text up to the next '<' or end of work.
            next_lt = work.find("<", cursor)
            if next_lt == -1:
                return work[cursor:], len(work) - cursor, None
            return work[cursor:next_lt], next_lt - cursor, None
        # Cursor is at '<': try to match the tag relevant to current state.
        tail = work[cursor:]
        if self._state.kind == KIND_CONTENT:
            if tail.startswith(_OPEN_TAG):
                return None, len(_OPEN_TAG), KIND_REASONING
            if _is_tag_prefix_for(tail, _OPEN_TAG):
                return None, 0, None  # partial prefix
        else:
            if tail.startswith(_CLOSE_TAG):
                return None, len(_CLOSE_TAG), KIND_CONTENT
            if _is_tag_prefix_for(tail, _CLOSE_TAG):
                return None, 0, None  # partial prefix
        # Literal '<' that is not a recognized tag start in this state.
        return "<", 1, None

    def _emit(self, segments: list[tuple[str, str]], text: str) -> None:
        """Append plain text to the current segment buffer."""
        if text:
            self._state.text.append(text)

    def _flush_current(self, segments: list[tuple[str, str]]) -> None:
        """Flush buffered text as one segment of the current kind."""
        if not self._state.text:
            return
        text = "".join(self._state.text)
        self._state.text.clear()
        if text:
            segments.append((self._state.kind, text))


def _is_tag_prefix_for(tail: str, tag: str) -> bool:
    """Return True if ``tail`` is a strict prefix of ``tag``.

    A strict prefix means ``tail`` is shorter than the tag and every
    character matches the tag so far.  E.g. ``"<th"`` is a prefix of
    ``"<think>"`` but ``"<x"`` is not.
    """
    if len(tail) >= len(tag):
        return False
    return tag.startswith(tail)


def split_thinking_stream(chunks: Iterator[str]) -> list[tuple[str, str]]:
    """Convenience: feed an entire iterable of chunks and return segments.

    Mainly for testing and non-streaming fallback paths.  For real
    streaming use :class:`ThinkStreamSplitter` directly.
    """
    splitter = ThinkStreamSplitter()
    segments: list[tuple[str, str]] = []
    for chunk in chunks:
        segments.extend(splitter.feed(chunk))
    segments.extend(splitter.finish())
    return segments
