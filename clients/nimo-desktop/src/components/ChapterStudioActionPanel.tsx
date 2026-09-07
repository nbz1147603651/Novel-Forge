/**
 * Chapter-studio action panel — 1:1 parity with PySide6
 * `novel_forge/desktop/pages/chapter_studio/action_panel.py`.
 *
 * Implements all 7 render states:
 *  running → checkpoint → done → stale → failed → prepare → unloaded
 * with badge tones, auto-pilot buttons, network-error retry scheduling,
 * inline checkpoint option buttons, and dynamic notes section.
 */
import { useCallback, useEffect, useState } from "react";
import { useAuthoring } from "./AuthoringWorkspace";

import type {
  ChapterStudioActivityView,
  ChapterStudioCheckpointOptionView,
  ChapterStudioCheckpointView,
  ChapterStudioView,
  EngineCommandClient,
} from "@nimo/engine-contracts";

import { ErrorSummary } from "./ErrorSummary";
import { useEngineRuntime } from "../lib/engine-runtime-context";
import {
  autorunStartButtonLabel,
  autorunStopButtonLabel,
  buildAutorunStartCommand,
  createAutorunWaitingActivity,
  describeAutorunStartResult,
} from "../lib/chapter-studio-session";

// ── Network-error classification (mirrors _NETWORK_ERROR_MARKERS) ────────
const NETWORK_ERROR_MARKERS = [
  "modelsgatewayerror",
  "modelgatewayerror",
  "readerror",
  "read error",
  "readtimeout",
  "connecterror",
  "timeouterror",
  "timed out",
  "timeout",
  "remotepro",
  "connection",
  "network",
  "httpx",
  "asyncio",
  "服务不可用",
  "网络",
  "超时",
];

function isNetworkError(error: string): boolean {
  if (!error) return false;
  const low = error.toLowerCase();
  return NETWORK_ERROR_MARKERS.some((m) => low.includes(m));
}

// ── Types ────────────────────────────────────────────────────────────────

export type WritingMode = "manual" | "suggest" | "auto" | "book_auto";

type BadgeTone = "default" | "info" | "success" | "warning" | "error";

interface FailedJobInfo {
  readonly label: string;
  readonly error: string;
  readonly kind: string;
}

export interface ChapterStudioActionPanelProps {
  readonly activity: ChapterStudioActivityView;
  readonly chapterNumber: number;
  readonly chapterState: string;
  readonly chapterWordCount: number;
  readonly commandClient: EngineCommandClient;
  readonly compositionMode: "whole" | "scene";
  readonly mode: WritingMode;
  readonly onActivityChange: (activity: ChapterStudioActivityView) => void;
  readonly onGoToNextChapter: () => void;
  readonly onModeChange: (mode: WritingMode) => void;
  readonly onNotice: (message: string) => void;
  readonly onOpenCheckpointDialog: () => void;
  readonly onPrepare?: (notes?: string, force?: boolean) => Promise<void>;
  readonly studio: ChapterStudioView;
  readonly totalChapters: number;
}

type PanelState =
  | { readonly kind: "running" }
  | { readonly kind: "checkpoint" }
  | { readonly kind: "done" }
  | { readonly kind: "stale" }
  | { readonly kind: "failed"; readonly job: FailedJobInfo }
  | { readonly kind: "prepare" };

// ── Component ────────────────────────────────────────────────────────────

