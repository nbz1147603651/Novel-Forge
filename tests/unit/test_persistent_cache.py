"""Tests for the persistent cache (SQLite backend)."""

from __future__ import annotations

from novel_forge.gateway.cache import CachePolicy
from novel_forge.gateway.types import ModelRequest, ModelResponse


def _make_request(content: str = "test") -> ModelRequest:
    from novel_forge.core.constants import TaskType

    return ModelRequest(
        task_type=TaskType.EVALUATE,
        messages=[{"role": "user", "content": content}],
        model_id="test-model",
        max_tokens=100,
        temperature=0.5,
    )


def _make_response(content: str = "response") -> ModelResponse:
    return ModelResponse(
        content=content,
        model_id="test-model",
        prompt_tokens=10,
        completion_tokens=20,
        cost_usd=0.001,
    )


class TestCachePolicyInMemory:
    """Test pure in-memory cache behavior."""

    def test_put_and_get(self):
        cache = CachePolicy(enabled=True)
        req = _make_request()
        resp = _make_response()
        cache.put(req, resp)
        assert cache.get(req) is not None
        assert cache.get(req).content == "response"

    def test_disabled_cache(self):
        cache = CachePolicy(enabled=False)
        req = _make_request()
        cache.put(req, _make_response())
        assert cache.get(req) is None

    def test_eviction(self):
        cache = CachePolicy(enabled=True, max_size=2)
        for i in range(3):
            cache.put(_make_request(f"msg_{i}"), _make_response(f"resp_{i}"))
        assert cache.size <= 2

    def test_stats(self):
        cache = CachePolicy(enabled=True)
        stats = cache.stats()
        assert "memory_size" in stats
        assert "hits" in stats
        assert "misses" in stats


class TestCachePolicySQLite:
    """Test SQLite-backed persistent cache."""

    def test_persist_and_reload(self, tmp_path):
        db = tmp_path / "cache.db"
        cache1 = CachePolicy(enabled=True, db_path=db)
        req = _make_request("persistent")
        cache1.put(req, _make_response("stored"))
        assert cache1.get(req).content == "stored"

        # Simulate process restart
        cache2 = CachePolicy(enabled=True, db_path=db)
        result = cache2.get(req)
        assert result is not None
        assert result.content == "stored"

    def test_clear_removes_db_entries(self, tmp_path):
        db = tmp_path / "cache.db"
        cache = CachePolicy(enabled=True, db_path=db)
        cache.put(_make_request(), _make_response())
        cache.clear()
        stats = cache.stats()
        assert stats["db_size"] == 0

    def test_db_stats(self, tmp_path):
        db = tmp_path / "cache.db"
        cache = CachePolicy(enabled=True, db_path=db)
        cache.put(_make_request(), _make_response())
        stats = cache.stats()
        assert stats["db_size"] >= 1
