import { afterEach, describe, expect, it } from "vitest";

import {
  deriveEffectiveRewriteStrategy,
  loadChapterStudioPreferences,
  saveChapterStudioPreferences,
} from "./chapter-studio-ui-state";

class MemoryStorage {
  private readonly values = new Map<string, string>();

  getItem(key: string): string | null {
    return this.values.get(key) ?? null;
  }

  setItem(key: string, value: string): void {
    this.values.set(key, value);
  }

  clear(): void {
    this.values.clear();
  }
}

const storage = new MemoryStorage();

Object.defineProperty(globalThis, "localStorage", {
  configurable: true,
  value: storage,
});

describe("chapter studio ui state", () => {
  afterEach(() => {
    storage.clear();
  });

  it("returns empty preferences when nothing was saved", () => {
    expect(loadChapterStudioPreferences("demo")).toEqual({});
    expect(loadChapterStudioPreferences("")).toEqual({});
  });

  it("round-trips per-project preferences", () => {
    saveChapterStudioPreferences("alpha", {
      compositionMode: "scene",
      writingMode: "auto",
      rewriteStrategy: "reconstruct",
      selectedChapterNumber: 7,
    });
    saveChapterStudioPreferences("beta", { rewriteStrategy: "surgical" });

    expect(loadChapterStudioPreferences("alpha")).toEqual({
      compositionMode: "scene",
      writingMode: "auto",
      rewriteStrategy: "reconstruct",
      selectedChapterNumber: 7,
    });
    expect(loadChapterStudioPreferences("beta")).toEqual({
      rewriteStrategy: "surgical",
    });
  });

  it("drops invalid values when restoring", () => {
    localStorage.setItem(
      "nimo:chapter-studio-ui-state",
      JSON.stringify({
        version: 1,
        projects: {
          demo: {
            compositionMode: "bogus",
            writingMode: "hyperdrive",
            rewriteStrategy: "yolo",
            selectedChapterNumber: -3,
          },
        },
      }),
    );
    expect(loadChapterStudioPreferences("demo")).toEqual({});
  });

  it("ignores foreign storage versions", () => {
    localStorage.setItem(
      "nimo:chapter-studio-ui-state",
      JSON.stringify({ version: 9, projects: { demo: { rewriteStrategy: "auto" } } }),
    );
    expect(loadChapterStudioPreferences("demo")).toEqual({});
  });
});

describe("deriveEffectiveRewriteStrategy", () => {
  it("mirrors the backend _effective_strategy fallback rules", () => {
    // auto without force always degrades to sequential.
    expect(
      deriveEffectiveRewriteStrategy("auto", { force: false, hasDownstream: true }),
    ).toBe("sequential");
    // auto with force follows downstream availability.
    expect(
      deriveEffectiveRewriteStrategy("auto", { force: true, hasDownstream: true }),
    ).toBe("compatible");
    expect(
      deriveEffectiveRewriteStrategy("auto", { force: true, hasDownstream: false }),
    ).toBe("sequential");
    // compatible without downstream degrades to sequential.
    expect(
      deriveEffectiveRewriteStrategy("compatible", { force: false, hasDownstream: false }),
    ).toBe("sequential");
    // Explicit strategies otherwise pass through untouched.
    expect(
      deriveEffectiveRewriteStrategy("reconstruct", { force: false, hasDownstream: true }),
    ).toBe("reconstruct");
    expect(
      deriveEffectiveRewriteStrategy("surgical", { force: false, hasDownstream: false }),
    ).toBe("surgical");
  });
});
