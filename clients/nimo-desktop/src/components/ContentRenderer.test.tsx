import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { ContentRenderer } from "./ContentRenderer";

describe("ContentRenderer", () => {
  it("renders primitive JSON keys instead of dropping their labels", () => {
    const html = renderToStaticMarkup(
      <ContentRenderer
        document={{
          title: "阶段产物",
          sourceLabel: "session://artifact.json",
          format: "json",
          content: JSON.stringify({
            stage: "叙事蓝图",
            completed: true,
            nested: { deferred: "only after expansion" },
          }),
        }}
      />,
    );

    expect(html).toContain("document-json-key\">stage");
    expect(html).toContain("document-json-key\">completed");
    expect(html).toContain("叙事蓝图");
    expect(html).toContain("结构视图");
    expect(html).not.toContain("only after expansion");
  });

  it("renders rich markdown semantics without accepting source HTML", () => {
    const html = renderToStaticMarkup(
      <ContentRenderer
        document={{
          title: "阅读报告",
          sourceLabel: "report.md",
          format: "markdown",
          content: "## 阅读报告\n\n**证据**、*迟疑*与~~旧结论~~。\n\n<script>alert(1)</script>",
        }}
      />,
    );

    expect(html).toContain("<strong>证据</strong>");
    expect(html).toContain("<em>迟疑</em>");
    expect(html).toContain("<del>旧结论</del>");
    expect(html).toContain("&lt;script&gt;alert(1)&lt;/script&gt;");
    expect(html).not.toContain("<script>");
  });

  it("shows a notice instead of inlining binary artifacts", () => {
    const html = renderToStaticMarkup(
      <ContentRenderer
        document={{
          title: "章节成品音频",
          sourceLabel: "tts/audio/chapter_005/chapter_full.mp3",
          format: "binary",
          content: "[二进制文件 · 2.3 MB · 暂不支持在线预览]",
        }}
      />,
    );

    expect(html).toContain("document-binary-notice");
    expect(html).toContain("暂不支持在线预览");
    expect(html).toContain("2.3 MB");
  });

  it("degrades subtitle captions to a plain text preview", () => {
    const html = renderToStaticMarkup(
      <ContentRenderer
        document={{
          title: "章节字幕",
          sourceLabel: "tts/audio/chapter_005/chapter.srt",
          format: "subtitle",
          content: "1\n00:00:01,000 --> 00:00:03,000\n你好，世界",
        }}
      />,
    );

    expect(html).toContain("document-plain");
    expect(html).toContain("你好，世界");
  });
});
