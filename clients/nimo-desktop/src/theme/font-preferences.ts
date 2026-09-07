import type {
  FontPreferencesView,
  ReadingFontFamilyId,
  UiFontFamilyId,
} from "@nimo/engine-contracts";

const storageKey = "nimo:font-preferences";

export const defaultFontPreferences: FontPreferencesView = {
  uiFamily: "source_sans",
  readingFamily: "source_serif",
  scale: 1,
};

export interface FontChoice<Id extends string> {
  readonly description: string;
  readonly id: Id;
  readonly label: string;
}

/**
 * The settings screen is only a consumer of these choices.  Keeping the
 * labels, IDs, fallback stacks and type scale in this one module means a new
 * page can use the same typography contract without inventing a local font
 * list or a one-off size.
 */
export const uiFontChoices: readonly FontChoice<UiFontFamilyId>[] = [
  { id: "source_sans", label: "主题宋体（默认）", description: "与 PySide6 立项界面一致，中文优先宋体，适合古典创作工作台。" },
  { id: "system_sans", label: "系统无衬线", description: "适合高密度模型、日志与结构化信息。" },
  { id: "literary_serif", label: "经典宋体", description: "以传统宋体字形呈现界面标题与文案。" },
];

export const readingFontChoices: readonly FontChoice<ReadingFontFamilyId>[] = [
  { id: "source_serif", label: "主题宋体（默认）", description: "用于正文、报告与叙事内容。" },
  { id: "ui_sans", label: "Arial / 系统无衬线", description: "适合结构化资料的快速浏览。" },
  { id: "calligraphy", label: "楷体", description: "仅用于偏文学的阅读偏好。" },
];

export const fontScaleChoices = [
  { value: 0.9, label: "90%" },
  { value: 1, label: "100%（舒适基线）" },
  { value: 1.1, label: "110%" },
  { value: 1.2, label: "120%" },
] as const;

/* global.css defines the local stacks. The default UI stack follows PySide6's
 * Songti contract; system_sans remains available for dense data surfaces. */
const themeUiStack = '"Nimo Theme UI Han", "Nimo Theme UI Latin", sans-serif';
const themeReadingStack = '"Nimo Theme Reading Han", "Nimo Theme Reading Latin", serif';
const commonSansStack = 'Arial, "Helvetica Neue", system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Microsoft YaHei", "Noto Sans CJK SC", sans-serif';
const songtiStack = '"Songti SC", STSong, SimSun, "Noto Serif CJK SC", "Times New Roman", Times, Arial, serif';

const uiStacks: Readonly<Record<UiFontFamilyId, string>> = {
  source_sans: themeUiStack,
  system_sans: commonSansStack,
  literary_serif: songtiStack,
};

const readingStacks: Readonly<Record<ReadingFontFamilyId, string>> = {
  source_serif: themeReadingStack,
  ui_sans: commonSansStack,
  calligraphy: 'STKaiti, "Kaiti SC", KaiTi, "Songti SC", STSong, "Times New Roman", Times, Arial, serif',
};

export const fontSizeRamp = {
  "2xs": 10,
  xs: 11,
  micro: 12,
  meta: 12,
  ui: 13,
  popup: 14,
  body: 14,
  label: 14,
  "popup-title": 15,
  subsection: 15,
  "section-sm": 16,
  section: 17,
  "section-lg": 18,
  heading: 19,
  nav: 19,
  page: 19,
  "page-lg": 20,
  display: 21,
  "display-lg": 22,
  hero: 23,
  "hero-lg": 24,
  metric: 28,
  "metric-lg": 30,
  brand: 30,
  "brand-lg": 32,
  "brand-xl": 34,
  "brand-hero": 37,
} as const;

export function normalizeFontPreferences(value: unknown): FontPreferencesView {
  if (typeof value !== "object" || value === null) return defaultFontPreferences;
  const source = value as Partial<FontPreferencesView>;
  const uiFamily = source.uiFamily;
  const readingFamily = source.readingFamily;
  const numericScale = Number(source.scale);
  return {
    uiFamily: uiFamily !== undefined && uiFamily in uiStacks
      ? uiFamily
      : defaultFontPreferences.uiFamily,
    readingFamily: readingFamily !== undefined && readingFamily in readingStacks
      ? readingFamily
      : defaultFontPreferences.readingFamily,
    scale: Number.isFinite(numericScale)
      ? Math.min(1.2, Math.max(0.9, numericScale))
      : defaultFontPreferences.scale,
  };
}

export function loadStoredFontPreferences(): FontPreferencesView {
  try {
    return normalizeFontPreferences(JSON.parse(localStorage.getItem(storageKey) ?? "null"));
  } catch {
    return defaultFontPreferences;
  }
}

export function applyFontPreferences(value: FontPreferencesView): FontPreferencesView {
  const preferences = normalizeFontPreferences(value);
  const root = document.documentElement;
  root.style.setProperty("--nf-font-ui-family", uiStacks[preferences.uiFamily]);
  root.style.setProperty("--nf-font-reading-family", readingStacks[preferences.readingFamily]);
  root.style.setProperty(
    "--nf-font-code-family",
    'Menlo, Monaco, Consolas, "Courier New", monospace',
  );
  for (const [token, px] of Object.entries(fontSizeRamp)) {
    root.style.setProperty(`--nf-font-${token}`, `${Math.round(px * preferences.scale * 10) / 10}px`);
  }
  root.dataset.uiFont = preferences.uiFamily;
  root.dataset.readingFont = preferences.readingFamily;
  root.dataset.fontScale = String(preferences.scale);
  localStorage.setItem(storageKey, JSON.stringify(preferences));
  return preferences;
}
