/**
 * Task Queue Manager — mirrors PySide6 desktop/jobs.py patterns.
 *
 * Provides concurrent task management with:
 * - Structured error summaries (DesktopErrorSummary pattern)
 * - Task cancellation with graceful shutdown
 * - Timeout handling
 * - Progress tracking via SSE events
 * - Checkpoint/resume support for crash recovery
 */

import type { TaskStreamEvent } from "@nimo/engine-contracts";

import type { ErrorSummaryData } from "../components/ErrorSummary";

// ── Types ───────────────────────────────────────────────────────────────────

export type TaskStatus = "queued" | "running" | "succeeded" | "failed" | "cancelled" | "timeout";

export interface TaskProgress {
  readonly percent: number;
  readonly currentStep: string;
  readonly detail?: string;
}

export interface ManagedTask {
  readonly id: string;
  readonly kind: string;
  readonly label: string;
  readonly projectId: string;
  status: TaskStatus;
  progress: TaskProgress;
  error: ErrorSummaryData | null;
  startedAt: number | null;
  finishedAt: number | null;
  /** Checkpoint data for crash recovery. */
  checkpoint: TaskCheckpoint | null;
  /** Bounded stream event log (keeps the most recent events). */
  events: TaskStreamEvent[];
  /** Cumulative delta characters received, updated incrementally. */
  streamChars: number;
}

/** Maximum events retained per task; older entries are dropped. */
export const MAX_TASK_EVENTS = 300;

export interface TaskCheckpoint {
  readonly stageId: string;
  readonly stageLabel: string;
  readonly savedAt: number;
  readonly resumePayload?: Record<string, unknown>;
}

export interface TaskQueueOptions {
  /** Maximum concurrent running tasks. Default: 3. */
  readonly maxConcurrent?: number;
  /** Default timeout per task in ms. Default: 900_000 (15 min). */
  readonly defaultTimeoutMs?: number;
}

type TaskListener = (task: ManagedTask) => void;

// ── Task Queue Manager ──────────────────────────────────────────────────────

/**
 * Manages a queue of background tasks with concurrency control,
 * timeout handling, and structured error reporting.
 *
 * Mirrors the PySide6 `jobs.py` BackgroundTaskManager pattern:
 * - Tasks are submitted and queued
 * - Up to maxConcurrent tasks run simultaneously
 * - Each task has progress tracking and error summaries
 * - Cancelled tasks preserve their checkpoint for resume
 */
export class TaskQueueManager {
  private readonly maxConcurrent: number;
  private readonly defaultTimeoutMs: number;
  private readonly tasks = new Map<string, ManagedTask>();
  private readonly listeners = new Set<TaskListener>();
  private readonly timeouts = new Map<string, ReturnType<typeof setTimeout>>();

  constructor(options: TaskQueueOptions = {}) {
    this.maxConcurrent = options.maxConcurrent ?? 3;
    this.defaultTimeoutMs = options.defaultTimeoutMs ?? 900_000;
  }

  // ── Public API ──────────────────────────────────────────────────────────

  /** Submit a new task to the queue. Returns the task ID. */
  submit(task: {
    id: string;
    kind: string;
    label: string;
    projectId: string;
    timeoutMs?: number;
  }): string {
    const managed: ManagedTask = {
      id: task.id,
      kind: task.kind,
      label: task.label,
      projectId: task.projectId,
      status: "queued",
      progress: { percent: 0, currentStep: "排队中" },
      error: null,
      startedAt: null,
      finishedAt: null,
      checkpoint: null,
      events: [],
      streamChars: 0,
    };
    this.tasks.set(task.id, managed);
    this.notify(managed);
    this.promoteQueued();
    return task.id;
  }

  /** Mark a task as running (called when backend confirms start). */
  markRunning(taskId: string): void {
    const task = this.tasks.get(taskId);
    if (!task || task.status !== "queued") return;
    task.status = "running";
    task.startedAt = Date.now();
    task.progress = { percent: 5, currentStep: "已启动" };
    this.startTimeout(taskId);
    this.notify(task);
  }

  /** Update task progress from SSE events. */
  updateProgress(taskId: string, progress: Partial<TaskProgress>): void {
    const task = this.tasks.get(taskId);
    if (!task || task.status !== "running") return;
    task.progress = { ...task.progress, ...progress };
    this.notify(task);
  }

  /** Append a stream event to the task's event log. */
  appendEvent(taskId: string, event: TaskStreamEvent): void {
    const task = this.tasks.get(taskId);
    if (!task) return;
    // Bounded event log: keep a constant-cost copy window instead of growing
    // the array without limit on long streams.
    task.events = [...task.events.slice(-(MAX_TASK_EVENTS - 1)), event];
    // Auto-update progress from stream events using an incremental counter;
    // a full filter+reduce over the whole log was O(n) per delta.
    if (event.kind === "delta" && event.text) {
      task.streamChars += event.text.length;
      task.progress = { ...task.progress, detail: `${task.streamChars} 字已生成` };
    }
    this.notify(task);
  }

