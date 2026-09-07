import { useMemo, useState } from "react";

import type { ProjectReaderArtifactView, RenderDocumentView, StepArtifactFile } from "@nimo/engine-contracts";

import { ContentRenderer } from "./ContentRenderer";
import { ReaderArtifactDocument } from "./ReaderArtifactDocument";
import { LONG_READER_SECTIONS, readerSectionForTaskArtifact, type ReaderSectionDefinition } from "../lib/reader-sections";

interface ArtifactViewerProps {
  readonly artifacts: readonly StepArtifactFile[];
  readonly candidatePaths?: readonly { readonly label: string; readonly path: string }[];
  readonly emptyHint?: string;
  /** Current task step provides a semantic fallback for intermediate files. */
  readonly stageLabel?: string;
}

function toRenderDoc(artifact: StepArtifactFile): RenderDocumentView {
  return {
    title: artifact.label,
    sourceLabel: artifact.path,
    format: artifact.format,
    content: artifact.content,
  };
}

interface ArtifactGroup {
  readonly id: string;
  readonly label: string;
  readonly artifacts: readonly StepArtifactFile[];
  readonly presentation: "reader" | "raw";
}

function artifactGroups(artifacts: readonly StepArtifactFile[], stageLabel: string | undefined): readonly ArtifactGroup[] {
  const grouped = new Map<string, StepArtifactFile[]>();
  const uncategorized: StepArtifactFile[] = [];
  for (const artifact of artifacts) {
    const section = readerSectionForTaskArtifact({
      ...artifact,
      label: stageLabel === undefined ? artifact.label : `${artifact.label} ${stageLabel}`,
    });
    if (section === null) {
      uncategorized.push(artifact);
      continue;
    }
    const entries = grouped.get(section.id) ?? [];
    entries.push(artifact);
    grouped.set(section.id, entries);
  }
  const readerGroups = LONG_READER_SECTIONS.flatMap((section): readonly ArtifactGroup[] => {
    const entries = grouped.get(section.id);
    return entries === undefined
      ? []
      : [{ id: section.id, label: section.label, artifacts: entries, presentation: "reader" }];
  });
  return uncategorized.length === 0
    ? readerGroups
    : [...readerGroups, { id: "other", label: "其他产物", artifacts: uncategorized, presentation: "raw" }];
}

function toReaderArtifact(
  artifact: StepArtifactFile,
  section: ReaderSectionDefinition | undefined,
): ProjectReaderArtifactView {
  const wordCount = typeof artifact.wordCount === "number" && artifact.wordCount > 0
    ? artifact.wordCount
    : undefined;
  const categoryFact = section === undefined ? [] : [{ label: "卷帙分类", value: section.label }];
  return {
    id: artifact.path,
    label: artifact.label,
    caption: [
      section === undefined ? "任务步骤生成的产物" : `归入卷帙 · ${section.label}`,
      wordCount === undefined ? "" : `实际字数 ${wordCount.toLocaleString()} 字`,
    ].filter(Boolean).join(" · "),
    sourceLabel: artifact.path,
    paragraphs: ["任务步骤生成的只读产物。"],
    facts: [
      ...categoryFact,
      ...(wordCount === undefined ? [] : [{ label: "实际字数", value: `${wordCount.toLocaleString()} 字` }]),
    ],
    content: artifact.content,
    format: artifact.format,
  };
}

/**
 * Unified task artifact viewer. Recognized long-form outputs reuse the project
 * reader's PySide-parity document host and its volume shelves; only unknown
 * files retain the generic format-aware JSON/Markdown fallback.
 */
export function ArtifactViewer({ artifacts, candidatePaths, emptyHint, stageLabel }: ArtifactViewerProps) {
  const groups = useMemo(() => artifactGroups(artifacts, stageLabel), [artifacts, stageLabel]);
  const [selectedGroupId, setSelectedGroupId] = useState<string | null>(null);
  const [selectedArtifactPath, setSelectedArtifactPath] = useState<string | null>(null);

  if (artifacts.length === 0) {
    return (
      <div className="artifact-viewer-empty">
        <p>{emptyHint || "该步骤尚无可查看的产出文件。任务完成后此处将展示相关内容。"}</p>
        {candidatePaths && candidatePaths.length > 0 && (
          <ul className="artifact-candidate-list">
            {candidatePaths.map((cp) => (
              <li key={cp.path}><code>{cp.path}</code> <span>{cp.label}</span></li>
            ))}
          </ul>
        )}
      </div>
    );
  }

  const activeGroup = groups.find((group) => group.id === selectedGroupId) ?? groups[0]!;
  const activeArtifact = activeGroup.artifacts.find((artifact) => artifact.path === selectedArtifactPath)
    ?? activeGroup.artifacts[0]!;
  const readerSection = LONG_READER_SECTIONS.find((section) => section.id === activeGroup.id);

  return (
    <section aria-label="任务产物卷帙视图" className="artifact-reader reader-page">
      {groups.length > 1 && <div aria-label="卷帙标签" className="reader-tabs artifact-reader-tabs" role="tablist">
        {groups.map((group) => (
          <button
            aria-selected={group.id === activeGroup.id}
            className={group.id === activeGroup.id ? "is-active" : ""}
            key={group.id}
            onClick={() => {
              setSelectedGroupId(group.id);
              setSelectedArtifactPath(group.artifacts[0]?.path ?? null);
            }}
            role="tab"
            type="button"
          >{group.label}</button>
        ))}
      </div>}
      <div className="artifact-reader-content" role="tabpanel">
        {activeGroup.artifacts.length > 1 && (
          <div aria-label={`${activeGroup.label}产物`} className="reader-source-tabs" role="tablist">
            {activeGroup.artifacts.map((artifact) => (
              <button
                aria-selected={artifact.path === activeArtifact.path}
                className={artifact.path === activeArtifact.path ? "is-active" : ""}
                key={artifact.path}
                onClick={() => setSelectedArtifactPath(artifact.path)}
                role="tab"
                title={artifact.path}
                type="button"
              >{artifact.label}</button>
            ))}
          </div>
        )}
        <div className="artifact-reader-document">
          {activeGroup.presentation === "reader" && readerSection !== undefined
            ? <ReaderArtifactDocument key={activeArtifact.path} artifact={toReaderArtifact(activeArtifact, readerSection)} projectTitle={readerSection.label} />
            : <div className="artifact-reader-fallback"><ContentRenderer key={activeArtifact.path} document={toRenderDoc(activeArtifact)} showJsonToolbar /></div>}
        </div>
      </div>
    </section>
  );
}
