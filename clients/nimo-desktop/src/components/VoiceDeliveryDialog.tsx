import { useEffect, useState } from "react";

import type { EngineCommandClient, ExportAudioCommand } from "@nimo/engine-contracts";

import { OperationProgress, type OperationState } from "./OperationProgress";
import { OverlaySurface } from "./OverlaySurface";
import { useTaskStream, type TaskStreamClient } from "../lib/use-task-stream";

export type VoiceDeliveryKind = "assemble" | "export";

export type ExportScope = "chapter" | "book";
export type ChapterExportFormat = "mp3" | "wav" | "flac" | "srt";

const operationCopy: Readonly<Record<VoiceDeliveryKind, { readonly completed: string; readonly running: string; readonly title: string; readonly steps: readonly { readonly id: string; readonly label: string }[] }>> = {
  assemble: {
    title: "装配章节音频",
    running: "正在用已接受分段混合章节主音轨",
    completed: "章节音频已重新装配",
    steps: [{ id: "takes", label: "核对已接受分段" }, { id: "mix", label: "混合章节主音轨" }, { id: "subtitle", label: "准备字幕对齐" }],
  },
  export: {
    title: "导出音频",
    running: "正在提交交付请求",
    completed: "导出请求已接受",
    steps: [{ id: "scope", label: "核对交付范围" }, { id: "format", label: "确认交付格式" }, { id: "ready", label: "等待 Engine 生成结果" }],
  },
};

function createExportCommand({
  chapterFormat,
  chapterNumber,
  includeSubtitles,
  normalizeLoudness,
  projectId,
  scope,
}: {
  readonly chapterFormat: ChapterExportFormat;
  readonly chapterNumber: number;
  readonly includeSubtitles: boolean;
  readonly normalizeLoudness: boolean;
  readonly projectId: string;
  readonly scope: ExportScope;
}): ExportAudioCommand {
  if (scope === "book") {
    return {
      kind: "export_audio",
      projectId,
      scope: "book",
      format: "zip",
      includeSubtitles,
    };
  }
  if (chapterFormat === "srt") {
    return {
      kind: "export_audio",
      projectId,
      scope: "chapter",
      chapterNumber,
      format: "srt",
    };
  }
  return {
    kind: "export_audio",
    projectId,
    scope: "chapter",
    chapterNumber,
    format: chapterFormat,
    ...(normalizeLoudness && chapterFormat === "mp3" ? { targetLufs: -14 } : {}),
  };
}

function exportRequestLabel(scope: ExportScope, chapterFormat: ChapterExportFormat): string {
  if (scope === "book") return "创建 ZIP 全书导出请求";
  if (chapterFormat === "srt") return "创建 SRT 字幕导出请求";
  return `创建 ${chapterFormat.toUpperCase()} 导出请求`;
}

