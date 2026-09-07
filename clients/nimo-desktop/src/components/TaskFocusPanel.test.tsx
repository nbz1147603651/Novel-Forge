import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import type { JobView } from "@nimo/engine-contracts";

import type { TaskStreamState } from "../lib/task-stream";
import { TaskFocusPanel } from "./TaskFocusPanel";

function job(overrides: Partial<JobView> = {}): JobView {
  return {
    id: "job-1",
    label: "长篇立项 · 眠咒",
    projectId: "sleep-spell",
    state: "running",
    currentStep: "adjudicate_init_conflict_candidates_73_80",
    progressPercent: 62,
    detail: "",
    ...overrides,
  };
}

function stream(): TaskStreamState {
  return {
    taskId: "job-1",
    title: "长篇立项 · 眠咒",
    stepLabel: "adjudicate_init_conflict_candidates_73_80",
    stepId: "adjudicate_init_conflict_candidates_73_80",
    status: "streaming",
    jobState: "running",
    progressPercent: 62,
    events: [],
  };
}

describe("TaskFocusPanel", () => {
  it("prefers the engine stepLabel with batch detail over the raw step key", () => {
    const html = renderToStaticMarkup(
      <TaskFocusPanel
        job={job({ stepLabel: "冲突候选裁判 · 当前层：章节契约 · 批次 7 / 7 · 判定：通过" })}
        scope="workflow"
        stream={stream()}
        title="机杼关注"
      />,
    );

    expect(html).toContain("冲突候选裁判 · 当前层：章节契约 · 批次 7 / 7 · 判定：通过");
    expect(html).not.toContain("adjudicate_init_conflict_candidates_73_80");
  });

  it("shows batch progress once instead of duplicating it beside the title", () => {
    const html = renderToStaticMarkup(
      <TaskFocusPanel
        job={job({ stepLabel: "冲突候选裁判 · 当前层：章节契约 · 批次 7 / 7 · 判定：通过" })}
        scope="workflow"
        stream={stream()}
        title="机杼关注"
      />,
    );

    expect(html).not.toContain("task-focus-detail-badge");
    expect(html.match(/批次 7 \/ 7/g)).toHaveLength(1);
  });

  it("falls back to the raw step key when the engine omits stepLabel", () => {
    const html = renderToStaticMarkup(
      <TaskFocusPanel job={job()} scope="workflow" stream={stream()} title="机杼关注" />,
    );

    expect(html).toContain("adjudicate_init_conflict_candidates_73_80");
  });

  it("hides a step label when it repeats the task title", () => {
    const html = renderToStaticMarkup(
      <TaskFocusPanel
        job={job({ label: "一致性画像", stepLabel: "一致性画像" })}
        scope="workflow"
        stream={stream()}
      />,
    );

    expect(html.match(/一致性画像/g)).toHaveLength(1);
  });

  it("replaces raw event counts with merged output characters", () => {
    const html = renderToStaticMarkup(
      <TaskFocusPanel
        job={job()}
        scope="workflow"
        stream={{
          ...stream(),
          events: [
            { streamId: "s1", sequence: 1, kind: "delta", segment: "content", text: "灰瓦" },
            { streamId: "s1", sequence: 2, kind: "delta", segment: "content", text: "在雨里" },
          ],
        }}
      />,
    );

    expect(html).toContain("5 字输出");
    expect(html).toContain("灰瓦在雨里");
    expect(html).not.toContain("条流事件");
  });

  it("marks the preview as clickable when an expand handler exists", () => {
    const html = renderToStaticMarkup(
      <TaskFocusPanel
        job={job()}
        onExpand={() => undefined}
        scope="workflow"
        stream={{ ...stream(), events: [{ streamId: "s1", sequence: 1, kind: "delta", segment: "content", text: "沈岸握住了银手链。" }] }}
        title="机杼关注"
      />,
    );

    expect(html).toContain("task-focus-preview is-clickable");
  });

  it("shows a protected live JSON draft while structured output is still arriving", () => {
    const html = renderToStaticMarkup(
      <TaskFocusPanel
        job={job()}
        scope="workflow"
        stream={{
          ...stream(),
          summary: { outputKind: "json", outputCharacters: 18 },
          events: [{
            streamId: "s-json",
            sequence: 1,
            kind: "delta",
            segment: "content",
            outputKind: "json",
            text: '{"裁定":"等待补全',
          }],
        }}
      />,
    );

    expect(html).toContain("结构化结果生成中");
    expect(html).toContain("完成校验后展示字段");
    expect(html).toContain("等待补全");
    expect(html).toContain("草稿不会写入正式结果");
  });

  it("renders a paused validated checkpoint as a clipped result snapshot, not live generation", () => {
    const html = renderToStaticMarkup(
      <TaskFocusPanel
        job={job({
          state: "paused",
          currentStep: "plan_checkpoint",
          stepLabel: "方案确认 · 章节方案待确认",
          decisions: [{
            id: "approve",
            decisionId: "plan_checkpoint",
            label: "接受方案",
            description: "确认后进入正文生成。",
            requiresExplicitApproval: true,
            approvalVersion: "approval-v1",
          }],
        })}
        prominent
        scope="workflow"
        stream={{
          ...stream(),
          status: "paused",
          jobState: "paused",
          summary: { outputKind: "json", outputCharacters: 23_938 },
          events: [{
            streamId: "s-plan",
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
        }}
      />,
    );

    expect(html).toContain("结构化结果已校验");
    expect(html).toContain("完整长度 23,938 字符");
    expect(html).toContain("等待你的确认");
    expect(html).not.toContain("结构化结果生成中");
    expect(html).not.toContain("暂停自动跟随");
  });

  it("renders the quiet empty state without a job", () => {
    const html = renderToStaticMarkup(
      <TaskFocusPanel job={null} scope="workflow" stream={null} title="机杼关注" />,
    );

    expect(html).toContain("机杼暂静");
  });

  it("uses retained source in the detail reader while keeping cards compact", () => {
    const source = `{"开头":"保留详情起点","描述":"${"正文内容".repeat(700)}","末尾":"最新片段"`;
    const liveStream: TaskStreamState = {
      ...stream(),
      summary: { outputKind: "json" },
      events: [{ streamId: "s-json", sequence: 1, kind: "delta", segment: "content", outputKind: "json", text: source }],
    };
    const detail = renderToStaticMarkup(<TaskFocusPanel job={job()} prominent stream={liveStream} />);
    const card = renderToStaticMarkup(<TaskFocusPanel job={job()} stream={liveStream} />);
    expect(detail).toContain("保留详情起点");
    expect(detail).toContain("最新片段");
    expect(detail).toContain("暂停自动跟随");
    expect(card).not.toContain("保留详情起点");
    expect(card).toContain("显示最新 600 字符");

    const completed = renderToStaticMarkup(<TaskFocusPanel job={job({ state: "succeeded" })} prominent stream={{
      ...liveStream,
      status: "completed",
      events: [
        { ...liveStream.events[0]!, text: `${source}}` },
        {
          streamId: "s-json",
          sequence: 2,
          kind: "validation",
          segment: "system",
          outputKind: "json",
          validationStatus: "validated",
        },
      ],
    }} />);
    expect(completed).toContain("查看完整 JSON 源码");
    expect(completed).not.toContain("结构化结果尚未通过校验");
  });
});
