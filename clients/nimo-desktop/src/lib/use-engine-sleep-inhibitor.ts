import { useEffect, useRef } from "react";

import type { EngineRuntimeView } from "@nimo/engine-contracts";

import {
  acquireSleepInhibitor,
  releaseSleepInhibitor,
} from "./native-bridge";

/**
 * Keep the assertion across the short worker gaps used by chapter autorun.
 *
 * The runtime diagnostic is polled every 15 seconds and the longest default
 * checkpoint retry delay is 48 seconds. Ninety seconds therefore prevents a
 * sleeping Mac from suspending the Engine between two durable attempts while
 * still releasing a stale assertion after a stopped or unreachable Engine.
 */
export const SLEEP_INHIBITOR_RELEASE_GRACE_MS = 90_000;

type SleepCommand = () => Promise<void>;
type TimerHandle = ReturnType<typeof setTimeout>;

interface SleepInhibitorControllerOptions {
  readonly acquire?: SleepCommand;
  readonly release?: SleepCommand;
  readonly releaseGraceMs?: number;
  readonly schedule?: (callback: () => void, delayMs: number) => TimerHandle;
  readonly cancel?: (timer: TimerHandle) => void;
}

/**
 * Serialize the UI's desired native sleep assertion without tying the policy
 * to a page or to one particular job id. The Engine runtime remains the source
 * of truth for whether executable work exists.
 */
export class SleepInhibitorController {
  private readonly acquire: SleepCommand;
  private readonly release: SleepCommand;
  private readonly releaseGraceMs: number;
  private readonly schedule: (callback: () => void, delayMs: number) => TimerHandle;
  private readonly cancel: (timer: TimerHandle) => void;
  private held = false;
  private releaseTimer: TimerHandle | null = null;

  constructor(options: SleepInhibitorControllerOptions = {}) {
    this.acquire = options.acquire ?? acquireSleepInhibitor;
    this.release = options.release ?? releaseSleepInhibitor;
    this.releaseGraceMs = options.releaseGraceMs ?? SLEEP_INHIBITOR_RELEASE_GRACE_MS;
    this.schedule = options.schedule ?? setTimeout;
    this.cancel = options.cancel ?? clearTimeout;
  }

  update(hasActiveWork: boolean): void {
    this.cancelPendingRelease();
    if (hasActiveWork) {
      if (this.held) return;
      this.held = true;
      void this.acquire();
      return;
    }
    if (!this.held) {
      // A WebView reload can outlive its previous React tree. Reconcile an
      // orphaned native assertion even when this controller did not acquire it.
      void this.release();
      return;
    }
    this.releaseTimer = this.schedule(() => {
      this.releaseTimer = null;
      if (!this.held) return;
      this.held = false;
      void this.release();
    }, this.releaseGraceMs);
  }

  dispose(): void {
    this.cancelPendingRelease();
    if (!this.held) return;
    this.held = false;
    void this.release();
  }

  private cancelPendingRelease(): void {
    if (this.releaseTimer === null) return;
    this.cancel(this.releaseTimer);
    this.releaseTimer = null;
  }
}

export function engineRuntimeHasActiveWork(runtime: EngineRuntimeView | undefined): boolean {
  return runtime !== undefined
    && (runtime.activeJobCount > 0 || runtime.queuedJobCount > 0);
}

/** Hold the native idle-sleep assertion while the Engine owns executable work. */
export function useEngineSleepInhibitor(runtime: EngineRuntimeView | undefined): void {
  const hasActiveWork = engineRuntimeHasActiveWork(runtime);
  const controllerRef = useRef<SleepInhibitorController | null>(null);
  if (controllerRef.current === null) {
    controllerRef.current = new SleepInhibitorController();
  }
  const controller = controllerRef.current;

  useEffect(() => {
    controller.update(hasActiveWork);
  }, [controller, hasActiveWork]);

  useEffect(() => () => controller.dispose(), [controller]);
}
