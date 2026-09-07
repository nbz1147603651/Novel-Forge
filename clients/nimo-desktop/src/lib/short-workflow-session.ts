import { EXTENSION_CARDS, GENRE_PRESETS } from "./blueprint-element-data";
import type { RunShortWorkflowInput } from "@nimo/engine-contracts";
import { outputLanguageForLocale, translate, type Locale } from "./i18n";

export type ShortWritingMode = "auto" | "whole_chapter" | "scene_level";
export type ShortTone = "neutral" | "warm" | "gentle" | "dark" | "suspenseful" | "humorous" | "solemn" | "lyrical";
export type ShortLanguage = "zh" | "en";

/** One row in the blueprint element preference panel. */
export interface BlueprintElementPreferenceItem {
  readonly elementId: string;
  readonly enabled: boolean | null;
  readonly locked: boolean;
  readonly weight: number;
}

/** Full blueprint element preference payload (mirrors Python collect_preferences()). */
export interface BlueprintElementPreferences {
  readonly presetId: string;
  readonly manualOverride: boolean;
  readonly items: readonly BlueprintElementPreferenceItem[];
}

export const emptyBlueprintElementPreferences: BlueprintElementPreferences = {
  presetId: "",
  manualOverride: false,
  items: [],
};

export interface ShortWorkflowPayload {
  readonly theme: string;
  readonly genre: string;
  readonly tone: ShortTone;
  readonly lengthTarget: number;
  readonly maxEditRounds: number;
  readonly segmentTriggerWords: number;
  readonly writingMode: ShortWritingMode;
  readonly title: string;
  readonly language: ShortLanguage;
  readonly charactersHint: string;
  readonly worldHint: string;
  readonly conflictHint: string;
  readonly povHint: string;
  readonly openingStyle: string;
  readonly endingStyle: string;
  readonly extraInstructions: string;
  readonly researchEnabled: boolean;
  readonly researchProvider: string;
  readonly researchQueryHint: string;
  readonly projectId: string;
  readonly blueprintElementPreferences: BlueprintElementPreferences;
}

export interface ShortWorkflowPreset {
  readonly id: string;
  readonly name: string;
  readonly payload: ShortWorkflowPayload;
}

export interface ShortWorkflowHistoryEntry {
  readonly id: string;
  readonly label: string;
  /** The preset binding used by PySide6 to keep separate creative-note histories. */
  readonly presetId: string;
  readonly changedKeys: readonly (keyof ShortWorkflowPayload)[];
  /** Full payload snapshot after applying the patch, for restore. */
  readonly snapshot: ShortWorkflowPayload;
  /** User-facing audit data, matching the legacy AI creative-note record. */
  readonly timeLabel?: string;
  readonly userHint?: string;
  readonly selectedSuggestions?: readonly string[];
  readonly creativeNote?: Readonly<Record<string, unknown>> | undefined;
  readonly creativeProfile?: Readonly<Record<string, string>>;
}

export type ShortWorkflowHistoryMetadata = Pick<
  ShortWorkflowHistoryEntry,
  "timeLabel" | "userHint" | "selectedSuggestions" | "creativeNote" | "creativeProfile"
>;

export interface ShortWorkflowSession {
  readonly activePresetId: string | null;
  readonly history: readonly ShortWorkflowHistoryEntry[];
  readonly payload: ShortWorkflowPayload;
  readonly presets: readonly ShortWorkflowPreset[];
}

export type ShortWorkflowTextKey =
  | "theme"
  | "genre"
  | "title"
  | "charactersHint"
  | "worldHint"
  | "conflictHint"
  | "povHint"
  | "openingStyle"
  | "endingStyle"
  | "extraInstructions"
  | "researchProvider"
  | "researchQueryHint"
  | "projectId";

export type ShortWorkflowKey = keyof ShortWorkflowPayload;

export type ShortWorkflowPayloadPatch = Partial<ShortWorkflowPayload>;

export const shortToneOptions: readonly { readonly label: string; readonly value: ShortTone }[] = [
  { value: "neutral", label: "中性" },
  { value: "warm", label: "温暖" },
  { value: "gentle", label: "温柔" },
  { value: "dark", label: "阴郁" },
  { value: "suspenseful", label: "悬疑" },
  { value: "humorous", label: "幽默" },
  { value: "solemn", label: "庄重" },
  { value: "lyrical", label: "抒情" },
];

