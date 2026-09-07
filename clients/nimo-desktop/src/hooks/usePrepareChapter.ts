/**
 * usePrepareChapter — thin React shell for the prepare_chapter flow.
 *
 * All state transitions are delegated to the pure `prepareSessionReducer`
 * in `lib/prepare-chapter-session.ts`. This hook only manages:
 * - Polling timers and visibility-based throttling
 * - Component unmount cleanup
 * - Async command submission and stream fetching
 *
 * The hook never invents state; it only dispatches actions derived from
 * Engine responses. State logic is fully testable without React.
 */

import { useCallback, useEffect, useReducer, useRef } from "react";

import type {
  ChapterCommandResult,
  EngineClient,
  EngineCommandClient,
} from "@nimo/engine-contracts";

import {
  INITIAL_PREPARE_STATE,
  prepareSessionReducer,
  type PrepareSessionState,
} from "../lib/prepare-chapter-session";

// Re-export for consumers that imported from this module previously.
export type { PreparePhase, PrepareSessionState } from "../lib/prepare-chapter-session";

export interface PrepareChapterOptions {
  readonly force?: boolean;
  readonly notes?: string;
  readonly rewriteStrategy?: "auto" | "sequential" | "compatible" | "reconstruct" | "surgical";
  readonly writingMode?: "whole_chapter" | "scene_level";
}

// ── Configuration ─────────────────────────────────────────────────────────────

const POLL_INTERVAL_MS = 2_000;
const POLL_INTERVAL_HIDDEN_MS = 10_000;
const DEFAULT_DEADLINE_MS = 30 * 60 * 1000; // 30 minutes
const TASK_ID_STORAGE_KEY = "nimo:prepare-task-id";
const TASK_ID_MAX_AGE_MS = 30 * 60 * 1000; // 30 minutes

// ── localStorage persistence for page-refresh recovery ────────────────────────

interface StoredTaskRecord {
  readonly taskId: string;
  readonly projectId: string;
  readonly savedAt: number;
}

function persistTaskId(taskId: string, projectId: string): void {
  try {
    const record: StoredTaskRecord = { taskId, projectId, savedAt: Date.now() };
    localStorage.setItem(TASK_ID_STORAGE_KEY, JSON.stringify(record));
  } catch { /* storage unavailable */ }
}

function loadStoredTaskId(projectId: string): string | null {
  try {
    const raw = localStorage.getItem(TASK_ID_STORAGE_KEY);
    if (!raw) return null;
    const record = JSON.parse(raw) as StoredTaskRecord;
    if (record.projectId !== projectId) return null;
    if (Date.now() - record.savedAt > TASK_ID_MAX_AGE_MS) {
      clearStoredTaskId();
      return null;
    }
    return record.taskId;
  } catch {
    return null;
  }
}

function clearStoredTaskId(): void {
  try {
    localStorage.removeItem(TASK_ID_STORAGE_KEY);
  } catch { /* ignore */ }
}

// ── Hook ──────────────────────────────────────────────────────────────────────

