/**
 * Stream content type detection and rendering helpers.
 *
 * Mirrors PySide6 `stream_rendering.py::StreamRenderKind` — detects whether
 * a stream text payload is plain prose, JSON, partial JSON, or a structured
 * evaluation report so the renderer can choose the appropriate visual treatment.
 */

export type StreamRenderKind = "text" | "json" | "json_partial" | "report";

export const STREAM_RENDER_KIND_LABEL: Record<StreamRenderKind, string> = {
  text: "文本",
  json: "JSON",
  json_partial: "JSON 片段",
  report: "结构化报告",
};

const REPORT_SCORE_KEYS = [
  "overall_score",
  "alignment_score",
  "continuity_score",
  "causal_score",
  "humanize_score",
] as const;

const JSON_INLINE_STRING_LIMIT = 180;
const JSON_LONG_STRING_LIMIT = 1800;

/**
 * Split a text blob into a prose prefix and a JSON candidate suffix.
 * Models sometimes emit a short explanation before the JSON body.
 */
function splitJsonCandidate(text: string): [string, string] {
  const firstBrace = text.indexOf("{");
  const firstBracket = text.indexOf("[");
  let start = -1;
  if (firstBrace >= 0 && firstBracket >= 0) {
    start = Math.min(firstBrace, firstBracket);
  } else if (firstBrace >= 0) {
    start = firstBrace;
  } else if (firstBracket >= 0) {
    start = firstBracket;
  }
  if (start < 0) return ["", ""];
  return [text.slice(0, start), text.slice(start)];
}

function looksLikeGuardReport(parsed: Record<string, unknown>): boolean {
  return (
    Array.isArray(parsed.issues) ||
    Array.isArray(parsed.violations) ||
    typeof parsed.compliant === "boolean"
  );
}

/**
 * Detect the render kind for a stream text payload.
 * Pure function — safe to call on every render without memoization concerns.
 */
export function detectStreamRenderKind(text: string): StreamRenderKind {
  const [, candidate] = splitJsonCandidate(text);
  if (candidate.length === 0) return "text";
  try {
    const parsed: unknown = JSON.parse(candidate);
    if (
      parsed !== null &&
      typeof parsed === "object" &&
      !Array.isArray(parsed)
    ) {
      const record = parsed as Record<string, unknown>;
      if (
        REPORT_SCORE_KEYS.some((key) => key in record) ||
        looksLikeGuardReport(record) ||
        typeof (record.report_type as string | undefined) === "string"
      ) {
        return "report";
      }
    }
    return "json";
  } catch {
    return "json_partial";
  }
}

/**
 * Extract the JSON portion from a stream text for syntax-highlighted rendering.
 */
export function extractJsonBody(text: string): string {
  const [, candidate] = splitJsonCandidate(text);
  return candidate || text;
}

/**
 * Lightweight JSON syntax highlighter using regex tokenization.
 * Returns an array of {type, value} tokens for React rendering.
 * Avoids heavy dependencies like prism or highlight.js.
 */
export interface JsonToken {
  readonly type: "key" | "string" | "number" | "boolean" | "null" | "punctuation" | "whitespace";
  readonly value: string;
}

const JSON_TOKEN_REGEX =
  /("(?:[^"\\]|\\.)*")\s*:|("(?:[^"\\]|\\.)*")|(\b\d+(?:\.\d+)?(?:[eE][+-]?\d+)?\b)|(\btrue\b|\bfalse\b)|(\bnull\b)|([{}[\],:])|(\s+)/g;

export function tokenizeJson(json: string): JsonToken[] {
  const tokens: JsonToken[] = [];
  let match: RegExpExecArray | null;
  const regex = new RegExp(JSON_TOKEN_REGEX.source, "g");
  while ((match = regex.exec(json)) !== null) {
    if (match[1] !== undefined) {
      tokens.push({ type: "key", value: match[1] });
      tokens.push({ type: "punctuation", value: ":" });
    } else if (match[2] !== undefined) {
      tokens.push({ type: "string", value: match[2] });
    } else if (match[3] !== undefined) {
      tokens.push({ type: "number", value: match[3] });
    } else if (match[4] !== undefined) {
      tokens.push({ type: "boolean", value: match[4] });
    } else if (match[5] !== undefined) {
      tokens.push({ type: "null", value: match[5] });
    } else if (match[6] !== undefined) {
      tokens.push({ type: "punctuation", value: match[6] });
    } else if (match[7] !== undefined) {
      tokens.push({ type: "whitespace", value: match[7] });
    }
  }
  return tokens;
}

/**
 * Truncate long string values in JSON for compact inline display.
 */
export function truncateJsonStrings(json: string, limit: number = JSON_LONG_STRING_LIMIT): string {
  return json.replace(
    /"(?:[^"\\]|\\.)*"/g,
    (match) => {
      if (match.length <= limit + 2) return match;
      return `${match.slice(0, limit)}…"`;
    },
  );
}

/**
 * Split prose text into paragraphs for rich typographic rendering.
 * Collapses consecutive blank lines into a single separator.
 */
export function textToParagraphs(text: string): (string | null)[] {
  const rows: (string | null)[] = [];
  let blankPending = false;
  for (const raw of text.split("\n")) {
    const line = raw.trimEnd();
    if (line.length > 0) {
      rows.push(line);
      blankPending = false;
    } else if (rows.length > 0 && !blankPending) {
      rows.push(null);
      blankPending = true;
    }
  }
  return rows;
}

export { JSON_INLINE_STRING_LIMIT, JSON_LONG_STRING_LIMIT };
