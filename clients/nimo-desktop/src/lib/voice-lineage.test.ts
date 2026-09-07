import type { VoiceStudioView } from "@nimo/engine-contracts";
import { describe, expect, it } from "vitest";

import { voiceLineageState } from "./voice-lineage";

function studio(overrides: Partial<VoiceStudioView> = {}): VoiceStudioView {
  return {
    projectId: "book",
    projectTitle: "书",
    providerLabel: "mock",
    configuredModelLabel: "mock",
    chapterNumber: 1,
    availableChapters: [1],
    teamConfirmed: true,
    scriptFresh: true,
    novelSourceState: "ready",
    scriptFreshness: "current",
    audioFreshness: "missing",
    freshnessBlockingReasons: [],
    unresolvedSpeakerCount: 0,
    audioReady: false,
    subtitleReady: false,
    deliveryState: "not_started",
    activeTaskId: null,
    subtitleText: "",
    mixTracks: [],
    soundAssets: [],
    cast: [],
    script: [],
    roomTakes: [],
    ...overrides,
  };
}

describe("voiceLineageState", () => {
  it("blocks synthesis when a novel rewrite made the script stale", () => {
    const state = voiceLineageState(studio({
      scriptFresh: false,
      scriptFreshness: "stale",
      audioFreshness: "stale",
    }));

    expect(state.synthesisAllowed).toBe(false);
    expect(state.deliveryAllowed).toBe(false);
    expect(state.title).toContain("脚本已过期");
  });

  it("allows synthesis but not delivery while current audio is missing", () => {
    const state = voiceLineageState(studio());

    expect(state.synthesisAllowed).toBe(true);
    expect(state.deliveryAllowed).toBe(false);
    expect(state.title).toContain("脚本为当前版本");
  });

  it("allows delivery only for current Engine-verified audio", () => {
    const state = voiceLineageState(studio({
      audioFreshness: "current",
      audioReady: true,
      deliveryState: "ready",
    }));

    expect(state.deliveryAllowed).toBe(true);
    expect(state.title).toContain("版本一致");
  });
});
