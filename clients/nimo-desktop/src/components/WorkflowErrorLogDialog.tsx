import { useMemo, useState } from "react";

import type {
  ClearClosedTaskErrorsResult,
  TaskErrorResolutionResult,
  WorkflowErrorLogEntryView,
} from "@nimo/engine-contracts";
import { OverlaySurface } from "./OverlaySurface";

type ErrorLogFilter = "pending" | "handled" | "all";

interface WorkflowErrorLogDialogProps {
  readonly entries: readonly WorkflowErrorLogEntryView[];
  readonly onClose: () => void;
  readonly onAcknowledge: (
    entries: readonly WorkflowErrorLogEntryView[],
  ) => Promise<TaskErrorResolutionResult>;
  readonly onClearClosed: (
    entries: readonly WorkflowErrorLogEntryView[],
  ) => Promise<ClearClosedTaskErrorsResult>;
  readonly onReopen: (
    entries: readonly WorkflowErrorLogEntryView[],
  ) => Promise<TaskErrorResolutionResult>;
}

interface ErrorInsight {
  readonly category: string;
  readonly summary: string;
  readonly nextCheck: string;
  readonly repairState: string;
  readonly repairExplanation: string;
  readonly recoveryActionKinds: readonly string[];
}

interface ErrorGroup {
  readonly key: string;
  readonly entries: readonly WorkflowErrorLogEntryView[];
  readonly insight: ErrorInsight;
}

function compactText(value: string, limit = 150): string {
  const text = value.replaceAll(/\s+/g, " ").trim();
  return text.length > limit ? `${text.slice(0, limit)}…` : text;
}

const causeLabels: Readonly<Record<string, string>> = {
  known_format_alias: "兼容字段迁移",
  format_retry_exhausted: "格式重试耗尽",
  stale_runtime: "运行版本陈旧",
  intent_protection: "意图保护阻断",
  quality_regression: "质量回归",
  provider_unavailable: "模型 / 网络服务",
  format_contract_mismatch: "格式合同",
  not_auto_repairable: "不可自动修复",
};

const repairStateLabels: Readonly<Record<string, string>> = {
  resolved: "已自动无损修复",
  retryable: "可安全重试",
  blocked: "为保护用户意图而停止",
  exhausted: "自动重试已耗尽",
  rolled_back: "已回滚到安全版本",
  not_applicable: "不适用自动修复",
  unknown: "未分类",
};

function errorInsight(entry: WorkflowErrorLogEntryView): ErrorInsight {
  if (
    entry.causeCode !== undefined
    || entry.autoRepairState !== undefined
    || entry.autoRepairExplanation !== undefined
  ) {
    return {
      category: causeLabels[entry.causeCode ?? ""] ?? entry.kindLabel ?? "任务诊断",
      summary: compactText(entry.errorMessage)
        || compactText(entry.autoRepairExplanation ?? "")
        || "Engine 已提供结构化诊断。",
      nextCheck: entry.recommendedAction?.trim() || "依据 Engine 提供的恢复操作继续。",
      repairState: repairStateLabels[entry.autoRepairState ?? "unknown"] ?? "未分类",
      repairExplanation: entry.autoRepairExplanation?.trim() || "Engine 未提供停止原因。",
      recoveryActionKinds: entry.recoveryActionKinds ?? [],
    };
  }
  const message = `${entry.errorMessage}\n${entry.excerpt}`.toLowerCase();
  if (message.includes("all stream route attempts failed") || message.includes("model_gateway_error")) {
    return {
      category: "模型路由",
      summary: "所有候选流式通路均未得到可用响应。",
      nextCheck: "检查路由绑定、端点可达性、凭据与限流；“closed”仅表示断路器未熔断，并不代表请求已成功。",
      repairState: "旧日志未记录",
      repairExplanation: "客户端仅能识别为模型路由失败，无法确认后端是否尝试过自动修复。",
      recoveryActionKinds: [],
    };
  }
  if (message.includes("json") || message.includes("缺少字段") || message.includes("format")) {
    return {
      category: "格式合同",
      summary: compactText(entry.errorMessage) || "模型输出未通过结构化格式校验。",
      nextCheck: "检查任务对应的输出合同与提示词；若重试已耗尽，再调整模型或降级策略。",
      repairState: "旧日志未记录",
      repairExplanation: "缺少 Engine 结构化修复状态，当前仅按错误文本显示兼容提示。",
      recoveryActionKinds: [],
    };
  }
  return {
    category: entry.kindLabel || "任务失败",
    summary: compactText(entry.errorMessage) || "任务未提供可读的失败摘要。",
    nextCheck: "展开诊断摘录并根据完整运行日志确认根因，再决定是否重试或调整配置。",
    repairState: "旧日志未记录",
    repairExplanation: "历史日志没有自动修复结论；客户端不会据此生成修复按钮。",
    recoveryActionKinds: [],
  };
}

