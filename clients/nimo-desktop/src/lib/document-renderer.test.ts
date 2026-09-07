import { describe, expect, it } from "vitest";

import { parseJsonDocument, parseMarkdownBlocks, safeDocumentHref, summarizeJsonDocument, tokenizeInline } from "./document-renderer";

describe("document renderer parser", () => {
  it("groups source-style markdown into semantic blocks", () => {
    const blocks = parseMarkdownBlocks("# 标题\n\n正文第一行\n正文第二行\n\n- 证据\n- 后果\n\n```json\n{\"ok\": true}\n```");
    expect(blocks).toMatchObject([
      { kind: "heading", level: 1, text: "标题" },
      { kind: "paragraph", text: "正文第一行\n正文第二行" },
      { kind: "unordered-list", items: ["证据", "后果"] },
      { kind: "code", language: "json", text: "{\"ok\": true}" },
    ]);
  });

  it("recognizes tables and keeps cell content as text", () => {
    expect(parseMarkdownBlocks("字段 | 状态\n--- | ---\n计划 | 已确认")).toEqual([{ kind: "table", headers: ["字段", "状态"], rows: [["计划", "已确认"]] }]);
  });

  it("does not turn unsafe document links into navigation", () => {
    expect(safeDocumentHref("javascript:alert(1)")).toBeNull();
    expect(tokenizeInline("[安全](https://example.com) [危险](javascript:alert(1))")).toMatchObject([{ kind: "link", href: "https://example.com" }, { kind: "text" }, { kind: "link", href: null }]);
  });

  it("keeps emphasis and revision marks as safe inline data", () => {
    expect(tokenizeInline("**关键证据**、*人物迟疑*与~~旧结论~~")).toEqual([
      { kind: "strong", value: "关键证据" },
      { kind: "text", value: "、" },
      { kind: "emphasis", value: "人物迟疑" },
      { kind: "text", value: "与" },
      { kind: "delete", value: "旧结论" },
    ]);
  });

  it("retains malformed JSON as an explicit error instead of hiding diagnostics", () => {
    expect(parseJsonDocument("{broken")).toMatchObject({ error: expect.any(String) });
  });

  it("summarizes JSON collections for source-shaped status bars", () => {
    expect(summarizeJsonDocument('{"one":1,"two":2}')).toEqual({ count: 2, kindLabel: "个顶层字段" });
    expect(summarizeJsonDocument("[1,2,3]")).toEqual({ count: 3, kindLabel: "个数组元素" });
    expect(summarizeJsonDocument("true")).toEqual({ count: 1, kindLabel: "个根值" });
    expect(summarizeJsonDocument("{broken")).toBeNull();
  });
});
