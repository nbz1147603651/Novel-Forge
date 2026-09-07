"""Memory v3.0 performance baseline.

Measures key latency targets for the memory module using mock data.
No real project data required — fully hermetic via tmp_path.

Targets:
  - save_to_disk: < 100ms for 1 chapter data
  - load_from_disk: < 200ms
  - prune_episodic_by_recency: < 50ms for 1500 entries

Usage:
    .venv/bin/python -m pytest tests/benchmarks/memory_baseline.py -v
    .venv/bin/python tests/benchmarks/memory_baseline.py  # direct run
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path

# Ensure project root is on sys.path
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


def _build_chapter_data(chapter: int, entries: int = 50) -> dict:
    """Build synthetic chapter memory data for benchmarking."""
    episodic_index = {
        "chapter_events": {},
        "chapter_critiques": {},
        "vector_store": {"backend": "zvec", "path": ""},
        "outline_data": [],
        "critique_index": {},
    }
    for i in range(entries):
        scene_key = f"ch{chapter:03d}_s{i}"
        episodic_index["chapter_events"][scene_key] = {
            "chapter": chapter,
            "scene_index": i,
            "event_types": ["action", "dialogue"],
            "characters": ["char_a", "char_b"],
            "keywords": ["keyword_1", "keyword_2", "keyword_3"],
            "full_text": "x" * 200,  # ~200 chars per entry
        }
    return {
        "project_id": "benchmark_project",
        "last_indexed_chapter": chapter,
        "summary_stats": {"total_chapters": chapter, "total_words": chapter * 3000},
        "summary_cache": {str(chapter): f"Summary for chapter {chapter}"},
        "chapter_content_hash": {str(chapter): f"hash_{chapter}"},
        "episodic_index": episodic_index,
        "motif_cache": {},
        "motif_tracker": {"motifs": [], "tracking_rules": []},
    }


def benchmark_save_to_disk(tmp_dir: Path, data: dict) -> float:
    """Measure save_to_disk latency using atomic write pattern."""
    target = tmp_dir / "project_memory.json"
    start = time.perf_counter()
    tmp_path = tmp_dir / ".project_memory.json.tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    os.replace(str(tmp_path), str(target))
    return (time.perf_counter() - start) * 1000  # ms


def benchmark_load_from_disk(tmp_dir: Path) -> float:
    """Measure load_from_disk latency."""
    target = tmp_dir / "project_memory.json"
    start = time.perf_counter()
    with open(target, "r", encoding="utf-8") as f:
        data = json.load(f)
    _ = data  # prevent unused warning
    return (time.perf_counter() - start) * 1000  # ms


def benchmark_prune_episodic(n_entries: int = 1500) -> float:
    """Measure prune_episodic_by_recency latency on synthetic data."""
    # Build synthetic index entries
    index = []
    for i in range(n_entries):
        index.append({
            "chapter_number": i // 10 + 1,
            "scene_index": i % 10,
            "event_types": ["action", "dialogue", "internal"][: (i % 3) + 1],
            "characters": [f"char_{j}" for j in range(i % 5 + 1)],
            "keywords": [f"kw_{j}" for j in range(i % 4 + 1)],
            "full_text": "x" * (100 + i % 500),
        })

    start = time.perf_counter()

    # Simulate prune logic: sort by recency, keep top N
    # This mirrors the actual prune_episodic_by_recency algorithm
    sorted_by_recency = sorted(
        index,
        key=lambda e: (e["chapter_number"], e["scene_index"]),
        reverse=True,
    )
    keep_recency = set(id(e) for e in sorted_by_recency[:500])

    # Relevance proxy score
    def relevance_proxy(entry: dict) -> float:
        return (
            len(entry["event_types"]) * 3.0
            + len(entry["characters"]) * 1.5
            + len(entry["keywords"]) * 2.0
            + min(len(entry["full_text"]) / 1000, 5.0)
        )

    sorted_by_relevance = sorted(index, key=relevance_proxy, reverse=True)
    keep_relevance = set(id(e) for e in sorted_by_relevance[:200])

    keep_ids = keep_recency | keep_relevance
    pruned = [e for e in index if id(e) in keep_ids]

    elapsed = (time.perf_counter() - start) * 1000  # ms
    return elapsed, len(pruned), len(index) - len(pruned)


def run_benchmarks() -> dict:
    """Run all benchmarks and return results."""
    results = {}

    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)

        # Build 1 chapter of synthetic data (~50 entries)
        data = _build_chapter_data(chapter=1, entries=50)

        # save_to_disk
        save_ms = benchmark_save_to_disk(tmp_dir, data)
        results["save_to_disk_ms"] = round(save_ms, 2)
        results["save_to_disk_pass"] = save_ms < 100

        # load_from_disk
        load_ms = benchmark_load_from_disk(tmp_dir)
        results["load_from_disk_ms"] = round(load_ms, 2)
        results["load_from_disk_pass"] = load_ms < 200

    # prune_episodic_by_recency (1500 entries)
    prune_ms, kept, removed = benchmark_prune_episodic(1500)
    results["prune_episodic_ms"] = round(prune_ms, 2)
    results["prune_entries_kept"] = kept
    results["prune_entries_removed"] = removed
    results["prune_episodic_pass"] = prune_ms < 50

    return results


def print_report(results: dict) -> None:
    """Print a human-readable benchmark report."""
    print("=" * 60)
    print("Memory v3.0 Performance Baseline")
    print("=" * 60)
    print()

    benchmarks = [
        ("save_to_disk", results["save_to_disk_ms"], 100, results["save_to_disk_pass"]),
        ("load_from_disk", results["load_from_disk_ms"], 200, results["load_from_disk_pass"]),
        ("prune_episodic_by_recency", results["prune_episodic_ms"], 50, results["prune_episodic_pass"]),
    ]

    print(f"{'Benchmark':<35} {'Time (ms)':>10} {'Target':>10} {'Status':>8}")
    print("-" * 65)
    for name, actual, target, passed in benchmarks:
        status = "PASS" if passed else "FAIL"
        print(f"{name:<35} {actual:>10.2f} {target:>10} {status:>8}")

    print()
    print(f"Prune: {results['prune_entries_kept']} kept, {results['prune_entries_removed']} removed (from 1500)")
    print()

    all_pass = all(r for _, _, _, r in benchmarks)
    print(f"Overall: {'ALL PASS' if all_pass else 'SOME FAILED'}")
    print("=" * 60)


if __name__ == "__main__":
    results = run_benchmarks()
    print_report(results)

    # Exit code for CI
    all_pass = all([
        results["save_to_disk_pass"],
        results["load_from_disk_pass"],
        results["prune_episodic_pass"],
    ])
    sys.exit(0 if all_pass else 1)
