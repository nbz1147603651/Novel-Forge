import { afterEach, describe, expect, it, vi } from "vitest";
import { ProjectionPoller } from "./projection-poller";

afterEach(() => vi.useRealTimers());

describe("live reader projections", () => {
  it("publishes newly generated characters without blanking or repeating unchanged data", async () => {
    vi.useFakeTimers();
    const load = vi.fn().mockResolvedValueOnce({characters: []}).mockResolvedValue({characters: ["沈昭"]});
    const onValue = vi.fn();
    const poller = new ProjectionPoller({load, onValue, intervalMs: 5000});
    poller.start();
    await vi.advanceTimersByTimeAsync(0);
    expect(onValue).toHaveBeenLastCalledWith({characters: []});
    await vi.advanceTimersByTimeAsync(5000);
    expect(onValue).toHaveBeenLastCalledWith({characters: ["沈昭"]});
    await vi.advanceTimersByTimeAsync(10000);
    expect(onValue).toHaveBeenCalledTimes(2);
    poller.stop();
  });

  it("never overlaps slow requests and ignores late responses after project switches", async () => {
    vi.useFakeTimers();
    let resolve!: (value: string) => void;
    const load = vi.fn(() => new Promise<string>((done) => { resolve = done; }));
    const onValue = vi.fn();
    const poller = new ProjectionPoller({load, onValue, intervalMs: 5000});
    poller.start();
    await vi.advanceTimersByTimeAsync(20000);
    expect(load).toHaveBeenCalledTimes(1);
    poller.stop();
    resolve("old project");
    await vi.advanceTimersByTimeAsync(10000);
    expect(onValue).not.toHaveBeenCalled();
    expect(load).toHaveBeenCalledTimes(1);
  });

  it("keeps the previous content on failure and publishes recovery", async () => {
    vi.useFakeTimers();
    const load = vi.fn().mockResolvedValueOnce("saved").mockRejectedValueOnce(new Error("offline")).mockResolvedValue("saved");
    const onValue = vi.fn();
    const onError = vi.fn();
    const poller = new ProjectionPoller({load, onValue, onError, intervalMs: 5000});
    poller.start();
    await vi.advanceTimersByTimeAsync(5000);
    expect(onValue).toHaveBeenCalledTimes(1);
    expect(onError).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(5000);
    expect(onValue).toHaveBeenCalledTimes(2);
    poller.stop();
  });

  it("does only one read when the page is inactive", async () => {
    vi.useFakeTimers();
    const load = vi.fn().mockResolvedValue("saved");
    const poller = new ProjectionPoller({load, onValue: vi.fn()});
    poller.start();
    await vi.advanceTimersByTimeAsync(30000);
    expect(load).toHaveBeenCalledTimes(1);
    poller.stop();
  });
});
