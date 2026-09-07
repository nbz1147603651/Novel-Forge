/**
 * Desktop notification utility.
 *
 * Mirrors PySide6 desktop notification settings: task completion, failure,
 * and decision-required notifications via the Tauri notification plugin.
 * Falls back to the browser Notification API when running outside Tauri.
 */

export type NotificationKind = "error" | "info" | "success" | "warning";

interface NotificationOptions {
  readonly body?: string;
  readonly kind?: NotificationKind;
  readonly title: string;
}

let permissionGranted = false;

/** Check and cache notification permission status. */
async function ensurePermission(): Promise<boolean> {
  if (permissionGranted) return true;
  try {
    // Try Tauri notification plugin first
    const { isPermissionGranted, requestPermission } = await import(
      "@tauri-apps/plugin-notification"
    );
    let granted = await isPermissionGranted();
    if (!granted) {
      const result = await requestPermission();
      granted = result === "granted";
    }
    permissionGranted = granted;
    return granted;
  } catch {
    // Fallback to browser Notification API
    if (typeof Notification === "undefined") return false;
    if (Notification.permission === "granted") {
      permissionGranted = true;
      return true;
    }
    if (Notification.permission === "denied") return false;
    const result = await Notification.requestPermission();
    permissionGranted = result === "granted";
    return permissionGranted;
  }
}

/**
 * Send a desktop notification.
 *
 * Respects the user's notification settings (enabled/disabled) stored in
 * localStorage under `nimo:settings:notifySound`.
 */
export async function sendDesktopNotification(options: NotificationOptions): Promise<void> {
  // Check if notifications are enabled in settings
  try {
    const enabled = localStorage.getItem("nimo:settings:notifyEnabled");
    if (enabled === "false") return;
  } catch {
    // ignore storage errors
  }

  const granted = await ensurePermission();
  if (!granted) return;

  try {
    // Try Tauri notification plugin
    const { sendNotification } = await import("@tauri-apps/plugin-notification");
    sendNotification({
      title: options.title,
      body: options.body ?? "",
    });
  } catch {
    // Fallback to browser Notification API
    if (typeof Notification !== "undefined" && Notification.permission === "granted") {
      new Notification(options.title, { body: options.body ?? "" });
    }
  }
}

/** Convenience: notify task completion. */
export function notifyTaskSuccess(taskLabel: string): void {
  void sendDesktopNotification({
    title: "任务完成",
    body: `${taskLabel} 已成功完成。`,
    kind: "success",
  });
}

/** Convenience: notify task failure. */
export function notifyTaskFailure(taskLabel: string, error?: string): void {
  void sendDesktopNotification({
    title: "任务失败",
    body: error ? `${taskLabel}：${error}` : `${taskLabel} 执行失败。`,
    kind: "error",
  });
}

/** Convenience: notify decision required. */
export function notifyDecisionRequired(taskLabel: string, question: string): void {
  void sendDesktopNotification({
    title: "等待决策",
    body: `${taskLabel}：${question}`,
    kind: "warning",
  });
}
