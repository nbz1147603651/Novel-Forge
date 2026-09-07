import { useCallback, useEffect, useRef, useState } from "react";

import type { ChapterStudioActivityView, ChapterStudioView, ClearClosedTaskErrorsResult, EngineClient, EngineCommandClient, JobView, NarrativeToolsView, TaskErrorResolutionResult, WorkflowErrorLogEntryView } from "@nimo/engine-contracts";

import { AppDialog } from "./AppDialog";
import { AuthoringWorkspace, useAuthoring } from "./AuthoringWorkspace";
import { ChapterStudioActionPanel, type WritingMode } from "./ChapterStudioActionPanel";
import { ChapterWorkflowTimeline } from "./ChapterWorkflowTimeline";
import { ContentRenderer } from "./ContentRenderer";
import {
  ChapterBookAuditDialog,
  ChapterBookRepairDialog,
  ChapterCheckpointDialog,
  ChapterExportDialog,
  ChapterVersionDiffDialog,
} from "./ChapterStudioDialogs";
import { ChapterVersionDiffArtifact } from "./ChapterVersionDiffArtifact";
import { ChapterMemoryPanel } from "./ChapterMemoryPanel";
import { ChapterMaintenanceDialog } from "./ChapterMaintenanceDialog";
import {
  ChapterCleanDialog,
  ChapterProjectSwitchDialog,
} from "./ChapterOperationsDialogs";
import { NarrativeToolsWorkbench } from "./NarrativeToolsWorkbench";
import { RepairWorkbench, summarizeRepairCases } from "./RepairWorkbench";
import { TaskFocusPanel } from "./TaskFocusPanel";
import { StudioSelectMenu } from "./StudioSelectMenu";
import { WorkflowErrorLogDialog } from "./WorkflowErrorLogDialog";
import { chapterVersionsFromReader, type ChapterVersionComparison, type ChapterVersionView } from "../lib/chapter-version-diff";
import {
  createChapterCheckpointActivity,
  createPreparingChapterActivity,
  createRunningChapterActivity,
  type ChapterStudioParityState,
} from "../lib/chapter-studio-session";
import { ChapterContextCards, type ChapterContextDetail } from "./chapter_studio/ChapterContextCards";
import { createChapterContextDocument } from "../lib/chapter-context-document";
import type { ChapterStudioDialogFixture } from "../lib/parity-fixture";
import type { TaskStreamState } from "../lib/task-stream";
import { usePrepareChapter } from "../hooks/usePrepareChapter";
import { useEngineRuntime } from "../lib/engine-runtime-context";
import { buildAuditBookCommand, buildAuditBookEditorialCommand } from "../lib/chapter-book-audit-session";
import { buildExecuteGlobalRepairQueueCommand } from "../lib/chapter-book-repair-session";
import { describeChapterCleanFailure, executeChapterClean } from "../lib/chapter-clean-session";
import {
  deriveEffectiveRewriteStrategy,
  loadChapterStudioPreferences,
  saveChapterStudioPreferences,
  type RewriteStrategyValue,
} from "../lib/chapter-studio-ui-state";
import { LatestRequestGate } from "../lib/latest-request";

interface ChapterStudioPageProps {
  readonly commandClient: EngineCommandClient;
  readonly engineClient: EngineClient;
  readonly focusJob?: JobView | null;
  readonly focusStream?: TaskStreamState | null;
  readonly initialDialog: ChapterStudioDialogFixture | null;
  readonly onNavigate: (page: "workflow") => void;
  readonly onOpenObservation?: () => void;
  readonly onProjectChange: (projectId: string) => void;
  readonly onReadProject: (projectId: string) => void;
  readonly onRefresh: () => void;
  /** 上报当前选中章节号，供顶栏派生 primary 动态标签（对标 coord 的 chapter spin 值）。 */
  readonly onSelectedChapterChange?: (chapterNumber: number) => void;
  readonly parityState: ChapterStudioParityState | null;
  readonly projects: readonly { readonly id: string; readonly title: string }[];
  readonly studio: ChapterStudioView;
  readonly tools: NarrativeToolsView;
}

export function ChapterStudioPage(props: ChapterStudioPageProps) {
  return <AuthoringWorkspace
    commandClient={props.commandClient}
    engineClient={props.engineClient}
    projectId={props.studio.projectId}
    initialChapter={props.studio.nextChapter}
    triggerPlacement="embedded"
  >
    <ChapterStudioPageContent {...props} />
  </AuthoringWorkspace>;
}

