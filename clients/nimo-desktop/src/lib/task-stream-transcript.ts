import type { TaskStreamEvent } from "@nimo/engine-contracts";

import type { TaskStreamState } from "./task-stream";

export type TaskStreamTranscriptItemKind = "content" | "reasoning" | "error";

/**
 * A reader-oriented projection of the append-only Engine event log.
 *
 * The Engine boundary intentionally preserves each delta for replay and
 * recovery. Readers need a different shape: a small number of coherent
 * output blocks that do not repeat a label or card for every token chunk.
 */
export interface TaskStreamTranscriptItem {
  readonly key: string;
  readonly kind: TaskStreamTranscriptItemKind;
  readonly text: string;
  readonly characterCount: number;
  readonly eventCount: number;
  readonly streamId: string;
  readonly attempt?: number;
  /** Engine-declared output contract carried from the source event. */
  readonly outputKind?: string;
}

export interface TaskStreamTranscript {
  readonly items: readonly TaskStreamTranscriptItem[];
  readonly contentCharacterCount: number;
  readonly reasoningBlockCount: number;
}

function eventIdentity(event: TaskStreamEvent): string {
  return event.cursor
    ?? `${event.streamId}:${event.sequence}:${event.kind}:${event.segment}:${event.at ?? ""}`;
}

function eventText(event: TaskStreamEvent): string {
  return event.text ?? event.message ?? "";
}

function characterCount(text: string): number {
  return [...text].length;
}

function canMerge(
  previous: TaskStreamTranscriptItem | undefined,
  event: TaskStreamEvent,
): previous is TaskStreamTranscriptItem {
  return previous !== undefined
    && (previous.kind === "content" || previous.kind === "reasoning")
    && previous.kind === event.segment
    && previous.streamId === event.streamId
    && previous.attempt === event.attempt;
}

/** Build the stable reader projection without changing event replay semantics. */
export function buildTaskStreamTranscript(
  events: readonly TaskStreamEvent[],
): TaskStreamTranscript {
  const items: TaskStreamTranscriptItem[] = [];
  const seen = new Set<string>();

  for (const event of events) {
    const identity = eventIdentity(event);
    if (seen.has(identity)) continue;
    seen.add(identity);
    if (event.kind === "restart") {
      for (let index = items.length - 1; index >= 0; index -= 1) {
        if (items[index]?.streamId === event.streamId) items.splice(index, 1);
      }
    }
    const text = eventText(event);
    if (event.segment === "system") {
      if (event.kind === "stream_error" && text.trim().length > 0) {
        items.push({
          key: eventIdentity(event),
          kind: "error",
          text,
          characterCount: characterCount(text),
          eventCount: 1,
          streamId: event.streamId,
          ...(event.attempt === undefined ? {} : { attempt: event.attempt }),
          ...(event.outputKind === undefined ? {} : { outputKind: event.outputKind }),
        });
      }
      continue;
    }
    if (event.textMode === "snapshot") {
      // Replace all blocks for this segment of this stream. Snapshots recover
      // missed deltas and may also contain the backend's validated repair.
      let firstIndex: number | undefined;
      for (let index = items.length - 1; index >= 0; index -= 1) {
        const item = items[index];
        if (item?.streamId === event.streamId && item.kind === event.segment) {
          firstIndex = index;
          items.splice(index, 1);
        }
      }
      if (text.length > 0) {
        items.splice(firstIndex ?? items.length, 0, {
          key: `${event.streamId}:${event.segment}:snapshot`,
          kind: event.segment,
          text,
          characterCount: characterCount(text),
          eventCount: 1,
          streamId: event.streamId,
          ...(event.attempt === undefined ? {} : { attempt: event.attempt }),
          ...(event.outputKind === undefined ? {} : { outputKind: event.outputKind }),
        });
      }
      continue;
    }
    if (text.length === 0) continue;

    const previous = items.at(-1);
    if (canMerge(previous, event)) {
      // Repeated closers, whitespace and words are legitimate token deltas.
      // Only the Engine cursor identifies a duplicate, never text equality.
      const mergedText = previous.text + text;
      items[items.length - 1] = {
        ...previous,
        text: mergedText,
        characterCount: characterCount(mergedText),
        eventCount: previous.eventCount + 1,
      };
      continue;
    }

    items.push({
      key: eventIdentity(event),
      kind: event.segment,
      text,
      characterCount: characterCount(text),
      eventCount: 1,
      streamId: event.streamId,
      ...(event.attempt === undefined ? {} : { attempt: event.attempt }),
      ...(event.outputKind === undefined ? {} : { outputKind: event.outputKind }),
    });
  }

  return {
    items,
    contentCharacterCount: items
      .filter((item) => item.kind === "content")
      .reduce((total, item) => total + item.characterCount, 0),
    reasoningBlockCount: items.filter((item) => item.kind === "reasoning").length,
  };
}

export function taskStreamTranscriptFromState(stream: TaskStreamState | null): TaskStreamTranscript {
  return buildTaskStreamTranscript(stream?.events ?? []);
}
