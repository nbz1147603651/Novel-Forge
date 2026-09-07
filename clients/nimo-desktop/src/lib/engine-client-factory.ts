/**
 * Engine client factory — selects the appropriate EngineClient implementation
 * based on the VITE_ENGINE_MODE environment variable.
 *
 * - `VITE_ENGINE_MODE=legacy` → LegacyLocalEngineClient (real Python backend)
 * - `VITE_ENGINE_MODE=mock` (default) → MockEngineClient (deterministic fixtures)
 *
 * The factory is the single composition point where the transport is chosen.
 * Page components never import a concrete client; they receive the contract
 * interface via App props.
 *
 * In legacy mode, capabilities negotiation runs at startup. If the backend
 * is unreachable or incompatible, the negotiation state surfaces a typed
 * error — the UI never silently falls back to Mock.
 */

import type { EngineClient, EngineCommandClient } from "@nimo/engine-contracts";

import { LegacyLocalEngineClient } from "./legacy-engine-client";
import {
  negotiateCapabilities,
  type NegotiationState,
} from "./engine-negotiation";

export type EngineMode = "mock" | "legacy";

export interface EngineRuntimeConfig {
  readonly mode: EngineMode;
  readonly baseUrl: string;
  readonly accessToken?: string;
  readonly expectedRevision?: string;
  readonly backendMode?: string;
  readonly readOnly: boolean;
  readonly startupDiagnostic?: string;
  readonly backendReload: boolean;
}

declare global {
  interface Window {
    /**
     * Immutable document-start configuration injected by the native launcher
     * or a cloud web host. Credentials are kept in memory and never persisted
     * to localStorage or compiled into the frontend bundle.
     */
    readonly __NIMO_ENGINE_CONFIG__?: {
      readonly mode?: string;
      readonly baseUrl?: string;
      readonly accessToken?: string;
      readonly expectedRevision?: string;
      readonly backendMode?: string;
      readonly readOnly?: boolean;
      readonly startupDiagnostic?: string;
      readonly backendReload?: boolean;
    };
  }
}

export interface EngineClients {
  readonly engineClient: EngineClient;
  readonly engineCommandClient: EngineCommandClient;
  readonly engineBaseUrl: string;
  readonly mode: EngineMode;
  readonly runtimeConfig: EngineRuntimeConfig;
}

function runtimeSeed(): Window["__NIMO_ENGINE_CONFIG__"] | undefined {
  return typeof window === "undefined" ? undefined : window.__NIMO_ENGINE_CONFIG__;
}

function normalizeBaseUrl(value: string): string {
  const trimmed = value.trim().replace(/\/+$/, "");
  if (trimmed === "") return "";
  const parsed = new URL(trimmed);
  if (parsed.protocol !== "http:" && parsed.protocol !== "https:") {
    throw new Error("NIMO Engine URL must use http or https.");
  }
  if (parsed.username || parsed.password) {
    throw new Error("NIMO Engine URL must not contain credentials.");
  }
  return trimmed;
}

export function resolveEngineRuntimeConfig(): EngineRuntimeConfig {
  const seed = runtimeSeed();
  const nativeDefaultMode = typeof window !== "undefined" && "__TAURI_INTERNALS__" in window
    ? "legacy"
    : "mock";
  const rawMode = (
    seed?.mode
    ?? import.meta.env.VITE_ENGINE_MODE
    ?? nativeDefaultMode
  ).toLowerCase().trim();
  const mode: EngineMode =
    rawMode === "legacy" || rawMode === "http" ? "legacy" : "mock";
  const baseUrl = normalizeBaseUrl(
    seed?.baseUrl
    ?? import.meta.env.VITE_ENGINE_BASE_URL
    ?? "http://127.0.0.1:8000",
  );
  const accessToken = seed?.accessToken?.trim();
  const expectedRevision = seed?.expectedRevision?.trim();
  const backendMode = seed?.backendMode?.trim();
  const startupDiagnostic = seed?.startupDiagnostic?.trim();
  return {
    mode,
    baseUrl,
    ...(accessToken ? { accessToken } : {}),
    ...(expectedRevision ? { expectedRevision } : {}),
    ...(backendMode ? { backendMode } : {}),
    readOnly: seed?.readOnly === true,
    ...(startupDiagnostic ? { startupDiagnostic } : {}),
    backendReload: seed?.backendReload === true,
  };
}

