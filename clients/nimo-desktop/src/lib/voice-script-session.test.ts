import { describe, expect, it } from "vitest";

import { applySafeVoiceSuggestion, commitVoiceScriptDrafts, createVoiceScriptEditResult, createVoiceScriptDrafts, mergeVoiceScriptDrafts, updateVoiceScriptDraft } from "./voice-script-session";

const segments = [
  { id: "seg-1", segmentIndex: 0, speakerId: "narrator", speakerLabel: "旁白", kindLabel: "叙述", emotionLabel: "低语", content: "雨声压在高架桥底", statusLabel: "已合成", needsSpeakerReview: false },
  { id: "seg-2", segmentIndex: 1, speakerId: "lin-zhu", speakerLabel: "林逐", kindLabel: "对白", emotionLabel: "克制", content: "这串时间戳不该出现", statusLabel: "已合成", needsSpeakerReview: false },
] as const;

describe("voice script local session", () => {
  it("only performs the bounded punctuation suggestion", () => {
    expect(applySafeVoiceSuggestion("  雨声压在高架桥底  ")).toBe("雨声压在高架桥底。");
  });

  it("marks only changed segments as pending review on local save", () => {
    const drafts = updateVoiceScriptDraft(createVoiceScriptDrafts(segments), "seg-2", { content: "这串时间戳，不该出现。", emotionLabel: "警觉" });
    expect(commitVoiceScriptDrafts(segments, drafts)).toMatchObject([
      { id: "seg-1", statusLabel: "已合成" },
      { id: "seg-2", content: "这串时间戳，不该出现。", emotionLabel: "警觉", statusLabel: "待审听" },
    ]);
  });

  it("keeps the extra directing controls local until the engine contract grows", () => {
    const drafts = updateVoiceScriptDraft(createVoiceScriptDrafts(segments), "seg-1", {
      narratorDistance: "intimate",
      voiceEffect: "memory",
      platformInstruction: "句尾稍收，留出环境声。",
    });

    expect(commitVoiceScriptDrafts(segments, drafts)[0]).toEqual(segments[0]);
  });

  it("marks a reassigned speaker as pending review", () => {
    const drafts = updateVoiceScriptDraft(createVoiceScriptDrafts(segments), "seg-1", { speakerId: "lin-zhu", speakerLabel: "林逐" });

    expect(commitVoiceScriptDrafts(segments, drafts)[0]).toMatchObject({ speakerId: "lin-zhu", speakerLabel: "林逐", statusLabel: "待审听" });
  });

  it("keeps performance guidance outside the source-script save channel", () => {
    const initial = updateVoiceScriptDraft(createVoiceScriptDrafts(segments), "seg-2", { toneHint: "压低声线" });
    const rehydrated = mergeVoiceScriptDrafts(segments, initial);
    const result = createVoiceScriptEditResult("performance", segments, rehydrated);

    expect(result.drafts["seg-2"]?.toneHint).toBe("压低声线");
    expect(result.segments).toEqual(segments);
  });
});