export const shortWritingModeOptions: readonly { readonly label: string; readonly value: ShortWritingMode }[] = [
  { value: "auto", label: "自动" },
  { value: "whole_chapter", label: "整篇写作" },
  { value: "scene_level", label: "分段写作" },
];

export const shortLanguageOptions: readonly { readonly label: string; readonly value: ShortLanguage }[] = [
  { value: "zh", label: "中文 (zh)" },
  { value: "en", label: "English (en)" },
];

/** The exact empty-state defaults exposed by PySide6 ShortForm.reset_form(). */
export const emptyShortWorkflowPayload: ShortWorkflowPayload = {
  theme: "",
  genre: "",
  tone: "neutral",
  lengthTarget: 3200,
  maxEditRounds: 2,
  segmentTriggerWords: 6000,
  writingMode: "auto",
  title: "",
  language: "zh",
  charactersHint: "",
  worldHint: "",
  conflictHint: "",
  povHint: "",
  openingStyle: "",
  endingStyle: "",
  extraInstructions: "",
  researchEnabled: false,
  researchProvider: "auto",
  researchQueryHint: "",
  projectId: "",
  blueprintElementPreferences: emptyBlueprintElementPreferences,
};

/** A deterministic populated session is useful for the Phase 1 Mock surface. */
export const shortWorkflowFixturePayload: ShortWorkflowPayload = {
  ...emptyShortWorkflowPayload,
  theme: "一只旧怀表在雨夜停止走动，失主留下的录音指向一段被刻意抹去的家庭记忆。",
  genre: "悬疑",
  tone: "suspenseful",
  charactersHint: "整理遗物的女儿与始终回避真相的父亲；两人都要决定是否听完最后一段录音。",
  worldHint: "南方旧城的雨夜与一间即将拆迁的老宅；录音带是唯一没有被数字化的证据。",
  conflictHint: "女儿想追问录音中的名字，父亲坚持让旧事随拆迁一同消失。",
  povHint: "第三人称限知，跟随女儿的行动与迟疑。",
  openingStyle: "从雨夜停摆的怀表与倒带声切入。",
  endingStyle: "让录音成为可见的行动后果，而不是只用解释收束。",
  extraInstructions: "减少解释性旁白，让物件、动作与停顿承担信息。",
};

/** Return the initial short-form draft for the selected desktop locale. */
export function shortWorkflowPayloadForLocale(locale: Locale): ShortWorkflowPayload {
  return { ...shortWorkflowFixturePayload, language: outputLanguageForLocale(locale) };
}

/** Return an empty short-form draft that stays aligned with the desktop locale. */
export function emptyShortWorkflowPayloadForLocale(locale: Locale): ShortWorkflowPayload {
  return { ...emptyShortWorkflowPayload, language: outputLanguageForLocale(locale) };
}

const shortToneValues = new Set<ShortTone>(shortToneOptions.map((option) => option.value));
const shortWritingModeValues = new Set<ShortWritingMode>(shortWritingModeOptions.map((option) => option.value));
const shortLanguageValues = new Set<ShortLanguage>(shortLanguageOptions.map((option) => option.value));
const extensionElementIds = new Set(EXTENSION_CARDS.map((card) => card.id));
const genrePresetIds = new Set(GENRE_PRESETS.map((preset) => preset.id));

function cleanText(value: unknown, fallback = ""): string {
  return typeof value === "string" ? value : fallback;
}

function boundedInteger(value: unknown, fallback: number, min: number, max: number): number {
  const parsed = typeof value === "number" ? value : Number(value);
  if (!Number.isFinite(parsed)) return fallback;
  return Math.min(max, Math.max(min, Math.round(parsed)));
}

function optionValue<T extends string>(value: unknown, options: ReadonlySet<T>, fallback: T): T {
  return typeof value === "string" && options.has(value as T) ? value as T : fallback;
}

function hasOwn(record: Record<string, unknown>, key: string): boolean {
  return Object.prototype.hasOwnProperty.call(record, key);
}

function valueFor(record: Record<string, unknown>, camelCase: string, snakeCase?: string): unknown {
  if (hasOwn(record, camelCase)) return record[camelCase];
  return snakeCase !== undefined && hasOwn(record, snakeCase) ? record[snakeCase] : undefined;
}

