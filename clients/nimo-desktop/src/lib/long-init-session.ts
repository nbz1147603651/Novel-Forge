/**
 * Long-form project initialization session logic.
 *
 * Mirrors PySide6 `LongInitForm` (workflow/forms.py): form payload, validation,
 * volume-mode linkage, research section, preset binding and launch preview.
 * Follows the same pure-function reducer pattern as `short-workflow-session.ts`.
 */

import type { BlueprintElementPreferences } from "./short-workflow-session";
import type { InitLongWorkflowInput, WorkflowLaunchMode } from "@nimo/engine-contracts";
import { emptyBlueprintElementPreferences, normalizeBlueprintPreferences } from "./short-workflow-session";
import { outputLanguageForLocale, translate, type Locale } from "./i18n";

// ── Types ────────────────────────────────────────────────────────────

export type LongTone = "neutral" | "warm" | "gentle" | "dark" | "suspenseful" | "humorous" | "solemn" | "lyrical";
export type LongLanguage = "zh" | "zh-Hant" | "en";
export type VolumeMode = "auto" | "on" | "off";
export type ResearchProvider = "auto" | "tavily" | "brave" | "searxng" | "http_json" | "bailian_web_search" | "mcp_search";
export type CreativeExploration = "adaptive" | "single";
export type PlanningCommitment = "progressive" | "full";

export interface LongInitPayload {
  readonly premise: string;
  readonly charactersHint: string;
  readonly worldHint: string;
  readonly conflictHint: string;
  readonly genre: string;
  readonly tone: LongTone;
  readonly totalChapters: number;
  readonly wordsPerChapter: number;
  readonly volumeMode: VolumeMode;
  readonly chaptersPerVolume: number;
  readonly researchEnabled: boolean;
  readonly researchProvider: ResearchProvider;
  readonly researchQueryHint: string;
  readonly title: string;
  readonly language: LongLanguage;
  readonly povHint: string;
  readonly openingStyle: string;
  readonly endingStyle: string;
  readonly extraInstructions: string;
  readonly projectId: string;
  readonly blueprintElementPreferences: BlueprintElementPreferences;
  readonly creativeExploration: CreativeExploration;
  readonly planningCommitment: PlanningCommitment;
}

export interface LongInitPreset {
  readonly id: string;
  readonly name: string;
  readonly payload: LongInitPayload;
}

export interface LongInitHistoryEntry {
  readonly id: string;
  readonly label: string;
  readonly presetId: string;
  readonly changedKeys: readonly (keyof LongInitPayload)[];
  readonly snapshot: LongInitPayload;
  readonly timeLabel?: string;
  readonly userHint?: string;
  readonly selectedSuggestions?: readonly string[];
  readonly creativeNote?: Readonly<Record<string, unknown>> | undefined;
  readonly creativeProfile?: Readonly<Record<string, string>>;
}

export interface LongInitSession {
  readonly activePresetId: string | null;
  readonly history: readonly LongInitHistoryEntry[];
  readonly payload: LongInitPayload;
  readonly presets: readonly LongInitPreset[];
}

export type LongInitPayloadPatch = Partial<LongInitPayload>;

export type LongInitTextKey =
  | "premise"
  | "genre"
  | "title"
  | "charactersHint"
  | "worldHint"
  | "conflictHint"
  | "povHint"
  | "openingStyle"
  | "endingStyle"
  | "extraInstructions"
  | "projectId"
  | "researchQueryHint";

export type LongInitKey = keyof LongInitPayload;

// ── Options ──────────────────────────────────────────────────────────

export const longToneOptions: readonly { readonly label: string; readonly value: LongTone }[] = [
  { value: "neutral", label: "中性" },
  { value: "warm", label: "温暖" },
  { value: "gentle", label: "温柔" },
  { value: "dark", label: "阴郁" },
  { value: "suspenseful", label: "悬疑" },
  { value: "humorous", label: "幽默" },
  { value: "solemn", label: "庄重" },
  { value: "lyrical", label: "抒情" },
];

export const longLanguageOptions: readonly { readonly label: string; readonly value: LongLanguage }[] = [
  { value: "zh", label: "简体中文" },
  { value: "zh-Hant", label: "繁体中文" },
  { value: "en", label: "English" },
];

