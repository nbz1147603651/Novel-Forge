/**
 * Project-level attention grouping for task observation UI.
 *
 * Mirrors PySide6 `ObservedAttentionGroup` — groups jobs by project so
 * the pet companion and task focus panel show project-level work instead
 * of every historical job as a top-level node.
 */
import type { JobView, TaskState } from "@nimo/engine-contracts";

export type TaskFlowOutcome =
  | "queued"
  | "running"
  | "paused"
  | "succeeded"
  | "failed"
  | "cancelled";

/** Derive a semantic outcome from the engine TaskState. */
export function taskFlowOutcome(state: TaskState): TaskFlowOutcome {
  switch (state) {
    case "queued": return "queued";
    case "running": return "running";
    case "paused": return "paused";
    case "succeeded": return "succeeded";
    case "failed": return "failed";
  }
}

export function isTerminalOutcome(outcome: TaskFlowOutcome): boolean {
  return outcome === "succeeded" || outcome === "failed" || outcome === "cancelled";
}

export function isActiveOutcome(outcome: TaskFlowOutcome): boolean {
  return outcome === "running" || outcome === "queued" || outcome === "paused";
}

export interface AttentionGroup {
  readonly key: string;
  readonly label: string;
  readonly primary: JobView;
  readonly states: readonly JobView[];
  readonly activeCount: number;
  readonly historyCount: number;
  readonly liveStreamCount: number;
  readonly decisionCount: number;
}

const ACTIVE_STATES: ReadonlySet<TaskState> = new Set(["running", "queued", "paused"]);

/**
 * Group a flat job list into project-level attention groups.
 *
 * The primary job for each group is the most recently active (running >
 * queued > paused > terminal). Terminal jobs are kept as history context
 * but do not compete for the primary slot unless no active job exists.
 */
export function groupJobsByAttention(jobs: readonly JobView[]): AttentionGroup[] {
  const byProject = new Map<string, JobView[]>();
  for (const job of jobs) {
    const existing = byProject.get(job.projectId);
    if (existing !== undefined) {
      existing.push(job);
    } else {
      byProject.set(job.projectId, [job]);
    }
  }

  const groups: AttentionGroup[] = [];
  for (const [projectId, projectJobs] of byProject) {
    const activeJobs = projectJobs.filter((job) => ACTIVE_STATES.has(job.state));
    const historyJobs = projectJobs.filter((job) => !ACTIVE_STATES.has(job.state));
    const decisionJobs = projectJobs.filter(
      (job) => job.decisions !== undefined && job.decisions.length > 0 && job.state === "paused",
    );

    // Primary selection: prefer running > queued > paused > latest terminal
    const statePriority: Record<TaskState, number> = { running: 3, queued: 2, paused: 1, succeeded: 0, failed: 0 };
    const sorted = [...projectJobs].sort(
      (a, b) => (statePriority[b.state] - statePriority[a.state]),
    );
    const primary = sorted[0];
    if (primary === undefined) continue;

    groups.push({
      key: projectId,
      label: primary.label || projectId,
      primary,
      states: projectJobs,
      activeCount: activeJobs.length,
      historyCount: historyJobs.length,
      liveStreamCount: activeJobs.filter((job) => job.state === "running").length,
      decisionCount: decisionJobs.length,
    });
  }

  // Sort groups: those with active decisions first, then by active count.
  groups.sort((a, b) => {
    if (a.decisionCount !== b.decisionCount) return b.decisionCount - a.decisionCount;
    if (a.activeCount !== b.activeCount) return b.activeCount - a.activeCount;
    return a.key.localeCompare(b.key);
  });

  return groups;
}

/**
 * Select the top N candidate jobs for pet bubble display.
 * Prioritizes running/decision jobs over terminal ones.
 */
export function candidatesForBubbles(
  jobs: readonly JobView[],
  limit: number = 2,
): JobView[] {
  const statePriority: Record<TaskState, number> = { running: 4, paused: 3, queued: 2, failed: 1, succeeded: 0 };
  return [...jobs]
    .sort((a, b) => statePriority[b.state] - statePriority[a.state])
    .slice(0, limit);
}

/** Whether any job in the list requires user attention (active or decision). */
export function hasActiveAttention(jobs: readonly JobView[]): boolean {
  return jobs.some((job) => ACTIVE_STATES.has(job.state));
}

/** Count of jobs that are currently active (running/queued/paused). */
export function activeAttentionCount(jobs: readonly JobView[]): number {
  return jobs.filter((job) => ACTIVE_STATES.has(job.state)).length;
}