export function normalizeBlueprintPreferences(raw: unknown): BlueprintElementPreferences {
  if (!raw || typeof raw !== "object") return emptyBlueprintElementPreferences;
  const obj = raw as Record<string, unknown>;
  const suppliedItems = Array.isArray(obj.items) ? obj.items : [];
  const itemsById = new Map<string, BlueprintElementPreferenceItem>();
  for (const rawItem of suppliedItems) {
    if (!rawItem || typeof rawItem !== "object") continue;
    const item = rawItem as Record<string, unknown>;
    const elementId = String(item.element_id ?? item.elementId ?? "");
    if (!extensionElementIds.has(elementId)) continue;
    const weight = boundedInteger(item.weight, 50, 0, 100);
    itemsById.set(elementId, {
      elementId,
      enabled: item.enabled === true ? true : item.enabled === false ? false : null,
      locked: Boolean(item.locked),
      weight,
    });
  }
  // The PySide panel rebuilds its rows from the library, discarding unknown IDs,
  // duplicate rows, and no-op defaults before it collects a payload again.
  const items = EXTENSION_CARDS.flatMap((card) => {
    const item = itemsById.get(card.id);
    return item !== undefined && (item.enabled === true || item.locked || item.weight !== 50) ? [item] : [];
  });
  const requestedPresetId = String(obj.preset_id ?? obj.presetId ?? "");
  return {
    presetId: genrePresetIds.has(requestedPresetId) ? requestedPresetId : "",
    manualOverride: Boolean(obj.manual_override ?? obj.manualOverride),
    items,
  };
}

/**
 * Normalise only fields actually supplied by imported JSON.
 *
 * `ShortForm._fill()` in PySide6 merges an import into the current form rather
 * than resetting omitted fields to defaults. Keeping this separate from the
 * full-payload normaliser makes that boundary explicit and reusable.
 */
export function normalizeShortWorkflowPatch(input: Record<string, unknown>): ShortWorkflowPayloadPatch {
  type MutableShortWorkflowPayloadPatch = {
    -readonly [Key in keyof ShortWorkflowPayloadPatch]: ShortWorkflowPayloadPatch[Key];
  };
  const patch: MutableShortWorkflowPayloadPatch = {};
  const setText = (key: ShortWorkflowTextKey, snakeKey?: string) => {
    const value = valueFor(input, key, snakeKey);
    if (value !== undefined) patch[key] = cleanText(value);
  };
  setText("theme");
  setText("genre");
  setText("title");
  setText("charactersHint", "characters_hint");
  setText("worldHint", "world_hint");
  setText("conflictHint", "conflict_hint");
  setText("povHint", "pov_hint");
  setText("openingStyle", "opening_style");
  setText("endingStyle", "ending_style");
  setText("extraInstructions", "extra_instructions");
  setText("researchProvider", "research_provider");
  setText("researchQueryHint", "research_query_hint");
  setText("projectId", "project_id");

  const researchEnabled = valueFor(input, "researchEnabled", "research_enabled");
  if (researchEnabled !== undefined) patch.researchEnabled = Boolean(researchEnabled);

  const tone = valueFor(input, "tone");
  if (typeof tone === "string" && shortToneValues.has(tone as ShortTone)) patch.tone = tone as ShortTone;
  const language = valueFor(input, "language");
  if (typeof language === "string" && shortLanguageValues.has(language as ShortLanguage)) patch.language = language as ShortLanguage;
  const writingMode = valueFor(input, "writingMode", "writing_mode");
  if (typeof writingMode === "string" && shortWritingModeValues.has(writingMode as ShortWritingMode)) {
    patch.writingMode = writingMode as ShortWritingMode;
  }

  const setBoundedInteger = (
    key: "lengthTarget" | "maxEditRounds" | "segmentTriggerWords",
    snakeKey: string,
    fallback: number,
    min: number,
    max: number,
  ) => {
    const value = valueFor(input, key, snakeKey);
    const parsed = typeof value === "number" ? value : Number(value);
    if (value !== undefined && Number.isFinite(parsed)) patch[key] = boundedInteger(value, fallback, min, max);
  };
  setBoundedInteger("lengthTarget", "length_target", emptyShortWorkflowPayload.lengthTarget, 500, 50_000);
  setBoundedInteger("maxEditRounds", "max_edit_rounds", emptyShortWorkflowPayload.maxEditRounds, 0, 10);
  setBoundedInteger("segmentTriggerWords", "segment_trigger_words", emptyShortWorkflowPayload.segmentTriggerWords, 1_500, 50_000);

  const preferences = valueFor(input, "blueprintElementPreferences", "blueprint_element_preferences");
  if (preferences !== undefined) patch.blueprintElementPreferences = normalizeBlueprintPreferences(preferences);
  return patch;
}

