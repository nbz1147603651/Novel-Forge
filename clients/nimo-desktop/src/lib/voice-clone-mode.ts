import type { VoiceStudioView } from "@nimo/engine-contracts";

export type VoiceCloneReferenceMode = "local-file" | "provider-file-id";

function normalized(value: string): string {
  return value.trim().toLocaleLowerCase();
}

/**
 * Resolve the only valid clone-reference input from the Engine's active model.
 *
 * Sending a local path to a provider that requires a provider-side File ID
 * always fails (and a file name is not a usable path). Prefer the conservative
 * provider-ID flow whenever the selected provider/model cannot be identified.
 */
export function voiceCloneReferenceMode(studio: VoiceStudioView): VoiceCloneReferenceMode {
  const providerId = normalized(studio.providerLabel);
  const provider = studio.providerCatalog?.find((candidate) =>
    [candidate.id, candidate.label, ...candidate.aliases]
      .map(normalized)
      .includes(providerId),
  );
  if (provider === undefined) return "provider-file-id";

  const configuredModel = normalized(studio.configuredModelLabel);
  const configured = provider.models.find((candidate) =>
    normalized(candidate.id) === configuredModel || normalized(candidate.label) === configuredModel,
  );
  if (configured !== undefined) return configured.localReferenceAudio ? "local-file" : "provider-file-id";
  if (configuredModel !== "") return "provider-file-id";

  const model = provider.models.find(
    (candidate) => normalized(candidate.id) === normalized(provider.defaultModel),
  );
  return model?.localReferenceAudio === true ? "local-file" : "provider-file-id";
}
