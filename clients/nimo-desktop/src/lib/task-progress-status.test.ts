import { describe, expect, it } from "vitest";
import type { ChapterStudioView, JobView, WorkflowRunView } from "@nimo/engine-contracts";
import { taskProgressStatus } from "./task-progress-status";

const running: JobView = { id: "running", label: "立项", projectId: "book", state: "running",
  currentStep: "plan_blueprint", progressPercent: 0, detail: "" };
const run = { id: "running", currentStageLabel: "叙事蓝图", progressPercent: 43 } as WorkflowRunView;

describe("taskProgressStatus", () => {
  it("uses the matching task card snapshot, not the first historical job or its stale zero", () => {
    const jobs: JobView[] = [{ ...running, id: "finished", state: "succeeded", progressPercent: 100 }, running];
    expect(taskProgressStatus("workflow", jobs, [run], null)).toEqual({ label: "叙事蓝图", progressPercent: 43 });
  });
  it("uses the visible chapter activity even when a different task is first", () => {
    const chapter = { projectId: "chapter-book", activity: { state: "checkpoint", progressPercent: 93,
      currentStepLabel: "归档选择", taskId: "checkpoint" } } as ChapterStudioView;
    expect(taskProgressStatus("chapter_studio", [running], [run], chapter)).toEqual({ label: "归档选择", progressPercent: 93 });
  });
  it("does not show progress from a different project or an unrelated page", () => {
    const chapter = { projectId: "another-book", activity: { state: "idle" } } as ChapterStudioView;
    expect(taskProgressStatus("chapter_studio", [running], [run], chapter)).toBeNull();
    expect(taskProgressStatus("settings", [running], [run], null)).toBeNull();
  });
});
