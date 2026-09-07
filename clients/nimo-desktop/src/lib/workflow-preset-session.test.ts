import { describe, expect, it } from "vitest";

import { allDiffIds, nextSelectedIds } from "./workflow-preset-session";

describe("review selection helpers", () => {
  const diffs = [
    { id: "premise", key: "core_premise", label: "核心前提", oldValue: "旧前提", newValue: "新前提" },
    { id: "theme", key: "theme", label: "主题", oldValue: "旧主题", newValue: "新主题" },
  ] as const;

  it("toggles selection and enumerates all diff ids deterministically", () => {
    expect([...nextSelectedIds(new Set(["premise"]), "premise")]).toEqual([]);
    expect([...nextSelectedIds(new Set(), "theme")]).toEqual(["theme"]);
    expect([...allDiffIds(diffs)]).toEqual(["premise", "theme"]);
  });
});
