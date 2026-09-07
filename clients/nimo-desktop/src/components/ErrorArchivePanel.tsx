/**
 * Task-flow error archive panel.
 *
 * Replicates PySide6 Settings error archive section: shows summary
 * (entry count, project count, latest time) and supports clearing.
 */
import { useCallback, useEffect, useState } from "react";

import type { EngineClient, EngineCommandClient, ErrorArchiveSummaryView } from "@nimo/engine-contracts";

interface ErrorArchivePanelProps {
  readonly commandClient: Pick<EngineCommandClient, "clearErrorArchive">;
  readonly summaryClient: Pick<EngineClient, "getErrorArchiveSummary">;
}

/**
 * Error archive observer/control. The component owns only rendering state;
 * the aggregate and destructive clear operation remain Engine commands.
 */
export function ErrorArchivePanel({ commandClient, summaryClient }: ErrorArchivePanelProps) {
  const [summary, setSummary] = useState<ErrorArchiveSummaryView | null>(null);
  const [loading, setLoading] = useState(false);
  const [clearing, setClearing] = useState(false);
  const [message, setMessage] = useState("");

  const fetchSummary = useCallback(async () => {
    setLoading(true);
    setMessage("");
    try {
      setSummary(await summaryClient.getErrorArchiveSummary());
    } catch {
      setMessage("无法读取错误档案；请确认 Engine 正在运行。");
    } finally {
      setLoading(false);
    }
  }, [summaryClient]);

  useEffect(() => {
    void fetchSummary();
  }, [fetchSummary]);

  const handleClear = useCallback(async () => {
    if (!window.confirm("确定清空所有项目的任务错误档案？此操作不可撤销。")) return;
    setClearing(true);
    setMessage("");
    try {
      const result = await commandClient.clearErrorArchive({ kind: "clear_error_archive" });
      setMessage(result.message);
      await fetchSummary();
    } catch {
      setMessage("清空失败；请检查引擎连接。");
    } finally {
      setClearing(false);
    }
  }, [commandClient, fetchSummary]);

  const latestLabel = summary?.latestTime
    ? new Date(summary.latestTime).toLocaleString("zh-CN", {
        month: "2-digit",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
      })
    : "无记录";

  return (
    <div className="error-archive-panel">
      <div className="error-archive-header">
        <strong>任务错误档案</strong>
        <button
          className="button button-secondary"
          disabled={loading}
          onClick={() => void fetchSummary()}
          type="button"
        >
          {loading ? "读取中…" : "刷新"}
        </button>
      </div>
      <p className="error-archive-description">
        死信队列保留失败任务的诊断信息，便于回溯。超过保留期限的记录会自动清理。
      </p>
      {summary !== null && (
        <div className="error-archive-metrics">
          <span><strong>{summary.entryCount}</strong> 条记录</span>
          <span><strong>{summary.projectCount}</strong> 个项目</span>
          <span>最近：{latestLabel}</span>
        </div>
      )}
      {message && <p className="error-archive-message" role="status">{message}</p>}
      <div className="error-archive-actions">
        <button
          className="button button-danger"
          disabled={clearing || !summary || summary.entryCount === 0}
          onClick={() => void handleClear()}
          type="button"
        >
          {clearing ? "清空中…" : "清空全部档案"}
        </button>
      </div>
    </div>
  );
}
