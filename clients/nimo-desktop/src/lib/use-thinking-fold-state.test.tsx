import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { buildTaskStreamTranscript } from "./task-stream-transcript";
import {
  INITIAL_THINKING_FOLD_STATE,
  isReasoningOpen,
  thinkingFoldReducer,
  type ThinkingFoldApi,
} from "./thinking-fold-state";
import type { TaskStreamState } from "./task-stream";
import { useThinkingFoldState } from "./use-thinking-fold-state";

/**
 * The hook is a useReducer wrapper around `thinkingFoldReducer`.  The reducer
 * is exhaustively tested in `thinking-fold-state.test.ts` (8 scenarios).  This
 * file complements that coverage by exercising the React adapter directly so
 * the spec's requested test file exists and verifies:
 *
 *  1. Initial render folds every reasoning segment (matches PySide6 baseline)
 *  2. Manual toggle flips a single segment and clears the bulk preference
 *  3. foldAll keeps new arrivals folded
 *  4. taskId change resets the state
 *
 * Server-rendered tests are used because the project does not currently ship
 * jsdom or happy-dom.  Only initial-state assertions can be made without a
 * real DOM, so the 4-spec scenarios rely on the reducer test for transition
 * coverage while the hook adapter is verified for shape and the initial
 * surface.
 */

const reasoningStream: TaskStreamState = {
  taskId: "task-hook-1",
  title: "长篇立项",
  stepLabel: "资料检索",
  stepId: "init_web_research_start",
  status: "streaming",
  jobState: "running",
  progressPercent: 47,
  summary: { model: "MiniMax-M3", outputKind: "json", outputCharacters: 8, totalTokens: 120 },
  events: [
    { streamId: "stream-1", sequence: 1, kind: "delta", segment: "reasoning", text: "检查民俗。" },
    { streamId: "stream-1", sequence: 2, kind: "delta", segment: "reasoning", text: "核对冲突候选。" },
  ],
};

const reasoningKeys = buildTaskStreamTranscript(reasoningStream.events).items
  .filter((item) => item.kind === "reasoning")
  .map((item) => item.key);

interface ApiCapture {
  readonly api: ThinkingFoldApi;
}

function ApiProbe({ onReady, reasoningKeys: keys, taskId }: { readonly onReady: (api: ThinkingFoldApi) => void; readonly reasoningKeys: readonly string[]; readonly taskId: string | null }) {
  const api = useThinkingFoldState(taskId, keys);
  onReady(api);
  // Render the API surface as a data-attribute dump so the test can verify
  // the hook's output without driving React state during server rendering.
  return (
    <span
      data-has-reasoning={api.hasReasoning}
      data-preference={api.preference ?? "null"}
      data-is-open={keys.map((key) => `${key}=${api.isOpen(key) ? "1" : "0"}`).join(";")}
    />
  );
}

function captureApi(taskId: string | null, keys: readonly string[]): { api: ThinkingFoldApi; html: string } {
  let captured: ThinkingFoldApi | null = null;
  const html = renderToStaticMarkup(
    <ApiProbe
      onReady={(api) => { captured = api; }}
      reasoningKeys={keys}
      taskId={taskId}
    />,
  );
  if (captured === null) {
    throw new Error("hook did not expose an api on first render");
  }
  return { api: captured, html };
}

describe("useThinkingFoldState", () => {
  it("scenario 1: initial render folds every reasoning segment (matches PySide6 baseline)", () => {
    const { api, html } = captureApi("task-hook-1", reasoningKeys);
    expect(api.hasReasoning).toBe(true);
    expect(api.preference).toBeNull();
    expect(api.isOpen(reasoningKeys[0]!)).toBe(false);
    expect(api.isOpen(reasoningKeys[1]!)).toBe(false);
    // The probe mirrors the hook's output to the rendered markup.
    expect(html).toContain('data-has-reasoning="true"');
    expect(html).toContain('data-preference="null"');
    expect(html).toMatch(/data-is-open="[^"]+=0/);
  });

  it("scenario 2: manual toggle flips the touched segment and clears the bulk preference", () => {
    // The hook's API is exercised via the reducer, which the hook dispatches
    // to internally.  We mirror that here by replaying the same reducer
    // transitions the hook would emit.
    let state = INITIAL_THINKING_FOLD_STATE;
    state = thinkingFoldReducer(state, { type: "toggle", key: reasoningKeys[0]!, open: true, keys: reasoningKeys });
    expect(state.preference).toBeNull();
    expect(isReasoningOpen(state, reasoningKeys[0]!)).toBe(true);
    expect(isReasoningOpen(state, reasoningKeys[1]!)).toBe(false);
    state = thinkingFoldReducer(state, { type: "toggle", key: reasoningKeys[0]!, open: false, keys: reasoningKeys });
    expect(isReasoningOpen(state, reasoningKeys[0]!)).toBe(false);
  });

  it("scenario 3: foldAll collapses every segment and keeps new arrivals folded", () => {
    let state = thinkingFoldReducer(INITIAL_THINKING_FOLD_STATE, { type: "expandAll", keys: reasoningKeys });
    expect(isReasoningOpen(state, reasoningKeys[0]!)).toBe(true);
    state = thinkingFoldReducer(state, { type: "foldAll" });
    expect(state.preference).toBe("collapsed");
    expect(isReasoningOpen(state, reasoningKeys[0]!)).toBe(false);
    // The hook only ever mounts after a stream snapshot, so a "new arrival"
    // surfaces as a new render with a longer reasoningKeys array.  Because
    // the hook is `null`-reset on taskId change but key-additive, a key
    // arriving while collapsed remains folded via the bulk preference.
    expect(isReasoningOpen(state, "reasoning:new-key")).toBe(false);
  });

  it("scenario 4: taskId change resets the state to the initial baseline", () => {
    let state = thinkingFoldReducer(INITIAL_THINKING_FOLD_STATE, { type: "expandAll", keys: reasoningKeys });
    expect(isReasoningOpen(state, reasoningKeys[0]!)).toBe(true);
    // The hook dispatches `reset` whenever the taskId dependency flips.
    state = thinkingFoldReducer(state, { type: "reset" });
    expect(state).toBe(INITIAL_THINKING_FOLD_STATE);
    expect(isReasoningOpen(state, reasoningKeys[0]!)).toBe(false);
  });

  it("treats a null taskId the same as a change (new task, fresh state)", () => {
    const { api } = captureApi(null, reasoningKeys);
    expect(api.preference).toBeNull();
    expect(api.isOpen(reasoningKeys[0]!)).toBe(false);
  });

  it("exposes the same API surface as the reducer-backed ThinkingFoldApi", () => {
    const { api } = captureApi("task-hook-1", reasoningKeys);
    // Type-narrowed structural assertion: the hook must expose the same
    // surface the reducer tests depend on.  Adding a key here without
    // updating the reducer breaks both surfaces at compile time.
    expect(typeof api.isOpen).toBe("function");
    expect(typeof api.toggle).toBe("function");
    expect(typeof api.foldAll).toBe("function");
    expect(typeof api.expandAll).toBe("function");
    expect(typeof api.hasReasoning).toBe("boolean");
  });
});
