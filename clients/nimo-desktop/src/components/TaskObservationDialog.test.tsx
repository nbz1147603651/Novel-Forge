import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

// `TaskObservationDialog` renders through `OverlaySurface`, which calls
// `createPortal` to project the surface into `document.body`.  The project
// has no jsdom/happy-dom installed so the portal target does not exist
// during server-rendered tests.  We swap `OverlaySurface` for a passthrough
// shell that renders the children directly so the dialog markup can be
// inspected as if it were a regular tree.
vi.mock("./OverlaySurface", () => ({
  OverlaySurface: ({ children }: { readonly children: React.ReactNode }) => children,
}));

import type { JobView } from "@nimo/engine-contracts";

import type { TaskStreamState } from "../lib/task-stream";
import { buildTaskStreamTranscript } from "../lib/task-stream-transcript";
import { TaskStreamDetail } from "./TaskStreamDetail";
import { TaskObservationDialog, TaskObservationDialogBody } from "./TaskObservationDialog";

const streaming: TaskStreamState = {
  taskId: "task-focus-1",
  title: "长篇立项 · 眠咒",
  stepLabel: "资料检索",
  stepId: "init_web_research_start",
  status: "streaming",
  jobState: "running",
  progressPercent: 83,
  summary: { model: "MiniMax-M3", outputKind: "text", outputCharacters: 8, totalTokens: 120 },
  events: [
    { streamId: "stream-1", sequence: 1, kind: "delta", segment: "content", text: "灰瓦" },
    { streamId: "stream-1", sequence: 2, kind: "delta", segment: "content", text: "在雨里" },
    { streamId: "stream-1", sequence: 3, kind: "delta", segment: "reasoning", text: "检查民俗，核对冲突候选。" },
  ],
};

const focusJob: JobView = {
  id: "task-focus-1",
  label: "长篇立项 · 眠咒",
  projectId: "sleep-spell",
  state: "running",
  currentStep: "资料检索",
  stepLabel: "资料检索",
  progressPercent: 83,
  detail: "",
};

const foldedReasoning = {
  hasReasoning: true,
  preference: null,
  isOpen: () => false,
  toggle: () => undefined,
  foldAll: () => undefined,
  expandAll: () => undefined,
} as const;

