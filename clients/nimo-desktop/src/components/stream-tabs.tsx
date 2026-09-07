import type { TaskStreamState } from "../lib/task-stream";

/** 任务观察对话框 tab（当前节点 / 运行轨迹 / 调用详情）。 */
export type ObservationTab = "current" | "stream" | "calls";

/** 浮动流式详情 tab（运行轨迹 / 调用详情 / 原始事件）。 */
export type FloatingStreamTab = "stream" | "calls" | "events";

export const OBSERVATION_TABS: readonly ObservationTab[] = [
  "current",
  "stream",
  "calls",
];

export const FLOATING_STREAM_TABS: readonly FloatingStreamTab[] = [
  "stream",
  "calls",
  "events",
];

/** 共享 tab 标签映射：两个任务观察对话框使用同一份文案。 */
export const STREAM_TAB_LABELS: Readonly<Record<string, string>> = {
  current: "当前节点",
  stream: "运行轨迹",
  calls: "调用详情",
  events: "原始事件",
};

/**
 * 原始事件只读视图：两个对话框共用同一渲染，避免各自实现一套。
 * 保留最近 500 条，按事件游标/序号排序展示。
 */
export function RawStreamEvents({ stream }: { readonly stream: TaskStreamState | null }) {
  if (stream === null) {
    return <pre className="floating-stream-events">正在连接任务流。</pre>;
  }
  const text = stream.events
    .slice(-500)
    .map(
      (event) =>
        `[${event.cursor ?? `${event.streamId}:${event.sequence}`}] ${event.kind} · ${event.segment}\n${event.text ?? event.message ?? ""}`,
    )
    .join("\n\n");
  return <pre className="floating-stream-events">{text}</pre>;
}
