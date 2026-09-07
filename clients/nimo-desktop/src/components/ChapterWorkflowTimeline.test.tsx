import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import type { ChapterStudioActivityView, EngineClient } from "@nimo/engine-contracts";

import { LocaleProvider } from "../lib/i18n";
import {
  CHAPTER_WORKFLOW_PHASES,
  chapterWorkflowPhaseLabel,
  chapterWorkflowPhaseSteps,
} from "./chapter-workflow-model";
import { ChapterWorkflowTimeline } from "./ChapterWorkflowTimeline";
import { PREPARE_CHAPTER_STEPS, RESOLVE_CHECKPOINT_STEPS, RESOLVE_FINALIZE_STEPS, RUN_CHAPTER_STEPS } from "./StepIndicatorRow";

const engineClient = {
  getStepArtifacts: vi.fn(),
} as unknown as EngineClient;

function activity(): ChapterStudioActivityView {
  return {
    state: "running",
    taskLabel: "第 5 章生成",
    currentStepLabel: "reading_power_repair",
    operationDetail: "追读力评估已返回，正在校验结构（第 1/2 次）",
    progressPercent: 68,
    checkpoint: null,
    stages: RUN_CHAPTER_STEPS.map((step, index) => ({
      id: step.key,
      label: step.label,
      state: index < 11 ? "completed" as const : index === 11 ? "active" as const : "pending" as const,
    })),
  };
}

