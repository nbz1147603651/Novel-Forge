/**
 * Render-level and command-shape tests for ChapterStudioActionPanel.
 *
 * The repo's vitest config runs in a Node environment without a DOM (see
 * App.test.tsx), so click-driven interaction is covered by the pure helpers
 * in chapter-studio-session.ts while the panel's render states are asserted
 * through renderToStaticMarkup.
 */

import { describe, expect, it } from "vitest";
import { renderToStaticMarkup } from "react-dom/server";

import type {
  ChapterStudioActivityView,
  ChapterStudioView,
  EngineCommandClient,
} from "@nimo/engine-contracts";

import { ChapterStudioActionPanel, type WritingMode } from "./ChapterStudioActionPanel";
import { EngineRuntimeContext, type EngineRuntimeState } from "../lib/engine-runtime-context";
import {
  autorunStartButtonLabel,
  autorunStopButtonLabel,
  buildAutorunStartCommand,
  createAutorunWaitingActivity,
  describeAutorunStartResult,
} from "../lib/chapter-studio-session";

const runtime: EngineRuntimeState = {
  mode: "mock",
  negotiation: { status: "negotiating" },
  diagnostic: { connection: "ready", message: "mock engine", canSubmitTasks: true },
  canSubmitTasks: true,
  isCommandAvailable: () => true,
  isFeatureEnabled: () => true,
};

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
  previousSummary: "",
  previousExitSummary: "",
  currentGoal: "",
  currentOutlineSummary: "",
  nextGoal: "",
  suggestion: "",
  memories: [],
  memoryTabs: [],
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

interface RenderPanelOptions {
  readonly studio: ChapterStudioView;
  readonly mode: WritingMode;
  readonly activity?: ChapterStudioActivityView;
  readonly chapterState?: string;
}

function renderPanel(options: RenderPanelOptions): string {
  return renderToStaticMarkup(
    <EngineRuntimeContext.Provider value={runtime}>
      <ChapterStudioActionPanel
        activity={options.activity ?? options.studio.activity}
        chapterNumber={5}
        chapterState={options.chapterState ?? "current"}
        chapterWordCount={0}
        commandClient={{} as EngineCommandClient}
        compositionMode="whole"
        mode={options.mode}
        onActivityChange={() => {}}
        onGoToNextChapter={() => {}}
        onModeChange={() => {}}
        onNotice={() => {}}
        onOpenCheckpointDialog={() => {}}
        studio={options.studio}
        totalChapters={options.studio.totalChapters}
      />
    </EngineRuntimeContext.Provider>,
  );
}

describe("ChapterStudioActionPanel render states", () => {
  it("renders the 启动章节连跑 button for the book_auto prepare state", () => {
    const html = renderPanel({ studio, mode: "book_auto" });

    expect(html).toContain("启动章节连跑 ▶");
    expect(html).toContain("章节连跑模式：点击「启动章节连跑」后");
  });

  it("flips to 章节连跑推进中 once the engine autorun projection is running", () => {
    const runningStudio: ChapterStudioView = {
      ...studio,
      autorun: {
        ...studio.autorun,
        status: "running",
        mode: "book",
        currentChapter: 5,
        endChapter: 24,
        totalChapters: 24,
      },
      activity: {
        state: "running",
        taskLabel: "第 5 章生成",
        currentStepLabel: "准备章节方案",
        progressPercent: 5,
        checkpoint: null,
        stages: [],
      },
    };

    const html = renderPanel({ studio: runningStudio, mode: "book_auto" });

    expect(html).toContain("章节连跑推进中");
    expect(html).toContain("章节连跑中");
    expect(html).toContain("停止章节连跑");
  });
});

