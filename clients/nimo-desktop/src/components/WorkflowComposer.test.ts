import { describe, expect, it } from "vitest";

import type { ProjectView } from "@nimo/engine-contracts";

import { resumableLongProject } from "./WorkflowComposer";

function project(overrides: Partial<ProjectView> = {}): ProjectView {
  return {
    id: "long-demo",
    title: "测试长篇",
    mode: "long",
    initResumeAvailable: false,
    status: "writing",
    statusLabel: "连载中",
    progressLabel: "2/20",
    progressPercent: 10,
    nextAction: "继续写作",
    updatedLabel: "刚刚",
    headline: "测试",
    genre: "悬疑",
    tone: "克制",
    completedChapters: 2,
    totalChapters: 20,
    ...overrides,
  };
}

describe("resumableLongProject", () => {
  it("uses the engine's initResumeAvailable contract instead of project status", () => {
    const resumable = project({ initResumeAvailable: true, status: "writing" });

    expect(resumableLongProject([resumable])).toBe(resumable);
  });

  it("does not treat an arbitrary planning project as resumable", () => {
    expect(resumableLongProject([project({ status: "planning" })])).toBeNull();
  });
});
