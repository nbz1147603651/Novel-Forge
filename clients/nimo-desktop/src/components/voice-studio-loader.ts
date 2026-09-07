import { lazy } from "react";

const loadVoiceStudioPage = () =>
  import("./VoiceStudioPage").then((m) => ({ default: m.VoiceStudioPage }));

/**
 * Keep the voice studio surface out of the application-shell chunk.
 *
 * The voice studio includes clone/design/rebuild/script dialogs and the
 * delivery export flow — heavy enough to warrant splitting.
 */
export const LazyVoiceStudioPage = lazy(loadVoiceStudioPage);

export function preloadVoiceStudioPage(): Promise<unknown> {
  return loadVoiceStudioPage();
}
