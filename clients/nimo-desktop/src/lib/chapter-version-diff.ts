import type { ProjectReaderArtifactView, ProjectReaderView } from "@nimo/engine-contracts";

export interface ChapterVersionView {
  readonly id: string;
  readonly version: number;
  readonly label: string;
  readonly wordCount: number;
  readonly text: string;
}

export type VersionDiffSegmentKind = "context" | "addition" | "deletion";

export interface VersionDiffSegment {
  readonly kind: VersionDiffSegmentKind;
  readonly text: string;
}

export interface ChapterVersionDiffHunk {
  readonly id: string;
  readonly segments: readonly VersionDiffSegment[];
}

export interface ChapterVersionComparison {
  readonly chapterNumber: number;
  readonly older: ChapterVersionView;
  readonly newer: ChapterVersionView;
  readonly additions: number;
  readonly deletions: number;
  readonly similarityRatio: number;
  readonly hunks: readonly ChapterVersionDiffHunk[];
}

interface DiffOperation {
  readonly kind: VersionDiffSegmentKind;
  readonly line: string;
}

const diffMatrixCellLimit = 1_000_000;
const hunkContextLineCount = 2;

function artifactChapterNumber(artifact: ProjectReaderArtifactView): number | null {
  const match = /第\s*(\d+)\s*章/.exec(artifact.label) ?? /chapter[-_](\d+)/i.exec(artifact.id);
  return match === null ? null : Number(match[1]);
}

function isReportArtifact(artifact: ProjectReaderArtifactView): boolean {
  return artifact.id.includes("report") || artifact.sourceLabel.startsWith("reports/");
}

function versionMetadata(artifact: ProjectReaderArtifactView): { readonly label: string; readonly version: number } {
  if (artifact.sourceLabel.startsWith("chapters/")) {
    return { label: "最终版本", version: 100 };
  }
  if (/\/v_final_review\.md$/u.test(artifact.sourceLabel)) {
    return { label: artifact.label, version: 99 };
  }
  const match = /\/v(\d+)(?:_[^/]+)?\.md$/u.exec(artifact.sourceLabel);
  if (match !== null) return { label: artifact.label, version: Number(match[1]) };
  return { label: artifact.label, version: 50 };
}

function textForArtifact(artifact: ProjectReaderArtifactView): string {
  return artifact.content ?? artifact.paragraphs.join("\n\n");
}

function countProseWords(text: string): number {
  const cjk = text.match(/[\u3400-\u9fff]/gu)?.length ?? 0;
  const nonCjk = text
    .replace(/[\u3400-\u9fff]/gu, " ")
    .trim()
    .split(/\s+/u)
    .filter(Boolean).length;
  return cjk + nonCjk;
}

function declaredWordCount(artifact: ProjectReaderArtifactView, text: string): number {
  const source = [artifact.caption, ...artifact.facts.map((fact) => `${fact.label} ${fact.value}`)]
    .find((value) => /(?:正文长度|字数|长度)/u.test(value));
  const matched = source === undefined ? null : /([\d,]+)\s*字/u.exec(source);
  if (matched !== null && matched?.[1] !== undefined) return Number(matched[1].replaceAll(",", ""));
  return countProseWords(text);
}

/**
 * Derive the selectable version chain from the Engine-owned project reader.
 * The API projects draft paths into the read model, so the client never
 * enumerates project files or manufactures chapter-specific version fixtures.
 */
export function chapterVersionsFromReader(reader: ProjectReaderView, chapterNumber: number): readonly ChapterVersionView[] {
  const chapterTab = reader.tabs.find((tab) => tab.id === "chapters");
  return (chapterTab?.artifacts ?? [])
    .filter((artifact) => artifactChapterNumber(artifact) === chapterNumber && !isReportArtifact(artifact))
    .map((artifact) => {
      const text = textForArtifact(artifact);
      const metadata = versionMetadata(artifact);
      return {
        id: artifact.id,
        label: metadata.label,
        text,
        version: metadata.version,
        wordCount: declaredWordCount(artifact, text),
      };
    })
    .sort((left, right) => left.version - right.version || left.id.localeCompare(right.id));
}

function splitLines(text: string): readonly string[] {
  if (text.length === 0) return [];
  return text.replaceAll("\r\n", "\n").split("\n");
}

