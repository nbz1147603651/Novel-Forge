"""Unit tests for the streaming <think> tag splitter state machine."""

from __future__ import annotations

from novel_forge.gateway.adapters._think_splitter import (
    KIND_CONTENT,
    KIND_REASONING,
    ThinkStreamSplitter,
    split_thinking_stream,
)


class TestThinkStreamSplitterBasic:
    """Basic splitting without tag fragmentation across chunks."""

    def test_plain_content_no_tags(self) -> None:
        splitter = ThinkStreamSplitter()
        assert splitter.feed("hello world") == [(KIND_CONTENT, "hello world")]
        assert splitter.finish() == []

    def test_complete_think_block_in_one_chunk(self) -> None:
        splitter = ThinkStreamSplitter()
        segments = splitter.feed("hello <think>reasoning</think> world")
        assert segments == [
            (KIND_CONTENT, "hello "),
            (KIND_REASONING, "reasoning"),
            (KIND_CONTENT, " world"),
        ]

    def test_multiple_think_blocks(self) -> None:
        splitter = ThinkStreamSplitter()
        segments = splitter.feed("<think>a</think> mid <think>b</think>")
        assert segments == [
            (KIND_REASONING, "a"),
            (KIND_CONTENT, " mid "),
            (KIND_REASONING, "b"),
        ]

    def test_empty_chunks_ignored(self) -> None:
        splitter = ThinkStreamSplitter()
        assert splitter.feed("") == []

    def test_empty_think_block(self) -> None:
        splitter = ThinkStreamSplitter()
        segments = splitter.feed("text<think></think>more")
        # Empty reasoning segment is still emitted (kind flips matter).
        assert (KIND_CONTENT, "text") in segments
        assert (KIND_CONTENT, "more") in segments


class TestThinkStreamSplitterCrossChunk:
    """Tags split across chunk boundaries — the core state machine challenge."""

    def test_open_tag_split_across_chunks(self) -> None:
        splitter = ThinkStreamSplitter()
        assert splitter.feed("hello <thi") == [(KIND_CONTENT, "hello ")]
        assert splitter.feed("nk>reasoning") == [(KIND_REASONING, "reasoning")]

    def test_close_tag_split_across_chunks(self) -> None:
        splitter = ThinkStreamSplitter()
        assert splitter.feed("<think>reasoning</thi") == [(KIND_REASONING, "reasoning")]
        assert splitter.feed("nk> world") == [(KIND_CONTENT, " world")]

    def test_tag_split_one_char_at_a_time(self) -> None:
        splitter = ThinkStreamSplitter()
        segments: list[tuple[str, str]] = []
        for ch in "hi<think>ok</think>bye":
            segments.extend(splitter.feed(ch))
        segments.extend(splitter.finish())
        content = "".join(t for k, t in segments if k == KIND_CONTENT)
        reasoning = "".join(t for k, t in segments if k == KIND_REASONING)
        assert content == "hibye"
        assert reasoning == "ok"

    def test_partial_tag_then_plain_text(self) -> None:
        # "<th" could be start of "<think>" but next chunk makes it plain.
        splitter = ThinkStreamSplitter()
        assert splitter.feed("text <th") == [(KIND_CONTENT, "text ")]
        # "<thx" is not a tag prefix → "<th" was literal + "x" is content
        result = splitter.feed("x more")
        content = "".join(t for k, t in result if k == KIND_CONTENT)
        assert content == "<thx more"

    def test_partial_close_tag_then_plain(self) -> None:
        # "</t" is buffered as partial close tag; "ext" makes it "</text"
        # which is NOT a valid close tag.  Since we're still inside an
        # unclosed <think> block, "</text" is reasoning text.
        splitter = ThinkStreamSplitter()
        assert splitter.feed("<think>ok</t") == [(KIND_REASONING, "ok")]
        result = splitter.feed("ext")
        reasoning = "".join(t for k, t in result if k == KIND_REASONING)
        assert reasoning == "</text"


class TestThinkStreamSplitterEdgeCases:
    """Unclosed tags, stray close tags, and EOF handling."""

    def test_unclosed_think_at_eof_treated_as_reasoning(self) -> None:
        # <think> opens reasoning; "ongoing reasoning" is flushed at end of
        # feed() as reasoning text.  finish() has nothing left to emit.
        splitter = ThinkStreamSplitter()
        segments = splitter.feed("hello <think>ongoing reasoning")
        content = "".join(t for k, t in segments if k == KIND_CONTENT)
        reasoning = "".join(t for k, t in segments if k == KIND_REASONING)
        assert content == "hello "
        assert reasoning == "ongoing reasoning"
        assert splitter.finish() == []

    def test_stray_close_tag_without_open(self) -> None:
        # In content mode, </think> is NOT recognized as a tag (state-aware),
        # so it's preserved as literal text.
        splitter = ThinkStreamSplitter()
        segments = splitter.feed("text </think> more")
        content = "".join(t for k, t in segments if k == KIND_CONTENT)
        assert content == "text </think> more"

    def test_literal_angle_bracket_not_tag(self) -> None:
        splitter = ThinkStreamSplitter()
        segments = splitter.feed("a < b > c")
        content = "".join(t for k, t in segments if k == KIND_CONTENT)
        assert content == "a < b > c"

    def test_inner_think_inside_reasoning_is_literal(self) -> None:
        # In reasoning mode, a second <think> is NOT recognized as a tag;
        # it's preserved as literal reasoning text (no nesting).
        splitter = ThinkStreamSplitter()
        segments = splitter.feed("<think>outer <think> inner</think> tail")
        reasoning = "".join(t for k, t in segments if k == KIND_REASONING)
        content = "".join(t for k, t in segments if k == KIND_CONTENT)
        assert reasoning == "outer <think> inner"
        assert content == " tail"

    def test_finish_with_no_buffered_state(self) -> None:
        splitter = ThinkStreamSplitter()
        splitter.feed("complete text")
        assert splitter.finish() == []


class TestSplitThinkingStreamHelper:
    """The convenience function that processes an entire iterable."""

    def test_full_stream(self) -> None:
        chunks = ["hello ", "<thi", "nk>reason", "ing</think>", " world"]
        segments = split_thinking_stream(iter(chunks))
        content = "".join(t for k, t in segments if k == KIND_CONTENT)
        reasoning = "".join(t for k, t in segments if k == KIND_REASONING)
        assert content == "hello  world"
        assert reasoning == "reasoning"

    def test_empty_iterable(self) -> None:
        assert split_thinking_stream(iter([])) == []

    def test_no_tags(self) -> None:
        segments = split_thinking_stream(iter(["just", " content"]))
        content = "".join(t for k, t in segments if k == KIND_CONTENT)
        assert content == "just content"
