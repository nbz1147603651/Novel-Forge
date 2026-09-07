import { useMemo, useState, type ReactNode } from "react";

import type { ProjectReaderArtifactView, RenderDocumentView } from "@nimo/engine-contracts";

import { AppDialog } from "./AppDialog";
import { ContentRenderer } from "./ContentRenderer";
import { formatDocumentSize, RichDocumentViewer } from "./RichDocumentViewer";
import { parseJsonContent } from "./document/document-parse";
import { ChapterDesignMatrixView } from "./document/ChapterDesignMatrixView";
import { CharacterBibleDocumentView } from "./document/CharacterBibleDocumentView";
import { ElementSelectionDocumentView } from "./document/ElementSelectionDocumentView";
import { GenericReportView } from "./document/GenericReportView";
import { HumanizeReportView, parseHumanizeReport } from "./document/HumanizeReportView";
import { SpecDocumentView } from "./document/SpecDocumentView";
import { StoryBibleDocumentView } from "./document/StoryBibleDocumentView";
import { TextRevisionDiffView, parseTextRevisionDiff } from "./document/TextRevisionDiffView";
import { revealFileInFolder } from "../lib/native-bridge";

export interface ReaderArtifactDocumentProps {
  readonly artifact: ProjectReaderArtifactView;
  readonly projectTitle: string;
}

/**
 * Shared read-only source-document host used by the project reader and task
 * flow. Its dispatch mirrors PySide6 smart document rendering: source-specific
 * documents use their rich renderer, reports use structured cards, and raw
 * JSON remains an explicit fallback instead of the default experience.
 */
