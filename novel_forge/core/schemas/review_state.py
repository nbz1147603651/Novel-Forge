"""Review-progress checkpoint state for pipeline resume-on-failure.

Extracted from workspace/chapter_session_state.py so that pipeline can
import these types without a reverse dependency on workspace.
"""

from __future__ import annotations

import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from novel_forge.core.exceptions import StorageError, ValidationError
from novel_forge.persistence.models import ProjectLayout

_logger = logging.getLogger(__name__)


class ReviewProgressState(BaseModel):
    """Intermediate review progress persisted at pipeline milestones.

    When ``review_chapter_draft`` fails mid-execution, this state allows
    the pipeline to resume from the last completed stage rather than
    re-running the entire review from scratch.
    """

    completed_stage: Literal[
        "draft_done",  # DRAFT + WAVE generate handoff completed
        "quality_done",  # quality checks + continuity repair completed
        "causal_repair_done",  # causal repair completed; reading-power repair may follow
        "repair_done",  # all repair loops (incl. reading-power) completed; text refinement may follow
        "canon_done",  # extract_canon completed successfully
        "refinement_done",  # all semantic text mutation completed; final verification may follow
        "final_verify_done",  # final text hash verified; only idempotent commit may remain
    ]
    current_text: str
    performed_edits: int = 0
    total_repair_rounds_used: int = 0
    alignment_report: dict[str, Any] | None = None
    continuity_report: dict[str, Any] | None = None
    chapter_repair_report: dict[str, Any] | None = None
    causal_report: dict[str, Any] | None = None
    repair_plan: dict[str, Any] | None = None
    reading_power_report: dict[str, Any] | None = None
    eval_report: dict[str, Any] | None = None
    warnings: list[str] = Field(default_factory=list)
    pipeline_version: str = ""
    source_text_hash: str = ""
    refinement_text_hash: str = ""
    final_verify_text_hash: str = ""
    # ── Budget pause state (Phase 6) ──────────────────────────────────
    # run_status / pause_reason are NOT completed stages — they describe
    # *why* the pipeline paused so resume can continue from completed_stage.
    run_status: Literal["running", "paused", "failed"] = "running"
    pause_reason: Literal["budget_blocked", "user_decision_required"] | None = None
    blocked_task_type: str = ""
    estimated_required_cost_usd: float | None = None


def save_review_progress(
    *,
    storage: Any,
    layout: ProjectLayout,
    chapter_number: int,
    progress: ReviewProgressState,
) -> None:
    """Persist intermediate review progress for resume-from-failure.

    Uses ``model_dump_json()`` (Rust-layer serialization) and skips
    directory fsync via ``save_text_fast`` for lower write latency.
    Checkpoint files are best-effort recovery aids, not critical data.
    """
    save_fn = getattr(storage, "save_text_fast", None)
    if callable(save_fn):
        save_fn(
            layout.chapter_review_progress_path(chapter_number),
            progress.model_dump_json(indent=2),
        )
    else:
        # Fallback for storage backends without save_text_fast.
        storage.save_json(
            layout.chapter_review_progress_path(chapter_number),
            progress.model_dump(mode="json"),
        )


def load_review_progress(
    storage: Any,
    layout: ProjectLayout,
    chapter_number: int,
    *,
    artifact_loader: Any | None = None,
) -> ReviewProgressState | None:
    """Load previously saved review progress, or None if absent/corrupt."""
    path = layout.chapter_review_progress_path(chapter_number)
    if not storage.exists(path):
        return None
    try:
        data = (
            artifact_loader.load_json(path)
            if artifact_loader is not None
            else storage.load_json(path)
        )
        return ReviewProgressState.model_validate(data)
    except (OSError, StorageError, ValidationError) as exc:
        _logger.warning(
            "Failed to load review progress for chapter %d: %s",
            chapter_number,
            exc,
        )
        _quarantine_review_progress(layout, chapter_number, path)
        return None


def _quarantine_review_progress(
    layout: ProjectLayout,
    chapter_number: int,
    path: Path,
) -> None:
    """Move corrupt review-progress checkpoints aside for later inspection."""
    try:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        dest = (
            layout.states_dir
            / "review_progress_rollbacks"
            / f"chapter_{chapter_number:03d}_{stamp}.json"
        )
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(path), str(dest))
    except FileNotFoundError:
        return
    except (OSError, shutil.Error) as exc:
        _logger.warning(
            "Failed to quarantine review progress for chapter %d: %s",
            chapter_number,
            exc,
        )
        return


def clear_review_progress(
    storage: Any,
    layout: ProjectLayout,
    chapter_number: int,
) -> None:
    """Remove review progress file (e.g. after successful completion)."""
    path = layout.chapter_review_progress_path(chapter_number)
    if path.exists():
        path.unlink()
