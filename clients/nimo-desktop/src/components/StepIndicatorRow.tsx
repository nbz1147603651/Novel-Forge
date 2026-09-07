import type { ReactNode } from "react";

import type { WorkflowStageView } from "@nimo/engine-contracts";

import { useLocale, type Locale } from "../lib/i18n";

/** A pipeline label rendered in the active desktop language. */
export interface PipelineStepLabels {
  readonly zh: string;
  readonly en: string;
}

/** Pipeline step definition for the indicator row. */
export interface PipelineStep {
  readonly key: string;
  /** Legacy Chinese label retained for existing engine-facing integrations. */
  readonly label: string;
  /** Complete locale mapping for the user-facing step label. */
  readonly labels: PipelineStepLabels;
  readonly isPrefix?: boolean;
}

/** Visual state of a step dot. */
export type StepState =
  | "pending"
  | "active"
  | "done"
  | "failed"
  | "skipped"
  | "blocked"
  | "rolled_back";

/** Shared visual state derived from an Engine workflow stage projection. */
export interface StepIndicatorState {
  /** Preserve every Engine state, including concurrent active/failed stages. */
  readonly stageStates?: ReadonlyMap<string, StepState>;
  readonly completedSteps: ReadonlySet<string>;
  readonly skippedSteps: ReadonlySet<string>;
  readonly rolledBackSteps: ReadonlySet<string>;
  readonly currentStepKey: string;
  readonly failedStepKey: string;
  readonly blockedStepKey: string;
  readonly isComplete: boolean;
}

const STEP_STATE_LABELS: Record<StepState, PipelineStepLabels> = {
  pending: { zh: "待执行", en: "Pending" },
  active: { zh: "执行中", en: "In progress" },
  done: { zh: "已完成", en: "Completed" },
  failed: { zh: "已失败", en: "Failed" },
  skipped: { zh: "已跳过", en: "Skipped" },
  blocked: { zh: "已阻断", en: "Blocked" },
  rolled_back: { zh: "已回滚", en: "Rolled back" },
};

export interface StepIndicatorRowProps {
  readonly stageStates?: ReadonlyMap<string, StepState>;
  /** Pipeline step definitions. */
  readonly steps: readonly PipelineStep[];
  /** Current step key (for active state). */
  readonly currentStepKey?: string;
  /** Set of completed step keys. */
  readonly completedSteps?: ReadonlySet<string>;
  readonly skippedSteps?: ReadonlySet<string>;
  readonly rolledBackSteps?: ReadonlySet<string>;
  /** Failed step key (if any). */
  readonly failedStepKey?: string;
  readonly blockedStepKey?: string;
  /** Whether the entire pipeline is done. */
  readonly isComplete?: boolean;
  /** Called when a completed step dot is clicked. */
  readonly onStepClick?: (index: number, step: PipelineStep) => void;
}

/**
 * Resolve one visible milestone against the shared Engine stage projection.
 *
 * Timeline, task card, and compact dot-row surfaces all use this instead of
 * reimplementing completed/active/failed precedence locally.
 */
export function workflowStepVisualState(
  step: PipelineStep,
  state: StepIndicatorState,
  steps: readonly PipelineStep[],
): StepState {
  const projected = state.stageStates?.get(step.key);
  if (projected !== undefined) return projected;
  if (state.rolledBackSteps.has(step.key)) return "rolled_back";
  if (state.skippedSteps.has(step.key)) return "skipped";
  if (workflowStepForKey(steps, state.blockedStepKey)?.key === step.key) return "blocked";
  if (workflowStepForKey(steps, state.failedStepKey)?.key === step.key) return "failed";
  if (
    state.completedSteps.has(step.key)
    || [...state.completedSteps].some((key) => workflowStepForKey(steps, key)?.key === step.key)
  ) {
    return "done";
  }
  if (state.isComplete) return "done";
  if (workflowStepForKey(steps, state.currentStepKey)?.key === step.key) return "active";
  return "pending";
}

