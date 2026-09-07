import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { formatDocumentSize, RichDocumentViewer } from "./RichDocumentViewer";

describe("RichDocumentViewer", () => {
  it("mirrors the source toolbar and status metadata for JSON documents", () => {
    const html = renderToStaticMarkup(
      <RichDocumentViewer
        document={{
          title: "质量评估",
          sourceLabel: "reports/chapter_004_eval.json",
          format: "json",
          content: JSON.stringify({ score: 8.3, status: "pass" }),
          byteSize: 2_048,
          modifiedAtLabel: "2026-07-26 14:20",
        }}
      />,
    );

    expect(html).toContain("复制全文");
    expect(html).toContain("在文件夹中显示");
    expect(html).toContain("disabled");
    expect(html).toContain("切换原始 JSON");
    expect(html).toContain("文件大小 2.0 KB");
    expect(html).toContain("修改时间 2026-07-26 14:20");
    expect(html).toContain("2 个顶层字段");
    expect(html).not.toContain("document-json-toolbar");
  });

  it("matches the PySide file-size thresholds", () => {
    expect(formatDocumentSize(512)).toBe("512 B");
    expect(formatDocumentSize(1_536)).toBe("1.5 KB");
    expect(formatDocumentSize(2 * 1_024 * 1_024)).toBe("2.0 MB");
  });
});
