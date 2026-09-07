import type { PageId, TaskStreamView } from "@nimo/engine-contracts";

import { normalizeThemeId, type ThemeId } from "../theme/theme";
import { isVoiceStudioTabId, type VoiceStudioTabId } from "./ui-session";

declare global {
  interface Window {
    /**
     * Tauri's restricted native capture runner seeds this value at document
     * start. It is absent from every normal desktop/browser session.
     */
    __NIMO_UI_PARITY_QUERY__?: string;
  }
}

const uiSessionStorageKey = "nimo.ui-session.v1";
const themeStorageKey = "nimo-ui-parity.theme";

export type WorkflowDialogFixture =
  | "floating-stream"
  | "error-log"
  | "cancel";

/** Frozen task-stream state used only by the workflow observation capture. */
export type WorkflowStreamFixture = "paused-validated";

/** Source-sized PySide model profile dialogs. Kept outside normal sessions. */
export type SettingsDialogFixture = "model-profile-add" | "model-profile-edit";

/** A small native-shaped voice prompt, available only to parity capture URLs. */
export type VoiceDialogFixture = "clone-provider-file-id";

/** The configured Voice Studio cast captured from the source desktop page. */
export type VoiceStudioStateFixture = "configured";

/** Source-sized Chapter Studio dialogs, available only to capture URLs. */
export type ChapterStudioDialogFixture = "checkpoint" | "export" | "book-audit" | "clean" | "project-switch" | "error-log";

const pageIds = new Set<PageId>([
  "dashboard",
  "projects",
  "workflow",
  "settings",
  "chapter_studio",
  "voice_studio",
]);

export interface ParityFixture {
  readonly active: boolean;
  /** A named, source-shaped Chapter Studio overlay for native capture only. */
  readonly chapterStudioDialog: ChapterStudioDialogFixture | null;
  readonly chapterStudioState: "prepared" | "running" | "checkpoint" | "history" | "notice" | "source-conflict" | null;
  readonly projectReaderId: string | null;
  /** A source-sized model dialog used only by visual capture URLs. */
  readonly settingsDialog: SettingsDialogFixture | null;
  readonly settingsSection: "creative-temperature" | "model-routing" | null;
  /** A source-sized voice dialog used only by visual capture URLs. */
  readonly voiceDialog: VoiceDialogFixture | null;
  /** A source-shaped Voice Studio read model used only by visual capture URLs. */
  readonly voiceStudioState: VoiceStudioStateFixture | null;
  /** Explicit source tab for a Voice Studio capture; normal sessions restore their own tab. */
  readonly voiceStudioTab: VoiceStudioTabId | null;
  /** A source-sized workflow overlay. Normal launches never set this value. */
  readonly workflowDialog: WorkflowDialogFixture | null;
  /** Explicit observation state for the source-sized workflow overlay. */
  readonly workflowStream: WorkflowStreamFixture | null;
}

const inactiveFixture: ParityFixture = {
  active: false,
  chapterStudioDialog: null,
  chapterStudioState: null,
  projectReaderId: null,
  settingsDialog: null,
  settingsSection: null,
  voiceDialog: null,
  voiceStudioState: null,
  voiceStudioTab: null,
  workflowDialog: null,
  workflowStream: null,
};

/**
 * Small, capture-only stream snapshot used by the floating observation dialog.
 *
 * Keep this fixture at the parity boundary instead of importing the full Mock
 * engine into the application shell. Native/HTTP launches must not pay the
 * bundle and parse cost of deterministic development project data.
 */