function workflowStepForKey(steps: readonly PipelineStep[], key: string): PipelineStep | undefined {
  if (!key) return undefined;
  const exact = steps.find((step) => step.key === key);
  if (exact !== undefined) return exact;
  // A nested event has one owner: the most specific matching milestone.
  // In particular, blueprint element events never belong to the blueprint.
  return steps.reduce<PipelineStep | undefined>((owner, step) => (
    step.isPrefix && key.startsWith(step.key) && step.key.length > (owner?.key.length ?? 0)
      ? step : owner
  ), undefined);
}

/**
 * A minimal fallback for an in-flight card when an older Engine projection
 * has not yet populated its `stages` array.  `currentStageLabel` is still
 * authoritative enough to draw the same frontier as PySide6 instead of
 * leaving every dot pending.
 */
export interface StepIndicatorStageFallback {
  readonly currentStepKey?: string;
  readonly steps?: readonly PipelineStep[];
  /** Keep a live task visibly started while an older projection catches up. */
  readonly useFirstStepWhenUnknown?: boolean;
}

/**
 * Horizontal step-by-step progress indicator for pipeline jobs.
 * 1:1 React mirror of PySide6 `StepIndicatorRow` with enhanced web-native visuals.
 *
 * Shows N labelled milestones connected by thin horizontal lines.
 * Each dot transitions: pending → active → done / failed.
 * Completed dots show a checkmark and are clickable to view artifacts.
 */
export function StepIndicatorRow({
  steps,
  stageStates,
  currentStepKey,
  completedSteps = new Set(),
  skippedSteps = new Set(),
  rolledBackSteps = new Set(),
  failedStepKey,
  blockedStepKey,
  isComplete = false,
  onStepClick,
}: StepIndicatorRowProps): ReactNode {
  const locale = useLocale();
  const workflowState: StepIndicatorState = {
    ...(stageStates !== undefined ? { stageStates } : {}),
    completedSteps,
    skippedSteps,
    rolledBackSteps,
    currentStepKey: currentStepKey ?? "",
    failedStepKey: failedStepKey ?? "",
    blockedStepKey: blockedStepKey ?? "",
    isComplete,
  };

  const handleDotClick = (index: number, step: PipelineStep, state: StepState) => {
    if ((state === "done" || state === "rolled_back") && onStepClick) {
      onStepClick(index, step);
    }
  };

  if (steps.length === 0) return null;

  const compact = steps.length >= 9;

  // Pre-compute all states so connectors can look ahead/behind.
  const states = steps.map((step) => workflowStepVisualState(step, workflowState, steps));

  return (
    <div
      aria-label={locale === "zh" ? "流程进度" : "Workflow progress"}
      className={`step-indicator-row ${compact ? "is-compact" : ""}`}
      role="list"
    >
      {steps.map((step, index) => {
        const state: StepState = states[index] ?? "pending";
        const isClickable = (state === "done" || state === "rolled_back") && !!onStepClick;
        const label = pipelineStepLabel(step, locale);
        // Connector is "filled" when the step BEFORE it is done.
        const connectorDone = ["done", "skipped", "rolled_back"].includes(state);
        // Connector is "active" (animated) when the step AFTER it is active.
        const nextState = index < steps.length - 1 ? states[index + 1] : undefined;
        const connectorActive = nextState === "active";

        return (
          <div key={step.key} className="step-indicator-item" role="listitem">
            <div className="step-indicator-column">
              <button
                type="button"
                className={`step-dot is-${state}${isClickable ? " is-clickable" : ""}`}
                onClick={() => handleDotClick(index, step, state)}
                disabled={!isClickable}
                aria-label={`${label}: ${stateLabel(state, locale)}`}
                title={isClickable ? `${label} · ${locale === "zh" ? "点击查看产出" : "View outputs"}` : label}
              >
                {state === "done" && (
                  <svg className="step-dot-check" viewBox="0 0 12 12" fill="none" aria-hidden="true">
                    <path d="M2.5 6.5L5 9L9.5 3.5" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
                  </svg>
                )}
                {state === "active" && <span className="step-dot-pulse" aria-hidden="true" />}
                {state === "skipped" && <span aria-hidden="true">–</span>}
                {state === "blocked" && <span aria-hidden="true">!</span>}
                {state === "rolled_back" && <span aria-hidden="true">↶</span>}
              </button>
              <span className={`step-label is-${state}`}>{label}</span>
            </div>
            {index < steps.length - 1 && (
              <div className={`step-connector${connectorDone ? " is-done" : ""}${connectorActive ? " is-active" : ""}`} />
            )}
          </div>
        );
      })}
    </div>
  );
}

