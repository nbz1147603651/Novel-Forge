import type { VoiceCastMemberView } from "@nimo/engine-contracts";

export type VoiceCloneRunStatus = "editing" | "running" | "completed" | "failed";

export interface VoiceCloneDraft {
  readonly referenceLabel: string;
  readonly referenceTranscript: string;
  readonly consentConfirmed: boolean;
  readonly simulateFailure: boolean;
  readonly status: VoiceCloneRunStatus;
}

export function createVoiceCloneDraft(): VoiceCloneDraft {
  return { referenceLabel: "", referenceTranscript: "", consentConfirmed: false, simulateFailure: false, status: "editing" };
}

export function updateVoiceCloneDraft(draft: VoiceCloneDraft, patch: Partial<Pick<VoiceCloneDraft, "referenceLabel" | "referenceTranscript" | "consentConfirmed" | "simulateFailure">>): VoiceCloneDraft {
  return { ...draft, ...patch, status: "editing" };
}

export function canStartVoiceClone(draft: VoiceCloneDraft, requiresConsent: boolean): boolean {
  return Boolean(draft.referenceLabel.trim()) && (!requiresConsent || draft.consentConfirmed) && draft.status !== "running";
}

export function startVoiceClone(draft: VoiceCloneDraft, requiresConsent: boolean): VoiceCloneDraft {
  return canStartVoiceClone(draft, requiresConsent) ? { ...draft, status: "running" } : draft;
}

export function resolveVoiceClone(draft: VoiceCloneDraft): VoiceCloneDraft {
  if (draft.status !== "running") return draft;
  return { ...draft, status: draft.simulateFailure ? "failed" : "completed" };
}

/** The reference itself never crosses this UI-only boundary. */
export function applyLocalVoiceClone(member: VoiceCastMemberView): VoiceCastMemberView {
  return { ...member, statusLabel: "待试听", voiceLabel: `克隆参考 · ${member.name}` };
}
