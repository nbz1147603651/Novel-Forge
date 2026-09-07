import type { PageId } from "@nimo/engine-contracts";

import type { ProjectFilter } from "../components/types";

const storageKey = "nimo.ui-session.v1";

const pageIds = new Set<PageId>([
  "dashboard",
  "projects",
  "workflow",
  "settings",
  "chapter_studio",
  "voice_studio",
  "film_studio",
]);

/**
 * Voice studio tab identifiers — mirrors the tab order built by the legacy
 * `VoiceStudioPage._create_tab_bar` ("1 配音团队" … "平台设置").
 */
export type VoiceStudioTabId = "team" | "script" | "room" | "post" | "settings";

const voiceTabIds = new Set<VoiceStudioTabId>([
  "team",
  "script",
  "room",
  "post",
  "settings",
]);

/** Keep fixture parsing and persisted-session recovery on one tab vocabulary. */
export function isVoiceStudioTabId(value: string): value is VoiceStudioTabId {
  return voiceTabIds.has(value as VoiceStudioTabId);
}

/**
 * 映界统一制片形态 —— 剧集电影、短剧、漫画同属一个模块：短剧是映界的
 * 剧集化产出，漫画是映界的分镜形态（可回流为短剧动态分镜）。
 */
export type FilmStudioFormatId = "feature" | "drama" | "comic";

const filmFormatIds = new Set<FilmStudioFormatId>(["feature", "drama", "comic"]);

export function isFilmStudioFormatId(value: string): value is FilmStudioFormatId {
  return filmFormatIds.has(value as FilmStudioFormatId);
}

const dashboardFilters = new Set<ProjectFilter>([
  "all",
  "writing",
  "planning",
  "completed",
  "long",
  "short",
]);

/** A safe, value-free description of the last user operation on one page. */
export interface UiPageOperationMemory {
  /** The visible control name, never an entered value or credential. */
  readonly label: string;
  readonly occurredAt: number;
}

export interface UiSession {
  readonly activePage: PageId;
  readonly sidebarCollapsed: boolean;
  /** Workflow page mode — mirrors WorkflowPage.export_ui_state()["mode"]. */
  readonly workflowMode: "short" | "long";
  /** Long panel sub-mode — mirrors LongPanel.export_ui_state()["mode"]. */
  readonly longPanelMode: "init" | "chapter";
  /** mirrors LongPanel.export_ui_state()["chapter_project_id"]. */
  readonly chapterProjectId: string;
  /** mirrors LongPanel.export_ui_state()["chapter_number"]. */
  readonly chapterNumber: number;
  /** mirrors voice_studio export_ui_state()["tab_index"] (stored as the tab id). */
  readonly voiceTab: VoiceStudioTabId;
  /** 映界内部当前激活的制片形态（剧集 / 短剧 / 漫画）。 */
  readonly filmFormat: FilmStudioFormatId;
  /** Persisted selected segment index within voice room tab. */
  readonly voiceSegmentIndex: number;
  /** Persisted selected chapter number within voice studio. */
  readonly voiceChapterNumber: number;
  /** mirrors voice_studio export_ui_state()["project_id"]. */
  readonly voiceProjectId: string;
  /** mirrors dashboard export_ui_state()["selected_project_id"]. */
  readonly dashboardSelectedProjectId: string;
  /** mirrors dashboard export_ui_state()["filter"]. */
  readonly dashboardFilter: ProjectFilter;
  /** mirrors dashboard export_ui_state()["search"]. */
  readonly dashboardSearch: string;
  /** User-managed bookshelf order. This changes the dashboard only, never project files. */
  readonly dashboardProjectOrder: readonly string[];
  /** mirrors projects_page (卷帙) export_ui_state()["project_id"]. */
  readonly readerProjectId: string;
  /** Last user-visible operation for each page; persisted across restarts. */
  readonly lastOperationByPage: Readonly<Partial<Record<PageId, UiPageOperationMemory>>>;
  /** Pet companion visibility (mirrors NOVEL_FORGE_DESKTOP_PET_VISIBLE). */
  readonly petVisible: boolean;
  /** Pet companion X position in viewport pixels. */
  readonly petPositionX: number;
  /** Pet companion Y position in viewport pixels. */
  readonly petPositionY: number;
}

const defaultSession: UiSession = {
  activePage: "dashboard",
  sidebarCollapsed: false,
  workflowMode: "short",
  longPanelMode: "init",
  chapterProjectId: "",
  chapterNumber: 1,
  voiceTab: "team",
  filmFormat: "feature",
  voiceSegmentIndex: 0,
  voiceChapterNumber: 0,
  voiceProjectId: "",
  dashboardSelectedProjectId: "",
  dashboardFilter: "all",
  dashboardSearch: "",
  dashboardProjectOrder: [],
  readerProjectId: "",
  lastOperationByPage: {},
  petVisible: true,
  petPositionX: 0,
  petPositionY: 0,
};

function loadLastOperationByPage(value: unknown): UiSession["lastOperationByPage"] {
  if (typeof value !== "object" || value === null) return {};
  const source = value as Record<string, unknown>;
  const restored: Partial<Record<PageId, UiPageOperationMemory>> = {};
  for (const page of pageIds) {
    const candidate = source[page];
    if (typeof candidate !== "object" || candidate === null) continue;
    const record = candidate as Partial<UiPageOperationMemory>;
    if (
      typeof record.label !== "string"
      || typeof record.occurredAt !== "number"
      || !Number.isFinite(record.occurredAt)
      || record.label.trim().length === 0
    ) {
      continue;
    }
    restored[page] = {
      label: record.label.trim().slice(0, 60),
      occurredAt: Math.floor(record.occurredAt),
    };
  }
  return restored;
}

