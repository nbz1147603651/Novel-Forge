import { useMemo } from "react";

import { parseJsonContent, type JsonDict } from "./document-parse";

type HumanizeSeverity = "critical" | "high" | "medium" | "low";

interface HumanizeHitView {
  readonly actionable: boolean;
  readonly category: string;
  readonly confidence: number | null;
  readonly evidence: string;
  readonly paragraphIndex: number;
  readonly patternName: string;
  readonly severity: HumanizeSeverity;
  readonly source: string;
  readonly suggestion: string;
}

interface HumanizeReportDocument {
  readonly chapterNumber: number | null;
  readonly criticalHits: number;
  readonly hits: readonly HumanizeHitView[];
  readonly hitsByCategory: readonly { readonly count: number; readonly label: string }[];
  readonly patchableHits: number;
  readonly score: number;
  readonly sourceTextHash: string;
  readonly summary: string;
  readonly totalHits: number;
  readonly unpatchableHits: number;
}

const SEVERITY_ORDER: Readonly<Record<HumanizeSeverity, number>> = {
  critical: 4,
  high: 3,
  medium: 2,
  low: 1,
};

const SEVERITY_LABEL: Readonly<Record<HumanizeSeverity, string>> = {
  critical: "严重",
  high: "高",
  medium: "中",
  low: "低",
};

const SOURCE_LABEL: Readonly<Record<string, string>> = {
  library: "内置模式库",
  library_user: "自定义模式库",
  local: "本地规则",
  llm: "模型裁判",
  merged: "规则与模型复核",
};

function asText(value: unknown): string {
  return typeof value === "string" ? value.trim() : value === null || value === undefined ? "" : String(value).trim();
}

function asNumber(value: unknown): number | null {
  const parsed = typeof value === "number" ? value : typeof value === "string" ? Number(value) : NaN;
  return Number.isFinite(parsed) ? parsed : null;
}

function asCount(value: unknown): number {
  return Math.max(0, Math.round(asNumber(value) ?? 0));
}

function asSeverity(value: unknown): HumanizeSeverity {
  return value === "critical" || value === "high" || value === "low" ? value : "medium";
}

function parseHit(value: unknown): HumanizeHitView | null {
  if (value === null || typeof value !== "object" || Array.isArray(value)) return null;
  const hit = value as JsonDict;
  const evidence = asText(hit.evidence_quote);
  const rawPatternName = asText(hit.pattern_name) || asText(hit.pattern_id);
  if (evidence.length === 0 && rawPatternName.length === 0) return null;
  const patternName = rawPatternName || "未命名模式";
  const paragraphIndex = Math.max(0, Math.round(asNumber(hit.paragraph_index) ?? 0));
  const confidence = asNumber(hit.confidence);
  return {
    actionable: hit.actionable === true,
    category: asText(hit.category) || "未分类",
    confidence: confidence === null ? null : Math.max(0, Math.min(1, confidence)),
    evidence,
    paragraphIndex,
    patternName,
    severity: asSeverity(hit.severity),
    source: asText(hit.source),
    suggestion: asText(hit.suggestion),
  };
}

export function parseHumanizeReport(content: string | undefined): HumanizeReportDocument | null {
  const data = parseJsonContent(content);
  if (data === null || !Array.isArray(data.pattern_hits) || asNumber(data.humanize_score) === null) return null;
  const hits = data.pattern_hits
    .map(parseHit)
    .filter((hit): hit is HumanizeHitView => hit !== null)
    .sort((left, right) => SEVERITY_ORDER[right.severity] - SEVERITY_ORDER[left.severity]
      || left.paragraphIndex - right.paragraphIndex);
  const categorySource = data.hits_by_category;
  const hitsByCategory = categorySource !== null && typeof categorySource === "object" && !Array.isArray(categorySource)
    ? Object.entries(categorySource as JsonDict)
      .map(([label, count]) => ({ label, count: asCount(count) }))
      .filter(({ count, label }) => count > 0 && label.trim().length > 0)
      .sort((left, right) => right.count - left.count || left.label.localeCompare(right.label, "zh-CN"))
    : [];
  const chapterNumber = asNumber(data.chapter_number);
  return {
    chapterNumber: chapterNumber === null ? null : Math.max(1, Math.round(chapterNumber)),
    criticalHits: asCount(data.critical_hits),
    hits,
    hitsByCategory,
    patchableHits: asCount(data.patchable_hits),
    score: Math.max(0, Math.min(10, asNumber(data.humanize_score) ?? 0)),
    sourceTextHash: asText(data.source_text_hash),
    summary: asText(data.summary),
    totalHits: Math.max(hits.length, asCount(data.total_hits)),
    unpatchableHits: asCount(data.unpatchable_hits),
  };
}

