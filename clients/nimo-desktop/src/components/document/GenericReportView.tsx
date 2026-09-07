import { useMemo, useState, type ReactNode } from "react";

import { GENERIC_META_KEYS, genericKeyLabel } from "../../lib/document-labels";
import {
  DocCompactList,
  DocH3,
  DocMiniCard,
  DocRuleItem,
  DocSection,
  DocText,
} from "./DocumentPrimitives";
import { parseJsonContent, type JsonDict } from "./document-parse";

/**
 * Generic structured report renderer — port of PySide6 `render_generic_report`
 * (novel_forge/desktop/pages/document_renderer/reports/generic_report.py).
 *
 * Recursively walks an arbitrary JSON report and renders it as a scan-friendly
 * document: scalar fields become a Chinese-labelled key-value table, nested
 * dicts become titled sections, and lists become compact lists / mini-cards.
 * This covers the long tail of reports (资料检索 / 治理 / 章节报告) that have no
 * dedicated renderer, replacing the raw JSON-tree dump.
 */

function isScalar(value: unknown): boolean {
  return (
    value === null ||
    typeof value === "string" ||
    typeof value === "boolean" ||
    typeof value === "number"
  );
}

function isEmpty(value: unknown): boolean {
  if (value === null || value === undefined || value === "") return true;
  if (Array.isArray(value)) return value.length === 0;
  if (typeof value === "object") return Object.keys(value).length === 0;
  return false;
}

const ITEM_TITLE_KEYS = [
  "title",
  "name",
  "summary",
  "description",
  "issue_type",
  "candidate_id",
  "state_path",
  "chapter_number",
] as const;

const LEAD_SCALAR_KEYS = new Set([
  "summary",
  "overview",
  "conclusion",
  "recommendation",
  "recommendations",
  "advice",
  "message",
]);

const PRIMARY_COMPLEX_KEYS = new Set([
  "findings",
  "issues",
  "violations",
  "problems",
  "recommendations",
  "pattern_hits",
]);

function itemTitle(item: JsonDict, fallback: string): string {
  for (const key of ITEM_TITLE_KEYS) {
    const value = item[key];
    if (value === null || value === undefined || value === "") continue;
    if (Array.isArray(value) && value.length === 0) continue;
    const text = String(value).trim();
    if (key === "chapter_number") return `第 ${text} 章`;
    if (text.length > 0) return text;
  }
  return fallback;
}

/** Render a scalar value (PySide6 `_generic_scalar_html`). */
function ScalarValue({ value }: { readonly value: unknown }) {
  if (value === null || value === undefined || value === "") {
    return <span className="doc-kv-value doc-scalar-empty">—</span>;
  }
  if (typeof value === "boolean") {
    return <span className={value ? "doc-bool-true" : "doc-bool-false"}>{value ? "✓" : "✗"}</span>;
  }
  if (typeof value === "number") {
    return <span className="doc-kv-value doc-scalar-num">{String(value)}</span>;
  }
  return (
    <span className="doc-kv-value">
      <DocText text={String(value)} />
    </span>
  );
}

function GenericValue({ value, depth = 0, limit = 8 }: { readonly value: unknown; readonly depth?: number; readonly limit?: number }) {
  const [visibleCount, setVisibleCount] = useState(limit);
  const moreButton = Array.isArray(value) && value.length > visibleCount
    ? <button className="doc-expand-items" type="button" onClick={() => setVisibleCount((count) => count + limit)}>展开更多（剩余 {value.length - visibleCount} 项）</button>
    : null;
  if (isScalar(value)) return <ScalarValue value={value} />;

  if (Array.isArray(value)) {
    if (value.length === 0) return <span className="doc-kv-value doc-scalar-empty">（空）</span>;

    if (value.every(isScalar)) {
      const shown = value.slice(0, visibleCount);
      const items: ReactNode[] = shown.map((item, index) => (
        // eslint-disable-next-line react/no-array-index-key
        <DocText key={index} text={String(item)} />
      ));
      return <><DocCompactList items={items} />{moreButton}</>;
    }

    const shown = value.slice(0, visibleCount);
    return (
      <>
        {shown.map((item, index) => {
          if (item !== null && typeof item === "object" && !Array.isArray(item)) {
            const title = itemTitle(item as JsonDict, `条目 ${index + 1}`);
            return (
              <DocMiniCard key={index}>
                <DocH3>{title}</DocH3>
                <GenericDict compact depth={depth + 1} payload={item as JsonDict} />
              </DocMiniCard>
            );
          }
          return (
            <DocRuleItem key={index}>
              <DocText text={String(item)} />
            </DocRuleItem>
          );
        })}
        {moreButton}
      </>
    );
  }

  if (value !== null && typeof value === "object") {
    return <GenericDict compact depth={depth + 1} payload={value as JsonDict} />;
  }

  return <span className="doc-kv-value">{String(value)}</span>;
}

