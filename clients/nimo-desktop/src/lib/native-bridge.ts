/**
 * Native bridge — typed wrappers around Tauri invoke commands.
 *
 * These functions call into the Rust backend (src-tauri/src/main.rs) for
 * desktop-native capabilities that cannot be achieved via pure HTTP:
 * - File system dialogs (directory picker, save dialog)
 * - Revealing engine-resolved files in the platform file manager
 * - Audio playback (afplay on macOS)
 * - Sleep inhibitor (caffeinate on macOS)
 * - Global shortcuts (Ctrl+1~4 navigation, Ctrl+Alt+N Nimo toggle)
 *
 * When running outside Tauri (e.g. browser dev mode), all functions
 * gracefully degrade to no-ops or return null.
 */

import { invoke } from "@tauri-apps/api/core";
import { listen, type UnlistenFn } from "@tauri-apps/api/event";

export const DESKTOP_RESUMED_EVENT = "nimo://desktop-resumed";

/**
 * Forward a bounded React render-failure diagnostic to the local Tauri process.
 * Browser development mode has no native process, so failure to invoke is
 * intentionally ignored there.
 */
export async function reportFrontendRenderFailure(
  error: Error,
  componentStack: string | null,
): Promise<void> {
  try {
    await invoke("cmd_report_frontend_render_failure", {
      summary: error.message || error.name,
      componentStack: componentStack ?? "",
    });
  } catch {
    // Keep the recovery boundary usable even when the native bridge is absent.
  }
}

// ── File System ─────────────────────────────────────────────────────────────

/** Open a native directory picker. Returns the selected path or null. */
export async function selectDirectory(): Promise<string | null> {
  try {
    return await invoke<string | null>("cmd_select_directory");
  } catch {
    return null;
  }
}

/**
 * Open the native chooser for a local voice-clone reference.
 *
 * The returned path is passed only to a loopback Engine whose TTS adapter
 * advertises ``localReferenceAudio``.  Cloud/provider-file-id flows never
 * invoke this bridge.
 */
export async function pickAudioReferenceFile(): Promise<string | null> {
  try {
    return await invoke<string | null>("cmd_pick_audio_reference_file");
  } catch {
    return null;
  }
}

/** Open a native save-file dialog. Returns the chosen path or null. */
export async function saveFileDialog(
  defaultName: string,
  filterExt?: string,
): Promise<string | null> {
  try {
    return await invoke<string | null>("cmd_save_file_dialog", {
      defaultName,
      filterExt: filterExt ?? null,
    });
  } catch {
    return null;
  }
}

/** Reveal an existing engine-resolved file in the platform file manager. */
export async function revealFileInFolder(path: string): Promise<boolean> {
  if (path.trim() === "") return false;
  try {
    await invoke("cmd_reveal_file_in_folder", { path });
    return true;
  } catch {
    return false;
  }
}

// ── Audio Playback ──────────────────────────────────────────────────────────

/** Play an audio file using the system default player. */
export async function playAudio(path: string): Promise<void> {
  try {
    await invoke("cmd_play_audio", { path });
  } catch {
    // Silently fail in non-Tauri environments
  }
}

/** Stop any currently playing audio. */
export async function stopAudio(): Promise<void> {
  try {
    await invoke("cmd_stop_audio");
  } catch {
    // Silently fail
  }
}

// ── Sleep Inhibitor ─────────────────────────────────────────────────────────

/** Prevent the system from sleeping (call when long tasks start). */
export async function acquireSleepInhibitor(): Promise<void> {
  try {
    await invoke("cmd_acquire_sleep_inhibitor");
  } catch {
    // Silently fail
  }
}

/** Allow the system to sleep again (call when all tasks complete). */
export async function releaseSleepInhibitor(): Promise<void> {
  try {
    await invoke("cmd_release_sleep_inhibitor");
  } catch {
    // Silently fail
  }
}

// ── Global Shortcuts ────────────────────────────────────────────────────────

export type ShortcutAction =
  | "nav.dashboard"
  | "nav.projects"
  | "nav.workflow"
  | "nav.chapter_studio"
  | "nav.settings"
  | "pet.toggle";

export interface ShortcutEventPayload {
  readonly action: ShortcutAction;
}

/**
 * Subscribe to global shortcut events emitted by the Tauri backend.
 * Returns an unsubscribe function.
 *
 * Shortcuts registered (matching PySide6 shortcuts.py):
 * - Ctrl+1 → nav.dashboard
 * - Ctrl+2 → nav.projects
 * - Ctrl+3 → nav.workflow
 * - Ctrl+4 → nav.chapter_studio
 * - Ctrl+, → nav.settings
 * - Ctrl+Alt+N → pet.toggle
 */
export async function onShortcut(
  handler: (action: ShortcutAction) => void,
): Promise<UnlistenFn> {
  try {
    return await listen<ShortcutEventPayload>("nimo://shortcut", (event) => {
      handler(event.payload.action);
    });
  } catch {
    // Return a no-op unsubscribe in non-Tauri environments
    return () => {};
  }
}

/**
 * Subscribe to the native desktop resume signal.  This is deliberately a
 * no-op in browser development mode, where standard visibility events cover
 * the same recovery path.
 */
export async function onDesktopResumed(handler: () => void): Promise<UnlistenFn> {
  if (!isTauriEnvironment()) return () => {};
  try {
    return await listen(DESKTOP_RESUMED_EVENT, () => handler());
  } catch {
    return () => {};
  }
}

/**
 * Detect whether we're running inside a Tauri webview.
 * Useful for conditionally showing native-only UI affordances.
 */
export function isTauriEnvironment(): boolean {
  return typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;
}
