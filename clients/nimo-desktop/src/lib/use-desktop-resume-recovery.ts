import { useEffect, useRef } from "react";

import { onDesktopResumed } from "./native-bridge";

export const RESUME_RECOVERY_DEDUP_MS = 1_000;

type Recover = () => void;

interface DesktopResumeRecoveryControllerOptions {
  readonly recover: Recover;
  readonly now?: () => number;
  readonly dedupMs?: number;
}

/**
 * Coalesce the overlapping browser and native resume signals that macOS can
 * emit when a Tauri WebView returns from sleep.  Refreshing the Engine views
 * forces a normal React render without reloading the WebView or discarding a
 * reader's unsaved draft.
 */
export class DesktopResumeRecoveryController {
  private readonly recover: Recover;
  private readonly now: () => number;
  private readonly dedupMs: number;
  private backgrounded = false;
  private lastRecoveryAt = Number.NEGATIVE_INFINITY;

  constructor(options: DesktopResumeRecoveryControllerOptions) {
    this.recover = options.recover;
    this.now = options.now ?? Date.now;
    this.dedupMs = options.dedupMs ?? RESUME_RECOVERY_DEDUP_MS;
  }

  markBackgrounded(): void {
    this.backgrounded = true;
  }

  recoverIfBackgrounded(): void {
    if (!this.backgrounded) return;
    this.backgrounded = false;
    this.requestRecovery();
  }

  /** Native lifecycle events can arrive without a DOM visibility transition. */
  recoverFromNativeResume(): void {
    this.backgrounded = false;
    this.requestRecovery();
  }

  private requestRecovery(): void {
    const now = this.now();
    if (now - this.lastRecoveryAt < this.dedupMs) return;
    this.lastRecoveryAt = now;
    this.recover();
  }
}

/**
 * Refresh the Engine connection and data projections after the desktop wakes.
 * DOM events cover browsers and normal WebView visibility changes; the native
 * event covers macOS cases where WebKit does not surface `visibilitychange`.
 */
export function useDesktopResumeRecovery(recover: Recover): void {
  const recoverRef = useRef(recover);
  recoverRef.current = recover;
  const controllerRef = useRef<DesktopResumeRecoveryController | null>(null);
  if (controllerRef.current === null) {
    controllerRef.current = new DesktopResumeRecoveryController({
      recover: () => recoverRef.current(),
    });
  }
  const controller = controllerRef.current;

  useEffect(() => {
    const markBackgrounded = () => controller.markBackgrounded();
    const handleVisibilityChange = () => {
      if (document.visibilityState === "hidden") controller.markBackgrounded();
      else controller.recoverIfBackgrounded();
    };

    document.addEventListener("visibilitychange", handleVisibilityChange);
    window.addEventListener("pagehide", markBackgrounded);
    window.addEventListener("pageshow", handleVisibilityChange);

    let disposed = false;
    let unlisten = () => {};
    void onDesktopResumed(() => controller.recoverFromNativeResume()).then((nextUnlisten) => {
      if (disposed) nextUnlisten();
      else unlisten = nextUnlisten;
    });

    return () => {
      disposed = true;
      document.removeEventListener("visibilitychange", handleVisibilityChange);
      window.removeEventListener("pagehide", markBackgrounded);
      window.removeEventListener("pageshow", handleVisibilityChange);
      unlisten();
    };
  }, [controller]);
}
