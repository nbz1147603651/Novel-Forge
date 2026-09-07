"""Advanced performance monitoring and profiling utilities for Novel Forge.

This module provides comprehensive performance monitoring, profiling, and
optimization utilities to enhance system performance across all components.
"""

from __future__ import annotations

import asyncio
import functools
import gc
import logging
import os
import time
import tracemalloc

# Optional psutil import - gracefully handle when not available
try:
    import psutil  # type: ignore[import-untyped]  # pyright: ignore[reportMissingModuleSource]
    PSUTIL_AVAILABLE = True
except ImportError:
    psutil = None
    PSUTIL_AVAILABLE = False

from collections import deque
from collections.abc import AsyncIterator, Iterator
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass, field
from threading import Lock
from typing import Any, Awaitable, Callable, Dict, Optional, TypeVar, cast

# Removed memory_profiler import as it's not commonly used and may not be installed
# import memory_profiler

logger = logging.getLogger(__name__)

T = TypeVar("T")


@dataclass
class PerformanceMetrics:
    """Container for performance metrics."""
    
    # Timing metrics
    execution_time: float = 0.0
    cpu_time: float = 0.0
    wall_time: float = 0.0
    
    # Memory metrics
    memory_before: int = 0
    memory_after: int = 0
    memory_peak: int = 0
    memory_delta: int = 0
    
    # Resource metrics
    cpu_percent: float = 0.0
    io_read_bytes: int = 0
    io_write_bytes: int = 0
    
    # Custom metrics
    custom_metrics: Dict[str, Any] = field(default_factory=dict)
    
    def __post_init__(self) -> None:
        if self.custom_metrics is None:
            self.custom_metrics = {}


