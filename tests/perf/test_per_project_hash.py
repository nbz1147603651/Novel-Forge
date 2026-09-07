"""Per-project_id details hash — Task 19.

Verifies that ``_compute_section_hashes`` splits the ``details`` section
into per-project hashes (``details/<project_id>``), so that modifying one
project's ``ProjectIndex`` only invalidates that project's section hash.
"""

from __future__ import annotations

import dataclasses
from typing import Any

from novel_forge.desktop.window import NovelForgeDesktopWindow
from tests.perf.conftest import build_synthetic_snapshot

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _compute(
    snapshot: Any,
    *,
    prev_payload: dict[str, Any] | None = None,
    prev_cache: dict[str, str] | None = None,
) -> tuple[dict[str, str], set[str], str]:
    """Thin wrapper around the static ``_compute_section_hashes``."""
    payload = NovelForgeDesktopWindow._build_snapshot_payload_static(snapshot)
    return NovelForgeDesktopWindow._compute_section_hashes(
        snapshot, payload, prev_payload, prev_cache
    )


def _mutate_index(snapshot: Any, project_id: str, **kwargs: Any) -> Any:
    """Return a copy of *snapshot* with one project's ``index`` mutated."""
    detail = snapshot.details[project_id]
    # ProjectIndex is Pydantic — use model_copy
    new_index = detail.index.model_copy(update=kwargs)
    # ProjectDetail is Pydantic — use model_copy
    new_detail = detail.model_copy(update={"index": new_index})
    new_details = dict(snapshot.details)
    new_details[project_id] = new_detail
    # DesktopWorkspaceSnapshot is a frozen dataclass — use replace
    return dataclasses.replace(snapshot, details=new_details)


# ---------------------------------------------------------------------------
# Tests — per-project hash isolation
# ---------------------------------------------------------------------------


class TestPerProjectHashIsolation:
    """Modifying one project's index only invalidates its own section hash."""

    def test_initial_hashes_contain_per_project_keys(self, tmp_path: Any) -> None:
        """First call produces ``details/<pid>`` keys for every project."""
        snap = build_synthetic_snapshot(num_projects=5, tmp_path=tmp_path)
        section_hashes, changed_sections, _ = _compute(snap)

        pids = sorted(snap.details.keys())
        for pid in pids:
            key = f"details/{pid}"
            assert key in section_hashes, f"missing per-project key {key}"

        # The old monolithic "details" key should NOT be present
        assert "details" not in section_hashes

    def test_only_modified_project_hash_differs(self, tmp_path: Any) -> None:
        """Modify project_001's index → only ``details/project_001`` changes."""
        snap = build_synthetic_snapshot(num_projects=5, tmp_path=tmp_path)
        initial_hashes, _, _ = _compute(snap)

        modified_pid = "project_001"
        snap2 = _mutate_index(snap, modified_pid, completed_chapters=99)

        prev_payload = NovelForgeDesktopWindow._build_snapshot_payload_static(snap)
        new_hashes, changed_sections, _ = _compute(
            snap2, prev_payload=prev_payload, prev_cache=initial_hashes
        )

        # The modified project's hash MUST differ
        key = f"details/{modified_pid}"
        assert new_hashes[key] != initial_hashes[key], (
            f"{key} hash should have changed"
        )

        # All OTHER details/<pid> hashes must be stable
        for pid in sorted(snap.details.keys()):
            if pid == modified_pid:
                continue
            other_key = f"details/{pid}"
            assert new_hashes[other_key] == initial_hashes[other_key], (
                f"{other_key} hash should be stable"
            )

        # changed_sections must contain the modified key
        assert key in changed_sections

        # changed_sections must NOT contain other details/<pid> keys
        for pid in sorted(snap.details.keys()):
            if pid == modified_pid:
                continue
            assert f"details/{pid}" not in changed_sections

    def test_non_details_sections_stable(self, tmp_path: Any) -> None:
        """Changing a project index must not affect metrics/projects/etc."""
        snap = build_synthetic_snapshot(num_projects=5, tmp_path=tmp_path)
        initial_hashes, _, _ = _compute(snap)

        snap2 = _mutate_index(snap, "project_002", total_words=12345)
        prev_payload = NovelForgeDesktopWindow._build_snapshot_payload_static(snap)
        new_hashes, _, _ = _compute(
            snap2, prev_payload=prev_payload, prev_cache=initial_hashes
        )

        for section in (
            "storage_root",
            "default_provider",
            "overview",
            "metrics",
            "providers",
            "projects",
            "featured_project",
        ):
            assert new_hashes[section] == initial_hashes[section], (
                f"{section} hash should be stable"
            )

    def test_combined_hash_changes_on_single_project(self, tmp_path: Any) -> None:
        """The combined final hash must change when one project changes."""
        snap = build_synthetic_snapshot(num_projects=5, tmp_path=tmp_path)
        _, _, hash1 = _compute(snap)

        snap2 = _mutate_index(snap, "project_003", chapter_count=42)
        prev_payload = NovelForgeDesktopWindow._build_snapshot_payload_static(snap)
        _, _, hash2 = _compute(
            snap2, prev_payload=prev_payload, prev_cache={}
        )

        assert hash1 != hash2


