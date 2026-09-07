/**
 * Workflow draft autosave — mirrors PySide6 page.py autosave behavior.
 *
 * PySide6 source (workflow/page.py):
 * - _AUTOSAVE_INTERVAL_MS = 30_000 (30s periodic save)
 * - _DRAFT_AUTOSAVE_DEBOUNCE_MS = 750 (debounced save on input)
 * - _draft_path(mode) → storage_root / ".presets" / ".draft" / f"_autosave_{mode}.json"
 * - _restore_drafts() on first bind or storage-root change
 * - _do_autosave() called by timer
 * - _wire_debounced_draft_autosave() connects textChanged/valueChanged signals
 *
 * React implementation persists through the shared Engine boundary, with the
 * same timing semantics: 30s periodic + 750ms debounce on changes.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import type {
  EngineCommandClient,
  WorkflowDraftView,
  WorkflowPersistenceResult,
} from "@nimo/engine-contracts";

const AUTOSAVE_INTERVAL_MS = 30_000;
const DEBOUNCE_MS = 750;
const STORAGE_KEY_PREFIX = "nimo:workflow-draft:";

// ── Storage helpers ──────────────────────────────────────────────────

function draftKey(mode: "short" | "long"): string {
  return `${STORAGE_KEY_PREFIX}${mode}`;
}

export function saveDraftToStorage(mode: "short" | "long", payload: unknown): void {
  try {
    localStorage.setItem(draftKey(mode), JSON.stringify({ savedAt: Date.now(), payload }));
  } catch {
    // Storage full or unavailable — non-fatal
  }
}

export function loadDraftFromStorage<T>(mode: "short" | "long"): T | null {
  try {
    const raw = localStorage.getItem(draftKey(mode));
    if (!raw) return null;
    const parsed = JSON.parse(raw) as { savedAt: number; payload: T };
    return parsed.payload;
  } catch {
    return null;
  }
}

export function clearDraftFromStorage(mode: "short" | "long"): void {
  try {
    localStorage.removeItem(draftKey(mode));
  } catch {
    // Non-fatal
  }
}

// ── Revision-aware persistence queue ─────────────────────────────────

export interface RevisionedDraftSaveGateway<T> {
  readonly load: () => Promise<WorkflowDraftView>;
  readonly save: (
    payload: T,
    expectedRevision: string | undefined,
  ) => Promise<WorkflowPersistenceResult>;
}

export interface RevisionedDraftSaveCallbacks {
  readonly onFailure: (message: string) => void;
  readonly onSaved: () => void;
}

/**
 * Coalesces local edits into a single revision-aware write lane.
 *
 * Debounced, periodic, and manual saves all enqueue their newest payload here.
 * The next write never starts until the preceding revision has been accepted.
 * If another window updated the draft, the queue reloads that revision and
 * retries the newest local payload once instead of silently dropping it.
 */
export class RevisionedDraftSaveQueue<T> {
  private disposed = false;
  private pendingPayload: T | null = null;
  private revision: string | undefined;
  private running: Promise<void> | null = null;

  constructor(
    private readonly gateway: RevisionedDraftSaveGateway<T>,
    private readonly callbacks: RevisionedDraftSaveCallbacks,
  ) {}

  setRevision(revision: string | undefined): void {
    this.revision = revision;
  }

  enqueue(payload: T): Promise<void> {
    if (this.disposed) return Promise.resolve();
    this.pendingPayload = payload;
    if (this.running === null) {
      this.running = this.drain().finally(() => {
        this.running = null;
        if (this.pendingPayload !== null && !this.disposed) void this.enqueue(this.pendingPayload);
      });
    }
    return this.running ?? Promise.resolve();
  }

  dispose(): void {
    this.disposed = true;
    this.pendingPayload = null;
  }

  private async drain(): Promise<void> {
    while (!this.disposed && this.pendingPayload !== null) {
      let payload = this.pendingPayload;
      this.pendingPayload = null;
      let retriedAfterConflict = false;

      while (!this.disposed) {
        let result: WorkflowPersistenceResult;
        try {
          result = await this.gateway.save(payload, this.revision);
        } catch (error) {
          this.callbacks.onFailure(error instanceof Error ? error.message : "草稿自动保存失败。");
          break;
        }
        if (this.disposed) return;

        if (result.status === "saved") {
          this.revision = result.revision;
          this.callbacks.onSaved();
          break;
        }

        if (result.status === "conflict" && !retriedAfterConflict) {
          retriedAfterConflict = true;
          try {
            const latest = await this.gateway.load();
            if (this.disposed) return;
            this.revision = latest.revision || undefined;
            // Prefer edits made while the conflicting request was in flight.
            payload = this.pendingPayload ?? payload;
            this.pendingPayload = null;
            continue;
          } catch (error) {
            this.callbacks.onFailure(
              error instanceof Error ? error.message : "草稿发生冲突，且无法重新读取最新版本。",
            );
            break;
          }
        }

        this.callbacks.onFailure(result.message || "草稿自动保存未完成。");
        break;
      }
    }
  }
}

// ── React hook ───────────────────────────────────────────────────────

export interface UseDraftAutosaveOptions<T> {
  /** Durable Engine boundary shared with PySide6's .presets/.draft files. */
  commandClient: EngineCommandClient;
  /** The mode key for storage separation. */
  mode: "short" | "long";
  /** Current payload to save. */
  payload: T;
  /** Called to restore payload on mount. */
  onRestore: (payload: T) => void;
  /** Whether autosave is enabled (e.g., disabled while a job is running). */
  enabled?: boolean;
}

