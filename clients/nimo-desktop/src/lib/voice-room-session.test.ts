import { describe, expect, it } from "vitest";

import { beginVoiceRoomAcceptance, beginVoiceRoomCandidate, changedVoicePerformanceDraftIds, completeVoiceRoomAcceptance, completeVoiceRoomCandidate, createVoiceRoomGuidanceDrafts, createVoiceRoomSession, discardVoiceRoomCandidate, saveVoiceRoomGuidance, voiceRoomTakeLabel } from "./voice-room-session";
import { createVoiceScriptDrafts, updateVoiceScriptDraft } from "./voice-script-session";

const segments = [
  { id: "seg-1", segmentIndex: 0, speakerId: "narrator", speakerLabel: "旁白", kindLabel: "叙述", emotionLabel: "低语", content: "雨声压在高架桥底。", statusLabel: "已合成", needsSpeakerReview: false },
  { id: "seg-2", segmentIndex: 1, speakerId: "lin-zhu", speakerLabel: "林逐", kindLabel: "对白", emotionLabel: "克制", content: "这串时间戳不该出现。", statusLabel: "已合成", needsSpeakerReview: false },
] as const;

describe("voice room local review session", () => {
  it("keeps guidance separate and invalidates a candidate when guidance changes", () => {
    const initial = createVoiceRoomSession(segments);
    const candidate = completeVoiceRoomCandidate(beginVoiceRoomCandidate(initial, "seg-1"), "seg-1");
    const revised = saveVoiceRoomGuidance(candidate, ["seg-1"]);

    expect(voiceRoomTakeLabel(candidate, segments[0])).toBe("候选试听 · 未接受");
    expect(revised["seg-1"]).toEqual({ state: "guidance_saved", hasDraftGuidance: true });
    expect(voiceRoomTakeLabel(revised, segments[0])).toBe("待审指导");
  });

  it("rehydrates persisted Engine guidance and candidate take states", () => {
    const session = createVoiceRoomSession(segments, [
      { segmentIndex: 0, state: "candidate", takeId: "take-candidate", audioUrl: "/takes/candidate.mp3" },
      {
        segmentIndex: 1,
        state: "guidance_saved",
        guidance: {
          emotion: "tender",
          emotion_intensity: 0.7,
          tone_hint: "压低声音",
          speed_override: 0.9,
          volume_override: 1.1,
          stress_words: ["时间戳"],
          narrator_distance: "close",
          pronunciation_overrides: ["林逐/lin2 zhu2"],
          language_code: "zh",
        },
      },
    ]);

    expect(session["seg-1"]).toMatchObject({
      state: "candidate",
      takeId: "take-candidate",
      audioUrl: "/takes/candidate.mp3",
    });
    expect(session["seg-2"]).toMatchObject({ state: "guidance_saved", hasDraftGuidance: true });
    expect(createVoiceRoomGuidanceDrafts(segments, [
      {
        segmentIndex: 1,
        state: "guidance_saved",
        guidance: {
          emotion: "tender",
          emotion_intensity: 0.7,
          tone_hint: "压低声音",
          speed_override: 0.9,
          volume_override: 1.1,
          stress_words: ["时间戳"],
          narrator_distance: "close",
          pronunciation_overrides: ["林逐/lin2 zhu2"],
          language_code: "zh",
        },
      },
    ])["seg-2"]).toMatchObject({
      emotionLabel: "温柔",
      emotionIntensity: 70,
      toneHint: "压低声音",
      speed: "0.9",
      volume: "1.1",
      stressWords: "时间戳",
      narratorDistance: "close",
      pronunciationOverrides: "林逐/lin2 zhu2",
      language: "zh",
    });
  });

  it("requires a completed candidate before acceptance and leaves formal data out of the reducer", () => {
    const initial = createVoiceRoomSession(segments);
    expect(beginVoiceRoomAcceptance(initial, "seg-1")).toBe(initial);

    const candidate = completeVoiceRoomCandidate(beginVoiceRoomCandidate(initial, "seg-1"), "seg-1");
    const accepting = beginVoiceRoomAcceptance(candidate, "seg-1");
    const accepted = completeVoiceRoomAcceptance(accepting, "seg-1");

    expect(voiceRoomTakeLabel(accepted, segments[0])).toBe("已接受 · 待装配");
    expect(accepted["seg-1"]?.hasDraftGuidance).toBe(false);
  });

  it("discarding a candidate restores the correct non-formal review state", () => {
    const withGuidance = saveVoiceRoomGuidance(createVoiceRoomSession(segments), ["seg-2"]);
    const candidate = completeVoiceRoomCandidate(beginVoiceRoomCandidate(withGuidance, "seg-2"), "seg-2");

    expect(discardVoiceRoomCandidate(candidate, "seg-2")["seg-2"]?.state).toBe("guidance_saved");
    expect(discardVoiceRoomCandidate(candidate, "seg-2")["seg-2"]?.hasDraftGuidance).toBe(true);
  });

  it("only marks actually changed performance drafts for review", () => {
    const defaults = createVoiceScriptDrafts(segments);
    const changed = updateVoiceScriptDraft(defaults, "seg-2", { toneHint: "压低声线", speed: "0.9×" });

    expect(changedVoicePerformanceDraftIds(segments, defaults, undefined)).toEqual([]);
    expect(changedVoicePerformanceDraftIds(segments, changed, undefined)).toEqual(["seg-2"]);
  });
});