export const frozenParityTaskStreamFixture: TaskStreamView = {
  taskId: "fixture-init-long-qingwa",
  title: "长篇立项 · 青瓦梦起",
  stepLabel: "冲突候选裁判 · 当前层：章节契约 · 批次 3/7 · 判定：通过",
  stepId: "adjudicate_init_conflict_candidates_25_36",
  status: "streaming",
  progressPercent: 35,
  jobState: "running",
  summary: {
    outputKind: "json",
    attempt: 1,
    outputCharacters: 286,
    elapsedMs: 7_000,
    provider: "openai",
    model: "gpt-4o-mini",
    promptTokens: 2_150,
    completionTokens: 832,
    totalTokens: 2_982,
    costUsd: 0.0112,
  },
  events: [
    { streamId: "stream-init-qingwa", sequence: 1, kind: "stream_start", segment: "system", message: "开始接收 一致性画像 输出" },
    { streamId: "stream-init-qingwa", sequence: 2, kind: "delta", segment: "reasoning", text: "先核对主题兑现是否落在可见行动，而非抽象说明。" },
    { streamId: "stream-init-qingwa", sequence: 3, kind: "delta", segment: "content", outputKind: "json", text: "{\"verdict\":\"pass\",\"current_layer\":\"章节契约\",\"batch\":\"3/7\"," },
    { streamId: "stream-init-qingwa", sequence: 4, kind: "delta", segment: "content", outputKind: "json", text: "\"visible_consequence\":\"主题兑现落在可见行动与后果中\",\"next_step\":\"写入下一阶段工作契约\"}" },
    { streamId: "stream-init-qingwa", sequence: 5, kind: "validation", segment: "system", outputKind: "json", validationStatus: "validated", repairSource: "local", modelId: "gpt-4o-mini", attempt: 1, maxAttempts: 2 },
    { streamId: "stream-init-invalid", sequence: 6, kind: "stream_start", segment: "system", message: "开始接收 异常 Claims 输出" },
    { streamId: "stream-init-invalid", sequence: 7, kind: "delta", segment: "content", outputKind: "json", text: "{\"batch\":\"4/7\",\"claim_count\":12,\"cognitive_level\":\"confirmed\"" },
    { streamId: "stream-init-invalid", sequence: 8, kind: "validation", segment: "system", outputKind: "json", validationStatus: "failed", message: "字段校验未通过，自动重试已耗尽。", attempt: 2, maxAttempts: 2 },
    { streamId: "stream-init-followup", sequence: 9, kind: "stream_start", segment: "system", message: "开始接收 下一批 Claims 输出" },
    { streamId: "stream-init-followup", sequence: 10, kind: "delta", segment: "content", outputKind: "json", text: "{\"batch\":\"5/7\",\"claim_count\":" },
  ],
  calls: [
    {
      callId: "call-claims-1",
      task: "INIT_CLAIM_CONTRACT_COVERAGE",
      taskLabel: "契约 Claims 抽取",
      provider: "openai",
      model: "gpt-4o-mini",
      route: "primary",
      status: "success",
      event: "api_stream_done",
      attempt: 1,
      maxAttempts: 3,
      promptTokens: 2_150,
      completionTokens: 832,
      totalTokens: 2_982,
      latencyMs: 7_000,
      costUsd: 0.0112,
      startedAt: "2026-08-11T10:08:01+08:00",
      finishedAt: "2026-08-11T10:08:08+08:00",
    },
    {
      callId: "call-claims-2",
      task: "INIT_CLAIM_CONTRACT_COVERAGE",
      taskLabel: "契约 Claims 抽取",
      provider: "deepseek",
      model: "deepseek-chat",
      route: "fallback-1",
      status: "success",
      event: "api_stream_done",
      attempt: 2,
      maxAttempts: 3,
      promptTokens: 1_760,
      completionTokens: 604,
      totalTokens: 2_364,
      latencyMs: 5_320,
      costUsd: 0.0048,
      startedAt: "2026-08-11T10:08:09+08:00",
      finishedAt: "2026-08-11T10:08:14+08:00",
    },
    {
      callId: "call-conflict-search",
      task: "ADJUDICATE_CONTRACT_COHERENCE",
      taskLabel: "冲突候选检索",
      provider: "openai",
      model: "gpt-4o-mini",
      route: "primary",
      status: "success",
      event: "api_stream_done",
      attempt: 1,
      maxAttempts: 3,
      promptTokens: 1_245,
      completionTokens: 318,
      totalTokens: 1_563,
      latencyMs: 3_870,
      costUsd: 0.0061,
      startedAt: "2026-08-11T10:08:15+08:00",
      finishedAt: "2026-08-11T10:08:19+08:00",
    },
    {
      callId: "call-conflict-judge",
      task: "ADJUDICATE_INIT_CONFLICT_CANDIDATES",
      taskLabel: "冲突候选裁判",
      provider: "minimax",
      model: "MiniMax-M3",
      route: "primary",
      status: "running",
      event: "api_stream_start",
      attempt: 1,
      maxAttempts: 3,
      maxTokens: 8_192,
      startedAt: "2026-08-11T10:08:20+08:00",
    },
  ],
};

