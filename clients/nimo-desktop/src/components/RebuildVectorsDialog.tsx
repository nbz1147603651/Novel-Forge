import { useState } from "react";

import type { ChapterCommandResult, EngineCommandClient } from "@nimo/engine-contracts";

import { AppDialog } from "./AppDialog";

export interface RebuildVectorsDialogProps {
  readonly commandClient?: EngineCommandClient | undefined;
  readonly onClose: () => void;
  readonly onSubmitted?: (() => Promise<unknown> | void) | undefined;
  readonly projectId: string;
}

/**
 * Mirrors DashboardPage._request_rebuild_vectors_for_selected.  The first
 * state is deliberately a confirmation rather than a configuration wizard:
 * rebuilding has a fixed source contract and must not imply that prose,
 * outlines, or project artifacts can be changed by this action.
 */
export function RebuildVectorsDialog({
  commandClient,
  onClose,
  onSubmitted,
  projectId,
}: RebuildVectorsDialogProps) {
  const [result, setResult] = useState<ChapterCommandResult | null>(null);
  const [submitting, setSubmitting] = useState(false);

  if (result !== null) {
    return <AppDialog confirmLabel="关闭" description={result.message} onClose={onClose} title={result.status === "accepted" ? "向量索引重建任务已提交" : "向量索引重建未提交"}>
      <div className="vector-rebuild-result" role="status">
        <strong>{result.status === "accepted" ? "已登记重建范围" : "任务状态未改变"}</strong>
        <span>zvec 记忆索引</span>
        <span>表达通道语义索引</span>
        {result.taskId !== undefined && <p>任务编号：{result.taskId}</p>}
        <p>任务由 Engine 持久化执行，可在任务流中观察、取消或恢复。</p>
      </div>
    </AppDialog>;
  }

  const submit = async () => {
    if (commandClient === undefined) {
      setResult({ status: "rejected", message: "当前客户端未连接 Engine，不能提交向量重建任务。" });
      return;
    }
    setSubmitting(true);
    try {
      const next = await commandClient.rebuildMemoryVectors({
        kind: "rebuild_memory_vectors",
        projectId,
        includeExpression: true,
      });
      setResult(next);
      if (next.status === "accepted") await onSubmitted?.();
    } catch (error) {
      setResult({
        status: "rejected",
        message: error instanceof Error ? error.message : "向量重建命令提交失败。",
      });
    } finally {
      setSubmitting(false);
    }
  };

  return <AppDialog closeOnConfirm={false} confirmDisabled={submitting} confirmLabel={submitting ? "提交中…" : "开始重建"} description={`将为项目「${projectId}」重建记忆向量索引。`} onClose={onClose} onConfirm={() => void submit()} title="重建向量索引？">
    <div className="vector-rebuild-notice">
      <strong>重建范围</strong>
      <p>该操作只重建 zvec 记忆索引和表达通道语义索引，不会改动正文、章节大纲或初始化产物。</p>
      <p>建议在没有章节生成或审计任务运行时执行。</p>
    </div>
  </AppDialog>;
}