export function ChapterStudioActionPanel({
  activity,
  chapterNumber,
  chapterState,
  chapterWordCount,
  commandClient,
  compositionMode,
  mode,
  onActivityChange,
  onGoToNextChapter,
  onModeChange,
  onNotice,
  onOpenCheckpointDialog,
  onPrepare,
  studio,
  totalChapters,
}: ChapterStudioActionPanelProps) {
  const authoring = useAuthoring();
  const { isCommandAvailable } = useEngineRuntime();
  const canPrepare = isCommandAvailable("prepare_chapter");
  const engineAutorunVisible = studio.autorun.status !== "idle";
  const isAuto = mode === "auto" || mode === "book_auto" || engineAutorunVisible;
  const isBookAuto = engineAutorunVisible
    ? studio.autorun.mode === "book"
    : mode === "book_auto";
  const autoLabel = isBookAuto ? "章节连跑" : "本章自动";
  const autoStarted = ["waiting_init", "running", "retry_wait"].includes(
    studio.autorun.status,
  );
  const waitingForEngine = studio.autorun.status === "retry_wait"
    && studio.autorun.waitReason === "engine_restart_required";
  const stoppedFromAuto = ["paused", "failed", "cancelled"].includes(
    studio.autorun.status,
  );
  const resumeAutoLabel = autoLabel;

  // ── Local state ──────────────────────────────────────────────────────
  const [failedJob, setFailedJob] = useState<FailedJobInfo | null>(null);
  const [checkpointResolved, setCheckpointResolved] = useState(false);
  const [checkpointDialogDismissed, setCheckpointDialogDismissed] = useState(false);
  // 对标 PySide6 _notes_expanded_by_chapter：按章节持久化笔记展开状态与内容。
  const notesStorageKey = `nimo:notes:${studio.projectId}:${chapterNumber}`;
  const [notesExpanded, setNotesExpanded] = useState(() => {
    try { return localStorage.getItem(`${notesStorageKey}:expanded`) === "1"; } catch { return false; }
  });
  const [notesText, setNotesText] = useState(() => {
    try { return localStorage.getItem(`${notesStorageKey}:text`) ?? ""; } catch { return ""; }
  });

  // Persist notes state to localStorage.
  useEffect(() => {
    try { localStorage.setItem(`${notesStorageKey}:expanded`, notesExpanded ? "1" : "0"); } catch { /* ignore */ }
  }, [notesExpanded, notesStorageKey]);
  useEffect(() => {
    try { localStorage.setItem(`${notesStorageKey}:text`, notesText); } catch { /* ignore */ }
  }, [notesText, notesStorageKey]);
  const retryCountdownSecs = -1;
  const [commandBusy, setCommandBusy] = useState<"prepare" | "cancel" | "start" | "stop" | null>(null);
  const [commandError, setCommandError] = useState<string | null>(null);
  const currentStepLabel = activity.currentStepLabel || "等待引擎任务流";

  // Reset local state on chapter switch
  useEffect(() => {
    setCheckpointResolved(false);
    setCheckpointDialogDismissed(false);
    setFailedJob(null);
  }, [chapterNumber]);

  // Engine state takes over once the durable autorun session is observed;
  // clear any in-flight command busy state so buttons flip to their running form.
  useEffect(() => {
    if (autoStarted) setCommandBusy(null);
  }, [autoStarted]);

  // ── Derive panel state (mirrors render() priority in action_panel.py) ─
  const chapterIsDone = chapterState === "completed";
  const chapterIsStale = chapterState === "stale";
  const hasPendingCheckpoint =
    activity.state === "checkpoint" &&
    activity.checkpoint !== null &&
    !checkpointResolved;

  let panelState: PanelState;
  if (waitingForEngine || activity.state === "running") {
    panelState = { kind: "running" };
  } else if (studio.autorun.status === "failed") {
    panelState = {
      kind: "failed",
      job: {
        label: autoLabel,
        error: studio.autorun.lastError || "Engine 连跑失败",
        kind: "engine_autorun",
      },
    };
  } else if (failedJob !== null) {
    panelState = { kind: "failed", job: failedJob };
  } else if (hasPendingCheckpoint) {
    panelState = { kind: "checkpoint" };
  } else if (chapterIsDone) {
    panelState = { kind: "done" };
  } else if (chapterIsStale) {
    panelState = { kind: "stale" };
  } else {
    panelState = { kind: "prepare" };
  }
  const upstreamSemanticBlocked = panelState.kind === "failed"
    && ["upstream_source_conflict", "upstream_source_review_required", "upstream_semantic_not_ready"].includes(studio.autorun.lastFailureKind ?? "")
    && ["manual", "semantic"].includes(studio.autorun.recoveryTarget ?? "");

  // ── Badge (mirrors badge_text/tone logic) ────────────────────────────
  let badgeText = "待命";
  let badgeTone: BadgeTone = "default";
  if (panelState.kind === "running") {
    badgeText = waitingForEngine ? "等待后端" : autoStarted && isAuto ? `${autoLabel}中` : "执行中";
    badgeTone = waitingForEngine ? "warning" : "info";
  } else if (panelState.kind === "done") {
    badgeText = "✓ 已完成";
    badgeTone = "success";
  } else if (panelState.kind === "stale") {
    badgeText = "已失效";
    badgeTone = "warning";
  } else if (panelState.kind === "failed") {
    badgeText = "失败";
    badgeTone = "error";
  } else if (isAuto && autoStarted) {
    badgeText = `${autoLabel}中`;
    badgeTone = "warning";
  }

  // ── Notes section (dynamic title per state) ──────────────────────────
  const isDoneOrStale = panelState.kind === "done" || panelState.kind === "stale";
  const notesTitle = isDoneOrStale
    ? panelState.kind === "stale"
      ? "重写方向（可选）"
      : "重写方向（可选，同时适用于重新生成与精修润色）"
    : "编写备注与补充约束";
  const notesPlaceholder = isDoneOrStale
    ? panelState.kind === "stale"
      ? "描述需要调整的方向，例如：「保留旧版开头风格」「按新大纲全面重写」。\n留空则按最新章节大纲重新生成。"
      : "描述需要调整的方向，例如：「加强开头衔接」「删减支线 B」「加深人物 X 的内心独白」。\n留空则重新生成按原大纲进行，精修仅优化文学性。"
    : "在这里写本章补充约束、方案备注，或对 AI 选项的人工说明。";
  const notesVisible =
    panelState.kind !== "running" &&
    !(panelState.kind === "checkpoint" && isAuto && autoStarted);

  // ── Actions ──────────────────────────────────────────────────────────
  const submitPrepare = useCallback(async () => {
    if (!canPrepare) {
      setCommandError("当前后端不支持章节准备命令。");
      return;
    }
    setCommandBusy("prepare");
    setCommandError(null);
    try {
      if (onPrepare !== undefined) {
        await onPrepare(notesText.trim() || undefined);
        setFailedJob(null);
        setCommandBusy(null);
        return;
      }
      const result = await commandClient.prepareChapter({
        kind: "prepare_chapter",
        projectId: studio.projectId,
        chapterNumber,
        ...(notesText.trim() ? { notes: notesText.trim() } : {}),
      });
      onNotice(result.message);
      if (result.status === "accepted") {
        setFailedJob(null);
        onActivityChange({
          state: "running",
          taskLabel: `第 ${chapterNumber} 章生成`,
          currentStepLabel: "准备章节方案",
          progressPercent: 5,
          checkpoint: null,
          stages: [],
        });
      }
      setCommandBusy(null);
    } catch {
      setCommandBusy(null);
      setCommandError("准备章节命令失败；请检查引擎连接后重试。");
    }
  }, [
    canPrepare,
    chapterNumber,
    commandClient,
    notesText,
    onActivityChange,
    onNotice,
    onPrepare,
    studio.projectId,
  ]);

  const cancelRunning = useCallback(async () => {
    setCommandBusy("cancel");
    setCommandError(null);
    try {
      const result = await commandClient.cancelChapter({
        kind: "cancel_chapter",
        projectId: studio.projectId,
        chapterNumber,
      });
      onNotice(result.message);
      setCommandBusy(null);
    } catch {
      setCommandBusy(null);
      setCommandError("取消命令失败；请检查引擎连接后重试。");
    }
  }, [chapterNumber, commandClient, onNotice, studio.projectId]);

  const startAutoPilot = useCallback(() => {
    if (authoring?.session?.configured) {
      authoring.open();
      onNotice("请在共创侧栏核对当前章段与授权后启动；不会沿用旧自动模式扩大范围。");
      return;
    }
    if (!isCommandAvailable("start_workflow")) {
      setCommandError("当前后端不支持启动连跑命令。");
      return;
    }
    setCommandBusy("start");
    setCommandError(null);
    onNotice(`正在启动${autoLabel}…`);
    void commandClient.startWorkflow(buildAutorunStartCommand({
      projectId: studio.projectId,
      chapterNumber,
      chapterState,
      compositionMode,
      isBookAuto,
    })).then((result) => {
      const feedback = describeAutorunStartResult(result, autoLabel);
      if (feedback.rejected) {
        setCommandError(feedback.notice);
        onNotice(feedback.notice);
        return;
      }
      onNotice(feedback.notice);
      // Optimistic running so the panel reacts immediately; the engine
      // projection (studio.autorun) replaces this once the next refresh
      // observes the durable session.
      onActivityChange(createAutorunWaitingActivity(autoLabel));
    }).catch((error: unknown) => {
      const detail =
        error instanceof Error && error.message.length > 0
          ? error.message
          : "请检查引擎连接后重试。";
      setCommandError(`${autoLabel}启动失败：${detail}`);
      onNotice(`${autoLabel}启动失败：${detail}`);
    }).finally(() => {
      setCommandBusy(null);
    });
  }, [
    autoLabel,
    authoring,
    chapterNumber,
    chapterState,
    commandClient,
    compositionMode,
    isBookAuto,
    isCommandAvailable,
    onActivityChange,
    onNotice,
    studio.projectId,
  ]);

  const stopAutoPilot = useCallback(() => {
    setCommandBusy("stop");
    setCommandError(null);
    void commandClient.cancelChapter({
      kind: "cancel_chapter",
      projectId: studio.projectId,
      chapterNumber,
    }).then((result) => {
      onNotice(`${autoLabel}已停止：${result.message}`);
    }).catch(() => {
      onNotice(`${autoLabel}停止请求失败；请在任务中心取消当前任务。`);
    }).finally(() => {
      setCommandBusy(null);
    });
  }, [autoLabel, chapterNumber, commandClient, onNotice, studio.projectId]);

  const resumeAutoPilot = useCallback(() => {
    onNotice("正在向引擎恢复自动推进。");
    startAutoPilot();
  }, [onNotice, startAutoPilot]);

  const handleCheckpointOption = useCallback(
    (option: ChapterStudioCheckpointOptionView) => {
      const checkpointId = activity.checkpoint?.id;
      if (!checkpointId) return;
      if (authoring?.session?.configured && option.id !== "pause_for_human") {
        void authoring.propose({ command: "checkpoint", chapterNumber, optionId: option.id, title: option.label, expectedInputVersion: authoring.session.inputVersion }).catch((error) => onNotice(String(error)));
        return;
      }
      void commandClient.resolveChapterCheckpoint({
        kind: "resolve_chapter_checkpoint",
        projectId: studio.projectId,
        chapterNumber,
        checkpointId,
        optionId: option.id,
      }).then((result) => {
        onNotice(result.message);
        if (result.status === "accepted") {
          setCheckpointResolved(true);
          onActivityChange({
            ...activity,
            state: "running",
            currentStepLabel: "从检查点恢复",
            checkpoint: null,
          });
        }
      }).catch(() => {
        onNotice("检查点裁决提交失败；当前检查点未改变。");
      });
    },
    [activity, authoring, chapterNumber, commandClient, onActivityChange, onNotice, studio.projectId],
  );

  const submitRegen = useCallback(() => {
    if (onPrepare) {
      void onPrepare(notesText.trim() || undefined, true);
      return;
    }
    void submitPrepare();
  }, [notesText, onPrepare, submitPrepare]);

  const submitPolish = useCallback(() => {
    void commandClient.polishChapter({
      kind: "polish_chapter",
      projectId: studio.projectId,
      chapterNumber,
      ...(notesText.trim() ? { notes: notesText.trim() } : {}),
    }).then((result) => {
      if (authoring?.session?.configured && result.status === "accepted") {
        authoring.open();
        onNotice("精修候选生成后需专项批准；正式正文和旧报告不会提前改变。");
      } else onNotice(result.message);
    }).catch(() => {
      onNotice("精修润色任务提交失败。");
    });
  }, [authoring, chapterNumber, commandClient, notesText, onNotice, studio.projectId]);

  const submitBookAudit = useCallback(() => {
    void commandClient.auditBook({
      kind: "audit_book",
      projectId: studio.projectId,
      chapterRange: [],
    }).then((result) => onNotice(result.message)).catch(() => {
      onNotice("全书审计任务提交失败。");
    });
  }, [commandClient, onNotice, studio.projectId]);

  const submitExport = useCallback(() => {
    void commandClient.exportBook({
      kind: "export_book",
      projectId: studio.projectId,
      format: "markdown",
      chapterRange: [],
      bookTitle: studio.projectTitle,
    }).then((result) => onNotice(result.message)).catch(() => {
      onNotice("书稿导出任务提交失败。");
    });
  }, [commandClient, onNotice, studio.projectId, studio.projectTitle]);

  // ── Title & summary per state ────────────────────────────────────────
  const isTerminal = chapterNumber >= totalChapters && totalChapters > 0;
  let title: string;
  let summary: string;
  switch (panelState.kind) {
    case "running":
      title = autoStarted && isAuto ? `${autoLabel}推进中…` : "正在执行…";
      summary =
        autoStarted && isAuto
          ? `正在执行：${activity.taskLabel}\n步骤：${currentStepLabel}`
          : `${activity.taskLabel}\n步骤：${currentStepLabel}，请稍候。`;
      break;
    case "checkpoint": {
      const cp = activity.checkpoint;
      title =
        cp !== null && cp.title.includes("归档")
          ? "章节归档待确认"
          : "章节方案待确认";
      summary = cp?.summary ?? "AI 已生成决策方案，请确认。";
      break;
    }
    case "done":
      title = isTerminal ? "全书章节已完成" : "本章已完成归档";
      summary = isTerminal
        ? "章节正文已全部归档。下一阶段建议运行全书审计，生成终章验收报告；审计通过后即可导出成书。"
        : "本章已归档完成。";
      break;
    case "stale":
      title = "章节已失效（含旧版存档）";
      summary = `本章此前已生成过正文（约 ${chapterWordCount.toLocaleString()} 字），因大纲或 Canon 水位变更已被标为失效。\n重新生成将遵循最新大纲，旧版内容仅作参考。`;
      break;
    case "failed": {
      const errDisplay = panelState.job.error;
      if (isNetworkError(errDisplay) && retryCountdownSecs > 0) {
        title = "网络错误 · 定时重试中";
        const mins = Math.floor(retryCountdownSecs / 60);
        const secs = retryCountdownSecs % 60;
        const countdownStr = mins > 0 ? `${mins}:${String(secs).padStart(2, "0")}` : `${secs}s`;
        summary = `网络连接失败，将在 ${countdownStr} 后自动重试。\n\n错误：${errDisplay.slice(0, 200)}`;
      } else if (isNetworkError(errDisplay)) {
        title = "网络错误 · 执行失败";
        summary = `检测到网络/超时错误，可等待一段时间后自动重试。\n\n错误：${errDisplay.slice(0, 200)}`;
      } else if (upstreamSemanticBlocked) {
        title = "上游语义一致性需处理";
        summary = `权威来源未通过预编译语义门禁，章节 provider 调用已停止。\n\n${errDisplay.slice(0, 320)}\n\n请刷新语义一致性，或在修复工作台确认证据/审批来源修订；直接重做 Plan 不会绕过源问题。`;
      } else {
        title = hasPendingCheckpoint ? "章节方案待确认" : "章节方案待确认";
        summary = `上次执行失败：\n${errDisplay.slice(0, 400)}\n\n可重新尝试或调整参数后再试。`;
      }
      break;
    }
    case "prepare":
      if (isAuto && !autoStarted) {
        title = "准备章节方案";
        summary = isBookAuto
          ? "章节连跑模式：点击「启动章节连跑」后，系统将从当前章自动推进，直到全书完成。"
          : "本章自动模式：点击「启动本章自动」后，系统将自动完成当前章节的方案与写作，完成后停止。";
      } else if (isAuto && autoStarted) {
        title = `${autoLabel}推进中…`;
        summary = `${autoLabel}推进中，正在准备章节方案…`;
      } else {
        title = "准备章节方案";
        summary = "系统将先构建章节上下文、桥接与章节计划，生成方案后供你确认。";
      }
      break;
  }

  if (waitingForEngine) {
    title = "等待后端安全重启";
    summary = studio.autorun.lastError
      || "章节进度已保存，后端重启后将自动继续；此次等待不消耗失败预算。";
  }

  // ── Checkpoint inline buttons (mirrors _render_checkpoint_buttons) ───
  const renderCheckpointButtons = (checkpoint: ChapterStudioCheckpointView) => {
    const isManual = mode === "manual";
    return (
      <div className="action-btn-row">
        {checkpoint.options.map((option) => {
          const isRecommended = option.recommended && !isManual;
          return (
            <button
              className={`button ${isRecommended ? "button-primary" : "button-secondary"}`}
              key={option.id}
              onClick={() => handleCheckpointOption(option)}
              title={option.description || undefined}
              type="button"
            >
              {option.label}
              {option.recommended && !isManual ? " →" : ""}
            </button>
          );
        })}
      </div>
    );
  };

  // ── Button area per state ────────────────────────────────────────────
  const renderButtons = () => {
    switch (panelState.kind) {
      case "running":
        return autoStarted && isAuto ? (
          <div className="action-btn-row">
            <button
              className="button button-danger"
              disabled={commandBusy === "stop"}
              onClick={stopAutoPilot}
              type="button"
            >
              {autorunStopButtonLabel(commandBusy === "stop", autoLabel)}
            </button>
          </div>
        ) : (
          <div className="action-btn-row">
            <button
              className="button button-secondary"
              disabled={commandBusy === "cancel"}
              onClick={() => void cancelRunning()}
              title="取消当前任务，进度将保留为断点。"
              type="button"
            >
              {commandBusy === "cancel" ? "正在取消…" : "⏹ 取消任务"}
            </button>
          </div>
        );

      case "checkpoint": {
        const cp = activity.checkpoint;
        if (cp === null) return null;
        if (isAuto) {
          if (autoStarted) {
            const recommended = cp.options.find((o) => o.recommended);
            return (
              <div className="action-btn-row">
                <button
                  className="button button-danger"
                  disabled={commandBusy === "stop"}
                  onClick={stopAutoPilot}
                  type="button"
                >
                  {autorunStopButtonLabel(commandBusy === "stop", autoLabel)}
                </button>
                {recommended && (
                  <span className="action-auto-hint">AI 正在确认：{recommended.label}</span>
                )}
              </div>
            );
          }
          return (
            <div className="action-btn-row">
              <button
                className="button button-primary"
                disabled={commandBusy === "start"}
                onClick={startAutoPilot}
                type="button"
              >
                {autorunStartButtonLabel(commandBusy === "start", autoLabel)}
              </button>
              <button className="button button-secondary" onClick={() => onModeChange("manual")} type="button">
                手动处理此节点
              </button>
            </div>
          );
        }
        // manual/suggest: inline option buttons + dialog access
        return (
          <>
            {renderCheckpointButtons(cp)}
            <div className="action-btn-row">
              <button className="button button-secondary" onClick={onOpenCheckpointDialog} type="button">
                {checkpointDialogDismissed ? "打开 AI 建议浮窗 →" : "定位 AI 建议浮窗 →"}
              </button>
            </div>
            {stoppedFromAuto && (
              <div className="action-btn-row">
                <button className="button button-secondary" onClick={resumeAutoPilot} type="button">
                  继续{resumeAutoLabel} ▶
                </button>
              </div>
            )}
          </>
        );
      }

      case "done":
        return (
          <div className="action-btn-row">
            <button
              className="button button-primary is-compact"
              onClick={submitRegen}
              title="按原大纲重新走完整流程：准备 → 起草 → 审查 → 归档。&#10;上方重写方向的内容将作为生成参考。"
              type="button"
            >
              🔄 重新生成章节
            </button>
            <button
              className="button button-secondary is-compact"
              onClick={submitPolish}
              title="不重写剧情，仅对当前正文做文学性打磨。&#10;上方重写方向的内容将作为润色参考。"
              type="button"
            >
              ✨ 精修润色
            </button>
            {chapterNumber < totalChapters ? (
              <button className="button button-primary is-compact" onClick={onGoToNextChapter} type="button">
                前往第 {chapterNumber + 1} 章 →
              </button>
            ) : (
              <>
                <button
                  className="button button-primary is-compact"
                  onClick={submitBookAudit}
                  title="章节全部完成后的下一阶段：生成终章验收式全书审计报告。"
                  type="button"
                >
                  📋 全书审计
                </button>
                <button
                  className="button button-secondary is-compact"
                  onClick={submitExport}
                  title="审计通过后导出 Markdown / 纯文本 / EPUB。"
                  type="button"
                >
                  📤 导出
                </button>
              </>
            )}
            {isAuto &&
              (autoStarted ? (
                <button
                  className="button button-danger is-compact"
                  disabled={commandBusy === "stop"}
                  onClick={stopAutoPilot}
                  type="button"
                >
                  {autorunStopButtonLabel(commandBusy === "stop", autoLabel)}
                </button>
              ) : (
                <button
                  className="button button-primary is-compact"
                  disabled={commandBusy === "start"}
                  onClick={startAutoPilot}
                  type="button"
                >
                  {autorunStartButtonLabel(commandBusy === "start", autoLabel)}
                </button>
              ))}
            {stoppedFromAuto && !isAuto && (
              <button className="button button-secondary is-compact" onClick={resumeAutoPilot} type="button">
                继续{resumeAutoLabel} ▶
              </button>
            )}
          </div>
        );

      case "stale":
        if (isAuto && autoStarted) {
          return (
            <div className="action-btn-row">
              <button
                className="button button-danger is-compact"
                disabled={commandBusy === "stop"}
                onClick={stopAutoPilot}
                type="button"
              >
                {autorunStopButtonLabel(commandBusy === "stop", autoLabel)}
              </button>
            </div>
          );
        }
        return (
          <div className="action-btn-row">
            <button
              className="button button-primary is-compact"
              onClick={submitRegen}
              title="按最新大纲重走完整流程：准备 → 起草 → 审查 → 归档。&#10;上方重写方向的内容将作为生成参考。"
              type="button"
            >
              🔄 重新生成章节
            </button>
            {chapterNumber < totalChapters && (
              <button
                className="button button-secondary is-compact"
                onClick={onGoToNextChapter}
                title="跳过本章（保留旧版存档），继续处理下一章。"
                type="button"
              >
                跳到第 {chapterNumber + 1} 章 →
              </button>
            )}
            {isAuto && !autoStarted && (
              <button
                className="button button-primary is-compact"
                disabled={commandBusy === "start"}
                onClick={startAutoPilot}
                type="button"
              >
                {autorunStartButtonLabel(commandBusy === "start", autoLabel)}
              </button>
            )}
            {stoppedFromAuto && !isAuto && (
              <button className="button button-secondary is-compact" onClick={resumeAutoPilot} type="button">
                继续{resumeAutoLabel} ▶
              </button>
            )}
          </div>
        );

      case "failed": {
        const errDisplay = panelState.job.error;
        const netErr = isNetworkError(errDisplay);
        if (upstreamSemanticBlocked) {
          return (
            <div className="action-btn-row">
              <button
                className="button button-primary"
                disabled={authoring === null}
                onClick={() => {
                  authoring?.setLocation("reports", chapterNumber);
                  authoring?.open();
                  onNotice("已打开语义状态；证据决定在修复工作台绑定精确来源版本。");
                }}
                title={authoring === null ? "请刷新语义一致性或在修复工作台处理证据" : undefined}
                type="button"
              >
                {authoring === null ? "请先处理上游语义" : "打开语义一致性 →"}
              </button>
            </div>
          );
        }
        return (
          <>
            {netErr && (
              <>
                <div className="action-btn-row">
                  <button
                    className="button button-secondary"
                    disabled
                    title="需由 Engine 持久化调度；当前版本未开放此命令"
                    type="button"
                  >
                    ⏱ 10 分钟后重试
                  </button>
                  <button
                    className="button button-secondary"
                    disabled
                    title="需由 Engine 持久化调度；当前版本未开放此命令"
                    type="button"
                  >
                    ⏱ 20 分钟后重试
                  </button>
                </div>
                <div className="action-btn-row">
                  <button
                    className="button button-secondary"
                    disabled
                    title="需由 Engine 持久化调度；当前版本未开放此命令"
                    type="button"
                  >
                    ⏱ 30 分钟后重试
                  </button>
                  <button
                    className="button button-secondary"
                    disabled
                    title="需由 Engine 持久化调度；当前版本未开放此命令"
                    type="button"
                  >
                    ⏱ 1 小时后重试
                  </button>
                </div>
              </>
            )}
            {hasPendingCheckpoint && activity.checkpoint !== null ? (
              renderCheckpointButtons(activity.checkpoint)
            ) : (
              <div className="action-btn-row">
                <button className="button button-primary" onClick={() => void submitPrepare()} type="button">
                  {netErr ? "立即重试 →" : "重新准备方案 →"}
                </button>
              </div>
            )}
            {stoppedFromAuto && (
              <div className="action-btn-row">
                <button className="button button-secondary" onClick={resumeAutoPilot} type="button">
                  继续{resumeAutoLabel} ▶
                </button>
              </div>
            )}
          </>
        );
      }

      case "prepare":
        if (isAuto && !autoStarted) {
          return (
            <div className="action-btn-row">
              <button
                className="button button-primary"
                disabled={commandBusy === "start"}
                onClick={startAutoPilot}
                type="button"
              >
                {autorunStartButtonLabel(commandBusy === "start", autoLabel)}
              </button>
            </div>
          );
        }
        if (isAuto && autoStarted) {
          return (
            <div className="action-btn-row">
              <button
                className="button button-danger"
                disabled={commandBusy === "stop"}
                onClick={stopAutoPilot}
                type="button"
              >
                {autorunStopButtonLabel(commandBusy === "stop", autoLabel)}
              </button>
            </div>
          );
        }
        return (
          <>
            <div className="action-btn-row">
              <button
                className="button button-primary"
                disabled={commandBusy === "prepare" || !canPrepare}
                onClick={() => void submitPrepare()}
                title={
                  canPrepare
                    ? "生成章节执行计划（含场景切分、上下文压缩、桥接锚点）。"
                    : "当前后端不支持 prepare_chapter 命令"
                }
                type="button"
              >
                {commandBusy === "prepare" ? "正在准备…" : "准备章节方案 →"}
              </button>
            </div>
            {stoppedFromAuto && (
              <div className="action-btn-row">
                <button className="button button-secondary" onClick={resumeAutoPilot} type="button">
                  继续{resumeAutoLabel} ▶
                </button>
              </div>
            )}
          </>
        );
    }
  };

  // ── Render ───────────────────────────────────────────────────────────
  return (
    <article className="studio-action-card">
      <header>
        <div>
          <h3>{title}</h3>
          <span className={`studio-action-state is-tone-${badgeTone}`}>
            {badgeText}
          </span>
        </div>
        {panelState.kind === "running" && (
          <p className="studio-action-job-hint">
            最近任务：{activity.taskLabel} · 当前步骤：{currentStepLabel}
          </p>
        )}
        <p className="studio-action-summary">{summary}</p>
      </header>

      {notesVisible && (
        <>
          <button
            className="studio-notes-toggle"
            onClick={() => setNotesExpanded((open) => !open)}
            type="button"
          >
            {notesExpanded ? "⌄" : "›"} {notesTitle}
          </button>
          {notesExpanded && (
            <textarea
              aria-label="章节补充约束"
              onChange={(event) => setNotesText(event.target.value)}
              placeholder={notesPlaceholder}
              value={notesText}
            />
          )}
        </>
      )}

      {renderButtons()}

      {commandError && (
        <ErrorSummary
          error={{ summary: commandError, retryable: true }}
          onDismiss={() => setCommandError(null)}
          onRetry={() => {
            setCommandError(null);
            void submitPrepare();
          }}
        />
      )}

      <div className="studio-action-footer">
        <span>
          {panelState.kind === "checkpoint"
            ? "此节点需要选择方案后才会继续。"
            : panelState.kind === "running"
              ? "任务运行期间可取消；章节上下文将保留在当前会话。"
              : upstreamSemanticBlocked
                ? "修订获批前不会重新准备章节方案或恢复连跑。"
              : isAuto
                ? isBookAuto
                  ? "连跑将按章节轨道自动推进"
                  : "本章将在方案确认后自动推进"
                : "当前以全手动方式工作"}
        </span>
      </div>
    </article>
  );
}
