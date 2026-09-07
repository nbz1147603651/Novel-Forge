"""Performance optimization utilities for Novel Forge.

This module provides caching, async processing, and concurrency control
utilities to optimize system performance.
"""

from __future__ import annotations

import asyncio
import functools
import logging
import time
from collections import OrderedDict
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, TypeVar

from novel_forge.core.exceptions import TimeoutException

logger = logging.getLogger(__name__)

T = TypeVar("T")


@dataclass
class AsyncCacheEntry:
    """Cache entry with expiration tracking."""
    
    value: Any
    created_at: float = field(default_factory=time.time)
    access_count: int = 1
    
    def is_expired(self, ttl: float) -> bool:
        """Check if entry has expired based on TTL."""
        if ttl <= 0:
            return False
        return (time.time() - self.created_at) > ttl
    
    def touch(self) -> None:
        """Update access tracking."""
        self.access_count += 1


class AsyncCache:
    """Thread-safe async-capable cache with TTL and LRU eviction.
    
    Features:
    - TTL-based expiration
    - LRU eviction policy
    - Thread-safe operations
    - Access statistics tracking
    """
    
    def __init__(self, max_size: int = 256, ttl: float = 3600.0) -> None:
        """Initialize async cache.
        
        Args:
            max_size: Maximum number of entries
            ttl: Time-to-live in seconds (0 = no expiration)
        """
        self._max_size = max_size
        self._ttl = ttl
        self._cache: OrderedDict[str, AsyncCacheEntry] = OrderedDict()
        self._lock = asyncio.Lock()
        self._hits = 0
        self._misses = 0
    
    async def get(self, key: str) -> Any | None:
        """Get value from cache.
        
        Args:
            key: Cache key
            
        Returns:
            Cached value or None if not found/expired
        """
        async with self._lock:
            if key not in self._cache:
                self._misses += 1
                return None
            
            entry = self._cache[key]
            
            if entry.is_expired(self._ttl):
                del self._cache[key]
                self._misses += 1
                return None
            
            self._hits += 1
            entry.touch()
            self._cache.move_to_end(key)
            return entry.value
    
    async def set(self, key: str, value: Any) -> None:
        """Set value in cache.
        
        Args:
            key: Cache key
            value: Value to cache
        """
        async with self._lock:
            if len(self._cache) >= self._max_size and key not in self._cache:
                self._cache.popitem(last=False)
            
            self._cache[key] = AsyncCacheEntry(value=value)
            self._cache.move_to_end(key)
    
    async def delete(self, key: str) -> bool:
        """Delete entry from cache.
        
        Args:
            key: Cache key
            
        Returns:
            True if key was found and deleted
        """
        async with self._lock:
            if key in self._cache:
                del self._cache[key]
                return True
            return False
    
    async def clear(self) -> None:
        """Clear all cache entries."""
        async with self._lock:
            self._cache.clear()
            self._hits = 0
            self._misses = 0
    
    def stats(self) -> dict[str, Any]:
        """Get cache statistics.
        
        Returns:
            Dictionary with cache stats
        """
        total = self._hits + self._misses
        return {
            "max_size": self._max_size,
            "current_size": len(self._cache),
            "ttl": self._ttl,
            "hits": self._hits,
            "misses": self._misses,
            "hit_rate": round(self._hits / max(1, total), 3),
        }


def async_retry(
    max_retries: int = 3,
    delay: float = 1.0,
    backoff: float = 2.0,
    exceptions: tuple[type[BaseException], ...] = (Exception,),
) -> Callable[[Callable[..., Awaitable[T]]], Callable[..., Awaitable[T]]]:
    """Decorator for automatic retry with exponential backoff.
    
    Args:
        max_retries: Maximum number of retry attempts
        delay: Initial delay between retries in seconds
        backoff: Multiplier for delay on each retry
        exceptions: Tuple of exception types to catch
        
    Example:
        @async_retry(max_retries=3, delay=1.0, exceptions=(NetworkError,))
        async def fetch_data():
            ...
    """
    def decorator(func: Callable[..., Awaitable[T]]) -> Callable[..., Awaitable[T]]:
        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> T:
            current_delay = delay
            last_exc: BaseException | None = None
            
            for attempt in range(max_retries + 1):
                try:
                    return await func(*args, **kwargs)
                except exceptions as e:
                    last_exc = e
                    if attempt < max_retries:
                        logger.warning(
                            "Retry %d/%d for %s after %.1fs: %s",
                            attempt + 1, max_retries, func.__name__,
                            current_delay, str(e)
                        )
                        await asyncio.sleep(current_delay)
                        current_delay *= backoff
                    else:
                        logger.error(
                            "All %d retries exhausted for %s: %s",
                            max_retries, func.__name__, str(e)
                        )
            
            if last_exc:
                raise last_exc
            raise RuntimeError(f"All retries exhausted for {func.__name__}")
        
        return wrapper
    return decorator


