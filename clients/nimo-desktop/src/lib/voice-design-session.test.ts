import { describe, expect, it } from "vitest";

import { applyLocalVoiceDesign, canStartVoiceDesign, createVoiceDesignDraft, resolveVoiceDesign, startVoiceDesign, updateVoiceDesignDraft } from "./voice-design-session";

const member = {
  id: "lin-zhu",
  name: "林逐",
  role: "主视角",
  statusLabel: "已配",
  voiceLabel: "冷静女声",
  description: "习惯先判断，再回答。",
} as const;

describe("voice design local session", () => {
  it("requires a non-empty brief before an in-memory run can start", () => {
    const empty = updateVoiceDesignDraft(createVoiceDesignDraft(member), { brief: "  " });

    expect(canStartVoiceDesign(empty)).toBe(false);
    expect(startVoiceDesign(empty).status).toBe("editing");
  });

  it("keeps success and explicit local failure as separate states", () => {
    const completed = resolveVoiceDesign(startVoiceDesign(createVoiceDesignDraft(member)));
    const failed = resolveVoiceDesign(startVoiceDesign(updateVoiceDesignDraft(createVoiceDesignDraft(member), { simulateFailure: true })));

    expect(completed.status).toBe("completed");
    expect(failed.status).toBe("failed");
  });

  it("projects a completed design only into the front-end cast session", () => {
    const designed = applyLocalVoiceDesign(member);

    expect(designed).toMatchObject({ id: "lin-zhu", statusLabel: "待试听", voiceLabel: "AI 设计 · 林逐" });
    expect(designed.description).toBe(member.description);
  });
});
