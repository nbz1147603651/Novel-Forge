"""Rate limiter for LLM API calls — per-provider token bucket."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field


@dataclass
class RateLimitConfig:
    """Rate limit configuration per provider."""

    requests_per_minute: int = 60
    tokens_per_minute: int = 200_000


class TokenBucketLimiter:
    """Simple async token-bucket rate limiter.

    Supports both request-count and token-count limits.
    """

    def __init__(self, config: RateLimitConfig) -> None:
        self._rpm = config.requests_per_minute
        self._tpm = config.tokens_per_minute
        self._request_tokens = float(self._rpm)
        self._token_tokens = float(self._tpm)
        self._last_refill = time.monotonic()
        # Lazy-init: Python 3.9's asyncio.Lock() requires a running event
        # loop in __init__, but TokenBucketLimiter is created synchronously
        # at app startup.  The first acquire() call creates the lock.
        # This is safe in asyncio's cooperative model — there's no await
        # between the None-check and assignment in acquire().
        self._lock: asyncio.Lock | None = None

    def _refill(self) -> None:
        now = time.monotonic()
        elapsed = now - self._last_refill
        self._last_refill = now
        self._request_tokens = min(
            float(self._rpm),
            self._request_tokens + elapsed * self._rpm / 60.0,
        )
        self._token_tokens = min(
            float(self._tpm),
            self._token_tokens + elapsed * self._tpm / 60.0,
        )

    async def acquire(self, estimated_tokens: int = 1, *, priority: int = 0) -> None:
        """Wait until rate limit budget is available, then consume.

        Uses calculated wait time instead of fixed 100ms polling for
        better responsiveness and reduced CPU wake-ups.

        Args:
            estimated_tokens: Estimated token cost of the request.
            priority: Request priority (0=normal, 1=high). High-priority
                requests (e.g. chapter generation) proceed as soon as budget
                is available. Low-priority requests (priority < 0, e.g. TTS
                script generation) add extra backoff when the bucket is below
                20% capacity to avoid starving interactive tasks.
        """
        requested_tokens = max(1, int(estimated_tokens or 1))
        token_cost = min(requested_tokens, self._tpm)
        if self._lock is None:
            self._lock = asyncio.Lock()
        while True:
            async with self._lock:
                self._refill()
                if self._request_tokens >= 1.0 and self._token_tokens >= token_cost:
                    # Low-priority requests yield when bucket is nearly empty
                    if priority < 0 and self._token_tokens < self._tpm * 0.2:
                        # Let high-priority traffic through first
                        wait_s = 0.5
                    else:
                        self._request_tokens -= 1.0
                        self._token_tokens -= token_cost
                        return
                else:
                    # Calculate precise wait time based on deficit
                    request_wait = (
                        (1.0 - self._request_tokens) / (self._rpm / 60.0)
                        if self._request_tokens < 1.0
                        else 0.0
                    )
                    token_wait = (
                        (token_cost - self._token_tokens) / (self._tpm / 60.0)
                        if self._token_tokens < token_cost
                        else 0.0
                    )
                    wait_s = max(request_wait, token_wait, 0.01)  # min 10ms
                    # Low-priority requests add extra backoff
                    if priority < 0:
                        wait_s = max(wait_s, 0.3)
            await asyncio.sleep(wait_s)

    async def report_usage(self, actual_tokens: int, estimated_tokens: int) -> None:
        """Adjust token budget after actual usage is known.

        If actual > estimated, deducts the surplus.
        If actual < estimated (common for small responses or failures), refunds
        the over-estimated portion so the bucket doesn't drain unnecessarily.

        This method is async and acquires the same lock as acquire() to
        prevent concurrent corruption of _token_tokens.
        """
        if self._lock is None:
            self._lock = asyncio.Lock()
        async with self._lock:
            diff = actual_tokens - estimated_tokens
            if diff > 0:
                self._token_tokens = max(0.0, self._token_tokens - diff)
            elif diff < 0:
                # Refund over-estimated tokens, capped at bucket maximum
                self._token_tokens = min(float(self._tpm), self._token_tokens - diff)


@dataclass
class RateLimiterRegistry:
    """Registry of per-provider rate limiters."""

    _limiters: dict[str, TokenBucketLimiter] = field(default_factory=dict)
    _configs: dict[str, RateLimitConfig] = field(default_factory=dict)
    enabled: bool = True

    def configure(self, provider: str, config: RateLimitConfig) -> None:
        """Set rate limit for a provider."""
        self._configs[provider] = config
        self._limiters[provider] = TokenBucketLimiter(config)

    def get(self, provider: str) -> TokenBucketLimiter | None:
        """Get limiter for provider, or None if not configured."""
        if not self.enabled:
            return None
        return self._limiters.get(provider)

    def configure_defaults(self) -> None:
        """Set sensible defaults for known providers."""
        defaults = {
            "openai": RateLimitConfig(requests_per_minute=500, tokens_per_minute=800_000),
            "anthropic": RateLimitConfig(requests_per_minute=60, tokens_per_minute=400_000),
            "deepseek": RateLimitConfig(requests_per_minute=60, tokens_per_minute=200_000),
            "tongyi": RateLimitConfig(requests_per_minute=120, tokens_per_minute=400_000),
            "kimi": RateLimitConfig(requests_per_minute=30, tokens_per_minute=100_000),
            "mimo": RateLimitConfig(requests_per_minute=30, tokens_per_minute=200_000),
            "minimax": RateLimitConfig(requests_per_minute=60, tokens_per_minute=200_000),
        }
        for provider, config in defaults.items():
            if provider not in self._configs:
                self.configure(provider, config)
