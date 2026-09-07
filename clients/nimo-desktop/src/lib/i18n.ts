/**
 * i18n — Single barrel import for all internationalization utilities.
 *
 * Components should import from here:
 *   import { useT, useLocale, LocaleProvider, type Locale } from "../lib/i18n";
 *
 * Non-React code (session helpers, pure functions):
 *   import { translate, DICT, pick, type Locale } from "../lib/i18n";
 *
 * Architecture overview:
 * ┌─────────────────────────────────────────────────────────────────┐
 * │  i18n.ts  (this file — barrel re-export)                       │
 * │    ├── locale.tsx   → Context, Provider, useLocale, useT,      │
 * │    │                   translate, loadStoredLocale, saveLocale  │
 * │    └── locale-dict.ts → DICT, pick, translate (raw)            │
 * └─────────────────────────────────────────────────────────────────┘
 *
 * Adding a new translatable string:
 *   1. Add key to DICT in locale-dict.ts  (e.g. "mySection.myKey")
 *   2. Use in component:  const t = useT();  t("mySection.myKey")
 *   3. Use in pure fn:    translate(locale, "mySection.myKey")
 */

// Re-export types and React utilities
export type { Locale } from "./locale";
export {
  LocaleContext,
  LocaleProvider,
  useLocale,
  useT,
  translate,
  loadStoredLocale,
  saveLocale,
  outputLanguageForLocale,
} from "./locale";

// Re-export dictionary and raw helpers
export { DICT, pick, translate as rawTranslate } from "./locale-dict";
export type { DictEntry } from "./locale-dict";