export function VoiceDeliveryDialog({
  chapterNumber,
  commandClient,
  initialFormat = "mp3",
  initialIncludeSubtitles = false,
  initialNormalizeLoudness = false,
  initialScope = "chapter",
  kind,
  onClose,
  projectId,
  streamClient,
}: {
  readonly chapterNumber: number;
  readonly commandClient: EngineCommandClient;
  readonly initialFormat?: ChapterExportFormat;
  readonly initialIncludeSubtitles?: boolean;
  readonly initialNormalizeLoudness?: boolean;
  readonly initialScope?: ExportScope;
  readonly kind: VoiceDeliveryKind;
  readonly onClose: () => void;
  readonly projectId: string;
  readonly streamClient: TaskStreamClient;
}) {
  const [state, setState] = useState<OperationState>("configuration");
  const [scope, setScope] = useState<ExportScope>(initialScope);
  const [chapterFormat, setChapterFormat] = useState<ChapterExportFormat>(initialFormat);
  const [includeSubtitles, setIncludeSubtitles] = useState(initialIncludeSubtitles);
  const [normalizeLoudness, setNormalizeLoudness] = useState(initialNormalizeLoudness);
  const [resultMessage, setResultMessage] = useState("");
  const [downloadUrl, setDownloadUrl] = useState("");
  const [taskId, setTaskId] = useState<string | null>(null);
  const taskStream = useTaskStream(streamClient, taskId);
  const copy = operationCopy[kind];

  useEffect(() => {
    if (taskId === null || taskStream === null || taskStream.taskId !== taskId) return;
    if (taskStream.status === "completed") {
      setTaskId(null);
      setDownloadUrl(taskStream.delivery?.downloadUrl ?? "");
      setResultMessage(
        taskStream.delivery === undefined
          ? "导出任务已完成，但未找到可下载的交付文件。"
          : "导出已完成，可下载交付文件。",
      );
      setState("completed");
    } else if (taskStream.status === "failed") {
      setTaskId(null);
      setResultMessage(taskStream.error?.message ?? "导出任务失败；请检查交付门禁后重试。");
      setState("failed");
    }
  }, [taskId, taskStream?.delivery, taskStream?.error?.message, taskStream?.status, taskStream?.taskId]);

  const handleConfirm = async () => {
    if (kind === "export") {
      setState("running");
      setResultMessage("");
      setDownloadUrl("");
      try {
        const result = await commandClient.exportAudio(createExportCommand({
          chapterFormat,
          chapterNumber,
          includeSubtitles,
          normalizeLoudness,
          projectId,
          scope,
        }));
        setResultMessage(result.message);
        if (result.status !== "accepted") {
          setState("failed");
        } else if (result.taskId) {
          setTaskId(result.taskId);
        } else {
          // Transitional Engine compatibility: older sidecars may still
          // perform the export synchronously and return a direct link.
          setDownloadUrl(result.downloadUrl ?? "");
          setState("completed");
        }
      } catch {
        setState("failed");
        setResultMessage("导出命令失败；请检查引擎连接后重试。");
      }
      return;
    }
    setState("running");
    setResultMessage("");
    try {
      const result = await commandClient.reassembleVoice({
        kind: "reassemble_voice",
        projectId,
        chapterNumber,
      });
      setResultMessage(result.message);
      setState(result.status === "accepted" ? "completed" : "failed");
    } catch {
      setState("failed");
      setResultMessage("章节重装配失败；既有正式音轨保持不变。");
    }
  };

  const handleCancel = async () => {
    if (taskId === null) return;
    try {
      const result = await commandClient.cancelJob({
        kind: "cancel_job",
        taskId,
        reason: "用户取消音频导出",
      });
      if (result.status === "accepted") {
        setTaskId(null);
        setState("failed");
        setResultMessage("音频导出已取消；未发布部分交付文件。");
      }
    } catch {
      setResultMessage("取消导出任务失败；请稍候在任务中心重试。");
    }
  };

  return (
    <OverlaySurface ariaLabel={copy.title} onClose={onClose}>
      <section className="voice-delivery-dialog">
        <header>
          <h2>{copy.title}</h2>
          <p>{kind === "assemble"
            ? `将第 ${chapterNumber} 章已采纳的片段装配为新的正式版本，并刷新字幕、混音清单与交付状态。`
            : "选择交付范围与格式；导出任务由 Engine 持久化并可在任务中心恢复。"}</p>
        </header>
        {state === "configuration" && (
          <div className="voice-delivery-config">
            {kind === "assemble" ? (
              <div className="voice-delivery-checks">
                <span>✓ 已接受片段会被纳入装配</span>
                <span>✓ 旧音轨和字幕保留为只读回滚锚点</span>
                <span>✓ Engine 将重算混音、响度、字幕与交付就绪状态</span>
              </div>
            ) : (
              <>
                <label>
                  <span>导出范围</span>
                  <select aria-label="音频导出范围" onChange={(event) => setScope(event.target.value as ExportScope)} value={scope}>
                    <option value="chapter">第 {chapterNumber} 章</option>
                    <option value="book">当前作品全部已交付章节</option>
                  </select>
                </label>
                {scope === "chapter" ? (
                  <>
                    <label>
                      <span>交付格式</span>
                      <select aria-label="音频导出格式" onChange={(event) => setChapterFormat(event.target.value as ChapterExportFormat)} value={chapterFormat}>
                        <option value="mp3">MP3 · 章节音频</option>
                        <option value="wav">WAV · 无压缩音频</option>
                        <option value="flac">FLAC · 无损音频</option>
                        <option value="srt">SRT · 字幕文件</option>
                      </select>
                    </label>
                    {chapterFormat === "mp3" && (
                      <label className="voice-delivery-option">
                        <input checked={normalizeLoudness} onChange={(event) => setNormalizeLoudness(event.target.checked)} type="checkbox" />
                        <span>标准化到 -14 LUFS（适合成品交付）</span>
                      </label>
                    )}
                  </>
                ) : (
                  <label className="voice-delivery-option">
                    <input checked={includeSubtitles} onChange={(event) => setIncludeSubtitles(event.target.checked)} type="checkbox" />
                    <span>ZIP 同时包含 SRT 字幕</span>
                  </label>
                )}
              </>
            )}
          </div>
        )}
        {state !== "configuration" && <OperationProgress completedLabel={copy.completed} runningLabel={copy.running} state={state} steps={copy.steps} title={copy.title} />}
        {(state === "completed" || state === "failed") && resultMessage && <p className="voice-delivery-result" role="status">{resultMessage}</p>}
        {state === "completed" && downloadUrl && (
          <a className="button button-primary voice-delivery-download" download href={downloadUrl}>
            下载导出文件
          </a>
        )}
        <footer>
          {state === "configuration" && <button className="button button-secondary" onClick={onClose} type="button">取消</button>}
          {state === "completed" && <button className="button button-primary" onClick={onClose} type="button">完成</button>}
          {state === "failed" && <button className="button button-primary" onClick={handleConfirm} type="button">重试</button>}
          {state === "running" && taskId !== null && <button className="button button-secondary" onClick={() => void handleCancel()} type="button">取消导出</button>}
          {state === "configuration" && <button className="button button-primary" onClick={handleConfirm} type="button">{kind === "assemble" ? "开始装配" : exportRequestLabel(scope, chapterFormat)}</button>}
        </footer>
      </section>
    </OverlaySurface>
  );
}
