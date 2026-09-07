/**
 * Stream anomaly detection for the voice-script streaming preview.
 *
 * Mirrors PySide6 `novel_forge/desktop/pages/voice_studio/helpers.py`
 * `detect_stream_anomalies`: duplicated segment texts (adjacent or within a
 * 3-segment sliding window), JSON structural echoes leaked into prose, and
 * character inflation (received chars >> source chapter length) flag a
 * preview that the backend's validated output will replace.
 *
 * `sourceChars` is optional: when unknown (0), the inflation check is
 * skipped but duplicate/echo detection still runs.
 */

export interface StreamAnomalyReport {
  readonly duplicateIndices: readonly number[];
  readonly jsonEchoIndices: readonly number[];
  readonly charInflation: boolean;
  readonly hasAnomalies: boolean;
}

/** JSON structural key pattern that should never appear inside segment prose. */
const JSON_ECHO_PATTERN = /"segment_(?:index|type)"\s*:/;

/** Received chars exceeding this multiple of the source chapter length. */
const CHAR_INFLATION_FACTOR = 3.0;

export function detectStreamAnomalies(
  segments: readonly { readonly text: string }[],
  receivedChars: number,
  sourceChars: number,
): StreamAnomalyReport {
  const duplicateIndices: number[] = [];
  const jsonEchoIndices: number[] = [];
  const texts = segments.map((item) => String(item.text ?? "").trim());

  for (let index = 0; index < texts.length; index += 1) {
    const text = texts[index];
    if (text === undefined || text.length === 0) continue;
    // Adjacent duplicate.
    if (index > 0 && text === texts[index - 1]) {
      duplicateIndices.push(index);
      continue;
    }
    // Sliding 3-segment window duplicate (covers A-B-A patterns).
    const windowStart = Math.max(0, index - 2);
    if (texts.slice(windowStart, index).includes(text)) {
      duplicateIndices.push(index);
    }
    if (JSON_ECHO_PATTERN.test(text)) {
      jsonEchoIndices.push(index);
    }
  }

  const charInflation = sourceChars > 0 && receivedChars > sourceChars * CHAR_INFLATION_FACTOR;

  return {
    duplicateIndices,
    jsonEchoIndices,
    charInflation,
    hasAnomalies: duplicateIndices.length > 0 || jsonEchoIndices.length > 0 || charInflation,
  };
}
