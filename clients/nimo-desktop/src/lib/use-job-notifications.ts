/**
 * Job state transition notifications.
 *
 * Watches the shared jobs list and fires desktop notifications when a job
 * transitions to succeeded or failed state. Mirrors PySide6 desktop
 * notification behavior (task completion / failure / decision required).
 */
import { useEffect, useRef } from "react";

import type { JobView } from "@nimo/engine-contracts";

import { notifyTaskFailure, notifyTaskSuccess } from "./desktop-notification";

/**
 * Fires desktop notifications when jobs transition to terminal states.
 *
 * Call this once at the App level with the shared jobs projection.
 */
export function useJobNotifications(jobs: readonly JobView[]): void {
  const previousStatesRef = useRef<ReadonlyMap<string, JobView["state"]>>(new Map());

  useEffect(() => {
    const previousStates = previousStatesRef.current;
    const nextStates = new Map<string, JobView["state"]>();

    for (const job of jobs) {
      nextStates.set(job.id, job.state);
      const previous = previousStates.get(job.id);

      // Only notify on transitions from active to terminal states
      if (previous === "running" || previous === "paused") {
        if (job.state === "succeeded") {
          notifyTaskSuccess(job.label);
        } else if (job.state === "failed") {
          notifyTaskFailure(job.label, job.detail || undefined);
        }
      }
    }

    previousStatesRef.current = nextStates;
  }, [jobs]);
}
