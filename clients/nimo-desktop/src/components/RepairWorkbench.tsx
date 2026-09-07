import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import type {
  AuthoringProposalView,
  EngineClient,
  EngineCommandClient,
  RepairCase,
  RepairCaseDetailView,
  RepairCaseStatus,
  RepairSourceView,
} from "@nimo/engine-contracts";

import { OverlaySurface } from "./OverlaySurface";
import "../styles/repair-workbench.css";

type RepairTab = "issues" | "location" | "candidate" | "verification" | "history";
type RepairScope = "chapter" | "project";

interface RepairWorkbenchProps {
  readonly chapterNumber: number;
  readonly commandClient: EngineCommandClient;
  readonly engineClient: EngineClient;
  readonly onClose: () => void;
  readonly onPublished?: () => void;
  readonly projectId: string;
}

const tabs: readonly { readonly id: RepairTab; readonly label: string; readonly step: number }[] = [
  { id: "issues", label: "问题", step: 1 },
  { id: "location", label: "定位", step: 2 },
  { id: "candidate", label: "候选", step: 3 },
  { id: "verification", label: "验证与发布", step: 4 },
  { id: "history", label: "历史", step: 5 },
];

const attentionStatuses = new Set<RepairCaseStatus>([
  "open",
  "located",
  "candidate_ready",
  "needs_verification",
  "verified",
  "awaiting_approval",
  "manual_required",
  "failed",
]);

export function summarizeRepairCases(cases: readonly RepairCase[]) {
  const attention = cases.filter((item) => attentionStatuses.has(item.status));
  const urgent = attention.filter((item) => (item.issues ?? []).some(
    (issue) => issue.severity === "critical" || issue.severity === "high",
  ));
  return { attention: attention.length, urgent: urgent.length };
}

function proposalIsRepairManaged(proposal: AuthoringProposalView | null): boolean {
  if (proposal === null) return false;
  return (proposal.applicationResult.approvalFlow
    ?? proposal.applicationResult.approval_flow) === "repair_workbench";
}

function tabCompleted(tab: RepairTab, detail: RepairCaseDetailView | null): boolean {
  if (detail === null) return false;
  if (tab === "issues") return true;
  if (tab === "location") return detail.case.targets.length > 0;
  if (tab === "candidate") return detail.case.candidates.length > 0;
  if (tab === "verification") return detail.case.verification?.passed === true;
  return detail.events.length > 0;
}

const statusLabels: Readonly<Record<RepairCaseStatus, string>> = {
  open: "待定位",
  located: "已定位",
  candidate_ready: "候选已准备",
  needs_verification: "需要复验",
  verified: "已验证",
  awaiting_approval: "等待批准",
  published: "已发布",
  resolved: "已确认兼容",
  manual_required: "需要人工处理",
  stale: "已过期",
  rejected: "已拒绝",
  deferred: "已延期",
  failed: "失败",
};

const authorityLabels: Readonly<Record<RepairCase["authority"], string>> = {
  automatic_derived: "派生产物可自动",
  automatic_working_candidate: "工作候选可自动",
  proposal_required: "需要提案批准",
  manual_only: "仅限人工",
};

const sourceLabels: Readonly<Record<string, string>> = {
  author_annotation: "作者标注",
  long_continuity_review: "长篇连贯性审查",
  long_causal_review: "长篇因果审查",
  long_reading_power_review: "长篇追读力审查",
  book_consistency_audit: "全书一致性审查",
  model_semantic_adjudication: "上游语义裁决",
};

function displayValue(value: unknown): string {
  if (value === undefined || value === null || value === "") return "—";
  return typeof value === "string" ? value : JSON.stringify(value, null, 2);
}

function latestCandidateVersion(detail: RepairCaseDetailView): number {
  return detail.case.candidates.at(-1)?.version ?? 0;
}

interface SemanticClaimView {
  readonly artifact: string;
  readonly claimId: string;
  readonly claimText: string;
  readonly evidence: string;
}

function stringList(value: unknown): readonly string[] {
  return Array.isArray(value)
    ? value.map((item) => String(item).trim()).filter((item) => item.length > 0)
    : [];
}

function semanticClaimIds(detail: RepairCaseDetailView): readonly string[] {
  return stringList(detail.case.metadata.claim_ids ?? detail.case.metadata.claimIds);
}

function semanticClaims(detail: RepairCaseDetailView): readonly SemanticClaimView[] {
  if (typeof detail.sourcePayload !== "object" || detail.sourcePayload === null) return [];
  const payload = detail.sourcePayload as Readonly<Record<string, unknown>>;
  if (!Array.isArray(payload.claims)) return [];
  return payload.claims.flatMap((raw) => {
    if (typeof raw !== "object" || raw === null) return [];
    const claim = raw as Readonly<Record<string, unknown>>;
    const claimId = String(claim.claim_id ?? claim.claimId ?? "").trim();
    if (!claimId) return [];
    return [{
      artifact: String(claim.artifact ?? ""),
      claimId,
      claimText: String(claim.claim_text ?? claim.claimText ?? ""),
      evidence: String(claim.evidence ?? ""),
    }];
  });
}

