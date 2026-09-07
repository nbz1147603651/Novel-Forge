"""Tests for the gateway pricing module."""

from __future__ import annotations

from novel_forge.gateway.pricing import estimate_cost


class TestEstimateCost:
    """Verify cost calculation for various models."""

    def test_known_model_exact_match(self):
        cost = estimate_cost("gpt-4o", prompt_tokens=1000, completion_tokens=500)
        # gpt-4o: $2.50/M input + $10.00/M output
        expected = (1000 * 2.50 + 500 * 10.00) / 1_000_000
        assert abs(cost - expected) < 1e-10

    def test_known_model_prefix_match(self):
        """Versioned model IDs should match by prefix."""
        cost = estimate_cost("gpt-4o-2024-08-06", prompt_tokens=1000, completion_tokens=1000)
        assert cost > 0

    def test_unknown_model_returns_zero(self):
        cost = estimate_cost("unknown-model-xyz", prompt_tokens=1000, completion_tokens=1000)
        assert cost == 0.0

    def test_zero_tokens(self):
        cost = estimate_cost("gpt-4o", prompt_tokens=0, completion_tokens=0)
        assert cost == 0.0

    def test_anthropic_model(self):
        cost = estimate_cost("claude-sonnet-4-20250514", prompt_tokens=2000, completion_tokens=1000)
        assert cost > 0

    def test_deepseek_model(self):
        cost = estimate_cost("deepseek-chat", prompt_tokens=5000, completion_tokens=2000)
        assert cost > 0