class PerformanceMonitor:
    """Advanced performance monitoring with real-time metrics collection."""
    
    def __init__(self, *, enable_tracemalloc: bool = True) -> None:
        self._metrics_history: deque[PerformanceMetrics] = deque(maxlen=1000)
        self._active_monitors: Dict[str, asyncio.Task[Any]] = {}
        self._lock = Lock()
        # Use getattr to safely access Process class when psutil might be None
        # Type is Any since psutil.Process may not be available at runtime
        self._process: Optional[Any] = (
            getattr(psutil, "Process", lambda: None)() if PSUTIL_AVAILABLE else None
        )
        self._cpu_times: Dict[str, float] = {}
        self._wall_times: Dict[str, float] = {}
        self._io_counters: Dict[str, Dict[str, int]] = {}
        self._tracemalloc_enabled = enable_tracemalloc
        self._tracemalloc_started = False
    
    def ensure_tracemalloc(self) -> None:
        """Lazily start tracemalloc if enabled and not already started."""
        if self._tracemalloc_enabled and not self._tracemalloc_started:
            if not tracemalloc.is_tracing():
                tracemalloc.start()
            self._tracemalloc_started = True
    
    def is_tracemalloc_active(self) -> bool:
        """Check if tracemalloc is currently active."""
        return tracemalloc.is_tracing()

    def _get_process_io_bytes(self) -> tuple[int, int]:
        """Safely read process IO counters in a cross-platform/type-safe way."""
        if self._process is None:
            return 0, 0
        io_counters_func = getattr(cast(Any, self._process), "io_counters", None)
        if not callable(io_counters_func):
            return 0, 0

        try:
            io_counters = io_counters_func()
        except Exception:
            return 0, 0

        read_bytes = int(getattr(io_counters, "read_bytes", 0) or 0)
        write_bytes = int(getattr(io_counters, "write_bytes", 0) or 0)
        return read_bytes, write_bytes

    def _get_process_io_counters_dict(self) -> Dict[str, Any]:
        """Safely return process IO counters as a dictionary."""
        if self._process is None:
            return {}
        io_counters_func = getattr(cast(Any, self._process), "io_counters", None)
        if not callable(io_counters_func):
            return {}

        try:
            io_counters = io_counters_func()
        except Exception:
            return {}

        as_dict = getattr(io_counters, "_asdict", None)
        if callable(as_dict):
            try:
                io_dict = as_dict()
                if isinstance(io_dict, dict):
                    return io_dict
            except Exception:
                pass

        read_bytes, write_bytes = self._get_process_io_bytes()
        return {
            "read_bytes": read_bytes,
            "write_bytes": write_bytes,
        }
    
    def start_monitoring(self, operation_name: str) -> None:
        """Start monitoring for a specific operation."""
        with self._lock:
            self._cpu_times[operation_name] = time.process_time()
            self._wall_times[operation_name] = time.perf_counter()
            read_bytes, write_bytes = self._get_process_io_bytes()
            self._io_counters[operation_name] = {
                "read_bytes": read_bytes,
                "write_bytes": write_bytes,
            }
    
    def stop_monitoring(self, operation_name: str) -> PerformanceMetrics:
        """Stop monitoring and collect metrics for an operation."""
        wall_time_end = time.perf_counter()
        cpu_time_end = time.process_time()
        
        # Get memory info if psutil is available
        memory_after = 0
        cpu_percent = 0.0
        if self._process is not None:
            try:
                memory_info = self._process.memory_info()
                memory_after = memory_info.rss
                cpu_percent = self._process.cpu_percent()
            except Exception:
                pass
        
        initial_cpu_time = self._cpu_times.pop(operation_name, cpu_time_end)
        initial_wall_time = self._wall_times.pop(operation_name, wall_time_end)
        initial_io = self._io_counters.pop(operation_name, {'read_bytes': 0, 'write_bytes': 0})

        current_read_bytes, current_write_bytes = self._get_process_io_bytes()
        io_read_delta = current_read_bytes - initial_io["read_bytes"]
        io_write_delta = current_write_bytes - initial_io["write_bytes"]
        
        metrics = PerformanceMetrics(
            execution_time=wall_time_end - initial_wall_time,
            cpu_time=cpu_time_end - initial_cpu_time,
            wall_time=wall_time_end - initial_wall_time,
            memory_before=0,
            memory_after=memory_after,
            memory_peak=0,
            memory_delta=0,
            cpu_percent=cpu_percent,
            io_read_bytes=io_read_delta,
            io_write_bytes=io_write_delta
        )
        
        with self._lock:
            self._metrics_history.append(metrics)
        
        return metrics
    
    def get_average_metrics(self, operation_name: Optional[str] = None) -> Dict[str, float]:
        """Get average metrics for operations."""
        with self._lock:
            if not self._metrics_history:
                return {}
            
            avg_metrics = {
                'avg_execution_time': sum(m.execution_time for m in self._metrics_history) / len(self._metrics_history),
                'avg_cpu_time': sum(m.cpu_time for m in self._metrics_history) / len(self._metrics_history),
                'avg_memory_delta': sum(m.memory_delta for m in self._metrics_history) / len(self._metrics_history),
                'avg_cpu_percent': sum(m.cpu_percent for m in self._metrics_history) / len(self._metrics_history),
                'avg_io_read_bytes': sum(m.io_read_bytes for m in self._metrics_history) / len(self._metrics_history),
                'avg_io_write_bytes': sum(m.io_write_bytes for m in self._metrics_history) / len(self._metrics_history),
            }
        
        return avg_metrics
    
    def get_peak_metrics(self) -> Dict[str, Any]:
        """Get peak resource usage metrics."""
        with self._lock:
            if not self._metrics_history:
                return {}
            
            return {
                'peak_memory': max((m.memory_after for m in self._metrics_history), default=0),
                'peak_cpu_percent': max((m.cpu_percent for m in self._metrics_history), default=0),
                'peak_io_read_bytes': max((m.io_read_bytes for m in self._metrics_history), default=0),
                'peak_io_write_bytes': max((m.io_write_bytes for m in self._metrics_history), default=0),
            }
    
    def get_current_system_stats(self) -> Dict[str, Any]:
        """Get current system resource usage."""
        if self._process is None or not PSUTIL_AVAILABLE:
            return {}
        try:
            memory = self._process.memory_info()
            return {
                'memory_rss': memory.rss,
                'memory_vms': memory.vms,
                'cpu_percent': self._process.cpu_percent(),
                'num_threads': self._process.num_threads(),
                'num_fds': self._process.num_fds() if hasattr(self._process, 'num_fds') else 0,
                'io_counters': self._get_process_io_counters_dict(),
                'system_cpu_percent': psutil.cpu_percent(interval=None) if psutil else 0,
                'system_memory_percent': psutil.virtual_memory().percent if psutil else 0,
            }
        except Exception as e:
            logger.warning(f"Could not get system stats: {e}")
            return {}
    
    def get_memory_rss(self) -> int:
        """Safely get current process memory RSS. Returns 0 if psutil not available."""
        if self._process is None:
            return 0
        try:
            return int(self._process.memory_info().rss)
        except Exception:
            return 0