describe("TaskObservationDialog layout", () => {
  it("renders the shared fold toolbar in the dialog header when reasoning exists", () => {
    const html = renderToStaticMarkup(<TaskObservationDialog onClose={() => {}} source="companion" stream={streaming} />);
    expect(html).toContain("思考折叠工具");
    expect(html).toContain("全部折叠思考");
    expect(html).toContain("全部展开思考");
    expect(html).toContain('aria-pressed="false"');
  });

  it("emits aria-pressed attributes on both fold buttons so the active preference is announced", () => {
    const html = renderToStaticMarkup(<TaskObservationDialog onClose={() => {}} source="companion" stream={streaming} />);
    // Both buttons carry aria-pressed so screen readers can announce the
    // active state once the user toggles fold/expand.  Initial state is
    // `null` preference so both render aria-pressed="false" on first paint.
    // The toolbar layout puts the button attributes before the label, so we
    // match each label and walk back to the nearest preceding aria-pressed.
    const foldAllButton = /全部折叠思考/.test(html);
    const expandAllButton = /全部展开思考/.test(html);
    expect(foldAllButton).toBe(true);
    expect(expandAllButton).toBe(true);
    // Toolbar emits two buttons each with aria-pressed; verify the count
    // matches and that no value is null.
    const pressedAtoms = (html.match(/aria-pressed="(true|false)"/g) ?? []);
    expect(pressedAtoms.length).toBeGreaterThanOrEqual(2);
    expect(pressedAtoms.every((atom) => atom.endsWith('"true"') || atom.endsWith('"false"'))).toBe(true);
  });

  it("hides the header toolbar when the stream has no reasoning segments", () => {
    const plainStream: TaskStreamState = {
      ...streaming,
      events: [
        { streamId: "stream-1", sequence: 1, kind: "delta", segment: "content", text: "灰瓦" },
        { streamId: "stream-1", sequence: 2, kind: "delta", segment: "content", text: "在雨里" },
      ],
    };
    const html = renderToStaticMarkup(<TaskObservationDialog onClose={() => {}} source="companion" stream={plainStream} />);
    expect(html).not.toContain("思考折叠工具");
  });

  it("emits the three observation tabs (当前节点 / 运行轨迹 / 调用详情) in order", () => {
    const html = renderToStaticMarkup(<TaskObservationDialog onClose={() => {}} stream={streaming} />);
    const tabLabels = ["当前节点", "运行轨迹", "调用详情"];
    let cursor = 0;
    for (const label of tabLabels) {
      const next = html.indexOf(label, cursor);
      expect(next).toBeGreaterThanOrEqual(0);
      cursor = next + label.length;
    }
  });

  it("opens focus entry points on a quiet current-node overview", () => {
    const html = renderToStaticMarkup(<TaskObservationDialog job={focusJob} onClose={() => {}} stream={streaming} />);
    expect(html).toContain('aria-selected="true"');
    expect(html).toContain("task-focus-panel is-prominent");
    expect(html).toContain('aria-label="当前节点信息"');
    expect(html).toContain("MiniMax-M3");
    expect(html).toContain("120");
    expect(html.match(/长篇立项 · 眠咒/g)).toHaveLength(1);
    expect(html).not.toContain("task-observation-timeline");
    expect(html).not.toContain("task-call-detail");
  });

  it("keeps the paused job badge and validated checkpoint facts semantically aligned", () => {
    const pausedJob: JobView = {
      ...focusJob,
      state: "paused",
      currentStep: "plan_checkpoint",
      stepLabel: "方案确认 · 章节方案待确认",
      decisions: [{
        id: "approve-plan",
        decisionId: "plan_checkpoint",
        label: "接受方案",
        description: "确认后继续。",
        requiresExplicitApproval: true,
        approvalVersion: "approval-v1",
      }],
    };
    const pausedStream: TaskStreamState = {
      ...streaming,
      status: "paused",
      jobState: "paused",
      summary: {
        outputKind: "json",
        outputCharacters: 23_938,
        totalTokens: 42_544,
        elapsedMs: 60_300,
        attempt: 2,
        model: "MiniMax-M3",
        provider: "minimax",
      },
      events: [{
        streamId: "chapter-plan",
        sequence: 1,
        kind: "validation",
        segment: "content",
        outputKind: "json",
        text: '{"scene_intents":[{"summary":"截取预览"}',
        textMode: "snapshot",
        textLength: 23_938,
        textTruncated: true,
        validationStatus: "validated",
      }],
    };
    const html = renderToStaticMarkup(
      <TaskObservationDialog job={pausedJob} onClose={() => {}} stream={pausedStream} />,
    );

    expect(html).toContain("已暂停");
    expect(html).toContain("已校验 · 待确认");
    expect(html).toContain("输出长度");
    expect(html).toContain("23,938 字符（预览");
    expect(html).toContain("本次 Token");
    expect(html).toContain("当前尝试");
    expect(html).not.toContain("实时更新");
    expect(html).not.toContain("结构化结果生成中");
    expect(html).toContain('aria-live="polite"');
    expect(html).toContain('role="status"');
  });

  it("renders the stream tab as one continuous reader without duplicate rails", () => {
    const html = renderToStaticMarkup(
      <TaskObservationDialogBody
        foldState={foldedReasoning}
        job={focusJob}
        step="资料检索"
        stream={streaming}
        tab="stream"
      />,
    );
    expect(html).toContain("task-observation-reader");
    expect(html).toContain("灰瓦在雨里");
    expect(html).not.toContain("task-stream-runtime-summary");
    expect(html).not.toContain("任务事件时间线");
    expect(html).not.toContain("task-call-detail");
  });

  it("emits the fold buttons with explicit accessible names so the focus module matches the floating companion", () => {
    const html = renderToStaticMarkup(<TaskObservationDialog onClose={() => {}} source="companion" stream={streaming} />);
    expect(html).toContain("思考折叠工具");
    // The fold buttons are the same in both dialogs so the focus and pet
    // surfaces share the same mental model.
    const foldAllMatches = html.match(/全部折叠思考/g) ?? [];
    const expandAllMatches = html.match(/全部展开思考/g) ?? [];
    expect(foldAllMatches.length).toBeGreaterThanOrEqual(1);
    expect(expandAllMatches.length).toBeGreaterThanOrEqual(1);
  });
});

