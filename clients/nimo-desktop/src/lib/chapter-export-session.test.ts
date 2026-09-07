import { describe, expect, it } from "vitest";

import { chapterExportExtension, createChapterExportRequest } from "./chapter-export-session";

describe("chapter export session", () => {
  it("keeps the PySide6 empty-list convention for all completed chapters", () => {
    expect(createChapterExportRequest({
      format: "markdown",
      range: "all",
      selectedChapters: new Set([1, 2, 3, 4]),
      outputDirectory: "/tmp/nimo-export",
      bookTitle: "测试长篇",
      defaultBookTitle: "测试长篇",
    })).toEqual({
      format: "markdown",
      selectedChapters: [],
      outputDirectory: "/tmp/nimo-export",
      bookTitle: "测试长篇",
    });
  });

  it("keeps custom selection ordered and falls back to the initialized book title", () => {
    const request = createChapterExportRequest({
      format: "epub",
      range: "custom",
      selectedChapters: new Set([4, 1, 3]),
      outputDirectory: "/tmp/nimo-export-selected",
      bookTitle: "   ",
      defaultBookTitle: "测试长篇",
    });

    expect(request.selectedChapters).toEqual([1, 3, 4]);
    expect(request.bookTitle).toBe("测试长篇");
    expect(chapterExportExtension(request.format)).toBe("epub");
  });
});