export function normalizeShortWorkflowPayload(input: ShortWorkflowPayloadPatch | Record<string, unknown>): ShortWorkflowPayload {
  const aliases = input as Record<string, unknown>;
  return {
    theme: cleanText(input.theme),
    genre: cleanText(input.genre),
    tone: optionValue(input.tone, shortToneValues, emptyShortWorkflowPayload.tone),
    lengthTarget: boundedInteger(input.lengthTarget ?? aliases.length_target, emptyShortWorkflowPayload.lengthTarget, 500, 50_000),
    maxEditRounds: boundedInteger(input.maxEditRounds ?? aliases.max_edit_rounds, emptyShortWorkflowPayload.maxEditRounds, 0, 10),
    segmentTriggerWords: boundedInteger(input.segmentTriggerWords ?? aliases.segment_trigger_words, emptyShortWorkflowPayload.segmentTriggerWords, 1_500, 50_000),
    writingMode: optionValue(input.writingMode ?? aliases.writing_mode, shortWritingModeValues, emptyShortWorkflowPayload.writingMode),
    title: cleanText(input.title),
    language: optionValue(input.language, shortLanguageValues, emptyShortWorkflowPayload.language),
    charactersHint: cleanText(input.charactersHint ?? aliases.characters_hint),
    worldHint: cleanText(input.worldHint ?? aliases.world_hint),
    conflictHint: cleanText(input.conflictHint ?? aliases.conflict_hint),
    povHint: cleanText(input.povHint ?? aliases.pov_hint),
    openingStyle: cleanText(input.openingStyle ?? aliases.opening_style),
    endingStyle: cleanText(input.endingStyle ?? aliases.ending_style),
    extraInstructions: cleanText(input.extraInstructions ?? aliases.extra_instructions),
    researchEnabled: Boolean(input.researchEnabled ?? aliases.research_enabled),
    researchProvider: cleanText(
      input.researchProvider ?? aliases.research_provider,
      emptyShortWorkflowPayload.researchProvider,
    ) || "auto",
    researchQueryHint: cleanText(input.researchQueryHint ?? aliases.research_query_hint),
    projectId: cleanText(input.projectId ?? aliases.project_id),
    blueprintElementPreferences: normalizeBlueprintPreferences(
      input.blueprintElementPreferences ?? aliases.blueprint_element_preferences
    ),
  };
}

export function createShortWorkflowSession(payload: ShortWorkflowPayload = shortWorkflowFixturePayload): ShortWorkflowSession {
  return {
    activePresetId: null,
    history: [],
    payload: normalizeShortWorkflowPayload(payload),
    presets: [],
  };
}

export function updateShortWorkflowPayload(
  session: ShortWorkflowSession,
  patch: ShortWorkflowPayloadPatch,
): ShortWorkflowSession {
  return { ...session, payload: normalizeShortWorkflowPayload({ ...session.payload, ...patch }) };
}

export function resetShortWorkflowSession(
  session: ShortWorkflowSession,
  locale: Locale = "zh",
): ShortWorkflowSession {
  return {
    ...session,
    activePresetId: null,
    payload: emptyShortWorkflowPayloadForLocale(locale),
  };
}

export function normalizeShortPresetName(name: string): string {
  return name.trim().replace(/\s+/g, " ");
}

export function findShortWorkflowPresetByName(
  session: ShortWorkflowSession,
  name: string,
): ShortWorkflowPreset | undefined {
  const normalized = normalizeShortPresetName(name);
  return session.presets.find((preset) => preset.name === normalized);
}

export function saveShortWorkflowPreset(
  session: ShortWorkflowSession,
  name: string,
  payload: ShortWorkflowPayload = session.payload,
): { readonly error?: string; readonly session: ShortWorkflowSession } {
  const normalized = normalizeShortPresetName(name);
  if (!/[\p{L}\p{N}]/u.test(normalized)) {
    return { error: "预设名称至少需要包含一个文字、字母或数字。", session };
  }
  const existing = findShortWorkflowPresetByName(session, normalized);
  const saved: ShortWorkflowPreset = {
    id: existing?.id ?? `short-preset:${normalized}`,
    name: normalized,
    payload: normalizeShortWorkflowPayload(payload),
  };
  return {
    session: {
      ...session,
      activePresetId: saved.id,
      presets: existing === undefined
        ? [...session.presets, saved]
        : session.presets.map((preset) => preset.id === existing.id ? saved : preset),
    },
  };
}

