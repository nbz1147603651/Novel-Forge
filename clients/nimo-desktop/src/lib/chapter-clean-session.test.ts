import type { EngineCommandClient } from "@nimo/engine-contracts";
import { describe, expect, it, vi } from "vitest";

import { createChapterCleanRequest, describeChapterCleanFailure, executeChapterClean } from "./chapter-clean-session";

describe("chapter clean session", () => {
  it("normalizes browser values to the source QSpinBox range", () => {
    expect(createChapterCleanRequest({ cutoff: -4, maxChapter: 24 })).toEqual({ cutoff: 1 });
    expect(createChapterCleanRequest({ cutoff: 99, maxChapter: 24 })).toEqual({ cutoff: 24 });
    expect(createChapterCleanRequest({ cutoff: Number.NaN, maxChapter: 24 })).toEqual({ cutoff: 1 });
  });

  it("refreshes after verified success or a backend-reported partial write", async () => {
    const accepted = vi.fn();
    const cleanChapters = vi.fn().mockResolvedValue({
      status: "accepted",
      message: "已清理",
    });

    await executeChapterClean({
      client: { cleanChapters } as unknown as EngineCommandClient,
      projectId: "book",
      cutoff: 3,
      onRefreshRequired: accepted,
    });

    expect(cleanChapters).toHaveBeenCalledWith({
      kind: "clean_chapters",
      projectId: "book",
      fromChapter: 3,
    });
    expect(accepted).toHaveBeenCalledWith(3);

    cleanChapters.mockResolvedValueOnce({ status: "rejected", message: "仍在运行" });
    await executeChapterClean({
      client: { cleanChapters } as unknown as EngineCommandClient,
      projectId: "book",
      cutoff: 4,
      onRefreshRequired: accepted,
    });
    expect(accepted).toHaveBeenCalledTimes(1);

    cleanChapters.mockResolvedValueOnce({
      status: "rejected",
      message: "清理未收敛",
      data: { cleanupComplete: false, refreshRequired: true },
    });
    await executeChapterClean({
      client: { cleanChapters } as unknown as EngineCommandClient,
      projectId: "book",
      cutoff: 5,
      onRefreshRequired: accepted,
    });
    expect(accepted).toHaveBeenLastCalledWith(5);
    expect(accepted).toHaveBeenCalledTimes(2);
  });

  it("does not claim that a transport failure left destructive state unchanged", () => {
    expect(describeChapterCleanFailure(new Error("连接已中断"))).toContain("未能确认");
    expect(describeChapterCleanFailure(new Error("连接已中断"))).toContain("不要立即重复清理");
    expect(describeChapterCleanFailure(new Error("连接已中断"))).not.toContain("均保持不变");
  });
});
