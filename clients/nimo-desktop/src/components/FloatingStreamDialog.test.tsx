import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

// Mirror the focus dialog setup: `OverlaySurface` calls `createPortal`,
// which has no real DOM target in this Node-only test environment.  Stub
// `OverlaySurface` to render its children directly so the rendered markup
// can be inspected.  We preserve `className` so anchor marker assertions
// can find `floating-stream-overlay is-anchored` in the output.
vi.mock("./OverlaySurface", () => ({
  OverlaySurface: ({
    children,
    className = "",
    style,
  }: {
    readonly children: React.ReactNode;
    readonly className?: string;
    readonly style?: React.CSSProperties;
  }) => (
    <div className={className} style={style}>
      {children}
    </div>
  ),
}));

import type { TaskStreamState } from "../lib/task-stream";
import { buildTaskStreamTranscript } from "../lib/task-stream-transcript";
import { useThinkingFoldState } from "../lib/use-thinking-fold-state";
import { computeAnchorOffset, FloatingStreamDialog, FloatingStreamDialogBody } from "./FloatingStreamDialog";

const streaming: TaskStreamState = {
  taskId: "task-floating-1",
  title: "长篇立项 · 章节续写",
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

const noopFoldState = {
  hasReasoning: true,
  preference: null,
  isOpen: () => false,
  toggle: () => undefined,
  foldAll: () => undefined,
  expandAll: () => undefined,
} as const;

describe("FloatingStreamDialog layout", () => {
  it("mounts the shared fold toolbar in the header so the pet and focus modules stay in sync", () => {
    const html = renderToStaticMarkup(<FloatingStreamDialog onClose={() => {}} stream={streaming} />);
    expect(html).toContain("思考折叠工具");
    expect(html).toContain("全部折叠思考");
    expect(html).toContain("全部展开思考");
  });

  it("renders the three stream tabs (运行轨迹 / 调用详情 / 原始事件) with the stream tab active by default", () => {
    const html = renderToStaticMarkup(<FloatingStreamDialog onClose={() => {}} stream={streaming} />);
    const labels = ["运行轨迹", "调用详情", "原始事件"];
    let cursor = 0;
    for (const label of labels) {
      const next = html.indexOf(label, cursor);
      expect(next).toBeGreaterThanOrEqual(0);
      cursor = next + label.length;
    }
    expect(html).toContain('aria-selected="true"');
  });

  it("hides the fold toolbar when the stream has no reasoning segments", () => {
    const plainStream: TaskStreamState = {
      ...streaming,
      events: [
        { streamId: "stream-1", sequence: 1, kind: "delta", segment: "content", text: "灰瓦" },
      ],
    };
    const html = renderToStaticMarkup(<FloatingStreamDialog onClose={() => {}} stream={plainStream} />);
    expect(html).not.toContain("思考折叠工具");
  });

  it("tolerates a null stream without crashing the fold controller", () => {
    const html = renderToStaticMarkup(<FloatingStreamDialog onClose={() => {}} stream={null} />);
    expect(html).toContain("运行轨迹");
    expect(html).not.toContain("思考折叠工具");
  });

  it("surfaces the task title in the header so the pet and focus modules keep the same title pattern", () => {
    const html = renderToStaticMarkup(<FloatingStreamDialog onClose={() => {}} stream={streaming} />);
    expect(html).toContain("长篇立项 · 章节续写");
    expect(html).not.toContain("实时流");
  });
});

describe("FloatingStreamDialogBody per-tab projection", () => {
  it("renders TaskStreamDetail for the 运行轨迹 tab", () => {
    const html = renderToStaticMarkup(
      <FloatingStreamDialogBody
        displayStepLabel="资料检索"
        foldState={noopFoldState}
        stream={streaming}
        tab="stream"
      />,
    );
    expect(html).toContain("task-stream-detail");
    expect(html).not.toContain("task-stream-runtime-summary");
    // Per-segment reasoning is rendered with the fold/expand semantics.
    expect(html).toContain("task-stream-reasoning");
  });

  it("renders TaskCallDetails for the 调用详情 tab", () => {
    const html = renderToStaticMarkup(
      <FloatingStreamDialogBody
        displayStepLabel="资料检索"
        foldState={noopFoldState}
        stream={streaming}
        tab="calls"
      />,
    );
    expect(html).toContain("task-call-detail");
    expect(html).toContain("模型调用全景");
    expect(html).not.toContain("task-stream-detail");
  });

  it("renders RawStreamEvents for the 原始事件 tab", () => {
    const html = renderToStaticMarkup(
      <FloatingStreamDialogBody
        displayStepLabel="资料检索"
        foldState={noopFoldState}
        stream={streaming}
        tab="events"
      />,
    );
    expect(html).toContain("floating-stream-events");
    expect(html).not.toContain("task-call-detail");
    expect(html).not.toContain("task-stream-detail");
  });

  it("still renders RawStreamEvents when the stream is null", () => {
    const html = renderToStaticMarkup(
      <FloatingStreamDialogBody
        displayStepLabel={undefined}
        foldState={noopFoldState}
        stream={null}
        tab="events"
      />,
    );
    expect(html).toContain("正在连接任务流。");
  });
});

describe("FloatingStreamDialog header foldAll/expandAll behavior", () => {
  // Helper: count <details class="task-stream-reasoning" open> elements.
  const reasoningOpenCount = (html: string): number =>
    (html.match(/<details[^>]*class="task-stream-reasoning"[^>]*open=/g) ?? []).length;

  it("starts the dialog with every reasoning segment folded (matches PySide6 baseline)", () => {
    const html = renderToStaticMarkup(<FloatingStreamDialog onClose={() => {}} stream={streaming} />);
    expect(reasoningOpenCount(html)).toBe(0);
  });

  it("opens every reasoning segment when the fold controller reports `expanded` preference (expandAll)", () => {
    const reasoningKeys = buildTaskStreamTranscript(streaming.events).items
      .filter((item) => item.kind === "reasoning")
      .map((item) => item.key);
    const expandedFoldState = {
      hasReasoning: reasoningKeys.length > 0,
      preference: "expanded" as const,
      isOpen: () => true,
      toggle: () => undefined,
      foldAll: () => undefined,
      expandAll: () => undefined,
    };
    const html = renderToStaticMarkup(
      <FloatingStreamDialogBody
        displayStepLabel="资料检索"
        foldState={expandedFoldState}
        stream={streaming}
        tab="stream"
      />,
    );
    expect(reasoningOpenCount(html)).toBe(reasoningKeys.length);
  });

  it("closes every reasoning segment when the fold controller reports `collapsed` preference (foldAll)", () => {
    const collapsedFoldState = {
      hasReasoning: true,
      preference: "collapsed" as const,
      isOpen: () => false,
      toggle: () => undefined,
      foldAll: () => undefined,
      expandAll: () => undefined,
    };
    const html = renderToStaticMarkup(
      <FloatingStreamDialogBody
        displayStepLabel="资料检索"
        foldState={collapsedFoldState}
        stream={streaming}
        tab="stream"
      />,
    );
    expect(reasoningOpenCount(html)).toBe(0);
  });

  it("wires the header toolbar to the live fold controller returned by useThinkingFoldState", () => {
    // Render the body through a real hook instance to confirm the toolbar
    // and the body stay in lockstep.  This is the closest a server-rendered
    // test can get to "click 全部展开思考 and watch the count change".
    function FoldBody() {
      const reasoningKeys = buildTaskStreamTranscript(streaming.events).items
        .filter((item) => item.kind === "reasoning")
        .map((item) => item.key);
      const foldState = useThinkingFoldState(streaming.taskId, reasoningKeys);
      return (
        <FloatingStreamDialogBody
          displayStepLabel="资料检索"
          foldState={foldState}
          stream={streaming}
          tab="stream"
        />
      );
    }
    const html = renderToStaticMarkup(<FoldBody />);
    // The hook starts every reasoning segment folded.
    expect(reasoningOpenCount(html)).toBe(0);
    expect(html).toContain("task-stream-reasoning");
  });
});

describe("computeAnchorOffset (pet-adjacent placement)", () => {
  it("keeps the dialog at the natural bottom-right when the anchor would not move it", () => {
    // Pet sitting exactly at the bottom-right of the viewport: the offset
    // collapses to roughly the difference between the anchor's expected
    // right edge and the natural dialog right edge, modulo the backdrop
    // padding.  We assert the math instead of the exact pixel.
    const offset = computeAnchorOffset(
      { left: 1200, top: 700, width: 60, height: 60 },
      1280,
      800,
    );
    expect(offset.x).toBeCloseTo(1200 + 60 - 900 - (1280 - 900 - 28), -1);
    expect(typeof offset.y).toBe("number");
  });

  it("clamps the horizontal offset so the dialog never goes past the left edge", () => {
    // Pet at the far left: dialog must still be visible at x = ANCHOR_GAP.
    const offset = computeAnchorOffset(
      { left: 0, top: 400, width: 30, height: 30 },
      1280,
      800,
    );
    // naturalX = 1280 - 900 - 28 = 352; minX = 12 - 352 = -340.
    // The desired x is 0 + 30 - 900 - 352 = -1222, clamped to -340.
    expect(offset.x).toBe(-340);
  });

  it("falls back to the offset of 0,0 when window is unavailable (SSR safety)", () => {
    const offset = computeAnchorOffset(
      { left: 500, top: 500, width: 50, height: 50 },
      0,
      0,
    );
    expect(offset).toEqual({ x: 0, y: 0 });
  });
});

describe("FloatingStreamDialog anchor prop", () => {
  it("emits the is-anchored marker class when an anchor is provided", () => {
    const html = renderToStaticMarkup(
      <FloatingStreamDialog
        anchor={{ left: 1100, top: 600, width: 80, height: 80 }}
        onClose={() => {}}
        stream={streaming}
      />,
    );
    expect(html).toContain("floating-stream-overlay is-anchored");
  });

  it("omits the marker class when no anchor is provided", () => {
    const html = renderToStaticMarkup(<FloatingStreamDialog onClose={() => {}} stream={streaming} />);
    expect(html).toContain("floating-stream-overlay");
    expect(html).not.toContain("is-anchored");
  });
});
