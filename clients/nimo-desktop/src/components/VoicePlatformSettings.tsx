import { useEffect, useRef, useState, type ReactNode } from "react";

import type {
  SettingsView,
  VoiceProviderSettingView,
  VoiceProviderView,
  VoiceStudioView,
} from "@nimo/engine-contracts";

import {
  createVoiceTextRouteDrafts,
  routeableVoiceProfiles,
  voiceTextRouteDefinitions,
  type VoiceTextRouteDrafts,
  type VoiceTextRouteTaskKey,
} from "../lib/voice-text-routing";
import { VoiceLocalModelCenter, type VoiceAudioModelCenterClient } from "./VoiceLocalModelCenter";
import {
  ttsProviderKeyFromLabel,
  ttsProviderLabelForId,
  ttsProviderOptions,
  ttsProviderSpec,
} from "../lib/voice-provider-options";

export type VoiceSettingsSectionId =
  | "local-models"
  | "quality-routing"
  | "platform"
  | "credentials"
  | "strategy"
  | "output-runtime"
  | "sound-design"
  | "text-intelligence";

export interface VoicePlatformSettingsDraft {
  readonly enabled: boolean;
  readonly provider: string;
  readonly automationMode: "manual" | "assisted" | "autonomous";
  readonly audioAcceleratorPreference: "auto" | "cpu" | "cuda" | "mps";
  readonly audioDualAlignmentValidation: boolean;
  readonly audioLocationPolicy: "cloud_only" | "hybrid" | "local_only" | "prefer_cloud" | "prefer_local";
  readonly audioMemoryBudget: "high" | "light" | "medium";
  readonly audioMfaCommand: string;
  readonly audioPluginManifestDirs: string;
  readonly audioPluginOverrides: string;
  readonly audioQualityPreset: "custom" | "low_resource" | "master" | "production" | "quick_preview";
  readonly audioQwen3AsrApiKey: string;
  readonly audioQwen3AsrBaseUrl: string;
  readonly audioSherpaBaseUrl: string;
  readonly audioWhisperxBaseUrl: string;
  readonly concurrency: string;
  readonly localModelResourceBudget: "high" | "light" | "medium";
  readonly localModelResourceWaitTimeoutS: string;
  readonly model: string;
  readonly ttsAlignmentRepairRounds: string;
  readonly ttsAudioQualityTier: "audition" | "standard" | "commercial";
  readonly ttsMasterQualityGateBlocking: boolean;
  readonly ttsMaxTextErrorRate: string;
  readonly ttsSubtitleWordLevel: boolean;
  readonly soundReuse: "project" | "application" | "off";
  readonly postArchiveRetryEnabled: boolean;
  readonly postArchiveRetryDelayS: string;
  readonly voiceDesignEnabled: boolean;
  readonly voiceSemanticMatchingEnabled: boolean;
  readonly voiceSemanticTopK: string;
  readonly monthlyBudgetUsd: string;
  readonly bookBudgetUsd: string;
  readonly chapterBudgetUsd: string;
  readonly projectStorageMb: string;
  // Provider-native values are keyed by the Engine's guarded parameter ids.
  readonly providerCatalog: readonly VoiceProviderView[];
  readonly providerParameters: Readonly<Record<string, string>>;
  // -- Output & runtime --
  readonly backgroundPipelineConcurrency: string;
  readonly scriptBatchConcurrency: string;
  readonly rateLimitRpm: string;
  readonly rateLimitCooldownS: string;
  readonly retryLimit: string;
  readonly outputFormat: string;
  readonly sampleRate: string;
  readonly cloneTtlDays: string;
  readonly parallelClone: boolean;
  // -- Strategy extras --
  readonly defaultSpeed: string;
  readonly autoTriggerAfterChapter: boolean;
  readonly narratorVoiceId: string;
  // -- Sound design workflow --
  readonly soundGenerationEnabled: boolean;
  readonly soundAutoGenerate: boolean;
  readonly soundAutoApprove: boolean;
  readonly soundDesignEnabled: boolean;
  // -- Text intelligence --
  readonly scriptLlmReviewEnabled: boolean;
  readonly scriptLlmReviewMaxTokens: string;
  readonly voiceLlmAdjudicationEnabled: boolean;
  readonly voiceLlmMinScore: string;
  readonly voiceLlmMaxCandidates: string;
  readonly voiceLlmChoicesPerChar: string;
  readonly voiceLlmAutoSelectScore: string;
  readonly voiceLlmMaxTokens: string;
  readonly ttsScriptTemperature: string;
  readonly scriptGenerationTopP: string;
  readonly ttsReviewTemperature: string;
  readonly ttsNarratorTemperature: string;
  readonly ttsSoundDesignTemperature: string;
  readonly soundDesignTopP: string;
  readonly textRoutes: VoiceTextRouteDrafts;
}

interface VoicePlatformModelOption {
  readonly label: string;
  readonly value: string;
}

/**
 * Early Web parity snapshots stored MiniMax's display label as the setting
 * value. The TTS gateway accepts the provider-native model id, so normalize
 * that legacy shape at the browser boundary before it can be persisted.
 */
export function normalizeVoicePlatformModel(model: string): string {
  const separator = " · ";
  return model.includes(separator) ? model.split(separator).at(-1)!.trim() : model.trim();
}

/**
 * Return the canonical model catalog a provider ships with, or an empty
 * list when the platform accepts free-form model ids. Single source of
 * truth for both the "当前平台模型" dropdown and the provider-specific
 * credential panels, so new platforms only add one catalog here.
 */
export function platformModelValues(
  provider: string,
  catalog: readonly VoiceProviderView[] = [],
  purpose: "formal" | "preview" | "clone" | "design" = "formal",
): readonly VoicePlatformModelOption[] {
  const spec = ttsProviderSpec(provider, catalog);
  return (spec?.models ?? [])
    .filter((model) => model.adapterSupported && model.purposes.includes(purpose))
    .map((model) => ({ value: model.id, label: model.label }));
}

export interface VoiceCommercialReadinessCheck {
  readonly detail: string;
  readonly id:
    | "delivery"
    | "dual-alignment"
    | "master-gate"
    | "planner"
    | "repair"
    | "sound-rights"
    | "subtitles";
  readonly label: string;
  readonly ready: boolean;
}

/**
 * Explain the independent controls that together make a commercial delivery.
 * Keeping this as a pure projection makes the readiness card testable and
 * prevents the UI from presenting a single preset as proof that every gate is
 * active.
 */
export function voiceCommercialReadinessChecks(
  draft: VoicePlatformSettingsDraft,
): readonly VoiceCommercialReadinessCheck[] {
  return [
    {
      id: "planner",
      label: "母带级能力计划",
      detail: "使用 master 计划器组合正式 TTS、对齐、质检与两遍母带。",
      ready: draft.audioQualityPreset === "master",
    },
    {
      id: "delivery",
      label: "商业交付档",
      detail: "启用跨章节响度、峰值、混音风险和商用素材权利门。",
      ready: draft.ttsAudioQualityTier === "commercial",
    },
    {
      id: "master-gate",
      label: "硬质量门",
      detail: "对齐或母带未达标时保留产物供检查，但禁止标记为正式成片。",
      ready: draft.ttsMasterQualityGateBlocking,
    },
    {
      id: "dual-alignment",
      label: "双对齐器复核",
      detail: "正式时间线由第二实现独立复核，避免单一 ASR 假阳性。",
      ready: draft.audioDualAlignmentValidation,
    },
    {
      id: "repair",
      label: "证据化局部返工",
      detail: "至少保留一轮局部修复，只重生成有失配证据的片段。",
      ready: Number(draft.ttsAlignmentRepairRounds) >= 1,
    },
    {
      id: "subtitles",
      label: "字词级交付字幕",
      detail: "保留可校对的字词时间线，供 SRT、校音与后期工程复用。",
      ready: draft.ttsSubtitleWordLevel,
    },
    {
      id: "sound-rights",
      label: "声音素材人工授权复核",
      detail: "禁止生成素材绕过试听与商业权利确认直接进入正式混音。",
      ready: !draft.soundAutoApprove,
    },
  ];
}

