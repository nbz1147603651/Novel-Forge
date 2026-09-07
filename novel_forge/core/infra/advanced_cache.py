"""Advanced caching system with performance optimization for Novel Forge.

This module provides enhanced caching mechanisms with sophisticated eviction policies,
performance monitoring, and async support.
"""

from __future__ import annotations

import asyncio
import functools
import hashlib
import logging
import pickle
import sqlite3
import time
from collections import OrderedDict, defaultdict
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from threading import RLock
from typing import Any, Awaitable, Callable, Generic, Hashable, Optional, TypeVar, cast

logger = logging.getLogger(__name__)

T = TypeVar("T")
KT = TypeVar("KT", bound=Hashable)
VT = TypeVar("VT")

# Sentinel object to distinguish "not found" from "found but None"
_CACHE_MISS = object()


class CacheEvictionPolicy(Enum):
    """Supported cache eviction policies."""
    LRU = "lru"           # Least Recently Used
    LFU = "lfu"           # Least Frequently Used
    FIFO = "fifo"         # First In, First Out
    TTL = "ttl"           # Time To Live
    CUSTOM = "custom"     # Custom eviction policy


@dataclass
class CacheStats:
    """Statistics for cache performance."""
    hits: int = 0
    misses: int = 0
    evictions: int = 0
    size: int = 0
    max_size: int = 0
    memory_usage: int = 0  # Approximate memory usage in bytes
    
    @property
    def hit_rate(self) -> float:
        total = self.hits + self.misses
        return self.hits / total if total > 0 else 0.0
    
    @property
    def miss_rate(self) -> float:
        total = self.hits + self.misses
        return self.misses / total if total > 0 else 0.0


@dataclass
class CacheItem(Generic[VT]):
    """Represents a cached item with metadata."""
    value: VT
    created_at: float = field(default_factory=time.time)
    access_count: int = 1
    access_times: list[float] = field(default_factory=list)
    size: int = 0  # Approximate size in bytes
    
    def __post_init__(self) -> None:
        if not self.access_times:
            self.access_times = [self.created_at]
        # size is not used for eviction, skip expensive pickle.dumps
        # can use sys.getsizeof(self.value) for rough estimate if needed later
    
    def touch(self) -> None:
        """Update access tracking."""
        self.access_count += 1
        self.access_times.append(time.time())
    
    def is_expired(self, ttl: float) -> bool:
        """Check if item has expired based on TTL."""
        if ttl <= 0:
            return False
        return (time.time() - self.created_at) > ttl


