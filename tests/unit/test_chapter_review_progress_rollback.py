"""Regression tests for corrupt chapter review-progress rollback."""

from __future__ import annotations

from novel_forge.core.schemas.review_state import ReviewProgressState
from novel_forge.persistence.filesystem import FileSystemStorage
from novel_forge.persistence.models import ProjectLayout
from novel_forge.workspace.sessions.chapter_session_state import load_review_progress


def test_corrupt_review_progress_is_quarantined(tmp_path) -> None:
    storage = FileSystemStorage(tmp_path)
    layout = ProjectLayout(storage.ensure_project_dir("review_progress_rollback"))
    layout.ensure_dirs()
    progress_path = layout.chapter_review_progress_path(3)
    storage.save_text(progress_path, "{not json")

    progress = load_review_progress(storage, layout, 3)

    assert progress is None
    assert not progress_path.exists()
    rollback_files = list((layout.states_dir / "review_progress_rollbacks").glob("*.json"))
    assert len(rollback_files) == 1
    assert rollback_files[0].read_text(encoding="utf-8") == "{not json"


def test_legacy_review_progress_defaults_finalization_hashes() -> None:
    progress = ReviewProgressState.model_validate(
        {
            "completed_stage": "canon_done",
            "current_text": "legacy text",
        }
    )

    assert progress.pipeline_version == ""
    assert progress.refinement_text_hash == ""
    assert progress.final_verify_text_hash == ""
    assert progress.eval_report is None
