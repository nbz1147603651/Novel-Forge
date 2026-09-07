import { describe, expect, it, vi } from "vitest";

import { ConnectivityController, type ConnectivityScheduler } from "./connectivity-controller";

const settings = {
  activeThemeId: "narrative_ember",
  defaultProvider: "openai:gpt-4o-mini",
  storageRootLabel: "本地创作目录",
  mockMode: true,
  modelProfiles: ["alpha", "beta", "gamma", "delta"].map((id) => ({
    id,
    label: id,
    provider: "openai",
    model: id,
    tierLabel: "标准",
    statusLabel: "已配置",
    supportsThinking: false,
    supportsMultiTurn: true,
  })),
  routingGroups: [],
} as const;

class FakeScheduler implements ConnectivityScheduler {
  private nextId = 1;
  readonly animationFrames = new Map<number, FrameRequestCallback>();

  cancelAnimationFrame = (id: number) => { this.animationFrames.delete(id); };
  requestAnimationFrame = (callback: FrameRequestCallback) => {
    const id = this.nextId++;
    this.animationFrames.set(id, callback);
    return id;
  };

  flushAnimationFrame(): void {
    const callbacks = [...this.animationFrames.values()];
    this.animationFrames.clear();
    callbacks.forEach((callback) => callback(16));
  }
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((nextResolve) => { resolve = nextResolve; });
  return { promise, resolve };
}

const successfulProbe = {
  ok: true,
  detail: "真实探活正常",
  latencyMs: 51,
  supportsThinking: true,
  supportsMultiTurn: true,
} as const;

describe("ConnectivityController", () => {
  it("runs real Engine probes with a two-model ceiling and batches their UI updates", async () => {
    const scheduler = new FakeScheduler();
    const alphaProbe = deferred<typeof successfulProbe>();
    const betaProbe = deferred<typeof successfulProbe>();
    const gammaProbe = deferred<typeof successfulProbe>();
    const deltaProbe = deferred<typeof successfulProbe>();
    const probes = [alphaProbe, betaProbe, gammaProbe, deltaProbe];
    const commandClient = {
      testModelProfile: vi.fn(() => probes.shift()!.promise),
    };
    const controller = new ConnectivityController(settings, commandClient, scheduler);
    let notifications = 0;
    controller.subscribe(() => { notifications += 1; });

    controller.start();
    expect(notifications).toBe(1);
    expect(controller.getSnapshot().activeProfileIds).toEqual(["alpha", "beta"]);
    expect(commandClient.testModelProfile).toHaveBeenCalledTimes(2);

    alphaProbe.resolve(successfulProbe);
    betaProbe.resolve(successfulProbe);
    await Promise.resolve();
    await Promise.resolve();
    expect(notifications).toBe(1);
    expect(controller.getSnapshot().activeProfileIds).toHaveLength(2);
    expect(controller.getSnapshot().activeProfileIds).toEqual(expect.arrayContaining(["gamma", "delta"]));
    expect(commandClient.testModelProfile).toHaveBeenCalledTimes(4);
    expect(scheduler.animationFrames.size).toBe(1);

    scheduler.flushAnimationFrame();
    expect(notifications).toBe(2);
    controller.dispose();
  });

  it("drops deleted profiles immediately and ignores their in-flight probe result", async () => {
    const scheduler = new FakeScheduler();
    const betaProbe = deferred<typeof successfulProbe>();
    const commandClient = {
      testModelProfile: vi.fn((command: { id: string }) => command.id === "beta"
        ? betaProbe.promise
        : Promise.resolve(successfulProbe)),
    };
    const controller = new ConnectivityController(settings, commandClient, scheduler);

    controller.start();
    expect(controller.getSnapshot().activeProfileIds).toEqual(["alpha", "beta"]);
    controller.syncProfiles(settings.modelProfiles.filter((profile) => profile.id !== "beta"));

    expect(controller.getSnapshot().entries.map((entry) => entry.id)).toEqual(["alpha", "gamma", "delta"]);
    expect(controller.getSnapshot().activeProfileIds).toEqual([]);

    betaProbe.resolve(successfulProbe);
    await Promise.resolve();
    expect(controller.getSnapshot().entries.some((entry) => entry.id === "beta")).toBe(false);
    controller.dispose();
  });

  it("keeps completed diagnostics and their probe cache through an unchanged Settings sync", async () => {
    const scheduler = new FakeScheduler();
    const commandClient = {
      testModelProfile: vi.fn(() => Promise.resolve(successfulProbe)),
    };
    const controller = new ConnectivityController(settings, commandClient, scheduler);

    controller.start();
    await vi.waitFor(() => {
      expect(controller.getSnapshot().entries.every((entry) => entry.state === "succeeded")).toBe(true);
    });
    const probeCount = commandClient.testModelProfile.mock.calls.length;

    controller.syncProfiles(settings.modelProfiles);
    expect(controller.getSnapshot().entries.every((entry) => entry.state === "succeeded")).toBe(true);
    expect(controller.getSnapshot().entries.map((entry) => entry.latencyMs)).toEqual([51, 51, 51, 51]);

    controller.start();
    expect(commandClient.testModelProfile).toHaveBeenCalledTimes(probeCount);
    controller.dispose();
  });

  it("invalidates only a changed model while retaining other completed diagnostics", async () => {
    const scheduler = new FakeScheduler();
    const commandClient = {
      testModelProfile: vi.fn(() => Promise.resolve(successfulProbe)),
    };
    const controller = new ConnectivityController(settings, commandClient, scheduler);

    controller.start();
    await vi.waitFor(() => {
      expect(controller.getSnapshot().entries.every((entry) => entry.state === "succeeded")).toBe(true);
    });
    const changedProfiles = settings.modelProfiles.map((profile) => profile.id === "beta"
      ? { ...profile, model: "beta-v2" }
      : profile);

    controller.syncProfiles(changedProfiles);
    expect(controller.getSnapshot().entries.find((entry) => entry.id === "beta")).toMatchObject({
      state: "idle",
      latencyMs: null,
      detail: "",
    });
    expect(controller.getSnapshot().entries.filter((entry) => entry.id !== "beta").every((entry) => entry.state === "succeeded")).toBe(true);

    controller.start();
    expect(commandClient.testModelProfile).toHaveBeenCalledTimes(5);
    controller.dispose();
  });

  it("renders an Engine authentication failure instead of manufacturing a success", async () => {
    const scheduler = new FakeScheduler();
    const commandClient = {
      testModelProfile: vi.fn(() => Promise.resolve({
        ok: false,
        detail: "认证失败：请检查 API Key。",
        latencyMs: null,
        supportsThinking: false,
        supportsMultiTurn: false,
      })),
    };
    const controller = new ConnectivityController({ ...settings, modelProfiles: settings.modelProfiles.slice(0, 1) }, commandClient, scheduler);

    controller.start();
    await Promise.resolve();
    await Promise.resolve();

    expect(controller.getSnapshot().entries[0]).toMatchObject({
      id: "alpha",
      state: "failed",
      detail: "认证失败：请检查 API Key。",
      latencyMs: null,
    });
    expect(commandClient.testModelProfile).toHaveBeenCalledWith(expect.objectContaining({
      kind: "test_model_profile",
      id: "alpha",
    }));
    controller.dispose();
  });
});