let cached: EngineClients | null = null;
let pendingClients: Promise<EngineClients> | null = null;
let negotiationResult: NegotiationState = { status: "negotiating" };

function createLegacyEngineClients(runtime: EngineRuntimeConfig): EngineClients {
  const client = new LegacyLocalEngineClient({
    baseUrl: runtime.baseUrl,
    timeoutMs: 15_000,
    maxRetries: 1,
    ...(runtime.accessToken ? { token: runtime.accessToken } : {}),
  });
  return {
    engineClient: client,
    engineCommandClient: client,
    engineBaseUrl: runtime.baseUrl,
    mode: runtime.mode,
    runtimeConfig: runtime,
  };
}

/**
 * Resolve the transport at the application composition boundary.
 *
 * Mock project fixtures are intentionally imported only for mock launches.
 * Native and HTTP production launches therefore avoid downloading and parsing
 * the large deterministic development catalog.
 */
export async function loadEngineClients(): Promise<EngineClients> {
  if (cached !== null) return cached;
  if (pendingClients !== null) return pendingClients;

  const runtime = resolveEngineRuntimeConfig();
  if (runtime.mode === "legacy") {
    cached = createLegacyEngineClients(runtime);
    return cached;
  }

  const request = import("./mock-engine")
    .then(({ mockEngineClient, mockEngineCommandClient }) => {
      const clients: EngineClients = {
        engineClient: mockEngineClient,
        engineCommandClient: mockEngineCommandClient,
        engineBaseUrl: runtime.baseUrl,
        mode: runtime.mode,
        runtimeConfig: runtime,
      };
      cached = clients;
      return clients;
    });
  pendingClients = request;
  try {
    return await request;
  } finally {
    // A hot reload can start a newer request while this one is resolving.
    // Only the matching request may release the shared in-flight slot.
    if (pendingClients === request) pendingClients = null;
  }
}

/**
 * Run capabilities negotiation against the backend.
 *
 * In mock mode this immediately returns a synthetic "connected" state.
 * In legacy mode it fetches real capabilities and validates the contract.
 * The result is cached; call `resetEngineClients()` to force re-negotiation.
 */
export async function negotiateEngine(): Promise<NegotiationState> {
  const { mode, engineBaseUrl } = await loadEngineClients();

  if (mode === "mock") {
    negotiationResult = {
      status: "connected",
      capabilities: {
        contractVersion: "1.0",
        apiVersion: "v1",
        transports: ["mock"],
        features: { job_queries: true, engine_commands: true },
        commands: {
          prepare_chapter: true,
          cancel_job: true,
          continue_long_init: true,
          repair_continuity: true,
          repair_causal: true,
          repair_issues: true,
          reevaluate_chapter: true,
          reextract_relationships: true,
          repair_motif_history: true,
          audit_book: true,
          audit_book_editorial: true,
          execute_global_repair_queue: true,
          export_book: true,
          retry_init_repair: true,
          save_init_manual_repair: true,
          rebuild_memory_vectors: true,
          restart_long_init: true,
          start_workflow: true,
          generate_workflow_fields: true,
          synthesize_voice: true,
          resolve_speakers: true,
          export_audio: false,
        },
      },
    };
    return negotiationResult;
  }

  negotiationResult = await negotiateCapabilities(engineBaseUrl);
  return negotiationResult;
}

/**
 * Return the current negotiation state (synchronous accessor for React renders).
 */
export function getNegotiationState(): NegotiationState {
  return negotiationResult;
}

/**
 * Reset the cached clients and negotiation state.
 * Useful for testing or hot-reload scenarios.
 */
export function resetEngineClients(): void {
  cached = null;
  pendingClients = null;
  negotiationResult = { status: "negotiating" };
}
