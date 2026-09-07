import { describe, expect, it } from "vitest";

import type { TaskStreamState } from "./task-stream";
import { deriveTaskStreamTrace, diagnoseStructuredFragment } from "./task-stream-trace";

const base: TaskStreamState = {
  taskId: "trace-task",
  title: "长篇立项",
  stepLabel: "章节契约 Claims",
  stepId: "claims",
  status: "streaming",
  jobState: "running",
  progressPercent: 80,
  summary: { outputKind: "json" },
  events: [],
};

describe("task stream trace projection", () => {
  it("uses backend validation instead of deciding success from JSON syntax", () => {
    const presentation = deriveTaskStreamTrace({
      ...base,
      events: [
        { streamId: "done", sequence: 1, kind: "delta", segment: "content", text: '{"claim":1}' },
        { streamId: "done", sequence: 2, kind: "validation", segment: "system", validationStatus: "validated" },
        { streamId: "running", sequence: 3, kind: "delta", segment: "content", text: '{"claim":' },
        { streamId: "invalid", sequence: 4, kind: "delta", segment: "content", text: '{"claim":2' },
        { streamId: "invalid", sequence: 5, kind: "validation", segment: "system", validationStatus: "failed" },
      ],
    });

    expect(presentation.nodes.map((node) => node.streamId)).toEqual(["done", "running", "invalid"]);
    expect(presentation.nodes.map((node) => node.status)).toEqual(["completed", "running", "attention"]);
    expect(presentation).toMatchObject({ completedCount: 1, runningCount: 1, attentionCount: 1 });
  });

  it("does not mark historical fragments as failed or let a sibling end settle a live stream", () => {
    const presentation = deriveTaskStreamTrace({
      ...base,
      status: "completed",
      events: [
        { streamId: "legacy", sequence: 1, kind: "delta", segment: "content", text: '{"claim":' },
        { streamId: "legacy", sequence: 2, kind: "stream_end", segment: "system" },
        { streamId: "live", sequence: 3, kind: "delta", segment: "content", text: '{"claim":' },
      ],
    });
    expect(presentation.nodes.map((node) => node.status)).toEqual(["unverified", "running"]);
    expect(presentation.attentionCount).toBe(0);
  });

  it("shows clipped validated snapshots as completed and preserves raw evidence", () => {
    const presentation = deriveTaskStreamTrace({
      ...base,
      events: [
        { streamId: "repaired", sequence: 1, kind: "stream_end", segment: "content", textMode: "snapshot", text: '{"summary":"原始"' },
        { streamId: "repaired", sequence: 2, kind: "validation", segment: "content", textMode: "snapshot", text: '{"summary":"原始', textLength: 6001, textTruncated: true, validationStatus: "validated", repairSource: "local" },
      ],
    });
    expect(presentation.nodes[0]).toMatchObject({ status: "completed", textTruncated: true, characterCount: 6001, rawContentText: '{"summary":"原始"' });
    expect(presentation.attentionCount).toBe(0);
  });

  it("supersedes retries by operation id, never by shared task name", () => {
    const presentation = deriveTaskStreamTrace({
      ...base,
      events: [
        { streamId: "a1", sequence: 1, kind: "validation", segment: "system", operationId: "a", attempt: 1, modelTaskId: "extract_claims", validationStatus: "retrying" },
        { streamId: "b1", sequence: 2, kind: "validation", segment: "system", operationId: "b", attempt: 1, modelTaskId: "extract_claims", validationStatus: "failed" },
        { streamId: "a2", sequence: 3, kind: "validation", segment: "system", operationId: "a", attempt: 2, modelTaskId: "extract_claims", validationStatus: "validated" },
      ],
    });
    expect(presentation.nodes.map((node) => node.status)).toEqual(["superseded", "attention", "completed"]);
    expect(presentation).toMatchObject({ completedCount: 1, attentionCount: 1, runningCount: 0, supersededCount: 1 });
  });

  it("supersedes failed prose attempts when backend retries keep one operation id", () => {
    const presentation = deriveTaskStreamTrace({
      ...base,
      summary: { outputKind: "text" },
      events: [
        { streamId: "text-1", sequence: 1, kind: "stream_start", segment: "system", operationId: "write-op", attempt: 1 },
        { streamId: "text-1", sequence: 2, kind: "delta", segment: "content", operationId: "write-op", attempt: 1, text: "失败片段" },
        { streamId: "text-1", sequence: 3, kind: "stream_error", segment: "system", operationId: "write-op", attempt: 1, message: "连接中断" },
        { streamId: "text-1", sequence: 4, kind: "restart", segment: "system", operationId: "write-op", attempt: 1 },
        { streamId: "text-2", sequence: 5, kind: "stream_start", segment: "system", operationId: "write-op", attempt: 2 },
        { streamId: "text-2", sequence: 6, kind: "delta", segment: "content", operationId: "write-op", attempt: 2, text: "重试成功" },
        { streamId: "text-2", sequence: 7, kind: "stream_end", segment: "system", operationId: "write-op", attempt: 2 },
      ],
    });

    expect(presentation.nodes.map((node) => node.status)).toEqual(["superseded", "completed"]);
    expect(presentation).toMatchObject({ completedCount: 1, attentionCount: 0, supersededCount: 1 });
  });

  it("keeps backend validation running after transport ends and flags interrupted validation", () => {
    const events = [{ streamId: "pending", sequence: 1, kind: "stream_end" as const, segment: "system" as const, validationStatus: "repairing" as const }];
    expect(deriveTaskStreamTrace({ ...base, events }).nodes[0]?.status).toBe("running");
    expect(deriveTaskStreamTrace({ ...base, events, jobState: "failed" }).nodes[0]?.status).toBe("attention");
  });

  it("keeps ordinary prose streams live without treating punctuation as JSON", () => {
    const presentation = deriveTaskStreamTrace({
      ...base,
      summary: { outputKind: "text" },
      events: [{ streamId: "prose", sequence: 1, kind: "delta", segment: "content", text: "雨落在灰瓦上。" }],
    });

    expect(presentation.nodes[0]).toMatchObject({ structured: false, status: "running" });
  });

  it("describes missing closers and the last visible field without repairing source", () => {
    expect(diagnoseStructuredFragment('{"claims":[{"cognitive_level":"confirmed"}')).toEqual({
      expectedClosers: "]}",
      lastField: "cognitive_level",
    });
  });
});
