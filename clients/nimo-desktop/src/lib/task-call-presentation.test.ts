import { describe, expect, it } from "vitest";

import type { TaskStreamState } from "./task-stream";
import { deriveTaskCallPresentation } from "./task-call-presentation";

function baseStream(): TaskStreamState {
  return {
    taskId: "task-1",
    title: "长篇立项",
    stepId: "plan_chapter",
    stepLabel: "章节计划",
    status: "streaming",
    jobState: "running",
    progressPercent: 30,
    events: [],
  };
}

describe("deriveTaskCallPresentation", () => {
  it("groups logical calls by task and calculates one token comparison", () => {
    const result = deriveTaskCallPresentation({
      ...baseStream(),
      calls: [
        {
          callId: "plan-1",
          task: "PLAN_CHAPTER",
          taskLabel: "章节计划",
          provider: "openai",
          model: "gpt-test",
          status: "success",
          event: "api_stream_done",
          promptTokens: 100,
          completionTokens: 40,
          totalTokens: 140,
          latencyMs: 900,
          costUsd: 0.01,
        },
        {
          callId: "plan-2",
          task: "PLAN_CHAPTER",
          taskLabel: "章节计划",
          provider: "deepseek",
          model: "deepseek-chat",
          status: "retrying",
          event: "api_stream_error",
          promptTokens: 80,
          completionTokens: 20,
          willRetry: true,
        },
        {
          callId: "wave-1",
          task: "WAVE_CHAPTER",
          taskLabel: "章节织波",
          status: "running",
          event: "api_stream_start",
        },
      ],
    });

    expect(result.calls).toHaveLength(3);
    expect(result.groups).toHaveLength(2);
    expect(result.groups[0]).toMatchObject({
      label: "章节计划",
      promptTokens: 180,
      completionTokens: 60,
      totalTokens: 240,
      status: "retrying",
    });
    expect(result.totalTokens).toBe(240);
    expect(result.retryCount).toBe(1);
    expect(result.runningCount).toBe(1);
    expect(result.hasTokenData).toBe(true);
  });

  it("uses a legacy summary without presenting an unknown zero as real usage", () => {
    const result = deriveTaskCallPresentation({
      ...baseStream(),
      summary: { outputKind: "json", totalTokens: 0 },
    });

    expect(result.calls).toHaveLength(1);
    expect(result.totalTokens).toBe(0);
    expect(result.hasTokenData).toBe(false);
  });
});