export const volumeModeOptions: readonly { readonly label: string; readonly value: VolumeMode }[] = [
  { value: "auto", label: "自动" },
  { value: "on", label: "开启" },
  { value: "off", label: "关闭" },
];

export const researchProviderOptions: readonly { readonly label: string; readonly value: ResearchProvider }[] = [
  { value: "auto", label: "自动" },
  { value: "tavily", label: "Tavily" },
  { value: "brave", label: "Brave" },
  { value: "searxng", label: "SearXNG" },
  { value: "http_json", label: "HTTP JSON" },
  { value: "bailian_web_search", label: "百炼 Web Search" },
  { value: "mcp_search", label: "MCP Search" },
];

// ── Defaults ─────────────────────────────────────────────────────────

/** Mirrors PySide6 LongInitForm.reset_form() defaults. */
export const emptyLongInitPayload: LongInitPayload = {
  premise: "",
  charactersHint: "",
  worldHint: "",
  conflictHint: "",
  genre: "",
  tone: "neutral",
  totalChapters: 24,
  wordsPerChapter: 4500,
  volumeMode: "auto",
  chaptersPerVolume: 0,
  researchEnabled: false,
  researchProvider: "auto",
  researchQueryHint: "",
  title: "",
  language: "zh",
  povHint: "",
  openingStyle: "",
  endingStyle: "",
  extraInstructions: "",
  projectId: "",
  blueprintElementPreferences: emptyBlueprintElementPreferences,
  creativeExploration: "adaptive",
  planningCommitment: "full",
};

/** Return an empty long-form draft that stays aligned with the desktop locale. */
export function emptyLongInitPayloadForLocale(locale: Locale): LongInitPayload {
  return { ...emptyLongInitPayload, language: outputLanguageForLocale(locale) };
}

// ── Normalization helpers ────────────────────────────────────────────

const longToneValues = new Set<LongTone>(longToneOptions.map((o) => o.value));
const longLanguageValues = new Set<LongLanguage>(longLanguageOptions.map((o) => o.value));
const volumeModeValues = new Set<VolumeMode>(volumeModeOptions.map((o) => o.value));
const researchProviderValues = new Set<ResearchProvider>(researchProviderOptions.map((o) => o.value));
const creativeExplorationValues = new Set<CreativeExploration>(["adaptive", "single"]);
const planningCommitmentValues = new Set<PlanningCommitment>(["progressive", "full"]);

function cleanText(value: unknown, fallback = ""): string {
  return typeof value === "string" ? value : fallback;
}

function boundedInteger(value: unknown, fallback: number, min: number, max: number): number {
  const parsed = typeof value === "number" ? value : Number(value);
  if (!Number.isFinite(parsed)) return fallback;
  return Math.min(max, Math.max(min, Math.round(parsed)));
}

function optionValue<T extends string>(value: unknown, options: ReadonlySet<T>, fallback: T): T {
  return typeof value === "string" && options.has(value as T) ? (value as T) : fallback;
}

function hasOwn(record: Record<string, unknown>, key: string): boolean {
  return Object.prototype.hasOwnProperty.call(record, key);
}

function valueFor(record: Record<string, unknown>, camelCase: string, snakeCase?: string): unknown {
  if (hasOwn(record, camelCase)) return record[camelCase];
  return snakeCase !== undefined && hasOwn(record, snakeCase) ? record[snakeCase] : undefined;
}

// ── Volume mode linkage ──────────────────────────────────────────────

/**
 * Mirrors `_sync_volume_mode_controls`: when volume mode is not "on",
 * chapters_per_volume is forced to 0 and disabled.
 */
export function normalizeVolumeFields(mode: VolumeMode, chaptersPerVolume: number): { chaptersPerVolume: number; hint: string } {
  if (mode === "on") {
    return {
      chaptersPerVolume: Math.max(0, chaptersPerVolume),
      hint: "on=强制分卷；可指定每卷章节数，设为「自动」时使用火候页的默认值。 每章字数建议 3000-6000；初始化会按当前模型能力自动选择大纲与契约批次。",
    };
  }
  if (mode === "off") {
    return {
      chaptersPerVolume: 0,
      hint: "off=强制不分卷；每卷章节数不适用。 每章字数建议 3000-6000；初始化会按当前模型能力自动选择大纲与契约批次。",
    };
  }
  return {
    chaptersPerVolume: 0,
    hint: "auto=系统根据总章节数和预计总字数决定是否分卷；每卷章节数由系统自动决定。 每章字数建议 3000-6000；初始化会按当前模型能力自动选择大纲与契约批次。",
  };
}

