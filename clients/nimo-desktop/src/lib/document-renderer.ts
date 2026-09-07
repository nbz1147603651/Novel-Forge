export type InlineToken =
  | { readonly kind: "text" | "code" | "strong" | "emphasis" | "delete"; readonly value: string }
  | { readonly kind: "link"; readonly label: string; readonly href: string | null };

export type MarkdownBlock =
  | { readonly kind: "heading"; readonly level: 1 | 2 | 3; readonly text: string }
  | { readonly kind: "paragraph" | "quote"; readonly text: string }
  | { readonly kind: "unordered-list"; readonly items: readonly string[] }
  | { readonly kind: "ordered-list"; readonly start: number; readonly items: readonly string[] }
  | { readonly kind: "code"; readonly language: string; readonly text: string }
  | { readonly kind: "table"; readonly headers: readonly string[]; readonly rows: readonly (readonly string[])[] }
  | { readonly kind: "rule" };

export type JsonValue = null | boolean | number | string | readonly JsonValue[] | { readonly [key: string]: JsonValue };

export interface JsonDocumentSummary {
  readonly count: number;
  readonly kindLabel: "个数组元素" | "个顶层字段" | "个根值";
}

function splitTableCells(line: string): readonly string[] {
  return line.trim().replace(/^\||\|$/g, "").split("|").map((cell) => cell.trim());
}

function isTableDivider(line: string): boolean {
  const cells = splitTableCells(line);
  return cells.length > 0 && cells.every((cell) => /^:?-{3,}:?$/.test(cell));
}

