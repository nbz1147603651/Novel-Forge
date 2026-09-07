"""Tests for OpenAIAdapter."""

from __future__ import annotations

from novel_forge.gateway.adapters.openai import OpenAIAdapter


class TestOpenAIAdapter:
    def test_default_initialization(self) -> None:
        adapter = OpenAIAdapter(api_key="test-key")
        assert adapter.provider_name == "openai"
        assert adapter.default_model == "gpt-4o-mini"

    def test_custom_initialization(self) -> None:
        adapter = OpenAIAdapter(
            api_key="test-key",
            base_url="https://api.openai.com/v1",
            default_model="gpt-4o",
        )
        assert adapter.provider_name == "openai"
        assert adapter.default_model == "gpt-4o"

    def test_base_url_trailing_slash_stripped(self) -> None:
        adapter = OpenAIAdapter(
            api_key="test-key",
            base_url="https://api.openai.com/v1/",
        )
        assert adapter._base_url == "https://api.openai.com/v1"

    def test_empty_base_url_defaults_to_openai(self) -> None:
        adapter = OpenAIAdapter(api_key="test-key", base_url="")
        assert adapter._base_url == "https://api.openai.com"

    def test_last_health_error_initial_empty(self) -> None:
        adapter = OpenAIAdapter(api_key="test-key")
        assert adapter.last_health_error == ""

    def test_whitespace_base_url_stripped(self) -> None:
        adapter = OpenAIAdapter(
            api_key="test-key",
            base_url="  https://custom.openai.com  ",
        )
        assert adapter._base_url == "https://custom.openai.com"