# Global performance monitor instance
_perf_monitor = PerformanceMonitor()


def profile_function(
    enabled: bool = True,
    track_memory: bool = True,
    track_cpu: bool = True
) -> Callable[[Callable[..., T]], Callable[..., T]]:
    """Decorator to profile function performance with detailed metrics."""
    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        if not enabled:
            return func
        
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> T:
            operation_name = f"{func.__module__}.{func.__qualname__}"
            
            # Ensure tracemalloc is started if memory tracking is enabled
            if track_memory:
                _perf_monitor.ensure_tracemalloc()
            
            # Start monitoring
            _perf_monitor.start_monitoring(operation_name)
            
            # Track memory before execution
            if track_memory:
                gc.collect()  # Clean up before measuring
                memory_before = _perf_monitor.get_memory_rss()
                snapshot_before = tracemalloc.take_snapshot() if tracemalloc.is_tracing() else None
            else:
                memory_before = 0
                snapshot_before = None
            
            start_time = time.perf_counter()
            
            try:
                # Execute the function
                result = func(*args, **kwargs)
                
                # Calculate execution time
                execution_time = time.perf_counter() - start_time
                
                # Track memory after execution
                if track_memory:
                    gc.collect()
                    memory_after = _perf_monitor.get_memory_rss()
                    snapshot_after = tracemalloc.take_snapshot() if tracemalloc.is_tracing() else None
                    
                    # Calculate memory delta and peak usage
                    memory_delta = memory_after - memory_before
                    peak_memory = memory_after
                    
                    if snapshot_before and snapshot_after:
                        top_stats = snapshot_after.compare_to(snapshot_before, 'lineno')
                        for stat in top_stats[:3]:  # Top 3 memory allocations
                            logger.debug(f"Memory allocation: {stat}")
                else:
                    memory_after = 0
                    memory_delta = 0
                    peak_memory = 0
                
                # Stop monitoring and collect metrics
                metrics = _perf_monitor.stop_monitoring(operation_name)
                metrics.memory_before = memory_before
                metrics.memory_after = memory_after
                metrics.memory_delta = memory_delta
                metrics.memory_peak = peak_memory
                metrics.execution_time = execution_time
                
                # Log performance metrics
                logger.debug(
                    f"Performance metrics for {operation_name}: "
                    f"Execution time: {execution_time:.4f}s, "
                    f"Memory delta: {memory_delta / 1024 / 1024:.2f}MB, "
                    f"CPU percent: {metrics.cpu_percent:.2f}%"
                )
                
                return result
            except Exception:
                # Stop monitoring even if function fails
                _perf_monitor.stop_monitoring(operation_name)
                raise
        
        return wrapper
    
    return decorator


