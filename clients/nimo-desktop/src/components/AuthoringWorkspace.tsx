import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import type { AuthoringContext, AuthoringMessageView, AuthoringPolicy, AuthoringProposalRequest, AuthoringProposalView, AuthoringSessionView, EngineClient, EngineCommandClient } from "@nimo/engine-contracts";
import { useEngineRuntime } from "../lib/engine-runtime-context";
import { AppDialog } from "./AppDialog";
import { OverlaySurface } from "./OverlaySurface";
import "../styles/authoring-workspace.css";

interface AuthoringContextValue {
  session: AuthoringSessionView | null;
  setLocation: (context: AuthoringContext, chapter?: number) => void;
  propose: (request: AuthoringProposalRequest) => Promise<void>;
  open: () => void;
  entry: {
    visible: boolean;
    expanded: boolean;
    label: string;
    toggle: () => void;
  };
  appliedProse?: { chapter: number; text: string; revision: string } | undefined;
}
const AuthoringContextProvider = createContext<AuthoringContextValue | null>(null);
export const useAuthoring = () => useContext(AuthoringContextProvider);

interface Props {
  children: ReactNode;
  engineClient?: EngineClient;
  commandClient: EngineCommandClient;
  projectId: string;
  initialChapter?: number;
  enabled?: boolean;
  triggerPlacement?: "floating" | "embedded";
}

/** Optional view of the existing domain runtime, never a second scheduler. */
export function AuthoringWorkspace(props: Props) {
  const { isFeatureEnabled } = useEngineRuntime();
  const available = props.enabled !== false && props.projectId.length > 0
    && !!props.engineClient?.getAuthoringSession && !!props.commandClient.pauseAuthoringSession;
  const released = isFeatureEnabled("authoring_coauthor") && !!props.engineClient?.getAuthoringSession
    && !!props.engineClient.getAuthoringProposals && !!props.engineClient.getAuthoringMessages
    && !!props.commandClient.createAuthoringProposal && !!props.commandClient.sendAuthoringMessage
    && !!props.commandClient.setAuthoringPolicy && !!props.commandClient.startAuthoringSession
    && !!props.commandClient.pauseAuthoringSession && !!props.commandClient.decideAuthoringProposal
    && !!props.commandClient.applyAuthoringProposal;
  if (!available) return <>{props.children}</>;
  return <AuthoringWorkspaceReady key={props.projectId} {...props} released={released} />;
}

