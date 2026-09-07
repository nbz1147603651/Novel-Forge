import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import type { JobView } from "@nimo/engine-contracts";

import { TaskBubbleCard } from "./TaskBubbleCard";

function makeJob(overrides: Partial<JobView> = {}): JobView {
  return {
    id: "job-1",
    label: "长篇立项 · 眠咒",
    projectId: "p-1",
    state: "running",
    currentStep: "init_web_research_start",
    progressPercent: 47,
    detail: "",
    ...overrides,
  };
}

describe("TaskBubbleCard streaming indicator", () => {
  it("renders the streaming tag by default for running jobs", () => {
    const html = renderToStaticMarkup(<TaskBubbleCard job={makeJob()} />);
    expect(html).toContain("pet-bubble-card is-active is-streaming");
    expect(html).toContain("流式输出中");
    expect(html).toContain("pet-bubble-streaming-tag");
  });

  it("omits the streaming tag for non-running jobs even when the streaming prop is not set", () => {
    const html = renderToStaticMarkup(<TaskBubbleCard job={makeJob({ state: "succeeded", progressPercent: 100 })} />);
    expect(html).not.toContain("流式输出中");
    expect(html).not.toContain("pet-bubble-streaming-tag");
  });

  it("honors an explicit `streaming` override (true forces the indicator)", () => {
    const html = renderToStaticMarkup(
      <TaskBubbleCard job={makeJob({ state: "paused" })} streaming />,
    );
    expect(html).toContain("流式输出中");
  });

  it("honors an explicit `streaming={false}` override to suppress the indicator", () => {
    const html = renderToStaticMarkup(
      <TaskBubbleCard job={makeJob()} streaming={false} />,
    );
    expect(html).not.toContain("流式输出中");
  });
});
