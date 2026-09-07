import { useEffect, useState } from "react";

import type { EngineCommandClient } from "@nimo/engine-contracts";

import { AppDialog } from "../AppDialog";
import { useTaskStream, type TaskStreamClient } from "../../lib/use-task-stream";

interface AudiobookExportDialogProps {
  readonly commandClient: EngineCommandClient;
  readonly onClose: () => void;
  readonly projectId: string;
  readonly streamClient: TaskStreamClient;
}

/**
 * Finished audiobook delivery package export.  The backend applies the
 * sequential chapter gate: chapters that are not assembled and
 * delivery-confirmed come back as a rejection message ("第 N 章确认后继续"),
 * so this dialog stays open and surfaces the exact gap to the author.
 */
export function AudiobookExportDialog({
  commandClient,
  onClose,
  projectId,
  streamClient,
}: AudiobookExportDialogProps) {
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const [downloadUrl, setDownloadUrl] = useState("");
  const [taskId, setTaskId] = useState<string | null>(null);
  const taskStream = useTaskStream(streamClient, taskId);

  useEffect(() => {
    if (taskId === null || taskStream === null || taskStream.taskId !== taskId) return;
    if (taskStream.status === "completed") {
      setTaskId(null);
      setBusy(false);
      setDownloadUrl(taskStream.delivery?.downloadUrl ?? "");
      setNotice(
        taskStream.delivery === undefined
          ? "有声书包任务已完成，但未找到可下载的交付文件。"
          : "有声书包已导出，可下载交付包。",
      );
    } else if (taskStream.status === "failed") {
      setTaskId(null);
      setBusy(false);
      setNotice(taskStream.error?.message ?? "有声书包导出失败；请检查章节交付门禁。");
    }
  }, [taskId, taskStream?.delivery, taskStream?.error?.message, taskStream?.status, taskStream?.taskId]);

  const handleExport = async () => {
    if (busy) return;
    setBusy(true);
    setNotice("正在导出有声书包…");
    setDownloadUrl("");
    let durableTaskSubmitted = false;
    try {
      const result = await commandClient.exportAudiobook({
        kind: "export_audiobook",
        projectId,
      });
      setNotice(result.message);
      if (result.status === "accepted" && result.taskId) {
        setTaskId(result.taskId);
        durableTaskSubmitted = true;
        return;
      }
      setDownloadUrl(result.downloadUrl ?? "");
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "有声书包导出失败");
    } finally {
      if (!durableTaskSubmitted) setBusy(false);
    }
  };

  const handleCancel = async () => {
    if (taskId === null) return;
    try {
      const result = await commandClient.cancelJob({
        kind: "cancel_job",
        taskId,
        reason: "用户取消有声书包导出",
      });
      if (result.status === "accepted") {
        setTaskId(null);
        setBusy(false);
        setNotice("有声书包导出已取消；未发布部分交付包。");
      }
    } catch {
      setNotice("取消有声书包导出失败；请稍候在任务中心重试。");
    }
  };

  return (
    <AppDialog
      closeOnConfirm={false}
      confirmDisabled={busy}
      confirmLabel={busy ? "导出中…" : "导出有声书包"}
      description="成品交付包：分章 MP3 + 目录/封面元数据 + 段级汇编报告。复用已装配的成品母带，不重新渲染；导出按章节顺序门控，未确认章节需先确认。"
      onClose={onClose}
      onConfirm={() => void handleExport()}
      title="导出有声书包"
    >
      <p>将收集全部已装配并通过交付门禁的章节；汇编报告含段号与时间戳，可直接定位需要重录的片段。</p>
      {notice.length > 0 && <p role="status">{notice}</p>}
      {downloadUrl.length > 0 && (
        <a className="button button-secondary" download href={downloadUrl}>
          下载交付包
        </a>
      )}
      {taskId !== null && (
        <button className="button button-secondary" onClick={() => void handleCancel()} type="button">
          取消导出
        </button>
      )}
    </AppDialog>
  );
}
