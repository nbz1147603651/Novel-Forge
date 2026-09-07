import type { ChapterStudioView, JobView, PageId, WorkflowRunView } from "@nimo/engine-contracts";

/** Match task identity before choosing a surface's progress snapshot. */
export function taskProgressStatus(
  page: PageId,
  jobs: readonly JobView[],
  runs: readonly WorkflowRunView[],
  chapter: ChapterStudioView | null,
): { readonly progressPercent: number; readonly label: string } | null {
  if (page !== "workflow" && page !== "chapter_studio") return null;
  if (page === "chapter_studio" && chapter?.activity.state !== "idle"
    && chapter?.activity.progressPercent != null) {
    return {
      progressPercent: chapter.activity.progressPercent,
      label: chapter.activity.currentStepLabel,
    };
  }
  const job = jobs.find((candidate) => (
    ["running", "paused", "queued"].includes(candidate.state)
    && (page !== "chapter_studio" || candidate.projectId === chapter?.projectId)
    && (page !== "workflow" || runs.some((run) => run.id === candidate.id))
  ));
  if (job === undefined) return null;
  const run = runs.find((candidate) => candidate.id === job.id);
  return {
    progressPercent: run?.progressPercent ?? job.progressPercent,
    label: run?.currentStageLabel || job.stepLabel || job.currentStep,
  };
}
