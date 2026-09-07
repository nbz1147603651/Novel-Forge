"""Unit tests for ``_RefreshSignals`` — verify signal payload structure.

After Task 7, ``_RefreshSignals`` has a ``refresh_ready`` signal that carries
all pre-computed values from the background thread:
``(snapshot, service, payload, section_hashes, changed_sections, final_hash)``.

These tests assert the signal exists and delivers all 6 fields correctly.
"""

from __future__ import annotations

from typing import Any

from novel_forge.desktop.window import _RefreshSignals


class TestRefreshReadySignal:
    """``refresh_ready`` signal must exist and carry 6 fields."""

    def test_refresh_ready_signal_exists(self) -> None:
        """_RefreshSignals has a refresh_ready signal attribute."""
        signals = _RefreshSignals()
        assert hasattr(signals, "refresh_ready"), (
            "_RefreshSignals missing 'refresh_ready' signal"
        )

    def test_refresh_ready_emits_six_fields(self) -> None:
        """refresh_ready signal delivers exactly 6 arguments."""
        signals = _RefreshSignals()
        received: list[tuple[Any, ...]] = []
        signals.refresh_ready.connect(lambda *args: received.append(args))

        snap = object()
        svc = object()
        payload = {"storage_root": "/tmp", "metrics": {}}
        section_hashes = {"overview": "abc123", "metrics": "def456"}
        changed: set[str] = {"overview"}
        final_hash = "a" * 64

        signals.refresh_ready.emit(snap, svc, payload, section_hashes, changed, final_hash)

        assert len(received) == 1
        args = received[0]
        assert len(args) == 6
        assert args[0] is snap
        assert args[1] is svc
        assert args[2] is payload
        assert args[3] is section_hashes
        assert args[4] is changed
        assert args[5] == final_hash

    def test_finished_signal_still_exists(self) -> None:
        """Backward compat: ``finished`` signal still present (2 fields)."""
        signals = _RefreshSignals()
        assert hasattr(signals, "finished")
        received: list[tuple[Any, ...]] = []
        signals.finished.connect(lambda *args: received.append(args))
        signals.finished.emit("snap", "svc")
        assert len(received) == 1
        assert len(received[0]) == 2

    def test_failed_signal_still_exists(self) -> None:
        """Backward compat: ``failed`` signal still present."""
        signals = _RefreshSignals()
        assert hasattr(signals, "failed")
        received: list[str] = []
        signals.failed.connect(lambda msg: received.append(msg))
        signals.failed.emit("boom")
        assert received == ["boom"]


class TestComputeSectionHashesStatic:
    """``_compute_section_hashes`` must be a static method on the window."""

    def test_static_method_exists(self) -> None:
        """NovelForgeDesktopWindow has _compute_section_hashes static method."""
        from novel_forge.desktop.window import NovelForgeDesktopWindow

        assert hasattr(NovelForgeDesktopWindow, "_compute_section_hashes"), (
            "NovelForgeDesktopWindow missing '_compute_section_hashes'"
        )

    def test_build_snapshot_payload_static_exists(self) -> None:
        """NovelForgeDesktopWindow has _build_snapshot_payload_static static method."""
        from novel_forge.desktop.window import NovelForgeDesktopWindow

        assert hasattr(NovelForgeDesktopWindow, "_build_snapshot_payload_static"), (
            "NovelForgeDesktopWindow missing '_build_snapshot_payload_static'"
        )

    def test_compute_section_hashes_returns_tuple(self, tmp_path: Any) -> None:
        """_compute_section_hashes returns (section_hashes, changed_sections, final_hash)."""
        from pathlib import Path

        from novel_forge.desktop.window import NovelForgeDesktopWindow
        from tests.perf.conftest import build_synthetic_snapshot

        snapshot = build_synthetic_snapshot(tmp_path=Path(tmp_path))
        payload = NovelForgeDesktopWindow._build_snapshot_payload_static(snapshot)
        result = NovelForgeDesktopWindow._compute_section_hashes(snapshot, payload)

        assert isinstance(result, tuple)
        assert len(result) == 3
        section_hashes, changed_sections, final_hash = result
        assert isinstance(section_hashes, dict)
        assert isinstance(changed_sections, set)
        assert isinstance(final_hash, str)
        assert len(final_hash) == 64

    def test_compute_section_hashes_no_changes_when_same_payload(self, tmp_path: Any) -> None:
        """When prev_payload matches current, changed_sections is empty."""
        from pathlib import Path

        from novel_forge.desktop.window import NovelForgeDesktopWindow
        from tests.perf.conftest import build_synthetic_snapshot

        snapshot = build_synthetic_snapshot(tmp_path=Path(tmp_path))
        payload = NovelForgeDesktopWindow._build_snapshot_payload_static(snapshot)

        # First call — all sections "changed" (no previous)
        sh1, cs1, fh1 = NovelForgeDesktopWindow._compute_section_hashes(snapshot, payload)
        assert len(cs1) > 0, "first call should report all sections as changed"

        # Second call with same payload as prev — no changes
        sh2, cs2, fh2 = NovelForgeDesktopWindow._compute_section_hashes(
            snapshot, payload, prev_payload=payload, prev_section_hash_cache=sh1
        )
        assert len(cs2) == 0, f"expected no changed sections, got {cs2}"
        assert fh1 == fh2, "hash should be identical for same payload"