/** Apply only platform-neutral, reversible commercial-quality controls. */
export function commercialVoiceSettingsPatch(
  draft: VoicePlatformSettingsDraft,
): Partial<VoicePlatformSettingsDraft> {
  return {
    audioDualAlignmentValidation: true,
    audioMemoryBudget: "high",
    audioQualityPreset: "master",
    soundAutoApprove: false,
    ttsAlignmentRepairRounds: Number(draft.ttsAlignmentRepairRounds) >= 1
      ? draft.ttsAlignmentRepairRounds
      : "1",
    ttsAudioQualityTier: "commercial",
    ttsMasterQualityGateBlocking: true,
    ttsSubtitleWordLevel: true,
  };
}

/**
 * Switch the active provider and its model as one transaction. Provider-
 * specific persisted selections are preferred; an incompatible model id is
 * never carried into the newly selected platform.
 */
export function voiceProviderSwitchPatch(
  draft: VoicePlatformSettingsDraft,
  nextProvider: string,
): Partial<VoicePlatformSettingsDraft> {
  const currentSpec = ttsProviderSpec(draft.provider, draft.providerCatalog);
  const nextSpec = ttsProviderSpec(nextProvider, draft.providerCatalog);
  const providerParameters = { ...draft.providerParameters };
  if (currentSpec?.activeModelParameterId) {
    providerParameters[currentSpec.activeModelParameterId] = normalizeVoicePlatformModel(draft.model);
  }
  const remembered = nextSpec?.activeModelParameterId
    ? providerParameters[nextSpec.activeModelParameterId]
    : undefined;
  const models = platformModelValues(nextProvider, draft.providerCatalog);
  const selected = models.some((option) => option.value === remembered)
    ? remembered!
    : (nextSpec?.defaultModel ?? remembered ?? draft.model);
  if (nextSpec?.activeModelParameterId) {
    providerParameters[nextSpec.activeModelParameterId] = selected;
  }
  return { provider: nextProvider, model: selected, providerParameters };
}

/** Keep the generic model picker and the provider-specific setting in lockstep. */
export function voiceActiveModelPatch(
  draft: VoicePlatformSettingsDraft,
  model: string,
): Partial<VoicePlatformSettingsDraft> {
  const provider = ttsProviderSpec(draft.provider, draft.providerCatalog);
  if (!provider?.activeModelParameterId) return { model };
  return {
    model,
    providerParameters: {
      ...draft.providerParameters,
      [provider.activeModelParameterId]: model,
    },
  };
}

export function createVoicePlatformSettingsDraft(
  studio: Pick<VoiceStudioView, "configuredModelLabel">
    & Partial<Pick<VoiceStudioView, "providerCatalog" | "providerLabel">>,
  parameters: Readonly<Record<string, string>> = {},
  routingSettings?: Pick<SettingsView, "defaultProfileId" | "modelProfiles" | "routingGroups">,
): VoicePlatformSettingsDraft {
  const v = (id: string, fallback: string) => parameters[id] ?? fallback;
  const b = (id: string, fallback: string) => v(id, fallback) === "true";
  // The studio projection carries the engine's authoritative provider id
  // for the active session. When the host just switched platforms via the
  // top-bar dropdown, the persisted settings may still echo the prior
  // tts-provider (mock fixtures, slow sidecar, etc.), so prefer the
  // studio value whenever the two diverge.
  const providerCatalog = studio.providerCatalog ?? [];
  const persistedProvider = parameters["tts-provider"];
  const resolvedProvider = ttsProviderKeyFromLabel(
    studio.providerLabel,
    providerCatalog,
    persistedProvider ?? providerCatalog[0]?.id ?? "minimax",
  );
  const providerParameters: Record<string, string> = {};
  for (const provider of providerCatalog) {
    if (provider.activeModelParameterId) {
      providerParameters[provider.activeModelParameterId] = v(
        provider.activeModelParameterId,
        provider.id === resolvedProvider && studio.configuredModelLabel
          ? normalizeVoicePlatformModel(studio.configuredModelLabel)
          : provider.defaultModel,
      );
    }
    for (const setting of provider.settings) {
      providerParameters[setting.parameterId] = setting.kind === "secret"
        ? ""
        : v(setting.parameterId, setting.defaultValue);
    }
  }
  const activeProvider = ttsProviderSpec(resolvedProvider, providerCatalog);
  const activeModel = normalizeVoicePlatformModel(
    activeProvider?.activeModelParameterId
      ? providerParameters[activeProvider.activeModelParameterId] ?? activeProvider.defaultModel
      : studio.configuredModelLabel || activeProvider?.defaultModel || v("tts-model", ""),
  );
  return {
    enabled: b("tts-enabled", "false"),
    provider: resolvedProvider,
    automationMode: v("tts-automation-mode", "assisted") as VoicePlatformSettingsDraft["automationMode"],
    audioAcceleratorPreference: v("audio-accelerator-preference", "auto") as VoicePlatformSettingsDraft["audioAcceleratorPreference"],
    audioDualAlignmentValidation: b("audio-dual-alignment-validation", "false"),
    audioLocationPolicy: v("audio-location-policy", "hybrid") as VoicePlatformSettingsDraft["audioLocationPolicy"],
    audioMemoryBudget: v("audio-memory-budget", "high") as VoicePlatformSettingsDraft["audioMemoryBudget"],
    audioMfaCommand: v("audio-mfa-command", "mfa"),
    audioPluginManifestDirs: v("audio-plugin-manifest-dirs", ""),
    audioPluginOverrides: v("audio-plugin-overrides", "{}"),
    audioQualityPreset: v("audio-quality-preset", "production") as VoicePlatformSettingsDraft["audioQualityPreset"],
    audioQwen3AsrApiKey: "",
    audioQwen3AsrBaseUrl: v("audio-qwen3-asr-base-url", "http://127.0.0.1:8012/v1"),
    audioSherpaBaseUrl: v("audio-sherpa-base-url", "http://127.0.0.1:8014/v1"),
    audioWhisperxBaseUrl: v("audio-whisperx-base-url", "http://127.0.0.1:8013/v1"),
    concurrency: v("tts-concurrency", v("tts-max-concurrent", "4")),
    localModelResourceBudget: v("local-model-resource-budget", "medium") as VoicePlatformSettingsDraft["localModelResourceBudget"],
    localModelResourceWaitTimeoutS: v("local-model-resource-wait-timeout-s", "900"),
    model: activeModel,
    providerCatalog,
    providerParameters,
    ttsAlignmentRepairRounds: v("tts-alignment-repair-rounds", "1"),
    ttsAudioQualityTier: v("tts-audio-quality-tier", "standard") as VoicePlatformSettingsDraft["ttsAudioQualityTier"],
    ttsMasterQualityGateBlocking: b("tts-master-quality-gate-blocking", "true"),
    ttsMaxTextErrorRate: v("tts-max-text-error-rate", "0.12"),
    ttsSubtitleWordLevel: b("tts-subtitle-word-level", "false"),
    soundReuse: v("tts-voice-library-scope", "project") as VoicePlatformSettingsDraft["soundReuse"],
    postArchiveRetryEnabled: b("tts-post-archive-retry-enabled", "true"),
    postArchiveRetryDelayS: v("tts-post-archive-retry-delay-s", "60"),
    voiceDesignEnabled: b("tts-voice-design-enabled", "false"),
    voiceSemanticMatchingEnabled: b("tts-voice-semantic-matching-enabled", "true"),
    voiceSemanticTopK: v("tts-voice-semantic-top-k", "24"),
    monthlyBudgetUsd: v("tts-monthly-cost-budget-usd", "0"),
    bookBudgetUsd: v("tts-book-cost-budget-usd", "0"),
    chapterBudgetUsd: v("audio-budget-limit-usd", "0"),
    projectStorageMb: v("tts-project-max-storage-mb", "1024"),
    // Output & runtime
    backgroundPipelineConcurrency: v("tts-background-pipeline-concurrency", "1"),
    scriptBatchConcurrency: v("tts-script-max-concurrent-batches", "2"),
    rateLimitRpm: v("tts-synthesis-rpm", "45"),
    rateLimitCooldownS: v("tts-rate-limit-cooldown-s", "60"),
    retryLimit: v("tts-synthesis-retry-limit", "3"),
    outputFormat: v("tts-output-format", "mp3"),
    sampleRate: v("tts-sample-rate", "32000"),
    cloneTtlDays: v("tts-voice-clone-ttl-days", "7"),
    parallelClone: b("tts-parallel-voice-clone", "true"),
    // Strategy extras
    defaultSpeed: v("tts-speed", v("tts-default-speed", "1.0")),
    autoTriggerAfterChapter: b("tts-auto-trigger-after-chapter", "false"),
    narratorVoiceId: v("tts-narrator-voice-id", ""),
    // Sound design workflow
    soundGenerationEnabled: b("sound-generation-enabled", "false"),
    soundAutoGenerate: b("sound-generation-auto-generate", "false"),
    soundAutoApprove: b("sound-generation-auto-approve", "false"),
    soundDesignEnabled: b("tts-sound-design-enabled", "true"),
    // Text intelligence
    scriptLlmReviewEnabled: b("tts-script-llm-review-enabled", "true"),
    scriptLlmReviewMaxTokens: v("tts-script-llm-review-max-output-tokens", "8192"),
    voiceLlmAdjudicationEnabled: b("tts-voice-llm-adjudication-enabled", "false"),
    voiceLlmMinScore: v("tts-voice-llm-adjudication-min-match-score", "0.6"),
    voiceLlmMaxCandidates: v("tts-voice-llm-adjudication-max-candidates", "4"),
    voiceLlmChoicesPerChar: v("tts-voice-llm-adjudication-choices-per-character", "3"),
    voiceLlmAutoSelectScore: v("tts-voice-llm-adjudication-auto-select-score", "0.85"),
    voiceLlmMaxTokens: v("tts-voice-llm-adjudication-max-output-tokens", "1024"),
    ttsScriptTemperature: v("tts-script-generation-temperature", "0.2"),
    scriptGenerationTopP: v("tts-script-generation-top-p", "0.95"),
    ttsReviewTemperature: v("tts-review-adjudication-temperature", "0.0"),
    ttsNarratorTemperature: v("tts-narrator-profile-temperature", "0.2"),
    ttsSoundDesignTemperature: v("tts-sound-design-temperature", "0.5"),
    soundDesignTopP: v("tts-sound-design-top-p", "0.95"),
    textRoutes: createVoiceTextRouteDrafts(routingSettings),
  };
}

