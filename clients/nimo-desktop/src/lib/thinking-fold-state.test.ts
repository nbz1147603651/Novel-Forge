import { describe, expect, it } from "vitest";

import {
  INITIAL_THINKING_FOLD_STATE,
  isReasoningOpen,
  thinkingFoldReducer,
} from "./thinking-fold-state";

function makeKeys(): readonly string[] {
  return ["reason-1", "reason-2", "reason-3"];
}

function expand(keys: readonly string[]) {
  return thinkingFoldReducer(INITIAL_THINKING_FOLD_STATE, { type: "expandAll", keys });
}

function fold() {
  return thinkingFoldReducer(INITIAL_THINKING_FOLD_STATE, { type: "foldAll" });
}

describe("thinkingFoldReducer", () => {
  it("starts every reasoning segment folded (matching PySide6 reader baseline)", () => {
    const state = INITIAL_THINKING_FOLD_STATE;
    expect(isReasoningOpen(state, "reason-1")).toBe(false);
    expect(isReasoningOpen(state, "reason-2")).toBe(false);
    expect(isReasoningOpen(state, "reason-3")).toBe(false);
    expect(state.preference).toBeNull();
  });

  it("toggle only flips the touched segment and clears the bulk preference", () => {
    const keys = makeKeys();
    let state = expand(keys);
    expect(isReasoningOpen(state, "reason-1")).toBe(true);

    state = thinkingFoldReducer(state, { type: "toggle", key: "reason-2", open: true, keys });
    expect(state.preference).toBeNull();
    expect(isReasoningOpen(state, "reason-1")).toBe(true);
    expect(isReasoningOpen(state, "reason-2")).toBe(true);
    expect(isReasoningOpen(state, "reason-3")).toBe(true);

    state = thinkingFoldReducer(state, { type: "toggle", key: "reason-2", open: false, keys });
    expect(state.preference).toBeNull();
    expect(isReasoningOpen(state, "reason-1")).toBe(true);
    expect(isReasoningOpen(state, "reason-2")).toBe(false);
    expect(isReasoningOpen(state, "reason-3")).toBe(true);
  });

  it("foldAll collapses every segment and keeps new arrivals folded", () => {
    const keys = makeKeys();
    let state = expand(keys);
    state = thinkingFoldReducer(state, { type: "foldAll" });
    expect(state.preference).toBe("collapsed");
    expect(isReasoningOpen(state, "reason-1")).toBe(false);
    expect(isReasoningOpen(state, "reason-4")).toBe(false);
  });

  it("expandAll opens every existing segment and any future segment", () => {
    const state = thinkingFoldReducer(INITIAL_THINKING_FOLD_STATE, {
      type: "expandAll",
      keys: makeKeys(),
    });
    expect(state.preference).toBe("expanded");
    expect(isReasoningOpen(state, "reason-1")).toBe(true);
    expect(isReasoningOpen(state, "reason-2")).toBe(true);
    expect(isReasoningOpen(state, "reason-3")).toBe(true);
    // A new reasoning key (not in the original list) is also expanded while
    // the bulk preference holds.
    expect(isReasoningOpen(state, "reason-4")).toBe(true);
  });

  it("manual toggle after a bulk action clears the bulk preference", () => {
    const keys = makeKeys();
    let state = expand(keys);
    state = thinkingFoldReducer(state, { type: "toggle", key: "reason-1", open: false, keys });
    expect(state.preference).toBeNull();
    expect(isReasoningOpen(state, "reason-1")).toBe(false);
    expect(isReasoningOpen(state, "reason-2")).toBe(true);
  });

  it("toggle while collapsed starts from an empty baseline, leaving siblings folded", () => {
    const keys = makeKeys();
    let state = fold();
    state = thinkingFoldReducer(state, { type: "toggle", key: "reason-2", open: true, keys });
    expect(state.preference).toBeNull();
    expect(isReasoningOpen(state, "reason-1")).toBe(false);
    expect(isReasoningOpen(state, "reason-2")).toBe(true);
    expect(isReasoningOpen(state, "reason-3")).toBe(false);
  });

  it("reset returns to the initial baseline so the new stream starts clean", () => {
    const keys = makeKeys();
    let state = expand(keys);
    state = thinkingFoldReducer(state, { type: "reset" });
    expect(state).toEqual(INITIAL_THINKING_FOLD_STATE);
    expect(isReasoningOpen(state, "reason-1")).toBe(false);
  });

  it("does not mutate the input state object", () => {
    const keys = makeKeys();
    const state = expand(keys);
    const snapshot = new Set(state.expandedKeys);
    thinkingFoldReducer(state, { type: "toggle", key: "reason-1", open: false, keys });
    expect(state.expandedKeys).toEqual(snapshot);
  });
});
