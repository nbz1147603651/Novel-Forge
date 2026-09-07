"""Tests for performance optimization utilities."""

from __future__ import annotations

import asyncio
import time

import pytest

from novel_forge.core.infra.performance import (
    AsyncCache,
    BatchConfig,
    PerformanceMonitor,
    RateLimiter,
    RateLimiterConfig,
    SemaphorePool,
    async_retry,
    process_batch,
    run_with_timeout,
    sync_retry,
)


class TestAsyncCache:
    """Tests for AsyncCache."""

    @pytest.mark.asyncio
    async def test_set_and_get(self):
        """Test basic set and get operations."""
        cache = AsyncCache(max_size=10, ttl=60)
        
        await cache.set("key1", "value1")
        result = await cache.get("key1")
        
        assert result == "value1"

    @pytest.mark.asyncio
    async def test_cache_miss(self):
        """Test cache miss returns None."""
        cache = AsyncCache()
        
        result = await cache.get("nonexistent")
        
        assert result is None

    @pytest.mark.asyncio
    async def test_lru_eviction(self):
        """Test LRU eviction when cache is full."""
        cache = AsyncCache(max_size=3)
        
        await cache.set("a", 1)
        await cache.set("b", 2)
        await cache.set("c", 3)
        
        await cache.get("a")
        await cache.set("d", 4)
        
        result = await cache.get("b")
        assert result is None
        
        result = await cache.get("a")
        assert result == 1

    @pytest.mark.asyncio
    async def test_ttl_expiration(self):
        """Test TTL-based expiration."""
        cache = AsyncCache(ttl=0.1)
        
        await cache.set("key", "value")
        await asyncio.sleep(0.15)
        
        result = await cache.get("key")
        assert result is None

    @pytest.mark.asyncio
    async def test_delete(self):
        """Test delete operation."""
        cache = AsyncCache()
        
        await cache.set("key", "value")
        deleted = await cache.delete("key")
        
        assert deleted is True
        assert await cache.get("key") is None

    @pytest.mark.asyncio
    async def test_clear(self):
        """Test clear operation."""
        cache = AsyncCache()
        
        await cache.set("a", 1)
        await cache.set("b", 2)
        await cache.clear()
        
        assert await cache.get("a") is None
        assert await cache.get("b") is None

    @pytest.mark.asyncio
    async def test_stats(self):
        """Test cache statistics."""
        cache = AsyncCache()
        
        await cache.set("a", 1)
        await cache.get("a")
        await cache.get("nonexistent")
        
        stats = cache.stats()
        
        assert stats["current_size"] == 1
        assert stats["hits"] == 1
        assert stats["misses"] == 1
        assert stats["hit_rate"] == 0.5


class TestRateLimiter:
    """Tests for RateLimiter."""

    @pytest.mark.asyncio
    async def test_acquire_tokens(self):
        """Test token acquisition."""
        config = RateLimiterConfig(max_calls=10, time_window=1.0, burst_size=5)
        limiter = RateLimiter(config)
        
        result = await limiter.try_acquire(tokens=3)
        
        assert result is True

    @pytest.mark.asyncio
    async def test_rate_limit_enforcement(self):
        """Test rate limiting is enforced."""
        config = RateLimiterConfig(max_calls=5, time_window=1.0, burst_size=5)
        limiter = RateLimiter(config)
        
        start = time.time()
        for _ in range(5):
            await limiter.acquire()
        elapsed = time.time() - start
        
        assert elapsed >= 0.9

    @pytest.mark.asyncio
    async def test_try_acquire_no_wait(self):
        """Test try_acquire doesn't wait."""
        config = RateLimiterConfig(max_calls=1, time_window=60.0, burst_size=1)
        limiter = RateLimiter(config)
        
        result1 = await limiter.try_acquire()
        result2 = await limiter.try_acquire()
        
        assert result1 is True
        assert result2 is False


class TestSemaphorePool:
    """Tests for SemaphorePool."""

    @pytest.mark.asyncio
    async def test_context_manager(self):
        """Test semaphore pool as context manager."""
        pool = SemaphorePool(max_concurrent=2)
        
        async with pool as p:
            stats = p.stats()
            assert stats["active"] == 1
            assert stats["total"] == 1

    @pytest.mark.asyncio
    async def test_concurrent_limit(self):
        """Test concurrent limit is enforced."""
        pool = SemaphorePool(max_concurrent=2)
        active_count = 0
        max_active = 0
        
        async def worker():
            nonlocal active_count, max_active
            async with pool:
                active_count += 1
                max_active = max(max_active, active_count)
                await asyncio.sleep(0.1)
                active_count -= 1
        
        await asyncio.gather(*[worker() for _ in range(5)])
        
        assert max_active <= 2