  /** Save a checkpoint for crash recovery. */
  saveCheckpoint(taskId: string, checkpoint: TaskCheckpoint): void {
    const task = this.tasks.get(taskId);
    if (!task) return;
    task.checkpoint = checkpoint;
    this.notify(task);
  }

  /** Mark a task as succeeded. */
  markSucceeded(taskId: string): void {
    const task = this.tasks.get(taskId);
    if (!task) return;
    task.status = "succeeded";
    task.finishedAt = Date.now();
    task.progress = { percent: 100, currentStep: "已完成" };
    this.clearTimeout(taskId);
    this.notify(task);
    this.promoteQueued();
  }

  /** Mark a task as failed with a structured error summary. */
  markFailed(taskId: string, error: ErrorSummaryData): void {
    const task = this.tasks.get(taskId);
    if (!task) return;
    task.status = "failed";
    task.finishedAt = Date.now();
    task.error = error;
    this.clearTimeout(taskId);
    this.notify(task);
    this.promoteQueued();
  }

  /** Cancel a task. Preserves checkpoint for potential resume. */
  cancel(taskId: string): void {
    const task = this.tasks.get(taskId);
    if (!task || task.status === "succeeded" || task.status === "failed") return;
    task.status = "cancelled";
    task.finishedAt = Date.now();
    task.progress = { ...task.progress, currentStep: "已取消" };
    this.clearTimeout(taskId);
    this.notify(task);
    this.promoteQueued();
  }

  /** Resume a cancelled/failed task from its checkpoint. */
  resume(taskId: string): boolean {
    const task = this.tasks.get(taskId);
    if (!task || !task.checkpoint) return false;
    task.status = "queued";
    task.error = null;
    task.finishedAt = null;
    task.progress = { percent: 0, currentStep: `从断点恢复：${task.checkpoint.stageLabel}` };
    this.notify(task);
    this.promoteQueued();
    return true;
  }

  /** Get a task by ID. */
  get(taskId: string): ManagedTask | undefined {
    return this.tasks.get(taskId);
  }

  /** List all tasks, optionally filtered by status. */
  list(filter?: TaskStatus): ManagedTask[] {
    const all = [...this.tasks.values()];
    return filter ? all.filter((t) => t.status === filter) : all;
  }

  /** Get the count of currently running tasks. */
  get runningCount(): number {
    return this.list("running").length;
  }

  /** Subscribe to task state changes. Returns unsubscribe function. */
  subscribe(listener: TaskListener): () => void {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  /** Remove a task from the queue (cleanup). */
  remove(taskId: string): void {
    this.clearTimeout(taskId);
    this.tasks.delete(taskId);
  }

  /** Clear all finished tasks (succeeded/failed/cancelled). */
  clearFinished(): void {
    for (const [id, task] of this.tasks) {
      if (task.status === "succeeded" || task.status === "failed" || task.status === "cancelled") {
        this.tasks.delete(id);
      }
    }
  }

  // ── Private ─────────────────────────────────────────────────────────────

  private promoteQueued(): void {
    if (this.runningCount >= this.maxConcurrent) return;
    const queued = this.list("queued");
    for (const task of queued) {
      if (this.runningCount >= this.maxConcurrent) break;
      // Promotion is signaled to the backend; actual running state
      // is set when the backend confirms via markRunning()
      this.notify(task);
    }
  }

  private startTimeout(taskId: string): void {
    this.clearTimeout(taskId);
    const timer = setTimeout(() => {
      const task = this.tasks.get(taskId);
      if (task && task.status === "running") {
        task.status = "timeout";
        task.finishedAt = Date.now();
        task.error = {
          summary: "任务超时",
          detail: `任务在 ${Math.round(this.defaultTimeoutMs / 1000)} 秒内未完成，已自动终止。`,
          code: "TASK_TIMEOUT",
          retryable: true,
          timestamp: new Date().toLocaleTimeString("zh-CN"),
        };
        this.notify(task);
        this.promoteQueued();
      }
    }, this.defaultTimeoutMs);
    this.timeouts.set(taskId, timer);
  }

  private clearTimeout(taskId: string): void {
    const timer = this.timeouts.get(taskId);
    if (timer) {
      clearTimeout(timer);
      this.timeouts.delete(taskId);
    }
  }

  private notify(task: ManagedTask): void {
    for (const listener of this.listeners) {
      listener(task);
    }
  }
}

// ── Singleton instance ──────────────────────────────────────────────────────

let defaultManager: TaskQueueManager | null = null;

/** Get the default task queue manager singleton. */
export function getTaskQueue(): TaskQueueManager {
  if (!defaultManager) {
    defaultManager = new TaskQueueManager();
  }
  return defaultManager;
}
