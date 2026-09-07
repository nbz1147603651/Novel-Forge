import { describe, expect, it } from "vitest";

import type { ChapterStudioView } from "@nimo/engine-contracts";

import {
  createChapterCheckpointActivity,
  createPreparingChapterActivity,
  createRunningChapterActivity,
  projectChapterStudioParityState,
} from "./chapter-studio-session";

const studio: ChapterStudioView = {
  projectId: "test-long",
  projectTitle: "测试长篇",
  projectSynopsis: "测试项目",
  nextChapter: 5,
  totalChapters: 24,
  chapters: [
    { number: 4, title: "高架桥下", state: "completed", detail: "已归档" },
    { number: 5, title: "档案室", state: "current", detail: "待创作" },
  ],
  planTitle: "准备章节方案",
  planSummary: "章节工作台当前无运行中任务。",
  previousSummary: "上一章实际结果。",
  previousExitSummary: "上一章退出点。",
  currentGoal: "本章目标。",
  currentOutlineSummary: "本章方案。",
  nextGoal: "下一章预埋。",
  suggestion: "建议。",
  memories: [],
  memoryTabs: [{
    id: "overview",
    label: "概览",
    cards: [
      { id: "checkpoint", label: "待决策节点", content: "暂无待处理的决策节点。" },
      { id: "scores", label: "评分", content: "对齐 8.7" },
    ],
  }],
  activity: {
    state: "idle",
    taskLabel: "",
    currentStepLabel: "",
    progressPercent: null,
    checkpoint: null,
    stages: [],
  },
  autorun: {
    status: "idle",
    phase: "",
    mode: "book",
    startChapter: 0,
    currentChapter: 0,
    endChapter: 0,
    totalChapters: 0,
    completedChapters: [],
    activeTaskId: "",
    checkpoint: null,
    checkpointAttempts: 0,
    checkpointBudget: 0,
    totalFailures: 0,
    failureBudget: 0,
    nextRetryAt: "",
    lastError: "",
    updatedAt: "",
  },
  taskErrorLog: [],
};

describe("chapter studio activity projection", () => {
  it("derives the running presentation from the typed chapter view", () => {
    const activity = createRunningChapterActivity(studio);

    expect(activity).toMatchObject({
      state: "running",
      taskLabel: "第 5 章 · 档案室",
      currentStepLabel: "章节生成",
      progressPercent: 43,
      checkpoint: null,
    });
  });

  it("keeps the prepare command state distinct from the running capture fixture", () => {
    const activity = createPreparingChapterActivity(studio, 6);

    expect(activity).toMatchObject({
      state: "running",
      taskLabel: "第 6 章生成",
      currentStepLabel: "准备章节方案",
      operationDetail: "正在向引擎提交章节准备任务",
      progressPercent: 5,
      checkpoint: null,
    });
  });

  it("projects the native-capture running scores without mutating the engine view", () => {
    const projected = projectChapterStudioParityState(studio, "running");

    expect(studio.activity.state).toBe("idle");
    expect(projected.activity.state).toBe("running");
    expect(projected.memoryTabs.find((tab) => tab.id === "overview")?.cards.find((card) => card.id === "scores")?.content)
      .toContain("等待初稿生成");
  });

  it("marks only the current chapter as a checkpoint decision and carries typed options", () => {
    const activity = createChapterCheckpointActivity(studio);
    const projected = projectChapterStudioParityState(studio, "checkpoint");

    expect(activity.checkpoint?.options).toEqual([
      expect.objectContaining({ id: "adopt", recommended: true }),
      expect.objectContaining({ id: "regenerate", recommended: false }),
    ]);
    expect(projected.chapters.find((chapter) => chapter.number === studio.nextChapter)?.state).toBe("needs_decision");
    expect(projected.chapters.find((chapter) => chapter.number === 4)?.state).toBe("completed");
  });
});
