import type { VoiceStudioView } from "@nimo/engine-contracts";
import type { VoiceScriptDraft } from "../../lib/voice-script-session";

/**
 * Build a voice-script segment override from a guidance draft.
 * Mirrors PySide6 表演指导 → 合成覆盖参数映射（情绪标签归一化 + 可选数值解析）。
 */
export function buildVoiceSegmentOverride(
  segment: VoiceStudioView["script"][number],
  draft?: VoiceScriptDraft,
): Readonly<Record<string, unknown>> {
  const source = draft ?? segment;
  const emotionByLabel: Readonly<Record<string, string>> = {
    中性: "neutral",
    低语: "whisper",
    克制: "neutral",
    警觉: "anxious",
    愤怒: "angry",
    悲伤: "sad",
    温柔: "tender",
  };
  const optionalNumber = (value: string | undefined) => {
    if (!value || value === "default") return null;
    const numeric = Number(value.match(/[+-]?\d+(?:\.\d+)?/)?.[0]);
    return Number.isFinite(numeric) ? numeric : null;
  };
  return {
    segment_index: segment.segmentIndex,
    segment_type: segment.kindLabel.includes("对白") ? "dialogue" : "narration",
    character_id: source.speakerId,
    character_name: source.speakerLabel,
    text: source.content,
    emotion: emotionByLabel[source.emotionLabel] ?? "neutral",
    emotion_intensity: (draft?.emotionIntensity ?? 50) / 100,
    tone_hint: draft?.toneHint ?? "",
    speed_override: optionalNumber(draft?.speed),
    vol_override: optionalNumber(draft?.volume),
    pitch_override: optionalNumber(draft?.pitch),
    stress_words: draft?.stressWords
      .split(/[，,\s]+/)
      .map((item) => item.trim())
      .filter(Boolean) ?? [],
    narrator_distance: draft?.narratorDistance === "project-default"
      ? ""
      : draft?.narratorDistance ?? "",
    pronunciation_overrides: draft?.pronunciationOverrides
      .split(/\r?\n/)
      .map((item) => item.trim())
      .filter(Boolean) ?? [],
    language_code: draft?.language ?? "auto",
  };
}
