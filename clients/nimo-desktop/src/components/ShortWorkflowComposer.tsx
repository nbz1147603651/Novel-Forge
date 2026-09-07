import { useCallback, useEffect, useRef, useState } from "react";

import type { EngineClient, EngineCommandClient, StepArtifactFile, WorkflowAiHistoryEntry, WorkflowRunView } from "@nimo/engine-contracts";

import { BlueprintElementPreferencePanel } from "./BlueprintElementPreferencePanel";
import {
  pipelineStepLabel,
  RUN_SHORT_STEPS,
  StepIndicatorRow,
  stepIndicatorStateFromStages,
  type PipelineStep,
} from "./StepIndicatorRow";
import {
  applyShortWorkflowPreview,
  createShortWorkflowSession,
  deleteShortWorkflowPreset,
  emptyShortWorkflowPayload,
  findShortWorkflowPresetByName,
  historyForShortWorkflowPreset,
  loadShortWorkflowPreset,
  normalizeShortWorkflowPayload,
  parseShortWorkflowJson,
  resetShortWorkflowSession,
  saveShortWorkflowPreset,
  serializeShortWorkflowPayload,
  shortWorkflowPayloadForLocale,
  shortLanguageOptions,
  shortToneOptions,
  shortWorkflowLaunchPreview,
  shortWritingModeOptions,
  toRunShortWorkflowInput,
  updateShortWorkflowPayload,
  validateShortWorkflowPayload,
  type ShortWorkflowHistoryEntry,
  type ShortWorkflowHistoryMetadata,
  type ShortWorkflowKey,
  type ShortWorkflowPayload,
  type ShortWorkflowPayloadPatch,
  type ShortWorkflowSession,
  type ShortWorkflowTextKey,
} from "../lib/short-workflow-session";
import { useLocale } from "../lib/i18n";
import { useDraftAutosave } from "../lib/workflow-draft-autosave";
import { workflowRunProjectId } from "../lib/workflow-run-session";
import { AppDialog } from "./AppDialog";
import { WorkflowArtifactDialog } from "./WorkflowArtifactDialog";
import { OverlaySurface } from "./OverlaySurface";
import { useEngineRuntime } from "../lib/engine-runtime-context";
import { WorkflowAiAssistant, type WorkflowAiApplyContext, type WorkflowAiField } from "./WorkflowAiAssistant";
import {
  advancedFields,
  coreFields,
  type FieldDefinition,
  type FieldGroup,
} from "./workflow/short/fields";
import {
  BlueprintCollapsibleSection,
  FieldCards,
  ShortFieldEditorDialog,
} from "./workflow/short/FieldDialogs";
import {
  ImportJsonDialog,
  SavePresetDialog,
} from "./workflow/short/PresetDialogs";

type ShortDialog =
  | { readonly type: "fields"; readonly group: FieldGroup; readonly selectedId: string }
  | { readonly type: "save"; readonly payload: ShortWorkflowPayload; readonly suggestedName: string }
  | { readonly type: "overwrite"; readonly name: string; readonly payload: ShortWorkflowPayload }
  | { readonly type: "delete"; readonly presetId: string; readonly presetName: string }
  | { readonly type: "import" }
  | { readonly type: "import-choice"; readonly payload: ShortWorkflowPayloadPatch }
  | { readonly type: "export-choice" }
  | { readonly type: "export"; readonly content: string; readonly title: string }
  | { readonly type: "launch"; readonly preview: Readonly<Record<string, string | number>> }
  | { readonly type: "validation"; readonly issues: readonly string[] }
  | { readonly type: "history" }
  | { readonly type: "history-clear-confirm" }
  | { readonly type: "history-restore"; readonly entry: ShortWorkflowHistoryEntry }
  | { readonly type: "no-preset-bound" };

const keyLabels: Readonly<Record<ShortWorkflowKey, string>> = {
  theme: "故事主题",
  genre: "题材",
  tone: "基调",
  lengthTarget: "目标字数",
  maxEditRounds: "最多修订轮次",
  segmentTriggerWords: "开始分段字数",
  writingMode: "写作模式",
  title: "作品名",
  language: "语言",
  charactersHint: "人物提示",
  worldHint: "世界观 / 场景提示",
  conflictHint: "核心冲突提示",
  povHint: "叙事视角",
  openingStyle: "开篇方式",
  endingStyle: "结尾方式",
  extraInstructions: "额外创作指令",
  researchEnabled: "联网资料",
  researchProvider: "检索后端",
  researchQueryHint: "必须核实的事实",
  projectId: "项目 ID",
  blueprintElementPreferences: "叙事要素偏好",
};

