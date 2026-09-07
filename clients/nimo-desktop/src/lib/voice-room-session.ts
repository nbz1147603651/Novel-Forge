import type { VoiceRoomTakeView, VoiceScriptSegmentView } from "@nimo/engine-contracts";

import { mergeVoiceScriptDrafts, type VoiceScriptDraft } from "./voice-script-session";

/**
 * A room take is deliberately separate from the source dubbing script.
 *
 * The Engine owns durable guidance/candidate/accepted states. This reducer
 * only supplies request-in-flight feedback until the next Engine projection
 * rehydrates the Voice Room.
 */
export type VoiceRoomTakeState = "formal" | "guidance_saved" | "generating" | "candidate" | "accepting" | "accepted";

export interface VoiceRoomSegmentSession {
  readonly audioUrl?: string;
  readonly hasDraftGuidance: boolean;
  readonly state: VoiceRoomTakeState;
  readonly takeId?: string;
}

export type VoiceRoomSession = Readonly<Record<string, VoiceRoomSegmentSession>>;

const performanceFields: readonly (keyof VoiceScriptDraft)[] = [
  "content",
  "emotionLabel",
  "toneHint",
  "emotionIntensity",
  "speed",
  "volume",
  "pitch",
  "stressWords",
  "voiceEffect",
  "narratorDistance",
  "pronunciationOverrides",
  "language",
  "deliveryStyle",
  "energy",
  "articulation",
  "breathiness",
  "tension",
  "intent",
  "platformInstruction",
];

export function createVoiceRoomSession(
  segments: readonly VoiceScriptSegmentView[],
  roomTakes: readonly VoiceRoomTakeView[] = [],
): VoiceRoomSession {
  const takeBySegmentIndex = new Map(roomTakes.map((take) => [take.segmentIndex, take]));
  return Object.fromEntries(segments.map((segment) => {
    const take = takeBySegmentIndex.get(segment.segmentIndex);
    return [segment.id, {
      state: take?.state ?? "formal",
      hasDraftGuidance: take?.state === "guidance_saved",
      ...(take?.takeId ? { takeId: take.takeId } : {}),
      ...(take?.audioUrl ? { audioUrl: take.audioUrl } : {}),
    }];
  }));
}

const emotionLabelByEngineValue: Readonly<Record<string, string>> = {
  neutral: "中性",
  whisper: "低语",
  anxious: "警觉",
  angry: "愤怒",
  sad: "悲伤",
  tender: "温柔",
};

function guidanceField(
  guidance: Readonly<Record<string, unknown>>,
  camelCase: string,
  snakeCase: string,
): unknown {
  return guidance[camelCase] ?? guidance[snakeCase];
}

function optionalNumberDraft(value: unknown): string {
  return typeof value === "number" && Number.isFinite(value) ? String(value) : "default";
}

function optionalWordsDraft(value: unknown): string {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string").join(", ") : "";
}

/** Rebuild editable guidance from the Engine projection after a reload. */
export function createVoiceRoomGuidanceDrafts(
  segments: readonly VoiceScriptSegmentView[],
  roomTakes: readonly VoiceRoomTakeView[] = [],
): Readonly<Record<string, VoiceScriptDraft>> {
  const defaults = mergeVoiceScriptDrafts(segments, undefined);
  const takeBySegmentIndex = new Map(roomTakes.map((take) => [take.segmentIndex, take]));
  return Object.fromEntries(segments.flatMap((segment) => {
    const guidance = takeBySegmentIndex.get(segment.segmentIndex)?.guidance;
    const base = defaults[segment.id];
    if (guidance === undefined || base === undefined) return [];
    const emotion = guidanceField(guidance, "emotion", "emotion");
    const intensity = guidanceField(guidance, "emotionIntensity", "emotion_intensity");
    const normalizedIntensity = typeof intensity === "number" && Number.isFinite(intensity)
      ? Math.max(0, Math.min(100, intensity <= 1 ? intensity * 100 : intensity))
      : base.emotionIntensity;
    const narratorDistance = guidanceField(guidance, "narratorDistance", "narrator_distance");
    const toneHint = guidanceField(guidance, "toneHint", "tone_hint");
    const language = guidanceField(guidance, "languageCode", "language_code");
    return [[segment.id, {
      ...base,
      emotionLabel: typeof emotion === "string"
        ? emotionLabelByEngineValue[emotion] ?? base.emotionLabel
        : base.emotionLabel,
      emotionIntensity: normalizedIntensity,
      toneHint: typeof toneHint === "string" ? toneHint : base.toneHint,
      speed: optionalNumberDraft(guidanceField(guidance, "speedOverride", "speed_override")),
      volume: optionalNumberDraft(guidanceField(guidance, "volumeOverride", "volume_override")),
      pitch: optionalNumberDraft(guidanceField(guidance, "pitchOverride", "pitch_override")),
      stressWords: optionalWordsDraft(guidanceField(guidance, "stressWords", "stress_words")),
      narratorDistance: typeof narratorDistance === "string" && narratorDistance
        ? narratorDistance
        : "project-default",
      pronunciationOverrides: optionalWordsDraft(
        guidanceField(guidance, "pronunciationOverrides", "pronunciation_overrides"),
      ),
      language: typeof language === "string" && language ? language : "auto",
    }]];
  }));
}

