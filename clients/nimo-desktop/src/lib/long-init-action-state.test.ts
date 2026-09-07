import { describe, expect, it } from "vitest";

import type { WorkflowRunView } from "@nimo/engine-contracts";

import { longInitActionState, shouldShowLongInitResumeAction } from "./long-init-action-state";

function run(stateLabel: string, extra?: Partial<WorkflowRunView>): WorkflowRunView {
  return {
    id: "run-1",
    title: "长篇立项 · 测试项目",
    projectLabel: "测试项目",
    elapsedLabel: "01:00",
    progressPercent: 10,
    stateLabel,
    currentStageLabel: "叙事蓝图",
    validationLabel: "",
    activityLabel: "",
    stages: [],
    kind: "init_long",
    initRepairAvailable: false,
    hasCheckpoint: false,
    ...extra,
  };
}

describe("longInitActionState", () => {
  it("hides the task-flow resume action while an initialization is running", () => {
    expect(shouldShowLongInitResumeAction(true, true)).toBe(false);
    expect(shouldShowLongInitResumeAction(true, false)).toBe(true);
    expect(shouldShowLongInitResumeAction(false, false)).toBe(false);
  });

  it("shows create labels when no run exists and nothing is resumable", () => {
    const state = longInitActionState({ activeRun: null, initRunning: false, resumable: false });
    expect(state.isFailed).toBe(false);
    expect(state.isPaused).toBe(false);
    expect(state.canResume).toBe(false);
    expect(state.showResumePanel).toBe(false);
    expect(state.showFailedPanel).toBe(false);
    expect(state.submitLabel).toBe("创建长篇项目 →");
    expect(state.copilotLabel).toBe("AI 伴随立项 →");
    expect(state.autorunLabel).toBe("创建并连跑 →");
  });

  it("enables resume from an engine-confirmed resumable project without a run", () => {
    const state = longInitActionState({ activeRun: null, initRunning: false, resumable: true });
    expect(state.canResume).toBe(true);
    expect(state.showResumePanel).toBe(true);
    expect(state.showFailedPanel).toBe(false);
    expect(state.submitLabel).toBe("继续立项 →");
    expect(state.copilotLabel).toBe("伴随继续 →");
    expect(state.autorunLabel).toBe("继续并连跑 →");
  });

  it("maps a failed run to the failed/resumable branch", () => {
    const state = longInitActionState({ activeRun: run("failed"), initRunning: false, resumable: false });
    expect(state.activeRunState).toBe("failed");
    expect(state.isFailed).toBe(true);
    expect(state.canResume).toBe(true);
    expect(state.showFailedPanel).toBe(true);
    expect(state.showResumePanel).toBe(false);
    expect(state.submitLabel).toBe("继续立项 →");
  });

  it("returns to create labels after a confirmed fresh-init reset", () => {
    const beforeReset = longInitActionState({
      activeRun: run("failed"),
      initRunning: false,
      resumable: true,
    });
    const afterReset = longInitActionState({
      activeRun: null,
      initRunning: false,
      resumable: false,
    });

    expect(beforeReset.submitLabel).toBe("继续立项 →");
    expect(afterReset.showResumePanel).toBe(false);
    expect(afterReset.submitLabel).toBe("创建长篇项目 →");
    expect(afterReset.copilotLabel).toBe("AI 伴随立项 →");
    expect(afterReset.autorunLabel).toBe("创建并连跑 →");
  });

  it("normalizes cancelled/interrupted Chinese labels into failed", () => {
    for (const label of ["已取消", "已中断", "cancelled", "canceled", "interrupted"]) {
      const state = longInitActionState({ activeRun: run(label), initRunning: false, resumable: false });
      expect(state.activeRunState, label).toBe("failed");
      expect(state.isFailed, label).toBe(true);
      expect(state.canResume, label).toBe(true);
    }
  });

  it("treats isCancelled=true as failed regardless of label", () => {
    const state = longInitActionState({
      activeRun: run("running", { isCancelled: true }),
      initRunning: false,
      resumable: false,
    });
    expect(state.activeRunState).toBe("failed");
    expect(state.isFailed).toBe(true);
    expect(state.canResume).toBe(true);
  });

  it("maps a paused run to the paused/resumable branch", () => {
    for (const label of ["paused", "已暂停"]) {
      const state = longInitActionState({ activeRun: run(label), initRunning: false, resumable: false });
      expect(state.activeRunState, label).toBe("paused");
      expect(state.isPaused, label).toBe(true);
      expect(state.canResume, label).toBe(true);
      expect(state.showFailedPanel, label).toBe(false);
    }
  });

  it("shows the resume panel for running and queued runs without enabling resume", () => {
    for (const label of ["running", "执行中", "queued", "排队中"]) {
      const state = longInitActionState({ activeRun: run(label), initRunning: false, resumable: false });
      expect(state.activeRunState, label).toBe(label === "执行中" ? "running" : label === "排队中" ? "queued" : label);
      expect(state.showResumePanel, label).toBe(true);
      expect(state.isFailed, label).toBe(false);
      expect(state.canResume, label).toBe(false);
      expect(state.submitLabel, label).toBe("创建长篇项目 →");
    }
  });

  it("keeps the running labels while initialization is in progress", () => {
    const state = longInitActionState({ activeRun: null, initRunning: true, resumable: false });
    expect(state.submitLabel).toBe("立项进行中…");
    expect(state.copilotLabel).toBe("伴随立项中…");
    expect(state.autorunLabel).toBe("继续并连跑…");
  });

  it("keeps the running labels during a failed run while initialization restarts", () => {
    const state = longInitActionState({ activeRun: run("failed"), initRunning: true, resumable: false });
    expect(state.submitLabel).toBe("立项进行中…");
    expect(state.isFailed).toBe(true);
  });

  it("maps an unknown label to unknown without enabling resume", () => {
    const state = longInitActionState({ activeRun: run("weird-state"), initRunning: false, resumable: false });
    expect(state.activeRunState).toBe("unknown");
    expect(state.isFailed).toBe(false);
    expect(state.isPaused).toBe(false);
    expect(state.canResume).toBe(false);
    expect(state.showResumePanel).toBe(false);
  });
});
