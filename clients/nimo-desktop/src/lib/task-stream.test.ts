import { describe, expect, it } from "vitest";

import type { TaskStreamView } from "@nimo/engine-contracts";

import {
  MAX_TASK_STREAM_EVENTS,
  createTaskStreamState,
  taskStreamReducer,
} from "./task-stream";

const fixture: TaskStreamView = {
  taskId: "task-1",
  title: "测试任务",
  stepLabel: "冲突候选裁判 · 当前层：章节契约 · 批次 3/7 · 判定：通过",
  stepId: "adjudicate_init_conflict_candidates_25_36",
  status: "streaming",
  progressPercent: 0,
  jobState: "running",
  summary: { attempt: 1, elapsedMs: 7_000, outputCharacters: 12, totalTokens: 340 },
  calls: [{
    callId: "call-1",
    task: "DRAFT_CHAPTER",
    taskLabel: "DRAFT 原稿",
    status: "success",
    event: "api_stream_done",
    totalTokens: 340,
  }],
  events: [{ streamId: "stream-1", sequence: 2, kind: "delta", segment: "content", text: "第二段" }, { streamId: "stream-1", sequence: 1, kind: "stream_start", segment: "system" }],
};

describe("task stream presentation state", () => {
  it("orders a replayed snapshot by sequence", () => {
    const state = createTaskStreamState(fixture);
    expect(state.events.map((event) => event.sequence)).toEqual([1, 2]);
    expect(state.summary).toEqual(fixture.summary);
    expect(state.calls).toEqual(fixture.calls);
    expect(state).toMatchObject({ jobState: "running", progressPercent: 0 });
  });

  it("carries the raw step id for phase detection and the Chinese label for display", () => {
    const state = createTaskStreamState(fixture);
    expect(state.stepId).toBe("adjudicate_init_conflict_candidates_25_36");
    expect(state.stepLabel).toBe("冲突候选裁判 · 当前层：章节契约 · 批次 3/7 · 判定：通过");
  });

  it("falls back to the step label when stepId is absent (legacy snapshot)", () => {
    const { stepId: _stepId, ...legacy } = fixture;
    const state = createTaskStreamState(legacy);
    expect(state.stepId).toBe(state.stepLabel);
  });

  it("clears the previous task before a new subscription receives its snapshot", () => {
    const state = createTaskStreamState(fixture);
    expect(taskStreamReducer(state, { type: "reset" })).toBeNull();
  });

  it("preserves the durable job error for inline task feedback", () => {
    const state = createTaskStreamState({
      ...fixture,
      jobState: "failed",
      status: "failed",
      error: { code: "tts_unavailable", message: "TTS 服务不可用", retryable: true },
    });
    expect(state.error?.message).toBe("TTS 服务不可用");
  });

  it("ignores duplicate events after a reconnect", () => {
    const state = createTaskStreamState(fixture);
    const next = taskStreamReducer(state, { type: "event", event: fixture.events[1]! });
    expect(next?.events).toHaveLength(2);
  });

  it("keeps workflow status authoritative when one model stream ends", () => {
    const state = createTaskStreamState(fixture);
    const next = taskStreamReducer(state, { type: "event", event: { streamId: "stream-1", sequence: 3, kind: "stream_end", segment: "system", message: "完成" } });
    expect(next?.status).toBe("streaming");
    expect(next?.events).toHaveLength(3);
  });

  it("bounds the event log to the most recent events", () => {
    const state = createTaskStreamState({ ...fixture, events: [] });
    const batch = Array.from({ length: MAX_TASK_STREAM_EVENTS + 50 }, (_, index) => ({
      streamId: "stream-1",
      sequence: index + 1,
      kind: "delta" as const,
      segment: "content" as const,
      text: `t${index}`,
    }));
    const next = taskStreamReducer(state, { type: "events", events: batch });
    expect(next?.events).toHaveLength(MAX_TASK_STREAM_EVENTS);
    expect(next?.events[0]?.sequence).toBe(51);
    expect(next?.events.at(-1)?.sequence).toBe(MAX_TASK_STREAM_EVENTS + 50);
  });

  it("carries engine-declared outputKind on events", () => {
    const state = createTaskStreamState(fixture);
    const next = taskStreamReducer(state, {
      type: "event",
      event: {
        streamId: "stream-1",
        sequence: 3,
        kind: "delta",
        segment: "content",
        text: "第三段",
        outputKind: "json",
      },
    });
    expect(next?.events.at(-1)?.outputKind).toBe("json");
  });
});
