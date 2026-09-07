import { describe, expect, it } from "vitest";

import type { WorkflowRunView } from "@nimo/engine-contracts";

import {
  isActiveWorkflowRun,
  isClearableWorkflowRun,
  isFailedWorkflowRun,
  workflowRunState,
} from "./workflow-run-state";

function run(stateLabel: string, isCancelled?: boolean): WorkflowRunView {
  return {
    id: "job-1",
    title: "测试任务",
    projectLabel: "demo",
    elapsedLabel: "00:00",
    progressPercent: 0,
    stateLabel,
    currentStageLabel: "draft",
    validationLabel: "",
    activityLabel: "",
    stages: [],
    kind: "run_short",
    initRepairAvailable: false,
    hasCheckpoint: false,
    ...(isCancelled === undefined ? {} : { isCancelled }),
  };
}

describe("workflow run state", () => {
  it("uses raw failed status for terminal actions instead of localized text", () => {
    const failed = run("failed");

    expect(workflowRunState(failed)).toBe("failed");
    expect(isFailedWorkflowRun(failed)).toBe(true);
    expect(isClearableWorkflowRun(failed)).toBe(true);
  });

  it("keeps active work non-clearable in either supported locale", () => {
    expect(isActiveWorkflowRun(run("执行中"))).toBe(true);
    expect(isActiveWorkflowRun(run("queued"))).toBe(true);
    expect(isClearableWorkflowRun(run("queued"))).toBe(false);
  });

  it("allows an inactive paused record to be dismissed", () => {
    expect(isClearableWorkflowRun(run("已暂停"))).toBe(true);
    expect(isClearableWorkflowRun(run("paused"))).toBe(true);
  });
});