class AdvancedCache(Generic[KT, VT]):
    """Advanced cache with multiple eviction policies and async support."""
    
    def __init__(
        self,
        max_size: int = 1000,
        ttl: float = 3600.0,  # 1 hour default
        policy: CacheEvictionPolicy = CacheEvictionPolicy.LRU,
        enable_stats: bool = True,
        persistence_path: Optional[str | Path] = None,
        compression: bool = False,
    ) -> None:
        self._max_size = max_size
        self._ttl = ttl
        self._policy = policy
        self._enable_stats = enable_stats
        self._compression = compression
        self._persistence_path = Path(persistence_path) if persistence_path else None
        
        # Internal storage based on policy - use OrderedDict for all to support move_to_end
        self._frequency_map: defaultdict[int, set[KT]] = defaultdict(set)
        self._cache: OrderedDict[KT, CacheItem[VT]] = OrderedDict()
        
        # Stats tracking
        self._stats = CacheStats(max_size=max_size) if enable_stats else None
        self._stats_lock = RLock()
        
        # Threading support
        self._lock = RLock()
        self._async_lock = asyncio.Lock()
        
        # Persistence support - initialize in __init__ not in _set_stats_size
        self._db_conn: Optional[sqlite3.Connection] = None
        if self._persistence_path:
            self._setup_persistence()
    
    def _update_stats(self, *, hits: int = 0, misses: int = 0, evictions: int = 0) -> None:
        """Safely update cache statistics."""
        if self._stats is not None:
            self._stats.hits += hits
            self._stats.misses += misses
            self._stats.evictions += evictions
        
    def _set_stats_size(self, size: int) -> None:
        """Safely set cache size in statistics."""
        if self._stats is not None:
            self._stats.size = size
    
    def _setup_persistence(self) -> None:
        """Setup database for persistent caching."""
        try:
            if self._persistence_path is not None:
                self._persistence_path.parent.mkdir(parents=True, exist_ok=True)
            self._db_conn = sqlite3.connect(
                str(self._persistence_path), 
                check_same_thread=False
            )
            self._db_conn.execute("""
                CREATE TABLE IF NOT EXISTS cache (
                    key_hash TEXT PRIMARY KEY,
                    value BLOB,
                    created_at REAL,
                    access_count INTEGER,
                    ttl REAL
                )
            """)
            self._db_conn.commit()
        except Exception as e:
            logger.warning(f"Failed to setup persistent cache: {e}")
            self._db_conn = None
    
    def _serialize_value(self, value: Any) -> bytes:
        """Serialize value for storage."""
        if self._compression:
            import gzip
            return gzip.compress(pickle.dumps(value))
        return pickle.dumps(value)
    
    def _deserialize_value(self, data: bytes) -> Any:
        """Deserialize value from storage."""
        try:
            if self._compression:
                import gzip
                return pickle.loads(gzip.decompress(data))
            return pickle.loads(data)
        except Exception as e:
            logger.warning(f"Failed to deserialize cached value: {e}")
            return None
    
    def _make_key_hash(self, key: KT) -> str:
        """Create a hash for the key."""
        if isinstance(key, str):
            return hashlib.sha256(key.encode()).hexdigest()
        return hashlib.sha256(str(key).encode()).hexdigest()
    
    def _evict_items(self) -> int:
        """Evict items based on the current policy."""
        evicted_count = 0
        
        with self._lock:
            if len(self._cache) <= self._max_size:
                return 0
            
            if self._policy == CacheEvictionPolicy.LRU:
                # Remove oldest accessed items
                while len(self._cache) > self._max_size:
                    oldest_key = next(iter(self._cache))
                    del self._cache[oldest_key]
                    evicted_count += 1
            
            elif self._policy == CacheEvictionPolicy.LFU:
                # Remove least frequently used items
                min_freq = min((item.access_count for item in self._cache.values()), default=1)
                items_to_remove = [
                    key for key, item in self._cache.items()
                    if item.access_count == min_freq
                ]
                
                for key in items_to_remove[:len(self._cache) - self._max_size]:
                    if key in self._cache:
                        del self._cache[key]
                        evicted_count += 1
            
            elif self._policy == CacheEvictionPolicy.FIFO:
                # Remove first inserted items
                while len(self._cache) > self._max_size:
                    oldest_key = next(iter(self._cache))
                    del self._cache[oldest_key]
                    evicted_count += 1
            
            elif self._policy == CacheEvictionPolicy.TTL:
                # Remove expired items
                expired_keys = [
                    key for key, item in self._cache.items()
                    if item.is_expired(self._ttl)
                ]
                
                for key in expired_keys:
                    del self._cache[key]
                    evicted_count += 1
        
        if self._enable_stats:
            self._update_stats(evictions=evicted_count)
        
        return evicted_count
    
    def get(self, key: KT, default: Optional[VT] = None) -> Optional[VT]:
        """Get value from cache with optional default."""
        with self._lock:
            # Check in-memory cache first
            if key in self._cache:
                item = self._cache[key]
                
                # Check TTL
                if item.is_expired(self._ttl):
                    del self._cache[key]
                    self._update_stats(misses=1)
                    return default
                
                # Update access tracking based on policy
                item.touch()
                
                # Only move to end for OrderedDict (LRU/TTL policies)
                if self._policy == CacheEvictionPolicy.LRU and hasattr(self._cache, 'move_to_end'):
                    # Move to end for LRU
                    self._cache.move_to_end(key)
                
                self._update_stats(hits=1)
                self._set_stats_size(len(self._cache))
                
                return item.value
            
            # Check persistent storage if configured
            if self._db_conn:
                try:
                    cursor = self._db_conn.execute(
                        "SELECT value FROM cache WHERE key_hash = ?",
                        (self._make_key_hash(key),)
                    )
                    row = cursor.fetchone()
                    if row:
                        value = self._deserialize_value(row[0])
                        if value is not None:
                            # Add back to in-memory cache
                            self.put(key, value)
                            self._update_stats(hits=1)
                            return cast(VT, value)
                except Exception as e:
                    logger.warning(f"Persistent cache read failed: {e}")
            
            # Update stats if enabled
            if self._stats is not None:
                self._update_stats(misses=1)
            
            return default
    
    def put(self, key: KT, value: VT) -> None:
        """Put value in cache with eviction if needed."""
        with self._lock:
            item = CacheItem(value=value)
            
            if self._policy in [CacheEvictionPolicy.LRU, CacheEvictionPolicy.TTL]:
                # For LRU/TTL, use OrderedDict
                if key in self._cache:
                    # Update existing item
                    self._cache[key] = item
                    if self._policy == CacheEvictionPolicy.LRU and hasattr(self._cache, 'move_to_end'):
                        self._cache.move_to_end(key)
                else:
                    # Add new item
                    self._cache[key] = item
                    if self._policy == CacheEvictionPolicy.LRU and hasattr(self._cache, 'move_to_end'):
                        self._cache.move_to_end(key)
            else:
                # For other policies, use regular dict
                self._cache[key] = item
            
            # Persist to database if configured
            if self._db_conn:
                try:
                    serialized_value = self._serialize_value(value)
                    self._db_conn.execute(
                        "INSERT OR REPLACE INTO cache (key_hash, value, created_at, access_count, ttl) VALUES (?, ?, ?, ?, ?)",
                        (self._make_key_hash(key), serialized_value, item.created_at, item.access_count, self._ttl)
                    )
                    self._db_conn.commit()
                except Exception as e:
                    logger.warning(f"Persistent cache write failed: {e}")
            
            # Evict items if needed
            self._evict_items()
            
            if self._stats is not None:
                self._set_stats_size(len(self._cache))
    
    async def aget(self, key: KT, default: Optional[VT] = None) -> Optional[VT]:
        """Async version of get."""
        async with self._async_lock:
            return self.get(key, default)
    
    async def aput(self, key: KT, value: VT) -> None:
        """Async version of put."""
        async with self._async_lock:
            self.put(key, value)
    
    def delete(self, key: KT) -> bool:
        """Delete item from cache."""
        with self._lock:
            if key in self._cache:
                del self._cache[key]
                if self._db_conn:
                    try:
                        self._db_conn.execute(
                            "DELETE FROM cache WHERE key_hash = ?",
                            (self._make_key_hash(key),)
                        )
                        self._db_conn.commit()
                    except Exception as e:
                        logger.warning(f"Persistent cache delete failed: {e}")
                return True
            return False
    
    def clear(self) -> None:
        """Clear all cache entries."""
        with self._lock:
            self._cache.clear()
            if self._db_conn:
                try:
                    self._db_conn.execute("DELETE FROM cache")
                    self._db_conn.commit()
                except Exception as e:
                    logger.warning(f"Persistent cache clear failed: {e}")
            if self._stats is not None:
                with self._stats_lock:
                    self._stats.hits = 0
                    self._stats.misses = 0
                    self._stats.evictions = 0
                    self._stats.size = 0
    
    def stats(self) -> Optional[CacheStats]:
        """Get cache statistics."""
        if self._stats is None:
            return None
        with self._stats_lock:
            return CacheStats(
                hits=self._stats.hits,
                misses=self._stats.misses,
                evictions=self._stats.evictions,
                size=self._stats.size,
                max_size=self._stats.max_size,
                memory_usage=sum(getattr(item, 'size', 0) for item in self._cache.values())
            )
    
    def close(self) -> None:
        """Close cache resources."""
        if self._db_conn:
            try:
                self._db_conn.close()
            except Exception:
                pass
            finally:
                self._db_conn = None


