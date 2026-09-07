"""Tests for EmbeddingSignature — compute, compare, parse."""

from __future__ import annotations

import pytest

from novel_forge.memory.humanize_library_store import EmbeddingSignature


class TestEmbeddingSignatureCompute:
    def test_stable_hash(self) -> None:
        sig1 = EmbeddingSignature.compute("openai", "text-embedding-3-small", 1536)
        sig2 = EmbeddingSignature.compute("openai", "text-embedding-3-small", 1536)
        assert sig1 == sig2

    def test_different_providers(self) -> None:
        sig_a = EmbeddingSignature.compute("openai", "text-embedding-3-small", 1536)
        sig_b = EmbeddingSignature.compute("bge", "text-embedding-3-small", 1536)
        assert sig_a != sig_b

    def test_different_models(self) -> None:
        sig_a = EmbeddingSignature.compute("openai", "text-embedding-3-small", 1536)
        sig_b = EmbeddingSignature.compute("openai", "text-embedding-3-large", 1536)
        assert sig_a != sig_b

    def test_different_dimensions(self) -> None:
        sig_a = EmbeddingSignature.compute("openai", "text-embedding-3-small", 1536)
        sig_b = EmbeddingSignature.compute("openai", "text-embedding-3-small", 512)
        assert sig_a != sig_b

    def test_returns_hex_string(self) -> None:
        sig = EmbeddingSignature.compute("openai", "model", 128)
        assert len(sig) == 64
        int(sig, 16)  # Should not raise

    def test_chinese_provider(self) -> None:
        sig = EmbeddingSignature.compute("通义", "text-embedding-v2", 1536)
        assert len(sig) == 64


class TestEmbeddingSignatureEquals:
    def test_equal(self) -> None:
        sig = EmbeddingSignature.compute("openai", "model", 128)
        assert EmbeddingSignature.equals(sig, sig) is True

    def test_not_equal(self) -> None:
        sig_a = EmbeddingSignature.compute("openai", "model", 128)
        sig_b = EmbeddingSignature.compute("bge", "model", 128)
        assert EmbeddingSignature.equals(sig_a, sig_b) is False

    def test_both_none(self) -> None:
        assert EmbeddingSignature.equals(None, None) is True

    def test_one_none(self) -> None:
        sig = EmbeddingSignature.compute("openai", "model", 128)
        assert EmbeddingSignature.equals(sig, None) is False
        assert EmbeddingSignature.equals(None, sig) is False


class TestEmbeddingSignatureParse:
    def test_valid_signature(self) -> None:
        sig = EmbeddingSignature.compute("openai", "model", 128)
        result = EmbeddingSignature.parse(sig)
        assert result[0] == sig

    def test_invalid_length(self) -> None:
        with pytest.raises(ValueError, match="Invalid embedding signature length"):
            EmbeddingSignature.parse("abc")

    def test_invalid_hex(self) -> None:
        with pytest.raises(ValueError, match="Invalid hex"):
            EmbeddingSignature.parse("g" * 64)
