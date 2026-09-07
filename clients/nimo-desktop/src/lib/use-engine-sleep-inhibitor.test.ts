import { afterEach, describe, expect, it, vi } from "vitest";

import type { EngineRuntimeView } from "@nimo/engine-contracts";

import {
  engineRuntimeHasActiveWork,
  SleepInhibitorController,
} from "./use-engine-sleep-inhibitor";

const idleRuntime: EngineRuntimeView = {
  contractVersion: "1.0",
  status: "ready",
  bootRevision: "same",
  currentRevision: "same",
  instanceId: "local",
  managedBy: "nimo-t",
  startedAt: "2026-08-30T12:00:00Z",
  activeJobCount: 0,
  queuedJobCount: 0,
  canSubmitTasks: true,
};

afterEach(() => {
  vi.useRealTimers();
});

describe("Engine sleep inhibition", () => {
  it("follows Engine worker and queue counts rather than page-local task state", () => {
    expect(engineRuntimeHasActiveWork(undefined)).toBe(false);
    expect(engineRuntimeHasActiveWork(idleRuntime)).toBe(false);
    expect(engineRuntimeHasActiveWork({ ...idleRuntime, activeJobCount: 1 })).toBe(true);
    expect(engineRuntimeHasActiveWork({ ...idleRuntime, queuedJobCount: 1 })).toBe(true);
  });

  it("holds across a short autorun worker gap and releases after the grace period", () => {
    vi.useFakeTimers();
    const acquire = vi.fn(async () => undefined);
    const release = vi.fn(async () => undefined);
    const controller = new SleepInhibitorController({
      acquire,
      release,
      releaseGraceMs: 1_000,
    });

    controller.update(true);
    controller.update(false);
    vi.advanceTimersByTime(900);
    controller.update(true);
    vi.advanceTimersByTime(200);

    expect(acquire).toHaveBeenCalledTimes(1);
    expect(release).not.toHaveBeenCalled();

    controller.update(false);
    vi.advanceTimersByTime(1_000);
    expect(release).toHaveBeenCalledTimes(1);
  });

  it("releases immediately when the application tree is disposed", () => {
    const acquire = vi.fn(async () => undefined);
    const release = vi.fn(async () => undefined);
    const controller = new SleepInhibitorController({ acquire, release });

    controller.update(true);
    controller.dispose();

    expect(acquire).toHaveBeenCalledTimes(1);
    expect(release).toHaveBeenCalledTimes(1);
  });

  it("clears an orphaned native assertion after a WebView reload", () => {
    const release = vi.fn(async () => undefined);
    const controller = new SleepInhibitorController({ release });

    controller.update(false);

    expect(release).toHaveBeenCalledTimes(1);
  });
});
