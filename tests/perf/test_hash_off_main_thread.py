"""P0-7: Payload + hash computation must run in background thread, not main.

After Task 7, ``_WorkspaceRefreshRunnable.run()`` calls
``_build_snapshot_payload_static`` and ``_compute_section_hashes`` INSIDE
the background thread.  The main thread (``_on_workspace_refreshed``) only
receives pre-computed values.

These tests verify that the expensive payload/hash functions are NEVER
invoked on the main thread during refresh.
"""

from __future__ import annotations

import importlib
import threading
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

from PySide6.QtCore import Qt

from novel_forge.desktop.window import (
    NovelForgeDesktopWindow,
    _WorkspaceRefreshRunnable,
)
from tests.perf.conftest import build_synthetic_snapshot

# The runnable calls the module-level payload/hash helpers directly; access
# them through Any so mypy does not require explicit re-exports.
_runnables_mod: Any = importlib.import_module("novel_forge.desktop.window._runnables")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_service(snapshot: Any) -> MagicMock:
    """Return a mock ``DesktopWorkspaceService`` that yields *snapshot*."""
    svc = MagicMock()
    svc.build_snapshot.return_value = snapshot
    return svc


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestPayloadBuiltOffMainThread:
    """``_build_snapshot_payload_static`` must run off the main thread."""

    def test_runnable_invokes_static_payload_builder(self, tmp_path: Path) -> None:
        """_WorkspaceRefreshRunnable.run() calls the payload builder in-thread.

        v2: the runnable calls the module-level payload/hash functions directly
        (``workspace_snapshot_hash``), not the window static-method wrapper.
        """
        snapshot = build_synthetic_snapshot(tmp_path=tmp_path)
        service = _make_mock_service(snapshot)

        called_in_threads: list[int] = []
        original = _runnables_mod._snapshot_build_payload_static

        def _spy(snap: Any) -> dict[str, Any]:
            called_in_threads.append(threading.current_thread().ident)  # type: ignore[arg-type]
            return original(snap)

        runnable = _WorkspaceRefreshRunnable(
            service,
            reload_from_settings=False,
            mock_enabled=False,
        )

        # Run in a background thread (simulating QThreadPool)
        received: list[tuple[Any, ...]] = []
        runnable.signals.refresh_ready.connect(
            lambda *a: received.append(a),
            type=Qt.ConnectionType.DirectConnection,
        )

        _runnables_mod._snapshot_build_payload_static = _spy
        try:
            worker = threading.Thread(target=runnable.run)
            worker.start()
            worker.join(timeout=10)
        finally:
            _runnables_mod._snapshot_build_payload_static = original

        assert not worker.is_alive(), "runnable.run() timed out"
        assert len(called_in_threads) == 1, "expected exactly 1 call to payload builder"
        assert called_in_threads[0] != threading.main_thread().ident, (
            "payload builder ran on main thread!"
        )

    def test_50_refreshes_never_on_main_thread(self, tmp_path: Path) -> None:
        """50 background refreshes: payload builder NEVER on main thread."""
        snapshot = build_synthetic_snapshot(tmp_path=tmp_path)
        main_tid = threading.main_thread().ident

        payload_builder_threads: list[int] = []
        hash_threads: list[int] = []
        original_build = _runnables_mod._snapshot_build_payload_static
        original_hash = _runnables_mod._snapshot_compute_section_hashes

        def _spy_build(snap: Any) -> dict[str, Any]:
            payload_builder_threads.append(threading.current_thread().ident)  # type: ignore[arg-type]
            return original_build(snap)

        def _spy_hash(*args: Any, **kwargs: Any) -> Any:
            hash_threads.append(threading.current_thread().ident)  # type: ignore[arg-type]
            return original_hash(*args, **kwargs)

        _runnables_mod._snapshot_build_payload_static = _spy_build
        _runnables_mod._snapshot_compute_section_hashes = _spy_hash

        try:
            for _ in range(50):
                service = _make_mock_service(snapshot)
                runnable = _WorkspaceRefreshRunnable(
                    service,
                    reload_from_settings=False,
                    mock_enabled=False,
                )
                # Run directly — in real usage QThreadPool calls run() in a
                # worker thread.  Here we use a fresh thread each iteration.
                t = threading.Thread(target=runnable.run)
                t.start()
                t.join(timeout=10)
                assert not t.is_alive()
        finally:
            _runnables_mod._snapshot_build_payload_static = original_build
            _runnables_mod._snapshot_compute_section_hashes = original_hash

        assert len(payload_builder_threads) == 50
        assert len(hash_threads) == 50
        assert all(tid != main_tid for tid in payload_builder_threads), (
            "_build_snapshot_payload_static ran on main thread at least once!"
        )
        assert all(tid != main_tid for tid in hash_threads), (
            "_compute_section_hashes ran on main thread at least once!"
        )

    def test_refresh_ready_signal_carries_computed_values(self, tmp_path: Path) -> None:
        """refresh_ready signal delivers payload + hashes from background."""
        snapshot = build_synthetic_snapshot(tmp_path=tmp_path)
        service = _make_mock_service(snapshot)

        runnable = _WorkspaceRefreshRunnable(
            service,
            reload_from_settings=False,
            mock_enabled=False,
        )
        received: list[tuple[Any, ...]] = []
        runnable.signals.refresh_ready.connect(
            lambda *a: received.append(a),
            type=Qt.ConnectionType.DirectConnection,
        )

        t = threading.Thread(target=runnable.run)
        t.start()
        t.join(timeout=10)

        assert len(received) == 1
        args = received[0]
        assert len(args) == 6, f"expected 6 args, got {len(args)}: {args}"
        sig_snapshot, sig_service, payload, section_hashes, changed_sections, final_hash = args
        assert sig_snapshot is snapshot
        assert sig_service is service
        assert isinstance(payload, dict)
        assert isinstance(section_hashes, dict)
        assert isinstance(changed_sections, set)
        assert isinstance(final_hash, str)
        assert len(final_hash) == 64  # SHA-256 hex digest


class TestInstanceMethodNotCalledOnMainThread:
    """The instance method ``_build_snapshot_payload`` must NOT be called on
    the main thread during refresh (only the static version is used)."""

    def test_instance_method_not_called_during_run(self, tmp_path: Path) -> None:
        """_WorkspaceRefreshRunnable.run() does not call instance _build_snapshot_payload."""
        snapshot = build_synthetic_snapshot(tmp_path=tmp_path)
        service = _make_mock_service(snapshot)

        instance_calls: list[int] = []
        original = NovelForgeDesktopWindow._build_snapshot_payload

        def _spy(self: Any, snap: Any) -> dict[str, Any]:
            instance_calls.append(threading.current_thread().ident)  # type: ignore[arg-type]
            return original(self, snap)

        runnable = _WorkspaceRefreshRunnable(
            service,
            reload_from_settings=False,
            mock_enabled=False,
        )

        NovelForgeDesktopWindow._build_snapshot_payload = _spy  # type: ignore[assignment]
        try:
            t = threading.Thread(target=runnable.run)
            t.start()
            t.join(timeout=10)
        finally:
            NovelForgeDesktopWindow._build_snapshot_payload = original  # type: ignore[method-assign]

        main_tid = threading.main_thread().ident
        main_thread_calls = [tid for tid in instance_calls if tid == main_tid]
        assert len(main_thread_calls) == 0, (
            f"_build_snapshot_payload (instance) called {len(main_thread_calls)} "
            "time(s) on main thread during refresh"
        )
