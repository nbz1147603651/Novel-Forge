import { describe, expect, it } from "vitest";

import { applyGroupRouteDraft, applySessionGroupRouteDraft, clearSessionGroupRouteOverrides, effectivePrimaryProfileId, modelRoutingSessionFromSettings, removeLocalModelProfile, reorderFallbackRoute, replaceProfileInRouteDrafts, sanitizeRouteDraft, saveLocalModelProfile, setSessionDefaultProfile } from "./model-routing-session";

const profiles = [
  { id: "openai:gpt-4o", label: "OpenAI · GPT-4o", provider: "openai", model: "gpt-4o", tierLabel: "高质量", statusLabel: "已配置", supportsThinking: true, supportsMultiTurn: true },
  { id: "deepseek:chat", label: "DeepSeek · Chat", provider: "deepseek", model: "chat", tierLabel: "标准", statusLabel: "已配置", supportsThinking: false, supportsMultiTurn: true },
] as const;

const drafts = {
  draft: { primaryProfileId: "openai:gpt-4o", fallbackRoutes: [{ profileId: "deepseek:chat", thinkingEnabled: false, multiTurnEnabled: true }], thinkingEnabled: true, multiTurnEnabled: true, temperature: 0.8 },
  check: { primaryProfileId: "deepseek:chat", fallbackRoutes: [{ profileId: "openai:gpt-4o", thinkingEnabled: true, multiTurnEnabled: true }], thinkingEnabled: false, multiTurnEnabled: true, temperature: null },
} as const;

