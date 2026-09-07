import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import type { StepArtifactFile } from "@nimo/engine-contracts";

import { readerSectionForTaskArtifact } from "../lib/reader-sections";
import { ArtifactViewer } from "./ArtifactViewer";

function artifact(path: string, label: string, content: string, wordCount?: number): StepArtifactFile {
  return {
    path,
    label,
    content,
    format: path.endsWith(".md") ? "markdown" : "json",
    ...(wordCount === undefined ? {} : { wordCount }),
  };
}

describe("ArtifactViewer", () => {
  it("reuses the reader shelf and rich document host for a narrative blueprint", () => {
    const html = renderToStaticMarkup(
      <ArtifactViewer
        artifacts={[artifact(
          "plans/narrative_blueprint.json",
          "叙事蓝图",
          JSON.stringify({ synopsis: "雨夜的城与旧线索", narrative_phases: [{ title: "开端", summary: "谜团浮现" }] }),
        )]}
      />,
    );

    expect(html).not.toContain("卷帙标签");
    expect(html).toContain("叙事蓝图");
    expect(html).toContain("雨夜的城与旧线索");
    expect(html).toContain("切换原始 JSON");
    expect(html).not.toContain("结构视图");
  });

  it("keeps the generic JSON renderer as a fallback for unclassified files", () => {
    const html = renderToStaticMarkup(
      <ArtifactViewer artifacts={[artifact("tmp/opaque.json", "临时产物", JSON.stringify({ value: "保留原始结构" }))]} />,
    );

    expect(html).toContain("结构视图");
    expect(html).toContain("保留原始结构");
  });

  it("shows category navigation only when there are multiple shelves", () => {
    const html = renderToStaticMarkup(<ArtifactViewer artifacts={[
      artifact("story_bible.json", "世界观", '{"summary":"城"}'),
      artifact("outline.json", "章节大纲", '{"chapters":[]}'),
    ]} />);
    expect(html).toContain("卷帙标签");
    expect(html).toContain("章节大纲");
  });

  it("uses a single file selector for unclassified artifacts", () => {
    const html = renderToStaticMarkup(<ArtifactViewer artifacts={[
      artifact("tmp/first.json", "第一份", '{"value":"第一份内容"}'),
      artifact("tmp/second.json", "第二份", '{"value":"第二份内容"}'),
    ]} />);
    expect(html.match(/role="tablist"/g)).toHaveLength(1);
    expect(html).toContain("第一份内容");
    expect(html).not.toContain("第二份内容");
  });

  it("uses the read-only graph for character artifacts", () => {
    const html = renderToStaticMarkup(<ArtifactViewer artifacts={[artifact("character_bible.json", "角色设定", JSON.stringify({characters: [
      {name: "沈昭", role: "protagonist", relationships: {陆衍: "共同调查旧案"}},
      {name: "陆衍", role: "deuteragonist", relationships: {沈昭: "共同调查旧案"}},
    ]}))]} />);
    expect(html).toContain("角色设定图谱阅读器");
    expect(html).toContain("2 位角色 · 1 条关系");
    expect(html).toContain("产物角色完整设定");
    expect(html).not.toContain("编辑角色");
    expect(html).not.toContain("is-relationship-editable");
  });

  it("shows the actual prose count for a chapter artifact", () => {
    const html = renderToStaticMarkup(
      <ArtifactViewer artifacts={[artifact("drafts/chapter_001/v1_wave.md", "章节草稿", "雨夜正文", 1341)]} />,
    );

    expect(html).toContain("实际字数 1,341 字");
    expect(html).toContain("实际字数");
  });
});

describe("readerSectionForTaskArtifact", () => {
  it.each([
    ["story_bible.json", "世界观", "foundation"],
    ["tmp/intermediate.md", "规格确认", "foundation"],
    ["reports/init_web_research.json", "资料检索", "research"],
    ["plans/narrative_blueprint.json", "叙事蓝图", "blueprint"],
    ["plans/chapter_design_matrix.json", "章节设计矩阵", "chapter_design"],
    ["outline.json", "章节大纲", "outline"],
    ["chapters/chapter_001.md", "第 1 章", "chapters"],
    ["reports/init_readiness.json", "初始化准入", "governance"],
    ["memory/chapter_001_summary.json", "章节摘要", "tracking"],
  ])("maps %s to the shared %s reader shelf", (path, label, sectionId) => {
    expect(readerSectionForTaskArtifact({ path, label })?.id).toBe(sectionId);
  });
});
