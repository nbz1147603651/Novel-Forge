import { describe, expect, it } from "vitest";

import type { EngineRuntimeView } from "@nimo/engine-contracts";

import type { EngineRuntimeConfig } from "./engine-client-factory";
import {
  isNimoManagedEngine,
  projectEngineRuntimeDiagnostic,
} from "./engine-runtime-status";

const config: EngineRuntimeConfig = {
  mode: "legacy", baseUrl: "http://127.0.0.1:8000", readOnly: false,
  backendReload: false, expectedRevision: "src-old",
};
const runtime: EngineRuntimeView = {
  contractVersion: "1.0", status: "ready", bootRevision: "src-new", currentRevision: "src-new",
  instanceId: "replacement", managedBy: "nimo", startedAt: "2026-08-27T10:00:00Z",
  activeJobCount: 0, queuedJobCount: 0, canSubmitTasks: true,
};

describe("Engine runtime recovery", () => {
  it("accepts a healthy restarted backend without reopening the client", () => {
    expect(projectEngineRuntimeDiagnostic(runtime, config).canSubmitTasks).toBe(true);
    expect(projectEngineRuntimeDiagnostic(runtime, config).connection).toBe("ready");
  });

  it("recognizes the primary and compatibility launcher identities", () => {
    expect(isNimoManagedEngine(runtime)).toBe(true);
    expect(isNimoManagedEngine({ ...runtime, managedBy: "nimo-t" })).toBe(true);
    expect(isNimoManagedEngine({ ...runtime, managedBy: "external" })).toBe(false);
  });

  it("continues protecting a backend whose loaded source is stale", () => {
    const diagnostic = projectEngineRuntimeDiagnostic({ ...runtime, bootRevision: "src-old" }, config);
    expect(diagnostic.canSubmitTasks).toBe(false);
    expect(diagnostic.connection).toBe("restartRequired");
  });

  it("never overrides explicit read-only or server submission protection", () => {
    expect(projectEngineRuntimeDiagnostic(runtime, { ...config, readOnly: true }).canSubmitTasks).toBe(false);
    expect(projectEngineRuntimeDiagnostic({ ...runtime, canSubmitTasks: false }, config).canSubmitTasks).toBe(false);
  });
});
