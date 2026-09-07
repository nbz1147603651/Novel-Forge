import type { VoiceStudioView } from "@nimo/engine-contracts";
import { describe, expect, it } from "vitest";

import { voiceCloneReferenceMode } from "./voice-clone-mode";

function studio(overrides: Partial<VoiceStudioView> = {}): VoiceStudioView {
  return {
    projectId: "voice-project",
    projectTitle: "声腔测试",
    providerLabel: "dashscope",
    configuredModelLabel: "speech-02-hd",
    providerCatalog: [{
      id: "dashscope",
      label: "阿里百炼",
      aliases: ["bailian"],
      officialDocsUrl: "",
      defaultModel: "speech-02-hd",
      activeModelParameterId: "tts-dashscope-model",
      isLocal: false,
      adapterStatus: "production",
      highlights: [],
      settings: [],
      models: [{
        id: "speech-02-hd",
        label: "Speech 02 HD",
        purposes: ["clone"],
        features: [],
        voiceClone: true,
        voiceDesign: true,
        systemVoiceCatalog: true,
        localReferenceAudio: true,
        adapterSupported: true,
        recommended: true,
        maxInputChars: 0,
        notes: [],
      }],
    }],
    chapterNumber: 1,
    availableChapters: [1],
    teamConfirmed: false,
    scriptFresh: false,
    unresolvedSpeakerCount: 0,
    audioReady: false,
    subtitleReady: false,
    subtitleText: "",
    deliveryState: "not_started",
    activeTaskId: null,
    mixTracks: [],
    soundAssets: [],
    cast: [],
    script: [],
    roomTakes: [],
    ...overrides,
  };
}

describe("voiceCloneReferenceMode", () => {
  it("uses the native local-file flow only when the configured model accepts local audio", () => {
    expect(voiceCloneReferenceMode(studio())).toBe("local-file");
  });

  it("uses the provider File-ID flow when the configured model cannot read local audio", () => {
    const providerCatalog = (studio().providerCatalog ?? []).map((provider) => ({
      ...provider,
      models: provider.models.map((model) => ({ ...model, localReferenceAudio: false })),
    }));
    expect(voiceCloneReferenceMode(studio({ providerCatalog }))).toBe("provider-file-id");
  });

  it("fails closed to provider File ID for an unknown provider", () => {
    expect(voiceCloneReferenceMode(studio({ providerLabel: "unconfigured" }))).toBe(
      "provider-file-id",
    );
  });

  it("fails closed to provider File ID for an unknown configured model", () => {
    expect(voiceCloneReferenceMode(studio({ configuredModelLabel: "unknown-model" }))).toBe(
      "provider-file-id",
    );
  });

});
