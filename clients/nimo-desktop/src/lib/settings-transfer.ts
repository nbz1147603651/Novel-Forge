import type { ChapterRuntimePolicyCommand, FontPreferencesView, ModelProfileView } from "@nimo/engine-contracts";

import type { CreativeTemperatureDraft } from "../components/CreativeTemperatureSection";
import type { RouteDraft } from "./model-routing-session";

export interface SettingsTransferPayload {
  readonly schemaVersion: "nimo.settings.v1";
  readonly defaultProfileId: string;
  readonly profiles: readonly ModelProfileView[];
  readonly routes: Readonly<Record<string, RouteDraft>>;
  readonly creativeTemperature: CreativeTemperatureDraft;
  readonly themeId: string;
  readonly fontPreferences: FontPreferencesView;
  readonly creationParameters: Readonly<Record<string, string>>;
  readonly chapterRuntimePolicy?: ChapterRuntimePolicyCommand;
}

export interface ParsedSettingsTransfer {
  defaultProfileId?: string;
  profiles?: readonly ModelProfileView[];
  routes?: Readonly<Record<string, RouteDraft>>;
  creativeTemperature?: CreativeTemperatureDraft;
  themeId?: string;
  fontPreferences?: FontPreferencesView;
  creationParameters?: Readonly<Record<string, string>>;
  chapterRuntimePolicy?: ChapterRuntimePolicyCommand;
}

function recordValue(value: unknown): Record<string, unknown> | null {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? value as Record<string, unknown>
    : null;
}

function isProfile(value: unknown): value is ModelProfileView {
  const profile = recordValue(value);
  return profile !== null
    && typeof profile.id === "string"
    && typeof profile.label === "string"
    && typeof profile.provider === "string"
    && typeof profile.model === "string"
    && typeof profile.tierLabel === "string"
    && typeof profile.statusLabel === "string"
    && typeof profile.supportsThinking === "boolean"
    && typeof profile.supportsMultiTurn === "boolean";
}

function routeDraft(value: unknown): RouteDraft | null {
  const route = recordValue(value);
  if (route === null
    || typeof route.primaryProfileId !== "string"
    || !Array.isArray(route.fallbackRoutes)
    || typeof route.thinkingEnabled !== "boolean"
    || typeof route.multiTurnEnabled !== "boolean"
    || !(route.temperature === null || typeof route.temperature === "number")) return null;
  const fallbackRoutes = route.fallbackRoutes.flatMap((candidate) => {
    const fallback = recordValue(candidate);
    return fallback !== null
      && typeof fallback.profileId === "string"
      && typeof fallback.thinkingEnabled === "boolean"
      && typeof fallback.multiTurnEnabled === "boolean"
      ? [{
        profileId: fallback.profileId,
        thinkingEnabled: fallback.thinkingEnabled,
        multiTurnEnabled: fallback.multiTurnEnabled,
      }]
      : [];
  });
  return {
    primaryProfileId: route.primaryProfileId,
    fallbackRoutes,
    thinkingEnabled: route.thinkingEnabled,
    multiTurnEnabled: route.multiTurnEnabled,
    temperature: route.temperature,
  };
}

function creativeTemperature(value: unknown): CreativeTemperatureDraft | null {
  const draft = recordValue(value);
  if (draft === null
    || typeof draft.enabled !== "boolean"
    || !["recommended", "chapter_core", "init_and_chapter", "custom"].includes(String(draft.scope))
    || typeof draft.lowerDelta !== "number"
    || typeof draft.upperDelta !== "number"
    || !Array.isArray(draft.customTaskKeys)
    || !draft.customTaskKeys.every((item) => typeof item === "string")) return null;
  return {
    enabled: draft.enabled,
    scope: draft.scope as CreativeTemperatureDraft["scope"],
    lowerDelta: draft.lowerDelta,
    upperDelta: draft.upperDelta,
    customTaskKeys: draft.customTaskKeys as string[],
  };
}

function fontPreferences(value: unknown): FontPreferencesView | null {
  const draft = recordValue(value);
  return draft !== null
    && typeof draft.uiFamily === "string"
    && typeof draft.readingFamily === "string"
    && typeof draft.scale === "number"
    ? draft as unknown as FontPreferencesView
    : null;
}

