import type { PageId } from "@nimo/engine-contracts";

import type { IconName } from "./Icon";
import type { Locale } from "../lib/i18n";
import { translate } from "../lib/i18n";

/**
 * PageMeta stores i18n *keys* (not translated strings).
 * Use `resolvePageMeta(meta, locale)` inside React components to get display strings.
 */
export interface PageMeta {
  readonly id: PageId;
  /** i18n key for the short nav label (e.g. "nav.dashboard.label") */
  readonly labelKey: string;
  /** i18n key for the top-bar eyebrow */
  readonly eyebrowKey: string;
  /** i18n key for the top-bar title */
  readonly titleKey: string;
  /** i18n key for the top-bar subtitle */
  readonly subtitleKey: string;
  readonly icon: IconName;
}

/** Resolved (translated) page meta for rendering. */
export interface ResolvedPageMeta {
  readonly id: PageId;
  readonly label: string;
  readonly eyebrow: string;
  readonly title: string;
  readonly subtitle: string;
  readonly icon: IconName;
}

/** Resolve a PageMeta's i18n keys to display strings for the given locale. */
export function resolvePageMeta(meta: PageMeta, locale: Locale): ResolvedPageMeta {
  return {
    id: meta.id,
    label: translate(locale, meta.labelKey),
    eyebrow: translate(locale, meta.eyebrowKey),
    title: translate(locale, meta.titleKey),
    subtitle: translate(locale, meta.subtitleKey),
    icon: meta.icon,
  };
}

export type ProjectFilter = "all" | "writing" | "planning" | "completed" | "long" | "short";

/** Returns filter labels for the given locale. */
export function getFilterLabels(locale: Locale): Readonly<Record<ProjectFilter, string>> {
  return {
    all: translate(locale, "dashboard.filter.all"),
    writing: translate(locale, "dashboard.filter.writing"),
    planning: translate(locale, "dashboard.filter.planning"),
    completed: translate(locale, "dashboard.filter.completed"),
    long: translate(locale, "dashboard.filter.long"),
    short: translate(locale, "dashboard.filter.short"),
  };
}

export function formatSideRailLabel(label: string): string {
  return label.length === 2 ? `·  ${label[0]} ${label[1]}  ·` : `·  ${label}  ·`;
}
