import { describe, expect, it } from "vitest";

import { applyLocalVoiceClone, canStartVoiceClone, createVoiceCloneDraft, resolveVoiceClone, startVoiceClone, updateVoiceCloneDraft } from "./voice-clone-session";

const member = { id: "zhou-yan", name: "周砚", role: "调查官", statusLabel: "已配", voiceLabel: "低沉男声 · Bo", description: "语句短。" } as const;

describe("voice clone local session", () => {
  it("requires a reference label and provider-required consent", () => {
    const selected = updateVoiceCloneDraft(createVoiceCloneDraft(), { referenceLabel: "zhou-yan-reference.wav" });

    expect(canStartVoiceClone(selected, false)).toBe(true);
    expect(canStartVoiceClone(selected, true)).toBe(false);
    expect(canStartVoiceClone(updateVoiceCloneDraft(selected, { consentConfirmed: true }), true)).toBe(true);
  });

  it("models the local async success and failure paths without a file upload", () => {
    const success = resolveVoiceClone(startVoiceClone(updateVoiceCloneDraft(createVoiceCloneDraft(), { referenceLabel: "sample.wav" }), false));
    const failed = resolveVoiceClone(startVoiceClone(updateVoiceCloneDraft(createVoiceCloneDraft(), { referenceLabel: "sample.wav", simulateFailure: true }), false));

    expect(success.status).toBe("completed");
    expect(failed.status).toBe("failed");
  });

  it("updates only the local cast projection after explicit apply", () => {
    expect(applyLocalVoiceClone(member)).toMatchObject({ id: "zhou-yan", statusLabel: "待试听", voiceLabel: "克隆参考 · 周砚" });
  });
});