def profile_async_function(
    enabled: bool = True,
    track_memory: bool = True,
    track_cpu: bool = True
) -> Callable[[Callable[..., Awaitable[T]]], Callable[..., Awaitable[T]]]:
    """Async version of the profiling decorator."""
    def decorator(func: Callable[..., Awaitable[T]]) -> Callable[..., Awaitable[T]]:
        if not enabled:
            return func
        
        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> T:
            operation_name = f"{func.__module__}.{func.__qualname__}"
            
            # Ensure tracemalloc is started if memory tracking is enabled
            if track_memory:
                _perf_monitor.ensure_tracemalloc()
            
            # Start monitoring
            _perf_monitor.start_monitoring(operation_name)
            
            # Track memory before execution
            if track_memory:
                gc.collect()  # Clean up before measuring
                memory_before = _perf_monitor.get_memory_rss()
                snapshot_before = tracemalloc.take_snapshot() if tracemalloc.is_tracing() else None
            else:
                memory_before = 0
                snapshot_before = None
            
            start_time = time.perf_counter()
            
            try:
                # Execute the async function
                result = await func(*args, **kwargs)
                
                # Calculate execution time
                execution_time = time.perf_counter() - start_time
                
                # Track memory after execution
                if track_memory:
                    gc.collect()
                    memory_after = _perf_monitor.get_memory_rss()
                    snapshot_after = tracemalloc.take_snapshot() if tracemalloc.is_tracing() else None
                    
                    # Calculate memory delta and peak usage
                    memory_delta = memory_after - memory_before
                    peak_memory = memory_after
                    
                    if snapshot_before and snapshot_after:
                        top_stats = snapshot_after.compare_to(snapshot_before, 'lineno')
                        for stat in top_stats[:3]:  # Top 3 memory allocations
                            logger.debug(f"Memory allocation: {stat}")
                else:
                    memory_after = 0
                    memory_delta = 0
                    peak_memory = 0
                
                # Stop monitoring and collect metrics
                metrics = _perf_monitor.stop_monitoring(operation_name)
                metrics.memory_before = memory_before
                metrics.memory_after = memory_after
                metrics.memory_delta = memory_delta
                metrics.memory_peak = peak_memory
                metrics.execution_time = execution_time
                
                # Log performance metrics
                logger.debug(
                    f"Performance metrics for {operation_name}: "
                    f"Execution time: {execution_time:.4f}s, "
                    f"Memory delta: {memory_delta / 1024 / 1024:.2f}MB, "
                    f"CPU percent: {metrics.cpu_percent:.2f}%"
                )
                
                return result
            except Exception:
                # Stop monitoring even if function fails
                _perf_monitor.stop_monitoring(operation_name)
                raise
        
        return wrapper
    
    return decorator


@contextmanager
def performance_context(operation_name: str, track_memory: bool = True) -> Iterator[None]:
    """Context manager for performance monitoring."""
    if track_memory:
        _perf_monitor.ensure_tracemalloc()
    
    _perf_monitor.start_monitoring(operation_name)
    
    if track_memory:
        gc.collect()
        memory_before = _perf_monitor.get_memory_rss()
    else:
        memory_before = 0
    
    start_time = time.perf_counter()
    
    try:
        yield
    finally:
        execution_time = time.perf_counter() - start_time
        
        if track_memory:
            gc.collect()
            memory_after = _perf_monitor.get_memory_rss()
            
            memory_delta = memory_after - memory_before
            peak_memory = memory_after
        else:
            memory_after = 0
            memory_delta = 0
            peak_memory = 0
        
        metrics = _perf_monitor.stop_monitoring(operation_name)
        metrics.memory_before = memory_before
        metrics.memory_after = memory_after
        metrics.memory_delta = memory_delta
        metrics.memory_peak = peak_memory
        metrics.execution_time = execution_time
        
        logger.debug(
            f"Context performance metrics for {operation_name}: "
            f"Execution time: {execution_time:.4f}s, "
            f"Memory delta: {memory_delta / 1024 / 1024:.2f}MB"
        )


@asynccontextmanager
async def async_performance_context(
    operation_name: str,
    track_memory: bool = True,
) -> AsyncIterator[None]:
    """Async context manager for performance monitoring."""
    if track_memory:
        _perf_monitor.ensure_tracemalloc()
    
    _perf_monitor.start_monitoring(operation_name)
    
    if track_memory:
        gc.collect()
        memory_before = _perf_monitor.get_memory_rss()
    else:
        memory_before = 0
    
    start_time = time.perf_counter()
    
    try:
        yield
    finally:
        execution_time = time.perf_counter() - start_time
        
        if track_memory:
            gc.collect()
            memory_after = _perf_monitor.get_memory_rss()
            
            memory_delta = memory_after - memory_before
            peak_memory = memory_after
        else:
            memory_after = 0
            memory_delta = 0
            peak_memory = 0
        
        metrics = _perf_monitor.stop_monitoring(operation_name)
        metrics.memory_before = memory_before
        metrics.memory_after = memory_after
        metrics.memory_delta = memory_delta
        metrics.memory_peak = peak_memory
        metrics.execution_time = execution_time
        
        logger.debug(
            f"Async context performance metrics for {operation_name}: "
            f"Execution time: {execution_time:.4f}s, "
            f"Memory delta: {memory_delta / 1024 / 1024:.2f}MB"
        )