export function voicePlatformSettingsEqual(
  left: VoicePlatformSettingsDraft,
  right: VoicePlatformSettingsDraft,
): boolean {
  return Object.keys(left).every(
    (key) => {
      const field = key as keyof VoicePlatformSettingsDraft;
      return field === "textRoutes" || field === "providerParameters"
        ? JSON.stringify(left[field]) === JSON.stringify(right[field])
        : field === "providerCatalog"
          ? true
        : left[field] === right[field];
    },
  );
}

/** Translate the rendered Voice Studio controls to the Engine's guarded save schema. */
export function voicePlatformSettingsToCreationParameters(
  draft: VoicePlatformSettingsDraft,
): Readonly<Record<string, string>> {
  const providerParameters = Object.fromEntries(
    Object.entries(draft.providerParameters).filter(([parameterId, value]) => {
      const setting = draft.providerCatalog
        .flatMap((provider) => provider.settings)
        .find((candidate) => candidate.parameterId === parameterId);
      return setting?.kind !== "secret" || value.trim().length > 0;
    }),
  );
  const rememberedDefaultModel = providerParameters["tts-model"] ?? draft.model;
  return {
    ...providerParameters,
    "tts-enabled": String(draft.enabled),
    "tts-provider": draft.provider,
    "tts-model": rememberedDefaultModel,
    "tts-automation-mode": draft.automationMode,
    "tts-concurrency": draft.concurrency,
    "tts-voice-library-scope": draft.soundReuse,
    "tts-post-archive-retry-enabled": String(draft.postArchiveRetryEnabled),
    "tts-post-archive-retry-delay-s": draft.postArchiveRetryDelayS,
    "tts-voice-design-enabled": String(draft.voiceDesignEnabled),
    "tts-voice-semantic-matching-enabled": String(draft.voiceSemanticMatchingEnabled),
    "tts-voice-semantic-top-k": draft.voiceSemanticTopK,
    "tts-monthly-cost-budget-usd": draft.monthlyBudgetUsd,
    "tts-book-cost-budget-usd": draft.bookBudgetUsd,
    "audio-budget-limit-usd": draft.chapterBudgetUsd,
    "tts-project-max-storage-mb": draft.projectStorageMb,
    "audio-quality-preset": draft.audioQualityPreset,
    "audio-location-policy": draft.audioLocationPolicy,
    "audio-accelerator-preference": draft.audioAcceleratorPreference,
    "local-model-resource-budget": draft.localModelResourceBudget,
    "local-model-resource-wait-timeout-s": draft.localModelResourceWaitTimeoutS,
    "audio-memory-budget": draft.audioMemoryBudget,
    "audio-plugin-overrides": draft.audioPluginOverrides,
    "audio-dual-alignment-validation": String(draft.audioDualAlignmentValidation),
    "audio-qwen3-asr-base-url": draft.audioQwen3AsrBaseUrl,
    ...(draft.audioQwen3AsrApiKey.trim() ? { "audio-qwen3-asr-api-key": draft.audioQwen3AsrApiKey.trim() } : {}),
    "audio-whisperx-base-url": draft.audioWhisperxBaseUrl,
    "audio-sherpa-base-url": draft.audioSherpaBaseUrl,
    "audio-mfa-command": draft.audioMfaCommand,
    "audio-plugin-manifest-dirs": draft.audioPluginManifestDirs,
    "tts-alignment-repair-rounds": draft.ttsAlignmentRepairRounds,
    "tts-audio-quality-tier": draft.ttsAudioQualityTier,
    "tts-max-text-error-rate": draft.ttsMaxTextErrorRate,
    "tts-master-quality-gate-blocking": String(draft.ttsMasterQualityGateBlocking),
    "tts-subtitle-word-level": String(draft.ttsSubtitleWordLevel),
    // Output & runtime
    "tts-background-pipeline-concurrency": draft.backgroundPipelineConcurrency,
    "tts-script-max-concurrent-batches": draft.scriptBatchConcurrency,
    "tts-synthesis-rpm": draft.rateLimitRpm,
    "tts-rate-limit-cooldown-s": draft.rateLimitCooldownS,
    "tts-synthesis-retry-limit": draft.retryLimit,
    "tts-output-format": draft.outputFormat,
    "tts-sample-rate": draft.sampleRate,
    "tts-voice-clone-ttl-days": draft.cloneTtlDays,
    "tts-parallel-voice-clone": String(draft.parallelClone),
    // Strategy extras
    "tts-speed": draft.defaultSpeed,
    "tts-auto-trigger-after-chapter": String(draft.autoTriggerAfterChapter),
    "tts-narrator-voice-id": draft.narratorVoiceId,
    // Sound design
    "sound-generation-enabled": String(draft.soundGenerationEnabled),
    "sound-generation-auto-generate": String(draft.soundAutoGenerate),
    "sound-generation-auto-approve": String(draft.soundAutoApprove),
    "tts-sound-design-enabled": String(draft.soundDesignEnabled),
    // Text intelligence
    "tts-script-llm-review-enabled": String(draft.scriptLlmReviewEnabled),
    "tts-script-llm-review-max-output-tokens": draft.scriptLlmReviewMaxTokens,
    "tts-voice-llm-adjudication-enabled": String(draft.voiceLlmAdjudicationEnabled),
    "tts-voice-llm-adjudication-min-match-score": draft.voiceLlmMinScore,
    "tts-voice-llm-adjudication-max-candidates": draft.voiceLlmMaxCandidates,
    "tts-voice-llm-adjudication-choices-per-character": draft.voiceLlmChoicesPerChar,
    "tts-voice-llm-adjudication-auto-select-score": draft.voiceLlmAutoSelectScore,
    "tts-voice-llm-adjudication-max-output-tokens": draft.voiceLlmMaxTokens,
    "tts-script-generation-temperature": draft.ttsScriptTemperature,
    "tts-script-generation-top-p": draft.scriptGenerationTopP,
    "tts-review-adjudication-temperature": draft.ttsReviewTemperature,
    "tts-narrator-profile-temperature": draft.ttsNarratorTemperature,
    "tts-sound-design-temperature": draft.ttsSoundDesignTemperature,
    "tts-sound-design-top-p": draft.soundDesignTopP,
  };
}

