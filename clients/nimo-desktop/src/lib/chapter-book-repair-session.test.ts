import { describe, expect, it } from "vitest";

import {
  buildExecuteGlobalRepairQueueCommand,
  defaultChapterBookRepairParameters,
  normalizeChapterBookRepairParameters,
} from "./chapter-book-repair-session";

describe("chapter book repair session", () => {
  it("builds a guarded ready-only repair command", () => {
    expect(buildExecuteGlobalRepairQueueCommand("novel-a", defaultChapterBookRepairParameters)).toEqual({
      kind: "execute_global_repair_queue",
      projectId: "novel-a",
      statuses: ["ready"],
      maxItems: 20,
      verifyBeforeApply: true,
      rollbackOnFailure: true,
      concurrency: 1,
    });
  });

  it("clamps batch size and concurrency at the Engine boundary", () => {
    expect(normalizeChapterBookRepairParameters({
      maxItems: 1000,
      verifyBeforeApply: true,
      rollbackOnFailure: true,
      concurrency: 0,
    })).toEqual({
      maxItems: 500,
      verifyBeforeApply: true,
      rollbackOnFailure: true,
      concurrency: 1,
    });
  });
});
