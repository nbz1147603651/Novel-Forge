import type {
  EngineCommandClient,
  ModelProfileView,
  SettingsView,
} from "@nimo/engine-contracts";

import {
  cancelConnectivityChecks,
  createConnectivitySession,
  resolveConnectivityCheck,
  startConnectivityChecks,
  synchronizeConnectivitySession,
  type ConnectivityProbeResult,
  type ConnectivitySession,
} from "./connectivity-session";
import type { ModelProfileConnectionState } from "./model-routing-session";

export interface ConnectivityScheduler {
  readonly cancelAnimationFrame: (id: number) => void;
  readonly requestAnimationFrame: (callback: FrameRequestCallback) => number;
}

function browserScheduler(): ConnectivityScheduler {
  return {
    cancelAnimationFrame: (id) => window.cancelAnimationFrame(id),
    requestAnimationFrame: (callback) => window.requestAnimationFrame(callback),
  };
}

type ConnectivityCommandClient = Pick<EngineCommandClient, "testModelProfile">;

interface ConnectivityProfileSource {
  readonly profile: ModelProfileView;
  readonly connection?: ModelProfileConnectionState | undefined;
}

interface CachedProbeResult {
  readonly at: number;
  readonly fingerprint: string;
  readonly result: ConnectivityProbeResult;
}

function probeFingerprint(source: ConnectivityProfileSource): string {
  const connection = source.connection;
  // Do not include the raw key in a diagnostic/cache object. It is compared
  // transiently by `sameProbeSource` while synchronizing and never stored in
  // a cache or rendered snapshot.
  return [
    source.profile.id,
    source.profile.label,
    source.profile.provider,
    source.profile.model,
    source.profile.tierLabel,
    source.profile.statusLabel,
    source.profile.supportsThinking ? "thinking" : "no-thinking",
    source.profile.supportsMultiTurn ? "multi-turn" : "single-turn",
    source.profile.maskedKey ?? "",
    connection?.baseUrl ?? source.profile.baseUrl ?? "",
    connection?.apiKeyAction ?? "preserve",
    source.profile.keyConfigured ? "configured" : "missing",
    source.profile.isEmbedding ? "embedding" : "generation",
    connection?.previousId ?? "",
  ].join("\u0001");
}

function sameProbeSource(
  previous: ConnectivityProfileSource,
  next: ConnectivityProfileSource,
): boolean {
  return probeFingerprint(previous) === probeFingerprint(next)
    && (previous.connection?.apiKey ?? "") === (next.connection?.apiKey ?? "");
}

/**
 * App-lifetime façade for bounded, Engine-backed model diagnostics.
 *
 * The profile list is synchronized from the editable routing session, so
 * adding, editing, or removing a profile cannot leave stale cards mounted
 * behind the model-management modal. Result updates are rAF-batched and the
 * Engine is asked to probe at most two providers in parallel.
 */
export class ConnectivityController {
  private animationFrameId: number | null = null;
  private readonly listeners = new Set<() => void>();
  private session: ConnectivitySession;
  private readonly activeProbeRuns = new Map<string, number>();
  private sources = new Map<string, ConnectivityProfileSource>();
  /** Cache: profileId → a real, sanitized Engine probe result. */
  private readonly resultCache = new Map<string, CachedProbeResult>();
  /** Successful check results are cached to speed up an explicit recheck (ms). */
  private readonly successTtlMs: number;
  /** Failed check results use a shorter TTL to allow quick retry. */
  private readonly failureTtlMs: number;

  constructor(
    settings: SettingsView,
    private readonly commandClient: ConnectivityCommandClient,
    private readonly scheduler: ConnectivityScheduler = browserScheduler(),
    successTtlMs = 60_000,
    failureTtlMs = 15_000,
  ) {
    this.session = createConnectivitySession(settings);
    this.sources = new Map(settings.modelProfiles.map((profile) => [profile.id, { profile }]));
    this.successTtlMs = successTtlMs;
    this.failureTtlMs = failureTtlMs;
  }

  readonly getSnapshot = (): ConnectivitySession => this.session;

