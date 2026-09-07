import { describe, expect, it, vi } from "vitest";

import { DesktopResumeRecoveryController } from "./use-desktop-resume-recovery";

describe("desktop resume recovery", () => {
  it("refreshes only after a browser visibility transition", () => {
    const recover = vi.fn();
    const controller = new DesktopResumeRecoveryController({ recover, now: () => 1_000 });

    controller.recoverIfBackgrounded();
    controller.markBackgrounded();
    controller.recoverIfBackgrounded();

    expect(recover).toHaveBeenCalledTimes(1);
  });

  it("refreshes for a native resume even when WebKit omitted visibilitychange", () => {
    const recover = vi.fn();
    const controller = new DesktopResumeRecoveryController({ recover, now: () => 1_000 });

    controller.recoverFromNativeResume();

    expect(recover).toHaveBeenCalledTimes(1);
  });

  it("coalesces matching native and DOM resume signals", () => {
    const recover = vi.fn();
    let now = 1_000;
    const controller = new DesktopResumeRecoveryController({ recover, now: () => now, dedupMs: 500 });

    controller.markBackgrounded();
    controller.recoverIfBackgrounded();
    controller.recoverFromNativeResume();
    now += 500;
    controller.recoverFromNativeResume();

    expect(recover).toHaveBeenCalledTimes(2);
  });
});
