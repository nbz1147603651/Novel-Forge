/**
 * Unit tests for prepare-chapter-session pure state machine.
 *
 * These tests run in plain Vitest (Node environment) without jsdom or
 * @testing-library/react, validating all state transitions deterministically.
 */

import { describe, expect, it } from "vitest";

import type { TaskStreamView } from "@nimo/engine-contracts";

import {
  INITIAL_PREPARE_STATE,
  prepareSessionReducer,
  type PrepareSessionState,
} from "./prepare-chapter-session";

// ── Helpers ───────────────────────────────────────────────────────────────────

function makeStream(overrides: Partial<TaskStreamView> = {}): TaskStreamView {
  return {
    taskId: "task-1",
    title: "章节准备",
    stepLabel: "生成中",
    status: "streaming",
    progressPercent: 0,
    jobState: "running",
    events: [],
    ...overrides,
  };
}

function runningState(overrides: Partial<PrepareSessionState> = {}): PrepareSessionState {
  return {
    ...INITIAL_PREPARE_STATE,
    phase: "running",
    taskId: "task-1",
    message: "已提交",
    ...overrides,
  };
}

// ── Tests ─────────────────────────────────────────────────────────────────────

describe("prepareSessionReducer", () => {
  it("SUBMIT transitions from idle to submitting", () => {
    const next = prepareSessionReducer(INITIAL_PREPARE_STATE, {
      type: "SUBMIT",
      projectId: "proj-1",
      chapterNumber: 3,
    });
    expect(next.phase).toBe("submitting");
    expect(next.message).toContain("第 3 章");
    expect(next.taskId).toBeNull();
    expect(next.progressPercent).toBe(0);
  });

  it("ACCEPTED transitions to running with taskId", () => {
    const submitting: PrepareSessionState = {
      ...INITIAL_PREPARE_STATE,
      phase: "submitting",
      message: "正在提交…",
    };
    const next = prepareSessionReducer(submitting, {
      type: "ACCEPTED",
      taskId: "task-42",
      message: "章节 3 准备任务已提交",
    });
    expect(next.phase).toBe("running");
    expect(next.taskId).toBe("task-42");
    expect(next.message).toBe("章节 3 准备任务已提交");
    expect(next.progressPercent).toBe(0);
  });

  it("REJECTED transitions to failed with error message", () => {
    const submitting: PrepareSessionState = {
      ...INITIAL_PREPARE_STATE,
      phase: "submitting",
    };
    const next = prepareSessionReducer(submitting, {
      type: "REJECTED",
      message: "项目不存在",
    });
    expect(next.phase).toBe("failed");
    expect(next.error).toBe("项目不存在");
  });

  it("ALREADY_RUNNING transitions to running", () => {
    const submitting: PrepareSessionState = {
      ...INITIAL_PREPARE_STATE,
      phase: "submitting",
    };
    const next = prepareSessionReducer(submitting, {
      type: "ALREADY_RUNNING",
      message: "任务已在运行中",
    });
    expect(next.phase).toBe("running");
    expect(next.message).toBe("任务已在运行中");
  });

  it("POLL_RESULT with completed stream transitions to succeeded", () => {
    const state = runningState();
    const stream = makeStream({ status: "completed" });
    const next = prepareSessionReducer(state, { type: "POLL_RESULT", stream });
    expect(next.phase).toBe("succeeded");
    expect(next.progressPercent).toBe(100);
    expect(next.message).toBe("章节准备完成");
    expect(next.stream).toBe(stream);
  });

  it("POLL_RESULT with failed stream extracts error event message", () => {
    const state = runningState();
    const stream = makeStream({
      status: "failed",
      events: [
        { streamId: "task-1", sequence: 1, kind: "stream_start", segment: "content" },
        { streamId: "task-1", sequence: 2, kind: "stream_error", segment: "system", message: "模型调用超时" },
      ],
    });
    const next = prepareSessionReducer(state, { type: "POLL_RESULT", stream });
    expect(next.phase).toBe("failed");
    expect(next.error).toBe("模型调用超时");
  });

  it("POLL_RESULT with failed stream and no error event uses fallback message", () => {
    const state = runningState();
    const stream = makeStream({ status: "failed", events: [] });
    const next = prepareSessionReducer(state, { type: "POLL_RESULT", stream });
    expect(next.phase).toBe("failed");
    expect(next.error).toBe("任务执行失败");
  });

  it("POLL_RESULT with streaming status updates stream but keeps phase", () => {
    const state = runningState({ progressPercent: 10 });
    const stream = makeStream({ status: "streaming" });
    const next = prepareSessionReducer(state, { type: "POLL_RESULT", stream });
    expect(next.phase).toBe("running");
    expect(next.stream).toBe(stream);
  });

  it("CHECKPOINT_DETECTED transitions to checkpoint", () => {
    const state = runningState();
    const next = prepareSessionReducer(state, { type: "CHECKPOINT_DETECTED" });
    expect(next.phase).toBe("checkpoint");
    expect(next.message).toContain("等待人工决策");
  });

  it("CHECKPOINT_DETECTED with custom message", () => {
    const state = runningState();
    const next = prepareSessionReducer(state, {
      type: "CHECKPOINT_DETECTED",
      message: "方案需确认",
    });
    expect(next.phase).toBe("checkpoint");
    expect(next.message).toBe("方案需确认");
  });

  it("CANCELLED transitions to cancelled", () => {
    const state = runningState();
    const next = prepareSessionReducer(state, { type: "CANCELLED" });
    expect(next.phase).toBe("cancelled");
    expect(next.message).toBe("任务已取消");
  });

  it("ERROR transitions to failed", () => {
    const state = runningState();
    const next = prepareSessionReducer(state, {
      type: "ERROR",
      message: "网络请求失败",
    });
    expect(next.phase).toBe("failed");
    expect(next.error).toBe("网络请求失败");
  });

  it("TIMEOUT transitions to disconnected with guidance", () => {
    const state = runningState();
    const next = prepareSessionReducer(state, { type: "TIMEOUT" });
    expect(next.phase).toBe("disconnected");
    expect(next.error).toContain("后台任务可能仍在运行");
  });

  it("DISCONNECTED transitions to disconnected", () => {
    const state = runningState();
    const next = prepareSessionReducer(state, { type: "DISCONNECTED" });
    expect(next.phase).toBe("disconnected");
    expect(next.error).toContain("连接中断");
  });

  it("RESET returns to initial state", () => {
    const state: PrepareSessionState = {
      phase: "failed",
      taskId: "task-99",
      message: "出错了",
      stream: makeStream(),
      error: "某些错误",
      progressPercent: 42,
    };
    const next = prepareSessionReducer(state, { type: "RESET" });
    expect(next).toEqual(INITIAL_PREPARE_STATE);
  });

  it("preserves taskId through POLL_RESULT transitions", () => {
    const state = runningState({ taskId: "task-7" });
    const stream = makeStream({ status: "streaming" });
    const next = prepareSessionReducer(state, { type: "POLL_RESULT", stream });
    expect(next.taskId).toBe("task-7");
  });
});
