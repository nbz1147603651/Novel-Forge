import type { VoiceCastMemberView } from "@nimo/engine-contracts";

export type VoiceDesignRunStatus = "editing" | "running" | "completed" | "failed";

export interface VoiceDesignDraft {
  readonly brief: string;
  readonly simulateFailure: boolean;
  readonly status: VoiceDesignRunStatus;
}

export function defaultVoiceDesignBrief(member: VoiceCastMemberView): string {
  return [
    `${member.name} · ${member.role}`,
    member.designBrief || member.description || "根据角色档案保留清晰的身份辨识度。",
    "关注声线年龄感、音域、语速、情绪底色与表达习惯。",
  ].join("\n");
}

export function createVoiceDesignDraft(member: VoiceCastMemberView): VoiceDesignDraft {
  return { brief: defaultVoiceDesignBrief(member), simulateFailure: false, status: "editing" };
}

export function updateVoiceDesignDraft(draft: VoiceDesignDraft, patch: Partial<Pick<VoiceDesignDraft, "brief" | "simulateFailure">>): VoiceDesignDraft {
  return { ...draft, ...patch, status: "editing" };
}

export function canStartVoiceDesign(draft: VoiceDesignDraft): boolean {
  return Boolean(draft.brief.trim()) && draft.status !== "running";
}

export function startVoiceDesign(draft: VoiceDesignDraft): VoiceDesignDraft {
  return canStartVoiceDesign(draft) ? { ...draft, status: "running" } : draft;
}

export function resolveVoiceDesign(draft: VoiceDesignDraft): VoiceDesignDraft {
  if (draft.status !== "running") return draft;
  return { ...draft, status: draft.simulateFailure ? "failed" : "completed" };
}

/** Phase 1 projection only. It never writes a voice profile or contacts a TTS provider. */
export function applyLocalVoiceDesign(member: VoiceCastMemberView): VoiceCastMemberView {
  return {
    ...member,
    statusLabel: "待试听",
    voiceLabel: `AI 设计 · ${member.name}`,
  };
}