class CacheManager:
    """Centralized cache manager for the application."""
    
    def __init__(self) -> None:
        self._caches: dict[str, AdvancedCache[Any, Any]] = {}
        self._default_ttl = 3600.0  # 1 hour
        self._default_max_size = 1000
        self._lock = RLock()
    
    def get_cache(
        self,
        name: str,
        max_size: Optional[int] = None,
        ttl: Optional[float] = None,
        policy: CacheEvictionPolicy = CacheEvictionPolicy.LRU,
        persistence_path: Optional[str | Path] = None,
    ) -> AdvancedCache[Any, Any]:
        """Get or create a named cache."""
        with self._lock:
            if name in self._caches:
                return self._caches[name]
            
            cache: AdvancedCache[Any, Any] = AdvancedCache(
                max_size=max_size or self._default_max_size,
                ttl=ttl or self._default_ttl,
                policy=policy,
                enable_stats=True,
                persistence_path=persistence_path,
            )
            self._caches[name] = cache
            return cache
    
    def invalidate_cache(self, name: str) -> bool:
        """Invalidate a specific cache."""
        with self._lock:
            if name in self._caches:
                self._caches[name].clear()
                del self._caches[name]
                return True
            return False
    
    def clear_all(self) -> None:
        """Clear all caches."""
        with self._lock:
            for cache in self._caches.values():
                cache.clear()
            self._caches.clear()

    def close_all(self) -> None:
        """Close and release all managed caches."""
        with self._lock:
            for cache in self._caches.values():
                try:
                    cache.close()
                except Exception:
                    logger.debug("Failed to close cache cleanly", exc_info=True)
            self._caches.clear()
    
    def get_stats(self) -> dict[str, CacheStats]:
        """Get statistics for all caches."""
        with self._lock:
            result = {}
            for name, cache in self._caches.items():
                stats = cache.stats()
                if stats is not None:
                    result[name] = stats
            return result


