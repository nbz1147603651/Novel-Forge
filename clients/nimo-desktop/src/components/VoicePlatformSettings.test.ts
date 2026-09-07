import { describe, expect, it } from "vitest";
import type { VoiceProviderView } from "@nimo/engine-contracts";

import {
  commercialVoiceSettingsPatch,
  createVoicePlatformSettingsDraft,
  platformModelValues,
  voiceActiveModelPatch,
  voiceCommercialReadinessChecks,
  voicePlatformSettingsEqual,
  voicePlatformSettingsToCreationParameters,
  voiceProviderSwitchPatch,
} from "./VoicePlatformSettings";
import { voiceTextRoutesToCommands, withoutVoiceTextRoutes } from "../lib/voice-text-routing";
import { generatedVoiceProviderCatalog } from "../lib/generated-settings.fixture";

function voiceStudio(
  configuredModelLabel: string,
  providerLabel = "minimax",
) {
  return {
    configuredModelLabel,
    providerCatalog: generatedVoiceProviderCatalog,
    providerLabel,
  };
}

const engineProviderCatalog: readonly VoiceProviderView[] = [{
  id: "acme",
  label: "Acme Voice",
  aliases: ["acme-tts"],
  officialDocsUrl: "https://example.invalid/acme",
  defaultModel: "acme-pro",
  activeModelParameterId: "tts-acme-model",
  isLocal: false,
  adapterStatus: "production",
  highlights: ["Engine 目录驱动"],
  models: [{
    id: "acme-pro",
    label: "Acme Pro",
    purposes: ["formal", "preview"],
    features: ["speed"],
    voiceClone: false,
    voiceDesign: false,
    systemVoiceCatalog: true,
    localReferenceAudio: false,
    adapterSupported: true,
    recommended: true,
    maxInputChars: 5000,
    notes: [],
  }],
  settings: [
    {
      parameterId: "tts-acme-model",
      label: "Acme 模型",
      description: "正式合成模型。",
      kind: "select",
      defaultValue: "acme-pro",
      options: [],
      modelPurpose: "formal",
      minimum: null,
      maximum: null,
      step: null,
      advanced: false,
      experimental: false,
      visibleModelIds: [],
      visibilityParameterId: "",
      visibilityValues: [],
    },
    {
      parameterId: "tts-acme-api-key",
      label: "Acme API Key",
      description: "服务凭据。",
      kind: "secret",
      defaultValue: "",
      options: [],
      modelPurpose: null,
      minimum: null,
      maximum: null,
      step: null,
      advanced: false,
      experimental: false,
      visibleModelIds: [],
      visibilityParameterId: "",
      visibilityValues: [],
    },
  ],
}];