/**
 * React hook implementing PySide6's dual autosave strategy:
 * 1. Periodic 30s save (mirrors _autosave_timer)
 * 2. Debounced 750ms save on payload change (mirrors _draft_autosave_timer)
 * 3. Restore on mount (mirrors _restore_drafts)
 */
export function useDraftAutosave<T>({
  commandClient,
  mode,
  payload,
  onRestore,
  enabled = true,
}: UseDraftAutosaveOptions<T>): {
  /** Whether a draft was restored on mount. */
  restored: boolean;
  /** Timestamp of last save, for UI feedback. */
  lastSavedAt: number | null;
  /** Latest durable persistence failure; local form state is retained. */
  lastSaveError: string | null;
  /** Whether the initial durable draft read has finished (success or failure). */
  restoreFinished: boolean;
  /** Manually trigger a save (mirrors save_pending_changes). */
  saveNow: () => void;
} {
  const [restored, setRestored] = useState(false);
  const [lastSavedAt, setLastSavedAt] = useState<number | null>(null);
  const [lastSaveError, setLastSaveError] = useState<string | null>(null);
  const [restoreFinished, setRestoreFinished] = useState(false);
  const payloadRef = useRef(payload);
  const debounceTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const payloadVersionRef = useRef(0);
  const commandClientRef = useRef(commandClient);
  const modeRef = useRef(mode);
  const onRestoreRef = useRef(onRestore);
  const queueRef = useRef<RevisionedDraftSaveQueue<Readonly<Record<string, unknown>>> | null>(null);

  // Keep the latest form data and transport boundary available to the shared
  // queue without recreating a save lane on every keystroke.
  payloadRef.current = payload;
  commandClientRef.current = commandClient;
  modeRef.current = mode;
  onRestoreRef.current = onRestore;

  if (queueRef.current === null) {
    queueRef.current = new RevisionedDraftSaveQueue({
      load: () => commandClientRef.current.loadWorkflowDraft(modeRef.current),
      save: (nextPayload, expectedRevision) => commandClientRef.current.saveWorkflowDraft({
        kind: "save_workflow_draft",
        mode: modeRef.current,
        payload: nextPayload,
        ...(expectedRevision === undefined ? {} : { expectedRevision }),
      }),
    }, {
      onFailure: (message) => setLastSaveError(message),
      onSaved: () => {
        setLastSaveError(null);
        setLastSavedAt(Date.now());
      },
    });
  }

  useEffect(() => {
    payloadVersionRef.current += 1;
  }, [payload]);

  useEffect(() => () => queueRef.current?.dispose(), []);

  // Restore on mount
  useEffect(() => {
    let cancelled = false;
    const payloadVersion = payloadVersionRef.current;
    setRestoreFinished(false);
    void commandClient.loadWorkflowDraft(mode).then((draft) => {
      if (cancelled) return;
      queueRef.current?.setRevision(draft.revision || undefined);
      // Never replace edits made while the asynchronous initial read was in
      // flight. The local form remains authoritative in that situation.
      if (draft.payload !== null && payloadVersionRef.current === payloadVersion) {
        onRestoreRef.current(draft.payload as T);
        setRestored(true);
      }
      setRestoreFinished(true);
    }).catch(() => {
      if (!cancelled) setRestoreFinished(true);
    });
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [commandClient, mode]);

  // Debounced save on payload change (mirrors _wire_debounced_draft_autosave)
  useEffect(() => {
    if (!enabled || !restoreFinished) return;
    if (debounceTimerRef.current) {
      clearTimeout(debounceTimerRef.current);
    }
    debounceTimerRef.current = setTimeout(() => {
      void queueRef.current?.enqueue(payloadRef.current as Readonly<Record<string, unknown>>);
    }, DEBOUNCE_MS);
    return () => {
      if (debounceTimerRef.current) {
        clearTimeout(debounceTimerRef.current);
      }
    };
  }, [commandClient, payload, mode, enabled, restoreFinished]);

  // Periodic 30s save (mirrors _autosave_timer)
  useEffect(() => {
    if (!enabled || !restoreFinished) return;
    const interval = setInterval(() => {
      void queueRef.current?.enqueue(payloadRef.current as Readonly<Record<string, unknown>>);
    }, AUTOSAVE_INTERVAL_MS);
    return () => {
      clearInterval(interval);
    };
  }, [commandClient, mode, enabled, restoreFinished]);

  const saveNow = useCallback(() => {
    void queueRef.current?.enqueue(payloadRef.current as Readonly<Record<string, unknown>>);
  }, []);

  return { restored, lastSavedAt, lastSaveError, restoreFinished, saveNow };
}

// ── Unsaved changes tracking ─────────────────────────────────────────

/**
 * Tracks whether the form has unsaved changes (mirrors PySide6 has_unsaved_changes).
 * Returns a dirty flag and a markClean function.
 */
export function useUnsavedChangesTracker(): {
  isDirty: boolean;
  markDirty: () => void;
  markClean: () => void;
} {
  const [isDirty, setIsDirty] = useState(false);
  const markDirty = useCallback(() => setIsDirty(true), []);
  const markClean = useCallback(() => setIsDirty(false), []);
  return { isDirty, markDirty, markClean };
}
