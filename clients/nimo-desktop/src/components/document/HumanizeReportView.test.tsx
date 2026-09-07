import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import { ReaderArtifactDocument } from "../ReaderArtifactDocument";
import { GenericReportView } from "./GenericReportView";
import { HumanizeReportView, parseHumanizeReport } from "./HumanizeReportView";
import { TextRevisionDiffView } from "./TextRevisionDiffView";

const humanizeContent = JSON.stringify({
  chapter_number: 4,
  source_text_hash: "abc123",
  summary: "已完成候选裁判，请先核对原文证据。",
  total_hits: 2,
  critical_hits: 0,
  patchable_hits: 1,
  unpatchable_hits: 1,
  humanize_score: 4.5,
  hits_by_category: { "标点习惯": 1, "抽象判断": 1 },
  pattern_hits: [
    { pattern_id: "em_dash_overuse", pattern_name: "破折号滥用", category: "标点习惯", severity: "low", evidence_quote: "他看清了——那是时间戳。", paragraph_index: 8, suggestion: "他看清了：那是时间戳。", actionable: true, confidence: 0.9, source: "local" },
    { pattern_id: "significance_inflation", pattern_name: "意义抬高", category: "抽象判断", severity: "high", evidence_quote: "这一刻注定改变所有人的命运。", paragraph_index: 5, suggestion: "他把时间戳抄进笔记。", actionable: false, confidence: 0.78, source: "llm" },
  ],
});

describe("content-first report readers", () => {
  it("presents paragraph evidence and repair guidance before provenance fields", () => {
    const parsed = parseHumanizeReport(humanizeContent);
    expect(parsed?.hits.map((hit) => hit.patternName)).toEqual(["意义抬高", "破折号滥用"]);

    const html = renderToStaticMarkup(<HumanizeReportView content={humanizeContent} />);
    expect(html).toContain("第 6 段");
    expect(html).toContain("这一刻注定改变所有人的命运");
    expect(html).toContain("他把时间戳抄进笔记");
    expect(html).toContain("需人工复核");
    expect(html).toContain('class="humanize-report-score"');
    expect(html).not.toContain("pattern_hits");
    expect(html).not.toContain("humanize_score");
  });

  it("keeps a long source path separate from compact toolbar actions", () => {
    const html = renderToStaticMarkup(<ReaderArtifactDocument
      artifact={{
        id: "humanize",
        label: "拟人化",
        caption: "AI 痕迹证据",
        sourceLabel: "reports/revisions/chapter_004_humanize_scan_with_paragraph_evidence.json",
        paragraphs: [],
        facts: [],
        format: "json",
        content: humanizeContent,
      }}
      projectTitle="书稿"
    />);
    expect(html).toContain('class="reader-source-heading"');
    expect(html).toContain('class="reader-source-actions"');
    expect(html).toContain("chapter_004_humanize_scan_with_paragraph_evidence.json");
    expect(html).not.toContain("<h1>拟人化</h1>");
  });

  it("keeps the revision score inline and prioritizes generic report conclusions", () => {
    const onViewRaw = vi.fn();
    const diff = renderToStaticMarkup(<TextRevisionDiffView content={JSON.stringify({
      artifact_type: "text_revision_diff",
      label_before: "原文",
      label_after: "拟人化层输出",
      similarity_ratio: 0.609,
      additions: 9,
      deletions: 9,
      hunks: [{ tag: "replace", a_text: "旧句", b_text: "新句" }],
    })} onViewRaw={onViewRaw} />);
    expect(diff).toContain('class="reader-text-diff-heading"');
    expect(diff).toContain("相似度 <strong>60.9%</strong>");
    expect(diff).toContain("变更详情");
    expect(onViewRaw).not.toHaveBeenCalled();

    const generic = renderToStaticMarkup(<GenericReportView content={JSON.stringify({ score: 9.2, count: 3, summary: "先看这条结论", details: { source: "report" } })} />);
    expect(generic.indexOf("先看这条结论")).toBeLessThan(generic.indexOf("9.2"));
    expect(generic).toContain('class="doc-report-lead"');
  });
});
