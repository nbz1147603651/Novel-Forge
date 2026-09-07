/**
 * Presentation-only grouping for the Engine-owned `run_chapter` milestones.
 *
 * The canonical node list and labels remain `RUN_CHAPTER_STEPS`; this module
 * only assigns those stable keys to the six chapter-production phases.
 */
import type { Locale } from "../lib/i18n";
import type { PipelineStep, PipelineStepLabels } from "./StepIndicatorRow";

export interface ChapterWorkflowPhase {
  readonly id: "planning" | "generate" | "review" | "polish" | "humanize" | "finalize";
  /** Legacy Chinese label retained for stable snapshots and native parity. */
  readonly label: string;
  readonly labels: PipelineStepLabels;
  readonly stepKeys: readonly string[];
}

function workflowPhase(
  id: ChapterWorkflowPhase["id"],
  zh: string,
  en: string,
  stepKeys: readonly string[],
): ChapterWorkflowPhase {
  return { id, label: zh, labels: { zh, en }, stepKeys };
}

export const CHAPTER_WORKFLOW_PHASES: readonly ChapterWorkflowPhase[] = [
  workflowPhase(
    "planning",
    "准备与规划",
    "Planning & Context",
    ["state_packet", "chapter_research", "bridge", "plan"],
  ),
  workflowPhase("generate", "生成", "Generate", ["draft", "wave"]),
  workflowPhase(
    "review",
    "审读与修复",
    "Review & Repair",
    [
      "opening_guard",
      "alignment",
      "continuity_repair",
      "alignment_repair",
      "guard_review",
      "causal_repair",
      "reading_power_repair",
    ],
  ),
  workflowPhase("polish", "润色", "Polish", ["polish"]),
  workflowPhase("humanize", "拟人化", "Humanize", ["humanize"]),
  workflowPhase(
    "finalize",
    "归档",
    "Finalize",
    ["extract_canon", "persist", "memory_updated"],
  ),
];

/** Resolve phase copy in the current desktop language, never from Engine labels. */
export function chapterWorkflowPhaseLabel(
  phase: ChapterWorkflowPhase,
  locale: Locale,
): string {
  return phase.labels[locale];
}

/** Return a phase's Engine-known milestones without creating a second label registry. */
export function chapterWorkflowPhaseSteps(
  phase: ChapterWorkflowPhase,
  steps: readonly PipelineStep[],
): readonly PipelineStep[] {
  const aliases: Readonly<Record<string, ChapterWorkflowPhase["id"]>> = {
    plan_checkpoint: "planning",
    pre_alignment: "review",
    post_alignment: "polish",
    polish_start: "planning",
    guard_checkpoint: "finalize",
    post_guard_repair: "finalize",
    polish_reextract_canon: "finalize",
    evaluate: "finalize",
    volume_audit: "finalize",
  };
  return steps.filter((step) => {
    const owner = CHAPTER_WORKFLOW_PHASES.find((candidate) => candidate.stepKeys.includes(step.key))?.id
      ?? aliases[step.key]
      ?? "review";
    return owner === phase.id;
  });
}