// ── Full payload normalization ───────────────────────────────────────

export function normalizeLongInitPayload(input: LongInitPayloadPatch | Record<string, unknown>): LongInitPayload {
  const aliases = input as Record<string, unknown>;
  const volumeMode = optionValue(input.volumeMode ?? aliases.volume_mode, volumeModeValues, emptyLongInitPayload.volumeMode);
  const rawChaptersPerVolume = boundedInteger(
    input.chaptersPerVolume ?? aliases.chapters_per_volume,
    emptyLongInitPayload.chaptersPerVolume,
    0,
    500,
  );
  const { chaptersPerVolume } = normalizeVolumeFields(volumeMode, rawChaptersPerVolume);

  return {
    premise: cleanText(input.premise),
    charactersHint: cleanText(input.charactersHint ?? aliases.characters_hint),
    worldHint: cleanText(input.worldHint ?? aliases.world_hint),
    conflictHint: cleanText(input.conflictHint ?? aliases.conflict_hint),
    genre: cleanText(input.genre),
    tone: optionValue(input.tone, longToneValues, emptyLongInitPayload.tone),
    totalChapters: boundedInteger(input.totalChapters ?? aliases.total_chapters, emptyLongInitPayload.totalChapters, 1, 1000),
    wordsPerChapter: boundedInteger(input.wordsPerChapter ?? aliases.words_per_chapter, emptyLongInitPayload.wordsPerChapter, 500, 20000),
    volumeMode,
    chaptersPerVolume,
    researchEnabled: Boolean(input.researchEnabled ?? aliases.research_enabled),
    researchProvider: optionValue(input.researchProvider ?? aliases.research_provider, researchProviderValues, emptyLongInitPayload.researchProvider),
    researchQueryHint: cleanText(input.researchQueryHint ?? aliases.research_query_hint),
    creativeExploration: optionValue(
      input.creativeExploration ?? aliases.creative_exploration,
      creativeExplorationValues,
      emptyLongInitPayload.creativeExploration,
    ),
    planningCommitment: optionValue(
      input.planningCommitment ?? aliases.planning_commitment,
      planningCommitmentValues,
      emptyLongInitPayload.planningCommitment,
    ),
    title: cleanText(input.title),
    language: optionValue(input.language, longLanguageValues, emptyLongInitPayload.language),
    povHint: cleanText(input.povHint ?? aliases.pov_hint),
    openingStyle: cleanText(input.openingStyle ?? aliases.opening_style),
    endingStyle: cleanText(input.endingStyle ?? aliases.ending_style),
    extraInstructions: cleanText(input.extraInstructions ?? aliases.extra_instructions),
    projectId: cleanText(input.projectId ?? aliases.project_id),
    blueprintElementPreferences: normalizeBlueprintPreferences(
      input.blueprintElementPreferences ?? aliases.blueprint_element_preferences
    ),
  };
}

/**
 * Normalize only fields actually supplied by imported JSON.
 * Mirrors `LongInitForm._fill()` merge semantics.
 */
