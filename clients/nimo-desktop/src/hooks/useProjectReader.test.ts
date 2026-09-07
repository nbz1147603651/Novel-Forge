/**
 * Unit tests for the pure ``loadNarrativeTools`` helper used by
 * ``useNarrativeTools``. These tests guard the contract that
 * ``getNarrativeTools`` failures are translated into a user-visible error
 * message rather than being swallowed (the original bug that left 章台 stuck
 * on the loading skeleton).
 */

import { describe, expect, it } from "vitest";

import type {
  EngineClient,
  NarrativeToolsView,
} from "@nimo/engine-contracts";

import { loadNarrativeTools } from "./useProjectReader";

function makeTools(overrides: Partial<NarrativeToolsView> = {}): NarrativeToolsView {
  return {
    characters: [],
    characterDetails: [],
    relationships: [],
    subplots: [],
    ...overrides,
  } as NarrativeToolsView;
}

function makeClient(
  handler: (projectId: string) => Promise<NarrativeToolsView>,
): EngineClient {
  return {
    getNarrativeTools: (projectId: string) => handler(projectId),
  } as unknown as EngineClient;
}

describe("loadNarrativeTools", () => {
  it("returns the payload on success", async () => {
    const tools = makeTools();
    const client = makeClient(async () => tools);

    const result = await loadNarrativeTools(client, "proj-1");

    expect(result.status).toBe("ok");
    if (result.status === "ok") {
      expect(result.tools).toBe(tools);
    }
  });

  it("surfaces a 404 error from the engine as a user-visible message", async () => {
    const client = makeClient(async () => {
      throw new Error("Engine HTTP 404 at /api/v1/ui/projects/old/narrative-tools");
    });

    const result = await loadNarrativeTools(client, "old");

    expect(result.status).toBe("error");
    if (result.status === "error") {
      expect(result.message).toContain("Engine HTTP 404");
    }
  });

  it("falls back to a generic message when the rejection is not an Error", async () => {
    const client = makeClient(async () => {
      // String rejection (uncommon but defensive code path)
      // eslint-disable-next-line @typescript-eslint/no-throw-literal
      throw "network down";
    });

    const result = await loadNarrativeTools(client, "proj-2");

    expect(result.status).toBe("error");
    if (result.status === "error") {
      expect(result.message).toContain("proj-2");
      expect(result.message).toContain("叙事工具");
    }
  });

  it("is safe to call for sequential projectIds (useEffect re-fetch contract)", async () => {
    // The hook's useEffect deps include ``projectId`` so each project switch
    // re-runs ``loadNarrativeTools``; this test guards the contract that the
    // helper is pure-per-call and can be invoked repeatedly with different
    // projectIds.
    const calls: string[] = [];
    const client = makeClient(async (projectId) => {
      calls.push(projectId);
      return makeTools();
    });

    const first = await loadNarrativeTools(client, "proj-1");
    const second = await loadNarrativeTools(client, "proj-2");
    const third = await loadNarrativeTools(client, "proj-1");

    expect(calls).toEqual(["proj-1", "proj-2", "proj-1"]);
    expect(first.status).toBe("ok");
    expect(second.status).toBe("ok");
    expect(third.status).toBe("ok");
  });

  it("useNarrativeTools' useEffect re-fires when projectId changes (source-pinned regression test)", async () => {
    /**
     * Spec §2.4: "当 projectId 从 null → 'proj-1' 变化, hook 重新发起请求".
     *
     * The codebase has no React renderer (no jsdom / happy-dom /
     * react-test-renderer), so we cannot mount the hook and observe the
     * useEffect lifecycle. Instead we pin the useEffect dependency array
     * in the production source: as long as the dep array contains
     * ``projectId``, React guarantees the effect re-fires when the caller
     * changes projectId from null to "proj-1". A regression that drops
     * ``projectId`` from the deps would silently break the contract.
     *
     * The dynamic ``import("node:fs")`` is wrapped in a // @ts-expect-error
     * because the project's tsconfig only declares ``vite/client`` types.
     * Vitest still resolves and runs the import at test time.
     */
    // @ts-expect-error -- node:fs not in declared @types, only present at test time
    const { readFileSync } = await import("node:fs");
    const source: string = readFileSync(
      new URL("./useProjectReader.ts", import.meta.url),
      "utf-8",
    );

    // The useEffect block that wraps ``loadNarrativeTools`` must list
    // ``projectId`` in its dependency array.
    expect(source).toMatch(
      /useEffect\(\s*[\s\S]*?loadNarrativeTools[\s\S]*?\}\s*,\s*\[[\s\S]*?projectId[\s\S]*?\]\s*\)/,
    );
  });
});