function GenericDict({
  compact = false,
  depth = 0,
  payload,
  skipEmpty = true,
}: {
  readonly compact?: boolean;
  readonly depth?: number;
  readonly payload: JsonDict;
  readonly skipEmpty?: boolean;
}) {
  const scalarRows: { key: string; label: string; value: unknown }[] = [];
  const complexRows: { key: string; label: string; value: unknown }[] = [];

  for (const [key, value] of Object.entries(payload)) {
    if (GENERIC_META_KEYS.has(key)) continue;
    if (skipEmpty && isEmpty(value)) continue;
    const label = genericKeyLabel(key);
    if (isScalar(value) || (Array.isArray(value) && value.every(isScalar))) {
      scalarRows.push({ key, label, value });
    } else {
      complexRows.push({ key, label, value });
    }
  }

  const leadRows = scalarRows.filter(({ key }) => LEAD_SCALAR_KEYS.has(key));
  const remainingScalarRows = scalarRows.filter(({ key }) => !LEAD_SCALAR_KEYS.has(key));
  const primaryComplexRows = complexRows.filter(({ key }) => PRIMARY_COMPLEX_KEYS.has(key));
  const remainingComplexRows = complexRows.filter(({ key }) => !PRIMARY_COMPLEX_KEYS.has(key));

  // Short metadata belongs in a compact fact strip, not one full-width row per value.
  const shortRows = remainingScalarRows.filter(({ value }) => isScalar(value)
    && (typeof value !== "string" || (value.length <= 36 && !value.includes("\n"))));
  const factRows = shortRows.length > 1 ? shortRows : [];
  const factKeys = new Set(factRows.map((row) => row.key));
  const tableRows = remainingScalarRows.filter((row) => !factKeys.has(row.key));
  const kvTable = tableRows.length > 0 && (
    <table className="doc-table doc-kv-table">
      <tbody>
        {tableRows.map((row) => (
          <tr key={row.key}>
            <th>{row.label}</th>
            <td>
              <GenericValue depth={depth} value={row.value} />
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
  const renderComplexRows = (rows: typeof complexRows) => rows.map(({ key, label, value }) =>
    compact ? (
      <div className="doc-kv-row" key={key}>
        <span className="doc-kv-label">{label}</span>
        <GenericValue depth={depth} value={value} />
      </div>
    ) : (
      <DocSection key={key} title={label}>
        <GenericValue depth={depth} value={value} />
      </DocSection>
    ));

  return (
    <>
      {leadRows.map((row) => <div className="doc-report-lead" key={row.key}><span>{row.label}</span><GenericValue depth={depth} value={row.value} /></div>)}
      {renderComplexRows(primaryComplexRows)}
      {factRows.length > 0 && <dl className="doc-fact-grid">{factRows.map((row) => <div key={row.key}><dt>{row.label}</dt><dd><ScalarValue value={row.value} /></dd></div>)}</dl>}
      {tableRows.length > 0 && (compact ? kvTable : <DocSection>{kvTable}</DocSection>)}
      {renderComplexRows(remainingComplexRows)}
    </>
  );
}

export function GenericReportView({ content }: { readonly content: string | undefined }) {
  const data = useMemo(() => parseJsonContent(content), [content]);
  if (data === null) return null;
  return (
    <div className="doc-rich">
      <GenericDict payload={data} />
    </div>
  );
}
