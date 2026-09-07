"""Long-lived embedder adapter for humanize embedding calls.

Extracted from workspace/runtime.py so that pipeline can
import this class without a reverse dependency on workspace.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from typing import Any

from novel_forge.core.config import Settings

_logger = logging.getLogger(__name__)


class HumanizeEmbedderAdapter:
    """Long-lived embedder adapter: 1 loop + 1 thread for many embed() calls.

    Why: previous implementation created a new ThreadPoolExecutor + asyncio
    loop + thread per embed() call (1000 embeds → 1000 threads created and
    immediately torn down). This was expensive (startup/teardown cost) and
    noisy (lots of short-lived threads under load).

    embed() now submits to the singleton loop via call_soon_threadsafe and
    blocks on a concurrent.futures.Future. shutdown() must be called from
    RuntimeServices.shutdown() to release the thread + close the loop.

    The existing `_ensure_service()` logic is preserved (EmbeddingService +
    get_embedding_config_from_profiles + build_lock + build_failed flag).
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._service: Any = None
        self._build_lock = threading.Lock()
        self._build_failed = False
        # NEW: long-lived loop + thread
        self._loop: asyncio.AbstractEventLoop | None = asyncio.new_event_loop()
        self._thread: threading.Thread | None = threading.Thread(
            target=self._run_loop, daemon=True, name="humanize_embedder"
        )
        self._thread.start()

    def _run_loop(self) -> None:
        loop = self._loop
        if loop is None:
            return
        try:
            asyncio.set_event_loop(loop)
            loop.run_forever()
        except Exception as exc:
            _logger.debug("humanize_embedder_loop_crashed | error=%s", exc)
        finally:
            pass

    def _ensure_service(self) -> Any:
        # PRESERVED from existing implementation — keep build_lock + build_failed
        if self._service is not None or self._build_failed:
            return self._service
        with self._build_lock:
            if self._service is not None or self._build_failed:
                return self._service
            try:
                from novel_forge.gateway.embedding import EmbeddingService
                from novel_forge.memory.integration import (
                    get_embedding_config_from_profiles,
                )

                profile_id = getattr(self._settings, "memory_embedding_profile_id", None)
                config = get_embedding_config_from_profiles(profile_id)
                if not config:
                    self._build_failed = True
                    return None
                self._service = EmbeddingService.create_from_config(config)
            except Exception as exc:
                _logger.debug("humanize_embedder_build_failed | error=%s", exc)
                self._build_failed = True
        return self._service

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        service = self._ensure_service()
        if service is None:
            return []
        import concurrent.futures

        future: concurrent.futures.Future[list[list[float]]] = concurrent.futures.Future()

        async def _do() -> None:
            try:
                results = await service.generate_batch(texts)
                future.set_result(
                    [
                        list(getattr(r, "embedding", []) or [])
                        for r in (results or [])
                    ]
                )
            except Exception as exc:
                future.set_exception(exc)

        try:
            loop = self._loop
            if loop is None:
                return []
            loop.call_soon_threadsafe(
                lambda: asyncio.ensure_future(_do(), loop=loop)
            )
            return future.result(timeout=30)
        except Exception as exc:
            # Timeouts are common enough in production that DEBUG is too
            # quiet — log at WARNING so ops can grep for them. Everything
            # else stays at DEBUG.
            import concurrent.futures
            if isinstance(exc, concurrent.futures.TimeoutError):
                _logger.warning(
                    "humanize_embedder_embed_timeout | seconds=30 | len(texts)=%d",
                    len(texts),
                )
            else:
                _logger.debug(
                    "humanize_embedder_embed_failed | error=%s", exc,
                )
            return []

    def shutdown(self) -> None:
        """Release the worker thread and close the loop.

        Called from RuntimeServices.shutdown() so the daemon thread doesn't
        outlive the runtime.
        """
        thread = self._thread
        loop = self._loop
        if thread is None or loop is None:
            return
        try:
            loop.call_soon_threadsafe(loop.stop)
        except Exception:
            pass
        try:
            thread.join(timeout=3)
        except Exception:
            pass
        try:
            loop.close()
        except Exception:
            pass
        self._thread = None
        self._loop = None

    def reload(self) -> None:
        """Drop cached service + reset build flags; used by reload_runtime_dependencies."""
        try:
            self.shutdown()
        except Exception:
            pass
        self._service = None
        self._build_failed = False
        # Re-create loop + thread for next user
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(
            target=self._run_loop, daemon=True, name="humanize_embedder"
        )
        self._thread.start()


# Backward-compatible alias
_HumanizeEmbedderAdapter = HumanizeEmbedderAdapter
