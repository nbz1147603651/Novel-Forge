import type { WorkflowRunView } from "@nimo/engine-contracts";

/** Job kinds that support chapter checkpoint resume, mirroring `chapter_kinds` in `_build_action_bar`. */
export const CHAPTER_CHECKPOINT_KINDS: ReadonlySet<string> = new Set([
  "run_chapter",
  "prepare_chapter",
  "resolve_chapter_checkpoint",
  "resolve_chapter_checkpoint_finalize",
]);

/**
 * Return the stable project identity required by Engine commands and artifact
 * reads.  ``projectLabel`` is a display field and remains a compatibility
 * fallback for projections emitted by older Engines.
 */
export function workflowRunProjectId(run: WorkflowRunView): string {
  return run.projectId?.trim() || run.projectLabel.trim();
}

function replaceRun(runs: readonly WorkflowRunView[], runId: string, update: (run: WorkflowRunView) => WorkflowRunView): readonly WorkflowRunView[] {
  return runs.map((run) => run.id === runId ? update(run) : run);
}

/**
 * Apply the visible outcome of a user-confirmed stop.
 *
 * Mirrors PySide6: a stopped job is persisted as FAILED with a cancelled
 * marker, and `task_flow_status_spec` renders it as 「已取消」(tone muted,
 * is_terminal, not an error — no failure text is shown).  The optimistic
 * update deliberately leaves checkpoint fields untouched: the next Engine
 * refresh is the only authority for durable resume availability.
 */
export function cancelWorkflowRun(runs: readonly WorkflowRunView[], runId: string): readonly WorkflowRunView[] {
  return replaceRun(runs, runId, (run) => ({
    ...run,
    actualState: "cancelled",
    stateLabel: "已取消",
    validationLabel: "",
    activityLabel: "任务已取消",
    initRepairAvailable: false,
  }));
}

/**
 * Start the AI repair pass for a failed init_long run.
 *
 * Mirrors `init_retry_requested`: reuse verified on-disk artifacts and
 * resubmit the auto-repair + init re-adjudication flow
 * (「复用可验证的已落盘产物，重新提交自动修复与初始化复审。」).
 */
export function initRepairWorkflowRun(runs: readonly WorkflowRunView[], runId: string): readonly WorkflowRunView[] {
  return replaceRun(runs, runId, (run) => ({
    ...run,
    actualState: "running",
    stateLabel: "执行中",
    validationLabel: "复用已落盘产物，重新提交自动修复与初始化复审",
    activityLabel: "正在执行：AI 自动修复",
    initRepairAvailable: false,
  }));
}