function ChapterStudioPageContent({
  commandClient,
  engineClient,
  focusJob = null,
  focusStream = null,
  initialDialog,
  onNavigate,
  onOpenObservation,
  onProjectChange,
  onReadProject,
  onRefresh,
  onSelectedChapterChange,
  parityState,
  projects,
  studio,
  tools,
}: ChapterStudioPageProps) {
  const authoring = useAuthoring();
  const setAuthoringLocation = authoring?.setLocation;
  const { mode, isCommandAvailable, isFeatureEnabled } = useEngineRuntime();
  const canPrepare = isCommandAvailable("prepare_chapter");
  const canMaintainChapter = isCommandAvailable("repair_issues")
    || isCommandAvailable("reevaluate_chapter");
  const canExecuteBookRepair = isCommandAvailable("execute_global_repair_queue");
  const canAuditBookEditorial = isCommandAvailable("audit_book_editorial");
  const canOpenRepairWorkbench = isFeatureEnabled("repair_workbench_v1")
    && engineClient.getRepairSource !== undefined
    && engineClient.listRepairCases !== undefined
    && engineClient.getRepairCase !== undefined;
  const {
    state: prepareState,
    submit: submitPrepare,
    resume: resumePrepare,
  } = usePrepareChapter(engineClient, commandClient);
  const [initialPreferences] = useState(() =>
    loadChapterStudioPreferences(studio.projectId),
  );
  const [selectedChapterNumber, setSelectedChapterNumber] = useState(
    initialPreferences.selectedChapterNumber ?? studio.nextChapter,
  );
  const [writingMode, setWritingMode] = useState<WritingMode>(
    initialPreferences.writingMode ?? "manual",
  );
  const [compositionMode, setCompositionMode] = useState<"whole" | "scene">(
    initialPreferences.compositionMode ?? "whole",
  );
  const [rewriteStrategy, setRewriteStrategy] = useState<RewriteStrategyValue>(
    initialPreferences.rewriteStrategy ?? "auto",
  );
  const [lastProjectId, setLastProjectId] = useState(studio.projectId);
  const [activity, setActivity] =
    useState<ChapterStudioActivityView>(studio.activity);
  const [artifactTab, setArtifactTab] = useState("正文");
  const [versionComparison, setVersionComparison] =
    useState<ChapterVersionComparison | null>(null);
  const [versionCandidates, setVersionCandidates] = useState<readonly ChapterVersionView[]>([]);
  const [versionDiffLoading, setVersionDiffLoading] = useState(false);
  const versionDiffRequestGate = useRef(new LatestRequestGate());
  const [dialog, setDialog] = useState<
    | "audit"
    | "book-repair"
    | "editorial-audit"
    | "export"
    | "diff"
    | "diff-unavailable"
    | "memory"
    | "maintenance"
    | "repair-workbench"
    | "checkpoint"
    | "tools"
    | "clean"
    | "project-switch"
    | "error-log"
    | ChapterContextDetail
    | null
  >(() => (initialDialog === "book-audit" ? "audit" : initialDialog));
  const [operationNotice, setOperationNotice] = useState(() =>
    parityState === "notice"
      ? "第 1 章起已无可删除文件；已清除旧任务流与续跑指针，可从该章重新开始。"
      : "",
  );
  const [cleaning, setCleaning] = useState(false);
  const [pendingProjectSwitchId, setPendingProjectSwitchId] = useState<string | null>(null);
  const engineIsOrchestrating = ["waiting_init", "running", "retry_wait"].includes(
    studio.autorun.status,
  );
  const visibleChapterNumber =
    engineIsOrchestrating && studio.autorun.currentChapter > 0
      ? studio.autorun.currentChapter
      : selectedChapterNumber;
  const displayedActivity = engineIsOrchestrating ? studio.activity : activity;
  useEffect(() => { setAuthoringLocation?.("chapter", visibleChapterNumber); }, [setAuthoringLocation, visibleChapterNumber]);
  const selectedChapter =
    studio.chapters.find(
      (chapter) => chapter.number === visibleChapterNumber,
    ) ?? studio.chapters[0] ?? { number: studio.nextChapter, title: "", state: "pending" as const, detail: "" };
  const archivedCount = studio.chapters.filter(
    (chapter) => chapter.state === "completed",
  ).length;
  const completedChapterNumbers = studio.chapters
    .filter((chapter) => chapter.state === "completed")
    .map((chapter) => chapter.number);
  const unresolvedTaskErrorCount = studio.taskErrorLog.filter(
    (entry) => !entry.autoResolved && !entry.acknowledgedAt,
  ).length;
  const [repairAttention, setRepairAttention] = useState({ attention: 0, urgent: 0 });
  const [repairSummaryReloadKey, setRepairSummaryReloadKey] = useState(0);
  useEffect(() => {
    if (!canOpenRepairWorkbench || engineClient.listRepairCases === undefined) {
      setRepairAttention({ attention: 0, urgent: 0 });
      return;
    }
    let active = true;
    void engineClient.listRepairCases(studio.projectId, { chapter: selectedChapter.number })
      .then((items) => {
        if (active) setRepairAttention(summarizeRepairCases(items));
      })
      .catch(() => {
        if (active) setRepairAttention({ attention: 0, urgent: 0 });
      });
    return () => { active = false; };
  }, [canOpenRepairWorkbench, engineClient, repairSummaryReloadKey, selectedChapter.number, studio.projectId]);
  const acknowledgeErrorEntries = useCallback(async (
    entries: readonly WorkflowErrorLogEntryView[],
  ): Promise<TaskErrorResolutionResult> => {
    const result = await commandClient.acknowledgeTaskErrors({
      kind: "acknowledge_task_errors",
      errorEntryIds: entries.map((entry) => entry.id),
    });
    if (result.status !== "acknowledged") throw new Error(result.message);
    setOperationNotice(result.message);
    onReadProject(studio.projectId);
    return result;
  }, [commandClient, onReadProject, studio.projectId]);
  const reopenErrorEntries = useCallback(async (
    entries: readonly WorkflowErrorLogEntryView[],
  ): Promise<TaskErrorResolutionResult> => {
    const result = await commandClient.reopenTaskErrors({
      kind: "reopen_task_errors",
      errorEntryIds: entries.map((entry) => entry.id),
    });
    if (result.status !== "reopened") throw new Error(result.message);
    setOperationNotice(result.message);
    onReadProject(studio.projectId);
    return result;
  }, [commandClient, onReadProject, studio.projectId]);
  const clearClosedErrorEntries = useCallback(async (
    entries: readonly WorkflowErrorLogEntryView[],
  ): Promise<ClearClosedTaskErrorsResult> => {
    const result = await commandClient.clearClosedTaskErrors({
      kind: "clear_closed_task_errors",
      errorEntryIds: entries.map((entry) => entry.id),
    });
    if (result.status !== "cleared") throw new Error(result.message);
    setOperationNotice(result.message);
    onReadProject(studio.projectId);
    return result;
  }, [commandClient, onReadProject, studio.projectId]);
  const isRunning = displayedActivity.state === "running";
  const projectSwitchNeedsConfirmation = engineIsOrchestrating || isRunning;
  const requestProjectChange = (projectId: string) => {
    if (projectId === studio.projectId) return;
    if (!projectSwitchNeedsConfirmation) {
      onProjectChange(projectId);
      return;
    }
    setPendingProjectSwitchId(projectId);
    setDialog("project-switch");
  };
  const projectSelectOptions = projects.map((project) => ({
    value: project.id,
    label: project.title,
  }));
  const rewriteStrategyOptions = [
    {
      value: "auto",
      label: "由引擎自动裁决",
      description: "按下游章节与强制状态选择顺序重写或增量修补",
    },
    {
      value: "sequential",
      label: "顺序重写",
      description: "按计划逐场景推进重写，不兼顾下游正文",
    },
    {
      value: "compatible",
      label: "增量修补（保留兼容正文）",
      description: "保持与已归档下游章节兼容；无下游时退化为顺序重写",
    },
    {
      value: "reconstruct",
      label: "全章重构（按最新契约重写）",
      description: "按最新契约整体重写本章，放弃现有结构",
    },
    {
      value: "surgical",
      label: "定点重写（修复指定问题）",
      description: "只重写被标记的问题片段，其余正文保留",
    },
  ] as const;

  const hasDownstreamChapters = studio.chapters.some(
    (chapter) =>
      chapter.number > selectedChapter.number && chapter.state === "completed",
  );
  const effectiveRewriteStrategy = deriveEffectiveRewriteStrategy(
    rewriteStrategy,
    { force: false, hasDownstream: hasDownstreamChapters },
  );
  const rewriteStrategyHint =
    effectiveRewriteStrategy === rewriteStrategy
      ? ""
      : `非强制运行时引擎将实际执行为「${
          rewriteStrategyOptions.find(
            (option) => option.value === effectiveRewriteStrategy,
          )?.label ?? effectiveRewriteStrategy
        }」。`;

  // Restore the newly selected project's saved preferences during render so
  // the very next commit already carries the correct values (no transient
  // persist of the previous project's choices under the new project key).
  if (lastProjectId !== studio.projectId) {
    setLastProjectId(studio.projectId);
    const preferences = loadChapterStudioPreferences(studio.projectId);
    setSelectedChapterNumber(preferences.selectedChapterNumber ?? studio.nextChapter);
    setWritingMode(preferences.writingMode ?? "manual");
    setCompositionMode(preferences.compositionMode ?? "whole");
    setRewriteStrategy(preferences.rewriteStrategy ?? "auto");
  }

  // Persist user selections per project (mirrors PySide6 project preferences;
  // only user choices are stored, never in-flight execution state).
  useEffect(() => {
    saveChapterStudioPreferences(studio.projectId, {
      compositionMode,
      writingMode,
      rewriteStrategy,
      selectedChapterNumber,
    });
  }, [studio.projectId, compositionMode, writingMode, rewriteStrategy, selectedChapterNumber]);

  useEffect(() => {
    setActivity(studio.activity);
  }, [studio.activity]);

  useEffect(() => {
    versionDiffRequestGate.current.invalidate();
    setVersionCandidates([]);
    setVersionComparison(null);
    setVersionDiffLoading(false);
    setArtifactTab((current) => current === "版本对比" ? "正文" : current);
  }, [selectedChapter.number, studio.projectId]);

  // 上报当前选中章节号，供 App 顶栏派生 primary_action_label
  // （对标 PySide6 coord 顶栏读取 chapter spin 值）。
  useEffect(() => {
    onSelectedChapterChange?.(visibleChapterNumber);
  }, [visibleChapterNumber, onSelectedChapterChange]);

  useEffect(() => {
    if (mode === "legacy") {
      resumePrepare(studio.projectId);
    }
  }, [mode, resumePrepare, studio.projectId]);

  useEffect(() => {
    if (mode !== "legacy") return;
    if (prepareState.phase === "submitting" || prepareState.phase === "running") {
      setActivity({
        state: "running",
        taskLabel: `第 ${selectedChapter.number} 章生成`,
        currentStepLabel: prepareState.stream?.stepLabel || "准备章节方案",
        progressPercent: prepareState.progressPercent,
        checkpoint: null,
        stages: [],
      });
    } else if (prepareState.phase === "failed") {
      setOperationNotice(prepareState.error ?? "章节准备失败。");
    } else if (prepareState.message) {
      setOperationNotice(prepareState.message);
    }
  }, [
    mode,
    prepareState.error,
    prepareState.message,
    prepareState.phase,
    prepareState.progressPercent,
    prepareState.stream?.stepLabel,
    selectedChapter.number,
  ]);

  const handlePrimaryAction = async (notes?: string, force = false) => {
    if (!canPrepare) {
      setOperationNotice("当前后端不支持章节准备命令。");
      return;
    }
    if (mode === "legacy") {
      await submitPrepare(studio.projectId, selectedChapter.number, {
        force,
        ...(notes ? { notes } : {}),
        rewriteStrategy,
        writingMode: compositionMode === "scene" ? "scene_level" : "whole_chapter",
      });
      return;
    }
    try {
      const result = await commandClient.prepareChapter({
        kind: "prepare_chapter",
        projectId: studio.projectId,
        chapterNumber: selectedChapter.number,
        ...(notes ? { notes } : {}),
        force,
        rewriteStrategy,
        writingMode: compositionMode === "scene" ? "scene_level" : "whole_chapter",
      });
      setOperationNotice(result.message);
      if (result.status === "accepted") {
        setActivity(
          createPreparingChapterActivity(studio, selectedChapter.number),
        );
      }
    } catch {
      setOperationNotice("准备章节命令失败；请检查引擎连接后重试。");
    }
  };

  const requestVersionDiff = async () => {
    const projectId = studio.projectId;
    const chapterNumber = selectedChapter.number;
    const request = versionDiffRequestGate.current.begin(`chapter-version-diff:${projectId}:${chapterNumber}`);
    setVersionDiffLoading(true);
    try {
      const reader = await engineClient.getProjectReader(projectId);
      if (!versionDiffRequestGate.current.isCurrent(request)) return;
      const versions = chapterVersionsFromReader(reader, chapterNumber);
      if (versions.length < 2) {
        setDialog("diff-unavailable");
        return;
      }
      setVersionCandidates(versions);
      setDialog("diff");
    } catch {
      if (versionDiffRequestGate.current.isCurrent(request)) {
        setOperationNotice("读取章节版本失败；请检查 Engine 连接后重试。");
      }
    } finally {
      if (versionDiffRequestGate.current.isCurrent(request)) setVersionDiffLoading(false);
    }
  };

  const artifactCopy: Readonly<
    Record<string, { readonly title: string; readonly detail: string }>
  > = {
    正文: {
      title: `第 ${selectedChapter.number} 章正文`,
      detail:
        selectedChapter.state === "completed"
          ? "已归档正文已通过质量门禁；阅读器会保留章节内的段落与版本状态。"
          : "正文将在方案确认、草稿生成与质量门禁完成后归档。",
    },
    "章节计划": {
      title: `第 ${selectedChapter.number} 章计划`,
      detail:
        selectedChapter.state === "current" ||
        selectedChapter.state === "needs_decision"
          ? studio.planSummary
          : "计划以当前章节的故事状态、未兑现伏笔和角色关系为边界生成。",
    },
    "创作报告": {
      title: `第 ${selectedChapter.number} 章创作报告`,
      detail:
        "报告汇总对齐、一致性、因果链与追读力结果；不影响已经归档的正文。",
    },
    "Canon 状态": {
      title: "当前故事状态",
      detail:
        "章节完成后，已验证的角色关系、物件、时间线与承诺会写入故事核心状态。",
    },
  };
  const artifactTabs =
    versionComparison === null
      ? Object.keys(artifactCopy)
      : [...Object.keys(artifactCopy), "版本对比"];
  const showVersionComparison =
    artifactTab === "版本对比" && versionComparison !== null;

  return (
    <div className="studio-page">
      <section className="studio-selector">
        <div className="studio-selector-leading">
          <h2>章台工作台</h2>
          <div className="studio-selector-leading-actions">
            {authoring?.entry.visible && <button
              aria-controls="authoring-sidebar"
              aria-expanded={authoring.entry.expanded}
              className={`button button-secondary studio-coauthor-trigger${authoring.entry.expanded ? " is-open" : ""}`}
              onClick={authoring.entry.toggle}
              type="button"
            >
              {authoring.entry.label}
            </button>}
            <div className="studio-selector-navigation-actions">
              <button
                className="button button-secondary"
                onClick={() => onReadProject(studio.projectId)}
                type="button"
              >
                阅卷
              </button>
              <button
                className="button button-secondary"
                onClick={() => onNavigate("workflow")}
                type="button"
              >
                回机杼 →
              </button>
            </div>
          </div>
        </div>
        <div className="studio-selector-controls">
          <StudioSelectMenu
            ariaLabel="章台项目"
            label="长篇项目"
            onChange={requestProjectChange}
            options={projectSelectOptions}
            title={projectSwitchNeedsConfirmation
              ? "此项目任务正在后台运行；可切换查看其他项目，任务不会中断。"
              : "切换后从 Engine 重新载入章台状态"}
            value={studio.projectId}
          />
          <div
            className="studio-mode-control"
            role="group"
            aria-label="章节写作模式"
          >
            <span>写作模式</span>
            <button
              className={compositionMode === "whole" ? "is-active" : ""}
              onClick={() => setCompositionMode("whole")}
              type="button"
            >
              整章写作
            </button>
            <button
              className={compositionMode === "scene" ? "is-active" : ""}
              onClick={() => setCompositionMode("scene")}
              type="button"
            >
              场景级写作
            </button>
          </div>
          <div
            className="studio-mode-control"
            role="group"
            aria-label="章节裁决模式"
          >
            <span>裁决模式</span>
            <button
              className={writingMode === "manual" ? "is-active" : ""}
              onClick={() => setWritingMode("manual")}
              type="button"
            >
              全手动
            </button>
            <button
              className={writingMode === "suggest" ? "is-active" : ""}
              onClick={() => setWritingMode("suggest")}
              type="button"
            >
              AI 建议
            </button>
            <button
              className={writingMode === "auto" ? "is-active" : ""}
              onClick={() => setWritingMode("auto")}
              type="button"
            >
              本章自动
            </button>
            <button
              className={writingMode === "book_auto" ? "is-active" : ""}
              onClick={() => setWritingMode("book_auto")}
              type="button"
            >
              章节连跑
            </button>
          </div>
          <div className="studio-rewrite-strategy">
            <StudioSelectMenu
              ariaLabel="重写策略"
              className="studio-select-menu--rewrite"
              label="重写策略"
              onChange={setRewriteStrategy}
              options={rewriteStrategyOptions}
              title="选择本章重写时的正文保留与修订边界"
              value={rewriteStrategy}
            />
            {rewriteStrategyHint && (
              <small className="studio-rewrite-hint">{rewriteStrategyHint}</small>
            )}
          </div>
        </div>
        <div className="studio-selector-tools" aria-label="章台工具">
          <button
            className={`button button-secondary workflow-error-button${unresolvedTaskErrorCount > 0 ? " is-unresolved" : " is-resolved"}`}
            onClick={() => setDialog("tools")}
            title="管理支线、母题、蓝图元素与拟人化库。"
            type="button"
          >
            叙事工具
          </button>
          <button
            className="button button-secondary"
            disabled={!canMaintainChapter}
            onClick={() => setDialog("maintenance")}
            title="提交连续性、因果、评估、关系和母题维护任务，由 Engine 持久化执行。"
            type="button"
          >
            章节维护
          </button>
          <button
            aria-label={repairAttention.attention > 0
              ? `修复工作台，${repairAttention.attention} 项待处理${repairAttention.urgent > 0 ? `，${repairAttention.urgent} 项高优先级` : ""}`
              : "修复工作台"}
            className={`button button-secondary repair-workbench-trigger${repairAttention.attention > 0 ? " is-attention" : ""}`}
            disabled={!canOpenRepairWorkbench}
            onClick={() => setDialog("repair-workbench")}
            title={canOpenRepairWorkbench
              ? "查看本章或全书修复证据；精确候选经复验与作者批准后，按内容权限分级发布"
              : "当前 Engine 未开放统一修复工作台"}
            type="button"
          >
            <span>修复工作台</span>
            {repairAttention.attention > 0 && <strong aria-hidden="true">{repairAttention.attention > 99 ? "99+" : repairAttention.attention}</strong>}
          </button>
          <button
            className="button button-secondary"
            onClick={() => setDialog("audit")}
            title="检查全书一致性：命名、时间线、世界观、角色状态等，可选择审计范围"
            type="button"
          >
            全书审计
          </button>
          <button
            className="button button-secondary"
            disabled={!canAuditBookEditorial}
            onClick={() => setDialog("editorial-audit")}
            title={canAuditBookEditorial ? "按出版编辑契约审查结构、节奏、人物弧光与语言成熟度" : "请先重启本地 Engine 以加载出版编辑审查能力"}
            type="button"
          >
            出版审查
          </button>
          <button
            className="button button-secondary"
            disabled={!canExecuteBookRepair}
            onClick={() => setDialog("book-repair")}
            title={canExecuteBookRepair ? "执行最近一次全书审计产生的 ready 修复队列" : "请先重启本地 Engine 以加载全书修复队列能力"}
            type="button"
          >
            全书修复
          </button>
          <button
            className="button button-secondary"
            onClick={() => setDialog("export")}
            title="导出为 Markdown / 纯文本 / EPUB，可选择章节范围；由 Engine 统一写入项目 exports 目录"
            type="button"
          >
            导出
          </button>
          <button
            className="button button-secondary"
            disabled={versionDiffLoading}
            onClick={() => { void requestVersionDiff(); }}
            title="对比当前章节的不同草稿版本，行级高亮显示修改差异"
            type="button"
          >
            {versionDiffLoading ? "读取版本…" : "版本对比"}
          </button>
          <button
            className="button button-secondary"
            onClick={() => setOperationNotice(
              studio.autorun.status === "idle"
                ? "Engine 连跑尚未启动；可在裁决模式中选择本章自动或章节连跑。"
                : `Engine 连跑：第 ${studio.autorun.currentChapter}/${studio.autorun.endChapter} 章，失败 ${studio.autorun.totalFailures}/${studio.autorun.failureBudget}，检查点 ${studio.autorun.checkpointAttempts}/${studio.autorun.checkpointBudget}。`,
            )}
            title="查看 Engine 持久化连跑状态与失败预算"
            type="button"
          >
            连跑状态
          </button>
          {studio.chapters.length > 0 && <button
            className="button button-secondary"
            disabled={isRunning || engineIsOrchestrating || cleaning}
            onClick={() => setDialog("clean")}
            title={isRunning || engineIsOrchestrating
              ? "请先停止运行中的章节任务"
              : "删除当前或失效章节的已生成文件，并同步回滚故事状态"}
            type="button"
          >
            {cleaning ? "清理中…" : "清理章节"}
          </button>}
          <button
            className="button button-secondary"
            onClick={() => setDialog("error-log")}
            title="查看任务流中的错误观察记录与修复状态。"
            type="button"
          >
            {unresolvedTaskErrorCount > 0 ? `错误日志 ${unresolvedTaskErrorCount}` : "错误日志 · 已处理"}
          </button>
        </div>
        {operationNotice && (
          <p
            aria-atomic="true"
            aria-live="polite"
            className="studio-operation-notice"
            title={operationNotice}
          >
            {operationNotice}
          </p>
        )}
      </section>

      <section className="studio-workbench">
        <aside className="studio-chapter-rail">
          <div className="studio-panel-heading">
            <div>
              <h3>章节轨道</h3>
              <p>
                {studio.projectTitle} · 共 {studio.totalChapters} 章 · 当前第{" "}
                {selectedChapter.number} 章
              </p>
            </div>
          </div>
          <ol>
            {studio.chapters.map((chapter) => (
              <li key={chapter.number}>
                <button
                  aria-current={
                    selectedChapter.number === chapter.number
                      ? "step"
                      : undefined
                  }
                  className={`is-${chapter.state}${
                    selectedChapter.number === chapter.number
                      ? " is-selected"
                      : ""
                  }`}
                  onClick={() => {
                    setSelectedChapterNumber(chapter.number);
                    setActivity(studio.activity);
                  }}
                  type="button"
                >
                  <span>
                    {chapter.state === "completed"
                      ? "✓"
                      : chapter.state === "needs_decision"
                        ? "◆"
                        : chapter.state === "current"
                          ? "●"
                          : "○"}
                  </span>
                  {chapter.state === "current" && (
                    <i className="studio-rail-pulse" aria-hidden="true" />
                  )}
                  <div>
                    <strong>
                      第 {chapter.number} 章 · {chapter.title}
                    </strong>
                    <small>{chapter.detail}</small>
                  </div>
                </button>
              </li>
            ))}
          </ol>
        </aside>
        <div className="studio-center">
          <ChapterStudioActionPanel
            activity={displayedActivity}
            chapterNumber={selectedChapter.number}
            chapterState={selectedChapter.state}
            chapterWordCount={0}
            commandClient={commandClient}
            mode={writingMode}
            onActivityChange={setActivity}
            onGoToNextChapter={() => {
              setSelectedChapterNumber(selectedChapter.number + 1);
              setActivity(studio.activity);
            }}
            onModeChange={setWritingMode}
            onNotice={setOperationNotice}
            onOpenCheckpointDialog={() => setDialog("checkpoint")}
            onPrepare={handlePrimaryAction}
            compositionMode={compositionMode}
            studio={studio}
            totalChapters={studio.totalChapters}
          />
          <ChapterContextCards
            onOpenDetail={(detail) => setDialog(detail)}
            studio={studio}
          />
        </div>
        <ChapterMemoryPanel
          onOpen={() => setDialog("memory")}
          tabs={studio.memoryTabs}
        />
      </section>

      <ChapterWorkflowTimeline
        activity={displayedActivity}
        chapterNumber={selectedChapter.number}
        engineClient={engineClient}
        onNotice={setOperationNotice}
        projectId={studio.projectId}
      />

      <section className="studio-artifacts">
        <div className="studio-artifacts-heading">
          <div>
            <h2>工作台产物</h2>
            <p>正文、章节计划、创作报告与故事状态都可在这里直接查看。</p>
          </div>
          <span>第 {selectedChapter.number} 章</span>
        </div>
        <div className="studio-artifact-tabs" role="tablist">
          {artifactTabs.map((tab) => (
            <button
              aria-selected={artifactTab === tab}
              className={artifactTab === tab ? "is-active" : ""}
              key={tab}
              onClick={() => setArtifactTab(tab)}
              role="tab"
              type="button"
            >
              {tab}
            </button>
          ))}
        </div>
        {showVersionComparison ? (
          <ChapterVersionDiffArtifact comparison={versionComparison} />
        ) : (
          <article className="studio-artifact-content">
            <span className="section-kicker">{artifactTab}</span>
            <h3>{artifactCopy[artifactTab]!.title}</h3>
            <p>{artifactCopy[artifactTab]!.detail}</p>
            <button
              className="button button-secondary"
              onClick={() => onReadProject(studio.projectId)}
              type="button"
            >
              在卷帙中打开
            </button>
          </article>
        )}
      </section>
      {/* 章台关注放在工作台产物之后，避免挤占章节行动与上下文的中栏高度。 */}
      <section className="studio-focus-section studio-focus-section-bottom" aria-label="章台关注">
        <div className="studio-focus-header">
          <h3>章台关注</h3>
          <p>当前节点输出、诊断与需要确认的动作。</p>
        </div>
        <TaskFocusPanel
          compact
          job={focusJob}
          {...(onOpenObservation !== undefined ? { onExpand: onOpenObservation } : {})}
          scope="chapter"
          stream={focusStream}
          title="章台关注"
        />
      </section>
      {dialog === "audit" && (
        <ChapterBookAuditDialog
          chapterNumbers={completedChapterNumbers}
          onClose={() => setDialog(null)}
          onConfirm={(request) => {
            void commandClient.auditBook(buildAuditBookCommand(studio.projectId, request)).then((result) => setOperationNotice(result.message)).catch(() => {
              setOperationNotice("全书一致性审计提交失败。");
            });
          }}
        />
      )}
      {dialog === "book-repair" && (
        <ChapterBookRepairDialog
          onClose={() => setDialog(null)}
          onConfirm={(parameters) => {
            void commandClient.executeGlobalRepairQueue(
              buildExecuteGlobalRepairQueueCommand(studio.projectId, parameters),
            ).then((result) => setOperationNotice(result.message)).catch(() => {
              setOperationNotice("全书审计修复队列提交失败。");
            });
          }}
        />
      )}
      {dialog === "editorial-audit" && (
        <ChapterBookAuditDialog
          auditDomain="editorial"
          chapterNumbers={completedChapterNumbers}
          onClose={() => setDialog(null)}
          onConfirm={(request) => {
            void commandClient.auditBookEditorial(
              buildAuditBookEditorialCommand(studio.projectId, request),
            ).then((result) => setOperationNotice(result.message)).catch(() => {
              setOperationNotice("全书出版编辑审查提交失败。");
            });
          }}
        />
      )}
      {dialog === "tools" && (
        <AppDialog
          description="支线管理通过 Engine 命令持久化并刷新受影响章节；角色、关系、大纲和拟人化仍以当前视图提供浏览与草案编辑。"
          onClose={() => setDialog(null)}
          size="wide"
          title="叙事工具"
        >
          <NarrativeToolsWorkbench
            commandClient={commandClient}
            onNarrativeToolsRefresh={() => onReadProject(studio.projectId)}
            projectId={studio.projectId}
            tools={tools}
          />
        </AppDialog>
      )}
      {dialog === "maintenance" && (
        <ChapterMaintenanceDialog
          chapterNumber={selectedChapter.number}
          commandClient={commandClient}
          onClose={() => setDialog(null)}
          onSubmitted={(result) => {
            setOperationNotice(result.message);
            if (result.status === "accepted") onReadProject(studio.projectId);
          }}
          projectId={studio.projectId}
        />
      )}
      {dialog === "repair-workbench" && (
        <RepairWorkbench
          chapterNumber={selectedChapter.number}
          commandClient={commandClient}
          engineClient={engineClient}
          onClose={() => {
            setDialog(null);
            setRepairSummaryReloadKey((value) => value + 1);
          }}
          onPublished={() => {
            setRepairSummaryReloadKey((value) => value + 1);
            onReadProject(studio.projectId);
          }}
          projectId={studio.projectId}
        />
      )}
      {dialog !== null &&
        typeof dialog === "object" &&
        dialog.kind === "context-detail" && (
        <AppDialog
          className="studio-context-dialog"
          description="完整上下文以结构化阅读格式呈现；识别到的旧版记录会提取叙事内容，不再干扰核对。"
          onClose={() => setDialog(null)}
          size="wide"
          title={dialog.title}
        >
          <div className="studio-context-detail-document">
            <ContentRenderer document={createChapterContextDocument(dialog)} />
          </div>
        </AppDialog>
      )}
      {dialog === "export" && (
        <ChapterExportDialog
          chapterNumbers={completedChapterNumbers}
          onClose={() => setDialog(null)}
          onConfirm={(request) => {
            void commandClient.exportBook({
              kind: "export_book",
              projectId: studio.projectId,
              format: request.format,
              chapterRange: request.selectedChapters,
              bookTitle: request.bookTitle,
            }).then((result) => setOperationNotice(result.message)).catch(() => {
              setOperationNotice("书稿导出任务提交失败。");
            });
          }}
          projectTitle={studio.projectTitle}
        />
      )}
      {dialog === "diff" && (
        <ChapterVersionDiffDialog
          chapterNumber={selectedChapter.number}
          onClose={() => setDialog(null)}
          onCompare={(comparison) => {
            setVersionComparison(comparison);
            setArtifactTab("版本对比");
            setOperationNotice(
              `已从 Engine 项目阅读模型加载第 ${comparison.chapterNumber} 章「${comparison.older.label} → ${comparison.newer.label}」的只读版本对比。`,
            );
          }}
          versions={versionCandidates}
        />
      )}
      {dialog === "diff-unavailable" && (
        <AppDialog
          confirmLabel="知道了"
          description={`第 ${selectedChapter.number} 章的草稿版本不足 2 个，无法进行对比。完成至少一轮编辑后即可使用版本对比功能。`}
          onClose={() => setDialog(null)}
          onConfirm={() => setDialog(null)}
          title="版本不足"
        />
      )}
      {dialog === "clean" && (
        <ChapterCleanDialog
          chapters={studio.chapters}
          defaultCutoff={selectedChapter.number}
          maxChapter={studio.totalChapters}
          onClose={() => setDialog(null)}
          onConfirm={(cutoff) => {
            setCleaning(true);
            setOperationNotice(`正在从第 ${cutoff} 章起清理失效产物…`);
            void executeChapterClean({
              client: commandClient,
              projectId: studio.projectId,
              cutoff,
              onRefreshRequired: (refreshCutoff) => {
                setSelectedChapterNumber(refreshCutoff);
                onRefresh();
              },
            }).then((result) => {
              setOperationNotice(result.message);
            }).catch((error: unknown) => {
              setOperationNotice(describeChapterCleanFailure(error));
            }).finally(() => {
              setCleaning(false);
            });
          }}
        />
      )}
      {dialog === "project-switch" && (
        <ChapterProjectSwitchDialog
          chapterNumber={selectedChapter.number}
          jobStatus={
            isRunning
              ? `正在生成第 ${selectedChapter.number} 章`
              : "任务会保留在后台"
          }
          onClose={() => {
            setPendingProjectSwitchId(null);
            setDialog(null);
          }}
          onConfirm={() => {
            const targetId = pendingProjectSwitchId
              ?? projects.find((project) => project.id !== studio.projectId)?.id;
            if (targetId !== undefined) onProjectChange(targetId);
            setPendingProjectSwitchId(null);
            setDialog(null);
          }}
          projectTitle={studio.projectTitle}
        />
      )}
      {dialog === "error-log" && (
        <WorkflowErrorLogDialog
          entries={studio.taskErrorLog}
          onAcknowledge={acknowledgeErrorEntries}
          onClearClosed={clearClosedErrorEntries}
          onClose={() => setDialog(null)}
          onReopen={reopenErrorEntries}
        />
      )}
      {dialog === "memory" && (
        <AppDialog
          description="以下为本章工作台加载的记忆边界；第一阶段只读呈现，不会修改故事核心状态。"
          onClose={() => setDialog(null)}
          title="完整记忆上下文"
        >
          <div className="dialog-check-list">
            {studio.memories.map((memory) => (
              <span key={memory.label}>
                ✓ {memory.label}：{memory.value}
              </span>
            ))}
          </div>
        </AppDialog>
      )}
      {dialog === "checkpoint" && displayedActivity.checkpoint !== null && (
        <ChapterCheckpointDialog
          checkpoint={displayedActivity.checkpoint}
          onClose={() => setDialog(null)}
          onResolve={(optionId, notes) => {
            const option = displayedActivity.checkpoint?.options.find(
              (candidate) => candidate.id === optionId,
            );
            const checkpointId = displayedActivity.checkpoint?.id;
            if (!checkpointId) return;
            if (authoring?.session?.configured) {
              void authoring.propose({ command: "checkpoint", chapterNumber: selectedChapter.number, optionId, notes, title: option?.label ?? "本章确认", expectedInputVersion: authoring.session.inputVersion }).then(() => setDialog(null)).catch((error) => setOperationNotice(String(error)));
              return;
            }
            void commandClient.resolveChapterCheckpoint({
              kind: "resolve_chapter_checkpoint",
              projectId: studio.projectId,
              chapterNumber: selectedChapter.number,
              checkpointId,
              optionId,
              ...(notes ? { notes } : {}),
            }).then((result) => {
              setOperationNotice(`${option?.label ?? optionId}：${result.message}`);
              if (result.status === "accepted") {
                setActivity(createRunningChapterActivity(studio));
                setDialog(null);
              }
            }).catch(() => {
              setOperationNotice("检查点裁决提交失败；检查点仍保持待处理。");
            });
          }}
        />
      )}
    </div>
  );
}
