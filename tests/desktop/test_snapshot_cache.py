"""Tests for JobSnapshotCache: LRU + physical signature + TTL backstop."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from novel_forge.desktop.state.snapshot_cache import (
    JobSnapshotCache,
    _SnapshotSignature,
)


@pytest.fixture
def model_calls_dir(tmp_path: Path) -> Path:
    d = tmp_path / "logs" / "run-x" / "model_calls"
    d.mkdir(parents=True)
    return d


def _write_call(d: Path, name: str, content: dict) -> Path:
    p = d / name
    p.write_text(json.dumps(content))
    return p


def test_signature_counts_and_mtime(model_calls_dir):
    _write_call(model_calls_dir, "001.json", {"a": 1})
    _write_call(model_calls_dir, "002.json", {"a": 2})

    sig = _SnapshotSignature.compute(model_calls_dir)
    assert sig.file_count == 2
    assert sig.total_size_bytes > 0
    assert sig.latest_mtime_ns > 0


def test_signature_invalidates_on_new_file(model_calls_dir):
    cache = JobSnapshotCache(ttl_s=600.0)  # long TTL; rely on signature

    # First load: 0 files
    snap1 = {"calls": []}
    got = cache.get_or_load("run-x", model_calls_dir, lambda: snap1)
    assert got is snap1

    # Now add a new file → signature changes → invalidate
    _write_call(model_calls_dir, "003.json", {"a": 3})
    snap2 = {"calls": ["003"]}
    got2 = cache.get_or_load("run-x", model_calls_dir, lambda: snap2)
    assert got2 is snap2  # not cached snap1; signature invalidated


def test_cache_holds_entry_when_signature_unchanged(model_calls_dir):
    cache = JobSnapshotCache(ttl_s=600.0)
    snap = {"calls": ["001"]}
    counter = {"n": 0}

    def loader():
        counter["n"] += 1
        return snap

    cache.get_or_load("run-x", model_calls_dir, loader)
    cache.get_or_load("run-x", model_calls_dir, loader)
    cache.get_or_load("run-x", model_calls_dir, loader)
    assert counter["n"] == 1  # cached after first call


def test_cache_invalidate_explicit(model_calls_dir):
    cache = JobSnapshotCache(ttl_s=600.0)
    snap = {"v": 1}
    cache.get_or_load("run-x", model_calls_dir, lambda: snap)
    cache.invalidate("run-x")

    snap2 = {"v": 2}
    counter = {"n": 0}

    def loader():
        counter["n"] += 1
        return snap2

    cache.get_or_load("run-x", model_calls_dir, loader)
    assert counter["n"] == 1


def test_cache_lru_eviction(model_calls_dir):
    cache = JobSnapshotCache(max_entries=2, ttl_s=600.0)
    for run_id in ("a", "b", "c"):
        # each get_or_load writes to signature (empty dir OK)
        cache.get_or_load(run_id, model_calls_dir, lambda run_id=run_id: {"id": run_id})
    # Only 'b' and 'c' should remain (LRU evict 'a')
    assert "a" not in cache._cache
    assert "b" in cache._cache
    assert "c" in cache._cache
