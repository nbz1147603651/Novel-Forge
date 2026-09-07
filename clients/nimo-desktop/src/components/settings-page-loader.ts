import { lazy } from "react";

const loadSettingsPage = () => import("./SettingsPage");

/**
 * Keep the dense settings surface out of the application-shell chunk.
 *
 * The caller may prefetch this module while the dashboard is idle, but route
 * selection never synchronously constructs the settings form or its dialogs.
 */
export const LazySettingsPage = lazy(loadSettingsPage);

export function preloadSettingsPage(): Promise<unknown> {
  return loadSettingsPage();
}
