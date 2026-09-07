import { useMemo } from "react";

import { parseJsonContent, type JsonDict } from "./document-parse";

type RevisionDiffTag = "delete" | "equal" | "insert" | "replace";

interface RevisionDiffHunk {
  readonly aText: string;
  readonly bText: string;
  readonly tag: RevisionDiffTag;
}

interface TextRevisionDiffDocument {
  readonly additions: number | null;
  readonly changeRatio: number | null;
  readonly changeRatioCap: number | null;
  readonly deletions: number | null;
  readonly hunks: readonly RevisionDiffHunk[];
  readonly labelAfter: string;
  readonly labelBefore: string;
  readonly patchesApplied: number | null;
  readonly similarity: number | null;
  readonly status: string;
}

function asText(value: unknown): string {
  return typeof value === "string" ? value : value === null || value === undefined ? "" : String(value);
}

function asFiniteNumber(value: unknown): number | null {
  const number = typeof value === "number" ? value : typeof value === "string" ? Number(value) : NaN;
  return Number.isFinite(number) ? number : null;
}

function asTag(value: unknown): RevisionDiffTag | null {
  return value === "delete" || value === "equal" || value === "insert" || value === "replace" ? value : null;
}

function parseHunks(value: unknown): readonly RevisionDiffHunk[] {
  if (!Array.isArray(value)) return [];
  return value.flatMap((item) => {
    if (item === null || typeof item !== "object" || Array.isArray(item)) return [];
    const source = item as JsonDict;
    const tag = asTag(source.tag);
    if (tag === null) return [];
    const aText = asText(source.a_text);
    const bText = asText(source.b_text);
    return aText.trim().length > 0 || bText.trim().length > 0 ? [{ aText, bText, tag }] : [];
  });
}

/** Recover a readable comparison when an older report only carries unified diff text. */
function hunksFromUnifiedDiff(value: unknown): readonly RevisionDiffHunk[] {
  if (typeof value !== "string" || value.trim().length === 0) return [];
  const hunks: RevisionDiffHunk[] = [];
  let deletions: string[] = [];
  let additions: string[] = [];
  const flushChanges = () => {
    if (deletions.length === 0 && additions.length === 0) return;
    hunks.push({
      aText: deletions.join("\n"),
      bText: additions.join("\n"),
      tag: deletions.length > 0 && additions.length > 0 ? "replace" : deletions.length > 0 ? "delete" : "insert",
    });
    deletions = [];
    additions = [];
  };
  for (const line of value.split("\n")) {
    if (line.startsWith("--- ") || line.startsWith("+++ ") || line.startsWith("@@")) continue;
    if (line.startsWith("-")) {
      deletions.push(line.slice(1));
      continue;
    }
    if (line.startsWith("+")) {
      additions.push(line.slice(1));
      continue;
    }
    flushChanges();
    if (line.startsWith(" ") && line.slice(1).trim().length > 0) hunks.push({ aText: line.slice(1), bText: line.slice(1), tag: "equal" });
  }
  flushChanges();
  return hunks;
}

export function parseTextRevisionDiff(content: string | undefined): TextRevisionDiffDocument | null {
  const data = parseJsonContent(content);
  if (data?.artifact_type !== "text_revision_diff") return null;
  const parsedHunks = parseHunks(data.hunks);
  const metadata = data.metadata !== null && typeof data.metadata === "object" && !Array.isArray(data.metadata)
    ? data.metadata as JsonDict
    : {};
  return {
    additions: asFiniteNumber(data.additions),
    changeRatio: asFiniteNumber(data.change_ratio),
    changeRatioCap: asFiniteNumber(metadata.change_ratio_cap),
    deletions: asFiniteNumber(data.deletions),
    hunks: parsedHunks.length > 0 ? parsedHunks : hunksFromUnifiedDiff(data.unified_diff),
    labelAfter: asText(data.label_after).trim() || "修订后",
    labelBefore: asText(data.label_before).trim() || "原文",
    patchesApplied: asFiniteNumber(data.patches_applied),
    similarity: asFiniteNumber(data.similarity_ratio),
    status: asText(data.status).trim(),
  };
}

function splitParagraphs(text: string): readonly string[] {
  return text.split(/\n\s*\n/u).map((paragraph) => paragraph.trim()).filter(Boolean);
}

function InlineTextDiff({ after, before }: { readonly after: string; readonly before: string }) {
  const prefixLength = (() => {
    const maximum = Math.min(before.length, after.length);
    let index = 0;
    while (index < maximum && before[index] === after[index]) index += 1;
    return index;
  })();
  const suffixLength = (() => {
    const beforeTail = before.length - prefixLength;
    const afterTail = after.length - prefixLength;
    const maximum = Math.min(beforeTail, afterTail);
    let index = 0;
    while (index < maximum && before[before.length - 1 - index] === after[after.length - 1 - index]) index += 1;
    return index;
  })();
  const unchangedStart = before.slice(0, prefixLength);
  const removed = before.slice(prefixLength, before.length - suffixLength);
  const added = after.slice(prefixLength, after.length - suffixLength);
  const unchangedEnd = suffixLength > 0 ? before.slice(before.length - suffixLength) : "";
  return <><span>{unchangedStart}</span>{removed.length > 0 && <del className="reader-text-diff-delete">{removed}</del>}{added.length > 0 && <ins className="reader-text-diff-add">{added}</ins>}<span>{unchangedEnd}</span></>;
}