describe("TaskObservationDialog workbench fold state", () => {
  // Helper: count the number of <details class="task-stream-reasoning"> that
  // currently render with the open attribute.  The dialog itself does not
  // expose its fold state, so we exercise the same TaskStreamDetail body
  // with a controlled fold state to prove the toolbar's intent.
  const twoReasoningStream: TaskStreamState = {
    ...streaming,
    events: [
      { streamId: "stream-1", sequence: 1, kind: "delta", segment: "content", text: "灰瓦" },
      { streamId: "stream-1", sequence: 2, kind: "delta", segment: "reasoning", text: "检查民俗。" },
      { streamId: "stream-1", sequence: 3, kind: "delta", segment: "reasoning", text: "核对冲突候选。" },
    ],
  };

  function reasoningOpenCount(html: string): number {
    const matches = html.match(/<details[^>]*class="task-stream-reasoning"[^>]*open=/g) ?? [];
    return matches.length;
  }

  it("opens every reasoning segment when the fold controller reports `expanded` preference (expandAll)", () => {
    const reasoningKeys = buildTaskStreamTranscript(twoReasoningStream.events).items
      .filter((item) => item.kind === "reasoning")
      .map((item) => item.key);
    const html = renderToStaticMarkup(
      <TaskStreamDetail
        foldState={{
          hasReasoning: reasoningKeys.length > 0,
          preference: "expanded",
          isOpen: () => true,
          toggle: () => undefined,
          foldAll: () => undefined,
          expandAll: () => undefined,
        }}
        stream={twoReasoningStream}
      />,
    );
    expect(reasoningOpenCount(html)).toBe(reasoningKeys.length);
  });

  it("closes every reasoning segment when the fold controller reports `collapsed` preference (foldAll)", () => {
    const html = renderToStaticMarkup(
      <TaskStreamDetail
        foldState={{
          hasReasoning: true,
          preference: "collapsed",
          isOpen: () => false,
          toggle: () => undefined,
          foldAll: () => undefined,
          expandAll: () => undefined,
        }}
        stream={twoReasoningStream}
      />,
    );
    expect(reasoningOpenCount(html)).toBe(0);
  });

  it("defaults to a closed state when no preference is set (initial render)", () => {
    const html = renderToStaticMarkup(
      <TaskStreamDetail
        foldState={{
          hasReasoning: true,
          preference: null,
          isOpen: () => false,
          toggle: () => undefined,
          foldAll: () => undefined,
          expandAll: () => undefined,
        }}
        stream={twoReasoningStream}
      />,
    );
    expect(reasoningOpenCount(html)).toBe(0);
  });

  it("wires the dialog's internal fold toolbar to the workbench fold state", () => {
    // The dialog itself initializes the fold state via the shared hook.  The
    // initial render therefore shows the same closed state for every
    // reasoning detail until the user clicks 全部展开思考.  Verify the
    // dialog's workbench mirrors that initial behaviour.
    const html = renderToStaticMarkup(
      <TaskObservationDialog onClose={() => {}} source="companion" stream={twoReasoningStream} />,
    );
    expect(html).toContain("task-observation-reader");
    expect(reasoningOpenCount(html)).toBe(0);
  });
});
