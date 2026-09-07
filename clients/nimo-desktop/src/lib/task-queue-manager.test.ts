import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { TaskQueueManager } from "./task-queue-manager";

describe("TaskQueueManager", () => {
  let queue: TaskQueueManager;

  beforeEach(() => {
    vi.useFakeTimers();
    queue = new TaskQueueManager({ maxConcurrent: 2, defaultTimeoutMs: 60_000 });
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("submits a task in queued state", () => {
    const id = queue.submit({ id: "t1", kind: "workflow", label: "测试任务", projectId: "p1" });
    expect(id).toBe("t1");
    const task = queue.get("t1");
    expect(task).toMatchObject({ status: "queued", progress: { percent: 0, currentStep: "排队中" } });
  });

  it("marks a task as running", () => {
    queue.submit({ id: "t1", kind: "workflow", label: "测试", projectId: "p1" });
    queue.markRunning("t1");
    expect(queue.get("t1")!.status).toBe("running");
    expect(queue.get("t1")!.startedAt).not.toBeNull();
  });

  it("updates progress for running tasks only", () => {
    queue.submit({ id: "t1", kind: "workflow", label: "测试", projectId: "p1" });
    queue.updateProgress("t1", { percent: 50 });
    // Still queued, should not update
    expect(queue.get("t1")!.progress.percent).toBe(0);

    queue.markRunning("t1");
    queue.updateProgress("t1", { percent: 50, currentStep: "草稿生成" });
    expect(queue.get("t1")!.progress).toMatchObject({ percent: 50, currentStep: "草稿生成" });
  });

  it("marks a task as succeeded and clears timeout", () => {
    queue.submit({ id: "t1", kind: "workflow", label: "测试", projectId: "p1" });
    queue.markRunning("t1");
    queue.markSucceeded("t1");
    const task = queue.get("t1")!;
    expect(task.status).toBe("succeeded");
    expect(task.progress.percent).toBe(100);
    expect(task.finishedAt).not.toBeNull();
  });

  it("marks a task as failed with structured error", () => {
    queue.submit({ id: "t1", kind: "workflow", label: "测试", projectId: "p1" });
    queue.markRunning("t1");
    queue.markFailed("t1", { summary: "模型超时", code: "TIMEOUT", retryable: true });
    const task = queue.get("t1")!;
    expect(task.status).toBe("failed");
    expect(task.error).toMatchObject({ summary: "模型超时", code: "TIMEOUT" });
  });

  it("cancels a running task", () => {
    queue.submit({ id: "t1", kind: "workflow", label: "测试", projectId: "p1" });
    queue.markRunning("t1");
    queue.cancel("t1");
    expect(queue.get("t1")!.status).toBe("cancelled");
  });

  it("does not cancel already finished tasks", () => {
    queue.submit({ id: "t1", kind: "workflow", label: "测试", projectId: "p1" });
    queue.markRunning("t1");
    queue.markSucceeded("t1");
    queue.cancel("t1");
    expect(queue.get("t1")!.status).toBe("succeeded");
  });

  it("saves and resumes from checkpoint", () => {
    queue.submit({ id: "t1", kind: "chapter", label: "第3章", projectId: "p1" });
    queue.markRunning("t1");
    queue.saveCheckpoint("t1", { stageId: "draft", stageLabel: "草稿生成", savedAt: Date.now() });
    queue.cancel("t1");
    expect(queue.get("t1")!.status).toBe("cancelled");

    const resumed = queue.resume("t1");
    expect(resumed).toBe(true);
    expect(queue.get("t1")!.status).toBe("queued");
    expect(queue.get("t1")!.progress.currentStep).toContain("从断点恢复");
  });

  it("cannot resume without checkpoint", () => {
    queue.submit({ id: "t1", kind: "chapter", label: "第3章", projectId: "p1" });
    queue.markRunning("t1");
    queue.cancel("t1");
    expect(queue.resume("t1")).toBe(false);
  });

  it("times out a running task after defaultTimeoutMs", () => {
    queue.submit({ id: "t1", kind: "workflow", label: "测试", projectId: "p1" });
    queue.markRunning("t1");
    vi.advanceTimersByTime(60_001);
    const task = queue.get("t1")!;
    expect(task.status).toBe("timeout");
    expect(task.error!.code).toBe("TASK_TIMEOUT");
    expect(task.error!.retryable).toBe(true);
  });

  it("notifies subscribers on state changes", () => {
    const listener = vi.fn();
    queue.subscribe(listener);
    queue.submit({ id: "t1", kind: "workflow", label: "测试", projectId: "p1" });
    // submit triggers notify + promoteQueued notify
    expect(listener.mock.calls.length).toBeGreaterThanOrEqual(1);
    const callsAfterSubmit = listener.mock.calls.length;
    queue.markRunning("t1");
    expect(listener.mock.calls.length).toBeGreaterThan(callsAfterSubmit);
  });

  it("unsubscribes correctly", () => {
    const listener = vi.fn();
    const unsub = queue.subscribe(listener);
    unsub();
    queue.submit({ id: "t1", kind: "workflow", label: "测试", projectId: "p1" });
    expect(listener).not.toHaveBeenCalled();
  });

  it("lists tasks filtered by status", () => {
    queue.submit({ id: "t1", kind: "a", label: "A", projectId: "p1" });
    queue.submit({ id: "t2", kind: "b", label: "B", projectId: "p1" });
    queue.markRunning("t1");
    expect(queue.list("running")).toHaveLength(1);
    expect(queue.list("queued")).toHaveLength(1);
    expect(queue.list()).toHaveLength(2);
  });

  it("clears finished tasks", () => {
    queue.submit({ id: "t1", kind: "a", label: "A", projectId: "p1" });
    queue.submit({ id: "t2", kind: "b", label: "B", projectId: "p1" });
    queue.markRunning("t1");
    queue.markSucceeded("t1");
    queue.clearFinished();
    expect(queue.list()).toHaveLength(1);
    expect(queue.get("t1")).toBeUndefined();
  });

  it("tracks runningCount correctly", () => {
    queue.submit({ id: "t1", kind: "a", label: "A", projectId: "p1" });
    queue.submit({ id: "t2", kind: "b", label: "B", projectId: "p1" });
    queue.submit({ id: "t3", kind: "c", label: "C", projectId: "p1" });
    queue.markRunning("t1");
    queue.markRunning("t2");
    expect(queue.runningCount).toBe(2);
  });

  it("appends events and auto-updates progress detail for delta events", () => {
    queue.submit({ id: "t1", kind: "a", label: "A", projectId: "p1" });
    queue.markRunning("t1");
    queue.appendEvent("t1", { streamId: "s1", sequence: 1, kind: "delta", segment: "content", text: "你好世界" });
    const task = queue.get("t1")!;
    expect(task.events).toHaveLength(1);
    expect(task.progress.detail).toContain("4 字已生成");
  });

  it("accumulates streamChars incrementally across deltas", () => {
    queue.submit({ id: "t1", kind: "a", label: "A", projectId: "p1" });
    queue.markRunning("t1");
    queue.appendEvent("t1", { streamId: "s1", sequence: 1, kind: "delta", segment: "content", text: "你好" });
    queue.appendEvent("t1", { streamId: "s1", sequence: 2, kind: "delta", segment: "content", text: "世界" });
    const task = queue.get("t1")!;
    expect(task.streamChars).toBe(4);
    expect(task.progress.detail).toBe("4 字已生成");
    // Non-delta events do not inflate the counter.
    queue.appendEvent("t1", { streamId: "s1", sequence: 3, kind: "stream_end", segment: "system", message: "完成" });
    expect(queue.get("t1")!.streamChars).toBe(4);
  });

  it("bounds the event log to the most recent events", () => {
    queue.submit({ id: "t1", kind: "a", label: "A", projectId: "p1" });
    queue.markRunning("t1");
    const total = 305;
    for (let index = 0; index < total; index += 1) {
      queue.appendEvent("t1", {
        streamId: "s1",
        sequence: index + 1,
        kind: "delta",
        segment: "content",
        text: "字",
      });
    }
    const task = queue.get("t1")!;
    expect(task.events).toHaveLength(300);
    expect(task.events[0]!.sequence).toBe(6);
    expect(task.events.at(-1)!.sequence).toBe(305);
    expect(task.streamChars).toBe(305);
  });
});
