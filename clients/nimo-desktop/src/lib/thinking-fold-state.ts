/**
 * Pure state machine for the shared thinking fold/expand controller.
 *
 * The React hook in `use-thinking-fold-state.ts` is a thin wrapper over this
 * reducer so the same behavior can be exercised by Node-only unit tests
 * without a DOM (the project does not currently ship jsdom/happy-dom).
 *
 * Behavior matches the source PySide6 reader (stream_detail.py::SegmentWidget):
 *  - new reasoning segments start folded
 *  - `foldAll()` collapses every segment and any future segment
 *  - `expandAll()` opens every segment and any future segment
 *  - a manual `toggle()` clears the bulk preference so only the touched
 *    segment changes
 *  - `taskId` change resets the state
 */

export type ThinkingFoldPreference = "expanded" | "collapsed" | null;

export interface ThinkingFoldState {
  /** Identifiers of segments the user has explicitly expanded. */
  readonly expandedKeys: ReadonlySet<string>;
  /** Bulk override applied to all segments, including future arrivals. */
  readonly preference: ThinkingFoldPreference;
}

export type ThinkingFoldAction =
  | { readonly type: "reset" }
  | { readonly type: "toggle"; readonly key: string; readonly open: boolean; readonly keys: readonly string[] }
  | { readonly type: "foldAll" }
  | { readonly type: "expandAll"; readonly keys: readonly string[] };

export const INITIAL_THINKING_FOLD_STATE: ThinkingFoldState = {
  expandedKeys: new Set<string>(),
  preference: null,
};

export function thinkingFoldReducer(
  state: ThinkingFoldState,
  action: ThinkingFoldAction,
): ThinkingFoldState {
  switch (action.type) {
    case "reset":
      return INITIAL_THINKING_FOLD_STATE;
    case "foldAll":
      return { expandedKeys: new Set<string>(), preference: "collapsed" };
    case "expandAll":
      return { expandedKeys: new Set<string>(action.keys), preference: "expanded" };
    case "toggle": {
      // When the bulk preference is set, the toggle baseline should match it
      // so flipping one key keeps every other key in sync with the bulk
      // state.  Otherwise we mutate the existing per-key set.
      const baseline = state.preference === "expanded"
        ? new Set<string>(action.keys)
        : state.preference === "collapsed"
          ? new Set<string>()
          : new Set(state.expandedKeys);
      if (action.open) baseline.add(action.key);
      else baseline.delete(action.key);
      return { expandedKeys: baseline, preference: null };
    }
  }
}

export function isReasoningOpen(
  state: ThinkingFoldState,
  key: string,
): boolean {
  if (state.preference === "expanded") return true;
  if (state.preference === "collapsed") return false;
  return state.expandedKeys.has(key);
}

/**
 * Public surface exposed to the React hook and any future consumer.  Kept
 * here so the hook module only re-exports the type without redefining it.
 */
export interface ThinkingFoldApi {
  /** Whether the named reasoning key should render in its expanded state. */
  readonly isOpen: (key: string) => boolean;
  /** Persist a manual toggle for a single segment. */
  readonly toggle: (key: string, open: boolean) => void;
  /** Collapse every reasoning segment (existing and forthcoming). */
  readonly foldAll: () => void;
  /** Expand every reasoning segment (existing and forthcoming). */
  readonly expandAll: () => void;
  /** True when at least one reasoning key is known. */
  readonly hasReasoning: boolean;
  /** Current bulk preference, exposed for tests and toolbar accessibility. */
  readonly preference: ThinkingFoldPreference;
}
