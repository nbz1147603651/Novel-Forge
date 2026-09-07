import type { ChapterMemoryTabView, ChapterStudioActivityView, ChapterStudioCheckpointView, ChapterStudioView, StartLongChapterWorkflowCommand, WorkflowCommandResult } from "@nimo/engine-contracts";

export type ChapterStudioParityState = "prepared" | "running" | "checkpoint" | "history" | "notice" | "source-conflict";

const completedChapterFlowStages = [
  ["state_packet", "准备上下文"],
  ["chapter_research", "章节研究"],
  ["bridge", "章节桥接"],
  ["plan", "章节规划"],
  ["draft", "DRAFT 草稿"],
  ["wave", "WAVE 编织"],
  ["opening_guard", "开篇护栏"],
  ["alignment", "质量审读"],
  ["continuity_repair", "连续性修复"],
  ["alignment_repair", "对齐修复"],
  ["guard_review", "护栏复核"],
  ["causal_repair", "因果修复"],
  ["reading_power_repair", "追读力修复"],
  ["polish", "文学精修"],
  ["humanize", "拟人化清理"],
  ["extract_canon", "状态提取"],
  ["persist", "正文归档"],
  ["memory_updated", "记忆更新"],
] as const;

const completedFinalizeAttemptStages = [
  ["guard_checkpoint", "归档选择"],
  ["post_guard_repair", "归档前修复"],
  ["polish_reextract_canon", "状态提取"],
  ["persist", "正文落盘"],
  ["evaluate", "质量评估"],
  ["volume_audit", "卷末审计"],
  ["memory_updated", "记忆更新"],
] as const;

const sessionCheckpoint = (): ChapterStudioCheckpointView => ({
  id: "session-plan-checkpoint",
  title: "方案需确认",
  summary: "本章的开场证据、人物边界与章节落点已经汇总。请选择继续路径。",
  prompt: "需要人工确认后才能继续执行本章草稿。",
  options: [
    { id: "adopt", label: "采用当前方案", description: "保留时间戳、授权链和林小满在场的既定边界。", recommended: true },
    { id: "regenerate", label: "带备注重新规划", description: "基于人工补充约束重新生成章节计划。", recommended: false },
  ],
});

export function createRunningChapterActivity(studio: ChapterStudioView): ChapterStudioActivityView {
  return {
    state: "running",
    taskLabel: `第 ${studio.nextChapter} 章 · ${studio.chapters.find((chapter) => chapter.number === studio.nextChapter)?.title ?? studio.planTitle}`,
    currentStepLabel: "章节生成",
    progressPercent: 43,
    checkpoint: null,
    stages: [],
  };
}

export function createPreparingChapterActivity(
  studio: ChapterStudioView,
  chapterNumber: number = studio.nextChapter,
): ChapterStudioActivityView {
  return {
    state: "running",
    taskLabel: `第 ${chapterNumber} 章生成`,
    currentStepLabel: "准备章节方案",
    operationDetail: "正在向引擎提交章节准备任务",
    progressPercent: 5,
    checkpoint: null,
    stages: [],
  };
}

export function createChapterCheckpointActivity(studio: ChapterStudioView): ChapterStudioActivityView {
  return {
    state: "checkpoint",
    taskLabel: "",
    currentStepLabel: "",
    progressPercent: null,
    checkpoint: sessionCheckpoint(),
    stages: [],
  };
}

export interface AutorunStartCommandInput {
  readonly projectId: string;
  readonly chapterNumber: number;
  readonly chapterState: string;
  readonly compositionMode: "whole" | "scene";
  readonly isBookAuto: boolean;
}

/**
 * Build the durable Engine start-workflow command for chapter auto-run.
 *
 * Extracted from the action panel so the command shape (runMode, scope,
 * skipDone, force rule) stays testable without a DOM.
 */
export function buildAutorunStartCommand(
  input: AutorunStartCommandInput,
): StartLongChapterWorkflowCommand {
  const scope = input.isBookAuto ? "book" : "chapter";
  return {
    kind: "start_workflow",
    projectId: input.projectId,
    workflowType: "long_chapter",
    runMode: "autorun",
    idempotencyKey: `autorun-${input.projectId}-${input.chapterNumber}-${scope}`,
    payload: {
      projectId: input.projectId,
      chapterNumber: input.chapterNumber,
      force: input.chapterState === "completed" || input.chapterState === "stale",
      writingMode: input.compositionMode === "scene" ? "scene_level" : "whole_chapter",
      autorunScope: scope,
      skipDone: true,
    },
  };
}

