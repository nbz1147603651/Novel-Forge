/**
 * ChapterStudioPoller tests — the polling channel that surfaces Engine
 * autorun state (studio.autorun) to the chapter-studio page.
 */

import { afterEach, describe, expect, it, vi } from "vitest";

import type { ChapterStudioView } from "@nimo/engine-contracts";

import { ChapterStudioPoller } from "./chapter-studio-poller";

function studio(projectId: string): ChapterStudioView {
  return { projectId } as ChapterStudioView;
}

describe("ChapterStudioPoller", () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  it("polls the studio projection periodically and applies fresh results", async () => {
    vi.useFakeTimers();
    const fetchStudio = vi.fn().mockResolvedValue(studio("a"));
    const onStudio = vi.fn();
    const poller = new ChapterStudioPoller({ fetchStudio, onStudio, intervalMs: 2_500 });
    poller.start("a");

    expect(fetchStudio).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(2_500);
    expect(fetchStudio).toHaveBeenCalledWith("a");
    expect(onStudio).toHaveBeenCalledWith(studio("a"));
    poller.stop();
  });

  it("stops polling after stop and drops in-flight results", async () => {
    vi.useFakeTimers();
    let resolveFetch: (value: ChapterStudioView) => void = () => {};
    const fetchStudio = vi.fn().mockImplementation(
      () => new Promise<ChapterStudioView>((resolve) => {
        resolveFetch = resolve;
      }),
    );
    const onStudio = vi.fn();
    const poller = new ChapterStudioPoller({ fetchStudio, onStudio, intervalMs: 10 });
    poller.start("a");
    await vi.advanceTimersByTimeAsync(10);
    expect(fetchStudio).toHaveBeenCalledTimes(1);

    poller.stop();
    resolveFetch(studio("a"));
    await vi.advanceTimersByTimeAsync(100);
    expect(onStudio).not.toHaveBeenCalled();
  });

  it("does not overlap requests while a slow poll is still in flight", async () => {
    vi.useFakeTimers();
    let resolveFetch: (value: ChapterStudioView) => void = () => {};
    const fetchStudio = vi.fn().mockImplementation(
      () => new Promise<ChapterStudioView>((resolve) => {
        resolveFetch = resolve;
      }),
    );
    const onStudio = vi.fn();
    const poller = new ChapterStudioPoller({ fetchStudio, onStudio, intervalMs: 10 });
    poller.start("a");

    await vi.advanceTimersByTimeAsync(30);
    expect(fetchStudio).toHaveBeenCalledTimes(1);

    resolveFetch(studio("a"));
    await vi.advanceTimersByTimeAsync(0);
    expect(onStudio).toHaveBeenCalledWith(studio("a"));
    poller.stop();
  });

  it("keeps the last loaded view when a poll fails", async () => {
    vi.useFakeTimers();
    const fetchStudio = vi.fn()
      .mockRejectedValueOnce(new Error("engine down"))
      .mockResolvedValue(studio("a"));
    const onStudio = vi.fn();
    const poller = new ChapterStudioPoller({ fetchStudio, onStudio, intervalMs: 2_500 });
    poller.start("a");

    await vi.advanceTimersByTimeAsync(2_500);
    expect(onStudio).not.toHaveBeenCalled();

    await vi.advanceTimersByTimeAsync(2_500);
    expect(onStudio).toHaveBeenCalledWith(studio("a"));
    poller.stop();
  });

});
