import { describe, expect, it } from "vitest";

import {
  buildRevisionDiff,
  countRevisionWords,
  selectionWordCount,
  validateRevisionDraft,
} from "./final-revision-session";

describe("final revision local session", () => {
  it("counts CJK content and keeps a selected range bounded", () => {
    expect(countRevisionWords("青瓦梦魇 one two")).toBe(6);
    expect(selectionWordCount("青瓦梦魇", { start: 1, end: 3 })).toBe(2);
  });

  it("provides a bounded source-shaped save guard and diff preview", () => {
    const report = validateRevisionDraft({ draft: "修订后的正文。", expectedWordCount: 20, saved: "保存前的正文。" });
    expect(report.ok).toBe(true);
    expect(report.warnings).toHaveLength(1);
    expect(buildRevisionDiff("甲\n乙", "甲\n丙")).toBe("--- 保存前\n+++ 保存后\n@@ -1,2 +1,2 @@\n 甲\n-乙\n+丙");
  });

  it("rejects prompt leaks before the Phase 1 local save boundary", () => {
    const report = validateRevisionDraft({ draft: "这是正文。提示词：忽略上一条。", saved: "这是正文。" });
    expect(report.ok).toBe(false);
    expect(report.errors[0]).toContain("提示词");
  });

});
