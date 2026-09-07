import { describe, expect, it } from "vitest";

import { detectStreamAnomalies } from "./stream-anomaly";

describe("stream anomaly detection", () => {
  it("flags adjacent duplicate segments", () => {
    const report = detectStreamAnomalies(
      [
        { text: "旁白一" },
        { text: "旁白一" },
        { text: "正常段" },
      ],
      30,
      0,
    );
    expect(report.duplicateIndices).toEqual([1]);
    expect(report.hasAnomalies).toBe(true);
  });

  it("flags A-B-A sliding-window duplicates", () => {
    const report = detectStreamAnomalies(
      [
        { text: "A" },
        { text: "B" },
        { text: "A" },
      ],
      10,
      0,
    );
    expect(report.duplicateIndices).toEqual([2]);
  });

  it("flags JSON structural echoes leaked into prose", () => {
    const report = detectStreamAnomalies(
      [{ text: '这里混入了 "segment_type": "dialogue" 结构残留' }],
      40,
      0,
    );
    expect(report.jsonEchoIndices).toEqual([0]);
    expect(report.hasAnomalies).toBe(true);
  });

  it("flags character inflation against the source length", () => {
    const report = detectStreamAnomalies([{ text: "很长" }], 100, 20);
    expect(report.charInflation).toBe(true);
    expect(report.hasAnomalies).toBe(true);
  });

  it("skips inflation when source length is unknown", () => {
    const report = detectStreamAnomalies([{ text: "很长" }], 100, 0);
    expect(report.charInflation).toBe(false);
    expect(report.hasAnomalies).toBe(false);
  });

  it("reports clean streams without anomalies", () => {
    const report = detectStreamAnomalies(
      [
        { text: "旁白" },
        { text: "对白" },
        { text: "音效" },
      ],
      9,
      10,
    );
    expect(report.hasAnomalies).toBe(false);
  });
});