export function usePrepareChapter(
  engineClient: EngineClient,
  commandClient: EngineCommandClient,
  deadlineMs: number = DEFAULT_DEADLINE_MS,
) {
  const [state, dispatch] = useReducer(prepareSessionReducer, INITIAL_PREPARE_STATE);
  const pollTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const startTimeRef = useRef(0);
  const mountedRef = useRef(true);
  const activeRunRef = useRef(0);

  const clearPoll = useCallback(() => {
    if (pollTimerRef.current !== null) {
      clearTimeout(pollTimerRef.current);
      pollTimerRef.current = null;
    }
  }, []);

  const getPollDelay = useCallback((): number => {
    if (typeof document !== "undefined" && document.visibilityState === "hidden") {
      return POLL_INTERVAL_HIDDEN_MS;
    }
    return POLL_INTERVAL_MS;
  }, []);

  const pollTaskStream = useCallback(async (taskId: string, runId: number) => {
    if (!mountedRef.current || activeRunRef.current !== runId) return;

    // Check deadline
    if (Date.now() - startTimeRef.current > deadlineMs) {
      dispatch({ type: "TIMEOUT" });
      return;
    }

    try {
      const stream = await engineClient.getTaskStream(taskId);
      if (!mountedRef.current || activeRunRef.current !== runId) return;

      dispatch({ type: "POLL_RESULT", stream });

      // Terminal states — stop polling and clear persisted taskId
      if (stream.status === "completed" || stream.status === "failed") {
        clearStoredTaskId();
        return;
      }

      // Detect paused → checkpoint (batch 4 will add jobState to stream;
      // for now check the status field which maps paused → "paused").
      if (stream.status === "paused") {
        dispatch({ type: "CHECKPOINT_DETECTED" });
        return;
      }

      // Continue polling with visibility-aware delay
      pollTimerRef.current = setTimeout(() => void pollTaskStream(taskId, runId), getPollDelay());
    } catch {
      if (!mountedRef.current || activeRunRef.current !== runId) return;
      // Network error during poll — retry with backoff, don't fail the task
      const delay = Math.min(getPollDelay() * 2, 15_000);
      pollTimerRef.current = setTimeout(() => void pollTaskStream(taskId, runId), delay);
    }
  }, [engineClient, deadlineMs, getPollDelay]);

  const submit = useCallback(async (
    projectId: string,
    chapterNumber: number,
    options: PrepareChapterOptions = {},
  ) => {
    const runId = activeRunRef.current + 1;
    activeRunRef.current = runId;
    clearPoll();
    startTimeRef.current = Date.now();
    dispatch({ type: "SUBMIT", projectId, chapterNumber });

    try {
      const result: ChapterCommandResult = await commandClient.prepareChapter({
        kind: "prepare_chapter",
        projectId,
        chapterNumber,
        ...options,
      });

      if (!mountedRef.current || activeRunRef.current !== runId) return;

      if (result.status === "rejected") {
        dispatch({ type: "REJECTED", message: result.message });
        return;
      }

      if (result.status === "already_running") {
        dispatch({ type: "ALREADY_RUNNING", message: result.message });
        return;
      }

      // Accepted — start polling task stream
      const taskId = result.taskId ?? null;
      dispatch({ type: "ACCEPTED", taskId: taskId ?? "", message: result.message });

      if (taskId) {
        persistTaskId(taskId, projectId);
        pollTimerRef.current = setTimeout(() => void pollTaskStream(taskId, runId), POLL_INTERVAL_MS);
      }
    } catch (err) {
      if (!mountedRef.current || activeRunRef.current !== runId) return;
      dispatch({ type: "ERROR", message: err instanceof Error ? err.message : String(err) });
    }
  }, [commandClient, clearPoll, pollTaskStream]);

  const cancel = useCallback(async (projectId: string, chapterNumber: number) => {
    const runId = activeRunRef.current + 1;
    activeRunRef.current = runId;
    clearPoll();
    try {
      await commandClient.cancelChapter({
        kind: "cancel_chapter",
        projectId,
        chapterNumber,
      });
      if (mountedRef.current && activeRunRef.current === runId) {
        dispatch({ type: "CANCELLED" });
      }
    } catch (err) {
      if (mountedRef.current && activeRunRef.current === runId) {
        dispatch({ type: "ERROR", message: err instanceof Error ? err.message : "取消失败" });
      }
    }
  }, [commandClient, clearPoll]);

  const reset = useCallback(() => {
    activeRunRef.current += 1;
    clearPoll();
    clearStoredTaskId();
    startTimeRef.current = 0;
    dispatch({ type: "RESET" });
  }, [clearPoll]);

  // Page-refresh recovery: resume polling if a taskId was persisted.
  const resume = useCallback((projectId: string) => {
    const storedTaskId = loadStoredTaskId(projectId);
    if (storedTaskId === null) return;
    const runId = activeRunRef.current + 1;
    activeRunRef.current = runId;
    clearPoll();
    startTimeRef.current = Date.now();
    dispatch({ type: "ACCEPTED", taskId: storedTaskId, message: "正在恢复任务观察…" });
    pollTimerRef.current = setTimeout(() => void pollTaskStream(storedTaskId, runId), POLL_INTERVAL_MS);
  }, [clearPoll, pollTaskStream]);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      activeRunRef.current += 1;
      clearPoll();
    };
  }, [clearPoll]);

  return { state: state as PrepareSessionState, submit, cancel, reset, resume };
}
