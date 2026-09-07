"""Tests for gateway/cache.py CachePolicy LRU semantics.

Covers:
- In-memory store eviction (FIFO / LRU approximation)
- SQLite-backed persistence
- Thread safety basics
- get / put / clear / stats
"""

from __future__ import annotations

import threading
from pathlib import Path

from novel_forge.gateway.cache import CachePolicy
from novel_forge.gateway.types import ModelRequest, ModelResponse


def _make_request(prompt: str, model_id: str = "gpt-4o") -> ModelRequest:
    """Helper: build a minimal ModelRequest."""
    from novel_forge.core.constants import TaskType

    return ModelRequest(
        task_type=TaskType.DRAFT,
        messages=[{"role": "user", "content": prompt}],
        model_id=model_id,
        max_tokens=256,
        temperature=0.0,
    )


def _make_response(text: str) -> ModelResponse:
    """Helper: build a minimal ModelResponse."""
    return ModelResponse(
        content=text,
        model_id="gpt-4o",
        prompt_tokens=10,
        completion_tokens=20,
        total_tokens=30,
    )


# ── Basic put / get ─────────────────────────────────────────────


class TestCachePolicyBasic:
    """Basic put/get lifecycle tests."""

    def test_put_and_get_returns_response(self):
        """A stored response is retrievable by the matching request."""
        cache = CachePolicy(enabled=True, max_size=10)
        req = _make_request("你好世界")
        resp = _make_response("你好！这是一段回复。")

        cache.put(req, resp)
        retrieved = cache.get(req)

        assert retrieved is not None
        assert retrieved.content == "你好！这是一段回复。"

    def test_get_unknown_request_returns_none(self):
        """Requests not in cache return None."""
        cache = CachePolicy(enabled=True, max_size=10)
        req = _make_request("未知的问题")
        assert cache.get(req) is None

    def test_disabled_cache_always_returns_none(self):
        """When enabled=False, get always returns None."""
        cache = CachePolicy(enabled=False)
        cache.put(_make_request("test"), _make_response("ignored"))
        assert cache.get(_make_request("test")) is None

    def test_stats_tracks_hits_and_misses(self):
        """Stats reflect hit/miss counters."""
        cache = CachePolicy(enabled=True, max_size=10)
        req = _make_request("计数测试")

        # miss
        cache.get(req)
        # hit after put
        cache.put(req, _make_response("hit"))
        cache.get(req)

        stats = cache.stats()
        assert stats["hits"] == 1
        assert stats["misses"] == 1


# ── LRU / eviction ─────────────────────────────────────────────


class TestCachePolicyLRU:
    """Tests for in-memory LRU-style eviction."""

    def test_max_size_limits_memory_entries(self):
        """When in-memory store reaches max_size, the LRU entry is evicted."""
        cache = CachePolicy(enabled=True, max_size=3)
        # Fill to capacity — order: req0, req1, req2 (req2 is most recent)
        for i in range(3):
            cache.put(_make_request(f"请求{i}"), _make_response(f"响应{i}"))

        # Adding a 4th entry evicts the LRU (req0, oldest never accessed)
        cache.put(_make_request("请求3"), _make_response("响应3"))

        # Oldest (req0) should be evicted
        assert cache.get(_make_request("请求0")) is None
        # req1, req2 still present
        assert cache.get(_make_request("请求1")) is not None
        assert cache.get(_make_request("请求2")) is not None
        # Newest should be present
        assert cache.get(_make_request("请求3")) is not None

    def test_access_after_get_updates_lru_order(self):
        """Cache uses true LRU: accessing an entry refreshes its eviction priority."""
        cache = CachePolicy(enabled=True, max_size=2)
        cache.put(_make_request("A"), _make_response("a"))
        cache.put(_make_request("B"), _make_response("b"))

        # Accessing A refreshes its position (now A is most recent)
        cache.get(_make_request("A"))

        # Adding C evicts B (the oldest), not A
        cache.put(_make_request("C"), _make_response("c"))

        # A was accessed recently so survives; B is evicted
        assert cache.get(_make_request("A")) is not None
        assert cache.get(_make_request("B")) is None
        assert cache.get(_make_request("C")) is not None

    def test_duplicate_put_does_not_cause_eviction(self):
        """Re-putting the same key updates the value without evicting anything."""
        cache = CachePolicy(enabled=True, max_size=2)
        cache.put(_make_request("X"), _make_response("v1"))
        cache.put(_make_request("Y"), _make_response("y"))
        # Re-put same key
        cache.put(_make_request("X"), _make_response("v2"))
        # Adding Z should evict Y (oldest after X's second put)
        cache.put(_make_request("Z"), _make_response("z"))

        assert cache.get(_make_request("X")) is not None
        assert cache.get(_make_request("Y")) is None
        assert cache.get(_make_request("Z")) is not None


# ── SQLite persistence ───────────────────────────────────────────