function stateLabel(state: StepState, locale: Locale): string {
  return STEP_STATE_LABELS[state][locale];
}

function pipelineStep(key: string, zh: string, en: string, isPrefix = false): PipelineStep {
  return {
    key,
    label: zh,
    labels: { zh, en },
    ...(isPrefix ? { isPrefix: true } : {}),
  };
}

/** Resolve a workflow step label for an explicit desktop locale. */
export function pipelineStepLabel(step: PipelineStep, locale: Locale): string {
  return step.labels[locale];
}

/**
 * Localize an engine-supplied label when it matches a known workflow step.
 * Suffixes such as " · 7/15" are retained for progress detail.
 */
export function localizePipelineStepLabel(
  label: string,
  locale: Locale,
  steps: readonly PipelineStep[],
): string {
  const owner = workflowStepForKey(steps, label);
  if (owner !== undefined) return pipelineStepLabel(owner, locale);
  for (const step of steps) {
    for (const source of [step.labels.zh, step.labels.en]) {
      if (label === source) return pipelineStepLabel(step, locale);
      const prefix = `${source} · `;
      if (label.startsWith(prefix)) {
        return `${pipelineStepLabel(step, locale)} · ${label.slice(prefix.length)}`;
      }
    }
  }
  return label;
}

/** Run-short pipeline steps (mirrors Python _SUMMARY_STEPS["run_short"]). */
export const RUN_SHORT_STEPS: readonly PipelineStep[] = [
  pipelineStep("spec", "规格确认", "Brief Confirmation", true),
  pipelineStep("chapter_research", "章节研究", "Chapter Research", true),
  pipelineStep("short_blueprint_elements", "要素选择", "Element Selection"),
  pipelineStep("short_blueprint", "叙事蓝图", "Narrative Blueprint"),
  pipelineStep("short_profile_style", "风格规范", "Style Profile"),
  pipelineStep("beats", "节拍生成", "Beat Generation"),
  pipelineStep("short_execution_plan", "执行方案", "Execution Plan"),
  pipelineStep("draft", "初稿", "Draft"),
  pipelineStep("edit_", "自适应修订", "Adaptive Revision", true),
  pipelineStep("short_completeness_check", "完整性检查", "Completeness Check"),
  pipelineStep("evaluate", "质量评估", "Quality Evaluation"),
  pipelineStep("creative_summary", "创作分析", "Creative Analysis"),
];

/**
 * Init-long pipeline steps (mirrors Python _SUMMARY_STEPS["init_long"]).
 * Hidden auxiliary steps (init_character_system, init_entity_registry,
 * init_entity_graph, creative_director_packet, coherence sub-steps, etc.)
 * are excluded per `_AUXILIARY_STEP_KEYS_BY_KIND` in jobs.py.
 */