export function normalizeLongInitPatch(input: Record<string, unknown>): LongInitPayloadPatch {
  type MutablePatch = { -readonly [K in keyof LongInitPayloadPatch]: LongInitPayloadPatch[K] };
  const patch: MutablePatch = {};

  const setText = (key: LongInitTextKey, snakeKey?: string) => {
    const value = valueFor(input, key, snakeKey);
    if (value !== undefined) patch[key] = cleanText(value);
  };
  setText("premise");
  setText("genre");
  setText("title");
  setText("charactersHint", "characters_hint");
  setText("worldHint", "world_hint");
  setText("conflictHint", "conflict_hint");
  setText("povHint", "pov_hint");
  setText("openingStyle", "opening_style");
  setText("endingStyle", "ending_style");
  setText("extraInstructions", "extra_instructions");
  setText("projectId", "project_id");
  setText("researchQueryHint", "research_query_hint");

  const tone = valueFor(input, "tone");
  if (typeof tone === "string" && longToneValues.has(tone as LongTone)) patch.tone = tone as LongTone;
  const language = valueFor(input, "language");
  if (typeof language === "string" && longLanguageValues.has(language as LongLanguage)) patch.language = language as LongLanguage;
  const volumeMode = valueFor(input, "volumeMode", "volume_mode");
  if (typeof volumeMode === "string" && volumeModeValues.has(volumeMode as VolumeMode)) patch.volumeMode = volumeMode as VolumeMode;
  const researchProvider = valueFor(input, "researchProvider", "research_provider");
  if (typeof researchProvider === "string" && researchProviderValues.has(researchProvider as ResearchProvider)) {
    patch.researchProvider = researchProvider as ResearchProvider;
  }
  const creativeExploration = valueFor(input, "creativeExploration", "creative_exploration");
  if (typeof creativeExploration === "string" && creativeExplorationValues.has(creativeExploration as CreativeExploration)) {
    patch.creativeExploration = creativeExploration as CreativeExploration;
  }
  const planningCommitment = valueFor(input, "planningCommitment", "planning_commitment");
  if (typeof planningCommitment === "string" && planningCommitmentValues.has(planningCommitment as PlanningCommitment)) {
    patch.planningCommitment = planningCommitment as PlanningCommitment;
  }

  const setBoundedInt = (key: "totalChapters" | "wordsPerChapter" | "chaptersPerVolume", snakeKey: string, fallback: number, min: number, max: number) => {
    const value = valueFor(input, key, snakeKey);
    const parsed = typeof value === "number" ? value : Number(value);
    if (value !== undefined && Number.isFinite(parsed)) patch[key] = boundedInteger(value, fallback, min, max);
  };
  setBoundedInt("totalChapters", "total_chapters", emptyLongInitPayload.totalChapters, 1, 1000);
  setBoundedInt("wordsPerChapter", "words_per_chapter", emptyLongInitPayload.wordsPerChapter, 500, 20000);
  setBoundedInt("chaptersPerVolume", "chapters_per_volume", emptyLongInitPayload.chaptersPerVolume, 0, 500);

  const researchEnabled = valueFor(input, "researchEnabled", "research_enabled");
  if (typeof researchEnabled === "boolean") patch.researchEnabled = researchEnabled;

  return patch;
}

// ── Session operations ───────────────────────────────────────────────

export function createLongInitSession(payload: LongInitPayload = emptyLongInitPayload): LongInitSession {
  return {
    activePresetId: null,
    history: [],
    payload: normalizeLongInitPayload(payload),
    presets: [],
  };
}

export function updateLongInitPayload(session: LongInitSession, patch: LongInitPayloadPatch): LongInitSession {
  return { ...session, payload: normalizeLongInitPayload({ ...session.payload, ...patch }) };
}

export function resetLongInitSession(
  session: LongInitSession,
  locale: Locale = "zh",
): LongInitSession {
  return {
    ...session,
    activePresetId: null,
    payload: emptyLongInitPayloadForLocale(locale),
  };
}

// ── Preset CRUD ──────────────────────────────────────────────────────

export function normalizeLongPresetName(name: string): string {
  return name.trim().replace(/\s+/g, " ");
}

export function findLongInitPresetByName(session: LongInitSession, name: string): LongInitPreset | undefined {
  const normalized = normalizeLongPresetName(name);
  return session.presets.find((p) => p.name === normalized);
}

export function saveLongInitPreset(
  session: LongInitSession,
  name: string,
  payload: LongInitPayload = session.payload,
): { readonly error?: string; readonly session: LongInitSession } {
  const normalized = normalizeLongPresetName(name);
  if (!/[\p{L}\p{N}]/u.test(normalized)) {
    return { error: "预设名称至少需要包含一个文字、字母或数字。", session };
  }
  const existing = findLongInitPresetByName(session, normalized);
  const saved: LongInitPreset = {
    id: existing?.id ?? `long-preset:${normalized}`,
    name: normalized,
    payload: normalizeLongInitPayload(payload),
  };
  return {
    session: {
      ...session,
      activePresetId: saved.id,
      presets: existing === undefined
        ? [...session.presets, saved]
        : session.presets.map((p) => (p.id === existing.id ? saved : p)),
    },
  };
}

