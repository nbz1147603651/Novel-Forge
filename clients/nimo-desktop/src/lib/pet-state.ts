/**
 * Pet companion state derivation.
 *
 * Mirrors PySide6 `TaskObservationStore.focus_for_scope` + companion state
 * mapping logic. Derives the pet's visual animation state from the current
 * job list and stream status.
 */
import type { JobView } from "@nimo/engine-contracts";

import {
  activeAttentionCount,
  candidatesForBubbles,
  hasActiveAttention,
} from "./task-attention-group";

/**
 * The 9 visual states supported by the pet atlas spritesheet.
 * Maps 1:1 to PySide6 CODEX_ROW_SPECS rows.
 */
export type PetVisualState =
  | "idle"
  | "running-right"
  | "running-left"
  | "waving"
  | "jumping"
  | "failed"
  | "waiting"
  | "running"
  | "review";

/** Atlas row specs matching PySide6 CODEX_ROW_SPECS. */
export const PET_ATLAS_ROW_SPECS: Record<PetVisualState, { row: number; frames: number; fps: number }> = {
  "idle": { row: 0, frames: 6, fps: 6 },
  "running-right": { row: 1, frames: 8, fps: 8 },
  "running-left": { row: 2, frames: 8, fps: 8 },
  "waving": { row: 3, frames: 4, fps: 6 },
  "jumping": { row: 4, frames: 5, fps: 7 },
  "failed": { row: 5, frames: 8, fps: 6 },
  "waiting": { row: 6, frames: 6, fps: 5 },
  "running": { row: 7, frames: 6, fps: 7 },
  "review": { row: 8, frames: 6, fps: 5 },
};

/** States that play once then auto-return to idle. */
export const NON_LOOPING_STATES: ReadonlySet<PetVisualState> = new Set(["waving", "jumping"]);

/** Idle animation cycle: (state, duration_in_ticks) pairs. */
export const IDLE_ANIMATION_CYCLE: readonly (readonly [PetVisualState, number])[] = [
  ["idle", 24],
  ["waving", 4],
  ["idle", 18],
  ["jumping", 5],
];

export const IDLE_CYCLE_TOTAL_TICKS = IDLE_ANIMATION_CYCLE.reduce(
  (sum, [, duration]) => sum + duration,
  0,
);

export interface PetStateDerivation {
  readonly visualState: PetVisualState;
  readonly stateLabel: string;
  readonly activeCount: number;
  readonly hasAttention: boolean;
  readonly bubbleJobs: readonly JobView[];
  readonly tokenUsageLabel: string;
}

const STATE_LABELS: Record<PetVisualState, string> = {
  "idle": "待命",
  "running-right": "执行中",
  "running-left": "执行中",
  "waving": "打招呼",
  "jumping": "开心",
  "failed": "出错了",
  "waiting": "等待中",
  "running": "执行中",
  "review": "审阅中",
};

/**
 * Derive the pet's visual state from the current job collection.
 *
 * Mapping (mirrors PySide6 FloatingTaskCompanion.refresh):
 * - has_active_decision → "decision" (mapped to "waiting" with attention)
 * - PAUSED → "waiting"
 * - QUEUED → "waiting"
 * - FAILED → "failed"
 * - RUNNING → "running"
 * - No active tasks → idle animation cycle
 */
export function derivePetState(
  jobs: readonly JobView[],
  idleTick: number = 0,
): PetStateDerivation {
  const attention = hasActiveAttention(jobs);
  const count = activeAttentionCount(jobs);
  const bubbles = candidatesForBubbles(jobs, 2);

  if (!attention) {
    // Idle animation cycle
    const tick = idleTick % IDLE_CYCLE_TOTAL_TICKS;
    let elapsed = 0;
    let idleState: PetVisualState = "idle";
    for (const [state, duration] of IDLE_ANIMATION_CYCLE) {
      elapsed += duration;
      if (tick < elapsed) {
        idleState = state;
        break;
      }
    }
    return {
      visualState: idleState,
      stateLabel: STATE_LABELS.idle,
      activeCount: 0,
      hasAttention: false,
      bubbleJobs: bubbles,
      tokenUsageLabel: "",
    };
  }

  // Find the primary focus job (first active one)
  const focusJob = jobs.find(
    (job) => job.state === "running" || job.state === "paused" || job.state === "queued",
  );

  let visualState: PetVisualState;
  if (focusJob === undefined) {
    visualState = "running";
  } else if (focusJob.decisions !== undefined && focusJob.decisions.length > 0 && focusJob.state === "paused") {
    visualState = "waiting"; // decision state
  } else if (focusJob.state === "paused") {
    visualState = "waiting";
  } else if (focusJob.state === "queued") {
    visualState = "waiting";
  } else if (focusJob.state === "failed") {
    visualState = "failed";
  } else {
    visualState = "running";
  }

  const label = count > 1 ? `${count} 项` : STATE_LABELS[visualState];

  return {
    visualState,
    stateLabel: label,
    activeCount: count,
    hasAttention: true,
    bubbleJobs: bubbles,
    tokenUsageLabel: "",
  };
}

/** Format a compact token count label (mirrors format_token_count). */
export function formatTokenCountCompact(count: number): string {
  if (count >= 1_000_000) return `${(count / 1_000_000).toFixed(1)}M`;
  if (count >= 1_000) return `${(count / 1_000).toFixed(1)}K`;
  return String(count);
}