describe("ChapterWorkflowTimeline", () => {
  it.each(["pre_alignment", "post_alignment", "guard_checkpoint"])(
    "retains the interactive checkpoint cursor %s", (key) => {
      const index = RESOLVE_CHECKPOINT_STEPS.findIndex((step) => step.key === key);
      const html = renderToStaticMarkup(<ChapterWorkflowTimeline
        activity={{ ...activity(), kind: "resolve_chapter_checkpoint", currentStepLabel: key,
          stages: RESOLVE_CHECKPOINT_STEPS.map((step, i) => ({ id: step.key, label: step.label,
            state: i < index ? "completed" : i === index ? "active" : "pending" })) }}
        chapterNumber={5} engineClient={engineClient} onNotice={vi.fn()} projectId="test-long"
      />);
      expect(html).toContain(`aria-label="${RESOLVE_CHECKPOINT_STEPS[index]!.label}：执行中"`);
      expect(html).toContain("六个阶段、7 个可追踪节点");
      expect(html.match(/<li class="is-active"/g)).toHaveLength(1);
    },
  );

  it("groups every finalization node without inventing extra completed milestones", () => {
    const grouped = CHAPTER_WORKFLOW_PHASES.flatMap((phase) => chapterWorkflowPhaseSteps(phase, RESOLVE_FINALIZE_STEPS));
    expect(grouped.map((step) => step.key)).toEqual(RESOLVE_FINALIZE_STEPS.map((step) => step.key));
  });
  it("groups every detailed chapter milestone into the six production phases", () => {
    expect(CHAPTER_WORKFLOW_PHASES.map((phase) => phase.label)).toEqual([
      "准备与规划",
      "生成",
      "审读与修复",
      "润色",
      "拟人化",
      "归档",
    ]);
    expect(CHAPTER_WORKFLOW_PHASES.flatMap((phase) => phase.stepKeys)).toEqual(
      RUN_CHAPTER_STEPS.map((step) => step.key),
    );
    expect(CHAPTER_WORKFLOW_PHASES.map((phase) => chapterWorkflowPhaseLabel(phase, "en"))).toEqual([
      "Planning & Context",
      "Generate",
      "Review & Repair",
      "Polish",
      "Humanize",
      "Finalize",
    ]);
  });

  it("shows precise progress and exposes completed nodes as artifact buttons", () => {
    const html = renderToStaticMarkup(
      <LocaleProvider locale="zh">
        <ChapterWorkflowTimeline
          activity={activity()}
          chapterNumber={5}
          engineClient={engineClient}
          onNotice={vi.fn()}
          projectId="test-long"
        />
      </LocaleProvider>,
    );

    expect(html).toContain("章台任务流");
    expect(html).toContain("六个阶段、18 个可追踪节点");
    expect(html).toContain("章节研究");
    expect(html).toContain('aria-label="章节任务进度 68%"');
    expect(html).toContain("WAVE 编织");
    expect(html).toContain("追读力修复");
    expect(html).toContain('aria-label="准备上下文：查看产物"');
    expect(html).toContain('aria-label="护栏复核：查看产物"');
    expect(html).toContain('aria-label="因果修复：执行中"');
    expect(html).toContain("追读力评估已返回，正在校验结构（第 1/2 次）");
  });

  it("defaults to the latest historical chapter task when no task is active", () => {
    const html = renderToStaticMarkup(
      <LocaleProvider locale="zh">
        <ChapterWorkflowTimeline
          activity={{
            state: "idle",
            taskLabel: "",
            currentStepLabel: "",
            progressPercent: null,
            checkpoint: null,
            stages: [],
            history: [{
              kind: "prepare_chapter",
              taskId: "prepare-history",
              status: "paused",
              taskLabel: "第 1 章方案准备",
              currentStepLabel: "方案确认",
              progressPercent: 93,
              updatedAt: "2026-08-30T14:18:00+00:00",
              stages: PREPARE_CHAPTER_STEPS.map((step, index) => ({
                id: step.key,
                label: step.label,
                state: index < 3 ? "completed" as const : "blocked" as const,
              })),
            }],
          }}
          chapterNumber={1}
          engineClient={engineClient}
          onNotice={vi.fn()}
          projectId="test-long"
        />
      </LocaleProvider>,
    );

    expect(html).toContain("某次任务的持久记录");
    expect(html).toContain("历史尝试：");
    expect(html).toContain("六个阶段、3 个可追踪节点");
    expect(html).toContain('aria-label="桥接与方案：查看产物"');
    expect(html).toContain("本次未涉及");
    expect(html).not.toContain("0/0");
  });

  it("defaults to the canonical chapter flow while preserving raw attempts", () => {
    const html = renderToStaticMarkup(
      <LocaleProvider locale="zh">
        <ChapterWorkflowTimeline
          activity={{
            state: "idle",
            taskLabel: "",
            currentStepLabel: "",
            progressPercent: null,
            checkpoint: null,
            stages: [],
            chapterFlow: {
              kind: "run_chapter",
              taskId: "chapter-flow:finalize-history",
              status: "succeeded",
              taskLabel: "第 1 章·本章全流程",
              currentStepLabel: "记忆更新",
              progressPercent: 100,
              updatedAt: "2026-08-30T18:00:00+00:00",
              stages: RUN_CHAPTER_STEPS.map((step) => ({
                id: step.key,
                label: step.label,
                state: "completed" as const,
              })),
            },
            history: [{
              kind: "resolve_chapter_checkpoint_finalize",
              taskId: "finalize-history",
              status: "succeeded",
              taskLabel: "章节断点继续",
              currentStepLabel: "记忆更新",
              progressPercent: 100,
              updatedAt: "2026-08-30T18:00:00+00:00",
              stages: RESOLVE_FINALIZE_STEPS.map((step) => ({
                id: step.key,
                label: step.label,
                state: "completed" as const,
              })),
            }],
          }}
          chapterNumber={1}
          engineClient={engineClient}
          onNotice={vi.fn()}
          projectId="test-long"
        />
      </LocaleProvider>,
    );

    expect(html).toContain("本章累计流程");
    expect(html).toContain("本章流程：");
    expect(html).toContain("六个阶段、18 个可追踪节点");
    expect(html).toContain("本章全流程 · 08-30 18:00");
    expect(html).toContain('aria-label="DRAFT 草稿：查看产物"');
    expect(html).not.toContain("本次未涉及");
  });

  it("localizes phase and node states without trusting Engine labels", () => {
    const base = activity();
    const mixedStateActivity: ChapterStudioActivityView = {
      ...base,
      currentStepLabel: "plan",
      stages: RUN_CHAPTER_STEPS.map((step) => ({
        id: step.key,
        label: `engine:${step.key}`,
        state: step.key === "chapter_research"
          ? "skipped" as const
          : step.key === "bridge"
            ? "rolled_back" as const
            : step.key === "plan"
              ? "blocked" as const
              : "pending" as const,
      })),
    };
    const html = renderToStaticMarkup(
      <LocaleProvider locale="en">
        <ChapterWorkflowTimeline
          activity={mixedStateActivity}
          chapterNumber={5}
          engineClient={engineClient}
          onNotice={vi.fn()}
          projectId="test-long"
        />
      </LocaleProvider>,
    );

    expect(html).toContain("Chapter workflow");
    expect(html).toContain("Planning &amp; Context");
    expect(html).toContain("Review &amp; Repair");
    expect(html).toContain('aria-label="Chapter Research: Skipped"');
    expect(html).toContain('aria-label="Chapter Bridge: Rolled back"');
    expect(html).toContain('aria-label="Chapter Plan: Blocked"');
    expect(html).not.toContain("engine:plan");
  });
});
