"""Pure decision helpers for chapter studio auto-run and continuity repair."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from novel_forge.common.constants import severity_at_least
from novel_forge.core.config import get_settings
from novel_forge.desktop.constants import LABEL_CHAPTER_RE
from novel_forge.desktop.jobs import DesktopJobRecord, DesktopJobState
from novel_forge.workspace.contracts import ChapterWorkspaceSnapshot, DecisionOption

AutoPilotAction = Literal[
    "none",
    "refresh_context",
    "resolve_checkpoint",
    "advance_chapter",
    "prepare_chapter",
    "stop",
    "finish",
]


@dataclass(frozen=True)
class AutoPilotDecision:
    """Next action for the chapter studio auto-pilot."""

    action: AutoPilotAction = "none"
    delay_ms: int = 0
    option: DecisionOption | None = None
    next_chapter: int | None = None


@dataclass(frozen=True)
class AutoPilotContext:
    """Snapshot of state fed into autopilot decision functions.

    Bundles all the inputs that ``decide_autopilot_action`` and
    ``should_auto_submit_repair`` need so callers pass a single object
    instead of 9+ keyword arguments.
    """

    mode: str
    auto_started: bool
    auto_pilot_pending: bool
    studio: ChapterWorkspaceSnapshot | None
    latest_job: DesktopJobRecord | None
    last_submitted_checkpoint_id: str | None
    current_chapter_done: bool
    book_auto_skip_done: bool
    chapter_prepared_this_run: bool = False
    auto_repair_pending: bool = False
    repair_attempts: int = 0
    max_repair_attempts: int = 2
    upstream_chapter_ready: bool = True
    current_project_id: str = ""
    user_navigated: bool = False
    """True when the current studio context was triggered by a user manual chapter
    navigation (spinbox change) rather than auto-pilot's own chapter advance.
    When set, ``decide_autopilot_action`` will not return permanent stop/finish
    decisions for the browsed chapter."""


@dataclass(frozen=True)
class ContinuityHintState:
    """UI-facing continuity repair hint content."""

    text: str
    warning: bool = False


def is_auto_mode(mode: str) -> bool:
    """Return True when chapter studio is in one of the auto-run modes."""
    return mode in {"auto", "book_auto"}


def auto_mode_label(mode: str) -> str:
    """Human-friendly label for the active auto-run mode."""
    return "章节连跑" if mode == "book_auto" else "本章自动"


def job_chapter_number(job: DesktopJobRecord | None) -> int:
    """Extract the chapter number from a job result payload."""
    if job is None:
        return 0
    try:
        raw = (job.result or {}).get("chapter_number", 0)
        value = int(raw or 0)
        if value > 0:
            return value
    except (TypeError, ValueError):
        pass
    match = LABEL_CHAPTER_RE.search(job.label or "")
    if match is None:
        return 0
    try:
        return max(int(match.group(1)), 0)
    except ValueError:
        return 0


def job_completed_current_chapter(job: DesktopJobRecord | None, *, chapter_number: int) -> bool:
    """Return True when *job* represents a completed write/archive for the chapter."""
    if job is None or job.status != DesktopJobState.SUCCEEDED:
        return False
    if job_chapter_number(job) != chapter_number:
        return False
    result_status = str((job.result or {}).get("status", "") or "").strip().lower()
    if result_status in {"completed", "done"}:
        return True
    if job.kind == "run_chapter":
        return True
    if job.kind == "resolve_chapter_checkpoint_finalize":
        label = str(job.label or "")
        return "归档暂停" not in label
    return False


def first_stale_chapter_number(studio: ChapterWorkspaceSnapshot | None) -> int | None:
    """Return the earliest stale chapter exposed by the workspace snapshot."""
    if studio is None:
        return None
    stale_numbers = [
        chapter.chapter_number
        for chapter in studio.chapters
        if chapter.status == "stale"
    ]
    return min(stale_numbers) if stale_numbers else None


def latest_job_for_current_chapter(
    job: DesktopJobRecord | None,
    *,
    current_chapter: int,
) -> DesktopJobRecord | None:
    """Ignore stale jobs that belong to another chapter after an auto-jump."""
    if job is None:
        return None
    job_chapter = job_chapter_number(job)
    if job_chapter not in {0, current_chapter}:
        return None
    return job


def default_checked_issue_indices(
    *,
    mode: str,
    issues: list[dict[str, Any]],
    previous_checked: set[int],
    orig_indices: list[int] | None = None,
) -> set[int]:
    """Return which continuity issues should be checked by default.

    All returned indices are in *original* (unfiltered) index space so they can be
    stored directly in checkbox properties and later passed into a v2 repair mission.
    ``orig_indices[filtered_pos]`` maps a filtered-list position to the original index;
    when ``orig_indices`` is None no filtering was applied and the two spaces are identical.
    """
    def _to_orig(filtered_pos: int) -> int:
        return orig_indices[filtered_pos] if orig_indices is not None else filtered_pos

    if is_auto_mode(mode):
        return {_to_orig(i) for i in range(len(issues))}
    if mode == "suggest":
        severity_defaults = {
            _to_orig(index)
            for index, issue in enumerate(issues)
            if (issue.get("severity") or "").lower() in {"hard", "high", "critical"}
        }
        # 保留用户之前手动勾选的索引（如果仍在可见的过滤集内），与严重级别默认值合并
        visible_orig = set(orig_indices) if orig_indices is not None else None
        valid_previous = {
            i for i in previous_checked
            if visible_orig is None or i in visible_orig
        }
        return severity_defaults | valid_previous
    return previous_checked


def build_continuity_hint(
    *,
    last_repair_job: DesktopJobRecord | None,
    attempts: int,
    max_attempts: int,
) -> ContinuityHintState:
    """Build the continuity hint shown above the repair button."""
    last_repair_not_applied = (
        last_repair_job is not None
        and last_repair_job.status == DesktopJobState.SUCCEEDED
        and not (last_repair_job.result or {}).get("applied", True)
    )
    over_limit = attempts >= max_attempts

    if last_repair_not_applied:
        assert last_repair_job is not None
        failure_reason = (
            (last_repair_job.result or {}).get("failure_reason")
            or "修复结果未通过字数守卫，原文已回滚。"
        )
        return ContinuityHintState(
            text=(
                f"⚠️ 上次自动修复未能应用：{failure_reason}\n"
                "请手动编辑章节文本或调整后再点击「修复选中」重试。"
            ),
            warning=True,
        )
    if over_limit:
        return ContinuityHintState(
            text=(
                f"⚠️ 已连续自动修复 {attempts} 次，但连贯性问题仍未完全解决，已停止自动重试。\n"
                "请手动检查章节文本，或点击「修复选中」再次尝试。"
            ),
            warning=True,
        )
    return ContinuityHintState(
        text="勾选问题后点击修复，仅修正文本，不归档章节，不影响已存在的后续章节。",
        warning=False,
    )


def should_auto_submit_repair(
    ctx: AutoPilotContext,
) -> bool:
    """Return True when the current issues (continuity and/or causal) should auto-submit for repair."""
    if (
        not is_auto_mode(ctx.mode)
        or not ctx.auto_started
        or ctx.auto_repair_pending
        or ctx.studio is None
        or ctx.current_chapter_done
    ):
        return False

    # If the chapter has a pending plan_checkpoint, the chapter text hasn't been written yet
    # (e.g. consistency_replan aborted the previous write, leaving stale reports on disk).
    # Running repair in this state would immediately fail with "no chapter text available".
    if (
        ctx.studio.pending_checkpoint is not None
        and getattr(ctx.studio.pending_checkpoint, "checkpoint_type", "") == "plan_checkpoint"
    ):
        return False

    if ctx.latest_job is None or ctx.latest_job.status in {DesktopJobState.RUNNING, DesktopJobState.QUEUED}:
        return False

    active_job_chapter = job_chapter_number(ctx.latest_job)
    if active_job_chapter != 0 and active_job_chapter != ctx.studio.chapter_number:
        return False
    if ctx.latest_job.status != DesktopJobState.SUCCEEDED:
        return False

    # Combine continuity + causal issues for the repair check.
    all_issues: list[dict[str, Any]] = list(ctx.studio.continuity_issues or []) + list(
        getattr(ctx.studio, "causal_issues", None) or []
    )

    last_repair_not_applied = (
        ctx.latest_job.kind in {"repair_continuity", "repair_issues"}
        and not (ctx.latest_job.result or {}).get("applied", True)
    )
    over_limit = ctx.repair_attempts >= ctx.max_repair_attempts
    all_low = bool(all_issues) and all(
        (issue.get("severity") or "medium").lower() == "low"
        for issue in all_issues
    )

    # must-fix bypass: critical/high issues continue even after over_limit
    if over_limit and not last_repair_not_applied and not all_low:
        _must_fix_sev = (getattr(get_settings(), "repair_must_fix_severity", "critical") or "critical").lower()
        if _must_fix_sev != "off" and any(
            severity_at_least((iss.get("severity") or "").lower(), _must_fix_sev)
            for iss in all_issues
        ):
            return True

    # No issues at all → nothing to repair
    if not all_issues:
        return False

    return not any([last_repair_not_applied, over_limit, all_low])


def _autopilot_viable_repair_option(checkpoint: Any) -> DecisionOption | None:
    """Return the apply-repairs option if metadata proves a viable auto path.

    Defensive fallback for the archive-gate retry checkpoint: even when the
    recommended flag says ``pause_for_human``, the persisted checkpoint metadata
    may record that an autonomous repair is still scheduled (budget remaining
    and either bound evidence or a refresh-first path).  In that case the
    auto-pilot should proceed with the repair instead of stopping, keeping
    book-auto runs self-healing when the recommended flag and the retry state
    momentarily disagree (e.g. a checkpoint persisted by an older build).
    """
    metadata = getattr(checkpoint, "metadata", None) or {}
    if not isinstance(metadata, dict):
        return None
    if not metadata.get("archive_quality_proceed_with_repair"):
        return None
    if metadata.get("archive_quality_auto_repair_exhausted"):
        return None
    for option in getattr(checkpoint, "options", None) or []:
        if isinstance(option, DecisionOption) and option.option_id == "apply_repairs_and_finalize":
            return option
    return None


def decide_autopilot_action(
    ctx: AutoPilotContext,
) -> AutoPilotDecision:
    """Decide the next auto-pilot action without touching Qt state."""
    # Guard: project_id consistency check - skip if studio's project differs from UI's current project
    if ctx.studio is not None and ctx.studio.project_id != ctx.current_project_id:
        return AutoPilotDecision()

    if (
        not is_auto_mode(ctx.mode)
        or not ctx.auto_started
        or ctx.auto_pilot_pending
        or ctx.studio is None
    ):
        return AutoPilotDecision()

    # When the user manually navigated to a different chapter during auto-run,
    # redirect back to the earliest unprocessed (stale) chapter instead of making
    # a permanent stop/finish decision for the browsed chapter.
    if ctx.user_navigated:
        if ctx.mode == "book_auto":
            stale = first_stale_chapter_number(ctx.studio)
            if stale is not None and stale != ctx.studio.chapter_number:
                return AutoPilotDecision(
                    action="advance_chapter",
                    next_chapter=stale,
                    delay_ms=300,
                )
        # No stale target to redirect to — skip this cycle and wait for
        # the next event-driven evaluation.
        return AutoPilotDecision()

    if ctx.latest_job is not None and ctx.latest_job.status in {
        DesktopJobState.RUNNING,
        DesktopJobState.QUEUED,
    }:
        return AutoPilotDecision()

    studio = ctx.studio
    current_chapter = studio.chapter_number
    next_chapter = current_chapter + 1
    current_job = latest_job_for_current_chapter(
        ctx.latest_job,
        current_chapter=current_chapter,
    )
    stale_target = first_stale_chapter_number(studio)
    chapter_completed_by_job = job_completed_current_chapter(
        current_job,
        chapter_number=current_chapter,
    )

    # Terminal chapter race: the archive/write job can finish and update canon before
    # the workspace snapshot marks the current rail item as "done".  At the end of
    # the outline there is no next chapter to wait for, so finish the auto-run
    # immediately instead of cycling into another prepare_chapter for the same chapter.
    if (
        chapter_completed_by_job
        and studio.total_chapters > 0
        and next_chapter > studio.total_chapters
        and (ctx.book_auto_skip_done or ctx.chapter_prepared_this_run or ctx.mode == "auto")
    ):
        return AutoPilotDecision(action="finish")

    # Only jump to a different stale chapter when the current chapter is truly idle:
    # no pending checkpoint visible in the snapshot AND no PAUSED job for this chapter.
    # A PAUSED job means the chapter is mid-execution waiting for a decision (e.g.
    # guard_checkpoint at 82%).  When the chapter status is "rewriting", first_stale_chapter_number
    # skips it (only looks at status=="stale"), so stale_target can point to chapter N+1
    # even while chapter N still has an unresolved checkpoint — causing the UI to jump
    # past the in-progress chapter and submit a prepare for the downstream one while the
    # canon is still rolled back, triggering "上游重写失效" RuntimeError.
    _current_chapter_has_pending_work = (
        studio.pending_checkpoint is not None
        or (current_job is not None and current_job.status == DesktopJobState.PAUSED)
    )
    if (
        ctx.mode == "book_auto"
        and ctx.book_auto_skip_done
        and stale_target is not None
        and stale_target != current_chapter
        and not _current_chapter_has_pending_work
        and ctx.upstream_chapter_ready
    ):
        return AutoPilotDecision(
            action="advance_chapter",
            next_chapter=stale_target,
            delay_ms=300,
        )

    if (
        current_job is not None
        and current_job.status == DesktopJobState.FAILED
        and current_job.current_step != "cancelled"
    ):
        return AutoPilotDecision(action="stop")

    pending_checkpoint = studio.pending_checkpoint

    if pending_checkpoint is not None:
        # Both "resolve_chapter_checkpoint" (write/edit flow) and
        # "resolve_chapter_checkpoint_finalize" (archive flow: accept/apply-repairs/
        # adjust-outline/pause) represent a completed resolve for this chapter.
        _resolve_kinds = {"resolve_chapter_checkpoint", "resolve_chapter_checkpoint_finalize"}
        if (
            pending_checkpoint.checkpoint_id == ctx.last_submitted_checkpoint_id
            and current_job is not None
            and current_job.status == DesktopJobState.SUCCEEDED
            and current_job.kind in _resolve_kinds
            and job_chapter_number(current_job) == current_chapter
        ):
            return AutoPilotDecision(action="refresh_context")

        # Extra guard: even if _last_submitted_checkpoint_id was cleared
        # (e.g. chapter advance then return), do not re-submit if the
        # latest job already succeeded for this chapter's resolve flow.
        if (
            ctx.last_submitted_checkpoint_id is None
            and current_job is not None
            and current_job.status == DesktopJobState.SUCCEEDED
            and current_job.kind in _resolve_kinds
            and job_chapter_number(current_job) == current_chapter
        ):
            return AutoPilotDecision(action="refresh_context")

        # Guard: checkpoint already submitted but jobs list is transiently
        # empty (bind_studio clears _jobs before _force_refresh_jobs restores
        # them).  Without this, current_job=None bypasses the guards above
        # and the auto-pilot re-submits the same checkpoint in a tight loop.
        if (
            pending_checkpoint.checkpoint_id == ctx.last_submitted_checkpoint_id
            and current_job is None
        ):
            return AutoPilotDecision(action="refresh_context")

        # Guard: a completed resolve job (PAUSED or SUCCEEDED) created a new
        # checkpoint, but the workspace snapshot still shows the old one.
        # needs_decision results are mapped to PAUSED by the job manager, so
        # the SUCCEEDED-only guards above miss this case.  Without this guard,
        # the autopilot re-submits with the stale checkpoint_id, causing a
        # ChapterSessionStaleError (checkpoint_id_drift) on the backend.
        if (
            ctx.last_submitted_checkpoint_id is not None
            and pending_checkpoint.checkpoint_id != ctx.last_submitted_checkpoint_id
            and current_job is not None
            and current_job.status in {DesktopJobState.PAUSED, DesktopJobState.SUCCEEDED}
            and current_job.kind in _resolve_kinds
            and job_chapter_number(current_job) == current_chapter
        ):
            return AutoPilotDecision(action="refresh_context")

        recommended = next(
            (option for option in pending_checkpoint.options if option.is_recommended),
            None,
        )
        if recommended is None and pending_checkpoint.options:
            recommended = pending_checkpoint.options[0]
        if recommended is not None:
            # pause_for_human normally means the system cannot proceed
            # autonomously (archive hard gate blocked with no viable repair
            # path).  Submitting it just returns the same checkpoint, creating
            # an infinite loop, so we stop auto-pilot for the user to inspect.
            # But if the checkpoint metadata records a still-viable autonomous
            # repair (budget remaining, evidence bound or refreshable), prefer
            # that path over stopping so book-auto stays self-healing.
            if recommended.option_id == "pause_for_human":
                viable_repair = _autopilot_viable_repair_option(pending_checkpoint)
                if viable_repair is not None:
                    return AutoPilotDecision(
                        action="resolve_checkpoint",
                        delay_ms=600,
                        option=viable_repair,
                    )
                return AutoPilotDecision(action="stop")
            return AutoPilotDecision(
                action="resolve_checkpoint",
                delay_ms=600,
                option=recommended,
            )
        return AutoPilotDecision(action="stop")

    if ctx.current_chapter_done:
        if ctx.mode == "auto":
            return AutoPilotDecision(action="finish")
        if ctx.mode == "book_auto" and ctx.book_auto_skip_done:
            if next_chapter <= studio.total_chapters:
                return AutoPilotDecision(
                    action="advance_chapter",
                    next_chapter=next_chapter,
                    delay_ms=800,
                )
            return AutoPilotDecision(action="finish")

    if chapter_completed_by_job and ctx.current_chapter_done:
        # In regen-all mode (book_auto_skip_done=False), only advance if this
        # auto-pilot run actually prepared the chapter.  A pre-existing SUCCEEDED
        # job from a previous run must not be mistaken for "just done, advance".
        if not ctx.book_auto_skip_done and not ctx.chapter_prepared_this_run:
            pass  # fall through to prepare_chapter below
        elif next_chapter <= studio.total_chapters:
            return AutoPilotDecision(
                action="advance_chapter",
                next_chapter=next_chapter,
                delay_ms=800,
            )
        else:
            return AutoPilotDecision(action="finish")

    # Guard: if the last succeeded job was for the current chapter BUT the snapshot
    # hasn't caught up yet (sub-second sync lag between job completion and disk/workspace
    # refresh).  Two cases require this protection:
    #   - resolve_chapter_checkpoint SUCCEEDED → chapter becomes "done", but snapshot
    #     still shows current_chapter_done=False.  Re-submitting prepare_chapter with
    #     force=False would hit preflight's "Chapter N has already been generated" guard.
    #   - prepare_chapter SUCCEEDED → a new pending_checkpoint was written to disk, but
    #     snapshot still shows pending_checkpoint=None.  Re-submitting prepare_chapter
    #     would overwrite the freshly-created plan before auto-pilot gets to resolve it
    #     (root cause of 章节连跑 stopping after the first chapter).
    # In both cases: defer to a context refresh so the snapshot can reflect the
    # completed state before the next decision.
    _chapter_completing_kinds = {
        "prepare_chapter",
        "resolve_chapter_checkpoint",
        "resolve_chapter_checkpoint_finalize",
    }
    if (
        not ctx.current_chapter_done  # snapshot is still stale
        and current_job is not None
        and current_job.status == DesktopJobState.SUCCEEDED
        and current_job.kind in _chapter_completing_kinds
        and job_chapter_number(current_job) == current_chapter
    ):
        return AutoPilotDecision(action="refresh_context")

    if current_job is None or current_job.status == DesktopJobState.SUCCEEDED:
        return AutoPilotDecision(action="prepare_chapter", delay_ms=600)

    # A FAILED job means the last task errored out — stop auto-pilot to prevent
    # an infinite resubmission loop.
    # Exception: user-cancelled jobs (current_step == "cancelled") should not block
    # resumption — treat them as "no job" so the user can continue after a manual stop.
    if current_job.status == DesktopJobState.FAILED:
        if current_job.current_step != "cancelled":
            return AutoPilotDecision(action="stop")
        # User-cancelled: proceed to prepare_chapter as if no prior job
        return AutoPilotDecision(action="prepare_chapter", delay_ms=600)

    # PAUSED job with no visible checkpoint means studio hasn't synced from disk yet
    # (e.g. prepare_chapter just created a plan checkpoint).  Trigger an immediate
    # context refresh instead of waiting for the 30-second polling timer.
    if (
        current_job is not None
        and current_job.status == DesktopJobState.PAUSED
        and studio.pending_checkpoint is None
    ):
        return AutoPilotDecision(action="refresh_context")

    return AutoPilotDecision()