  readonly subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  };

  /**
   * Synchronize the rendered source of truth with the editable model draft.
   *
   * Completed diagnostics belong to this controller's client lifetime, not a
   * SettingsPage mount. An unchanged profile keeps its displayed result across
   * navigation, settings reloads, and unrelated saves. A changed/added/deleted
   * profile invalidates only the affected diagnostic; changing any source also
   * cancels an in-flight batch so old requests cannot write into new metadata.
   */
  syncProfiles(
    profiles: readonly ModelProfileView[],
    connectionDrafts: Readonly<Record<string, ModelProfileConnectionState>> = {},
  ): void {
    const nextSources = new Map(profiles.map((profile) => [profile.id, {
      profile,
      connection: connectionDrafts[profile.id],
    }]));
    const resetProfileIds = new Set<string>();
    for (const [profileId, nextSource] of nextSources) {
      const previousSource = this.sources.get(profileId);
      if (previousSource === undefined || !sameProbeSource(previousSource, nextSource)) {
        resetProfileIds.add(profileId);
      }
    }
    for (const profileId of this.sources.keys()) {
      if (!nextSources.has(profileId)) resetProfileIds.add(profileId);
    }

    if (resetProfileIds.size > 0) {
      this.activeProbeRuns.clear();
      resetProfileIds.forEach((profileId) => this.resultCache.delete(profileId));
    }
    this.sources = nextSources;
    this.session = synchronizeConnectivitySession(this.session, profiles, resetProfileIds);
    this.notifyImmediately();
  }

  start(): void {
    const next = startConnectivityChecks(this.session);
    if (next === this.session) return;
    this.session = this.resolveFreshCachedChecks(next);
    // The click must show queued/checking state in the same interaction frame.
    // Subsequent real provider probe completions use rAF batching below.
    this.notifyImmediately();
    this.startActiveProbes();
  }

  /** Invalidate cache for a specific profile (useful to host integrations). */
  invalidate(profileId: string): void {
    this.resultCache.delete(profileId);
  }

  /** Invalidate every cached result (useful to host integrations). */
  invalidateAll(): void {
    this.resultCache.clear();
  }

  cancel(): void {
    const next = cancelConnectivityChecks(this.session);
    if (next === this.session) return;
    this.session = next;
    this.activeProbeRuns.clear();
    this.notifyImmediately();
  }

  dispose(): void {
    this.activeProbeRuns.clear();
    if (this.animationFrameId !== null) {
      this.scheduler.cancelAnimationFrame(this.animationFrameId);
      this.animationFrameId = null;
    }
    this.listeners.clear();
  }

  private resolveFreshCachedChecks(started: ConnectivitySession): ConnectivitySession {
    let next = started;
    while (true) {
      const profileId = next.activeProfileIds.find((id) => this.cachedResultFor(id) !== undefined);
      if (profileId === undefined) return next;
      const cached = this.cachedResultFor(profileId);
      if (cached === undefined) return next;
      next = resolveConnectivityCheck(next, profileId, cached.result);
    }
  }

  private cachedResultFor(profileId: string): CachedProbeResult | undefined {
    const source = this.sources.get(profileId);
    const cached = this.resultCache.get(profileId);
    if (source === undefined || cached === undefined || cached.fingerprint !== probeFingerprint(source)) {
      return undefined;
    }
    const ttl = cached.result.ok ? this.successTtlMs : this.failureTtlMs;
    return Date.now() - cached.at < ttl ? cached : undefined;
  }

  private startActiveProbes(): void {
    const runId = this.session.runId;
    for (const profileId of this.session.activeProfileIds) {
      if (this.activeProbeRuns.get(profileId) === runId) continue;
      const source = this.sources.get(profileId);
      if (source === undefined) {
        this.completeProbe(profileId, runId, {
          ok: false,
          detail: "模型档案已不在当前设置中。",
          latencyMs: null,
          supportsThinking: false,
          supportsMultiTurn: false,
        });
        continue;
      }
      this.activeProbeRuns.set(profileId, runId);
      const connection = source.connection;
      void this.commandClient.testModelProfile({
        kind: "test_model_profile",
        id: source.profile.id,
        previousId: connection?.previousId,
        provider: source.profile.provider,
        model: source.profile.model,
        apiKeyAction: connection?.apiKeyAction ?? "preserve",
        apiKey: connection?.apiKey || undefined,
        baseUrl: connection?.baseUrl ?? source.profile.baseUrl ?? "",
      }).then(
        (result) => this.completeProbe(profileId, runId, result),
        (error: unknown) => this.completeProbe(profileId, runId, {
          ok: false,
          detail: `探活请求失败：${error instanceof Error ? error.message : "本地引擎无响应"}`,
          latencyMs: null,
          supportsThinking: false,
          supportsMultiTurn: false,
        }),
      );
    }
  }

  private completeProbe(
    profileId: string,
    runId: number,
    result: ConnectivityProbeResult,
  ): void {
    if (this.activeProbeRuns.get(profileId) !== runId || this.session.runId !== runId) return;
    this.activeProbeRuns.delete(profileId);
    if (!this.session.activeProfileIds.includes(profileId)) return;
    const source = this.sources.get(profileId);
    if (source !== undefined) {
      this.resultCache.set(profileId, {
        at: Date.now(),
        fingerprint: probeFingerprint(source),
        result,
      });
    }
    this.session = resolveConnectivityCheck(this.session, profileId, result);
    this.startActiveProbes();
    this.notifyAtNextFrame();
  }

  private notifyImmediately(): void {
    this.listeners.forEach((listener) => listener());
  }

  private notifyAtNextFrame(): void {
    if (this.animationFrameId !== null) return;
    this.animationFrameId = this.scheduler.requestAnimationFrame(() => {
      this.animationFrameId = null;
      this.notifyImmediately();
    });
  }
}
