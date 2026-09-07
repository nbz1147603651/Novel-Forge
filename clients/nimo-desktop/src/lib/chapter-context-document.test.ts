import { describe, expect, it } from "vitest";

import {
  createChapterContextDocument,
  formatChapterContextValue,
  summarizeChapterContextValue,
} from "./chapter-context-document";

describe("chapter context documents", () => {
  const serializedExit = [
    '{"schema_version":"2.0","text":"芯片上的授权链指向旧城区档案室。","status":"open"}',
    '{"schema_version":"2.0","text":"门禁记录却显示沈岸从未拥有访问权限。","status":"open"}',
  ].join("; ");

  it("keeps narrative text while removing serialized transport metadata", () => {
    expect(formatChapterContextValue(serializedExit)).toBe(
      "芯片上的授权链指向旧城区档案室。\n\n门禁记录却显示沈岸从未拥有访问权限。",
    );
    expect(summarizeChapterContextValue(serializedExit)).toBe(
      "芯片上的授权链指向旧城区档案室。 门禁记录却显示沈岸从未拥有访问权限。",
    );
  });

  it("creates a markdown document for the shared rich renderer", () => {
    const document = createChapterContextDocument({
      title: "上一章实际结果",
      primaryLabel: "章节结果",
      preview: "沈岸带回一枚存储芯片。",
      secondaryLabel: "退出点",
      secondary: serializedExit,
    });

    expect(document).toMatchObject({
      title: "上一章实际结果",
      sourceLabel: "章台上下文",
      format: "markdown",
    });
    expect(document.content).toContain("## 章节结果");
    expect(document.content).toContain("## 退出点");
    expect(document.content).not.toContain("schema_version");
  });
});