function groupEntries(entries: readonly WorkflowErrorLogEntryView[]): readonly ErrorGroup[] {
  const groups = new Map<string, WorkflowErrorLogEntryView[]>();
  for (const entry of entries) {
    const insight = errorInsight(entry);
    const key = [insight.category, entry.jobLabel, entry.taskLabel, insight.summary]
      .map((value) => value.replaceAll(/\s+/g, " ").trim().toLocaleLowerCase())
      .join("\u241f");
    const group = groups.get(key);
    if (group === undefined) groups.set(key, [entry]);
    else group.push(entry);
  }
  return [...groups.entries()].map(([key, groupedEntries]) => ({
    key,
    entries: groupedEntries,
    insight: errorInsight(groupedEntries[0]!),
  }));
}

function resolutionLabel(
  entry: WorkflowErrorLogEntryView,
  acknowledgedIds: ReadonlySet<string>,
  reopenedIds: ReadonlySet<string>,
): "pending" | "handled" | "automatic" {
  if (entry.autoResolved) return "automatic";
  if (reopenedIds.has(entry.id)) return "pending";
  return entry.acknowledgedAt || acknowledgedIds.has(entry.id) ? "handled" : "pending";
}

function formatAcknowledgedAt(value: string | undefined): string {
  if (value === undefined || value.length === 0) return "";
  const date = new Date(value);
  return Number.isNaN(date.valueOf()) ? value : date.toLocaleString("zh-CN", { hour12: false });
}

interface ErrorLogCardProps {
  readonly group: ErrorGroup;
  readonly acknowledgedIds: ReadonlySet<string>;
  readonly reopenedIds: ReadonlySet<string>;
  readonly selectedIds: ReadonlySet<string>;
  readonly isMutating: boolean;
  readonly onAcknowledge: (entryIds: readonly string[]) => void;
  readonly onReopen: (entryIds: readonly string[]) => void;
  readonly onToggleSelected: (entryIds: readonly string[]) => void;
}