export function loadLongInitPreset(session: LongInitSession, presetId: string): LongInitSession {
  const preset = session.presets.find((c) => c.id === presetId);
  return preset === undefined ? session : { ...session, activePresetId: preset.id, payload: preset.payload };
}

export function deleteLongInitPreset(session: LongInitSession, presetId: string): LongInitSession {
  return {
    ...session,
    activePresetId: session.activePresetId === presetId ? null : session.activePresetId,
    presets: session.presets.filter((p) => p.id !== presetId),
  };
}

// ── Validation ───────────────────────────────────────────────────────

export function validateLongInitPayload(payload: LongInitPayload): readonly string[] {
  return payload.premise.trim() === "" ? ["请填写「故事前提」。"] : [];
}

// ── Serialization ────────────────────────────────────────────────────

export function serializeLongInitPayload(payload: LongInitPayload): string {
  return JSON.stringify(
    {
      premise: payload.premise,
      genre: payload.genre,
      tone: payload.tone,
      total_chapters: payload.totalChapters,
      words_per_chapter: payload.wordsPerChapter,
      volume_mode: payload.volumeMode,
      chapters_per_volume: payload.chaptersPerVolume,
      research_enabled: payload.researchEnabled,
      research_provider: payload.researchProvider,
      research_query_hint: payload.researchQueryHint,
      creative_exploration: payload.creativeExploration,
      planning_commitment: payload.planningCommitment,
      title: payload.title,
      language: payload.language,
      characters_hint: payload.charactersHint,
      world_hint: payload.worldHint,
      conflict_hint: payload.conflictHint,
      pov_hint: payload.povHint,
      opening_style: payload.openingStyle,
      ending_style: payload.endingStyle,
      extra_instructions: payload.extraInstructions,
      project_id: payload.projectId,
      blueprint_element_preferences: {
        preset_id: payload.blueprintElementPreferences.presetId,
        manual_override: payload.blueprintElementPreferences.manualOverride,
        items: payload.blueprintElementPreferences.items.map((item) => ({
          element_id: item.elementId,
          enabled: item.enabled,
          locked: item.locked,
          weight: item.weight,
        })),
      },
    },
    null,
    2,
  );
}

export function toInitLongWorkflowInput(
  payload: LongInitPayload,
  runMode: WorkflowLaunchMode,
): InitLongWorkflowInput {
  return {
    projectId: payload.projectId.trim(),
    premise: payload.premise.trim(),
    genre: payload.genre.trim(),
    tone: payload.tone,
    totalChapters: payload.totalChapters,
    wordsPerChapter: payload.wordsPerChapter,
    volumeMode: payload.volumeMode,
    chaptersPerVolume: payload.volumeMode === "on" ? payload.chaptersPerVolume : 0,
    title: payload.title.trim(),
    language: payload.language,
    charactersHint: payload.charactersHint.trim(),
    worldHint: payload.worldHint.trim(),
    conflictHint: payload.conflictHint.trim(),
    povHint: payload.povHint.trim(),
    openingStyle: payload.openingStyle.trim(),
    endingStyle: payload.endingStyle.trim(),
    extraInstructions: payload.extraInstructions.trim(),
    polishHint: "",
    researchEnabled: payload.researchEnabled,
    researchProvider: payload.researchProvider,
    researchQueryHint: payload.researchQueryHint.trim(),
    creativeExploration: payload.creativeExploration,
    planningCommitment: payload.planningCommitment,
    regenerateOutline: false,
    blueprintElementPreferences: payload.blueprintElementPreferences,
    copilotGates: runMode === "copilot" ? ["concept", "spec", "blueprint", "outline"] : [],
  };
}

