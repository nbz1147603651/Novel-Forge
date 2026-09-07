import { useState } from "react";

import type { TaskStreamState } from "../lib/task-stream";
import {
  diagnoseStructuredFragment,
  type TaskStreamTraceNode,
  type TaskStreamTracePresentation,
  type TaskStreamTraceStatus,
} from "../lib/task-stream-trace";
import { useStreamFollow } from "../lib/use-stream-follow";
import { StreamContent } from "./StreamContent";

type TraceFilter = "all" | "running" | "attention" | "unverified";

const STATUS_LABEL: Readonly<Record<TaskStreamTraceStatus, string>> = {
  completed: "已完成",
  running: "运行中",
  attention: "需处理",
  unverified: "待核验",
  superseded: "已重试",
};

function formatNumber(value: number): string {
  return new Intl.NumberFormat("zh-CN").format(value);
}

function formatDuration(value: number | undefined): string {
  if (value === undefined) return "—";
  if (value < 60_000) return `${(value / 1_000).toFixed(1)}s`;
  return `${Math.floor(value / 60_000)}m ${Math.round((value % 60_000) / 1_000)}s`;
}

function TraceStatusIcon({ status }: { readonly status: TaskStreamTraceStatus }) {
  if (status === "unverified" || status === "superseded") return <span aria-hidden="true">○</span>;
  if (status === "completed") {
    return (
      <svg aria-hidden="true" fill="none" height="16" viewBox="0 0 16 16" width="16">
        <circle cx="8" cy="8" r="6.25" stroke="currentColor" strokeWidth="1.5" />
        <path d="m5.2 8.1 1.75 1.75 3.9-4" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="1.5" />
      </svg>
    );
  }
  if (status === "attention") {
    return (
      <svg aria-hidden="true" fill="none" height="16" viewBox="0 0 16 16" width="16">
        <circle cx="8" cy="8" r="6.25" fill="currentColor" />
        <path d="M8 4.6v4.1M8 11.35v.05" stroke="white" strokeLinecap="round" strokeWidth="1.5" />
      </svg>
    );
  }
  return (
    <svg aria-hidden="true" className="task-trace-spinner" fill="none" height="16" viewBox="0 0 16 16" width="16">
      <circle cx="8" cy="8" r="6.25" stroke="currentColor" strokeDasharray="3 2" strokeWidth="1.5" />
    </svg>
  );
}

function HealthRail({ nodes }: { readonly nodes: readonly TaskStreamTraceNode[] }) {
  return (
    <div aria-label="批次健康分布" className="task-trace-health-rail" role="img">
      {nodes.map((node) => <i className={`is-${node.status}`} key={node.streamId} />)}
    </div>
  );
}

function TraceHealthHeader({
  presentation,
  stream,
}: {
  readonly presentation: TaskStreamTracePresentation;
  readonly stream: TaskStreamState;
}) {
  const callsTotal = (stream.calls ?? []).reduce((total, call) => total + (call.totalTokens ?? 0), 0);
  const totalTokens = callsTotal || stream.summary?.totalTokens;
  const characters = Math.max(presentation.totalCharacters, stream.summary?.outputCharacters ?? 0);
  return (
    <header className="task-trace-health">
      <div className="task-trace-health-total">
        <strong>{presentation.nodes.length}</strong>
        <span>/ {presentation.nodes.length} 子流</span>
      </div>
      <div className="task-trace-health-main">
        <HealthRail nodes={presentation.nodes} />
        <div className="task-trace-health-counts">
          <span className="is-completed"><TraceStatusIcon status="completed" />已完成 <strong>{presentation.completedCount}</strong></span>
          <span className="is-running"><TraceStatusIcon status="running" />运行中 <strong>{presentation.runningCount}</strong></span>
          <span className="is-attention"><TraceStatusIcon status="attention" />需处理 <strong>{presentation.attentionCount}</strong></span>
          {presentation.unverifiedCount > 0 && <span className="is-unverified">待核验 <strong>{presentation.unverifiedCount}</strong></span>}
          {presentation.supersededCount > 0 && <span className="is-superseded">历史重试 <strong>{presentation.supersededCount}</strong></span>}
        </div>
      </div>
      <div className="task-trace-health-facts">
        <span><strong>{formatNumber(characters)}</strong> 字符</span>
        {totalTokens !== undefined && totalTokens > 0 && <span><strong>{formatNumber(totalTokens)}</strong> Token</span>}
        <span><strong>{presentation.runningCount}</strong> 生成 / 校验中</span>
      </div>
    </header>
  );
}

