"""Tests for the rate limiter module."""

from __future__ import annotations

import asyncio

import pytest

from novel_forge.gateway.rate_limiter import (
    RateLimitConfig,
    RateLimiterRegistry,
    TokenBucketLimiter,
)


class TestTokenBucketLimiter:
    """Verify token bucket limiter behavior."""

    @pytest.mark.asyncio
    async def test_acquire_within_budget(self):
        limiter = TokenBucketLimiter(RateLimitConfig(requests_per_minute=60, tokens_per_minute=100_000))
        await limiter.acquire(100)  # Should not raise or block significantly

    @pytest.mark.asyncio
    async def test_report_usage(self):
        limiter = TokenBucketLimiter(RateLimitConfig(requests_per_minute=60, tokens_per_minute=100_000))
        await limiter.acquire(100)
        await limiter.report_usage(50, 100)  # Should not raise

    @pytest.mark.asyncio
    async def test_acquire_clamps_oversized_estimate_to_bucket_capacity(self):
        limiter = TokenBucketLimiter(
            RateLimitConfig(requests_per_minute=60, tokens_per_minute=100_000)
        )

        await asyncio.wait_for(limiter.acquire(1_000_000), timeout=0.2)

        assert limiter._token_tokens == 0

    def test_disabled_limiter(self):
        """Limiter with huge limits should effectively be disabled."""
        limiter = TokenBucketLimiter(RateLimitConfig(requests_per_minute=10000, tokens_per_minute=100_000_000))
        # Should have plenty of budget
        assert limiter._request_tokens >= 10000


class TestRateLimiterRegistry:
    """Verify registry-level provider management."""

    def test_get_returns_none_when_not_configured(self):
        registry = RateLimiterRegistry()
        assert registry.get("nonexistent_provider") is None

    def test_configure_and_get(self):
        registry = RateLimiterRegistry()
        registry.configure("test_provider", RateLimitConfig(requests_per_minute=30, tokens_per_minute=50_000))
        limiter = registry.get("test_provider")
        assert limiter is not None

    def test_configure_defaults(self):
        registry = RateLimiterRegistry()
        registry.configure_defaults()
        # Known providers should be configured
        assert registry.get("openai") is not None
        assert registry.get("anthropic") is not None
