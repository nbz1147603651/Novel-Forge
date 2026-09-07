import { describe, expect, it } from "vitest";

import { createIncrementalJsonParser } from "./incremental-json";

describe("incremental JSON array parser", () => {
  it("extracts complete objects as the stream grows", () => {
    const parser = createIncrementalJsonParser();
    const partial = '{"segments": [{"segment_index": 1, "text": "第一段';
    expect(parser.parse(partial, "segments")).toHaveLength(0);

    const grown = `${partial}", "emotion": "calm"}]}`;
    const parsed = parser.parse(grown, "segments");
    expect(parsed).toHaveLength(1);
    expect(parsed[0]).toMatchObject({ segment_index: 1, text: "第一段", emotion: "calm" });
  });

  it("handles escaped quotes inside segment prose", () => {
    // 台词含转义引号：正则版解析器在此截断为 `他说\`，状态机必须完整解析。
    const parser = createIncrementalJsonParser();
    const raw = '{"segments": [{"segment_index": 1, "character_name": "沈岸", "text": "他说\\"我来了\\"就走", "emotion": "calm"}]}';
    const parsed = parser.parse(raw, "segments");
    expect(parsed).toHaveLength(1);
    expect(parsed[0]).toMatchObject({ text: '他说"我来了"就走' });
  });

  it("keeps nested objects inside a segment", () => {
    // 嵌套对象（如 soundscape / stress_words）：正则版 `[^{}]*` 整体漏段。
    const parser = createIncrementalJsonParser();
    const raw = '{"segments": [{"segment_index": 1, "text": "旁白", "nested": {"a": 1}}]}';
    const parsed = parser.parse(raw, "segments");
    expect(parsed).toHaveLength(1);
    expect(parsed[0]).toMatchObject({ text: "旁白", nested: { a: 1 } });
  });

  it("rescans a partial object from its start on the next call", () => {
    const parser = createIncrementalJsonParser();
    // First call sees one complete object; the second object is mid-stream.
    const partial = '{"segments": [{"segment_index": 1, "text": "一"}, {"segment_index": 2, "text": "二';
    const first = parser.parse(partial, "segments");
    expect(first).toHaveLength(1);

    const grown = `${partial}"}]}`;
    const second = parser.parse(grown, "segments");
    expect(second).toHaveLength(2);
    expect(second[1]).toMatchObject({ segment_index: 2, text: "二" });
  });

  it("full rescans when the text shrinks (stream reset)", () => {
    const parser = createIncrementalJsonParser();
    const firstText = '{"segments": [{"segment_index": 1, "text": "旧流"}, {"segment_index": 2, "text": "旧流二"}]}';
    expect(parser.parse(firstText, "segments")).toHaveLength(2);

    // New stream is shorter than the old scan position → must not reuse cache.
    const newStream = '{"segments": [{"segment_index": 1, "text": "新流"}]}';
    const parsed = parser.parse(newStream, "segments");
    expect(parsed).toHaveLength(1);
    expect(parsed[0]).toMatchObject({ text: "新流" });
  });

  it("returns nothing before the array opens", () => {
    const parser = createIncrementalJsonParser();
    expect(parser.parse('{"other": 1}', "segments")).toHaveLength(0);
  });

  it("reset forgets cached scan state", () => {
    const parser = createIncrementalJsonParser();
    parser.parse('{"segments": [{"text": "a"}]}', "segments");
    parser.reset();
    expect(parser.parse('{"segments": [{"text": "b"}]}', "segments")).toHaveLength(1);
  });
});
