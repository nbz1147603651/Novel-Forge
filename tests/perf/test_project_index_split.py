"""Tests for ProjectIndex / ProjectDetail split (Wave 3 / Task 18).

Verifies:
- ``ProjectIndex`` exists with the correct lightweight fields.
- ``ProjectDetail`` has an ``index`` field of type ``ProjectIndex``.
- ``ProjectInspector.get_project_index()`` computes counters without
  reading chapter files (no per-chapter I/O).
- ``_build_snapshot_payload_static`` serialises only ``ProjectIndex``
  fields for the ``details`` section (not full ``ProjectDetail``).
- Backward compatibility: existing ``ProjectDetail`` fields still work.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from novel_forge.desktop.window import NovelForgeDesktopWindow
from novel_forge.workspace.projects import (
    ChapterSummary,
    ProjectDetail,
    ProjectIndex,
    ProjectInspector,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_detail(
    project_id: str = "test_proj",
    *,
    chapters: int = 5,
    mode: str = "long",
) -> ProjectDetail:
    """Build a ``ProjectDetail`` with synthetic chapters."""
    chapter_list = [
        ChapterSummary(
            chapter_number=c,
            title=f"Chapter {c}",
            word_count=3000,
            overall_score=0.8,
            continuity_score=0.85,
            updated_at="2026-01-15T12:00:00Z",
            preview=f"Preview {c}",
        )
        for c in range(1, chapters + 1)
    ]
    idx = ProjectIndex(
        project_id=project_id,
        name=f"Test Project {project_id}",
        mode=mode,
        chapter_count=chapters,
        completed_chapters=chapters,
        total_words=chapters * 3000,
        updated_at="2026-01-15T12:00:00Z",
        is_degraded=False,
    )
    return ProjectDetail(
        project_id=project_id,
        mode=mode,
        title=f"Test Project {project_id}",
        genre="fantasy",
        tone="epic",
        premise="A test premise.",
        preview="A test preview.",
        total_chapters=chapters,
        completed_chapters=chapters,
        latest_chapter=chapters,
        completion_ratio=1.0,
        has_outline=True,
        has_canon=True,
        updated_at="2026-01-15T12:00:00Z",
        chapters=chapter_list,
        recent_files=[f"data/{project_id}/chapter_{c}.md" for c in range(1, 4)],
        artifact_counts={"chapters": chapters},
        index=idx,
    )


def _make_snapshot(
    *,
    num_projects: int = 3,
    chapters_per_project: int = 10,
) -> Any:
    """Build a minimal ``DesktopWorkspaceSnapshot`` with ProjectIndex."""
    from novel_forge.desktop.workspace import (
        DesktopProjectItem,
        DesktopWorkspaceMetrics,
        DesktopWorkspaceSnapshot,
        ProviderStatus,
    )
    from novel_forge.workspace.projects import WorkspaceOverview

    root = Path("/tmp/perf_test_index_split")
    details: dict[str, ProjectDetail] = {}
    projects: list[DesktopProjectItem] = []

    for i in range(num_projects):
        pid = f"proj_{i:03d}"
        detail = _make_detail(pid, chapters=chapters_per_project)
        details[pid] = detail
        projects.append(
            DesktopProjectItem(
                project_id=pid,
                title=f"Project {i}",
                mode="long",
                mode_label="长篇",
                status="writing",
                status_label="连载中",
                progress_label=f"{chapters_per_project}/20",
                progress_percent=50,
                last_updated_label="2026-01-15",
                headline="Test headline",
                next_action="继续推进",
                genre="fantasy",
                tone="epic",
                completed_chapters=chapters_per_project,
                total_chapters=20,
                next_chapter=chapters_per_project + 1,
                has_outline=True,
                has_canon=True,
            )
        )

    overview = WorkspaceOverview(
        storage_root=str(root),
        total_projects=num_projects,
        short_projects=0,
        long_projects=num_projects,
        total_generated_chapters=num_projects * chapters_per_project,
        providers=["mock"],
        default_provider="mock",
    )
    metrics = DesktopWorkspaceMetrics(
        total_projects=num_projects,
        total_chapters=num_projects * chapters_per_project,
        total_words=num_projects * chapters_per_project * 3000,
        configured_providers=1,
    )
    providers = [
        ProviderStatus("mock", "Mock", True, True, True, "Mock adapter"),
    ]
    return DesktopWorkspaceSnapshot(
        storage_root=root,
        default_provider="mock",
        overview=overview,
        metrics=metrics,
        providers=providers,
        projects=projects,
        featured_project=projects[0] if projects else None,
        details=details,
    )


# ---------------------------------------------------------------------------
# 1. ProjectIndex model shape
# ---------------------------------------------------------------------------


class TestProjectIndexModel:
    """ProjectIndex has the correct lightweight fields."""

    def test_project_index_exists(self) -> None:
        """ProjectIndex is importable from workspace.projects."""
        assert ProjectIndex is not None

    def test_project_index_fields(self) -> None:
        """ProjectIndex has exactly the expected fields."""
        idx = ProjectIndex(
            project_id="p1",
            name="Test",
            mode="long",
            chapter_count=5,
            completed_chapters=3,
            total_words=15000,
            updated_at="2026-01-15T12:00:00Z",
            is_degraded=False,
        )
        assert idx.project_id == "p1"
        assert idx.name == "Test"
        assert idx.mode == "long"
        assert idx.chapter_count == 5
        assert idx.completed_chapters == 3
        assert idx.total_words == 15000
        assert idx.updated_at == "2026-01-15T12:00:00Z"
        assert idx.is_degraded is False

    def test_project_index_is_pydantic_model(self) -> None:
        """ProjectIndex is a Pydantic BaseModel (serialisable)."""
        from pydantic import BaseModel

        assert issubclass(ProjectIndex, BaseModel)

    def test_project_index_serialises_to_small_dict(self) -> None:
        """ProjectIndex.model_dump() has only the lightweight fields."""
        idx = ProjectIndex(
            project_id="p1",
            name="Test",
            mode="long",
            chapter_count=5,
            completed_chapters=3,
            total_words=15000,
            updated_at=None,
            is_degraded=False,
        )
        data = idx.model_dump()
        # Must NOT contain heavy fields like chapters, recent_files, preview
        assert "chapters" not in data
        assert "recent_files" not in data
        # Must contain exactly the index fields
        expected_keys = {
            "project_id",
            "name",
            "mode",
            "chapter_count",
            "completed_chapters",
            "total_words",
            "updated_at",
            "is_degraded",
        }
        assert set(data.keys()) == expected_keys


# ---------------------------------------------------------------------------
# 2. ProjectDetail has an index field
# ---------------------------------------------------------------------------


class TestProjectDetailIndexField:
    """ProjectDetail includes a ProjectIndex as its ``index`` field."""

    def test_detail_has_index_field(self) -> None:
        """ProjectDetail has an ``index`` field of type ProjectIndex."""
        detail = _make_detail("p1")
        assert hasattr(detail, "index")
        assert isinstance(detail.index, ProjectIndex)

    def test_detail_index_matches_detail_fields(self) -> None:
        """Index fields are consistent with the parent detail."""
        detail = _make_detail("p1", chapters=7)
        assert detail.index.project_id == detail.project_id
        assert detail.index.mode == detail.mode
        assert detail.index.completed_chapters == detail.completed_chapters

    def test_detail_backward_compat_chapters(self) -> None:
        """Existing ``detail.chapters`` field still works."""
        detail = _make_detail("p1", chapters=5)
        assert len(detail.chapters) == 5
        assert detail.chapters[0].chapter_number == 1

    def test_detail_backward_compat_completed_chapters(self) -> None:
        """Existing ``detail.completed_chapters`` field still works."""
        detail = _make_detail("p1", chapters=5)
        assert detail.completed_chapters == 5

    def test_detail_default_index(self) -> None:
        """ProjectDetail without explicit index gets a default one."""
        detail = ProjectDetail(
            project_id="p2",
            mode="short",
            title="Short Story",
        )
        assert isinstance(detail.index, ProjectIndex)
        assert detail.index.project_id == "p2"


# ---------------------------------------------------------------------------
# 3. Snapshot payload uses ProjectIndex for details
# ---------------------------------------------------------------------------


class TestSnapshotPayloadUsesIndex:
    """``_build_snapshot_payload_static`` serialises only ProjectIndex."""

    def test_details_section_uses_index_not_full_detail(self) -> None:
        """The ``details`` section in the payload uses ProjectIndex fields."""
        snapshot = _make_snapshot(num_projects=2, chapters_per_project=5)
        payload = NovelForgeDesktopWindow._build_snapshot_payload_static(snapshot)

        details_payload = payload["details"]
        assert isinstance(details_payload, dict)
        assert len(details_payload) == 2

        for pid, detail_data in details_payload.items():
            # Must NOT contain heavy fields from full ProjectDetail
            assert "chapters" not in detail_data, (
                f"details[{pid}] should not contain 'chapters' — "
                "use ProjectIndex for snapshot payload"
            )
            assert "recent_files" not in detail_data, (
                f"details[{pid}] should not contain 'recent_files'"
            )
            # Must contain ProjectIndex fields
            assert "project_id" in detail_data
            assert "chapter_count" in detail_data
            assert "completed_chapters" in detail_data

    def test_payload_does_not_contain_chapter_previews(self) -> None:
        """No chapter preview text leaks into the payload details."""
        snapshot = _make_snapshot(num_projects=1, chapters_per_project=20)
        payload = NovelForgeDesktopWindow._build_snapshot_payload_static(snapshot)

        details_str = str(payload["details"])
        assert "Preview" not in details_str, (
            "Chapter preview text should not be in the payload — "
            "only ProjectIndex fields should be serialised"
        )


# ---------------------------------------------------------------------------
# 4. get_project_index() fast path
# ---------------------------------------------------------------------------


class TestGetProjectIndex:
    """``ProjectInspector.get_project_index()`` is a fast path."""

    def test_get_project_index_method_exists(self) -> None:
        """ProjectInspector has a ``get_project_index`` method."""
        assert hasattr(ProjectInspector, "get_project_index")

    def test_get_project_index_returns_project_index(
        self,
        tmp_path: Path,
    ) -> None:
        """get_project_index returns a ProjectIndex for a real project."""
        from novel_forge.persistence.filesystem import CachedFileSystemStorage

        project_dir = tmp_path / "test_novel"
        chapters_dir = project_dir / "chapters"
        chapters_dir.mkdir(parents=True)
        for c in range(1, 6):
            (chapters_dir / f"chapter_{c:03d}.md").write_text(
                f"第{c}章内容" * 100, encoding="utf-8"
            )
        (project_dir / "spec.json").write_text('{"title": "Test"}', encoding="utf-8")

        storage = CachedFileSystemStorage(tmp_path)
        inspector = ProjectInspector(storage)

        t0 = time.perf_counter()
        index = inspector.get_project_index("test_novel")
        elapsed_ms = (time.perf_counter() - t0) * 1000

        assert isinstance(index, ProjectIndex)
        assert index.project_id == "test_novel"
        assert index.chapter_count == 5
        assert index.completed_chapters == 5
        assert index.is_degraded is False
        # Fast path: should be well under 50ms
        assert elapsed_ms < 50, f"get_project_index took {elapsed_ms:.1f}ms"

    def test_get_project_index_does_not_read_chapter_files(
        self,
        tmp_path: Path,
    ) -> None:
        """get_project_index does NOT read chapter file contents."""
        from novel_forge.persistence.filesystem import CachedFileSystemStorage

        project_dir = tmp_path / "test_novel"
        chapters_dir = project_dir / "chapters"
        chapters_dir.mkdir(parents=True)
        for c in range(1, 4):
            (chapters_dir / f"chapter_{c:03d}.md").write_text(
                "x" * 5000, encoding="utf-8"
            )
        (project_dir / "spec.json").write_text('{"title": "Test"}', encoding="utf-8")

        storage = CachedFileSystemStorage(tmp_path)
        inspector = ProjectInspector(storage)

        # Track read_text calls on chapter files
        original_read_text = Path.read_text
        chapter_reads: list[Path] = []

        def _spy_read_text(self_path: Path, *args: Any, **kwargs: Any) -> str:
            if "chapter_" in self_path.name and self_path.suffix == ".md":
                chapter_reads.append(self_path)
            return original_read_text(self_path, *args, **kwargs)

        with patch.object(Path, "read_text", _spy_read_text):
            inspector.get_project_index("test_novel")

        assert len(chapter_reads) == 0, (
            f"get_project_index read {len(chapter_reads)} chapter files — "
            "it should use only directory-level operations"
        )

    def test_get_project_index_missing_project_raises(
        self,
        tmp_path: Path,
    ) -> None:
        """get_project_index raises KeyError for missing projects."""
        from novel_forge.persistence.filesystem import CachedFileSystemStorage

        storage = CachedFileSystemStorage(tmp_path)
        inspector = ProjectInspector(storage)

        with pytest.raises(KeyError):
            inspector.get_project_index("nonexistent_project")


# ---------------------------------------------------------------------------
# 5. Section hash uses ProjectIndex
# ---------------------------------------------------------------------------


class TestSectionHashUsesIndex:
    """``_compute_section_hashes`` uses ProjectIndex for details hash."""

    def test_details_hash_unchanged_when_only_chapters_change(self) -> None:
        """Changing chapter previews (but not index) keeps details hash same."""
        snap1 = _make_snapshot(num_projects=1, chapters_per_project=5)

        # Build snap2 with same index but different chapter previews
        snap2 = _make_snapshot(num_projects=1, chapters_per_project=5)
        for detail in snap2.details.values():
            # Mutate chapter previews (heavy data) but keep index the same
            for ch in detail.chapters:
                ch.preview = "MUTATED PREVIEW " * 100

        payload1 = NovelForgeDesktopWindow._build_snapshot_payload_static(snap1)
        payload2 = NovelForgeDesktopWindow._build_snapshot_payload_static(snap2)

        hashes1, _, _ = NovelForgeDesktopWindow._compute_section_hashes(
            snap1, payload1
        )
        hashes2, _, _ = NovelForgeDesktopWindow._compute_section_hashes(
            snap2, payload2
        )

        # Per-project details hash should be SAME because ProjectIndex didn't change
        for pid in snap1.details:
            key = f"details/{pid}"
            assert hashes1[key] == hashes2[key], (
                f"{key} hash changed when only chapter previews changed — "
                "hash should be based on ProjectIndex, not full ProjectDetail"
            )

    def test_details_hash_changes_when_index_changes(self) -> None:
        """Changing chapter_count in index DOES change the details hash."""
        snap1 = _make_snapshot(num_projects=1, chapters_per_project=5)
        snap2 = _make_snapshot(num_projects=1, chapters_per_project=10)

        payload1 = NovelForgeDesktopWindow._build_snapshot_payload_static(snap1)
        payload2 = NovelForgeDesktopWindow._build_snapshot_payload_static(snap2)

        hashes1, _, _ = NovelForgeDesktopWindow._compute_section_hashes(
            snap1, payload1
        )
        hashes2, _, _ = NovelForgeDesktopWindow._compute_section_hashes(
            snap2, payload2
        )

        for pid in snap1.details:
            key = f"details/{pid}"
            assert hashes1[key] != hashes2[key], (
                f"{key} hash should change when ProjectIndex changes"
            )
