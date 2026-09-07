import { describe, expect, it } from "vitest";

import { panelOffsetAfterPointer } from "./use-panel-drag";

describe("floating panel drag geometry", () => {
  it("moves an existing offset by the pointer delta", () => {
    expect(panelOffsetAfterPointer({ x: 18, y: -6 }, 120, 80, 154, 101)).toEqual({ x: 52, y: 15 });
  });
});