class TestPagesNeedingBindPrefixMatch:
    """``_pages_needing_bind`` must match ``details/<pid>`` against ``details``."""

    def test_details_prefix_triggers_details_pages(self, tmp_path: Any) -> None:
        """A ``details/<pid>`` change binds pages subscribed to ``details``."""
        from types import SimpleNamespace

        win = SimpleNamespace()
        win._pages = {
            "dashboard": object(),
            "projects": object(),
            "chapter_studio": object(),
            "workflow": object(),
            "settings": object(),
        }
        win._PAGE_SECTION_MAP = NovelForgeDesktopWindow._PAGE_SECTION_MAP
        win._changed_sections_overlap = NovelForgeDesktopWindow._changed_sections_overlap

        changed = {"details/project_001"}
        result = NovelForgeDesktopWindow._pages_needing_bind(
            win, changed, "dashboard", initial=False
        )

        assert "projects" in result
        assert "chapter_studio" in result

    def test_details_prefix_does_not_trigger_unrelated(self, tmp_path: Any) -> None:
        """A ``details/<pid>`` change does NOT bind settings (subscribes to providers)."""
        from types import SimpleNamespace

        win = SimpleNamespace()
        win._pages = {
            "dashboard": object(),
            "projects": object(),
            "settings": object(),
        }
        win._PAGE_SECTION_MAP = NovelForgeDesktopWindow._PAGE_SECTION_MAP
        win._changed_sections_overlap = NovelForgeDesktopWindow._changed_sections_overlap

        changed = {"details/project_001"}
        result = NovelForgeDesktopWindow._pages_needing_bind(
            win, changed, "dashboard", initial=False
        )

        assert "settings" not in result


class TestIncrementalCachePerProject:
    """Incremental mode caches per-project hashes independently."""

    def test_cached_projects_skip_rehash(self, tmp_path: Any) -> None:
        """Unchanged projects reuse their cached hash."""
        snap = build_synthetic_snapshot(num_projects=5, tmp_path=tmp_path)
        initial_hashes, _, _ = _compute(snap)

        # Second call with same snapshot — all should be cached
        payload = NovelForgeDesktopWindow._build_snapshot_payload_static(snap)
        new_hashes, changed_sections, _ = NovelForgeDesktopWindow._compute_section_hashes(
            snap, payload, prev_payload=payload, prev_section_hash_cache=initial_hashes
        )

        # No sections should be marked changed (same data)
        assert len(changed_sections) == 0

        # All per-project hashes should match
        for pid in sorted(snap.details.keys()):
            key = f"details/{pid}"
            assert new_hashes[key] == initial_hashes[key]
