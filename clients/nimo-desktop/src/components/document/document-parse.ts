/**
 * Shared JSON parsing helper for rich document views.
 *
 * Each document view receives the artifact's full raw `content` string and
 * parses it into a typed record. Returns null when the content is missing or
 * not a JSON object, so the caller can fall back to the generic renderer.
 */
export type JsonDict = Record<string, unknown>;

export function parseJsonContent(content: string | undefined): JsonDict | null {
  if (content === undefined || content.length === 0) return null;
  try {
    const parsed: unknown = JSON.parse(content);
    return parsed !== null && typeof parsed === "object" && !Array.isArray(parsed)
      ? (parsed as JsonDict)
      : null;
  } catch {
    return null;
  }
}

/** Read a string field, coercing non-strings and defaulting to "". */
export function str(data: JsonDict, key: string): string {
  const value = data[key];
  if (typeof value === "string") return value;
  if (value === null || value === undefined) return "";
  return String(value);
}

/** Read a numeric field, defaulting to 0. */
export function num(data: JsonDict, key: string): number {
  const value = data[key];
  if (typeof value === "number") return value;
  if (typeof value === "string" && value.trim().length > 0) {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : 0;
  }
  return 0;
}

/** Read a string-list field, dropping empty entries. */
export function strList(data: JsonDict, key: string): string[] {
  const value = data[key];
  if (!Array.isArray(value)) return [];
  return value
    .map((item) => (typeof item === "string" ? item : String(item)))
    .filter((item) => item.trim().length > 0);
}
