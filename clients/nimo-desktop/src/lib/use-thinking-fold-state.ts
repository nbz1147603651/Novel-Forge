import { useCallback, useEffect, useMemo, useReducer } from "react";

import {
  INITIAL_THINKING_FOLD_STATE,
  isReasoningOpen,
  thinkingFoldReducer,
  type ThinkingFoldApi,
  type ThinkingFoldState,
} from "./thinking-fold-state";

export type { ThinkingFoldApi, ThinkingFoldPreference, ThinkingFoldState } from "./thinking-fold-state";

/**
 * React adapter over the pure `thinking-fold-state` reducer.  The reducer
 * is exported separately so the same behavior can be unit-tested without a
 * DOM.
 */
export function useThinkingFoldState(
  taskId: string | null | undefined,
  reasoningKeys: readonly string[],
): ThinkingFoldApi {
  const [state, dispatch] = useReducer(thinkingFoldReducer, INITIAL_THINKING_FOLD_STATE);

  // Switching tasks (or losing the stream) drops the fold state so the new
  // reader starts from a clean baseline instead of inheriting toggle memory
  // from a previous task.
  useEffect(() => {
    dispatch({ type: "reset" });
  }, [taskId]);

  const isOpen = useCallback(
    (key: string): boolean => isReasoningOpen(state, key),
    [state],
  );

  const toggle = useCallback(
    (key: string, open: boolean) => {
      dispatch({ type: "toggle", key, open, keys: reasoningKeys });
    },
    [reasoningKeys],
  );

  const foldAll = useCallback(() => {
    dispatch({ type: "foldAll" });
  }, []);

  const expandAll = useCallback(() => {
    dispatch({ type: "expandAll", keys: reasoningKeys });
  }, [reasoningKeys]);

  return useMemo<ThinkingFoldApi>(
    () => ({
      isOpen,
      toggle,
      foldAll,
      expandAll,
      hasReasoning: reasoningKeys.length > 0,
      preference: state.preference,
    }),
    [expandAll, foldAll, isOpen, reasoningKeys.length, state.preference, toggle],
  );
}
