import type { ProjectReaderView } from "@nimo/engine-contracts";
import { describe, expect, it } from "vitest";

import { chapterVersionsFromReader, createChapterVersionComparison } from "./chapter-version-diff";

const reader: ProjectReaderView = {
  modeLabel: "长篇项目",
  projectId: "casebook",
  projectTitle: "版本链测试",
  tabs: [{
    artifacts: [
      {
        caption: "DRAFT 原稿",
        content: "雨声压在高架桥底。\n她没有立刻拨给周砚。",
        facts: [],
        id: "chapter-4-draft-v0_draft",
        label: "DRAFT 原稿",
        paragraphs: [],
        sourceLabel: "drafts/chapter_004/v0_draft.md",
      },
      {
        caption: "初稿成章",
        content: "雨声压在高架桥底。\n她并无立刻拨给周砚。",
        facts: [],
        id: "chapter-4-draft-v1_wave",
        label: "初稿成章",
        paragraphs: [],
        sourceLabel: "drafts/chapter_004/v1_wave.md",
      },
      {
        caption: "已归档正文 · 4,216 字",
        content: "雨声压在高架桥底。\n她并无立刻拨给周砚。",
        facts: [{ label: "正文长度", value: "4,216 字" }],
        id: "chapter-4",
        label: "第 4 章正文",
        paragraphs: [],
        sourceLabel: "chapters/chapter_004.md",
      },
      {
        caption: "质量报告",
        facts: [],
        id: "chapter-4-report-quality",
        label: "质量报告",
        paragraphs: ["忽略报告"],
        sourceLabel: "reports/chapter_004_quality.json",
      },
    ],
    id: "chapters",
    label: "章节",
  }],
  updatedLabel: "刚刚同步",
};

describe("chapter version diff", () => {
  it("derives the complete chapter version chain from the Engine reader model", () => {
    expect(chapterVersionsFromReader(reader, 4)).toMatchObject([
      { id: "chapter-4-draft-v0_draft", label: "DRAFT 原稿", version: 0 },
      { id: "chapter-4-draft-v1_wave", label: "初稿成章", version: 1 },
      { id: "chapter-4", label: "最终版本", version: 100, wordCount: 4216 },
    ]);
  });

  it("keeps a same-version submit as a no-op", () => {
    const versions = chapterVersionsFromReader(reader, 4);
    expect(createChapterVersionComparison(4, versions[0]!, versions[0]!)).toBeNull();
  });

  it("computes a readable line diff from Engine-supplied version content", () => {
    const versions = chapterVersionsFromReader(reader, 4);
    const comparison = createChapterVersionComparison(4, versions[0]!, versions[1]!);

    expect(comparison).toMatchObject({ additions: 1, deletions: 1, similarityRatio: 0.5 });
    expect(comparison?.hunks[0]?.segments).toEqual(expect.arrayContaining([
      { kind: "deletion", text: "她没有立刻拨给周砚。\n" },
      { kind: "addition", text: "她并无立刻拨给周砚。\n" },
    ]));
  });
});
