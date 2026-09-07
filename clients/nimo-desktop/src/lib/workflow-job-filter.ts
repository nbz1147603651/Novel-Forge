/**
 * Workflow job filtering utilities — mirrors PySide6 page.py _is_visible_task_flow_job.
 *
 * PySide6 source (workflow/page.py):
 * - Filters out test projects (is_generated_test_project_id)
 * - Filters out "dlq" project
 * - Shows jobs for visible projects or running/queued jobs
 */

import type { WorkflowRunView } from "@nimo/engine-contracts";

import { workflowRunProjectId } from "./workflow-run-session";
import { isActiveWorkflowRun } from "./workflow-run-state";

/**
 * Test project ID patterns that should be hidden from the task flow.
 * Mirrors Python is_generated_test_project_id().
 */
const TEST_PROJECT_PATTERNS = [
  /^test-/i,
  /^tmp-/i,
  /^temp-/i,
  /^demo-/i,
  /^sample-/i,
  /^example-/i,
  /^mock-/i,
  /^fixture-/i,
  /^__test__/i,
  /^__mock__/i,
];

/**
 * Check if a project ID is a generated test project that should be hidden.
 */
export function isGeneratedTestProjectId(projectId: string): boolean {
  const normalized = projectId.trim().toLowerCase();
  if (!normalized) return false;
  if (normalized === "dlq") return true;
  return TEST_PROJECT_PATTERNS.some((pattern) => pattern.test(normalized));
}

/**
 * Filter workflow runs to only show visible jobs.
 * Mirrors PySide6 _is_visible_task_flow_job logic:
 * - Always show jobs without a project ID
 * - Hide test projects and "dlq"
 * - Show running/queued jobs regardless of project visibility
 */
export function filterVisibleRuns(
  runs: readonly WorkflowRunView[],
  visibleProjectIds?: ReadonlySet<string>,
): readonly WorkflowRunView[] {
  return runs.filter((run) => {
    const projectId = workflowRunProjectId(run);

    // Always show jobs without a project
    if (!projectId) return true;

    // Hide test projects and dlq
    if (isGeneratedTestProjectId(projectId)) return false;

    // Always show running/queued jobs
    if (isActiveWorkflowRun(run)) return true;

    // If we have a visibility set, check against it
    if (visibleProjectIds && !visibleProjectIds.has(projectId)) {
      return false;
    }

    return true;
  });
}

/**
 * Maximum number of rendered task cards (mirrors PySide6 _MAX_RENDERED_TASKS = 24).
 */
export const MAX_RENDERED_TASKS = 24;

/**
 * Limit the number of rendered runs to prevent performance issues.
 */
export function limitRenderedRuns(runs: readonly WorkflowRunView[]): readonly WorkflowRunView[] {
  return runs.slice(0, MAX_RENDERED_TASKS);
}
