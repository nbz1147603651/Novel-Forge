import { useMemo, useState } from "react";

import type { RenderDocumentView } from "@nimo/engine-contracts";

import { parseJsonDocument, parseMarkdownBlocks, summarizeJsonValue, tokenizeInline, type JsonValue, type MarkdownBlock } from "../lib/document-renderer";

export type JsonDocumentMode = "tree" | "raw";

function InlineContent({ text }: { readonly text: string }) {
  return <>{tokenizeInline(text).map((token, index) => {
    const key = `${token.kind}-${index}`;
    if (token.kind === "code") return <code key={key}>{token.value}</code>;
    if (token.kind === "strong") return <strong key={key}>{token.value}</strong>;
    if (token.kind === "emphasis") return <em key={key}>{token.value}</em>;
    if (token.kind === "delete") return <del key={key}>{token.value}</del>;
    if (token.kind === "link") return token.href === null ? <span key={key}>{token.label}</span> : <a href={token.href} key={key} rel="noreferrer" target="_blank">{token.label}</a>;
    return token.value;
  })}</>;
}

function TextWithBreaks({ text }: { readonly text: string }) {
  return <>{text.split("\n").map((line, index) => <span key={index}>{index > 0 && <br />}<InlineContent text={line} /></span>)}</>;
}

function MarkdownBlockView({ block }: { readonly block: MarkdownBlock }) {
  if (block.kind === "heading") {
    const Tag = `h${block.level}` as "h1" | "h2" | "h3";
    return <Tag><InlineContent text={block.text} /></Tag>;
  }
  if (block.kind === "paragraph") return <p><TextWithBreaks text={block.text} /></p>;
  if (block.kind === "quote") return <blockquote><TextWithBreaks text={block.text} /></blockquote>;
  if (block.kind === "unordered-list") return <ul>{block.items.map((item, index) => <li key={index}><InlineContent text={item} /></li>)}</ul>;
  if (block.kind === "ordered-list") return <ol start={block.start}>{block.items.map((item, index) => <li key={index}><InlineContent text={item} /></li>)}</ol>;
  if (block.kind === "code") return <pre className="document-code"><code data-language={block.language || undefined}>{block.text}</code></pre>;
  if (block.kind === "table") return <div className="document-table-wrap"><table><thead><tr>{block.headers.map((header, index) => <th key={index}><InlineContent text={header} /></th>)}</tr></thead><tbody>{block.rows.map((row, rowIndex) => <tr key={rowIndex}>{block.headers.map((_, cellIndex) => <td key={cellIndex}><InlineContent text={row[cellIndex] ?? ""} /></td>)}</tr>)}</tbody></table></div>;
  return <hr />;
}

function MarkdownDocument({ content }: { readonly content: string }) {
  return <div className="rendered-markdown">{parseMarkdownBlocks(content).map((block, index) => <MarkdownBlockView block={block} key={`${block.kind}-${index}`} />)}</div>;
}

function JsonPrimitive({ value }: { readonly value: null | boolean | number | string }) {
  const kind = value === null ? "null" : typeof value;
  const text = typeof value === "string" ? value : JSON.stringify(value);
  return <span className={`document-json-value is-${kind}`}>{text}</span>;
}

function JsonCollectionTree({
  depth,
  entries,
  isArray,
  label,
}: {
  readonly depth: number;
  readonly entries: readonly (readonly [string, JsonValue])[];
  readonly isArray: boolean;
  readonly label?: string;
}) {
  const [open, setOpen] = useState(depth < 1);
  const collectionLabel = isArray ? `数组 · ${entries.length} 项` : `对象 · ${entries.length} 项`;
  return (
    <details
      className="document-json-tree"
      data-depth={depth}
      onToggle={(event) => setOpen(event.currentTarget.open)}
      open={open}
    >
      <summary>
        {label !== undefined && <span className="document-json-key">{label}</span>}
        <span className="document-json-kind">{collectionLabel}</span>
      </summary>
      {open && <div className="document-json-children">{entries.map(([key, entry]) => <div className="document-json-entry" key={key}><JsonValueTree depth={depth + 1} label={key} value={entry} /></div>)}</div>}
    </details>
  );
}