function TraceRow({
  active,
  node,
  onSelect,
  stepLabel,
}: {
  readonly active: boolean;
  readonly node: TaskStreamTraceNode;
  readonly onSelect: (streamId: string) => void;
  readonly stepLabel: string;
}) {
  const typeLabel = node.structured ? "结构化输出" : "文本输出";
  return (
    <button
      aria-label={`选择子流 ${String(node.index).padStart(2, "0")} · ${STATUS_LABEL[node.status]}`}
      aria-pressed={active}
      className={`task-trace-row is-${node.status}${active ? " is-active" : ""}`}
      onClick={() => onSelect(node.streamId)}
      type="button"
    >
      <span className="task-trace-row-index">{String(node.index).padStart(2, "0")}</span>
      <span className="task-trace-row-status"><TraceStatusIcon status={node.status} /></span>
      <span className="task-trace-row-copy">
        <strong>{typeLabel}</strong>
        <small title={stepLabel}>{stepLabel}</small>
      </span>
      <span className="task-trace-row-metrics">
        <strong>{formatNumber(node.characterCount)} 字</strong>
        <small>{formatDuration(node.durationMs)}</small>
      </span>
    </button>
  );
}

function InvalidStructuredOutput({ node }: { readonly node: TaskStreamTraceNode }) {
  const diagnostic = diagnoseStructuredFragment(node.contentText);
  return (
    <section aria-label="结构化输出诊断" className="task-trace-diagnostic">
      <header>
        <TraceStatusIcon status="attention" />
        <div>
          <strong>后端校验或恢复未完成</strong>
          <p>{node.errorText ?? "任务已停止，尚未收到校验通过的结果。"}</p>
        </div>
      </header>
      <dl>
        <div>
          <dt>校验结论</dt>
          <dd>{node.textTruncated ? "当前是截取预览，不能据此判断完整 JSON" : diagnostic.expectedClosers.length > 0 ? `原始片段可能缺失 ${diagnostic.expectedClosers} 闭合符号` : "请查看后端字段/语义校验错误"}</dd>
        </div>
        <div>
          <dt>最后可识别字段</dt>
          <dd>{diagnostic.lastField ?? "尚未识别"}</dd>
        </div>
      </dl>
      <details className="task-trace-source-fold">
        <summary>查看原始片段</summary>
        <pre>{node.contentText.slice(-2_000)}</pre>
      </details>
    </section>
  );
}

function RecoveryPath({ node }: { readonly node: TaskStreamTraceNode }) {
  const verified = node.validationStatus === "validated";
  const source = node.repairSource === "llm" ? "云端定点修复" : node.repairSource === "local" ? "本地格式修复" : "契约校验";
  const state = node.status === "superseded" ? "已转入后续尝试"
    : verified ? `${source}通过`
    : node.status === "attention" ? "自动恢复已停止"
    : node.validationStatus === "retrying" ? "正在重新请求模型"
    : node.validationStatus === "repairing" ? `${source}中`
    : "后端校验中";
  return (
    <section aria-label="恢复路径" className="task-trace-recovery">
      <h4>恢复路径</h4>
      <ol>
        <li className="is-complete"><span>1</span><strong>输出记录</strong><small>{node.contentText ? "已接收" : "未返回正文"}</small></li>
        <li className={verified ? "is-complete" : node.status === "running" ? "is-active" : ""}><span>2</span><strong>{state}</strong><small>{node.attempt === undefined ? "" : `第 ${node.attempt} / ${node.maxAttempts ?? "?"} 次`}</small></li>
        <li className={verified ? "is-complete" : ""}><span>3</span><strong>契约校验</strong><small>{verified ? "已通过" : "未通过 / 未完成"}</small></li>
        <li><span>4</span><strong>交给主流程</strong><small>{verified ? "持久化由主流程确认" : "未交付正式结果"}</small></li>
      </ol>
    </section>
  );
}

