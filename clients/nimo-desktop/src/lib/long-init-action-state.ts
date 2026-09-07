import type { WorkflowRunView } from "@nimo/engine-contracts";

import { workflowRunState, type WorkflowRunState } from "./workflow-run-state";

/**
 * Pure derivation of the long-init form's dynamic action state.
 *
 * Extracted from `LongInitFormPanel` so the resume/failure/paused semantics
 * (including the cancelled/interrupted -> failed normalization) are testable
 * without rendering the full form.
 */
export interface LongInitActionStateInput {
  readonly activeRun: WorkflowRunView | null;
  readonly initRunning: boolean;
  readonly resumable: boolean;
}

export interface LongInitActionState {
  readonly activeRunState: WorkflowRunState | null;
  readonly isFailed: boolean;
  readonly isPaused: boolean;
  readonly canResume: boolean;
  readonly showResumePanel: boolean;
  readonly showFailedPanel: boolean;
  readonly submitLabel: string;
  readonly copilotLabel: string;
  readonly autorunLabel: string;
}

/**
 * A continuation is only meaningful after the previous initialization has
 * stopped. Keeping it out of live task controls prevents a second intent
 * from being mistaken for an action on the run already in progress.
 */
export function shouldShowLongInitResumeAction(resumable: boolean, initRunning: boolean): boolean {
  return resumable && !initRunning;
}

export function longInitActionState({
  activeRun,
  initRunning,
  resumable,
}: LongInitActionStateInput): LongInitActionState {
  const activeRunState = activeRun === null ? null : workflowRunState(activeRun);
  const isFailed = activeRunState === "failed";
  const isPaused = activeRunState === "paused";
  const canResume = resumable || isFailed || isPaused;
  const showResumePanel =
    resumable || (activeRun !== null && (activeRunState === "running" || activeRunState === "queued"));
  const showFailedPanel = isFailed;

  return {
    activeRunState,
    isFailed,
    isPaused,
    canResume,
    showResumePanel,
    showFailedPanel,
    submitLabel: initRunning ? "立项进行中…" : canResume ? "继续立项 →" : "创建长篇项目 →",
    copilotLabel: initRunning ? "伴随立项中…" : canResume ? "伴随继续 →" : "AI 伴随立项 →",
    autorunLabel: initRunning ? "继续并连跑…" : canResume ? "继续并连跑 →" : "创建并连跑 →",
  };
}
