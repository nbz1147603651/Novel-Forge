import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import type { TaskStreamState } from "../lib/task-stream";
import { useThinkingFoldState } from "../lib/use-thinking-fold-state";
import { TaskStreamDetail } from "./TaskStreamDetail";
import { TaskFocusPanel } from "./TaskFocusPanel";

const streaming: TaskStreamState = {
  taskId: "task-1",
  title: "长篇立项",
  stepLabel: "init_web_research_start",
  stepId: "init_web_research_start",
  status: "streaming",
  jobState: "running",
  progressPercent: 47,
  summary: { model: "MiniMax-M3", outputKind: "text", outputCharacters: 8, totalTokens: 120 },
  events: [
    { streamId: "stream-1", sequence: 1, kind: "delta", segment: "content", text: "灰瓦" },
    { streamId: "stream-1", sequence: 2, kind: "delta", segment: "reasoning", text: "检查民俗。" },
  ],
};

function FoldAwareWrapper({ stream, taskId }: { readonly stream: TaskStreamState; readonly taskId: string }) {
  const reasoningKeys = stream.events
    .filter((event) => event.segment === "reasoning")
    .map((event) => `${event.streamId}:${event.sequence}`);
  const foldState = useThinkingFoldState(taskId, reasoningKeys);
  return <TaskStreamDetail foldState={foldState} stream={stream} />;
}

describe("TaskStreamDetail fold integration", () => {
  it("renders the per-segment details without showing the legacy inline toolbar", () => {
    const html = renderToStaticMarkup(<FoldAwareWrapper stream={streaming} taskId="task-1" />);
    expect(html).toContain("task-stream-reasoning");
    // The fold controls are now owned by the parent dialog so the body only
    // renders the per-segment details and the runtime summary.
    expect(html).not.toContain("全部折叠思考");
  });

  it("applies aria-expanded to reasoning details", () => {
    const html = renderToStaticMarkup(<FoldAwareWrapper stream={streaming} taskId="task-1" />);
    expect(html).toContain('aria-expanded="false"');
  });
});

describe("TaskFocusPanel foldable reasoning preview", () => {
  it("keeps prose visible and adds a secondary fold when the latest event is reasoning", () => {
    const reasoningLastStream: TaskStreamState = {
      ...streaming,
      events: [
        { streamId: "stream-1", sequence: 1, kind: "delta", segment: "content", text: "灰瓦" },
        { streamId: "stream-1", sequence: 2, kind: "delta", segment: "reasoning", text: "检查民俗，核对冲突候选。" },
      ],
    };
    const html = renderToStaticMarkup(
      <TaskFocusPanel
        job={{
          id: "task-1",
          label: "长篇立项",
          projectId: "p-1",
          state: "running",
          currentStep: "init_web_research_start",
          stepLabel: "冲突候选裁判",
          progressPercent: 47,
          detail: "",
        }}
        stream={reasoningLastStream}
      />,
    );
    expect(html).toContain("task-focus-preview-fold");
    expect(html).toContain("task-focus-preview");
    expect(html).toContain("思考中 ·");
    expect(html).toContain("最新推理片段");
    expect(html).toContain("检查民俗，核对冲突候选。");
    expect(html).toContain("灰瓦");
    expect(html).toMatch(/思考中 · \d+ 字/);
  });

  it("renders the prose preview (not the fold) when the latest event is content", () => {
    const contentLastStream: TaskStreamState = {
      ...streaming,
      events: [
        { streamId: "stream-1", sequence: 1, kind: "delta", segment: "reasoning", text: "检查民俗。" },
        { streamId: "stream-1", sequence: 2, kind: "delta", segment: "content", text: "灰瓦在雨里" },
      ],
    };
    const html = renderToStaticMarkup(
      <TaskFocusPanel
        job={{
          id: "task-1",
          label: "长篇立项",
          projectId: "p-1",
          state: "running",
          currentStep: "init_web_research_start",
          stepLabel: "冲突候选裁判",
          progressPercent: 47,
          detail: "",
        }}
        stream={contentLastStream}
      />,
    );
    expect(html).not.toContain("task-focus-preview-fold");
    expect(html).toContain("task-focus-preview");
  });

  it("defaults active reasoning to folded so it never displaces the prose", () => {
    const reasoningLastStream: TaskStreamState = {
      ...streaming,
      events: [
        { streamId: "stream-1", sequence: 1, kind: "delta", segment: "content", text: "灰瓦" },
        { streamId: "stream-1", sequence: 2, kind: "delta", segment: "reasoning", text: "检查民俗。" },
      ],
    };
    const html = renderToStaticMarkup(
      <TaskFocusPanel
        job={{
          id: "task-1",
          label: "长篇立项",
          projectId: "p-1",
          state: "running",
          currentStep: "init_web_research_start",
          stepLabel: "冲突候选裁判",
          progressPercent: 47,
          detail: "",
        }}
        stream={reasoningLastStream}
      />,
    );
    expect(html).not.toMatch(/<details class="task-focus-preview-fold"[^>]*open=/);
  });
});