class OptimizedThreadPool:
    """Optimized thread pool with performance monitoring and resource management."""
    
    def __init__(
        self,
        max_workers: Optional[int] = None,
        monitor_performance: bool = True,
    ) -> None:
        self._max_workers = max_workers or min(32, (os.cpu_count() or 1) + 4)
        self._executor = ThreadPoolExecutor(max_workers=self._max_workers)
        self._monitor_performance = monitor_performance
        self._task_count = 0
        self._completed_tasks = 0
        self._failed_tasks = 0
    
    def submit(self, fn: Callable[..., T], *args: Any, **kwargs: Any) -> Future[T]:
        """Submit a function to the thread pool with optional performance monitoring."""
        self._task_count += 1
        
        if self._monitor_performance:
            # Wrap the function with performance monitoring
            @profile_function(enabled=True, track_memory=True, track_cpu=False)
            def monitored_fn(*a: Any, **kw: Any) -> T:
                return fn(*a, **kw)
            
            future = self._executor.submit(monitored_fn, *args, **kwargs)
        else:
            future = self._executor.submit(fn, *args, **kwargs)
        
        def callback(f: Future[T]) -> None:
            if f.exception():
                self._failed_tasks += 1
            else:
                self._completed_tasks += 1
        
        future.add_done_callback(callback)
        return future
    
    def map(self, fn: Callable[..., T], *iterables: Any) -> Iterator[T]:
        """Apply function to iterables with performance monitoring."""
        if self._monitor_performance:
            # Wrap the function with performance monitoring
            @profile_function(enabled=True, track_memory=True, track_cpu=False)
            def monitored_fn(*args: Any) -> T:
                return fn(*args)
            
            return self._executor.map(monitored_fn, *iterables)
        else:
            return self._executor.map(fn, *iterables)
    
    def shutdown(self, wait: bool = True) -> None:
        """Shutdown the thread pool."""
        self._executor.shutdown(wait=wait)
    
    def get_stats(self) -> Dict[str, Any]:
        """Get performance statistics for the thread pool."""
        return {
            'max_workers': self._max_workers,
            'total_tasks': self._task_count,
            'completed_tasks': self._completed_tasks,
            'failed_tasks': self._failed_tasks,
            'active_tasks': self._task_count - self._completed_tasks - self._failed_tasks
        }


class LRUCacheWithMetrics:
    """LRU Cache with performance metrics and monitoring."""
    
    def __init__(self, maxsize: int = 128, ttl: Optional[float] = None) -> None:
        self.maxsize = maxsize
        self.ttl = ttl
        self.cache: dict[Any, Any] = {}
        self.access_times: dict[Any, float] = {}
        self.insertion_times: dict[Any, float] = {}
        self.hits = 0
        self.misses = 0
        self.lock = Lock()
    
    def get(self, key: Any) -> Any:
        """Get value from cache with metrics."""
        with self.lock:
            current_time = time.time()
            
            if key in self.cache:
                # Check if TTL expired
                if self.ttl and (current_time - self.insertion_times[key]) > self.ttl:
                    del self.cache[key]
                    del self.access_times[key]
                    del self.insertion_times[key]
                    self.misses += 1
                    return None
                
                self.access_times[key] = current_time
                self.hits += 1
                return self.cache[key]
            else:
                self.misses += 1
                return None
    
    def put(self, key: Any, value: Any) -> None:
        """Put value in cache with LRU eviction."""
        with self.lock:
            current_time = time.time()
            
            # If cache is full, evict least recently used item
            if len(self.cache) >= self.maxsize and key not in self.cache:
                # Find least recently accessed item
                lru_key = min(self.access_times.keys(), key=lambda k: self.access_times[k])
                del self.cache[lru_key]
                del self.access_times[lru_key]
                del self.insertion_times[lru_key]
            
            self.cache[key] = value
            self.access_times[key] = current_time
            self.insertion_times[key] = current_time
    
    def stats(self) -> Dict[str, Any]:
        """Get cache statistics."""
        total_accesses = self.hits + self.misses
        hit_rate = self.hits / total_accesses if total_accesses > 0 else 0
        
        return {
            'size': len(self.cache),
            'maxsize': self.maxsize,
            'hits': self.hits,
            'misses': self.misses,
            'hit_rate': hit_rate,
            'ttl': self.ttl
        }
    
    def clear(self) -> None:
        """Clear the cache."""
        with self.lock:
            self.cache.clear()
            self.access_times.clear()
            self.insertion_times.clear()
            self.hits = 0
            self.misses = 0


