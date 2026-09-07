import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { LocaleProvider } from "../lib/i18n";
import {
  INIT_LONG_STEPS,
  localizePipelineStepLabel,
  POLISH_CHAPTER_STEPS,
  PREPARE_CHAPTER_STEPS,
  RESOLVE_CHECKPOINT_STEPS,
  RESOLVE_FINALIZE_STEPS,
  RUN_CHAPTER_STEPS,
  RUN_SHORT_STEPS,
  StepIndicatorRow,
  stepIndicatorStateFromStages,
  taskProgressProjection,
} from "./StepIndicatorRow";

const ALL_WORKFLOW_STEPS = [
  ...RUN_SHORT_STEPS,
  ...INIT_LONG_STEPS,
  ...RUN_CHAPTER_STEPS,
  ...PREPARE_CHAPTER_STEPS,
  ...RESOLVE_CHECKPOINT_STEPS,
  ...RESOLVE_FINALIZE_STEPS,
  ...POLISH_CHAPTER_STEPS,
];

describe("StepIndicatorRow localization", () => {
  it("defines Chinese and English copy for every workflow step", () => {
    for (const step of ALL_WORKFLOW_STEPS) {
      expect(step.labels.zh, step.key).not.toBe("");
      expect(step.labels.en, step.key).not.toBe("");
    }
  });

  it("renders English labels and state text in the English interface", () => {
    const markup = renderToStaticMarkup(
      <LocaleProvider locale="en">
        <StepIndicatorRow currentStepKey="spec" steps={RUN_SHORT_STEPS} />
      </LocaleProvider>,
    );

    expect(markup).toContain("Workflow progress");
    expect(markup).toContain("Brief Confirmation");
    expect(markup).toContain("Chapter Research");
    expect(markup).toContain("Adaptive Revision");
    expect(markup).toContain("In progress");
    expect(markup).not.toContain("规格确认");
  });

  it("localizes legacy engine labels while preserving their progress suffix", () => {
    expect(localizePipelineStepLabel("叙事蓝图 · 7/15", "en", INIT_LONG_STEPS)).toBe(
      "Narrative Blueprint · 7/15",
    );
    expect(localizePipelineStepLabel("Narrative Blueprint", "zh", INIT_LONG_STEPS)).toBe(
      "叙事蓝图",
    );
  });

  it("keeps an in-flight task visible when the engine has only an internal event", () => {
    const progress = taskProgressProjection({
      currentStepLabel: "init_claim_entity_adjudication_cache_hit",
      isRunning: true,
      kind: "init_long",
      locale: "en",
      reportedProgress: 0,
      stages: [],
    });

    expect(progress.stepState.currentStepKey).toBe("spec");
    expect(progress.progressPercent).toBeGreaterThan(0);
    expect(progress.currentStepLabel).toBe("Brief Confirmation");
  });

  it("adopts the Engine stage sequence so card progress cannot use a stale local count", () => {
    const progress = taskProgressProjection({
      currentStepLabel: "章节大纲",
      isRunning: true,
      kind: "init_long",
      locale: "en",
      reportedProgress: 65,
      stages: [
        { id: "spec", label: "规格确认", state: "completed" },
        { id: "plan_outline", label: "章节大纲", state: "active" },
        { id: "init_readiness", label: "初始化准入", state: "pending" },
      ],
    });

    expect(progress.steps.map((step) => step.key)).toEqual([
      "spec",
      "plan_outline",
      "init_readiness",
    ]);
    expect(progress.stepState.currentStepKey).toBe("plan_outline");
    expect(progress.progressPercent).toBe(65);
    expect(progress.currentStepLabel).toBe("Chapter Outline");
  });

  it("preserves Engine failed and blocked stage states", () => {
    const progress = taskProgressProjection({
      currentStepLabel: "连续性修复",
      isRunning: false,
      kind: "resolve_chapter_checkpoint",
      locale: "zh",
      reportedProgress: 52,
      stages: [
        { id: "draft", label: "初稿成章", state: "completed" },
        { id: "continuity_repair", label: "连续性修复", state: "failed" },
        { id: "guard_checkpoint", label: "归档选择", state: "pending" },
      ],
    });

    expect(progress.stepState.failedStepKey).toBe("continuity_repair");
    expect(progress.stepState.currentStepKey).toBe("continuity_repair");
    expect(progress.stepState.isComplete).toBe(false);
  });

  it("renders skipped, blocked, and rolled-back milestones as distinct states", () => {
    const stages = [
      { id: "state_packet", label: "准备上下文", state: "completed" as const },
      { id: "chapter_research", label: "章节研究", state: "skipped" as const },
      { id: "bridge", label: "章节桥接", state: "rolled_back" as const },
      { id: "plan", label: "章节规划", state: "blocked" as const },
    ];
    const state = stepIndicatorStateFromStages(stages);
    const markup = renderToStaticMarkup(
      <LocaleProvider locale="en">
        <StepIndicatorRow
          blockedStepKey={state.blockedStepKey}
          completedSteps={state.completedSteps}
          currentStepKey={state.currentStepKey}
          onStepClick={() => undefined}
          rolledBackSteps={state.rolledBackSteps}
          skippedSteps={state.skippedSteps}
          steps={RUN_CHAPTER_STEPS.slice(0, 4)}
        />
      </LocaleProvider>,
    );

    expect(state.currentStepKey).toBe("plan");
    expect(markup).toContain("step-dot is-skipped");
    expect(markup).toContain("step-dot is-rolled_back is-clickable");
    expect(markup).toContain("step-dot is-blocked");
    expect(markup).toContain("Chapter Research: Skipped");
    expect(markup).toContain("Chapter Bridge: Rolled back");
    expect(markup).toContain("Chapter Plan: Blocked");
  });

  it("repairs stale stage counters from the live Engine sequence", () => {
    const stages = INIT_LONG_STEPS.map((step, index) => ({
      id: step.key,
      label: step.label,
      state: index < 6 ? "completed" as const : index === 6 ? "active" as const : "pending" as const,
    }));
    const progress = taskProgressProjection({
      currentStepLabel: "叙事蓝图 · 7/13",
      isRunning: true,
      kind: "init_long",
      locale: "en",
      reportedProgress: 48,
      stages,
    });

    expect(progress.steps).toHaveLength(15);
    expect(progress.currentStepLabel).toBe("Narrative Blueprint · 7/15");
  });

  it("keeps the Engine percentage authoritative for parallel work", () => {
    const stages = INIT_LONG_STEPS.map((step, index) => ({
      id: step.key,
      label: step.label,
      state: index < 2 ? "completed" as const : index < 4 ? "active" as const : "pending" as const,
    }));
    const progress = taskProgressProjection({
      currentStepLabel: "要素选择",
      isRunning: true,
      kind: "init_long",
      locale: "zh",
      reportedProgress: 20,
      stages,
    });

    expect(progress.progressPercent).toBe(20);
    expect(progress.stepState.currentStepKey).toBe("plan_blueprint_elements");
    const html = renderToStaticMarkup(<StepIndicatorRow {...progress.stepState} steps={progress.steps} />);
    expect(html.match(/step-dot is-active/g)).toHaveLength(2);
    expect(html).toContain('aria-label="叙事蓝图: 待执行"');
  });

  it.each(["plan_blueprint_elements", "plan_blueprint_elements_starting"])(
    "assigns %s to only one milestone for active, failed and blocked states", (key) => {
      for (const prop of ["currentStepKey", "failedStepKey", "blockedStepKey"] as const) {
        const state = prop === "currentStepKey" ? "active" : prop === "failedStepKey" ? "failed" : "blocked";
        const html = renderToStaticMarkup(<StepIndicatorRow {...{ [prop]: key }} steps={INIT_LONG_STEPS} />);
        expect(html.match(new RegExp(`step-dot is-${state}`, "g"))).toHaveLength(1);
        expect(html).toContain('aria-label="叙事蓝图: 待执行"');
      }
      expect(localizePipelineStepLabel(key, "en", [...INIT_LONG_STEPS].reverse())).toBe("Element Selection");
    },
  );

  it("keeps all failed/skipped stages and does not substitute the wrong current label", () => {
    const progress = taskProgressProjection({
      kind: "run_chapter", locale: "zh", isRunning: true, reportedProgress: 42,
      currentStepLabel: "memory_updated",
      stages: [
        { id: "chapter_research", label: "研究", state: "failed" },
        { id: "alignment", label: "审读", state: "active" },
        { id: "humanize", label: "拟人", state: "skipped" },
      ],
    });
    expect(progress.currentStepLabel).toBe("质量审读");
    expect(progress.stepState.currentStepKey).toBe("alignment");
    const html = renderToStaticMarkup(<StepIndicatorRow {...progress.stepState} steps={progress.steps} />);
    expect(html).toContain('aria-label="章节研究: 已失败"');
    expect(html).toContain('aria-label="拟人化清理: 已跳过"');
  });

  it("does not invent an active step over a skipped-only snapshot", () => {
    const state = stepIndicatorStateFromStages([
      { id: "chapter_research", label: "章节研究", state: "skipped" },
      { id: "bridge", label: "桥接", state: "pending" },
    ], { currentStepKey: "chapter_research", steps: RUN_CHAPTER_STEPS });
    expect(state.currentStepKey).toBe("");
    expect(state.completedSteps.size).toBe(0);
    expect(state.stageStates?.get("chapter_research")).toBe("skipped");
  });
});