export function parseLongInitJson(source: string): { readonly error?: string; readonly payload?: LongInitPayloadPatch } {
  try {
    const parsed = JSON.parse(source);
    if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
      return { error: "该 JSON 不包含可用于长篇表单的对象。" };
    }
    const candidate = (parsed.long_init ?? parsed.init_long ?? parsed) as Record<string, unknown>;
    const knownKeys = [
      "premise", "genre", "tone", "total_chapters", "totalChapters", "words_per_chapter", "wordsPerChapter",
      "volume_mode", "volumeMode", "chapters_per_volume", "chaptersPerVolume", "title", "language",
      "characters_hint", "charactersHint", "world_hint", "worldHint", "conflict_hint", "conflictHint",
      "pov_hint", "povHint", "opening_style", "openingStyle", "ending_style", "endingStyle",
      "extra_instructions", "extraInstructions", "project_id", "projectId",
      "research_enabled", "researchEnabled", "research_provider", "researchProvider",
      "creative_exploration", "creativeExploration", "planning_commitment", "planningCommitment",
    ];
    if (!knownKeys.some((key) => key in candidate)) {
      return { error: "该 JSON 不包含可用于长篇表单的字段。" };
    }
    return { payload: normalizeLongInitPatch(candidate) };
  } catch {
    return { error: "无法解析 JSON，请检查括号、引号和字段格式。" };
  }
}

// ── Launch preview ───────────────────────────────────────────────────

export function longInitLaunchPreview(payload: LongInitPayload, locale: Locale = "zh"): Readonly<Record<string, string | number>> {
  const prefs = payload.blueprintElementPreferences;
  const prefItems = prefs.items.filter((i) => i.enabled === true || i.locked);
  const { hint: _volumeHint, ...volumeFields } = normalizeVolumeFields(payload.volumeMode, payload.chaptersPerVolume);
  const toneLabel = translate(locale, `tone.${payload.tone}`);
  const volumeLabel = translate(locale, `value.${payload.volumeMode}`);
  const langLabel = translate(locale, `lang.${payload.language}`);
  return {
    [translate(locale, "preview.projectId")]: payload.projectId.trim() || translate(locale, "value.engineGenerated"),
    [translate(locale, "preview.premise")]: payload.premise.trim().slice(0, 80) + (payload.premise.trim().length > 80 ? "…" : ""),
    [translate(locale, "preview.genre")]: payload.genre.trim() || translate(locale, "genre.literary"),
    [translate(locale, "preview.tone")]: toneLabel,
    [translate(locale, "preview.totalChapters")]: payload.totalChapters,
    [translate(locale, "preview.wordsPerChapter")]: payload.wordsPerChapter,
    [translate(locale, "preview.volumeMode")]: volumeLabel,
    [translate(locale, "preview.chaptersPerVolume")]: volumeFields.chaptersPerVolume === 0 ? translate(locale, "value.auto") : volumeFields.chaptersPerVolume,
    [translate(locale, "preview.researchEnabled")]: payload.researchEnabled ? translate(locale, "value.yes") : translate(locale, "value.no"),
    [translate(locale, "preview.language")]: langLabel,
    [translate(locale, "preview.blueprintPreset")]: prefs.presetId || translate(locale, "value.notSelected"),
    [translate(locale, "preview.blueprintManualItems")]: prefItems.length,
  };
}

// ── History ──────────────────────────────────────────────────────────

export function appendLongInitHistory(
  session: LongInitSession,
  label: string,
  patch: LongInitPayloadPatch,
): LongInitSession {
  const updated = updateLongInitPayload(session, patch);
  if (session.activePresetId === null) return updated;
  const payloadKeys = new Set<LongInitKey>(Object.keys(updated.payload) as LongInitKey[]);
  const changedKeys = (Object.keys(patch) as LongInitKey[]).filter((key) => payloadKeys.has(key));
  return {
    ...updated,
    history: [
      ...session.history,
      {
        id: `long-history:${session.history.length + 1}`,
        label,
        presetId: session.activePresetId,
        changedKeys,
        snapshot: updated.payload,
      },
    ],
  };
}

export function historyForLongInitPreset(session: LongInitSession): readonly LongInitHistoryEntry[] {
  return session.activePresetId === null
    ? []
    : session.history.filter((entry) => entry.presetId === session.activePresetId);
}
