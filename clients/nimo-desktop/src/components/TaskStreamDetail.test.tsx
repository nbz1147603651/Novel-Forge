import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import type { TaskStreamState } from "../lib/task-stream";
import { TaskCallDetails, TaskStreamDetail } from "./TaskStreamDetail";

const stream: TaskStreamState = {
  taskId: "task-1",
  title: "长篇立项",
  stepLabel: "init_web_research_start",
  stepId: "init_web_research_start",
  status: "streaming",
  jobState: "running",
  progressPercent: 0,
  summary: { model: "MiniMax-M3", outputKind: "text", outputCharacters: 8, totalTokens: 120 },
  events: [
    { streamId: "stream-1", sequence: 1, kind: "delta", segment: "content", text: "灰瓦" },
    { streamId: "stream-1", sequence: 2, kind: "delta", segment: "content", text: "在雨里" },
    { streamId: "stream-1", sequence: 3, kind: "delta", segment: "reasoning", text: "检查民俗。" },
  ],
};

describe("TaskStreamDetail", () => {
  it("renders one clean content rail for contiguous output chunks", () => {
    const html = renderToStaticMarkup(<TaskStreamDetail showToolbar stream={stream} />);

    expect(html).toContain("灰瓦在雨里");
    expect(html.match(/task-stream-event is-content/g)).toHaveLength(1);
    expect(html).toContain("思考 · 5 字 · 1 个片段");
    expect(html).toContain("全部展开思考");
    expect(html).not.toContain("预览中，未校验，未写入磁盘。");
  });

  it("only renders an attention callout when output needs attention", () => {
    const html = renderToStaticMarkup(<TaskStreamDetail stream={{ ...stream, status: "failed" }} />);

    expect(html).toContain("task-stream-attention");
    expect(html).toContain("流式输出出错；失败片段不会写入磁盘。");
  });

  it("streams incomplete JSON as a protected source draft until validation", () => {
    const partialJsonStream: TaskStreamState = {
      ...stream,
      summary: { ...stream.summary, outputKind: "json" },
      events: [
        {
          streamId: "stream-partial",
          sequence: 1,
          kind: "delta",
          segment: "content",
          text: '{"街景":"骑楼、青石板巷、旧',
        },
      ],
    };

    const html = renderToStaticMarkup(<TaskStreamDetail stream={partialJsonStream} />);

    expect(html).toContain("结构化结果生成中");
    expect(html).toContain("骑楼、青石板巷、旧");
    expect(html).toContain("草稿不会写入正式结果");
  });

  it("renders completed JSON as fields after matching backend validation", () => {
    const completedJsonStream: TaskStreamState = {
      ...stream,
      status: "completed",
      summary: { ...stream.summary, outputKind: "json" },
      events: [
        { streamId: "stream-json", sequence: 1, kind: "delta", segment: "content", outputKind: "json", text: '{"裁定":"通过","冲突数":0}' },
        { streamId: "stream-json", sequence: 2, kind: "validation", segment: "system", outputKind: "json", validationStatus: "validated" },
      ],
    };

    const html = renderToStaticMarkup(<TaskStreamDetail stream={completedJsonStream} />);

    expect(html).toContain("结构化结果");
    expect(html).toContain("裁定");
    expect(html).toContain("通过");
  });

  it("reconciles settled and running JSON siblings into one trace browser", () => {
    const concurrentJsonStream: TaskStreamState = {
      ...stream,
      summary: { ...stream.summary, outputKind: "json" },
      events: [
        {
          streamId: "stream-done",
          sequence: 1,
          kind: "delta",
          segment: "content",
          outputKind: "json",
          text: '{"章节":1,"Claims":6}',
        },
        {
          streamId: "stream-done",
          sequence: 2,
          kind: "stream_end",
          segment: "system",
          outputKind: "json",
          validationStatus: "validated",
        },
        {
          streamId: "stream-running",
          sequence: 3,
          kind: "delta",
          segment: "content",
          outputKind: "json",
          text: '{"章节":2,"Claims":',
        },
      ],
    };

    const html = renderToStaticMarkup(<TaskStreamDetail stream={concurrentJsonStream} />);

    expect(html).toContain("批次运行轨迹");
    expect(html).toContain("选择子流 01 · 已完成");
    expect(html).toContain("选择子流 02 · 运行中");
    expect(html.match(/stream-structured-state is-pending/g)).toHaveLength(1);
    expect(html).toContain('{&quot;章节&quot;:2');
    expect(html).toContain("自动跟随");
    expect(html).not.toContain("task-trace-live-strip");
  });

  it("selects backend-rejected JSON for diagnosis before a still-running sibling", () => {
    const html = renderToStaticMarkup(<TaskStreamDetail stream={{
      ...stream,
      summary: { ...stream.summary, outputKind: "json" },
      events: [
        { streamId: "invalid", sequence: 1, kind: "delta", segment: "content", outputKind: "json", text: '{"claim_count":12,"cognitive_level":"confirmed"' },
        { streamId: "invalid", sequence: 2, kind: "validation", segment: "system", outputKind: "json", validationStatus: "failed", message: "重试已耗尽" },
        { streamId: "running", sequence: 3, kind: "delta", segment: "content", outputKind: "json", text: '{"claim_count":' },
      ],
    }} />);

    expect(html).toContain("结构化输出诊断");
    expect(html).toContain("最后可识别字段");
    expect(html).toContain("cognitive_level");
    expect(html).toContain("恢复路径");
    expect(html).toContain("重试已耗尽");
    expect(html).toContain("自动恢复已停止");
    expect(html).toContain("仍有 1 个子流生成或校验中");
  });

  it("does not claim legacy fragments will be repaired automatically", () => {
    const html = renderToStaticMarkup(<TaskStreamDetail stream={{
      ...stream,
      events: [
        { streamId: "legacy", sequence: 1, kind: "delta", segment: "content", outputKind: "json", text: '{"summary":' },
        { streamId: "legacy", sequence: 2, kind: "stream_end", segment: "system", outputKind: "json" },
      ],
    }} />);
    expect(html).toContain("待核验");
    expect(html).toContain("历史片段或回放缺失不等于模型输出失败");
    expect(html).not.toContain("结构化输出诊断");
    expect(html).not.toContain("等待修复或重试");
  });

  it("shows a validated but clipped preview without a false JSON alarm", () => {
    const html = renderToStaticMarkup(<TaskStreamDetail stream={{
      ...stream,
      events: [{ streamId: "clipped", sequence: 1, kind: "validation", segment: "content", outputKind: "json", text: '{"summary":"截取预览', textMode: "snapshot", textTruncated: true, textLength: 7000, validationStatus: "validated" }],
    }} />);
    expect(html).toContain("后端已校验通过");
    expect(html).not.toContain("结构化输出诊断");
    expect(html).not.toContain("stream-structured-state is-invalid");
  });

  it("starts reasoning collapsed, matching the focused PySide reader", () => {
    const html = renderToStaticMarkup(<TaskStreamDetail showToolbar stream={stream} />);

    expect(html).toContain("思考 · 5 字 · 1 个片段");
    expect(html).not.toContain('<details class="task-stream-reasoning" open="">');
  });

  it("uses the owning workflow label in the reader summary", () => {
    const html = renderToStaticMarkup(
      <TaskStreamDetail
        displayStepLabel="资料检索"
        stream={{ ...stream, stepLabel: "init_web_research_start" }}
      />,
    );

    expect(html).toContain("资料检索");
    expect(html).not.toContain("init_web_research_start");
  });

  it("uses the owning workflow label in the call inspection", () => {
    const html = renderToStaticMarkup(
      <TaskCallDetails
        displayStepLabel="资料检索"
        stream={{ ...stream, stepLabel: "init_web_research_start" }}
      />,
    );

    expect(html).toContain("资料检索");
    expect(html).not.toContain("init_web_research_start");
  });

  it("groups calls by node and compares input/output token usage", () => {
    const html = renderToStaticMarkup(
      <TaskCallDetails
        stream={{
          ...stream,
          calls: [
            {
              callId: "call-1",
              task: "PLAN_CHAPTER",
              taskLabel: "章节计划",
              provider: "openai",
              model: "gpt-test",
              status: "success",
              event: "api_stream_done",
              promptTokens: 900,
              completionTokens: 300,
              totalTokens: 1200,
              latencyMs: 2100,
            },
            {
              callId: "call-2",
              task: "WAVE_CHAPTER",
              taskLabel: "章节织波",
              provider: "deepseek",
              model: "deepseek-chat",
              status: "running",
              event: "api_stream_start",
            },
          ],
        }}
      />,
    );

    expect(html).toContain("模型调用全景");
    expect(html).toContain("章节计划");
    expect(html).toContain("章节织波");
    expect(html).toContain("输入");
    expect(html).toContain("输出");
    expect(html).toContain("1,200");
    expect(html).not.toContain("总 Token</dt><dd>0");
  });
});