# Global cache manager instance
cache_manager = CacheManager()


def memoize(
    ttl: float = 3600.0,
    max_size: int = 128,
    cache_name: Optional[str] = None
) -> Callable[[Callable[..., T]], Callable[..., T]]:
    """Decorator to memoize function results with TTL and size limits."""
    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        nonlocal cache_name
        if not cache_name:
            cache_name = f"memoize_{func.__module__}_{func.__name__}"
        
        cache = cache_manager.get_cache(
            name=cache_name,
            max_size=max_size,
            ttl=ttl,
            policy=CacheEvictionPolicy.TTL
        )
        
        def wrapper(*args: Any, **kwargs: Any) -> T:
            # Create cache key from args and kwargs
            key_parts = [str(args)]
            if kwargs:
                key_parts.append(str(sorted(kwargs.items())))
            key = ":".join(key_parts)
            
            # Try to get from cache
            cached_result = cache.get(key, default=_CACHE_MISS)
            if cached_result is not _CACHE_MISS:
                return cast(T, cached_result)
            
            # Execute function and cache result
            result = func(*args, **kwargs)
            cache.put(key, result)
            return result
        
        # Copy function attributes
        functools.update_wrapper(wrapper, func)
        
        # Add cache management methods to the wrapper
        wrapper_with_cache_attrs = cast(Any, wrapper)
        wrapper_with_cache_attrs.cache_clear = lambda: cache.clear()
        wrapper_with_cache_attrs.cache_stats = lambda: cache.stats()
        
        return wrapper
    
    return decorator


def async_memoize(
    ttl: float = 3600.0,
    max_size: int = 128,
    cache_name: Optional[str] = None
) -> Callable[[Callable[..., Awaitable[T]]], Callable[..., Awaitable[T]]]:
    """Async version of the memoize decorator."""
    def decorator(func: Callable[..., Awaitable[T]]) -> Callable[..., Awaitable[T]]:
        nonlocal cache_name
        if not cache_name:
            cache_name = f"async_memoize_{func.__module__}_{func.__name__}"
        
        cache = cache_manager.get_cache(
            name=cache_name,
            max_size=max_size,
            ttl=ttl,
            policy=CacheEvictionPolicy.TTL
        )
        
        async def wrapper(*args: Any, **kwargs: Any) -> T:
            # Create cache key from args and kwargs
            key_parts = [str(args)]
            if kwargs:
                key_parts.append(str(sorted(kwargs.items())))
            key = ":".join(key_parts)
            
            # Try to get from cache
            cached_result = cache.get(key, default=_CACHE_MISS)
            if cached_result is not _CACHE_MISS:
                return cast(T, cached_result)
            
            # Execute function and cache result
            result = await func(*args, **kwargs)
            cache.put(key, result)
            return result
        
        # Copy function attributes
        functools.update_wrapper(wrapper, func)
        
        # Add cache management methods to the wrapper
        wrapper_with_cache_attrs = cast(Any, wrapper)
        wrapper_with_cache_attrs.cache_clear = lambda: cache.clear()
        wrapper_with_cache_attrs.cache_stats = lambda: cache.stats()
        
        return wrapper
    
    return decorator