export const INIT_LONG_STEPS: readonly PipelineStep[] = [
  pipelineStep("spec", "规格确认", "Brief Confirmation", true),
  pipelineStep("init_web_research", "资料检索", "Research", true),
  pipelineStep("init_story_bible", "世界观设定", "Story Bible", true),
  pipelineStep("plan_blueprint_elements", "要素选择", "Element Selection", true),
  pipelineStep("init_character_bible", "角色设定", "Character Bible", true),
  pipelineStep("profile_style", "风格与实体", "Style & Entities", true),
  pipelineStep("plan_blueprint", "叙事蓝图", "Narrative Blueprint", true),
  pipelineStep("derive_editorial_contract", "编辑契约", "Editorial Contract", true),
  pipelineStep("plan_chapter_design_matrix", "章节设计矩阵", "Chapter Design Matrix", true),
  pipelineStep("plan_outline", "章节大纲", "Chapter Outline", true),
  pipelineStep("init_narrative_contract", "叙事契约", "Narrative Contract", true),
  pipelineStep("plan_chapter_contracts", "章节契约", "Chapter Contracts", true),
  pipelineStep("init_claim_contract_coverage", "契约覆盖", "Contract Coverage", true),
  pipelineStep("init_readiness", "初始化准入", "Initialization Readiness", true),
  pipelineStep("canon_state", "规范初始化", "Canon Initialization", true),
];

/** Run-chapter pipeline steps (mirrors Python _SUMMARY_STEPS["run_chapter"]). */
export const RUN_CHAPTER_STEPS: readonly PipelineStep[] = [
  pipelineStep("state_packet", "准备上下文", "Prepare Context"),
  pipelineStep("chapter_research", "章节研究", "Chapter Research", true),
  pipelineStep("bridge", "章节桥接", "Chapter Bridge"),
  pipelineStep("plan", "章节规划", "Chapter Plan"),
  pipelineStep("draft", "DRAFT 草稿", "DRAFT Manuscript"),
  pipelineStep("wave", "WAVE 编织", "WAVE Weave"),
  pipelineStep("opening_guard", "开篇护栏", "Opening Guard"),
  pipelineStep("alignment", "质量审读", "Quality Review"),
  pipelineStep("continuity_repair", "连续性修复", "Continuity Repair"),
  pipelineStep("alignment_repair", "对齐修复", "Alignment Repair"),
  pipelineStep("guard_review", "护栏复核", "Guard Review"),
  pipelineStep("causal_repair", "因果修复", "Causal Repair"),
  pipelineStep("reading_power_repair", "追读力修复", "Reading-power Repair"),
  pipelineStep("polish", "文学精修", "Prose Polish"),
  pipelineStep("humanize", "拟人化清理", "Humanize Cleanup"),
  pipelineStep("extract_canon", "状态提取", "State Extraction"),
  pipelineStep("persist", "正文归档", "Archive Manuscript"),
  pipelineStep("memory_updated", "记忆更新", "Memory Update"),
];

/** Prepare-chapter pipeline steps (mirrors Python _SUMMARY_STEPS["prepare_chapter"]). */
export const PREPARE_CHAPTER_STEPS: readonly PipelineStep[] = [
  pipelineStep("state_packet", "章节上下文", "Chapter Context"),
  pipelineStep("bridge", "桥接与方案", "Bridge & Plan"),
  pipelineStep("plan_checkpoint", "方案确认", "Plan Confirmation"),
];

/** Resolve-chapter-checkpoint steps (mirrors Python _SUMMARY_STEPS["resolve_chapter_checkpoint"]). */
export const RESOLVE_CHECKPOINT_STEPS: readonly PipelineStep[] = [
  pipelineStep("plan_checkpoint", "方案确认", "Plan Confirmation"),
  pipelineStep("draft", "初稿成章", "Draft & Weave"),
  pipelineStep("pre_alignment", "质量检查", "Quality Check"),
  pipelineStep("continuity_repair", "连续性修复", "Continuity Repair"),
  pipelineStep("alignment_repair", "对齐修复", "Alignment Repair"),
  pipelineStep("post_alignment", "文本精修", "Prose Polish"),
  pipelineStep("guard_checkpoint", "归档选择", "Archive Decision"),
];

