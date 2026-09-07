import { useCallback, useEffect, useMemo, useState } from "react";

import type { EngineClient, EngineCommandClient, ProjectView, StepArtifactFile, WorkflowAiHistoryEntry, WorkflowRunView } from "@nimo/engine-contracts";

import {
  createLongInitSession,
  deleteLongInitPreset,
  emptyLongInitPayload,
  emptyLongInitPayloadForLocale,
  findLongInitPresetByName,
  loadLongInitPreset,
  longInitLaunchPreview,
  longLanguageOptions,
  longToneOptions,
  normalizeLongInitPayload,
  normalizeLongInitPatch,
  normalizeVolumeFields,
  researchProviderOptions,
  resetLongInitSession,
  saveLongInitPreset,
  serializeLongInitPayload,
  toInitLongWorkflowInput,
  updateLongInitPayload,
  validateLongInitPayload,
  volumeModeOptions,
  type LongInitPayload,
  type LongInitPayloadPatch,
  type LongInitHistoryEntry,
  type LongInitSession,
  type LongTone,
  type CreativeExploration,
  type PlanningCommitment,
  type ResearchProvider,
  type VolumeMode,
} from "../lib/long-init-session";
import { useDraftAutosave } from "../lib/workflow-draft-autosave";
import type { BlueprintElementPreferences } from "../lib/short-workflow-session";
import { useLocale } from "../lib/i18n";
import { longInitActionState } from "../lib/long-init-action-state";
import { workflowRunProjectId } from "../lib/workflow-run-session";
import { BlueprintElementPreferencePanel } from "./BlueprintElementPreferencePanel";
import {
  pipelineStepLabel,
  StepIndicatorRow,
  taskProgressProjection,
  type PipelineStep,
} from "./StepIndicatorRow";
import { AppDialog } from "./AppDialog";
import { WorkflowArtifactDialog } from "./WorkflowArtifactDialog";
import { useEngineRuntime } from "../lib/engine-runtime-context";
import { WorkflowAiAssistant, type WorkflowAiApplyContext, type WorkflowAiField } from "./WorkflowAiAssistant";
import {
  advancedFields,
  coreFields,
  type FieldDefinition,
  type FieldGroup,
} from "./workflow/long-init/fields";
import {
  FieldCardSection,
  FieldEditDialog,
} from "./workflow/long-init/FieldEditDialog";
import {
  ImportDialog,
  SavePresetDialog,
} from "./workflow/long-init/PresetDialogs";

const longAiFields: readonly WorkflowAiField[] = [
  { key: "premise", label: "故事前提" },
  { key: "genre", label: "题材" },
  { key: "tone", label: "基调" },
  { key: "totalChapters", label: "总章节数" },
  { key: "wordsPerChapter", label: "每章字数" },
  { key: "title", label: "作品名" },
  { key: "language", label: "语言" },
  { key: "charactersHint", label: "主角群提示" },
  { key: "worldHint", label: "世界观 / 时代背景" },
  { key: "conflictHint", label: "主冲突提示" },
  { key: "povHint", label: "叙事视角" },
  { key: "openingStyle", label: "开篇方式" },
  { key: "endingStyle", label: "结尾方式" },
  { key: "extraInstructions", label: "额外创作指令" },
];

const longAiFieldLabels = new Map(longAiFields.map((field) => [field.key, field.label]));

function historyTimeLabel(): string {
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(new Date()).replaceAll("/", "-");
}

function noteText(value: unknown): string {
  if (typeof value === "string") return value.trim();
  if (Array.isArray(value)) return value.map(noteText).filter(Boolean).join("；");
  return "";
}

function historyDetails(entry: LongInitSession["history"][number]): readonly string[] {
  const details = [
    entry.userHint ? `方向：${entry.userHint}` : "",
    entry.selectedSuggestions?.length ? `灵感：${entry.selectedSuggestions.join("、")}` : "",
    entry.creativeProfile?.style ? `创意取向：${({ balanced: "均衡完整", plot: "剧情优先", character: "人物优先" }[entry.creativeProfile.style] ?? entry.creativeProfile.style)}` : "",
  ];
  const note = entry.creativeNote;
  if (note !== undefined) {
    const corePitch = noteText(note.core_pitch);
    const designIntent = noteText(note.design_intent);
    const nextMoves = noteText(note.next_moves);
    if (corePitch) details.push(`核心卖点：${corePitch}`);
    if (designIntent) details.push(`设计意图：${designIntent}`);
    if (nextMoves) details.push(`后续方向：${nextMoves}`);
  }
  return details.filter(Boolean);
}

