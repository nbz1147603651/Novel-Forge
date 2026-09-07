import { describe, expect, it } from "vitest";

import {
  cancelWorkflowRun,
  CHAPTER_CHECKPOINT_KINDS,
  initRepairWorkflowRun,
  workflowRunProjectId,
} from "./workflow-run-session";

const run = {
  id: "init-long",
  title: "长篇立项 · 青瓦梦起",
  projectLabel: "青瓦梦起",
  elapsedLabel: "已运行 31:48",
  progressPercent: 48,
  stateLabel: "执行中",
  currentStageLabel: "叙事蓝图 · 7/15",
  validationLabel: "后台步骤：当前 一致性画像",
  activityLabel: "正在执行：一致性画像",
  stages: [],
  kind: "init_long",
  initRepairAvailable: false,
  hasCheckpoint: false,
} as const;

const chapterRun = {
  ...run,
  id: "run-chapter-3",
  title: "第 3 章 · 雨幕跟踪",
  kind: "run_chapter",
} as const;

describe("workflow run Engine session", () => {
  it("marks a confirmed stop as cancelled without failure copy", () => {
    const [stopped] = cancelWorkflowRun([run], run.id);
    expect(stopped).toMatchObject({
      stateLabel: "已取消",
      validationLabel: "",
      activityLabel: "任务已取消",
      initRepairAvailable: false,
    });
  });

  it("does not fabricate a chapter checkpoint after an optimistic cancel", () => {
    const [stopped] = cancelWorkflowRun([chapterRun], chapterRun.id);
    expect(stopped?.hasCheckpoint).toBe(false);
    expect(CHAPTER_CHECKPOINT_KINDS.has(stopped?.kind ?? "")).toBe(true);
    expect(stopped?.actualState).toBe("cancelled");
  });

  it("starts an AI repair pass and clears the repair affordance", () => {
    const failed = {
      ...run,
      stateLabel: "失败",
      initRepairAvailable: true,
    };
    const [repaired] = initRepairWorkflowRun([failed], run.id);
    expect(repaired).toMatchObject({
      stateLabel: "执行中",
      activityLabel: "正在执行：AI 自动修复",
      initRepairAvailable: false,
    });
  });

  it("prefers the stable project id when resolving task artifacts", () => {
    expect(workflowRunProjectId({
      ...run,
      projectId: "stable-project-id",
      projectLabel: "本地化显示名称",
    })).toBe("stable-project-id");
  });

  it("keeps legacy task cards readable when the Engine has no project id yet", () => {
    expect(workflowRunProjectId(run)).toBe("青瓦梦起");
  });
});
