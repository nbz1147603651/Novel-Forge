"""Regression tests for StableAudioModelManager._is_installed.

Verifies that interrupted Hugging Face downloads (broken symlinks, tiny
partial caches) are NOT reported as "installed".
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from novel_forge.tts.sound_generation.model_manager import StableAudioModelManager


@dataclass(frozen=True)
class _FakeDescriptor:
    """Minimal stand-in for SoundModelDescriptor — only the fields _is_installed reads."""

    estimated_download_bytes: int = 2_270_000_000  # ~2.1 GB


def _make_snapshots(cache_path: Path) -> Path:
    snapshots = cache_path / "snapshots" / "abc123"
    snapshots.mkdir(parents=True, exist_ok=True)
    return snapshots


# ---------------------------------------------------------------------------
# Cases where _is_installed must return False
# ---------------------------------------------------------------------------


class TestIsInstalledFalse:
    def test_no_snapshots_dir(self, tmp_path: Path) -> None:
        assert StableAudioModelManager._is_installed(tmp_path, _FakeDescriptor()) is False  # type: ignore[arg-type]

    def test_empty_snapshots_dir(self, tmp_path: Path) -> None:
        _make_snapshots(tmp_path)
        assert StableAudioModelManager._is_installed(tmp_path, _FakeDescriptor()) is False  # type: ignore[arg-type]

    def test_broken_symlink_only(self, tmp_path: Path) -> None:
        snapshots = _make_snapshots(tmp_path)
        target = tmp_path / "blobs" / "nonexistent_weight.bin"
        link = snapshots / "model.safetensors"
        link.symlink_to(target)
        assert not link.exists()  # symlink target missing
        assert StableAudioModelManager._is_installed(tmp_path, _FakeDescriptor()) is False  # type: ignore[arg-type]

    def test_tiny_cache_below_threshold(self, tmp_path: Path) -> None:
        """A cache with real files but far below 100 MB must be rejected."""
        snapshots = _make_snapshots(tmp_path)
        # Write a small config file (~1 KB)
        (snapshots / "config.json").write_text('{"model": "stub"}')
        # Also create a blobs dir with small files, matching real HF layout
        blobs = tmp_path / "blobs"
        blobs.mkdir()
        (blobs / "tiny_blob").write_bytes(b"\x00" * 512)

        desc = _FakeDescriptor(estimated_download_bytes=2_270_000_000)
        assert StableAudioModelManager._is_installed(tmp_path, desc) is False  # type: ignore[arg-type]

    def test_no_descriptor_still_checks_files(self, tmp_path: Path) -> None:
        """Without a descriptor, still requires at least one valid file."""
        _make_snapshots(tmp_path)
        assert StableAudioModelManager._is_installed(tmp_path) is False


# ---------------------------------------------------------------------------
# Cases where _is_installed must return True
# ---------------------------------------------------------------------------


class TestIsInstalledTrue:
    def test_valid_cache_above_threshold(self, tmp_path: Path) -> None:
        """A cache with real files above the minimum threshold is installed."""
        snapshots = _make_snapshots(tmp_path)
        # Write real bytes so stat().st_size reflects the full size on APFS
        # Threshold = max(100 MB, 2.27 GB // 20) = 113.5 MB; write 120 MB
        big_file = snapshots / "model.safetensors"
        chunk = b"\x00" * (1024 * 1024)  # 1 MiB
        target_bytes = 120_000_000
        with open(big_file, "wb") as fh:
            written = 0
            while written < target_bytes:
                fh.write(chunk)
                written += len(chunk)

        desc = _FakeDescriptor(estimated_download_bytes=2_270_000_000)
        assert StableAudioModelManager._is_installed(tmp_path, desc) is True  # type: ignore[arg-type]

    def test_no_descriptor_with_file(self, tmp_path: Path) -> None:
        """Without a descriptor, any valid file in snapshots is enough."""
        snapshots = _make_snapshots(tmp_path)
        (snapshots / "config.json").write_text('{"ok": true}')
        assert StableAudioModelManager._is_installed(tmp_path) is True

    def test_valid_symlink_to_real_blob(self, tmp_path: Path) -> None:
        """A symlink that resolves to a real file counts as valid."""
        snapshots = _make_snapshots(tmp_path)
        blobs = tmp_path / "blobs"
        blobs.mkdir()
        real_blob = blobs / "real_weight.bin"
        chunk = b"\x00" * (1024 * 1024)
        target_bytes = 120_000_000  # above the 113.5 MB threshold
        with open(real_blob, "wb") as fh:
            written = 0
            while written < target_bytes:
                fh.write(chunk)
                written += len(chunk)
        link = snapshots / "model.safetensors"
        link.symlink_to(real_blob)
        assert link.exists()  # symlink target exists

        desc = _FakeDescriptor(estimated_download_bytes=2_270_000_000)
        assert StableAudioModelManager._is_installed(tmp_path, desc) is True  # type: ignore[arg-type]