function confidenceLabel(confidence: number | null): string {
  return confidence === null ? "未记录置信度" : `置信度 ${Math.round(confidence * 100)}%`;
}

function sourceLabel(source: string): string {
  return SOURCE_LABEL[source] ?? (source || "来源未记录");
}

export function HumanizeReportView({ content }: { readonly content: string | undefined }) {
  const report = useMemo(() => parseHumanizeReport(content), [content]);
  if (report === null) return null;
  const chapterLabel = report.chapterNumber === null ? "本章" : `第 ${report.chapterNumber} 章`;
  return (
    <section aria-label="拟人化诊断报告" className="humanize-report doc-rich">
      <header className="humanize-report-overview">
        <div>
          <span className="humanize-report-kicker">{chapterLabel} · 拟人化诊断</span>
          <h2>{report.totalHits > 0 ? `定位到 ${report.totalHits} 处待处理痕迹` : "未定位到需处理的痕迹"}</h2>
          <p>{report.summary || (report.totalHits > 0 ? "请按下方证据逐项核对修复边界。" : "当前正文未保留真实 AI 痕迹候选。")}</p>
        </div>
        <div aria-label={`拟人化评分 ${report.score.toFixed(1)} 分`} className="humanize-report-score">
          <span>拟人化评分</span><strong>{report.score.toFixed(1)}</strong><small>/ 10</small>
        </div>
      </header>

      <dl aria-label="拟人化诊断摘要" className="humanize-report-metrics">
        <div><dt>真实命中</dt><dd>{report.totalHits}</dd></div>
        <div><dt>严重命中</dt><dd>{report.criticalHits}</dd></div>
        <div><dt>可局部修复</dt><dd>{report.patchableHits}</dd></div>
        <div><dt>需人工复核</dt><dd>{report.unpatchableHits}</dd></div>
      </dl>

      {report.hitsByCategory.length > 0 && <div aria-label="命中分类" className="humanize-report-categories">
        {report.hitsByCategory.map(({ count, label }) => <span key={label}>{label} <strong>{count}</strong></span>)}
      </div>}

      <section className="humanize-report-findings">
        <header><h3>问题定位与修复</h3><span>按严重度排列，段落序号从 1 开始</span></header>
        {report.hits.length === 0
          ? <p className="humanize-report-empty">没有需要展开的命中证据。</p>
          : report.hits.map((hit, index) => <article className={`humanize-report-hit is-${hit.severity}`} key={`${hit.patternName}-${hit.paragraphIndex}-${index}`}>
            <header>
              <div><span>第 {hit.paragraphIndex + 1} 段</span><strong>{hit.patternName}</strong></div>
              <div><span>{hit.category}</span><span className={`is-${hit.severity}`}>{SEVERITY_LABEL[hit.severity]}</span><span className={hit.actionable ? "is-actionable" : "is-review"}>{hit.actionable ? "可安全局部修复" : "需人工复核"}</span></div>
            </header>
            <blockquote>{hit.evidence || "报告未保留可展示的原文证据。"}</blockquote>
            <div className="humanize-report-suggestion">
              <strong>{hit.actionable ? "建议替换" : "处理建议"}</strong>
              <p>{hit.suggestion || "当前命中没有通过安全局部替换门槛；请结合上下文人工判断，不应自动改写。"}</p>
            </div>
            <footer><span>{sourceLabel(hit.source)}</span><span>{confidenceLabel(hit.confidence)}</span></footer>
          </article>)}
      </section>

      {report.sourceTextHash.length > 0 && <details className="humanize-report-provenance">
        <summary>报告依据</summary>
        <p><strong>正文版本哈希</strong><code>{report.sourceTextHash}</code></p>
        <p>完整机器字段可通过页面右上角“切换原始 JSON”核对。</p>
      </details>}
    </section>
  );
}
