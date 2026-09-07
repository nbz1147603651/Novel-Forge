import { useEffect, useReducer, useRef } from "react";

import type { EngineClient, TaskStreamEvent } from "@nimo/engine-contracts";

import { LatestRequestGate } from "./latest-request";
import { taskStreamReducer, type TaskStreamState } from "./task-stream";
import { getTaskQueue } from "./task-queue-manager";

export type TaskStreamClient = Pick<
  EngineClient,
  "getTaskStream" | "subscribeTaskStream"
>;

/** Subscribe at the engine boundary and expose a stable UI projection.
 *  Also feeds events into the TaskQueueManager for progress tracking,
 *  checkpoint persistence, and structured error reporting.
 */
export function useTaskStream(client: TaskStreamClient, taskId: string | null): TaskStreamState | null {
  const [state, dispatch] = useReducer(taskStreamReducer, null);
  const pendingEventsRef = useRef<TaskStreamEvent[]>([]);
  const flushTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const snapshotCursorRef = useRef<string | undefined>(undefined);
  const requestGateRef = useRef(new LatestRequestGate());

  useEffect(() => {
    if (taskId === null) {
      requestGateRef.current.invalidate();
      dispatch({ type: "reset" });
      return;
    }
    const queue = getTaskQueue();
    let disposed = false;
    let snapshotInFlight = false;
    let snapshotTimer: ReturnType<typeof setInterval> | null = null;
    const request = requestGateRef.current.begin(`task-stream:${taskId}`);
    dispatch({ type: "reset" });
    snapshotCursorRef.current = undefined;
    const flushPending = () => {
      flushTimerRef.current = null;
      if (disposed || !requestGateRef.current.isCurrent(request) || pendingEventsRef.current.length === 0) return;
      const events = pendingEventsRef.current;
      pendingEventsRef.current = [];
      dispatch({ type: "events", events });
    };
    const refreshSnapshot = async () => {
      if (snapshotInFlight || disposed || !requestGateRef.current.isCurrent(request)) return;
      snapshotInFlight = true;
      try {
        let hasMore = true;
        while (hasMore && !disposed && requestGateRef.current.isCurrent(request)) {
          try {
          const snapshot = await client.getTaskStream(
            taskId,
            snapshotCursorRef.current === undefined
              ? undefined
              : { afterCursor: snapshotCursorRef.current },
          );
          if (disposed || !requestGateRef.current.isCurrent(request)) return;
          dispatch({ type: "replace", snapshot });
          snapshotCursorRef.current = snapshot.nextCursor
            ?? snapshot.events.at(-1)?.cursor
            ?? snapshotCursorRef.current;
          hasMore = snapshot.hasMore === true && snapshotCursorRef.current !== undefined;
          if (snapshot.jobState !== "queued" && snapshot.jobState !== "running" && snapshotTimer !== null) {
            clearInterval(snapshotTimer);
            snapshotTimer = null;
          }
          } catch {
            return;
          }
        }
      } finally {
        snapshotInFlight = false;
      }
    };
    void refreshSnapshot();
    // Model stream end/error events are call-level signals. Polling the
    // durable job projection keeps pause/retry/final status authoritative and
    // also repairs any event gap after a network reconnect.
    snapshotTimer = setInterval(() => {
      void refreshSnapshot();
    }, 1_000);
    const unsubscribe = client.subscribeTaskStream(taskId, (event) => {
      if (disposed || !requestGateRef.current.isCurrent(request)) return;
      pendingEventsRef.current.push(event);
      if (flushTimerRef.current === null) {
        // Token deltas can arrive dozens of times per second. A short batch
        // window preserves perceived streaming while avoiding one React
        // render for every provider chunk.
        flushTimerRef.current = setTimeout(flushPending, 32);
      }

      // Feed events into the task queue for progress/error tracking
      queue.appendEvent(taskId, event);
      // Progress remains owned by the durable job snapshot. Counting provider
      // chunks here made the bar race to 95% for verbose models.
    });
    return () => {
      disposed = true;
      if (requestGateRef.current.isCurrent(request)) requestGateRef.current.invalidate();
      if (snapshotTimer !== null) clearInterval(snapshotTimer);
      if (flushTimerRef.current !== null) {
        clearTimeout(flushTimerRef.current);
        flushTimerRef.current = null;
      }
      pendingEventsRef.current = [];
      unsubscribe();
    };
  }, [client, taskId]);

  // Effects run after paint. Do not briefly render a prior task while React
  // switches subscriptions and waits for the new authoritative snapshot.
  return state?.taskId === taskId ? state : null;
}
