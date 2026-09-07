from __future__ import annotations

import hashlib

import pytest

from novel_forge.persistence.models import ProjectLayout
from novel_forge.persistence.project_staleness import (
    InvalidationScope,
    RevisionScope,
    UpstreamArtifactKind,
    assert_upstream_revision_fingerprint_current,
    build_upstream_revision_fingerprint,
    invalidate_downstream_generated_artifacts,
    preview_upstream_artifact_impact,
    record_upstream_artifact_revision,
    resolve_manual_invalidation_range,
    scoped_stale_chapters,
)


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _write_chapters(tmp_storage, layout: ProjectLayout, count: int) -> None:
    tmp_storage.save_json(
        layout.canon_dir / "canon_current.json",
        {"project_id": layout.root.name, "current_chapter": count},
    )
    for chapter in range(1, count + 1):
        text = f"chapter {chapter}"
        tmp_storage.save_text(layout.chapter_path(chapter), text)
        tmp_storage.save_text(layout.states_dir / f"chapter_{chapter:03d}_snapshot.txt", text)


def test_resolve_manual_invalidation_range_next_and_none(tmp_storage) -> None:
    layout = ProjectLayout(tmp_storage.project_dir("manual_scope"))
    layout.ensure_dirs()
    _write_chapters(tmp_storage, layout, 4)

    assert resolve_manual_invalidation_range(
        scope=InvalidationScope.NONE,
        chapter_number=1,
        layout=layout,
    ) == (None, None, ())
    assert resolve_manual_invalidation_range(
        scope=InvalidationScope.NEXT,
        chapter_number=1,
        layout=layout,
    ) == (2, 2, (2,))


def test_scoped_stale_chapters_respects_manual_next_scope(tmp_storage) -> None:
    layout = ProjectLayout(tmp_storage.project_dir("manual_next"))
    layout.ensure_dirs()
    _write_chapters(tmp_storage, layout, 4)

    edited = "chapter 1 revised"
    tmp_storage.save_text(layout.chapter_path(1), edited)
    # Refresh the chain snapshot, proving the scoped marker controls the range
    # instead of the legacy mtime heuristic expanding it to all downstream chapters.
    tmp_storage.save_text(layout.states_dir / "chapter_001_snapshot.txt", edited)
    status_dir = layout.states_dir / "final_revision_status"
    status_dir.mkdir(parents=True, exist_ok=True)
    tmp_storage.save_json(
        status_dir / "chapter_001.json",
        {
            "schema_version": "1.0",
            "chapter_number": 1,
            "current_hash": _hash(edited),
            "requires_reevaluation": True,
            "requires_state_reextract": True,
            "scope": "next",
            "range_start": 2,
            "range_end": 2,
            "affected_chapters": [2],
        },
    )

    assert scoped_stale_chapters(tmp_storage, layout) == {2}


def test_scoped_stale_chapters_none_scope_does_not_mark_downstream(tmp_storage) -> None:
    layout = ProjectLayout(tmp_storage.project_dir("manual_none"))
    layout.ensure_dirs()
    _write_chapters(tmp_storage, layout, 3)

    edited = "chapter 1 revised"
    tmp_storage.save_text(layout.chapter_path(1), edited)
    tmp_storage.save_text(layout.states_dir / "chapter_001_snapshot.txt", edited)
    status_dir = layout.states_dir / "final_revision_status"
    status_dir.mkdir(parents=True, exist_ok=True)
    tmp_storage.save_json(
        status_dir / "chapter_001.json",
        {
            "schema_version": "1.0",
            "chapter_number": 1,
            "current_hash": _hash(edited),
            "requires_reevaluation": True,
            "requires_state_reextract": True,
            "scope": "none",
            "affected_chapters": [],
        },
    )

    assert scoped_stale_chapters(tmp_storage, layout) == set()


def test_invalidate_downstream_generated_artifacts_respects_max_chapter(tmp_storage) -> None:
    layout = ProjectLayout(tmp_storage.project_dir("manual_max"))
    layout.ensure_dirs()
    _write_chapters(tmp_storage, layout, 4)
    for chapter in (2, 3):
        tmp_storage.save_json(layout.chapter_checkpoint_path(chapter), {"chapter": chapter})
    tmp_storage.save_json(
        layout.element_progress_path,
        {
            "chapters": {
                "2": {"chapter_number": 2, "results": [{"element_id": "e2", "status": "hit"}]},
                "3": {"chapter_number": 3, "results": [{"element_id": "e3", "status": "miss"}]},
            },
            "totals": {},
            "pending_element_ids": [],
            "arbiter_totals": {},
        },
    )

    invalidated = invalidate_downstream_generated_artifacts(
        tmp_storage,
        layout,
        completed_chapter=1,
        delete_chapter_files=True,
        max_chapter=2,
    )

    assert invalidated == [2]
    assert not layout.chapter_path(2).exists()
    assert layout.chapter_path(3).exists()
    payload = tmp_storage.load_json(layout.element_progress_path)
    assert set(payload["chapters"]) == {"3"}