function HunkView({ hunk, index }: { readonly hunk: RevisionDiffHunk; readonly index: number }) {
  if (hunk.tag === "equal") return <>{splitParagraphs(hunk.aText).map((paragraph, paragraphIndex) => <p key={`${index}-${paragraphIndex}`}>{paragraph}</p>)}</>;
  if (hunk.tag === "replace") return <p><InlineTextDiff after={hunk.bText} before={hunk.aText} /></p>;
  if (hunk.tag === "delete") return <p><del className="reader-text-diff-delete">{hunk.aText}</del></p>;
  return <p><ins className="reader-text-diff-add">{hunk.bText}</ins></p>;
}

function statValue(value: number | null, prefix: string): string | null {
  return value === null ? null : `${prefix}${Math.max(0, Math.round(value))}`;
}

function asPercent(value: number | null): string | null {
  return value === null ? null : `${(Math.max(0, Math.min(1, value)) * 100).toFixed(1)}%`;
}

function diagnosticSummary(document: TextRevisionDiffDocument): readonly string[] {
  const details = [
    document.status === "accepted" ? "已采纳" : document.status === "rejected" ? "已回滚" : document.status,
    document.patchesApplied === null ? null : `补丁：${Math.max(0, Math.round(document.patchesApplied))}`,
    document.changeRatio === null ? null : `变更率：${asPercent(document.changeRatio)}`,
    document.changeRatioCap === null ? null : `上限：${asPercent(document.changeRatioCap)}`,
  ];
  return details.filter((detail): detail is string => detail !== null && detail.length > 0);
}

/**
 * PySide6-aligned renderer for `text_revision_diff` reports.  The reading
 * surface gives the inline red/green diff the whole pane; machine statistics
 * stay available in a compact disclosure rather than becoming a key/value table.
 */
export function TextRevisionDiffView({ content, onViewRaw }: { readonly content: string; readonly onViewRaw: () => void }) {
  const document = useMemo(() => parseTextRevisionDiff(content), [content]);
  if (document === null) return null;
  const similarityLabel = asPercent(document.similarity);
  const metrics = [
    similarityLabel === null ? null : `相似度 ${similarityLabel}`,
    statValue(document.additions, "+"),
    statValue(document.deletions, "−"),
  ].filter((value): value is string => value !== null);
  const statusLabel = document.status === "accepted" ? "已采纳" : document.status === "rejected" ? "已回滚" : document.status;
  const diagnostics = diagnosticSummary(document);
  return <article aria-label={`${document.labelBefore}至${document.labelAfter}的修订对比`} className="reader-text-revision-diff">
    <header className="reader-text-diff-summary">
      <div className="reader-text-diff-heading">
        <h2>版本对比</h2>
        <div className="reader-text-diff-score">
          <span>相似度 <strong>{similarityLabel ?? "—"}</strong></span>
          <span className="is-addition">{statValue(document.additions, "+") ?? "+0"}</span>
          <span className="is-deletion">{statValue(document.deletions, "−") ?? "−0"}</span>
          <small>{document.labelBefore} → {document.labelAfter}</small>
        </div>
      </div>
      {diagnostics.length > 0 && <p className={statusLabel === "已采纳" ? "is-accepted" : "reader-text-diff-status"}>{diagnostics.join(" · ")}</p>}
    </header>
    <span className="sr-only">删除内容以朱红删除线标记，新增内容以青绿底纹标记。</span>
    <div className="reader-text-diff-flow">
      <h3>变更详情</h3>
      {document.hunks.length > 0
        ? document.hunks.map((hunk, index) => <HunkView hunk={hunk} index={index} key={`${hunk.tag}-${index}`} />)
        : <p className="reader-text-diff-empty">此修订报告未保留可展示的差异片段。可查看原始数据核对完整记录。</p>}
    </div>
    <footer className="reader-text-diff-footer">
      <details>
        <summary>变更统计{metrics.length > 0 ? ` · ${metrics.join(" · ")}` : ""}</summary>
        <dl>
          <div><dt>原稿</dt><dd>{document.labelBefore}</dd></div>
          <div><dt>修订稿</dt><dd>{document.labelAfter}</dd></div>
          {document.additions !== null && <div><dt>新增</dt><dd>+{Math.max(0, Math.round(document.additions))}</dd></div>}
          {document.deletions !== null && <div><dt>删减</dt><dd>−{Math.max(0, Math.round(document.deletions))}</dd></div>}
        </dl>
      </details>
      <button className="button button-quiet" onClick={onViewRaw} type="button">查看原始数据</button>
    </footer>
  </article>;
}
