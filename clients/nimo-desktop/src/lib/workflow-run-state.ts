import type { WorkflowRunView } from "@nimo/engine-contracts";

export type WorkflowRunState = "queued" | "running" | "paused" | "succeeded" | "failed" | "unknown";

const STATE_BY_LABEL: Readonly<Record<string, WorkflowRunState>> = {
  queued: "queued",
  "排队中": "queued",
  running: "running",
  "执行中": "running",
  paused: "paused",
  "已暂停": "paused",
  succeeded: "succeeded",
  completed: "succeeded",
  "已完成": "succeeded",
  "完成": "succeeded",
  failed: "failed",
  interrupted: "failed",
  cancelled: "failed",
  canceled: "failed",
  "失败": "failed",
  "已中断": "failed",
  "已取消": "failed",
};

/** Translate a display label at the UI boundary; never use it directly in logic. */
export function workflowRunState(run: WorkflowRunView): WorkflowRunState {
  if (run.isCancelled === true) return "failed";
  if (run.actualState !== undefined) {
    if (run.actualState === "cancelled" || run.actualState === "timeout") return "failed";
    return run.actualState;
  }
  return STATE_BY_LABEL[run.stateLabel.trim().toLocaleLowerCase()] ?? "unknown";
}

export function isActiveWorkflowRun(run: WorkflowRunView): boolean {
  const state = workflowRunState(run);
  return state === "queued" || state === "running";
}

export function isClearableWorkflowRun(run: WorkflowRunView): boolean {
  const state = workflowRunState(run);
  // A paused job is not executing.  The PySide6 task-flow lets authors
  // dismiss these suspended records (including stale decision checkpoints)
  // without touching queued or running work.
  return state === "succeeded" || state === "failed" || state === "paused";
}

export function isFailedWorkflowRun(run: WorkflowRunView): boolean {
  return workflowRunState(run) === "failed";
}