function chapterRuntimePolicy(value: unknown): ChapterRuntimePolicyCommand | null {
  const draft = recordValue(value);
  if (draft === null
    || !["compat", "safe", "balanced", "enhanced", "custom"].includes(String(draft.preset))
    || !["off", "warn", "block"].includes(String(draft.intentGuardMode))
    || typeof draft.factRefreshEnabled !== "boolean"
    || typeof draft.inspirationEnabled !== "boolean"
    || typeof draft.inspirationCooldown !== "number"
    || draft.inspirationCooldown < 1
    || draft.inspirationCooldown > 20
    || typeof draft.shortAdaptiveRevisionEnabled !== "boolean"
    || typeof draft.longSingleFinalVerifyEnabled !== "boolean") return null;
  return draft as unknown as ChapterRuntimePolicyCommand;
}

export function buildSettingsTransferPayload(
  payload: Omit<SettingsTransferPayload, "schemaVersion">,
): SettingsTransferPayload {
  return { schemaVersion: "nimo.settings.v1", ...payload };
}

export function parseSettingsTransferPayload(value: unknown): ParsedSettingsTransfer {
  const payload = recordValue(value);
  if (payload === null) throw new Error("配置文件必须是 JSON 对象。");
  if (payload.schemaVersion !== undefined && payload.schemaVersion !== "nimo.settings.v1") {
    throw new Error("配置文件版本不受支持。");
  }

  const result: ParsedSettingsTransfer = {};
  if (payload.defaultProfileId !== undefined) {
    if (typeof payload.defaultProfileId !== "string") throw new Error("默认模型 ID 无效。");
    result.defaultProfileId = payload.defaultProfileId;
  }
  if (payload.profiles !== undefined) {
    if (!Array.isArray(payload.profiles)) throw new Error("模型档案必须是数组。");
    if (!payload.profiles.every(isProfile)) throw new Error("模型档案结构不完整。");
    result.profiles = payload.profiles;
  }
  const rawRoutes = recordValue(payload.routes);
  if (payload.routes !== undefined && rawRoutes === null) throw new Error("任务路由必须是对象。");
  if (rawRoutes !== null) {
    const routes: Record<string, RouteDraft> = {};
    for (const [routeId, value] of Object.entries(rawRoutes)) {
      const parsed = routeDraft(value);
      if (parsed === null) throw new Error(`路由 ${routeId} 结构无效。`);
      routes[routeId] = parsed;
    }
    result.routes = routes;
  }
  const parsedCreative = creativeTemperature(payload.creativeTemperature);
  if (payload.creativeTemperature !== undefined && parsedCreative === null) {
    throw new Error("创作火候结构无效。");
  }
  if (parsedCreative !== null) result.creativeTemperature = parsedCreative;
  if (payload.themeId !== undefined) {
    if (typeof payload.themeId !== "string") throw new Error("主题 ID 无效。");
    result.themeId = payload.themeId;
  }
  const parsedFont = fontPreferences(payload.fontPreferences);
  if (payload.fontPreferences !== undefined && parsedFont === null) {
    throw new Error("字体设置结构无效。");
  }
  if (parsedFont !== null) result.fontPreferences = parsedFont;
  const rawCreationParameters = recordValue(payload.creationParameters);
  if (payload.creationParameters !== undefined && rawCreationParameters === null) {
    throw new Error("创作参数必须是对象。");
  }
  if (rawCreationParameters !== null) {
    if (!Object.values(rawCreationParameters).every((item) => typeof item === "string")) {
      throw new Error("创作参数必须是字符串键值对。");
    }
    result.creationParameters = rawCreationParameters as Readonly<Record<string, string>>;
  }
  const parsedChapterPolicy = chapterRuntimePolicy(payload.chapterRuntimePolicy);
  if (payload.chapterRuntimePolicy !== undefined && parsedChapterPolicy === null) {
    throw new Error("章节运行策略结构无效。");
  }
  if (parsedChapterPolicy !== null) result.chapterRuntimePolicy = parsedChapterPolicy;
  if (Object.keys(result).length === 0) throw new Error("配置文件中没有可导入的设置。");
  return result;
}