function JsonValueTree({ depth = 0, label, value }: { readonly depth?: number; readonly label?: string; readonly value: JsonValue }) {
  if (value === null || typeof value === "boolean" || typeof value === "number" || typeof value === "string") {
    return (
      <div className="document-json-leaf" data-depth={depth}>
        {label !== undefined && <><span className="document-json-key">{label}</span><span aria-hidden="true" className="document-json-separator">:</span></>}
        <JsonPrimitive value={value} />
      </div>
    );
  }
  const entries = Array.isArray(value) ? value.map((item, index) => [String(index), item] as const) : Object.entries(value);
  return <JsonCollectionTree depth={depth} entries={entries} isArray={Array.isArray(value)} {...(label === undefined ? {} : { label })} />;
}

function JsonDocument({
  content,
  mode: controlledMode,
  onModeChange,
  showToolbar,
}: {
  readonly content: string;
  readonly mode?: JsonDocumentMode;
  readonly onModeChange?: (mode: JsonDocumentMode) => void;
  readonly showToolbar: boolean;
}) {
  const [internalMode, setInternalMode] = useState<JsonDocumentMode>("tree");
  const mode = controlledMode ?? internalMode;
  const setMode = (nextMode: JsonDocumentMode) => {
    if (controlledMode === undefined) setInternalMode(nextMode);
    onModeChange?.(nextMode);
  };
  const result = useMemo(() => parseJsonDocument(content), [content]);
  if ("error" in result) return <section className="document-json-error"><strong>JSON 片段格式异常</strong><p>{result.error}</p><pre>{content}</pre></section>;
  const summary = showToolbar ? summarizeJsonValue(result.value) : null;
  return (
    <section className={`document-json-shell${showToolbar ? "" : " is-toolbarless"}`}>
      {showToolbar && <header className="document-json-toolbar">
        <span>{summary === null ? "JSON" : `${summary.count} ${summary.kindLabel}`}</span>
        <div aria-label="JSON 显示方式" role="group">
          <button aria-pressed={mode === "tree"} onClick={() => setMode("tree")} type="button">结构视图</button>
          <button aria-pressed={mode === "raw"} onClick={() => setMode("raw")} type="button">原始 JSON</button>
        </div>
      </header>}
      {mode === "tree"
        ? <div className="document-json"><JsonValueTree value={result.value} /></div>
        : <pre className="document-json-raw">{content}</pre>}
    </section>
  );
}

/** Safe, read-only renderer shared by project documents, reports and task artifacts. */
export function ContentRenderer({
  document,
  jsonMode,
  onJsonModeChange,
  showJsonToolbar = true,
}: {
  readonly document: RenderDocumentView;
  readonly jsonMode?: JsonDocumentMode;
  readonly onJsonModeChange?: (mode: JsonDocumentMode) => void;
  readonly showJsonToolbar?: boolean;
}) {
  if (document.format === "json") {
    return (
      <JsonDocument
        content={document.content}
        showToolbar={showJsonToolbar}
        {...(jsonMode === undefined ? {} : { mode: jsonMode })}
        {...(onJsonModeChange === undefined ? {} : { onModeChange: onJsonModeChange })}
      />
    );
  }
  if (document.format === "markdown") return <MarkdownDocument content={document.content} />;
  if (document.format === "binary") {
    return (
      <section className="document-binary-notice">
        <p>该产物是二进制文件（音频、压缩包、Office 文档等），暂不支持在线预览。</p>
        {document.content ? <pre className="document-plain">{document.content}</pre> : null}
      </section>
    );
  }
  // ``subtitle`` (.srt/.vtt) and unknown formats degrade to a plain preview.
  return <pre className="document-plain">{document.content}</pre>;
}
