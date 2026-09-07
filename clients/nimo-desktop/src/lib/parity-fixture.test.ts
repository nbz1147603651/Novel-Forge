import { afterEach, describe, expect, it } from "vitest";

import { applyUrlParityFixture } from "./parity-fixture";

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
const originalWindow = Object.getOwnPropertyDescriptor(globalThis, "window");

function installWindow(search = ""): Window {
  const fixtureWindow = { location: { search } } as Window;
  Object.defineProperty(globalThis, "window", { configurable: true, value: fixtureWindow });
  Object.defineProperty(globalThis, "localStorage", { configurable: true, value: storage });
  return fixtureWindow;
}

afterEach(() => {
  storage.clear();
  if (originalWindow === undefined) {
    delete (globalThis as { window?: Window }).window;
  } else {
    Object.defineProperty(globalThis, "window", originalWindow);
  }
});

describe("native parity fixture", () => {
  it("uses a document-start capture query before the stored session is read", () => {
    const fixtureWindow = installWindow();
    Object.defineProperty(fixtureWindow, "__NIMO_UI_PARITY_QUERY__", {
      configurable: true,
      value: "?__nimo_ui_parity=1&page=dashboard&theme=narrative_ember&rail=collapsed",
    });
    storage.setItem("nimo.ui-session.v1", JSON.stringify({ activePage: "chapter_studio", sidebarCollapsed: false }));

    const fixture = applyUrlParityFixture();

    expect(fixture.active).toBe(true);
    expect(JSON.parse(storage.getItem("nimo.ui-session.v1") ?? "{}")).toEqual({
      activePage: "dashboard",
      sidebarCollapsed: true,
    });
  });

  it("keeps an explicit browser fixture URL as the primary source", () => {
    const fixtureWindow = installWindow("?__nimo_ui_parity=1&page=voice_studio&theme=twilight_ink");
    Object.defineProperty(fixtureWindow, "__NIMO_UI_PARITY_QUERY__", {
      configurable: true,
      value: "?__nimo_ui_parity=1&page=dashboard&theme=narrative_ember",
    });

    const fixture = applyUrlParityFixture();

    expect(fixture.active).toBe(true);
    expect(JSON.parse(storage.getItem("nimo.ui-session.v1") ?? "{}")).toEqual({
      activePage: "voice_studio",
      sidebarCollapsed: false,
    });
  });

  it("only opens source-sized export, audit and cleanup overlays over the prepared studio state", () => {
    installWindow("?__nimo_ui_parity=1&page=chapter_studio&theme=narrative_ember&chapter_state=prepared&chapter_dialog=export");
    expect(applyUrlParityFixture().chapterStudioDialog).toBe("export");

    installWindow("?__nimo_ui_parity=1&page=chapter_studio&theme=narrative_ember&chapter_state=prepared&chapter_dialog=book-audit");
    expect(applyUrlParityFixture().chapterStudioDialog).toBe("book-audit");

    installWindow("?__nimo_ui_parity=1&page=chapter_studio&theme=narrative_ember&chapter_state=prepared&chapter_dialog=clean");
    expect(applyUrlParityFixture().chapterStudioDialog).toBe("clean");

    installWindow("?__nimo_ui_parity=1&page=chapter_studio&theme=narrative_ember&chapter_state=running&chapter_dialog=export");
    expect(applyUrlParityFixture().chapterStudioDialog).toBeNull();
  });

  it("exposes the completed chapter-flow history fixture only when requested", () => {
    installWindow("?__nimo_ui_parity=1&page=chapter_studio&theme=narrative_ember&chapter_state=history");
    expect(applyUrlParityFixture().chapterStudioState).toBe("history");
  });

  it("exposes the compact Chapter Studio notice fixture only when requested", () => {
    installWindow("?__nimo_ui_parity=1&page=chapter_studio&theme=narrative_ember&chapter_state=notice");
    expect(applyUrlParityFixture().chapterStudioState).toBe("notice");
  });

  it("only applies the configured Voice Studio projection to its source page", () => {
    installWindow("?__nimo_ui_parity=1&page=voice_studio&theme=narrative_ember&voice_state=configured");
    expect(applyUrlParityFixture().voiceStudioState).toBe("configured");

    installWindow("?__nimo_ui_parity=1&page=dashboard&theme=narrative_ember&voice_state=configured");
    expect(applyUrlParityFixture().voiceStudioState).toBeNull();
  });

  it("pins a named Voice Studio capture tab and rejects it outside Voice Studio", () => {
    installWindow("?__nimo_ui_parity=1&page=voice_studio&theme=narrative_ember&voice_state=configured&voice_tab=settings");
    const fixture = applyUrlParityFixture();

    expect(fixture.voiceStudioTab).toBe("settings");
    expect(JSON.parse(storage.getItem("nimo.ui-session.v1") ?? "{}")).toMatchObject({
      activePage: "voice_studio",
      voiceTab: "settings",
    });

    installWindow("?__nimo_ui_parity=1&page=dashboard&theme=narrative_ember&voice_tab=settings");
    expect(applyUrlParityFixture().voiceStudioTab).toBeNull();
  });

  it("opens the source-shaped model-routing section only on the settings page", () => {
    installWindow("?__nimo_ui_parity=1&page=settings&theme=narrative_ember&settings_section=model-routing");
    expect(applyUrlParityFixture().settingsSection).toBe("model-routing");

    installWindow("?__nimo_ui_parity=1&page=dashboard&theme=narrative_ember&settings_section=model-routing");
    expect(applyUrlParityFixture().settingsSection).toBeNull();
  });

  it("limits the paused validated stream replay to the workflow observation overlay", () => {
    installWindow("?__nimo_ui_parity=1&page=workflow&workflow_dialog=floating-stream&workflow_stream=paused-validated");
    expect(applyUrlParityFixture().workflowStream).toBe("paused-validated");

    installWindow("?__nimo_ui_parity=1&page=workflow&workflow_stream=paused-validated");
    expect(applyUrlParityFixture().workflowStream).toBeNull();

    installWindow("?__nimo_ui_parity=1&page=dashboard&workflow_dialog=floating-stream&workflow_stream=paused-validated");
    expect(applyUrlParityFixture().workflowStream).toBeNull();
  });
});