def sync_retry(
    max_retries: int = 3,
    delay: float = 1.0,
    backoff: float = 2.0,
    exceptions: tuple[type[BaseException], ...] = (Exception,),
) -> Callable[[Callable[..., T]], Callable[..., T]]:
    """Decorator for synchronous retry with exponential backoff.
    
    Args:
        max_retries: Maximum number of retry attempts
        delay: Initial delay between retries in seconds
        backoff: Multiplier for delay on each retry
        exceptions: Tuple of exception types to catch
    """
    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> T:
            current_delay = delay
            last_exc: BaseException | None = None
            
            for attempt in range(max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except exceptions as e:
                    last_exc = e
                    if attempt < max_retries:
                        logger.warning(
                            "Retry %d/%d for %s after %.1fs: %s",
                            attempt + 1, max_retries, func.__name__,
                            current_delay, str(e)
                        )
                        time.sleep(current_delay)
                        current_delay *= backoff
                    else:
                        logger.error(
                            "All %d retries exhausted for %s: %s",
                            max_retries, func.__name__, str(e)
                        )
            
            if last_exc:
                raise last_exc
            raise RuntimeError(f"All retries exhausted for {func.__name__}")
        
        return wrapper
    return decorator


@dataclass
class RateLimiterConfig:
    """Configuration for rate limiting."""
    
    max_calls: int = 100
    time_window: float = 60.0
    burst_size: int = 10


class RateLimiter:
    """Token bucket rate limiter for async operations.
    
    Supports both per-second rate limiting and burst allowances.
    """
    
    def __init__(self, config: RateLimiterConfig | None = None) -> None:
        """Initialize rate limiter.
        
        Args:
            config: Rate limiter configuration
        """
        self._config = config or RateLimiterConfig()
        self._refill_rate = (
            self._config.max_calls / self._config.time_window
            if self._config.max_calls > 0 and self._config.time_window > 0
            else 0.0
        )
        self._seconds_per_token = (
            self._config.time_window / self._config.max_calls
            if self._config.max_calls > 0 and self._config.time_window > 0
            else 0.0
        )
        self._tokens = float(self._config.burst_size)
        self._last_update = time.time()
        # Smooth outbound request cadence so sustained traffic respects
        # max_calls/time_window without front-loading a burst on startup.
        self._next_slot_at = self._last_update + self._seconds_per_token
        self._lock = asyncio.Lock()
    
    async def acquire(self, tokens: int = 1) -> bool:
        """Acquire tokens, waiting if necessary.
        
        Args:
            tokens: Number of tokens to acquire
            
        Returns:
            True if tokens acquired
        """
        if tokens <= 0 or self._seconds_per_token <= 0:
            return True

        while True:
            wait_time = 0.0
            async with self._lock:
                now = time.time()
                elapsed = max(0.0, now - self._last_update)
                self._tokens = min(
                    float(self._config.burst_size),
                    self._tokens + elapsed * self._refill_rate,
                )
                self._last_update = now

                # Keep calls evenly paced, even when burst tokens exist.
                wait_for_slot = max(0.0, self._next_slot_at - now)
                if wait_for_slot > 0:
                    wait_time = wait_for_slot
                elif self._tokens >= tokens:
                    self._tokens -= tokens
                    self._next_slot_at = max(self._next_slot_at, now) + (
                        self._seconds_per_token * tokens
                    )
                    return True
                else:
                    wait_time = (tokens - self._tokens) * self._seconds_per_token

            await asyncio.sleep(wait_time)
    
    async def try_acquire(self, tokens: int = 1) -> bool:
        """Try to acquire tokens without waiting.
        
        Args:
            tokens: Number of tokens to acquire
            
        Returns:
            True if tokens acquired, False otherwise
        """
        if tokens <= 0 or self._seconds_per_token <= 0:
            return True

        async with self._lock:
            now = time.time()
            elapsed = max(0.0, now - self._last_update)
            
            available_tokens = min(
                self._config.burst_size,
                self._tokens + elapsed * self._refill_rate,
            )
            
            if available_tokens >= tokens:
                self._tokens = available_tokens - tokens
                self._last_update = now
                return True
            return False


class SemaphorePool:
    """Async semaphore pool for limiting concurrent operations.
    
    Useful for controlling the number of simultaneous API calls or file operations.
    """
    
    def __init__(self, max_concurrent: int = 10) -> None:
        """Initialize semaphore pool.
        
        Args:
            max_concurrent: Maximum concurrent operations
        """
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._max_concurrent = max_concurrent
        self._active_count = 0
        self._total_count = 0
        self._lock = asyncio.Lock()
    
    async def __aenter__(self) -> "SemaphorePool":
        """Context manager entry."""
        await self._semaphore.acquire()
        async with self._lock:
            self._active_count += 1
            self._total_count += 1
        return self
    
    async def __aexit__(self, *args: Any) -> None:
        """Context manager exit."""
        self._semaphore.release()
        async with self._lock:
            self._active_count -= 1
    
    def stats(self) -> dict[str, Any]:
        """Get pool statistics."""
        return {
            "max_concurrent": self._max_concurrent,
            "active": self._active_count,
            "total": self._total_count,
        }


async def run_with_timeout(
    coro: Awaitable[T],
    timeout: float,
    operation_name: str = "operation",
) -> T:
    """Run coroutine with timeout.
    
    Args:
        coro: Coroutine to run
        timeout: Timeout in seconds
        operation_name: Name for error reporting
        
    Returns:
        Result of coroutine
        
    Raises:
        TimeoutException: If operation times out
    """
    try:
        return await asyncio.wait_for(coro, timeout=timeout)
    except asyncio.TimeoutError as e:
        raise TimeoutException(
            message=f"{operation_name} timed out after {timeout}s",
            operation=operation_name,
            timeout_seconds=timeout,
        ) from e


def run_in_executor(
    max_workers: int = 4,
    pool_type: str = "thread",
) -> Callable[[Callable[..., T]], Callable[..., Awaitable[T]]]:
    """Decorator to run blocking functions in an executor pool.
    
    Args:
        max_workers: Maximum number of worker threads/processes
        pool_type: 'thread' or 'process'
        
    Example:
        @run_in_executor(max_workers=4)
        def blocking_io_operation():
            ...
    """
    def decorator(func: Callable[..., T]) -> Callable[..., Awaitable[T]]:
        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> T:
            executor_class = ThreadPoolExecutor if pool_type == "thread" else ProcessPoolExecutor
            
            loop = asyncio.get_running_loop()
            partial_func = functools.partial(func, *args, **kwargs)
            
            return await loop.run_in_executor(
                executor_class(max_workers=max_workers),
                partial_func
            )
        
        return wrapper
    return decorator


class PerformanceMonitor:
    """Monitor and track performance metrics."""
    
    def __init__(self) -> None:
        self._operations: dict[str, list[float]] = {}
        self._lock = asyncio.Lock()
    
    async def record(self, operation: str, duration: float) -> None:
        """Record operation duration.
        
        Args:
            operation: Operation name
            duration: Duration in seconds
        """
        async with self._lock:
            if operation not in self._operations:
                self._operations[operation] = []
            self._operations[operation].append(duration)
            
            if len(self._operations[operation]) > 1000:
                self._operations[operation] = self._operations[operation][-1000:]
    
    async def get_stats(self, operation: str) -> dict[str, float] | None:
        """Get statistics for an operation.
        
        Args:
            operation: Operation name
            
        Returns:
            Statistics dictionary or None if not found
        """
        async with self._lock:
            if operation not in self._operations:
                return None
            
            durations = self._operations[operation]
            sorted_durations = sorted(durations)
            
            return {
                "count": len(durations),
                "total": sum(durations),
                "mean": sum(durations) / len(durations),
                "min": min(durations),
                "max": max(durations),
                "p50": sorted_durations[len(sorted_durations) // 2],
                "p95": sorted_durations[int(len(sorted_durations) * 0.95)],
                "p99": sorted_durations[int(len(sorted_durations) * 0.99)],
            }
    
    async def get_all_stats(self) -> dict[str, dict[str, float]]:
        """Get statistics for all operations."""
        stats = {}
        for operation in list(self._operations.keys()):
            op_stats = await self.get_stats(operation)
            if op_stats:
                stats[operation] = op_stats
        return stats


@dataclass
class BatchConfig:
    """Configuration for batch processing."""
    
    batch_size: int = 10
    max_concurrent: int = 5
    timeout: float = 300.0


async def process_batch(
    items: list[T],
    processor: Callable[[T], Awaitable[Any]],
    config: BatchConfig | None = None,
) -> list[Any]:
    """Process items in batches with concurrency control.
    
    Args:
        items: Items to process
        processor: Async function to process each item
        config: Batch configuration
        
    Returns:
        List of results in same order as input
    """
    config = config or BatchConfig()
    
    results: list[Any] = [None] * len(items)
    semaphore = asyncio.Semaphore(config.max_concurrent)
    
    async def process_with_semaphore(index: int, item: T) -> tuple[int, Any]:
        async with semaphore:
            result = await run_with_timeout(
                processor(item),
                config.timeout / len(items),
                f"batch_item_{index}"
            )
            return index, result
    
    tasks = [
        process_with_semaphore(i, item) 
        for i, item in enumerate(items)
    ]
    
    completed = await asyncio.gather(*tasks, return_exceptions=True)
    
    for result in completed:
        if isinstance(result, tuple):
            index, value = result
            results[index] = value
        else:
            logger.error("Batch processing error: %s", result)
    
    return results