/** Resolve-chapter-checkpoint-finalize steps. */
export const RESOLVE_FINALIZE_STEPS: readonly PipelineStep[] = [
  pipelineStep("guard_checkpoint", "归档选择", "Archive Decision"),
  pipelineStep("post_guard_repair", "归档前修复", "Pre-archive Repair"),
  pipelineStep("polish_reextract_canon", "状态提取", "State Extraction"),
  pipelineStep("persist", "正文落盘", "Persist Manuscript"),
  pipelineStep("evaluate", "质量评估", "Quality Evaluation"),
  pipelineStep("volume_audit", "卷末审计", "Volume Audit"),
  pipelineStep("memory_updated", "记忆更新", "Memory Update"),
];

/** Polish-chapter steps. */
export const POLISH_CHAPTER_STEPS: readonly PipelineStep[] = [
  pipelineStep("polish_start", "准备精修", "Prepare Polish"),
  pipelineStep("polish", "精修与复查", "Polish & Review"),
];

/**
 * All visible workflow milestones.  Detail surfaces do not always receive a
 * run kind, so they use this same vocabulary instead of maintaining another
 * set of raw-engine label substitutions.
 */
const ALL_WORKFLOW_PIPELINE_STEPS: readonly PipelineStep[] = [
  ...RUN_SHORT_STEPS,
  ...INIT_LONG_STEPS,
  ...RUN_CHAPTER_STEPS,
  ...PREPARE_CHAPTER_STEPS,
  ...RESOLVE_CHECKPOINT_STEPS,
  ...RESOLVE_FINALIZE_STEPS,
  ...POLISH_CHAPTER_STEPS,
];

const PIPELINE_STEP_BY_KEY = new Map(
  ALL_WORKFLOW_PIPELINE_STEPS.map((step) => [step.key, step]),
);

/** Localize a visible pipeline label when the calling surface lacks a run kind. */
export function localizePipelineStepAcrossKinds(label: string, locale: Locale): string {
  return localizePipelineStepLabel(label, locale, ALL_WORKFLOW_PIPELINE_STEPS);
}

/** True when a value is still an implementation-oriented pipeline identifier. */
export function isRawPipelineStepLabel(label: string): boolean {
  return /^[a-z0-9]+(?:_[a-z0-9]+)+$/i.test(label.trim());
}

/**
 * Prefer the Engine/PySide stage sequence once it is available.  The static
 * list only supplies labels before a task exists and translations for known
 * ids, so the web row cannot drift when the native pipeline gains a milestone.
 */
function projectionStepsForStages(
  kind: string,
  stages: readonly WorkflowStageView[],
): readonly PipelineStep[] {
  if (stages.length === 0) return stepsForJobKind(kind);
  // Many workflow kinds intentionally reuse implementation keys such as
  // `state_packet`, `draft`, and `persist`.  Labels belong to the selected
  // workflow's contract first; falling straight through to the global lookup
  // lets a later array silently overwrite chapter-specific copy.
  const kindStepByKey = new Map(
    stepsForJobKind(kind).map((step) => [step.key, step]),
  );
  return stages.map((stage) => {
    const knownStep = kindStepByKey.get(stage.id) ?? PIPELINE_STEP_BY_KEY.get(stage.id);
    if (knownStep !== undefined) return knownStep;
    const engineLabel = stage.label.trim() || stage.id;
    return pipelineStep(stage.id, engineLabel, engineLabel);
  });
}

/**
 * Derive StepIndicatorRow props from engine stage data.
 *
 * Single shared source for mapping `WorkflowStageView[]` to
 * `completedSteps` / `currentStepKey` / `failedStepKey` / `isComplete`,
 * so every surface (short form, long-init form, chapter panel, workflow
 * cards) renders the same step states from the same stage stream.
 */
