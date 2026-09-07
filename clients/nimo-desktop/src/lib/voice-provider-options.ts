import type { VoiceProviderView } from "@nimo/engine-contracts";

import type { DropdownOption } from "../components/DropdownSelect";

/**
 * Provider presentation is owned by the Engine catalog. The browser keeps no
 * provider or alias table of its own, so adding a backend adapter immediately
 * updates every dropdown that consumes this helper.
 */
export function ttsProviderOptions(
  catalog: readonly VoiceProviderView[] | undefined,
  currentProvider = "",
): readonly DropdownOption[] {
  if (catalog && catalog.length > 0) {
    return catalog.map((provider) => ({ value: provider.id, label: provider.label }));
  }
  return currentProvider
    ? [{ value: currentProvider, label: currentProvider }]
    : [];
}

export function ttsProviderKeyFromLabel(
  label: string | undefined | null,
  catalog: readonly VoiceProviderView[] | undefined = undefined,
  fallback = "minimax",
): string {
  const normalized = (label ?? "").trim().toLocaleLowerCase();
  if (!normalized) return fallback;
  const match = catalog?.find((provider) => {
    const names = [provider.id, provider.label, ...provider.aliases];
    return names.some((name) => {
      const candidate = name.trim().toLocaleLowerCase();
      return candidate === normalized || (candidate.length > 0 && normalized.includes(candidate));
    });
  });
  return match?.id ?? normalized;
}

export function ttsProviderLabelForId(
  id: string | undefined | null,
  catalog: readonly VoiceProviderView[] | undefined = undefined,
): string {
  const normalized = (id ?? "").trim();
  return catalog?.find((provider) => provider.id === normalized)?.label ?? normalized;
}

export function ttsProviderSpec(
  providerId: string | undefined | null,
  catalog: readonly VoiceProviderView[] | undefined,
): VoiceProviderView | undefined {
  const resolved = ttsProviderKeyFromLabel(providerId, catalog, "");
  return catalog?.find((provider) => provider.id === resolved);
}
