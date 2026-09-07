/**
 * EngineRuntimeContext — provides engine mode, capabilities, and connection
 * state to all page components without prop drilling.
 *
 * Pages use `useContext(EngineRuntimeContext)` to determine:
 * - Whether the app runs in "mock" or "legacy" mode
 * - Which commands are available (via negotiated capabilities)
 * - Which features are enabled
 *
 * Pages never read VITE_ENGINE_MODE directly; they rely on this context.
 */

import { createContext, useContext } from "react";

import type { NegotiationState } from "./engine-negotiation";
import type { EngineRuntimeDiagnostic } from "./engine-runtime-status";

// ── Types ─────────────────────────────────────────────────────────────────────

export interface EngineRuntimeState {
  readonly mode: "mock" | "legacy";
  readonly negotiation: NegotiationState;
  readonly diagnostic: EngineRuntimeDiagnostic;
  readonly canSubmitTasks: boolean;
  readonly isCommandAvailable: (command: string) => boolean;
  readonly isFeatureEnabled: (feature: string) => boolean;
}

// ── Default (mock mode, no backend) ──────────────────────────────────────────

const DEFAULT_STATE: EngineRuntimeState = {
  mode: "mock",
  negotiation: { status: "negotiating" },
  diagnostic: { connection: "checking", message: "正在检查 Engine。", canSubmitTasks: false },
  canSubmitTasks: false,
  isCommandAvailable: () => true,
  isFeatureEnabled: () => true,
};

// ── Context ───────────────────────────────────────────────────────────────────

export const EngineRuntimeContext = createContext<EngineRuntimeState>(DEFAULT_STATE);

/**
 * Convenience hook for consuming the engine runtime context.
 */
export function useEngineRuntime(): EngineRuntimeState {
  return useContext(EngineRuntimeContext);
}