describe("model routing local session", () => {
  it("adds a credential-free profile with a deterministic id", () => {
    const result = saveLocalModelProfile(profiles, { label: "", provider: "Anthropic", model: "Claude-3.7", tierLabel: "高质量", supportsThinking: true, supportsMultiTurn: true });
    expect(result.profile).toMatchObject({ id: "anthropic:claude-3.7", statusLabel: "本地草案" });
    expect(result.profiles).toHaveLength(3);
  });

  it("moves primary and fallback references when an edited id changes", () => {
    const profile = { ...profiles[0], id: "openai:gpt-4.1", supportsThinking: false };
    const updatedProfiles = [profile, profiles[1]];
    expect(replaceProfileInRouteDrafts(drafts, "openai:gpt-4o", profile, updatedProfiles).draft).toMatchObject({ primaryProfileId: "openai:gpt-4.1", thinkingEnabled: false });
  });

  it("removes the deleted profile from every route and selects a viable replacement", () => {
    const result = removeLocalModelProfile(profiles, drafts, "openai:gpt-4o");
    expect(result.profiles.map((profile) => profile.id)).toEqual(["deepseek:chat"]);
    expect(result.drafts.draft).toMatchObject({ primaryProfileId: "deepseek:chat", fallbackRoutes: [], thinkingEnabled: false });
  });

  it("applies a single compatible draft to all routes in a group", () => {
    const result = applyGroupRouteDraft(drafts, ["draft", "check"], { primaryProfileId: "deepseek:chat", fallbackRoutes: [], thinkingEnabled: true, multiTurnEnabled: true, temperature: null }, profiles, { draft: true, check: false });
    expect(result.draft).toMatchObject({ primaryProfileId: "deepseek:chat", thinkingEnabled: false, multiTurnEnabled: true, temperature: 0.8 });
    expect(result.check).toMatchObject({ primaryProfileId: "deepseek:chat", thinkingEnabled: false, multiTurnEnabled: false, temperature: null });
  });

  it("applies a temperature-only group edit without changing routing", () => {
    const result = applyGroupRouteDraft(drafts, ["draft", "check"], {
      primaryProfileId: "",
      fallbackRoutes: [],
      thinkingEnabled: false,
      multiTurnEnabled: false,
      temperature: 1.15,
    }, profiles, { draft: true, check: false });

    expect(result.draft).toMatchObject({
      primaryProfileId: "openai:gpt-4o",
      fallbackRoutes: [{ profileId: "deepseek:chat", multiTurnEnabled: true }],
      thinkingEnabled: true,
      multiTurnEnabled: true,
      temperature: 1.15,
    });
    expect(result.check).toMatchObject({
      primaryProfileId: "deepseek:chat",
      temperature: null,
    });
  });

  it("keeps all visible candidates ordered while only each model's own capabilities enable fallbacks", () => {
    const unavailable = { id: "anthropic:sonnet", label: "Sonnet", provider: "anthropic", model: "sonnet", tierLabel: "高质量", statusLabel: "待补 API Key", supportsThinking: true, supportsMultiTurn: true };
    const result = sanitizeRouteDraft({
      primaryProfileId: "openai:gpt-4o",
      fallbackRoutes: [
        { profileId: "anthropic:sonnet", thinkingEnabled: true, multiTurnEnabled: true },
        { profileId: "deepseek:chat", thinkingEnabled: true, multiTurnEnabled: true },
      ],
      thinkingEnabled: true,
      multiTurnEnabled: true,
      temperature: 3,
    }, [...profiles, unavailable], true);
    expect(result.fallbackRoutes.map((route) => route.profileId)).toEqual(["anthropic:sonnet", "deepseek:chat"]);
    expect(result.fallbackRoutes[0]).toMatchObject({ thinkingEnabled: false, multiTurnEnabled: false });
    expect(result.fallbackRoutes[1]).toMatchObject({ thinkingEnabled: false, multiTurnEnabled: true });
    expect(result.temperature).toBe(2);
  });

  it("excludes embedding profiles from text-generation primaries and fallbacks", () => {
    const embedding = {
      id: "tongyi:text-embedding-v4",
      label: "Tongyi Embedding",
      provider: "tongyi",
      model: "text-embedding-v4",
      tierLabel: "标准",
      statusLabel: "已配置",
      supportsThinking: false,
      supportsMultiTurn: false,
      isEmbedding: true,
    } as const;
    const result = sanitizeRouteDraft({
      primaryProfileId: embedding.id,
      fallbackRoutes: [
        { profileId: embedding.id, thinkingEnabled: false, multiTurnEnabled: false },
        { profileId: "deepseek:chat", thinkingEnabled: false, multiTurnEnabled: true },
      ],
      thinkingEnabled: false,
      multiTurnEnabled: false,
      temperature: null,
    }, [...profiles, embedding]);

    expect(result.primaryProfileId).toBe("");
    expect(result.fallbackRoutes.map((route) => route.profileId)).toEqual([
      "deepseek:chat",
      "openai:gpt-4o",
    ]);
  });

  it("reorders a fallback without losing its per-model capability choices", () => {
    const result = reorderFallbackRoute(drafts.draft, "deepseek:chat", "up", profiles);
    expect(result.fallbackRoutes[0]).toMatchObject({ profileId: "deepseek:chat", multiTurnEnabled: true });
  });

  it("uses the default profile only when a task intentionally leaves its primary route empty", () => {
    const inherited = { ...drafts.draft, primaryProfileId: "" };
    expect(effectivePrimaryProfileId(inherited, "deepseek:chat")).toBe("deepseek:chat");
    expect(effectivePrimaryProfileId(drafts.draft, "deepseek:chat")).toBe("openai:gpt-4o");
  });

  it("keeps one default-model and bulk-routing projection for every settings workbench mount", () => {
    const session = modelRoutingSessionFromSettings({
      activeThemeId: "narrative_ember",
      defaultProvider: "openai:gpt-4o",
      defaultProfileId: "openai:gpt-4o",
      storageRootLabel: "fixture",
      mockMode: true,
      modelProfiles: profiles,
      routingGroups: [{ id: "short", label: "短篇", description: "", subgroups: [], routes: [
        { id: "draft", taskKey: "DRAFT", label: "初稿", hint: "", ...drafts.draft, temperatureKind: "base", supportsMultiTurn: true },
        { id: "check", taskKey: "CHECK", label: "校验", hint: "", ...drafts.check, temperatureKind: null, supportsMultiTurn: false },
      ] }],
    });
    const defaulted = setSessionDefaultProfile(session, "deepseek:chat");
    const bulk = applySessionGroupRouteDraft(defaulted, ["draft", "check"], {
      primaryProfileId: "openai:gpt-4o",
      fallbackRoutes: [{ profileId: "deepseek:chat", thinkingEnabled: false, multiTurnEnabled: true }],
      thinkingEnabled: true,
      multiTurnEnabled: true,
      temperature: null,
    }, { draft: true, check: false }, "group:short");
    expect(bulk.defaultProfileId).toBe("deepseek:chat");
    expect(bulk.bulkDrafts["group:short"]).toMatchObject({ primaryProfileId: "openai:gpt-4o", fallbackRoutes: [{ profileId: "deepseek:chat" }] });
    expect(bulk.drafts.draft).toMatchObject({ primaryProfileId: "openai:gpt-4o", temperature: 0.8, multiTurnEnabled: true });
    expect(bulk.drafts.check).toMatchObject({ primaryProfileId: "openai:gpt-4o", temperature: null, multiTurnEnabled: false });
  });

  it("clears a route group so every selected task inherits the default model", () => {
    const cleared = clearSessionGroupRouteOverrides({
      defaultProfileId: "deepseek:chat",
      profiles,
      connectionDrafts: {},
      drafts,
      bulkDrafts: {},
    }, ["draft", "check"], { draft: true, check: false });

    expect(cleared.drafts.draft).toMatchObject({
      primaryProfileId: "",
      thinkingEnabled: false,
      multiTurnEnabled: false,
      temperature: 0.8,
    });
    expect(cleared.drafts.check).toMatchObject({ primaryProfileId: "" });
  });

  it("moves the default profile to the same viable fallback when its profile is deleted", () => {
    const result = removeLocalModelProfile(profiles, drafts, "openai:gpt-4o", { draft: true, check: false }, "openai:gpt-4o");
    expect(result.defaultProfileId).toBe("deepseek:chat");
  });

  it("never selects an embedding profile as the default generation model", () => {
    const embedding = {
      ...profiles[0],
      id: "tongyi:text-embedding-v4",
      provider: "tongyi",
      model: "text-embedding-v4",
      isEmbedding: true,
    };
    const session = modelRoutingSessionFromSettings({
      activeThemeId: "narrative_ember",
      defaultProvider: embedding.id,
      defaultProfileId: embedding.id,
      storageRootLabel: "fixture",
      mockMode: true,
      modelProfiles: [embedding, ...profiles],
      routingGroups: [],
    });
    expect(session.defaultProfileId).toBe("openai:gpt-4o");
  });

  it("marks a new embedding-model draft before it can enter a text route", () => {
    const result = saveLocalModelProfile(profiles, {
      label: "",
      provider: "tongyi",
      model: "text-embedding-v4",
      tierLabel: "标准",
      supportsThinking: false,
      supportsMultiTurn: false,
    });
    expect(result.profile.isEmbedding).toBe(true);
  });
});