interface VoicePlatformSettingsProps {
  readonly draft: VoicePlatformSettingsDraft;
  readonly modelCenterClient: VoiceAudioModelCenterClient;
  /**
   * Counter that, when bumped, forces the embedded `VoiceLocalModelCenter`
   * to re-pull its catalog view. The host bumps it after the top-bar
   * provider switch forces the engine to refresh sidecar state.
   */
  readonly modelCenterRefreshToken?: number;
  readonly onChange: (patch: Partial<VoicePlatformSettingsDraft>) => void;
  readonly routingSettings?: Pick<SettingsView, "defaultProfileId" | "modelProfiles" | "routingGroups"> | undefined;
  readonly studio: Pick<
    VoiceStudioView,
    "configuredModelLabel" | "providerCatalog" | "providerLabel"
  >;
}

interface VoiceSettingsSectionProps {
  readonly children: ReactNode;
  readonly detail: string;
  readonly expanded: boolean;
  readonly id: VoiceSettingsSectionId;
  readonly onToggle: (id: VoiceSettingsSectionId) => void;
  readonly summary?: string;
  readonly title: string;
}

const sectionLabels: Readonly<Record<VoiceSettingsSectionId, string>> = {
  "local-models": "本机音频模型中心",
  "quality-routing": "质量目标与模型计划",
  platform: "连接与模型",
  credentials: "平台凭据与专属参数",
  strategy: "通用合成策略",
  "output-runtime": "合成性能与输出",
  "sound-design": "声音资产生成 — 环境声、SFX 与 BGM",
  "text-intelligence": "文本智能 — LLM 路由与温度",
};

const defaultExpandedSections: ReadonlySet<VoiceSettingsSectionId> = new Set([
  "local-models",
  "quality-routing",
  "platform",
  "credentials",
  "text-intelligence",
]);

function VoiceSettingsSection({ children, detail, expanded, id, onToggle, summary, title }: VoiceSettingsSectionProps) {
  const contentId = `voice-settings-${id}`;
  return (
    <section className="voice-settings-category">
      <header><h2>{title}</h2><p>{detail}</p></header>
      <div className="voice-settings-disclosure">
        <button aria-controls={contentId} aria-expanded={expanded} className="voice-settings-disclosure-toggle" onClick={() => onToggle(id)} type="button">
          <span aria-hidden="true">{expanded ? "\u2304" : "\u203A"}</span>
          <strong>{summary ?? sectionLabels[id]}</strong>
        </button>
        {expanded && <div className="voice-settings-disclosure-body" id={contentId}>{children}</div>}
      </div>
    </section>
  );
}

function Row({ children, detail, label }: { readonly children: ReactNode; readonly detail: string; readonly label: string }) {
  return (
    <label className="voice-platform-setting-row">
      <span><strong>{label}</strong><small>{detail}</small></span>
      {children}
    </label>
  );
}

/**
 * Shared model picker used by both the top-level "当前平台模型" row and the
 * dashscope credential group's "正式人声模型" row. Platforms with a
 * curated catalog render it; free-form platforms fall back to a plain text
 * input so custom/self-hosted model ids stay editable.
 */
function PlatformModelSelect({
  ariaLabel,
  catalog,
  onChange,
  purpose = "formal",
  provider,
  value,
}: {
  readonly ariaLabel: string;
  readonly catalog: readonly VoiceProviderView[];
  readonly onChange: (value: string) => void;
  readonly purpose?: "formal" | "preview" | "clone" | "design";
  readonly provider: string;
  readonly value: string;
}) {
  const modelOptions = platformModelValues(provider, catalog, purpose);
  const normalized = normalizeVoicePlatformModel(value);
  if (modelOptions.length === 0) {
    return <input aria-label={ariaLabel} onChange={(e) => onChange(e.target.value)} value={value} />;
  }
  const known = modelOptions.some((option) => option.value === normalized);
  return (
    <select aria-label={ariaLabel} onChange={(e) => onChange(e.target.value)} value={normalized}>
      {!normalized ? <option value="">随正式模型</option> : null}
      {!known && normalized ? <option value={normalized}>{normalized}（当前）</option> : null}
      {modelOptions.map((option) => (
        <option key={option.value} value={option.value}>{option.label}</option>
      ))}
    </select>
  );
}

function settingIsVisible(
  setting: VoiceProviderSettingView,
  draft: VoicePlatformSettingsDraft,
): boolean {
  if (setting.visibleModelIds.length > 0 && !setting.visibleModelIds.includes(draft.model)) {
    return false;
  }
  if (setting.visibilityParameterId) {
    const value = setting.visibilityParameterId === "tts-output-format"
      ? draft.outputFormat
      : draft.providerParameters[setting.visibilityParameterId] ?? "";
    if (!setting.visibilityValues.includes(value)) return false;
  }
  return true;
}

function ProviderSettingControl({
  draft,
  onChange,
  provider,
  setting,
}: {
  readonly draft: VoicePlatformSettingsDraft;
  readonly onChange: (parameterId: string, value: string) => void;
  readonly provider: VoiceProviderView;
  readonly setting: VoiceProviderSettingView;
}) {
  const value = draft.providerParameters[setting.parameterId] ?? setting.defaultValue;
  if (setting.modelPurpose) {
    return (
      <PlatformModelSelect
        ariaLabel={setting.label}
        catalog={draft.providerCatalog}
        onChange={(next) => onChange(setting.parameterId, next)}
        provider={provider.id}
        purpose={setting.modelPurpose}
        value={value}
      />
    );
  }
  if (setting.kind === "boolean") {
    return (
      <input
        aria-label={setting.label}
        checked={value === "true"}
        onChange={(event) => onChange(setting.parameterId, String(event.target.checked))}
        type="checkbox"
      />
    );
  }
  if (setting.kind === "select") {
    return (
      <select
        aria-label={setting.label}
        onChange={(event) => onChange(setting.parameterId, event.target.value)}
        value={value}
      >
        {setting.options.map((option) => (
          <option key={option.value} value={option.value}>{option.label}</option>
        ))}
      </select>
    );
  }
  return (
    <input
      aria-label={setting.label}
      autoComplete={setting.kind === "secret" ? "off" : undefined}
      max={setting.maximum ?? undefined}
      min={setting.minimum ?? undefined}
      onChange={(event) => onChange(setting.parameterId, event.target.value)}
      placeholder={setting.kind === "secret" ? "留空则保留已配置凭据" : undefined}
      step={setting.step ?? undefined}
      type={setting.kind === "secret" ? "password" : setting.kind === "number" ? "number" : "text"}
      value={value}
    />
  );
}

const VOICE_SETTINGS_STORAGE_KEY = "nimo:voice:expandedSections";

function loadExpandedSections(): ReadonlySet<VoiceSettingsSectionId> {
  try {
    const raw = localStorage.getItem(VOICE_SETTINGS_STORAGE_KEY);
    if (raw) {
      const arr = JSON.parse(raw) as string[];
      return new Set(arr.filter((s): s is VoiceSettingsSectionId => s in sectionLabels));
    }
  } catch { /* ignore */ }
  return new Set(defaultExpandedSections);
}