/**
 * Capture-only replay of a real chapter-plan boundary: the JSON is validated,
 * the durable observation snapshot is clipped, and the job is paused before
 * the author's plan decision. No provider or project write is involved.
 */
export const frozenPausedValidatedTaskStreamFixture: TaskStreamView = {
  taskId: "fixture-init-long-qingwa",
  title: "章节方案 · 演练项目 / 第 1 章",
  stepLabel: "方案确认 · 章节方案待确认",
  stepId: "plan_checkpoint",
  status: "paused",
  progressPercent: 83,
  jobState: "paused",
  summary: {
    outputKind: "json",
    attempt: 2,
    outputCharacters: 23_938,
    elapsedMs: 60_300,
    provider: "minimax",
    model: "MiniMax-M3",
    promptTokens: 31_142,
    completionTokens: 11_402,
    totalTokens: 42_544,
    costUsd: 0,
  },
  events: [{
    streamId: "fixture-chapter-plan",
    sequence: 1,
    kind: "validation",
    segment: "content",
    outputKind: "json",
    text: `{"scene_intents":[{"scene_id":"scene_01","summary":"${"永安十九年春首日，沈昭在晨钟仪轨前核对药汤封存记录。".repeat(90)}"`,
    textMode: "snapshot",
    textLength: 23_938,
    textTruncated: true,
    validationStatus: "validated",
    repairSource: "local",
    modelId: "MiniMax-M3",
    attempt: 2,
    maxAttempts: 3,
    finishReason: "stop",
  }],
  calls: [
    {
      callId: "fixture-plan-attempt-1",
      task: "PLAN_CHAPTER",
      taskLabel: "章节方案",
      provider: "minimax",
      model: "MiniMax-M3",
      route: "primary",
      status: "error",
      event: "api_stream_done",
      attempt: 1,
      maxAttempts: 3,
      totalTokens: 19_210,
      latencyMs: 31_800,
      finishReason: "length",
      willRetry: true,
    },
    {
      callId: "fixture-plan-attempt-2",
      task: "PLAN_CHAPTER",
      taskLabel: "章节方案",
      provider: "minimax",
      model: "MiniMax-M3",
      route: "primary",
      status: "success",
      event: "api_stream_done",
      attempt: 2,
      maxAttempts: 3,
      promptTokens: 31_142,
      completionTokens: 11_402,
      totalTokens: 42_544,
      latencyMs: 60_300,
      finishReason: "stop",
    },
  ],
};

function pageFromQuery(candidate: string | null): PageId {
  return candidate !== null && pageIds.has(candidate as PageId)
    ? candidate as PageId
    : "dashboard";
}

function fixtureSearch(): string {
  const locationSearch = window.location.search;
  if (locationSearch.length > 0) return locationSearch;

  const injectedQuery = window.__NIMO_UI_PARITY_QUERY__;
  return typeof injectedQuery === "string" && injectedQuery.startsWith("?__nimo_ui_parity=1")
    ? injectedQuery
    : "";
}

/**
 * Apply a deliberately narrow, test-only startup fixture.
 *
 * Native Tauri capture cannot rely on browser automation to seed storage
 * before React mounts.  A local URL such as
 * `?__nimo_ui_parity=1&page=settings&theme=narrative_ember` is therefore the
 * one supported way to freeze a native screenshot state.  Normal launches,
 * including every production URL, leave existing UI session storage alone.
 */
