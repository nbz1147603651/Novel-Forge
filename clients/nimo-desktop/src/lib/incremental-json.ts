/**
 * Incremental JSON array object extraction from a growing stream.
 *
 * Mirrors PySide6 `novel_forge/desktop/pages/voice_studio/helpers.py`
 * `incremental_json_array_objects`: a state machine that scans for complete
 * top-level objects inside one JSON array while the model output is still
 * arriving. It handles string state, `\\` escapes, nested `{}` depth, and
 * rewinds to a partial object's start so the next call re-scans it.
 *
 * The scan position is cached between calls; only newly appended text is
 * processed. A full rescan happens when the text shrinks (stream reset).
 *
 * The regex-based approach this replaces (`/\{[^{}]*"text"\s*:\s*"([^"]*)"[^{}]*\}/g`)
 * silently truncated texts containing escaped quotes and dropped objects with
 * nested structures.
 */

export interface IncrementalJsonParser {
  /** Parse complete objects for `key` in the accumulated stream text. */
  parse(text: string, key: string): Record<string, unknown>[];
  /** Forget cached scan state (e.g. on stream reset). */
  reset(): void;
}

interface ParserCache {
  scanPos: number;
  arrayStart: number;
  segments: Record<string, unknown>[];
}

function findArrayStart(text: string, key: string): number {
  // Locate `"key": [`
  const needle = `"${key}"`;
  let index = 0;
  while (index < text.length) {
    const found = text.indexOf(needle, index);
    if (found === -1) return -1;
    let cursor = found + needle.length;
    const skipWhitespace = (position: number): number => {
      while (
        position < text.length
        && (text[position] === " " || text[position] === "\t" || text[position] === "\n" || text[position] === "\r")
      ) {
        position += 1;
      }
      return position;
    };
    cursor = skipWhitespace(cursor);
    if (text[cursor] !== ":") {
      index = found + 1;
      continue;
    }
    cursor = skipWhitespace(cursor + 1);
    if (text[cursor] === "[") {
      return cursor + 1;
    }
    index = found + 1;
  }
  return -1;
}

export function createIncrementalJsonParser(): IncrementalJsonParser {
  const cache: ParserCache = { scanPos: 0, arrayStart: -1, segments: [] };

  function parse(text: string, key: string): Record<string, unknown>[] {
    const raw = text;
    const length = raw.length;

    // Stream reset: text shrunk or never scanned → full rescan.
    if (cache.arrayStart < 0 || length < cache.scanPos || cache.scanPos === 0) {
      const start = findArrayStart(raw, key);
      if (start === -1) {
        cache.segments = [];
        cache.scanPos = 0;
        cache.arrayStart = -1;
        return [];
      }
      cache.arrayStart = start;
      cache.scanPos = start;
      cache.segments = [];
    }

    const objects = cache.segments;
    let objectStart: number | null = null;
    let depth = 0;
    let inString = false;
    let escaped = false;
    let pos = cache.scanPos;

    while (pos < length) {
      const char = raw[pos];
      if (inString) {
        if (escaped) {
          escaped = false;
        } else if (char === "\\") {
          escaped = true;
        } else if (char === '"') {
          inString = false;
        }
        pos += 1;
        continue;
      }
      if (char === '"') {
        inString = true;
        pos += 1;
        continue;
      }
      if (char === "{") {
        if (depth === 0) {
          objectStart = pos;
        }
        depth += 1;
      } else if (char === "}" && depth > 0) {
        depth -= 1;
        if (depth === 0 && objectStart !== null) {
          try {
            const value: unknown = JSON.parse(raw.slice(objectStart, pos + 1));
            if (typeof value === "object" && value !== null && !Array.isArray(value)) {
              objects.push(value as Record<string, unknown>);
            }
          } catch {
            // Incomplete / invalid object: skip; the parser never repairs.
          }
          objectStart = null;
        }
      } else if (char === "]" && depth === 0) {
        pos += 1;
        break;
      }
      pos += 1;
    }

    // Mid-object: rewind to the object start so the next call re-scans it.
    if (depth > 0 && objectStart !== null) {
      cache.scanPos = objectStart;
    } else {
      cache.scanPos = pos;
    }
    cache.segments = objects;
    return objects;
  }

  function reset(): void {
    cache.scanPos = 0;
    cache.arrayStart = -1;
    cache.segments = [];
  }

  return { parse, reset };
}