class TestPerformanceMonitor:
    """Tests for PerformanceMonitor."""

    @pytest.mark.asyncio
    async def test_record_and_get_stats(self):
        """Test recording and retrieving stats."""
        monitor = PerformanceMonitor()
        
        await monitor.record("test_op", 0.5)
        await monitor.record("test_op", 1.0)
        await monitor.record("test_op", 1.5)
        
        stats = await monitor.get_stats("test_op")
        
        assert stats is not None
        assert stats["count"] == 3
        assert stats["mean"] == 1.0
        assert stats["min"] == 0.5
        assert stats["max"] == 1.5

    @pytest.mark.asyncio
    async def test_percentiles(self):
        """Test percentile calculations."""
        monitor = PerformanceMonitor()
        
        for i in range(100):
            await monitor.record("percentile_test", float(i))
        
        stats = await monitor.get_stats("percentile_test")
        
        assert stats is not None
        assert 49 <= stats["p50"] <= 51
        assert 94 <= stats["p95"] <= 96
        assert 98 <= stats["p99"] <= 100


class TestAsyncRetry:
    """Tests for async retry decorator."""

    @pytest.mark.asyncio
    async def test_successful_retry(self):
        """Test successful call after retries."""
        attempts = []
        
        @async_retry(max_retries=3, delay=0.01)
        async def flaky_operation():
            attempts.append(len(attempts))
            if len(attempts) < 3:
                raise ValueError("Temporary failure")
            return "success"
        
        result = await flaky_operation()
        
        assert result == "success"
        assert len(attempts) == 3

    @pytest.mark.asyncio
    async def test_all_retries_exhausted(self):
        """Test all retries exhausted."""
        @async_retry(max_retries=2, delay=0.01, exceptions=(ValueError,))
        async def always_fails():
            raise ValueError("Permanent failure")
        
        with pytest.raises(ValueError, match="Permanent failure"):
            await always_fails()

    @pytest.mark.asyncio
    async def test_specific_exceptions(self):
        """Test retry only for specific exceptions."""
        @async_retry(max_retries=1, delay=0.01, exceptions=(ValueError,))
        async def type_error_operation():
            raise TypeError("Not caught by retry")
        
        with pytest.raises(TypeError):
            await type_error_operation()


class TestSyncRetry:
    """Tests for sync retry decorator."""

    def test_successful_retry(self):
        """Test successful call after retries."""
        attempts = []
        
        @sync_retry(max_retries=3, delay=0.01)
        def flaky_operation():
            attempts.append(len(attempts))
            if len(attempts) < 3:
                raise ValueError("Temporary failure")
            return "success"
        
        result = flaky_operation()
        
        assert result == "success"
        assert len(attempts) == 3

    def test_all_retries_exhausted(self):
        """Test all retries exhausted."""
        @sync_retry(max_retries=2, delay=0.01)
        def always_fails():
            raise ValueError("Permanent failure")
        
        with pytest.raises(ValueError):
            always_fails()


class TestRunWithTimeout:
    """Tests for run_with_timeout."""

    @pytest.mark.asyncio
    async def test_successful_completion(self):
        """Test operation completes within timeout."""
        async def quick_operation():
            return "success"
        
        result = await run_with_timeout(
            quick_operation(),
            timeout=5.0,
            operation_name="quick_op"
        )
        
        assert result == "success"

    @pytest.mark.asyncio
    async def test_timeout_raises_exception(self):
        """Test timeout raises TimeoutException."""
        async def slow_operation():
            await asyncio.sleep(10)
            return "success"
        
        from novel_forge.core.exceptions_framework import TimeoutException
        
        with pytest.raises(TimeoutException):
            await run_with_timeout(
                slow_operation(),
                timeout=0.1,
                operation_name="slow_op"
            )


class TestProcessBatch:
    """Tests for batch processing."""

    @pytest.mark.asyncio
    async def test_batch_preserves_order(self):
        """Test batch processing preserves order."""
        async def process_item(item):
            await asyncio.sleep(0.01)
            return item * 2
        
        items = [1, 2, 3, 4, 5]
        results = await process_batch(
            items,
            process_item,
            config=BatchConfig(max_concurrent=2)
        )
        
        assert results == [2, 4, 6, 8, 10]

    @pytest.mark.asyncio
    async def test_batch_empty_list(self):
        """Test batch processing with empty list."""
        results = await process_batch([], lambda x: x)
        
        assert results == []

    @pytest.mark.asyncio
    async def test_batch_with_errors(self):
        """Test batch processing handles errors gracefully."""
        async def process_with_error(item):
            if item == 3:
                raise ValueError("Error at item 3")
            return item * 2
        
        items = [1, 2, 3, 4]
        results = await process_batch(
            items,
            process_with_error,
            config=BatchConfig(max_concurrent=2)
        )
        
        assert results[0] == 2
        assert results[1] == 4
        assert results[2] is None
        assert results[3] == 8
