import type { ModelProfileView, SettingsView, TestModelProfileResult } from "@nimo/engine-contracts";

export type ConnectivityEntryState = "idle" | "queued" | "checking" | "succeeded" | "failed" | "unavailable";

export interface ConnectivityEntry {
  readonly id: string;
  readonly label: string;
  readonly provider: string;
  readonly supportsMultiTurn: boolean;
  readonly supportsThinking: boolean;
  /** Real latency returned by the Engine probe; null until a probe finishes. */
  readonly latencyMs: number | null;
  /** Sanitized provider diagnostic returned by the Engine probe. */
  readonly detail: string;
  readonly state: ConnectivityEntryState;
}

export type ConnectivityProbeResult = Pick<
  TestModelProfileResult,
  "ok" | "detail" | "latencyMs" | "supportsThinking" | "supportsMultiTurn"
>;

export interface ConnectivitySession {
  /** Mirrors the native manual diagnostic queue's two-worker ceiling. */
  readonly activeProfileIds: readonly string[];
  readonly entries: readonly ConnectivityEntry[];
  readonly runId: number;
}

const MAX_CONCURRENT_CONNECTIVITY_CHECKS = 2;

function isConfigured(statusLabel: string): boolean {
  return statusLabel.includes("已配置") || statusLabel.includes("已启用") || statusLabel.includes("本地草案");
}

function entryForProfile(profile: ModelProfileView): ConnectivityEntry {
  return {
    id: profile.id,
    label: profile.label,
    provider: profile.provider,
    supportsMultiTurn: profile.supportsMultiTurn,
    supportsThinking: profile.supportsThinking,
    latencyMs: null,
    detail: "",
    state: isConfigured(profile.statusLabel) ? "idle" : "unavailable",
  };
}

function queuedProfileIds(entries: readonly ConnectivityEntry[], availableSlots: number): readonly string[] {
  if (availableSlots <= 0) return [];
  const profileIds: string[] = [];
  for (const entry of entries) {
    if (entry.state !== "queued") continue;
    profileIds.push(entry.id);
    if (profileIds.length === availableSlots) break;
  }
  return profileIds;
}

/**
 * Sanitized representation of the Engine-owned connection-probe queue.
 * Credential material stays in the controller and is never exposed through
 * this snapshot rendered by React.
 */
export function createConnectivitySession(settings: Pick<SettingsView, "modelProfiles">): ConnectivitySession {
  return {
    activeProfileIds: [],
    entries: settings.modelProfiles.map(entryForProfile),
    runId: 0,
  };
}

/**
 * Reconcile the app-lifetime probe session with a refreshed Settings view.
 *
 * Provider diagnostics are valid for the lifetime of this desktop client. A
 * background settings refresh must therefore retain them. Only profiles whose
 * source fingerprint changed (or were newly added) receive a fresh idle state.
 * If a model changes while a probe queue is active, the queue is cancelled so
 * an old response can never be attached to the revised configuration.
 */
export function synchronizeConnectivitySession(
  session: ConnectivitySession,
  profiles: readonly ModelProfileView[],
  resetProfileIds: ReadonlySet<string>,
): ConnectivitySession {
  const previousEntries = new Map(session.entries.map((entry) => [entry.id, entry]));
  const cancelActiveProbes = resetProfileIds.size > 0;
  const entries = profiles.map((profile) => {
    const previous = previousEntries.get(profile.id);
    if (previous === undefined || resetProfileIds.has(profile.id)) return entryForProfile(profile);

    const wasInFlight = previous.state === "checking" || previous.state === "queued";
    const state = cancelActiveProbes && wasInFlight ? "idle" : previous.state;
    return {
      ...entryForProfile(profile),
      state,
      latencyMs: state === "idle" ? null : previous.latencyMs,
      detail: state === "idle" ? "" : previous.detail,
    };
  });

  const visibleProfileIds = new Set(profiles.map((profile) => profile.id));
  return {
    activeProfileIds: cancelActiveProbes
      ? []
      : session.activeProfileIds.filter((profileId) => visibleProfileIds.has(profileId)),
    entries,
    runId: cancelActiveProbes ? session.runId + 1 : session.runId,
  };
}

export function startConnectivityChecks(session: ConnectivitySession): ConnectivitySession {
  if (session.activeProfileIds.length > 0) return session;
  const checkingIds: string[] = [];
  for (const entry of session.entries) {
    if (entry.state === "unavailable") continue;
    checkingIds.push(entry.id);
    if (checkingIds.length === MAX_CONCURRENT_CONNECTIVITY_CHECKS) break;
  }
  const checkingIdSet = new Set(checkingIds);
  return {
    ...session,
    activeProfileIds: checkingIds,
    entries: session.entries.map((entry) => {
      if (entry.state === "unavailable") return entry;
      return { ...entry, state: checkingIdSet.has(entry.id) ? "checking" as const : "queued" as const };
    }),
    runId: session.runId + 1,
  };
}

export function resolveConnectivityCheck(
  session: ConnectivitySession,
  profileId: string,
  result: ConnectivityProbeResult = {
    ok: true,
    detail: "连接正常",
    latencyMs: null,
    supportsThinking: false,
    supportsMultiTurn: false,
  },
): ConnectivitySession {
  if (!session.activeProfileIds.includes(profileId)) return session;
  const remainingActiveIds = session.activeProfileIds.filter((id) => id !== profileId);
  const nextQueuedIds = queuedProfileIds(
    session.entries,
    MAX_CONCURRENT_CONNECTIVITY_CHECKS - remainingActiveIds.length,
  );
  const nextQueuedIdSet = new Set(nextQueuedIds);
  return {
    ...session,
    activeProfileIds: [...remainingActiveIds, ...nextQueuedIds],
    entries: session.entries.map((entry) => {
      if (entry.id === profileId) {
        return {
          ...entry,
          state: result.ok ? "succeeded" as const : "failed" as const,
          latencyMs: result.latencyMs,
          detail: result.detail,
          supportsThinking: result.ok ? result.supportsThinking : entry.supportsThinking,
          supportsMultiTurn: result.ok ? result.supportsMultiTurn : entry.supportsMultiTurn,
        };
      }
      if (nextQueuedIdSet.has(entry.id)) return { ...entry, state: "checking" as const };
      return entry;
    }),
  };
}

/** Resolve the oldest active request; useful for deterministic unit tests. */
export function resolveActiveConnectivityCheck(
  session: ConnectivitySession,
  result: ConnectivityProbeResult = {
    ok: true,
    detail: "连接正常",
    latencyMs: null,
    supportsThinking: false,
    supportsMultiTurn: false,
  },
): ConnectivitySession {
  const profileId = session.activeProfileIds[0];
  return profileId === undefined ? session : resolveConnectivityCheck(session, profileId, result);
}

export function cancelConnectivityChecks(session: ConnectivitySession): ConnectivitySession {
  if (session.activeProfileIds.length === 0 && !session.entries.some((entry) => entry.state === "queued")) return session;
  return {
    ...session,
    activeProfileIds: [],
    entries: session.entries.map((entry) => entry.state === "queued" || entry.state === "checking" ? { ...entry, state: "idle" } : entry),
  };
}

export function connectivityDetail(entry: ConnectivityEntry): string {
  switch (entry.state) {
    case "idle": return "待检测";
    case "queued": return "等待检测…";
    case "checking": return "检测中…";
    case "succeeded": return entry.latencyMs === null ? "连接正常" : `连通 ${entry.latencyMs}ms`;
    case "failed": return entry.detail || "连接失败";
    case "unavailable": return "未配置 API Key";
  }
}