function isBlockStart(lines: readonly string[], index: number): boolean {
  const line = lines[index] ?? "";
  return /^ {0,3}(#{1,3})\s+/.test(line)
    || /^```/.test(line)
    || /^\s*([-*_])(?:\s*\1){2,}\s*$/.test(line)
    || /^>\s?/.test(line)
    || /^[-*+]\s+/.test(line)
    || /^\d+\.\s+/.test(line)
    || (line.includes("|") && isTableDivider(lines[index + 1] ?? ""));
}

/** Parse the subset needed by existing source documents without rendering HTML. */
export function parseMarkdownBlocks(content: string): readonly MarkdownBlock[] {
  const lines = content.replaceAll("\r\n", "\n").replaceAll("\r", "\n").split("\n");
  const blocks: MarkdownBlock[] = [];
  let index = 0;

  while (index < lines.length) {
    const line = lines[index] ?? "";
    if (line.trim() === "") {
      index += 1;
      continue;
    }
    const fence = line.match(/^```\s*([\w-]*)\s*$/);
    if (fence !== null) {
      const language = fence[1] ?? "";
      const code: string[] = [];
      index += 1;
      while (index < lines.length && !/^```\s*$/.test(lines[index] ?? "")) {
        code.push(lines[index] ?? "");
        index += 1;
      }
      if (index < lines.length) index += 1;
      blocks.push({ kind: "code", language, text: code.join("\n") });
      continue;
    }
    const heading = line.match(/^ {0,3}(#{1,3})\s+(.*?)\s*#*\s*$/);
    if (heading !== null) {
      blocks.push({ kind: "heading", level: heading[1]!.length as 1 | 2 | 3, text: heading[2] ?? "" });
      index += 1;
      continue;
    }
    if (/^\s*([-*_])(?:\s*\1){2,}\s*$/.test(line)) {
      blocks.push({ kind: "rule" });
      index += 1;
      continue;
    }
    if (/^>\s?/.test(line)) {
      const quote: string[] = [];
      while (index < lines.length && /^>\s?/.test(lines[index] ?? "")) {
        quote.push((lines[index] ?? "").replace(/^>\s?/, ""));
        index += 1;
      }
      blocks.push({ kind: "quote", text: quote.join("\n") });
      continue;
    }
    if (/^[-*+]\s+/.test(line)) {
      const items: string[] = [];
      while (index < lines.length && /^[-*+]\s+/.test(lines[index] ?? "")) {
        items.push((lines[index] ?? "").replace(/^[-*+]\s+/, ""));
        index += 1;
      }
      blocks.push({ kind: "unordered-list", items });
      continue;
    }
    const ordered = line.match(/^(\d+)\.\s+(.*)$/);
    if (ordered !== null) {
      const items: string[] = [];
      const start = Number(ordered[1]);
      while (index < lines.length && /^\d+\.\s+/.test(lines[index] ?? "")) {
        items.push((lines[index] ?? "").replace(/^\d+\.\s+/, ""));
        index += 1;
      }
      blocks.push({ kind: "ordered-list", start, items });
      continue;
    }
    if (line.includes("|") && isTableDivider(lines[index + 1] ?? "")) {
      const headers = splitTableCells(line);
      const rows: string[][] = [];
      index += 2;
      while (index < lines.length && (lines[index] ?? "").includes("|")) {
        rows.push([...splitTableCells(lines[index] ?? "")]);
        index += 1;
      }
      blocks.push({ kind: "table", headers, rows });
      continue;
    }
    const paragraph: string[] = [line];
    index += 1;
    while (index < lines.length && (lines[index] ?? "").trim() !== "" && !isBlockStart(lines, index)) {
      paragraph.push(lines[index] ?? "");
      index += 1;
    }
    blocks.push({ kind: "paragraph", text: paragraph.join("\n") });
  }
  return blocks;
}

export function safeDocumentHref(rawHref: string): string | null {
  try {
    const parsed = new URL(rawHref, "https://nimo.local");
    return ["https:", "http:", "mailto:"].includes(parsed.protocol) ? rawHref : null;
  } catch {
    return null;
  }
}

/** Inline tokens are data, never source HTML, so untrusted documents stay inert. */
export function tokenizeInline(text: string): readonly InlineToken[] {
  const tokens: InlineToken[] = [];
  const pattern = /(`[^`\n]+`|\*\*[^*\n]+\*\*|~~[^~\n]+~~|\*[^*\n]+\*|\[[^\]]+\]\((?:[^()\s]|\([^()\s]*\))+\))/g;
  let position = 0;
  for (const match of text.matchAll(pattern)) {
    const start = match.index ?? 0;
    if (start > position) tokens.push({ kind: "text", value: text.slice(position, start) });
    const value = match[0];
    if (value.startsWith("`")) {
      tokens.push({ kind: "code", value: value.slice(1, -1) });
    } else if (value.startsWith("**")) {
      tokens.push({ kind: "strong", value: value.slice(2, -2) });
    } else if (value.startsWith("~~")) {
      tokens.push({ kind: "delete", value: value.slice(2, -2) });
    } else if (value.startsWith("*")) {
      tokens.push({ kind: "emphasis", value: value.slice(1, -1) });
    } else {
      const link = value.match(/^\[([^\]]+)\]\((.+)\)$/);
      tokens.push({ kind: "link", label: link?.[1] ?? value, href: safeDocumentHref(link?.[2] ?? "") });
    }
    position = start + value.length;
  }
  if (position < text.length) tokens.push({ kind: "text", value: text.slice(position) });
  return tokens;
}

export function parseJsonDocument(content: string): { readonly value: JsonValue } | { readonly error: string } {
  try {
    return { value: JSON.parse(content) as JsonValue };
  } catch (error) {
    return { error: error instanceof Error ? error.message : "JSON 解析失败" };
  }
}

export function summarizeJsonDocument(content: string): JsonDocumentSummary | null {
  const result = parseJsonDocument(content);
  if ("error" in result) return null;
  return summarizeJsonValue(result.value);
}

export function summarizeJsonValue(value: JsonValue): JsonDocumentSummary {
  if (Array.isArray(value)) {
    return { count: value.length, kindLabel: "个数组元素" };
  }
  if (value !== null && typeof value === "object") {
    return { count: Object.keys(value).length, kindLabel: "个顶层字段" };
  }
  return { count: 1, kindLabel: "个根值" };
}
