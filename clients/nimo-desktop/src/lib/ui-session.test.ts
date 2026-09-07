import { afterEach, describe, expect, it } from "vitest";

import {
  loadUiSession,
  persistUiSession,
  rememberUiSessionOperation,
  type UiSession,
} from "./ui-session";

class MemoryStorage {
  private readonly values = new Map<string, string>();

  getItem(key: string): string | null {
    return this.values.get(key) ?? null;
  }

  setItem(key: string, value: string): void {
    this.values.set(key, value);
  }

  clear(): void {
    this.values.clear();
  }
}

const storage = new MemoryStorage();

Object.defineProperty(globalThis, "localStorage", {
  configurable: true,
  value: storage,
});

afterEach(() => storage.clear());

const defaults = {
  filmFormat: "feature",
  workflowMode: "short",
  longPanelMode: "init",
  chapterProjectId: "",
  chapterNumber: 1,
  voiceTab: "team",
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
} as const;

describe("UiSessionStore", () => {
  it("starts with a safe dashboard session", () => {
    expect(loadUiSession()).toEqual({
      activePage: "dashboard",
      sidebarCollapsed: false,
      ...defaults,
    });
  });

  it("persists only pure UI preferences", () => {
    const session: UiSession = {
      activePage: "voice_studio",
      sidebarCollapsed: true,
      ...defaults,
    };
    persistUiSession(session);

    expect(loadUiSession()).toEqual(session);
  });

  it("persists workflow state fields (export_ui_state parity)", () => {
    const session: UiSession = {
      activePage: "workflow",
      sidebarCollapsed: false,
      filmFormat: "feature",
      workflowMode: "long",
      longPanelMode: "chapter",
      chapterProjectId: "青瓦梦匙",
      chapterNumber: 7,
      voiceTab: "team",
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
    persistUiSession(session);

    expect(loadUiSession()).toEqual(session);
  });

  it("persists the voice studio tab (voice_studio tab_index parity)", () => {
    const session: UiSession = {
      activePage: "voice_studio",
      sidebarCollapsed: false,
      ...defaults,
      voiceTab: "settings",
    };
    persistUiSession(session);

    expect(loadUiSession().voiceTab).toBe("settings");
  });

  it("persists the voice studio project selector", () => {
    const session: UiSession = {
      activePage: "voice_studio",
      sidebarCollapsed: false,
      ...defaults,
      voiceProjectId: "青瓦梦匙",
    };
    persistUiSession(session);

    expect(loadUiSession().voiceProjectId).toBe("青瓦梦匙");
  });

  it("persists dashboard library choices (export_ui_state parity)", () => {
    const session: UiSession = {
      activePage: "dashboard",
      sidebarCollapsed: false,
      ...defaults,
      dashboardSelectedProjectId: "青瓦梦匙",
      dashboardFilter: "long",
      dashboardSearch: "梦",
      dashboardProjectOrder: ["青瓦梦匙", "梦侦探"],
    };
    persistUiSession(session);

    const restored = loadUiSession();
    expect(restored.dashboardSelectedProjectId).toBe("青瓦梦匙");
    expect(restored.dashboardFilter).toBe("long");
    expect(restored.dashboardSearch).toBe("梦");
    expect(restored.dashboardProjectOrder).toEqual(["青瓦梦匙", "梦侦探"]);
  });

  it("keeps only distinct project ids in the persisted bookshelf order", () => {
    storage.setItem(
      "nimo.ui-session.v1",
      JSON.stringify({ dashboardProjectOrder: ["青瓦梦匙", "", "青瓦梦匙", 42, "梦侦探"] }),
    );

    expect(loadUiSession().dashboardProjectOrder).toEqual(["青瓦梦匙", "梦侦探"]);
  });

  it("falls back when stored state contains an unknown page", () => {
    storage.setItem(
      "nimo.ui-session.v1",
      JSON.stringify({ activePage: "gateway", sidebarCollapsed: true }),
    );

    expect(loadUiSession()).toEqual({
      activePage: "dashboard",
      sidebarCollapsed: true,
      ...defaults,
    });
  });

  it("sanitizes invalid workflow state values", () => {
    storage.setItem(
      "nimo.ui-session.v1",
      JSON.stringify({
        activePage: "workflow",
        workflowMode: "bogus",
        longPanelMode: "nope",
        chapterNumber: -3,
      }),
    );

    const session = loadUiSession();
    expect(session.workflowMode).toBe("short");
    expect(session.longPanelMode).toBe("init");
    expect(session.chapterNumber).toBe(1);
  });

  it("sanitizes an unknown voice studio tab back to the default", () => {
    storage.setItem(
      "nimo.ui-session.v1",
      JSON.stringify({ activePage: "voice_studio", voiceTab: "bogus" }),
    );

    expect(loadUiSession().voiceTab).toBe("team");
  });

  it("sanitizes an unknown dashboard filter back to the default", () => {
    storage.setItem(
      "nimo.ui-session.v1",
      JSON.stringify({ activePage: "dashboard", dashboardFilter: "bogus" }),
    );

    expect(loadUiSession().dashboardFilter).toBe("all");
  });

  it("normalizes legacy drama/comic pages into the 映界 film format", () => {
    storage.setItem(
      "nimo.ui-session.v1",
      JSON.stringify({ activePage: "drama_studio" }),
    );
    const dramaSession = loadUiSession();
    expect(dramaSession.activePage).toBe("film_studio");
    expect(dramaSession.filmFormat).toBe("drama");

    storage.setItem(
      "nimo.ui-session.v1",
      JSON.stringify({ activePage: "comic_studio" }),
    );
    const comicSession = loadUiSession();
    expect(comicSession.activePage).toBe("film_studio");
    expect(comicSession.filmFormat).toBe("comic");
  });

  it("persists the reader project selection (卷帙 project_id parity)", () => {
    const session: UiSession = {
      activePage: "projects",
      sidebarCollapsed: false,
      ...defaults,
      readerProjectId: "梦侦探",
    };
    persistUiSession(session);

    expect(loadUiSession().readerProjectId).toBe("梦侦探");
  });

  it("remembers each page's last control label without storing field values", () => {
    const session = rememberUiSessionOperation(
      {
        activePage: "voice_studio",
        sidebarCollapsed: false,
        ...defaults,
      },
      "voice_studio",
      "  下载   Stable Audio Open Small  ",
      1_735_689_600_000,
    );
    persistUiSession(session);

    expect(loadUiSession().lastOperationByPage.voice_studio).toEqual({
      label: "下载 Stable Audio Open Small",
      occurredAt: 1_735_689_600_000,
    });
  });

  it("drops malformed saved operation history", () => {
    storage.setItem(
      "nimo.ui-session.v1",
      JSON.stringify({
        activePage: "voice_studio",
        lastOperationByPage: {
          voice_studio: { label: "", occurredAt: "now" },
          unknown: { label: "should not restore", occurredAt: 1 },
        },
      }),
    );

    expect(loadUiSession().lastOperationByPage).toEqual({});
  });
});
