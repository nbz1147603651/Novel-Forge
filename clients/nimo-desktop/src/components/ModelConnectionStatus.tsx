import { memo, useState, useTransition } from "react";

import type { ConnectivitySession } from "../lib/connectivity-session";
import { connectivityDetail } from "../lib/connectivity-session";

const INITIAL_CARD_LIMIT = 12;

function statusClass(state: ConnectivitySession["entries"][number]["state"]): string {
  if (state === "succeeded") return "is-success";
  if (state === "failed" || state === "unavailable") return "is-failed";
  // The source keeps an untested card visually neutral; its amber dot carries
  // the pending state without turning the whole card into a warning surface.
  return "";
}

function capabilityClass(supported: boolean, state: ConnectivitySession["entries"][number]["state"]): string {
  if (state === "idle" || state === "queued" || state === "checking") return "is-pending";
  return supported && state === "succeeded" ? "is-success" : "is-failed";
}

function providerLabel(provider: string): string {
  return ({ openai: "OpenAI", deepseek: "DeepSeek", minimax: "MiniMax" } as Readonly<Record<string, string>>)[provider] ?? provider;
}

/**
 * A connection run changes at most the completed profile and the next queued
 * profile.  Preserve the rest of the card tree while the controller advances
 * its bounded queue, otherwise a long profile list turns each result into a
 * full card-grid repaint.
 */
function statusTooltip(entry: ConnectivitySession["entries"][number]): string {
  switch (entry.state) {
    case "succeeded": return entry.latencyMs === null
      ? entry.detail || "连接正常"
      : `连接正常（${entry.latencyMs}ms）${entry.detail ? `：${entry.detail}` : ""}`;
    case "failed": return `连接失败：${entry.detail || "请检查 API Key、模型 ID 或网络设置"}`;
    case "unavailable": return "未配置 API Key";
    case "checking": return "检测中…";
    case "queued": return "等待检测…";
    default: return "待检测";
  }
}

const ModelConnectionCard = memo(function ModelConnectionCard({ entry }: { readonly entry: ConnectivitySession["entries"][number] }) {
  return <article className={`model-connection-card ${statusClass(entry.state)} is-${entry.state}`}>
    <header><i aria-hidden="true" className="model-connection-dot" title={statusTooltip(entry)} /><strong title={entry.id}>{entry.label}</strong>{entry.state === "unavailable" && <small>🔒 不可调用</small>}</header>
    <p title={entry.detail || undefined}>{providerLabel(entry.provider)} · {connectivityDetail(entry)}</p>
    <div className="model-connection-capabilities"><span><i className={capabilityClass(entry.supportsThinking, entry.state)} />思考</span><span><i className={capabilityClass(entry.supportsMultiTurn, entry.state)} />多轮</span></div>
  </article>;
});

/** Source-shaped model status cards. The caller owns the command/session boundary. */
export function ModelConnectionStatus({
  onManageModels,
  session,
}: {
  readonly onManageModels: () => void;
  readonly session: ConnectivitySession;
}) {
  const isRunning = session.activeProfileIds.length > 0;
  const [showAllCards, setShowAllCards] = useState(false);
  const [isRevealing, startRevealTransition] = useTransition();
  const visibleEntries = showAllCards ? session.entries : session.entries.slice(0, INITIAL_CARD_LIMIT);
  const hiddenCount = session.entries.length - visibleEntries.length;
  const queuedCount = session.entries.filter((entry) => entry.state === "queued").length;
  const progressLabel = !isRunning
    ? null
    : queuedCount > 0
      ? `正在检测 ${session.activeProfileIds.length} 条通路；${queuedCount} 条排队中。`
      : `正在检测 ${session.activeProfileIds.length} 条通路。`;

  return <section aria-busy={isRunning || isRevealing} className={isRevealing ? "model-connection-status is-revealing" : "model-connection-status"}>
    <header>
      <div><h2>模型连接状态</h2><p>点击“检测全部通路”以检测模型接口连通性。卡片左上绿灯 = 正常（含延迟），红灯 = 异常；底部能力红点 = 不支持。</p>{progressLabel !== null && <span aria-live="polite" className="model-connection-progress">{progressLabel}</span>}</div>
      <button aria-label="模型管理 — 添加、编辑和删除模型" className="button button-secondary button-compact model-connection-manage" onClick={onManageModels} type="button">管理模型 <span aria-hidden="true">›</span></button>
    </header>
    <div className="model-connection-grid">{visibleEntries.map((entry) => <ModelConnectionCard entry={entry} key={entry.id} />)}</div>
    {hiddenCount > 0 && <button aria-busy={isRevealing || undefined} className="model-connection-reveal" disabled={isRevealing} onClick={() => startRevealTransition(() => setShowAllCards(true))} type="button">显示其余 {hiddenCount} 个模型档案</button>}
  </section>;
}
