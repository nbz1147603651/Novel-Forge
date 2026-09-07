import type { RenderDocumentView } from "@nimo/engine-contracts";

export interface ChapterContextDocumentInput {
  readonly primaryLabel: string;
  readonly preview: string;
  readonly secondary?: string;
  readonly secondaryLabel?: string;
  readonly title: string;
}

const serializedTextPattern = /(?:["']text["']|text)\s*:\s*(?:"((?:\\.|[^"\\])*)"|'((?:\\.|[^'\\])*)')/g;

function decodeSerializedText(value: string): string {
  return value
    .replaceAll("\\n", "\n")
    .replaceAll('\\"', '"')
    .replaceAll("\\'", "'")
    .trim();
}

/**
 * Some legacy context fields contain one or more serialized state envelopes.
 * Keep their narrative `text` values and hide transport metadata from writers.
 */
export function formatChapterContextValue(value: string): string {
  const matches = [...value.matchAll(serializedTextPattern)]
    .map((match) => decodeSerializedText(match[1] ?? match[2] ?? ""))
    .filter(Boolean);
  const normalized = matches.length > 0 ? matches.join("\n\n") : value.trim();
  return normalized.replace(/\n{3,}/g, "\n\n") || "暂无可用摘要。";
}

/** Compact, single-flow copy for fixed-height cards; the full content stays in the detail view. */
export function summarizeChapterContextValue(value: string): string {
  return formatChapterContextValue(value).replace(/\s+/g, " ").trim();
}

/**
 * Converts chapter context into the existing safe Markdown document contract.
 * Rendering remains owned by ContentRenderer, as with reports and artifacts.
 */
export function createChapterContextDocument(
  input: ChapterContextDocumentInput,
): RenderDocumentView {
  const sections = [
    `## ${input.primaryLabel}\n\n${formatChapterContextValue(input.preview)}`,
    input.secondary === undefined || input.secondaryLabel === undefined
      ? null
      : `## ${input.secondaryLabel}\n\n${formatChapterContextValue(input.secondary)}`,
  ].filter((section): section is string => section !== null);

  return {
    title: input.title,
    sourceLabel: "章台上下文",
    format: "markdown",
    content: sections.join("\n\n---\n\n"),
  };
}