function TraceNodeOutput({ node }: { readonly node: TaskStreamTraceNode }) {
  if (node.status === "attention" && node.structured) {
    return (
      <>
        <InvalidStructuredOutput node={node} />
        <RecoveryPath node={node} />
      </>
    );
  }
  if (node.errorText !== undefined && node.status === "attention") {
    return <p className="task-trace-node-error" role="alert">{node.errorText}</p>;
  }
  if (node.status === "unverified" || node.status === "superseded" || node.textTruncated) {
    return <div className="task-trace-node-content">
      {node.validationStatus !== undefined && <RecoveryPath node={node} />}
      <p>{node.status === "superseded" ? "这是已被后续尝试替代的历史输出，不计为待处理故障。"
        : node.validationStatus === "validated" ? "后端已校验通过。当前仅显示截取预览，不能用预览判断 JSON 完整性。"
        : "尚无与该子流对应的后端校验结论。历史片段或回放缺失不等于模型输出失败。"}</p>
      <details className="task-trace-source-fold"><summary>查看输出预览</summary><pre>{node.contentText}</pre></details>
    </div>;
  }
  if (node.contentItems.length === 0) {
    return <p className="task-trace-node-empty">{node.status === "running" ? "正在等待第一段输出。" : "子流已结束，未返回正文。"}</p>;
  }
  return (
    <div className="task-trace-node-content">
      {node.validationStatus !== undefined && <RecoveryPath node={node} />}
      {node.contentItems.map((item) => (
        <StreamContent
          key={item.key}
          {...(node.outputKind === undefined ? {} : { outputKind: node.outputKind })}
          live={node.status === "running"}
          settled={node.status !== "running"}
          text={item.text}
          textLength={node.characterCount}
          textTruncated={node.textTruncated}
          {...(node.validationStatus === undefined
            ? {}
            : { validationStatus: node.validationStatus })}
        />
      ))}
      {node.validationStatus === "validated" && node.rawContentText.length > 0 && node.rawContentText !== node.contentText && (
        <details className="task-trace-source-fold"><summary>查看修复前输出预览</summary><pre>{node.rawContentText}</pre></details>
      )}
    </div>
  );
}

function TraceInspector({
  node,
}: {
  readonly node: TaskStreamTraceNode;
}) {
  const followKey = `${node.streamId}:${node.characterCount}:${node.contentItems.length}`;
  const {
    containerRef,
    sentinelRef,
    followingLatest,
    pauseFollow,
    scrollToLatest,
  } = useStreamFollow(followKey, node.streamId);
  const model = node.modelId;
  const metadata = [
    model,
    node.attempt === undefined ? undefined : `第 ${node.attempt} 次`,
    node.finishReason === undefined ? undefined : `结束原因：${node.finishReason}`,
    formatDuration(node.durationMs),
    `${formatNumber(node.characterCount)} 字符`,
  ].filter((value): value is string => value !== undefined);
  return (
    <section aria-label={`子流 ${String(node.index).padStart(2, "0")} 检查器`} className={`task-trace-inspector is-${node.status}`}>
      <header className="task-trace-inspector-header">
        <div>
          <h3>子流 {String(node.index).padStart(2, "0")} · {node.structured ? "结构化输出" : "文本输出"}</h3>
          <p>{metadata.join(" · ")}</p>
        </div>
        <div className="task-trace-inspector-actions">
          {node.status === "running" && (
            <button
              aria-label={followingLatest ? "暂停自动跟随" : "继续跟随最新输出"}
              aria-pressed={followingLatest}
              className={`task-trace-follow-toggle${followingLatest ? " is-following" : ""}`}
              onClick={followingLatest ? pauseFollow : scrollToLatest}
              type="button"
            >
              <span aria-hidden="true" />
              {followingLatest ? "自动跟随" : "继续跟随"}
            </button>
          )}
          <span className={`task-trace-status is-${node.status}`}><TraceStatusIcon status={node.status} />{STATUS_LABEL[node.status]}</span>
        </div>
      </header>
      <div className="task-trace-inspector-body" ref={containerRef}>
        <TraceNodeOutput node={node} />
        {node.reasoningItems.length > 0 && (
          <details className="task-trace-reasoning">
            <summary>思考过程 · {node.reasoningItems.reduce((total, item) => total + item.characterCount, 0)} 字</summary>
            {node.reasoningItems.map((item) => <p key={item.key}>{item.text}</p>)}
          </details>
        )}
        <div aria-hidden="true" className="task-stream-sentinel" ref={sentinelRef} />
      </div>
    </section>
  );
}