function ErrorLogCard({ acknowledgedIds, group, isMutating, onAcknowledge, onReopen, onToggleSelected, reopenedIds, selectedIds }: ErrorLogCardProps) {
  const latest = group.entries[0]!;
  const pendingEntries = group.entries.filter((entry) => resolutionLabel(entry, acknowledgedIds, reopenedIds) === "pending");
  const handledEntries = group.entries.filter((entry) => resolutionLabel(entry, acknowledgedIds, reopenedIds) === "handled");
  const automaticEntries = group.entries.filter((entry) => resolutionLabel(entry, acknowledgedIds, reopenedIds) === "automatic");
  const pendingIds = pendingEntries.map((entry) => entry.id);
  const selectedCount = pendingIds.filter((entryId) => selectedIds.has(entryId)).length;
  const handledAt = handledEntries[0] === undefined
    ? ""
    : formatAcknowledgedAt(handledEntries[0].acknowledgedAt);
  const detail = [latest.errorMessage, latest.excerpt].filter((part) => part.trim().length > 0).join("\n\n");
  const safeLogPath = latest.logPath.startsWith("/") || /^[A-Za-z]:[\\/]/.test(latest.logPath)
    ? "已记录于本地运行日志"
    : latest.logPath;
  const cardTone = pendingEntries.length > 0 ? "is-pending" : handledEntries.length > 0 ? "is-handled" : "is-automatic";

  return (
    <article className={`workflow-error-log-card ${cardTone}`}>
      <header>
        <div className="workflow-error-log-card-title">
          <span className="workflow-error-log-category">{group.insight.category}</span>
          <h3>{group.insight.summary}</h3>
          <p>{latest.jobLabel} · {latest.taskLabel || latest.taskId || "未标注任务"}</p>
        </div>
        <div className="workflow-error-log-card-statuses">
          {pendingEntries.length > 0 ? <span className="is-pending">待确认 {pendingEntries.length}</span> : null}
          {handledEntries.length > 0 ? <span className="is-handled">已处理 {handledEntries.length}</span> : null}
          {automaticEntries.length > 0 ? <span className="is-automatic">自动恢复 {automaticEntries.length}</span> : null}
        </div>
      </header>
      <dl className="workflow-error-log-facts">
        <div><dt>最近发生</dt><dd>{latest.timeLabel || "—"}</dd></div>
        <div><dt>尝试</dt><dd>{latest.attemptLabel || "未记录"}</dd></div>
        <div><dt>自动修复</dt><dd>{group.insight.repairState}</dd></div>
        <div><dt>为何停止</dt><dd>{group.insight.repairExplanation}</dd></div>
        <div><dt>建议动作</dt><dd>{group.insight.nextCheck}</dd></div>
        {group.insight.recoveryActionKinds.length > 0 ? (
          <div><dt>可用恢复</dt><dd>{group.insight.recoveryActionKinds.join("、")}</dd></div>
        ) : null}
      </dl>
      {pendingEntries.length > 0 ? (
        <fieldset className="workflow-error-log-item-selector">
          <legend>逐条选择（已选 {selectedCount}/{pendingEntries.length}）</legend>
          <div>
            {pendingEntries.map((entry, index) => (
              <button
                aria-label={`选择第 ${index + 1} 条${group.insight.category}错误`}
                aria-pressed={selectedIds.has(entry.id)}
                disabled={isMutating}
                key={entry.id}
                onClick={() => onToggleSelected([entry.id])}
                type="button"
              >
                <span>
                  <strong>{selectedIds.has(entry.id) ? "已选" : "选择"} · 第 {index + 1} 条</strong>
                  <small>{entry.timeLabel || "未记录时间"} · {entry.taskLabel || entry.taskId || "未标注任务"}</small>
                </span>
              </button>
            ))}
          </div>
        </fieldset>
      ) : null}
      {handledAt.length > 0 ? <p className="workflow-error-log-acknowledged">已在 {handledAt} 确认处理；这不会自动修复模型或重新运行任务。</p> : null}
      {detail.length > 0 || safeLogPath.length > 0 ? (
        <details className="workflow-error-log-details">
          <summary>诊断摘录与运行日志</summary>
          {detail.length > 0 ? <pre>{detail}</pre> : null}
          {safeLogPath.length > 0 ? <p><strong>运行日志：</strong>{safeLogPath}</p> : null}
        </details>
      ) : null}
      <footer className="workflow-error-log-card-actions">
        {pendingIds.length > 0 ? <button className="workflow-error-log-confirm" disabled={isMutating} onClick={() => onAcknowledge(pendingIds)} type="button">确认这 {pendingIds.length} 条已处理</button> : null}
        {handledEntries.length > 0 ? <button className="workflow-error-log-reopen" disabled={isMutating} onClick={() => onReopen(handledEntries.map((entry) => entry.id))} type="button">重新打开</button> : null}
      </footer>
    </article>
  );
}

/**
 * A task error queue: errors are grouped by actionable root cause, and an
 * acknowledgement is persisted as author-review state without pretending that
 * an upstream model was repaired. Explicit cleanup prunes only the compact
 * diagnostic index; the Engine's full run log remains available for audit.
 */