describe("autorun button labels", () => {
  it("shows an environment wait without calling the chapter failed or generating", () => {
    const html = renderPanel({
      studio: {
        ...studio,
        autorun: {
          ...studio.autorun,
          status: "retry_wait",
          waitReason: "engine_restart_required",
          lastError: "进度已保存，重启后自动继续，不消耗失败预算。",
        },
      },
      mode: "manual",
    });
    expect(html).toContain("等待后端安全重启");
    expect(html).toContain("不消耗失败预算");
    expect(html).toContain("停止章节连跑");
    expect(html).not.toContain("上次执行失败");
    expect(html).not.toContain("重新准备方案");
  });

  it("routes an upstream source conflict to semantic recovery instead of Plan retry", () => {
    const html = renderPanel({
      studio: {
        ...studio,
        autorun: {
          ...studio.autorun,
          status: "failed",
          lastError: "审查上游的「停职」期限冲突：大纲=[3, 5]日",
          lastFailureKind: "upstream_source_conflict",
          recoveryTarget: "manual",
        },
      },
      mode: "book_auto",
    });

    expect(html).toContain("上游语义一致性需处理");
    expect(html).toContain("请先处理上游语义");
    expect(html).not.toContain("重新准备方案");
    expect(html).not.toContain("继续章节连跑");
    expect(html).not.toContain("连跑将按章节轨道自动推进");
  });
  it("shows the busy label while a start/stop command is in flight", () => {
    expect(autorunStartButtonLabel(true, "章节连跑")).toBe("启动中…");
    expect(autorunStartButtonLabel(false, "章节连跑")).toBe("启动章节连跑 ▶");
    expect(autorunStartButtonLabel(false, "本章自动")).toBe("启动本章自动 ▶");
    expect(autorunStopButtonLabel(true, "章节连跑")).toBe("停止中…");
    expect(autorunStopButtonLabel(false, "章节连跑")).toBe("⏹ 停止章节连跑");
  });
});

describe("buildAutorunStartCommand", () => {
  it("issues an autorun start for the book scope with skipDone", () => {
    const command = buildAutorunStartCommand({
      projectId: "test-long",
      chapterNumber: 5,
      chapterState: "current",
      compositionMode: "whole",
      isBookAuto: true,
    });

    expect(command).toMatchObject({
      kind: "start_workflow",
      projectId: "test-long",
      workflowType: "long_chapter",
      runMode: "autorun",
      payload: {
        projectId: "test-long",
        chapterNumber: 5,
        force: false,
        writingMode: "whole_chapter",
        autorunScope: "book",
        skipDone: true,
      },
    });
    expect(command.idempotencyKey).toBe("autorun-test-long-5-book");
  });

  it("scopes chapter mode and forces rewrite for completed/stale chapters", () => {
    const command = buildAutorunStartCommand({
      projectId: "p",
      chapterNumber: 3,
      chapterState: "completed",
      compositionMode: "scene",
      isBookAuto: false,
    });

    expect(command.runMode).toBe("autorun");
    expect(command.payload).toMatchObject({
      force: true,
      writingMode: "scene_level",
      autorunScope: "chapter",
    });
    expect(command.idempotencyKey).toBe("autorun-p-3-chapter");
  });
});

describe("createAutorunWaitingActivity", () => {
  it("projects an immediate running state before the engine snapshot arrives", () => {
    expect(createAutorunWaitingActivity("章节连跑")).toEqual({
      state: "running",
      taskLabel: "章节连跑",
      currentStepLabel: "等待工作区同步",
      progressPercent: 5,
      checkpoint: null,
      stages: [],
    });
  });
});

describe("describeAutorunStartResult", () => {
  it("passes through accepted/already_running messages as non-rejected", () => {
    expect(
      describeAutorunStartResult(
        { status: "accepted", message: "Engine 连跑状态机已启动" },
        "章节连跑",
      ),
    ).toEqual({ rejected: false, notice: "Engine 连跑状态机已启动" });
    expect(
      describeAutorunStartResult(
        { status: "already_running", message: "Engine 连跑会话已在执行" },
        "章节连跑",
      ).rejected,
    ).toBe(false);
  });

  it("marks rejected starts as errors carrying the engine message", () => {
    const feedback = describeAutorunStartResult(
      { status: "rejected", message: "项目尚未生成大纲" },
      "章节连跑",
    );

    expect(feedback).toEqual({ rejected: true, notice: "章节连跑未启动：项目尚未生成大纲" });
  });
});
