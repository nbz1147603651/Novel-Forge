import { renderToStaticMarkup } from "react-dom/server";
import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";
import type { AuthoringProposalView, AuthoringSessionView, EngineClient, EngineCommandClient } from "@nimo/engine-contracts";
import { AuthoringAvailabilityControl, AuthoringWorkspace, ProposalCard, useAuthoring } from "./AuthoringWorkspace";
import { EngineRuntimeContext } from "../lib/engine-runtime-context";
import { LegacyLocalEngineClient } from "../lib/legacy-engine-client";
import { ReaderArtifactDocument } from "./ReaderArtifactDocument";
import { ChapterExportDialog } from "./ChapterStudioDialogs";

// Content-contract unit test only; portal/focus behavior is covered by browser QA.
vi.mock("./OverlaySurface", () => ({ OverlaySurface: ({ children }: { children: ReactNode }) => <div>{children}</div> }));

function EmbeddedAuthoringEntry() {
  const authoring = useAuthoring();
  return <button
    aria-expanded={authoring?.entry.expanded}
    data-testid="embedded-coauthor-entry"
    type="button"
  >
    {authoring?.entry.label}
  </button>;
}

describe("coauthor capability and transport", () => {
  it("distinguishes disabling from activation and waits for worker cleanup before enabling", () => {
    const action = vi.fn();
    const html = renderToStaticMarkup(<AuthoringAvailabilityControl session={{ disabled: true, stopState: "stopping" } as AuthoringSessionView} disabled={false} onChange={action} />);
    expect(html).toContain("重新启用共创（不启动任务）");
    expect(html).toContain("disabled");
    expect(html).toContain("旧任务和旧批准不会自动恢复");
    expect(action).not.toHaveBeenCalled();
  });

  it("allows declining stale suggestions while paused, without enabling approval or editing derived files", () => {
    const action = vi.fn();
    const proposal = { id: "p", title: "世界设定", action: "revise", editable: false, status: "stale", original: "旧版", candidate: "候选", affectedChapters: [1, 2], evidence: [], risks: [], lockConflicts: [], costHint: "不调用模型", applicationResult: {} } as unknown as AuthoringProposalView;
    const html = renderToStaticMarkup(<ProposalCard proposal={proposal} disabled={false} approvalDisabled onDecision={action} onRetry={action} />);
    expect(html).toContain('type="button">拒绝');
    expect(html).not.toContain("核对并批准");
    expect(html).not.toContain("编辑候选");
    expect(action).not.toHaveBeenCalled();
  });

  it("distinguishes a completed proposal action from accepting the next final draft", () => {
    const action = vi.fn();
    const proposal = { id: "p", title: "方案确认", action: "generate", status: "applied", original: "原方案", candidate: "新方案", affectedChapters: [3], evidence: [], risks: [], lockConflicts: [], costHint: "已有费用保留", applicationResult: { status: "needs_decision", message: "仍须对最终正文单独确认" } } as unknown as AuthoringProposalView;
    const html = renderToStaticMarkup(<ProposalCard proposal={proposal} disabled={false} onDecision={action} onRetry={action} />);
    expect(html).toContain('<p role="status">仍须对最终正文单独确认</p>');
    expect(html).not.toContain("核对并批准");
    expect(action).not.toHaveBeenCalled();
  });

  it("keeps repair-managed approval separate from application", () => {
    const action = vi.fn();
    const proposal = {
      id: "repair-proposal",
      title: "修复工作台：精确修订",
      action: "revise",
      editable: true,
      status: "approved",
      original: "原文",
      candidate: "候选",
      affectedChapters: [1],
      evidence: ["repair_case:case-1"],
      risks: [],
      lockConflicts: [],
      costHint: "不调用模型",
      applicationResult: { approvalFlow: "repair_workbench" },
    } as unknown as AuthoringProposalView;
    const html = renderToStaticMarkup(
      <ProposalCard proposal={proposal} disabled={false} onDecision={action} onRetry={action} />,
    );
    expect(html).toContain("发布仍由修复工作台应用候选");
    expect(html).not.toContain("执行已批准版本");
    expect(html).not.toContain("编辑候选");
    expect(action).not.toHaveBeenCalled();
  });

  it("keeps stale evidence visible above a rich report rather than presenting its score as current", () => {
    const html = renderToStaticMarkup(<ReaderArtifactDocument projectTitle="书稿" artifact={{ id: "report", label: "评估", caption: "高分报告", sourceLabel: "reports/chapter_001_eval.json", paragraphs: [], facts: [], format: "json", content: '{"score":10}', freshness: "stale", freshnessMessage: "正文已改，此报告仅供历史参考" }} />);
    expect(html).toContain("此报告仅供历史参考");
    expect(html).toContain('role="status"');
  });

  it("allows working-copy export while explaining version evidence", () => {
    const run = vi.fn();
    const html = renderToStaticMarkup(<ChapterExportDialog chapterNumbers={[1, 2]} projectTitle="书稿" onClose={run} onConfirm={run} />);
    expect(html).toContain("不代表已通过交付验证");
    expect(run).not.toHaveBeenCalled();
  });

  it("retains provenance warnings in plain-text and summary readers too", () => {
    for (const content of [undefined, "历史报告的正文"]) {
      const html = renderToStaticMarkup(<ReaderArtifactDocument projectTitle="书稿" artifact={{ id: "report", label: "评估", caption: "历史证据", sourceLabel: "reports/chapter_001_report.txt", paragraphs: ["旧结论"], facts: [], format: "plain_text", ...(content === undefined ? {} : { content }), freshness: "stale", freshnessMessage: "正文已改，不能作为新验证" }} />);
      expect(html).toContain("正文已改，不能作为新验证");
      expect(html).not.toContain("书稿 · 已同步");
    }
  });

  it("preserves legacy and short readers with no half-built entry or automatic action", () => {
    const command = {} as EngineCommandClient;
    const html = renderToStaticMarkup(<AuthoringWorkspace commandClient={command} projectId="book"><article>作者的阅读布局</article></AuthoringWorkspace>);
    expect(html).toBe("<article>作者的阅读布局</article>");
  });

  it("starts collapsed and never dispatches a task while rendering", () => {
    const dispatch = vi.fn();
    const engine = { getAuthoringSession: dispatch, getAuthoringMessages: dispatch, getAuthoringProposals: dispatch } as unknown as EngineClient;
    const commands = { createAuthoringProposal: dispatch, sendAuthoringMessage: dispatch, setAuthoringPolicy: dispatch, startAuthoringSession: dispatch, pauseAuthoringSession: dispatch, decideAuthoringProposal: dispatch, applyAuthoringProposal: dispatch } as unknown as EngineCommandClient;
    const html = renderToStaticMarkup(<AuthoringWorkspace engineClient={engine} commandClient={commands} projectId="book"><article>现有工作台</article></AuthoringWorkspace>);
    expect(html).toContain('aria-expanded="false"');
    expect(html).toContain("现有工作台");
    expect(html).not.toContain('class="authoring-sidebar"');
    expect(dispatch).not.toHaveBeenCalled();
  });

  it("lets chapter studio own an embedded entry without rendering the floating trigger", () => {
    const dispatch = vi.fn();
    const engine = {
      getAuthoringSession: dispatch,
      getAuthoringMessages: dispatch,
      getAuthoringProposals: dispatch,
    } as unknown as EngineClient;
    const commands = {
      createAuthoringProposal: dispatch,
      sendAuthoringMessage: dispatch,
      setAuthoringPolicy: dispatch,
      startAuthoringSession: dispatch,
      pauseAuthoringSession: dispatch,
      decideAuthoringProposal: dispatch,
      applyAuthoringProposal: dispatch,
    } as unknown as EngineCommandClient;
    const html = renderToStaticMarkup(
      <AuthoringWorkspace
        engineClient={engine}
        commandClient={commands}
        projectId="book"
        triggerPlacement="embedded"
      >
        <EmbeddedAuthoringEntry />
      </AuthoringWorkspace>,
    );
    expect(html).toContain('data-testid="embedded-coauthor-entry"');
    expect(html).toContain("AI 共创");
    expect(html).not.toContain('class="authoring-toggle"');
    expect(dispatch).not.toHaveBeenCalled();
  });

  it("hides the entry if the server declines the capability", () => {
    const method = vi.fn();
    const html = renderToStaticMarkup(<EngineRuntimeContext.Provider value={{ mode: "legacy", negotiation: { status: "negotiating" }, diagnostic: { connection: "checking", message: "", canSubmitTasks: false }, isFeatureEnabled: () => false, isCommandAvailable: () => true, canSubmitTasks: true }}><AuthoringWorkspace engineClient={{ getAuthoringSession: method } as unknown as EngineClient} commandClient={{} as EngineCommandClient} projectId="book"><p>原布局</p></AuthoringWorkspace></EngineRuntimeContext.Provider>);
    expect(html).toBe("<p>原布局</p>");
  });

  it("sends exact candidate versions and keeps action opt-in separate from discussion", async () => {
    const fetcher = vi.spyOn(globalThis, "fetch").mockImplementation(async () => new Response(JSON.stringify({ id: "m", response: { reply: "建议", proposals: [], actions: [] }, proposal_ids: [] }), { status: 200, headers: { "Content-Type": "application/json" } }));
    try {
      const client = new LegacyLocalEngineClient({ baseUrl: "http://localhost:9999", maxRetries: 0 });
      await client.sendAuthoringMessage("book", { message: "解释本章", chapterNumber: 3, context: "chapter", allowActions: false });
      expect(JSON.parse(String(fetcher.mock.calls[0]?.[1]?.body))).toMatchObject({ chapter_number: 3, allow_actions: false });
      await client.submitJobDecision("job", "decision", "accept", "", "candidate-version");
      expect(JSON.parse(String(fetcher.mock.calls[1]?.[1]?.body))).toMatchObject({ decision_id: "decision", choice: "accept", approval_version: "candidate-version" });
    } finally { fetcher.mockRestore(); }
  });
});