export function applyUrlParityFixture(): ParityFixture {
  if (typeof window === "undefined") return inactiveFixture;

  const query = new URLSearchParams(fixtureSearch());
  if (query.get("__nimo_ui_parity") !== "1") return inactiveFixture;

  const activePage = pageFromQuery(query.get("page"));
  const themeId: ThemeId = normalizeThemeId(query.get("theme"));
  const sidebarCollapsed = query.get("rail") === "collapsed";
  const projectReaderId = query.get("reader") === "long" ? "test-long" : null;
  const chapterStudioState = query.get("chapter_state") === "running"
    ? "running"
    : query.get("chapter_state") === "checkpoint"
      ? "checkpoint"
      : query.get("chapter_state") === "history"
        ? "history"
        : query.get("chapter_state") === "source-conflict"
          ? "source-conflict"
          : query.get("chapter_state") === "notice"
            ? "notice"
            : query.get("chapter_state") === "prepared"
              ? "prepared"
              : null;
  // Each overlay is constrained to the source page state it overlays. Keeping
  // that invariant at the query boundary prevents a component-only capture
  // flag from leaking into normal sessions or producing a false visual pair.
  const chapterDialogValue = query.get("chapter_dialog");
  const chapterStudioDialog = activePage !== "chapter_studio"
    ? null
    : chapterDialogValue === "checkpoint" && chapterStudioState === "checkpoint"
      ? "checkpoint"
      : chapterDialogValue === "export" && chapterStudioState === "prepared"
        ? "export"
        : chapterDialogValue === "book-audit" && chapterStudioState === "prepared"
          ? "book-audit"
          : chapterDialogValue === "clean" && chapterStudioState === "prepared"
            ? "clean"
            : chapterDialogValue === "project-switch" && chapterStudioState === "running"
              ? "project-switch"
              : chapterDialogValue === "error-log" && chapterStudioState === "prepared"
                ? "error-log"
            : null;
  const settingsSectionValue = query.get("settings_section");
  const settingsSection = activePage === "settings" && (settingsSectionValue === "creative-temperature" || settingsSectionValue === "model-routing")
    ? settingsSectionValue
    : null;
  const settingsDialogValue = query.get("settings_dialog");
  const settingsDialog = activePage === "settings"
    && (settingsDialogValue === "model-profile-add" || settingsDialogValue === "model-profile-edit")
    ? settingsDialogValue
    : null;
  const voiceDialog = activePage === "voice_studio"
    && query.get("voice_dialog") === "clone-provider-file-id"
    ? "clone-provider-file-id"
    : null;
  const voiceStudioState = activePage === "voice_studio" && query.get("voice_state") === "configured"
    ? "configured"
    : null;
  const requestedVoiceTab = query.get("voice_tab");
  const voiceStudioTab = activePage === "voice_studio"
    && requestedVoiceTab !== null
    && isVoiceStudioTabId(requestedVoiceTab)
    ? requestedVoiceTab
    : null;
  const workflowDialogValue = query.get("workflow_dialog");
  const workflowDialog = activePage === "workflow"
    && (workflowDialogValue === "floating-stream"
      || workflowDialogValue === "error-log"
      || workflowDialogValue === "cancel")
    ? workflowDialogValue as WorkflowDialogFixture
    : null;
  const workflowStream = workflowDialog === "floating-stream"
    && query.get("workflow_stream") === "paused-validated"
    ? "paused-validated"
    : null;

  try {
    localStorage.setItem(
      uiSessionStorageKey,
      JSON.stringify({ activePage, sidebarCollapsed, ...(voiceStudioTab === null ? {} : { voiceTab: voiceStudioTab }) }),
    );
    localStorage.setItem(themeStorageKey, themeId);
  } catch {
    // A disabled storage backend still leaves the regular, non-fixture UI usable.
  }

  return { active: true, chapterStudioDialog, chapterStudioState, projectReaderId, settingsDialog, settingsSection, voiceDialog, voiceStudioState, voiceStudioTab, workflowDialog, workflowStream };
}
