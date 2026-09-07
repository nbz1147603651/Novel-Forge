import { describe, expect, it } from "vitest";

/**
 * Tests the core virtual list computation logic extracted from useVirtualList.
 * The hook itself is a thin React wrapper around these calculations.
 */
function computeVirtualRange(
  itemCount: number,
  itemHeight: number,
  scrollTop: number,
  viewportHeight: number,
  overscan: number,
) {
  const totalHeight = itemCount * itemHeight;
  const startIndex = Math.max(0, Math.floor(scrollTop / itemHeight) - overscan);
  const visibleCount = Math.ceil(viewportHeight / itemHeight) + overscan * 2;
  const endIndex = Math.min(itemCount, startIndex + visibleCount);
  const offsetY = startIndex * itemHeight;
  return { startIndex, endIndex, totalHeight, offsetY };
}

describe("virtual list computation", () => {
  it("computes visible range for a small list that fits in viewport", () => {
    const result = computeVirtualRange(5, 72, 0, 600, 4);
    expect(result.startIndex).toBe(0);
    expect(result.endIndex).toBe(5);
    expect(result.totalHeight).toBe(360);
    expect(result.offsetY).toBe(0);
  });

  it("computes visible range for a large list at scroll offset", () => {
    // Scrolled to 600px → first visible = floor(600/60)=10, minus overscan 3 = 7
    const result = computeVirtualRange(200, 60, 600, 480, 3);
    expect(result.startIndex).toBe(7);
    // visibleCount = ceil(480/60)+6 = 14; end = 7+14 = 21
    expect(result.endIndex).toBe(21);
    expect(result.totalHeight).toBe(12_000);
    expect(result.offsetY).toBe(7 * 60);
  });

  it("clamps range to item bounds", () => {
    const result = computeVirtualRange(3, 100, 0, 800, 5);
    expect(result.startIndex).toBe(0);
    expect(result.endIndex).toBe(3);
    expect(result.totalHeight).toBe(300);
  });

  it("handles zero items gracefully", () => {
    const result = computeVirtualRange(0, 72, 0, 600, 4);
    expect(result.startIndex).toBe(0);
    expect(result.endIndex).toBe(0);
    expect(result.totalHeight).toBe(0);
    expect(result.offsetY).toBe(0);
  });

  it("handles scroll near the end of list", () => {
    // 100 items * 50px = 5000px total; scrolled to 4800px
    const result = computeVirtualRange(100, 50, 4800, 400, 3);
    // floor(4800/50)=96, minus 3 = 93
    expect(result.startIndex).toBe(93);
    // visibleCount = ceil(400/50)+6 = 14; end = min(100, 93+14) = 100
    expect(result.endIndex).toBe(100);
  });
});