describe("voice platform settings draft", () => {
  it("derives the source-facing platform model once and detects a real local edit", () => {
    const initial = createVoicePlatformSettingsDraft(voiceStudio("MiniMax · speech-02-hd"));
    const revised = { ...initial, concurrency: "3" as const };

    expect(initial.model).toBe("speech-02-hd");
    expect(voicePlatformSettingsEqual(initial, { ...initial })).toBe(true);
    expect(voicePlatformSettingsEqual(initial, revised)).toBe(false);
  });

  it("hydrates and saves every PySide audio-planning parameter without returning a sidecar token", () => {
    const draft = createVoicePlatformSettingsDraft(
      voiceStudio("MiniMax · speech-02-hd"),
      {
        "audio-quality-preset": "master",
        "audio-location-policy": "local_only",
        "audio-plugin-overrides": '{"alignment":"qwen3-forced-aligner"}',
        "audio-qwen3-asr-base-url": "http://127.0.0.1:9012/v1",
        "tts-audio-quality-tier": "commercial",
        "tts-subtitle-word-level": "true",
        "tts-minimax-force-cbr": "false",
        "tts-master-quality-gate-blocking": "false",
      },
    );
    const payload = voicePlatformSettingsToCreationParameters({
      ...draft,
      audioQwen3AsrApiKey: "new-token",
    });

    expect(draft.audioQualityPreset).toBe("master");
    expect(draft.ttsAudioQualityTier).toBe("commercial");
    expect(draft.ttsSubtitleWordLevel).toBe(true);
    expect(draft.providerParameters["tts-minimax-force-cbr"]).toBe("false");
    expect(draft.audioLocationPolicy).toBe("local_only");
    expect(draft.audioQwen3AsrApiKey).toBe("");
    expect(payload["audio-plugin-overrides"]).toContain("qwen3-forced-aligner");
    expect(payload["audio-qwen3-asr-api-key"]).toBe("new-token");
    expect(payload["tts-master-quality-gate-blocking"]).toBe("false");
    expect(payload["tts-audio-quality-tier"]).toBe("commercial");
    expect(payload["tts-subtitle-word-level"]).toBe("true");
    expect(payload["tts-minimax-force-cbr"]).toBe("false");
    expect(Object.keys(payload)).toEqual(expect.arrayContaining([
      "audio-accelerator-preference",
      "audio-dual-alignment-validation",
      "audio-location-policy",
      "audio-memory-budget",
      "audio-mfa-command",
      "audio-plugin-manifest-dirs",
      "audio-plugin-overrides",
      "audio-quality-preset",
      "audio-qwen3-asr-api-key",
      "audio-qwen3-asr-base-url",
      "audio-sherpa-base-url",
      "audio-whisperx-base-url",
      "local-model-resource-budget",
      "local-model-resource-wait-timeout-s",
      "tts-alignment-repair-rounds",
      "tts-audio-quality-tier",
      "tts-master-quality-gate-blocking",
      "tts-max-text-error-rate",
      "tts-minimax-force-cbr",
      "tts-subtitle-word-level",
    ]));
  });

  it("shares one curated model catalog per curated provider and free-form fallback", () => {
    expect(platformModelValues("MiniMax", generatedVoiceProviderCatalog).some((option) => option.value === "speech-2.8-hd")).toBe(true);
    expect(platformModelValues("dashscope", generatedVoiceProviderCatalog).some((option) => option.value === "qwen-audio-3.0-tts-plus")).toBe(true);
    expect(platformModelValues("阿里百炼 / DashScope", generatedVoiceProviderCatalog).some((option) => option.value === "qwen3-tts-instruct-flash")).toBe(true);
    // Free-form platforms keep an empty catalog so the UI renders a text input.
    expect(platformModelValues("local", generatedVoiceProviderCatalog)).toEqual([]);
    expect(platformModelValues("volcengine_ark", generatedVoiceProviderCatalog)).toHaveLength(1);
  });

  it("hydrates and saves Engine-owned provider fields without exposing persisted secrets", () => {
    const draft = createVoicePlatformSettingsDraft(
      {
        configuredModelLabel: "Acme Pro",
        providerCatalog: engineProviderCatalog,
        providerLabel: "Acme Voice",
      },
      {
        "tts-acme-api-key": "must-not-round-trip",
        "tts-acme-model": "acme-pro",
      },
    );

    expect(draft.provider).toBe("acme");
    expect(draft.model).toBe("acme-pro");
    expect(draft.providerParameters["tts-acme-api-key"]).toBe("");
    expect(platformModelValues("acme", engineProviderCatalog)).toEqual([
      { value: "acme-pro", label: "Acme Pro" },
    ]);

    const payload = voicePlatformSettingsToCreationParameters({
      ...draft,
      providerParameters: {
        ...draft.providerParameters,
        "tts-acme-api-key": "replacement-secret",
      },
    });
    expect(payload["tts-acme-model"]).toBe("acme-pro");
    expect(payload["tts-acme-api-key"]).toBe("replacement-secret");
  });

  it("keeps the active and Bailian formal model synchronized across hydration and provider switches", () => {
    const draft = createVoicePlatformSettingsDraft(
      voiceStudio("MiniMax · speech-2.8-hd", "dashscope"),
      {
        "tts-model": "speech-2.8-hd",
        "tts-dashscope-model": "qwen3-tts-instruct-flash",
      },
    );

    expect(draft.model).toBe("qwen3-tts-instruct-flash");
    expect(draft.providerParameters["tts-dashscope-model"]).toBe("qwen3-tts-instruct-flash");
    expect(voiceActiveModelPatch(draft, "qwen-audio-3.0-tts-plus")).toEqual({
      model: "qwen-audio-3.0-tts-plus",
      providerParameters: {
        ...draft.providerParameters,
        "tts-dashscope-model": "qwen-audio-3.0-tts-plus",
      },
    });
    expect(voiceProviderSwitchPatch(draft, "minimax")).toMatchObject({
      provider: "minimax",
      model: "speech-2.8-hd",
    });
  });

  it("remembers each provider model independently and exposes catalog-only special fields", () => {
    const minimax = createVoicePlatformSettingsDraft(
      voiceStudio("speech-2.6-hd"),
      {
        "tts-model": "speech-2.6-hd",
        "tts-dashscope-model": "qwen-audio-3.0-tts-plus",
        "tts-minimax-english-normalization": "true",
        "tts-tencent-emotion-intensity": "150",
      },
    );
    const dashscope = {
      ...minimax,
      ...voiceProviderSwitchPatch(minimax, "dashscope"),
    };
    const payload = voicePlatformSettingsToCreationParameters(dashscope);

    expect(dashscope.model).toBe("qwen-audio-3.0-tts-plus");
    expect(dashscope.providerParameters["tts-model"]).toBe("speech-2.6-hd");
    expect(dashscope.providerParameters["tts-minimax-english-normalization"]).toBe("true");
    expect(dashscope.providerParameters["tts-tencent-emotion-intensity"]).toBe("150");
    expect(payload["tts-model"]).toBe("speech-2.6-hd");
    expect(payload["tts-dashscope-model"]).toBe("qwen-audio-3.0-tts-plus");
  });

  it("projects and applies every independent commercial-delivery baseline control", () => {
    const draft = createVoicePlatformSettingsDraft(voiceStudio("speech-2.8-hd"));
    const initial = voiceCommercialReadinessChecks({
      ...draft,
      soundAutoApprove: true,
      ttsAlignmentRepairRounds: "0",
      ttsMasterQualityGateBlocking: false,
    });

    expect(initial.filter((check) => check.ready)).toHaveLength(0);
    const commercial = { ...draft, ...commercialVoiceSettingsPatch({
      ...draft,
      soundAutoApprove: true,
      ttsAlignmentRepairRounds: "0",
      ttsMasterQualityGateBlocking: false,
    }) };
    expect(voiceCommercialReadinessChecks(commercial).every((check) => check.ready)).toBe(true);
    expect(commercial.ttsAudioQualityTier).toBe("commercial");
    expect(commercial.audioQualityPreset).toBe("master");
    expect(commercial.soundAutoApprove).toBe(false);
  });

  it("round-trips the MiniMax native feature flags (continuous sound, async timeout, AIGC watermark)", () => {
    const draft = createVoicePlatformSettingsDraft(
      voiceStudio("MiniMax · speech-2.8-hd", "MiniMax"),
      {
        "tts-minimax-continuous-sound": "false",
        "tts-minimax-async-timeout-s": "3600",
        "tts-aigc-watermark": "true",
      },
    );

    expect(draft.providerParameters["tts-minimax-continuous-sound"]).toBe("false");
    expect(draft.providerParameters["tts-minimax-async-timeout-s"]).toBe("3600");
    expect(draft.providerParameters["tts-aigc-watermark"]).toBe("true");

    const payload = voicePlatformSettingsToCreationParameters(draft);
    expect(payload["tts-minimax-continuous-sound"]).toBe("false");
    expect(payload["tts-minimax-async-timeout-s"]).toBe("3600");
    expect(payload["tts-aigc-watermark"]).toBe("true");

    // Defaults when the engine has no persisted value yet.
    const fresh = createVoicePlatformSettingsDraft(voiceStudio(""));
    expect(fresh.providerParameters["tts-minimax-continuous-sound"]).toBe("false");
    expect(fresh.providerParameters["tts-minimax-async-timeout-s"]).toBe("7200");
    expect(fresh.providerParameters["tts-aigc-watermark"]).toBe("false");
  });

  it("hydrates the six PySide TTS text routes and serializes primary, fallbacks and temperature", () => {
    const route = {
      id: "tts_generate_dubbing_script",
      taskKey: "tts_generate_dubbing_script",
      label: "配音脚本生成",
      hint: "生成结构化配音脚本",
      primaryProfileId: "tongyi:qwen-max",
      fallbackRoutes: [{
        profileId: "openai:gpt-4o",
        thinkingEnabled: false,
        multiTurnEnabled: false,
      }],
      thinkingEnabled: false,
      multiTurnEnabled: false,
      temperature: null,
      temperatureKind: null,
      supportsMultiTurn: false,
    } as const;
    const draft = createVoicePlatformSettingsDraft(
      voiceStudio("MiniMax · speech-02-hd", "minimax"),
      { "tts-script-generation-temperature": "0.25" },
      {
        defaultProfileId: "tongyi:qwen-max",
        modelProfiles: [
          {
            id: "tongyi:qwen-max",
            label: "通义 · Qwen Max",
            provider: "tongyi",
            model: "qwen-max",
            tierLabel: "高级",
            statusLabel: "已配置",
            supportsThinking: false,
            supportsMultiTurn: false,
            keyConfigured: true,
          },
          {
            id: "openai:gpt-4o",
            label: "OpenAI · GPT-4o",
            provider: "openai",
            model: "gpt-4o",
            tierLabel: "高级",
            statusLabel: "已配置",
            supportsThinking: false,
            supportsMultiTurn: true,
            keyConfigured: true,
          },
        ],
        routingGroups: [{
          id: "配音",
          label: "配音",
          description: "配音文本任务",
          subgroups: [],
          routes: [route],
        }],
      },
    );

    const commands = voiceTextRoutesToCommands(draft.textRoutes, {
      ttsScriptTemperature: draft.ttsScriptTemperature,
      ttsReviewTemperature: draft.ttsReviewTemperature,
      ttsNarratorTemperature: draft.ttsNarratorTemperature,
      ttsSoundDesignTemperature: draft.ttsSoundDesignTemperature,
    });

    expect(draft.textRoutes.tts_generate_dubbing_script.primaryProfileId).toBe("tongyi:qwen-max");
    expect(draft.textRoutes.tts_generate_dubbing_script.fallbackProfileIds).toEqual(["openai:gpt-4o"]);
    expect(commands.tts_generate_dubbing_script?.temperature).toBe(0.25);
    expect(commands.tts_generate_dubbing_script?.fallbackRoutes[0]?.profileId).toBe("openai:gpt-4o");
    expect(Object.keys(commands)).toHaveLength(6);
  });

  it("removes only voice text routes from the shared 火候 routing projection", () => {
    const settings = {
      activeThemeId: "default",
      defaultProvider: "tongyi",
      storageRootLabel: "/tmp",
      mockMode: false,
      modelProfiles: [],
      routingGroups: [{
        id: "mixed",
        label: "混合",
        description: "",
        subgroups: [],
        routes: [
          {
            id: "tts_sound_design",
            taskKey: "tts_sound_design",
            label: "声音设计",
            hint: "",
            primaryProfileId: "",
            fallbackRoutes: [],
            thinkingEnabled: false,
            multiTurnEnabled: false,
            temperature: null,
            temperatureKind: null,
            supportsMultiTurn: false,
          },
          {
            id: "draft_chapter",
            taskKey: "draft_chapter",
            label: "章节起草",
            hint: "",
            primaryProfileId: "",
            fallbackRoutes: [],
            thinkingEnabled: false,
            multiTurnEnabled: false,
            temperature: 0.7,
            temperatureKind: "base",
            supportsMultiTurn: false,
          },
        ],
      }],
    } as const;

    const result = withoutVoiceTextRoutes(settings);

    expect(result.routingGroups[0]?.routes.map((route) => route.taskKey)).toEqual(["draft_chapter"]);
  });
});
