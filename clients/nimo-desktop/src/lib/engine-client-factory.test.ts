import { afterEach, describe, expect, it } from "vitest";

import {
  loadEngineClients,
  resetEngineClients,
  resolveEngineRuntimeConfig,
} from "./engine-client-factory";

function seedRuntimeConfig(
  config: Window["__NIMO_ENGINE_CONFIG__"] | undefined,
): void {
  Object.defineProperty(globalThis, "window", {
    configurable: true,
    value: config === undefined ? {} : { __NIMO_ENGINE_CONFIG__: config },
  });
}

afterEach(() => {
  resetEngineClients();
  Reflect.deleteProperty(globalThis, "window");
});

describe("runtime engine configuration", () => {
  it("prefers document-start runtime configuration over build defaults", async () => {
    seedRuntimeConfig({
      mode: "http",
      baseUrl: "https://engine.example.test/",
      accessToken: " ephemeral-token ",
    });

    expect(resolveEngineRuntimeConfig()).toEqual({
      mode: "legacy",
      baseUrl: "https://engine.example.test",
      accessToken: "ephemeral-token",
      readOnly: false,
      backendReload: false,
    });
    await expect(loadEngineClients()).resolves.toMatchObject({
      mode: "legacy",
      engineBaseUrl: "https://engine.example.test",
    });
  });

  it("rejects credential-bearing engine URLs", () => {
    seedRuntimeConfig({
      mode: "legacy",
      baseUrl: "https://user:secret@engine.example.test",
    });

    expect(() => resolveEngineRuntimeConfig()).toThrow(
      "must not contain credentials",
    );
  });

  it("loads and shares the development Mock client only on demand", async () => {
    seedRuntimeConfig({ mode: "mock", baseUrl: "http://127.0.0.1:8000" });

    const [first, second] = await Promise.all([
      loadEngineClients(),
      loadEngineClients(),
    ]);

    expect(first).toBe(second);
    expect(first.mode).toBe("mock");
    expect(first.engineClient.getWorkspace).toBeTypeOf("function");
  });

  it("never falls back to development Mock data in an unseeded native build", () => {
    Object.defineProperty(globalThis, "window", {
      configurable: true,
      value: { __TAURI_INTERNALS__: {} },
    });

    expect(resolveEngineRuntimeConfig()).toMatchObject({
      mode: "legacy",
      baseUrl: "http://127.0.0.1:8000",
    });
  });
});