export function stepIndicatorStateFromStages(
  stages: readonly WorkflowStageView[],
  fallback?: StepIndicatorStageFallback,
): StepIndicatorState {
  const stageStates = new Map<string, StepState>(stages.map((stage) => [
    stage.id, stage.state === "completed" ? "done" : stage.state,
  ]));
  const completedSteps = new Set(
    stages.filter((stage) => stage.state === "completed").map((stage) => stage.id),
  );
  const skippedSteps = new Set(
    stages.filter((stage) => stage.state === "skipped").map((stage) => stage.id),
  );
  const rolledBackSteps = new Set(
    stages.filter((stage) => stage.state === "rolled_back").map((stage) => stage.id),
  );
  const failedStepKey = stages.find((stage) => stage.state === "failed")?.id ?? "";
  const blockedStepKey = stages.find((stage) => stage.state === "blocked")?.id ?? "";
  const preferredIndex = findFallbackStepIndex(fallback?.steps ?? [], fallback?.currentStepKey ?? "");
  const preferredKey = fallback?.steps?.[preferredIndex]?.key;
  const currentStepKey = stages.find((stage) => stage.state === "active" && stage.id === preferredKey)?.id
    ?? stages.find((stage) => stage.state === "active")?.id
    ?? (blockedStepKey || failedStepKey);
  const isComplete = stages.length > 0 && stages.every(
    (stage) => ["completed", "skipped", "rolled_back"].includes(stage.state),
  );

  // A populated stage stream always wins.  The fallback only covers the
  // transient/legacy shape shown in the task card screenshot: a useful raw
  // current step but an all-pending (or absent) stages list.
  if (stages.some((stage) => stage.state !== "pending") || !fallback?.currentStepKey) {
    return {
      stageStates,
      blockedStepKey,
      completedSteps,
      currentStepKey,
      failedStepKey,
      isComplete,
      rolledBackSteps,
      skippedSteps,
    };
  }

  const steps = fallback.steps ?? [];
  const frontier = findFallbackStepIndex(steps, fallback.currentStepKey);
  if (frontier < 0 && !fallback.useFirstStepWhenUnknown) {
    return {
      blockedStepKey: "",
      completedSteps,
      currentStepKey,
      failedStepKey: "",
      isComplete,
      rolledBackSteps,
      skippedSteps,
    };
  }
  const safeFrontier = frontier < 0 ? 0 : frontier;
  if (steps.length === 0) {
    return {
      blockedStepKey: "",
      completedSteps,
      currentStepKey,
      failedStepKey: "",
      isComplete,
      rolledBackSteps,
      skippedSteps,
    };
  }

  return {
    completedSteps: new Set(steps.slice(0, safeFrontier).map((step) => step.key)),
    skippedSteps,
    rolledBackSteps,
    currentStepKey: steps[safeFrontier]?.key ?? "",
    failedStepKey: "",
    blockedStepKey: "",
    isComplete: false,
  };
}

function findFallbackStepIndex(steps: readonly PipelineStep[], currentStep: string): number {
  const current = currentStep.trim();
  if (!current) return -1;
  const owner = workflowStepForKey(steps, current);
  if (owner !== undefined) return steps.indexOf(owner);

  return steps.findIndex((step) => {
    return [step.labels.zh, step.labels.en].some(
      (label) => current === label || current.startsWith(`${label} · `),
    );
  });
}

/** A task-flow projection shared by 机杼 cards, task focus, and 章台. */
export interface TaskProgressProjection {
  readonly currentStepLabel: string;
  readonly progressPercent: number;
  readonly stepState: ReturnType<typeof stepIndicatorStateFromStages>;
  readonly steps: readonly PipelineStep[];
}

export interface TaskProgressProjectionInput {
  readonly currentStepLabel: string;
  readonly isRunning: boolean;
  readonly kind: string;
  readonly locale: Locale;
  readonly reportedProgress: number | null | undefined;
  readonly stages: readonly WorkflowStageView[];
}