export function sliceUnicodeRange(text: string, start: number, end: number): string {
  return Array.from(text).slice(start, end).join("");
}

export function RepairWorkbench({
  chapterNumber,
  commandClient,
  engineClient,
  onClose,
  onPublished,
  projectId,
}: RepairWorkbenchProps) {
  const [tab, setTab] = useState<RepairTab>("issues");
  const [scope, setScope] = useState<RepairScope>("chapter");
  const [source, setSource] = useState<RepairSourceView | null>(null);
  const [cases, setCases] = useState<readonly RepairCase[]>([]);
  const [selectedCaseId, setSelectedCaseId] = useState("");
  const [detail, setDetail] = useState<RepairCaseDetailView | null>(null);
  const [linkedProposal, setLinkedProposal] = useState<AuthoringProposalView | null>(null);
  const [proposalLoading, setProposalLoading] = useState(false);
  const [approvalAcknowledged, setApprovalAcknowledged] = useState(false);
  const [loading, setLoading] = useState(true);
  const [operation, setOperation] = useState("");
  const [error, setError] = useState("");
  const [sourceNotice, setSourceNotice] = useState("");
  const [statusFilter, setStatusFilter] = useState<RepairCaseStatus | "">("");
  const [severityFilter, setSeverityFilter] = useState("");
  const [sourceFilter, setSourceFilter] = useState("");
  const [contentTypeFilter, setContentTypeFilter] = useState("");
  const [selection, setSelection] = useState({ start: 0, end: 0, text: "" });
  const [summary, setSummary] = useState("");
  const [description, setDescription] = useState("");
  const [replacement, setReplacement] = useState("");
  const [authoritativeClaimIds, setAuthoritativeClaimIds] = useState<readonly string[]>([]);
  const [busy, setBusy] = useState(false);
  const sourceRef = useRef<HTMLTextAreaElement | null>(null);
  const proposalRequestRef = useRef(0);

  const loadLinkedProposal = useCallback(async (proposalId: string | undefined) => {
    const requestId = proposalRequestRef.current + 1;
    proposalRequestRef.current = requestId;
    setApprovalAcknowledged(false);
    if (!proposalId || engineClient.getAuthoringProposals === undefined) {
      setLinkedProposal(null);
      return null;
    }
    setLinkedProposal(null);
    setProposalLoading(true);
    try {
      const proposals = await engineClient.getAuthoringProposals(projectId);
      const proposal = proposals.find((item) => item.id === proposalId) ?? null;
      if (proposalRequestRef.current !== requestId) return null;
      setLinkedProposal(proposal);
      return proposal;
    } finally {
      if (proposalRequestRef.current === requestId) setProposalLoading(false);
    }
  }, [engineClient, projectId]);

  const reloadCases = useCallback(async (preferredCaseId = "") => {
    if (engineClient.listRepairCases === undefined) return;
    const loaded = await engineClient.listRepairCases(projectId, {
      ...(scope === "chapter" ? { chapter: chapterNumber } : {}),
      ...(contentTypeFilter ? { contentType: contentTypeFilter } : {}),
      ...(statusFilter ? { status: statusFilter } : {}),
      ...(severityFilter
        ? { severity: severityFilter as "critical" | "high" | "medium" | "low" }
        : {}),
      ...(sourceFilter ? { source: sourceFilter } : {}),
    });
    setCases(loaded);
    setSelectedCaseId((current) => {
      const requested = preferredCaseId || current;
      return loaded.some((item) => item.caseId === requested)
        ? requested
        : (loaded[0]?.caseId ?? "");
    });
  }, [
    chapterNumber,
    contentTypeFilter,
    engineClient,
    projectId,
    scope,
    severityFilter,
    sourceFilter,
    statusFilter,
  ]);

  useEffect(() => {
    let active = true;
    setLoading(true);
    setError("");
    setSourceNotice("");
    void Promise.allSettled([
      engineClient.getRepairSource?.(projectId, chapterNumber) ?? Promise.resolve(null),
      engineClient.listRepairCases?.(projectId, { chapter: chapterNumber })
        ?? Promise.resolve([]),
    ]).then(([sourceResult, casesResult]) => {
      if (!active) return;
      if (sourceResult.status === "fulfilled") {
        setSource(sourceResult.value);
      } else {
        setSource(null);
        setSourceNotice("当前章节没有可标注正文；历史案例和非写入操作仍可使用。");
      }
      if (casesResult.status === "fulfilled") {
        setCases(casesResult.value);
        setSelectedCaseId(casesResult.value[0]?.caseId ?? "");
      } else {
        setError(casesResult.reason instanceof Error
          ? casesResult.reason.message
          : String(casesResult.reason));
      }
    }).finally(() => {
      if (active) setLoading(false);
    });
    return () => { active = false; };
  }, [chapterNumber, engineClient, projectId]);

  useEffect(() => {
    if (selectedCaseId === "" || engineClient.getRepairCase === undefined) {
      setDetail(null);
      setLinkedProposal(null);
      return;
    }
    let active = true;
    setError("");
    void engineClient.getRepairCase(projectId, selectedCaseId).then((value) => {
      if (!active) return;
      setDetail(value);
      void loadLinkedProposal(value.case.proposalId).catch((reason: unknown) => {
        if (active) setError(reason instanceof Error ? reason.message : String(reason));
      });
      const target = value.case.targets[0];
      setReplacement(typeof target?.currentValue === "string" ? target.currentValue : "");
      setAuthoritativeClaimIds([]);
    }).catch((reason: unknown) => {
      if (active) setError(reason instanceof Error ? reason.message : String(reason));
    });
    return () => { active = false; };
  }, [engineClient, loadLinkedProposal, projectId, selectedCaseId]);

  useEffect(() => {
    if (!loading) void reloadCases();
  }, [contentTypeFilter, loading, reloadCases, severityFilter, sourceFilter, statusFilter]);

  const primaryIssue = detail?.case.issues[0] ?? null;
  const primaryLocator = primaryIssue?.repairTargets[0] ?? null;
  const semanticCase = detail?.case.contentType === "semantic_consistency";
  const sourceText = typeof detail?.sourcePayload === "string"
    ? detail.sourcePayload
    : semanticCase && detail !== null
      ? displayValue(detail.sourcePayload)
      : (source?.content ?? "");
  const candidateText = typeof detail?.candidatePayload === "string"
    ? detail.candidatePayload
    : detail?.candidatePayload == null
      ? "尚未保存候选。"
      : displayValue(detail.candidatePayload);
  const claims = useMemo(() => detail === null ? [] : semanticClaims(detail), [detail]);
  const highlighted = useMemo(() => {
    if (primaryLocator === null || sourceText === "") return null;
    const start = primaryLocator.charStart ?? 0;
    const end = primaryLocator.charEnd ?? 0;
    if (end <= start || sliceUnicodeRange(sourceText, start, end) !== primaryLocator.quote) {
      return null;
    }
    return {
      before: sliceUnicodeRange(sourceText, Math.max(0, start - 120), start),
      selected: sliceUnicodeRange(sourceText, start, end),
      after: sliceUnicodeRange(sourceText, end, end + 120),
    };
  }, [primaryLocator, sourceText]);
  const caseSummary = useMemo(() => summarizeRepairCases(cases), [cases]);
  const linkedRepairProposal = proposalIsRepairManaged(linkedProposal) ? linkedProposal : null;
  const inlineApprovalAvailable = linkedRepairProposal?.status === "pending"
    && commandClient.decideAuthoringProposal !== undefined;
  const nextAction = detail === null
    ? "先选择一个问题"
    : detail.capabilities.verify
      ? "重新运行原审查器"
      : detail.capabilities.requestApproval
        ? "创建版本化修订提案"
        : inlineApprovalAvailable
          ? "核对并批准当前候选"
          : detail.capabilities.publish
            ? "应用已批准的精确修订"
            : detail.capabilities.recover
              ? "核对发布回执"
              : "等待满足下一项安全门禁";

  const refreshSelection = () => {
    const textarea = sourceRef.current;
    if (textarea === null) return;
    const utf16Start = textarea.selectionStart;
    const utf16End = textarea.selectionEnd;
    const selectedText = textarea.value.slice(utf16Start, utf16End);
    const start = Array.from(textarea.value.slice(0, utf16Start)).length;
    const end = start + Array.from(selectedText).length;
    setSelection({ start, end, text: selectedText });
  };

  const createAnnotation = async () => {
    if (
      source === null
      || selection.text === ""
      || summary.trim() === ""
      || commandClient.createRepairAnnotation === undefined
    ) return;
    setBusy(true);
    setError("");
    try {
      const created = await commandClient.createRepairAnnotation(projectId, {
        chapterNumber,
        artifactId: source.artifactId,
        sourceHash: source.sourceHash,
        charStart: selection.start,
        charEnd: selection.end,
        selectedText: selection.text,
        summary: summary.trim(),
        description: description.trim(),
        severity: "medium",
      });
      setOperation("人工问题已保存；正文未改变。可在“候选”页编辑精确替换文本。");
      setSummary("");
      setDescription("");
      setDetail(created);
      await reloadCases(created.case.caseId);
      setSelectedCaseId(created.case.caseId);
      setTab("location");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setBusy(false);
    }
  };

  const saveCandidate = async () => {
    if (detail === null || commandClient.saveRepairCandidate === undefined) return;
    setBusy(true);
    setError("");
    try {
      const updated = await commandClient.saveRepairCandidate(projectId, detail.case.caseId, {
        caseVersion: detail.case.version,
        candidateVersion: latestCandidateVersion(detail),
        replacementText: replacement,
      });
      setDetail(updated);
      await loadLinkedProposal(updated.case.proposalId);
      await reloadCases(updated.case.caseId);
      setOperation("候选已隔离保存，旧验证已清除；正式正文未改变。");
      setTab("verification");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setBusy(false);
    }
  };

  const verifyCandidate = async () => {
    if (detail === null || commandClient.verifyRepairCandidate === undefined) return;
    const candidateVersion = latestCandidateVersion(detail);
    if (candidateVersion === 0) return;
    setBusy(true);
    setError("");
    try {
      const updated = await commandClient.verifyRepairCandidate(projectId, detail.case.caseId, {
        caseVersion: detail.case.version,
        candidateVersion,
      });
      setDetail(updated);
      await loadLinkedProposal(updated.case.proposalId);
      await reloadCases(updated.case.caseId);
      setOperation("原审查器已完成复验。");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setBusy(false);
    }
  };

  const decide = async (
    decision: "reject" | "defer" | "accept_compatible" | "authoritative_claims",
  ) => {
    if (detail === null || commandClient.decideRepairCase === undefined) return;
    setBusy(true);
    setError("");
    try {
      const semantic = decision === "accept_compatible" || decision === "authoritative_claims";
      const claimIds = semanticClaimIds(detail);
      const updated = await commandClient.decideRepairCase(projectId, detail.case.caseId, {
        caseVersion: detail.case.version,
        decision,
        reason: decision === "reject"
          ? "作者拒绝此修复案例"
          : decision === "defer"
            ? "作者延期处理此修复案例"
            : decision === "accept_compatible"
              ? "作者确认这些精确 claims 可同时成立"
              : "作者指定精确权威 claims，候选仍需复验与批准",
        ...(semantic ? { sourceHash: detail.case.sourceHash, claimIds } : {}),
        ...(decision === "authoritative_claims"
          ? { authoritativeClaimIds }
          : {}),
      });
      setDetail(updated);
      await loadLinkedProposal(updated.case.proposalId);
      await reloadCases(updated.case.caseId);
      setOperation(({ reject: "案例已拒绝。", defer: "案例已延期。", accept_compatible: "已绑定当前来源哈希与 claims：作者确认可同时成立。", authoritative_claims: "已绑定作者指定的权威 claims；未发布任何来源。" })[decision]);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setBusy(false);
    }
  };

  const refreshDetail = async () => {
    if (detail === null || engineClient.getRepairCase === undefined) return;
    setBusy(true);
    setError("");
    try {
      const updated = await engineClient.getRepairCase(projectId, detail.case.caseId);
      setDetail(updated);
      await loadLinkedProposal(updated.case.proposalId);
      await reloadCases(updated.case.caseId);
      setOperation("已刷新候选、提案批准与回执状态。");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setBusy(false);
    }
  };

  const requestApproval = async () => {
    if (detail === null || commandClient.requestRepairApproval === undefined) return;
    const candidateVersion = latestCandidateVersion(detail);
    if (candidateVersion === 0) return;
    setBusy(true);
    setError("");
    try {
      const updated = await commandClient.requestRepairApproval(
        projectId,
        detail.case.caseId,
        { caseVersion: detail.case.version, candidateVersion },
      );
      setDetail(updated);
      const proposal = await loadLinkedProposal(updated.case.proposalId);
      await reloadCases(updated.case.caseId);
      setOperation(proposal === null
        ? "版本化修订提案已创建；正文未改变。当前客户端不能原位批准，请在共创提案中核对。"
        : "版本化修订提案已创建；正文未改变。请在当前页核对差异并精确批准。");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setBusy(false);
    }
  };

  const approveLinkedProposal = async () => {
    if (
      detail === null
      || linkedProposal === null
      || linkedProposal.status !== "pending"
      || !proposalIsRepairManaged(linkedProposal)
      || commandClient.decideAuthoringProposal === undefined
      || engineClient.getRepairCase === undefined
    ) return;
    setBusy(true);
    setError("");
    try {
      const approved = await commandClient.decideAuthoringProposal(
        projectId,
        linkedProposal.id,
        {
          decision: "accept",
          candidateVersion: linkedProposal.candidateVersion,
          inputVersion: linkedProposal.inputVersion,
          policyVersion: linkedProposal.policyVersion,
        },
      );
      setLinkedProposal(approved);
      setApprovalAcknowledged(false);
      const updated = await engineClient.getRepairCase(projectId, detail.case.caseId);
      setDetail(updated);
      await reloadCases(updated.case.caseId);
      setOperation("候选已精确批准；正文仍未改变。现在可通过修复回执应用该版本。");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setBusy(false);
    }
  };

  const publishCandidate = async () => {
    if (detail === null || commandClient.publishRepairCase === undefined) return;
    const candidateVersion = latestCandidateVersion(detail);
    if (candidateVersion === 0) return;
    setBusy(true);
    setError("");
    try {
      const updated = await commandClient.publishRepairCase(projectId, detail.case.caseId, {
        caseVersion: detail.case.version,
        candidateVersion,
        authorityVersion: detail.case.policyVersion ?? 0,
      });
      setDetail(updated);
      await loadLinkedProposal(updated.case.proposalId);
      await reloadCases(updated.case.caseId);
      setOperation("已通过批准的修订提案应用；发布回执已保存。");
      onPublished?.();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setBusy(false);
    }
  };

  const recoverReceipt = async () => {
    const receipt = detail?.case.receipt;
    if (detail === null || receipt == null || commandClient.recoverRepairReceipt === undefined) return;
    setBusy(true);
    setError("");
    try {
      const updated = await commandClient.recoverRepairReceipt(projectId, detail.case.caseId, {
        caseVersion: detail.case.version,
        receiptId: receipt.receiptId,
      });
      setDetail(updated);
      await loadLinkedProposal(updated.case.proposalId);
      await reloadCases(updated.case.caseId);
      setOperation("已核对发布回执；未重放旧候选。");
      onPublished?.();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setBusy(false);
    }
  };

  return (
    <OverlaySurface
      ariaLabel={`第 ${chapterNumber} 章修复工作台`}
      backdropClassName="repair-workbench-backdrop"
      className="repair-workbench-surface"
      onClose={onClose}
    >
      <section className="repair-workbench">
        <header className="repair-workbench-header">
          <div>
            <span className="section-kicker">候选优先 · 非正史证据</span>
            <h2>统一修复工作台</h2>
            <p>{scope === "chapter" ? `第 ${chapterNumber} 章` : "全书证据"} · 审查 → 精确定位 → 隔离候选 → 原审查器复验 → 分级发布</p>
          </div>
          <div className="repair-workbench-header-actions">
            <span className="repair-workbench-lock">分级发布 · 正文需批准</span>
            <button className="button button-secondary" onClick={onClose} type="button">
              关闭
            </button>
          </div>
        </header>

        <div className="repair-workbench-tabs" role="tablist">
          {tabs.map((item) => (
            <button
              aria-label={`第 ${item.step} 步 ${item.label}${tabCompleted(item.id, detail) ? "，已完成" : ""}`}
              aria-selected={tab === item.id}
              className={`${tab === item.id ? "is-active" : ""}${tabCompleted(item.id, detail) ? " is-complete" : ""}`.trim()}
              key={item.id}
              onClick={() => setTab(item.id)}
              role="tab"
              type="button"
            >
              <span>{tabCompleted(item.id, detail) ? "✓" : item.step}</span>
              {item.label}
            </button>
          ))}
        </div>

        {operation && <p aria-live="polite" className="repair-workbench-notice">{operation}</p>}
        {sourceNotice && <p aria-live="polite" className="repair-workbench-notice">{sourceNotice}</p>}
        {error && <p aria-live="assertive" className="repair-workbench-error">{error}</p>}

        <div className="repair-workbench-body">
          <aside className="repair-workbench-list" aria-label="修复问题列表">
            <div className="repair-workbench-list-heading">
              <div>
                <strong>{scope === "chapter" ? `第 ${chapterNumber} 章问题` : "全书修复证据"}</strong>
                <small>{cases.length} 项 · {caseSummary.attention} 项待处理{caseSummary.urgent > 0 ? ` · ${caseSummary.urgent} 项高优先级` : ""}</small>
              </div>
              <div className="repair-workbench-scope" role="group" aria-label="修复证据范围">
                <button className={scope === "chapter" ? "is-active" : ""} onClick={() => setScope("chapter")} type="button">本章</button>
                <button className={scope === "project" ? "is-active" : ""} onClick={() => setScope("project")} type="button">全书</button>
              </div>
            </div>
            <div className="repair-workbench-filters">
              <select aria-label="内容类型" onChange={(event) => setContentTypeFilter(event.target.value)} value={contentTypeFilter}>
                <option value="">全部类型</option>
                <option value="chapter_text">章节正文</option>
                <option value="long_chapter_continuity">长篇·连贯性</option>
                <option value="long_chapter_causal">长篇·因果</option>
                <option value="long_chapter_reading_power">长篇·追读力</option>
                <option value="book_repair_chapter">全书·逐章候选</option>
                <option value="semantic_consistency">上游·语义一致性</option>
              </select>
              <select aria-label="问题状态" onChange={(event) => setStatusFilter(event.target.value as RepairCaseStatus | "")} value={statusFilter}>
                <option value="">全部状态</option>
                <option value="open">待定位</option>
                <option value="located">已定位</option>
                <option value="candidate_ready">候选已准备</option>
                <option value="needs_verification">需要复验</option>
                <option value="verified">已验证</option>
                <option value="awaiting_approval">等待批准</option>
                <option value="published">已发布</option>
                <option value="resolved">已确认兼容</option>
                <option value="manual_required">需要人工</option>
                <option value="stale">已过期</option>
                <option value="rejected">已拒绝</option>
                <option value="deferred">已延期</option>
                <option value="failed">失败</option>
              </select>
              <select aria-label="严重度" onChange={(event) => setSeverityFilter(event.target.value)} value={severityFilter}>
                <option value="">全部严重度</option>
                <option value="critical">严重</option>
                <option value="high">高</option>
                <option value="medium">中</option>
                <option value="low">低</option>
              </select>
              <select aria-label="问题来源" onChange={(event) => setSourceFilter(event.target.value)} value={sourceFilter}>
                <option value="">全部来源</option>
                <option value="author_annotation">作者标注</option>
                <option value="long_continuity_review">长篇连贯性审查</option>
                <option value="long_causal_review">长篇因果审查</option>
                <option value="long_reading_power_review">长篇追读力审查</option>
                <option value="book_consistency_audit">全书一致性审查</option>
                <option value="model_semantic_adjudication">上游语义裁决</option>
              </select>
            </div>
            <div className="repair-workbench-case-list">
              {loading && <p>正在读取修复证据…</p>}
              {!loading && cases.length === 0 && <p>本章暂无修复问题，可在右侧圈选正文创建。</p>}
              {cases.map((item) => (
                <button
                  className={selectedCaseId === item.caseId ? "is-selected" : ""}
                  key={item.caseId}
                  onClick={() => setSelectedCaseId(item.caseId)}
                  type="button"
                >
                  <strong>{item.title || (item.issues ?? [])[0]?.summary || "未命名问题"}</strong>
                  <span>{statusLabels[item.status]} · {authorityLabels[item.authority]}</span>
                  <small>{(item.chapterNumbers ?? []).length > 0 ? `第 ${(item.chapterNumbers ?? []).join("、")} 章 · ` : ""}{sourceLabels[item.source] ?? item.source} · v{item.version}</small>
                </button>
              ))}
            </div>
          </aside>

          <main className="repair-workbench-content">
            {tab === "issues" && (
              <div className="repair-workbench-annotation">
                <div className="repair-workbench-section-heading">
                  <div>
                    <h3>作者人工圈选</h3>
                    <p>选择正文字符区间后创建问题；保存的是哈希与位置，不会改写正文。</p>
                  </div>
                  {source && <span>{source.state === "official" ? "正式正文" : "工作候选"}</span>}
                </div>
                <textarea
                  aria-label="可圈选章节正文"
                  onKeyUp={refreshSelection}
                  onMouseUp={refreshSelection}
                  readOnly
                  ref={sourceRef}
                  value={source?.content ?? "当前章节没有可标注正文。"}
                />
                <p className="repair-workbench-selection">
                  {selection.text
                    ? `已选 ${selection.end - selection.start} 字：${selection.text}`
                    : "请在上方正文中拖动选择需要精确修复的文字。"}
                </p>
                <div className="repair-workbench-form-grid">
                  <label>
                    问题标题
                    <input onChange={(event) => setSummary(event.target.value)} value={summary} />
                  </label>
                  <label>
                    修复边界
                    <textarea onChange={(event) => setDescription(event.target.value)} value={description} />
                  </label>
                </div>
                <button
                  className="button button-primary"
                  disabled={busy || source === null || selection.text === "" || summary.trim() === "" || commandClient.createRepairAnnotation === undefined}
                  onClick={() => { void createAnnotation(); }}
                  type="button"
                >
                  创建人工问题
                </button>
              </div>
            )}

            {tab === "location" && (
              detail === null ? <EmptyCase /> : (
                <div className="repair-workbench-location">
                  <div className="repair-workbench-section-heading">
                    <div><h3>{primaryIssue?.summary ?? detail.case.title}</h3><p>{primaryIssue?.description}</p></div>
                    <span>{detail.case.artifactId}</span>
                  </div>
                  {highlighted === null ? (
                    <p className="repair-workbench-empty">该问题没有安全的精确高亮，必须降级人工处理。</p>
                  ) : (
                    <pre>{highlighted.before}<mark>{highlighted.selected}</mark>{highlighted.after}</pre>
                  )}
                  <div className="repair-workbench-evidence-grid">
                    <Evidence label="稳定节点" value={primaryLocator?.stableNodeId} />
                    <Evidence label="字段路径" value={primaryLocator?.fieldPath} />
                    <Evidence label="字符区间" value={primaryLocator ? `${primaryLocator.charStart ?? 0}–${primaryLocator.charEnd ?? 0}` : ""} />
                    <Evidence label="容器哈希" value={primaryLocator?.containerHash} />
                    <Evidence label="比较器" value={primaryLocator?.comparatorId} />
                    <Evidence label="原始期望值" value={primaryLocator?.expectedRaw} />
                    <Evidence label="原始实际值" value={primaryLocator?.actualRaw} />
                    <Evidence label="规范化期望值" value={primaryLocator?.expectedNormalized} />
                    <Evidence label="规范化实际值" value={primaryLocator?.actualNormalized} />
                  </div>
                </div>
              )
            )}

            {tab === "candidate" && (
              detail === null ? <EmptyCase /> : (
                <div className="repair-workbench-candidate">
                  <div className="repair-workbench-section-heading">
                    <div><h3>三方候选对比</h3><p>人工编辑只替换已解析的精确靶点；保存后必须重新复验。</p></div>
                    <span>变更比例 {((detail.case.candidates.at(-1)?.changeRatio ?? 0) * 100).toFixed(2)}%</span>
                  </div>
                  <div className="repair-workbench-diff-grid">
                    <label>原文<textarea readOnly value={sourceText} /></label>
                    <label>已保存候选<textarea readOnly value={candidateText} /></label>
                    <label>人工精确替换<textarea onChange={(event) => setReplacement(event.target.value)} value={replacement} /></label>
                  </div>
                  <div className="repair-workbench-protection">
                    <strong>保护项</strong>
                    <span>{detail.case.candidates.at(-1)?.protectedItems.join(" · ") || "未圈选正文 · 作者锁定内容 · 结局 · 人物命运 · 世界规则"}</span>
                  </div>
                  <button className="button button-primary" disabled={busy || !detail.capabilities.edit || commandClient.saveRepairCandidate === undefined} onClick={() => { void saveCandidate(); }} type="button">
                    保存隔离候选
                  </button>
                </div>
              )
            )}

            {tab === "verification" && (
              detail === null ? <EmptyCase /> : (
                <div className="repair-workbench-verification">
                  <div className="repair-workbench-section-heading">
                    <div><h3>复验与发布资格</h3><p>影子结果不等于通过；只有原审查器和必需门禁共同决定验证状态。</p></div>
                    <span>{statusLabels[detail.case.status]}</span>
                  </div>
                  <div className="repair-workbench-gates">
                    <Evidence label="原问题关闭" value={detail.case.verification?.resolvedIssueIds.join("、") || "未复验"} />
                    <Evidence label="残留问题" value={detail.case.verification?.residualIssueIds.join("、") || "—"} />
                    <Evidence label="新增回归" value={detail.case.verification?.regressionIssueIds.join("、") || "—"} />
                    <Evidence label="发布资格" value={detail.capabilities.publish ? "具备" : "不具备"} />
                  </div>
                  {semanticCase && claims.length > 0 && <section className="repair-workbench-semantic-decision" aria-label="作者语义决定">
                    <h4>精确 Claim 证据</h4>
                    <p>决定只绑定当前来源哈希和下列 Claim IDs；来源变化后自动失效。</p>
                    {claims.map((claim) => <label key={claim.claimId}>
                      <input
                        checked={authoritativeClaimIds.includes(claim.claimId)}
                        onChange={(event) => setAuthoritativeClaimIds((current) => event.target.checked
                          ? [...current, claim.claimId]
                          : current.filter((item) => item !== claim.claimId))}
                        type="checkbox"
                      />
                      <span><strong>{claim.claimId}</strong> · {claim.artifact}<br />{claim.claimText || claim.evidence}</span>
                    </label>)}
                    <div className="repair-workbench-actions">
                      <button className="button button-secondary" disabled={busy || !detail.capabilities.rejectOrDefer || commandClient.decideRepairCase === undefined} onClick={() => { void decide("accept_compatible"); }} type="button">确认可同时成立</button>
                      <button className="button button-primary" disabled={busy || !detail.capabilities.rejectOrDefer || authoritativeClaimIds.length === 0 || commandClient.decideRepairCase === undefined} onClick={() => { void decide("authoritative_claims"); }} type="button">将所选 Claim 设为权威</button>
                    </div>
                  </section>}
                  {detail.case.verification?.validators.map((validator) => (
                    <div className={`repair-workbench-validator ${validator.passed ? "is-pass" : "is-fail"}`} key={validator.validatorId}>
                      <strong>{validator.passed ? "通过" : "未通过"} · {validator.validatorId}</strong>
                      <span>{validator.details.join("；") || "无附加说明"}</span>
                    </div>
                  ))}
                  {proposalLoading && <p className="repair-workbench-proposal-sync" role="status">正在同步修订提案与批准状态…</p>}
                  {linkedRepairProposal && (
                    <section className={`repair-workbench-approval is-${linkedRepairProposal.status}`} aria-label="精确修复批准">
                      <header>
                        <div>
                          <span>版本化修订提案</span>
                          <strong>{linkedRepairProposal.title}</strong>
                        </div>
                        <b>{({ pending: "待批准", approved: "已批准", rejected: "已拒绝", deferred: "已延期", stale: "已过期", applied: "已应用" })[linkedRepairProposal.status]}</b>
                      </header>
                      <div className="repair-workbench-approval-diff">
                        <div><span>原文</span><pre>{linkedRepairProposal.original || "尚无正式正文"}</pre></div>
                        <div><span>候选</span><pre>{linkedRepairProposal.candidate}</pre></div>
                      </div>
                      <p>影响章节：{linkedRepairProposal.affectedChapters.join("、") || "当前章"}。{linkedRepairProposal.costHint}</p>
                      {[...linkedRepairProposal.risks, ...linkedRepairProposal.lockConflicts].length > 0 && (
                        <p className="repair-workbench-approval-risk">风险与锁定：{[...linkedRepairProposal.risks, ...linkedRepairProposal.lockConflicts].join("；")}</p>
                      )}
                      {linkedRepairProposal.status === "pending" && (
                        <div className="repair-workbench-approval-confirm">
                          <label>
                            <input checked={approvalAcknowledged} onChange={(event) => setApprovalAcknowledged(event.target.checked)} type="checkbox" />
                            我已核对候选、影响章节和锁定冲突，只批准当前版本
                          </label>
                          <button className="button button-primary" disabled={busy || !approvalAcknowledged || commandClient.decideAuthoringProposal === undefined} onClick={() => { void approveLinkedProposal(); }} type="button">批准此候选（暂不改正文）</button>
                        </div>
                      )}
                      {linkedRepairProposal.status === "approved" && <p className="repair-workbench-approval-ready" role="status">批准已绑定当前候选；请通过下方修复回执应用，通用提案入口不会直接改正文。</p>}
                    </section>
                  )}
                  <section className="repair-workbench-next-action" aria-label="当前下一步">
                    <div>
                      <span>当前下一步</span>
                      <strong>{nextAction}</strong>
                      <p>{detail.capabilities.reason}</p>
                    </div>
                    <div className="repair-workbench-actions">
                      {detail.capabilities.verify && <button className="button button-primary" disabled={busy || commandClient.verifyRepairCandidate === undefined} onClick={() => { void verifyCandidate(); }} type="button">重新复验</button>}
                      {detail.capabilities.requestApproval && <button className="button button-primary" disabled={busy || commandClient.requestRepairApproval === undefined} onClick={() => { void requestApproval(); }} type="button">创建修订提案</button>}
                      {detail.capabilities.publish && <button className="button button-primary" disabled={busy || commandClient.publishRepairCase === undefined} onClick={() => { void publishCandidate(); }} type="button">应用已批准提案</button>}
                      {detail.capabilities.recover && <button className="button button-primary" disabled={busy || commandClient.recoverRepairReceipt === undefined} onClick={() => { void recoverReceipt(); }} type="button">核对发布回执</button>}
                      {!detail.capabilities.verify && !detail.capabilities.requestApproval && !detail.capabilities.publish && !detail.capabilities.recover && !inlineApprovalAvailable && <button className="button button-secondary" disabled type="button">等待安全门禁</button>}
                    </div>
                  </section>
                  <div className="repair-workbench-secondary-actions">
                    <button className="button button-quiet" disabled={busy || engineClient.getRepairCase === undefined} onClick={() => { void refreshDetail(); }} type="button">刷新状态</button>
                    <button className="button button-secondary" disabled={busy || !detail.capabilities.rejectOrDefer} onClick={() => { void decide("defer"); }} type="button">延期处理</button>
                    <button className="button button-danger" disabled={busy || !detail.capabilities.rejectOrDefer} onClick={() => { void decide("reject"); }} type="button">拒绝案例</button>
                  </div>
                </div>
              )
            )}

            {tab === "history" && (
              detail === null ? <EmptyCase /> : (
                <div className="repair-workbench-history">
                  <div className="repair-workbench-section-heading"><div><h3>追加式事件轨迹</h3><p>投影可重建；恢复只核对回执，不重放旧内容。</p></div><span>{detail.events.length} 个事件</span></div>
                  <ol>
                    {detail.events.map((event) => (
                      <li key={event.eventId}>
                        <span>#{event.seq}</span>
                        <div><strong>{event.eventType}</strong><small>{event.actor} · case v{event.caseVersion}</small><pre>{JSON.stringify(event.data, null, 2)}</pre></div>
                      </li>
                    ))}
                  </ol>
                </div>
              )
            )}
          </main>
        </div>
      </section>
    </OverlaySurface>
  );
}

function Evidence({ label, value }: { readonly label: string; readonly value: unknown }) {
  return <div><span>{label}</span><strong>{displayValue(value)}</strong></div>;
}

function EmptyCase() {
  return <p className="repair-workbench-empty">请从左侧选择一个问题，或在“问题”页圈选正文创建。</p>;
}