# Convenience functions for common cache types
def get_model_response_cache() -> AdvancedCache[Any, Any]:
    """Get cache specifically for model responses."""
    return cache_manager.get_cache(
        name="model_responses",
        max_size=500,
        ttl=1800.0,  # 30 minutes
        policy=CacheEvictionPolicy.LRU,
        persistence_path="./data/cache/model_responses.db"
    )


def get_prompt_cache() -> AdvancedCache[Any, Any]:
    """Get cache for prompt templates and related data."""
    return cache_manager.get_cache(
        name="prompts",
        max_size=200,
        ttl=3600.0,  # 1 hour
        policy=CacheEvictionPolicy.TTL,
        persistence_path="./data/cache/prompts.db"
    )


def get_content_cache() -> AdvancedCache[Any, Any]:
    """Get cache for processed content."""
    return cache_manager.get_cache(
        name="content",
        max_size=1000,
        ttl=7200.0,  # 2 hours
        policy=CacheEvictionPolicy.LRU,
        persistence_path="./data/cache/content.db"
    )


# Lazy-initialized cache instances
# These module-level variables are initialized on first access to avoid
# creating SQLite connections and file I/O during module import.
_model_response_cache: Optional[AdvancedCache[Any, Any]] = None
_prompt_cache: Optional[AdvancedCache[Any, Any]] = None
_content_cache: Optional[AdvancedCache[Any, Any]] = None


def _get_model_response_cache_lazy() -> AdvancedCache[Any, Any]:
    """Lazily initialize and return the model response cache."""
    global _model_response_cache
    if _model_response_cache is None:
        _model_response_cache = get_model_response_cache()
    return _model_response_cache


def _get_prompt_cache_lazy() -> AdvancedCache[Any, Any]:
    """Lazily initialize and return the prompt cache."""
    global _prompt_cache
    if _prompt_cache is None:
        _prompt_cache = get_prompt_cache()
    return _prompt_cache


def _get_content_cache_lazy() -> AdvancedCache[Any, Any]:
    """Lazily initialize and return the content cache."""
    global _content_cache
    if _content_cache is None:
        _content_cache = get_content_cache()
    return _content_cache


def shutdown_cache_resources() -> None:
    """Close only caches that have been initialized and reset lazy handles."""
    global _model_response_cache, _prompt_cache, _content_cache
    cache_manager.close_all()
    _model_response_cache = None
    _prompt_cache = None
    _content_cache = None


# Module-level cache properties for backward compatibility
# These use lazy initialization to avoid import-time side effects
class _LazyCacheProxy:
    """Proxy object that lazily initializes the cache on first attribute access."""
    
    def __init__(self, getter_func: Callable[[], AdvancedCache[Any, Any]]) -> None:
        self._getter = getter_func
    
    def __getattr__(self, name: str) -> Any:
        return getattr(self._getter(), name)
    
    def __repr__(self) -> str:
        return repr(self._getter())


# Create proxy objects for backward compatibility with code that imports these directly
model_response_cache = _LazyCacheProxy(_get_model_response_cache_lazy)
prompt_cache = _LazyCacheProxy(_get_prompt_cache_lazy)
content_cache = _LazyCacheProxy(_get_content_cache_lazy)


__all__ = [
    "AdvancedCache",
    "CacheManager",
    "CacheItem",
    "CacheStats",
    "CacheEvictionPolicy",
    "CacheManager",
    "cache_manager",
    "memoize",
    "async_memoize",
    "get_model_response_cache",
    "get_prompt_cache",
    "get_content_cache",
    "model_response_cache",
    "prompt_cache",
    "content_cache",
    "_get_model_response_cache_lazy",
    "_get_prompt_cache_lazy",
    "_get_content_cache_lazy",
    "shutdown_cache_resources",
]
