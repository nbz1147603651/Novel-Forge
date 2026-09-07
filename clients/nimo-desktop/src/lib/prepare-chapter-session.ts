/**
 * prepare-chapter-session — pure state machine for the prepare_chapter flow.
 *
 * This module is framework-agnostic: no React, no timers, no side effects.
 * The `usePrepareChapter` hook delegates all state transitions here and
 * only manages lifecycle (polling timers, unmount cleanup).
 *
 * Design rationale:
 * - Testable in plain Vitest (Node environment) without jsdom or
 *   @testing-library/react.
 * - Every transition is deterministic and inspectable.
 * - The hook never invents state; it only dispatches actions derived
 *   from Engine responses.
 */

import type { TaskStreamEvent, TaskStreamView } from "@nimo/engine-contracts";

// ── Types ─────────────────────────────────────────────────────────────────────

export type PreparePhase =
  | "idle"
  | "submitting"
  | "running"
  | "checkpoint"
  | "succeeded"
  | "failed"
  | "cancelled"
  | "disconnected";

export interface PrepareSessionState {
  readonly phase: PreparePhase;
  readonly taskId: string | null;
  readonly message: string;
  readonly stream: TaskStreamView | null;
  readonly error: string | null;
  readonly progressPercent: number;
}

export type PrepareSessionAction =
  | { readonly type: "SUBMIT"; readonly projectId: string; readonly chapterNumber: number }
  | { readonly type: "ACCEPTED"; readonly taskId: string; readonly message: string }
  | { readonly type: "REJECTED"; readonly message: string }
  | { readonly type: "ALREADY_RUNNING"; readonly message: string }
  | { readonly type: "POLL_RESULT"; readonly stream: TaskStreamView }
  | { readonly type: "CHECKPOINT_DETECTED"; readonly message?: string }
  | { readonly type: "CANCELLED" }
  | { readonly type: "ERROR"; readonly message: string }
  | { readonly type: "TIMEOUT" }
  | { readonly type: "DISCONNECTED" }
  | { readonly type: "RESET" };

// ── Initial state ─────────────────────────────────────────────────────────────

export const INITIAL_PREPARE_STATE: PrepareSessionState = {
  phase: "idle",
  taskId: null,
  message: "",
  stream: null,
  error: null,
  progressPercent: 0,
};

// ── Reducer ───────────────────────────────────────────────────────────────────

export function prepareSessionReducer(
  state: PrepareSessionState,
  action: PrepareSessionAction,
): PrepareSessionState {
  switch (action.type) {
    case "SUBMIT":
      return {
        ...INITIAL_PREPARE_STATE,
        phase: "submitting",
        message: `正在提交第 ${action.chapterNumber} 章准备命令…`,
      };

    case "ACCEPTED":
      return {
        ...state,
        phase: "running",
        taskId: action.taskId,
        message: action.message,
        progressPercent: 0,
      };

    case "REJECTED":
      return {
        ...state,
        phase: "failed",
        error: action.message,
      };

    case "ALREADY_RUNNING":
      return {
        ...state,
        phase: "running",
        message: action.message,
      };

    case "POLL_RESULT": {
      const stream = action.stream;

      if (stream.status === "completed") {
        return {
          ...state,
          phase: "succeeded",
          stream,
          message: "章节准备完成",
          progressPercent: 100,
        };
      }

      if (stream.status === "failed") {
        const errorEvent = [...stream.events].reverse().find(
          (e: TaskStreamEvent) => e.kind === "stream_error",
        );
        return {
          ...state,
          phase: "failed",
          stream,
          error: errorEvent?.message ?? "任务执行失败",
        };
      }

      // Still streaming or paused — update progress from stream if available.
      const progress = "progressPercent" in stream
        ? (stream as unknown as { progressPercent: number }).progressPercent
        : state.progressPercent;

      return {
        ...state,
        stream,
        progressPercent: progress,
      };
    }

    case "CHECKPOINT_DETECTED":
      return {
        ...state,
        phase: "checkpoint",
        message: action.message ?? "任务已暂停，等待人工决策",
      };

    case "CANCELLED":
      return {
        ...state,
        phase: "cancelled",
        message: "任务已取消",
      };

    case "ERROR":
      return {
        ...state,
        phase: "failed",
        error: action.message,
      };

    case "TIMEOUT":
      return {
        ...state,
        phase: "disconnected",
        error: "任务轮询超时，后台任务可能仍在运行。请刷新页面或检查后端状态。",
      };

    case "DISCONNECTED":
      return {
        ...state,
        phase: "disconnected",
        error: "与后端连接中断，后台任务可能仍在运行。",
      };

    case "RESET":
      return INITIAL_PREPARE_STATE;

    default:
      return state;
  }
}
