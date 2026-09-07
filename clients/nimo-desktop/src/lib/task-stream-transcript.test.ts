import { describe, expect, it } from "vitest";

import type { TaskStreamEvent } from "@nimo/engine-contracts";

import { buildTaskStreamTranscript } from "./task-stream-transcript";

const event = (
  sequence: number,
  segment: TaskStreamEvent["segment"],
  text: string,
  overrides: Partial<TaskStreamEvent> = {},
): TaskStreamEvent => ({
  streamId: "stream-1",
  sequence,
  kind: segment === "system" ? "stream_error" : "delta",
  segment,
  text,
  ...overrides,
});

describe("task stream transcript", () => {
  it("merges contiguous token deltas into one readable output block", () => {
    const transcript = buildTaskStreamTranscript([
      event(1, "content", "灰瓦"),
      event(2, "content", "在雨里"),
      event(3, "content", "发亮。"),
    ]);

    expect(transcript.items).toEqual([
      expect.objectContaining({
        kind: "content",
        text: "灰瓦在雨里发亮。",
        characterCount: 8,
        eventCount: 3,
      }),
    ]);
    expect(transcript.contentCharacterCount).toBe(8);
  });

  it("uses explicit replacement snapshots to recover lost deltas", () => {
    const transcript = buildTaskStreamTranscript([
      event(1, "content", "第一段"),
      event(2, "content", "第一段续写", { kind: "stream_end", textMode: "snapshot" }),
    ]);

    expect(transcript.items).toHaveLength(1);
    expect(transcript.items[0]).toMatchObject({ text: "第一段续写" });
  });

  it("never removes repeated closers, content, or whitespace from distinct deltas", () => {
    const transcript = buildTaskStreamTranscript([
      event(1, "content", '{"nested":{"value":"哈'),
      event(2, "content", "哈"),
      event(3, "content", " "),
      event(4, "content", '"'),
      event(5, "content", "}"),
      event(6, "content", "}"),
    ]);
    expect(transcript.items[0]?.text).toBe('{"nested":{"value":"哈哈 "}}');
  });

  it("deduplicates by stable cursor even when replay sequence numbers change", () => {
    const transcript = buildTaskStreamTranscript([
      event(99, "content", "}" , { cursor: "same-event" }),
      event(1, "content", "}", { cursor: "same-event" }),
      event(2, "content", "}", { cursor: "different-event" }),
    ]);
    expect(transcript.items[0]?.text).toBe("}}");
  });

  it("replaces all fragments of one stream without overwriting an interleaved sibling", () => {
    const transcript = buildTaskStreamTranscript([
      event(1, "content", '{"first":'),
      event(2, "content", "其他输出", { streamId: "sibling" }),
      event(3, "content", "1"),
      event(4, "content", '{"first":1}', { kind: "validation", textMode: "snapshot" }),
    ]);
    expect(transcript.items.map((item) => item.text)).toEqual(['{"first":1}', "其他输出"]);
  });

  it("clears stale content on an explicit restart", () => {
    const transcript = buildTaskStreamTranscript([
      event(1, "content", "旧输出"),
      event(2, "system", "", { kind: "restart" }),
      event(3, "content", "新输出"),
    ]);
    expect(transcript.items.map((item) => item.text)).toEqual(["新输出"]);
  });

  it("keeps reasoning separate and exposes stream errors once", () => {
    const transcript = buildTaskStreamTranscript([
      event(1, "content", "正文。"),
      event(2, "reasoning", "检查伏笔。"),
      event(3, "reasoning", "再检查人称。"),
      event(4, "system", "模型连接中断。"),
    ]);

    expect(transcript.items.map((item) => item.kind)).toEqual(["content", "reasoning", "error"]);
    expect(transcript.items[1]).toMatchObject({ text: "检查伏笔。再检查人称。", eventCount: 2 });
    expect(transcript.reasoningBlockCount).toBe(1);
  });

  it("does not merge blocks across attempts or streams", () => {
    const transcript = buildTaskStreamTranscript([
      event(1, "content", "第一次", { attempt: 1 }),
      event(2, "content", "第二次", { attempt: 2 }),
      event(3, "content", "另一流", { streamId: "stream-2", attempt: 2 }),
    ]);

    expect(transcript.items).toHaveLength(3);
  });
});
