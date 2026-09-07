/**
 * Engine capabilities negotiation — validates backend compatibility at startup.
 *
 * In `legacy` mode the UI fetches `/api/v1/engine/capabilities` before
 * rendering data. If the backend is unreachable, uses an incompatible
 * contract version, or declares commands the client does not support, the
 * negotiation result surfaces a typed error state that the shell renders
 * as a full-screen diagnostic instead of silently falling back to Mock.
 *
 * Design constraints:
 * - Never swallow errors in legacy mode; the user must see why data is absent.
 * - Contract version uses semver major comparison: 1.x is compatible with 1.y.
 * - Per-command availability is exposed so pages can disable unsupported actions.
 */

import type { EngineClient } from "@nimo/engine-contracts";

// ── Types ─────────────────────────────────────────────────────────────────────

export interface EngineCapabilities {
  readonly contractVersion: string;
  readonly apiVersion: string;
  readonly transports: readonly string[];
  readonly features: Readonly<Record<string, boolean>>;
  readonly commands: Readonly<Record<string, boolean>>;
}

export type NegotiationState =
  | { readonly status: "negotiating" }
  | { readonly status: "connected"; readonly capabilities: EngineCapabilities }
  | { readonly status: "incompatible"; readonly contractVersion: string; readonly expectedMajor: number }
  | { readonly status: "unavailable"; readonly reason: string };

const EXPECTED_CONTRACT_MAJOR = 1;

// ── Negotiation logic ─────────────────────────────────────────────────────────

function parseMajor(version: string): number {
  const dot = version.indexOf(".");
  const majorStr = dot === -1 ? version : version.slice(0, dot);
  const parsed = parseInt(majorStr, 10);
  return Number.isNaN(parsed) ? 0 : parsed;
}

/**
 * Fetch and validate engine capabilities from the backend.
 *
 * @param baseUrl - The engine HTTP base URL (e.g. http://127.0.0.1:8000)
 * @param timeoutMs - Maximum wait time for the capabilities response
 * @returns A typed negotiation result; never throws.
 */
export async function negotiateCapabilities(
  baseUrl: string,
  timeoutMs = 8_000,
): Promise<NegotiationState> {
  const url = `${baseUrl}/api/v1/engine/capabilities`;
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);

  try {
    const response = await fetch(url, { signal: controller.signal });

    if (!response.ok) {
      return {
        status: "unavailable",
        reason: `后端返回 HTTP ${response.status}，无法完成能力协商`,
      };
    }

    const raw = (await response.json()) as Record<string, unknown>;

    // The backend uses camelCase aliases via EngineViewModel.
    const contractVersion = String(raw["contractVersion"] ?? raw["contract_version"] ?? "");
    const apiVersion = String(raw["apiVersion"] ?? raw["api_version"] ?? "");
    const transports = Array.isArray(raw["transports"]) ? (raw["transports"] as string[]) : [];
    const features = (raw["features"] ?? {}) as Record<string, boolean>;
    const commands = (raw["commands"] ?? {}) as Record<string, boolean>;

    if (!contractVersion) {
      return {
        status: "unavailable",
        reason: "后端响应缺少 contractVersion 字段，可能不是有效的 Engine 服务",
      };
    }

    const serverMajor = parseMajor(contractVersion);
    if (serverMajor !== EXPECTED_CONTRACT_MAJOR) {
      return {
        status: "incompatible",
        contractVersion,
        expectedMajor: EXPECTED_CONTRACT_MAJOR,
      };
    }

    return {
      status: "connected",
      capabilities: {
        contractVersion,
        apiVersion,
        transports,
        features,
        commands,
      },
    };
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") {
      return {
        status: "unavailable",
        reason: `连接超时（${timeoutMs}ms），请确认 Python 后端已启动`,
      };
    }
    const message = error instanceof Error ? error.message : String(error);
    return {
      status: "unavailable",
      reason: `无法连接后端: ${message}`,
    };
  } finally {
    clearTimeout(timer);
  }
}

/**
 * Check whether a specific command is available according to negotiated capabilities.
 */
export function isCommandAvailable(
  state: NegotiationState,
  command: string,
): boolean {
  if (state.status !== "connected") return false;
  return state.capabilities.commands[command] === true;
}

/**
 * Check whether a specific feature flag is enabled.
 */
export function isFeatureEnabled(
  state: NegotiationState,
  feature: string,
): boolean {
  if (state.status !== "connected") return false;
  return state.capabilities.features[feature] === true;
}