def get_performance_monitor() -> PerformanceMonitor:
    """Get the global performance monitor instance."""
    return _perf_monitor


def get_optimized_thread_pool(max_workers: Optional[int] = None) -> OptimizedThreadPool:
    """Get an optimized thread pool with performance monitoring."""
    return OptimizedThreadPool(max_workers=max_workers)


def get_lru_cache(maxsize: int = 128, ttl: Optional[float] = None) -> LRUCacheWithMetrics:
    """Get an LRU cache with performance metrics."""
    return LRUCacheWithMetrics(maxsize=maxsize, ttl=ttl)


# Lazy-initialized instances
# These are initialized on first access to avoid creating ThreadPoolExecutor
# and cache during module import, which can cause issues with event loops.
_optimized_thread_pool: Optional[OptimizedThreadPool] = None
_performance_cache: Optional[LRUCacheWithMetrics] = None


def _get_optimized_thread_pool_lazy() -> OptimizedThreadPool:
    """Lazily initialize and return the optimized thread pool."""
    global _optimized_thread_pool
    if _optimized_thread_pool is None:
        _optimized_thread_pool = get_optimized_thread_pool()
    return _optimized_thread_pool


def _get_performance_cache_lazy() -> LRUCacheWithMetrics:
    """Lazily initialize and return the performance cache."""
    global _performance_cache
    if _performance_cache is None:
        _performance_cache = get_lru_cache(maxsize=256, ttl=300)
    return _performance_cache


def shutdown_performance_resources(*, wait: bool = False) -> None:
    """Shutdown lazily-initialized performance resources if they exist."""
    global _optimized_thread_pool, _performance_cache

    if _optimized_thread_pool is not None:
        try:
            _optimized_thread_pool.shutdown(wait=wait)
        except Exception:
            logger.debug("Failed to shutdown optimized thread pool cleanly", exc_info=True)
        finally:
            _optimized_thread_pool = None

    if _performance_cache is not None:
        try:
            _performance_cache.clear()
        except Exception:
            logger.debug("Failed to clear performance cache cleanly", exc_info=True)
        finally:
            _performance_cache = None


class _LazyThreadPoolProxy:
    """Proxy object that lazily initializes the thread pool on first attribute access."""
    
    def __init__(self, getter_func: Callable[[], OptimizedThreadPool]) -> None:
        self._getter = getter_func
    
    def __getattr__(self, name: str) -> Any:
        return getattr(self._getter(), name)
    
    def __repr__(self) -> str:
        return repr(self._getter())


class _LazyCacheProxy:
    """Proxy object that lazily initializes the cache on first attribute access."""
    
    def __init__(self, getter_func: Callable[[], LRUCacheWithMetrics]) -> None:
        self._getter = getter_func
    
    def __getattr__(self, name: str) -> Any:
        return getattr(self._getter(), name)
    
    def __repr__(self) -> str:
        return repr(self._getter())


# Create proxy objects for backward compatibility
optimized_thread_pool = _LazyThreadPoolProxy(_get_optimized_thread_pool_lazy)
performance_cache = _LazyCacheProxy(_get_performance_cache_lazy)


__all__ = [
    "PerformanceMetrics",
    "PerformanceMonitor",
    "LRUCacheWithMetrics",
    "OptimizedThreadPool",
    "profile_function",
    "profile_async_function",
    "performance_context",
    "async_performance_context",
    "get_performance_monitor",
    "get_optimized_thread_pool",
    "get_lru_cache",
    "optimized_thread_pool",
    "performance_cache",
    "_get_optimized_thread_pool_lazy",
    "_get_performance_cache_lazy",
    "shutdown_performance_resources",
]