function longHistoryFromEngine(entry: WorkflowAiHistoryEntry, presetId: string): LongInitHistoryEntry {
  const metadata = entry.metadata ?? {};
  const accepted = Array.isArray(metadata.acceptedFields)
    ? metadata.acceptedFields
    : Array.isArray(metadata.accepted_fields) ? metadata.accepted_fields : Object.keys(entry.data);
  const validKeys = new Set<keyof LongInitPayload>(Object.keys(emptyLongInitPayload) as (keyof LongInitPayload)[]);
  const changedKeys = accepted.filter(
    (key): key is keyof LongInitPayload => typeof key === "string" && validKeys.has(key as keyof LongInitPayload),
  );
  const creativeNote = metadata.creativeNote ?? metadata.creative_note;
  const creativeProfile = metadata.creativeProfile ?? metadata.creative_profile;
  return {
    id: entry.id,
    label: entry.operation.startsWith("polish") ? "AI 定向润色" : "AI 生成并预览",
    presetId,
    changedKeys,
    snapshot: normalizeLongInitPayload(entry.data),
    timeLabel: entry.timestamp,
    ...(entry.hint === undefined ? {} : { userHint: entry.hint }),
    ...(entry.selectedSuggestions === undefined ? {} : { selectedSuggestions: entry.selectedSuggestions }),
    ...(creativeNote !== null && typeof creativeNote === "object" && !Array.isArray(creativeNote)
      ? { creativeNote: creativeNote as Readonly<Record<string, unknown>> } : {}),
    ...(creativeProfile !== null && typeof creativeProfile === "object" && !Array.isArray(creativeProfile)
      ? { creativeProfile: Object.fromEntries(Object.entries(creativeProfile).filter((item): item is [string, string] => typeof item[1] === "string")) } : {}),
  };
}

function autoAiPresetName(title: string, operation: "generate" | "polish"): string {
  const prefix = title.trim() || (operation === "generate" ? "AI 新方案" : "AI 润色方案");
  return `${prefix} · AI ${new Date().toISOString().slice(0, 16).replace("T", " ")}`;
}

// ── Dialog types ─────────────────────────────────────────────────────

type LongDialog =
  | { readonly type: "fields"; readonly group: FieldGroup; readonly selectedId: string }
  | { readonly type: "launch"; readonly preview: Readonly<Record<string, string | number>> }
  | { readonly type: "validation"; readonly issues: readonly string[] }
  | { readonly type: "save-preset"; readonly suggestedName: string }
  | { readonly type: "delete-preset"; readonly presetId: string; readonly presetName: string }
  | { readonly type: "restart-confirm" }
  | { readonly type: "export"; readonly content: string }
  | { readonly type: "import" }
  | { readonly type: "history" }
  | { readonly type: "history-clear-confirm" }
  | null;

// ── Props ────────────────────────────────────────────────────────────

interface LongInitFormPanelProps {
  readonly commandClient: EngineCommandClient;
  readonly engineClient: EngineClient;
  readonly initRunning: boolean;
  readonly resumable: boolean;
  /** Engine-confirmed long project whose initialization can continue. */
  readonly resumableProject?: ProjectView | null;
  readonly activeRun?: WorkflowRunView | null;
  /** Whether there is a failed/interrupted init_long run (for restart button). */
  readonly hasFailedInit?: boolean;
  readonly onCancelInit?: ((runId: string) => void) | undefined;
  readonly onRestartInit?: (() => Promise<void> | void) | undefined;
  readonly onWorkflowStarted?: (() => Promise<void> | void) | undefined;
}

/**
 * 1:1 React mirror of PySide6 `LongInitForm` (workflow/forms.py).
 */
