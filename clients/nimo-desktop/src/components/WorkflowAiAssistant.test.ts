import { describe, expect, it } from "vitest";

import { buildWorkflowInlineDiffSegments } from "./WorkflowAiAssistant";

describe("workflow AI comparison", () => {
  it("preserves shared Chinese prose and exposes changed text as delete/insert segments", () => {
    expect(buildWorkflowInlineDiffSegments("雨夜的录音停在门外。", "雨夜的录音在门外戛然而止。")).toEqual([
      { kind: "equal", text: "雨夜的录音" },
      { kind: "delete", text: "停" },
      { kind: "equal", text: "在门外" },
      { kind: "insert", text: "戛然而止" },
      { kind: "equal", text: "。" },
    ]);
  });

  it("uses a bounded comparison for long fields without losing the stable edges", () => {
    const before = `${"甲".repeat(400)}旧核心${"乙".repeat(400)}`;
    const after = `${"甲".repeat(400)}新核心${"乙".repeat(400)}`;

    expect(buildWorkflowInlineDiffSegments(before, after)).toEqual([
      { kind: "equal", text: "甲".repeat(400) },
      { kind: "delete", text: "旧" },
      { kind: "insert", text: "新" },
      { kind: "equal", text: `核心${"乙".repeat(400)}` },
    ]);
  });
});
