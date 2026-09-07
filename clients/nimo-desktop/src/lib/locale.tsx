/**
 * i18n Core — React Context + Hooks + Persistence
 *
 * Architecture:
 *   locale.tsx   → Context, Provider, useLocale(), useT(), persistence
 *   locale-dict.ts → DICT (flat key → {zh, en}), translate(), pick()
 *   i18n.ts      → barrel re-export (single import point for consumers)
 *
 * Usage in React components:
 *   import { useT } from "../lib/i18n";
 *   const t = useT();
 *   <span>{t("nav.dashboard.label")}</span>
 *   <span>{t("topbar.goToChapter", { n: 5 })}</span>
 *
 * Usage outside React (session helpers, pure functions):
 *   import { translate } from "../lib/i18n";
 *   translate(locale, "preview.projectId")
 */
import { createContext, useCallback, useContext, type ReactNode } from "react";
import { translate as _translate } from "./locale-dict";

// ── Types ───────────────────────────────────────────────────────────────────

export type Locale = "zh" | "en";

/**
 * Map the desktop locale to the language sent with a new workflow request.
 *
 * The engine derives the prompt pack from this output language, so keeping
 * this conversion in the i18n boundary prevents the interface and model
 * prompts from drifting apart.
 */
export function outputLanguageForLocale(locale: Locale): Locale {
  return locale;
}

// ── Context ─────────────────────────────────────────────────────────────────

export const LocaleContext = createContext<Locale>("zh");

export interface LocaleProviderProps {
  readonly locale: Locale;
  readonly children: ReactNode;
}

export function LocaleProvider({ locale, children }: LocaleProviderProps) {
  return <LocaleContext.Provider value={locale}>{children}</LocaleContext.Provider>;
}

// ── Hooks ────────────────────────────────────────────────────────────────────

/** Returns the current locale string. */
export function useLocale(): Locale {
  return useContext(LocaleContext);
}

/**
 * Returns a memoized translation function bound to the current locale.
 *
 * @example
 * const t = useT();
 * t("nav.dashboard.label")                    // "案头" | "Dashboard"
 * t("topbar.goToChapter", { n: 5 })           // "前往第 5 章 →" | "Go to Chapter 5 →"
 */
export function useT(): (key: string, params?: Record<string, string | number>) => string {
  const locale = useLocale();
  return useCallback(
    (key: string, params?: Record<string, string | number>) => _translate(locale, key, params),
    [locale],
  );
}

// ── Non-React helper (for session files, pure functions) ─────────────────────

/**
 * Translate a key for an explicitly-provided locale.
 * Use this in non-React contexts (e.g. session serialization, launch preview builders).
 */
export function translate(
  locale: Locale,
  key: string,
  params?: Record<string, string | number>,
): string {
  return _translate(locale, key, params);
}

// ── Persistence ──────────────────────────────────────────────────────────────

const LOCALE_STORAGE_KEY = "nimo:locale";

export function loadStoredLocale(): Locale {
  try {
    const stored = localStorage.getItem(LOCALE_STORAGE_KEY);
    if (stored === "en" || stored === "zh") return stored;
  } catch { /* SSR or storage blocked */ }
  return "zh";
}

export function saveLocale(locale: Locale): void {
  try { localStorage.setItem(LOCALE_STORAGE_KEY, locale); } catch { /* ignore */ }
}