function AuthoringWorkspaceReady({ children, engineClient, commandClient, projectId, initialChapter = 1, released, triggerPlacement = "floating" }: Props & { released: boolean }) {
  const [expanded, setExpanded] = useState(false);
  const [location, setContext] = useState({ context: "chapter" as AuthoringContext, chapter: initialChapter });
  const [session, setSession] = useState<AuthoringSessionView | null>(null);
  const [proposals, setProposals] = useState<readonly AuthoringProposalView[]>([]);
  const [messages, setMessages] = useState<readonly AuthoringMessageView[]>([]);
  const [error, setError] = useState("");
  const [syncError, setSyncError] = useState("");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const [refreshKey, setRefreshKey] = useState(0);
  const [message, setMessage] = useState("");
  const [allowActions, setAllowActions] = useState(false);
  const [confirm, setConfirm] = useState<AuthoringProposalView | null>(null);
  const [acknowledged, setAcknowledged] = useState(false);
  const mounted = useRef(true);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  const setLocation = useCallback((context: AuthoringContext, chapter?: number) => {
    setContext((current) => current.context === context && (chapter === undefined || chapter === current.chapter)
      ? current : { context, chapter: chapter ?? current.chapter });
  }, []);
  const open = useCallback(() => setExpanded(true), []);
  const toggle = useCallback(() => setExpanded((value) => !value), []);
  useEffect(() => {
    let active = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const refresh = async () => {
      try {
        const [nextSession, nextProposals, nextMessages] = await Promise.all([
          engineClient!.getAuthoringSession!(projectId, location.chapter),
          expanded && engineClient?.getAuthoringProposals ? engineClient.getAuthoringProposals(projectId) : Promise.resolve(null),
          expanded && released && engineClient?.getAuthoringMessages ? engineClient.getAuthoringMessages(projectId) : Promise.resolve(null),
        ]);
        if (!active) return;
        setSession(nextSession);
        if (nextProposals !== null) setProposals(nextProposals);
        if (nextMessages !== null) setMessages(nextMessages);
        setSyncError("");
      } catch (cause) { if (active) setSyncError(String(cause)); }
      if (active) timer = setTimeout(() => { void refresh(); }, expanded ? 2000 : 5000);
    };
    void refresh();
    return () => { active = false; clearTimeout(timer); };
  }, [engineClient, expanded, location.chapter, projectId, refreshKey, released]);

  const run = async (operation: () => Promise<unknown>) => {
    if (busy) return;
    setBusy(true); setError("");
    try { await operation(); }
    catch (cause) { if (mounted.current) setError(cause instanceof Error ? cause.message : String(cause)); }
    finally { if (mounted.current) { setBusy(false); setRefreshKey((value) => value + 1); } }
  };
  const propose = useCallback(async (request: AuthoringProposalRequest) => {
    if (!released || !commandClient.createAuthoringProposal) throw new Error("共创能力当前关闭或客户端不支持版本化提案，未降级执行");
    setExpanded(true);
    await commandClient.createAuthoringProposal(projectId, request);
    if (mounted.current) { setRefreshKey((value) => value + 1); setNotice("候选已保存，尚未修改正式作品。请核对差异后批准。"); }
  }, [commandClient, projectId, released]);
  const decide = async (proposal: AuthoringProposalView, decision: "accept" | "reject" | "defer" | "edit", editedCandidate?: string) => {
    if ((decision === "accept" || decision === "edit") && !released) throw new Error("共创能力当前关闭，未批准或执行");
    if (!commandClient.decideAuthoringProposal || (decision === "accept" && !commandClient.applyAuthoringProposal)) throw new Error("引擎不支持提案审批，未执行");
    await commandClient.decideAuthoringProposal(projectId, proposal.id, {
      decision, candidateVersion: proposal.candidateVersion, inputVersion: proposal.inputVersion,
      policyVersion: proposal.policyVersion, ...(editedCandidate === undefined ? {} : { editedCandidate }),
    });
    if (decision === "accept") {
      const repairManaged = (proposal.applicationResult.approvalFlow
        ?? proposal.applicationResult.approval_flow) === "repair_workbench";
      if (repairManaged) {
        setNotice("已批准精确修复提案；正文未改变。发布仍由修复工作台生成并核对回执。");
      } else {
        await commandClient.applyAuthoringProposal!(projectId, proposal.id);
        setNotice("批准已交给领域执行器；请以任务和正式归档状态为准。");
      }
      setConfirm(null);
    }
  };
  const disabled = busy || syncError.length > 0 || session === null;
  const can = (action: string) => released && !disabled && session?.allowedActions.some((item) => item.action === action && item.allowed);
  const visible = released || session?.configured === true;
  const planning = session?.planningTask;
  const semantic = session?.semanticConsistency;
  const applied = proposals.find((item) => item.status === "applied" && item.action === "revise" && item.chapterNumber === location.chapter && (item.applicationResult.currentHash ?? item.applicationResult.current_hash) === session?.currentTextHash);
  const appliedProse = applied && session?.currentTextHash ? { chapter: applied.chapterNumber, text: applied.candidate, revision: session.currentTextHash.slice(0, 16) } : undefined;

  const entryLabel = expanded ? "收起共创" : released ? "AI 共创" : "共创停止与恢复";
  return <AuthoringContextProvider.Provider value={{ session, setLocation, propose, open, entry: { visible, expanded, label: entryLabel, toggle }, appliedProse }}>
    <div className={`authoring-workspace${expanded && visible ? " is-expanded" : ""}`}>
      <div className="authoring-content">{children}</div>
      {visible && triggerPlacement === "floating" && <div className="authoring-toggle"><button className="button button-secondary" aria-expanded={expanded} aria-controls="authoring-sidebar" onClick={toggle} type="button">{entryLabel}</button></div>}
      {expanded && visible && <AuthoringSidebarSurface onClose={() => setExpanded(false)} confirmOpen={confirm !== null}><aside id="authoring-sidebar" className="authoring-sidebar" aria-label="AI 共创侧栏">
        <header><div className="authoring-sidebar-title"><h2>作者与 AI 共创</h2><button type="button" className="button button-quiet authoring-drawer-close" onClick={() => setExpanded(false)}>收起侧栏</button></div><p>你决定方向与正式版本，AI 在授权范围内执行。对话不写入正史。</p></header>
        {(error || syncError) && <p role="alert">{error || syncError}<button type="button" className="button button-quiet" onClick={() => { setError(""); setRefreshKey((value) => value + 1); }}>重新同步</button></p>}
        {!released && <p role="status">共创能力当前关闭。仅可查看、暂停、拒绝建议或核验已提交结果，不会降级为自动执行。</p>}
        {notice && <p role="status">{notice}</p>}
        {session && <>
          <AuthoringPolicyForm key={`${projectId}:${session.policy.version}:${session.configured}`} session={session} chapter={location.chapter} disabled={!released || disabled || session.disabled === true || ["running", "stopping"].includes(session.stopState)} onSave={(policy) => { void run(async () => { await commandClient.setAuthoringPolicy!(projectId, policy, session.configured ? session.policy.version : 0); setNotice("权限已保存，未启动任务。请明确启用，再选择准备或连续执行。"); }); }} />
          <p role="status">{({ idle: "等待操作", running: "运行中", stopping: "正在安全停止", stopped: "已停止" })[session.stopState]} · {session.waitingReason || "无待处理阻塞"}</p>
          {session.currentTaskId && <small>任务 {session.currentTaskId}</small>}
          <section className="authoring-semantic-status" aria-label="语义一致性">
            <h3>语义一致性</h3>
            <p><strong>{semanticStatusLabel(semantic?.status ?? "uncompiled")}</strong>{semantic?.issueCount ? ` · ${semantic.issueCount} 项证据需处理` : ""}</p>
            <p>{semantic?.reason || "尚未编译当前来源证据。"}</p>
            {semantic?.reportId && <small>报告 {semantic.reportId.slice(0, 16)} · 账本 {semantic.ledgerHash.slice(0, 16)}</small>}
            {session.configured && commandClient.refreshSemanticConsistency && <button
              className="button button-secondary"
              disabled={busy || session.policy.stopped || semantic?.status === "queued" || semantic?.status === "running" || semantic?.status === "clean"}
              onClick={() => { void run(async () => {
                await commandClient.refreshSemanticConsistency!(projectId, session.inputVersion, session.policy.version);
                setNotice("语义一致性刷新已交给现有后台任务；不会启动章节生成。");
              }); }}
              type="button"
            >{semantic?.status === "conflict" || semantic?.status === "review_required" ? "应用作者决定并复编" : "刷新语义一致性"}</button>}
          </section>
          <div className="authoring-actions">
            {session.configured && session.policy.stopped && <button className="button button-primary" disabled={!released || disabled || session.disabled === true || session.stopState === "stopping"} onClick={() => { void run(async () => { await commandClient.startAuthoringSession!(projectId, session.policy.version, session.inputVersion); setNotice("授权已启用，尚未生成章节。下一步请准备本章或启动已授权章段。"); }); }} type="button">启用所选授权</button>}
            {session.configured && <button className="button button-secondary" disabled={busy || session.stopState === "stopping"} onClick={() => { void run(() => commandClient.pauseAuthoringSession!(projectId)); }} type="button">暂停与保留候选</button>}
            <button className="button button-secondary" disabled={!session.configured || !can("prepare")} onClick={() => { void run(() => commandClient.prepareChapter({ kind: "prepare_chapter", projectId, chapterNumber: location.chapter })); }} type="button">准备第 {location.chapter} 章方案</button>
            {session.configured && session.policy.mode !== "manual" && <button className="button button-primary" disabled={!can("prepare") || session.stopState === "running"} onClick={() => { void run(() => commandClient.startWorkflow({ kind: "start_workflow", projectId, workflowType: "long_chapter", runMode: "autorun", idempotencyKey: `authoring-${projectId}-${session.policy.version}`, payload: { projectId, chapterNumber: session.policy.startChapter, autorunScope: session.policy.endChapter > session.policy.startChapter ? "book" : "chapter", skipDone: true, force: false, writingMode: "whole_chapter" } })); }} type="button">启动所选章段</button>}
          </div>
          <small>共创模式每章验收归档后才推进下一章。恢复或改权限后，旧批准不可复用。</small>
          {session.configured && commandClient.setAuthoringAvailability && <AuthoringAvailabilityControl session={session} disabled={busy || (!released && session.disabled === true)} onChange={(enabled) => { void run(() => commandClient.setAuthoringAvailability!(projectId, enabled, session.policy.version, session.inputVersion)); }} />}
          {(session.revisionHistory?.length ?? 0) > 0 && <details><summary>本章历史正文 · 恢复为新修订</summary><p>只恢复所选章正文；保留后续章节、候选证据与已发生费用，相关报告须重验。</p>{session.revisionHistory?.map((item) => <div key={item.timestamp}><small>{item.timestamp} · {item.transactionStatus || "历史记录"}</small><button type="button" className="button button-quiet" disabled={!released || disabled || session.policy.stopped} onClick={() => { void run(() => propose({ command: "restore_chapter", chapterNumber: location.chapter, revisionId: item.timestamp, revisionSide: "before", title: "恢复所选历史正文为新修订", expectedInputVersion: session.inputVersion })); }}>比较并提案恢复</button></div>)}</details>}
          {(session.checkpoint?.options?.length ?? 0) > 0 && <section><h3>本章等待确认</h3><p>{session.checkpoint?.summary}</p><div className="authoring-actions">{session.checkpoint?.options?.map((option) => <button key={option.optionId} className="button button-secondary" disabled={!released || disabled || session.policy.stopped} type="button" onClick={() => { void run(() => propose({ command: "checkpoint", chapterNumber: location.chapter, optionId: option.optionId, title: option.label, expectedInputVersion: session.inputVersion })); }}>{option.label} · 查看提案</button>)}</div></section>}
          {planning?.status && <section><h3>渐进规划</h3><p>规划任务：{planning.status} · 细化至第 {planning.targetChapter ?? "—"} 章</p>{planning.error && <p>{planning.error}</p>}{planning.status === "candidate" && planning.result?.revisionId && <button type="button" className="button button-secondary" disabled={!released || disabled || session.policy.stopped} onClick={() => { void run(() => propose({ command: "publish_planning", chapterNumber: location.chapter, revisionId: planning.result!.revisionId!, title: "下一批规划发布", expectedInputVersion: session.inputVersion })); }}>查看规划候选</button>}</section>}
        </>}
        <section><h3>提案与验收</h3>{proposals.length === 0 && <p>暂无提案。准备方案或提出修改后，在这里核对候选。</p>}{proposals.map((proposal) => <ProposalCard key={`${proposal.id}:${proposal.candidateVersion}`} proposal={proposal} disabled={disabled || !commandClient.decideAuthoringProposal} approvalDisabled={!released || disabled || !session || session.policy.stopped} onDecision={(decision, edited) => { if (decision === "accept") { setConfirm(proposal); setAcknowledged(false); } else { void run(() => decide(proposal, decision, edited)); } }} onRetry={() => { void run(() => commandClient.applyAuthoringProposal!(projectId, proposal.id)); }} />)}</section>
        <section><h3>讨论当前内容</h3><label>上下文<select aria-label="共创上下文" value={location.context} onChange={(event) => setLocation(event.target.value as AuthoringContext)}>{([['spec', '故事规格'], ['world', '世界观'], ['characters', '人物'], ['blueprint', '叙事蓝图'], ['outline', '大纲'], ['chapter', '章节'], ['reports', '报告']] as const).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label><label>章节<input aria-label="共创章节" type="number" min={1} value={location.chapter} onChange={(event) => setLocation(location.context, Math.max(1, Number(event.target.value) || 1))} /></label>
          <div className="authoring-messages" aria-label="共创对话记录">{messages.map((item) => <article key={item.id}><strong>作者 · 第 {item.chapterNumber} 章</strong><p>{item.message}</p>{item.response && <><strong>AI 建议</strong><p>{item.response.reply}</p>{item.response.actions.map((action, index) => <small key={index}>建议：{action.reason}</small>)}</>}<small>{item.status}{item.error ? `：${item.error}` : ""}</small>{["failed", "paused"].includes(item.status) && <button className="button button-quiet" type="button" disabled={!released || disabled || session?.policy.version !== item.policyVersion || session?.inputVersion !== item.inputVersion} onClick={() => { void run(() => commandClient.resumeJob({ kind: "resume_job", taskId: item.taskId })); }}>重试此消息任务</button>}</article>)}</div>
          <form onSubmit={(event) => { event.preventDefault(); if (!can("discuss")) return; void run(async () => { await commandClient.sendAuthoringMessage!(projectId, { message, chapterNumber: location.chapter, context: location.context, allowActions }); setMessage(""); setAllowActions(false); }); }}><label>给 AI 的指令<textarea aria-label="给 AI 的指令" value={message} maxLength={8000} onChange={(event) => setMessage(event.target.value)} rows={4} /></label><label className="authoring-checkbox"><input type="checkbox" checked={allowActions} onChange={(event) => setAllowActions(event.target.checked)} />允许本轮启动已授权操作；不代替提案批准</label><button className="button button-primary" disabled={!session?.configured || !can("discuss") || !message.trim()} type="submit">发送讨论</button></form>
        </section>
      </aside></AuthoringSidebarSurface>}
    </div>
    {confirm && <AppDialog title="批准此候选版本" description={`第 ${confirm.chapterNumber} 章 · ${confirm.title}。批准只绑定当前候选、故事输入和授权版本。`} confirmLabel={confirm.action === "archive" ? "验收此正文并申请归档" : confirm.action === "generate" ? "确认方案并生成" : "专项批准并应用"} confirmDisabled={!released || !acknowledged || busy} closeOnConfirm={false} onClose={() => setConfirm(null)} onConfirm={() => { void run(() => decide(confirm, "accept")); }} size="wide"><ProposalDiff proposal={confirm} /><p>{confirm.costHint}</p><p>{[...confirm.risks, ...confirm.lockConflicts].join("；")}</p><label className="authoring-checkbox"><input type="checkbox" checked={acknowledged} onChange={(event) => setAcknowledged(event.target.checked)} />我已核对候选、影响章段与锁定冲突，仅批准此版本</label>{error && <p role="alert">{error}</p>}</AppDialog>}
  </AuthoringContextProvider.Provider>;
}

function semanticStatusLabel(status: AuthoringSessionView["semanticConsistency"]["status"]): string {
  return ({
    uncompiled: "未编译",
    queued: "已排队",
    running: "编译中",
    clean: "已验证清洁",
    conflict: "明确冲突",
    review_required: "需作者确认",
    stale: "来源已变化",
    failed: "编译失败",
  })[status];
}

/** At narrow widths, reuse the app's viewport-bound focus/overlay shell. */
function AuthoringSidebarSurface({ children, onClose, confirmOpen }: { children: ReactNode; onClose: () => void; confirmOpen: boolean }) {
  const [narrow, setNarrow] = useState(() => typeof window !== "undefined" && window.matchMedia("(max-width: 1050px)").matches);
  useEffect(() => {
    const query = window.matchMedia("(max-width: 1050px)");
    const sync = () => setNarrow(query.matches);
    sync(); query.addEventListener("change", sync);
    return () => query.removeEventListener("change", sync);
  }, []);
  if (!narrow) return <>{children}</>;
  return <OverlaySurface ariaLabel="共创抽屉" className="authoring-sidebar-drawer" backdropClassName="authoring-drawer-backdrop" modal={!confirmOpen} onClose={() => { if (!confirmOpen) onClose(); }}>{children}</OverlaySurface>;
}

export function AuthoringAvailabilityControl({ session, disabled, onChange }: { session: AuthoringSessionView; disabled: boolean; onChange: (enabled: boolean) => void }) {
  return <details><summary>停用与回退保护</summary><p>仅作用于当前作品。先阻止新派发和候选发布，再等待已发出的请求安全结束。保留正文、候选、修订与费用记录。</p><button className="button button-secondary" type="button" disabled={disabled || session.stopState === "stopping"} onClick={() => onChange(session.disabled === true)}>{session.disabled ? "重新启用共创（不启动任务）" : "停用本作品共创"}</button><p>重新启用后仍须明确启动；旧任务和旧批准不会自动恢复。代码回退前须等到“已停止”，并处理已提交但未完成的投影。</p></details>;
}

function AuthoringPolicyForm({ session, chapter, disabled, onSave }: { session: AuthoringSessionView; chapter: number; disabled: boolean; onSave: (policy: AuthoringPolicy) => void }) {
  const [policy, setPolicy] = useState<AuthoringPolicy>(() => session.configured ? session.policy : { ...session.policy, mode: "coauthor", startChapter: chapter, endChapter: chapter });
  return <details open={!session.configured}><summary>授权范围 · {session.configured ? ({ manual: "手动", coauthor: "AI 共创", authorized_auto: "授权自动" })[session.policy.mode] : "尚未选择"}</summary><form onSubmit={(event) => { event.preventDefault(); onSave(policy); }}><label>协作模式<select aria-label="协作模式" value={policy.mode} disabled={disabled} onChange={(event) => setPolicy({ ...policy, mode: event.target.value as AuthoringPolicy["mode"] })}><option value="manual">手动</option><option value="coauthor">AI 共创（推荐）</option><option value="authorized_auto">授权自动</option></select></label><label>开始章<input aria-label="授权开始章" type="number" min={1} value={policy.startChapter} disabled={disabled} onChange={(event) => setPolicy({ ...policy, startChapter: Number(event.target.value) })} /></label><label>结束章<input aria-label="授权结束章" type="number" min={policy.startChapter} value={policy.endChapter} disabled={disabled} onChange={(event) => setPolicy({ ...policy, endChapter: Number(event.target.value) })} /></label><label>本作品共创累计预算（USD，可留空沿用现有预算）<input aria-label="共创预算" type="number" min={0} step="0.01" value={policy.budgetUsd ?? ""} disabled={disabled} onChange={(event) => setPolicy({ ...policy, budgetUsd: event.target.value === "" ? null : Number(event.target.value) })} /></label><p>已记录 ${session.budget?.spentUsd.toFixed(4) ?? "0"} · 未确认占用 ${session.budget?.reservedUsd.toFixed(4) ?? "0"} · 未知费用调用 {session.budget?.unknownCalls ?? 0} 次。</p><p>按既有价格表估算，实际账单以服务商为准；暂停或重启不重置费用。此预算只收紧既有模型预算，不增加额度。重大变更始终另行批准；保存后保持停止。</p><button className="button button-secondary" type="submit" disabled={disabled || !Number.isInteger(policy.startChapter) || policy.startChapter < 1 || !Number.isInteger(policy.endChapter) || policy.endChapter < policy.startChapter}>保存授权（不启动）</button></form></details>;
}

function ProposalDiff({ proposal }: { proposal: AuthoringProposalView }) {
  return <div className="authoring-diff"><section><strong>原文 / 原方案</strong><pre>{proposal.original || "尚无正式正文"}</pre></section><section><strong>候选</strong><pre>{proposal.candidate}</pre></section></div>;
}

export function ProposalCard({ proposal, disabled, approvalDisabled = disabled, onDecision, onRetry }: { proposal: AuthoringProposalView; disabled: boolean; approvalDisabled?: boolean; onDecision: (decision: "accept" | "reject" | "defer" | "edit", edited?: string) => void; onRetry: () => void }) {
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState(proposal.candidate);
  const actionable = ["pending", "deferred"].includes(proposal.status);
  const receipt = proposal.applicationResult.recoverableReceipt ?? proposal.applicationResult.recoverable_receipt;
  const outcome = proposal.applicationResult.message ?? proposal.applicationResult.error;
  const hasTask = !!(proposal.applicationResult.taskId ?? proposal.applicationResult.task_id);
  const repairManaged = (proposal.applicationResult.approvalFlow
    ?? proposal.applicationResult.approval_flow) === "repair_workbench";
  const canDecline = receipt !== true && !hasTask && proposal.applicationResult.status !== "applying"
    && ["pending", "deferred", "stale", "approved"].includes(proposal.status);
  return <>
    {receipt === true && <button className="button button-secondary" disabled={disabled} type="button" onClick={onRetry}>核验已提交结果（不重写正文）</button>}
    <details className="authoring-proposal">
      <summary>{proposal.title} · {({ pending: "待批准", approved: "已批准 / 待执行结果", rejected: "已拒绝", deferred: "已暂存", stale: "版本过期", applied: "已应用" })[proposal.status]}</summary>
      <ProposalDiff proposal={proposal} />
      {typeof outcome === "string" && <p role="status">{outcome}</p>}
      <p>影响章节：{proposal.affectedChapters.join("、")}</p>
      <p>依据：{proposal.evidence.join("；") || "来自当前选定内容"}</p>
      <p>{proposal.costHint}</p><p>{[...proposal.risks, ...proposal.lockConflicts].join("；")}</p>
      {Object.keys(proposal.applicationResult).length > 0 && <details><summary>执行回执与版本依据</summary><pre>{JSON.stringify(proposal.applicationResult, null, 2)}</pre></details>}
      <div className="authoring-actions">
        {actionable && <button className="button button-primary" disabled={approvalDisabled} onClick={() => onDecision("accept")} type="button">核对并批准</button>}
        {canDecline && <><button className="button button-secondary" disabled={disabled} onClick={() => onDecision("reject")} type="button">拒绝</button><button className="button button-quiet" disabled={disabled} onClick={() => onDecision("defer")} type="button">暂存</button></>}
        {actionable && proposal.editable === true && !repairManaged && <button className="button button-quiet" disabled={approvalDisabled} onClick={() => setEditing((value) => !value)} type="button">编辑候选</button>}
      </div>
      {editing && <><textarea aria-label="编辑提案候选" rows={10} value={text} onChange={(event) => setText(event.target.value)} /><button className="button button-secondary" type="button" disabled={approvalDisabled || !text.trim()} onClick={() => onDecision("edit", text)}>保存新候选（需重新批准）</button></>}
      {proposal.status === "approved" && repairManaged && <p role="status">已批准；发布仍由修复工作台应用候选并生成回执。</p>}
      {proposal.status === "approved" && !repairManaged && !hasTask && receipt !== true && <button className="button button-secondary" disabled={approvalDisabled} type="button" onClick={onRetry}>执行已批准版本</button>}
    </details>
  </>;
}
