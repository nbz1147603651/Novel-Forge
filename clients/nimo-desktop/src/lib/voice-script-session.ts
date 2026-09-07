import type { VoiceScriptSegmentView } from "@nimo/engine-contracts";

export interface VoiceScriptDraft extends VoiceScriptSegmentView {
  readonly toneHint: string;
  readonly emotionIntensity: number;
  readonly speed: string;
  readonly volume: string;
  readonly pitch: string;
  readonly stressWords: string;
  readonly voiceEffect: string;
  readonly narratorDistance: string;
  readonly pronunciationOverrides: string;
  readonly language: string;
  readonly deliveryStyle: string;
  readonly energy: string;
  readonly articulation: string;
  readonly breathiness: string;
  readonly tension: string;
  readonly intent: string;
  readonly platformInstruction: string;
}

export type VoiceScriptEditMode = "script" | "performance";

export interface VoiceScriptEditResult {
  readonly drafts: Readonly<Record<string, VoiceScriptDraft>>;
  readonly segments: readonly VoiceScriptSegmentView[];
}

export function createVoiceScriptDrafts(segments: readonly VoiceScriptSegmentView[]): Readonly<Record<string, VoiceScriptDraft>> {
  return Object.fromEntries(segments.map((segment) => [segment.id, {
    ...segment,
    toneHint: "",
    emotionIntensity: 50,
    speed: "default",
    volume: "default",
    pitch: "default",
    stressWords: "",
    voiceEffect: "none",
    narratorDistance: "project-default",
    pronunciationOverrides: "",
    language: "auto",
    deliveryStyle: "natural",
    energy: "natural",
    articulation: "natural",
    breathiness: "natural",
    tension: "natural",
    intent: "",
    platformInstruction: "",
  }]));
}

export function mergeVoiceScriptDrafts(
  segments: readonly VoiceScriptSegmentView[],
  existing: Readonly<Record<string, VoiceScriptDraft>> | undefined,
): Readonly<Record<string, VoiceScriptDraft>> {
  const defaults = createVoiceScriptDrafts(segments);
  if (existing === undefined) return defaults;
  return Object.fromEntries(segments.map((segment) => [segment.id, { ...defaults[segment.id]!, ...existing[segment.id] }]));
}

export function updateVoiceScriptDraft(
  drafts: Readonly<Record<string, VoiceScriptDraft>>,
  segmentId: string,
  patch: Partial<VoiceScriptDraft>,
): Readonly<Record<string, VoiceScriptDraft>> {
  const current = drafts[segmentId];
  return current === undefined ? drafts : { ...drafts, [segmentId]: { ...current, ...patch } };
}

/** Apply only whitespace/punctuation normalization; it never invents prose. */
export function applySafeVoiceSuggestion(content: string): string {
  const normalized = content.replaceAll(/\s+/g, " ").trim();
  if (!normalized) return normalized;
  return /[。！？.!?]$/.test(normalized) ? normalized : `${normalized}。`;
}

export function commitVoiceScriptDrafts(
  segments: readonly VoiceScriptSegmentView[],
  drafts: Readonly<Record<string, VoiceScriptDraft>>,
): readonly VoiceScriptSegmentView[] {
  return segments.map((segment) => {
    const draft = drafts[segment.id];
    if (draft === undefined) return segment;
    return {
      ...segment,
      content: draft.content,
      speakerId: draft.speakerId,
      speakerLabel: draft.speakerLabel,
      kindLabel: draft.kindLabel,
      emotionLabel: draft.emotionLabel,
      statusLabel: draft.content === segment.content
        && draft.speakerId === segment.speakerId
        && draft.speakerLabel === segment.speakerLabel
        && draft.kindLabel === segment.kindLabel
        && draft.emotionLabel === segment.emotionLabel
        ? segment.statusLabel
        : "待审听",
    };
  });
}

/** Script edits update the rendered script; performance edits remain isolated drafts. */
export function createVoiceScriptEditResult(
  mode: VoiceScriptEditMode,
  segments: readonly VoiceScriptSegmentView[],
  drafts: Readonly<Record<string, VoiceScriptDraft>>,
): VoiceScriptEditResult {
  return {
    drafts,
    segments: mode === "script" ? commitVoiceScriptDrafts(segments, drafts) : segments,
  };
}