export function LongInitFormPanel({
  commandClient,
  engineClient,
  initRunning,
  resumable,
  resumableProject = null,
  activeRun = null,
  hasFailedInit = false,
  onCancelInit,
  onRestartInit,
  onWorkflowStarted,
}: LongInitFormPanelProps) {
  const { isCommandAvailable } = useEngineRuntime();
  const locale = useLocale();
  const [session, setSession] = useState<LongInitSession>(() =>
    createLongInitSession(emptyLongInitPayloadForLocale(locale)),
  );
  const [selectedPresetId, setSelectedPresetId] = useState("");
  const [presetRevisions, setPresetRevisions] = useState<ReadonlyMap<string, string>>(() => new Map());
  const [dialog, setDialog] = useState<LongDialog>(null);
  const [researchExpanded, setResearchExpanded] = useState(false);
  const [preferencesExpanded, setPreferencesExpanded] = useState(false);
  const [operationNotice, setOperationNotice] = useState("");
  const [artifactDialog, setArtifactDialog] = useState<{
    readonly stepKey: string;
    readonly stepLabel: string;
    readonly artifacts: readonly StepArtifactFile[];
    readonly candidatePaths?: readonly { readonly label: string; readonly path: string }[];
    readonly emptyHint?: string;
  } | null>(null);

  const payload = session.payload;
  const selectedPreset = session.presets.find((p) => p.id === selectedPresetId);
  const historyPresetId = session.activePresetId ?? "当前草稿";
  const historyEntries = session.history.filter((entry) => entry.presetId === historyPresetId);
  const historyPresetName = selectedPreset?.name || payload.title.trim() || "当前草稿";
  const aiPreviewAvailable = isCommandAvailable("generate_workflow_fields");

  // ── Draft autosave (mirrors PySide6 30s + 750ms debounce) ──────────
  const { restored, lastSavedAt, lastSaveError } = useDraftAutosave<LongInitPayload>({
    commandClient,
    mode: "long",
    payload,
    onRestore: (restoredPayload) => {
      setSession((s) => ({ ...s, payload: normalizeLongInitPayload(restoredPayload) }));
    },
    enabled: !initRunning,
  });

  // Keep the unsent workflow draft aligned with the desktop language. Existing
  // projects retain their saved language; this only affects the form request
  // that will choose its prompt pack when launched.
  useEffect(() => {
    setSession((current) => updateLongInitPayload(current, { language: locale }));
  }, [locale, restored]);

  useEffect(() => {
    let cancelled = false;
    void commandClient.listWorkflowPresets("long").then((presets) => {
      if (cancelled) return;
      setPresetRevisions(new Map(presets.map((preset) => [preset.name, preset.revision])));
      setSession((current) => ({
        ...current,
        presets: presets.map((preset) => ({
          id: preset.name,
          name: preset.name,
          payload: normalizeLongInitPayload(preset.payload),
        })),
      }));
    }).catch(() => setOperationNotice("无法载入长篇预设；请检查引擎连接。"));
    return () => { cancelled = true; };
  }, [commandClient]);

  useEffect(() => {
    const activePreset = session.presets.find((preset) => preset.id === session.activePresetId);
    if (activePreset === undefined) return;
    let cancelled = false;
    void commandClient.listWorkflowAiHistory("long", activePreset.name).then((entries) => {
      if (cancelled) return;
      setSession((current) => current.activePresetId !== activePreset.id ? current : {
        ...current,
        history: [
          ...current.history.filter((entry) => entry.presetId !== activePreset.id),
          ...entries.map((entry) => longHistoryFromEngine(entry, activePreset.id)),
        ],
      });
    }).catch(() => setOperationNotice("无法载入创作札记；当前会话记录仍可使用。"));
    return () => { cancelled = true; };
  }, [commandClient, session.activePresetId, session.presets]);

  const patch = useCallback(
    (p: LongInitPayloadPatch) => setSession((s) => updateLongInitPayload(s, p)),
    [],
  );

  const applyAiPatch = useCallback((
    operation: "generate" | "polish",
    candidate: Readonly<Record<string, unknown>>,
    changedKeys: readonly string[],
    context: WorkflowAiApplyContext,
  ) => {
    const updated = updateLongInitPayload(session, normalizeLongInitPatch({ ...candidate }));
    const boundName = session.presets.find((preset) => preset.id === session.activePresetId)?.name
      ?? autoAiPresetName(updated.payload.title, operation);
    const saved = saveLongInitPreset(updated, boundName, updated.payload);
    if (saved.error !== undefined) {
      setOperationNotice(saved.error);
      return;
    }
    const boundPreset = saved.session.presets.find((preset) => preset.id === saved.session.activePresetId);
    if (boundPreset === undefined) return;
    const next: LongInitSession = {
      ...saved.session,
      history: [{
        id: `long-ai-${operation}-${Date.now().toString(36)}`,
        label: operation === "generate" ? "AI 生成并预览" : "AI 定向润色",
        presetId: boundPreset.id,
        changedKeys: changedKeys.filter((key): key is keyof LongInitPayload => key in saved.session.payload),
        snapshot: saved.session.payload,
        timeLabel: historyTimeLabel(),
        userHint: context.userHint,
        selectedSuggestions: context.selectedSuggestions,
        creativeNote: context.creativeNote,
        creativeProfile: context.creativeProfile,
      }, ...saved.session.history.filter((entry) => entry.presetId !== boundPreset.id)].slice(0, 20),
    };
    setSession(next);
    setSelectedPresetId(boundPreset.id);
    void (async () => {
      const expectedRevision = presetRevisions.get(boundPreset.name);
      const persistedPreset = await commandClient.saveWorkflowPreset({
        kind: "save_workflow_preset",
        mode: "long",
        name: boundPreset.name,
        payload: JSON.parse(serializeLongInitPayload(boundPreset.payload)) as Readonly<Record<string, unknown>>,
        ...(expectedRevision === undefined ? {} : { expectedRevision }),
      });
      if (persistedPreset.status !== "saved") {
        setOperationNotice(`AI 变更已保留在本页，但预设持久化失败：${persistedPreset.message}`);
        return;
      }
      setPresetRevisions((current) => new Map(current).set(boundPreset.name, persistedPreset.revision ?? ""));
      const savedHistory = await commandClient.saveWorkflowAiHistory({
        kind: "save_workflow_ai_history",
        mode: "long",
        presetName: boundPreset.name,
        operation: operation === "generate" ? "generate" : "polish_applied",
        data: JSON.parse(serializeLongInitPayload(boundPreset.payload)) as Readonly<Record<string, unknown>>,
        hint: context.userHint,
        selectedSuggestions: context.selectedSuggestions,
        focusFields: context.focusFields,
        metadata: {
          acceptedFields: changedKeys,
          creativeNote: context.creativeNote,
          creativeProfile: context.creativeProfile,
          generationMode: context.generationMode,
          hardConstraints: context.hardConstraints,
        },
      });
      if (savedHistory.status !== "saved") {
        setOperationNotice(`AI 变更已保存，但创作札记未写入：${savedHistory.message}`);
        return;
      }
      const entries = await commandClient.listWorkflowAiHistory("long", boundPreset.name);
      setSession((current) => current.activePresetId !== boundPreset.id ? current : {
        ...current,
        history: [
          ...current.history.filter((entry) => entry.presetId !== boundPreset.id),
          ...entries.map((entry) => longHistoryFromEngine(entry, boundPreset.id)),
        ],
      });
    })().catch(() => setOperationNotice("AI 变更已保留在本页，但预设或创作札记持久化失败。"));
  }, [commandClient, presetRevisions, session]);

  const volumeInfo = useMemo(
    () => normalizeVolumeFields(payload.volumeMode, payload.chaptersPerVolume),
    [payload.volumeMode, payload.chaptersPerVolume],
  );

  // ── Dynamic button text (mirrors PySide6 update_progress) ──────────
  const {
    activeRunState,
    isFailed,
    isPaused,
    canResume,
    showResumePanel,
    showFailedPanel,
    submitLabel,
    copilotLabel,
    autorunLabel,
  } = longInitActionState({ activeRun, initRunning, resumable });

  // 共享步骤状态推导：与机杼任务卡/章台面板使用同一份 stages → 状态映射。
  const progress = taskProgressProjection({
    kind: "init_long", locale, isRunning: initRunning,
    currentStepLabel: activeRun?.currentStageLabel ?? "",
    reportedProgress: activeRun?.progressPercent,
    stages: activeRun?.stages ?? [],
  });
  const stepState = progress.stepState;
  // 失败态将当前步骤标记为 failed（原实现 active step 直接标红）。
  const failedStepKey = isFailed ? stepState.currentStepKey : "";

  // ── Preset operations ──────────────────────────────────────────────
  const handleLoadPreset = useCallback(() => {
    if (!selectedPreset) return;
    setSession((s) => loadLongInitPreset(s, selectedPreset.id));
    setOperationNotice(`已载入预设「${selectedPreset.name}」。`);
  }, [selectedPreset]);

  const handleSavePreset = useCallback(async (name: string) => {
    const existing = findLongInitPresetByName(session, name);
    const localResult = saveLongInitPreset(session, name, payload);
    if (localResult.error) {
      setOperationNotice(localResult.error);
      return;
    }
    try {
      const expectedRevision = presetRevisions.get(name);
      const result = await commandClient.saveWorkflowPreset({
        kind: "save_workflow_preset",
        mode: "long",
        name,
        payload: JSON.parse(serializeLongInitPayload(payload)) as Readonly<Record<string, unknown>>,
        ...(expectedRevision === undefined ? {} : { expectedRevision }),
      });
      if (result.status !== "saved" || result.revision === undefined) {
        setOperationNotice(result.message);
        return;
      }
      setPresetRevisions((current) => new Map(current).set(name, result.revision!));
      setSession(localResult.session);
      setSelectedPresetId(localResult.session.activePresetId ?? "");
      setOperationNotice(existing ? `预设「${name}」已更新并持久化。` : `预设「${name}」已持久化。`);
      setDialog(null);
    } catch (error) {
      setOperationNotice(error instanceof Error ? error.message : "保存预设失败。");
    }
  }, [commandClient, payload, presetRevisions, session]);

  const handleDeletePreset = useCallback(async () => {
    if (!selectedPreset) return;
    try {
      const expectedRevision = presetRevisions.get(selectedPreset.name);
      const result = await commandClient.deleteWorkflowPreset({
        kind: "delete_workflow_preset",
        mode: "long",
        name: selectedPreset.name,
        ...(expectedRevision === undefined ? {} : { expectedRevision }),
      });
      if (result.status !== "deleted") {
        setOperationNotice(result.message);
        return;
      }
      setSession((s) => deleteLongInitPreset(s, selectedPreset.id));
      setPresetRevisions((current) => {
        const next = new Map(current);
        next.delete(selectedPreset.name);
        return next;
      });
      setSelectedPresetId("");
      setOperationNotice(`预设「${selectedPreset.name}」已从引擎持久层删除。`);
      setDialog(null);
    } catch (error) {
      setOperationNotice(error instanceof Error ? error.message : "删除预设失败。");
    }
  }, [commandClient, presetRevisions, selectedPreset]);

  // ── Submit / Cancel / Restart ──────────────────────────────────────
  const handleReset = useCallback(() => {
    setSession((s) => resetLongInitSession(s, locale));
    setSelectedPresetId("");
    setOperationNotice("表单已清空。");
  }, [locale]);

  const handleSubmit = useCallback(
    async (mode: "create" | "autorun" | "copilot") => {
      const issues = validateLongInitPayload(payload);
      const activeRunProjectId = activeRun === null || activeRun === undefined
        ? ""
        : workflowRunProjectId(activeRun);
      const resumeProjectId = (
        (isFailed || isPaused ? activeRunProjectId : "")
        || resumableProject?.id
        || activeRunProjectId
        || payload.projectId
      ).trim();

      if (canResume && isCommandAvailable("continue_long_init")) {
        if (!resumeProjectId) {
          setOperationNotice("未找到可继续的项目，请刷新项目状态后重试。");
          return;
        }
        setOperationNotice("正在读取原始立项意图与已落盘检查点…");
        try {
          const fallbackInput = issues.length === 0
            ? { ...toInitLongWorkflowInput(payload, mode), projectId: resumeProjectId }
            : undefined;
          const result = await commandClient.continueLongInit({
            kind: "continue_long_init",
            projectId: resumeProjectId,
            runMode: mode,
            ...(fallbackInput === undefined ? {} : { fallbackPayload: fallbackInput }),
          });
          setOperationNotice(result.message);
          if (result.status === "accepted" || result.status === "already_running") {
            await onWorkflowStarted?.();
          }
        } catch (error) {
          setOperationNotice(error instanceof Error ? error.message : "长篇立项续传失败。");
        }
        return;
      }

      if (
        canResume
        && activeRun !== null
        && (isFailed || isPaused)
        && isCommandAvailable("resume_job")
      ) {
        try {
          const result = await commandClient.resumeJob({ kind: "resume_job", taskId: activeRun.id });
          setOperationNotice(result.message);
          if (result.status === "accepted" || result.status === "already_running") {
            await onWorkflowStarted?.();
          }
        } catch (error) {
          setOperationNotice(error instanceof Error ? error.message : "长篇立项续传失败。");
        }
        return;
      }

      if (issues.length > 0) {
        setDialog({ type: "validation", issues });
        return;
      }
      const preview = longInitLaunchPreview(payload, locale);
      setDialog({ type: "launch", preview });
      if (!isCommandAvailable("start_workflow")) {
        setOperationNotice("当前引擎未开放长篇启动能力，命令未提交。");
        return;
      }
      setOperationNotice("正在向引擎提交完整立项参数…");
      try {
        const input = toInitLongWorkflowInput(payload, mode);
        const result = await commandClient.startWorkflow({
          kind: "start_workflow",
          projectId: input.projectId,
          workflowType: "long_init",
          runMode: mode,
          idempotencyKey: crypto.randomUUID(),
          payload: input,
        });
        setOperationNotice(result.message);
        if (result.status === "accepted" || result.status === "already_running") {
          await onWorkflowStarted?.();
        }
      } catch (error) {
        setOperationNotice(error instanceof Error ? error.message : "长篇立项提交失败。");
      }
    },
    [
      activeRun,
      canResume,
      commandClient,
      isCommandAvailable,
      isFailed,
      isPaused,
      locale,
      onWorkflowStarted,
      payload,
      resumableProject,
    ],
  );

  const handleCancelInit = useCallback(async () => {
    if (!activeRun) return;
    try {
      const result = await commandClient.cancelJob({
        kind: "cancel_job",
        taskId: activeRun.id,
        reason: "用户在机杼长篇立项表单中请求终止",
      });
      setOperationNotice(result.message);
      if (result.status === "accepted") {
        onCancelInit?.(activeRun.id);
        await onWorkflowStarted?.();
      }
    } catch (error) {
      setOperationNotice(error instanceof Error ? error.message : "终止立项失败，任务仍保持原状态。");
    }
  }, [activeRun, commandClient, onCancelInit, onWorkflowStarted]);

  const handleRestartConfirm = useCallback(async () => {
    if (!isCommandAvailable("restart_long_init")) {
      setOperationNotice("当前 Engine 版本未开放重新立项能力；请先重启本地 Engine。");
      return;
    }
    try {
      await onRestartInit?.();
      setDialog(null);
      setOperationNotice("旧立项产物已清理，项目已回到新建状态；现在可重新创建长篇项目。");
    } catch (error) {
      setOperationNotice(error instanceof Error ? error.message : "重新立项未获引擎确认。");
    }
  }, [isCommandAvailable, onRestartInit]);

  const handlePreferencesChange = useCallback(
    (prefs: BlueprintElementPreferences) => patch({ blueprintElementPreferences: prefs }),
    [patch],
  );

  // ── Step artifact viewing (mirrors PySide6 _on_step_clicked) ────────
  const handleStepClick = useCallback(async (_index: number, step: PipelineStep) => {
    const projectId = activeRun === null ? "" : workflowRunProjectId(activeRun);
    if (!projectId) {
      setOperationNotice("项目目录尚不可用，请在任务完成后查看。");
      return;
    }
    try {
      const result = await engineClient.getStepArtifacts(projectId, "init_long", step.key);
      setArtifactDialog({ stepKey: step.key, stepLabel: pipelineStepLabel(step, locale), artifacts: result.artifacts });
    } catch (error) {
      setOperationNotice(error instanceof Error ? error.message : "获取步骤产物失败。");
    }
  }, [activeRun, engineClient, locale]);

  // ── Progress binding ─────────────────────────────────────────────
  const resumePercent = activeRun ? progress.progressPercent : resumableProject?.progressPercent ?? 0;
  const resumeHint = activeRun?.activityLabel || resumableProject?.progressLabel || "";

  return (
    <section className="long-init-panel" id="long-init-form">
      <div className="section-heading">
        <h2>{"立项初始化"}</h2>
        <p>{"根据前提生成世界观、角色与大纲，之后去章台逐章续写。"}</p>
      </div>

      {/* Resume / Failed panel */}
      {(showResumePanel || showFailedPanel) && (
        <div className="long-init-resume">
          <p className="long-init-resume-title">
            {showFailedPanel
              ? `上次立项中断：${activeRun?.title ?? ""}`
              : resumable && activeRun === null
                ? `检测到可继续项目：${resumableProject?.title ?? resumableProject?.id ?? "长篇项目"}`
                : `当前任务进度：${activeRun?.title ?? "立项进行中"}`}
          </p>
          <div className="long-init-progress-line">
            <span className="long-init-progress-track">
              <span className="long-init-progress-fill" style={{ width: `${resumePercent}%` }} />
            </span>
            <strong className="long-init-progress-value">{resumePercent}%</strong>
          </div>
          <p className="long-init-resume-hint">
            {showFailedPanel
              ? "已取消或失败。继续时会恢复原始立项参数，并复用已校验的落盘产物。"
              : resumable && activeRun === null
                ? `${resumeHint ? `${resumeHint}。` : "上次立项已停在可恢复节点。"} 继续会沿用已落盘的立项参数；若要替换参数，请先重新立项。`
                : resumeHint}
          </p>
        </div>
      )}

      {/* PresetToolbar (mirrors PySide6 PresetToolbar) */}
      <fieldset className="long-init-form-body" disabled={initRunning}>
        <div className="long-init-toolbar">
          <select
            className="long-init-toolbar-select"
            onChange={(e) => setSelectedPresetId(e.target.value)}
            value={selectedPresetId}
          >
            <option value="">{"选择已保存的配置…"}</option>
            {session.presets.map((p) => (
              <option key={p.id} value={p.id}>{p.name}</option>
            ))}
          </select>
          <button className="long-init-toolbar-btn" disabled={!selectedPreset} onClick={handleLoadPreset} type="button">{"载入"}</button>
          <button className="long-init-toolbar-btn" disabled={!selectedPreset} onClick={() => selectedPreset && setDialog({ type: "save-preset", suggestedName: selectedPreset.name })} type="button">{"更新"}</button>
          <button className="long-init-toolbar-btn" disabled={!selectedPreset} onClick={() => selectedPreset && setDialog({ type: "delete-preset", presetId: selectedPreset.id, presetName: selectedPreset.name })} type="button">{"删除"}</button>
          <button className="long-init-toolbar-btn" onClick={() => setDialog({ type: "import" })} type="button">{"导入"}</button>
          <button className="long-init-toolbar-btn" onClick={() => setDialog({ type: "export", content: serializeLongInitPayload(payload) })} type="button">{"导出"}</button>
        </div>
        <div className="long-init-ai-bar">
          <div className="long-init-ai-copy">
            <strong>AI 共创</strong>
            <span>先生成候选，再逐项预览并应用；不会直接覆盖当前表单。</span>
          </div>
          <div className="long-init-ai-actions">
            <WorkflowAiAssistant
              available={aiPreviewAvailable}
              className="workflow-ai-inline-actions"
              commandClient={commandClient}
              disabled={initRunning}
              fields={longAiFields}
              mode="long"
              onApply={applyAiPatch}
              onNotice={setOperationNotice}
              payload={payload as unknown as Readonly<Record<string, unknown>>}
            />
            <button className="long-init-ai-history-button" onClick={() => setDialog({ type: "history" })} type="button">创作札记</button>
          </div>
        </div>
        <p className="long-init-toolbar-hint">{"预设负责复用配置，AI 共创负责生成可审阅的候选；应用后的草稿会自动保存。"}</p>

        {/* Core field cards */}
        <FieldCardSection
          description="点击编辑"
          fields={coreFields}
          group="core"
          onOpen={(id) => setDialog({ type: "fields", group: "core", selectedId: id })}
          payload={payload}
          title="核心梗概"
        />

        {/* Parameters */}
        <div className="long-init-params-row">
          <label className="long-init-param">
            <span className="long-init-param-label">{"题材"}</span>
            <input className="long-init-param-input" onChange={(e) => patch({ genre: e.target.value })} placeholder="悬疑、都市…" type="text" value={payload.genre} />
          </label>
          <label className="long-init-param">
            <span className="long-init-param-label">{"基调"}</span>
            <select className="long-init-param-select" onChange={(e) => patch({ tone: e.target.value as LongTone })} value={payload.tone}>
              {longToneOptions.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
            </select>
          </label>
          <label className="long-init-param">
            <span className="long-init-param-label">{"总章节"}</span>
            <input className="long-init-param-input is-number" max={1000} min={1} onChange={(e) => patch({ totalChapters: Number(e.target.value) || 24 })} type="number" value={payload.totalChapters} />
          </label>
          <label className="long-init-param">
            <span className="long-init-param-label">{"每章字数"}</span>
            <input className="long-init-param-input is-number" max={20000} min={500} onChange={(e) => patch({ wordsPerChapter: Number(e.target.value) || 4500 })} type="number" value={payload.wordsPerChapter} />
          </label>
          <label className="long-init-param">
            <span className="long-init-param-label">{"分卷"}</span>
            <select className="long-init-param-select" onChange={(e) => patch({ volumeMode: e.target.value as VolumeMode })} value={payload.volumeMode}>
              {volumeModeOptions.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
            </select>
          </label>
          <label className="long-init-param">
            <span className="long-init-param-label">{"每卷章数"}</span>
            <input className="long-init-param-input is-number" disabled={payload.volumeMode !== "on"} max={500} min={0} onChange={(e) => patch({ chaptersPerVolume: Number(e.target.value) || 0 })} type="number" value={payload.volumeMode === "on" ? payload.chaptersPerVolume : 0} />
          </label>
          <label className="long-init-param">
            <span className="long-init-param-label">{"创意探索"}</span>
            <select className="long-init-param-select" onChange={(e) => patch({ creativeExploration: e.target.value as CreativeExploration })} value={payload.creativeExploration}>
              <option value="adaptive">自适应 2+1</option>
              <option value="single">单路径（兼容）</option>
            </select>
          </label>
          <label className="long-init-param">
            <span className="long-init-param-label">{"规划承诺"}</span>
            <select className="long-init-param-select" onChange={(e) => patch({ planningCommitment: e.target.value as PlanningCommitment })} value={payload.planningCommitment}>
              <option value="full">全书规划（默认）</option>
              <option value="progressive">渐进规划（先规划前 10 章）</option>
            </select>
          </label>
        </div>
        <p className="long-init-volume-hint">{volumeInfo.hint}</p>
        <p className="long-init-volume-hint">{payload.planningCommitment === "full"
          ? `按总章数分批生成全部 ${payload.totalChapters} 章大纲与契约；初始化耗时和模型用量相应增加。`
          : `全书目标仍为 ${payload.totalChapters} 章；先规划前 ${Math.min(10, payload.totalChapters)} 章，其中前 ${Math.min(5, payload.totalChapters)} 章细化并同步契约，后续随写作推进，也可在卷帙补齐。`}</p>

        {/* Research collapsible */}
        <div className="long-init-collapsible">
          <button aria-expanded={researchExpanded} className="long-init-collapsible-toggle" onClick={() => setResearchExpanded((v) => !v)} type="button">
            <span className={`long-init-chevron ${researchExpanded ? "is-open" : ""}`} />
            {"联网资料检索"}
          </button>
          {researchExpanded && (
            <div className="long-init-collapsible-body">
              <div className="long-init-research-top">
                <label className="long-init-checkbox">
                  <input checked={payload.researchEnabled} onChange={(e) => patch({ researchEnabled: e.target.checked })} type="checkbox" />
                  {"本次启用"}
                </label>
                <label className="long-init-param">
                  <span className="long-init-param-label">{"后端"}</span>
                  <select className="long-init-param-select" onChange={(e) => patch({ researchProvider: e.target.value as ResearchProvider })} value={payload.researchProvider}>
                    {researchProviderOptions.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
                  </select>
                </label>
              </div>
              <textarea className="long-init-textarea is-research" onChange={(e) => patch({ researchQueryHint: e.target.value })} placeholder="可选：检索方向" value={payload.researchQueryHint} />
            </div>
          )}
        </div>

        {/* Advanced field cards */}
        <FieldCardSection
          description="可留空"
          fields={advancedFields}
          group="advanced"
          onOpen={(id) => setDialog({ type: "fields", group: "advanced", selectedId: id })}
          payload={payload}
          title="高级设定"
        />

        {/* Blueprint element preferences */}
        <div className="long-init-collapsible">
          <button aria-expanded={preferencesExpanded} className="long-init-collapsible-toggle" onClick={() => setPreferencesExpanded((v) => !v)} type="button">
            <span className={`long-init-chevron ${preferencesExpanded ? "is-open" : ""}`} />
            {"叙事要素偏好"}
          </button>
          {preferencesExpanded && (
            <div className="long-init-collapsible-body">
              <BlueprintElementPreferencePanel genreText={payload.genre} onChange={handlePreferencesChange} preferences={payload.blueprintElementPreferences} />
            </div>
          )}
        </div>
      </fieldset>

      {/* Action buttons (outside fieldset so cancel/restart always work) */}
      <div className="long-init-actions">
        <button className="button button-secondary" disabled={initRunning} onClick={() => setDialog({ type: "save-preset", suggestedName: payload.title.trim() || "长篇预设" })} type="button">{"存为预设"}</button>
        <button className="button button-secondary" disabled={initRunning} onClick={handleReset} type="button">{"清空表单"}</button>
        <button className="button button-primary" disabled={initRunning || !isCommandAvailable("start_workflow")} onClick={() => void handleSubmit("create")} type="button">{submitLabel}</button>
        <button className="button button-secondary" disabled={initRunning || !isCommandAvailable("start_workflow")} onClick={() => void handleSubmit("copilot")} type="button">{copilotLabel}</button>
        <button className="button button-primary" disabled={initRunning || !isCommandAvailable("start_workflow")} onClick={() => void handleSubmit("autorun")} type="button">{autorunLabel}</button>
        {initRunning && (
          <button className="button button-danger" onClick={() => void handleCancelInit()} type="button">{"终止立项"}</button>
        )}
        {(hasFailedInit || canResume) && !initRunning && (
          <button
            className="button button-secondary"
            disabled={!isCommandAvailable("restart_long_init")}
            onClick={() => setDialog({ type: "restart-confirm" })}
            title={isCommandAvailable("restart_long_init") ? "清理旧立项产物并从头开始" : "请先重启本地 Engine 以加载重新立项能力"}
            type="button"
          >{"重新立项"}</button>
        )}
      </div>

      {/* Step indicator */}
      <div className="long-init-steps">
        <StepIndicatorRow
          {...stepState}
          completedSteps={stepState.completedSteps}
          currentStepKey={stepState.currentStepKey}
          failedStepKey={failedStepKey}
          isComplete={stepState.isComplete}
          onStepClick={handleStepClick}
          steps={progress.steps}
        />
      </div>

      {operationNotice && <p aria-live="polite" className="studio-operation-notice">{operationNotice}</p>}

      {/* Draft autosave status */}
      {(restored || lastSavedAt !== null) && (
        <p className="long-init-draft-status">
          {restored && "已恢复上次草稿"}{restored && lastSavedAt !== null && " · "}{lastSavedAt !== null && `自动保存 ${new Date(lastSavedAt).toLocaleTimeString()}`}
        </p>
      )}
      {lastSaveError !== null && (
        <p className="long-init-draft-status" role="alert">
          草稿自动保存失败：{lastSaveError}；当前输入仍保留在页面中。
        </p>
      )}

      {/* Dialogs */}
      {dialog?.type === "fields" && (
        <FieldEditDialog
          fields={dialog.group === "core" ? coreFields : advancedFields}
          onClose={() => setDialog(null)}
          onApply={(p) => { patch(p); setDialog(null); }}
          payload={payload}
          selectedId={dialog.selectedId}
          title={dialog.group === "core" ? "核心梗概" : "高级设定"}
        />
      )}
      {dialog?.type === "launch" && (
        <AppDialog description="以下参数将提交给引擎" onClose={() => setDialog(null)} title="启动确认">
          <div className="long-init-launch-preview">
            <dl>{Object.entries(dialog.preview).map(([key, value]) => (
              <div key={key} className="long-init-launch-row"><dt>{key}</dt><dd>{String(value)}</dd></div>
            ))}</dl>
            <p className="long-init-launch-note">{"完整参数已提交给引擎；任务卡将以引擎状态和流式事件为准刷新。"}</p>
          </div>
          <div className="dialog-actions"><button className="button button-secondary" onClick={() => setDialog(null)} type="button">{"关闭"}</button></div>
        </AppDialog>
      )}
      {dialog?.type === "validation" && (
        <AppDialog description="请修正以下问题后重试" onClose={() => setDialog(null)} title="校验未通过">
          <ul className="long-init-validation-list">{dialog.issues.map((issue) => <li key={issue}>{issue}</li>)}</ul>
          <div className="dialog-actions"><button className="button button-primary" onClick={() => setDialog(null)} type="button">{"知道了"}</button></div>
        </AppDialog>
      )}
      {dialog?.type === "save-preset" && (
        <SavePresetDialog onClose={() => setDialog(null)} onSave={handleSavePreset} suggestedName={dialog.suggestedName} />
      )}
      {dialog?.type === "delete-preset" && (
        <AppDialog confirmLabel="删除" description={`确定删除预设「${dialog.presetName}」？`} onClose={() => setDialog(null)} onConfirm={handleDeletePreset} title="确认删除" tone="danger" />
      )}
      {dialog?.type === "restart-confirm" && (
        <AppDialog confirmLabel="确认重新立项" description="此操作将清除当前项目所有立项阶段产物（世界观、角色、大纲等），从头开始。已完成的章节不受影响。" onClose={() => setDialog(null)} onConfirm={handleRestartConfirm} title="重新立项" tone="danger" />
      )}
      {dialog?.type === "history" && (
        <AppDialog
          className="long-init-history-dialog"
          confirmLabel="关闭"
          description="AI 候选只有在你确认应用后才会进入札记；可随时恢复到任一已应用版本。"
          onClose={() => setDialog(null)}
          onConfirm={() => undefined}
          size="wide"
          title="创作札记"
        >
          <div className="long-init-history-list">
            <div className="long-init-history-actions">
              <strong>「{historyPresetName}」的 AI 构思与润色札记</strong>
              <button className="button button-secondary" disabled={historyEntries.length === 0} onClick={() => setDialog({ type: "history-clear-confirm" })} type="button">清空札记</button>
            </div>
            {historyEntries.length === 0 ? (
              <p>暂无创作札记。完成一次 AI 生成或定向润色并应用字段后，这里会留下可恢复版本。</p>
            ) : historyEntries.map((entry) => (
              <article key={entry.id}>
                <div>
                  <strong>{entry.timeLabel ? `${entry.timeLabel}　${entry.label}` : entry.label}</strong>
                  <span>{entry.changedKeys.map((key) => longAiFieldLabels.get(key) ?? key).join("、")}</span>
                  {historyDetails(entry).map((detail) => <small key={detail}>{detail}</small>)}
                </div>
                <button
                  className="button button-secondary"
                  onClick={() => {
                    setSession((current) => ({ ...current, payload: entry.snapshot }));
                    setOperationNotice(`已恢复「${entry.label}」版本，草稿将自动保存。`);
                    setDialog(null);
                  }}
                  type="button"
                >
                  恢复此版本
                </button>
              </article>
            ))}
          </div>
        </AppDialog>
      )}
      {dialog?.type === "history-clear-confirm" && (
        <AppDialog confirmLabel="清空札记" description="确定清空当前预设的所有 AI 构思与润色札记吗？此操作不会修改表单内容或预设配置。" onClose={() => setDialog({ type: "history" })} onConfirm={() => { const activePreset = session.presets.find((preset) => preset.id === session.activePresetId); setSession((current) => ({ ...current, history: current.history.filter((entry) => entry.presetId !== historyPresetId) })); if (activePreset !== undefined) void commandClient.clearWorkflowAiHistory("long", activePreset.name).then((result) => setOperationNotice(result.message)).catch(() => setOperationNotice("本页札记已清空，但引擎持久化失败。")); setDialog(null); }} title="清空创作札记" tone="danger" />
      )}
      {dialog?.type === "export" && (
        <AppDialog confirmLabel="关闭" description="只读 JSON 预览" onClose={() => setDialog(null)} onConfirm={() => setDialog(null)} title="导出长篇配置">
          <textarea className="long-init-textarea is-dialog" readOnly value={dialog.content} />
        </AppDialog>
      )}
      {dialog?.type === "import" && (
        <ImportDialog onClose={() => setDialog(null)} onImport={(parsed) => { patch(parsed); setDialog(null); setOperationNotice("已导入配置。"); }} />
      )}
      {artifactDialog && (
        <WorkflowArtifactDialog
          artifacts={artifactDialog.artifacts}
          {...(artifactDialog.candidatePaths ? { candidatePaths: artifactDialog.candidatePaths } : {})}
          {...(artifactDialog.emptyHint ? { emptyHint: artifactDialog.emptyHint } : {})}
          onClose={() => setArtifactDialog(null)}
          stageLabel={artifactDialog.stepLabel}
        />
      )}
    </section>
  );
}
