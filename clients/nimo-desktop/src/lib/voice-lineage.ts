import type { VoiceStudioView } from "@nimo/engine-contracts";

export type VoiceLineageTone = "danger" | "warning" | "success" | "info";

export interface VoiceLineageState {
  readonly sourceReady: boolean;
  readonly synthesisAllowed: boolean;
  readonly deliveryAllowed: boolean;
  readonly tone: VoiceLineageTone;
  readonly title: string;
  readonly detail: string;
}

/**
 * Translate Engine lineage codes into one author-facing production gate.
 * Older Engine hosts omit the detailed fields, so their existing booleans are
 * retained as a conservative compatibility fallback.
 */
export function voiceLineageState(studio: VoiceStudioView): VoiceLineageState {
  const sourceState = studio.novelSourceState ?? "ready";
  const scriptState = studio.scriptFreshness
    ?? (studio.scriptFresh ? "current" : studio.script.length > 0 ? "legacy" : "missing");
  const audioState = studio.audioFreshness
    ?? (studio.audioReady ? "current" : "missing");
  const sourceReady = sourceState === "ready";
  const synthesisAllowed = sourceReady && scriptState === "current";
  const deliveryAllowed = sourceReady && audioState === "current" && studio.audioReady;

  if (sourceState === "blocked") {
    return {
      sourceReady,
      synthesisAllowed: false,
      deliveryAllowed: false,
      tone: "danger",
      title: "小说终稿待验证",
      detail: "本章改写后的拟人化、状态回放或最终校验尚未闭环；完成终稿验证后才能生成或交付配音。",
    };
  }
  if (sourceState === "missing") {
    return {
      sourceReady,
      synthesisAllowed: false,
      deliveryAllowed: false,
      tone: "danger",
      title: "未找到小说终稿",
      detail: "当前章节没有可验证的归档正文；请先在章台完成章节归档。",
    };
  }
  if (scriptState === "stale") {
    return {
      sourceReady,
      synthesisAllowed: false,
      deliveryAllowed: false,
      tone: "danger",
      title: "配音脚本已过期",
      detail: "小说正文在脚本生成后发生了改写。请重新生成并复核配音脚本，旧音频会保留但不能作为当前版本交付。",
    };
  }
  if (scriptState === "legacy" || scriptState === "source_missing") {
    return {
      sourceReady,
      synthesisAllowed: false,
      deliveryAllowed: false,
      tone: "warning",
      title: "配音脚本版本待核验",
      detail: "现有脚本缺少可验证的终稿版本标识；重新生成脚本后才能继续合成。",
    };
  }
  if (scriptState === "missing") {
    return {
      sourceReady,
      synthesisAllowed: false,
      deliveryAllowed: false,
      tone: "info",
      title: "小说终稿已就绪",
      detail: "尚未生成本章配音脚本；生成后会自动绑定当前终稿版本。",
    };
  }
  if (audioState === "stale" || audioState === "legacy" || audioState === "source_missing") {
    return {
      sourceReady,
      synthesisAllowed,
      deliveryAllowed: false,
      tone: "warning",
      title: "成品音频需要重建",
      detail: "脚本已对齐当前终稿，但现有音频属于旧脚本或缺少版本证据；请重新合成并装配。",
    };
  }
  if (deliveryAllowed) {
    return {
      sourceReady,
      synthesisAllowed,
      deliveryAllowed,
      tone: "success",
      title: "小说、脚本与音频版本一致",
      detail: "当前成品已通过版本血缘检查，可继续试听或进入交付。",
    };
  }
  return {
    sourceReady,
    synthesisAllowed,
    deliveryAllowed: false,
    tone: "success",
    title: "配音脚本为当前版本",
    detail: "脚本与小说终稿一致；可继续复核说话人、合成与装配。",
  };
}
