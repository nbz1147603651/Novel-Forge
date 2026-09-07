import { describe, expect, it } from "vitest";

import {
  cancelConnectivityChecks,
  connectivityDetail,
  createConnectivitySession,
  resolveActiveConnectivityCheck,
  resolveConnectivityCheck,
  startConnectivityChecks,
  synchronizeConnectivitySession,
} from "./connectivity-session";

const settings = {
  activeThemeId: "narrative_ember",
  defaultProvider: "openai:gpt-4o-mini",
  storageRootLabel: "本地创作目录",
  mockMode: true,
  modelProfiles: [
    { id: "openai:gpt-4o-mini", label: "OpenAI Mini", provider: "openai", model: "gpt-4o-mini", tierLabel: "标准", statusLabel: "已配置", supportsThinking: false, supportsMultiTurn: true },
    { id: "deepseek:chat", label: "DeepSeek Chat", provider: "deepseek", model: "chat", tierLabel: "经济", statusLabel: "未启用", supportsThinking: true, supportsMultiTurn: true },
    { id: "local:draft", label: "本地草案", provider: "local", model: "draft", tierLabel: "标准", statusLabel: "本地草案", supportsThinking: true, supportsMultiTurn: false },
  ],
  routingGroups: [],
} as const;

describe("connectivity session", () => {
  it("keeps unconfigured profiles out of a local run", () => {
    const session = createConnectivitySession(settings);
    expect(session.entries.map((entry) => entry.state)).toEqual(["idle", "unavailable", "idle"]);
  });

  it("moves configured profiles through the native-shaped bounded queue", () => {
    const started = startConnectivityChecks(createConnectivitySession(settings));
    const firstDone = resolveActiveConnectivityCheck(started, {
      ok: true,
      detail: "OpenAI 正常",
      latencyMs: 42,
      supportsThinking: false,
      supportsMultiTurn: true,
    });
    const completed = resolveActiveConnectivityCheck(firstDone, {
      ok: true,
      detail: "本地模型正常",
      latencyMs: 58,
      supportsThinking: true,
      supportsMultiTurn: false,
    });

    expect(started.activeProfileIds).toEqual(["openai:gpt-4o-mini", "local:draft"]);
    expect(firstDone.activeProfileIds).toEqual(["local:draft"]);
    expect(completed.activeProfileIds).toEqual([]);
    expect(completed.entries.map((entry) => entry.state)).toEqual(["succeeded", "unavailable", "succeeded"]);
    expect(connectivityDetail(completed.entries[0]!)).toBe("连通 42ms");
  });

  it("keeps at most two provider probes active and backfills a free slot", () => {
    const manyProfiles = {
      ...settings,
      modelProfiles: ["alpha", "beta", "gamma", "delta"].map((id) => ({
        ...settings.modelProfiles[0],
        id,
        label: id,
      })),
    };
    const started = startConnectivityChecks(createConnectivitySession(manyProfiles));
    const advanced = resolveConnectivityCheck(started, "alpha", {
      ok: true,
      detail: "连接正常",
      latencyMs: 12,
      supportsThinking: false,
      supportsMultiTurn: true,
    });

    expect(started.activeProfileIds).toEqual(["alpha", "beta"]);
    expect(advanced.activeProfileIds).toEqual(["beta", "gamma"]);
    expect(advanced.entries.find((entry) => entry.id === "alpha")?.state).toBe("succeeded");
    expect(advanced.entries.find((entry) => entry.id === "beta")).toBe(started.entries.find((entry) => entry.id === "beta"));
    expect(advanced.entries.find((entry) => entry.id === "delta")).toBe(started.entries.find((entry) => entry.id === "delta"));
  });

  it("supports a local failure and cancellation without retaining queued work", () => {
    const started = startConnectivityChecks(createConnectivitySession(settings));
    const failed = resolveActiveConnectivityCheck(started, {
      ok: false,
      detail: "认证失败",
      latencyMs: null,
      supportsThinking: false,
      supportsMultiTurn: false,
    });
    const cancelled = cancelConnectivityChecks(failed);

    expect(failed.entries[0]!.state).toBe("failed");
    expect(cancelled.activeProfileIds).toEqual([]);
    expect(cancelled.entries[2]!.state).toBe("idle");
  });

  it("retains completed diagnostics when Settings refreshes unchanged profiles", () => {
    const started = startConnectivityChecks(createConnectivitySession(settings));
    const completed = resolveActiveConnectivityCheck(resolveActiveConnectivityCheck(started, {
      ok: true,
      detail: "OpenAI 正常",
      latencyMs: 42,
      supportsThinking: false,
      supportsMultiTurn: true,
    }), {
      ok: true,
      detail: "本地模型正常",
      latencyMs: 58,
      supportsThinking: true,
      supportsMultiTurn: false,
    });

    const synchronized = synchronizeConnectivitySession(completed, settings.modelProfiles, new Set());

    expect(synchronized.runId).toBe(completed.runId);
    expect(synchronized.entries.map((entry) => entry.state)).toEqual(["succeeded", "unavailable", "succeeded"]);
    expect(connectivityDetail(synchronized.entries[0]!)).toBe("连通 42ms");
  });

  it("invalidates only changed model profiles and retains the remaining results", () => {
    const started = startConnectivityChecks(createConnectivitySession(settings));
    const completed = resolveActiveConnectivityCheck(resolveActiveConnectivityCheck(started, {
      ok: true,
      detail: "OpenAI 正常",
      latencyMs: 42,
      supportsThinking: false,
      supportsMultiTurn: true,
    }), {
      ok: true,
      detail: "本地模型正常",
      latencyMs: 58,
      supportsThinking: true,
      supportsMultiTurn: false,
    });
    const updatedProfiles = settings.modelProfiles.map((profile) => profile.id === "openai:gpt-4o-mini"
      ? { ...profile, model: "gpt-4.1-mini" }
      : profile);

    const synchronized = synchronizeConnectivitySession(
      completed,
      updatedProfiles,
      new Set(["openai:gpt-4o-mini"]),
    );

    expect(synchronized.runId).toBe(completed.runId + 1);
    expect(synchronized.entries.find((entry) => entry.id === "openai:gpt-4o-mini")).toMatchObject({
      state: "idle",
      latencyMs: null,
      detail: "",
    });
    expect(connectivityDetail(synchronized.entries.find((entry) => entry.id === "local:draft")!)).toBe("连通 58ms");
  });
});
