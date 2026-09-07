import { describe, expect, it } from "vitest";

import { LatestRequestGate } from "./latest-request";

describe("LatestRequestGate", () => {
  it("accepts only the most recently started request", () => {
    const gate = new LatestRequestGate();
    const first = gate.begin("project:a");
    const second = gate.begin("project:b");

    expect(gate.isCurrent(first)).toBe(false);
    expect(gate.isCurrent(second)).toBe(true);
  });

  it("invalidates an in-flight request when a resource is cleared", () => {
    const gate = new LatestRequestGate();
    const request = gate.begin("voice:project-a:chapter-1");

    gate.invalidate();

    expect(gate.isCurrent(request)).toBe(false);
  });
});
