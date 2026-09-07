import { useCallback, useEffect, useRef, useState } from "react";

import type { OperationState } from "../components/OperationProgress";

/** Local-only state machine that lets the UI exercise command lifecycles. */
export function useSessionOperation() {
  const [state, setState] = useState<OperationState>("configuration");
  const timeoutRef = useRef<number | null>(null);

  useEffect(() => () => {
    if (timeoutRef.current !== null) window.clearTimeout(timeoutRef.current);
  }, []);

  const start = useCallback(() => {
    if (timeoutRef.current !== null) window.clearTimeout(timeoutRef.current);
    setState("running");
    timeoutRef.current = window.setTimeout(() => {
      timeoutRef.current = null;
      setState("completed");
    }, 780);
  }, []);

  const reset = useCallback(() => {
    if (timeoutRef.current !== null) window.clearTimeout(timeoutRef.current);
    timeoutRef.current = null;
    setState("configuration");
  }, []);

  return { reset, start, state } as const;
}
