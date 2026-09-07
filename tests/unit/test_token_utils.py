"""Tests for token estimation utilities."""

from __future__ import annotations

import sys
from types import SimpleNamespace

from novel_forge.core.parsing.token_utils import (
    _load_tokenizer,
    count_message_tokens,
    count_text_tokens,
    estimate_chinese_tokens,
    estimate_dict_tokens,
    estimate_text_length_tokens,
)


def test_estimate_chinese_tokens_chinese_text() -> None:
    text = "这是一个中文测试"
    result = estimate_chinese_tokens(text)
    assert result == int(len(text) * 1.5)


def test_estimate_chinese_tokens_mixed_text() -> None:
    text = "Hello 你好 World 世界"
    result = estimate_chinese_tokens(text)
    assert result == 10


def test_estimate_chinese_tokens_empty() -> None:
    result = estimate_chinese_tokens("")
    assert result == 0


def test_estimate_dict_tokens_nested_dict() -> None:
    data = {
        "name": "测试",
        "items": {"sub": "nested"},
        "list": ["a", "bc"],
    }
    result = estimate_dict_tokens(data)
    # CJK is conservative; Latin text no longer inherits the old CJK x1.5 rule.
    expected = 7
    assert result == expected


def test_estimate_dict_tokens_max_depth() -> None:
    data = {"level1": {"level2": {"level3": "deep"}}}
    result_depth_2 = estimate_dict_tokens(data, max_depth=2)
    result_depth_3 = estimate_dict_tokens(data, max_depth=3)
    assert result_depth_2 < result_depth_3


def test_count_text_tokens_prefers_real_model_tokenizer(monkeypatch) -> None:
    class _FakeEncoding:
        name = "fake_openai_encoding"

        @staticmethod
        def encode(text: str, **_: object) -> list[int]:
            return list(range(len(text)))

    fake_tiktoken = SimpleNamespace(
        encoding_for_model=lambda _model_id: _FakeEncoding(),
        get_encoding=lambda _name: _FakeEncoding(),
    )
    monkeypatch.setitem(sys.modules, "tiktoken", fake_tiktoken)
    _load_tokenizer.cache_clear()

    result = count_text_tokens("真实 tokenizer", provider="openai", model_id="gpt-test")

    assert result.tokens == len("真实 tokenizer")
    assert result.method == "model_tokenizer"
    assert result.tokenizer_name == "fake_openai_encoding"
    assert result.tokenizer_backed is True
    assert result.exact is True
    _load_tokenizer.cache_clear()


def test_count_messages_discloses_chat_framing_is_not_billing_exact(monkeypatch) -> None:
    class _FakeEncoding:
        name = "fake_openai_encoding"

        @staticmethod
        def encode(text: str, **_: object) -> list[int]:
            return list(range(len(text)))

    monkeypatch.setitem(
        sys.modules,
        "tiktoken",
        SimpleNamespace(encoding_for_model=lambda _model_id: _FakeEncoding()),
    )
    _load_tokenizer.cache_clear()

    result = count_message_tokens(
        [{"role": "user", "content": "测试"}],
        provider="openai",
        model_id="gpt-test",
    )

    assert result.tokenizer_backed is True
    assert result.exact is False
    assert result.method == "model_tokenizer_with_chat_framing"
    assert result.tokens > 2
    _load_tokenizer.cache_clear()


def test_character_only_budget_is_explicitly_conservative() -> None:
    assert estimate_text_length_tokens(100) == 150
