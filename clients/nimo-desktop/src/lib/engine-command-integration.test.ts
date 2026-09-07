/**
 * Integration tests: Engine Command → SSE → TaskQueueManager state updates.
 *
 * Verifies the full chain from command dispatch through stream events
 * to task queue state transitions, using the mock engine client.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { TaskStreamEvent } from "@nimo/engine-contracts";

import { mockEngineCommandClient, mockEngineClient } from "./mock-engine";
import { createLongInitSession, toInitLongWorkflowInput } from "./long-init-session";
import { createShortWorkflowSession, toRunShortWorkflowInput } from "./short-workflow-session";
import { TaskQueueManager } from "./task-queue-manager";
import { taskStreamReducer, createTaskStreamState, type TaskStreamState } from "./task-stream";

// Mock engine uses window.setTimeout for delays; stub it in Node environment
vi.stubGlobal("window", globalThis);

describe("Engine Command Integration", () => {
  describe("startWorkflow → SSE → state", () => {
    it("accepts workflow command and returns taskId", async () => {
      const payload = toRunShortWorkflowInput(createShortWorkflowSession().payload);
      const result = await mockEngineCommandClient.startWorkflow({
        kind: "start_workflow",
        workflowType: "short",
        projectId: payload.projectId,
        runMode: "create",
        idempotencyKey: "test-short-start",
        payload,
      });
      expect(result.status).toBe("accepted");
      expect(result.taskId).toBeDefined();
      expect(result.message).toContain("短篇");
    });

    it("accepts long_init workflow command", async () => {
      const payload = toInitLongWorkflowInput(createLongInitSession().payload, "create");
      const result = await mockEngineCommandClient.startWorkflow({
        kind: "start_workflow",
        workflowType: "long_init",
        projectId: payload.projectId,
        runMode: "create",
        idempotencyKey: "test-long-start",
        payload,
      });
      expect(result.status).toBe("accepted");
      expect(result.message).toContain("长篇初始化");
    });

    it("continues long_init through the dedicated durable resume command", async () => {
      const payload = toInitLongWorkflowInput(createLongInitSession().payload, "autorun");
      const result = await mockEngineCommandClient.continueLongInit({
        kind: "continue_long_init",
        projectId: "test-long",
        runMode: "autorun",
        fallbackPayload: { ...payload, projectId: "test-long" },
      });
      expect(result.status).toBe("accepted");
      expect(result.taskId).toContain("mock-long-init-resume");
      expect(result.message).toContain("原始立项参数");
    });
  });

  describe("prepareChapter → command result", () => {
    it("accepts prepare chapter command", async () => {
      const result = await mockEngineCommandClient.prepareChapter({
        kind: "prepare_chapter",
        projectId: "test-long",
        chapterNumber: 5,
      });
      expect(result.status).toBe("accepted");
      expect(result.message).toContain("第 5 章");
    });
  });

  describe("rebuildMemoryVectors → durable task result", () => {
    it("accepts a project-scoped vector rebuild command", async () => {
      const result = await mockEngineCommandClient.rebuildMemoryVectors({
        kind: "rebuild_memory_vectors",
        projectId: "test-long",
        includeExpression: true,
      });
      expect(result.status).toBe("accepted");
      expect(result.taskId).toBe("mock-vector-rebuild-test-long");
    });
  });

  describe("cancelChapter → command result", () => {
    it("accepts cancel chapter command", async () => {
      const result = await mockEngineCommandClient.cancelChapter({
        kind: "cancel_chapter",
        projectId: "test-long",
        chapterNumber: 5,
      });
      expect(result.status).toBe("accepted");
      expect(result.message).toContain("取消");
    });
  });

  describe("cancelJob → durable task stream", () => {
    it("projects the Engine cancellation terminal state instead of an active snapshot", async () => {
      const accepted = await mockEngineCommandClient.buildVoiceTeam({
        kind: "build_voice_team",
        projectId: "test-long",
      });
      expect(accepted.taskId).toBeDefined();
      const taskId = accepted.taskId!;

      const cancelled = await mockEngineCommandClient.cancelJob({
        kind: "cancel_job",
        taskId,
        reason: "用户已取消",
      });
      expect(cancelled.status).toBe("accepted");

      const snapshot = await mockEngineClient.getTaskStream(taskId);
      expect(snapshot.status).toBe("failed");
      expect(snapshot.jobState).toBe("failed");
      expect(snapshot.stepId).toBe("cancelled");
    });
  });

  describe("synthesizeVoice → command result", () => {
    it("accepts voice synthesis with segments", async () => {
      const result = await mockEngineCommandClient.synthesizeVoice({
        kind: "synthesize_voice",
        projectId: "test-long",
        chapterNumber: 1,
        segmentIds: ["seg-1", "seg-2"],
      });
      expect(result.status).toBe("accepted");
      expect(result.taskId).toBeDefined();
    });

    it("returns no_segments when no segmentIds provided", async () => {
      const result = await mockEngineCommandClient.synthesizeVoice({
        kind: "synthesize_voice",
        projectId: "test-long",
        chapterNumber: 1,
      });
      expect(result.status).toBe("no_segments");
    });
  });

  describe("saveSettings → command result", () => {
    it("accepts settings save with routes", async () => {
      const result = await mockEngineCommandClient.saveSettings({
        kind: "save_settings",
        routes: { DRAFT_CHAPTER: { primaryProfileId: "openai:gpt-4o", fallbackRoutes: [], thinkingEnabled: false, multiTurnEnabled: true, temperature: null } },
      });
      expect(result.status).toBe("saved");
      expect(result.acceptedRouteIds).toContain("DRAFT_CHAPTER");
    });

    it("accepts settings save with creationParameters", async () => {
      const result = await mockEngineCommandClient.saveSettings({
        kind: "save_settings",
        creationParameters: { "short-max-edit": "3", "humanize-enabled": "true" },
      });
      expect(result.status).toBe("saved");
    });
  });

  describe("SSE stream → TaskStreamState reducer", () => {
    it("builds state from snapshot", async () => {
      const snapshot = await mockEngineClient.getTaskStream("init-long-qingwa");
      const state = createTaskStreamState(snapshot);
      expect(state.taskId).toBe(snapshot.taskId);
      expect(state.events.length).toBeGreaterThan(0);
    });

    it("appends events via reducer with deduplication", () => {
      const snapshot = { taskId: "t1", title: "测试", stepLabel: "步骤1", status: "streaming" as const, progressPercent: 0, jobState: "running" as const, events: [] };
      let state: TaskStreamState | null = taskStreamReducer(null, { type: "replace", snapshot });
      expect(state).not.toBeNull();

      const event: TaskStreamEvent = { streamId: "s1", sequence: 1, kind: "delta", segment: "content", text: "你好" };
      state = taskStreamReducer(state, { type: "event", event });
      expect(state!.events).toHaveLength(1);

      // Duplicate event should be ignored
      state = taskStreamReducer(state, { type: "event", event });
      expect(state!.events).toHaveLength(1);
    });

    it("does not treat a model stream_end as workflow completion", () => {
      const snapshot = { taskId: "t1", title: "测试", stepLabel: "步骤1", status: "streaming" as const, progressPercent: 0, jobState: "running" as const, events: [] };
      let state = taskStreamReducer(null, { type: "replace", snapshot });
      const event: TaskStreamEvent = { streamId: "s1", sequence: 1, kind: "stream_end", segment: "system" };
      state = taskStreamReducer(state, { type: "event", event });
      expect(state!.status).toBe("streaming");
    });

    it("does not treat a retryable model stream_error as workflow failure", () => {
      const snapshot = { taskId: "t1", title: "测试", stepLabel: "步骤1", status: "streaming" as const, progressPercent: 0, jobState: "running" as const, events: [] };
      let state = taskStreamReducer(null, { type: "replace", snapshot });
      const event: TaskStreamEvent = { streamId: "s1", sequence: 1, kind: "stream_error", segment: "system", text: "模型超时" };
      state = taskStreamReducer(state, { type: "event", event });
      expect(state!.status).toBe("streaming");
    });
  });

  describe("SSE events → TaskQueueManager integration", () => {
    let queue: TaskQueueManager;

    beforeEach(() => {
      vi.useFakeTimers();
      queue = new TaskQueueManager({ defaultTimeoutMs: 900_000 });
    });

    afterEach(() => {
      vi.useRealTimers();
    });

    it("tracks full lifecycle: submit → running → delta events → succeeded", () => {
      queue.submit({ id: "wf-1", kind: "workflow", label: "短篇工作流", projectId: "test-short" });
      queue.markRunning("wf-1");

      // Simulate SSE delta events
      const events: TaskStreamEvent[] = [
        { streamId: "s1", sequence: 1, kind: "stream_start", segment: "system" },
        { streamId: "s1", sequence: 2, kind: "delta", segment: "content", text: "第一段内容" },
        { streamId: "s1", sequence: 3, kind: "delta", segment: "content", text: "第二段内容" },
        { streamId: "s1", sequence: 4, kind: "stream_end", segment: "system" },
      ];

      for (const event of events) {
        queue.appendEvent("wf-1", event);
        if (event.kind === "delta") {
          queue.updateProgress("wf-1", {
            currentStep: event.text ?? "执行中",
            percent: Math.min(95, (queue.get("wf-1")?.progress.percent ?? 5) + 1),
          });
        } else if (event.kind === "stream_end") {
          queue.markSucceeded("wf-1");
        }
      }

      const task = queue.get("wf-1")!;
      expect(task.status).toBe("succeeded");
      expect(task.events).toHaveLength(4);
      expect(task.progress.percent).toBe(100);
    });

    it("tracks error lifecycle: submit → running → stream_error → failed", () => {
      queue.submit({ id: "wf-2", kind: "workflow", label: "长篇初始化", projectId: "test-long" });
      queue.markRunning("wf-2");

      queue.appendEvent("wf-2", { streamId: "s2", sequence: 1, kind: "stream_start", segment: "system" });
      queue.appendEvent("wf-2", { streamId: "s2", sequence: 2, kind: "stream_error", segment: "system", text: "RateLimitError" });
      queue.markFailed("wf-2", {
        summary: "RateLimitError",
        code: "STREAM_ERROR",
        retryable: true,
      });

      const task = queue.get("wf-2")!;
      expect(task.status).toBe("failed");
      expect(task.error!.summary).toBe("RateLimitError");
      expect(task.error!.retryable).toBe(true);
    });

    it("supports cancel → checkpoint → resume flow", () => {
      queue.submit({ id: "ch-5", kind: "chapter", label: "第5章生成", projectId: "test-long" });
      queue.markRunning("ch-5");
      queue.updateProgress("ch-5", { percent: 43, currentStep: "草稿生成" });

      // User cancels
      queue.saveCheckpoint("ch-5", { stageId: "draft", stageLabel: "草稿生成", savedAt: Date.now() });
      queue.cancel("ch-5");
      expect(queue.get("ch-5")!.status).toBe("cancelled");

      // Resume from checkpoint
      const resumed = queue.resume("ch-5");
      expect(resumed).toBe(true);
      expect(queue.get("ch-5")!.status).toBe("queued");
      expect(queue.get("ch-5")!.progress.currentStep).toContain("草稿生成");
    });
  });

  describe("subscribeTaskStream → live events", () => {
    it("delivers events via subscription for known taskId", async () => {
      // Use real timers for this test since mock engine uses setTimeout delays
      const snapshot = await mockEngineClient.getTaskStream("init-long-qingwa");
      expect(snapshot.taskId).toBe("init-long-qingwa");
      expect(snapshot.events.length).toBeGreaterThan(0);

      // Subscribe and collect events
      const received: TaskStreamEvent[] = [];
      const unsubscribe = mockEngineClient.subscribeTaskStream("init-long-qingwa", (event) => {
        received.push(event);
      });

      // Wait for at least one event to arrive (mock uses 850ms intervals)
      await new Promise((resolve) => setTimeout(resolve, 1200));
      expect(received.length).toBeGreaterThan(0);

      unsubscribe();
    }, 10_000);

    it("returns noop unsubscribe for unknown taskId", () => {
      const unsubscribe = mockEngineClient.subscribeTaskStream("unknown-task", () => {});
      expect(typeof unsubscribe).toBe("function");
      unsubscribe(); // Should not throw
    });
  });
});
