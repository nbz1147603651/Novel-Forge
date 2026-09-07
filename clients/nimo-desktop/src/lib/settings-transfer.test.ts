import { describe, expect, it } from "vitest";

import { generatedSettingsFixture } from "./generated-settings.fixture";
import { modelRoutingSessionFromSettings } from "./model-routing-session";
import { buildSettingsTransferPayload, parseSettingsTransferPayload } from "./settings-transfer";

describe("settings transfer", () => {
  it("round-trips routes and runtime parameters instead of exporting models only", () => {
    const session = modelRoutingSessionFromSettings(generatedSettingsFixture);
    const payload = buildSettingsTransferPayload({
      defaultProfileId: session.defaultProfileId,
      profiles: session.profiles,
      routes: session.drafts,
      creativeTemperature: {
        enabled: true,
        scope: "recommended",
        lowerDelta: 0.3,
        upperDelta: 0.1,
        customTaskKeys: [],
      },
      themeId: generatedSettingsFixture.activeThemeId,
      fontPreferences: generatedSettingsFixture.fontPreferences!,
      creationParameters: generatedSettingsFixture.creationParameters!,
      chapterRuntimePolicy: {
        preset: "balanced",
        intentGuardMode: "block",
        factRefreshEnabled: true,
        inspirationEnabled: true,
        inspirationCooldown: 3,
        shortAdaptiveRevisionEnabled: true,
        longSingleFinalVerifyEnabled: true,
      },
    });

    const parsed = parseSettingsTransferPayload(JSON.parse(JSON.stringify(payload)));

    expect(parsed.routes).toEqual(session.drafts);
    expect(parsed.creationParameters).toEqual(generatedSettingsFixture.creationParameters);
    expect(parsed.creativeTemperature?.scope).toBe("recommended");
    expect(parsed.chapterRuntimePolicy?.preset).toBe("balanced");
    expect(payload.schemaVersion).toBe("nimo.settings.v1");
  });

  it("rejects malformed route payloads before they enter the settings session", () => {
    expect(() => parseSettingsTransferPayload({
      profiles: [],
      routes: { draft_chapter: { primaryProfileId: 42 } },
    })).toThrow("路由 draft_chapter 结构无效");
  });

  it("rejects unsupported versions and malformed optional sections", () => {
    expect(() => parseSettingsTransferPayload({
      schemaVersion: "nimo.settings.v99",
      profiles: [],
    })).toThrow("配置文件版本不受支持");
    expect(() => parseSettingsTransferPayload({
      profiles: [],
      creativeTemperature: { enabled: true },
    })).toThrow("创作火候结构无效");
  });
});