export function WorkflowErrorLogDialog({ entries, onAcknowledge, onClearClosed, onClose, onReopen }: WorkflowErrorLogDialogProps) {
  const [filter, setFilter] = useState<ErrorLogFilter>("pending");
  const [selectedIds, setSelectedIds] = useState<ReadonlySet<string>>(() => new Set());
  const [clearedIds, setClearedIds] = useState<ReadonlySet<string>>(() => new Set());
  const [acknowledgedIds, setAcknowledgedIds] = useState<ReadonlySet<string>>(() => new Set());
  const [reopenedIds, setReopenedIds] = useState<ReadonlySet<string>>(() => new Set());
  const [clearNotice, setClearNotice] = useState("");
  const [activeMutation, setActiveMutation] = useState<"acknowledge" | "clear" | "reopen" | null>(null);
  const activeEntries = useMemo(
    () => entries.filter((entry) => !clearedIds.has(entry.id)),
    [clearedIds, entries],
  );
  const statusById = useMemo(
    () => new Map(activeEntries.map((entry) => [
      entry.id,
      resolutionLabel(entry, acknowledgedIds, reopenedIds),
    ])),
    [acknowledgedIds, activeEntries, reopenedIds],
  );
  const pendingEntries = useMemo(() => activeEntries.filter((entry) => statusById.get(entry.id) === "pending"), [activeEntries, statusById]);
  const handledEntries = useMemo(() => activeEntries.filter((entry) => statusById.get(entry.id) === "handled"), [activeEntries, statusById]);
  const automaticEntries = useMemo(() => activeEntries.filter((entry) => statusById.get(entry.id) === "automatic"), [activeEntries, statusById]);
  const closedEntries = useMemo(() => [...handledEntries, ...automaticEntries], [automaticEntries, handledEntries]);
  const visibleEntries = filter === "pending" ? pendingEntries : filter === "handled" ? closedEntries : activeEntries;
  const visibleGroups = useMemo(() => groupEntries(visibleEntries), [visibleEntries]);
  const visiblePendingIds = useMemo(() => visibleEntries.filter((entry) => statusById.get(entry.id) === "pending").map((entry) => entry.id), [statusById, visibleEntries]);

  const acknowledge = async (entryIds: readonly string[]) => {
    if (activeMutation !== null || entryIds.length === 0) return;
    setActiveMutation("acknowledge");
    setClearNotice("");
    try {
      const result = await onAcknowledge(
        activeEntries.filter((entry) => entryIds.includes(entry.id)),
      );
      const updated = new Set(result.updatedErrorEntryIds);
      if (updated.size === 0) throw new Error(result.message);
      setAcknowledgedIds((current) => new Set([...current, ...updated]));
      setReopenedIds((current) => {
        const next = new Set(current);
        updated.forEach((entryId) => next.delete(entryId));
        return next;
      });
      setSelectedIds((current) => {
        const next = new Set(current);
        updated.forEach((entryId) => next.delete(entryId));
        return next;
      });
      const skippedCount = entryIds.filter((entryId) => !updated.has(entryId)).length;
      setClearNotice(
        skippedCount > 0
          ? `已确认 ${updated.size} 条；${skippedCount} 条未更新，仍保持待确认。`
          : `${result.message} 条目已移入“已处理”，可按需清理已闭环诊断。`,
      );
    } catch (error) {
      setClearNotice(error instanceof Error ? error.message : "确认已处理失败，错误仍保持待确认状态。");
    } finally {
      setActiveMutation(null);
    }
  };
  const reopen = async (entryIds: readonly string[]) => {
    if (activeMutation !== null || entryIds.length === 0) return;
    setActiveMutation("reopen");
    setClearNotice("");
    try {
      const result = await onReopen(
        activeEntries.filter((entry) => entryIds.includes(entry.id)),
      );
      const updated = new Set(result.updatedErrorEntryIds);
      setReopenedIds((current) => new Set([...current, ...updated]));
      setAcknowledgedIds((current) => {
        const next = new Set(current);
        updated.forEach((entryId) => next.delete(entryId));
        return next;
      });
      setClearNotice(result.message);
    } catch (error) {
      setClearNotice(error instanceof Error ? error.message : "重新打开错误失败。");
    } finally {
      setActiveMutation(null);
    }
  };
  const clearClosed = async () => {
    if (closedEntries.length === 0 || activeMutation !== null) return;
    setActiveMutation("clear");
    setClearNotice("");
    try {
      const result = await onClearClosed(closedEntries);
      const removed = new Set(result.clearedErrorEntryIds);
      if (removed.size === 0) {
        setClearNotice(result.message || "Engine 未清理任何条目；待确认错误与运行日志均未改动。");
        return;
      }
      setClearedIds((current) => new Set([...current, ...removed]));
      setAcknowledgedIds((current) => {
        const next = new Set(current);
        removed.forEach((entryId) => next.delete(entryId));
        return next;
      });
      setReopenedIds((current) => {
        const next = new Set(current);
        removed.forEach((entryId) => next.delete(entryId));
        return next;
      });
      setClearNotice(result.message);
    } catch (error) {
      setClearNotice(error instanceof Error ? error.message : "清理已闭环错误失败。");
    } finally {
      setActiveMutation(null);
    }
  };
  const toggleSelected = (entryIds: readonly string[]) => setSelectedIds((current) => {
    const next = new Set(current);
    const everySelected = entryIds.every((entryId) => next.has(entryId));
    entryIds.forEach((entryId) => everySelected ? next.delete(entryId) : next.add(entryId));
    return next;
  });
  const selectVisiblePending = () => setSelectedIds(new Set(visiblePendingIds));
  const selectedPendingIds = [...selectedIds].filter((entryId) => pendingEntries.some((entry) => entry.id === entryId));
  const summary = `任务流错误日志 · ${activeEntries.length} 条`;

  return (
    <OverlaySurface ariaLabel="任务流错误日志" onClose={onClose}>
      <section className="workflow-error-log-dialog">
        <header className="workflow-error-log-header">
          <div>
            <span className="section-kicker">任务诊断</span>
            <h2>{summary}</h2>
            <p>先确认已核查的条目，再依据完整运行日志修复配置或重试任务。清理已闭环条目只移除诊断索引与对应终态任务卡，不删除完整运行日志。</p>
          </div>
          <dl aria-label="错误日志状态摘要" className="workflow-error-log-summary">
            <div className="is-pending"><dt>待确认</dt><dd>{pendingEntries.length}</dd></div>
            <div className="is-handled"><dt>已处理</dt><dd>{handledEntries.length}</dd></div>
            <div className="is-automatic"><dt>自动恢复</dt><dd>{automaticEntries.length}</dd></div>
          </dl>
        </header>
        <div aria-label="错误日志筛选" className="workflow-error-log-filters" role="tablist">
          <button aria-selected={filter === "pending"} className={filter === "pending" ? "is-active" : ""} onClick={() => setFilter("pending")} role="tab" type="button">待确认 ({pendingEntries.length})</button>
          <button aria-selected={filter === "handled"} className={filter === "handled" ? "is-active" : ""} onClick={() => setFilter("handled")} role="tab" type="button">已处理 ({handledEntries.length + automaticEntries.length})</button>
          <button aria-selected={filter === "all"} className={filter === "all" ? "is-active" : ""} onClick={() => setFilter("all")} role="tab" type="button">全部 ({entries.length})</button>
        </div>
        <div className="workflow-error-log-list-toolbar">
          <p>{filter === "pending" ? "按相同根因归并，避免重复失败淹没真正需要处理的项。" : "已闭环条目可重新打开，或清理诊断索引；完整运行日志仍保留。"}</p>
          {visiblePendingIds.length > 0 ? <button onClick={selectVisiblePending} type="button">全选待确认</button> : null}
        </div>
        <div aria-label="错误日志条目" className="workflow-error-log-list" role="list">
          {visibleGroups.length > 0 ? visibleGroups.map((group) => (
            <ErrorLogCard
              group={group}
              key={group.key}
              acknowledgedIds={acknowledgedIds}
              isMutating={activeMutation !== null}
              onAcknowledge={(entryIds) => void acknowledge(entryIds)}
              onReopen={(entryIds) => void reopen(entryIds)}
              onToggleSelected={toggleSelected}
              reopenedIds={reopenedIds}
              selectedIds={selectedIds}
            />
          )) : <div className="workflow-error-log-empty"><strong>当前筛选下没有条目</strong><p>新的任务错误会自动出现在“待确认”中。</p></div>}
        </div>
        <footer className="workflow-error-log-footer">
          <p aria-live="polite">{clearNotice || (selectedPendingIds.length > 0 ? `已选择 ${selectedPendingIds.length} 条待确认错误。` : "可逐条选择错误，或使用全选后记录已完成核查。")}</p>
          <div>
            {closedEntries.length > 0 ? (
              <button className="workflow-error-log-clear" disabled={activeMutation !== null} onClick={() => void clearClosed()} title="仅移除 Engine 已确认处理或自动恢复的诊断索引及对应终态任务卡；完整运行日志仍保留。" type="button">
                {activeMutation === "clear" ? "正在清理…" : `清理已闭环 (${closedEntries.length})`}
              </button>
            ) : null}
            <button className="workflow-error-log-primary" disabled={selectedPendingIds.length === 0 || activeMutation !== null} onClick={() => void acknowledge(selectedPendingIds)} type="button">确认已处理{selectedPendingIds.length > 0 ? ` (${selectedPendingIds.length})` : ""}</button>
            <button className="workflow-error-log-close" onClick={onClose} type="button">关闭</button>
          </div>
        </footer>
      </section>
    </OverlaySurface>
  );
}
