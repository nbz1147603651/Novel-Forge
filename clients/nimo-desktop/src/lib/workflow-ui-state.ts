/**
 * Workflow UI state persistence — mirrors PySide6 page.py export_ui_state/restore_ui_state.
 *
 * PySide6 source (workflow/page.py):
 * - export_ui_state() → {"version": 1, "mode": "short"|"long", "long": {...}}
 * - restore_ui_state(payload) → restores mode bar and long panel context
 * - Long panel state: mode, init_project_id, chapter_project_id, chapter_number
 *
 * React implementation uses localStorage with the same shape.
 */

import { useCallback, useEffect, useState } from "react";

const STORAGE_KEY = "nimo:workflow-ui-state";

export interface WorkflowUiStateLongContext {
  readonly mode?: string;
  readonly initProjectId?: string;
  readonly chapterProjectId?: string;
  readonly chapterNumber?: number;
}

export interface WorkflowUiState {
  readonly version: number;
  readonly mode: "short" | "long";
  readonly long?: WorkflowUiStateLongContext;
}

const defaultUiState: WorkflowUiState = {
  version: 1,
  mode: "short",
};

// ── Storage helpers ──────────────────────────────────────────────────

export function saveWorkflowUiState(state: WorkflowUiState): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
  } catch {
    // Storage full or unavailable — non-fatal
  }
}

export function loadWorkflowUiState(): WorkflowUiState {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return defaultUiState;
    const parsed = JSON.parse(raw) as Record<string, unknown>;
    if (parsed.version !== 1) return defaultUiState;
    const mode = parsed.mode === "long" ? "long" : "short";
    const long = parsed.long;
    if (long !== undefined && long !== null && typeof long === "object") {
      return { version: 1, mode, long: long as WorkflowUiStateLongContext };
    }
    return { version: 1, mode };
  } catch {
    return defaultUiState;
  }
}

// ── React hook ───────────────────────────────────────────────────────

export interface UseWorkflowUiStateOptions {
  /** Current mode from the mode bar. */
  mode: "short" | "long";
  /** Called when state is restored on mount. */
  onRestore?: (state: WorkflowUiState) => void;
}

/**
 * React hook for workflow UI state persistence.
 * Saves on mode change, restores on mount.
 */
export function useWorkflowUiState({ mode, onRestore }: UseWorkflowUiStateOptions): {
  /** The restored state (available after mount). */
  restoredState: WorkflowUiState | null;
  /** Manually save the current state. */
  saveState: (longContext?: WorkflowUiStateLongContext) => void;
} {
  const [restoredState, setRestoredState] = useState<WorkflowUiState | null>(null);

  // Restore on mount
  useEffect(() => {
    const state = loadWorkflowUiState();
    setRestoredState(state);
    onRestore?.(state);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Save on mode change
  useEffect(() => {
    saveWorkflowUiState({ version: 1, mode });
  }, [mode]);

  const saveState = useCallback(
    (longContext?: WorkflowUiStateLongContext) => {
      if (longContext !== undefined) {
        saveWorkflowUiState({ version: 1, mode, long: longContext });
      } else {
        saveWorkflowUiState({ version: 1, mode });
      }
    },
    [mode],
  );

  return { restoredState, saveState };
}

// ── Focus navigation helpers ─────────────────────────────────────────

/**
 * Focus request types mirroring PySide6 _pending_focus_request:
 * - "project": focus a specific project
 * - "short": focus short create mode
 * - "long_init": focus long init mode
 * - "long_init_project": focus long init with a specific project
 * - "long_chapter": focus long chapter mode
 */
export type WorkflowFocusRequest =
  | { readonly action: "project"; readonly projectId: string; readonly chapterNumber?: number }
  | { readonly action: "short" }
  | { readonly action: "long_init" }
  | { readonly action: "long_init_project"; readonly projectId: string }
  | { readonly action: "long_chapter" };

/**
 * Encodes a focus request for cross-page navigation.
 * Used when navigating from Dashboard or Chapter Studio to Workflow.
 */
export function encodeFocusRequest(request: WorkflowFocusRequest): string {
  return encodeURIComponent(JSON.stringify(request));
}

export function decodeFocusRequest(encoded: string): WorkflowFocusRequest | null {
  try {
    const parsed = JSON.parse(decodeURIComponent(encoded)) as WorkflowFocusRequest;
    if (!parsed || typeof parsed.action !== "string") return null;
    return parsed;
  } catch {
    return null;
  }
}