export function VoicePlatformSettings({
  draft,
  modelCenterClient,
  modelCenterRefreshToken,
  onChange,
  routingSettings,
  studio,
}: VoicePlatformSettingsProps) {
  const [expanded, setExpanded] = useState<ReadonlySet<VoiceSettingsSectionId>>(loadExpandedSections);
  useEffect(() => {
    try { localStorage.setItem(VOICE_SETTINGS_STORAGE_KEY, JSON.stringify([...expanded])); } catch { /* ignore */ }
  }, [expanded]);
  const toggle = (id: VoiceSettingsSectionId) => {
    setExpanded((current) => {
      const next = new Set(current);
      if (next.has(id)) next.delete(id); else next.add(id);
      return next;
    });
  };
  // The host bumps `modelCenterRefreshToken` after a top-bar provider
  // switch; reuse that single signal to reveal the credential panel so the
  // new platform's fields and capability hints are immediately visible.
  const providerSwitchSkipRef = useRef(true);
  useEffect(() => {
    if (providerSwitchSkipRef.current) {
      providerSwitchSkipRef.current = false;
      return;
    }
    setExpanded((current) => (current.has("credentials") ? current : new Set([...current, "credentials"])));
  }, [modelCenterRefreshToken]);
  const isExpanded = (id: VoiceSettingsSectionId) => expanded.has(id);
  // Use the shared provider label helper so the platform name shown in
  // section summaries always matches the top-bar dropdown text.
  const activeProvider = ttsProviderSpec(draft.provider, draft.providerCatalog);
  const platformName = ttsProviderLabelForId(draft.provider, draft.providerCatalog);
  const providerOptions = ttsProviderOptions(draft.providerCatalog, draft.provider);
  const activeModel = activeProvider?.models.find((model) => model.id === draft.model);
  const providerSettings = (activeProvider?.settings ?? []).filter((setting) =>
    settingIsVisible(setting, draft)
  );
  const unavailableNativeModels = (activeProvider?.models ?? []).filter(
    (model) => !model.adapterSupported,
  );
  const commercialReadiness = voiceCommercialReadinessChecks(draft);
  const commercialReadyCount = commercialReadiness.filter((check) => check.ready).length;
  const routeProfiles = routingSettings ? routeableVoiceProfiles(routingSettings) : [];
  const changeTextRoute = (
    taskKey: VoiceTextRouteTaskKey,
    patch: Partial<VoiceTextRouteDrafts[VoiceTextRouteTaskKey]>,
  ) => {
    onChange({
      textRoutes: {
        ...draft.textRoutes,
        [taskKey]: {
          ...draft.textRoutes[taskKey],
          ...patch,
        },
      },
    });
  };
  const changeProviderParameter = (parameterId: string, value: string) => {
    onChange({
      providerParameters: {
        ...draft.providerParameters,
        [parameterId]: value,
      },
    });
  };

  return (
    <div className="voice-settings" aria-label="平台设置">
      {/* ── 1. 本机模型中心 ── */}
      <VoiceSettingsSection detail="下载、校验、删除所有项目共享的本地模型，并检查独立 sidecar 运行时。" expanded={isExpanded("local-models")} id="local-models" onToggle={toggle} title="本机模型中心">
        <VoiceLocalModelCenter client={modelCenterClient} refreshToken={modelCenterRefreshToken ?? 0} />
      </VoiceSettingsSection>

      {/* ── 2. 质量与路由 ── */}
      <VoiceSettingsSection detail="以成片目标组织模型组合，并保留每个平台的原生字段和专属运行参数。" expanded={isExpanded("quality-routing")} id="quality-routing" onToggle={toggle} title="质量与路由">
        <section aria-label="商业成片就绪度" className="voice-commercial-readiness">
          <header>
            <span>
              <strong>商业成片基线</strong>
              <small>{commercialReadyCount}/{commercialReadiness.length} 项已就绪</small>
            </span>
            <button
              className="button button-secondary"
              disabled={commercialReadyCount === commercialReadiness.length}
              onClick={() => onChange(commercialVoiceSettingsPatch(draft))}
              type="button"
            >
              {commercialReadyCount === commercialReadiness.length ? "基线已就绪" : "应用商业基线"}
            </button>
          </header>
          <ul>
            {commercialReadiness.map((check) => (
              <li className={check.ready ? "is-ready" : "is-pending"} key={check.id}>
                <span aria-hidden="true">{check.ready ? "✓" : "!"}</span>
                <span><strong>{check.label}</strong><small>{check.detail}</small></span>
              </li>
            ))}
          </ul>
          <p>此处只配置技术基线；每个 BGM、环境声和 SFX 仍须在声音资源库逐项确认商业使用权。</p>
        </section>
        <Row detail="普通用户只需选择目标；自定义模式可在下方固定各阶段插件。" label="质量预设">
          <select aria-label="质量预设" onChange={(e) => onChange({ audioQualityPreset: e.target.value as VoicePlatformSettingsDraft["audioQualityPreset"] })} value={draft.audioQualityPreset}>
            <option value="quick_preview">快速试听</option><option value="production">均衡成片</option><option value="master">极致精校</option><option value="low_resource">低资源设备</option><option value="custom">自定义</option>
          </select>
        </Row>
        <Row detail={'与质量预设独立；"全部本地"不会静默回退到云端。'} label="运行位置策略">
          <select aria-label="运行位置策略" onChange={(e) => onChange({ audioLocationPolicy: e.target.value as VoicePlatformSettingsDraft["audioLocationPolicy"] })} value={draft.audioLocationPolicy}>
            <option value="hybrid">混合（推荐）</option><option value="prefer_cloud">优先云端</option><option value="prefer_local">优先本地</option><option value="cloud_only">仅云端</option><option value="local_only">仅本地</option>
          </select>
        </Row>
        <Row detail="只影响本地模型排序；无法满足时会在计划中报告回退。" label="加速器偏好">
          <select aria-label="加速器偏好" onChange={(e) => onChange({ audioAcceleratorPreference: e.target.value as VoicePlatformSettingsDraft["audioAcceleratorPreference"] })} value={draft.audioAcceleratorPreference}>
            <option value="auto">自动检测</option><option value="mps">Apple MPS</option><option value="cuda">NVIDIA CUDA</option><option value="cpu">CPU</option>
          </select>
        </Row>
        <Row detail="同时约束本地 TTS、ASR、对齐、声音生成和后处理。" label="全局本机资源预算">
          <select aria-label="全局本机资源预算" onChange={(e) => onChange({ localModelResourceBudget: e.target.value as VoicePlatformSettingsDraft["localModelResourceBudget"] })} value={draft.localModelResourceBudget}>
            <option value="light">轻量（单重任务）</option><option value="medium">均衡（默认）</option><option value="high">宽裕（允许轻模型并行）</option>
          </select>
        </Row>
        <Row detail="超时会报告正在等待的本地负载，不会无限卡住。" label="本机任务最长排队（秒）">
          <input aria-label="本机任务最长排队（秒）" max="7200" min="30" onChange={(e) => onChange({ localModelResourceWaitTimeoutS: e.target.value })} type="number" value={draft.localModelResourceWaitTimeoutS} />
        </Row>
        <Row detail="用于能力计划器筛选音频模型；运行并发仍受全局资源预算限制。" label="音频选型内存上限">
          <select aria-label="音频选型内存上限" onChange={(e) => onChange({ audioMemoryBudget: e.target.value as VoicePlatformSettingsDraft["audioMemoryBudget"] })} value={draft.audioMemoryBudget}>
            <option value="light">轻量（约 8-12 GB）</option><option value="medium">均衡（16 GB）</option><option value="high">宽裕（24 GB+）</option>
          </select>
        </Row>
        <Row detail="正式时间线由不同实现独立复核；极致精校预设会自动开启。" label="双对齐器复核">
          <input aria-label="双对齐器复核" checked={draft.audioDualAlignmentValidation} onChange={(e) => onChange({ audioDualAlignmentValidation: e.target.checked })} type="checkbox" />
        </Row>
        <h3 className="voice-settings-subheading">闭环质量门</h3>
        <Row detail="只重生成有失配证据的片段，旧 take 会保留为回滚锚点。" label="人声自动修复轮数">
          <input aria-label="人声自动修复轮数" max="3" min="0" onChange={(e) => onChange({ ttsAlignmentRepairRounds: e.target.value })} type="number" value={draft.ttsAlignmentRepairRounds} />
        </Row>
        <Row detail="超过阈值的片段进入有界修复；0.12 表示 12%。" label="最大转写错误率">
          <input aria-label="最大转写错误率" max="1" min="0" onChange={(e) => onChange({ ttsMaxTextErrorRate: e.target.value })} step="0.01" type="number" value={draft.ttsMaxTextErrorRate} />
        </Row>
        <Row detail="未达削波、对齐覆盖或转写质量标准时保留产物供检查，但不标记为正式成片。" label="极致精校硬门">
          <input aria-label="极致精校硬门" checked={draft.ttsMasterQualityGateBlocking} onChange={(e) => onChange({ ttsMasterQualityGateBlocking: e.target.checked })} type="checkbox" />
        </Row>
        <Row detail="试听只做单遍母带；标准做两遍响度；商业档强制跨章节响度、峰值与混音风险门。" label="交付质量档">
          <select aria-label="交付质量档" onChange={(e) => onChange({ ttsAudioQualityTier: e.target.value as VoicePlatformSettingsDraft["ttsAudioQualityTier"] })} value={draft.ttsAudioQualityTier}>
            <option value="audition">试听（最快）</option><option value="standard">标准成片（推荐）</option><option value="commercial">商业交付（最严）</option>
          </select>
        </Row>
        <Row detail="开启后导出字词级 SRT；MiniMax 同时请求原生 word subtitle，其他平台使用 ASR 对齐。" label="字词级字幕">
          <input aria-label="字词级字幕" checked={draft.ttsSubtitleWordLevel} onChange={(e) => onChange({ ttsSubtitleWordLevel: e.target.checked })} type="checkbox" />
        </Row>
        <h3 className="voice-settings-subheading">阶段覆盖</h3>
        <Row detail="JSON 对象：执行阶段到插件 ID；保留 {} 即自动推荐。" label="阶段插件固定">
          <input aria-label="阶段插件固定" onChange={(e) => onChange({ audioPluginOverrides: e.target.value })} value={draft.audioPluginOverrides} />
        </Row>
      </VoiceSettingsSection>

      {/* ── 3. 当前平台 ── */}
      <VoiceSettingsSection detail="配置本次配音所用平台的模型、连接地址、凭据与推理选项。" expanded={isExpanded("platform")} id="platform" onToggle={toggle} summary={`${platformName} — 连接与模型`} title="当前平台">
        <p className="voice-settings-intro">当前平台：{platformName}。平台模型、质量策略和运行时参数会与 PySide 使用同一套受控环境变量保存。</p>
        <Row detail="关闭后章节完成不会进入配音流程；已生成音频和角色音色不会被删除。" label="启用配音模块">
          <input aria-label="启用配音模块" checked={draft.enabled} onChange={(e) => onChange({ enabled: e.target.checked })} type="checkbox" />
        </Row>
        <Row detail="决定人声合成、平台目录、音色设计与声音克隆使用的默认供应商。" label="默认 TTS 平台">
          <select aria-label="默认 TTS 平台" onChange={(e) => onChange(voiceProviderSwitchPatch(draft, e.target.value))} value={draft.provider}>
            {providerOptions.map((option) => (
              <option key={option.value} value={option.value}>{option.label}</option>
            ))}
          </select>
        </Row>
        <Row detail="切换后同时影响片段合成与试听。" label="当前平台模型">
          <PlatformModelSelect
            ariaLabel="当前平台模型"
            catalog={draft.providerCatalog}
            onChange={(value) => onChange(voiceActiveModelPatch(draft, value))}
            provider={draft.provider}
            value={draft.model}
          />
        </Row>
        <h3 className="voice-settings-subheading">独立运行时</h3>
        <Row detail="正式转写与 11 语种已知文本对齐服务。" label="Qwen3-ASR / ForcedAligner">
          <input aria-label="Qwen3-ASR / ForcedAligner" onChange={(e) => onChange({ audioQwen3AsrBaseUrl: e.target.value })} value={draft.audioQwen3AsrBaseUrl} />
        </Row>
        <Row detail="只绑定本机回环地址时可留空；已有令牌不会回显。" label="Qwen3-ASR Sidecar Token">
          <input aria-label="Qwen3-ASR Sidecar Token" autoComplete="off" onChange={(e) => onChange({ audioQwen3AsrApiKey: e.target.value })} placeholder="留空则保留已配置令牌" type="password" value={draft.audioQwen3AsrApiKey} />
        </Row>
        <Row detail="精校复核、语言扩展与 Qwen 对齐器的独立验证。" label="WhisperX 服务">
          <input aria-label="WhisperX 服务" onChange={(e) => onChange({ audioWhisperxBaseUrl: e.target.value })} value={draft.audioWhisperxBaseUrl} />
        </Row>
        <Row detail="快速试听、CPU、VAD 和轻量多语言模型包。" label="Sherpa ONNX 服务">
          <input aria-label="Sherpa ONNX 服务" onChange={(e) => onChange({ audioSherpaBaseUrl: e.target.value })} value={draft.audioSherpaBaseUrl} />
        </Row>
        <Row detail="专业词典精校时使用；普通章节不会自动启动。" label="MFA 命令">
          <input aria-label="MFA 命令" onChange={(e) => onChange({ audioMfaCommand: e.target.value })} value={draft.audioMfaCommand} />
        </Row>
        <Row detail="逗号分隔；只加载 *.audio-plugin.json，不执行目录中的 Python 代码。" label="第三方插件清单目录">
          <input aria-label="第三方插件清单目录" onChange={(e) => onChange({ audioPluginManifestDirs: e.target.value })} value={draft.audioPluginManifestDirs} />
        </Row>
      </VoiceSettingsSection>

      {/* ── 4. 平台凭据与专属参数 ── */}
      <VoiceSettingsSection detail="当前 TTS 平台的 API 密钥、服务地址与平台专属模型路由。" expanded={isExpanded("credentials")} id="credentials" onToggle={toggle} summary={`${platformName} — 凭据与专属参数`} title="平台凭据">
        <p className="voice-settings-intro">凭据输入框保留为空时不会覆盖后端已配置的令牌、域名或路径；如需覆盖，请直接重新输入，与 PySide 6 保留旧值的语义一致。</p>
        {activeProvider ? (
          <>
            <section className="voice-provider-summary">
              <div>
                <strong>{activeProvider.label}</strong>
                <small>{activeProvider.isLocal ? "本地运行时" : "云端平台"} · {activeProvider.adapterStatus}</small>
              </div>
              {activeProvider.officialDocsUrl ? (
                <a href={activeProvider.officialDocsUrl} rel="noreferrer" target="_blank">官方文档</a>
              ) : null}
            </section>
            {activeProvider.highlights.length > 0 ? (
              <ul aria-label={`${platformName} 平台原生能力`} className="voice-settings-capability-hints">
                {activeProvider.highlights.map((hint) => <li key={hint}>{hint}</li>)}
              </ul>
            ) : null}
            {activeModel ? (
              <div className="voice-provider-model-capabilities">
                <strong>{activeModel.label}</strong>
                <span>{[
                  ...activeModel.features,
                  ...(activeModel.voiceClone ? ["voice_clone"] : []),
                  ...(activeModel.voiceDesign ? ["voice_design"] : []),
                  ...(activeModel.systemVoiceCatalog ? ["system_voice_catalog"] : []),
                ].map((feature) => <small key={feature}>{feature}</small>)}</span>
              </div>
            ) : null}
            {providerSettings.map((setting) => (
              <Row
                detail={`${setting.description}${setting.experimental ? "（实验性）" : ""}`}
                key={setting.parameterId}
                label={setting.label}
              >
                <ProviderSettingControl
                  draft={draft}
                  onChange={changeProviderParameter}
                  provider={activeProvider}
                  setting={setting}
                />
              </Row>
            ))}
            {unavailableNativeModels.length > 0 ? (
              <aside className="voice-provider-native-pending">
                <strong>平台原生、待稳定接入</strong>
                {unavailableNativeModels.map((model) => (
                  <p key={model.id}><span>{model.label}</span><small>{model.notes.join("；")}</small></p>
                ))}
              </aside>
            ) : null}
          </>
        ) : (
          <p className="voice-routing-empty">当前 Engine 未返回平台能力目录，请刷新项目后重试。</p>
        )}
      </VoiceSettingsSection>

      {/* ── 5. 项目配音策略 ── */}
      <VoiceSettingsSection detail="配置所有 TTS 平台共用的叙述节奏与章节自动合成行为。" expanded={isExpanded("strategy")} id="strategy" onToggle={toggle} title="项目配音策略">
        <Row detail="保护平台限流与本机资源；同一项目的片段会按此预算排队。" label="最大并发合成数">
          <input aria-label="最大并发合成数" max="16" min="1" onChange={(e) => onChange({ concurrency: e.target.value })} type="number" value={draft.concurrency} />
        </Row>
        <Row detail="控制配音流程的自动执行程度。" label="自动化模式">
          <select aria-label="自动化模式" onChange={(e) => onChange({ automationMode: e.target.value as VoicePlatformSettingsDraft["automationMode"] })} value={draft.automationMode}>
            <option value="manual">手动（每步确认）</option><option value="assisted">半自动（生成后确认）</option><option value="autonomous">全自动（端到端）</option>
          </select>
        </Row>
        <Row detail="未设置片段语速时使用的项目级倍率，试听与正式合成都生效。" label="默认语速">
          <input aria-label="默认语速" max="2.0" min="0.5" onChange={(e) => onChange({ defaultSpeed: e.target.value })} step="0.1" type="number" value={draft.defaultSpeed} />
        </Row>
        <Row detail="章节生成完成后自动开始配音流程。" label="章节完成后自动触发">
          <input aria-label="章节完成后自动触发" checked={draft.autoTriggerAfterChapter} onChange={(e) => onChange({ autoTriggerAfterChapter: e.target.checked })} type="checkbox" />
        </Row>
        <Row detail="自动配音因待确认或音色过期而未完成时，在归档后再尝试一次。" label="失败后延迟重试">
          <input aria-label="失败后延迟重试" checked={draft.postArchiveRetryEnabled} onChange={(e) => onChange({ postArchiveRetryEnabled: e.target.checked })} type="checkbox" />
        </Row>
        <Row detail="自动配音失败后等待多久再重新进入队列。" label="延迟重试秒数">
          <input aria-label="延迟重试秒数" disabled={!draft.postArchiveRetryEnabled} max="600" min="10" onChange={(e) => onChange({ postArchiveRetryDelayS: e.target.value })} type="number" value={draft.postArchiveRetryDelayS} />
        </Row>
        <Row detail="旁白使用的默认音色 ID（留空则使用系统默认）。" label="旁白音色 ID">
          <input aria-label="旁白音色 ID" onChange={(e) => onChange({ narratorVoiceId: e.target.value })} placeholder="例如: male-qingnian-zhiye" value={draft.narratorVoiceId} />
        </Row>
        <h3 className="voice-settings-subheading">音色匹配与费用边界</h3>
        <Row detail="只有音色库与平台目录都没有可靠候选时才调用付费音色设计。" label="允许 AI 音色设计">
          <input aria-label="允许 AI 音色设计" checked={draft.voiceDesignEnabled} onChange={(e) => onChange({ voiceDesignEnabled: e.target.checked })} type="checkbox" />
        </Row>
        <Row detail="使用嵌入向量从已批准音色库召回最相近候选。" label="启用语义音色匹配">
          <input aria-label="启用语义音色匹配" checked={draft.voiceSemanticMatchingEnabled} onChange={(e) => onChange({ voiceSemanticMatchingEnabled: e.target.checked })} type="checkbox" />
        </Row>
        <Row detail="每个角色送入规则与模型复核前的语义候选上限。" label="语义候选 TopK">
          <input aria-label="语义候选 TopK" disabled={!draft.voiceSemanticMatchingEnabled} max="100" min="1" onChange={(e) => onChange({ voiceSemanticTopK: e.target.value })} type="number" value={draft.voiceSemanticTopK} />
        </Row>
        <Row detail="跨项目共享的云端配音月度预算；0 表示只记录、不阻断。" label="月度预算（USD）">
          <input aria-label="月度预算（USD）" max="10000" min="0" onChange={(e) => onChange({ monthlyBudgetUsd: e.target.value })} step="1" type="number" value={draft.monthlyBudgetUsd} />
        </Row>
        <Row detail="按作品累计的云端配音预算；0 表示不设硬上限。" label="单书预算（USD）">
          <input aria-label="单书预算（USD）" max="10000" min="0" onChange={(e) => onChange({ bookBudgetUsd: e.target.value })} step="1" type="number" value={draft.bookBudgetUsd} />
        </Row>
        <Row detail="音频能力计划器用于约束本章云端任务；0 表示不设硬上限。" label="单章预算（USD）">
          <input aria-label="单章预算（USD）" max="1000" min="0" onChange={(e) => onChange({ chapterBudgetUsd: e.target.value })} step="1" type="number" value={draft.chapterBudgetUsd} />
        </Row>
        <Row detail="单个项目 tts/ 目录的总容量上限。" label="项目存储上限（MB）">
          <input aria-label="项目存储上限（MB）" max="20480" min="64" onChange={(e) => onChange({ projectStorageMb: e.target.value })} type="number" value={draft.projectStorageMb} />
        </Row>
      </VoiceSettingsSection>

      {/* ── 6. 输出与运行 ── */}
      <VoiceSettingsSection detail="配置并发、重试、文件格式、采样率与克隆音色的生命周期。" expanded={isExpanded("output-runtime")} id="output-runtime" onToggle={toggle} title="合成性能与输出">
        <Row detail="同时运行的整章配音流水线数；默认 1 让多章排队。" label="后台整章配音并发">
          <input aria-label="后台整章配音并发" max="3" min="1" onChange={(e) => onChange({ backgroundPipelineConcurrency: e.target.value })} type="number" value={draft.backgroundPipelineConcurrency} />
        </Row>
        <Row detail="长章脚本拆批后同时调用文本模型的上限。" label="配音脚本分批并发">
          <input aria-label="配音脚本分批并发" max="4" min="1" onChange={(e) => onChange({ scriptBatchConcurrency: e.target.value })} type="number" value={draft.scriptBatchConcurrency} />
        </Row>
        <Row detail="每分钟最多向云端 TTS 发起的请求数（包含重试）。" label="远程合成 RPM 上限">
          <input aria-label="远程合成 RPM 上限" max="600" min="1" onChange={(e) => onChange({ rateLimitRpm: e.target.value })} type="number" value={draft.rateLimitRpm} />
        </Row>
        <Row detail="服务商返回限流后，暂停整条远程合成队列的时间。" label="限流冷却秒数">
          <input aria-label="限流冷却秒数" max="600" min="5" onChange={(e) => onChange({ rateLimitCooldownS: e.target.value })} type="number" value={draft.rateLimitCooldownS} />
        </Row>
        <Row detail="单个片段合成失败时的最大重试次数。" label="单段重试次数">
          <input aria-label="单段重试次数" max="10" min="0" onChange={(e) => onChange({ retryLimit: e.target.value })} type="number" value={draft.retryLimit} />
        </Row>
        <Row detail="合成音频的输出文件格式。" label="输出格式">
          <select aria-label="输出格式" onChange={(e) => onChange({ outputFormat: e.target.value })} value={draft.outputFormat}>
            <option value="mp3">mp3</option><option value="wav">wav</option><option value="flac">flac</option><option value="pcm">pcm</option>
          </select>
        </Row>
        <Row detail="合成音频的采样率（Hz）。" label="采样率">
          <select aria-label="采样率" onChange={(e) => onChange({ sampleRate: e.target.value })} value={draft.sampleRate}>
            <option value="16000">16000</option><option value="22050">22050</option><option value="24000">24000</option><option value="32000">32000</option><option value="44100">44100</option><option value="48000">48000</option>
          </select>
        </Row>
        <Row detail="克隆音色的有效天数，过期后需重新克隆。" label="克隆音色有效期（天）">
          <input aria-label="克隆音色有效期（天）" max="30" min="1" onChange={(e) => onChange({ cloneTtlDays: e.target.value })} type="number" value={draft.cloneTtlDays} />
        </Row>
        <Row detail="Init 阶段是否并行执行多个角色的音色克隆。" label="并行克隆">
          <input aria-label="并行克隆" checked={draft.parallelClone} onChange={(e) => onChange({ parallelClone: e.target.checked })} type="checkbox" />
        </Row>
      </VoiceSettingsSection>

      {/* ── 7. 声音设计 ── */}
      <VoiceSettingsSection detail="管理环境声、短音效与背景音乐的生成模型、目录与复用策略。" expanded={isExpanded("sound-design")} id="sound-design" onToggle={toggle} title="声音设计">
        <Row detail="已批准的声音资产可在相同边界内复用。" label="声音资产复用">
          <select aria-label="声音资产复用" onChange={(e) => onChange({ soundReuse: e.target.value as VoicePlatformSettingsDraft["soundReuse"] })} value={draft.soundReuse}>
            <option value="project">仅当前项目</option><option value="application">已批准后发布到应用库</option><option value="off">不自动复用</option>
          </select>
        </Row>
        <Row detail="开启后，配音流程会处理 BGM、环境声与 SFX 提示。" label="启用生成式声音">
          <input aria-label="启用生成式声音" checked={draft.soundGenerationEnabled} onChange={(e) => onChange({ soundGenerationEnabled: e.target.checked })} type="checkbox" />
        </Row>
        <Row detail="素材库未匹配时自动生成；关闭时只报告缺失声音。" label="自动生成缺失素材">
          <input aria-label="自动生成缺失素材" checked={draft.soundAutoGenerate} onChange={(e) => onChange({ soundAutoGenerate: e.target.checked })} type="checkbox" />
        </Row>
        <Row detail="关闭后，生成资产会保留为待审核，不会自动进入章节混音。" label="自动进入混音">
          <input aria-label="自动进入混音" checked={draft.soundAutoApprove} onChange={(e) => onChange({ soundAutoApprove: e.target.checked })} type="checkbox" />
        </Row>
        <Row detail="表演脚本定稿后提取音效、BGM 与环境声需求。" label="声音设计提取">
          <input aria-label="声音设计提取" checked={draft.soundDesignEnabled} onChange={(e) => onChange({ soundDesignEnabled: e.target.checked })} type="checkbox" />
        </Row>
        <h3 className="voice-settings-subheading">Stable Audio 模型</h3>
        <p className="voice-settings-intro">环境声与短音效的本地生成模型（Stable Audio Small-SFX）在上方「本机模型中心」统一管理：下载、校验、删除与 sidecar 健康检查均在该区域完成。</p>
        <Row detail="声音资产生成时的采样参数；降低会更保守，提高会更发散。" label="声音设计 Top-P">
          <input aria-label="声音设计 Top-P" max="1" min="0" onChange={(e) => onChange({ soundDesignTopP: e.target.value })} step="0.05" type="number" value={draft.soundDesignTopP} />
        </Row>
      </VoiceSettingsSection>

      {/* ── 8. 文本智能 ── */}
      <VoiceSettingsSection detail="为配音脚本生成、说话人复核、角色音色复核与旁白画像选择文本模型与温度。" expanded={isExpanded("text-intelligence")} id="text-intelligence" onToggle={toggle} title="文本智能">
        <h3 className="voice-settings-subheading">TTS 文本模型路由</h3>
        <p className="voice-settings-intro">这里是配音文本任务的唯一模型路由入口。保存后只影响新任务，不会中断正在运行的脚本生成或合成。</p>
        {routeProfiles.length > 0 ? (
          <div className="voice-text-route-list">
            {voiceTextRouteDefinitions.map((definition) => {
              const route = draft.textRoutes[definition.taskKey];
              const fallbackSlots = Array.from({ length: 3 }, (_, index) => route.fallbackProfileIds[index] ?? "");
              return (
                <section className="voice-text-route-card" key={definition.taskKey}>
                  <header>
                    <div>
                      <strong>{definition.label}</strong>
                      <small>{definition.hint}</small>
                    </div>
                    <label>
                      <span>Temperature</span>
                      <input
                        aria-label={`${definition.label} Temperature`}
                        max="1"
                        min="0"
                        onChange={(event) => onChange({ [definition.temperatureKey]: event.target.value })}
                        step="0.05"
                        type="number"
                        value={draft[definition.temperatureKey]}
                      />
                    </label>
                  </header>
                  <div className="voice-text-route-controls">
                    <label>
                      <span>主模型</span>
                      <select
                        aria-label={`${definition.label} 主模型`}
                        onChange={(event) => changeTextRoute(definition.taskKey, { primaryProfileId: event.target.value })}
                        value={route.primaryProfileId}
                      >
                        {routeProfiles.map((profile) => (
                          <option key={profile.id} value={profile.id}>{profile.label}</option>
                        ))}
                      </select>
                    </label>
                    {fallbackSlots.map((profileId, index) => (
                      <label key={`${definition.taskKey}-fallback-${index}`}>
                        <span>{`后备 ${index + 1}`}</span>
                        <select
                          aria-label={`${definition.label} 后备模型 ${index + 1}`}
                          onChange={(event) => {
                            const next = [...fallbackSlots];
                            next[index] = event.target.value;
                            changeTextRoute(definition.taskKey, {
                              fallbackProfileIds: next.filter(Boolean),
                            });
                          }}
                          value={profileId}
                        >
                          <option value="">不设置</option>
                          {routeProfiles
                            .filter((profile) => profile.id !== route.primaryProfileId)
                            .map((profile) => (
                              <option key={profile.id} value={profile.id}>{profile.label}</option>
                            ))}
                        </select>
                      </label>
                    ))}
                  </div>
                </section>
              );
            })}
          </div>
        ) : (
          <p className="voice-routing-empty">尚无可用文本模型。请先在火候页的“模型管理”中补充 API Key，再回到这里选择配音路由。</p>
        )}
        <h3 className="voice-settings-subheading">配音专业审校</h3>
        <Row detail="脚本生成时的采样参数；降低会更保守，提高会更发散。" label="脚本生成 Top-P">
          <input aria-label="脚本生成 Top-P" max="1" min="0" onChange={(e) => onChange({ scriptGenerationTopP: e.target.value })} step="0.05" type="number" value={draft.scriptGenerationTopP} />
        </Row>
        <Row detail="按可演性、说话人风险、情绪意图和 TTS 稳定性复核。" label="启用配音专业审校">
          <input aria-label="启用配音专业审校" checked={draft.scriptLlmReviewEnabled} onChange={(e) => onChange({ scriptLlmReviewEnabled: e.target.checked })} type="checkbox" />
        </Row>
        <Row detail="整章仅返回问题与安全修复；章节较长时可适当提高。" label="审校输出预算（tokens）">
          <input aria-label="审校输出预算" max="32768" min="2048" onChange={(e) => onChange({ scriptLlmReviewMaxTokens: e.target.value })} step="1024" type="number" value={draft.scriptLlmReviewMaxTokens} />
        </Row>
        <h3 className="voice-settings-subheading">角色音色 LLM 复核</h3>
        <Row detail="只处理规则和向量匹配仍不够确定的角色。" label="启用低置信度复核">
          <input aria-label="启用低置信度复核" checked={draft.voiceLlmAdjudicationEnabled} onChange={(e) => onChange({ voiceLlmAdjudicationEnabled: e.target.checked })} type="checkbox" />
        </Row>
        <Row detail="画像匹配分低于此值时才进入 LLM 复核。" label="触发阈值">
          <input aria-label="触发阈值" max="1" min="0" onChange={(e) => onChange({ voiceLlmMinScore: e.target.value })} step="0.05" type="number" value={draft.voiceLlmMinScore} />
        </Row>
        <Row detail="按最低匹配分优先，限制每次建组时的模型费用。" label="单次最大复核角色数">
          <input aria-label="单次最大复核角色数" max="12" min="1" onChange={(e) => onChange({ voiceLlmMaxCandidates: e.target.value })} type="number" value={draft.voiceLlmMaxCandidates} />
        </Row>
        <Row detail="从音色库和平台目录中选取通过硬约束的候选供 LLM 比较。" label="每个角色候选数">
          <input aria-label="每个角色候选数" max="8" min="2" onChange={(e) => onChange({ voiceLlmChoicesPerChar: e.target.value })} type="number" value={draft.voiceLlmChoicesPerChar} />
        </Row>
        <Row detail="达到此阈值且候选未被其他角色占用时，才允许 LLM 自动替换音色。" label="自动改配置信度">
          <input aria-label="自动改配置信度" max="1" min="0.5" onChange={(e) => onChange({ voiceLlmAutoSelectScore: e.target.value })} step="0.05" type="number" value={draft.voiceLlmAutoSelectScore} />
        </Row>
        <Row detail="每批复核可使用的最大输出 token。" label="复核输出预算（tokens）">
          <input aria-label="复核输出预算" max="4096" min="256" onChange={(e) => onChange({ voiceLlmMaxTokens: e.target.value })} step="128" type="number" value={draft.voiceLlmMaxTokens} />
        </Row>
      </VoiceSettingsSection>
    </div>
  );
}