export function ReaderArtifactDocument({ artifact, projectTitle }: ReaderArtifactDocumentProps) {
  const [rawDataOpen, setRawDataOpen] = useState(false);
  const [sourceMode, setSourceMode] = useState<"rich" | "raw">("rich");
  const [sourceFeedback, setSourceFeedback] = useState("");
  const hasFullContent = artifact.content !== undefined && artifact.content.length > 0;
  const documentView: RenderDocumentView | null = hasFullContent
    ? {
        title: artifact.label,
        sourceLabel: artifact.sourceLabel,
        format: artifact.format ?? "plain_text",
        content: artifact.content!,
        ...(artifact.sourceLocalPath === undefined ? {} : { localPath: artifact.sourceLocalPath }),
        ...(artifact.sourceByteSize === undefined ? {} : { byteSize: artifact.sourceByteSize }),
        ...(artifact.sourceModifiedAtLabel === undefined ? {} : { modifiedAtLabel: artifact.sourceModifiedAtLabel }),
      }
    : null;
  const fallbackDocument: RenderDocumentView = {
    title: artifact.label,
    sourceLabel: artifact.sourceLabel,
    format: "json",
    content: JSON.stringify({ source: artifact.sourceLabel, facts: artifact.facts, paragraphs: artifact.paragraphs }),
    ...(artifact.sourceLocalPath === undefined ? {} : { localPath: artifact.sourceLocalPath }),
    ...(artifact.sourceByteSize === undefined ? {} : { byteSize: artifact.sourceByteSize }),
    ...(artifact.sourceModifiedAtLabel === undefined ? {} : { modifiedAtLabel: artifact.sourceModifiedAtLabel }),
  };
  const sourceBasename = artifact.sourceLabel.split("/").pop() ?? "";
  const parsedJson = useMemo(() => artifact.format === "json" ? parseJsonContent(artifact.content) : null, [artifact.content, artifact.format]);
  const textRevisionDiff = useMemo(() => hasFullContent && artifact.format === "json"
    ? parseTextRevisionDiff(artifact.content) : null, [artifact.content, artifact.format, hasFullContent]);
  const humanizeReport = useMemo(() => hasFullContent && artifact.format === "json"
    ? parseHumanizeReport(artifact.content) : null, [artifact.content, artifact.format, hasFullContent]);
  const isCharacterDocument = sourceBasename === "character_bible.json";
  let richBody: ReactNode = null;
  if (parsedJson !== null) {
    if (sourceBasename === "spec.json") richBody = <SpecDocumentView content={artifact.content} />;
    else if (sourceBasename === "story_bible.json") richBody = <StoryBibleDocumentView content={artifact.content} />;
    else if (sourceBasename === "blueprint_elements_selection.json") richBody = <ElementSelectionDocumentView content={artifact.content} />;
    else if (sourceBasename === "chapter_design_matrix.json") richBody = <ChapterDesignMatrixView content={artifact.content} />;
    else if (isCharacterDocument) richBody = <CharacterBibleDocumentView content={artifact.content} />;
    else if (humanizeReport !== null) richBody = <HumanizeReportView content={artifact.content} />;
    else richBody = <GenericReportView content={artifact.content} />;
  }
  const sourceTitle = parsedJson === null || typeof parsedJson.title !== "string"
    ? artifact.label
    : parsedJson.title;
  const sourceByteSize = artifact.sourceByteSize
    ?? new TextEncoder().encode(artifact.content ?? "").byteLength;
  const sourceFieldCount = parsedJson === null ? 0 : Object.keys(parsedJson).length;
  const copySource = async () => {
    // Copy the complete file, including collapsed collections and other characters.
    const text = artifact.content ?? "";
    try {
      if (navigator.clipboard === undefined) throw new Error("clipboard unavailable");
      await navigator.clipboard.writeText(text);
      setSourceFeedback("已复制全文");
    } catch {
      setSourceFeedback("复制失败");
    }
  };
  const revealSource = async () => {
    if (artifact.sourceLocalPath === undefined) return;
    setSourceFeedback(await revealFileInFolder(artifact.sourceLocalPath) ? "" : "无法在文件夹中显示");
  };
  if (textRevisionDiff !== null && artifact.content !== undefined) {
    return <>
      {artifact.freshnessMessage && <p role="status" className="reader-version-notice">{artifact.freshnessMessage}</p>}
      <TextRevisionDiffView content={artifact.content} onViewRaw={() => setRawDataOpen(true)} />
      {rawDataOpen && <AppDialog description={`${artifact.sourceLabel} 的只读预览；当前会话不会写回项目文件。`} onClose={() => setRawDataOpen(false)} title={`${artifact.label} · 原始数据`}><RichDocumentViewer document={documentView ?? fallbackDocument} /></AppDialog>}
    </>;
  }
  if (richBody !== null) {
    return <article className="reader-document is-source-parity">
      {artifact.freshnessMessage && <p role="status" className="reader-version-notice">{artifact.freshnessMessage}</p>}
      <header className="reader-source-toolbar">
        <div className="reader-source-heading">
          <strong>{sourceBasename || artifact.label}</strong>
          {sourceBasename !== artifact.sourceLabel && <span title={artifact.sourceLabel}>{artifact.sourceLabel}</span>}
        </div>
        <div className="reader-source-actions">
          <button onClick={copySource} type="button">复制全文</button>
          <button
            disabled={artifact.sourceLocalPath === undefined}
            onClick={revealSource}
            title={artifact.sourceLocalPath === undefined ? "Engine 未提供可访问的本地路径" : "在文件夹中显示此文件"}
            type="button"
          >
            在文件夹中显示
          </button>
          <button
            aria-pressed={sourceMode === "raw"}
            onClick={() => setSourceMode((current) => current === "raw" ? "rich" : "raw")}
            type="button"
          >
            {sourceMode === "raw" ? "返回富视图" : "切换原始 JSON"}
          </button>
        </div>
      </header>
      <div className={`reader-source-body${isCharacterDocument && sourceMode === "rich" ? " is-character-document" : ""}`}>
        {sourceMode === "raw"
          ? <pre className="reader-source-json">{artifact.content}</pre>
          : <>{!isCharacterDocument && humanizeReport === null && <h1>{sourceTitle}</h1>}{richBody}</>}
      </div>
      <footer className="reader-source-status">
        <span>文件大小 {formatDocumentSize(sourceByteSize)}</span>
        <span>修改时间 {artifact.sourceModifiedAtLabel ?? "—"}</span>
        <span>{sourceFieldCount} 个顶层字段</span>
        <span aria-live="polite" role="status">{sourceFeedback}</span>
      </footer>
    </article>;
  }
  return <article className="reader-document">
    {artifact.freshnessMessage && <p role="status" className="reader-version-notice">{artifact.freshnessMessage}</p>}
    <header className="reader-document-heading"><div><span className="section-kicker">{projectTitle} · 只读视图</span><h2>{artifact.label}</h2><p>{artifact.caption}</p></div><span>{artifact.sourceLabel}</span></header>
    {artifact.facts.length > 0 && <dl className="reader-facts">{artifact.facts.map((fact, index) => <div key={`${fact.label}-${index}`}><dt>{fact.label}</dt><dd>{fact.value}</dd></div>)}</dl>}
    {documentView !== null
      ? <div className="reader-document-body is-rich"><ContentRenderer document={documentView} showJsonToolbar /></div>
      : <div className="reader-document-body">{artifact.paragraphs.map((paragraph, index) => <p key={`${artifact.id}-${index}`}>{paragraph}</p>)}</div>}
    <footer className="reader-document-footer"><span>内容按引擎只读视图呈现</span><button className="button button-secondary" onClick={() => setRawDataOpen(true)} type="button">查看原始数据</button></footer>
    {rawDataOpen && <AppDialog description={`${artifact.sourceLabel} 的只读预览；当前会话不会写回项目文件。`} onClose={() => setRawDataOpen(false)} title={`${artifact.label} · 原始数据`}><RichDocumentViewer document={documentView ?? fallbackDocument} /></AppDialog>}
  </article>;
}
