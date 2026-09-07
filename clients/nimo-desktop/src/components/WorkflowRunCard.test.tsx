import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import type { WorkflowRunView } from "@nimo/engine-contracts";

import { WorkflowRunCard } from "./WorkflowRunCard";

function pausedRun(kind: string): WorkflowRunView {
  return {
    id: `${kind}-1`,
    title: "长篇立项 · 测试项目",
    projectLabel: "测试项目",
    elapsedLabel: "12:45",
    progressPercent: 48,
    stateLabel: "已暂停",
    currentStageLabel: "叙事蓝图",
    validationLabel: "等待作者确认",
    activityLabel: "已保存恢复点",
    stages: [],
    kind,
    initRepairAvailable: false,
    hasCheckpoint: false,
  };
}

function renderCard(run: WorkflowRunView): string {
  return renderToStaticMarkup(
    <WorkflowRunCard
      onInitRepair={vi.fn()}
      onManualRepair={vi.fn()}
      onOpenArtifact={vi.fn()}
      onRunAction={vi.fn()}
      run={run}
      tick={0}
    />,
  );
}

describe("WorkflowRunCard resume labels", () => {
  it("uses the work name without repeating its hidden project identifier", () => {
    const html = renderCard({
      ...pausedRun("init_long"),
      title: "药证",
      projectId: "long_be882a54",
      projectLabel: "药证",
      stateLabel: "running",
    });

    expect(html).toContain("<h3>药证</h3>");
    expect(html).toContain("已运行 12:45");
    expect(html).not.toContain("long_be882a54");
    expect(html).not.toContain("药证 · 已运行");
  });

  it("prefers the display label from an older Engine response", () => {
    const html = renderCard({
      ...pausedRun("init_long"),
      title: "长篇立项 · long_be882a54",
      projectLabel: "青瓦梦起",
      elapsedLabel: "已运行 31:48",
      stateLabel: "running",
    });

    expect(html).toContain("<h3>青瓦梦起</h3>");
    expect(html).toContain("<p>已运行 31:48</p>");
    expect(html).not.toContain("long_be882a54");
  });

  it("calls out a paused long initialization as continuing initialization", () => {
    expect(renderCard(pausedRun("init_long"))).toContain("继续立项 →");
  });

  it("keeps generic resume wording for non-initialization jobs", () => {
    expect(renderCard(pausedRun("run_short"))).toContain("▶ 恢复");
  });

  it("draws a PySide-style frontier when a live projection has only the raw current step", () => {
    const html = renderCard({
      ...pausedRun("init_long"),
      progressPercent: 0,
      stateLabel: "running",
      currentStageLabel: "plan_blueprint_subplots",
    });

    expect(html).toContain('aria-label="任务进度 43%"');
    expect((html.match(/step-dot is-done/g) ?? []).length).toBe(6);
    expect(html).toContain("step-dot is-active");
  });

  it("renders signed lineage, degradation, and Engine-owned recovery", () => {
    const html = renderCard({
      ...pausedRun("run_chapter"),
      actualState: "failed",
      stateLabel: "failed",
      qualityStatus: "degraded",
      degradationReason: "Humanize 使用本地兜底",
      derivationStatus: "stale",
      staleDependencies: ["配音脚本过期"],
      workflowVersion: "novel.chapter.v2",
      outputVersion: 3,
      inputSignature: "0123456789abcdef",
      cumulativeTokens: 8800,
      checkpoint: {
        exists: true,
        completedStage: "repair_done",
        sourceTextHash: "hash-3",
        inputSignature: "checkpoint-sig",
      },
      recoveryActions: [
        { id: "resume_checkpoint", kind: "resume_checkpoint", label: "断点续写", enabled: true },
      ],
      stages: [
        { id: "draft", label: "初稿成章", state: "completed" },
        { id: "alignment", label: "质量检查", state: "failed" },
      ],
    });

    expect(html).toContain("质量：已降级");
    expect(html).toContain("血缘：stale");
    expect(html).toContain("Humanize 使用本地兜底");
    expect(html).toContain("配音脚本过期");
    expect(html).toContain("novel.chapter.v2 · 产物 v3 · 签名 0123456789 · 8,800 tokens");
    expect(html).toContain("🔄 断点续写");
    expect(html).toContain("step-dot is-failed");
  });

  it("distinguishes execution completion from an explicitly blocked final quality", () => {
    const html = renderCard({
      ...pausedRun("init_long"),
      actualState: "succeeded",
      stateLabel: "succeeded",
      qualityStatus: "blocked",
      degradationReason: "最终契约仍有缺口",
    });
    expect(html).toContain("执行完成 · 质量待处理");
    expect(html).toContain("质量：未通过");
    expect(html).not.toContain("is-completed");
  });

  it("uses a localized completed label after successful repair", () => {
    const html = renderCard({
      ...pausedRun("run_short"),
      actualState: "succeeded",
      stateLabel: "succeeded",
      qualityStatus: "actual",
    });
    expect(html).toContain("已完成");
    expect(html).not.toContain("质量：未通过");
    expect(html).not.toContain(">succeeded<");
  });

  it("surfaces chapter governance insights without affecting legacy cards", () => {
    const html = renderCard({
      ...pausedRun("run_chapter"),
      runInsights: [{
        id: "intent",
        status: "blocked",
        label: "意图保护",
        summary: "候选修复会改变用户指定结局",
        detail: "已保留安全版本。",
        count: 1,
        artifactStepKey: "",
      }],
      efficiency: {
        llmCalls: 3,
        promptTokens: 100,
        completionTokens: 50,
        totalTokens: 150,
        costUsd: 0.01,
        researchQueries: 0,
        researchCacheHits: 0,
        semanticMutations: 1,
        reportRefreshes: 1,
        repairRounds: 1,
        shortRevisionRounds: 0,
        rollbacks: 1,
        finalHashVerifications: 0,
      },
    });

    expect(html).toContain('aria-label="章节运行透明度"');
    expect(html).toContain("意图保护");
    expect(html).toContain("已阻断");
  });
});