function currentSegmentSession(session: VoiceRoomSession, segmentId: string): VoiceRoomSegmentSession {
  return session[segmentId] ?? { state: "formal", hasDraftGuidance: false };
}

function updateSegmentSession(
  session: VoiceRoomSession,
  segmentId: string,
  next: VoiceRoomSegmentSession,
): VoiceRoomSession {
  const current = currentSegmentSession(session, segmentId);
  return current.state === next.state
      && current.hasDraftGuidance === next.hasDraftGuidance
      && current.takeId === next.takeId
      && current.audioUrl === next.audioUrl
    ? session
    : { ...session, [segmentId]: next };
}

/** Saving guidance retires a previously generated candidate, as in PySide. */
export function saveVoiceRoomGuidance(
  session: VoiceRoomSession,
  segmentIds: readonly string[],
): VoiceRoomSession {
  return segmentIds.reduce<VoiceRoomSession>(
    (next, segmentId) => updateSegmentSession(next, segmentId, { state: "guidance_saved", hasDraftGuidance: true }),
    session,
  );
}

export function beginVoiceRoomCandidate(session: VoiceRoomSession, segmentId: string): VoiceRoomSession {
  const current = currentSegmentSession(session, segmentId);
  return updateSegmentSession(session, segmentId, { ...current, state: "generating" });
}

export function completeVoiceRoomCandidate(session: VoiceRoomSession, segmentId: string): VoiceRoomSession {
  const current = currentSegmentSession(session, segmentId);
  return current.state === "generating"
    ? updateSegmentSession(session, segmentId, { ...current, state: "candidate" })
    : session;
}

export function registerVoiceRoomCandidate(
  session: VoiceRoomSession,
  segmentId: string,
  takeId: string,
  audioUrl?: string,
): VoiceRoomSession {
  const current = currentSegmentSession(session, segmentId);
  return current.state === "generating"
    ? updateSegmentSession(session, segmentId, {
        ...current,
        state: "candidate",
        takeId,
        ...(audioUrl ? { audioUrl } : {}),
      })
    : session;
}

/** A candidate must exist before it can be promoted to the formal take. */
export function beginVoiceRoomAcceptance(session: VoiceRoomSession, segmentId: string): VoiceRoomSession {
  const current = currentSegmentSession(session, segmentId);
  return current.state === "candidate"
    ? updateSegmentSession(session, segmentId, { ...current, state: "accepting" })
    : session;
}

export function completeVoiceRoomAcceptance(session: VoiceRoomSession, segmentId: string): VoiceRoomSession {
  const current = currentSegmentSession(session, segmentId);
  return current.state === "accepting"
    ? updateSegmentSession(session, segmentId, {
        state: "accepted",
        hasDraftGuidance: false,
      })
    : session;
}

/** Discarding never changes the formal take or source script. */
export function discardVoiceRoomCandidate(session: VoiceRoomSession, segmentId: string): VoiceRoomSession {
  const current = currentSegmentSession(session, segmentId);
  if (current.state !== "candidate" && current.state !== "generating") return session;
  return updateSegmentSession(session, segmentId, {
    hasDraftGuidance: current.hasDraftGuidance,
    state: current.hasDraftGuidance ? "guidance_saved" : "formal",
  });
}

export function voiceRoomTakeLabel(
  session: VoiceRoomSession,
  segment: Pick<VoiceScriptSegmentView, "id" | "statusLabel">,
): string {
  switch (currentSegmentSession(session, segment.id).state) {
    case "guidance_saved": return "待审指导";
    case "generating": return "正在生成试听";
    case "candidate": return "候选试听 · 未接受";
    case "accepting": return "正在接受试听";
    case "accepted": return "已接受 · 待装配";
    default: return segment.statusLabel;
  }
}

export function changedVoicePerformanceDraftIds(
  segments: readonly VoiceScriptSegmentView[],
  drafts: Readonly<Record<string, VoiceScriptDraft>>,
  existing: Readonly<Record<string, VoiceScriptDraft>> | undefined,
): readonly string[] {
  const baseline = mergeVoiceScriptDrafts(segments, existing);
  return segments
    .filter((segment) => {
      const draft = drafts[segment.id];
      const original = baseline[segment.id];
      return draft !== undefined && original !== undefined
        && performanceFields.some((field) => draft[field] !== original[field]);
    })
    .map((segment) => segment.id);
}

export function hasVoiceScriptChanges(
  before: readonly VoiceScriptSegmentView[],
  after: readonly VoiceScriptSegmentView[],
): boolean {
  return before.length !== after.length || before.some((segment, index) => {
    const next = after[index];
    return next === undefined
      || segment.content !== next.content
      || segment.speakerId !== next.speakerId
      || segment.speakerLabel !== next.speakerLabel
      || segment.kindLabel !== next.kindLabel
      || segment.emotionLabel !== next.emotionLabel;
  });
}