/**
 * Convert an Engine task projection into one consistent presentation model.
 *
 * The Engine remains authoritative for completed stages and exact progress.
 * Only legacy snapshots lacking stages use the current visible label as a
 * fallback frontier. Populated Engine snapshots are never recalculated here.
 */
export function taskProgressProjection({
  currentStepLabel,
  isRunning,
  kind,
  locale,
  reportedProgress,
  stages,
}: TaskProgressProjectionInput): TaskProgressProjection {
  const steps = projectionStepsForStages(kind, stages);
  const stepState = stepIndicatorStateFromStages(stages, {
    currentStepKey: currentStepLabel,
    steps,
    useFirstStepWhenUnknown: isRunning,
  });
  const fallbackStep = steps.find((step) => step.key === stepState.currentStepKey);
  const localizedLabel = localizePipelineStepLabel(currentStepLabel, locale, steps);
  const rawEngineKey = isRawPipelineStepLabel(localizedLabel);
  const hasStagePositionSuffix = / · \d+\/\d+$/.test(currentStepLabel);
  const activeIndex = steps.findIndex((step) => step.key === stepState.currentStepKey);
  const activeStep = activeIndex < 0 ? undefined : steps[activeIndex];
  const activeLabel = activeStep === undefined ? "" : pipelineStepLabel(activeStep, locale);
  const stableCurrentStepLabel = activeLabel && hasStagePositionSuffix
    ? `${activeLabel} · ${activeIndex + 1}/${steps.length}`
    : "";

  return {
    steps,
    stepState,
    progressPercent: displayTaskProgressPercent(
      reportedProgress,
      steps,
      stepState.currentStepKey,
      isRunning,
      stages.length > 0,
    ),
    currentStepLabel: stableCurrentStepLabel || activeLabel || (!rawEngineKey && localizedLabel
      ? localizedLabel
      : (fallbackStep ? pipelineStepLabel(fallbackStep, locale) : "")),
  };
}

function displayTaskProgressPercent(
  reportedProgress: number | null | undefined,
  steps: readonly PipelineStep[],
  currentStepKey: string,
  isRunning: boolean,
  hasEngineStages: boolean,
): number {
  const normalized = typeof reportedProgress === "number" && Number.isFinite(reportedProgress)
    ? Math.max(0, Math.min(100, reportedProgress))
    : 0;
  // Progress and stages arrive in one snapshot. Never overwrite its progress
  // with a second frontier calculation (parallel stages are not a linear index).
  if ((hasEngineStages && typeof reportedProgress === "number" && Number.isFinite(reportedProgress))
    || !isRunning || !currentStepKey || steps.length === 0) {
    return normalized;
  }

  const currentIndex = steps.findIndex((step) => step.key === currentStepKey);
  if (currentIndex < 0) return normalized;

  // Older Engines may provide only a current label, with no stage projection.
  const visibleFrontier = Math.round(((currentIndex + 0.5) / steps.length) * 100);
  return Math.max(normalized, visibleFrontier);
}

/**
 * Resolve the step definitions for a given job kind.
 * Returns the matching step array or an empty array if unknown.
 */
export function stepsForJobKind(kind: string): readonly PipelineStep[] {
  switch (kind) {
    case "init_long": return INIT_LONG_STEPS;
    case "run_short": return RUN_SHORT_STEPS;
    case "run_chapter": return RUN_CHAPTER_STEPS;
    case "prepare_chapter": return PREPARE_CHAPTER_STEPS;
    case "resolve_chapter_checkpoint": return RESOLVE_CHECKPOINT_STEPS;
    case "resolve_chapter_checkpoint_finalize": return RESOLVE_FINALIZE_STEPS;
    case "polish_chapter": return POLISH_CHAPTER_STEPS;
    default: return [];
  }
}
