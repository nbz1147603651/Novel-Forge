import type { TaskModelCallView, TaskStreamEvent, TaskStreamRuntimeSummary, TaskStreamView } from "@nimo/engine-contracts";

export interface TaskStreamState {
  readonly taskId: string;
  readonly title: string;
  readonly stepLabel: string;
  /** Raw step key from the engine snapshot; phase detection prefers it. */
  readonly stepId: string;
  readonly status: TaskStreamView["status"];
  readonly jobState: TaskStreamView["jobState"];
  readonly progressPercent: number;
  readonly delivery?: TaskStreamView["delivery"];
  readonly summary?: TaskStreamRuntimeSummary;
  readonly calls?: readonly TaskModelCallView[];
  readonly error?: TaskStreamView["error"];
  readonly events: readonly TaskStreamEvent[];
}

export type TaskStreamAction =
  | { readonly type: "reset" }
  | { readonly type: "replace"; readonly snapshot: TaskStreamView }
  | { readonly type: "event"; readonly event: TaskStreamEvent }
  | { readonly type: "events"; readonly events: readonly TaskStreamEvent[] };

/**
 * Maximum events retained in the presentation state. Long streams otherwise
 * grow without bound; the raw-event viewer already clips to the last 500.
 */
export const MAX_TASK_STREAM_EVENTS = 2_000;

function eventIdentity(event: TaskStreamEvent): string {
  return event.cursor
    ?? `${event.streamId}:${event.sequence}:${event.kind}:${event.segment}:${event.at ?? ""}`;
}

function orderedUnique(events: readonly TaskStreamEvent[]): readonly TaskStreamEvent[] {
  const byCursor = new Map<string, TaskStreamEvent>();
  for (const event of events) {
    byCursor.set(eventIdentity(event), event);
  }
  return [...byCursor.values()].sort((left, right) => {
    // Engine sequence numbers are page-local after bounded history compaction.
    // Durable timestamps/cursors retain order across those replay pages.
    const timeOrder = (left.at ?? "").localeCompare(right.at ?? "");
    if (timeOrder !== 0) return timeOrder;
    if (left.streamId === right.streamId) {
      const sequenceOrder = left.sequence - right.sequence;
      if (sequenceOrder !== 0) return sequenceOrder;
    }
    const sequenceOrder = left.sequence - right.sequence;
    if (sequenceOrder !== 0) return sequenceOrder;
    return eventIdentity(left).localeCompare(eventIdentity(right));
  });
}

export function createTaskStreamState(snapshot: TaskStreamView): TaskStreamState {
  return {
    taskId: snapshot.taskId,
    title: snapshot.title,
    stepLabel: snapshot.stepLabel,
    stepId: snapshot.stepId ?? snapshot.stepLabel,
    status: snapshot.status,
    jobState: snapshot.jobState,
    progressPercent: snapshot.progressPercent,
    ...(snapshot.delivery === undefined ? {} : { delivery: snapshot.delivery }),
    ...(snapshot.summary === undefined ? {} : { summary: snapshot.summary }),
    calls: snapshot.calls ?? [],
    ...(snapshot.error === undefined ? {} : { error: snapshot.error }),
    events: orderedUnique(snapshot.events),
  };
}

function statusAfterEvent(current: TaskStreamState["status"], event: TaskStreamEvent): TaskStreamState["status"] {
  if (event.kind === "stream_start" || event.kind === "restart") {
    return "streaming";
  }
  // stream_end / stream_error describe one model call, not necessarily the
  // whole workflow. The canonical job snapshot is the only terminal source.
  return current;
}

/**
 * Presentation reducer for a monotonic stream. Duplicate events can arrive
 * after an SSE reconnect or a sidecar replay, so sequence number is the
 * idempotency key rather than arrival order.
 */
export function taskStreamReducer(state: TaskStreamState | null, action: TaskStreamAction): TaskStreamState | null {
  if (action.type === "reset") return null;
  if (action.type === "replace") {
    const snapshotState = createTaskStreamState(action.snapshot);
    if (state === null || state.taskId !== snapshotState.taskId) {
      return snapshotState;
    }
    return {
      ...snapshotState,
      events: orderedUnique([
        ...state.events.slice(-MAX_TASK_STREAM_EVENTS),
        ...snapshotState.events,
      ]).slice(-MAX_TASK_STREAM_EVENTS),
    };
  }
  if (state === null) {
    return state;
  }
  const incoming = action.type === "event" ? [action.event] : action.events;
  const validIncoming = incoming.filter((event) => event.streamId !== "");
  if (validIncoming.length === 0) {
    return state;
  }
  const currentIds = new Set(state.events.map(eventIdentity));
  const novelEvents = validIncoming.filter((event) => !currentIds.has(eventIdentity(event)));
  if (novelEvents.length === 0) return state;
  const finalEvent = novelEvents[novelEvents.length - 1]!;
  return {
    ...state,
    status: statusAfterEvent(state.status, finalEvent),
    events: orderedUnique([
      ...state.events.slice(-MAX_TASK_STREAM_EVENTS),
      ...novelEvents,
    ]).slice(-MAX_TASK_STREAM_EVENTS),
  };
}
