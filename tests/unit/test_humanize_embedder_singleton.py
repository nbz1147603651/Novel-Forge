"""Tests that _HumanizeEmbedderAdapter uses one loop+thread for many embed calls
and that shutdown() releases them properly."""

from __future__ import annotations

import threading
from unittest.mock import MagicMock

from novel_forge.workspace.runtime import _HumanizeEmbedderAdapter


class _MockService:
    def __init__(self):
        self.call_count = 0
        self._lock = threading.Lock()

    async def generate_batch(self, texts):
        with self._lock:
            self.call_count += 1
        return [MagicMock(embedding=[0.1, 0.2, 0.3]) for _ in texts]


def test_embed_reuses_loop_and_thread():
    settings = MagicMock()
    adapter = _HumanizeEmbedderAdapter(settings)
    try:
        # Force the service to be the mock
        svc = _MockService()
        adapter._service = svc

        thread_id_before = adapter._thread.ident

        results = [adapter.embed(["hello"]) for _ in range(5)]
        assert all(len(r) == 1 for r in results)
        assert svc.call_count == 5

        thread_id_after = adapter._thread.ident
        assert thread_id_before == thread_id_after  # ONE thread for all 5 calls
    finally:
        adapter.shutdown()


def test_shutdown_releases_thread():
    settings = MagicMock()
    adapter = _HumanizeEmbedderAdapter(settings)

    thread = adapter._thread
    adapter.shutdown()
    # The thread should have been joined (or at least asked to stop)
    thread.join(timeout=2.0)
    assert not thread.is_alive()


def test_reload_resets_state():
    settings = MagicMock()
    adapter = _HumanizeEmbedderAdapter(settings)
    try:
        adapter._build_failed = True
        adapter._service = MagicMock()

        adapter.reload()
        assert adapter._service is None
        assert adapter._build_failed is False
        # reload() should also re-create the worker thread
        assert adapter._thread is not None
    finally:
        adapter.shutdown()