/**
 * Optimistic activity shown right after the start command is accepted — the
 * engine projection (studio.autorun) replaces it on the next refresh.
 */
export function createAutorunWaitingActivity(autoLabel: string): ChapterStudioActivityView {
  return {
    state: "running",
    taskLabel: autoLabel,
    currentStepLabel: "等待工作区同步",
    progressPercent: 5,
    checkpoint: null,
    stages: [],
  };
}

export interface AutorunStartFeedback {
  readonly rejected: boolean;
  readonly notice: string;
}

/** Map a start-workflow result to user-facing feedback (rejected ≠ accepted). */
export function describeAutorunStartResult(
  result: WorkflowCommandResult,
  autoLabel: string,
): AutorunStartFeedback {
  if (result.status === "rejected") {
    return { rejected: true, notice: `${autoLabel}未启动：${result.message}` };
  }
  return { rejected: false, notice: result.message };
}

/** Button label for the autorun start action, with an in-flight busy form. */
export function autorunStartButtonLabel(busy: boolean, autoLabel: string): string {
  return busy ? "启动中…" : `启动${autoLabel} ▶`;
}

/** Button label for the autorun stop action, with an in-flight busy form. */
export function autorunStopButtonLabel(busy: boolean, autoLabel: string): string {
  return busy ? "停止中…" : `⏹ 停止${autoLabel}`;
}

function replaceOverviewCard(
  tabs: readonly ChapterMemoryTabView[],
  id: string,
  content: string,
): readonly ChapterMemoryTabView[] {
  return tabs.map((tab) => tab.id !== "overview" ? tab : {
    ...tab,
    cards: tab.cards.map((card) => card.id === id ? { ...card, content } : card),
  });
}

/** Apply only the explicit native-capture state requested by the parity URL. */
export function projectChapterStudioParityState(
  studio: ChapterStudioView,
  state: ChapterStudioParityState | null,
): ChapterStudioView {
  if (state === null || state === "prepared" || state === "notice") return studio;

  if (state === "history") {
    const updatedAt = "2026-08-30T18:00:58+00:00";
    return {
      ...studio,
      activity: {
        state: "idle",
        taskLabel: "",
        currentStepLabel: "",
        progressPercent: null,
        checkpoint: null,
        stages: [],
        chapterFlow: {
          kind: "run_chapter",
          taskId: "chapter-flow:fixture-finalize",
          status: "succeeded",
          taskLabel: `第 ${studio.nextChapter} 章·本章全流程`,
          currentStepLabel: "记忆更新",
          progressPercent: 100,
          updatedAt,
          stages: completedChapterFlowStages.map(([id, label]) => ({
            id,
            label,
            state: "completed" as const,
          })),
        },
        history: [{
          kind: "resolve_chapter_checkpoint_finalize",
          taskId: "fixture-finalize",
          status: "succeeded",
          taskLabel: "章节断点继续",
          currentStepLabel: "记忆更新",
          progressPercent: 100,
          updatedAt,
          stages: completedFinalizeAttemptStages.map(([id, label]) => ({
            id,
            label,
            state: "completed" as const,
          })),
        }],
      },
    };
  }

  if (state === "running") {
    return {
      ...studio,
      activity: createRunningChapterActivity(studio),
      memoryTabs: replaceOverviewCard(
        studio.memoryTabs,
        "scores",
        "对齐 · 等待章节检查\n连贯 · 等待初稿生成\n质量 · 等待初稿生成\n因果 · 等待章节检查\n追读 · 等待初稿生成\n提醒：档案室场景需保留门禁失效的因果链。",
      ),
    };
  }

  if (state === "source-conflict") {
    return {
      ...studio,
      autorun: {
        ...studio.autorun,
        status: "failed",
        mode: "book",
        currentChapter: studio.nextChapter,
        lastError: "审查上游的「停职」期限冲突：大纲=[3, 5]日，Plan=[3, 5]日。",
        lastFailureKind: "upstream_source_conflict",
        recoveryTarget: "manual",
      },
    };
  }

  const checkpoint = createChapterCheckpointActivity(studio);
  return {
    ...studio,
    chapters: studio.chapters.map((chapter) => chapter.number === studio.nextChapter
      ? { ...chapter, state: "needs_decision", detail: "方案待确认" }
      : chapter),
    activity: checkpoint,
    memoryTabs: replaceOverviewCard(
      studio.memoryTabs,
      "checkpoint",
      `${checkpoint.checkpoint?.summary}\n\n${checkpoint.checkpoint?.prompt}\n\n提醒：档案室场景需保留门禁失效的因果链。`,
    ),
  };
}