function fallbackDiff(olderLines: readonly string[], newerLines: readonly string[]): readonly DiffOperation[] {
  return [
    ...olderLines.map((line) => ({ kind: "deletion" as const, line })),
    ...newerLines.map((line) => ({ kind: "addition" as const, line })),
  ];
}

/** A bounded line-level LCS diff keeps comparison responsive for large chapters. */
function lineDiff(olderText: string, newerText: string): readonly DiffOperation[] {
  const olderLines = splitLines(olderText);
  const newerLines = splitLines(newerText);
  if (olderLines.length * newerLines.length > diffMatrixCellLimit) {
    return fallbackDiff(olderLines, newerLines);
  }

  const matrix = Array.from(
    { length: olderLines.length + 1 },
    () => new Uint32Array(newerLines.length + 1),
  );
  for (let olderIndex = olderLines.length - 1; olderIndex >= 0; olderIndex -= 1) {
    for (let newerIndex = newerLines.length - 1; newerIndex >= 0; newerIndex -= 1) {
      matrix[olderIndex]![newerIndex] = olderLines[olderIndex] === newerLines[newerIndex]
        ? matrix[olderIndex + 1]![newerIndex + 1]! + 1
        : Math.max(matrix[olderIndex + 1]![newerIndex]!, matrix[olderIndex]![newerIndex + 1]!);
    }
  }

  const operations: DiffOperation[] = [];
  let olderIndex = 0;
  let newerIndex = 0;
  while (olderIndex < olderLines.length || newerIndex < newerLines.length) {
    if (olderIndex === olderLines.length) {
      operations.push({ kind: "addition", line: newerLines[newerIndex]! });
      newerIndex += 1;
    } else if (newerIndex === newerLines.length) {
      operations.push({ kind: "deletion", line: olderLines[olderIndex]! });
      olderIndex += 1;
    } else if (olderLines[olderIndex] === newerLines[newerIndex]) {
      operations.push({ kind: "context", line: olderLines[olderIndex]! });
      olderIndex += 1;
      newerIndex += 1;
    } else if (matrix[olderIndex + 1]![newerIndex]! >= matrix[olderIndex]![newerIndex + 1]!) {
      operations.push({ kind: "deletion", line: olderLines[olderIndex]! });
      olderIndex += 1;
    } else {
      operations.push({ kind: "addition", line: newerLines[newerIndex]! });
      newerIndex += 1;
    }
  }
  return operations;
}

function changeHunks(operations: readonly DiffOperation[]): readonly ChapterVersionDiffHunk[] {
  const changedIndexes = operations
    .map((operation, index) => (operation.kind === "context" ? null : index))
    .filter((index): index is number => index !== null);
  if (changedIndexes.length === 0) return [];

  const ranges: Array<{ start: number; end: number }> = [];
  for (const index of changedIndexes) {
    const start = Math.max(0, index - hunkContextLineCount);
    const end = Math.min(operations.length, index + hunkContextLineCount + 1);
    const previous = ranges.at(-1);
    if (previous !== undefined && start <= previous.end) {
      previous.end = Math.max(previous.end, end);
    } else {
      ranges.push({ start, end });
    }
  }
  return ranges.map((range, index) => ({
    id: `hunk-${index + 1}`,
    segments: operations.slice(range.start, range.end).map((operation) => ({
      kind: operation.kind,
      text: `${operation.line}\n`,
    })),
  }));
}

/** Returns no comparison for a same-version choice, matching the PySide6 dialog. */
export function createChapterVersionComparison(
  chapterNumber: number,
  older: ChapterVersionView,
  newer: ChapterVersionView,
): ChapterVersionComparison | null {
  if (older.id === newer.id) return null;
  const operations = lineDiff(older.text, newer.text);
  const additions = operations.filter((operation) => operation.kind === "addition").length;
  const deletions = operations.filter((operation) => operation.kind === "deletion").length;
  const commonLines = operations.filter((operation) => operation.kind === "context").length;
  const totalLines = splitLines(older.text).length + splitLines(newer.text).length;
  return {
    additions,
    chapterNumber,
    deletions,
    hunks: changeHunks(operations),
    newer,
    older,
    similarityRatio: totalLines === 0 ? 1 : (2 * commonLines) / totalLines,
  };
}