def test_upstream_revision_fingerprint_detects_manual_upstream_change(tmp_storage) -> None:
    layout = ProjectLayout(tmp_storage.project_dir("manual_fingerprint"))
    layout.ensure_dirs()
    _write_chapters(tmp_storage, layout, 2)

    fingerprint = build_upstream_revision_fingerprint(tmp_storage, layout, 2)
    tmp_storage.save_text(layout.chapter_path(1), "chapter 1 revised")

    with pytest.raises(RuntimeError):
        assert_upstream_revision_fingerprint_current(tmp_storage, layout, 2, fingerprint)


def test_upstream_revision_fingerprint_detects_core_source_and_slice_changes(
    tmp_storage,
) -> None:
    layout = ProjectLayout(tmp_storage.project_dir("upstream_fingerprint_11"))
    layout.ensure_dirs()
    _write_chapters(tmp_storage, layout, 2)
    tmp_storage.save_json(layout.outline_path, {"chapters": []})
    tmp_storage.save_json(layout.source_artifacts_dir / "outline.json", {"payload": "v1"})
    tmp_storage.save_json(layout.chapter_source_slice_path(2), {"payload": "slice-v1"})

    fingerprint = build_upstream_revision_fingerprint(tmp_storage, layout, 2)
    assert fingerprint["schema_version"] == "1.1"

    tmp_storage.save_json(layout.outline_path, {"chapters": [{"chapter_number": 1}]})
    with pytest.raises(RuntimeError):
        assert_upstream_revision_fingerprint_current(tmp_storage, layout, 2, fingerprint)

    tmp_storage.save_json(layout.outline_path, {"chapters": []})
    fingerprint = build_upstream_revision_fingerprint(tmp_storage, layout, 2)
    tmp_storage.save_json(layout.source_artifacts_dir / "outline.json", {"payload": "v2"})
    with pytest.raises(RuntimeError):
        assert_upstream_revision_fingerprint_current(tmp_storage, layout, 2, fingerprint)

    tmp_storage.save_json(layout.source_artifacts_dir / "outline.json", {"payload": "v1"})
    fingerprint = build_upstream_revision_fingerprint(tmp_storage, layout, 2)
    tmp_storage.save_json(layout.chapter_source_slice_path(2), {"payload": "slice-v2"})
    with pytest.raises(RuntimeError):
        assert_upstream_revision_fingerprint_current(tmp_storage, layout, 2, fingerprint)


def test_preview_upstream_artifact_impact_is_read_only(tmp_storage) -> None:
    layout = ProjectLayout(tmp_storage.project_dir("upstream_preview"))
    layout.ensure_dirs()
    _write_chapters(tmp_storage, layout, 3)
    tmp_storage.save_json(layout.chapter_checkpoint_path(2), {"chapter": 2})
    tmp_storage.save_json(layout.chapter_source_slice_path(2), {"chapter": 2})

    preview = preview_upstream_artifact_impact(
        tmp_storage,
        layout,
        artifact_kind=UpstreamArtifactKind.OUTLINE,
        scope=RevisionScope.WHOLE_BOOK,
    )

    assert preview["affected_chapters"] == [1, 2, 3]
    assert layout.chapter_checkpoint_path(2).exists()
    assert layout.chapter_source_slice_path(2).exists()


def test_scoped_stale_chapters_includes_upstream_artifact_revision(tmp_storage) -> None:
    layout = ProjectLayout(tmp_storage.project_dir("upstream_stale"))
    layout.ensure_dirs()
    _write_chapters(tmp_storage, layout, 3)
    tmp_storage.save_json(layout.outline_path, {"chapters": []})
    tmp_storage.save_json(layout.chapter_source_slice_path(2), {"chapter": 2})

    record = record_upstream_artifact_revision(
        tmp_storage,
        layout,
        artifact_kind=UpstreamArtifactKind.OUTLINE,
        scope=RevisionScope.WHOLE_BOOK,
        reason="test",
    )

    assert record.affected_chapters == (1, 2, 3)
    assert scoped_stale_chapters(tmp_storage, layout) == {1, 2, 3}
    assert not layout.chapter_source_slice_path(2).exists()