const shortAiFields: readonly WorkflowAiField[] = [
  "theme",
  "genre",
  "tone",
  "lengthTarget",
  "title",
  "language",
  "charactersHint",
  "worldHint",
  "conflictHint",
  "povHint",
  "openingStyle",
  "endingStyle",
  "extraInstructions",
].map((key) => ({ key, label: keyLabels[key as ShortWorkflowKey] }));

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

function shortHistoryDetails(entry: ShortWorkflowHistoryEntry): readonly string[] {
  const details = [
    entry.userHint ? `方向：${entry.userHint}` : "",
    entry.selectedSuggestions?.length ? `灵感：${entry.selectedSuggestions.join("、")}` : "",
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

function asStringRecord(value: unknown): Readonly<Record<string, string>> | undefined {
  if (value === null || typeof value !== "object" || Array.isArray(value)) return undefined;
  const result = Object.entries(value).filter((item): item is [string, string] => typeof item[1] === "string");
  return result.length === 0 ? undefined : Object.fromEntries(result);
}

function asUnknownRecord(value: unknown): Readonly<Record<string, unknown>> | undefined {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? value as Readonly<Record<string, unknown>>
    : undefined;
}

function historyLabel(operation: string): string {
  if (operation.startsWith("polish")) return "AI 定向润色";
  return "AI 生成并预览";
}

function shortHistoryFromEngine(entry: WorkflowAiHistoryEntry, presetId: string): ShortWorkflowHistoryEntry {
  const metadata = entry.metadata ?? {};
  const creativeNote = asUnknownRecord(metadata.creativeNote ?? metadata.creative_note);
  const creativeProfile = asStringRecord(metadata.creativeProfile ?? metadata.creative_profile);
  const accepted = Array.isArray(metadata.acceptedFields)
    ? metadata.acceptedFields
    : Array.isArray(metadata.accepted_fields) ? metadata.accepted_fields : Object.keys(entry.data);
  const changedKeys = accepted.filter(
    (key): key is ShortWorkflowKey => typeof key === "string" && key in keyLabels,
  );
  return {
    id: entry.id,
    label: historyLabel(entry.operation),
    presetId,
    changedKeys,
    snapshot: normalizeShortWorkflowPayload(entry.data),
    timeLabel: entry.timestamp,
    ...(entry.hint === undefined ? {} : { userHint: entry.hint }),
    ...(entry.selectedSuggestions === undefined ? {} : { selectedSuggestions: entry.selectedSuggestions }),
    ...(creativeNote === undefined ? {} : { creativeNote }),
    ...(creativeProfile === undefined ? {} : { creativeProfile }),
  };
}
export function ShortWorkflowComposer({ commandClient, engineClient, externalTemplateExportRequest = 0, activeRun = null, onWorkflowStarted }: ShortWorkflowComposerProps) {
  const { isCommandAvailable } = useEngineRuntime();
  const locale = useLocale();
  const [session, setSession] = useState<ShortWorkflowSession>(() =>
    createShortWorkflowSession(shortWorkflowPayloadForLocale(locale)),
  );
  const [selectedPresetId, setSelectedPresetId] = useState("");
  const [launchState, setLaunchState] = useState<"idle" | "launching" | "launched" | "error">("idle");
  const [dialog, setDialog] = useState<ShortDialog | null>(null);
  const [notice, setNotice] = useState("正在从引擎载入草稿与预设…");
  const [persistenceReady, setPersistenceReady] = useState(false);
  const [presetRevisions, setPresetRevisions] = useState<ReadonlyMap<string, string>>(() => new Map());
  const [artifactDialog, setArtifactDialog] = useState<{
    readonly stepKey: string;
    readonly stepLabel: string;
    readonly artifacts: readonly StepArtifactFile[];
    readonly candidatePaths?: readonly { readonly label: string; readonly path: string }[];
    readonly emptyHint?: string;
  } | null>(null);
  const handledExportRequest = useRef(externalTemplateExportRequest);
  // 防止「清空表单」与 saveNamedPreset 之间的竞态：保存未落定前若
  // 用户已清空表单，await 完成后的 setSession 仍会把 activePresetId
  // 写回新建的预设，破坏清空语义。bumpSaveToken 每次保存自增；清空表
  // 单也自增一次，saveNamedPreset await 完成后仅在 token 未变时落定。
  const saveTokenRef = useRef(0);
  const workflowRunning = activeRun !== null && (activeRun.stateLabel === "执行中" || activeRun.stateLabel === "排队中");
  const formBusy = launchState === "launching" || workflowRunning;
  const startAvailable = isCommandAvailable("start_workflow");
  // 共享步骤状态推导：与机杼任务卡/章台面板使用同一份 stages → 状态映射。
  const stepState = stepIndicatorStateFromStages(activeRun?.stages ?? []);
  const aiPreviewAvailable = isCommandAvailable("generate_workflow_fields");

  // ── Step artifact viewing (mirrors PySide6 _on_step_clicked) ────────
  const handleStepClick = useCallback(async (_index: number, step: PipelineStep) => {
    const projectId = activeRun === null ? "" : workflowRunProjectId(activeRun);
    if (!projectId) {
      setNotice("项目目录尚不可用，请在任务完成后查看。");
      return;
    }
    try {
      const result = await engineClient.getStepArtifacts(projectId, "run_short", step.key);
      setArtifactDialog({ stepKey: step.key, stepLabel: pipelineStepLabel(step, locale), artifacts: result.artifacts });
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "获取步骤产物失败。");
    }
  }, [activeRun, engineClient, locale]);

  const persistencePayload = (payload: ShortWorkflowPayload): Readonly<Record<string, unknown>> =>
    JSON.parse(serializeShortWorkflowPayload(payload)) as Record<string, unknown>;

  const { restored, lastSaveError, restoreFinished } = useDraftAutosave<Readonly<Record<string, unknown>>>({
    commandClient,
    mode: "short",
    payload: session.payload as unknown as Readonly<Record<string, unknown>>,
    onRestore: (draftPayload) => {
      setSession((current) => ({
        ...current,
        payload: normalizeShortWorkflowPayload(draftPayload),
      }));
      setNotice("已恢复引擎草稿。");
    },
    enabled: persistenceReady,
  });

  useEffect(() => {
    let cancelled = false;
    void commandClient.listWorkflowPresets("short").then((presets) => {
      if (cancelled) return;
      setPresetRevisions(new Map(presets.map((preset) => [preset.name, preset.revision])));
      setSession((current) => ({
        ...current,
        presets: presets.map((preset) => ({
          id: preset.name,
          name: preset.name,
          payload: normalizeShortWorkflowPayload(preset.payload),
        })),
      }));
      setPersistenceReady(true);
      setNotice("引擎已连接；正在恢复草稿。");
    }).catch(() => {
      if (cancelled) return;
      setPersistenceReady(true);
      setNotice("无法载入预设；草稿恢复仍会继续尝试。请检查引擎连接。");
    });
    return () => { cancelled = true; };
  }, [commandClient]);

  // An interface-language switch is also the default language switch for the
  // next workflow request. The engine maps `language=en` to the stable English
  // prompt pack, including the system preamble and format contract.
  useEffect(() => {
    if (!persistenceReady) return;
    setSession((current) => updateShortWorkflowPayload(current, { language: locale }));
  }, [locale, persistenceReady]);

  useEffect(() => {
    if (externalTemplateExportRequest <= handledExportRequest.current) return;
    handledExportRequest.current = externalTemplateExportRequest;
    setDialog({ type: "export", title: "短篇模板 JSON", content: serializeShortWorkflowPayload(emptyShortWorkflowPayload) });
  }, [externalTemplateExportRequest]);

  const updatePayload = (patch: ShortWorkflowPayloadPatch) => setSession((current) => updateShortWorkflowPayload(current, patch));
  const selectedPreset = session.presets.find((preset) => preset.id === selectedPresetId);
  const historyEntries = historyForShortWorkflowPreset(session);
  const importPreview = dialog?.type === "import-choice"
    ? normalizeShortWorkflowPayload({ ...session.payload, ...dialog.payload })
    : undefined;

  useEffect(() => {
    const presetId = session.activePresetId;
    if (presetId === null) return;
    let cancelled = false;
    void commandClient.listWorkflowAiHistory("short", presetId).then((entries) => {
      if (cancelled) return;
      setSession((current) => current.activePresetId !== presetId ? current : {
        ...current,
        history: [
          ...current.history.filter((entry) => entry.presetId !== presetId),
          ...entries.map((entry) => shortHistoryFromEngine(entry, presetId)),
        ],
      });
    }).catch(() => setNotice("无法载入创作札记；当前会话记录仍可使用。"));
    return () => { cancelled = true; };
  }, [commandClient, session.activePresetId]);

  const beginSave = (payload: ShortWorkflowPayload, suggestedName = "") => {
    setDialog({ type: "save", payload, suggestedName });
  };
  const saveNamedPreset = async (name: string, payload: ShortWorkflowPayload) => {
    const existing = findShortWorkflowPresetByName(session, name);
    if (existing !== undefined) {
      setDialog({ type: "overwrite", name, payload });
      return;
    }
    const tokenAtStart = saveTokenRef.current;
    const normalizedName = name.trim();
    try {
      const persisted = await commandClient.saveWorkflowPreset({
        kind: "save_workflow_preset",
        mode: "short",
        name: normalizedName,
        payload: persistencePayload(payload),
      });
      if (persisted.status !== "saved") {
        setNotice(persisted.message);
        return;
      }
      setPresetRevisions((current) => new Map(current).set(normalizedName, persisted.revision ?? ""));
    } catch {
      setNotice("预设保存失败；请检查引擎连接后重试。");
      return;
    }
    if (saveTokenRef.current !== tokenAtStart) {
      // 「清空表单」之类的破坏性操作已在 await 期间触发；不覆盖 session。
      // 但 selectOption 仍然要能选回新预设：把新 preset 追加到 session.presets。
      setSession((current) => {
        if (current.presets.some((preset) => preset.name === normalizedName)) return current;
        return {
          ...current,
          presets: [
            ...current.presets,
            { id: `short-preset:${normalizedName}`, name: normalizedName, payload: normalizeShortWorkflowPayload(payload) },
          ],
        };
      });
      return;
    }
    const result = saveShortWorkflowPreset(session, name, payload);
    if (result.error !== undefined) {
      setNotice(result.error);
      setDialog(null);
      return;
    }
    // 镜像 PySide6：写入完成后立即关闭弹窗，避免「清空表单」之类
    // 后续操作与异步 setSession 之间产生竞态——否则清空可能在 save
    // 完成前先重置 activePresetId，save 落定后又把它再次写回。
    setDialog(null);
    setSession(result.session);
    setSelectedPresetId(result.session.activePresetId ?? "");
    setNotice(`预设「${name.trim()}」已保存在当前前端会话。`);
  };
  const commitOverwrite = async (name: string, payload: ShortWorkflowPayload) => {
    try {
      const expectedRevision = presetRevisions.get(name.trim());
      const persisted = await commandClient.saveWorkflowPreset({
        kind: "save_workflow_preset",
        mode: "short",
        name: name.trim(),
        payload: persistencePayload(payload),
        ...(expectedRevision === undefined ? {} : { expectedRevision }),
      });
      if (persisted.status !== "saved") {
        setNotice(persisted.message);
        return;
      }
      setPresetRevisions((current) => new Map(current).set(name.trim(), persisted.revision ?? ""));
    } catch {
      setNotice("预设更新失败；请重新载入后重试。");
      return;
    }
    const result = saveShortWorkflowPreset(session, name, payload);
    setSession(result.session);
    setSelectedPresetId(result.session.activePresetId ?? "");
    setNotice(`预设「${name.trim()}」已更新，旧版本已由引擎备份。`);
    setDialog(null);
  };
  const deletePersistedPreset = async (presetId: string, presetName: string) => {
    try {
      const expectedRevision = presetRevisions.get(presetName);
      const result = await commandClient.deleteWorkflowPreset({
        kind: "delete_workflow_preset",
        mode: "short",
        name: presetName,
        ...(expectedRevision === undefined ? {} : { expectedRevision }),
      });
      if (result.status !== "deleted" && result.status !== "not_found") {
        setNotice(result.message);
        return;
      }
      setSession((current) => deleteShortWorkflowPreset(current, presetId));
      setPresetRevisions((current) => {
        const next = new Map(current);
        next.delete(presetName);
        return next;
      });
      setSelectedPresetId("");
      setNotice(`预设「${presetName}」已从持久化存储删除。`);
      setDialog(null);
    } catch {
      setNotice("预设删除失败；请检查引擎连接后重试。");
    }
  };
  const requestLaunch = async () => {
    const issues = validateShortWorkflowPayload(session.payload);
    if (issues.length > 0) {
      setDialog({ type: "validation", issues });
      return;
    }
    // Show launch preview dialog first
    const preview = shortWorkflowLaunchPreview(session.payload, locale);
    setDialog({ type: "launch", preview });
    setLaunchState("launching");
    try {
      const workflowPayload = toRunShortWorkflowInput(session.payload);
      const result = await commandClient.startWorkflow({
        kind: "start_workflow",
        projectId: workflowPayload.projectId,
        workflowType: "short",
        runMode: "create",
        idempotencyKey: globalThis.crypto?.randomUUID?.() ?? `short-${Date.now().toString(36)}`,
        payload: workflowPayload,
      });
      setNotice(result.message);
      setLaunchState(result.status === "accepted" || result.status === "already_running" ? "launched" : "error");
      if (result.status === "accepted" || result.status === "already_running") {
        await onWorkflowStarted?.();
      }
    } catch {
      setLaunchState("error");
      setNotice("启动短篇创作命令失败；请检查引擎连接后重试。");
    }
  };
  const applyPreview = (
    operation: "generate" | "polish",
    label: string,
    patch: ShortWorkflowPayloadPatch,
    context: WorkflowAiApplyContext,
  ) => {
    const metadata: ShortWorkflowHistoryMetadata = {
      timeLabel: historyTimeLabel(),
      userHint: context.userHint,
      selectedSuggestions: context.selectedSuggestions,
      creativeNote: context.creativeNote,
      creativeProfile: context.creativeProfile,
    };
    const result = applyShortWorkflowPreview(session, label, patch, metadata);
    setSession(result.session);
    setSelectedPresetId(result.session.activePresetId ?? "");
    const bindingMessage = result.autoSavedPresetName === undefined
      ? "已同步当前预设草稿。"
      : `已创建待持久化预设「${result.autoSavedPresetName}」。`;
    setNotice(`已应用「${label}」中的 ${Object.keys(patch).length} 项字段变更；${bindingMessage}`);
    const presetId = result.session.activePresetId;
    const boundPreset = result.session.presets.find((preset) => preset.id === presetId);
    if (presetId !== null && boundPreset !== undefined) {
      void (async () => {
        const expectedRevision = presetRevisions.get(presetId);
        const persistedPreset = await commandClient.saveWorkflowPreset({
          kind: "save_workflow_preset",
          mode: "short",
          name: presetId,
          payload: persistencePayload(boundPreset.payload),
          ...(expectedRevision === undefined ? {} : { expectedRevision }),
        });
        if (persistedPreset.status !== "saved") {
          setNotice(`AI 变更已保留在本页，但预设持久化失败：${persistedPreset.message}`);
          return;
        }
        setPresetRevisions((current) => new Map(current).set(presetId, persistedPreset.revision ?? ""));
        const savedHistory = await commandClient.saveWorkflowAiHistory({
          kind: "save_workflow_ai_history",
          mode: "short",
          presetName: presetId,
          operation: operation === "generate" ? "generate" : "polish_applied",
          data: persistencePayload(boundPreset.payload),
          hint: context.userHint,
          selectedSuggestions: context.selectedSuggestions,
          focusFields: context.focusFields,
          metadata: {
            acceptedFields: Object.keys(patch),
            creativeNote: context.creativeNote,
            creativeProfile: context.creativeProfile,
            generationMode: context.generationMode,
            hardConstraints: context.hardConstraints,
          },
        });
        if (savedHistory.status !== "saved") {
          setNotice(`AI 变更已保存，但创作札记未写入：${savedHistory.message}`);
          return;
        }
        const entries = await commandClient.listWorkflowAiHistory("short", presetId);
        setSession((current) => current.activePresetId !== presetId ? current : {
          ...current,
          history: [
            ...current.history.filter((entry) => entry.presetId !== presetId),
            ...entries.map((entry) => shortHistoryFromEngine(entry, presetId)),
          ],
        });
      })().catch(() => setNotice("AI 变更已保留在本页，但预设或创作札记持久化失败。"));
    }
    setDialog(null);
  };

  return <section className="workflow-short-composer">
    <header><h2>短篇创作</h2><p>填写基础参数后一键发起 — 规格确认 → 可选研究 → 初稿 → 自适应修订（0–2 轮）→ 质量评估。</p></header>
    {/* 对标 PySide6 _set_inputs_enabled：运行中禁用全部表单控件 */}
    <fieldset className="workflow-short-form-fields" disabled={formBusy}>
    <div className="workflow-short-toolbar">
      <select aria-label="选择短篇预设" onChange={(event) => setSelectedPresetId(event.target.value)} value={selectedPresetId}><option value="">选择已保存的配置…</option>{session.presets.map((preset) => <option key={preset.id} value={preset.id}>{preset.name}</option>)}</select>
      <button disabled={selectedPreset === undefined} onClick={() => {
        if (selectedPreset === undefined) return;
        setSession((current) => loadShortWorkflowPreset(current, selectedPreset.id));
        setNotice(`已载入预设「${selectedPreset.name}」。`);
      }} type="button">载入</button>
      <button disabled={selectedPreset === undefined} onClick={() => {
        if (selectedPreset === undefined) return;
        beginSave(session.payload, selectedPreset.name);
      }} type="button">更新</button>
      <button disabled={selectedPreset === undefined} onClick={() => selectedPreset !== undefined && setDialog({ type: "delete", presetId: selectedPreset.id, presetName: selectedPreset.name })} type="button">删除</button>
      <button onClick={() => setDialog({ type: "import" })} type="button">导入 JSON</button>
      <button onClick={() => setDialog({ type: "export-choice" })} type="button">导出 JSON</button>
    </div>
    <div className="workflow-short-actions">
      <WorkflowAiAssistant
        available={aiPreviewAvailable}
        className="workflow-ai-inline-actions"
        commandClient={commandClient}
        fields={shortAiFields}
        mode="short"
        onApply={(operation, patch, _changedKeys, context) => applyPreview(
          operation,
          operation === "generate" ? "AI 生成并预览" : "AI 定向润色",
          patch as ShortWorkflowPayloadPatch,
          context,
        )}
        onNotice={setNotice}
        payload={session.payload as unknown as Readonly<Record<string, unknown>>}
      />
      <button onClick={() => { if (session.activePresetId === null) { setDialog({ type: "no-preset-bound" }); return; } setDialog({ type: "history" }); }} type="button">创作札记</button>
    </div>
    <p aria-live="polite" className="workflow-short-notice">{notice}</p>
    {restoreFinished && !restored && <p className="workflow-short-notice">暂无已保存草稿。</p>}
    {lastSaveError !== null && <p className="workflow-short-notice" role="alert">草稿自动保存失败：{lastSaveError}；当前输入仍保留在页面中。</p>}

    <FieldCards fields={coreFields} description="点击字段按钮可在大窗口中查看和编辑；主页面只保留摘要，方便扫配置。" onOpen={(selectedId) => setDialog({ type: "fields", group: "core", selectedId })} payload={session.payload} title="核心故事" />
    <section className="workflow-short-parameters" aria-label="短篇参数">
      <label><span>题材</span><input aria-label="短篇题材" onChange={(event) => updatePayload({ genre: event.target.value })} placeholder="例：言情、悬疑言情、古代宫斗言情" value={session.payload.genre} /></label>
      <label><span>基调</span><select aria-label="短篇基调" onChange={(event) => updatePayload({ tone: event.target.value as ShortWorkflowPayload["tone"] })} value={session.payload.tone}>{shortToneOptions.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}</select></label>
      <label><span>目标字数</span><span className="workflow-short-param-suffix"><input aria-label="短篇目标字数" max="50000" min="500" onChange={(event) => updatePayload({ lengthTarget: Number(event.target.value) })} type="number" value={session.payload.lengthTarget} /><span className="workflow-short-suffix">字</span></span></label>
      <label><span>最多修订轮次</span><input aria-label="短篇最多修订轮次" max="10" min="0" onChange={(event) => updatePayload({ maxEditRounds: Number(event.target.value) })} type="number" value={session.payload.maxEditRounds} /></label>
      <label><span>开始分段字数</span><span className="workflow-short-param-suffix"><input aria-label="短篇开始分段字数" max="50000" min="1500" onChange={(event) => updatePayload({ segmentTriggerWords: Number(event.target.value) })} type="number" value={session.payload.segmentTriggerWords} /><span className="workflow-short-suffix">字</span></span></label>
      <label><span>写作模式</span><select aria-label="短篇写作模式" onChange={(event) => updatePayload({ writingMode: event.target.value as ShortWorkflowPayload["writingMode"] })} value={session.payload.writingMode}>{shortWritingModeOptions.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}</select></label>
    </section>
    <p className="workflow-short-parameter-hint">自适应模式按问题决定 0–2 轮修订；该值是上限。自动模式会按目标字数启用多段起稿。</p>
    <BlueprintCollapsibleSection label="联网资料与低频灵感">
      <section className="workflow-short-parameters" aria-label="短篇联网资料">
        <label><span>启用章节证据包</span><input aria-label="短篇启用联网资料" checked={session.payload.researchEnabled} onChange={(event) => updatePayload({ researchEnabled: event.target.checked })} type="checkbox" /></label>
        <label><span>检索后端</span><select aria-label="短篇检索后端" onChange={(event) => updatePayload({ researchProvider: event.target.value })} value={session.payload.researchProvider}>{[
          ["auto", "自动"], ["tavily", "Tavily"], ["brave", "Brave"], ["searxng", "SearXNG"],
          ["http_json", "HTTP JSON"], ["bailian_web_search", "百炼 Web Search"], ["mcp_search", "MCP Search"],
        ].map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
        <label><span>必须核实的事实</span><input aria-label="短篇检索方向" onChange={(event) => updatePayload({ researchQueryHint: event.target.value })} placeholder="例：当代急诊分诊流程" value={session.payload.researchQueryHint} /></label>
      </section>
      <p className="workflow-short-parameter-hint">整次短篇最多构造一个证据包；外部资料只能补事实或抽象机制，不得改写人物、POV、结局或架空规则。</p>
    </BlueprintCollapsibleSection>
    <FieldCards fields={advancedFields} description="视角、开篇、结尾和项目标识集中在这里；不需要时保持空白即可。" onOpen={(selectedId) => setDialog({ type: "fields", group: "advanced", selectedId })} payload={session.payload} title="高级设定" />

    <BlueprintCollapsibleSection label="叙事要素偏好（手动勾选 / 锁定 / 权重）">
      <BlueprintElementPreferencePanel
        genreText={session.payload.genre}
        preferences={session.payload.blueprintElementPreferences}
        onChange={(prefs) => updatePayload({ blueprintElementPreferences: prefs })}
      />
    </BlueprintCollapsibleSection>
    </fieldset>
    
    <div className="workflow-short-submit-row"><button className="button button-secondary" disabled={formBusy || !persistenceReady} onClick={() => beginSave(session.payload, session.payload.title.trim() || "短篇预设")} type="button">存为预设</button><button className="button button-secondary" disabled={formBusy} onClick={() => { saveTokenRef.current += 1; setSession((current) => resetShortWorkflowSession(current, locale)); setSelectedPresetId(""); setNotice("短篇表单已恢复为 PySide6 的空白默认值；预设未删除。"); }} type="button"> 清空表单</button><button className={`button button-primary${launchState === "launching" ? " is-loading" : ""}`} disabled={formBusy || !startAvailable} onClick={requestLaunch} title={startAvailable ? "提交完整短篇参数并启动任务" : "当前 Engine 未提供 start_workflow 能力"} type="button">{launchState === "launching" ? "正在启动…" : workflowRunning ? "短篇创作进行中…" : "发起短篇创作 →"}</button></div>

    <StepIndicatorRow
      {...stepState}
      completedSteps={stepState.completedSteps}
      currentStepKey={stepState.currentStepKey}
      isComplete={stepState.isComplete}
      onStepClick={handleStepClick}
      steps={RUN_SHORT_STEPS}
    />

    {dialog?.type === "fields" && <ShortFieldEditorDialog fields={dialog.group === "core" ? coreFields : advancedFields} initialId={dialog.selectedId} key={`${dialog.group}:${dialog.selectedId}:${session.payload.theme}`} onApply={(payload) => { setSession((current) => updateShortWorkflowPayload(current, payload)); setNotice(`已更新「${dialog.group === "core" ? "核心故事" : "高级设定"}」字段。`); setDialog(null); }} onClose={() => setDialog(null)} payload={session.payload} title={dialog.group === "core" ? "核心故事" : "高级设定"} />}
    {dialog?.type === "save" && <SavePresetDialog onClose={() => setDialog(null)} onSave={(name) => saveNamedPreset(name, dialog.payload)} suggestedName={dialog.suggestedName} />}
    {dialog?.type === "overwrite" && <AppDialog confirmLabel="更新预设" description={`预设「${dialog.name.trim()}」已存在。更新后旧版本会由引擎自动备份。`} onClose={() => setDialog(null)} onConfirm={() => { void commitOverwrite(dialog.name, dialog.payload); }} title="覆盖预设" />}
    {dialog?.type === "delete" && <AppDialog confirmLabel="删除配置" description={`确定删除持久化配置「${dialog.presetName}」？关联的创作札记会保留。`} onClose={() => setDialog(null)} onConfirm={() => { void deletePersistedPreset(dialog.presetId, dialog.presetName); }} title="确认删除" tone="danger" />}
    {dialog?.type === "import" && <ImportJsonDialog onClose={() => setDialog(null)} onParsed={(payload) => setDialog({ type: "import-choice", payload })} />}
    {dialog?.type === "import-choice" && importPreview !== undefined && <AppDialog closeOnConfirm={false} confirmLabel="保存并填入" description="已解析短篇配置。仅填入会保留 JSON 未提供的现有字段；保存并填入会将其写入 Engine 预设目录。" onClose={() => setDialog(null)} onConfirm={() => beginSave(importPreview, "导入短篇")} title="导入方式"><div className="workflow-short-import-summary"><strong>{importPreview.theme || "（故事主题为空）"}</strong><span>{importPreview.genre || "未设题材"} · {importPreview.lengthTarget.toLocaleString("zh-CN")} 字 · {importPreview.writingMode}</span><button className="button button-secondary" onClick={() => { setSession((current) => updateShortWorkflowPayload(current, dialog.payload)); setNotice("导入内容已填入短篇表单，JSON 未提供的字段已保留；尚未保存为预设。"); setDialog(null); }} type="button">仅填入</button></div></AppDialog>}
    {dialog?.type === "export-choice" && <AppDialog closeOnConfirm={false} confirmLabel="导出当前配置" description="选择要导出的内容。模板导出仅包含当前模式所需的 JSON 字段，不带 run_short 外层包装。" onClose={() => setDialog(null)} onConfirm={() => setDialog({ type: "export", title: "当前短篇配置 JSON", content: serializeShortWorkflowPayload(session.payload) })} title="导出 JSON"><div className="workflow-short-export-choice"><button className="button button-secondary" onClick={() => setDialog({ type: "export", title: "短篇模板 JSON", content: serializeShortWorkflowPayload(emptyShortWorkflowPayload) })} type="button">导出模板</button></div></AppDialog>}
    {dialog?.type === "export" && <AppDialog confirmLabel="关闭" description="这是与引擎请求一致的只读 JSON，可复制后用于导入或版本管理。" onClose={() => setDialog(null)} onConfirm={() => undefined} title={dialog.title}><label className="workflow-short-editor"><span>导出内容</span><textarea aria-label="只读短篇 JSON" readOnly value={dialog.content} /></label></AppDialog>}
    {dialog?.type === "validation" && <AppDialog confirmLabel="返回填写" description={dialog.issues.join("\n")} onClose={() => setDialog(null)} onConfirm={() => undefined} title="输入有误" />}
    {dialog?.type === "launch" && <AppDialog confirmLabel="关闭" description={launchState === "error" ? "启动失败，请检查引擎连接后重试。" : "短篇参数已提交给 Engine；任务流将持续显示真实进度。"} onClose={() => setDialog(null)} onConfirm={() => undefined} title="短篇启动意向"><div className="workflow-short-launch-preview"><p>流程：规格确认 → 可选研究 → 初稿 → 自适应修订（0–2 轮，上限 {session.payload.maxEditRounds}）→ 质量评估</p><p>提交确认后会启动任务，任务流将持续显示真实进度。</p><pre>{JSON.stringify(dialog.preview, null, 2)}</pre></div></AppDialog>}
    {dialog?.type === "history" && <AppDialog confirmLabel="关闭" description="AI 候选只有在确认应用后才会进入札记；札记按预设独立归属，可随时恢复。" onClose={() => setDialog(null)} onConfirm={() => undefined} title="创作札记"><div className="workflow-short-history"><div className="workflow-creative-history-actions"><strong>「{selectedPreset?.name ?? (session.payload.title.trim() || "当前预设")}」的 AI 构思与润色札记</strong><button className="button button-secondary" disabled={historyEntries.length === 0} onClick={() => setDialog({ type: "history-clear-confirm" })} type="button">清空札记</button></div>{historyEntries.length === 0 ? <p>暂无创作札记。完成一次 AI 生成或定向润色并应用字段后，这里会留下可恢复版本。</p> : historyEntries.map((entry) => <article key={entry.id}><div><strong>{entry.timeLabel ? `${entry.timeLabel}　${entry.label}` : entry.label}</strong><span>{entry.changedKeys.map((key) => keyLabels[key]).join("、")}</span>{shortHistoryDetails(entry).map((detail) => <small key={detail}>{detail}</small>)}</div><button className="button button-secondary" onClick={() => setDialog({ type: "history-restore", entry })} type="button">恢复此版本</button></article>)}</div></AppDialog>}
    {dialog?.type === "history-clear-confirm" && <AppDialog confirmLabel="清空札记" description="确定清空当前预设的所有 AI 构思与润色札记吗？此操作不会修改表单内容或预设配置。" onClose={() => setDialog({ type: "history" })} onConfirm={() => { const activePresetId = session.activePresetId; if (activePresetId !== null) { setSession((current) => ({ ...current, history: current.history.filter((entry) => entry.presetId !== activePresetId) })); void commandClient.clearWorkflowAiHistory("short", activePresetId).then((result) => setNotice(result.message)).catch(() => setNotice("本页札记已清空，但引擎持久化失败。")); } setDialog(null); }} title="清空创作札记" tone="danger" />}
    {dialog?.type === "history-restore" && <AppDialog confirmLabel="恢复" description={`确定要将表单恢复为「${dialog.entry.label}」的状态吗？当前未保存的修改会丢失。`} onClose={() => setDialog(null)} onConfirm={() => { setSession((current) => ({ ...current, payload: dialog.entry.snapshot })); setNotice(`已恢复到「${dialog.entry.label}」的状态。`); setDialog(null); }} title="恢复创作札记" />}
    {dialog?.type === "no-preset-bound" && <AppDialog confirmLabel="知道了" description="请先载入预设。" onClose={() => setDialog(null)} onConfirm={() => undefined} title="未绑定预设"><p>可先在「选择短篇预设」中载入，或使用「AI 生成并预览 / 导入 JSON」建立预设关联，再来查看创作札记。</p></AppDialog>}
    {artifactDialog && (
      <WorkflowArtifactDialog
        artifacts={artifactDialog.artifacts}
        {...(artifactDialog.candidatePaths ? { candidatePaths: artifactDialog.candidatePaths } : {})}
        {...(artifactDialog.emptyHint ? { emptyHint: artifactDialog.emptyHint } : {})}
        onClose={() => setArtifactDialog(null)}
        stageLabel={artifactDialog.stepLabel}
      />
    )}
  </section>;
}

export interface ShortWorkflowComposerProps {
  readonly commandClient: EngineCommandClient;
  readonly engineClient: EngineClient;
  /** Top-bar template action; it intentionally opens a read-only session export. */
  readonly externalTemplateExportRequest?: number;
  /** Active run_short job for StepIndicator progress binding. */
  readonly activeRun?: WorkflowRunView | null;
  readonly onWorkflowStarted?: (() => Promise<void> | void) | undefined;
}