export function loadShortWorkflowPreset(session: ShortWorkflowSession, presetId: string): ShortWorkflowSession {
  const preset = session.presets.find((candidate) => candidate.id === presetId);
  return preset === undefined ? session : {
    ...session,
    activePresetId: preset.id,
    payload: preset.payload,
  };
}

export function deleteShortWorkflowPreset(session: ShortWorkflowSession, presetId: string): ShortWorkflowSession {
  return {
    ...session,
    activePresetId: session.activePresetId === presetId ? null : session.activePresetId,
    presets: session.presets.filter((preset) => preset.id !== presetId),
  };
}

function importCandidate(value: unknown): Record<string, unknown> | null {
  if (typeof value !== "object" || value === null || Array.isArray(value)) return null;
  const record = value as Record<string, unknown>;
  const nested = record.short ?? record.run_short;
  if (typeof nested === "object" && nested !== null && !Array.isArray(nested)) {
    return nested as Record<string, unknown>;
  }
  return record;
}

export function parseShortWorkflowJson(source: string): { readonly error?: string; readonly payload?: ShortWorkflowPayloadPatch } {
  try {
    const candidate = importCandidate(JSON.parse(source));
    if (candidate === null) {
      return { error: "该 JSON 不包含可用于短篇表单的对象。" };
    }
    const knownKeys = [
      "theme", "genre", "tone", "length_target", "lengthTarget", "max_edit_rounds", "maxEditRounds",
      "segment_trigger_words", "segmentTriggerWords", "writing_mode", "writingMode", "title", "language",
      "characters_hint", "charactersHint", "world_hint", "worldHint", "conflict_hint", "conflictHint",
      "pov_hint", "povHint", "opening_style", "openingStyle", "ending_style", "endingStyle",
      "extra_instructions", "extraInstructions", "project_id", "projectId", "blueprint_element_preferences",
      "blueprintElementPreferences", "research_enabled", "researchEnabled", "research_provider",
      "researchProvider", "research_query_hint", "researchQueryHint",
    ];
    if (!knownKeys.some((key) => key in candidate)) {
      return { error: "该 JSON 不包含可用于短篇表单的字段。" };
    }
    return { payload: normalizeShortWorkflowPatch(candidate) };
  } catch {
    return { error: "无法解析 JSON，请检查括号、引号和字段格式。" };
  }
}

export function serializeShortWorkflowPayload(payload: ShortWorkflowPayload): string {
  return JSON.stringify({
    theme: payload.theme,
    genre: payload.genre,
    tone: payload.tone,
    length_target: payload.lengthTarget,
    max_edit_rounds: payload.maxEditRounds,
    segment_trigger_words: payload.segmentTriggerWords,
    writing_mode: payload.writingMode,
    title: payload.title,
    language: payload.language,
    characters_hint: payload.charactersHint,
    world_hint: payload.worldHint,
    conflict_hint: payload.conflictHint,
    pov_hint: payload.povHint,
    opening_style: payload.openingStyle,
    ending_style: payload.endingStyle,
    extra_instructions: payload.extraInstructions,
    research_enabled: payload.researchEnabled,
    research_provider: payload.researchProvider,
    research_query_hint: payload.researchQueryHint,
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
  }, null, 2);
}

export function toRunShortWorkflowInput(payload: ShortWorkflowPayload): RunShortWorkflowInput {
  return {
    projectId: payload.projectId.trim(),
    theme: payload.theme.trim(),
    genre: payload.genre.trim(),
    tone: payload.tone,
    lengthTarget: payload.lengthTarget,
    maxEditRounds: payload.maxEditRounds,
    segmentTriggerWords: payload.segmentTriggerWords,
    writingMode: payload.writingMode,
    title: payload.title.trim(),
    language: payload.language,
    charactersHint: payload.charactersHint.trim(),
    worldHint: payload.worldHint.trim(),
    conflictHint: payload.conflictHint.trim(),
    povHint: payload.povHint.trim(),
    openingStyle: payload.openingStyle.trim(),
    endingStyle: payload.endingStyle.trim(),
    extraInstructions: payload.extraInstructions.trim(),
    researchEnabled: payload.researchEnabled,
    researchProvider: payload.researchProvider.trim() || "auto",
    researchQueryHint: payload.researchQueryHint.trim(),
    blueprintElementPreferences: payload.blueprintElementPreferences,
  };
}

export function validateShortWorkflowPayload(payload: ShortWorkflowPayload): readonly string[] {
  return payload.theme.trim() === "" ? ["请填写「故事主题」。"] : [];
}

