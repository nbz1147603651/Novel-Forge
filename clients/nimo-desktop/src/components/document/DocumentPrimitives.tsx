import type { ReactNode } from "react";

/**
 * Document design-system primitives — the React counterpart of PySide6's
 * `_BASE_CSS_TEMPLATE` (novel_forge/desktop/pages/standalone/renderer_html.py).
 *
 * Each primitive maps 1:1 to a PySide6 document CSS class so the rich
 * document renderers reproduce the source reading experience (Chinese labels,
 * sections, badges, metric cards, score bars, serif prose). Colors come from
 * the shared `--nf-*` theme tokens (document-rich.css), which theme.ts writes
 * with the same names PySide6 uses (text.artifact -> --nf-text-artifact).
 */

/** Render a string with newlines converted to <br/> (PySide6 `nl2br`). */
export function DocText({ text }: { readonly text: string }) {
  const lines = text.split("\n");
  return (
    <>
      {lines.map((line, index) => (
        // eslint-disable-next-line react/no-array-index-key
        <span key={index}>
          {index > 0 && <br />}
          {line}
        </span>
      ))}
    </>
  );
}

export function DocH1({ children }: { readonly children: ReactNode }) {
  return <h1 className="doc-h1">{children}</h1>;
}

export function DocH2({ children }: { readonly children: ReactNode }) {
  return <h2 className="doc-h2">{children}</h2>;
}

export function DocH3({ children }: { readonly children: ReactNode }) {
  return <h3 className="doc-h3">{children}</h3>;
}

/** `.section` — a rounded content card, optionally titled (renders an h3). */
export function DocSection({ children, title }: { readonly children: ReactNode; readonly title?: string }) {
  return (
    <div className="doc-section">
      {title !== undefined && title.length > 0 && <DocH3>{title}</DocH3>}
      {children}
    </div>
  );
}

/** `.kv-row` — a label + value row; value string gets nl2br. */
export function DocKV({ label, value }: { readonly label: string; readonly value: string }) {
  return (
    <div className="doc-kv-row">
      <span className="doc-kv-label">{label}</span>
      <span className="doc-kv-value">
        <DocText text={value} />
      </span>
    </div>
  );
}

/** `.tag` / `.tag-muted` — a small badge. */
export function DocTag({ children, muted = false }: { readonly children: ReactNode; readonly muted?: boolean }) {
  return <span className={muted ? "doc-tag doc-tag-muted" : "doc-tag"}>{children}</span>;
}

/** A row of `.tag` badges with the source's wrapping margin. */
export function DocTagRow({ children }: { readonly children: ReactNode }) {
  return <div className="doc-tag-row">{children}</div>;
}

/** `.rule-item` — an accent left-border block (world rules etc.). */
export function DocRuleItem({ children }: { readonly children: ReactNode }) {
  return <div className="doc-rule-item">{children}</div>;
}

/** `.theme-item` — a success-tinted left-border block (core themes). */
export function DocThemeItem({ children }: { readonly children: ReactNode }) {
  return <div className="doc-theme-item">{children}</div>;
}

/** `.hint-block` — a dashed-border hint block (spec hints etc.). */
export function DocHintBlock({ children }: { readonly children: ReactNode }) {
  return <div className="doc-hint-block">{children}</div>;
}

/** `.metric-grid` — responsive grid of metric cards. */
export function DocMetricGrid({ children }: { readonly children: ReactNode }) {
  return <div className="doc-metric-grid">{children}</div>;
}

/** `.metric-card` — a label + large serif value. */
export function DocMetricCard({ label, value }: { readonly label: string; readonly value: ReactNode }) {
  return (
    <div className="doc-metric-card">
      <div className="doc-metric-label">{label}</div>
      <div className="doc-metric-value">{value}</div>
    </div>
  );
}

function scoreTier(score: number): "good" | "mid" | "bad" {
  if (score >= 8) return "good";
  if (score >= 6) return "mid";
  return "bad";
}

/**
 * `.score-bar` — a 0-10 score bar with traffic-light color (PySide6
 * `score_bar_html` + `score_color`): >=8 success, >=6 accent, else danger.
 */
export function DocScoreBar({ score, maxScore = 10 }: { readonly score: number; readonly maxScore?: number }) {
  const pct = Math.min(100, Math.max(0, (score / maxScore) * 100));
  const tier = scoreTier(score);
  return (
    <div className="doc-score-bar">
      <div className="doc-score-track">
        <div className={`doc-score-fill is-${tier}`} style={{ width: `${pct}%` }} />
      </div>
      <span className={`doc-score-value is-${tier}`}>{score.toFixed(1)}</span>
    </div>
  );
}

/** `.doc-table` — a styled data table (PySide6 `table/th/td`). */
export function DocTable({
  headers,
  rows,
}: {
  readonly headers: readonly ReactNode[];
  readonly rows: readonly (readonly ReactNode[])[];
}) {
  return (
    <div className="doc-table-wrap">
      <table className="doc-table">
        <thead>
          <tr>
            {headers.map((header, index) => (
              // eslint-disable-next-line react/no-array-index-key
              <th key={index}>{header}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, rowIndex) => (
            // eslint-disable-next-line react/no-array-index-key
            <tr key={rowIndex}>
              {row.map((cell, cellIndex) => (
                // eslint-disable-next-line react/no-array-index-key
                <td key={cellIndex}>{cell}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** `.doc-prose` — serif prose with 2em first-line indent (PySide6 `.prose-body`). */
export function DocProse({ text }: { readonly text: string }) {
  const paragraphs = text
    .split(/\n\s*\n/)
    .map((paragraph) => paragraph.trim())
    .filter((paragraph) => paragraph.length > 0);
  return (
    <div className="doc-prose">
      {paragraphs.map((paragraph, index) => (
        // eslint-disable-next-line react/no-array-index-key
        <p key={index}>{paragraph}</p>
      ))}
    </div>
  );
}

/** `.doc-mini-card` — a compact inset card (PySide6 `.mini-card`). */
export function DocMiniCard({ children }: { readonly children: ReactNode }) {
  return <div className="doc-mini-card">{children}</div>;
}

/** `.doc-compact-list` — a compact bulleted list (PySide6 `.compact-list`). */
export function DocCompactList({ items }: { readonly items: readonly ReactNode[] }) {
  return (
    <ul className="doc-compact-list">
      {items.map((item, index) => (
        // eslint-disable-next-line react/no-array-index-key
        <li key={index}>{item}</li>
      ))}
    </ul>
  );
}