class TestCachePolicySQLite:
    """Tests for SQLite-backed cache."""

    def test_response_persists_across_instances(self, tmp_path: Path):
        """A response stored by one instance is retrievable by another instance."""
        db_path = tmp_path / "cache.db"

        # Write
        cache1 = CachePolicy(enabled=True, max_size=5, db_path=db_path)
        req = _make_request("持久化测试")
        cache1.put(req, _make_response("持久化的内容"))

        # Read with fresh instance (simulates restart)
        cache2 = CachePolicy(enabled=True, max_size=5, db_path=db_path)
        retrieved = cache2.get(req)
        assert retrieved is not None

    def test_clear_removes_memory_and_db_entries(self, tmp_path: Path):
        """clear() wipes both in-memory store and SQLite table."""
        db_path = tmp_path / "cache.db"
        cache = CachePolicy(enabled=True, max_size=5, db_path=db_path)

        for i in range(3):
            cache.put(_make_request(f"清理{i}"), _make_response(f"数据{i}"))

        cache.clear()

        assert cache.size == 0
        stats = cache.stats()
        assert stats["memory_size"] == 0
        assert stats["db_size"] == 0

    def test_db_eviction_when_over_max_size(self, tmp_path: Path):
        """SQLite store evicts oldest entries when exceeding max_size * 2."""
        max_size = 3
        db_path = tmp_path / "cache.db"
        cache = CachePolicy(enabled=True, max_size=max_size, db_path=db_path)

        # Fill well beyond threshold
        for i in range(max_size * 3):
            cache.put(_make_request(f"淘汰{i}"), _make_response(f"内容{i}"))

        # Only the most recent max_size entries should remain
        stats = cache.stats()
        assert stats["db_size"] <= max_size * 2


# ── Thread safety ────────────────────────────────────────────────


class TestCachePolicyThreadSafety:
    """Basic concurrency safety tests."""

    def test_concurrent_put_get_does_not_crash(self):
        """Simultaneous puts and gets from multiple threads do not raise."""
        cache = CachePolicy(enabled=True, max_size=50)
        errors: list[BaseException] = []

        def writer(start: int) -> None:
            try:
                for i in range(20):
                    req = _make_request(f"线程{start}-{i}")
                    cache.put(req, _make_response(f"来自线程{start}"))
            except BaseException as e:
                errors.append(e)

        def reader() -> None:
            try:
                for _ in range(100):
                    cache.get(_make_request("随机请求"))
            except BaseException as e:
                errors.append(e)

        threads = [threading.Thread(target=writer, args=(i,)) for i in range(3)] + [
            threading.Thread(target=reader) for _ in range(2)
        ]

        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5.0)

        assert not errors, f"Thread errors: {errors}"


# ── Hash field coverage ──────────────────────────────────────────


class TestCachePolicyHashFields:
    """Verify that all critical route/request fields participate in the hash."""

    def _req(self, **kwargs) -> ModelRequest:
        from novel_forge.core.constants import TaskType

        defaults = dict(
            task_type=TaskType.EVALUATE,
            messages=[{"role": "user", "content": "test"}],
            provider_id="openai",
            model_id="gpt-4o",
            max_tokens=256,
            temperature=0.3,
            top_p=1.0,
            thinking=False,
            multi_turn=False,
            response_json_schema=None,
        )
        defaults.update(kwargs)
        return ModelRequest(**defaults)

    def test_task_type_affects_hash(self):
        from novel_forge.core.constants import TaskType

        h1 = CachePolicy._hash(self._req(task_type=TaskType.EVALUATE))
        h2 = CachePolicy._hash(self._req(task_type=TaskType.DRAFT_CHAPTER))
        assert h1 != h2

    def test_top_p_affects_hash(self):
        h1 = CachePolicy._hash(self._req(top_p=0.5))
        h2 = CachePolicy._hash(self._req(top_p=1.0))
        assert h1 != h2

    def test_thinking_affects_hash(self):
        h1 = CachePolicy._hash(self._req(thinking=False))
        h2 = CachePolicy._hash(self._req(thinking=True))
        assert h1 != h2

    def test_multi_turn_affects_hash(self):
        h1 = CachePolicy._hash(self._req(multi_turn=False))
        h2 = CachePolicy._hash(self._req(multi_turn=True))
        assert h1 != h2

    def test_response_json_schema_affects_hash(self):
        h1 = CachePolicy._hash(self._req(response_json_schema=None))
        h2 = CachePolicy._hash(self._req(response_json_schema={"type": "object"}))
        assert h1 != h2

    def test_model_id_affects_hash(self):
        h1 = CachePolicy._hash(self._req(model_id="gpt-4o"))
        h2 = CachePolicy._hash(self._req(model_id="deepseek-chat"))
        assert h1 != h2

    def test_provider_id_affects_hash(self):
        h1 = CachePolicy._hash(self._req(provider_id="openai"))
        h2 = CachePolicy._hash(self._req(provider_id="deepseek"))
        assert h1 != h2

    def test_same_fields_same_hash(self):
        h1 = CachePolicy._hash(self._req())
        h2 = CachePolicy._hash(self._req())
        assert h1 == h2