export function appendShortWorkflowHistory(
  session: ShortWorkflowSession,
  label: string,
  patch: ShortWorkflowPayloadPatch,
  metadata: ShortWorkflowHistoryMetadata = {},
): ShortWorkflowSession {
  const updated = updateShortWorkflowPayload(session, patch);
  if (session.activePresetId === null) return updated;
  const payloadKeys = new Set<ShortWorkflowKey>(Object.keys(updated.payload) as ShortWorkflowKey[]);
  const changedKeys = (Object.keys(patch) as ShortWorkflowKey[]).filter((key) => payloadKeys.has(key));
  return {
    ...updated,
    history: [...session.history, {
      id: `short-history:${session.history.length + 1}`,
      label,
      presetId: session.activePresetId,
      changedKeys,
      snapshot: updated.payload,
      ...metadata,
    }],
  };
}

/** Return only the creative notes belonging to the currently bound preset. */
export function historyForShortWorkflowPreset(session: ShortWorkflowSession): readonly ShortWorkflowHistoryEntry[] {
  return session.activePresetId === null
    ? []
    : session.history.filter((entry) => entry.presetId === session.activePresetId);
}

function autoPresetName(session: ShortWorkflowSession, payload: ShortWorkflowPayload): string {
  const source = normalizeShortPresetName(payload.title || payload.theme);
  const baseName = Array.from(source).slice(0, 32).join("") || "AI创意生成";
  if (findShortWorkflowPresetByName(session, baseName) === undefined) return baseName;
  let suffix = 2;
  while (findShortWorkflowPresetByName(session, `${baseName} ${suffix}`) !== undefined) suffix += 1;
  return `${baseName} ${suffix}`;
}

/**
 * Apply an accepted preview with the same binding semantics as PySide6:
 * AI work updates its bound preset, or creates a named one before recording
 * a creative-note snapshot. The new UI deliberately keeps this in memory
 * until the Engine command layer owns persistence.
 */
export function applyShortWorkflowPreview(
  session: ShortWorkflowSession,
  label: string,
  patch: ShortWorkflowPayloadPatch,
  metadata: ShortWorkflowHistoryMetadata = {},
): { readonly session: ShortWorkflowSession; readonly autoSavedPresetName?: string } {
  const updated = updateShortWorkflowPayload(session, patch);
  const activePreset = session.presets.find((preset) => preset.id === session.activePresetId);
  const presetName = activePreset?.name ?? autoPresetName(updated, updated.payload);
  const saved = saveShortWorkflowPreset(updated, presetName, updated.payload);
  if (saved.error !== undefined) return { session: updated };
  return {
    session: appendShortWorkflowHistory(saved.session, label, patch, metadata),
    ...(activePreset === undefined ? { autoSavedPresetName: presetName } : {}),
  };
}

export function shortWorkflowLaunchPreview(payload: ShortWorkflowPayload, locale: Locale = "zh"): Readonly<Record<string, string | number>> {
  const prefs = payload.blueprintElementPreferences;
  const prefItems = prefs.items.filter((i) => i.enabled === true || i.locked);
  const toneLabel = translate(locale, `tone.${payload.tone}`);
  const langLabel = translate(locale, `lang.${payload.language}`);
  const writingModeLabel = translate(locale, `writingMode.${payload.writingMode === "whole_chapter" ? "wholeChapter" : payload.writingMode === "scene_level" ? "sceneLevel" : "auto"}`);
  return {
    [translate(locale, "preview.projectId")]: payload.projectId.trim() || translate(locale, "value.engineGenerated"),
    [translate(locale, "preview.theme")]: payload.theme.trim(),
    [translate(locale, "preview.genre")]: payload.genre.trim() || translate(locale, "genre.literary"),
    [translate(locale, "preview.tone")]: toneLabel,
    [translate(locale, "preview.lengthTarget")]: payload.lengthTarget,
    [translate(locale, "preview.writingMode")]: writingModeLabel,
    [translate(locale, "preview.segmentedMode")]: payload.lengthTarget >= payload.segmentTriggerWords ? translate(locale, "value.on") : translate(locale, "value.off"),
    [translate(locale, "preview.maxEditRounds")]: payload.maxEditRounds,
    [translate(locale, "preview.language")]: langLabel,
    [translate(locale, "preview.blueprintPreset")]: prefs.presetId || translate(locale, "value.notSelected"),
    [translate(locale, "preview.blueprintManualItems")]: prefItems.length,
    [translate(locale, "preview.blueprintManualOverride")]: prefs.manualOverride ? translate(locale, "value.yes") : translate(locale, "value.no"),
  };
}
