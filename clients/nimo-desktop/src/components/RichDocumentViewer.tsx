import { useMemo, useRef, useState } from "react";

import type { RenderDocumentView } from "@nimo/engine-contracts";

import { summarizeJsonDocument } from "../lib/document-renderer";
import { revealFileInFolder } from "../lib/native-bridge";
import { ContentRenderer, type JsonDocumentMode } from "./ContentRenderer";

export function formatDocumentSize(size: number): string {
  const normalized = Math.max(0, Math.floor(size));
  if (normalized < 1_024) return `${normalized} B`;
  if (normalized < 1_024 * 1_024) return `${(normalized / 1_024).toFixed(1)} KB`;
  return `${(normalized / (1_024 * 1_024)).toFixed(1)} MB`;
}

function contentByteSize(content: string): number {
  return new TextEncoder().encode(content).byteLength;
}

async function writeClipboard(text: string): Promise<boolean> {
  try {
    if (navigator.clipboard === undefined) return false;
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    return false;
  }
}

export interface RichDocumentViewerProps {
  readonly document: RenderDocumentView;
  readonly onRevealFile?: (path: string) => Promise<boolean>;
  readonly onWriteClipboard?: (text: string) => Promise<boolean>;
}

/** React/Tauri counterpart to PySide6 `RichDocumentViewer`. */
export function RichDocumentViewer({
  document,
  onRevealFile = revealFileInFolder,
  onWriteClipboard = writeClipboard,
}: RichDocumentViewerProps) {
  const bodyRef = useRef<HTMLDivElement>(null);
  const [jsonMode, setJsonMode] = useState<JsonDocumentMode>("tree");
  const [copyStatus, setCopyStatus] = useState<"idle" | "copied" | "failed">("idle");
  const [revealStatus, setRevealStatus] = useState<"idle" | "failed">("idle");
  const isJson = document.format === "json";
  const summary = useMemo(
    () => isJson ? summarizeJsonDocument(document.content) : null,
    [document.content, isJson],
  );
  const byteSize = useMemo(
    () => document.byteSize ?? contentByteSize(document.content),
    [document.byteSize, document.content],
  );

  const handleCopy = async () => {
    const renderedText = bodyRef.current?.innerText.trim();
    const text = isJson && jsonMode === "raw"
      ? document.content
      : renderedText || document.content;
    setCopyStatus(await onWriteClipboard(text) ? "copied" : "failed");
  };

  const handleReveal = async () => {
    if (document.localPath === undefined) return;
    setRevealStatus(await onRevealFile(document.localPath) ? "idle" : "failed");
  };

  return (
    <section aria-label={`${document.title} · 文档查看器`} className="rich-document-viewer">
      <header className="rich-document-toolbar">
        <div className="rich-document-heading">
          <strong>{document.title}</strong>
          <span title={document.sourceLabel}>{document.sourceLabel}</span>
        </div>
        <div className="rich-document-actions">
          <button onClick={handleCopy} type="button">复制全文</button>
          <button
            disabled={document.localPath === undefined}
            onClick={handleReveal}
            title={document.localPath === undefined ? "虚拟或云端文档尚无可显示的本地文件" : "在文件夹中显示此文件"}
            type="button"
          >
            在文件夹中显示
          </button>
          {isJson && (
            <button
              aria-pressed={jsonMode === "raw"}
              onClick={() => setJsonMode((current) => current === "tree" ? "raw" : "tree")}
              type="button"
            >
              {jsonMode === "raw" ? "返回富视图" : "切换原始 JSON"}
            </button>
          )}
        </div>
      </header>

      <div className="rich-document-body" ref={bodyRef}>
        <ContentRenderer
          document={document}
          jsonMode={jsonMode}
          onJsonModeChange={setJsonMode}
          showJsonToolbar={false}
        />
      </div>

      <footer className="rich-document-status">
        <span>文件大小 {formatDocumentSize(byteSize)}</span>
        <span>修改时间 {document.modifiedAtLabel ?? "—"}</span>
        {summary !== null && <span>{summary.count} {summary.kindLabel}</span>}
        <span aria-live="polite" className="rich-document-feedback" role="status">
          {copyStatus === "copied" ? "已复制全文" : copyStatus === "failed" ? "复制失败" : revealStatus === "failed" ? "无法在文件夹中显示" : ""}
        </span>
      </footer>
    </section>
  );
}