function LiveStrip({ nodes }: { readonly nodes: readonly TaskStreamTraceNode[] }) {
  if (nodes.length === 0) return null;
  return (
    <aside aria-label="正在生成的子流" className="task-trace-live-strip">
      <strong>仍有 {nodes.length} 个子流生成或校验中</strong>
      <div>
        {nodes.slice(0, 4).map((node) => (
          <span key={node.streamId}>
            <TraceStatusIcon status="running" />
            子流 {String(node.index).padStart(2, "0")} · 已接收 {formatNumber(node.characterCount)} 字
          </span>
        ))}
      </div>
    </aside>
  );
}

export function TaskStreamTraceBrowser({
  presentation,
  stepLabel,
  stream,
}: {
  readonly presentation: TaskStreamTracePresentation;
  readonly stepLabel: string;
  readonly stream: TaskStreamState;
}) {
  const [filter, setFilter] = useState<TraceFilter>("all");
  const [selectedStreamId, setSelectedStreamId] = useState<string | null>(null);
  const filteredNodes = presentation.nodes.filter((node) => filter === "all" || node.status === filter);
  const activeNode = filteredNodes.find((node) => node.streamId === selectedStreamId)
    ?? filteredNodes.find((node) => node.status === "attention")
    ?? [...filteredNodes].reverse().find((node) => node.status === "running")
    ?? filteredNodes.at(-1);
  const filterItems: readonly { readonly id: TraceFilter; readonly label: string; readonly count: number }[] = [
    { id: "all", label: "全部", count: presentation.nodes.length },
    { id: "running", label: "运行中", count: presentation.runningCount },
    { id: "attention", label: "需处理", count: presentation.attentionCount },
    { id: "unverified", label: "待核验", count: presentation.unverifiedCount },
  ];

  return (
    <section aria-label="批次运行轨迹" className="task-stream-trace-browser">
      <TraceHealthHeader presentation={presentation} stream={stream} />
      <div className="task-stream-trace-workbench">
        <aside aria-label="批次轨迹" className="task-trace-list">
          <header>
            <strong>批次轨迹</strong>
            <div aria-label="轨迹筛选" role="group">
              {filterItems.map((item) => (
                <button
                  aria-pressed={filter === item.id}
                  key={item.id}
                  onClick={() => setFilter(item.id)}
                  type="button"
                >
                  {item.label} <strong>{item.count}</strong>
                </button>
              ))}
            </div>
          </header>
          <div className="task-trace-rows">
            {filteredNodes.length === 0 ? (
              <p>当前筛选下没有子流。</p>
            ) : filteredNodes.map((node) => (
              <TraceRow
                active={activeNode?.streamId === node.streamId}
                key={node.streamId}
                node={node}
                onSelect={setSelectedStreamId}
                stepLabel={stepLabel}
              />
            ))}
          </div>
        </aside>
        <div className="task-trace-detail-column">
          {activeNode === undefined
            ? <p className="task-trace-node-empty">选择一个子流查看运行详情。</p>
            : <TraceInspector node={activeNode} />}
          <LiveStrip nodes={presentation.nodes.filter((node) => node.status === "running" && node.streamId !== activeNode?.streamId)} />
        </div>
      </div>
    </section>
  );
}