function loadDashboardProjectOrder(value: unknown): readonly string[] {
  if (!Array.isArray(value)) return [];
  const seen = new Set<string>();
  const order: string[] = [];
  for (const candidate of value) {
    if (typeof candidate !== "string") continue;
    const projectId = candidate.trim();
    if (projectId.length === 0 || seen.has(projectId)) continue;
    seen.add(projectId);
    order.push(projectId);
    if (order.length >= 500) break;
  }
  return order;
}

/**
 * Update one page's operation memory without ever serializing a form value.
 * Callers should pass a control label only (for example “下载” or “保存设置”).
 */
export function rememberUiSessionOperation(
  session: UiSession,
  page: PageId,
  label: string,
  occurredAt = Date.now(),
): UiSession {
  const normalized = label.replaceAll(/\s+/g, " ").trim().slice(0, 60);
  if (normalized.length === 0) return session;
  return {
    ...session,
    lastOperationByPage: {
      ...session.lastOperationByPage,
      [page]: { label: normalized, occurredAt: Math.floor(occurredAt) },
    },
  };
}

export function loadUiSession(): UiSession {
  try {
    const raw = localStorage.getItem(storageKey);
    if (raw === null) {
      return defaultSession;
    }
    const parsed: unknown = JSON.parse(raw);
    if (typeof parsed !== "object" || parsed === null) {
      return defaultSession;
    }
    const candidate = parsed as Partial<UiSession>;
    // 旧版会话把短剧/漫画作为独立页面持久化；融合后归一化为映界形态。
    const legacyPage = typeof candidate.activePage === "string" ? candidate.activePage : "";
    const legacyFilmFormat: FilmStudioFormatId =
      legacyPage === "drama_studio" ? "drama" : legacyPage === "comic_studio" ? "comic" : "feature";
    const restoredActivePage: string =
      legacyPage === "drama_studio" || legacyPage === "comic_studio" ? "film_studio" : legacyPage;
    return {
      activePage: pageIds.has(restoredActivePage as PageId)
        ? restoredActivePage as PageId
        : defaultSession.activePage,
      filmFormat: typeof candidate.filmFormat === "string" && isFilmStudioFormatId(candidate.filmFormat)
        ? candidate.filmFormat
        : legacyFilmFormat,
      sidebarCollapsed: candidate.sidebarCollapsed === true,
      workflowMode: candidate.workflowMode === "long" ? "long" : "short",
      longPanelMode: candidate.longPanelMode === "chapter" ? "chapter" : "init",
      chapterProjectId: typeof candidate.chapterProjectId === "string" ? candidate.chapterProjectId : "",
      chapterNumber: typeof candidate.chapterNumber === "number" && candidate.chapterNumber >= 1
        ? Math.floor(candidate.chapterNumber)
        : 1,
      voiceTab: typeof candidate.voiceTab === "string" && isVoiceStudioTabId(candidate.voiceTab)
        ? (candidate.voiceTab as VoiceStudioTabId)
        : defaultSession.voiceTab,
      voiceSegmentIndex: typeof candidate.voiceSegmentIndex === "number" && candidate.voiceSegmentIndex >= 0
        ? Math.floor(candidate.voiceSegmentIndex)
        : defaultSession.voiceSegmentIndex,
      voiceChapterNumber: typeof candidate.voiceChapterNumber === "number" && candidate.voiceChapterNumber >= 1
        ? Math.floor(candidate.voiceChapterNumber)
        : defaultSession.voiceChapterNumber,
      voiceProjectId: typeof candidate.voiceProjectId === "string"
        ? candidate.voiceProjectId
        : "",
      dashboardSelectedProjectId: typeof candidate.dashboardSelectedProjectId === "string"
        ? candidate.dashboardSelectedProjectId
        : "",
      dashboardFilter: typeof candidate.dashboardFilter === "string" && dashboardFilters.has(candidate.dashboardFilter as ProjectFilter)
        ? (candidate.dashboardFilter as ProjectFilter)
        : defaultSession.dashboardFilter,
      dashboardSearch: typeof candidate.dashboardSearch === "string" ? candidate.dashboardSearch : "",
      dashboardProjectOrder: loadDashboardProjectOrder(candidate.dashboardProjectOrder),
      readerProjectId: typeof candidate.readerProjectId === "string" ? candidate.readerProjectId : "",
      lastOperationByPage: loadLastOperationByPage(candidate.lastOperationByPage),
      petVisible: candidate.petVisible !== false,
      petPositionX: typeof candidate.petPositionX === "number" && Number.isFinite(candidate.petPositionX)
        ? candidate.petPositionX
        : 0,
      petPositionY: typeof candidate.petPositionY === "number" && Number.isFinite(candidate.petPositionY)
        ? candidate.petPositionY
        : 0,
    };
  } catch {
    return defaultSession;
  }
}

export function persistUiSession(session: UiSession): void {
  try {
    localStorage.setItem(storageKey, JSON.stringify(session));
  } catch {
    // A disabled storage backend must not block basic desktop navigation.
  }
}
